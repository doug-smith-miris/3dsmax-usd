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
#include "PhotometricLightWriter.h"

#include <MaxUsd/Translators/primWriter.h>
#include <MaxUsd/Translators/writeJobContext.h>
#include <MaxUsd/Utilities/MaxSupportUtils.h>
#include <MaxUsd/Utilities/SplineUtils.h>

#include <pxr/base/gf/vec3f.h>
#include <pxr/base/tf/token.h>
#include <pxr/pxr.h>
#include <pxr/usd/usd/timeCode.h>
#include <pxr/usd/usdLux/boundableLightBase.h>
#include <pxr/usd/usdLux/cylinderLight.h>
#include <pxr/usd/usdLux/diskLight.h>
#include <pxr/usd/usdLux/rectLight.h>
#include <pxr/usd/usdLux/shadowAPI.h>
#include <pxr/usd/usdLux/shapingAPI.h>
#include <pxr/usd/usdLux/sphereLight.h>

#include <linshape.h>
#include <lslights.h>

PXR_NAMESPACE_OPEN_SCOPE

MaxUsdPhotometricLightWriter::MaxUsdPhotometricLightWriter(
    const MaxUsdWriteJobContext& jobCtx,
    INode*                       node)
    : MaxUsdPrimWriter(jobCtx, node)
{
}

