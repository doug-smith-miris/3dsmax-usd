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
#include "VRayDecalWriter.h"

#include <MaxUsd/Translators/primWriter.h>
#include <MaxUsd/Translators/writeJobContext.h>
#include <MaxUsd/Utilities/Logging.h>
#include <MaxUsd/Utilities/MaxSupportUtils.h>
#include <MaxUsd/Utilities/TranslationUtils.h>
#include <MaxUsd/Utilities/TypeUtils.h>

#include <pxr/base/gf/vec2f.h>
#include <pxr/base/gf/vec3f.h>
#include <pxr/base/tf/token.h>
#include <pxr/pxr.h>
#include <pxr/usd/usdGeom/mesh.h>
#include <pxr/usd/usdGeom/primvarsAPI.h>
#include <pxr/usd/usdGeom/tokens.h>

#include <maxscript/maxscript.h>

#include <sstream>
#include <string>
#include <vector>

PXR_NAMESPACE_OPEN_SCOPE

namespace {

// MAX-VRAYDECAL-021: probe + CONFORM a VRayDecal in MAXScript, returning the projected patch.
//
// The geometry work happens here rather than in C++ for two reasons. First, VRayDecal's parameter
// names are version-specific — this V-Ray build calls the rectangle `width` x `length` (not
// `height`), with `projection_depth` / `projection_offset` / `Bend` alongside — and MAXScript
// property probing degrades gracefully across versions where linking a plugin SDK would not.
// Second, the conform is a raycast against arbitrary scene geometry, and MAXScript's
// intersectRayEx is both simpler and far better tested than hand-rolled GeomObject::IntersectRay
// plus scene traversal inside a writer.
//
// The projection is ORTHOGRAPHIC along the decal's local Z, so the patch is exactly the four
// rectangle corners pushed along that axis onto the receiving surface. Two acceptance tests keep
// the right surface — both were derived from measured arena data (C:\suts\decal_probe2.txt), not
// assumed:
//   * WITHIN projection_depth. The Dr Pepper decals see their real receiver 0.33 ft away and a
//     second wall 4.5-9.5 ft across the room; the depth budget (2.0 ft) separates them.
//   * FACING the decal, dot(normal, castDirection) < 0. This one is defensive rather than
//     measured: every far hit in the arena data is rejected by depth alone. It guards the case a
//     solid receiver creates -- a ray entering a wall also exits through its back face, and an
//     exit hit must never be mistaken for a surface the decal can land on.
// The direction is NOT fixed: measured receivers sit on +Z for the Dr Pepper and Hornets decals
// and on -Z for BUZZ/CITY, so both are cast and the nearest qualifying hit wins.
//
// Returned manifest is pipe-delimited `key|value`, one per line, matching the probe convention
// MaxUsdVRayLightWriter established.
static const TSTR discoverMaxVrayDecalFn = LR"(
    fn discoverMaxVrayDecal nodeAnimHandle = (
        local n = getAnimByHandle nodeAnimHandle
        local result = ""
        if n == undefined then return result
        local obj = n
        if (isProperty n #baseobject) then obj = n.baseobject
        if obj == undefined then return result
        result += ("className|" + ((classOf obj) as string) + "\n")

        local w = 0.0
        local h = 0.0
        local dep = 0.0
        local poff = 0.0
        local bend = 0.0
        for pn in #(#width, #decal_width, #size_x) do (
            if w == 0.0 and (isProperty obj pn) do w = ((getProperty obj pn) as float)
        )
        for pn in #(#length, #height, #decal_height, #size_y) do (
            if h == 0.0 and (isProperty obj pn) do h = ((getProperty obj pn) as float)
        )
        for pn in #(#projection_depth, #depth) do (
            if dep == 0.0 and (isProperty obj pn) do dep = ((getProperty obj pn) as float)
        )
        if (isProperty obj #projection_offset) do poff = ((getProperty obj #projection_offset) as float)
        if (isProperty obj #Bend) do bend = ((getProperty obj #Bend) as float)
        result += ("width|" + (w as string) + "\n")
        result += ("length|" + (h as string) + "\n")
        result += ("depth|" + (dep as string) + "\n")
        result += ("bend|" + (bend as string) + "\n")
        if w <= 0.0 or h <= 0.0 then (
            result += "err|decal rectangle has no size\n"
            return result
        )
        if dep <= 0.0 do dep = 2.0

        local tm = n.transform
        local xax = normalize tm.row1
        local yax = normalize tm.row2
        local zax = normalize tm.row3
        local ctr = tm.row4 + (zax * poff)
        local hw = w / 2.0
        local hh = h / 2.0
        local bb = nodeGetBoundingBox n (matrix3 1)

        local cands = #()
        for o in objects do (
            local skip = (o == n)
            if (matchPattern (toLower ((classof o) as string)) pattern:"*decal*") do skip = true
            if not skip do (
                try (
                    local ob = nodeGetBoundingBox o (matrix3 1)
                    if (ob[2].x >= bb[1].x and ob[1].x <= bb[2].x and ob[2].y >= bb[1].y and ob[1].y <= bb[2].y and ob[2].z >= bb[1].z and ob[1].z <= bb[2].z) do append cands o
                ) catch ()
            )
        )
        result += ("cands|" + (cands.count as string) + "\n")

        local ux = #(-1.0, 1.0, 1.0, -1.0)
        local uy = #(-1.0, -1.0, 1.0, 1.0)
        local hitP = #(undefined, undefined, undefined, undefined)
        local hitN = #(undefined, undefined, undefined, undefined)
        local recvName = ""
        local recvDist = 0.0
        local nHits = 0
        for i = 1 to 4 do (
            local org = ctr + (xax * (ux[i] * hw)) + (yax * (uy[i] * hh))
            local bestD = 1e30
            local bestP = undefined
            local bestN = undefined
            local bestName = ""
            for sgn in #(-1.0, 1.0) do (
                local dir = zax * sgn
                local r = ray org dir
                for c in cands do (
                    try (
                        local hit = intersectRayEx c r
                        if hit != undefined do (
                            local hp = hit[1].pos
                            local hn = hit[1].dir
                            local dd = distance org hp
                            if dd <= (dep + 0.001) and (dot hn dir) < 0.0 and dd < bestD do (
                                bestD = dd
                                bestP = hp
                                bestN = hn
                                bestName = c.name
                            )
                        )
                    ) catch ()
                )
            )
            if bestP != undefined do (
                hitP[i] = bestP
                hitN[i] = bestN
                nHits += 1
                if recvName == "" do (
                    recvName = bestName
                    recvDist = bestD
                )
            )
        )
        result += ("hits|" + (nHits as string) + "\n")
        result += ("receiver|" + recvName + "\n")
        result += ("dist|" + (recvDist as string) + "\n")

        local nrmAvg = [0,0,0]
        local cen = [0,0,0]
        if nHits > 0 then (
            for i = 1 to 4 do (
                if hitP[i] != undefined do (
                    nrmAvg += hitN[i]
                    cen += hitP[i]
                )
            )
            nrmAvg = normalize (nrmAvg / nHits)
            cen = cen / nHits
            for i = 1 to 4 do (
                if hitP[i] == undefined do (
                    local org = ctr + (xax * (ux[i] * hw)) + (yax * (uy[i] * hh))
                    local dn = dot nrmAvg zax
                    if (abs dn) < 1e-6 then hitP[i] = org
                    else hitP[i] = org + (zax * ((dot nrmAvg (cen - org)) / dn))
                    hitN[i] = nrmAvg
                )
            )
        ) else (
            result += "fallback|no receiver within projection depth\n"
            nrmAvg = -zax
            for i = 1 to 4 do (
                hitP[i] = ctr + (xax * (ux[i] * hw)) + (yax * (uy[i] * hh))
                hitN[i] = nrmAvg
            )
        )

        local inv = inverse tm
        local lnv = normalize (((cen + nrmAvg) * inv) - (cen * inv))

        -- UV HANDEDNESS. The C++ side always assigns st (0,0) (1,0) (1,1) (0,1) to the four corners
        -- in emitted order, so the corner order here decides which way the artwork reads. Viewed
        -- from the side the patch normal points to, u must increase to the RIGHT, i.e. along
        -- cross(up, n). In the decal's own frame up is +Y and n is +/-Z, so:
        --     n = +Z  ->  u runs along +X   (emit corners -x,+x,+x,-x as below)
        --     n = -Z  ->  u runs along -X   (emit them mirrored)
        -- Assigning +X unconditionally rendered Dr Pepper and Hornets as MIRROR IMAGES -- caught
        -- only by looking at the render, since the patch geometry and UVs were each individually
        -- correct. BUZZ and CITY happened to sit on +Z receivers and looked right, which is exactly
        -- how a handedness bug hides.
        local ord = #(1, 2, 3, 4)
        if lnv.z < 0.0 do ord = #(2, 1, 4, 3)

        -- LIFT off the receiver. 0.02 ft (~6 mm) rather than the 0.002 ft (0.6 mm) first shipped.
        -- This is PRECAUTIONARY, not a fix for an observed defect: 0.002 ft rendered the Dr Pepper
        -- patch cleanly with no z-fighting, and a test at 0.02 changed nothing (the frame used to
        -- judge it turned out to be occluded, so it proved neither way). 0.6 mm is simply a thin
        -- margin against renderer ray bias at these scene coordinates (~220 ft from origin), and
        -- 6 mm is still under 12% of the CLOSEST measured receiver standoff (0.169 ft), so the
        -- patch cannot read as floating.
        local eps = 0.02
        if (dep * 0.01) > eps do eps = dep * 0.01
        local qs = ""
        for i = 1 to 4 do (
            local p = (hitP[ord[i]] + (hitN[ord[i]] * eps)) * inv
            if i > 1 do qs += ";"
            qs += ((formattedPrint p.x format:".6f") + "," + (formattedPrint p.y format:".6f") + "," + (formattedPrint p.z format:".6f"))
        )
        result += ("quad|" + qs + "\n")
        result += ("flipU|" + ((lnv.z < 0.0) as string) + "\n")
        result += ("nrm|" + (formattedPrint lnv.x format:".6f") + "," + (formattedPrint lnv.y format:".6f") + "," + (formattedPrint lnv.z format:".6f") + "\n")
        result
    )
    discoverMaxVrayDecal )";

