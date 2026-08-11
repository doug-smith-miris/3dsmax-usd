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

    _SetShaderInfoAttributes(shaderNode, shaderSchema);
    _AddDependentNodes(shaderNode, collectedNodes, GetUsdStage(), parentPath);

    for (auto input : shaderNode->getInputs()) {
        _AddShaderInput(input, shaderSchema, parentPath, GetUsdStage());
    }
}

PXR_NAMESPACE_CLOSE_SCOPE
#endif // IS_MAX2025_OR_GREATER