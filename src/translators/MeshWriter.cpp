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

#include "VRayUnits.h"

#include <MaxUsd/MeshConversion/MeshConverter.h>
#include <MaxUsd/Translators/primWriter.h>
#include <MaxUsd/Translators/writeJobContext.h>
#include <MaxUsd/Utilities/Logging.h>
#include <MaxUsd/Utilities/NodeVisibility.h>

#include <pxr/base/gf/vec3f.h>
#include <pxr/pxr.h>
#include <pxr/usd/usdGeom/mesh.h>
#include <pxr/usd/usdGeom/primvarsAPI.h>
#include <pxr/usd/usdLux/lightAPI.h>
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

// MAX-LIT-GEOLIGHT-022: return the first VRayLightMtl in the tree (same walk as
// _TreeHasVRayLightMtl, which stays for the cheap boolean tests). We need the material ITSELF so
// the geometry light can be given that emitter's own intensity and colour. Relying on the renderer
// to derive them from the bound material does not work here: the emissive faces are a Multi/Sub-
// Object SUBSET, so the light prim has no directly bound material, and Karma falls back to
// intensity 1.0. On the Spectrum arena that left 230 of 240 geometry lights emitting nothing --
// the recessed ceiling cans and the twelve bowl light banks, i.e. the fixtures that light the seating
// bowl -- which is why indirect light measured ~3x too weak against the V-Ray reference.
Mtl* _FindVRayLightMtl(Mtl* mtl, int depth = 0)
{
    if (mtl == nullptr || depth > 8) {
        return nullptr;
    }
    MSTR className;
    mtl->GetClassName(className);
    if (wcsstr(className.data(), L"VRayLightMtl") != nullptr) {
        return mtl;
    }
    const int n = mtl->NumSubMtls();
    for (int i = 0; i < n; ++i) {
        if (Mtl* found = _FindVRayLightMtl(mtl->GetSubMtl(i), depth + 1)) {
            return found;
        }
    }
    return nullptr;
}

// MAX-LIT-GEOLIGHT-022: read a VRayLightMtl's colour + multiplier through the Max SDK param
// blocks, NOT through MAXScript.
//
// The first version of this called ExecuteMAXScriptScript, mirroring what MaxUsdVRayLightWriter does
// for V-Ray light OBJECTS. That crashed the export with EXCEPTION_ACCESS_VIOLATION reading 0x620.
// The light writer gets away with it because it runs over a handful of lights; the mesh write path
// runs over tens of thousands of meshes and is not guaranteed to be on the main thread, and the
// MAXScript interpreter is not thread-safe. Reading the param block directly has no such
// constraint, and avoids the scripting engine entirely.
//
// Parameter internal names differ between V-Ray builds, so match case-insensitively on a substring
// rather than pinning exact spellings.
struct VRayLightMtlProbe
{
    float multiplier = 1.0f;
    float r = 1.0f, g = 1.0f, b = 1.0f;
    bool  compensateExposure = false;
    bool  valid = false;
};