struct VRayDecalProbe
{
    std::string          className;
    std::string          receiver;
    std::string          error;
    float                width = 0.f;
    float                length = 0.f;
    float                depth = 0.f;
    float                bend = 0.f;
    float                dist = 0.f;
    int                  hits = 0;
    int                  candidates = 0;
    bool                 fellBack = false;
    std::vector<GfVec3f> quad;   // 4 object-space corners, UV order (0,0) (1,0) (1,1) (0,1)
    GfVec3f              normal { 0.f, 0.f, 1.f };
};

// Parse "x,y,z" (repeated, ';'-separated) into object-space points.
bool _ParseQuad(const std::string& value, std::vector<GfVec3f>& out)
{
    out.clear();
    std::stringstream corners(value);
    std::string       corner;
    while (std::getline(corners, corner, ';')) {
        std::stringstream comps(corner);
        std::string       comp;
        float             xyz[3] = { 0.f, 0.f, 0.f };
        int               i = 0;
        while (std::getline(comps, comp, ',') && i < 3) {
            try {
                xyz[i] = std::stof(comp);
            } catch (...) {
                return false;
            }
            ++i;
        }
        if (i != 3) {
            return false;
        }
        out.emplace_back(xyz[0], xyz[1], xyz[2]);
    }
    return out.size() == 4;
}

