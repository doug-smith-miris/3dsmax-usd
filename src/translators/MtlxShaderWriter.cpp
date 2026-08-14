//
// Copyright 2025 Autodesk
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
#include <MaxUsd/Utilities/MaxSupportUtils.h>
#ifdef IS_MAX2025_OR_GREATER
#include <MaxUsd/Translators/ShaderWriter.h>
#include <MaxUsd/Translators/ShaderWriterRegistry.h>
#include <MaxUsd/Utilities/ListenerUtils.h>

#include <usdufe/base/tokens.h>

#include <pxr/base/tf/token.h>
#include <pxr/pxr.h>
#include <pxr/usd/usdMtlx/utils.h>
#if PXR_VERSION > 2411
#include <pxr/usd/usdMtlx/tokens.h>
#endif
#include <pxr/usd/usdShade/material.h>
#include <pxr/usd/usdShade/nodeGraph.h>
#include <pxr/usd/usdUI/nodeGraphNodeAPI.h>

#include <maxscript/maxscript.h>
#include <maxscript/util/listener.h>

#include <MaterialXFormat/XmlIo.h>

PXR_NAMESPACE_OPEN_SCOPE

bool _IsWellFormedPath(const fs::path& p)
{
    try {
        // Attempt to create an absolute path to check its validity
        fs::path absPath = fs::absolute(p);
        return true;
    } catch (const fs::filesystem_error&) {
        return false;
    }
}

// Retrieves the standard library document for MaterialX.
MaterialX::ConstDocumentPtr _GetStandardLibraryDocument()
{
    static auto standardDoc = UsdMtlxGetDocument("");
    return standardDoc;
}

// Gets the node definition string for a given node.
// If the Node already has it's nodeDefString set, use that.
std::string _GetNodeDefString(const MaterialX::NodePtr& node)
{
    // For now the 3dsmax MaterialX component guarantees that the nodeDefString is set.
    // If we ever need to support nodes without nodeDefString, add a fallback here.
    auto nodeDefString = node->getNodeDefString();
    if (!nodeDefString.empty()) {
        return nodeDefString;
    }
    return std::string();
}

// Retrieves the node definition for a given node.
MaterialX::ConstNodeDefPtr _GetNodeDef(const MaterialX::NodePtr& node)
{
    auto nodeDefName = _GetNodeDefString(node);
    auto nodeDefPtr = node->getDocument()->getNodeDef(nodeDefName);
    if (!nodeDefPtr) {
        // Get the standard library document and check that.
        nodeDefPtr = _GetStandardLibraryDocument()->getNodeDef(nodeDefName);
    }
    if (!nodeDefPtr) {
        TF_WARN("Could not find nodeDef for node '%s'", node->getName().c_str());
    }
    return nodeDefPtr;
}

// Sets shader info:id attribute on a USD ShadeShader based on a MaterialX node.
void _SetShaderInfoAttributes(const MaterialX::NodePtr& node, UsdShadeShader& usdShader)
{
    auto nodeDefString = _GetNodeDefString(node);
    usdShader.CreateIdAttr(VtValue(TfToken(nodeDefString)));
}

// Checks if the input type supports color space.
bool _TypeSupportsColorSpace(const MaterialX::InputPtr& mxElem)
{
    // ColorSpaces are supported on
    //  - inputs of type color3 or color4
    //  - filename inputs on image nodes with color3 or color4 outputs
    const std::string& type = mxElem->getType();
    const bool         colorInput = type == "color3" || type == "color4";

    bool colorImageNode = false;
    if (type == "filename") {
        // verify the output is color3 or color4
        auto node = mxElem->getParent()->asA<MaterialX::Node>();
        if (auto parentNodeDef = _GetNodeDef(node)) {
            for (const MaterialX::OutputPtr& output : parentNodeDef->getOutputs()) {
                const std::string& type = output->getType();
                colorImageNode |= type == "color3" || type == "color4";
            }
        }
    }
    return colorInput || colorImageNode;
}

// Sets UI attributes for a USD input based on a MaterialX input.
void _SetInputUIAttributes(const MaterialX::InputPtr mtlxInput, UsdShadeInput& usdInput)
{
    auto attr = usdInput.GetAttr();
    for (const auto& key : UsdUfe::MetadataTokens->allTokens) {
        if (mtlxInput->hasAttribute(key.GetString())) {
            auto value = mtlxInput->getAttribute(key);
            if (key == UsdUfe::MetadataTokens->UIDoc) {
                attr.SetDocumentation(value);
            } else if (key == UsdUfe::MetadataTokens->UIEnumLabels) {
                const auto       enumStrings = UsdUfe::TfStringSplit(value, ",");
                VtArray<TfToken> allowedTokens;
                allowedTokens.reserve(enumStrings.size());
                for (const auto& tokenString : enumStrings) {
                    allowedTokens.push_back(TfToken(TfStringTrim(tokenString, " ")));
                }
                attr.SetMetadata(SdfFieldKeys->AllowedTokens, allowedTokens);
            } else if (key == UsdUfe::MetadataTokens->UIFolder) {
                std::replace(value.begin(), value.end(), '/', ':');
                attr.SetDisplayGroup(value);
            } else if (key == UsdUfe::MetadataTokens->UIName) {
                attr.SetDisplayName(value);
            } else if (SdfSchema::GetInstance().IsRegistered(key)) {
                attr.SetMetadata(key, VtValue(value));
            } else {
                attr.SetCustomDataByKey(key, VtValue(value));
            }
        }
    }
}

// Gets the output name of the mxNode connected to a portElement
// If the portElement has an outputString, use that.
// Otherwise, look for the output name in the NodeDef
// If that fails, use the default output name.
std::string
_GetOutputName(const MaterialX::PortElementPtr& portElement, const MaterialX::NodePtr& mxNode)
{
    std::string outputName;
    if (portElement->hasOutputString()) {
        outputName = portElement->getOutputString();
    } else if (auto nodeDef = _GetNodeDef(mxNode)) {
        auto outVec = nodeDef->getOutputs();
        if (!outVec.empty()) {
            outputName = outVec[0]->getName();
        }
    }
    if (outputName.empty()) {
        outputName = UsdMtlxTokens->DefaultOutputName.GetString();
    }
    return outputName;
}

// Connects a USD input to a node output based on a MaterialX input.
void _ConnectToNode(
    const MaterialX::InputPtr& input,
    UsdShadeInput&             usdInput,
    const SdfPath&             parentPath,
    const UsdStagePtr&         stage)
{
    auto connectedNode = input->getConnectedNode();

    std::string outputName = _GetOutputName(input, connectedNode);
    auto        nodeOutput = UsdShadeShader(stage->GetPrimAtPath(
                                         parentPath.AppendPath(SdfPath(connectedNode->getName()))))
                          .GetOutput(TfToken(outputName));
    if (nodeOutput.IsDefined()) {
        usdInput.ConnectToSource(nodeOutput);
    }
}

// Connect a USD input to a NodeGraph input based on a MaterialX input.
void _ConnectToInterfaceInput(
    const MaterialX::InputPtr& interfaceInput,
    UsdShadeInput&             usdInput,
    const SdfPath&             parentPath,
    const UsdStagePtr&         stage)
{
    auto interfaceInputNode = interfaceInput->getParent();
    auto interfaceInputNodePrim = stage->GetPrimAtPath(parentPath);
    if (interfaceInputNode->getName() == interfaceInputNodePrim.GetName()) {
        auto interfaceInputNodeGraph = UsdShadeNodeGraph(interfaceInputNodePrim);
        auto valType = UsdMtlxGetUsdType(interfaceInput->getType());
        auto interfaceInputNodeOutput = interfaceInputNodeGraph.CreateInput(
            TfToken(interfaceInput->getName()), valType.valueTypeName);
        if (interfaceInputNodeOutput.IsDefined()) {
            usdInput.ConnectToSource(interfaceInputNodeOutput);
        }
    }
}

// Connects a USD input to a node graph output based on a MaterialX input.
void _ConnectToNodeGraph(
    const MaterialX::InputPtr& input,
    UsdShadeInput&             usdInput,
    const SdfPath&             parentPath,
    const UsdStagePtr&         stage)
{
    auto output = input->getConnectedOutput();
    auto nodeGraphName = output->getParent()->getName();
    auto nodeGraphPrim = stage->GetPrimAtPath(parentPath.AppendPath(SdfPath(nodeGraphName)));
    if (nodeGraphPrim.IsDefined()) {
        auto nodeGraph = UsdShadeNodeGraph(nodeGraphPrim);
        auto usdOutput = nodeGraph.GetOutput(TfToken(output->getName()));
        if (usdOutput.IsDefined()) {
            usdInput.ConnectToSource(usdOutput);
        }
    }
}

// Sets the value of a USD input based on a MaterialX input.
void _SetInputValue(const MaterialX::InputPtr& input, UsdShadeInput& usdInput)
{
    usdInput.Set(UsdMtlxGetUsdValue(input));
    if (_TypeSupportsColorSpace(input)) {
        auto colorSpace = input->getActiveColorSpace();
        if (!colorSpace.empty()) {
            usdInput.GetAttr().SetColorSpace(TfToken(colorSpace));
        }
    }
}

// Adds a USD input based on a MaterialX input.
void _AddInput(
    const MaterialX::InputPtr& input,
    UsdShadeInput&             usdInput,
    const SdfPath&             parentPath,
    const UsdStagePtr&         stage)
{
    if (input->hasNodeGraphString()) {
        _ConnectToNodeGraph(input, usdInput, parentPath, stage);
    } else if (input->hasNodeName()) {
        _ConnectToNode(input, usdInput, parentPath, stage);
    } else if (input->hasInterfaceName()) {
        auto interfaceInput = input->getInterfaceInput();
        _ConnectToInterfaceInput(interfaceInput, usdInput, parentPath, stage);
    } else if (!input->hasOutputString()) {
        _SetInputValue(input, usdInput);
    }
}

// Adds a shader input to a USD shader based on a MaterialX input.
void _AddShaderInput(
    const MaterialX::InputPtr& input,
    UsdShadeShader&            usdShader,
    const SdfPath&             parentPath,
    const UsdStagePtr&         stage)
{
    auto typeStr = input->getType();
    auto valType = UsdMtlxGetUsdType(typeStr);
    auto usdInput = usdShader.CreateInput(TfToken(input->getName()), valType.valueTypeName);
    if (usdInput.IsDefined()) {
        _AddInput(input, usdInput, parentPath, stage);
    }
}

