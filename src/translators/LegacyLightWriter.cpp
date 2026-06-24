//
// Copyright 2026 Miris Inc. (fork: 3dsmax-usd)
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
#include "LegacyLightWriter.h"

#include <MaxUsd/Translators/primWriter.h>
#include <MaxUsd/Translators/writeJobContext.h>
#include <MaxUsd/Utilities/Logging.h>
#include <MaxUsd/Utilities/MaxSupportUtils.h>

#include <pxr/base/gf/vec3f.h>
#include <pxr/base/tf/token.h>
#include <pxr/pxr.h>
#include <pxr/usd/usd/timeCode.h>
#include <pxr/usd/usdLux/boundableLightBase.h>
#include <pxr/usd/usdLux/domeLight.h>
#include <pxr/usd/usdLux/nonboundableLightBase.h>
#include <pxr/usd/usdLux/shadowAPI.h>
#include <pxr/usd/usdLux/sphereLight.h>

// 3ds Max SDK headers. OMNI_LIGHT_CLASS_ID lives in <object.h>; the
// legacy SKY_LIGHT_CLASS_ID lives in <lslights.h> alongside the
// photometric LIGHTSCAPE_LIGHT_CLASS that the existing
// PhotometricLightWriter handles. Pulling them in symbolically (rather
// than hardcoding the Class_ID literals) keeps us robust to the SDK
// reshuffles that have happened across Max releases.
#include <genlight.h>
#include <lslights.h>
#include <object.h>

PXR_NAMESPACE_OPEN_SCOPE

namespace {

// Tighter-than-photometric radius for "treatAsPoint" sphere lights.
// Mirrors the value PhotometricLightWriter uses for the point-light case
// (see PhotometricLightWriter.cpp line ~196). Renderers that ignore the
// treatAsPoint hint still see a finite emitter and avoid the degenerate
// "radius==0 → divide-by-area" failure mode some samplers exhibit.
constexpr float kTreatAsPointRadius = 0.001f;

// Empirical normalization factor that brings 3ds Max's "intensity"
// (multiplier × RGB magnitude in the legacy lights' unitless space) to
// the same approximate energy a UsdLux normalized intensity expects.
// This is the same shape as the
// `lightIntensity / 1500 * M_PI / (sysUnit^2)` block in
// PhotometricLightWriter::Write() but without the photometric
// candela-specific scaling; the legacy GenLight intensity is already
// unitless, so we just rescale into a render-friendly range. Treated as
// a tunable: round-trip parity isn't possible without the renderer's
// scene tone-mapping, so we aim for "scene looks lit, not blown out, not
// dark".
constexpr float kLegacyIntensityScale = 1.0f;

// Generic helper to author all the universal-light attributes the
// LightObject base interface exposes (color, intensity, on/off,
// shadow enable + color). Both the SphereLight (Omnilight) and the
// DomeLight (Skylight) inherit `UsdLuxLightAPI`, so we accept the
// boundable / nonboundable union via the underlying UsdPrim and apply
// the LightAPI directly.
void _AuthorCommonLightProps(
    const UsdPrim&    lightPrim,
    GenLight*         genLight,
    LightObject*      lightObject,
    const TimeValue   timeVal,
    const UsdTimeCode usdTimeCode)
{
    if (!lightPrim) {
        return;
    }

    // Color + Intensity. GenLight reaches deeper than LightObject and
    // exposes the multiplier / use-light gates, so prefer it when
    // available; fall back to the EvalLightState() shape for objects
    // that only implement LightObject (e.g. Skylight).
    GfVec3f color { 1.0f, 1.0f, 1.0f };
    float   intensity { 1.0f };
    bool    lightOn { true };
    if (genLight) {
        Point3 rgb = genLight->GetRGBColor(timeVal);
        color = GfVec3f(rgb[0], rgb[1], rgb[2]);
        intensity = genLight->GetIntensity(timeVal);
        // GetUseLight returns BOOL (int); 0 = "off in viewport / render".
        lightOn = (genLight->GetUseLight() != 0);
    } else {
        LightState ls;
        Interval   valid = FOREVER;
        if (lightObject->EvalLightState(timeVal, valid, &ls) != REF_SUCCEED) {
            // Fall back to neutral white if EvalLightState refuses.
            ls.color = Point3(1.0f, 1.0f, 1.0f);
            ls.intens = 1.0f;
            ls.on = TRUE;
        }
        color = GfVec3f(ls.color.x, ls.color.y, ls.color.z);
        intensity = ls.intens;
        lightOn = (ls.on != FALSE);
    }

    // When the light is gated off in 3ds Max, USD's closest equivalent
    // is `intensity = 0`. We deliberately keep the `inputs:color` value
    // around so a round-trip importer can still recover the artist's
    // tint; zeroing intensity is the smallest-knob switch that produces
    // the right rendered behavior (no contribution to the scene).
    if (!lightOn) {
        intensity = 0.0f;
    }

    auto lightAPI = pxr::UsdLuxLightAPI(lightPrim);
    lightAPI.CreateColorAttr().Set(color, usdTimeCode);
    lightAPI.CreateIntensityAttr().Set(
        intensity * kLegacyIntensityScale, usdTimeCode);
    // Normalized intensity is the convention the photometric writer
    // adopts (see PhotometricLightWriter.cpp:272) and matches what most
    // Hydra delegates expect for area lights so the visible brightness
    // doesn't depend on the emitter's size.
    lightAPI.CreateNormalizeAttr().Set(true, usdTimeCode);

    // Shadow API — both shadow-on flag and shadow color, when available.
    if (genLight) {
        const bool shadow = (genLight->GetShadow() != 0);
        pxr::UsdLuxShadowAPI shadowAPI = pxr::UsdLuxShadowAPI::Apply(lightPrim);
        shadowAPI.CreateShadowEnableAttr().Set(shadow, usdTimeCode);
        Point3 shadColor = genLight->GetShadColor(timeVal);
        shadowAPI.CreateShadowColorAttr().Set(
            GfVec3f(shadColor.x, shadColor.y, shadColor.z), usdTimeCode);
    }
}

} // namespace

