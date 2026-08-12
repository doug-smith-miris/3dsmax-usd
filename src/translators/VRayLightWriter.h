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
#pragma once

#include <MaxUsd/Translators/primWriter.h>
#include <MaxUsd/Translators/writeJobContext.h>

#include <pxr/usd/usdLux/boundableLightBase.h>

PXR_NAMESPACE_OPEN_SCOPE

// MAX-LIT-002 — writer for V-Ray light classes (VRayLight, VRayIES, VRaySun).
// V-Ray is a third-party renderer plugin whose light classes derive from the
// stock 3ds Max LightObject / GenLight base, but never had a USD writer
// registered against them. Prior to this fix, every V-Ray light in a scene
// silently disappeared from the exported USD stage (185/185 VRayLights on
// the Spectrum Center arena baseline produced zero UsdLux prims and zero
// LightAPIs). This writer catches any V-Ray light class as a Fallback base
// writer, dispatches to the closest UsdLux prim type based on the light's
// class name + V-Ray-specific `type` param (Plane/Rect/Sphere/Disc/Dome/Mesh
// for VRayLight; distant/directional for VRaySun; disk-with-IES-profile for
// VRayIES), and populates the common attrs (color, intensity, on/off,
// shadow) from the standard GenLight interface plus V-Ray-specific attrs
// (size0/size1, temperature, IES file) probed via MAXScript.
class MaxUsdVRayLightWriter : public MaxUsdPrimWriter
{
public:
    static ContextSupport CanExport(INode* node, const MaxUsd::USDSceneBuilderOptions& exportArgs);

    MaxUsdVRayLightWriter(const MaxUsdWriteJobContext& jobCtx, INode* node);

    bool
    Write(UsdPrim& targetPrim, bool applyOffsetTransform, const MaxUsd::ExportTime& time) override;

    MaxUsd::XformSplitRequirement RequiresXformPrim() override;

    TfToken GetObjectPrimSuffix() override { return TfToken("Light"); };

    TfToken GetPrimType() override;

    WStr GetWriterName() override { return L"V-Ray light writer"; };
};

PXR_NAMESPACE_CLOSE_SCOPE