VRayDecalProbe _ProbeVRayDecal(INode* node)
{
    VRayDecalProbe probe;
    if (node == nullptr) {
        return probe;
    }
    const AnimHandle animHandle = ::Animatable::GetHandleByAnim(node);
    if (animHandle == 0) {
        return probe;
    }
    FPValue rvalue;
    rvalue.Init();
    std::wstringstream ss;
    ss << discoverMaxVrayDecalFn << animHandle << L'\0';
    ExecuteMAXScriptScript(ss.str().c_str(), MAXScript::ScriptSource::Dynamic, false, &rvalue);
    const auto manifest = MaxUsd::MaxStringToUsdString(rvalue.s);
    if (manifest.empty()) {
        probe.error = "MAXScript probe returned nothing";
        return probe;
    }

    std::stringstream lines(manifest);
    std::string       line;
    while (std::getline(lines, line)) {
        while (!line.empty() && (line.back() == '\r' || line.back() == ' ' || line.back() == '\t')) {
            line.pop_back();
        }
        const auto pipe = line.find('|');
        if (pipe == std::string::npos) {
            continue;
        }
        const auto key = line.substr(0, pipe);
        const auto val = line.substr(pipe + 1);
        try {
            if (key == "className") {
                probe.className = val;
            } else if (key == "width") {
                probe.width = std::stof(val);
            } else if (key == "length") {
                probe.length = std::stof(val);
            } else if (key == "depth") {
                probe.depth = std::stof(val);
            } else if (key == "bend") {
                probe.bend = std::stof(val);
            } else if (key == "dist") {
                probe.dist = std::stof(val);
            } else if (key == "hits") {
                probe.hits = std::stoi(val);
            } else if (key == "cands") {
                probe.candidates = std::stoi(val);
            } else if (key == "receiver") {
                probe.receiver = val;
            } else if (key == "fallback") {
                probe.fellBack = true;
            } else if (key == "err") {
                probe.error = val;
            } else if (key == "quad") {
                if (!_ParseQuad(val, probe.quad)) {
                    probe.error = "unparseable quad: " + val;
                }
            } else if (key == "nrm") {
                std::vector<GfVec3f> one;
                if (_ParseQuad(val + ";0,0,0;0,0,0;0,0,0", one) && one.size() == 4) {
                    probe.normal = one[0];
                }
            }
        } catch (...) {
            probe.error = "bad numeric value for key " + key;
        }
    }
    return probe;
}

} // anonymous namespace

