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
#include <maxscript/maxscript.h>
#include <maxscript/foundation/functions.h>
#include <maxscript/util/listener.h>

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
};

// Test-only mirror of the class-name test used by CanExport(). Kept in one
// place so the writer, the classifier, and the Python validator all speak
// the same manifest of V-Ray light class names.
bool _IsVRayLightClassName(const std::string& cn)
{
    // V-Ray exposes several light classes across releases: `VRayLight`,
    // `VRayIES`, `VRaySun`, and (rarer, older) `VRayAmbientLight`. Match on
    // a case-insensitive substring so future subclass renames don't silently
    // regress the fix.
    if (cn.empty()) {
        return false;
    }
    std::string lower;
    lower.reserve(cn.size());
    for (auto ch : cn) {
        lower.push_back(static_cast<char>(std::tolower(static_cast<unsigned char>(ch))));
    }
    return lower.find("vray") != std::string::npos && lower.find("light") != std::string::npos
        || lower.find("vrayies") != std::string::npos
        || lower.find("vraysun") != std::string::npos;
}

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
// Intensity is passed through VERBATIM from GenLight::GetIntensity() on every
// branch — no unit conversion, no scaling. IES asset path is written verbatim
// via SdfAssetPath — no relative rewriting or drive-letter normalization.
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
    if (!exportArgs.GetTranslateLights()) {
        return ContextSupport::Unsupported;
    }
    const auto object = node->EvalWorldState(exportArgs.GetResolvedTimeConfig().GetStartTime()).obj;
    if (object == nullptr) {
        return ContextSupport::Unsupported;
    }
    // Only claim actual light-super-class objects. VRayIES / VRayLight /
    // VRaySun all inherit from LIGHT_CLASS_ID; anything that isn't a light
    // (e.g. a VRayProxy geometry that happens to have "VRay" in the name)
    // is rejected here.
    if (object->SuperClassID() != LIGHT_CLASS_ID) {
        return ContextSupport::Unsupported;
    }
    // The stock PhotometricLightWriter already handles LightscapeLight-
    // derived Autodesk photometric lights; we only claim things it hasn't.
    // LIGHTSCAPE_LIGHT_CLASS is defined in <lslights.h> — since we don't
    // include it here, defer to the class-name check instead.
    MSTR className;
    object->GetClassName(className);
    const auto cn = MaxUsd::MaxStringToUsdString(className.data());
    if (!_IsVRayLightClassName(cn)) {
        return ContextSupport::Unsupported;
    }
    return ContextSupport::Fallback;
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
            boundableLight.CreateNormalizeAttr().Set(true, pxr::UsdTimeCode::Default());
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
            pxr::UsdLuxShadowAPI shadowApi(primForShadow);
            const bool shadowEnable = (genLight != nullptr) ? (genLight->GetShadow() != 0) : true;
            shadowApi.CreateShadowEnableAttr().Set(
                shadowEnable, pxr::UsdTimeCode::Default());
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

    // Color + intensity. GenLight gives us both in canonical units; the
    // temperature branch mirrors PhotometricLightWriter so downstream
    // consumers treat V-Ray and Autodesk photometric lights identically.
    if (genLight != nullptr) {
        Interval  ivColor = FOREVER;
        Interval  ivIntensity = FOREVER;
        const Point3 rgb = genLight->GetRGBColor(timeVal, ivColor);
        const float  intensity = genLight->GetIntensity(timeVal, ivIntensity);

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
