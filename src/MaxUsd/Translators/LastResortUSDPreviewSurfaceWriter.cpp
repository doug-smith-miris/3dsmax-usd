//
// Copyright 2023 Autodesk
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.
//
#include "LastResortUSDPreviewSurfaceWriter.h"

#include "ShaderWriter.h"
#include "ShaderWriterRegistry.h"
#include "ShadingModeRegistry.h"
#include "WriteJobContext.h"

#include <pxr/base/tf/diagnostic.h>
#include <pxr/base/tf/token.h>
#include <pxr/base/vt/value.h>
#include <pxr/pxr.h>
#include <pxr/usd/sdf/path.h>
#include <pxr/usd/sdf/types.h>
#include <pxr/usd/usdShade/input.h>
#include <pxr/usd/usdShade/shader.h>
#include <pxr/usd/usdShade/tokens.h>
#include <pxr/usdImaging/usdImaging/tokens.h>

#include <Materials/mtl.h>
#include <max.h>

PXR_NAMESPACE_OPEN_SCOPE

MaxUsdShaderWriter::ContextSupport
LastResortUSDPreviewSurfaceWriter::CanExport(const MaxUsd::USDSceneBuilderOptions& exportArgs)
{
    if (exportArgs.GetConvertMaterialsTo() == pxr::UsdImagingTokens->UsdPreviewSurface) {
        return ContextSupport::Fallback;
    }
    return ContextSupport::Unsupported;
}

LastResortUSDPreviewSurfaceWriter::LastResortUSDPreviewSurfaceWriter(
    Mtl*                   material,
    const SdfPath&         usdPath,
    MaxUsdWriteJobContext& jobCtx)
    : MaxUsdShaderWriter(material, usdPath, jobCtx)
{
    MSTR warning = L"No Shader Writer found to convert Material \"";
    warning.append(material->GetName());
    warning.append(L"\" of type \"");
    MSTR className;
    material->GetClassName(className);
    warning.append(className);
    warning.append(L"\"to USDPreviewSurface. Generating a basic USDPreviewSurface with a diffuse "
                   L"color as a fallback.");
    MaxUsd::Log::Warn(warning.data());

    UsdShadeShader shaderSchema = UsdShadeShader::Define(GetUsdStage(), GetUsdPath());
    if (!TF_VERIFY(
            shaderSchema,
            "Could not define UsdShadeShader at path '%s'\n",
            GetUsdPath().GetText())) {
        return;
    }

    shaderSchema.CreateIdAttr(VtValue(UsdImagingTokens->UsdPreviewSurface));

    usdPrim = shaderSchema.GetPrim();
    if (!TF_VERIFY(
            usdPrim,
            "Could not get UsdPrim for UsdShadeShader at path '%s'\n",
            shaderSchema.GetPath().GetText())) {
        return;
    }

    // Surface Output
    shaderSchema.CreateOutput(UsdShadeTokens->surface, SdfValueTypeNames->Token);
}

