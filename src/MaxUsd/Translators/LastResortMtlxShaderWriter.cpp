//
// Copyright 2026 Miris Inc.
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
// MAX-MTLX-004: A minimum-viable ND_standard_surface_surfaceshader emitted
// as a last-resort fallback for the MaterialX render context. See header for
// rationale. The wiring of the material's `outputs:mtlx:surface` -> this
// shader's default output is done by the caller
// (MaxUsdShadingUtils::CreateShaderOutputAndConnectMaterial), so this writer
// only defines the shader prim, sets info:id, and authors a base_color from
// the material's diffuse. Deliberately narrow — the purpose is dispatch
// coverage, not shading fidelity, mirroring the UsdPreviewSurface fallback.
//
#include "LastResortMtlxShaderWriter.h"

#include "ShaderWriter.h"
#include "ShaderWriterRegistry.h"
#include "ShadingModeRegistry.h"
#include "WriteJobContext.h"

#include <MaxUsd/DebugCodes.h>

#include <pxr/base/tf/diagnostic.h>
#include <pxr/base/tf/token.h>
#include <pxr/base/vt/value.h>
#include <pxr/pxr.h>
#include <pxr/usd/sdf/path.h>
#include <pxr/usd/sdf/types.h>
#include <pxr/usd/usdShade/input.h>
#include <pxr/usd/usdShade/shader.h>
#include <pxr/usd/usdShade/tokens.h>

#include <Materials/mtl.h>
#include <max.h>

PXR_NAMESPACE_OPEN_SCOPE

namespace {
// The MaterialX target's render-context token, matching the runtime
// registration string used by `ShadingModeRegistry::RegisterExportConversion`
// (see the plugin's `register-materialx-target.ms`), and the target string
// the MtlxShaderWriter itself matches on in its own CanExport.
static const TfToken kMaterialXTarget("MaterialX");

// The ND_standard_surface info:id used by the primary MtlxShaderWriter for
// converted PhysicalMaterial / OpenPBR sources. Same shader identity so that
// downstream Karma / Hydra / MaterialX consumers evaluate the fallback
// through the same node definition, just with a plain base_color and no
// texture graph.
static const TfToken kNdStandardSurfaceId("ND_standard_surface_surfaceshader");
} // namespace

MaxUsdShaderWriter::ContextSupport
LastResortMtlxShaderWriter::CanExport(const MaxUsd::USDSceneBuilderOptions& exportArgs)
{
    if (exportArgs.GetConvertMaterialsTo() == kMaterialXTarget) {
        return ContextSupport::Fallback;
    }
    return ContextSupport::Unsupported;
}

LastResortMtlxShaderWriter::LastResortMtlxShaderWriter(
    Mtl*                   material,
    const SdfPath&         usdPath,
    MaxUsdWriteJobContext& jobCtx)
    : MaxUsdShaderWriter(material, usdPath, jobCtx)
{
    MSTR warning = L"No MaterialX Shader Writer found to convert Material \"";
    warning.append(material->GetName());
    warning.append(L"\" of type \"");
    MSTR className;
    material->GetClassName(className);
    warning.append(className);
    warning.append(L"\" to MaterialX. Generating a minimal "
                   L"ND_standard_surface_surfaceshader with the material's "
                   L"diffuse color as base_color as a fallback.");
    MaxUsd::Log::Warn(warning.data());

    UsdShadeShader shaderSchema = UsdShadeShader::Define(GetUsdStage(), GetUsdPath());
    if (!TF_VERIFY(
            shaderSchema,
            "Could not define UsdShadeShader at path '%s'\n",
            GetUsdPath().GetText())) {
        return;
    }

    shaderSchema.CreateIdAttr(VtValue(kNdStandardSurfaceId));

    usdPrim = shaderSchema.GetPrim();
    if (!TF_VERIFY(
            usdPrim,
            "Could not get UsdPrim for UsdShadeShader at path '%s'\n",
            shaderSchema.GetPath().GetText())) {
        return;
    }
}

/* virtual */
void LastResortMtlxShaderWriter::Write()
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

    // Mirror the UsdPreviewSurface fallback: read the base material's diffuse
    // color and set it as `base_color`. base_color on ND_standard_surface is
    // a color3f, matching Color::r/g/b as floats in [0,1].
    const auto color = material->GetDiffuse();

    const auto baseColorInput
        = shaderSchema.CreateInput(pxr::TfToken("base_color"), pxr::SdfValueTypeNames->Color3f);
    const pxr::GfVec3f usdColor = { color.r, color.g, color.b };
    baseColorInput.Set(usdColor);
}

PXR_NAMESPACE_CLOSE_SCOPE