// MAX-MTLX-001: 3ds Max's `MtlxIOUtil.ExportMtlxString` (invoked via
// `exportMtlToMtlx` above) reliably emits the `ND_standard_surface` shader
// and the `NG_<name>` NodeGraph output declarations, but drops the interior
// `<tiledimage>` (image) nodes that carry the actual Bitmap/VRayBitmap file
// slots. `_AddDependentNodes` faithfully translates whatever the doc contains,
// so with an empty NG the exported USD ends up with dangling NodeGraph outputs
// and zero `ND_tiledimage_*` shader prims. This helper walks the Max material's
// map slots via MAXScript, discovers each Bitmap slot's file path + MaterialX
// input mapping, and injects the missing `<tiledimage>` nodes into the
// in-memory MaterialX document *before* the walker runs.
//
// Returned string is one entry per line: `mtlxInputName|mtlxType|filePath`.
// Types are the ND_standard_surface input types (color3, float, vector3).
// The `normal` input is special-cased: it flows through an `ND_normalmap_float`
// whose `in` input needs a `vector3` image; we emit both the tiledimage and,
// if missing, connect it through the existing normalmap node.
static const TSTR discoverMaxMtlxTexmapsFn = LR"(
    -- MAX-MTLX-006: recursively walk common wrapper texmap classes to reach
    -- the underlying Bitmap. Arch-viz scenes (Spectrum Center arena baseline
    -- was 137/179 flat) routinely wrap the diffuse map in a Color_Correction
    -- for tinting, in an OutputMap for gain/offset, in a UVW_Xform for
    -- planar-projection tweaks, or in a Composite for multi-layer blends.
    -- After V-Ray Scene Converter runs VRayMtl -> PhysicalMaterial the
    -- resulting `base_color_map` inherits whatever wrapper stack the source
    -- VRayMtl had, and MAX-MTLX-001's original leaf detector (Bitmaptexture
    -- / #filename / #bitmap.filename) walked past it silently. This helper
    -- recurses through the wrapper's known map-carrying properties (.map /
    -- .map1 / .mapList / .normal_map / .baseTex / .sourceA) until it finds
    -- a Bitmaptexture-like leaf, then returns its filename. Depth-capped at
    -- 8 so a malformed cyclic map graph can't spin forever.
    fn resolveMaxTexmapFilename tex depth = (
        if tex == undefined or depth > 8 then return undefined
        local cls = classOf tex
        -- Leaf detectors first (identical to the pre-fix behavior).
        if cls == Bitmaptexture then return tex.filename
        -- MAX-MTLX-VRAYHDRI-015: V-Ray arch-viz maps (VRayHDRI / VRayBitmap) store their file path in
        -- .HDRIMapName, NOT .filename/.bitmap. Without this leaf a texture-driven VRayMtl diffuse slot
        -- resolves to nothing -> the dangling NodeGraph output is pruned to the material's flat
        -- placeholder constant (the green SEAT_FAB_BASE / EAST_TILE_BLUE / WALL_PAINT_WHITE symptom, and
        -- the flat-white graphic screens). Probed before the generic #filename branch so the V-Ray
        -- canonical property wins over an empty/legacy #filename on the same node.
        if (isProperty tex #HDRIMapName) then (
            local hn = getProperty tex #HDRIMapName
            if hn != undefined and hn != "" then return hn
        )
        if (isProperty tex #filename) then (
            local fname = getProperty tex #filename
            if fname != undefined and fname != "" then return fname
        )
        if (isProperty tex #bitmap) then (
            local bmp = getProperty tex #bitmap
            if bmp != undefined and (isProperty bmp #filename) then (
                local fname = getProperty bmp #filename
                if fname != undefined and fname != "" then return fname
            )
        )
        -- Wrapper classes with a single nested map. Covers Color_Correction
        -- (.map), UVW_Xform (.map), Normal_Bump (.normal_map, with .bump_map
        -- fallback), OSL/procedural wrappers exposing .map1 (OutputMap,
        -- RGB_Multiply, RGB_Tint, Mix map). Order tries the most-common
        -- carrier first so a hit short-circuits the rest.
        local nested = undefined
        if (isProperty tex #map) then nested = getProperty tex #map
        if nested == undefined and (isProperty tex #map1) then (
            nested = getProperty tex #map1
        )
        if nested == undefined and (isProperty tex #normal_map) then (
            nested = getProperty tex #normal_map
        )
        if nested == undefined and (isProperty tex #bump_map) then (
            nested = getProperty tex #bump_map
        )
        -- V-Ray combiner classes wrap their source under different names:
        -- VRayColor2Bump uses `.baseTex`; VRayCompTex uses `.sourceA` (with
        -- `.sourceB` as the overlay layer).
        if nested == undefined and (isProperty tex #baseTex) then (
            nested = getProperty tex #baseTex
        )
        if nested == undefined and (isProperty tex #sourceA) then (
            nested = getProperty tex #sourceA
        )
        if nested != undefined then (
            local hit = resolveMaxTexmapFilename nested (depth + 1)
            if hit != undefined and hit != "" then return hit
        )
        -- Composite maps carry an array of layers in .mapList. Return the
        -- first layer whose recursion finds a bitmap. Layer 1 is the base
        -- layer in Composite Map's UI, which is the closest thing to a
        -- "diffuse texture" for tinted / stamped composites.
        if (isProperty tex #mapList) then (
            local ml = getProperty tex #mapList
            if ml != undefined then (
                for layer in ml do (
                    if layer != undefined then (
                        local hit = resolveMaxTexmapFilename layer (depth + 1)
                        if hit != undefined and hit != "" then return hit
                    )
                )
            )
        )
        return undefined
    )
)"
        LR"(    -- MAX-MTLX-OPACITY-ALPHA-018: walk the same wrapper chain as
    -- resolveMaxTexmapFilename and return the leaf Bitmaptexture's monoOutput
    -- (0 = RGB Intensity, 1 = Alpha). When an opacity slot's leaf bitmap uses
    -- its ALPHA channel for the mono value (the standard alpha-cutout decal
    -- setup: white-RGB logo/text bitmap whose shape lives only in alpha), a
    -- plain ND_tiledimage_float reads RGB luminance (white -> fully opaque),
    -- silently dropping the cutout (BUZZ/CITY marquee, DR_PEPPER, laser-cut
    -- logos exported as solid white cards). Detecting mono==1 lets the emit
    -- tag the type "float_a" so the C++ authoring reads the real alpha channel.
    fn resolveMaxTexmapMono tex depth = (
        if tex == undefined or depth > 8 then return 0
        if (classOf tex) == Bitmaptexture then (
            if (isProperty tex #monoOutput) then return (getProperty tex #monoOutput)
            return 0
        )
        local nested = undefined
        if (isProperty tex #map) then nested = getProperty tex #map
        if nested == undefined and (isProperty tex #map1) then nested = getProperty tex #map1
        if nested == undefined and (isProperty tex #normal_map) then nested = getProperty tex #normal_map
        if nested == undefined and (isProperty tex #bump_map) then nested = getProperty tex #bump_map
        if nested == undefined and (isProperty tex #baseTex) then nested = getProperty tex #baseTex
        if nested == undefined and (isProperty tex #sourceA) then nested = getProperty tex #sourceA
        if nested != undefined then return (resolveMaxTexmapMono nested (depth + 1))
        return 0
    )
)"
        LR"(    -- MAX-MTLX-007: expand a possibly-wrapped material into the ordered list
    -- of concrete sub-materials whose PhysicalMaterial / VRayMtl slot map
    -- carries the actual texture maps. Non-wrapper materials return a
    -- 1-element list (`#(m)`) so PhysicalMaterial / OpenPBR / VRayMtl /
    -- StdMaterial behavior is preserved verbatim. The wrapper classes we
    -- descend into are:
    --
    --   VRayBlendMtl        — layered paint / weathered surfaces. Holds the
    --                         primary layer under `.baseMtl` and up to 9
    --                         `.coatMtl_1..coatMtl_9` overlays. Neither the
    --                         wrapper itself NOR the coat/base holder carry
    --                         `base_color_map` — every map lives on one of
    --                         the sub-materials. Pre-007, this wrapper's
    --                         entire texture graph was silently dropped from
    --                         the exported MaterialX network.
    --   VRayOverrideMtl     — V-Ray's per-ray-type override trick. `.baseMtl`
    --                         is the primary surface; `.giMtl` / `.reflectMtl`
    --                         / `.refractMtl` / `.shadowMtl` are the per-ray
    --                         overrides. baseMtl is the correct source for
    --                         the exported UsdPreviewSurface / MaterialX
    --                         surface (Karma / Hydra don't honor V-Ray's
    --                         per-ray override contract). MAX-MTLX-005
    --                         already unwraps this same wrapper for emission
    --                         color in `LastResortMtlxShaderWriter`; this
    --                         extends the same unwrap to the standard
    --                         texture-map discovery path.
    --   Composite (stock)   — MAX-MTLX-COMPOSITE-DECAL-014. Stock 3ds Max
    --                         Composite Mtl. Holds up to 10 sub-materials in
    --                         `.materialList`; index 1 is the base surface,
    --                         2..N are overlays stacked upward. Per-layer
    --                         `.mapEnabled[i]` boolean + `.opacity[i]` scalar
    --                         gates whether the layer contributes any pixels.
    --                         Court-line decals (DECAL_/LOGO_ layers on a
    --                         concrete court PhysicalMaterial base) are the
    --                         motivating arch-viz use case — pre-014 the
)"
        LR"(    --                         entire layer stack was dropped and the
    --                         Composite fell through to a flat last-resort
    --                         surface with no textures. Walked base-first so
    --                         base surface's textures win the first-hit-wins
    --                         slotMap dedupe; disabled and opacity-0 layers
    --                         (except the base itself) are skipped.
    --   Blend (stock)       — MAX-MTLX-COMPOSITE-DECAL-014. Stock 3ds Max
    --                         Blend material. Two sub-materials (`.map1` is
    --                         the base, `.map2` is the overlay) mixed by
    --                         `.mask` texmap + `.mixAmount` scalar. Bicolor
    --                         floor tiles + two-tone plastics are the
    --                         motivating arch-viz use case. Walked map1-first
    --                         so base's textures win first-hit-wins; per-side
    --                         `.mapNEnabled` flags are respected.
    --
    -- Recursion order is (self, baseMtl-tree, coat_1-tree, ..., coat_9-tree)
    -- so that when the outer loop first-hit-dedupes on `seenInputs`, the
    -- BASE material's textures win over any coat's — matching the layered-
    -- material authoring convention (base = underlying surface, coats =
    -- weathering / dirt / decals). Coats fill gaps when the base has no map
    -- for a given slot. Depth-capped at 6 (max plausible arch-viz nesting)
    -- and cycle-guarded via a shared `visited` list.
    fn unwrapBlendMaterialSubMtls m visited depth = (
        local out = #()
        if m == undefined or depth > 6 then return out
        -- Cycle guard. VRayBlendMtl allows an artist to (accidentally)
        -- point .baseMtl back at the wrapper; guard so recursion terminates.
        for v in visited do (
            if v == m then return out
        )
        append visited m
        append out m
        local cls = (classOf m) as string
        if cls == "VRayBlendMtl" then (
            if (isProperty m #baseMtl) then (
                local base = getProperty m #baseMtl
                if base != undefined then (
                    for sub in (unwrapBlendMaterialSubMtls base visited (depth + 1)) do (
                        append out sub
                    )
                )
            )
            for i = 1 to 9 do (
                local pn = ("coatMtl_" + (i as string))
                if (isProperty m pn) then (
                    local coat = getProperty m pn
                    if coat != undefined then (
                        for sub in (unwrapBlendMaterialSubMtls coat visited (depth + 1)) do (
                            append out sub
                        )
                    )
)"
        LR"(                )
            )
        )
        if cls == "VRayOverrideMtl" then (
            -- baseMtl first so its texture graph wins the first-hit dedupe
            -- over the per-ray overrides.
            for sn in #(#baseMtl, #giMtl, #reflectMtl, #refractMtl, #shadowMtl) do (
                if (isProperty m sn) then (
                    local sub = getProperty m sn
                    if sub != undefined then (
                        for r in (unwrapBlendMaterialSubMtls sub visited (depth + 1)) do (
                            append out r
                        )
                    )
                )
            )
        )
)"
        LR"(
        -- MAX-MTLX-COMPOSITE-DECAL-014: stock 3ds Max Composite Mtl. Holds
        -- an ordered stack of up to N sub-materials in `.materialList`;
        -- index 1 is the base surface, indices 2..N are overlays stacked
        -- upward. Per-layer `.mapEnabled[i]` boolean + `.opacity[i]` scalar
        -- gate whether the layer contributes to the composited surface.
        -- Walk base first so its texture graph wins the first-hit-wins
        -- slotMap dedupe; then walk 2..N in ascending index order. Skip
        -- layers whose `.mapEnabled[i]` is false OR (for i > 1) whose
        -- `.opacity[i]` is exactly 0 — those layers cannot contribute
        -- pixels to the composited surface ("per-layer opacity respect").
        -- Base (i == 1) is always walked regardless of its opacity value
        -- since the base IS the surface an unmasked pixel resolves to.
        if cls == "Composite" then (
            if (isProperty m #materialList) then (
                local ml = getProperty m #materialList
                if ml != undefined then (
                    local mEnabled = undefined
                    local mOpacity = undefined
                    if (isProperty m #mapEnabled) then (
                        mEnabled = getProperty m #mapEnabled
                    )
                    if (isProperty m #opacity) then (
                        mOpacity = getProperty m #opacity
                    )
                    local n = ml.count
                    for i = 1 to n do (
                        local layer = ml[i]
                        if layer != undefined then (
                            local enabled = true
                            if mEnabled != undefined and i <= mEnabled.count then (
                                if mEnabled[i] == false then enabled = false
                            )
                            local op = 100.0
                            if mOpacity != undefined and i <= mOpacity.count then (
                                op = mOpacity[i] as float
                            )
                            local skip = false
                            if not enabled then skip = true
                            if i > 1 and op == 0.0 then skip = true
                            if not skip then (
                                for sub in (unwrapBlendMaterialSubMtls layer visited (depth + 1)) do (
                                    append out sub
                                )
                            )
                        )
                    )
                )
            )
        )
        -- MAX-MTLX-COMPOSITE-DECAL-014: stock 3ds Max Blend material. Two
        -- sub-materials (`.map1` = base, `.map2` = overlay) mixed by
        -- `.mask` texmap + `.mixAmount` scalar. Walk `.map1` first so its
        -- texture graph wins the first-hit-wins slotMap dedupe over
        -- `.map2`'s. Per-side `.mapNEnabled` flags are respected — a
        -- disabled sub-material cannot contribute pixels and is skipped.
        -- The `.mask` / `.mixAmount` values themselves are NOT gates on
        -- traversal (they control the mix ratio at render time, not
        -- whether a sub-material's texture data participates in
        -- discovery).
        if cls == "Blend" then (
            if (isProperty m #map1) then (
                local m1 = getProperty m #map1
                local m1Enabled = true
                if (isProperty m #map1Enabled) then (
                    if (getProperty m #map1Enabled) == false then (
                        m1Enabled = false
                    )
                )
                if m1 != undefined and m1Enabled then (
                    for sub in (unwrapBlendMaterialSubMtls m1 visited (depth + 1)) do (
                        append out sub
                    )
                )
            )
            if (isProperty m #map2) then (
                local m2 = getProperty m #map2
                local m2Enabled = true
                if (isProperty m #map2Enabled) then (
                    if (getProperty m #map2Enabled) == false then (
                        m2Enabled = false
                    )
                )
                if m2 != undefined and m2Enabled then (
                    for sub in (unwrapBlendMaterialSubMtls m2 visited (depth + 1)) do (
                        append out sub
                    )
                )
            )
        )
        return out
    )
    fn discoverMaxMtlxTexmaps materialAnimHandle = (
        local m = getAnimByHandle materialAnimHandle
        local result = ""
        if m == undefined then return result
        -- MAX-MTLX-007: expand blend / override wrappers to the list of
        -- concrete sub-materials that actually carry the slot-map
        -- properties. For a plain PhysicalMaterial / OpenPBR / VRayMtl
        -- this returns `#(m)` — behavior is identical to pre-007. For a
        -- VRayBlendMtl / VRayOverrideMtl the list is (self, baseMtl-tree,
        -- coats-tree...) and the first-hit-wins dedupe on `seenInputs`
        -- keeps baseMtl's textures winning over coats'.
        --
        -- MAX-MTLX-008 (scope/coverage audit — WHERE THE WRAPPER WALK
        -- STOPS). The class-name gate above (`cls == "VRayBlendMtl"` /
        -- `cls == "VRayOverrideMtl"`) is an EXACT equality match — case-
        -- insensitive per MAXScript `==` string semantics but otherwise
        -- literal. Any other wrapper-shaped material class the plugin
        -- encounters is INTENTIONALLY out of scope; do not extend the
        -- gate to a prefix/substring/suffix heuristic. The audit's
        -- 48-case Python mirror at
        -- `src/Tests/Integration/test_miris_max_mtlx_008.py` catches
        -- superstring / substring / suffix drift, adjacent-wrapper
        -- descent, novel-slot descent, and slot-order flips.
        --
        -- COLLISION SHAPE — `.baseMtl` exists on VRayOverrideMtl (walked)
        -- AND on VRayMtlWrapper / VRayBumpMtl (NOT walked). The class-
        -- name gate is the ONLY guard preventing a silent descent into
        -- the wrapper-material family. A refactor to "just check for
        -- `.baseMtl`" would silently widen scope to every V-Ray matte/
        -- bump wrapper in an arch-viz scene. See MAX-MTLX-008 in
        -- `doc/translation-mapping.md` for the full bounds.
        --
        -- Adjacent V-Ray classes deliberately excluded:
)"
        LR"(        --   VRay2SidedMtl   — front/back are visually distinct; not a
        --                     first-hit-wins texture merge.
        --   VRayMtlWrapper  — matte/render-pass wrapper (has .baseMtl!);
        --                     the wrapper itself IS the surface artists
        --                     see.
        --   VRayBumpMtl     — bump-only wrapper (has .baseMtl!);
        --                     descending would double-count the
        --                     substrate against its own .bump_map.
        --   VRayFastSSS2, VRayLightMtl, VRayHairMtl — no wrapper role
        --                     for the standard texmap-discovery path
        --                     (VRayLightMtl is separately handled by
        --                     MAX-MTLX-005 in LastResortMtlxShaderWriter).
        --
        -- Stock 3ds Max classes descended into (MAX-MTLX-COMPOSITE-DECAL-014):
        --   Composite      — layered stack (base + up to 9 overlays).
        --                    First-hit-wins so base beats overlays;
        --                    disabled + opacity-0 layers skipped.
        --   Blend          — two-sub-mtl mix (map1 base + map2 overlay).
        --                    map1-first so base beats overlay; disabled
        --                    sides skipped.
        --
        -- Stock 3ds Max classes deliberately EXCLUDED (require different
        -- combining semantics than the first-hit-wins slotMap dedupe):
        --   Multi/Sub-Object — per-face-ID; MAX-GEO-002/006 owns the
        --                     GeomSubset partition. Descending here would
        --                     merge face-partitioned materials into a
        --                     single MaterialX network.
        --   DoubleSided      — front / back are visually distinct; a
        --                     first-hit-wins texture merge would smear
        --                     one face's map onto the other.
        --   Shell Material   — original / bake pair; the bake side is a
        --                     rendered output, not a substrate texture.
        --   Top/Bottom       — separate top / bottom by world-Z; texture
        --                     precedence depends on face orientation, not
        --                     wrapping order. A follow-on bite could
        --                     author a MaterialX `<mix>` split by
        --                     surface normal.
        --   Matte/Shadow     — invisible-except-shadows; carries no
        --                     surface textures the discovery loop can
        --                     use.
        --
        -- Extending MAX-MTLX-007 to a NEW wrapper class is a SEPARATE
        -- future bite with its own captured corpus of leak values, its
        -- own tests, and its own doc entry.
        local subMtls = unwrapBlendMaterialSubMtls m #() 0
        -- Slot map: (Max PhysicalMaterial/OpenPBR property name,
        --           ND_standard_surface input name,
        --           MaterialX type token used in the NodeGraph)
        --
        -- MAX-MTLX-009: specular_IOR maps (glass / water / coated
        -- dielectrics) were silently dropped by every prior discovery
        -- pass — the property spellings below are the canonical
        -- IOR-map slot names for the three PBR material families the
        -- arch-viz pipeline exercises (PhysicalMaterial, OpenPBR, VRayMtl).
        -- All entries map to the SAME ND_standard_surface `specular_IOR`
        -- input (float, port default 1.5), so the outer `seenInputs`
        -- first-hit-wins dedupe picks whichever spelling the source
        -- material used. VRayMtl exposes TWO IOR maps (reflectionIOR +
        -- refractionIOR); we honor the base assumption that a
        -- physically-plausible dielectric shares one IOR across both
        -- reflection and refraction and route both to the SAME input —
        -- first-hit-wins gives reflection priority since that is the
        -- one the ND_standard_surface `specular_IOR` input controls
        -- directly. Property-spelling audit:
        --   PhysicalMaterial:  trans_ior_map (see
        --     `src/Tests/Integration/export_material_test.ms:544` and
        --     the plugin's own `3dsmax_materials.mat_def` line 41).
        --   OpenPBR:           specular_ior_map (see
        --     `3dsmax_materials.mat_def` line 192).
        --   VRayMtl:           texmap_reflectionIOR / texmap_refractionIOR
        --     (V-Ray SDK canonical names — surfaced at the MAXScript
        --     layer as `.texmap_reflectionIOR` / `.texmap_refractionIOR`).
        -- MAX-MTLX-010: anisotropy + anisotropy-rotation maps
        -- (`specular_anisotropy` / `specular_rotation` on
        -- ND_standard_surface — both float, port defaults 0.0). Prior
        -- to this fix the slotMap covered NO anisotropy slots at all,
        -- so every VRayMtl brushed-metal / anisotropic-fabric / carbon-
        -- fiber material silently exported with anisotropy locked at
        -- its port default 0.0 (i.e. isotropic) regardless of what the
        -- source scene said. Prior-run evidence:
        --   evidence-slotmap-and-wrappers.md:31-32 lists
        --   `texmap_anisotropy` / `anisotropy_map` and
        --   `texmap_anisotropyRotation` / `anisotropy_rotation_map` as
        --   silent-drop classes distinct from MTLX-006's wrapper walk
        --   and MTLX-007's blend/override unwrap.
        --
        -- NOTE: PhysicalMaterial (Autodesk stock) does NOT expose
        -- anisotropy in its parameter surface (see
        -- `3dsmax_materials.mat_def:10-43` — no anisotropy inputs),
        -- so no PhysicalMaterial-only spellings are added. VRayMtl
        -- exposes `texmap_anisotropy` / `texmap_anisotropyRotation`
        -- directly (V-Ray SDK canonical). The generic
        -- `anisotropy_map` / `anisotropy_rotation_map` spellings
        -- (snake_case + camelCase) cover MAXScript-authored
        -- workflows and third-party PBR materials that route through
        -- the same discovery loop.
        --
        -- Displacement maps are ALSO silently dropped
        -- (`evidence-slotmap-and-wrappers.md:30`), but ND_standard_surface
        -- has no displacement input (verified via
)"
        LR"(        -- `mx.getNodeDef("ND_standard_surface_surfaceshader")` — displacement
        -- is on the MaterialX <material>'s `outputs:displacement` via
        -- a separate `ND_displacement_float` / `ND_displacement_vector3`
        -- node, not a shader input). A slotMap-only fix like this
        -- one cannot wire displacement — that requires a separate
        -- authoring path (new node type, new material output arc),
        -- scoped OUT of MAX-MTLX-010 to a follow-on bite.
        --
        -- MAX-MTLX-011: transmission (refraction) roughness + sheen
        -- roughness maps. Prior to this fix the slotMap covered no
        -- transmission-roughness or sheen-roughness slots at all, so
        -- every glass / glazing / frosted-dielectric material with a
        -- refraction-roughness map, and every velvet / satin / brushed-
        -- fabric material with a sheen-roughness map, silently exported
        -- with those inputs locked at their ND_standard_surface port
        -- defaults (transmission_extra_roughness = 0.0, sheen_roughness
        -- = 0.3) regardless of what the source scene said. Prior-run
        -- evidence:
        --   evidence-slotmap-and-wrappers.md:33-35 lists
        --   `texmap_refractionGlossiness` / `trans_roughness_map` /
        --   `transmissionRoughnessMap` and `sheen_roughness_map` as
        --   silent-drop classes distinct from MTLX-006's wrapper walk
        --   and MTLX-007's blend/override unwrap.
        --
        -- MaterialX stdlib target inputs (verified via `hython` +
        -- `mx.getNodeDef("ND_standard_surface_surfaceshader")`):
        --   transmission_extra_roughness  (float, default 0.0) — ADDED
        --     on top of specular_roughness for transmission ray paths.
        --     There is NO plain `transmission_roughness` input on
        --     ND_standard_surface; `transmission_extra_roughness` is
        --     the canonical target and is what MaterialX 1.38+ uses.
        --   sheen_roughness               (float, default 0.3) —
        --     directly drives the sheen BRDF's roughness.
        --
        -- Property spellings:
        --   PhysicalMaterial-style (custom / third-party):
        --     trans_roughness_map (snake) / transRoughnessMap (camel)
        --   OpenPBR-style long form (custom / third-party):
        --     transmission_roughness_map / transmissionRoughnessMap
        --   Generic sheen:
        --     sheen_roughness_map (snake) / sheenRoughnessMap (camel)
        --
        -- NOTE: Neither stock PhysicalMaterial nor stock OpenPBR
        -- exposes a transmission-roughness map slot in the shipping
        -- `3dsmax_materials.mat_def` (stock PhysicalMaterial has no
        -- roughness family for transmission at all; stock OpenPBR
        -- shares `specular_roughness_map` between reflection and
        -- transmission per the OpenPBR spec). These slotMap entries
        -- cover MAXScript-authored / third-party PhysicalMaterial-
        -- derived materials that DO expose a distinct transmission
        -- roughness map. Stock OpenPBR scenes route to
        -- ND_open_pbr_surface_surfaceshader (which uses
        -- `fuzz_roughness`, not `sheen_roughness`) and are handled by
        -- their own writer path — MAX-MTLX-011 targets the
        -- ND_standard_surface path (PhysicalMaterial + VRayMtl
        -- converted to Physical, plus any custom material whose
        -- slotMap exposes these names).
        --
        -- VRayMtl's `texmap_refractionGlossiness` is EXCLUDED from
        -- this bite: V-Ray exposes glossiness (0 = rough, 1 = smooth)
        -- while ND_standard_surface uses roughness (0 = smooth,
        -- 1 = rough). Wiring glossiness directly to
        -- transmission_extra_roughness would produce a semantically
        -- inverted map — polished glass would render as frost. Adding
        -- V-Ray's glossiness map requires an `ND_invert_float` node
        -- inserted between the tiledimage and the shader input, which
        -- is an authoring-path change beyond a slotMap extension. That
        -- is a follow-on bite (candidateMission
        -- `max-mtlx-013-vray-glossiness-to-roughness-invert`). The
        -- dominant arch-viz path — VRayMtl scenes run through the
        -- V-Ray Scene Converter to become PhysicalMaterial — reaches
        -- transmission roughness via the PhysicalMaterial spellings
        -- authored above.
        --
)"
        LR"(        -- MAX-MTLX-COAT-015: coat weight/color/roughness/IOR/normal
        -- maps. Prior to this fix the slotMap covered NO coat slots
        -- at all, so every glossy floor sealer, car paint, gymnasium
        -- lacquer, and metallic-finish car body/table silently
        -- exported with the ND_standard_surface coat family locked
        -- at port defaults (coat=0 (weight), coat_color=(1,1,1),
        -- coat_roughness=0.1, coat_IOR=1.5, coat_normal=unset) —
        -- i.e. the clear-coat overlay was invisible in the exported
        -- MaterialX network regardless of what the source material
        -- said. Prior-run evidence:
        --   evidence/gaps-audit.md:139-156 (S1) enumerates the
        --   silent-drop property spellings for PhysicalMaterial
        --   (`coat_map`, `coat_rough_map`, `clearcoat_map`,
        --   `clearcoatRoughness_map`), OpenPBR (`coat_weight_map`,
        --   `coat_color_map`, `coat_roughness_map`, `coat_ior_map`,
        --   `coat_normal_map`), and VRayMtl (`coat_amount_texmap`).
        --
        -- MaterialX stdlib target inputs on
        -- ND_standard_surface_surfaceshader (verified via `hython` +
        -- `mx.getNodeDef(...)`):
        --   coat            (float,   default 0.0) — coat weight.
        --   coat_color      (color3,  default (1,1,1)) — coat tint.
        --   coat_roughness  (float,   default 0.1) — coat BRDF
        --                                            roughness.
        --   coat_IOR        (float,   default 1.5) — coat Fresnel IOR
        --                                            (capital IOR to
        --                                            match specular_IOR
        --                                            spelling).
        --   coat_normal     (vector3, default unset) — coat-space
        --                                              normal map input.
        --
        -- Property spellings routed to `coat` (weight):
        --   PhysicalMaterial: `coat_map` / `coatMap` (mat_def:35).
        --   PhysicalMaterial alt: `clearcoat_map` / `clearcoatMap`
        --     (mat_def:72 — some 3ds Max builds spell it clearcoat).
        --   OpenPBR: `coat_weight_map` / `coatWeightMap` (mat_def:218).
        --   VRayMtl: `coat_amount_texmap` / `coatAmountTexmap`
        --     (V-Ray SDK canonical for the coat weight scalar).
        -- Property spellings routed to `coat_roughness`:
        --   PhysicalMaterial: `coat_rough_map` / `coatRoughMap`
        --     (mat_def:38, short spelling used by the stock PhysMat UI).
        --   PhysicalMaterial alt: `clearcoatRoughness_map` /
        --     `clearcoatRoughnessMap` (mat_def:74).
        --   OpenPBR: `coat_roughness_map` / `coatRoughnessMap`
        --     (mat_def:222, long spelling).
        -- Property spellings routed to `coat_color`:
        --   OpenPBR: `coat_color_map` / `coatColorMap` (mat_def:220).
        --   NOTE: PhysicalMaterial doesn't expose a coat_color map
        --   in stock mat_def; the coat tint is fixed to (1,1,1) in
        --   stock PhysMat and lives only on OpenPBR / VRayMtl.
        -- Property spellings routed to `coat_IOR`:
        --   OpenPBR: `coat_ior_map` / `coatIorMap` (mat_def:226).
        --   NOTE: no PhysicalMaterial coat_ior slot — stock PhysMat
        --   uses a scalar-only coat IOR.
        -- Property spellings routed to `coat_normal`:
        --   OpenPBR: `coat_normal_map` / `coatNormalMap` (mat_def:250).
        --   NOTE: this wires a vector3 tiledimage directly into the
        --   ND_standard_surface `coat_normal` input. The main-normal
        --   input flows through an ND_normalmap_float node scaffolded
        --   by `_WireDanglingNormalmapInputs` (MAX-MTLX-003) so it
        --   correctly remaps 0..1 -> -1..1; scaffolding an equivalent
        --   node for the COAT-normal path is a candidate follow-on
        --   bite (`max-mtlx-016-coat-normalmap-remap`). Without it,
        --   a coat_normal map that was authored as tangent-space
        --   0..1 encoded will read half-strength in Karma. A raw
        --   pass-through is nonetheless preferable to silently
        --   dropping the map, which is what pre-fix code did.
        --
)"
        LR"(        -- All entries follow the pre-existing conventions of the
        -- slotMap: snake_case declared before camelCase within each
        -- spelling family so first-hit-wins order matches every other
        -- family; PhysicalMaterial spellings declared before OpenPBR /
        -- VRayMtl spellings so the base PhysicalMaterial texture wins
        -- when the same map is (unusually) authored on multiple slot
        -- names in the same material. The wrapper walk (MAX-MTLX-007
        -- `unwrapBlendMaterialSubMtls`, extended for Composite / Blend
        -- by MAX-MTLX-COMPOSITE-DECAL-014) applies because slotMap
        -- iteration lives inside `for currentMat in subMtls do` —
        -- VRayBlendMtl / VRayOverrideMtl / Composite Mtl / stock Blend
        -- unwrap identically for the new entries, and baseMtl's coat
        -- textures win over any coat's / per-ray override's.
        local slotMap = #(
            #("base_color_map",         "base_color",         "color3"),
            #("baseColorMap",           "base_color",         "color3"),
            #("roughness_map",          "specular_roughness", "float"),
            #("roughnessMap",           "specular_roughness", "float"),
            #("metalness_map",          "metalness",          "float"),
            #("metalnessMap",           "metalness",          "float"),
            #("bump_map",               "normal",             "vector3"),
            #("bumpMap",                "normal",             "vector3"),
            #("norm_map",               "normal",             "vector3"),
            #("normalMap",              "normal",             "vector3"),
            #("emit_color_map",         "emission_color",     "color3"),
            #("emissionColorMap",       "emission_color",     "color3"),
            -- MAX-MTLX-SELFILLUM-NONVRAY-019 additions: scalar
            -- emission-weight maps on non-VRayLightMtl emissive
            -- materials. Complements MAX-MTLX-EMISSIVE-UNITS-013
            -- (which fixes VRayLightMtl weight-into-color baking on
            -- the LastResortMtlxShaderWriter path — a DIFFERENT
            -- .cpp file). 019 fixes the non-VRay PhysicalMaterial-
            -- based / OpenPBR-based emissive path routed through
            -- THIS writer: the pre-existing `emit_color_map` /
            -- `emissionColorMap` entries above only cover the
            -- COLOR emission slot (routed to
            -- `ND_standard_surface.emission_color`, color3),
            -- leaving the SCALAR emission-weight map slot
            -- silently dropped. All entries route to
            -- `ND_standard_surface.emission` (scalar weight, float,
            -- port default 0.0) — the port that actually turns the
            -- emission BRDF on. Symptom pre-fix: LED text signage,
            -- warm-tungsten practicals, and glowing-edge materials
            -- authored as PhysicalMaterial / OpenPBR (rather than
            -- VRayLightMtl) fell through with `emission=0` locked
            -- at the ND_standard_surface port default, so Karma
            -- rendered them as unlit surfaces regardless of the
            -- source scene's self_illum / emission_weight map. See
            -- `agent/pipeline-runs/40dca678-.../evidence/gaps-audit.md`
            -- S5. Slot precedence: PhysicalMaterial spellings first
            -- (`self_illum_map` / `emit_intensity_map`) then OpenPBR
            -- (`emission_weight_map`, mat_def:236), matching every
            -- other family in the slotMap. Snake_case before
            -- camelCase within each spelling family. No colorspace
            -- attribute is authored (`float` scalar, not `color3` —
            -- the existing type gate in `_EnrichMtlxDocFromMaxMaterial`
            -- does the right thing; an sRGB decode of an emission-
            -- weight scalar would silently dim the emitter by the
            -- gamma curve). Ordering: placed immediately after the
            -- emission_color entries so all emission-family slotMap
            -- entries live in one contiguous block — matches the
            -- ordering conventions established by MTLX-009 (IOR
            -- family) and MTLX-011 (transmission-roughness family).
            -- The wrapper walk (MAX-MTLX-007 `unwrapBlendMaterialSubMtls`,
            -- extended for Composite / Blend by MTLX-014) applies
            -- because slotMap iteration lives inside
            -- `for currentMat in subMtls do` — VRayBlendMtl /
            -- VRayOverrideMtl / CompositeMtl / stock Blend unwrap
            -- identically for the new entries, and baseMtl's
            -- emission weight wins over any coat's / per-ray
            -- override's under first-hit-wins.
            #("self_illum_map",         "emission",           "float"),
            #("selfIllumMap",           "emission",           "float"),
            #("emit_intensity_map",     "emission",           "float"),
            #("emitIntensityMap",       "emission",           "float"),
            #("emission_weight_map",    "emission",           "float"),
            #("emissionWeightMap",      "emission",           "float"),
            #("refl_color_map",         "specular_color",     "color3"),
            #("specularColorMap",       "specular_color",     "color3"),
            -- MAX-MTLX-VRAYMTL-REFLECTION-TINT-017 addition: VRayMtl
            -- reflection-tint texmap. `texmap_reflection` is the V-Ray
            -- SDK canonical name for the reflection-color map on
            -- VRayMtl (see the V-Ray for 3ds Max SDK's VRayMtl param
            -- surface — the pre-existing MTLX-009 `texmap_reflectionIOR`
            -- / `texmap_refractionIOR` entries follow the same
            -- `texmap_reflection*` naming family). Sits as a sibling to
            -- the PhysicalMaterial `refl_color_map` / OpenPBR
            -- `specularColorMap` entries above so all three families
            -- route to the SAME ND_standard_surface `specular_color`
            -- input (color3, port default (1,1,1)) and the outer
            -- `seenInputs` first-hit-wins dedupe selects whichever
            -- spelling the source material used. Symptom pre-fix:
            -- every metal / tinted-chrome / anodized-aluminum V-Ray
            -- material (Spectrum Center's anodized-aluminum handrails,
            -- chrome-tinted glass surrounds, tinted-brass fixtures)
            -- rendered with neutral-white specular in Karma instead of
)"
        LR"(            -- the artist-tinted color the source scene authored — see
            -- `agent/pipeline-runs/40dca678-.../evidence/gaps-audit.md`
            -- S3 for the 179-material arena census delta (0 -> ~14
            -- tinted-reflection materials post-fix). No camelCase alt
            -- because V-Ray's SDK ships only the snake_case spelling
            -- (matching MTLX-010's `texmap_anisotropy` /
            -- MTLX-011's `texmap_opacity` single-spelling pattern for
            -- V-Ray-only slots). The color3 colorspace gate in
            -- `_EnrichMtlxDocFromMaxMaterial` does the right thing by
            -- construction — `texmap_reflection` is a color tint, so
            -- the injected ND_tiledimage_color3 carries the standard
            -- `colorspace="srgb_texture"` attribute, matching the
            -- pre-existing `refl_color_map` / `specularColorMap`
            -- treatment.
            #("texmap_reflection",      "specular_color",     "color3"),
            #("trans_color_map",        "transmission_color", "color3"),
            #("transmissionColorMap",   "transmission_color", "color3"),
            #("cutout_map",             "opacity",            "float"),
            #("cutoutMap",              "opacity",            "float"),
            -- MAX-MTLX-009 additions: specular_IOR maps.
            #("trans_ior_map",          "specular_IOR",       "float"),
            #("transIorMap",            "specular_IOR",       "float"),
            #("specular_ior_map",       "specular_IOR",       "float"),
            #("specularIorMap",         "specular_IOR",       "float"),
            #("texmap_reflectionIOR",   "specular_IOR",       "float"),
            #("texmap_refractionIOR",   "specular_IOR",       "float"),
            -- MAX-MTLX-010 additions: specular_anisotropy +
            -- specular_rotation maps. All entries route to a
            -- float ND_tiledimage; no colorspace attribute is
            -- authored (float scalar, not color3 — see the type
            -- gate in `_EnrichMtlxDocFromMaxMaterial`).
            #("anisotropy_map",             "specular_anisotropy", "float"),
            #("anisotropyMap",              "specular_anisotropy", "float"),
            #("texmap_anisotropy",          "specular_anisotropy", "float"),
            #("anisotropy_rotation_map",    "specular_rotation",   "float"),
            #("anisotropyRotationMap",      "specular_rotation",   "float"),
            #("texmap_anisotropyRotation",  "specular_rotation",   "float"),
            -- MAX-MTLX-011 additions: transmission (refraction)
            -- roughness + sheen roughness maps. All route to float
            -- ND_tiledimage; the color3 colorspace gate does the
            -- right thing (no sRGB decode of scalar roughness).
            #("trans_roughness_map",         "transmission_extra_roughness", "float"),
            #("transRoughnessMap",           "transmission_extra_roughness", "float"),
)"
        LR"(            #("transmission_roughness_map",  "transmission_extra_roughness", "float"),
            #("transmissionRoughnessMap",    "transmission_extra_roughness", "float"),
            #("sheen_roughness_map",         "sheen_roughness",              "float"),
            #("sheenRoughnessMap",           "sheen_roughness",              "float"),
            -- MAX-MTLX-COAT-015 additions: coat weight/color/roughness/
            -- IOR/normal maps routed to ND_standard_surface coat family.
            -- PhysicalMaterial spellings (mat_def:35/38/72/74):
            #("coat_map",                    "coat",           "float"),
            #("coatMap",                     "coat",           "float"),
            #("coat_rough_map",              "coat_roughness", "float"),
            #("coatRoughMap",                "coat_roughness", "float"),
            #("clearcoat_map",               "coat",           "float"),
            #("clearcoatMap",                "coat",           "float"),
            #("clearcoatRoughness_map",      "coat_roughness", "float"),
            #("clearcoatRoughnessMap",       "coat_roughness", "float"),
            -- OpenPBR spellings (mat_def:218/220/222/226/250):
            #("coat_weight_map",             "coat",           "float"),
            #("coatWeightMap",               "coat",           "float"),
            #("coat_color_map",              "coat_color",     "color3"),
            #("coatColorMap",                "coat_color",     "color3"),
            #("coat_roughness_map",          "coat_roughness", "float"),
            #("coatRoughnessMap",            "coat_roughness", "float"),
            #("coat_ior_map",                "coat_IOR",       "float"),
            #("coatIorMap",                  "coat_IOR",       "float"),
            #("coat_normal_map",             "coat_normal",    "vector3"),
            #("coatNormalMap",               "coat_normal",    "vector3"),
            -- VRayMtl spelling (V-Ray SDK canonical):
            #("coat_amount_texmap",          "coat",           "float"),
            #("coatAmountTexmap",            "coat",           "float"),
            -- MAX-MTLX-OPACITY-MAP-016 additions: soft/continuous opacity
            -- maps. The pre-existing `cutout_map` / `cutoutMap` entries
            -- (above) handle 3ds Max's BINARY hard-alpha cutout slot
            -- (mat_def:26 — either fully opaque or fully transparent
            -- per texel). These new entries cover the semantically
            -- DIFFERENT SOFT/CONTINUOUS opacity family: PhysicalMaterial
            -- `opacity_map` / `opacityMap` (mat_def:64-66 — 0..1
            -- continuous alpha for glass etch, mesh screens, tinted
            -- plastic, court boundary-line halftone maps), OpenPBR
            -- `geometry_opacity_map` / `geometryOpacityMap`
            -- (mat_def:262), and VRayMtl `texmap_opacity` (V-Ray SDK
            -- canonical). Prior-run evidence:
            --   evidence/gaps-audit.md:158-170 (S2) — every material
            --   whose alpha lives in one of these slots (rather than
            --   in `cutout_map`) exported fully opaque, silently
            --   dropping the soft-alpha authoring.
            -- All route to `ND_standard_surface.opacity` as `float`,
            -- matching the pre-existing cutout treatment. The
            -- MaterialX shader input `opacity` is color3 in the stdlib
            -- (default (1,1,1)); the evaluator broadcasts an
            -- ND_tiledimage_float into it channel-broadcast so a
            -- single-channel opacity texture reads as (a,a,a) — which
            -- is the correct semantic for a mono-alpha map. No
            -- ND_convert_float_color3 scaffolding is needed because
            -- MaterialX handles the promotion at evaluation time.
            -- No colorspace attribute is authored (`float` scalar, not
            -- `color3` — the existing type gate in
            -- `_EnrichMtlxDocFromMaxMaterial` does the right thing).
            -- Ordering: these entries come AFTER `cutout_map` in
            -- slotMap order so a material authoring BOTH cutout AND
            -- opacity_map (unusual — most workflows author one or the
            -- other) resolves to cutout under first-hit-wins. Within
            -- this block, PhysicalMaterial declared before OpenPBR
            -- before VRayMtl, snake_case before camelCase — matching
            -- every other slotMap family.
            -- PhysicalMaterial's `opacityThreshold` scalar (soft-to-
            -- hard cutoff — mat_def:66) is intentionally out of scope:
            -- authoring it requires an ND_ifgreatereq_float node
            -- inserted between the tiledimage and `opacity`, which is
            -- an authoring-path change beyond a slotMap entry. Follow-
            -- on bite candidate: `max-mtlx-017-opacity-threshold-hard-cutoff`.
            #("opacity_map",                 "opacity",        "float"),
            #("opacityMap",                  "opacity",        "float"),
            #("geometry_opacity_map",        "opacity",        "float"),
            #("geometryOpacityMap",          "opacity",        "float"),
            #("texmap_opacity",              "opacity",        "float")
        )
        local seenInputs = #()
        for currentMat in subMtls do (
            for entry in slotMap do (
                local propName  = entry[1]
                local mtlxInput = entry[2]
                local mtlxType  = entry[3]
                -- MAXScript has no `continue`; guard each stage with nested ifs.
                if (isProperty currentMat propName) then (
                    local tex = getProperty currentMat propName
                    if tex != undefined then (
                        -- MAX-MTLX-006: delegate to `resolveMaxTexmapFilename`,
                        -- which walks past wrapper maps (Color_Correction /
                        -- OutputMap / Composite / VRayColor2Bump / etc.) to
                        -- reach the leaf Bitmap. The pre-006 code only handled
                        -- direct Bitmap / #filename / #bitmap leaves and
                        -- returned undefined for wrapper-wrapped textures — the
                        -- root cause of the arch-viz 137/179 flat-material
                        -- census, where V-Ray Scene Converter had migrated the
                        -- VRayMtl.texmap_diffuse -> base_color_map slot but the
)"
        LR"(                        -- MtlxShaderWriter discovery couldn't see through the
                        -- surviving wrapper stack.
                        local fname = resolveMaxTexmapFilename tex 0
                        if fname != undefined and fname != "" then (
                            -- MAX-TEX-003: route the discovered filename through
                            -- FileResolutionManager.getFullFilePath so the
                            -- ND_tiledimage `file` input agrees, character-for-
                            -- character, with the UsdUVTexture `inputs:file` on
                            -- the dual-network sister shader — which resolves via
                            -- the same accessor in
                            -- `scripts/materials/usd_utils.get_file_path_mxs`.
                            -- Some ingest pipelines (V-Ray Scene Converter, batch
                            -- import scripts) normalize the raw `tex.filename` /
                            -- `.bitmap.filename` to lowercase; the resolver looks
                            -- up the actual on-disk case, which is what the
                            -- UsdPreviewSurface side ends up authoring too. Both
                            -- branches then serialize identical strings and the
                            -- USD is portable to case-sensitive render farms
                            -- (Linux/ARM Karma / Hydra). If the resolver can't
                            -- find the file, fall back to the raw fname so the
                            -- dangling-file case still ships an authored path
                            -- (matching legacy behavior).
                            local resolved = fname
                            local resolverOk = false
                            try (
                                resolverOk = FileResolutionManager.getFullFilePath &resolved #bitmap
                            ) catch (
                                resolverOk = false
                            )
                            if resolverOk and resolved != undefined and resolved != "" then (
                                fname = resolved
                            )
                            -- Deduplicate on the mtlx input name; first hit wins.
                            -- MAX-MTLX-007: with the outer `for currentMat in
                            -- subMtls` loop this makes baseMtl's textures win
                            -- over any coat's — the correct precedence for
                            -- layered arch-viz materials.
                            -- MAX-MTLX-OPACITY-ALPHA-018: for opacity slots whose
                            -- leaf bitmap outputs its ALPHA channel (monoOutput==1),
                            -- tag the type "float_a" so the C++ authoring reads the
                            -- real alpha (ND_tiledimage_vector4 + extract idx 3)
                            -- instead of RGB luminance (white -> always opaque).
                            local effType = mtlxType
                            if mtlxInput == "opacity" then (
                                local monoV = 0
                                try ( monoV = resolveMaxTexmapMono tex 0 ) catch ( monoV = 0 )
                                if monoV == 1 then effType = "float_a"
                            )
                            if (findItem seenInputs mtlxInput) == 0 then (
                                append seenInputs mtlxInput
                                result += (mtlxInput + "|" + effType + "|" + fname + "\n")
                            )
                        )
                    )
                )
            )
        )
        return result
    )
    discoverMaxMtlxTexmaps )";

