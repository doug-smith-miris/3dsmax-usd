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
#pragma once

#include <MaxUsd/Translators/primWriter.h>
#include <MaxUsd/Translators/writeJobContext.h>

#include <pxr/pxr.h>
#include <pxr/usd/usd/prim.h>

PXR_NAMESPACE_OPEN_SCOPE

// Writes the 3ds Max **legacy** standard light classes that the
// installed PhotometricLightWriter does NOT cover (it gates on
// LIGHTSCAPE_LIGHT_CLASS and therefore drops every legacy light
// silently). See MAX-LIT-001 in
// `doc/translation-mapping.md` for the full catalog entry.
//
// Currently in scope: Omnilight (OMNI_LIGHT_CLASS_ID) and the
// legacy Skylight (SKY_LIGHT_CLASS_ID). Spot / Directional lights
// are tracked as follow-on bites (see candidate missions on the
// MAX-LIT-001 envelope).
class MaxUsdLegacyLightWriter : public MaxUsdPrimWriter
{
public:
    static ContextSupport
    CanExport(INode* node, const MaxUsd::USDSceneBuilderOptions& exportArgs);

    MaxUsdLegacyLightWriter(const MaxUsdWriteJobContext& jobCtx, INode* node);

    bool Write(UsdPrim& targetPrim, bool applyOffsetTransform, const MaxUsd::ExportTime& time)
        override;

    MaxUsd::XformSplitRequirement RequiresXformPrim() override;

    TfToken GetObjectPrimSuffix() override { return TfToken("Light"); };

    TfToken GetPrimType() override;

    WStr GetWriterName() override { return L"Legacy light writer"; };
};

PXR_NAMESPACE_CLOSE_SCOPE