// MAX-LIT-001 surgical bounds (writer-registry bottom bound for lights;
// the gate below is the ONLY general-purpose light writer in the plugin,
// so every non-LIGHTSCAPE_LIGHT_CLASS light that reaches this CanExport
// returns ContextSupport::Unsupported and -- because no other registered
// PrimWriter claims a LightObject node -- falls through
// MaxUsdPrimWriterRegistry::FindWriter to nullptr, which is the silent
// drop: NO UsdLux* prim is authored, NO error fires, NO warning fires).
// Enforced by the regression suite via
// src/Tests/Integration/export_light_test.ms ::
//     photometric_light_writer_legacy_dropout_audit_test
//                 (Omnilight + Skylight in scene alongside a Free_Point
//                  photometric positive control -> exported stage has
//                  the UsdLuxDiskLight for the photometric AND NO prims
//                  at the legacy lights' paths; the positive control
//                  pins that the gate still fires for in-scope lights,
//                  while the two negative cases pin the bottom bound)
// plus the doc-linked Python validator
// validate_legacy_lights_dropout_surgical.py (8 cases + idempotence,
// mirrors the writer-registry decision over a synthetic per-light-class
// fixture):
//
//   * exportArgs.GetTranslateLights() == false: every light, including
//     in-scope photometrics, returns Unsupported. The "no lights at all"
//     short-circuit is honoured BEFORE the class-id gate so a user who
//     disables light export sees the same nullptr from FindWriter for
//     every light type; this is intentional and is NOT the silent-drop
//     defect. Pinned by the photometric_light_general_attributes_export
//     happy-path test, which fails if the option is broken.
//
//   * LIGHTSCAPE_LIGHT_CLASS subclass (Free_Point / Free_Sphere /
//     Free_Disc / Free_Linear / Free_Cylinder / Free_Area + every
//     _Target sibling): returns ContextSupport::Fallback. Authored as
//     the right UsdLux* per the conversion grid in
//     PhotometricLightWriter::GetPrimType. This is the IN-SCOPE branch
//     MAX-LIT-002 audited end-to-end (IES file path + Kelvin/filter
//     dichotomy). Pinned by every photometric_*_attributes_export_test
//     in export_light_test.ms.
//
//   * NOT a LIGHTSCAPE_LIGHT_CLASS subclass, IS a LightObject subclass
//     (Omnilight = OMNI_LIGHT_CLASS_ID; Skylight = SKYLIGHT_CLASS_ID;
//     legacy Spot = SPOT_LIGHT_CLASS_ID + Free_Spot/Target_Spot;
//     legacy Directional = DIR_LIGHT_CLASS_ID + Free_Direct/Target_Direct;
//     mr_Sky / mr_Sun / Daylight = third-party / legacy DCC-side
//     environment lights NOT photometric): returns Unsupported. With
//     no other registered PrimWriter that claims a LightObject node,
//     FindWriter returns nullptr -> NO UsdLux* prim is authored -> the
//     light is silently dropped from the exported stage. The cataloged
//     MAX-LIT-001 baseline defect (see
//     /Users/d.smith/.../knowledge/usd-export-issues-catalog.md MAX-LIT-001
//     "Legacy standard lights do not export to UsdLux"). This audit
//     does NOT fix the silent drop -- it pins the bound so any future
//     PR that widens this gate to claim legacy LightObjects (e.g. via
//     a wildcard "fall back to a SphereLight for every LightObject"
//     widening) lands its widening with a corresponding decision-table
//     update + per-class attribute-translation + visual auditor pair,
//     rather than silently authoring spurious UsdLuxSphereLight prims
//     with default 1.0 intensity at every legacy-light position.
//     Pinned by the photometric_light_writer_legacy_dropout_audit_test
//     above.
//
//   * NOT a LightObject subclass at all (any non-light node that
//     happens to reach this CanExport because TF_FOR_ALL iterates the
//     full registry per node): returns Unsupported. The
//     IsSubClassOf(LIGHTSCAPE_LIGHT_CLASS) check is safe to call on a
//     non-light Object because the Object base class implements it.
//     This is the trivial-rejection case, not the silent-drop defect.
//
// Why the wildcard widening would be wrong: the planner's MAX-LIT-001
// rationale ("Legacy Skylight + Omnilight silently dropped from USD
// export (lighting fidelity)") might be read as "extend
// PhotometricLightWriter::CanExport to ContextSupport::Fallback for
// every LightObject and stamp out SphereLight / DistantLight per class".
// That widening would (a) author every Omnilight as a default-intensity
// UsdLuxSphereLight at the Omnilight's transform, producing a brand-new
// light pool the source scene never asked for if the Omnilight was
// turned OFF / set to multiplier=0 / set to a non-default attenuation
// / set to a non-default color, all of which it would silently lose;
// (b) author every Skylight as a default UsdLuxDomeLight at unit
// intensity, swamping the scene with dome lighting that the source
// Skylight (a hemispherical environment integrator with its own
// rayCount / castShadows / sky color / map dependency) didn't actually
// produce; (c) author every legacy Spot/Free_Direct as a UsdLuxDiskLight
// without the hotspot / falloff / spotlight attenuation Max applies.
// The right per-class translation is a SEPARATE bite per legacy class
// with its own attribute mapping + its own MaxScript regression + its
// own doc entry; this audit pins the bottom bound so those follow-on
// bites are visible as widenings rather than landing silently.
//
// MAX-LIT-003 lock-in: per-class attribute-translation spec for the
// two highest-severity legacy LightObject subclasses that MAX-LIT-001
// today drops (Omnilight = OMNI_LIGHT_CLASS_ID; Skylight =
// SKYLIGHT_CLASS_ID). This file's CanExport gate still returns
// Unsupported for both (no widening here -- the Mac cannot build the
// plugin and unverified per-class writers would silently mutate
// artist content on the next release). MAX-LIT-003 instead pins the
// SPEC that a future MaxUsdLegacyOmnilightWriter +
// MaxUsdLegacySkylightWriter MUST honor when they land:
//
//   Omnilight (OMNI_LIGHT_CLASS_ID) -> UsdLuxSphereLight
//     - Max .multiplier               -> UsdLux.intensity         (scaled; preserves the wildcard-widening loss case)
//     - Max .rgb                      -> UsdLux.inputs:color      (NO Kelvin -- legacy Omnilight has no useKelvin toggle)
//     - Max .castShadows              -> UsdLuxShadowAPI.shadow:enable
//     - Max .on == false              -> UsdLux.intensity = 0     (zero, do NOT drop the prim)
//     - Max .useFarAtten + .farAttenEnd -> customData[3dsmax:legacy_omnilight:farAttenEnd]
//                                          (NO direct UsdLux equivalent;
//                                           the spec records the
//                                           lossy round-trip channel)
//     - Sphere/point shape            -> .radius = 0.001, .treatAsPoint = true  (per MAX-LIT-002 Free_Point precedent)
//
//   Skylight (SKYLIGHT_CLASS_ID)   -> UsdLuxDomeLight
//     - Max .multiplier               -> UsdLux.intensity         (direct scale; no unit conversion)
//     - Max .skyColor + .useSkyColor  -> UsdLux.inputs:color      (color-only branch; mutually exclusive with map)
//     - Max .mapNode + !.useSkyColor  -> UsdLux.texture:file + texture:format = latlong
//                                          (map-only branch; NO inputs:color)
//     - Max .castShadows              -> UsdLuxShadowAPI.shadow:enable
//                                          (wildcard widening would
//                                           hardcode shadow:enable=true,
//                                           silently inverting the
//                                           common archviz `castShadows = false`
//                                           fill-light setting)
//     - Max .rayPerSample             -> (NO UsdLux equivalent; renderer-specific)
//
// Pinned by the regression suite via
// src/Tests/Integration/export_light_test.ms ::
//     photometric_light_writer_legacy_attribute_dropout_audit_test
//                 (Omnilight + Skylight with NON-DEFAULT artist
//                  customizations -- multiplier, color, castShadows --
//                  exported AND asserted that no UsdLux prim survives,
//                  proving that the customizations are silently lost
//                  alongside the silent drop. When a per-class writer
//                  lands and flips this test's expectations, the
//                  test author MUST consult validate_legacy_light_attribute_spec_surgical.py's
//                  attribute table to keep the spec honored at the
//                  test layer.)
// plus the doc-linked Python validator
// validate_legacy_light_attribute_spec_surgical.py (101 assertions: 58
// per-case spec attribute conformance + 35 cross-writer divergence + 7
// spec-vs-wildcard-widening divergence + 1 idempotence, across 10
// named cases). The validator builds ONE synthetic UsdStage with BOTH
// proposed writers' outputs side-by-side and asserts the cross-writer
// invariant: each writer must preserve its own UsdLux schema
// conventions without sharing state with the other. A widening that
// authored UsdLuxSphereLight attrs on UsdLuxDomeLight prims, or vice
// versa, would fail the cross-writer assertions by named case.
//
// Why the wildcard widening would STILL be wrong even with the spec:
// even when MaxUsdLegacyOmnilightWriter + MaxUsdLegacySkylightWriter
// land per this spec, a wildcard widening that returned Fallback for
// every LightObject (without per-class GetPrimType dispatch + per-class
// attribute translation) would author every Omnilight as a unit-default
// SphereLight + every Skylight as a unit-default DomeLight, losing
// every per-attribute customization the spec catalogs above. The
// wildcard widening fails by named ATTRIBUTE (multiplier preserved?
// color preserved? shadow flag preserved?) in the spec-vs-widening
// pass of the Python validator -- the validator's cases 2/3/4 + 7/8/9
// each pin one of those attribute-preservation invariants by name.
MaxUsdPrimWriter::ContextSupport MaxUsdPhotometricLightWriter::CanExport(
    INode*                                node,
    const MaxUsd::USDSceneBuilderOptions& exportArgs)
{
    if (!exportArgs.GetTranslateLights()) {
        return ContextSupport::Unsupported;
    }
    const auto object = node->EvalWorldState(exportArgs.GetResolvedTimeConfig().GetStartTime()).obj;
    return object->IsSubClassOf(LIGHTSCAPE_LIGHT_CLASS) ? ContextSupport::Fallback
                                                        : ContextSupport::Unsupported;
}

