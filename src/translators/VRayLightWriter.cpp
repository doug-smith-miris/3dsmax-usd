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
#include "VRayLightWriter.h"

#include <MaxUsd/Translators/primWriter.h>
#include <MaxUsd/Translators/writeJobContext.h>
#include <MaxUsd/Utilities/Logging.h>
#include <MaxUsd/Utilities/MaxSupportUtils.h>
#include <MaxUsd/Utilities/TranslationUtils.h>
#include <MaxUsd/Utilities/TypeUtils.h>

#include <pxr/base/gf/vec3f.h>
#include <pxr/base/tf/token.h>
#include <pxr/pxr.h>
#include <pxr/usd/sdf/assetPath.h>
#include <pxr/usd/sdf/types.h>
#include <pxr/usd/usd/timeCode.h>
#include <pxr/usd/usdLux/boundableLightBase.h>
#include <pxr/usd/usdLux/diskLight.h>
#include <pxr/usd/usdLux/distantLight.h>
#include <pxr/usd/usdLux/domeLight.h>
#include <pxr/usd/usdLux/nonboundableLightBase.h>
#include <pxr/usd/usdLux/rectLight.h>
#include <pxr/usd/usdLux/shadowAPI.h>
#include <pxr/usd/usdLux/shapingAPI.h>
#include <pxr/usd/usdLux/sphereLight.h>

#include <genlight.h>
#include <lslights.h> // LIGHTSCAPE_LIGHT_CLASS — defer photometric lights to PhotometricLightWriter
#include <maxscript/maxscript.h>
#include <maxscript/foundation/functions.h>
#include <maxscript/util/listener.h>

#include <fstream>
#include <sstream>
#include <string>

PXR_NAMESPACE_OPEN_SCOPE