// Parses one line of the discoverMaxMtlxTexmaps output into (input, type, path).
static bool _ParseTexmapLine(
    const std::string& line,
    std::string&       mtlxInput,
    std::string&       mtlxType,
    std::string&       filePath)
{
    auto pipe1 = line.find('|');
    if (pipe1 == std::string::npos) {
        return false;
    }
    auto pipe2 = line.find('|', pipe1 + 1);
    if (pipe2 == std::string::npos) {
        return false;
    }
    mtlxInput = line.substr(0, pipe1);
    mtlxType = line.substr(pipe1 + 1, pipe2 - pipe1 - 1);
    filePath = line.substr(pipe2 + 1);
    // trim trailing whitespace / CR
    while (!filePath.empty()
           && (filePath.back() == '\r' || filePath.back() == '\n'
               || filePath.back() == ' ')) {
        filePath.pop_back();
    }
    return !mtlxInput.empty() && !mtlxType.empty() && !filePath.empty();
}

// Locates the NodeGraph feeding a given input on a shader node. Returns null
// if the input doesn't route through a NodeGraph output.
MaterialX::NodeGraphPtr _GetInputNodeGraph(
    const MaterialX::DocumentPtr& doc,
    const MaterialX::NodePtr&     shaderNode,
    const std::string&            inputName)
{
    auto input = shaderNode->getInput(inputName);
    if (!input) {
        return nullptr;
    }
    auto ngName = input->getNodeGraphString();
    if (!ngName.empty()) {
        return doc->getNodeGraph(ngName);
    }
    return nullptr;
}

