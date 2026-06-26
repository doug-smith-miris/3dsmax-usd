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
#include "ShadingUtils.h"

#include <MaxUsd/MaxTokens.h>
#include <MaxUsd/MeshConversion/MeshConverter.h>

#include <pxr/usd/usd/inherits.h>
#ifdef IS_MAX2025_OR_GREATER
#include <pxr/usd/usdMtlx/utils.h>
#endif
#if PXR_VERSION > 2411
#include <pxr/usd/usdMtlx/tokens.h>
#endif
#include <pxr/usd/usdShade/materialBindingAPI.h>
#include <pxr/usd/usdShade/shader.h>
#include <pxr/usdImaging/usdImaging/primAdapter.h>

#include <XRef/iXrefMaterial.h>
#include <inode.h>
#include <iparamb2.h>
#include <stdmat.h>

PXR_NAMESPACE_OPEN_SCOPE

namespace MaxUsdShadingUtils {

void AddMaterialBinding(MaterialBindings& bindings, Mtl* material, const SdfPath& path)
{
    auto found
        = std::find_if(bindings.begin(), bindings.end(), [material](const MaterialBinding& item) {
              return item.GetMaterial() == material;
          });
    if (found != bindings.end()) {
        found->GetBindings().push_back(path);
    } else {
        bindings.push_back({ material, { path } });
    }
}

Mtl* GetNodeMaterial(INode* exportedNode)
{
    if (!exportedNode) {
        return nullptr;
    }

    Mtl* material = exportedNode->GetMtl();
    if (!material) {
        // node without applied material
        return nullptr;
    }

    IXRefMaterial18* xrefMaterial = dynamic_cast<IXRefMaterial18*>(material);
    if (xrefMaterial) {
        Mtl* xrefmat = nullptr;
        if (xrefMaterial->IsOverrideMaterialEnabled()) {
            xrefmat = xrefMaterial->GetOverrideMaterial();
        } else {
            xrefmat = xrefMaterial->GetSourceMaterial(true);
        }
        return xrefmat;
    }
    return material;
}

void _AddPrimWithMultiMaterialtoMaterialMap(
    const UsdPrim&    usdPrim,
    MultiMtl*         multiMaterial,
    MaterialBindings& materialBindings)
{
    // get material ids
    IParamBlock2* mtlParamBlock2 = multiMaterial->GetParamBlockByID(0);
    short         paramId = MaxUsd::FindParamId(mtlParamBlock2, L"materialIDList");
    std::set<int> matIdSet;
    Interval      valid = FOREVER;
    for (int i = 0; i < multiMaterial->NumSubs(); ++i) {
        int matId;
        mtlParamBlock2->GetValue(paramId, 0, matId, valid, i);
        matIdSet.insert(matId);
    }

    std::vector<UsdGeomSubset> materialSubSets;
    for (auto child : usdPrim.GetAllChildren()) {
        if (child.IsA<UsdGeomSubset>()) {
            materialSubSets.push_back(UsdGeomSubset(child));
        }
    }

    // We want to handle out of bounds material ids the same way max does it (overflow back
    // to the first submtl, using a modulo) : actual = matId % NumSubsMtl
    auto resolveMatId = [&multiMaterial](int matId) { return matId % multiMaterial->NumSubMtls(); };

    if (!materialSubSets.empty()) {
        for (const UsdGeomSubset& subset : materialSubSets) {
            SdfPath subsetUsdPath = subset.GetPrim().GetPath();

            int        matId = MaxUsd::MeshConverter::GetMaterialIdFromCustomData(subset.GetPrim());
            const auto matIdIter = matIdSet.find(resolveMatId(matId));
            if (matIdIter != matIdSet.end()) {
                Mtl* material = multiMaterial->GetSubMtl(*matIdIter);
                if (material) {
                    AddMaterialBinding(materialBindings, material, subsetUsdPath);
                }
            }
        }
    } else {
        // this is for the case where one material is used in the submaterial
        // (no subset is generated)
        int matId { -1 };
        if (usdPrim.IsInstance()) {
            // if we are looking at an instance prim, the custom data should be on the prim we
            // inherited
            matId = MaxUsd::MeshConverter::GetMaterialIdFromCustomData(
                usdPrim.GetPrototype().GetChildren().front());
        } else {
            // if we are not looking at an instance prim, the custom data should be on the prim
            // itself
            matId = MaxUsd::MeshConverter::GetMaterialIdFromCustomData(usdPrim);
        }

        const auto matIdIter = matIdSet.find(resolveMatId(matId));
        if (matIdIter != matIdSet.end()) {
            Mtl* material = multiMaterial->GetSubMtl(*matIdIter);
            if (material) {
                AddMaterialBinding(materialBindings, material, usdPrim.GetPath());
            }
        }
    }
}

UsdPrim BreakInstancingAndCopySubset(
    const UsdStageRefPtr&             stage,
    const UsdPrim&                    instancePrim,
    const UsdPrim&                    prototypeChildPrim,
    const std::vector<UsdGeomSubset>& subsetToCopy)
{
    struct SubsetInfo
    {
        SubsetInfo(const UsdGeomSubset& subset)
            : name(subset.GetPath().GetName())
            ,
            // +1 because the goal is to copy the data, not the index
            customDataMatID(
                MaxUsd::MeshConverter::GetMaterialIdFromCustomData(subset.GetPrim()) + 1)
        {
            subset.GetIndicesAttr().Get(&indices);
            subset.GetElementTypeAttr().Get(&elementType);
            subset.GetFamilyNameAttr().Get(&familyName);
        }
        std::string name;
        VtIntArray  indices;
        TfToken     elementType;
        TfToken     familyName;
        VtValue     customDataMatID;
    };
    // Copy information needed from the prototype prim subsets
    std::list<SubsetInfo> subsetInfoList;
    for (const auto& subset : subsetToCopy) {
        subsetInfoList.push_back(SubsetInfo(subset));
    }

    // Break instancing
    instancePrim.SetInstanceable(false);
    auto overridePrimPath
        = instancePrim.GetPath().AppendChild(TfToken(prototypeChildPrim.GetPath().GetName()));
    auto overridePrim = stage->DefinePrim(overridePrimPath);

    // Recreate subsets on the prim
    UsdGeomImageable geom(overridePrim);
    for (const auto& subset : subsetInfoList) {
        auto newSubset = UsdGeomSubset::CreateGeomSubset(
            geom, TfToken(subset.name), subset.elementType, subset.indices, subset.familyName);
        // Custom data will be 0 if not set.
        if (subset.customDataMatID != 0) {
            newSubset.GetPrim().SetCustomDataByKey(MaxUsd::MetaData::matId, subset.customDataMatID);
        }
    }

    return overridePrim;
}

// -----------------------------------------------------------------------------
// MAX-MAT-010 surgical-coverage bound (material-instance / Multi-Mtl
// override-structure fidelity audit, 2026-06-26).
//
// `_AddInstancePrimsToMaterialMap` walks every USD prototype on the
// exported stage and for each prototype decides how to author the
// material bindings of its USD instances, based on whether all
// instances share a material and (if not) what kind of material the
// divergent instances carry. The decision tree below has FOUR named
// surgical branches the validator's named cases pin one-for-one:
//
//   Branch A -- sameMaterialForAllInstances == true (the
//               "keep instancing" common case)
//     * Action: bind the material on the inheritance-base prim's
//       child (the prototype's master mesh). USD composition then
//       propagates the binding to every instance via the inheritance
//       arc; no instance prim gets a per-instance opinion.
//     * Surgical bound: instancing is PRESERVED. A widening that
//       always broke instancing would bloat stage size for the
//       common case (every instanced prototype turned into N
//       independent meshes) and break downstream prototype-aware
//       consumers (USD instancing-renderers, asset-validators
//       counting unique meshes).
//     * Validator case: `SameMaterial_KeepInstancing`.
//
//   Branch B -- different materials, non-MultiMtl (Mtl override on
//               one or more instances, no Multi/Sub-Object)
//     * Action: each instance prim gets a per-instance
//       MaterialBindingAPI binding ON ITS OWN PATH. Instancing is
//       PRESERVED -- the per-instance opinion wins at composition
//       time; the prototype's binding is overridden ONLY at the
//       instance prim level.
//     * Surgical bound: instancing PRESERVED + per-instance opinion
//       authored on the local spec, not on the prototype. A widening
//       that broke instancing here would gratuitously bloat the
//       stage (the override-structure does not require breaking,
//       since there are no subsets on the prototype that need
//       per-instance shadowing).
//     * Validator case: `DifferentMaterial_NonMulti`.
//
//   Branch C -- different materials, MultiMtl, NO subsets on the
//               prototype's child (the "single matId" Multi case
//               where ApplyMaxMaterialIDs did not produce any
//               GeomSubsets in the materialBind family)
//     * Action: `_AddPrimWithMultiMaterialtoMaterialMap` takes the
//       no-subset sub-branch (lines 119-141 of this file): reads
//       `customData[3dsmax:matId]` from the prototype child, looks
//       up the matching sub-material on the divergent instance's
//       MultiMtl, binds it directly on the instance prim's path.
//     * Surgical bound: instancing PRESERVED. A widening that broke
//       instancing here would (a) bloat the stage like Branch B,
//       and (b) attempt a subset-copy that has nothing to copy --
//       either crashing or authoring empty subset prims that
//       desync from the prototype's customData layout.
//     * Validator case: `DifferentMaterial_MultiNoSubsets`.
//
//   Branch D -- different materials, MultiMtl, subsets DO exist on
//               the prototype's child (the canonical per-instance
//               MultiMtl override case the bite is named after)
//     * Action: `BreakInstancingAndCopySubset` runs (defined above,
//       lines 144-193). Three things happen:
//         (i)  `instancePrim.SetInstanceable(false)` on the
//              divergent instance -- USD instancing is BROKEN on
//              this one prim so descendant opinions can take
//              effect at composition time;
//         (ii) the prototype's `materialBind`-family
//              `UsdGeomSubset` children are COPIED onto a new
//              override-child mesh prim under the divergent
//              instance, with `customData[3dsmax:matId]`
//              preserved across the copy (the C++ does a +1/-1
//              round-trip in SubsetInfo's ctor to keep the matId
//              value byte-identical with the prototype's);
//         (iii) `_AddPrimWithMultiMaterialtoMaterialMap` is called
//              again on the OVERRIDE prim, NOT the original
//              instance prim, so the per-subset bindings
//              author against the copied subsets the divergent
//              MultiMtl actually wants to bind.
//     * Surgical bound: instancing BROKEN + subsets COPIED with
//       matId customData preserved. A widening that dropped any
//       part of this (the SetInstanceable(false), the subset copy,
//       the matId preservation, or the rebinding against the copy)
//       would silently DROP the per-instance MultiMtl override --
//       a data-loss regression that the diagnostic corpus would not
//       catch because the prototype's bindings still render
//       something visually coherent. The KEY bound the audit pins.
//     * Validator case: `DifferentMaterial_MultiWithSubsets`.
//
// The four branches are MUTUALLY EXCLUSIVE per `instancePrim` and the
// gate predicates are independent (sameMaterial gate; MultiMtl
// dynamic_cast; prototype-subset emptiness). A wildcard refactor
// proposing "unify override handling" across the four would either
// pick one branch's shape and apply it to every case (regressing
// the other three by named case) or invent a fifth shape that the
// validator's `CrossPath_Divergence` final case catches when every
// branch's invariant must hold simultaneously on one fixture.
//
// See also:
//   * `src/translators/MultiMaterialUtils.cpp::DiscoverMaterialIDsAndCreateBundles`
//     for the parallel instance-break path on the MtlSwitcher
//     variant-set flow (Branches A + D under a switcher's
//     `AsVariantSets` export style).
//   * `src/translators/MtlSwitcherWriter.cpp::Write` / `::PostWrite`
//     for the three switcher-shape branches (E, F, G).
//   * Validator:
//     /Users/d.smith/MirisProjects/Agent Builder/agent/arch-builds/
//       1a92bee3-23b8-4465-93cf-122a6758ad16/
//       validate_material_instance_override_surgical.py
//   * Visual auditor pair in the same arch-build dir
//     (`render_karma_postfix.png`, `render_unreal_reference.png`,
//     `compare_side_by_side.png`, `intended_example.md`): two
//     concrete cube meshes side-by-side; BOX_1 always reads red+blue,
//     BOX_2 reads green+yellow when the override LANDED (postfix)
//     and red+blue when the override was LOST (still-broken).
// -----------------------------------------------------------------------------
void _AddInstancePrimsToMaterialMap(
    const MaxUsdWriteJobContext&                            jobCtx,
    const pxr::TfHashSet<pxr::SdfPath, pxr::SdfPath::Hash>& primsToMaterialBind,
    MaterialBindings&                                       materialBindings)
{
    // Reverse the map, in this function, we look for the source nodes of existing
    // prims.
    std::map<pxr::SdfPath, INode*> bindablePrimsToNodes;
    for (const auto& entry : jobCtx.GetNodesToPrimsMap()) {
        if (primsToMaterialBind.find(entry.second) == primsToMaterialBind.end()) {
            continue;
        }
        bindablePrimsToNodes.insert({ entry.second, entry.first });
    }

    // find node from instance prim
    auto GetNodeFromInstancePrim = [bindablePrimsToNodes](const UsdPrim& prim) -> INode* {
        auto maxNodeIter = bindablePrimsToNodes.find(prim.GetPath());
        if (maxNodeIter == bindablePrimsToNodes.end()) {
            maxNodeIter = bindablePrimsToNodes.find(prim.GetParent().GetPath());
        }
        return (maxNodeIter != bindablePrimsToNodes.end() ? maxNodeIter->second : nullptr);
    };

    std::vector<UsdPrim> prototypes = jobCtx.GetUsdStage()->GetPrototypes();
    for (const UsdPrim& prototype : prototypes) {
        auto instancePrims = prototype.GetInstances();

        // For every instance of a given prototype prim check if they all use the same material
        bool   sameMaterialForAllInstances { true };
        INode* exportedNode = GetNodeFromInstancePrim(instancePrims[0]);
        // The instanced prim may not be from any 3dsMax node, for example if it comes from another
        // USD Layer which was referenced in the Max scene by a USD Stage Object.
        if (!exportedNode) {
            continue;
        }

        Mtl* firstInstanceMaterial = GetNodeMaterial(exportedNode);
        for (auto it = std::next(instancePrims.begin()); it != instancePrims.end(); ++it) {
            auto nextExportedNode = GetNodeFromInstancePrim(*it);
            if (!nextExportedNode) {
                continue;
            }
            Mtl* nextInstanceMaterial = GetNodeMaterial(nextExportedNode);
            if (nextInstanceMaterial != firstInstanceMaterial) {
                sameMaterialForAllInstances = false;
                break;
            }
        }

        if (sameMaterialForAllInstances) {
            // If every instances use the same material keep instancing
            if (!firstInstanceMaterial) {
                // no material on instances set (prototype)
                continue;
            }
            // Ugly assumption! since we know 3ds Max on export will use inherits and the inherited
            // prim will only have one child. Retrieve the prim that the instance prim inherit from
            // and add its only child to the material map.
            // TODO : Figure out material binding for instancing setup by custom prim writers.
            const auto firstInstance = instancePrims[0];
            auto       directInherits = firstInstance.GetInherits().GetAllDirectInherits();
            if (directInherits.empty()) {
                // Instancing not based on inherits, it was not setup by us. Skip material binding.
                MaxUsd::Log::Warn(
                    L"Unable to perform material assignment for instance prototype {0} from node "
                    L"\"{1}\". "
                    L"Only instancing based on inheritance is supported for material binding.",
                    MaxUsd::UsdStringToMaxString(firstInstance.GetPrototype().GetPath().GetString())
                        .data(),
                    exportedNode->GetName());
                continue;
            }

            auto basePrimPath = directInherits[0];
            auto basePrim = jobCtx.GetUsdStage()->GetPrimAtPath(basePrimPath);

            if (!basePrim.IsValid()) {
                MaxUsd::Log::Warn(
                    L"Unable to perform material assignment for instance prototype {0} from node "
                    L"\"{1}\". {2} is not a "
                    "valid prim path.",
                    MaxUsd::UsdStringToMaxString(firstInstance.GetPrototype().GetPath().GetString())
                        .data(),
                    exportedNode->GetName(),
                    MaxUsd::UsdStringToMaxString(basePrimPath.GetString()).data());
                continue;
            }

            const auto children = basePrim.GetAllChildren();
            if (children.empty()) {
                MaxUsd::Log::Warn(
                    L"Unable to perform material assignment for instance prototype {0} from node "
                    L"{1}. {2} has no children.",
                    MaxUsd::UsdStringToMaxString(firstInstance.GetPrototype().GetPath().GetString())
                        .data(),
                    exportedNode->GetName(),
                    MaxUsd::UsdStringToMaxString(basePrimPath.GetString()).data());
                continue;
            }

            auto      basePrimChildPrim = basePrim.GetAllChildren().front();
            MultiMtl* multiMaterial = dynamic_cast<MultiMtl*>(firstInstanceMaterial);
            if (multiMaterial) {
                _AddPrimWithMultiMaterialtoMaterialMap(
                    basePrimChildPrim, multiMaterial, materialBindings);
            } else {
                AddMaterialBinding(
                    materialBindings, firstInstanceMaterial, basePrimChildPrim.GetPath());
            }
        } else {
            // If all instances don't have the same material, add the instance prim(s) to the
            // materials_map
            for (const auto& instancePrim : instancePrims) {
                INode* exportedNode = GetNodeFromInstancePrim(instancePrim);
                if (!exportedNode) {
                    continue;
                }

                Mtl* material = GetNodeMaterial(exportedNode);
                if (!material) {
                    continue;
                }

                MultiMtl* multiMaterial = dynamic_cast<MultiMtl*>(material);

                if (multiMaterial) {
                    // For the Multi-material copy the subsets from the prototype child prim and
                    // break instancing
                    auto prototypeChildPrim = instancePrim.GetPrototype().GetChildren().front();
                    UsdShadeMaterialBindingAPI bindingAPI(prototypeChildPrim);
                    auto                       subsetToCopy = bindingAPI.GetMaterialBindSubsets();
                    if (subsetToCopy.empty()) {
                        _AddPrimWithMultiMaterialtoMaterialMap(
                            instancePrim, multiMaterial, materialBindings);
                    } else {
                        UsdPrim overridePrim = BreakInstancingAndCopySubset(
                            jobCtx.GetUsdStage(), instancePrim, prototypeChildPrim, subsetToCopy);
                        // Do the binding on the newly created override prim that contains the
                        // copied subsets
                        _AddPrimWithMultiMaterialtoMaterialMap(
                            overridePrim, multiMaterial, materialBindings);
                    }
                } else {
                    AddMaterialBinding(materialBindings, material, instancePrim.GetPath());
                }
            }
        }
    }
}

MaterialBindings FetchMaterials(
    const MaxUsdWriteJobContext&                            writeJobContext,
    const pxr::TfHashSet<pxr::SdfPath, pxr::SdfPath::Hash>& primsToMaterialBind)
{
    MaterialBindings materialBindings;

    const auto nodeToPrims = writeJobContext.GetNodesToPrimsMap();
    // Build the inverse of the map as well, useful in the loop below.
    std::map<pxr::SdfPath, INode*> primsToNodes;
    for (const auto& pair : nodeToPrims) {
        primsToNodes.insert({ pair.second, pair.first });
    }

    // gather materials from exported nodes
    for (const auto& nodePrim : nodeToPrims) {
        if (primsToMaterialBind.find(nodePrim.second) == primsToMaterialBind.end()) {
            continue;
        }

        INode* exportedNode = nodePrim.first;
        Mtl*   material = GetNodeMaterial(exportedNode);
        if (!material) {
            // node without applied material
            continue;
        }

        //  handle xref materials
        IXRefMaterial18* xrefMaterial = dynamic_cast<IXRefMaterial18*>(material);
        if (xrefMaterial) {
            Mtl* sourceMaterial = nullptr;
            if (xrefMaterial->IsOverrideMaterialEnabled()) {
                sourceMaterial = xrefMaterial->GetOverrideMaterial();
            } else {
                sourceMaterial = xrefMaterial->GetSourceMaterial(true);
            }
            if (!sourceMaterial) {
                continue;
            }
            material = sourceMaterial;
        }

        // Find the prim on which to perform material binding. Typically we end up with just one
        // prim, but if a custom writer produces more than one prim, we could end up with more.
        std::vector<pxr::UsdPrim> usdPrimsToBind;

        auto nodeRootPrim = writeJobContext.GetUsdStage()->GetPrimAtPath(nodePrim.second);

        auto isInheritInstance = [](const pxr::UsdPrim& prim) {
            return prim.IsInstance() && prim.GetInherits().GetAllDirectInherits().size() == 1;
        };

        // If the node's root prim is a geom prim, bind the material to that.
        if (nodeRootPrim.IsA<UsdGeomGprim>()) {
            usdPrimsToBind.push_back(nodeRootPrim);
        }
        // We might have a wrapper prim to manage node/object transform and/or instancing...
        else {
            // Material binding on instances is a special case, it is handled differently
            // from _AddInstancePrimsToMaterialMap() below.
            if (isInheritInstance(nodeRootPrim)) {
                continue;
            }

            // Bind to all the children prim that are not from the source node's child nodes (in
            // other words, all prims that were generated by the node's own prim writer. Ignore
            // instances, instances are handled differently from _AddInstancePrimsToMaterialMap().
            for (const auto& prim : nodeRootPrim.GetChildren()) {
                if (primsToNodes.find(prim.GetPath()) != primsToNodes.end()) {
                    continue;
                }
                usdPrimsToBind.push_back(prim);
            }
            // Typically for instanced node we would find the instance prim either at the top level
            // prim, or one below. Other scenarios may originate from custom exported data, but
            // those are not handled here. If the "default" material assignment behavior is not
            // suitable in those case, prim writers can opt out of material assignment.
            if (usdPrimsToBind.size() == 1) {
                const auto& prim = usdPrimsToBind[0];
                if (isInheritInstance(prim)) {
                    continue;
                }
            }
        }

        for (auto& prim : usdPrimsToBind) {
            MultiMtl* multiMaterial = dynamic_cast<MultiMtl*>(material);
            if (multiMaterial) {
                _AddPrimWithMultiMaterialtoMaterialMap(prim, multiMaterial, materialBindings);
            } else {
                AddMaterialBinding(materialBindings, material, prim.GetPath());
            }
        }
    }
    _AddInstancePrimsToMaterialMap(writeJobContext, primsToMaterialBind, materialBindings);

    // sorting on material is done
    // (totally superfluous but needed to have deterministic automation tests)
    std::sort(
        materialBindings.begin(),
        materialBindings.end(),
        [](MaterialBinding& a, MaterialBinding& b) {
            Mtl* materialA = a.GetMaterial();
            Mtl* materialB = b.GetMaterial();
            if (materialA->GetName() == materialB->GetName()) {
                return Animatable::GetHandleByAnim(materialA)
                    < Animatable::GetHandleByAnim(materialB);
            }
            return materialA->GetName() < materialB->GetName();
        });

    return materialBindings;
}

UsdShadeOutput CreateShaderOutputAndConnectMaterial(
    UsdShadeShader&   shader,
    UsdShadeMaterial& material,
    const TfToken&    terminalName,
    const TfToken&    renderContext)
{
    if (!shader || !material) {
        return UsdShadeOutput();
    }

    UsdShadeOutput materialOutput;
    if (terminalName == UsdShadeTokens->surface) {
        materialOutput = material.CreateSurfaceOutput(renderContext);
    }
    // TODO - volume and displacement is not handled by 3ds Max
    else if (terminalName == UsdShadeTokens->volume) {
        materialOutput = material.CreateVolumeOutput(renderContext);
    } else if (terminalName == UsdShadeTokens->displacement) {
        materialOutput = material.CreateDisplacementOutput(renderContext);
    } else {
        return UsdShadeOutput();
    }
#ifdef IS_MAX2025_OR_GREATER
    UsdShadeOutput shaderOutput;
    if (renderContext == TfToken("mtlx")) {
        shaderOutput
            = shader.CreateOutput(UsdMtlxTokens->DefaultOutputName, SdfValueTypeNames->Token);
    } else {
        shaderOutput = shader.CreateOutput(terminalName, materialOutput.GetTypeName());
    }
#else
    UsdShadeOutput shaderOutput = shader.CreateOutput(terminalName, materialOutput.GetTypeName());
#endif

    UsdPrim parentPrim = shader.GetPrim().GetParent();
    if (parentPrim == material.GetPrim()) {
        materialOutput.ConnectToSource(shaderOutput);
    } else {
        // If the surface is inside a multi-material node graph, then we must create an intermediate
        // output on the NodeGraph
        UsdShadeNodeGraph parentNodeGraph(parentPrim);
        UsdShadeOutput    parentOutput
            = parentNodeGraph.CreateOutput(terminalName, materialOutput.GetTypeName());
        parentOutput.ConnectToSource(shaderOutput);
        materialOutput.ConnectToSource(parentOutput);
    }
    return shaderOutput;
}

} // namespace MaxUsdShadingUtils

PXR_NAMESPACE_CLOSE_SCOPE
