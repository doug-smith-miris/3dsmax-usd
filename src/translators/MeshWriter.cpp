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
#include <MaxUsd/Utilities/TranslationUtils.h>

#include <pxr/base/gf/vec3f.h>
#include <pxr/pxr.h>
#include <pxr/usd/usdGeom/mesh.h>
#include <pxr/usd/usdGeom/primvarsAPI.h>
#include <pxr/usd/usdLux/lightAPI.h>
#include <pxr/usd/usdLux/meshLightAPI.h>

#include <Materials/mtl.h>
#include <max.h>
#include <maxscript/maxscript.h>

#include <sstream>
#include <string>

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

// MAX-LIT-GEOLIGHT-022: read a VRayLightMtl's colour + multiplier via MAXScript. V-Ray is a
// third-party plugin whose SDK we cannot link, and the property set differs between builds, so this
// follows the probe pattern MaxUsdVRayLightWriter already uses for V-Ray light objects. Returns
// false if the material could not be read at all, in which case the caller leaves the light alone
// rather than authoring a guess.
struct VRayLightMtlProbe
{
    float multiplier = 1.0f;
    float r = 1.0f, g = 1.0f, b = 1.0f;
    bool  compensateExposure = false;
    bool  valid = false;
};

static const TSTR discoverVRayLightMtlFn = LR"(
    fn discoverVRayLightMtl mtlAnimHandle = (
        local m = getAnimByHandle mtlAnimHandle
        local result = ""
        if m == undefined then return result
        if (isProperty m #multiplier) do result += ("multiplier|" + ((getProperty m #multiplier) as string) + "\n")
        if (isProperty m #color) do (
            local c = getProperty m #color
            result += ("color|" + (c.r as string) + "," + (c.g as string) + "," + (c.b as string) + "\n")
        )
        if (isProperty m #compensateExposure) do result += ("compensate|" + ((getProperty m #compensateExposure) as string) + "\n")
        result
    )
    discoverVRayLightMtl )";

VRayLightMtlProbe _ProbeVRayLightMtl(Mtl* mtl)
{
    VRayLightMtlProbe probe;
    if (mtl == nullptr) {
        return probe;
    }
    const AnimHandle handle = ::Animatable::GetHandleByAnim(mtl);
    if (handle == 0) {
        return probe;
    }
    FPValue rvalue;
    rvalue.Init();
    std::wstringstream ss;
    ss << discoverVRayLightMtlFn << handle << L'\0';
    ExecuteMAXScriptScript(ss.str().c_str(), MAXScript::ScriptSource::Dynamic, false, &rvalue);
    const auto manifest = MaxUsd::MaxStringToUsdString(rvalue.s);
    if (manifest.empty()) {
        return probe;
    }
    std::stringstream lines(manifest);
    std::string       line;
    while (std::getline(lines, line)) {
        while (!line.empty() && (line.back() == '\r' || line.back() == ' ')) {
            line.pop_back();
        }
        const auto pipe = line.find('|');
        if (pipe == std::string::npos) {
            continue;
        }
        const auto key = line.substr(0, pipe);
        const auto val = line.substr(pipe + 1);
        try {
            if (key == "multiplier") {
                probe.multiplier = std::stof(val);
                probe.valid = true;
            } else if (key == "color") {
                const auto c1 = val.find(',');
                const auto c2 = val.find(',', c1 + 1);
                if (c1 != std::string::npos && c2 != std::string::npos) {
                    // Max colours are 0-255; UsdLux inputs:color is 0-1.
                    probe.r = std::stof(val.substr(0, c1)) / 255.0f;
                    probe.g = std::stof(val.substr(c1 + 1, c2 - c1 - 1)) / 255.0f;
                    probe.b = std::stof(val.substr(c2 + 1)) / 255.0f;
                    probe.valid = true;
                }
            } else if (key == "compensate") {
                probe.compensateExposure = (val == "true" || val == "True");
            }
        } catch (...) {
            // leave the field at its default; `valid` stays false unless something parsed
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
    if (time.IsFirstFrame() && _TreeHasVRayLightMtl(sourceNode->GetMtl())
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
                    = MaxUsdVRay::UnitsToNits(probe.multiplier, /*units*/ 0, /*hasUnits*/ true);
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