// Returns true if the NodeGraph output referenced by shaderNode->getInput(inputName)
// currently has no source (dangling — the MAX-MTLX-001 defect).
bool _IsShaderInputDangling(
    const MaterialX::DocumentPtr& doc,
    const MaterialX::NodePtr&     shaderNode,
    const std::string&            inputName)
{
    auto input = shaderNode->getInput(inputName);
    if (!input) {
        return true;
    }
    auto ng = _GetInputNodeGraph(doc, shaderNode, inputName);
    if (!ng) {
        return input->getNodeName().empty()
            && input->getValueString().empty();
    }
    auto outName = input->getOutputString();
    if (outName.empty()) {
        outName = inputName + "_output";
    }
    auto out = ng->getOutput(outName);
    if (!out) {
        return true;
    }
    return out->getNodeName().empty()
        && out->getNodeGraphString().empty();
}

// MAX-MTLX-001: inject the missing <tiledimage> nodes into the mtlxDoc's
// NodeGraphs / shader wiring based on the Max material's map-slot inventory.
// Returns the number of tiledimage nodes injected — 0 if the material is
// fully populated already or has no Bitmap slots.
size_t _EnrichMtlxDocFromMaxMaterial(
    const MaterialX::DocumentPtr& mtlxDoc,
    const MaterialX::NodePtr&     shaderNode,
    AnimHandle                    animHandle)
{
    if (!mtlxDoc || !shaderNode) {
        return 0;
    }

    FPValue rvalue;
    rvalue.Init();
    std::wstringstream ss;
    ss << discoverMaxMtlxTexmapsFn << animHandle << L'\0';
    ExecuteMAXScriptScript(
        ss.str().c_str(), MAXScript::ScriptSource::Dynamic, false, &rvalue);
    auto discovery = MaxUsd::MaxStringToUsdString(rvalue.s);
    if (discovery.empty()) {
        return 0;
    }

    size_t      injected = 0;
    std::string line;
    for (size_t start = 0; start <= discovery.size();) {
        auto nl = discovery.find('\n', start);
        if (nl == std::string::npos) {
            line = discovery.substr(start);
            start = discovery.size() + 1;
        } else {
            line = discovery.substr(start, nl - start);
            start = nl + 1;
        }
        if (line.empty()) {
            continue;
        }
        std::string mtlxInput, mtlxType, filePath;
        if (!_ParseTexmapLine(line, mtlxInput, mtlxType, filePath)) {
            continue;
        }

        // Only inject when the corresponding shader input is dangling —
        // don't overwrite MaterialX-authored connections that survived the
        // MAXScript export path.
        if (!_IsShaderInputDangling(mtlxDoc, shaderNode, mtlxInput)) {
            continue;
        }

        // Locate (or create) the NodeGraph that feeds this input.
        auto shaderInput = shaderNode->getInput(mtlxInput);
        MaterialX::NodeGraphPtr ng;
        std::string             outputName;
        if (shaderInput) {
            ng = _GetInputNodeGraph(mtlxDoc, shaderNode, mtlxInput);
            outputName = shaderInput->getOutputString();
        }
        if (outputName.empty()) {
            outputName = mtlxInput + "_output";
        }
        if (!ng) {
            // Fall back to any NodeGraph that already declares this output.
            for (auto candidate : mtlxDoc->getNodeGraphs()) {
                if (candidate->getOutput(outputName)) {
                    ng = candidate;
                    break;
                }
            }
        }
        if (!ng) {
            // No NodeGraph scaffold present — create one alongside the shader.
            auto ngName = "NG_" + shaderNode->getName();
            ng = mtlxDoc->getNodeGraph(ngName);
            if (!ng) {
                ng = mtlxDoc->addNodeGraph(ngName);
            }
        }
        if (!ng) {
            continue;
        }

        // MAX-MTLX-OPACITY-ALPHA-018: the discovery script tags opacity sourced
        // from a bitmap's ALPHA channel (Max Bitmaptexture monoOutput==1) as
        // "float_a". A plain ND_tiledimage_float reads RGB luminance, so a
        // white-RGB logo/text decal whose shape lives only in alpha exports
        // fully opaque (BUZZ/CITY marquee, DR_PEPPER, laser-cut logos). For
        // that case author an ND_tiledimage_vector4 (full RGBA read) feeding an
        // ND_extract_vector4 at index 3 (alpha) so opacity gets the real mask.
        const bool        isAlphaOpacity = (mtlxType == "float_a");
        const std::string imageType      = isAlphaOpacity ? "vector4" : mtlxType;
        const std::string outType        = isAlphaOpacity ? "float" : mtlxType;

        // Create the tiledimage node inside the NodeGraph.
        auto imgName = "img_" + mtlxInput;
        auto imgNode = ng->getNode(imgName);
        if (!imgNode) {
            imgNode = ng->addNode("tiledimage", imgName, imageType);
        }
        if (!imgNode) {
            continue;
        }
        // Author the explicit nodedef string. USD export derives `info:id` solely from
        // node->getNodeDefString() (see _GetNodeDefString: "the 3dsmax MaterialX component
        // guarantees that the nodeDefString is set"). addNode() sets category+type but NOT
        // this attribute, so without it info:id serializes empty and the node is inert —
        // MaterialX can't resolve its type, so it neither renders nor counts as a texture
        // node. Native exporter nodes always set it; match that (ND_tiledimage_<type>).
        imgNode->setNodeDefString("ND_tiledimage_" + imageType);
        auto fileInput = imgNode->getInput("file");
        if (!fileInput) {
            fileInput = imgNode->addInput("file", "filename");
        }
        if (fileInput) {
            fileInput->setValueString(filePath);
            if (imageType == "color3") {
                // Match the color space authoring the MaterialX exporter would use.
                fileInput->setAttribute("colorspace", "srgb_texture");
            }
        }

        // For alpha-sourced opacity, insert an ND_extract_vector4 (index 3)
        // between the RGBA image and the NodeGraph output so opacity reads alpha.
        std::string sourceNodeName = imgName;
        if (isAlphaOpacity) {
            auto extName = "extract_" + mtlxInput;
            auto extNode = ng->getNode(extName);
            if (!extNode) {
                extNode = ng->addNode("extract", extName, "float");
            }
            if (extNode) {
                extNode->setNodeDefString("ND_extract_vector4");
                auto inp = extNode->getInput("in");
                if (!inp) {
                    inp = extNode->addInput("in", "vector4");
                }
                if (inp) {
                    inp->setNodeName(imgName);
                }
                auto idx = extNode->getInput("index");
                if (!idx) {
                    idx = extNode->addInput("index", "integer");
                }
                if (idx) {
                    idx->setValueString("3");
                }
                sourceNodeName = extName;
            }
        }

        // Wire the NodeGraph output to the source node (image, or alpha extract).
        auto output = ng->getOutput(outputName);
        if (!output) {
            output = ng->addOutput(outputName, outType);
        }
        if (output) {
            output->setNodeName(sourceNodeName);
            // Clear any stale nodegraph reference that would fight the direct
            // connection.
            if (output->hasAttribute("nodegraph")) {
                output->removeAttribute("nodegraph");
            }
        }

        // Ensure the shader input references this NodeGraph + output. It usually
        // already does, but be defensive against exports that emitted a bare
        // constant value.
        if (shaderInput) {
            shaderInput->setNodeGraphString(ng->getName());
            shaderInput->setOutputString(outputName);
            // Remove any constant value that would shadow the connection.
            if (shaderInput->hasAttribute("value")) {
                shaderInput->removeAttribute("value");
            }
        }

        // Special-case: `normal` on ND_standard_surface flows through
        // ND_normalmap_float, whose `in` input needs a vector3 image. If the
        // NodeGraph already contains an ND_normalmap_float with a dangling
        // `in`, re-route the output through it and connect the tiledimage.
        if (mtlxInput == "normal") {
            for (auto n : ng->getNodes()) {
                if (n->getCategory() == "normalmap"
                    || n->getName().find("normalmap") != std::string::npos) {
                    auto nmIn = n->getInput("in");
                    if (nmIn && nmIn->getNodeName().empty()
                        && nmIn->getValueString().empty()) {
                        if (!nmIn) {
                            nmIn = n->addInput("in", "vector3");
                        }
                        nmIn->setNodeName(imgName);
                    }
                    // Point NodeGraph output at the normalmap instead of the
                    // raw image so downstream tangent-space handling kicks in.
                    if (output) {
                        output->setNodeName(n->getName());
                    }
                    break;
                }
            }
        }

        ++injected;
    }
    return injected;
}

