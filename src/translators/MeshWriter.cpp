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
#include "MeshWriter.h"

#include <MaxUsd/MeshConversion/MeshConverter.h>
#include <MaxUsd/Translators/primWriter.h>
#include <MaxUsd/Translators/writeJobContext.h>

#include <pxr/pxr.h>
#include <pxr/usd/usdGeom/mesh.h>
#include <pxr/usd/usdGeom/primvarsAPI.h>
#include <pxr/usd/usdLux/meshLightAPI.h>

#include <Materials/mtl.h>
#include <max.h>

PXR_NAMESPACE_OPEN_SCOPE

namespace {
// MAX-MTLX-005 (mesh-light half): true if the material tree contains a V-Ray self-illuminated
// material (VRayLightMtl), directly or as a Multi/Sub-Object sub-material or inside a
// VRayOverrideMtl. The emissive geometry in arch-viz scenes is usually a FACE SUBSET of a larger
// mesh (a Multi/Sub-Object slot), so we test the whole tree and light the parent mesh.
bool _TreeHasVRayLightMtl(Mtl* mtl, int depth = 0)
{
    if (mtl == nullptr || depth > 8) {
        return false;
    }
    MSTR className;
    mtl->GetClassName(className);
    if (wcsstr(className.data(), L"VRayLightMtl") != nullptr) {
        return true;
    }
    const int n = mtl->NumSubMtls();
    for (int i = 0; i < n; ++i) {
        if (_TreeHasVRayLightMtl(mtl->GetSubMtl(i), depth + 1)) {
            return true;
        }
    }
    return false;
}

// MAX-MTLX-005 (mesh-light guard): true if the material tree contains a transparent/refractive
// material. The whole-mesh MeshLightAPI light is REJECTED by Karma (and other Hydra renderers)
// when the source mesh has transparent or cutout faces -- husk logs "Failed to create geometry
// light because source object has stencil map or partial opacity" -- and the mesh then renders
// as a solid white fallback. Arch-viz props such as a basketball goal carry a small illuminated
// ad panel (VRayLightMtl) in the SAME Multi/Sub-Object as transparent backboard glass (a VRayMtl
// with refraction) and an alpha-cutout net, so applying a geometry light to the whole goal makes
// the net/rim/backboard glow white. Only opaque emitters (ribbon boards, LED video walls, light
// fixtures) become geometry lights; transparent mixed props fall back to their emission surface
// shader, so the illuminated faces still emit without lighting the transparent parts.
bool _TreeHasTransparentMtl(Mtl* mtl, int depth = 0)
{
    if (mtl == nullptr || depth > 8) {
        return false;
    }
    if (mtl->GetXParency() > 0.001f) {
        return true;
    }
    const int n = mtl->NumSubMtls();
    for (int i = 0; i < n; ++i) {
        if (_TreeHasTransparentMtl(mtl->GetSubMtl(i), depth + 1)) {
            return true;
        }
    }
    return false;
}
} // namespace

MaxUsdMeshWriter::MaxUsdMeshWriter(const MaxUsdWriteJobContext& jobCtx, INode* node)
    : MaxUsdPrimWriter(jobCtx, node)
{
}

MaxUsdPrimWriter::ContextSupport
MaxUsdMeshWriter::CanExport(INode* node, const MaxUsd::USDSceneBuilderOptions& exportArgs)
{
    if (!exportArgs.GetTranslateMeshes()) {
        return ContextSupport::Unsupported;
    }
    const auto object = node->EvalWorldState(exportArgs.GetResolvedTimeConfig().GetStartTime()).obj;
    if (object->CanConvertToType({ TRIOBJ_CLASS_ID, 0 })
        && object->SuperClassID() != SHAPE_CLASS_ID) {
        return ContextSupport::Fallback;
    }
    return ContextSupport::Unsupported;
}

MaxUsd::XformSplitRequirement MaxUsdMeshWriter::RequiresXformPrim()
{
    if (!GetExportArgs().GetAllowNestedGprims() && GetNode()->NumberOfChildren() > 0) {
        return MaxUsd::XformSplitRequirement::Always;
    }
    return !GetExportArgs().GetMeshConversionOptions().GetBakeObjectOffsetTransform()
        ? MaxUsd::XformSplitRequirement::ForOffsetObjects
        : MaxUsd::XformSplitRequirement::Never;
}

bool MaxUsdMeshWriter::Write(
    UsdPrim&                  targetPrim,
    bool                      applyOffsetTransform,
    const MaxUsd::ExportTime& time)
{
    INode* sourceNode = GetNode();

    const auto timeConfig = GetExportArgs().GetResolvedTimeConfig();

    // Currently, 3dsMax Shapes (for example, splines), are converted to poly prior to export. This
    // may not always give the best results. Until we can provide smarter results, log a warning
    // (only once, on the first frame).
    if (time.IsFirstFrame()) {
        const auto object = sourceNode->EvalWorldState(time.GetMaxTime()).obj;
        if (object->SuperClassID() == SHAPE_CLASS_ID) {
            MaxUsd::Log::Warn(
                L"{0} is a Shape, it will be converted to Poly prior to export.",
                sourceNode->GetName());
        }
    }
    MaxUsd::MeshConverter meshConverter;
    pxr::UsdGeomMesh      prim = meshConverter.ConvertToUSDMesh(
        sourceNode,
        targetPrim.GetStage(),
        targetPrim.GetPrimPath(),
        GetExportArgs().GetMeshConversionOptions(),
        applyOffsetTransform,
        timeConfig.IsAnimated(),
        time,
        GetExportArgs().GetTransformFormat());

    // MAX-MTLX-005 (mesh-light half): if any face of this mesh uses a VRayLightMtl (self-illum)
    // material, mark the whole mesh as a UsdLux geometry light. Karma (and other USD renderers
    // that support geometry lights) then importance-sample the emissive faces as actual lights
    // instead of relying on brute-force emissive-surface hits — which is how V-Ray's light cache
    // fills the room from these emitters. MeshLightAPI derives the emission per-face from the bound
    // material, so only the emissive subset actually emits. Applied once, on the first frame.
    if (time.IsFirstFrame() && _TreeHasVRayLightMtl(sourceNode->GetMtl())
        && !_TreeHasTransparentMtl(sourceNode->GetMtl())) {
        auto meshPrim = prim.GetPrim();
        if (meshPrim) {
            pxr::UsdLuxMeshLightAPI::Apply(meshPrim);
        }
    }
    return true;
}

PXR_NAMESPACE_CLOSE_SCOPE