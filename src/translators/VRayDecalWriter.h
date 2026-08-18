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

PXR_NAMESPACE_OPEN_SCOPE

// MAX-VRAYDECAL-021 — writer for VRayDecal, a PROJECTOR object rather than a surface.
//
// A VRayDecal carries its own material and, at render time, projects that material onto whatever
// geometry lies inside its box. The graphic exists ONLY as that projection: the receiving surface
// keeps whatever material it already had. On the Spectrum Center arena the Buzz City / Dr Pepper
// club graphics are 5 VRayDecals, and their receivers really are bound to the Revit placeholder
// `*TEMP-GRAY` in the .max file itself — verified in Max, not inferred.
//
// With no writer registered for the class, MaxUsdMeshWriter claimed each decal as a Fallback
// (VRayDecal is TriObject-convertible) and exported its GIZMO: an 8-vertex bounding box carrying
// the decal material on all six faces. So the graphic was lost twice over — the receiver rendered
// as bare placeholder grey (the white hexagon Doug spotted) and a meaningless box floated in the
// scene wearing the artwork.
//
// This writer emits what the projection actually produces: a quad conformed to the receiving
// surface. The receiver is found by raycasting the decal's rectangle corners along its projection
// axis, and the resulting patch is offset a hair along the surface normal so it wins the depth
// fight without z-fighting. UVs run 0..1 across the rectangle, which is the mapping the decal
// material already expects, so the existing MaterialX shader path (including the opacity map that
// masks the artwork to its letters) needs no change.
//
// The raycast runs in MAXScript via ExecuteMAXScriptScript, matching the probe pattern
// MaxUsdVRayLightWriter established for version-specific V-Ray parameters: VRayDecal's property
// names differ between V-Ray builds, and MAXScript's intersectRayEx is both simpler and better
// tested than hand-rolling scene traversal + GeomObject::IntersectRay in the writer.
class MaxUsdVRayDecalWriter : public MaxUsdPrimWriter
{
public:
    static ContextSupport CanExport(INode* node, const MaxUsd::USDSceneBuilderOptions& exportArgs);

    MaxUsdVRayDecalWriter(const MaxUsdWriteJobContext& jobCtx, INode* node);

    bool
    Write(UsdPrim& targetPrim, bool applyOffsetTransform, const MaxUsd::ExportTime& time) override;

    TfToken GetPrimType() override;

    /**
     * \brief Decals must never be instanced.
     *
     * Automatic instancing authors the mesh ONCE into a `_class_` prototype that every instance
     * inherits, and -- per InstancingRequirement::Default -- "only the first instance will hit the
     * Write() method". That is sound for an ordinary object, whose geometry is a pure function of
     * the object itself. A decal's patch is not: it is conformed to whatever surface it is
     * projected onto, so two nodes sharing one VRayDecal object can still need different patches.
     *
     * Measured on the arena: BOWL-UPPER_LOGO_DR_PEPP_WHITE001/002 share an object and were
     * instanced, so WHITE002 silently reused WHITE001's patch. Their own conforms differ (local z
     * 0.324539 vs 0.325043) -- only by 0.15 mm here, because the club walls are symmetric, but
     * that is luck, not correctness. Two instanced decals on differently-angled walls would put a
     * visibly wrong patch on one of them.
     */
    MaxUsd::InstancingRequirement RequiresInstancing() override
    {
        return MaxUsd::InstancingRequirement::NoInstancing;
    }

    WStr GetWriterName() override { return L"V-Ray decal writer"; };
};

PXR_NAMESPACE_CLOSE_SCOPE