VRayLightMtlProbe _ProbeVRayLightMtl(Mtl* mtl)
{
    VRayLightMtlProbe probe;
    if (mtl == nullptr) {
        return probe;
    }
    const int numBlocks = mtl->NumParamBlocks();
    for (int b = 0; b < numBlocks; ++b) {
        IParamBlock2* pb = mtl->GetParamBlock(b);
        if (pb == nullptr) {
            continue;
        }
        ParamBlockDesc2* desc = pb->GetDesc();
        if (desc == nullptr) {
            continue;
        }
        for (int i = 0; i < desc->count; ++i) {
            const ParamDef& pd = desc->paramdefs[i];
            if (pd.int_name == nullptr) {
                continue;
            }
            const MCHAR* nm = pd.int_name;
            Interval iv = FOREVER;
            if (_wcsicmp(nm, _T("multiplier")) == 0) {
                float v = 1.0f;
                if (pb->GetValue(pd.ID, 0, v, iv)) {
                    probe.multiplier = v;
                    probe.valid = true;
                }
            } else if (_wcsicmp(nm, _T("color")) == 0) {
                Point3 c(1.f, 1.f, 1.f);
                if (pb->GetValue(pd.ID, 0, c, iv)) {
                    // Max exposes colours as 0-1 through the SDK but 0-255 through MAXScript, and
                    // V-Ray builds have differed. Normalise only when the values clearly exceed 1.
                    const float maxc = (c.x > c.y ? (c.x > c.z ? c.x : c.z) : (c.y > c.z ? c.y : c.z));
                    const float k = (maxc > 1.001f) ? (1.0f / 255.0f) : 1.0f;
                    probe.r = c.x * k;
                    probe.g = c.y * k;
                    probe.b = c.z * k;
                    probe.valid = true;
                }
            } else if (wcsstr(nm, _T("ompensate")) != nullptr) { // compensateExposure / compensate_exposure
                int v = 0;
                if (pb->GetValue(pd.ID, 0, v, iv)) {
                    probe.compensateExposure = (v != 0);
                }
            }
        }
    }
    return probe;
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

// MAX-MTLX-005 (mesh-light guard): true if a VRayLightMtl in the tree has a texture map assigned
// -- i.e. it is a textured DISPLAY (video board, LED ribbon, backlit signage) rather than a
// uniform light fixture. A textured emitter driven as a whole-mesh geometry light renders as a
// flat WHITE panel in Karma (the geometry light samples a single averaged color, not the content
// image), which is why the center-hung videoboard and ribbon boards blew out. Routed instead
// through the emission surface shader, the content image shows correctly. Uniform fixtures
// (recessed cans, bulbs, light banks) have no texmap, so they keep the geometry light and still
// illuminate the room.
bool _TreeHasTexturedVRayLight(Mtl* mtl, int depth = 0)
{
    if (mtl == nullptr || depth > 8) {
        return false;
    }
    MSTR className;
    mtl->GetClassName(className);
    if (wcsstr(className.data(), L"VRayLightMtl") != nullptr) {
        const int nt = mtl->NumSubTexmaps();
        for (int i = 0; i < nt; ++i) {
            if (mtl->GetSubTexmap(i) != nullptr) {
                return true;
            }
        }
    }
    const int n = mtl->NumSubMtls();
    for (int i = 0; i < n; ++i) {
        if (_TreeHasTexturedVRayLight(mtl->GetSubMtl(i), depth + 1)) {
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
    // MAX-LIT-GEOLIGHT-022: a HIDDEN node must never become a light. Without this guard the export
    // turns hidden emitters into geometry lights that illuminate the scene from nowhere -- the two
    // white medallions on the studio's `Z-STUFF` layer (z-prefixed layers are never meant to render)
    // came through as invisible prims still emitting, lighting the Dr Pepper club wall with no
    // visible source. UseUSDVisibility already authors visibility=invisible for these; the light has
    // to go too, because a UsdLux light is not suppressed by the visibility of the mesh it rides on.
    // Match the exporter's OWN visibility test exactly: USDSceneBuilder authors
    // visibility=invisible from `node->IsNodeHidden()` with no argument. Passing TRUE asks
    // "hidden FROM THE RENDERER", which consults the .renderable flag -- and in this scene
    // .renderable is true on all 29,295 objects, so TRUE reported every node as visible and the
    // guard never fired. The two hidden medallions still became geometry lights as a result.
    // MAX-VIS-027: include layer-hidden. Without it a mesh hidden only by its layer still
    // becomes a geometry light, which is the exact failure this guard exists to prevent -- an
    // invisible prim lighting the scene with no visible source.
    const bool nodeHidden = MaxUsd::NodeVisibility::IsHiddenIncludingLayer(sourceNode);
    if (time.IsFirstFrame() && !nodeHidden && _TreeHasVRayLightMtl(sourceNode->GetMtl())
        && !_TreeHasTransparentMtl(sourceNode->GetMtl())
        && !_TreeHasTexturedVRayLight(sourceNode->GetMtl())) {
        auto meshPrim = prim.GetPrim();
        if (meshPrim) {
            pxr::UsdLuxMeshLightAPI::Apply(meshPrim);
            // intensity/color live on UsdLuxLightAPI, not MeshLightAPI. MeshLightAPI declares
            // LightAPI as a BUILT-IN api schema, so applying it auto-applies LightAPI and this
            // wrapper is valid -- but the attributes must be created through LightAPI.
            pxr::UsdLuxLightAPI lightAPI(meshPrim);

            // MAX-LIT-GEOLIGHT-022: author the light's own intensity and colour rather than
            // leaving them at the schema defaults. Applying the API alone yields intensity 1.0,
            // because the emissive faces are a Multi/Sub-Object subset and the light prim has no
            // directly bound material for the renderer to derive emission from. Same converter as
            // the light-object and emissive-surface paths, so one emitter cannot contribute
            // different energy depending on which writer handled it.
            //
            // Treated as units=0 (the artistic-scale path), NOT as unitless pass-through. This
            // matters and is easy to get backwards: the emissive SURFACE path already writes
            // `mult * kUnits0Gain` (LENS-ON_WARM at mult 50 becomes emission 190), so a geometry
            // light that passed the multiplier through unchanged would emit 3.8x LESS than the very
            // surface it represents. The light and its surface must agree -- that invariant is the
            // whole point of sharing one converter.
            const auto probe = _ProbeVRayLightMtl(_FindVRayLightMtl(sourceNode->GetMtl()));
            if (probe.valid) {
                const float intensity
                    = MaxUsdVRay::UnitsToNits(
                        probe.multiplier,
                        /*units*/ 0,
                        /*hasUnits*/ true,
                        probe.compensateExposure);
                lightAPI.CreateIntensityAttr().Set(intensity);
                lightAPI.CreateColorAttr().Set(pxr::GfVec3f(probe.r, probe.g, probe.b));
                // Logged at Warn so it lands in the export log regardless of level -- this line is
                // how a lighting regression gets diagnosed without re-instrumenting the exporter.
                MaxUsd::Log::Warn(
                    L"[GEOLIGHT] {0} intensity={1} color=({2},{3},{4}) mult={5} compensateExposure={6}",
                    sourceNode->GetName(),
                    intensity,
                    probe.r,
                    probe.g,
                    probe.b,
                    probe.multiplier,
                    probe.compensateExposure ? 1 : 0);
            } else {
                // Never silently emit at the default: an unreadable emitter is a fact worth seeing.
                MaxUsd::Log::Warn(
                    L"[GEOLIGHT] {0} — could not read its VRayLightMtl; light left at schema "
                    L"default intensity (it will contribute almost nothing)",
                    sourceNode->GetName());
            }
        }
    }
    return true;
}

PXR_NAMESPACE_CLOSE_SCOPE