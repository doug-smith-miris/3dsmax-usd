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
// MAX-MTLX-004: Symmetric MaterialX-target counterpart of
// LastResortUSDPreviewSurfaceWriter. Selected by ShaderWriterRegistry::Find
// when a 3ds Max material has no writer registered for the current target
// and the current target is MaterialX. Authors a minimal
// ND_standard_surface_surfaceshader whose `base_color` input carries the
// material's diffuse color (the same accessor the UsdPreviewSurface fallback
// uses), so every material — not just the two Class_IDs the MtlxShaderWriter
// covers — emits an `outputs:mtlx:surface` arc on export.
//
#pragma once

#include "ShaderWriter.h"
#include "ShaderWriterRegistry.h"
#include "WriteJobContext.h"

#include <pxr/pxr.h>
#include <pxr/usd/sdf/path.h>

PXR_NAMESPACE_OPEN_SCOPE

class LastResortMtlxShaderWriter : public MaxUsdShaderWriter
{
public:
    LastResortMtlxShaderWriter(
        Mtl*                   material,
        const SdfPath&         usdPath,
        MaxUsdWriteJobContext& jobCtx);

    static ContextSupport CanExport(const MaxUsd::USDSceneBuilderOptions&);

    void Write() override;
};

PXR_NAMESPACE_CLOSE_SCOPE