MaxUsdPrimWriter::ContextSupport MaxUsdVRayDecalWriter::CanExport(
    INode*                                 node,
    const MaxUsd::USDSceneBuilderOptions& exportArgs)
{
    if (node == nullptr) {
        return ContextSupport::Unsupported;
    }
    const auto object = node->EvalWorldState(exportArgs.GetResolvedTimeConfig().GetStartTime()).obj;
    if (object == nullptr) {
        return ContextSupport::Unsupported;
    }
    if (object->SuperClassID() != GEOMOBJECT_CLASS_ID) {
        return ContextSupport::Unsupported;
    }
    // Gate on the class NAME rather than a hard-coded Class_ID, matching how MeshWriter detects
    // VRayLightMtl. V-Ray ships its own ClassIDs and we cannot link its SDK to name them
    // symbolically; the observed VRayDecal ID on this build is #(1502564550L, 799867463L), but
    // pinning that would silently stop matching on a V-Ray upgrade -- exactly the failure mode
    // that let decals fall through to MeshWriter in the first place. A geometry object whose class
    // is literally named "VRayDecal" is unambiguous.
    MSTR className;
    object->GetClassName(className);
    if (wcsstr(className.data(), L"VRayDecal") == nullptr) {
        return ContextSupport::Unsupported;
    }
    // Supported, not Fallback: MaxUsdMeshWriter also claims this object (a VRayDecal is
    // TriObject-convertible, which is how it came out as an 8-vertex gizmo box), and among
    // Fallback claimants MeshWriter wins.
    return ContextSupport::Supported;
}

MaxUsdVRayDecalWriter::MaxUsdVRayDecalWriter(const MaxUsdWriteJobContext& jobCtx, INode* node)
    : MaxUsdPrimWriter(jobCtx, node)
{
}

TfToken MaxUsdVRayDecalWriter::GetPrimType() { return pxr::MaxUsdPrimTypeTokens->Mesh; }