MaxUsdLegacyLightWriter::MaxUsdLegacyLightWriter(
    const MaxUsdWriteJobContext& jobCtx,
    INode*                       node)
    : MaxUsdPrimWriter(jobCtx, node)
{
}

MaxUsdPrimWriter::ContextSupport MaxUsdLegacyLightWriter::CanExport(
    INode*                                node,
    const MaxUsd::USDSceneBuilderOptions& exportArgs)
{
    // Honor the global Translate Lights export option, exactly like
    // PhotometricLightWriter (see PhotometricLightWriter.cpp:51-53).
    if (!exportArgs.GetTranslateLights()) {
        return ContextSupport::Unsupported;
    }
    const auto object
        = node->EvalWorldState(exportArgs.GetResolvedTimeConfig().GetStartTime()).obj;
    if (object == nullptr) {
        return ContextSupport::Unsupported;
    }

    // PhotometricLightWriter already claims LIGHTSCAPE_LIGHT_CLASS
    // (returns Fallback for any subclass). Defer to it.
    if (object->IsSubClassOf(LIGHTSCAPE_LIGHT_CLASS)) {
        return ContextSupport::Unsupported;
    }

    const Class_ID classId = object->ClassID();

    // In-scope legacy lights for this bite (MAX-LIT-001). Spot /
    // Directional lights remain Unsupported here so that a follow-on
    // bite that adds them can claim them without surprising overlap.
    if (classId == OMNI_LIGHT_CLASS_ID || classId == SKY_LIGHT_CLASS_ID) {
        return ContextSupport::Fallback;
    }

    return ContextSupport::Unsupported;
}

MaxUsd::XformSplitRequirement MaxUsdLegacyLightWriter::RequiresXformPrim()
{
    // DomeLight (Skylight) is intentionally placed at world origin in
    // most renderers — its incoming direction matters but its position
    // does not. Match PhotometricLightWriter's default of
    // ForOffsetObjects for the SphereLight (Omnilight) path; the dome
    // case is harmless because the dome ignores translation anyway.
    return MaxUsd::XformSplitRequirement::ForOffsetObjects;
}