namespace {

// MAX-LIT-002: Probe the underlying V-Ray light instance for the subset of
// parameters that are NOT available through the standard GenLight interface.
// V-Ray is a third-party plugin; we cannot link against its SDK, but every
// V-Ray light exposes its shape/size/temperature/IES-file via MAXScript
// properties. Returned string is a pipe-delimited manifest, one line per
// probed field:
//
//   className|<VRayLight | VRayIES | VRaySun | ...>
//   type|<int>              -- VRayLight only: 0=Plane, 1=Dome, 2=Sphere,
//                              3=Mesh, 4=Disc
//   size0|<float>           -- VRayLight: size along local X (rect width /
//                              sphere-radius carrier)
//   size1|<float>           -- VRayLight: size along local Y (rect height)
//   size2|<float>           -- VRayLight: size along local Z (reserved)
//   temperature|<float>     -- VRayLight/VRayIES color-temperature Kelvin
//                              when `color_mode` requests it
//   colorMode|<int>         -- 0 = RGB, 1 = temperature
//   useTemp|<bool>          -- true if temperature is the active driver
//   iesFile|<path>          -- VRayIES: IES profile path (may be empty)
//   sunTurbidity|<float>    -- VRaySun: atmosphere turbidity multiplier
//   units|<int>             -- VRayLight units enum (MAX-LIT-INTENSITY-UNITS-005):
//                              0 = default (0-1 color multiplier, arbitrary scale)
//                              1 = lumens (total luminous flux)
//                              2 = lm/m²/sr = cd/m² = nits (luminance;
//                                  matches USD's physical convention on area lights
//                                  with normalize=true)
//                              3 = watts (total radiant flux)
//                              4 = W/m²/sr (radiance)
//   shadowRadius|<float>    -- VRayLight/VRayIES: shadow softness in world units
//                              (0 = pin-sharp; the fix's KEY signal — see
//                              MAX-LIT-SHADOW-SOFTNESS-006).
//   shadowSubdivs|<int>     -- VRayLight/VRayIES: sample-count hint for
//                              area-shadow evaluation (default 8).
//   shadowBias|<float>      -- VRayLight/VRayIES: depth offset (defaults 0.2).
//   areaShadow|<bool>       -- VRayLight: treat area light as area for shadow
//                              computation (true) or as a point-source (false).
//                              Maps to UsdLuxSphereLight's `treatAsPoint` on the
//                              SphereLight branch.
//   shadowColor|<r,g,b>     -- VRayLight/VRayIES: shadow tint. Comma-delimited
//                              float triplet (0..1); authored to the stdlib
//                              `UsdLuxShadowAPI.shadow:color` attr.
//
// Any line whose value cannot be probed is omitted. Absent lines mean
// "use the GenLight default" in the caller.
static const TSTR discoverMaxVrayLightFn = LR"(
    fn discoverMaxVrayLight nodeAnimHandle = (
        local n = getAnimByHandle nodeAnimHandle
        local result = ""
        if n == undefined then return result
        local obj = n
        -- Node -> baseobject probe; getAnimByHandle returns the node itself
        if (isProperty n #baseobject) then obj = n.baseobject
        if obj == undefined then return result
        local cn = (classOf obj) as string
        result += ("className|" + cn + "\n")
        if (isProperty obj #type) then (
            local t = getProperty obj #type
            result += ("type|" + (t as string) + "\n")
        )
        if (isProperty obj #size0) then (
            result += ("size0|" + ((getProperty obj #size0) as string) + "\n")
        )
        if (isProperty obj #size1) then (
            result += ("size1|" + ((getProperty obj #size1) as string) + "\n")
        )
        if (isProperty obj #size2) then (
            result += ("size2|" + ((getProperty obj #size2) as string) + "\n")
        )
        if (isProperty obj #temperature) then (
            result += ("temperature|" + ((getProperty obj #temperature) as string) + "\n")
        )
        if (isProperty obj #color_mode) then (
            result += ("colorMode|" + ((getProperty obj #color_mode) as string) + "\n")
        )
        if (isProperty obj #ies_file) then (
            local ies = getProperty obj #ies_file
            if ies != undefined then (
                result += ("iesFile|" + (ies as string) + "\n")
            )
        )
        if (isProperty obj #turbidity) then (
            result += ("sunTurbidity|" + ((getProperty obj #turbidity) as string) + "\n")
        )
        if (isProperty obj #units) then (
            result += ("units|" + ((getProperty obj #units) as string) + "\n")
        )
        -- MAX-LIT-SHADOW-SOFTNESS-006 shadow family. Each check is
        -- independent so mixing spellings (V-Ray's UI wobbles between
        -- shadowRadius / shadow_radius across releases) still works.
        if (isProperty obj #shadowRadius) then (
            result += ("shadowRadius|" + ((getProperty obj #shadowRadius) as string) + "\n")
        )
        if (isProperty obj #shadow_radius) then (
            result += ("shadowRadius|" + ((getProperty obj #shadow_radius) as string) + "\n")
        )
        if (isProperty obj #shadowSubdivs) then (
            result += ("shadowSubdivs|" + ((getProperty obj #shadowSubdivs) as string) + "\n")
        )
        if (isProperty obj #shadow_subdivs) then (
            result += ("shadowSubdivs|" + ((getProperty obj #shadow_subdivs) as string) + "\n")
        )
        if (isProperty obj #shadowBias) then (
            result += ("shadowBias|" + ((getProperty obj #shadowBias) as string) + "\n")
        )
        if (isProperty obj #shadow_bias) then (
            result += ("shadowBias|" + ((getProperty obj #shadow_bias) as string) + "\n")
        )
        if (isProperty obj #areaShadow) then (
            result += ("areaShadow|" + ((getProperty obj #areaShadow) as string) + "\n")
        )
        if (isProperty obj #area_shadow) then (
            result += ("areaShadow|" + ((getProperty obj #area_shadow) as string) + "\n")
        )
        if (isProperty obj #shadowColor) then (
            local sc = getProperty obj #shadowColor
            if sc != undefined then (
                result += ("shadowColor|" + ((sc.r / 255.0) as string) + "," + ((sc.g / 255.0) as string) + "," + ((sc.b / 255.0) as string) + "\n")
            )
        )
        if (isProperty obj #shadow_color) then (
            local sc2 = getProperty obj #shadow_color
            if sc2 != undefined then (
                result += ("shadowColor|" + ((sc2.r / 255.0) as string) + "," + ((sc2.g / 255.0) as string) + "," + ((sc2.b / 255.0) as string) + "\n")
            )
        )
        if (isProperty obj #enabled) then (
            result += ("enabled|" + ((getProperty obj #enabled) as string) + "\n")
        )
        if (isProperty obj #on) then (
            result += ("on|" + ((getProperty obj #on) as string) + "\n")
        )
        return result
    )
    discoverMaxVrayLight )";

// Parsed manifest returned by the discovery script above. Every field is
// optional; the writer falls back to GenLight defaults when a field is
// absent.
struct VRayLightProbe
{
    std::string className;
    bool        hasType { false };
    int         type { 0 };
    bool        hasSize0 { false };
    float       size0 { 0.f };
    bool        hasSize1 { false };
    float       size1 { 0.f };
    bool        hasSize2 { false };
    float       size2 { 0.f };
    bool        hasTemperature { false };
    float       temperature { 6500.f };
    bool        useTemperature { false };
    std::string iesFile;
    bool        hasEnabled { false };
    bool        enabled { true };
    // MAX-LIT-INTENSITY-UNITS-005: VRayLight `.units` selector.
    //   0 = default (0-1 color, arbitrary scale)
    //   1 = lumens (total luminous flux)
    //   2 = lm/m²/sr = cd/m² = nits (matches UsdLux normalize=true convention)
    //   3 = watts (total radiant flux)
    //   4 = W/m²/sr (radiance)
    // hasUnits=false means the source scene had no `.units` (very old V-Ray or
    // a light class without the property) -> treat as mode 0 (pass-through).
    bool        hasUnits { false };
    int         units { 0 };
    // MAX-LIT-SHADOW-SOFTNESS-006 — shadow-family fields probed off the
    // V-Ray light object. Every field is optional; a missing probe line
    // leaves the corresponding `has*` flag at false so the writer emits
    // nothing new (byte-identical to the pre-006 output for scenes that
    // author no shadow settings — the surgical-scope invariant the hython
    // mirror locks in).
    bool        hasShadowRadius { false };
    float       shadowRadius { 0.f };
    bool        hasShadowSubdivs { false };
    int         shadowSubdivs { 8 };
    bool        hasShadowBias { false };
    float       shadowBias { 0.2f };
    bool        hasAreaShadow { false };
    bool        areaShadow { true };
    bool        hasShadowColor { false };
    float       shadowColor[3] { 0.f, 0.f, 0.f };
};

VRayLightProbe _ProbeVRayLight(INode* node)
{
    VRayLightProbe probe;
    if (node == nullptr) {
        return probe;
    }
    const AnimHandle animHandle = ::Animatable::GetHandleByAnim(node);
    if (animHandle == 0) {
        return probe;
    }
    FPValue rvalue;
    rvalue.Init();
    std::wstringstream ss;
    ss << discoverMaxVrayLightFn << animHandle << L'\0';
    ExecuteMAXScriptScript(
        ss.str().c_str(), MAXScript::ScriptSource::Dynamic, false, &rvalue);
    const auto manifest = MaxUsd::MaxStringToUsdString(rvalue.s);
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
        while (!line.empty()
               && (line.back() == '\r' || line.back() == ' ' || line.back() == '\t')) {
            line.pop_back();
        }
        if (line.empty()) {
            continue;
        }
        auto pipe = line.find('|');
        if (pipe == std::string::npos) {
            continue;
        }
        auto key = line.substr(0, pipe);
        auto val = line.substr(pipe + 1);
        try {
            if (key == "className") {
                probe.className = val;
            } else if (key == "type") {
                probe.hasType = true;
                probe.type = std::stoi(val);
            } else if (key == "size0") {
                probe.hasSize0 = true;
                probe.size0 = std::stof(val);
            } else if (key == "size1") {
                probe.hasSize1 = true;
                probe.size1 = std::stof(val);
            } else if (key == "size2") {
                probe.hasSize2 = true;
                probe.size2 = std::stof(val);
            } else if (key == "temperature") {
                probe.hasTemperature = true;
                probe.temperature = std::stof(val);
            } else if (key == "colorMode") {
                // colorMode == 1 means temperature drives the color.
                probe.useTemperature = (std::stoi(val) == 1);
            } else if (key == "iesFile") {
                probe.iesFile = val;
            } else if (key == "enabled" || key == "on") {
                probe.hasEnabled = true;
                std::string v = val;
                for (auto& c : v) {
                    c = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
                }
                probe.enabled = !(v == "false" || v == "0");
            } else if (key == "units") {
                // MAX-LIT-INTENSITY-UNITS-005: only the documented 0..4 modes
                // are honored; anything outside that range falls back to 0
                // (author-scale pass-through) rather than trusting a
                // malformed manifest to drive a physical normalization.
                const int u = std::stoi(val);
                if (u >= 0 && u <= 4) {
                    probe.hasUnits = true;
                    probe.units = u;
                }
            } else if (key == "shadowRadius") {
                // MAX-LIT-SHADOW-SOFTNESS-006: soft-shadow radius in world
                // units. Clamp negatives to 0 (Max UI is non-negative) and
                // cap the upper end to prevent a malformed manifest from
                // ballooning a disk light's effective area at render time.
                float r = std::stof(val);
                if (r < 0.f) {
                    r = 0.f;
                }
                if (r > 100.f) {
                    r = 100.f;
                }
                probe.hasShadowRadius = true;
                probe.shadowRadius = r;
            } else if (key == "shadowSubdivs") {
                // MAX-LIT-SHADOW-SOFTNESS-006: shadow sample count. Clamp
                // to [1, 256] — VRayLight's UI cap is 100 but future
                // releases may raise it; 256 keeps Karma tractable.
                int s = std::stoi(val);
                if (s < 1) {
                    s = 1;
                }
                if (s > 256) {
                    s = 256;
                }
                probe.hasShadowSubdivs = true;
                probe.shadowSubdivs = s;
            } else if (key == "shadowBias") {
                // MAX-LIT-SHADOW-SOFTNESS-006: shadow depth-bias. Non-
                // negative; VRayLight's default is 0.2 world units.
                float b = std::stof(val);
                if (b < 0.f) {
                    b = 0.f;
                }
                probe.hasShadowBias = true;
                probe.shadowBias = b;
            } else if (key == "areaShadow") {
                // MAX-LIT-SHADOW-SOFTNESS-006: when false, treat the area
                // light as a point source for shadow evaluation. Maps to
                // UsdLuxSphereLight's `treatAsPoint` on the SphereLight
                // branch (RectLight/DiskLight preserve the raw value via
                // the `inputs:vray:shadow:areaShadow` opinion).
                std::string v = val;
                for (auto& c : v) {
                    c = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
                }
                probe.hasAreaShadow = true;
                probe.areaShadow = !(v == "false" || v == "0");
            } else if (key == "shadowColor") {
                // MAX-LIT-SHADOW-SOFTNESS-006: shadow tint as "r,g,b" —
                // three 0..1 floats. Malformed triplets leave the field
                // absent so the writer preserves the pre-006 behavior.
                auto c1 = val.find(',');
                if (c1 != std::string::npos) {
                    auto c2 = val.find(',', c1 + 1);
                    if (c2 != std::string::npos) {
                        float r = std::stof(val.substr(0, c1));
                        float g = std::stof(val.substr(c1 + 1, c2 - c1 - 1));
                        float b = std::stof(val.substr(c2 + 1));
                        // Clamp to a physically-sensible [0, 1] range so
                        // a rogue authored value doesn't lift shadows.
                        auto clamp01 = [](float v) {
                            if (v < 0.f) return 0.f;
                            if (v > 1.f) return 1.f;
                            return v;
                        };
                        probe.hasShadowColor = true;
                        probe.shadowColor[0] = clamp01(r);
                        probe.shadowColor[1] = clamp01(g);
                        probe.shadowColor[2] = clamp01(b);
                    }
                }
            }
        } catch (const std::exception&) {
            // Ignore parse errors — the field stays at its default.
        }
    }
    return probe;
}

// Classifies the light into one of the supported UsdLux prim types based on
// the probed manifest. Returns one of:
//   TfToken("SphereLight") / TfToken("RectLight") / TfToken("DiskLight") /
//   TfToken("DistantLight") / TfToken("DomeLight")
//
// MAX-LIT-003 — surgical bounds of the classifier. Each branch below has an
// exclusive, named authoring contract that MUST hold on every future refactor:
//
//   VRaySun          -> DistantLight  {inputs:angle=0.53°}
//                       — MUST NOT author: radius, width, height, ies:file
//   VRayIES          -> DiskLight     {inputs:radius=size0, shaping:ies:file}
//                       — MUST NOT author: width, height, angle
//   VRayAmbientLight -> DomeLight     {no shape attrs}
//                       — MUST NOT author: radius, width, height, angle, ies:file
//   VRayLight type=0 -> RectLight     {inputs:width=size0, inputs:height=size1}
//                       — MUST NOT author: radius, angle, ies:file
//   VRayLight type=1 -> DomeLight     {no shape attrs}
//                       — MUST NOT author: radius, width, height, angle, ies:file
//   VRayLight type=2 -> SphereLight   {inputs:radius=size0}
//                       — MUST NOT author: width, height, angle, ies:file
//   VRayLight type=3 -> SphereLight   {inputs:radius=size0}  (mesh->sphere fallback)
//                       — MUST NOT author: width, height, angle, ies:file
//   VRayLight type=4 -> DiskLight     {inputs:radius=size0}  (plain disc, NO IES)
//                       — MUST NOT author: shaping:ies:file, width, height, angle
//   unknown          -> RectLight     (V-Ray's out-of-box default; must not drop)
//
// Intensity is normalized to UsdLux's physical (nit-scale) convention via
// `_NormalizeVRayLightIntensity` (MAX-LIT-INTENSITY-UNITS-005). The
// normalization key is `.units` (0..4), NOT the light shape — so the value
// applied is IDENTICAL across every branch above (rect/sphere/disk/distant/dome),
// preserving the original MAX-LIT-003 "no per-branch scaling" invariant. IES
// asset path is written verbatim via SdfAssetPath — no relative rewriting or
// drive-letter normalization.
// A wildcard refactor that widens any of these branches must fail the
// MAX-LIT-003 scope-audit test suite (src/Tests/Integration/test_miris_max_lit_003.py)
// BEFORE landing — that's the safety catch.
TfToken _ClassifyVRayLight(const VRayLightProbe& probe)
{
    const std::string& cn = probe.className;
    // VRaySun -> DistantLight (single directional source).
    if (cn.find("VRaySun") != std::string::npos) {
        return pxr::MaxUsdPrimTypeTokens->DistantLight;
    }
    // VRayIES -> Disk with an IES shaping profile.
    if (cn.find("VRayIES") != std::string::npos) {
        return pxr::MaxUsdPrimTypeTokens->DiskLight;
    }
    // Ambient light — best represented as a DomeLight (soft global fill).
    if (cn.find("Ambient") != std::string::npos) {
        return pxr::MaxUsdPrimTypeTokens->DomeLight;
    }
    // VRayLight — dispatch on `type`.
    if (probe.hasType) {
        switch (probe.type) {
        case 0: // Plane
            return pxr::MaxUsdPrimTypeTokens->RectLight;
        case 1: // Dome
            return pxr::MaxUsdPrimTypeTokens->DomeLight;
        case 2: // Sphere
            return pxr::MaxUsdPrimTypeTokens->SphereLight;
        case 3: // Mesh
            // No first-class UsdLux representation for a mesh light — fall
            // back to a sphere with the mesh's bbox-derived radius. Loses
            // shape fidelity but keeps the light present in the stage.
            return pxr::MaxUsdPrimTypeTokens->SphereLight;
        case 4: // Disc
            return pxr::MaxUsdPrimTypeTokens->DiskLight;
        default:
            break;
        }
    }
    // Unknown VRayLight subtype: prefer RectLight (V-Ray's default `type`
    // out of the box is Plane, so this recovers the common case).
    return pxr::MaxUsdPrimTypeTokens->RectLight;
}

// MAX-LIT-INTENSITY-UNITS-005 — normalize VRayLight's intensity value to
// UsdLux's physical convention (candela / nit-scale, with normalize=true).
//
// Prior to this fix, `GenLight::GetIntensity()` was passed VERBATIM to
// `UsdLuxLight.inputs:intensity`. But VRayLight has a `.units` selector that
// changes the meaning of that scalar entirely — the same multiplier of `2000`
// means 2000 lumens (units=1) OR 2000 nits (units=2) OR 2000 watts (units=3),
// which differ by orders of magnitude in physical brightness. A scoreboard
// authored at `.multiplier=2000, .units=1` (2000 lumens) and a jumbotron at
// `.multiplier=2000, .units=2` (2000 nits) emitted at the SAME USD intensity
// pre-fix, so Karma rendered them identically — both saturating past its
// tone-mapper's headroom for the units=2 case where the value is physically
// plausible, and orders-of-magnitude too bright for the units=1 case where
// the value was total flux, not surface luminance.
//
// USD/UsdLux with `normalize=true` (which the writer sets on every branch —
// see the Write() body's normalize author) treats the intensity scalar as a
// per-unit-area radiance analog: for area lights the value acts as
// cd/m² (nit) scale, which IS the physical convention units=2 already
// represents (lm/m²/sr = cd/m² = nit). Other units need conversion:
//
//   units=0 (default 0-1 color):    pass-through — mode 0 is
//                                    "arbitrary artistic scale", not
//                                    physical; nothing to convert.
//   units=1 (lumens, total flux):   divide by pi to approximate a
//                                    Lambertian emitter's average luminance
//                                    (nit ≈ lumens / (pi * area); with
//                                    normalize=true USD handles area, so
//                                    only the pi factor remains).
//   units=2 (lm/m²/sr = nits):      pass-through — already USD's convention.
//   units=3 (watts, radiant flux):  multiply by the photopic-peak luminous
//                                    efficacy (683 lm/W) to convert to
//                                    lumens, then divide by pi (as units=1).
//   units=4 (W/m²/sr, radiance):    multiply by 683 — radiance -> luminance.
//
// Then clamp to [0, kIntensityCeiling] so a malformed multiplier can't drive
// the light past a value that breaks Karma's tone-mapper. The 10000 nit
// ceiling is empirical: brighter than the peak of the brightest commercial
// LED wall (~5000 nits), lower than values that reliably flat-white every
// tone-map operator. `TestIntensityCeiling.test_ceiling_is_10000` in the
// hython mirror locks this in as a contract; a future Karma tone-mapper
// change may want it bumped.
//
// Negative multipliers clamp to 0 to match Max's non-negative UI convention;
// hasUnits=false (very old V-Ray or a light class with no `.units` property
// at all) falls through to units=0 pass-through so pre-`.units` scenes
// keep their pre-013 behavior.
constexpr float kIntensityPi         = 3.14159265358979323846f;
constexpr float kIntensityPhotopicK  = 683.0f;   // Photopic peak, lm/W.
constexpr float kIntensityCeiling    = 10000.0f; // nit-scale, tone-mapper safe.

float _NormalizeVRayLightIntensity(float multiplier, int units, bool hasUnits)
{
    // Non-physical / absent-manifest -> pass-through (with negative clamp).
    float base = multiplier;
    if (hasUnits) {
        switch (units) {
        case 0: // Default: arbitrary artistic scale.
            base = multiplier;
            break;
        case 1: // Lumens (total luminous flux) -> nits (Lambertian).
            base = multiplier / kIntensityPi;
            break;
        case 2: // lm/m²/sr = cd/m² = nits: already USD's convention.
            base = multiplier;
            break;
        case 3: // Watts -> lumens -> nits.
            base = (multiplier * kIntensityPhotopicK) / kIntensityPi;
            break;
        case 4: // W/m²/sr -> nits via photopic peak.
            base = multiplier * kIntensityPhotopicK;
            break;
        default:
            base = multiplier;
            break;
        }
    }
    if (base < 0.f) {
        base = 0.f;
    }
    if (base > kIntensityCeiling) {
        base = kIntensityCeiling;
    }
    return base;
}

} // namespace

MaxUsdVRayLightWriter::MaxUsdVRayLightWriter(
    const MaxUsdWriteJobContext& jobCtx,
    INode*                       node)
    : MaxUsdPrimWriter(jobCtx, node)
{
}

MaxUsdPrimWriter::ContextSupport MaxUsdVRayLightWriter::CanExport(
    INode*                                node,
    const MaxUsd::USDSceneBuilderOptions& exportArgs)
{
    // [MAX-LIT-DIAG] Reliable runtime diagnostics via MaxUsd::Log (the exporter's own
    // logging facility — captured to opt.LogPath; C++ ofstream to C:\suts is a proven no-op).
    // One-time marker proves the writer is REGISTERED and consulted at all; the per-light
    // marker (gated on LIGHT super-class) shows why CanExport accepts/rejects each light.
    static bool s_vrayProbeRegistered = false;
    if (!s_vrayProbeRegistered) {
        s_vrayProbeRegistered = true;
        MaxUsd::Log::Warn(L"[VRAYLIGHTPROBE] CanExport reached — writer IS registered");
    }
    const bool tl = exportArgs.GetTranslateLights();
    const auto object = node->EvalWorldState(exportArgs.GetResolvedTimeConfig().GetStartTime()).obj;
    const bool objNull = (object == nullptr);
    const bool superIsLight = (!objNull && object->SuperClassID() == LIGHT_CLASS_ID);
    if (superIsLight) {
        MaxUsd::Log::Warn(
            L"[VRAYLIGHTPROBE] light node={0} translateLights={1} scid={2} class={3}",
            node->GetName(),
            tl ? 1 : 0,
            static_cast<int>(object->SuperClassID()),
            static_cast<int>(object->ClassID().PartA()));
    }
    if (!tl) {
        return ContextSupport::Unsupported;
    }
    if (objNull) {
        return ContextSupport::Unsupported;
    }
    if (object->SuperClassID() != LIGHT_CLASS_ID) {
        return ContextSupport::Unsupported;
    }
    // Photometric (Lightscape) lights belong to MaxUsdPhotometricLightWriter — defer to it.
    if (object->IsSubClassOf(LIGHTSCAPE_LIGHT_CLASS)) {
        return ContextSupport::Unsupported;
    }
    // Claim any non-photometric light object as SUPPORTED (not Fallback). This is deliberate:
    // MaxUsdMeshWriter::CanExport returns Fallback for ANY object that CanConvertToType(TriObject)
    // — which many V-Ray lights (VRayLight plane/sphere/disc/mesh, VRayIES) satisfy — and it is
    // registered BEFORE this writer, so among Fallback writers MeshWriter won and 168 of 185
    // lights exported as meshes/xforms instead of UsdLux (only the 17 non-mesh-convertible lights
    // fell through to us). Returning Supported makes FindWriter prefer this writer over every
    // Fallback claimant, so ALL non-photometric lights become UsdLux. The prior class-name gate
    // (matching "vray"+"light" on GetClassName) was dropped earlier as too fragile; GenLight in
    // Write() reads the standard interface and the MAXScript probe fills V-Ray specifics.
    return ContextSupport::Supported;
}

MaxUsd::XformSplitRequirement MaxUsdVRayLightWriter::RequiresXformPrim()
{
    return MaxUsd::XformSplitRequirement::ForOffsetObjects;
}

TfToken MaxUsdVRayLightWriter::GetPrimType()
{
    // Cache the probe as a scratch member? PhotometricLightWriter probes
    // twice (once in GetPrimType, once in Write). Match that pattern for
    // consistency; the MAXScript hop is cheap relative to the Write() body.
    const auto probe = _ProbeVRayLight(GetNode());
    return _ClassifyVRayLight(probe);
}

bool MaxUsdVRayLightWriter::Write(
    UsdPrim&                  targetPrim,
    bool                      applyOffsetTransform,
    const MaxUsd::ExportTime& time)
{
    INode* sourceNode = GetNode();
    if (sourceNode == nullptr) {
        return false;
    }
    const auto  timeVal = time.GetMaxTime();
    const auto  usdTimeCode = time.GetUsdTime();
    const auto  object = sourceNode->EvalWorldState(timeVal).obj;
    if (object == nullptr) {
        return false;
    }

    // GenLight is the standard 3ds Max light base interface — every plugin
    // light (including VRayLight, VRayIES, VRaySun) implements it.
    GenLight* genLight = dynamic_cast<GenLight*>(object);
    const auto probe = _ProbeVRayLight(sourceNode);
    const auto primType = _ClassifyVRayLight(probe);

    // [MAX-LIT-DIAG] Write() only runs if FindWriter selected THIS writer for the node.
    MaxUsd::Log::Warn(
        L"[VRAYLIGHTWRITE] node={0} className={1} primType={2} genLight={3}",
        sourceNode->GetName(),
        MaxUsd::UsdStringToMaxString(probe.className).data(),
        MaxUsd::UsdStringToMaxString(primType.GetString()).data(),
        (genLight != nullptr) ? 1 : 0);

    auto stage = targetPrim.GetStage();
    auto primPath = targetPrim.GetPath();

    // Time-independent properties on the first frame only.
    pxr::UsdLuxBoundableLightBase boundableLight;
    pxr::UsdLuxDistantLight       distantLight;
    pxr::UsdLuxDomeLight          domeLight;

    if (time.IsFirstFrame()) {
        if (primType == pxr::MaxUsdPrimTypeTokens->SphereLight) {
            auto sphere = pxr::UsdLuxSphereLight::Define(stage, primPath);
            // For VRayLight type=Sphere, `size0` carries the sphere radius.
            // For a mesh-light fallback, size0 is the bbox-derived radius.
            float radius = 0.f;
            if (probe.hasSize0 && probe.size0 > 0.f) {
                radius = probe.size0;
            } else {
                radius = 1.f;
            }
            sphere.CreateRadiusAttr().Set(radius, pxr::UsdTimeCode::Default());
            boundableLight = sphere;
        } else if (primType == pxr::MaxUsdPrimTypeTokens->RectLight) {
            auto rect = pxr::UsdLuxRectLight::Define(stage, primPath);
            const float width = (probe.hasSize0 && probe.size0 > 0.f) ? probe.size0 : 1.f;
            const float height = (probe.hasSize1 && probe.size1 > 0.f) ? probe.size1 : 1.f;
            rect.CreateWidthAttr().Set(width, pxr::UsdTimeCode::Default());
            rect.CreateHeightAttr().Set(height, pxr::UsdTimeCode::Default());
            boundableLight = rect;
        } else if (primType == pxr::MaxUsdPrimTypeTokens->DiskLight) {
            auto disk = pxr::UsdLuxDiskLight::Define(stage, primPath);
            // For a VRayIES light, disk radius has no direct source; use
            // 0.1 as a small-but-nonzero default so renderers actually emit.
            const float radius = (probe.hasSize0 && probe.size0 > 0.f) ? probe.size0 : 0.1f;
            disk.CreateRadiusAttr().Set(radius, pxr::UsdTimeCode::Default());
            boundableLight = disk;

            // Attach the IES profile if VRayIES surfaced one.
            if (!probe.iesFile.empty()) {
                pxr::UsdLuxShapingAPI shaping(disk);
                pxr::SdfAssetPath     assetPath(probe.iesFile);
                shaping.CreateShapingIesFileAttr().Set(
                    assetPath, pxr::UsdTimeCode::Default());
            }
        } else if (primType == pxr::MaxUsdPrimTypeTokens->DistantLight) {
            distantLight = pxr::UsdLuxDistantLight::Define(stage, primPath);
            // A physically-modest angular extent so a Karma/Storm delegate
            // renders soft-edged sun shadows out of the box.
            distantLight.CreateAngleAttr().Set(0.53f, pxr::UsdTimeCode::Default());
        } else if (primType == pxr::MaxUsdPrimTypeTokens->DomeLight) {
            domeLight = pxr::UsdLuxDomeLight::Define(stage, primPath);
        }

        // Enable-color-temperature toggle. When the V-Ray probe reports
        // that temperature is the active color driver, forward the choice
        // to USD so downstream renderers use it.
        if (boundableLight) {
            boundableLight.CreateEnableColorTemperatureAttr().Set(
                probe.useTemperature, pxr::UsdTimeCode::Default());
            // normalize=FALSE for area lights (RectLight/DiskLight/SphereLight). Intensity is
            // normalized to a per-area LUMINANCE (nit / cd/m²) by _NormalizeVRayLightIntensity,
            // and a nit is radiance-per-unit-area — which is exactly the normalize=false meaning
            // (emitted radiance = intensity; total power scales with the light's area). This
            // matches V-Ray's area-light model, where the multiplier is surface luminance and a
            // bigger light emits more total light. normalize=TRUE (the previous value) instead
            // holds TOTAL power constant regardless of size, so a large arena fixture spreads its
            // power over a huge area and renders far too dim — the whole room came in ~half
            // brightness. Making this a portable value (luminance + normalize=false) means any
            // Hydra renderer reproduces the intended lighting without external calibration.
            boundableLight.CreateNormalizeAttr().Set(false, pxr::UsdTimeCode::Default());
        } else if (distantLight) {
            distantLight.CreateEnableColorTemperatureAttr().Set(
                probe.useTemperature, pxr::UsdTimeCode::Default());
            distantLight.CreateNormalizeAttr().Set(true, pxr::UsdTimeCode::Default());
        } else if (domeLight) {
            domeLight.CreateEnableColorTemperatureAttr().Set(
                probe.useTemperature, pxr::UsdTimeCode::Default());
            domeLight.CreateNormalizeAttr().Set(true, pxr::UsdTimeCode::Default());
        }

        // Off-state handling — zero-out diffuse + specular when the light
        // is disabled so the USD scene renders the same darkness the 3ds
        // Max viewport shows.
        bool isOn = probe.hasEnabled ? probe.enabled : true;
        if (genLight != nullptr && genLight->GetUseLight() == 0) {
            isOn = false;
        }
        if (!isOn) {
            if (boundableLight) {
                boundableLight.CreateSpecularAttr().Set(0.f, pxr::UsdTimeCode::Default());
                boundableLight.CreateDiffuseAttr().Set(0.f, pxr::UsdTimeCode::Default());
            } else if (distantLight) {
                distantLight.CreateSpecularAttr().Set(0.f, pxr::UsdTimeCode::Default());
                distantLight.CreateDiffuseAttr().Set(0.f, pxr::UsdTimeCode::Default());
            } else if (domeLight) {
                domeLight.CreateSpecularAttr().Set(0.f, pxr::UsdTimeCode::Default());
                domeLight.CreateDiffuseAttr().Set(0.f, pxr::UsdTimeCode::Default());
            }
        }

        // Shadow toggle from GenLight (VRay-side `shadow_on` is exposed
        // through this interface too).
        pxr::UsdPrim primForShadow = boundableLight
            ? boundableLight.GetPrim()
            : (distantLight ? distantLight.GetPrim()
                            : (domeLight ? domeLight.GetPrim() : pxr::UsdPrim()));
        if (primForShadow) {
            pxr::UsdLuxShadowAPI shadowApi
                = pxr::UsdLuxShadowAPI::Apply(primForShadow);
            const bool shadowEnable = (genLight != nullptr) ? (genLight->GetShadow() != 0) : true;
            shadowApi.CreateShadowEnableAttr().Set(
                shadowEnable, pxr::UsdTimeCode::Default());

            // MAX-LIT-SHADOW-SOFTNESS-006 — forward V-Ray shadow-family
            // attrs so downstream renderers (Karma / Storm / Prman) get
            // the soft-area shadow authoring the source scene called for
            // instead of a pin-sharp point-shadow fallback.
            //
            // Two audiences:
            //   (a) stdlib UsdLuxShadowAPI attrs — `shadow:color` is the
            //       only stdlib shadow-family attr V-Ray directly maps
            //       to. Author when the probe surfaced a tint.
            //   (b) custom `inputs:vray:shadow:*` opinions preserve every
            //       V-Ray-specific field for lossless round-trip. Delegates
            //       that opt in read them; delegates that don't leave them
            //       intact. Paired with:
            //   (c) Karma's `karma:light:samplingquality` (int) receives
            //       the same value as `.shadowSubdivs` so Karma actually
            //       raises its sample count when the artist set a high
            //       shadow subdiv — the practical visible fix.
            //   (d) USD-canonical bridges — when `.areaShadow=false` on
            //       a light that maps to SphereLight, set `treatAsPoint`
            //       so any Hydra delegate skips the area-shadow integral.
            //       When `.shadowRadius > 0` on a light that maps to
            //       DiskLight (VRayIES), enlarge the disk's own radius so
            //       the finite-area emitter naturally casts soft shadows.
            //       normalize=true (authored above on every branch) keeps
            //       the radius change from also affecting brightness.
            if (probe.hasShadowColor) {
                shadowApi.CreateShadowColorAttr().Set(
                    pxr::GfVec3f(
                        probe.shadowColor[0],
                        probe.shadowColor[1],
                        probe.shadowColor[2]),
                    pxr::UsdTimeCode::Default());
            }
            if (probe.hasShadowRadius) {
                pxr::UsdAttribute vrRadius = primForShadow.CreateAttribute(
                    pxr::TfToken("inputs:vray:shadow:radius"),
                    pxr::SdfValueTypeNames->Float,
                    /*custom=*/false);
                vrRadius.Set(probe.shadowRadius, pxr::UsdTimeCode::Default());
            }
            if (probe.hasShadowBias) {
                pxr::UsdAttribute vrBias = primForShadow.CreateAttribute(
                    pxr::TfToken("inputs:vray:shadow:bias"),
                    pxr::SdfValueTypeNames->Float,
                    /*custom=*/false);
                vrBias.Set(probe.shadowBias, pxr::UsdTimeCode::Default());
            }
            if (probe.hasShadowSubdivs) {
                pxr::UsdAttribute vrSub = primForShadow.CreateAttribute(
                    pxr::TfToken("inputs:vray:shadow:subdivs"),
                    pxr::SdfValueTypeNames->Int,
                    /*custom=*/false);
                vrSub.Set(probe.shadowSubdivs, pxr::UsdTimeCode::Default());
                // Karma-visible sample-count mirror. `karma:light:samplingquality`
                // is the delegate-registered token that raises Karma's per-light
                // shadow sample count at render time.
                pxr::UsdAttribute karmaQ = primForShadow.CreateAttribute(
                    pxr::TfToken("karma:light:samplingquality"),
                    pxr::SdfValueTypeNames->Int,
                    /*custom=*/false);
                karmaQ.Set(probe.shadowSubdivs, pxr::UsdTimeCode::Default());
            }
            if (probe.hasAreaShadow) {
                pxr::UsdAttribute vrArea = primForShadow.CreateAttribute(
                    pxr::TfToken("inputs:vray:shadow:areaShadow"),
                    pxr::SdfValueTypeNames->Bool,
                    /*custom=*/false);
                vrArea.Set(probe.areaShadow, pxr::UsdTimeCode::Default());
                // USD-canonical bridge for SphereLight only — Rect/Disk
                // lack a `treatAsPoint` in the stdlib. Preserving the raw
                // value via `inputs:vray:shadow:areaShadow` above covers
                // the Rect/Disk cases losslessly.
                if (!probe.areaShadow
                    && primType == pxr::MaxUsdPrimTypeTokens->SphereLight) {
                    pxr::UsdLuxSphereLight sphere(primForShadow);
                    if (sphere) {
                        sphere.CreateTreatAsPointAttr().Set(
                            true, pxr::UsdTimeCode::Default());
                    }
                }
            }
            // Karma-visible shadow softness for VRayIES / VRayLight-disc.
            // Enlarge the DiskLight's radius by shadowRadius so the finite-
            // area emitter casts soft shadows in any Hydra renderer. The
            // authored `normalize=true` above keeps this from changing
            // brightness. Total radius is clamped to 100 world units so a
            // rogue authored value can't explode the light's footprint.
            if (probe.hasShadowRadius && probe.shadowRadius > 0.f
                && primType == pxr::MaxUsdPrimTypeTokens->DiskLight) {
                pxr::UsdLuxDiskLight disk(primForShadow);
                if (disk) {
                    float existing = 0.f;
                    if (auto radAttr = disk.GetRadiusAttr()) {
                        radAttr.Get(&existing, pxr::UsdTimeCode::Default());
                    }
                    float bumped = existing + probe.shadowRadius;
                    if (bumped > 100.f) {
                        bumped = 100.f;
                    }
                    disk.CreateRadiusAttr().Set(
                        bumped, pxr::UsdTimeCode::Default());
                }
            }
        }
    }

    // Refetch a handle for the animated-attribute pass on later frames.
    if (!boundableLight && !distantLight && !domeLight) {
        if (primType == pxr::MaxUsdPrimTypeTokens->DistantLight) {
            distantLight = pxr::UsdLuxDistantLight::Get(stage, primPath);
        } else if (primType == pxr::MaxUsdPrimTypeTokens->DomeLight) {
            domeLight = pxr::UsdLuxDomeLight::Get(stage, primPath);
        } else {
            boundableLight = pxr::UsdLuxBoundableLightBase::Get(stage, primPath);
        }
    }

    // Color + intensity. GenLight gives us the raw multiplier; VRayLight's
    // `.units` selector determines whether that scalar means lumens, nits,
    // watts, or a non-physical scale — see `_NormalizeVRayLightIntensity`
    // above for the full derivation. MAX-LIT-INTENSITY-UNITS-005 routes
    // every branch (Rect/Sphere/Disk/Distant/Dome) through the same
    // normalizer so a 2000-lumen scoreboard and a 2000-nit jumbotron no
    // longer emit at identical USD intensities. The temperature branch
    // mirrors PhotometricLightWriter so downstream consumers treat V-Ray
    // and Autodesk photometric lights identically.
    if (genLight != nullptr) {
        Interval  ivColor = FOREVER;
        Interval  ivIntensity = FOREVER;
        const Point3 rgb = genLight->GetRGBColor(timeVal, ivColor);
        const float  rawIntensity = genLight->GetIntensity(timeVal, ivIntensity);
        const float  intensity = _NormalizeVRayLightIntensity(
            rawIntensity, probe.units, probe.hasUnits);

        const pxr::GfVec3f usdColor { rgb[0], rgb[1], rgb[2] };
        if (boundableLight) {
            boundableLight.CreateColorAttr().Set(usdColor, usdTimeCode);
            boundableLight.CreateIntensityAttr().Set(intensity, usdTimeCode);
        } else if (distantLight) {
            distantLight.CreateColorAttr().Set(usdColor, usdTimeCode);
            distantLight.CreateIntensityAttr().Set(intensity, usdTimeCode);
        } else if (domeLight) {
            domeLight.CreateColorAttr().Set(usdColor, usdTimeCode);
            domeLight.CreateIntensityAttr().Set(intensity, usdTimeCode);
        }
    }

    if (probe.hasTemperature && probe.useTemperature) {
        const float clampedKelvin = std::min(std::max(1000.f, probe.temperature), 10000.f);
        if (boundableLight) {
            boundableLight.CreateColorTemperatureAttr().Set(clampedKelvin, usdTimeCode);
        } else if (distantLight) {
            distantLight.CreateColorTemperatureAttr().Set(clampedKelvin, usdTimeCode);
        } else if (domeLight) {
            domeLight.CreateColorTemperatureAttr().Set(clampedKelvin, usdTimeCode);
        }
        if (clampedKelvin != probe.temperature) {
            MaxUsd::Log::Warn(
                L"V-Ray light '{0}' color temperature clamped from '{1}' to '{2}' to match USD "
                L"specifications.",
                sourceNode->GetName(),
                probe.temperature,
                clampedKelvin);
        }
    }

    return true;
}

PXR_NAMESPACE_CLOSE_SCOPE