// MAX-PRIM-001 surgical-coverage bound (audit, no logic change): this
// writer is the C++ fallback on the UsdPreviewSurface writer path
// (registered via `ContextSupport::Fallback` for
// `UsdImagingTokens->UsdPreviewSurface`). It authors exactly one input
// (`inputs:diffuseColor` from `Mtl::GetDiffuse()`) and that is its
// COMPLETE responsibility -- no emission helper, no roughness helper,
// no metallic helper. The MaterialX-side post-parse normalization
// passes in `src/translators/MtlxShaderWriter.cpp`
// (`_NormalizeStandardSurfaceSpecularRotation`,
// `_NormalizeStandardSurfaceEmissionDefault`,
// `_NormalizeStandardSurfaceCoatDefaults`,
// `_NormalizeStandardSurfaceSubsurfaceRadiusDefault`,
// `_StripStandardSurfaceSpecDefaultInputs`) operate on a
// `MaterialX::DocumentPtr` returned by `MtlxIOUtil.ExportMtlxString`
// and gate on `doc->getNodes("standard_surface")`; they MUST NOT be
// ported here, shared with this writer, or invoked from `Write()`
// below. The two writer paths handle materially different inputs
// (this writer reads `Mtl*` directly; the MaterialX writer reads a
// MaterialX-XML serialization of the same material via the MaxScript
// bridge) and serve different USD targets (UsdPreviewSurface here,
// MaterialX `standard_surface` there) with fundamentally different
// input vocabularies, defaults, and topology shapes (see the
// MAX-PRIM-001 cross-writer comment block in MtlxShaderWriter.cpp at
// the chain call site for the collision-shape table).
//
// A future "unify color-emission helper across MaterialX and
// UsdPreviewSurface writers" refactor that introduced a shared
// emission-strip helper here would (a) be dead code if it preserved
// the MAX-MAT-002 strict pair-gate (UsdPS has no companion `emission`
// scalar), OR (b) silently corrupt artist-authored `emissiveColor`
// values if it widened the gate to fire on the color3 alone. The
// audit's visual pair (`render_karma_postfix.png` vs
// `render_unreal_reference.png` in the run's arch-build directory)
// captures both branches of the choice on a UsdPreviewSurface sphere
// with `emissiveColor = (0.05, 0, 0)` authored.
//
// The Python validator at
// `/Users/d.smith/MirisProjects/Agent Builder/agent/arch-builds/
// da4ca14a-c323-47a6-bd26-8997a625e3f6/
// validate_color_emission_writer_separation.py` pins the bound at the
// MaterialX-document layer with 36 named assertions (11 standard_surface
// strips as positive control + 23 UsdPreviewSurface-category preserves
// + 2 idempotence checks). The MaxScript regression
// `test_export_writer_path_separation_color_emission` in
// `src/Tests/Integration/mtlxShaderWriter_test.ms` pins the bound at
// the full-export layer by re-exporting the same PhysicalMaterial
// under both targets in a single test run.
//
// MAX-MAT-009 surgical-coverage bound (audit, no logic change): the
// `diffuseColor` value authored below is a `Color3f` USD attribute,
// NOT a `UsdUVTexture` shader output -- this writer does not create
// any child texture nodes. Therefore:
//   (a) NO `sourceColorSpace` token input is or can be authored --
//       `sourceColorSpace` is a UsdUVTexture-specific input convention
//       (UsdPreviewSurface spec). There is no UsdUVTexture child here.
//   (b) NO `colorSpace` USD metadata is authored on the `diffuseColor`
//       attribute itself. Per UsdPreviewSurface spec, `diffuseColor`
//       authored as a Color3f value is **linear by definition**;
//       tagging it with a `colorSpace` metadata would be misleading
//       (linear by spec -- renderers ignore the tag) or actively wrong
//       (an "sRGB" tag would either be ignored or applied
//       inconsistently across renderers, producing a visible appearance
//       drift the artist did not author).
// A future PR that extended this writer to bake a texture would have
// to author `sourceColorSpace` on its UsdUVTexture child, mirroring
// the Python `set_bitmap_scale_bias_sourcecolorspace` shape rather
// than tagging the `diffuseColor` attribute directly. The Python
// validator's `LastResort_no_colorSpace_anywhere` case pins this
// bound:
//   * NO child shader under the Material has id `UsdUVTexture` --
//     positive control for the value-only writer contract.
//   * NO `colorSpace` USD metadata on the `diffuseColor` attribute --
//     positive control that this writer does not invent a tag.
/* virtual */
void LastResortUSDPreviewSurfaceWriter::Write()
{
    MaxUsdShaderWriter::Write();

    Mtl* material = GetMaterial();

    UsdShadeShader shaderSchema(usdPrim);
    if (!TF_VERIFY(
            shaderSchema,
            "Could not get UsdShadeShader schema for UsdPrim at path '%s'\n",
            usdPrim.GetPath().GetText())) {
        return;
    }

    auto color = material->GetDiffuse();

    shaderSchema.CreateOutput(UsdShadeTokens->surface, SdfValueTypeNames->Token);

    const auto diffuseColorInput
        = shaderSchema.CreateInput(pxr::TfToken("diffuseColor"), pxr::SdfValueTypeNames->Color3f);

    const pxr::GfVec3f usdColor = { color.r, color.g, color.b };
    diffuseColorInput.Set(usdColor);
}

PXR_NAMESPACE_CLOSE_SCOPE
