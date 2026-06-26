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
#include "AnimExportTask.h"

#include <MaxUsd/DLLEntry.h>
#include <MaxUsd/resource.h>

namespace MAXUSD_NS_DEF {

AnimExportTask::AnimExportTask(const TimeConfig& timeConfig)
    : timeConfig(timeConfig)
{
}

void AnimExportTask::AddObjectExportOp(
    std::function<Interval(TimeValue)>     intervalFunc,
    std::function<void(const ExportTime&)> writeAtTime,
    std::function<void()>                  postExport)
{
    objectExportOps.push_back({ intervalFunc, writeAtTime, postExport });
}

void AnimExportTask::AddTransformExportOp(
    std::function<void(const ExportTime&, pxr::UsdGeomXformOp&)> writeAtTime)
{
    transformExportOps.push_back({ writeAtTime });
}

void AnimExportTask::Execute(MaxProgressBar& progress)
{
    // ------------------------------------------------------------------
    // [MAX-ANIM-001] CENTRAL TIME-AXIS SUBSTITUTION (animation export
    // scope/coverage audit, lock-in only -- no logic change in this
    // bite).
    //
    // The line below in the inner loop --
    //     const auto usdTime = animated
    //                        ? pxr::UsdTimeCode(GetFrameFromTimeValue(maxTime))
    //                        : pxr::UsdTimeCode::Default();
    // -- is the central per-call USD-time substitution that every
    // object/transform-export op registered via AddObjectExportOp /
    // AddTransformExportOp resolves through. `animated` is
    // `timeConfig.IsAnimated() = startFrame != endFrame` (TimeUtils.h).
    //
    // Surgical bound this audit pins (case `Static_Xform_NoTimeSamples`
    // / `Static_Camera_NoTimeSamples` / `Static_Light_NoTimeSamples`
    // / `Animated_*_TimeSamples` / `CrossWriter_Divergence` in the
    // Python validator `validate_animation_time_sampled_surgical.py`):
    //
    //   * IsAnimated() == false (single-frame export) -> EVERY writer's
    //     `attr.Set(value, usdTime)` call carries `usdTime =
    //     UsdTimeCode::Default()`, so the attribute is authored at the
    //     default time code with no time samples. This preserves the
    //     `startTimeCode == endTimeCode == 0` static-asset convention
    //     every single-frame export today produces.
    //   * IsAnimated() == true (multi-frame export) -> EVERY writer's
    //     `attr.Set(value, usdTime)` call carries `usdTime =
    //     UsdTimeCode(frame)`, so the attribute is authored as time
    //     samples at each frame in [startFrame, endFrame] step
    //     `timeStep = ticksPerFrame / samplesPerFrame`.
    //
    // A wildcard refactor that collapsed the gate to always-time-coded
    // (e.g. `usdTime = UsdTimeCode(frame)` even when
    // !timeConfig.IsAnimated()) would author every attribute on every
    // static export as a time-coded sample at frame 0, breaking the
    // single-frame-static convention. The case
    // `Static_Xform_NoTimeSamples` in the validator fails by named
    // case on that regression.
    //
    // The three writer paths that PARTICIPATE in this gate:
    //
    //   1. Transform export -- src/MaxUsd/Builders/USDSceneBuilder.cpp
    //      registers per-component transform-export ops via
    //      `animExportTask.AddTransformExportOp(...)`. See
    //      USDSceneBuilder.cpp:1289 and :1464 for the
    //      `if (exportTimeSamples)` gates that wrap the registration.
    //      Forced-TimeSamples cases (preserved BY this audit): node has
    //      a lookat target; node's TM controller is not a valid Curves
    //      controller (List / PRS without keys / Expose / Linkage).
    //   2. Camera export -- src/translators/CameraWriter.cpp:129 gates
    //      `exportTimeSamples = animType != Curves`. Time-samples
    //      EVERY animation type EXCEPT Curves, so the Curves mode
    //      still ships time-sample fallback alongside the spline (the
    //      `if (exportTimeSamples)` and `if (exportCurves)` branches
    //      are ADDITIVE on Curves mode, not alternatives).
    //   3. Photometric light export -- src/translators/
    //      PhotometricLightWriter.cpp:421-422 gates
    //      `exportTimeSamples = animType == TimeSamples`. STRICTLY
    //      equality (no time-sample fallback on Curves mode for
    //      photometric lights -- lights serialize as splines only on
    //      Curves mode).
    //
    // Each of those three writer paths ALSO carries attributes that are
    // NEVER time-coded regardless of `animated` -- they are authored at
    // `UsdTimeCode::Default()` ONCE per write, OUTSIDE the
    // `if (exportTimeSamples)` blocks. Those static-attribute
    // partitions (camera `projection`; light `enableColorTemperature`,
    // `normalize`, `shaping:ies:file` [MAX-LIT-002 IES branch bound],
    // `colorTemperatureAttr` [MAX-LIT-002 Kelvin branch bound]) are
    // pinned by the validator cases
    // `Camera_ProjectionStatic_EvenInAnimatedStage`,
    // `Light_ColorTemperatureStatic_EvenInAnimatedStage`, and
    // `Light_IesFile_Static_EvenInAnimatedStage`.
    //
    // A wildcard "unify time-sampling across the three writers" refactor
    // that bridged them with a single helper would either drop those
    // never-time-coded attrs onto the time-sample path (bloating every
    // animated export) OR drop the `forced-TimeSamples` cases (nodeTarget
    // / !isValidController) onto Curves (shipping empty
    // xformOp:transform attrs on every lookat-targeted camera) OR
    // flip CameraWriter's `!= Curves` gate to `== TimeSamples`
    // (dropping the Curves-mode time-sample fallback every Hydra /
    // Karma / ARKit consumer reads). Each surfaces by named case in
    // the validator.
    //
    // The cross-writer-path anchor case `CrossWriter_Divergence`
    // asserts ALL three writers preserve their own static-vs-animated
    // partition simultaneously on the same exported stage; a
    // unification refactor that bridged two of the three would fail by
    // named case rather than silently mutating output.
    //
    // Retires when a SEPARATE future MAX-ANIM-* bite genuinely needs
    // to bridge any two of the three writer paths with its own
    // captured corpus, MaxScript regression, and doc entry -- the
    // expected first retirement candidate is the Max 2026+
    // AnimationType::Curves support that the `#ifdef
    // USD_CURVES_SUPPORTED` blocks gate. Until that lands binary-
    // verified, the three-writer-path static/animated partition is
    // the documented, locked-in behaviour.
    // ------------------------------------------------------------------

    // Export the time samples for...
    // Object prims : Only export the required frames, which we infer from the export
    // data's validity interval (we get this from prim writers - typically the 3dsMax
    // object validity intervals).
    // Node transforms : Export the transforms at all time samples in the export range.

    // Simple struct defining what needs to be exported for a specific time.
    struct ExportReq
    {
        bool                      transform = false;
        std::vector<ObjectAnimOp> objects;
    };

    // Track all times that we need to export, and the object we need to export at each of them.
    std::map<TimeValue, ExportReq> exportTimes;

    // We do not currently use validity intervals for transforms, so at a minimum, we need to export
    // those at every time sample within the export range...
    const int       timeStep = timeConfig.GetTimeStep();
    const TimeValue startTime = timeConfig.GetStartTime();
    const TimeValue endTime = timeConfig.GetEndTime();
    for (TimeValue timeVal = startTime; timeVal <= endTime;) {
        exportTimes.insert({ timeVal, { true, {} } });
        if (timeVal == endTime) {
            break;
        }
        // Calculate the next time sample... Make sure the endTime is exported.
        timeVal = std::min(timeVal + timeStep, endTime);
    }

    // Next, cycle through all the objects we have queued for export, and figure out the first
    // time value at which we need to export them, from the validity interval at the anim start
    // time.
    for (auto& objectExpOp : objectExportOps) {
        const auto intervalAtStart = objectExpOp.getValidityInterval(startTime);

        TimeValue firstExportTime = 0;

        // Generally, we want to export the last time of the interval applicable at the start time.
        // [----00000------] (data change on frames marked with 0s, [...] indicates the export
        // range)
        //     ^--- this is the first meaningful frame, data doesn't change up until right after it.
        const auto lastTimeOfInterval = intervalAtStart.End();

        if (lastTimeOfInterval > startTime && lastTimeOfInterval < endTime) {
            firstExportTime = lastTimeOfInterval;
        } else {
            firstExportTime = startTime;
        }
        exportTimes[firstExportTime].objects.push_back(objectExpOp);
    }

    if (exportTimes.empty()) {
        return;
    }

    // We report progress differently depending on if we are exporting an animation or a single
    // frame. Animated : Report progress frame by frame. Non-Animated : Report progress per object
    // and then per transform (object are exported first, then their transforms).
    const bool animated = timeConfig.IsAnimated();

    // Get the resource strings only once per 3dsmax session.
    static const std::wstring framesProgMsg = GetString(IDS_EXPORT_FRAMES_PROGRESS_MESSAGE);
    static const std::wstring objectsProgMsg = GetString(IDS_EXPORT_OBJECTS_PROGRESS_MESSAGE);
    static const std::wstring transformProgMsg = GetString(IDS_EXPORT_TRANSFORMS_PROGRESS_MESSAGE);
    static const std::wstring objectPostExportProgMsg
        = GetString(IDS_EXPORT_POST_EXPORT_PROGRESS_MESSAGE);

    progress.SetTotal(animated ? exportTimes.size() : objectExportOps.size());
    progress.UpdateProgress(0, true, animated ? framesProgMsg.c_str() : objectsProgMsg.c_str());
    size_t frameProgress = 0;

    // Export at each time (exportTimes is ordered) - making sure that all objects & transforms
    // that need to be exported at each frames are exported in one go, and so we benefit from
    // the object state caching.
    for (auto& frame : exportTimes) {
        auto& req = frame.second;

        const auto maxTime = frame.first;
        const auto usdTime = animated ? pxr::UsdTimeCode(GetFrameFromTimeValue(maxTime))
                                      : pxr::UsdTimeCode::Default();
        // Write the object time samples we need at this frame...
        for (size_t i = 0; i < req.objects.size(); ++i) {
            auto& object = req.objects[i];

            const ExportTime expTime { maxTime, usdTime, object.firstFrame };
            object.write(expTime);

            // Non-animated case, report an object was exported.
            if (!animated) {
                progress.UpdateProgress(i + 1, true, objectsProgMsg.c_str());
            }

            // Figure out the next time sample we will need for this object, from the validity
            // interval of what we just exported.
            const auto nextTime = maxTime + timeStep;
            auto       interval = object.getValidityInterval(nextTime);
            const auto intervalEnd = interval.End();
            const auto intervalStart = interval.Start();

            TimeValue nextCandidateTime = 0;

            // Typically, we need to export at the start and end times of each object's interval,
            // unless the validity goes beyond the range of frames we are interested in.

            // If the object export interval goes up to, or beyond the range we are exporting,
            // consider the start time of the interval for export.
            if (intervalEnd >= endTime) {
                nextCandidateTime = intervalStart;
            }
            // Typical case, moving through the animation / object export intervals.
            else {
                // If we previously exported the start time of the interval, export the end time.
                if (maxTime == intervalStart) {
                    nextCandidateTime = intervalEnd;
                }
                // Otherwise, make sure we export the start time.
                else {
                    nextCandidateTime = intervalStart;
                }
            }

            // If for proper USD interpolation we should export a frame beyond the animation range,
            // make sure to at least export the object at the last time of the exported animation.
            if (nextCandidateTime > endTime) {
                nextCandidateTime = endTime;
            }

            // If we previously exported an equal or more advanced time, we are done.
            if (maxTime >= nextCandidateTime) {
                continue;
            }

            if (object.firstFrame) {
                object.firstFrame = false;
            }
            exportTimes[nextCandidateTime].objects.push_back(std::move(object));
        }
        req.objects.clear();

        // If we need to export transforms at this frame, do it!
        if (req.transform) {
            if (!animated) {
                progress.SetTotal(transformExportOps.size());
            }

            for (size_t i = 0; i < transformExportOps.size(); ++i) {
                auto& transformExpOp = transformExportOps[i];

                ExportTime expTime { maxTime, usdTime, false };
                transformExpOp.write(expTime, transformExpOp.usdGeomXFormOp);

                // Non-animated case - report that a transform was exported...
                if (!animated) {
                    progress.UpdateProgress(i + 1, true, transformProgMsg.c_str());
                }
            }

            // Animated case - report that a frame was exported...
            if (animated) {
                progress.UpdateProgress(frameProgress++, true, framesProgMsg.c_str());
            }
        }
    }

    progress.SetTotal(objectExportOps.size());

    int progressCounter = 0;
    for (const auto& objectExpOp : objectExportOps) {
        progress.UpdateProgress(progressCounter++, true, objectPostExportProgMsg.c_str());

        objectExpOp.postExport();
    }
}

} // namespace MAXUSD_NS_DEF