bool MaxUsdVRayDecalWriter::Write(
    UsdPrim&                  targetPrim,
    bool                      applyOffsetTransform,
    const MaxUsd::ExportTime& time)
{
    INode* sourceNode = GetNode();
    if (sourceNode == nullptr) {
        return false;
    }
    // A decal is a static projection: nothing to re-author per frame.
    if (!time.IsFirstFrame()) {
        return true;
    }

    const auto probe = _ProbeVRayDecal(sourceNode);
    if (!probe.error.empty() || probe.quad.size() != 4) {
        // Refuse rather than fall back to the gizmo box: a decal that silently exports as a solid
        // box wearing the artwork is harder to notice than one that is absent and logged.
        MaxUsd::Log::Warn(
            L"[VRAYDECAL] node={0} NOT exported — {1}",
            sourceNode->GetName(),
            MaxUsd::UsdStringToMaxString(
                probe.error.empty() ? std::string("probe returned no patch") : probe.error)
                .data());
        return false;
    }

    MaxUsd::Log::Warn(
        L"[VRAYDECAL] node={0} {1}x{2} depth={3} receiver=\"{4}\" at {5} ({6}/4 corners hit, {7} "
        L"candidates){8}",
        sourceNode->GetName(),
        probe.width,
        probe.length,
        probe.depth,
        MaxUsd::UsdStringToMaxString(probe.receiver).data(),
        probe.dist,
        probe.hits,
        probe.candidates,
        probe.fellBack ? L" [FALLBACK: left at the decal plane]" : L"");
    if (probe.bend != 0.f) {
        // Bend curves the projection around the receiver; a 4-corner patch cannot represent that.
        // Zero on every arena decal, so tessellation is deliberately not built yet — but say so
        // loudly if a scene ever uses it, instead of quietly exporting a flat approximation.
        MaxUsd::Log::Warn(
            L"[VRAYDECAL] node={0} has Bend={1}; exported patch is FLAT and will not follow the "
            L"curve",
            sourceNode->GetName(),
            probe.bend);
    }

    auto stage = targetPrim.GetStage();
    auto mesh = pxr::UsdGeomMesh::Define(stage, targetPrim.GetPath());
    if (!mesh) {
        return false;
    }
    const auto usdTimeCode = time.GetUsdTime();

    pxr::VtVec3fArray points(probe.quad.begin(), probe.quad.end());
    mesh.CreatePointsAttr().Set(points, usdTimeCode);
    pxr::VtIntArray faceVertexCounts;
    faceVertexCounts.push_back(4); // one quad
    pxr::VtIntArray faceVertexIndices;
    for (int i = 0; i < 4; ++i) {
        faceVertexIndices.push_back(i);
    }
    mesh.CreateFaceVertexCountsAttr().Set(faceVertexCounts, usdTimeCode);
    mesh.CreateFaceVertexIndicesAttr().Set(faceVertexIndices, usdTimeCode);

    pxr::GfVec3f bbMin = points[0];
    pxr::GfVec3f bbMax = points[0];
    for (const auto& p : points) {
        for (int c = 0; c < 3; ++c) {
            bbMin[c] = std::min(bbMin[c], p[c]);
            bbMax[c] = std::max(bbMax[c], p[c]);
        }
    }
    mesh.CreateExtentAttr().Set(pxr::VtVec3fArray { bbMin, bbMax }, usdTimeCode);

    mesh.CreateNormalsAttr().Set(pxr::VtVec3fArray(4, probe.normal), usdTimeCode);
    mesh.SetNormalsInterpolation(pxr::UsdGeomTokens->vertex);

    // A projected graphic is flat, never subdivided.
    mesh.CreateSubdivisionSchemeAttr().Set(pxr::UsdGeomTokens->none);
    // Double-sided deliberately. The patch is single-quad and its winding follows the receiving
    // surface's normal, which the raycast reports for the face it hit — if that normal disagrees
    // with the renderer's front-face convention the artwork would vanish entirely. An opaque-masked
    // decal looks identical from behind, so there is no cost to being safe here.
    mesh.CreateDoubleSidedAttr().Set(true);

    // UVs run 0..1 across the decal rectangle, in the same corner order the patch was built in.
    // That is the mapping the decal's own material already assumes, so the existing MaterialX
    // path — including reading the mask from the bitmap's alpha (MAX-MTLX-OPACITY-ALPHA-018),
    // which is what cuts the artwork down to its letters — needs no change.
    pxr::UsdGeomPrimvarsAPI primvarsApi(mesh.GetPrim());
    auto                    stPrimvar = primvarsApi.CreatePrimvar(
        pxr::TfToken("st"), pxr::SdfValueTypeNames->TexCoord2fArray, pxr::UsdGeomTokens->vertex);
    stPrimvar.Set(
        pxr::VtVec2fArray {
            { 0.f, 0.f },
            { 1.f, 0.f },
            { 1.f, 1.f },
            { 0.f, 1.f } },
        usdTimeCode);

    return true;
}

PXR_NAMESPACE_CLOSE_SCOPE
