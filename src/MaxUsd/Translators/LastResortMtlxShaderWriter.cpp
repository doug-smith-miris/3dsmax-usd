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
// bowl while the V-Ray original is fully lit. This writer probes VRayLightMtl
// for its color/multiplier/texmap (MAXScript — the multiplier is a V-Ray param,
// not exposed through the standard Mtl SDK) and authors real emission.
//
// MAX-MTLX-EMISSIVE-UNITS-013: The initial MAX-MTLX-005 landing baked the raw
// V-Ray `.multiplier` scalar into `emission_color` (Tier 1: emission=1,
// emission_color = color * multiplier). For a jumbotron / scoreboard authored
// with mult=150 and color=(1,1,1) that produced emission_color=(150,150,150) —
// an HDR value that Karma / any tone-mapped path tracer saturates to flat WHITE
// on every exposed pixel of the emitter, exactly matching the diagnostic run's
// "screens blow to flat WHITE" symptom. It also violates the ND_standard_surface
// convention (emission_color is a 0..1 tint; emission is the scalar weight).
// The fix routes the multiplier into `emission` (weight) and leaves
// `emission_color` at the raw tint. Additionally, VRayLightMtl.units controls
// the absolute-scale interpretation of `.multiplier`:
//   units=0 (default, color 0..1)   -> multiplier is arbitrary author-scale
//   units=1 (luminous power, lumens) -> divide by 683 (photopic peak lm/W)
//   units=2 (luminance, cd/m² × π)  -> divide by pi
//   units=3 (radiant power, watts)  -> pass through
//   units=4 (radiance, W/m²/sr)     -> pass through
// After per-units normalization the weight is clamped to a soft ceiling
// (kEmissionCeiling = 30.0) so LED-jumbotron-style HDR multipliers still read
// as very bright emitters in Karma without saturating a default tone-mapper.
// Final authoring pattern:
//   Tier 1 (constant color): emission=Normalize(multiplier, units),
//                             emission_color = raw tint (unbaked).
//   Tier 2 (texture-driven):  emission=Normalize(multiplier, units),
//                             emission_color <- ND_tiledimage (raw texture).
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

