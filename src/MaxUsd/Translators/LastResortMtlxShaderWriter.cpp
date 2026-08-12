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
// the material's diffuse.
//
// MAX-MTLX-005: Self-illuminated materials (V-Ray VRayLightMtl — LED ribbon
// boards, scoreboards, concourse signage) have no dedicated MaterialX writer
// and previously fell here, exporting as a plain diffuse surface with
// emission_color=(0,0,0). That dropped the arena's single biggest fill-light
// source, so a Karma/Hydra render of the export came out with a black seating
// bowl while the V-Ray original is fully lit. This writer now probes VRayLightMtl
// for its color/multiplier/texmap (MAXScript — the multiplier is a V-Ray param,
// not exposed through the standard Mtl SDK) and authors real emission:
//   Tier 1 (constant color): emission=1, emission_color = color * multiplier.
//   Tier 2 (texture-driven):  emission=multiplier, emission_color <- ND_tiledimage.
// base_color is set to black for a light material so it reads as a pure emitter.
//
#include "LastResortMtlxShaderWriter.h"

#include "ShaderWriter.h"
#include "ShaderWriterRegistry.h"
#include "ShadingModeRegistry.h"
#include "WriteJobContext.h"

#include <MaxUsd/DebugCodes.h>
#include <MaxUsd/Utilities/Logging.h>
#include <MaxUsd/Utilities/TranslationUtils.h>

#include <pxr/base/gf/vec2f.h>
#include <pxr/base/gf/vec3f.h>
#include <pxr/base/tf/diagnostic.h>
#include <pxr/base/tf/token.h>
#include <pxr/base/vt/value.h>
#include <pxr/pxr.h>
#include <pxr/usd/sdf/assetPath.h>
#include <pxr/usd/sdf/path.h>
#include <pxr/usd/sdf/types.h>
#include <pxr/usd/usdShade/input.h>
#include <pxr/usd/usdShade/output.h>
#include <pxr/usd/usdShade/shader.h>
#include <pxr/usd/usdShade/tokens.h>

#include <Materials/mtl.h>
#include <max.h>
#include <maxscript/foundation/functions.h>
#include <maxscript/maxscript.h>
#include <maxscript/util/listener.h>

#include <sstream>
#include <string>

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
static const TfToken kNdTiledImageColor3Id("ND_tiledimage_color3");

