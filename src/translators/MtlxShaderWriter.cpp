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
    fn discoverMaxMtlxTexmaps materialAnimHandle = (
        local m = getAnimByHandle materialAnimHandle
        local result = ""
        if m == undefined then return result
        -- Slot map: (Max PhysicalMaterial/OpenPBR property name,
        --           ND_standard_surface input name,
        --           MaterialX type token used in the NodeGraph)
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
            #("refl_color_map",         "specular_color",     "color3"),
            #("specularColorMap",       "specular_color",     "color3"),
            #("trans_color_map",        "transmission_color", "color3"),
            #("transmissionColorMap",   "transmission_color", "color3"),
            #("cutout_map",             "opacity",            "float"),
            #("cutoutMap",              "opacity",            "float")
        )
        local seenInputs = #()
        for entry in slotMap do (
            local propName  = entry[1]
            local mtlxInput = entry[2]
            local mtlxType  = entry[3]
            -- MAXScript has no `continue`; guard each stage with nested ifs.
            if (isProperty m propName) then (
                local tex = getProperty m propName
                if tex != undefined then (
                    -- Traverse through common wrappers to reach the underlying
                    -- Bitmap. V-Ray's VRayBitmap wraps a `.bitmap`; OSL bitmaps
                    -- expose `.filename`. Fall back to `.filename` on the
                    -- top-level tex.
                    local fname = undefined
                    local cls   = classOf tex
                    if cls == Bitmaptexture then (
                        fname = tex.filename
                    ) else if (isProperty tex #filename) then (
                        fname = getProperty tex #filename
                    ) else if (isProperty tex #bitmap) then (
                        local bmp = getProperty tex #bitmap
                        if bmp != undefined and (isProperty bmp #filename) then (
                            fname = getProperty bmp #filename
                        )
                    )
                    if fname != undefined and fname != "" then (
                        -- Deduplicate on the mtlx input name; first hit wins.
                        if (findItem seenInputs mtlxInput) == 0 then (
                            append seenInputs mtlxInput
                            result += (mtlxInput + "|" + mtlxType + "|" + fname + "\n")
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

        // Create the tiledimage node inside the NodeGraph.
        auto imgName = "img_" + mtlxInput;
        auto imgNode = ng->getNode(imgName);
        if (!imgNode) {
            imgNode = ng->addNode("tiledimage", imgName, mtlxType);
        }
        if (!imgNode) {
            continue;
        }
        auto fileInput = imgNode->getInput("file");
        if (!fileInput) {
            fileInput = imgNode->addInput("file", "filename");
        }
        if (fileInput) {
            fileInput->setValueString(filePath);
            if (mtlxType == "color3") {
                // Match the color space authoring the MaterialX exporter would use.
                fileInput->setAttribute("colorspace", "srgb_texture");
            }
        }

        // Wire the NodeGraph output to the newly-created tiledimage.
        auto output = ng->getOutput(outputName);
        if (!output) {
            output = ng->addOutput(outputName, mtlxType);
        }
        if (output) {
            output->setNodeName(imgName);
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

    _SetShaderInfoAttributes(shaderNode, shaderSchema);
    _AddDependentNodes(shaderNode, collectedNodes, GetUsdStage(), parentPath);

    for (auto input : shaderNode->getInputs()) {
        _AddShaderInput(input, shaderSchema, parentPath, GetUsdStage());
    }
}

PXR_NAMESPACE_CLOSE_SCOPE
#endif // IS_MAX2025_OR_GREATER