TfToken MaxUsdLegacyLightWriter::GetPrimType()
{
    const auto startTime = GetExportArgs().GetResolvedTimeConfig().GetStartTime();
    const auto object = GetNode()->EvalWorldState(startTime).obj;
    if (object == nullptr) {
        return pxr::MaxUsdPrimTypeTokens->SphereLight;
    }
    const Class_ID classId = object->ClassID();
    if (classId == SKY_LIGHT_CLASS_ID) {
        return pxr::MaxUsdPrimTypeTokens->DomeLight;
    }
    // Omnilight (and conservative fallback for any other legacy light
    // we later add to the CanExport gate).
    return pxr::MaxUsdPrimTypeTokens->SphereLight;
}

bool MaxUsdLegacyLightWriter::Write(
    UsdPrim&                  targetPrim,
    bool                      applyOffsetTransform,
    const MaxUsd::ExportTime& time)
{
    INode* sourceNode = GetNode();
    if (sourceNode == nullptr) {
        return false;
    }

    const TimeValue timeVal = time.GetMaxTime();
    Object* const   object = sourceNode->EvalWorldState(timeVal).obj;
    if (object == nullptr) {
        return false;
    }

    LightObject* lightObject = dynamic_cast<LightObject*>(object);
    if (lightObject == nullptr) {
        // CanExport claimed this node so this is a real error — the
        // ClassID matched OMNI_LIGHT_CLASS_ID or SKY_LIGHT_CLASS_ID but
        // ConvertToType has failed to give us a LightObject. Log and
        // skip rather than crash.
        MaxUsd::Log::Warn(
            L"Legacy light '{0}' did not resolve to a LightObject; skipping export.",
            sourceNode->GetName());
        return false;
    }
    // GenLight is the standard-light family base. Skylight inherits
    // from LightObject directly, NOT from GenLight, so this dynamic
    // cast is allowed to return null and the helper handles both shapes.
    GenLight* genLight = dynamic_cast<GenLight*>(lightObject);

    auto       stage = targetPrim.GetStage();
    const auto primPath = targetPrim.GetPath();
    const auto targetType = GetPrimType();
    const auto usdTimeCode = time.GetUsdTime();

    UsdPrim concreteLightPrim;

    if (time.IsFirstFrame()) {
        if (targetType == pxr::MaxUsdPrimTypeTokens->DomeLight) {
            // Skylight → UsdLuxDomeLight. We only author the universal
            // light attributes (color, intensity, normalize) — IES sky
            // models and HDR environment maps are not covered by this
            // bite and remain a follow-on.
            pxr::UsdLuxDomeLight domeLight = pxr::UsdLuxDomeLight::Define(stage, primPath);
            concreteLightPrim = domeLight.GetPrim();
        } else {
            // Omnilight → UsdLuxSphereLight with `treatAsPoint = true`.
            // This matches what PhotometricLightWriter does for the
            // LS_POINT_LIGHT_ID case (PhotometricLightWriter.cpp:188-201)
            // and the round-trip path the LightReader uses (see
            // TranslatorLight.cpp:58-63 which reads `treatAsPoint` back
            // into LS_POINT_LIGHT_ID on import).
            pxr::UsdLuxSphereLight sphereLight = pxr::UsdLuxSphereLight::Define(stage, primPath);
            sphereLight.CreateRadiusAttr().Set(kTreatAsPointRadius, pxr::UsdTimeCode::Default());
            sphereLight.CreateTreatAsPointAttr().Set(true, pxr::UsdTimeCode::Default());
            concreteLightPrim = sphereLight.GetPrim();
        }
    } else {
        concreteLightPrim = stage->GetPrimAtPath(primPath);
    }

    _AuthorCommonLightProps(
        concreteLightPrim, genLight, lightObject, timeVal, usdTimeCode);

    return true;
}

PXR_NAMESPACE_CLOSE_SCOPE