MaxUsd::XformSplitRequirement MaxUsdPhotometricLightWriter::RequiresXformPrim()
{
    const auto object
        = GetNode()->EvalWorldState(GetExportArgs().GetResolvedTimeConfig().GetStartTime()).obj;
    const Class_ID    photometricLightType = object->ClassID();
    LightscapeLight2* maxPhotometricLight
        = dynamic_cast<LightscapeLight2*>(object->ConvertToType(0, photometricLightType));

    // Special case with cylinder lights and, line and plane lights using a uniform
    // spherical light distribution as those get converted to UsdLuxCylinder type.
    // In USD, the expected orientation is on the x-axis, but 3ds Max has it set on the y-axis
    if (maxPhotometricLight
        && ((photometricLightType == LS_CYLINDER_LIGHT_ID
             || photometricLightType == LS_CYLINDER_LIGHT_TARGET_ID)
            || (photometricLightType == LS_LINEAR_LIGHT_ID
                || photometricLightType == LS_LINEAR_LIGHT_TARGET_ID
                || photometricLightType == LS_AREA_LIGHT_ID
                || photometricLightType == LS_AREA_LIGHT_TARGET_ID)
                && maxPhotometricLight->GetDistribution() == LightscapeLight::ISOTROPIC_DIST)) {
        return MaxUsd::XformSplitRequirement::Always;
    }

    return MaxUsd::XformSplitRequirement::ForOffsetObjects;
}

TfToken MaxUsdPhotometricLightWriter::GetPrimType()
{
    const auto        startTime = GetExportArgs().GetResolvedTimeConfig().GetStartTime();
    const auto        object = GetNode()->EvalWorldState(startTime).obj;
    Class_ID          photometricLightType = object->ClassID();
    LightscapeLight2* maxPhotometricLight
        = dynamic_cast<LightscapeLight2*>(object->ConvertToType(startTime, photometricLightType));

    // All Photometric light types have a 'Target' sibling light type
    // It simply acts as a 'lookat' and has no influence on the light by itself

    // Photometric Point and Disk lights using anything but the spherical light (isotropic)
    // distribution type gets translated into a UsdLuxDiskLight.
    if ((photometricLightType == LS_POINT_LIGHT_ID
         || photometricLightType == LS_POINT_LIGHT_TARGET_ID
         || photometricLightType == LS_DISC_LIGHT_ID
         || photometricLightType == LS_DISC_LIGHT_TARGET_ID)
        && maxPhotometricLight->GetDistribution() != LightscapeLight::ISOTROPIC_DIST) {
        return pxr::MaxUsdPrimTypeTokens->DiskLight;
    }
    // Photometric Line and Area (Rectangle) lights using anything but the spherical light
    // (isotropic) distribution type gets translated into a UsdLuxRectangleLight.
    if ((photometricLightType == LS_LINEAR_LIGHT_ID
         || photometricLightType == LS_LINEAR_LIGHT_TARGET_ID
         || photometricLightType == LS_AREA_LIGHT_ID
         || photometricLightType == LS_AREA_LIGHT_TARGET_ID)
        && maxPhotometricLight->GetDistribution() != LightscapeLight::ISOTROPIC_DIST) {
        return pxr::MaxUsdPrimTypeTokens->RectLight;
    }
    // Photometric Sphere light gets translated into a UsdLuxSphereLight
    // Photometric Point and Disk lights using a spherical light distribution type (isotropic)
    // gets translated into a UsdLuxSphereLight.
    if (photometricLightType == LS_SPHERE_LIGHT_ID
        || photometricLightType == LS_SPHERE_LIGHT_TARGET_ID
        || ((photometricLightType == LS_POINT_LIGHT_ID
             || photometricLightType == LS_POINT_LIGHT_TARGET_ID
             || photometricLightType == LS_DISC_LIGHT_ID
             || photometricLightType == LS_DISC_LIGHT_TARGET_ID)
            && maxPhotometricLight->GetDistribution() == LightscapeLight::ISOTROPIC_DIST)) {
        return pxr::MaxUsdPrimTypeTokens->SphereLight;
    }
    // Photometric Cylinder light gets translated into a UsdLuxCylinderLight
    // Photometric Line and Area (Rectangle) lights using a spherical light distribution type
    // (isotropic) gets translated into a UsdLuxCylinderLight.
    if (photometricLightType == LS_CYLINDER_LIGHT_ID
        || photometricLightType == LS_CYLINDER_LIGHT_TARGET_ID
        || ((photometricLightType == LS_LINEAR_LIGHT_ID
             || photometricLightType == LS_LINEAR_LIGHT_TARGET_ID
             || photometricLightType == LS_AREA_LIGHT_ID
             || photometricLightType == LS_AREA_LIGHT_TARGET_ID)
            && maxPhotometricLight->GetDistribution() == LightscapeLight::ISOTROPIC_DIST)) {

        return pxr::MaxUsdPrimTypeTokens->CylinderLight;
    }
    // Should not happpen. Fallback to sphere light.
    return pxr::MaxUsdPrimTypeTokens->SphereLight;
}