// MAX-MTLX-005 / MAX-MTLX-EMISSIVE-UNITS-013: MAXScript probe for a
// VRayLightMtl's emission parameters. Returns a pipe/newline manifest (empty if
// the material is not a VRayLightMtl):
//   isLightMtl|1
//   multiplier|<float>
//   units|<int>                   (MAX-MTLX-013: 0=default, 1=lumens, 2=lum, 3=W, 4=W/m²/sr)
//   color|<r>,<g>,<b>             (0-255 MAXScript Color; C++ normalizes to [0,1])
//   texmap|<resolved file path>   (only if the color slot is texture-driven)
// The base color is read in C++ via Mtl::GetDiffuse() as a fallback only; the
// V-Ray-specific `.color`, `.multiplier`, `.units`, and texmap file must be
// probed via MAXScript because they are NOT exposed through the standard Mtl SDK.
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
        -- MAX-MTLX-EMISSIVE-UNITS-013: VRayLightMtl.units controls the absolute-scale
        -- interpretation of `.multiplier`. Author-side values (V-Ray SDK):
        --   0 = Default (arbitrary scale on a 0-1 color; typical multipliers 1..200)
        --   1 = Luminous power (lumens)
        --   2 = Luminance (lm/m^2/sr = candela/m^2 * pi)
        --   3 = Radiant power (watts)
        --   4 = Radiance (W/m^2/sr)
        -- Absent on very old V-Ray versions -> C++ treats as 0.
        if (isProperty m #units) then (
            result += ("units|" + ((getProperty m #units) as string) + "\n")
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
    // MAX-MTLX-EMISSIVE-UNITS-013: VRayLightMtl.units mode; 0 = default
    // (color 0-1, arbitrary scale), 1 = lumens, 2 = luminance, 3 = watts,
    // 4 = W/m^2/sr. Absent -> treat as 0 (default) so pre-`units` V-Ray
    // scenes retain the existing behavior.
    int         units { 0 };
    bool        hasColor { false };
    float       cr { 0.f }, cg { 0.f }, cb { 0.f }; // emission color in [0,1]
    std::string texFile;
};

// MAX-MTLX-EMISSIVE-UNITS-013: Convert V-Ray's per-units multiplier into a
// canonical ND_standard_surface `emission` weight. The ND_standard_surface
// convention is `emission_color` = tint in [0,1], `emission` = float weight
// in [0,inf). Raw V-Ray multipliers (e.g. 150 on a jumbotron) mapped into a
// color3 tint saturate to flat WHITE in Karma's tone-mapper regardless of the
// authored color; mapping them into the SCALAR weight and clamping to a soft
// ceiling reads as a "very bright emitter" without blowing out. Per-units
// factors bring physical modes (lumens, watts) into the same nominal range as
// the arbitrary-scale default mode so a single ceiling is meaningful across
// all five modes. Ceiling of 30 chosen empirically: enough headroom for LED
// emitters to visibly dominate a scene, low enough that a default Karma render
// with a mid-range exposure does not flat-white every emitter pixel.
// MAX-LIT-DIMNESS-014: The arena's fixtures + area lights are ALL units=0 (default) — verified on the
// box (Hexagonal ceiling lights mult=50, area lights mult 1.5..85). Two dimness causes were measured:
// (1) the old ceiling of 30 CLAMPED the mult=50 ceiling fixtures down to 30 (a 1.67x loss on the main
// room light), and (2) V-Ray's default-unit multiplier does not map 1:1 to a Karma nit — V-Ray's color
// mapping brightens beyond the raw radiance, so a units=0 light reads ~2x too dim in Karma. Fix: raise
// the ceiling so mult<=~500 fixtures pass through, and apply a shared units=0 calibration gain
// (kUnits0Gain) — the SAME gain the area-light path (VRayLightWriter) applies — so both emission paths
// track V-Ray. kUnits0Gain is the single knob to tune against the vray-baseline mean (~62/255).
static constexpr float kEmissionCeiling = 1000.0f;
static constexpr float kUnits0Gain      = 2.3f; // default-mode V-Ray -> Karma calibration (tunable)

static float _NormalizeEmissionWeight(float multiplier, int units)
{
    // NB: 'PI' is a Max SDK macro — use prefixed locals (mirrors kIntensityPi in VRayLightWriter).
    constexpr float kEmitPi = 3.14159265358979323846f;
    constexpr float kEmitK  = 683.0f; // photopic peak lm/W
    float           w       = multiplier;
    switch (units) {
    case 1: // lumens (total luminous flux) -> nit-scale (Lambertian): / pi
        w = multiplier / kEmitPi;
        break;
    case 2: // lm/m^2/sr = cd/m^2 = nits: already the target scale, pass through
        w = multiplier;
        break;
    case 3: // watts (total radiant flux) -> lumens (x683) -> nits (/pi)
        w = (multiplier * kEmitK) / kEmitPi;
        break;
    case 4: // W/m^2/sr radiance -> luminance via photopic peak
        w = multiplier * kEmitK;
        break;
    case 0: // default (arbitrary artistic scale): apply the units=0 calibration gain
    default:
        w = multiplier * kUnits0Gain;
        break;
    }
    if (w > kEmissionCeiling) {
        w = kEmissionCeiling;
    }
    if (w < 0.0f) {
        w = 0.0f;
    }
    return w;
}

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
            } else if (key == "units") {
                // MAX-MTLX-EMISSIVE-UNITS-013: 0..4 expected; clamp anything
                // outside that range to 0 (default mode) rather than trusting
                // a malformed manifest.
                const int u = std::stoi(val);
                probe.units = (u >= 0 && u <= 4) ? u : 0;
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

        // MAX-MTLX-EMISSIVE-UNITS-013: route the multiplier into the SCALAR emission
        // weight (per ND_standard_surface convention) and normalize per VRayLightMtl.units
        // so LED-jumbotron-style HDR multipliers don't blow the emission_color tint to
        // saturated white. `emission_color` stays the raw 0..1 tint in BOTH tiers.
        const float emissionWeight = _NormalizeEmissionWeight(emit.multiplier, emit.units);

        if (!emit.texFile.empty()) {
            // Tier 2: texture-driven emission. Author an ND_tiledimage_color3 sibling shader
            // and connect it to emission_color (raw tint); emission weight carries the
            // units-normalized multiplier.
            emissionInput.Set(emissionWeight);
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
            // Tier 1: constant emission color. Author `emission = normalized weight` and
            // `emission_color = raw tint` (0..1) so a Karma tone-mapper renders the
            // emitter as bright-but-not-saturated. Pre-013 baked the multiplier into
            // the color3 tint (emission=1, emission_color=color*multiplier), producing
            // e.g. (150,150,150) on a scoreboard — flat white in any tone-mapped renderer.
            emissionInput.Set(emissionWeight);
            emissionColorInput.Set(pxr::GfVec3f(er, eg, eb));
        }
        return;
    }

    // Non-emissive fallback (unchanged): diffuse color as base_color.
    const pxr::GfVec3f usdColor = { color.r, color.g, color.b };
    baseColorInput.Set(usdColor);
}

PXR_NAMESPACE_CLOSE_SCOPE