// MAX-MTLX-005: MAXScript probe for a VRayLightMtl's emission parameters. Returns
// a pipe/newline manifest (empty if the material is not a VRayLightMtl):
//   isLightMtl|1
//   multiplier|<float>
//   texmap|<resolved file path>   (only if the color slot is texture-driven)
// The color itself is read in C++ via Mtl::GetDiffuse() (already [0,1]); only the
// V-Ray-specific multiplier + the texmap file need MAXScript.
static const TSTR discoverVRayLightMtlFn = LR"(
    fn discoverVRayLightMtl matAnimHandle = (
        local m = getAnimByHandle matAnimHandle
        local result = ""
        if m == undefined then return result
        -- Unwrap VRayOverrideMtl (V-Ray's per-ray override trick): the emissive VRayLightMtl is the
        -- base material; use it for the emission we author (single-material USD can't do per-ray).
        if ((classOf m) as string) == "VRayOverrideMtl" then (
            if (isProperty m #baseMtl) and ((getProperty m #baseMtl) != undefined) then m = getProperty m #baseMtl
        )
        if ((classOf m) as string) != "VRayLightMtl" then return result
        result += "isLightMtl|1\n"
        if (isProperty m #multiplier) then (
            result += ("multiplier|" + ((getProperty m #multiplier) as string) + "\n")
        )
        -- VRayLightMtl's emission color is `.color` (0-255), NOT the diffuse channel
        -- (Mtl::GetDiffuse returns black for a light material).
        if (isProperty m #color) then (
            local c = getProperty m #color
            result += ("color|" + (c.r as string) + "," + (c.g as string) + "," + (c.b as string) + "\n")
        )
        if (isProperty m #texmap) then (
            local tex = getProperty m #texmap
            if tex != undefined then (
                local fname = undefined
                if (classOf tex) == Bitmaptexture then (
                    fname = tex.filename
                ) else if (isProperty tex #filename) then (
                    fname = getProperty tex #filename
                ) else if (isProperty tex #bitmap) then (
                    local bmp = getProperty tex #bitmap
                    if bmp != undefined and (isProperty bmp #filename) then fname = bmp.filename
                )
                if fname != undefined and fname != "" then (
                    local resolved = fname
                    try ( FileResolutionManager.getFullFilePath &resolved #bitmap ) catch ()
                    if resolved == undefined or resolved == "" then resolved = fname
                    result += ("texmap|" + resolved + "\n")
                )
            )
        )
        return result
    )
    discoverVRayLightMtl )";

struct VRayLightMtlProbe
{
    bool        isLightMtl { false };
    float       multiplier { 1.f };
    bool        hasColor { false };
    float       cr { 0.f }, cg { 0.f }, cb { 0.f }; // emission color in [0,1]
    std::string texFile;
};

VRayLightMtlProbe _ProbeVRayLightMtl(Mtl* material)
{
    VRayLightMtlProbe probe;
    if (material == nullptr) {
        return probe;
    }
    const AnimHandle handle = ::Animatable::GetHandleByAnim(material);
    if (handle == 0) {
        return probe;
    }
    FPValue rvalue;
    rvalue.Init();
    std::wstringstream ss;
    ss << discoverVRayLightMtlFn << handle << L'\0';
    ExecuteMAXScriptScript(ss.str().c_str(), MAXScript::ScriptSource::Dynamic, false, &rvalue);
    const std::string manifest = MaxUsd::MaxStringToUsdString(rvalue.s);
    if (manifest.empty()) {
        return probe;
    }
    for (size_t start = 0; start <= manifest.size();) {
        auto        nl = manifest.find('\n', start);
        std::string line;
        if (nl == std::string::npos) {
            line = manifest.substr(start);
            start = manifest.size() + 1;
        } else {
            line = manifest.substr(start, nl - start);
            start = nl + 1;
        }
        while (!line.empty() && (line.back() == '\r' || line.back() == ' ')) {
            line.pop_back();
        }
        auto pipe = line.find('|');
        if (pipe == std::string::npos) {
            continue;
        }
        const auto key = line.substr(0, pipe);
        const auto val = line.substr(pipe + 1);
        try {
            if (key == "isLightMtl") {
                probe.isLightMtl = (val == "1");
            } else if (key == "multiplier") {
                probe.multiplier = std::stof(val);
            } else if (key == "color") {
                // "r,g,b" in 0-255 (MAXScript Color) -> [0,1].
                const auto c1 = val.find(',');
                const auto c2 = (c1 == std::string::npos) ? std::string::npos : val.find(',', c1 + 1);
                if (c1 != std::string::npos && c2 != std::string::npos) {
                    probe.cr = std::stof(val.substr(0, c1)) / 255.f;
                    probe.cg = std::stof(val.substr(c1 + 1, c2 - c1 - 1)) / 255.f;
                    probe.cb = std::stof(val.substr(c2 + 1)) / 255.f;
                    probe.hasColor = true;
                }
            } else if (key == "texmap") {
                probe.texFile = val;
            }
        } catch (const std::exception&) {
            // Ignore parse errors; keep defaults.
        }
    }
    return probe;
}
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

    // Read the base material's diffuse color; for a light material this is the
    // emission color (VRayLightMtl::GetDiffuse returns its color in [0,1]).
    const auto color = material->GetDiffuse();

    const auto baseColorInput
        = shaderSchema.CreateInput(pxr::TfToken("base_color"), pxr::SdfValueTypeNames->Color3f);

    // MAX-MTLX-005: self-illuminated (VRayLightMtl) materials author real emission.
    const VRayLightMtlProbe emit = _ProbeVRayLightMtl(material);
    { // [MAX-MTLX-DIAG] revert before PR — proves per-material emission authoring.
        MSTR mn = material->GetName();
        MaxUsd::Log::Warn(
            L"[EMITPROBE] mat={0} isLightMtl={1} mult={2} color=({3},{4},{5}) tex={6}",
            mn.data(),
            emit.isLightMtl ? 1 : 0,
            emit.multiplier,
            emit.cr,
            emit.cg,
            emit.cb,
            emit.texFile.empty() ? 0 : 1);
    }
    if (emit.isLightMtl) {
        // Pure emitter: no diffuse reflection.
        baseColorInput.Set(pxr::GfVec3f(0.f, 0.f, 0.f));

        const auto emissionInput
            = shaderSchema.CreateInput(pxr::TfToken("emission"), pxr::SdfValueTypeNames->Float);
        const auto emissionColorInput = shaderSchema.CreateInput(
            pxr::TfToken("emission_color"), pxr::SdfValueTypeNames->Color3f);

        // Emission color = VRayLightMtl.color (probed; GetDiffuse is black for a light material).
        const float er = emit.hasColor ? emit.cr : color.r;
        const float eg = emit.hasColor ? emit.cg : color.g;
        const float eb = emit.hasColor ? emit.cb : color.b;

        if (!emit.texFile.empty()) {
            // Tier 2: texture-driven emission. Author an ND_tiledimage_color3 sibling shader and
            // connect it to emission_color; emission weight carries the multiplier.
            emissionInput.Set(emit.multiplier);
            const SdfPath texPath
                = GetUsdPath().GetParentPath().AppendChild(pxr::TfToken("emission_tex"));
            UsdShadeShader texShader = UsdShadeShader::Define(GetUsdStage(), texPath);
            if (texShader) {
                texShader.CreateIdAttr(VtValue(kNdTiledImageColor3Id));
                texShader
                    .CreateInput(pxr::TfToken("file"), pxr::SdfValueTypeNames->Asset)
                    .Set(pxr::SdfAssetPath(emit.texFile));
                texShader.CreateInput(pxr::TfToken("uvtiling"), pxr::SdfValueTypeNames->Float2)
                    .Set(pxr::GfVec2f(1.f, 1.f));
                const auto texOut
                    = texShader.CreateOutput(pxr::TfToken("out"), pxr::SdfValueTypeNames->Color3f);
                emissionColorInput.ConnectToSource(texOut);
            } else {
                emissionColorInput.Set(pxr::GfVec3f(er, eg, eb));
            }
        } else {
            // Tier 1: constant emission color. Bake the multiplier into the color so a single
            // emission weight of 1 yields color * multiplier (values may exceed 1, as intended
            // for an emitter). WIRE-GLOW ×150, SPOTLIGHT-LENS ×75, LENS ×50, etc.
            emissionInput.Set(1.f);
            emissionColorInput.Set(
                pxr::GfVec3f(er * emit.multiplier, eg * emit.multiplier, eb * emit.multiplier));
        }
        return;
    }

    // Non-emissive fallback (unchanged): diffuse color as base_color.
    const pxr::GfVec3f usdColor = { color.r, color.g, color.b };
    baseColorInput.Set(usdColor);
}

PXR_NAMESPACE_CLOSE_SCOPE