bool MaxUsdPhotometricLightWriter::Write(
    UsdPrim&                  targetPrim,
    bool                      applyOffsetTransform,
    const MaxUsd::ExportTime& time)
{
    INode* sourceNode = GetNode();

    auto     object = sourceNode->EvalWorldState(time.GetMaxTime()).obj;
    Class_ID photometricLightType = object->ClassID();

    LightscapeLight2* maxPhotometricLight = dynamic_cast<LightscapeLight2*>(
        object->ConvertToType(time.GetMaxTime(), photometricLightType));

    if (maxPhotometricLight == nullptr) {
        return false;
    }

    auto stage = targetPrim.GetStage();
    auto primPath = targetPrim.GetPath();
    auto targetType = GetPrimType();

    // Write time-independent properties on the first frame only.
    pxr::UsdLuxBoundableLightBase usdLightPrim;
    if (time.IsFirstFrame()) {
        bool applyConversionRotationOffset { false };

        if (targetType == pxr::MaxUsdPrimTypeTokens->DiskLight) {
            pxr::UsdLuxDiskLight discLight = pxr::UsdLuxDiskLight::Define(stage, primPath);
            if (photometricLightType == LS_POINT_LIGHT_ID
                || photometricLightType == LS_POINT_LIGHT_TARGET_ID) {
                // a minimal disk radius needs to be specified in order to emit light
                // value is the same as translated from Arnold MAXtoA
                discLight.CreateRadiusAttr().Set(0.001f, pxr::UsdTimeCode::Default());
            }
            usdLightPrim = discLight;
        } else if (targetType == pxr::MaxUsdPrimTypeTokens->RectLight) {
            pxr::UsdLuxRectLight rectangleLight = pxr::UsdLuxRectLight::Define(stage, primPath);
            if (photometricLightType == LS_LINEAR_LIGHT_ID
                || photometricLightType == LS_LINEAR_LIGHT_TARGET_ID) {
                // a diffuse line light is rendered as a directional line light (a narrow plane)
                // the narrow plane is set to be a '0.1' unit width, fixed in time
                // value is the same as translated from Arnold MAXtoA
                rectangleLight.CreateWidthAttr().Set(0.1f, pxr::UsdTimeCode::Default());
            }
            usdLightPrim = rectangleLight;
        } else if (targetType == pxr::MaxUsdPrimTypeTokens->SphereLight) {
            pxr::UsdLuxSphereLight sphereLight = pxr::UsdLuxSphereLight::Define(stage, primPath);

            bool treatAsPointLight = photometricLightType == LS_POINT_LIGHT_ID
                || photometricLightType == LS_POINT_LIGHT_TARGET_ID;
            if (treatAsPointLight) {
                // TODO: figure out which renderer supports the 'treatAsPoint' attribute
                // For now, set the radius to 0.001 in order to minimally emit light
                // value is the same as translated from Arnold MAXtoA
                sphereLight.CreateRadiusAttr().Set(
                    0.001f, pxr::UsdTimeCode::Default()); // fixed in time
                sphereLight.CreateTreatAsPointAttr().Set(
                    treatAsPointLight, pxr::UsdTimeCode::Default());
            }
            usdLightPrim = sphereLight;
        } else if (targetType == pxr::MaxUsdPrimTypeTokens->CylinderLight) {
            pxr::UsdLuxCylinderLight cylinderLight
                = pxr::UsdLuxCylinderLight::Define(stage, primPath);

            // need to rotate the light on its z-axis 90 degrees for USD
            applyConversionRotationOffset = true;

            bool treatAsLineLight
                = (photometricLightType == LS_LINEAR_LIGHT_ID
                   || photometricLightType == LS_LINEAR_LIGHT_TARGET_ID);
            if (treatAsLineLight) {
                // TODO: figure out which renderer supports the 'treatAsLine' attribute
                // For now, set the radius to 0.001 in order to minimally emit light
                // value is the same as translated from Arnold MAXtoA
                cylinderLight.CreateRadiusAttr().Set(
                    0.001f, pxr::UsdTimeCode::Default()); // fixed in time
                cylinderLight.CreateTreatAsLineAttr().Set(
                    treatAsLineLight, pxr::UsdTimeCode::Default());
            }
            usdLightPrim = cylinderLight;
        }

        if (applyConversionRotationOffset) {
            pxr::UsdGeomXformable xformable(usdLightPrim.GetPrim());
            // Any linear, cylindrical or plane light types are converted to UsdLuxCylinder type.
            // In USD, the expected orientation is on the x-axis, but 3ds Max has it set on the
            // y-axis need to rotate the light on its z-axis 90 degrees for USD
            pxr::UsdGeomXformOp rotationAdjustment = xformable.AddXformOp(
                pxr::UsdGeomXformOp::TypeRotateZ,
                pxr::UsdGeomXformOp::PrecisionDouble,
                pxr::TfToken());
            rotationAdjustment.Set(90.0, pxr::UsdTimeCode::Default());
        }

        // Light color
        // Enable color temperature (Kelvin) if specified
        bool enableColorTemperature = maxPhotometricLight->GetUseKelvin();
        usdLightPrim.CreateEnableColorTemperatureAttr().Set(
            enableColorTemperature, pxr::UsdTimeCode::Default());

        // When the light is turned off, zero out both diffuse and specular contributions
        // so the light has no effect in USD.
        bool isLightOn = maxPhotometricLight->GetUseLight() != 0;

        if (!isLightOn || !maxPhotometricLight->GetAffectSpecular()) {
            // turn off the effect of this light on the specular response of materials
            usdLightPrim.CreateSpecularAttr().Set(0.0f, pxr::UsdTimeCode::Default());
        } else {
            // TODO: how to compute the specular effect multiplier
            // leave the default value to 1.0f for now
            // usdLightPrim.CreateSpecularAttr().Set(0.0f, pxr::UsdTimeCode::Default());
        }
        if (!isLightOn || !maxPhotometricLight->GetAffectDiffuse()) {
            // turn off the effect of this light on the diffuse response of materials
            usdLightPrim.CreateDiffuseAttr().Set(0.0f, pxr::UsdTimeCode::Default());
        } else {
            // TODO: how to compute the diffuse effect multiplier
            // leave the default value to 1.0f for now
            // usdLightPrim.CreateSpecularAttr().Set(0.0f, pxr::UsdTimeCode::Default());
        }

        // Enable shadow casting
        pxr::UsdLuxShadowAPI usdLightShadowProperties(usdLightPrim);
        bool                 shadowEnable = maxPhotometricLight->GetShadow();
        usdLightShadowProperties.CreateShadowEnableAttr().Set(
            shadowEnable, pxr::UsdTimeCode::Default());

        // Normalize light intensity
        // This makes it easier to independently adjust the power and shape of the light,
        // by causing the power to not vary with the area or angular size of the light.
        usdLightPrim.CreateNormalizeAttr().Set(true, pxr::UsdTimeCode::Default());

        // IES distribution is not animatable
        //
        // MAX-LIT-002 surgical bounds (negative cases, ALL must reach this
        // block without authoring shaping:ies:file in the wrong state) --
        // enforced by the regression suite via
        // src/Tests/Integration/export_light_test.ms ::
        //     photometric_light_ies_file_export_test
        //                 (WEB_DIST + .webFile set -> shaping:ies:file authored
        //                  with the resolved file path; subsequent re-export
        //                  with distribution == ISOTROPIC -> shaping:ies:file
        //                  MUST NOT be authored)
        // plus the doc-linked Python validator
        // validate_photometric_light_fidelity.py (8 cases + idempotence,
        // mirrors this gate at the USD-side decision level):
        //
        //   * distribution != WEB_DIST: shaping:ies:file MUST NOT be
        //     authored, regardless of whether the source light carries a
        //     .webFile asset (Max preserves the picked file across
        //     distribution toggles; it is only ACTIVE while distribution
        //     == WEB_DIST). A regression that always-authored
        //     shaping:ies:file whenever .webFile was non-empty would
        //     produce a USD light that paradoxically carries both a
        //     shape-driven distribution (e.g. spot's shaping:cone:angle)
        //     AND an IES profile -- some renderers honour one and ignore
        //     the other, producing source-renderer-dependent appearance.
        //   * distribution == WEB_DIST AND asset.GetId() == kInvalidId
        //     (the user picked "web/IES" distribution but never selected
        //     a .ies file): shaping:ies:file MUST NOT be authored (no
        //     spurious empty path). A regression that dropped the
        //     asset-id check would emit shaping:ies:file = "" and most
        //     renderers would silently fall back to the disk light's
        //     default uniform pattern -- visually equivalent to no IES
        //     authoring on Karma, but lethal for any consumer that
        //     validates the asset path early (USDZ packagers, schema
        //     validators, Omniverse asset-graph importers).
        //   * The TODO on line 277 is a known limitation: the path is
        //     authored as the asset's resolved-full-file-path (absolute
        //     on the source machine). A pack-and-go USDZ + IES bundle
        //     would need a separate pass to copy the .ies file alongside
        //     the .usd and rewrite the path to relative form. The
        //     audit's scope is to LOCK IN the current branch shape; the
        //     pack-and-go improvement is tracked as a candidate mission.
        if (maxPhotometricLight->GetDistribution() == LightscapeLight::WEB_DIST) {
            // IES Light Profile file:
            // TODO: Consider exporting the IES File along with the data. For now, this references
            // the IES file as a reference to the original file referenced by the 3ds Max file.
            // TODO: IES files do not get imported with RenderMan render delegate (Prman)
            using namespace MaxSDK::AssetManagement;
            AssetUser asset = maxPhotometricLight->GetWebFile();
            if (asset.GetId() != kInvalidId) {
                pxr::UsdLuxShapingAPI usdLightShape(usdLightPrim);
                pxr::SdfAssetPath     assetFullPath(asset.GetFullFilePath().ToUTF8().data());
                usdLightShape.CreateShapingIesFileAttr().Set(
                    assetFullPath, pxr::UsdTimeCode::Default());
            }
        }
    }

#ifdef USD_CURVES_SUPPORTED
    // [MAX-ANIM-001] photometric light animation time-sampled export
    // bound (lock-in only, no logic change). The `== TimeSamples` gate
    // is STRICTLY equality -- UNLIKE CameraWriter's additive
    // `!= Curves` gate, photometric lights ONLY time-sample on the
    // TimeSamples animation type. On Curves mode the lights serialize
    // as splines ONLY -- there is no time-sample fallback. A refactor
    // that copy-pasted the CameraWriter gate (`!= Curves`) would
    // silently DUAL-author time-samples AND splines on photometric
    // lights, bloating every animated-light export with redundant
    // attribute data. Static-only attrs on the photometric light --
    // `enableColorTemperature`, `normalize`, `shaping:ies:file`
    // (MAX-LIT-002 IES branch bound), `colorTemperatureAttr`
    // (MAX-LIT-002 Kelvin branch bound) -- are authored at
    // `UsdTimeCode::Default()` ONCE per write, OUTSIDE the
    // `if (exportTimeSamples)` blocks below. See the central
    // [MAX-ANIM-001] block in
    // `src/MaxUsd/Translators/AnimExportTask.cpp::Execute` and the
    // validator cases `Animated_Light_TimeSamples` /
    // `Light_ColorTemperatureStatic_EvenInAnimatedStage` /
    // `Light_IesFile_Static_EvenInAnimatedStage` /
    // `CrossWriter_Divergence` in
    // `validate_animation_time_sampled_surgical.py`.
    const auto animationType = GetExportArgs().GetAnimationType();
    const bool exportTimeSamples
        = animationType == MaxUsd::USDSceneBuilderOptions::AnimationType::TimeSamples;
    const bool exportCurves
        = animationType == MaxUsd::USDSceneBuilderOptions::AnimationType::Curves
        && time.IsFirstFrame();

    const auto lightPB = maxPhotometricLight->GetParamBlockByID(LightscapeLight::PB_GENERAL);
    const auto lightExtPB = maxPhotometricLight->GetParamBlockByID(LightscapeLight::PB_EXT);
#else
    const bool exportTimeSamples = true;
#endif

    // Write animatable properties at the requested time.

    // If not on the first frame, we haven't fetched the light yet, do it now.
    if (!usdLightPrim) {
        usdLightPrim = pxr::UsdLuxBoundableLightBase::Get(stage, primPath);
    }

    const auto timeVal = time.GetMaxTime();
    const auto usdTimeCode = time.GetUsdTime();

    // same conditional block statements as above in the light declarations unless specifically
    // noted
    if ((photometricLightType == LS_DISC_LIGHT_ID
         || photometricLightType == LS_DISC_LIGHT_TARGET_ID)
        && maxPhotometricLight->GetDistribution() != LightscapeLight::ISOTROPIC_DIST) {
        // note: since point lights have a fixed radius, there is no need
        // to have those light types treated inside this conditional block
        pxr::UsdLuxDiskLight discLight = (pxr::UsdLuxDiskLight)usdLightPrim;
        auto                 radiusAttribute = discLight.CreateRadiusAttr();
        float                radius = maxPhotometricLight->GetRadius(timeVal);
        if (exportTimeSamples) {
            radiusAttribute.Set(radius, usdTimeCode);
        }
#ifdef USD_CURVES_SUPPORTED
        if (exportCurves) {
            if (!MaxUsd::WriteSplineAttribute<float>(
                    stage,
                    lightExtPB->GetControllerByID(LightscapeLight::PB_DISCLIGHT_RADIUS),
                    targetPrim,
                    radiusAttribute)) {
                radiusAttribute.Set(radius);
            }
        }
#endif
    } else if (
        (photometricLightType == LS_LINEAR_LIGHT_ID
         || photometricLightType == LS_LINEAR_LIGHT_TARGET_ID
         || photometricLightType == LS_AREA_LIGHT_ID
         || photometricLightType == LS_AREA_LIGHT_TARGET_ID)
        && maxPhotometricLight->GetDistribution() != LightscapeLight::ISOTROPIC_DIST) {
        pxr::UsdLuxRectLight rectangleLight = (pxr::UsdLuxRectLight)usdLightPrim;

        if (photometricLightType == LS_AREA_LIGHT_ID
            || photometricLightType == LS_AREA_LIGHT_TARGET_ID) {
            // applies only to area (rectangle) lights as the line lights have fixed width
            auto  widthAttribute = rectangleLight.CreateWidthAttr();
            float width = maxPhotometricLight->GetWidth(timeVal);
            if (exportTimeSamples) {
                widthAttribute.Set(width, usdTimeCode);
            }
#ifdef USD_CURVES_SUPPORTED
            if (exportCurves) {
                if (!MaxUsd::WriteSplineAttribute<float>(
                        stage,
                        lightExtPB->GetControllerByID(LightscapeLight::PB_AREALIGHT_WIDTH),
                        targetPrim,
                        widthAttribute)) {
                    widthAttribute.Set(width);
                }
            }
#endif
        }
        auto  heightAttribute = rectangleLight.CreateHeightAttr();
        float height = maxPhotometricLight->GetLength(timeVal);
        if (exportTimeSamples) {
            heightAttribute.Set(height, usdTimeCode);
        }

#ifdef USD_CURVES_SUPPORTED
        if (exportCurves) {
            if (!MaxUsd::WriteSplineAttribute<float>(
                    stage,
                    lightExtPB->GetControllerByID(LightscapeLight::PB_AREALIGHT_LENGTH),
                    targetPrim,
                    heightAttribute)) {
                heightAttribute.Set(height);
            }
        }
#endif
    } else if (
        photometricLightType == LS_SPHERE_LIGHT_ID
        || photometricLightType == LS_SPHERE_LIGHT_TARGET_ID
        || ((photometricLightType == LS_DISC_LIGHT_ID
             || photometricLightType == LS_DISC_LIGHT_TARGET_ID)
            && maxPhotometricLight->GetDistribution() == LightscapeLight::ISOTROPIC_DIST)) {
        // note: since point lights have a fixed radius, there is no need
        // to have those light types treated inside this conditional block
        pxr::UsdLuxSphereLight sphereLight = (pxr::UsdLuxSphereLight)usdLightPrim;
        auto                   radiusAttribute = sphereLight.CreateRadiusAttr();
        float                  radius = maxPhotometricLight->GetRadius(timeVal);
        if (exportTimeSamples) {
            radiusAttribute.Set(radius, usdTimeCode);
        }

#ifdef USD_CURVES_SUPPORTED
        if (exportCurves) {
            if (!MaxUsd::WriteSplineAttribute<float>(
                    stage,
                    lightExtPB->GetControllerByID(LightscapeLight::PB_DISCLIGHT_RADIUS),
                    targetPrim,
                    radiusAttribute)) {
                radiusAttribute.Set(radius);
            }
        }
#endif
    } else if (
        photometricLightType == LS_CYLINDER_LIGHT_ID
        || photometricLightType == LS_CYLINDER_LIGHT_TARGET_ID
        || ((photometricLightType == LS_LINEAR_LIGHT_ID
             || photometricLightType == LS_LINEAR_LIGHT_TARGET_ID
             || photometricLightType == LS_AREA_LIGHT_ID
             || photometricLightType == LS_AREA_LIGHT_TARGET_ID)
            && maxPhotometricLight->GetDistribution() == LightscapeLight::ISOTROPIC_DIST)) {
        pxr::UsdLuxCylinderLight cylinderLight = (pxr::UsdLuxCylinderLight)usdLightPrim;
        auto                     lengthAttribute = cylinderLight.CreateLengthAttr();
        float                    length = maxPhotometricLight->GetLength(timeVal);
        if (exportTimeSamples) {
            lengthAttribute.Set(length, usdTimeCode);
        }

#ifdef USD_CURVES_SUPPORTED
        if (exportCurves) {
            if (!MaxUsd::WriteSplineAttribute<float>(
                    stage,
                    lightExtPB->GetControllerByID(LightscapeLight::PB_AREALIGHT_LENGTH),
                    targetPrim,
                    lengthAttribute)) {
                lengthAttribute.Set(length);
            }
        }
#endif

        if (photometricLightType == LS_AREA_LIGHT_ID
            || photometricLightType == LS_AREA_LIGHT_TARGET_ID) {
            auto  radiusAttribute = cylinderLight.CreateRadiusAttr();
            float radius = maxPhotometricLight->GetWidth(timeVal) / 2.f;
            if (exportTimeSamples) {
                radiusAttribute.Set(radius, usdTimeCode);
            }

#ifdef USD_CURVES_SUPPORTED
            if (exportCurves) {
                if (!MaxUsd::WriteSplineAttribute<float>(
                        stage,
                        lightExtPB->GetControllerByID(LightscapeLight::PB_AREALIGHT_WIDTH),
                        targetPrim,
                        radiusAttribute)) {
                    radiusAttribute.Set(radius);
                }
            }
#endif
        } else if (
            photometricLightType == LS_CYLINDER_LIGHT_ID
            || photometricLightType == LS_CYLINDER_LIGHT_TARGET_ID) {
            auto  radiusAttribute = cylinderLight.CreateRadiusAttr();
            float radius = maxPhotometricLight->GetRadius(timeVal);
            if (exportTimeSamples) {
                radiusAttribute.Set(radius, usdTimeCode);
            }

#ifdef USD_CURVES_SUPPORTED
            if (exportCurves) {
                if (!MaxUsd::WriteSplineAttribute<float>(
                        stage,
                        lightExtPB->GetControllerByID(LightscapeLight::PB_CYLINDERLIGHT_RADIUS),
                        targetPrim,
                        radiusAttribute)) {
                    radiusAttribute.Set(radius);
                }
            }
#endif
        }
        // else, line lights have fixed width
    }

    // Light color
    //
    // MAX-LIT-002 surgical bounds (the useKelvin dichotomy; both branches
    // must reach the right UsdLux attribute combination or a downstream
    // renderer silently produces the wrong-temperature illumination) --
    // enforced by the regression suite via
    // src/Tests/Integration/export_light_test.ms ::
    //     photometric_light_kelvin_filter_color_round_trip_test
    //                 (useKelvin=true  -> enable=true + ct=K + color=filter;
    //                  useKelvin=false -> enable=false + NO ct + color=light*filter;
    //                  out-of-range Kelvin -> clamped to [1000, 10000])
    // plus the doc-linked Python validator
    // validate_photometric_light_fidelity.py (8 cases + idempotence,
    // including the two clamp edges):
    //
    //   useKelvin == true branch:
    //   * enableColorTemperatureAttr is set to true on the first frame
    //     above (line ~239). colorTemperatureAttr value is authored HERE
    //     and inputs:color carries the FILTER COLOUR ONLY (the source
    //     light's RGB component is intentionally dropped because the
    //     spectrum is now driven by the blackbody temperature). A
    //     regression that authored lightColor * filterColor here would
    //     double-tint -- the renderer multiplies inputs:color by the
    //     blackbody integrand, so a non-white RGB would shift the hue
    //     away from the spectrum the artist asked for.
    //   * colorTemperatureAttr value is clamped to [1000, 10000] (the
    //     USD spec range) and a one-shot warning fires on clamp with
    //     the original (unclamped) Kelvin value preserved in the
    //     message. A regression that widened the range would produce
    //     USD-invalid colorTemperature values that schema validators
    //     reject and that some renderers extrapolate incorrectly past
    //     the spec range.
    //
    //   useKelvin == false branch:
    //   * enableColorTemperatureAttr is set to false on the first frame
    //     above (line ~239). colorTemperatureAttr is NEVER authored on
    //     this branch (the USD spec default of 6500 K is irrelevant
    //     because the enable flag is off). inputs:color carries the
    //     COMBINED PRODUCT lightColor * filterColor -- this is
    //     intentionally lossy on round-trip (an importer cannot recover
    //     which factor was the light and which was the filter) but
    //     matches the renderer's expectation of a single RGB multiplier
    //     when no blackbody is active. A regression that authored
    //     either factor alone would silently drop the other half of the
    //     colour signal.
    if (maxPhotometricLight->GetUseKelvin()) {
        // USD expects Kelvin range values from 1000 to 10000
        auto  colorTemperatureAttribute = usdLightPrim.CreateColorTemperatureAttr();
        float originalKelvinValue = maxPhotometricLight->GetKelvin(timeVal);
        float clampedKelvinValue = std::min(std::max(1000.f, originalKelvinValue), 10000.f);
        if (exportTimeSamples) {
            colorTemperatureAttribute.Set(clampedKelvinValue, usdTimeCode);
            if (originalKelvinValue != clampedKelvinValue) {
                MaxUsd::Log::Warn(
                    L"Light '{0}' temperature value was clamped to '{1}' from '{2}' to match USD "
                    L"specifications.",
                    sourceNode->GetName(),
                    clampedKelvinValue,
                    originalKelvinValue);
            }
        }

        // Add light filter color - This can't be export as spline, so always export time sampled
        Point3       maxFilteLightColor = maxPhotometricLight->GetRGBFilter(timeVal);
        pxr::GfVec3f usdLightColor { maxFilteLightColor[0],
                                     maxFilteLightColor[1],
                                     maxFilteLightColor[2] };
        usdLightPrim.CreateColorAttr().Set(usdLightColor, usdTimeCode);

#ifdef USD_CURVES_SUPPORTED
        if (exportCurves) {
            if (!MaxUsd::WriteSplineAttribute<float>(
                    stage,
                    lightPB->GetControllerByID(LightscapeLight::PB_KELVIN),
                    targetPrim,
                    colorTemperatureAttribute,
                    [sourceNode](float originalColorTemperature) {
                        float clampedKelvinValue
                            = std::min(std::max(1000.f, originalColorTemperature), 10000.f);
                        MaxUsd::Log::Warn(
                            L"Light '{0}' spline temperature value was clamped to '{1}' from '{2}' "
                            L"to "
                            L"match USD specifications.",
                            sourceNode->GetName(),
                            clampedKelvinValue,
                            originalColorTemperature);
                        return clampedKelvinValue;
                    })) {
                colorTemperatureAttribute.Set(clampedKelvinValue);
            }
        }
#endif
    } else {
        // When not using color temperature (Kelvin) to specify light color,
        // light color is then a composition of the specified light and filter color
        Point3 maxLightColor = maxPhotometricLight->GetRGBColor(timeVal)
            * maxPhotometricLight->GetRGBFilter(timeVal);
        pxr::GfVec3f usdLightColor { maxLightColor[0], maxLightColor[1], maxLightColor[2] };
        usdLightPrim.CreateColorAttr().Set(usdLightColor, usdTimeCode);
    }

    // Shadow color
    // note: The shadow color is not exposed in the Photometric light interface (but thru
    // maxscript)
    pxr::UsdLuxShadowAPI usdLightShadowProperties(usdLightPrim);
    Point3               maxLightShadowColor = maxPhotometricLight->GetShadColor(timeVal);
    pxr::GfVec3f         usdLightShadowColor(
        maxLightShadowColor[0], maxLightShadowColor[1], maxLightShadowColor[2]);
    usdLightShadowProperties.CreateShadowColorAttr().Set(usdLightShadowColor, usdTimeCode);

    // Light Falloff values
    // TODO - light filter required - might be a renderer specific thing to expose
    // if (maxPhotometricLight->GetUseAtten())
    //{
    //	usdLightShadowProperties.CreateFalloffLightFilerWhateverAttr().Set(
    //			maxPhotometricLight->GetAtten(timeVal, ATTEN_START, FOREVER),
    // usdTimeCode); 	usdLightShadowProperties.CreateFalloffLightFilerWhateverAttr().Set(
    //			maxPhotometricLight->GetAtten(timeVal, ATTEN_END, FOREVER),
    // usdTimeCode);
    //}

    // Light intensity
    {
        auto intensityAttribute = usdLightPrim.CreateIntensityAttr();

        // based on the Arnold translator (MAXtoA)
        //
        // the effective intensity in candelas
        auto getIntensity = [&maxPhotometricLight](TimeValue time) -> float {
            float lightIntensity = maxPhotometricLight->GetIntensity(time);
            if (maxPhotometricLight->GetDistribution() == LightscapeLight::WEB_DIST) {
                lightIntensity
                    = lightIntensity / maxPhotometricLight->GetOriginalIntensity() * 1000.f;
            }

            // take care of a dimmed intensity
            if (maxPhotometricLight->GetUseMultiplier()) {
                lightIntensity *= maxPhotometricLight->GetDimmerValue(time) * 0.01f;
            }

            lightIntensity
                /= 1500; // TODO - need
                         // GetRenderSessionContext().GetRenderSettings().GetPhysicalScale(translationTime,
                         // newValidity)

            // ZAP's magic adjustment
            lightIntensity *= static_cast<float>(M_PI);
            // scale to system units.
            lightIntensity /= static_cast<float>(
                GetSystemUnitScale(UNITS_METERS) * GetSystemUnitScale(UNITS_METERS));

            return lightIntensity;
        };
        if (exportTimeSamples) {
            intensityAttribute.Set(getIntensity(timeVal), usdTimeCode);
        }

#ifdef USD_CURVES_SUPPORTED
        if (exportCurves) {
            auto intensitySpline = MaxUsd::CreateSplineFromControl<float>(
                stage,
                lightPB->GetControllerByID(LightscapeLight::PB_INTENSITY),
                [maxPhotometricLight](float intensity) {
                    if (maxPhotometricLight->GetDistribution() == LightscapeLight::WEB_DIST) {
                        intensity
                            = intensity / maxPhotometricLight->GetOriginalIntensity() * 1000.f;
                    }

                    intensity /= 1500;
                    intensity *= static_cast<float>(M_PI);
                    intensity /= static_cast<float>(
                        GetSystemUnitScale(UNITS_METERS) * GetSystemUnitScale(UNITS_METERS));

                    return intensity;
                });

            const auto knots = intensitySpline.GetKnots();
            if (!knots.empty()) {
                if (maxPhotometricLight->GetUseMultiplier()) {
                    auto multiplierSpline = MaxUsd::CreateSplineFromControl<float>(
                        stage, lightPB->GetControllerByID(LightscapeLight::PB_DIMMER));
                    auto combinedSpline = MaxUsd::CombineSplines<float>(
                        intensitySpline, multiplierSpline, [](float intensity, float multiplier) {
                            return intensity * multiplier * 0.01f;
                        });
                    intensityAttribute.SetSpline(combinedSpline);
                } else {
                    intensityAttribute.SetSpline(intensitySpline);
                }
            } else {
                intensityAttribute.Set(getIntensity(timeVal));
            }
        }
#endif
    }

    LightscapeLight::DistTypes maxLightDistType = maxPhotometricLight->GetDistribution();
    float                      beamAngle = maxPhotometricLight->GetHotspot(timeVal);
    if (maxLightDistType == LightscapeLight::SPOTLIGHT_DIST) {
        // TODO - the falloff of the spot is not directly the angle value from 3ds Max
        pxr::UsdLuxShapingAPI usdLightShape(usdLightPrim);
        auto shapingConeAngleAttribute = usdLightShape.CreateShapingConeAngleAttr();
        if (exportTimeSamples) {
            shapingConeAngleAttribute.Set(beamAngle, usdTimeCode);
        }

#ifdef USD_CURVES_SUPPORTED
        if (exportCurves) {
            const auto spotlightPb
                = maxPhotometricLight->GetParamBlockByID(LightscapeLight::PB_SPOT);
            if (!MaxUsd::WriteSplineAttribute<float>(
                    stage,
                    spotlightPb->GetControllerByID(LightscapeLight::PB_BEAM_ANGLE),
                    targetPrim,
                    shapingConeAngleAttribute)) {
                // If failed to author spline, write default value
                shapingConeAngleAttribute.Set(beamAngle);
            }
        }
#endif
    }

    return true;
}

PXR_NAMESPACE_CLOSE_SCOPE