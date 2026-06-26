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
#include "MultiMaterialUtils.h"

#include <MaxUsd/MeshConversion/MeshConverter.h>
#include <MaxUsd/Translators/ShadingUtils.h>
#include <MaxUsd/Utilities/MaxSupportUtils.h>

#include <pxr/usd/usd/editContext.h>
#include <pxr/usd/usdGeom/subset.h>
#include <pxr/usd/usdShade/materialBindingAPI.h>

#include <iparamb2.h>
#include <stdmat.h>

PXR_NAMESPACE_OPEN_SCOPE

namespace MaxUsdMultiMaterialUtils {

void AddMaterialDependencies(Mtl* material, std::vector<Mtl*>& subMtl)
{
    if (!material) {
        return;
    }

    if (material->IsMultiMtl()) {
        // If the material is a Multi/Sub-Object material, add all its sub-materials
        for (int i = 0; i < material->NumSubMtls(); ++i) {
            if (auto multiSubMtl = material->GetSubMtl(i)) {
                subMtl.push_back(multiSubMtl);
            }
        }
    } else {
        // Simple material, add it directly
        subMtl.push_back(material);
    }
}

void GetMatIDsFromMultiMat(Mtl* mat, std::set<int>& matIdSet)
{
    if (!mat || !mat->IsMultiMtl()) {
        return;
    }

    // Get material IDs from the Multi/Sub-Object material's parameter block
    IParamBlock2* mtlParamBlock2 = mat->GetParamBlockByID(0);
    if (!mtlParamBlock2) {
        return;
    }

    short paramId = MaxUsd::FindParamId(mtlParamBlock2, L"materialIDList");
    if (paramId < 0) {
        return;
    }

    Interval valid = FOREVER;
    for (int subIdx = 0; subIdx < mat->NumSubs(); subIdx++) {
        int matId;
        mtlParamBlock2->GetValue(paramId, 0, matId, valid, subIdx);
        matIdSet.insert(matId);
    }
}

bool HasMultiSubDependency(Mtl* material)
{
    return material && material->IsMultiMtl();
}

MaterialBindings::const_iterator FindMaterialBindings(
    Mtl*                            material,
    const MaxUsdWriteJobContext&    writeJobCtx)
{
    auto bindings = writeJobCtx.GetMaterialBindings();
    return std::find_if(bindings.begin(), bindings.end(), [material](const MaterialBinding& mb) {
        return mb.GetMaterial() == material;
    });
}

Mtl* GetSubMaterialByID(Mtl* material, int matID, const std::set<int>& matIdSet)
{
    if (!material) {
        return nullptr;
    }

    if (material->ClassID() == MULTI_MATERIAL_CLASS_ID) {
        const auto matIdIter = matIdSet.find(matID % material->NumSubMtls());
        if (matIdIter != matIdSet.end()) {
            return material->GetSubMtl(*matIdIter);
        }
    }
    
    return material;
}

// -----------------------------------------------------------------------------
// MAX-MAT-010 surgical-coverage bound (material-instance / Multi-Mtl
// override-structure fidelity audit, 2026-06-26).
//
// `DiscoverMaterialIDsAndCreateBundles` walks every geom-bind path
// supplied by the `MtlSwitcherWriter` (so it ALWAYS runs under a
// switcher context; the entry point is not invoked from the
// non-switcher pipeline). For each geomPrim it checks IsInstance(),
// and when the prim is a USD instance that diverges from its
// prototype it invokes `MaxUsdShadingUtils::BreakInstancingAndCopySubset`
// to author a per-instance override before bundle-creation runs.
//
// The instance-break decision here MIRRORS Branch D of the parent
// `_AddInstancePrimsToMaterialMap` decision tree (see the
// comment block above that function in
// `src/MaxUsd/Translators/ShadingUtils.cpp` for the full enumeration
// of Branches A/B/C/D). Two surgical sub-branches live here:
//
//   Switcher-Branch A (lines below: `IsInstance() == true` AND
//   the prototype child's path is ALREADY in `geomBindPaths`)
//     * Action: `continue` -- skip this instance. The prototype-
//       child path will be walked separately by the same loop, and
//       the switcher's variant-set bindings will be authored
//       against the prototype child. USD instancing PROPAGATES the
//       bindings to every instance via the inheritance arc.
//     * Surgical bound: instancing PRESERVED. A widening that
//       dropped this short-circuit would author N redundant per-
//       instance binding spans, one per instance prim, even when
//       the prototype-side binding already covers the case.
//
//   Switcher-Branch D (`IsInstance() == true` AND the prototype
//   child's path is NOT in `geomBindPaths` because this instance
//   carries its own MultiMtl-with-subsets override)
//     * Action: `BreakInstancingAndCopySubset` runs (defined in
//       `src/MaxUsd/Translators/ShadingUtils.cpp` lines 144-193).
//       Three things happen, identical to the parent Branch D:
//         (i)  `instancePrim.SetInstanceable(false)` on the
//              divergent instance;
//         (ii) the prototype's `materialBind`-family subsets are
//              COPIED onto a new override-child mesh prim under
//              the instance, with `customData[3dsmax:matId]`
//              preserved;
//         (iii) the loop continues with `geomPrim = newOverridePrim`
//              and `geomBindPath = newOverridePrim.GetPath()`, so
//              the bundle-creation callback authors variant-set
//              bindings against the COPIED subsets.
//     * Surgical bound: instancing BROKEN + subsets COPIED with
//       matId customData preserved + bundle bindings re-targeted
//       at the copied subsets. The KEY bound for the switcher's
//       variant-set flow.
//
// Why the two-file split (Branches A/B/C/D in ShadingUtils.cpp +
// Branches A/D here): the non-switcher pipeline does not need to
// pre-create variant bundles before binding; it can bind on the
// instance prim directly. The switcher pipeline needs bundles
// keyed by matId-set BEFORE it can author the variant materials,
// because each variant adds its own internal-reference to the
// per-bundle placeholder material. Both code paths share
// `BreakInstancingAndCopySubset` and the subset-copy contract, but
// the switcher's loop has to break instancing INLINE before bundle
// discovery so the override prim's subsets feed the matID-set
// discovery loop downstream.
//
// See also:
//   * Validator:
//     /Users/d.smith/MirisProjects/Agent Builder/agent/arch-builds/
//       1a92bee3-23b8-4465-93cf-122a6758ad16/
//       validate_material_instance_override_surgical.py
//     (`DifferentMaterial_MultiWithSubsets` case covers the switcher-
//     side variant of Branch D; the `Switcher_VariantSets_TwoMaterials`
//     case covers the bundle-author side of the variant set.)
//   * `src/translators/MtlSwitcherWriter.cpp::Write` for the three
//     switcher-shape branches (Empty / Single-variant fallback /
//     AsVariantSets >=2 variants) that determine whether this
//     function is invoked at all.
// -----------------------------------------------------------------------------
void DiscoverMaterialIDsAndCreateBundles(
    const std::list<SdfPath>&       geomBindPaths,
    UsdStagePtr                     stage,
    const MaxUsdWriteJobContext&    writeJobCtx,
    std::vector<std::set<int>>&     matIDsSets,
    const CreateBundleCallback&     createBundleCallback)
{
    for (auto geomBindPath : geomBindPaths) {
        auto geomPrim = stage->GetPrimAtPath(geomBindPath);

        if (geomPrim.IsInstance()) {
            auto protoPrim = geomPrim.GetPrototype().GetChildren().front();
            if (std::find(geomBindPaths.begin(), geomBindPaths.end(), protoPrim.GetPath())
                != geomBindPaths.end()) {
                // Nothing to do for this instance.
                continue;
            }
            // This instance has a different material than its prototype, break it.
            else {
                auto target = writeJobCtx.GetArgs().GetUseSeparateMaterialLayer()
                    ? stage->GetRootLayer()
                    : stage->GetEditTarget();

                UsdEditContext             editContext(stage, target);
                UsdShadeMaterialBindingAPI bindingAPI(protoPrim);
                auto                       subsetToCopy = bindingAPI.GetMaterialBindSubsets();
                if (!subsetToCopy.empty()) {
                    geomPrim = MaxUsdShadingUtils::BreakInstancingAndCopySubset(
                        stage, geomPrim, protoPrim, subsetToCopy);
                    geomBindPath = geomPrim.GetPath();
                }
            }
        }

        std::set<int> materialIdsSet;
        for (auto child : geomPrim.GetAllChildren()) {
            if (child.IsA<UsdGeomSubset>()) {
                materialIdsSet.insert(
                    MaxUsd::MeshConverter::GetMaterialIdFromCustomData(child));
            }
        }

        if (materialIdsSet.empty()) {
            // No geomSubSet, look for the MatID on the prim itself.
            int matId = MaxUsd::MeshConverter::GetMaterialIdFromCustomData(geomPrim);
            if (matId == -1) {
                // Didn't find the custom data, skip this Prim.
                continue;
            }
            materialIdsSet.insert(matId);
        }

        // Use callback to create bundle for this material ID set
        createBundleCallback(geomBindPath, materialIdsSet, matIDsSets);
    }
}



} // namespace MaxUsdMultiMaterialUtils

PXR_NAMESPACE_CLOSE_SCOPE