// MAX-MTLX-002: after MAX-MTLX-001 wires up every Bitmap-backed input, walk
// the remaining ND_standard_surface inputs and query the Max material for the
// constant value that was authored on each un-textured slot. Returned string
// is one entry per line: `mtlxInputName|mtlxType|value`, where value is a
// comma-separated triple for color3 or a single float for float. The color
// components are normalized to [0,1] from Max's [0,255] float color scale.
// Emission-weight & metalness are only meaningful when > 0 so they are
// skipped when the Max property is 0 (preserving the ND_standard_surface
// NodeDef default rather than authoring redundant zeros).
static const TSTR discoverMaxMtlxConstantsFn = LR"(
    fn discoverMaxMtlxConstants materialAnimHandle = (
        local m = getAnimByHandle materialAnimHandle
        local result = ""
        if m == undefined then return result
        -- MAX-MTLX-VRAYCOLOR-016: a VRayColor solid-color node carries no file, so the filename resolver
        -- drops it and base_color falls back to the material's placeholder diffuse (a wrong color -- e.g.
        -- the green on WALL-PAINT-WHITE's aisles). Read the VRayColor's actual output color (red/green/
        -- blue * rgb_multiplier, 0-1) so it can be authored as the base_color constant. Walks the common
        -- wrapper carriers to reach a nested VRayColor.
        fn getVRayColorRGB tex depth = (
            if tex == undefined or depth > 6 then ( undefined ) else (
                if ((classof tex) as string) == "VRayColor" then (
                    local mlt = (try (getProperty tex #rgb_multiplier) catch (1.0))
                    if mlt == undefined then mlt = 1.0
                    local rr = (try ((getProperty tex #red) * mlt) catch (undefined))
                    local gg = (try ((getProperty tex #green) * mlt) catch (undefined))
                    local bb = (try ((getProperty tex #blue) * mlt) catch (undefined))
                    if rr == undefined then ( undefined ) else (
                        if rr > 1.0 do rr = 1.0
                        if gg > 1.0 do gg = 1.0
                        if bb > 1.0 do bb = 1.0
                        ((rr as string) + "," + (gg as string) + "," + (bb as string))
                    )
                ) else (
                    local nx = undefined
                    if (isProperty tex #map) then nx = getProperty tex #map
                    if nx == undefined and (isProperty tex #map1) then nx = getProperty tex #map1
                    if nx == undefined and (isProperty tex #sourceA) then nx = getProperty tex #sourceA
                    if nx == undefined and (isProperty tex #baseTex) then nx = getProperty tex #baseTex
                    if nx != undefined then ( getVRayColorRGB nx (depth + 1) ) else ( undefined )
                )
            )
        )
        -- Slot map: (Max PhysicalMaterial/OpenPBR property name,
        --           ND_standard_surface input name,
        --           MaterialX type token)
        local slotMap = #(
            #("base_color",         "base_color",         "color3"),
            #("baseColor",          "base_color",         "color3"),
            #("Base_Color",         "base_color",         "color3"),
            #("roughness",          "specular_roughness", "float"),
            #("Roughness",          "specular_roughness", "float"),
            #("metalness",          "metalness",          "float"),
            #("Metalness",          "metalness",          "float"),
            #("emit_color",         "emission_color",     "color3"),
            #("emissionColor",      "emission_color",     "color3"),
            #("refl_color",         "specular_color",     "color3"),
            #("specularColor",      "specular_color",     "color3"),
            #("Reflection_Color",   "specular_color",     "color3"),
            #("trans_color",        "transmission_color", "color3"),
            #("transmissionColor",  "transmission_color", "color3"),
            #("transparency",       "transmission",       "float"),
            #("cutout",             "opacity",            "float"),
            #("cutoutOpacity",      "opacity",            "float")
        )
        local seenInputs = #()
        -- MAX-MTLX-VRAYCOLOR-016: if the diffuse/base_color slot is a VRayColor solid-color node, author
        -- ITS color as base_color and mark the input seen so the slotMap loop below won't overwrite it
        -- with the material's placeholder diffuse.
        local bcMap = (try (getProperty m #base_color_map) catch (undefined))
        if bcMap == undefined do ( bcMap = (try (getProperty m #texmap_diffuse) catch (undefined)) )
        local vcRGB = (try (getVRayColorRGB bcMap 0) catch (undefined))
        if vcRGB != undefined do ( result += ("base_color|color3|" + vcRGB + "\n"); append seenInputs "base_color" )
        for entry in slotMap do (
            local propName  = entry[1]
            local mtlxInput = entry[2]
            local mtlxType  = entry[3]
            if (isProperty m propName) then (
                local v = getProperty m propName
                if v != undefined then (
                    if (findItem seenInputs mtlxInput) == 0 then (
                        append seenInputs mtlxInput
                        local valStr = ""
                        if mtlxType == "color3" then (
                            -- Max colors are in [0,255] float; normalize to [0,1].
                            -- We author the value in the same nominal color space
                            -- MtlxIOUtil uses for direct shader-input values
                            -- (linear-in-name; downstream tools honor colorSpace
                            -- metadata on the attribute — see color3 branch of
                            -- _SetInputValue for the paired-Bitmap case).
                            local rn = (v.r / 255.0)
                            local gn = (v.g / 255.0)
                            local bn = (v.b / 255.0)
                            valStr = ((rn as string) + "," + (gn as string) + "," + (bn as string))
                        ) else (
                            -- Floats and cutout/opacity come through as-is.
                            valStr = v as string
                        )
                        result += (mtlxInput + "|" + mtlxType + "|" + valStr + "\n")
                    )
                )
            )
        )
        return result
    )
    discoverMaxMtlxConstants )";

// Parses one line of the discoverMaxMtlxConstants output into (input, type, value).
// Mirrors _ParseTexmapLine's contract; kept separate so evolution of one does
// not silently break the other.
static bool _ParseConstantLine(
    const std::string& line,
    std::string&       mtlxInput,
    std::string&       mtlxType,
    std::string&       valStr)
{
    auto pipe1 = line.find('|');
    if (pipe1 == std::string::npos) {
        return false;
    }
    auto pipe2 = line.find('|', pipe1 + 1);
    if (pipe2 == std::string::npos) {
        return false;
    }
    mtlxInput = line.substr(0, pipe1);
    mtlxType = line.substr(pipe1 + 1, pipe2 - pipe1 - 1);
    valStr = line.substr(pipe2 + 1);
    while (!valStr.empty()
           && (valStr.back() == '\r' || valStr.back() == '\n'
               || valStr.back() == ' ')) {
        valStr.pop_back();
    }
    return !mtlxInput.empty() && !mtlxType.empty() && !valStr.empty();
}

// MAX-MTLX-002: after `_EnrichMtlxDocFromMaxMaterial` has wired every Bitmap
// slot it could recover, some ND_standard_surface inputs still reference
// NodeGraph outputs whose interior source `MtlxIOUtil.ExportMtlxString`
// dropped and no Bitmap map slot exists to restore them (117 dangling
// outputs across 81 arena materials in the MAX-MTLX-001 baseline: 74
// base_color, 28 specular_roughness, 7 opacity, 7 specular_color, 1
// transmission_color). Downstream Karma / Hydra evaluates a connection to
// a source-less NodeGraph output as zero, so every affected material
// renders black-on-that-input even though the artist authored a solid
// color.
//
// Fix: for each dangling input, drop the NodeGraph reference on the
// shader input (converting the connection back into a bare value slot)
// and set a constant value read from the Max material's PhysicalMaterial
// / OpenPBR property. Also drop the now-orphaned NG output so we do not
// leave declared-but-unused ports in the exported USD.  If the shader
// input already has a value string from `MtlxIOUtil` we prefer that; a
// MAXScript query is only consulted for inputs where the writer emitted
// nothing at all.
//
// Returns the number of shader inputs pruned. No-op when the mtlxDoc has
// no dangling connections (e.g. every input was Bitmap-wired by
// MAX-MTLX-001, or a future Autodesk fix to MtlxIOUtil populates them).
size_t _PruneDanglingNodeGraphOutputs(
    const MaterialX::DocumentPtr& mtlxDoc,
    const MaterialX::NodePtr&     shaderNode,
    AnimHandle                    animHandle)
{
    if (!mtlxDoc || !shaderNode) {
        return 0;
    }

    // Collect the set of currently-dangling shader inputs first, so a
    // single MAXScript discovery call can populate them all.
    std::vector<MaterialX::InputPtr> danglingInputs;
    for (auto input : shaderNode->getInputs()) {
        if (input->getNodeGraphString().empty()
            && input->getOutputString().empty()) {
            // Not wired through a NodeGraph — nothing for us to do.
            continue;
        }
        if (_IsShaderInputDangling(mtlxDoc, shaderNode, input->getName())) {
            danglingInputs.push_back(input);
        }
    }
    if (danglingInputs.empty()) {
        return 0;
    }

    // Query Max for the constant values that live on the shader inputs
    // whose NG side has no interior source. Building the lookup once is
    // cheaper than round-tripping to MAXScript per-input.
    std::map<std::string, std::pair<std::string, std::string>> constantByInput;
    {
        FPValue rvalue;
        rvalue.Init();
        std::wstringstream ss;
        ss << discoverMaxMtlxConstantsFn << animHandle << L'\0';
        ExecuteMAXScriptScript(
            ss.str().c_str(), MAXScript::ScriptSource::Dynamic, false, &rvalue);
        auto discovery = MaxUsd::MaxStringToUsdString(rvalue.s);
        for (size_t start = 0; start <= discovery.size();) {
            auto nl = discovery.find('\n', start);
            std::string line;
            if (nl == std::string::npos) {
                line = discovery.substr(start);
                start = discovery.size() + 1;
            } else {
                line = discovery.substr(start, nl - start);
                start = nl + 1;
            }
            if (line.empty()) {
                continue;
            }
            std::string mtlxInput, mtlxType, valStr;
            if (!_ParseConstantLine(line, mtlxInput, mtlxType, valStr)) {
                continue;
            }
            constantByInput[mtlxInput] = std::make_pair(mtlxType, valStr);
        }
    }

    size_t pruned = 0;
    std::set<std::pair<std::string, std::string>> outputsToRemove;

    for (auto input : danglingInputs) {
        auto inputName = input->getName();

        // Note which NG output is being orphaned before mutating the input.
        auto ngName = input->getNodeGraphString();
        std::string outputName = input->getOutputString();
        if (outputName.empty()) {
            outputName = inputName + "_output";
        }
        if (!ngName.empty()) {
            outputsToRemove.insert(std::make_pair(ngName, outputName));
        }

        // Break the connection. `nodegraph` / `output` attributes on a
        // MaterialX shader input are how NodeGraph promotion is expressed
        // in the serialized doc; clearing them turns the input back into
        // a bare value slot.
        if (input->hasAttribute("nodegraph")) {
            input->removeAttribute("nodegraph");
        }
        if (input->hasAttribute("output")) {
            input->removeAttribute("output");
        }
        // A `nodename` on a shader input (rare here — that's the "direct
        // shader connection" form) would also point at a now-gone node;
        // clear it defensively.
        if (input->hasAttribute("nodename")) {
            input->removeAttribute("nodename");
        }

        // If MtlxIOUtil didn't author a value alongside the connection,
        // read the constant we discovered from the Max material.
        if (input->getValueString().empty()) {
            auto it = constantByInput.find(inputName);
            if (it != constantByInput.end()) {
                const auto& mtlxType = it->second.first;
                const auto& valStr = it->second.second;
                // Only set the value when the type on the shader input
                // (declared by the NodeDef) matches the type we read from
                // Max. Mismatches are dropped rather than coerced — better
                // to fall back to the NodeDef default than author the wrong
                // type. In practice all mappings above are one-to-one, so
                // this guard is belt-and-suspenders.
                if (input->getType() == mtlxType) {
                    input->setValueString(valStr);
                }
            }
        }

        ++pruned;
    }

    // Drop the now-orphaned NG outputs so the exported USD does not
    // carry declared-but-unused NodeGraph ports.
    for (const auto& ngOut : outputsToRemove) {
        auto ng = mtlxDoc->getNodeGraph(ngOut.first);
        if (!ng) {
            continue;
        }
        // Do not remove the output if some *other* shader input still
        // references it (e.g. two ND_standard_surface siblings sharing
        // one NG in an unusual authoring). Cheap safety check.
        bool referencedElsewhere = false;
        for (auto sibling : shaderNode->getInputs()) {
            if (sibling->getNodeGraphString() == ngOut.first
                && sibling->getOutputString() == ngOut.second) {
                referencedElsewhere = true;
                break;
            }
            if (sibling->getNodeGraphString() == ngOut.first
                && sibling->getOutputString().empty()
                && (sibling->getName() + "_output") == ngOut.second) {
                referencedElsewhere = true;
                break;
            }
        }
        if (referencedElsewhere) {
            continue;
        }
        auto output = ng->getOutput(ngOut.second);
        if (output) {
            ng->removeOutput(ngOut.second);
        }
    }

    return pruned;
}

// MAX-MTLX-003: the ND_normalmap_float sub-shader that scaffolds the normal
// branch of a MaterialX NodeGraph reaches the exporter with its own `in`
// input dropped by `MtlxIOUtil.ExportMtlxString`. MAX-MTLX-001 only wires
// the tiledimage → normalmap.in when the shader-side `normal` input is
// itself dangling (i.e. the NG's `normal_output` had no source); it skips
// the case where the NG's `normal_output` DOES connect to a normalmap node
// but that node's own `in` is the dangling port. In the diagnostic run's
// baseline (`08_wrap_up.txt`, pipeline `0b6d5e38`), only 4 of 45 scaffolded
// ND_normalmap_float nodes had `in` wired to an ND_tiledimage_vector3; the
// other 41 fell through to the port default, so those materials lose their
// normal mapping entirely.
//
// Fix: after `_EnrichMtlxDocFromMaxMaterial` (MAX-MTLX-001) and
// `_PruneDanglingNodeGraphOutputs` (MAX-MTLX-002) run, walk every
// normalmap-class node in every NodeGraph belonging to this shader; for
// any whose `in` input is still dangling, re-run `discoverMaxMtlxTexmaps`
// to find the Bitmap-backed normal slot on the Max material, inject an
// `img_normal` (or reuse) ND_tiledimage_vector3 in the same NodeGraph,
// and wire the normalmap's `in` to it. This is the wiring MAX-MTLX-001
// would have authored had its outer skip check considered the normalmap's
// own port state instead of only the shader's `normal` input.
//
// Returns the number of normalmap.in connections wired. No-op when there
// is no dangling normalmap.in (either every normalmap was already wired
// by MtlxIOUtil / MAX-MTLX-001, or the material carries no normal map
// slot at all — in which case Karma correctly falls through to the port
// default and no wire is authored).
size_t _WireDanglingNormalmapInputs(
    const MaterialX::DocumentPtr& mtlxDoc,
    const MaterialX::NodePtr&     shaderNode,
    AnimHandle                    animHandle)
{
    if (!mtlxDoc || !shaderNode) {
        return 0;
    }

    // Collect (nodegraph, normalmap-node) pairs whose `in` input is
    // currently dangling. Doing this pass first means we only pay the
    // MAXScript round-trip when the shader actually has a normal branch
    // that needs help.
    std::vector<std::pair<MaterialX::NodeGraphPtr, MaterialX::NodePtr>> targets;
    for (auto ng : mtlxDoc->getNodeGraphs()) {
        for (auto n : ng->getNodes()) {
            const bool isNormalmap
                = n->getCategory() == "normalmap"
                  || n->getName().find("normalmap") != std::string::npos;
            if (!isNormalmap) {
                continue;
            }
            auto nmIn = n->getInput("in");
            // Consider the input dangling when it has no source node AND
            // no explicit value string. A normalmap authored with a real
            // vector3 value would be preserved by MtlxIOUtil and needs no
            // help.
            const bool inDangling
                = !nmIn
                  || (nmIn->getNodeName().empty()
                      && nmIn->getNodeGraphString().empty()
                      && nmIn->getOutputString().empty()
                      && nmIn->getValueString().empty());
            if (inDangling) {
                targets.emplace_back(ng, n);
            }
        }
    }
    if (targets.empty()) {
        return 0;
    }

    // Ask Max for the material's Bitmap-backed map slots. The `normal`
    // entry (if any) carries the filename we need to wire in. Reuses the
    // same MAX-MTLX-001 helper so the two paths stay in lockstep — if a
    // future edit teaches `discoverMaxMtlxTexmaps` about a new normal-map
    // property spelling, this fix picks it up automatically.
    std::string normalFilePath;
    {
        FPValue rvalue;
        rvalue.Init();
        std::wstringstream ss;
        ss << discoverMaxMtlxTexmapsFn << animHandle << L'\0';
        ExecuteMAXScriptScript(
            ss.str().c_str(), MAXScript::ScriptSource::Dynamic, false, &rvalue);
        auto discovery = MaxUsd::MaxStringToUsdString(rvalue.s);
        for (size_t start = 0; start <= discovery.size();) {
            auto nl = discovery.find('\n', start);
            std::string line;
            if (nl == std::string::npos) {
                line = discovery.substr(start);
                start = discovery.size() + 1;
            } else {
                line = discovery.substr(start, nl - start);
                start = nl + 1;
            }
            if (line.empty()) {
                continue;
            }
            std::string mtlxInput, mtlxType, filePath;
            if (!_ParseTexmapLine(line, mtlxInput, mtlxType, filePath)) {
                continue;
            }
            if (mtlxInput == "normal") {
                normalFilePath = filePath;
                break;
            }
        }
    }
    if (normalFilePath.empty()) {
        // No discoverable normal Bitmap on the Max side. Leave the
        // normalmap's `in` at its port default — this is the same behavior
        // downstream Karma / Hydra sees today and matches MAX-MTLX-001's
        // conservative no-op contract when a slot cannot be recovered.
        return 0;
    }

    size_t wired = 0;
    for (const auto& targetPair : targets) {
        auto ng = targetPair.first;
        auto normalmap = targetPair.second;

        // Reuse an existing `img_normal` in the same NodeGraph if one is
        // already there (defensive against future edits that pre-populate
        // the tiledimage but forget to wire it). Otherwise create the
        // ND_tiledimage_vector3 alongside the normalmap.
        const std::string imgName = "img_normal";
        auto              imgNode = ng->getNode(imgName);
        if (!imgNode) {
            imgNode = ng->addNode("tiledimage", imgName, "vector3");
        }
        if (!imgNode) {
            continue;
        }
        // Author the explicit nodedef so USD export sets info:id (see the matching comment
        // and _GetNodeDefString). Without it the injected normal-map image node is inert.
        imgNode->setNodeDefString("ND_tiledimage_vector3");
        auto fileInput = imgNode->getInput("file");
        if (!fileInput) {
            fileInput = imgNode->addInput("file", "filename");
        }
        if (fileInput) {
            fileInput->setValueString(normalFilePath);
            // The MaterialX exporter tags color3 filename inputs with
            // colorspace="srgb_texture" to route them through the sRGB
            // decode. For a normal map the raw texels ARE the tangent-space
            // vector, not sRGB-encoded color, so we omit that attribute —
            // matching the color-space handling MAX-MTLX-001 uses for its
            // vector3 slot.
            if (fileInput->hasAttribute("colorspace")) {
                fileInput->removeAttribute("colorspace");
            }
        }

        // Wire the normalmap's `in` to the tiledimage. Add the input if
        // MtlxIOUtil dropped it entirely (the common case) or update it
        // in place if a bare declaration survived.
        auto nmIn = normalmap->getInput("in");
        if (!nmIn) {
            nmIn = normalmap->addInput("in", "vector3");
        }
        if (nmIn) {
            nmIn->setNodeName(imgName);
            // Any stale value string would shadow the source connection
            // in some MaterialX evaluators — clear it defensively.
            if (nmIn->hasAttribute("value")) {
                nmIn->removeAttribute("value");
            }
            ++wired;
        }
    }
    return wired;
}

// MAX-MTLX-012: bump / normal-map STRENGTH scalar authoring on ND_normalmap.
//
// Every ND_normalmap_float scaffolded by MAX-MTLX-001 / MAX-MTLX-003 runs at
// its port-default `scale = 1.0` regardless of the source material's authored
// bump strength. On PhysicalMaterial that scalar lives at `.bump_map_amt`
// (float, default 1.0 in the UI), on VRayMtl at `.bump_multiplier` (float,
// default 1.0), on OpenPBR at `.bumpMapAmount` (mirror of PhysicalMaterial).
// Baseline arch-viz scenes ship a mix of these where the artist has dialed
// bump strength down (0.2-0.4) for weathered stone / carpet weave, and up
// (2.0+) for hero brick / masonry — every one of those materials serialized
// today with `scale` at 1.0, so Karma / Hydra render the bump at the WRONG
// intensity even when MAX-MTLX-003 successfully wires the tiledimage into
// `normalmap.in`. That is: the map data is present, the strength scalar is
// dropped.
//
// Fix: after `_WireDanglingNormalmapInputs` has ensured every normalmap has
// a source-wired `in`, walk the same normalmap-class node set and author
// `scale` from the Max material's bump-strength property. Uses a companion
// MAXScript helper (`discoverMaxMtlxBumpStrengthFn`) that mirrors the
// MAX-MTLX-007 wrapper-walk (base-first VRayBlendMtl / VRayOverrideMtl
// unwrap) so the base sub-material's bump strength wins over any coat's —
// same first-hit-wins precedence as `discoverMaxMtlxTexmaps`.
//
// Returns the number of normalmap nodes whose `scale` was authored. No-op
// when the material has no bump-strength property, when the property is
// present but equals the ND_normalmap port default (1.0 — nothing to
// author) so we do not pollute the exported doc with `1.0` no-ops, and
// when the shader has no normalmap nodes to touch (either UsdPreviewSurface-
// only material or a material with no normal branch at all — in which case
// MAX-MTLX-003 also correctly no-ops).

// MAX-MTLX-012: MAXScript helper that probes the Max material's bump-strength
// scalar. Returns a numeric string ("0.35", "2.0", ...) or "" when no
// property was found on any sub-material. Base-first traversal (same
// `unwrapBlendMaterialSubMtls` recursion order as `discoverMaxMtlxTexmaps`)
// so baseMtl's strength wins over any coat's.
//
// Property table (Max property → ND_normalmap.scale). Order matters — a hit
// on ANY row for a given sub-material short-circuits the rest for that
// sub-material, then the outer loop moves on to the next sub-material only
// if no hit was found. PhysicalMaterial + OpenPBR spellings first because
// they are the concrete surface classes both stock and V-Ray-Scene-Converted
// materials collapse to on modern arch-viz scenes; VRayMtl next for the
// legacy V-Ray direct-surface case.
static const TSTR discoverMaxMtlxBumpStrengthFn = LR"(
    fn discoverMaxMtlxBumpStrength materialAnimHandle = (
        local m = getAnimByHandle materialAnimHandle
        if m == undefined then return ""
        -- Reuse the MAX-MTLX-007 wrapper-walk so blend / override wrappers
        -- expand to their concrete sub-materials with base-first order. Non-
        -- wrapper materials return `#(m)` — 1-element list, behavior byte-
        -- identical to a direct probe on the surface class.
        local subMtls = unwrapBlendMaterialSubMtls m #() 0
        -- Property table: Max property spelling → note. Only the property
        -- name matters at runtime; the note is for the reader.
        --   bump_map_amt      — PhysicalMaterial (snake_case runtime API)
        --   bumpMapAmount     — PhysicalMaterial / OpenPBR (camelCase spelling
        --                       some MAXScript-authored scenes use)
        --   bump_multiplier   — VRayMtl (legacy V-Ray direct-surface case
        --                       where Scene Converter has NOT run)
        --   bumpAmount        — StdMaterial-flavored name (rare, tolerated
        --                       for legacy scenes that authored their own
        --                       Standard material with a custom scalar).
        local propNames = #(#bump_map_amt, #bumpMapAmount, #bump_multiplier, #bumpAmount)
        for currentMat in subMtls do (
            for pn in propNames do (
                if (isProperty currentMat pn) then (
                    local v = getProperty currentMat pn
                    if v != undefined then (
                        -- Only accept numeric values (float / integer). Reject
                        -- everything else (e.g. a Texmap in the same-name slot
                        -- on an unfamiliar wrapper) rather than mis-authoring
                        -- a garbage `scale`.
                        local vc = classOf v
                        if vc == Float or vc == Double or vc == Integer or vc == Integer64 then (
                            return (v as string)
                        )
                    )
                )
            )
        )
        return ""
    )
    discoverMaxMtlxBumpStrength )";

// Parses the MAXScript strength string into a float. Returns true on success.
// Empty string / non-numeric input → false. The MAXScript helper itself only
// emits stringified numeric values, so a parse failure here means the
// MAXScript env returned something unexpected — treat as "no strength" and
// let the caller no-op.
static bool _ParseBumpStrengthString(const std::string& s, float& out)
{
    if (s.empty()) {
        return false;
    }
    try {
        size_t pos = 0;
        out = std::stof(s, &pos);
        // Guard against negative-strength authoring — MaterialX ND_normalmap
        // treats scale as a magnitude multiplier, and a negative value would
        // invert the tangent-space delta (renderer-defined behavior). Max
        // clamps its own bump UI to a non-negative range; keep the same
        // contract here.
        if (out < 0.0f) {
            out = 0.0f;
        }
        return pos > 0;
    } catch (...) {
        return false;
    }
}

size_t _ApplyBumpStrengthToNormalmaps(
    const MaterialX::DocumentPtr& mtlxDoc,
    const MaterialX::NodePtr&     shaderNode,
    AnimHandle                    animHandle)
{
    if (!mtlxDoc || !shaderNode) {
        return 0;
    }

    // Collect normalmap-class nodes first. If there are none, don't pay the
    // MAXScript round-trip. Same category / name gate as
    // `_WireDanglingNormalmapInputs` so the two functions agree on which
    // nodes count as normalmap.
    std::vector<MaterialX::NodePtr> normalmaps;
    for (auto ng : mtlxDoc->getNodeGraphs()) {
        for (auto n : ng->getNodes()) {
            const bool isNormalmap
                = n->getCategory() == "normalmap"
                  || n->getName().find("normalmap") != std::string::npos;
            if (isNormalmap) {
                normalmaps.push_back(n);
            }
        }
    }
    if (normalmaps.empty()) {
        return 0;
    }

    // Ask Max for the material's bump-strength scalar.
    std::string strengthStr;
    {
        FPValue rvalue;
        rvalue.Init();
        std::wstringstream ss;
        ss << discoverMaxMtlxBumpStrengthFn << animHandle << L'\0';
        ExecuteMAXScriptScript(
            ss.str().c_str(), MAXScript::ScriptSource::Dynamic, false, &rvalue);
        strengthStr = MaxUsd::MaxStringToUsdString(rvalue.s);
        // Trim trailing whitespace / CR that some MAXScript stringifications
        // leave behind on certain locales.
        while (!strengthStr.empty()
               && (strengthStr.back() == '\r' || strengthStr.back() == '\n'
                   || strengthStr.back() == ' ' || strengthStr.back() == '\t')) {
            strengthStr.pop_back();
        }
    }
    float strength = 1.0f;
    if (!_ParseBumpStrengthString(strengthStr, strength)) {
        // No probeable strength on the Max side. Leave every normalmap's
        // `scale` at its port default — matches MAX-MTLX-001's conservative
        // no-op contract when a value cannot be recovered.
        return 0;
    }

    // Skip the port default. Authoring `scale = 1.0` is a no-op in the
    // MaterialX evaluator and would only clutter the exported doc with
    // synthetic-looking inputs on every material whose artist accepted the
    // Max UI's default. Use a small epsilon so a value like 0.9999999f from
    // MAXScript stringification still round-trips to the port default.
    if (std::fabs(strength - 1.0f) < 1e-6f) {
        return 0;
    }

    size_t authored = 0;
    for (const auto& normalmap : normalmaps) {
        // `scale` is the MaterialX 1.38+ spelling for the bump-strength
        // multiplier on ND_normalmap_float / ND_normalmap_vector2. See
        // `MaterialX/libraries/stdlib/stdlib_defs.mtlx` — the port is
        // `<input name="scale" type="float" value="1.0" />` on
        // ND_normalmap_float and `type="vector2"` on ND_normalmap_vector2.
        // We treat the value as a scalar float for both; for vector2 the
        // MaterialX doc will coerce a single float to (v, v) on read.
        auto scaleInput = normalmap->getInput("scale");
        if (!scaleInput) {
            scaleInput = normalmap->addInput("scale", "float");
        }
        if (!scaleInput) {
            continue;
        }
        // Clear any pre-existing connection so the value opinion wins. In
        // practice MtlxIOUtil.ExportMtlxString drops the scale input the
        // same way it drops `in`, so a connection here would be a synthetic
        // survivor — but be defensive.
        if (!scaleInput->getNodeName().empty()) {
            scaleInput->setNodeName("");
        }
        if (!scaleInput->getNodeGraphString().empty()) {
            scaleInput->setNodeGraphString("");
        }
        if (!scaleInput->getOutputString().empty()) {
            scaleInput->setOutputString("");
        }
        scaleInput->setValueString(strengthStr);
        ++authored;
    }
    return authored;
}

// MAX-MTLX-GLOSSINESS-INVERT-018: Route VRayMtl `texmap_reflectionGlossiness`
// and `texmap_refractionGlossiness` to ND_standard_surface's
// `specular_roughness` and `transmission_extra_roughness` respectively, via
// an `ND_invert_float` node inserted between the tiledimage and the
// NodeGraph output. Rationale: V-Ray's glossiness convention is
// (0 = rough, 1 = smooth); ND_standard_surface roughness is
// (0 = smooth, 1 = rough). Wiring the tiledimage directly to the roughness
// input would produce a SEMANTICALLY INVERTED map — polished chrome would
// render as brushed, frosted glass as polished. MAX-MTLX-011 explicitly
// scoped these V-Ray-specific slots out and punted them to this bite; see
// `MtlxShaderWriter.cpp:704-717` (the MAX-MTLX-011 slotMap comment block).
//
// Ordering: runs AFTER `_EnrichMtlxDocFromMaxMaterial` (MAX-MTLX-001), so
// materials that ALREADY have a polarity-correct roughness map wired via
// MTLX-001's slotMap (`roughness_map` → specular_roughness or MTLX-011's
// `trans_roughness_map` → transmission_extra_roughness) are respected: the
// `_IsShaderInputDangling` guard makes this pass a no-op for those inputs,
// preserving MTLX-001/011's direct wiring and surgical scope.
//
// Ordering: runs BEFORE `_AddDependentNodes`, so the invert + tiledimage
// nodes end up serialized to USD Shader prims by the standard walker.
//
// Scope: VRayMtl-only. The V-Ray SDK is the only material family whose
// canonical roughness-family texmap is authored as GLOSSINESS. Every
// PhysicalMaterial / OpenPBR-derived variant already exposes a roughness-
// convention map slot handled by MTLX-001 (`roughness_map` / `roughnessMap`
// / MTLX-011's transmission-roughness spellings). The wrapper walk reuses
// `unwrapBlendMaterialSubMtls` (MAX-MTLX-007 / MTLX-COMPOSITE-DECAL-014) so
// VRayBlendMtl / VRayOverrideMtl / Composite / Blend materials with a
// nested V-Ray sub-material also get the invert scaffolding via base-first
// precedence, matching MTLX-001's convention.
//
// Graph shape authored (post-fix):
//
//   [img_specular_roughness_gloss (tiledimage_float)]     [file = <glossiness map path>]
//                              |
//                             (in)
//                              v
//   [invert_specular_roughness_gloss (invert_float)]      [amount default = 1.0]
//                              |
//                             (out)
//                              v
//   [NG.specular_roughness_output]                        [output → invert node]
//                              |
//                              v
//   [ND_standard_surface.specular_roughness]              [input → NG output]
//
// ND_invert_float computes `out = amount - in`, so with the default
// `amount = 1.0` the result is `1.0 - glossiness = roughness` — the
// canonical V-Ray → MaterialX convention flip. Verified via hython:
//   mx.getNodeDef("ND_invert_float").getActiveInput("amount")
//     -> Input(type=float, default='1.0')
//
// No colorspace attribute on the tiledimage. Glossiness is a raw scalar
// (0..1), not a color — an sRGB → linear decode would silently gamma-shift
// the value. The existing type gate in `_EnrichMtlxDocFromMaxMaterial` only
// authors `colorspace="srgb_texture"` when `mtlxType == "color3"`, and we
// author `"float"` here for the same reason.

// MAX-MTLX-GLOSSINESS-INVERT-018: MAXScript helper that probes VRayMtl's
// canonical glossiness texmap slots. Returns pipe-delimited lines
// `<mtlxInput>|<absolute file path>`, one per discovered map. Empty result
// when no V-Ray glossiness map is discoverable on any sub-material.
//
// Reuses `unwrapBlendMaterialSubMtls` defined by the earlier
// `discoverMaxMtlxTexmapsFn` invocation (MAX-MTLX-001) — the MAXScript
// runtime persists global fn definitions across `ExecuteMAXScriptScript`
// calls within a single writer Write() pass, and MTLX-012's
// `discoverMaxMtlxBumpStrengthFn` already relies on the same persistence
// (see line ~1809).
//
// Ordering inside the slotMap: `texmap_reflectionGlossiness` before
// `texmap_refractionGlossiness` so a VRayMtl authoring BOTH maps
// still emits BOTH inverted-roughness paths (they target DIFFERENT
// ND_standard_surface inputs — reflection → specular_roughness,
// refraction → transmission_extra_roughness — so first-hit-wins does
// NOT collide between them; the seenInputs dedupe tracks them
// independently).
//
// Uses the same FileResolutionManager resolver call as MAX-TEX-003 so
// mixed-case texture paths agree with the UsdPreviewSurface side's
// `inputs:file` on the dual-network export.
static const TSTR discoverMaxVRayGlossinessMapsFn = LR"(
    fn discoverMaxVRayGlossinessMaps materialAnimHandle = (
        local m = getAnimByHandle materialAnimHandle
        local result = ""
        if m == undefined then return result
        local subMtls = unwrapBlendMaterialSubMtls m #() 0
        local slotMap = #(
            #("texmap_reflectionGlossiness",  "specular_roughness"),
            #("texmap_refractionGlossiness",  "transmission_extra_roughness")
        )
        local seenInputs = #()
        for currentMat in subMtls do (
            for entry in slotMap do (
                local propName  = entry[1]
                local mtlxInput = entry[2]
                if (isProperty currentMat propName) then (
                    local tex = getProperty currentMat propName
                    if tex != undefined then (
                        local fname = resolveMaxTexmapFilename tex 0
                        if fname != undefined and fname != "" then (
                            local resolved = fname
                            local resolverOk = false
                            try (
                                resolverOk = FileResolutionManager.getFullFilePath &resolved #bitmap
                            ) catch (
                                resolverOk = false
                            )
                            if resolverOk and resolved != undefined and resolved != "" then (
                                fname = resolved
                            )
                            if (findItem seenInputs mtlxInput) == 0 then (
                                append seenInputs mtlxInput
                                result += (mtlxInput + "|" + fname + "\n")
                            )
                        )
                    )
                )
            )
        )
        return result
    )
    discoverMaxVRayGlossinessMaps )";

// MAX-MTLX-GLOSSINESS-INVERT-018: parses one line of the
// discoverMaxVRayGlossinessMaps output into (mtlxInput, filePath).
static bool _ParseGlossinessLine(
    const std::string& line,
    std::string&       mtlxInput,
    std::string&       filePath)
{
    auto pipe = line.find('|');
    if (pipe == std::string::npos) {
        return false;
    }
    mtlxInput = line.substr(0, pipe);
    filePath = line.substr(pipe + 1);
    while (!filePath.empty()
           && (filePath.back() == '\r' || filePath.back() == '\n'
               || filePath.back() == ' ')) {
        filePath.pop_back();
    }
    return !mtlxInput.empty() && !filePath.empty();
}

// MAX-MTLX-GLOSSINESS-INVERT-018: for each VRayMtl glossiness texmap the
// MAXScript probe surfaces, scaffold an ND_tiledimage_float + ND_invert_float
// pair inside the shader's NodeGraph and wire the NG output for the target
// roughness input to the invert node. See the block comment above the
// MAXScript helper for the graph shape.
//
// Returns the number of (tiledimage, invert) pairs authored. Zero when the
// material has no V-Ray glossiness maps or when every target roughness input
// was already wired by MAX-MTLX-001's direct slotMap (polarity-correct
// roughness spelling wins).
size_t _WireVRayGlossinessAsInvertedRoughness(
    const MaterialX::DocumentPtr& mtlxDoc,
    const MaterialX::NodePtr&     shaderNode,
    AnimHandle                    animHandle)
{
    if (!mtlxDoc || !shaderNode) {
        return 0;
    }

    FPValue rvalue;
    rvalue.Init();
    std::wstringstream ss;
    ss << discoverMaxVRayGlossinessMapsFn << animHandle << L'\0';
    ExecuteMAXScriptScript(
        ss.str().c_str(), MAXScript::ScriptSource::Dynamic, false, &rvalue);
    auto discovery = MaxUsd::MaxStringToUsdString(rvalue.s);
    if (discovery.empty()) {
        return 0;
    }

    size_t      injected = 0;
    std::string line;
    for (size_t start = 0; start <= discovery.size();) {
        auto nl = discovery.find('\n', start);
        if (nl == std::string::npos) {
            line = discovery.substr(start);
            start = discovery.size() + 1;
        } else {
            line = discovery.substr(start, nl - start);
            start = nl + 1;
        }
        if (line.empty()) {
            continue;
        }
        std::string mtlxInput, filePath;
        if (!_ParseGlossinessLine(line, mtlxInput, filePath)) {
            continue;
        }

        // Respect MAX-MTLX-001's direct wiring: if a polarity-correct
        // roughness map (`roughness_map` for specular_roughness or
        // MTLX-011's `trans_roughness_map` for transmission_extra_roughness)
        // was already discovered on this material, MTLX-001 already wired
        // the correct tiledimage into the shader input. Do NOT overwrite
        // it with an inverted glossiness path. Surgical-scope invariant.
        if (!_IsShaderInputDangling(mtlxDoc, shaderNode, mtlxInput)) {
            continue;
        }

        // Locate or create the NodeGraph feeding this shader input.
        auto                    shaderInput = shaderNode->getInput(mtlxInput);
        MaterialX::NodeGraphPtr ng;
        std::string             outputName;
        if (shaderInput) {
            ng = _GetInputNodeGraph(mtlxDoc, shaderNode, mtlxInput);
            outputName = shaderInput->getOutputString();
        }
        if (outputName.empty()) {
            outputName = mtlxInput + "_output";
        }
        if (!ng) {
            for (auto candidate : mtlxDoc->getNodeGraphs()) {
                if (candidate->getOutput(outputName)) {
                    ng = candidate;
                    break;
                }
            }
        }
        if (!ng) {
            auto ngName = "NG_" + shaderNode->getName();
            ng = mtlxDoc->getNodeGraph(ngName);
            if (!ng) {
                ng = mtlxDoc->addNodeGraph(ngName);
            }
        }
        if (!ng) {
            continue;
        }

        // Author the ND_tiledimage_float carrying the glossiness texture.
        // Name is namespaced with `_gloss` so a subsequent MTLX-001 pass on
        // the same NG (unlikely — MTLX-001 already ran) cannot silently
        // collide with the standard `img_<slotname>` naming.
        auto imgName = "img_" + mtlxInput + "_gloss";
        auto imgNode = ng->getNode(imgName);
        if (!imgNode) {
            imgNode = ng->addNode("tiledimage", imgName, "float");
        }
        if (!imgNode) {
            continue;
        }
        imgNode->setNodeDefString("ND_tiledimage_float");
        auto fileInput = imgNode->getInput("file");
        if (!fileInput) {
            fileInput = imgNode->addInput("file", "filename");
        }
        if (fileInput) {
            fileInput->setValueString(filePath);
            // No `colorspace` attribute — glossiness is a raw scalar, not a
            // color. The type gate in _EnrichMtlxDocFromMaxMaterial does
            // the same for other float slots.
        }

        // Author the ND_invert_float that converts glossiness (0=rough,
        // 1=smooth) to roughness (0=smooth, 1=rough).
        //   out = amount - in
        //   amount default = 1.0 (verified against MaterialX stdlib)
        // So `in = tiledimage_float` gives `out = 1.0 - glossiness`, the
        // canonical V-Ray → MaterialX convention flip.
        auto invName = "invert_" + mtlxInput + "_gloss";
        auto invNode = ng->getNode(invName);
        if (!invNode) {
            invNode = ng->addNode("invert", invName, "float");
        }
        if (!invNode) {
            continue;
        }
        invNode->setNodeDefString("ND_invert_float");
        auto invIn = invNode->getInput("in");
        if (!invIn) {
            invIn = invNode->addInput("in", "float");
        }
        if (invIn) {
            invIn->setNodeName(imgName);
            if (invIn->hasAttribute("value")) {
                invIn->removeAttribute("value");
            }
            if (invIn->hasAttribute("nodegraph")) {
                invIn->removeAttribute("nodegraph");
            }
            // Clear any stale `output` reference — a fresh node has none,
            // but a re-invocation might.
            if (!invIn->getOutputString().empty()) {
                invIn->setOutputString("");
            }
        }

        // Route the NodeGraph output through the invert node (NOT the raw
        // tiledimage). This is the critical wiring: without it, the graph
        // would author a polished-vs-frosted flip in the exported USD.
        auto output = ng->getOutput(outputName);
        if (!output) {
            output = ng->addOutput(outputName, "float");
        }
        if (output) {
            output->setNodeName(invName);
            if (output->hasAttribute("nodegraph")) {
                output->removeAttribute("nodegraph");
            }
        }

        // Ensure the shader input references NG + output. Defensive against
        // exports that emitted a bare constant `value` opinion; matches the
        // MTLX-001 injection contract.
        if (shaderInput) {
            shaderInput->setNodeGraphString(ng->getName());
            shaderInput->setOutputString(outputName);
            if (shaderInput->hasAttribute("value")) {
                shaderInput->removeAttribute("value");
            }
        }

        ++injected;
    }
    return injected;
}

// Adds a node graph input to a USD node graph based on a MaterialX input.
void _AddNodeGraphInput(
    const MaterialX::InputPtr& input,
    UsdShadeNodeGraph&         usdNodeGraph,
    const SdfPath&             parentPath,
    const UsdStagePtr&         stage)
{
    auto typeStr = input->getType();
    auto valType = UsdMtlxGetUsdType(typeStr);
    auto usdInput = usdNodeGraph.CreateInput(TfToken(input->getName()), valType.valueTypeName);
    if (usdInput.IsDefined()) {
        _AddInput(input, usdInput, parentPath, stage);
        _SetInputUIAttributes(input, usdInput);
    }
}

// Sets UI attributes for a prim based on a MaterialX node.
void _SetShaderUIAttribute(const MaterialX::InterfaceElementPtr& node, UsdPrim& prim)
{
    if (!prim.HasAPI<UsdUINodeGraphNodeAPI>()) {
        UsdUINodeGraphNodeAPI::Apply(prim);
    }

    if (auto nodeGraphApi = UsdUINodeGraphNodeAPI(prim)) {
        if (node->hasAttribute("ypos") && node->hasAttribute("xpos")) {
            nodeGraphApi.CreatePosAttr(VtValue(GfVec2f(
                std::stof(node->getAttribute("xpos")), std::stof(node->getAttribute("ypos")))));
        }
    }
}

// Retrieves the output of a USD prim.
UsdShadeOutput _GetPrimOutput(UsdPrim& prim, const TfToken& outputName)
{
    auto           shader = UsdShadeShader(prim);
    UsdShadeOutput output;
    if (auto shader = UsdShadeShader(prim)) {
        output = shader.GetOutput(outputName);
    } else if (auto nodeGraph = UsdShadeNodeGraph(prim)) {
        output = nodeGraph.GetOutput(outputName);
    }
    return output;
}

// Adds a Shader prim to the USD stage based on a MaterialX node.
void _AddNode(const MaterialX::NodePtr& node, const UsdStagePtr& stage, const SdfPath& parentPath)
{
    // Don't do anything for NodeGraphs, they are handled separately.
    if (node->getCategory() == "nodegraph") {
        return;
    }

    auto primPath = parentPath.AppendPath(SdfPath(node->getName()));
    auto shader = UsdShadeShader::Define(stage, primPath);

    if (!TF_VERIFY(shader, "Could not define UsdShadeShader at path '%s'\n", primPath.GetText())) {
        return;
    }

    auto shaderPrim = shader.GetPrim();
    _SetShaderUIAttribute(node, shaderPrim);
    _SetShaderInfoAttributes(node, shader);

    if (auto nodeDef = _GetNodeDef(node)) {
        for (auto output : nodeDef->getOutputs()) {
            auto outName
                = output->getName().empty() ? UsdMtlxTokens->DefaultOutputName : output->getName();
            shader.CreateOutput(
                TfToken(outName), UsdMtlxGetUsdType(output->getType()).valueTypeName);
        }
    }

    for (auto input : node->getInputs()) {
        _AddShaderInput(input, shader, parentPath, stage);
    }
}

// Adds a node and all its dependent nodes to the USD stage.
void _AddDependentNodes(
    const MaterialX::InterfaceElementPtr&     node,
    std::set<MaterialX::InterfaceElementPtr>& collectedNodes,
    const UsdStagePtr&                        stage,
    const SdfPath&                            parentPath)
{
    if (!node || collectedNodes.find(node) != collectedNodes.end()) {
        return;
    }
    collectedNodes.insert(node);

    SdfPath           targetPath = parentPath;
    bool              isNodeGraph = node->getCategory() == "nodegraph";
    UsdShadeNodeGraph usdNodeGraph;
    if (isNodeGraph) {
        // Define the NodeGraph
        targetPath = parentPath.AppendPath(SdfPath(node->getName()));
        auto nodeGraphPrim = stage->DefinePrim(targetPath, TfToken("NodeGraph"));
        if (!TF_VERIFY(
                nodeGraphPrim, "Could not define NodeGraph at path '%s'\n", targetPath.GetText())) {
            return;
        }
        usdNodeGraph = UsdShadeNodeGraph(nodeGraphPrim);
        _SetShaderUIAttribute(node, nodeGraphPrim);

        auto nodeGraph = node->asA<MaterialX::NodeGraph>();
        for (auto graphNode : nodeGraph->getNodes()) {
            _AddDependentNodes(graphNode, collectedNodes, stage, targetPath);
            _AddNode(graphNode, stage, targetPath);
        }
        for (auto output : nodeGraph->getOutputs()) {
            auto usdOutput = usdNodeGraph.CreateOutput(
                TfToken(output->getName()), UsdMtlxGetUsdType(output->getType()).valueTypeName);
            if (auto targetOutput = output->getConnectedOutput()) {
                auto targetPrim = stage->GetPrimAtPath(
                    targetPath.AppendPath(SdfPath(targetOutput->getParent()->getName())));
                usdOutput.ConnectToSource(
                    _GetPrimOutput(targetPrim, TfToken(targetOutput->getName())));
            } else if (auto targetNode = output->getConnectedNode()) {
                if (targetNode->getParent() != node) {
                    TF_WARN(
                        "NodeGraph output '%s' is connected to a node outside the NodeGraph",
                        output->getName().c_str());
                    continue;
                }
                auto targetOutputName = _GetOutputName(output, targetNode);
                auto targetPrim
                    = stage->GetPrimAtPath(targetPath.AppendPath(SdfPath(targetNode->getName())));
                auto primOut = _GetPrimOutput(targetPrim, TfToken(targetOutputName));
                if (!TF_VERIFY(
                        primOut,
                        "Could not find output '%s' for Prim at path '%s\n'",
                        targetOutputName.c_str(),
                        targetPrim.GetPrimPath().GetText())) {
                    continue;
                }
                usdOutput.ConnectToSource(primOut);
            }
        }
    }

    for (auto input : node->getInputs()) {
        // if it's connected to a NodeGraph, collect all the nodes in the NodeGraph
        if (!input->getNodeGraphString().empty()) {
            _AddDependentNodes(
                node->getDocument()->getNodeGraph(input->getNodeGraphString()),
                collectedNodes,
                stage,
                parentPath);
        }
        // If it's connected to an "independent" node, add that node and its dependencies
        else if (MaterialX::NodePtr connectedNode = input->getConnectedNode()) {
            if (collectedNodes.find(connectedNode) == collectedNodes.end()) {
                _AddDependentNodes(connectedNode, collectedNodes, stage, targetPath);
                _AddNode(connectedNode, stage, parentPath);
            }
        }
        if (isNodeGraph) {
            _AddNodeGraphInput(input, usdNodeGraph, parentPath, stage);
        }
    }
}

class MtlxShaderWriter : public MaxUsdShaderWriter
{
public:
    MtlxShaderWriter(Mtl* material, const SdfPath& usdPath, MaxUsdWriteJobContext& jobCtx);

    static ContextSupport CanExport(const MaxUsd::USDSceneBuilderOptions&);
    void                  Write() override;
};

// OPENPBR_CLASS_ID, can't use the macro here, because it's only 2025.3+
PXR_MAXUSD_REGISTER_SHADER_WRITER(Class_ID(0xf1551e33, 0x37fb1337), MtlxShaderWriter);
PXR_MAXUSD_REGISTER_SHADER_WRITER(PHYSICALMATERIAL_CLASS_ID, MtlxShaderWriter);

MaxUsdShaderWriter::ContextSupport
MtlxShaderWriter::CanExport(const MaxUsd::USDSceneBuilderOptions& exportArgs)
{
    if (!exportArgs.GetTranslateMaterials()) {
        return ContextSupport::Unsupported;
    }

    return exportArgs.GetConvertMaterialsTo() == TfToken("MaterialX") ? ContextSupport::Fallback
                                                                      : ContextSupport::Unsupported;
}

MtlxShaderWriter::MtlxShaderWriter(
    Mtl*                   material,
    const SdfPath&         usdPath,
    MaxUsdWriteJobContext& jobCtx)
    : MaxUsdShaderWriter(material, usdPath, jobCtx)
{
}

void MtlxShaderWriter::Write()
{
    UsdShadeShader shaderSchema = UsdShadeShader::Define(GetUsdStage(), GetUsdPath());
    if (!TF_VERIFY(
            shaderSchema,
            "Could not define UsdShadeShader at path '%s'\n",
            GetUsdPath().GetText())) {
        return;
    }

    usdPrim = shaderSchema.GetPrim();
    if (!TF_VERIFY(
            usdPrim,
            "Could not get UsdPrim for UsdShadeShader at path '%s'\n",
            shaderSchema.GetPath().GetText())) {
        return;
    }
    auto parentPath = GetUsdPrim().GetParent().GetPath();

    // Export the materialX document via Maxscript
    auto     animHandle = Animatable::GetHandleByAnim(GetMaterial());
    auto     tarLayer = GetUsdStage()->GetEditTarget().GetLayer();
    fs::path p(tarLayer->GetIdentifier());
    if (!_IsWellFormedPath(p)) {
        p = fs::path("");
    } else if (p.has_filename() && !tarLayer->IsAnonymous()) {
        p = p.parent_path();
    } else {
        p = writeJobCtx.GetFilename();
        p = p.remove_filename();
    }

    static const TSTR exportMtlToMtlx = LR"(
		fn exportMtlToMtlx materialAnimHandle layerPath = (

			local m = getAnimByHandle materialAnimHandle
			local mArr = #(m)
			local mtlxStr = MtlxIOUtil.ExportMtlxString layerPath mArr
			return mtlxStr
		)
		exportMtlToMtlx )";

    FPValue rvalue;
    rvalue.Init();
    std::wstringstream ss;
    ss << exportMtlToMtlx << animHandle << L" " << "@"
       << "\"" << MaxUsd::UsdStringToMaxString(p.string()) << "\"" << L'\0';
    ExecuteMAXScriptScript(ss.str().c_str(), MAXScript::ScriptSource::Dynamic, false, &rvalue);
    auto mtlxString = MaxUsd::MaxStringToUsdString(rvalue.s);
    auto mtlxDoc = MaterialX::createDocument();
    try {
        readFromXmlString(mtlxDoc, mtlxString);
    } catch (MaterialX::ExceptionParseError& e) {
        TF_WARN("Error reading MaterialX document: %s", e.what());
        return;
    }

    // Sanitize the material name using the same logic as with the MaterialX component
    // to match the node name produced by the MaterialX exporter. createValidName
    // does not guard against leading digits.
    auto mtlxMatName
        = MaterialX::createValidName(MaxUsd::MaxStringToUsdString(GetMaterial()->GetName()));
    if (!mtlxMatName.empty()
        && std::isdigit(static_cast<unsigned char>(mtlxMatName[0]))) {
        mtlxMatName = "_" + mtlxMatName;
    }
    // surfaceMaterialNode
    auto MaterialNode = mtlxDoc->getNode(mtlxMatName);
    if (MaterialNode == nullptr) {
        TF_WARN(
            "Material Node '%s' not found in the MaterialX Document",
            mtlxMatName.c_str());
        return;
    }
    // Collection of the MaterialX nodes already processed, to avoid processing them again.
    std::set<MaterialX::InterfaceElementPtr> collectedNodes;

    // Handle the displacement shader output connection.
    if (auto displacementNode = MaterialNode->getConnectedNode("displacementshader")) {
        UsdShadeOutput _mtlDisplacementOutput;
        if (writeJobCtx.GetArgs().GetAllMaterialConversions().size() > 1) {
            _mtlDisplacementOutput
                = UsdShadeMaterial(GetUsdPrim().GetParent()).CreateDisplacementOutput();
            auto output = UsdShadeMaterial(GetUsdPrim().GetParent().GetParent())
                              .CreateDisplacementOutput(TfToken("mtlx"));
            output.ConnectToSource(_mtlDisplacementOutput);
        } else {
            _mtlDisplacementOutput = UsdShadeMaterial(GetUsdPrim().GetParent())
                                         .CreateDisplacementOutput(TfToken("mtlx"));
        }
        UsdShadeShader displacementShader;
        displacementShader = UsdShadeShader::Define(
            GetUsdStage(), parentPath.AppendPath(SdfPath(displacementNode->getName())));
        auto _ShaderDisplacementOutput = displacementShader.CreateOutput(
            UsdMtlxTokens->DefaultOutputName, _mtlDisplacementOutput.GetTypeName());
        _mtlDisplacementOutput.ConnectToSource(_ShaderDisplacementOutput);
        auto& dispNodeName = displacementNode->getName();
        _SetShaderInfoAttributes(displacementNode, displacementShader);
        _AddDependentNodes(displacementNode, collectedNodes, GetUsdStage(), parentPath);
        for (auto input : displacementNode->getInputs()) {
            _AddShaderInput(input, displacementShader, parentPath, GetUsdStage());
        }
    }

    auto shaderNode = MaterialNode->getConnectedNode("surfaceshader");
    if (shaderNode == nullptr) {
        TF_WARN(
            "Surface Shader Node not found in the MaterialX Document, for Shader at path '%s'",
            usdPrim.GetPrimPath().GetText());
        return;
    }
    // MAX-MTLX-001: enrich the in-memory MaterialX doc with the Bitmap map
    // slots that `MtlxIOUtil.ExportMtlxString` dropped, so the downstream
    // walker emits ND_tiledimage USD Shader prims wired into each dangling
    // NodeGraph output instead of leaving the NodeGraph with declared but
    // unconnected outputs. No-op when the doc is already fully populated or
    // when the material has no Bitmap-backed slots.
    _EnrichMtlxDocFromMaxMaterial(mtlxDoc, shaderNode, animHandle);

    // MAX-MTLX-002: after MAX-MTLX-001 has restored every wire-able slot,
    // some shader inputs still reference NodeGraph outputs with no source
    // (materials whose input is a solid color rather than a Bitmap). Prune
    // those connections and set the constant value read from the Max
    // material, so downstream Karma / Hydra evaluates the artist's chosen
    // color instead of a dangling-connection zero.
    _PruneDanglingNodeGraphOutputs(mtlxDoc, shaderNode, animHandle);

    // MAX-MTLX-003: MAX-MTLX-001 wires `ND_tiledimage_vector3` →
    // `ND_normalmap_float.in` only when the shader-side `normal` input is
    // itself dangling. When the NG's `normal_output` connects to a
    // scaffolded normalmap node but that normalmap's OWN `in` was dropped
    // by `MtlxIOUtil.ExportMtlxString`, MAX-MTLX-001 skips it and the
    // material renders with no normal mapping. Walk the normalmap nodes
    // in this shader's NodeGraphs and wire their `in` from the Max
    // material's normal-map Bitmap slot.
    _WireDanglingNormalmapInputs(mtlxDoc, shaderNode, animHandle);

    // MAX-MTLX-012: the ND_normalmap_float sub-shader's `scale` (bump-strength
    // multiplier) is dropped alongside `in` by MtlxIOUtil.ExportMtlxString, so
    // every scaffolded normalmap runs at its port default `1.0` even when the
    // artist authored a non-unit bump strength on the Max material
    // (`bump_map_amt` / `bumpMapAmount` on PhysicalMaterial / OpenPBR,
    // `bump_multiplier` on VRayMtl). Walk the shader's normalmap nodes and
    // author `scale` from the Max material's bump-strength property. Base-
    // first wrapper walk (`unwrapBlendMaterialSubMtls`) so baseMtl's strength
    // wins over any coat's, matching the layered-material precedence
    // MAX-MTLX-007 established for the texture-map discovery path. No-op
    // when no bump-strength property is discoverable or when the discovered
    // value equals the port default (1.0) — the exported doc stays clean.
    _ApplyBumpStrengthToNormalmaps(mtlxDoc, shaderNode, animHandle);

    // MAX-MTLX-GLOSSINESS-INVERT-018: V-Ray's glossiness maps
    // (`texmap_reflectionGlossiness` for specular_roughness,
    // `texmap_refractionGlossiness` for transmission_extra_roughness) use the
    // INVERSE convention of ND_standard_surface roughness (V-Ray: 0=rough,
    // 1=smooth; MaterialX: 0=smooth, 1=rough). MAX-MTLX-011 explicitly scoped
    // these V-Ray-only slots out of the standard slotMap because wiring them
    // directly would produce a semantically inverted map — polished chrome
    // rendering as brushed, frosted glass rendering as polished. Post-fix,
    // this pass discovers V-Ray glossiness maps and scaffolds an
    // ND_invert_float node between the tiledimage and the NG output for the
    // roughness input. `_IsShaderInputDangling` gates the fix so materials
    // whose polarity-correct roughness map was already discovered by
    // MAX-MTLX-001 (`roughness_map` / `trans_roughness_map`) keep their
    // direct wiring — this pass is a no-op for those.
    _WireVRayGlossinessAsInvertedRoughness(mtlxDoc, shaderNode, animHandle);

    _SetShaderInfoAttributes(shaderNode, shaderSchema);
    _AddDependentNodes(shaderNode, collectedNodes, GetUsdStage(), parentPath);

    for (auto input : shaderNode->getInputs()) {
        _AddShaderInput(input, shaderSchema, parentPath, GetUsdStage());
    }
}

PXR_NAMESPACE_CLOSE_SCOPE
#endif // IS_MAX2025_OR_GREATER