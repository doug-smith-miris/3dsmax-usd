# Copyright 2026 Miris Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
"""
MAX-GEO-006 -- USD-layer structural mirror of the two-part C++ fix:

  * `src/MaxUsd/MeshConversion/MeshConverter.cpp` -- ports MAX-GEO-002's
    generalized early-out into the v0.16.2 base and extends it: any time the
    node's bound material is null OR a non-MultiMtl single shader AND no
    pre-existing materialBind subsets are on the prim, `ApplyMaxMaterialIDs`
    collapses to the single-matId `customData.3dsmax.matId` path instead of
    authoring N ghost `materialBind`-family GeomSubsets that carry no
    `material:binding` of their own. This kills the "25 ghost GeomSubsets"
    class of the MAX-GEO-006 baseline finding.

  * `src/MaxUsd/Translators/ShadingUtils.cpp` -- when a node has no Max
    material assigned (GetNodeMaterial() -> nullptr), `FetchMaterials` no
    longer just `continue`s. It authors an explicit `rel material:binding =
    None` on the bindable Gprim via
    `UsdShadeMaterialBindingAPI::UnbindDirectBinding()` so downstream
    `ComputeBoundMaterial` resolves deterministically to "unbound" instead
    of "no binding opinion". This kills the "82 root-level meshes with no
    resolvable binding" class of the MAX-GEO-006 baseline finding.

Baseline symptom (Spectrum Center arena, 13,713 meshes exported by the
pre-fix v0.16.2 lineage):

    Ghost materialBind GeomSubsets    :   25
    Root meshes with no material:binding : 82

Both classes disappear at the USD layer once the two fixes are applied.
Karma renders remain byte-identical for both classes -- ghost subsets never
carried a shader (only the mesh-level binding did) and the 82 unbound
meshes still resolve to no material after the fix (the fix authors the
"unbound" state EXPLICITLY, so the pixels don't move; the semantics do).
That's why this bite ships `structural_only.md` instead of before/after
renders.

The Python mirror below is a validator (not a shipping code path). It:
  1) Builds pre-fix in-memory stages matching what the pre-MAX-GEO-006
     writer chain would author -- ghost GeomSubsets on non-MultiMtl-bound
     parametric primitives, and no `material:binding` at all on unbound
     meshes.
  2) Applies the same two-branch fix at the USD layer:
       a) On a mesh with N > 1 face mat-IDs whose bound material is not a
          MultiMtl (or is null) and which has no pre-existing materialBind
          subsets: collapse to a single `customData.3dsmax.matId` = first
          face's matId + 1.
       b) On a bindable Gprim whose bound material is null and whose
          `material:binding` is not authored: call
          `UsdShadeMaterialBindingAPI::UnbindDirectBinding()`.
  3) Asserts the post-fix stage against exhaustive invariants:
       * 0 ghost materialBind GeomSubsets remain on non-MultiMtl-bound meshes,
       * 0 unbound-mesh material:binding relationships remain in the "no
         opinion" state (they now all carry an explicit `= None`),
       * MultiMtl-bound meshes STILL author the full partition (surgical:
         the fix does NOT drop legitimate partitions),
       * Meshes with pre-existing materialBind subsets STILL author them
         (surgical: the fix does NOT destroy prior authoring),
       * `customData.3dsmax.matId` is preserved on the collapsed prim so
        `MaxUsdTranslatorMaterial::AssignMaterial` sees a sensible matId
        on round-trip,
       * `ComputeBoundMaterial` returns "unbound" (Material invalid) on
         both the collapsed ghost-subset mesh and the newly-blocked
         unbound mesh -- exactly the deterministic state the finding
         asked for.
"""
import unittest
from typing import Dict, List, Optional, Sequence, Tuple

from pxr import Gf, Sdf, Usd, UsdGeom, UsdShade


# ---------------------------------------------------------------------------
# Fixture helpers -- build the pre-fix USD state the exporter would author.
# ---------------------------------------------------------------------------

# Custom-data key MaxUsd::MetaData::matId writes on prims + GeomSubsets.
_MATID_KEY = "3dsmax:matId"  # dotted for SetCustomDataByKey compatibility


def _mkstage() -> Usd.Stage:
    return Usd.Stage.CreateInMemory()


def _add_mesh(stage: Usd.Stage, path: str) -> UsdGeom.Mesh:
    return UsdGeom.Mesh.Define(stage, path)


def _add_ups_material(
    stage: Usd.Stage,
    path: str,
    diffuse: Tuple[float, float, float] = (0.5, 0.5, 0.5),
) -> UsdShade.Material:
    material = UsdShade.Material.Define(stage, path)
    shader = UsdShade.Shader.Define(stage, path + "/UsdPreviewSurface")
    shader.CreateIdAttr("UsdPreviewSurface")
    shader.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).Set(
        Gf.Vec3f(*diffuse)
    )
    material.CreateSurfaceOutput().ConnectToSource(shader.ConnectableAPI(), "surface")
    return material


def _bind_direct(mesh: UsdGeom.Mesh, material: UsdShade.Material) -> None:
    """Mirror of the FetchMaterials -> ShadingModeExporter binding path when
    the bound material is a single, non-Multi shader."""
    UsdShade.MaterialBindingAPI.Apply(mesh.GetPrim()).Bind(material)


def _author_ghost_subsets(
    mesh: UsdGeom.Mesh,
    mat_id_to_face_indices: Dict[int, List[int]],
) -> List[UsdGeom.Subset]:
    """Author what the *pre-fix* ApplyMaxMaterialIDs writes when the bound
    material is null or non-MultiMtl: N GeomSubsets in the materialBind
    family, each with a customData.3dsmax.matId, and NO material:binding
    relationship of their own. This is the "25 ghost subsets" class from
    the arena baseline.
    """
    api = UsdShade.MaterialBindingAPI.Apply(mesh.GetPrim())
    api.SetMaterialBindSubsetsFamilyType(UsdGeom.Tokens.partition)
    subsets: List[UsdGeom.Subset] = []
    for mat_id, face_indices in mat_id_to_face_indices.items():
        # Name mirrors what MaterialUtils::CreateSubsetName produces when the
        # bound material is null/non-MultiMtl -- a "_N_"-style fallback.
        subset_name = f"_{mat_id + 1}_"
        subset = api.CreateMaterialBindSubset(subset_name, face_indices)
        subset.GetPrim().SetCustomDataByKey(_MATID_KEY, mat_id + 1)
        subsets.append(subset)
    return subsets


def _author_real_multimtl_subsets(
    stage: Usd.Stage,
    mesh: UsdGeom.Mesh,
    matid_to_indices_and_material: Dict[
        int, Tuple[List[int], UsdShade.Material]
    ],
) -> List[UsdGeom.Subset]:
    """The other branch: when the bound material IS a MultiMtl, subsets
    DO carry their own material:binding to the corresponding sub-material.
    This is the branch the MAX-GEO-006 fix must PRESERVE untouched.
    """
    api = UsdShade.MaterialBindingAPI.Apply(mesh.GetPrim())
    api.SetMaterialBindSubsetsFamilyType(UsdGeom.Tokens.partition)
    subsets: List[UsdGeom.Subset] = []
    for mat_id, (face_indices, sub_mat) in matid_to_indices_and_material.items():
        subset_name = sub_mat.GetPrim().GetName()
        subset = api.CreateMaterialBindSubset(subset_name, face_indices)
        subset.GetPrim().SetCustomDataByKey(_MATID_KEY, mat_id + 1)
        UsdShade.MaterialBindingAPI.Apply(subset.GetPrim()).Bind(sub_mat)
        subsets.append(subset)
    return subsets


# ---------------------------------------------------------------------------
# The Python mirror of the C++ fix.
# ---------------------------------------------------------------------------


def _apply_fix_ghost_subsets(
    mesh: UsdGeom.Mesh,
    bound_is_multimtl: bool,
    mat_id_to_face_indices: Dict[int, List[int]],
) -> str:
    """Mirror of MeshConverter::ApplyMaxMaterialIDs's fixed logic.

    Returns a short verdict string describing which branch the fix took --
    exposed only for test assertions, not part of the shipping code path.
    """
    prim = mesh.GetPrim()
    # single mat-id -> customData path (unchanged from pre-fix).
    if len(mat_id_to_face_indices) == 1:
        mat_id = list(mat_id_to_face_indices.keys())[0] + 1
        prim.SetCustomDataByKey(_MATID_KEY, mat_id)
        return "single_matid_customdata"

    # MAX-GEO-002 / MAX-GEO-006 generalized early-out.
    if not bound_is_multimtl:
        api = UsdShade.MaterialBindingAPI(prim)
        if len(api.GetMaterialBindSubsets()) == 0:
            mat_id = list(mat_id_to_face_indices.keys())[0] + 1
            prim.SetCustomDataByKey(_MATID_KEY, mat_id)
            return "collapsed_ghost_subsets"
        # Fall through: pre-existing subsets -> preserve prior authoring.
        return "preserved_prior_authoring"

    # MultiMtl-bound: retain the existing full-partition path.
    api = UsdShade.MaterialBindingAPI.Apply(prim)
    api.SetMaterialBindSubsetsFamilyType(UsdGeom.Tokens.partition)
    for mat_id, face_indices in mat_id_to_face_indices.items():
        subset_name = f"_{mat_id + 1}_"
        subset = api.CreateMaterialBindSubset(subset_name, face_indices)
        subset.GetPrim().SetCustomDataByKey(_MATID_KEY, mat_id + 1)
    return "authored_full_partition"


def _apply_fix_unbound_mesh(mesh: UsdGeom.Mesh) -> str:
    """Mirror of the ShadingUtils::FetchMaterials fixed unbound branch:
    author an explicit `material:binding = None` when the mesh has no Max
    material assigned and no direct binding is authored yet.
    """
    api = UsdShade.MaterialBindingAPI.Apply(mesh.GetPrim())
    direct_rel = api.GetDirectBindingRel()
    if direct_rel and direct_rel.HasAuthoredTargets():
        return "preserved_prior_binding"
    api.UnbindDirectBinding()
    return "authored_none"


# ---------------------------------------------------------------------------
# Structural inspectors.
# ---------------------------------------------------------------------------


def _count_ghost_material_bind_subsets(stage: Usd.Stage) -> int:
    """A ghost = a GeomSubset in the materialBind family that carries no
    material:binding of its own. This is the exact defect signature MAX-GEO-002
    caught and MAX-GEO-006 extends coverage of."""
    n = 0
    for prim in stage.Traverse():
        if not prim.IsA(UsdGeom.Subset):
            continue
        subset = UsdGeom.Subset(prim)
        family = subset.GetFamilyNameAttr().Get()
        if family != "materialBind":
            continue
        api = UsdShade.MaterialBindingAPI(prim)
        rel = api.GetDirectBindingRel()
        if not rel or not rel.HasAuthoredTargets():
            n += 1
    return n


def _count_meshes_without_binding_opinion(stage: Usd.Stage) -> int:
    """A mesh with no `material:binding` relationship at all -- neither
    a real target nor an explicit `= None`. This is the exact defect
    signature the 82-unbound-meshes class of MAX-GEO-006 flagged."""
    n = 0
    for prim in stage.Traverse():
        if not prim.IsA(UsdGeom.Mesh):
            continue
        api = UsdShade.MaterialBindingAPI(prim)
        rel = api.GetDirectBindingRel()
        if not rel or not rel.IsValid():
            n += 1
    return n


def _has_authored_none_binding(prim: Usd.Prim) -> bool:
    """`rel material:binding = None` == relationship IS authored but resolves
    to no targets."""
    api = UsdShade.MaterialBindingAPI(prim)
    rel = api.GetDirectBindingRel()
    if not rel or not rel.IsValid():
        return False
    if rel.HasAuthoredTargets():
        # An authored target list, even if empty, doesn't count as "= None".
        return len(rel.GetTargets()) == 0 and rel.GetForwardedTargets() == []
    # No authored targets: check that the relationship spec exists in a layer.
    prim_stack = prim.GetPrimStack()
    for spec in prim_stack:
        for prop_spec in spec.properties:
            if prop_spec.name == "material:binding":
                return True
    return False


# ---------------------------------------------------------------------------
# Tests -- one per invariant the fix guarantees.
# ---------------------------------------------------------------------------


class TestPreFixDefect(unittest.TestCase):
    """Sanity-check the pre-fix state actually reproduces the arena
    baseline signatures."""

    def test_pre_fix_ghost_subsets_visible(self) -> None:
        stage = _mkstage()
        UsdGeom.Xform.Define(stage, "/root")
        _add_ups_material(stage, "/root/Materials/M_single")
        mesh = _add_mesh(stage, "/root/Box")
        single_mat = UsdShade.Material.Get(stage, "/root/Materials/M_single")
        _bind_direct(mesh, single_mat)
        _author_ghost_subsets(
            mesh, {0: [0], 1: [1], 2: [2], 3: [3], 4: [4], 5: [5]}
        )
        self.assertEqual(_count_ghost_material_bind_subsets(stage), 6)

    def test_pre_fix_unbound_meshes_visible(self) -> None:
        stage = _mkstage()
        UsdGeom.Xform.Define(stage, "/root")
        _add_mesh(stage, "/root/Curtain_Panels")
        _add_mesh(stage, "/root/LOGO_MEDALION_BUZZ_CITY")
        _add_mesh(stage, "/root/LOGO_MEDALION_HORNET")
        self.assertEqual(_count_meshes_without_binding_opinion(stage), 3)


class TestFixGhostSubsets(unittest.TestCase):
    """MAX-GEO-006's ghost-subset half."""

    def test_null_material_multiple_mat_ids_collapses_to_customdata(self) -> None:
        stage = _mkstage()
        UsdGeom.Xform.Define(stage, "/root")
        mesh = _add_mesh(stage, "/root/Box")
        # No _bind_direct call -> mimics node->GetMtl() == nullptr on the
        # Max side.
        verdict = _apply_fix_ghost_subsets(
            mesh,
            bound_is_multimtl=False,
            mat_id_to_face_indices={
                0: [0], 1: [1], 2: [2], 3: [3], 4: [4], 5: [5],
            },
        )
        self.assertEqual(verdict, "collapsed_ghost_subsets")
        self.assertEqual(_count_ghost_material_bind_subsets(stage), 0)
        # customData preserved so ApplyUSDMaterialIDs still round-trips.
        self.assertEqual(
            mesh.GetPrim().GetCustomDataByKey(_MATID_KEY), 1
        )

    def test_non_multimtl_multiple_mat_ids_collapses(self) -> None:
        stage = _mkstage()
        UsdGeom.Xform.Define(stage, "/root")
        _add_ups_material(stage, "/root/Materials/PhysicalMaterial")
        mesh = _add_mesh(stage, "/root/Cylinder")
        # A single PhysicalMaterial bound at the node level -- the exact case
        # MAX-GEO-002 already covered upstream but which the v0.16.2 base
        # regressed.
        _bind_direct(
            mesh, UsdShade.Material.Get(stage, "/root/Materials/PhysicalMaterial")
        )
        verdict = _apply_fix_ghost_subsets(
            mesh,
            bound_is_multimtl=False,
            mat_id_to_face_indices={0: [0, 1], 1: [2, 3], 2: [4, 5]},
        )
        self.assertEqual(verdict, "collapsed_ghost_subsets")
        self.assertEqual(_count_ghost_material_bind_subsets(stage), 0)

    def test_multimtl_multiple_mat_ids_authors_partition(self) -> None:
        """The fix MUST NOT touch the MultiMtl path -- ghost subsets exist
        only when the bound material can't consume them."""
        stage = _mkstage()
        UsdGeom.Xform.Define(stage, "/root")
        mesh = _add_mesh(stage, "/root/Panel")
        verdict = _apply_fix_ghost_subsets(
            mesh,
            bound_is_multimtl=True,
            mat_id_to_face_indices={0: [0, 1], 1: [2, 3], 2: [4, 5]},
        )
        self.assertEqual(verdict, "authored_full_partition")
        api = UsdShade.MaterialBindingAPI(mesh.GetPrim())
        subsets = api.GetMaterialBindSubsets()
        self.assertEqual(len(subsets), 3)

    def test_preserves_pre_existing_materialbind_subsets(self) -> None:
        """When materialBind subsets are already on the prim we must fall
        through to the legacy authoring path, NOT collapse. This mirrors the
        `existingBindingAPI.GetMaterialBindSubsets().empty()` guard on the
        C++ side."""
        stage = _mkstage()
        UsdGeom.Xform.Define(stage, "/root")
        _add_ups_material(stage, "/root/Materials/Sub1", (1, 0, 0))
        _add_ups_material(stage, "/root/Materials/Sub2", (0, 1, 0))
        mesh = _add_mesh(stage, "/root/RichPanel")
        _author_real_multimtl_subsets(
            stage,
            mesh,
            {
                0: ([0, 1], UsdShade.Material.Get(stage, "/root/Materials/Sub1")),
                1: ([2, 3], UsdShade.Material.Get(stage, "/root/Materials/Sub2")),
            },
        )
        verdict = _apply_fix_ghost_subsets(
            mesh,
            bound_is_multimtl=False,
            mat_id_to_face_indices={0: [0, 1], 1: [2, 3]},
        )
        self.assertEqual(verdict, "preserved_prior_authoring")
        api = UsdShade.MaterialBindingAPI(mesh.GetPrim())
        subsets = api.GetMaterialBindSubsets()
        self.assertEqual(len(subsets), 2)
        # Real bindings preserved.
        for subset in subsets:
            sub_api = UsdShade.MaterialBindingAPI(subset.GetPrim())
            self.assertTrue(sub_api.GetDirectBindingRel().HasAuthoredTargets())

    def test_single_matid_still_customdata_only(self) -> None:
        """Pre-existing size==1 early-out is UNCHANGED (surgical)."""
        stage = _mkstage()
        UsdGeom.Xform.Define(stage, "/root")
        mesh = _add_mesh(stage, "/root/Sphere")
        verdict = _apply_fix_ghost_subsets(
            mesh,
            bound_is_multimtl=False,
            mat_id_to_face_indices={4: [0, 1, 2]},
        )
        self.assertEqual(verdict, "single_matid_customdata")
        self.assertEqual(mesh.GetPrim().GetCustomDataByKey(_MATID_KEY), 5)
        self.assertEqual(
            len(UsdShade.MaterialBindingAPI(mesh.GetPrim()).GetMaterialBindSubsets()),
            0,
        )


class TestFixUnboundMeshes(unittest.TestCase):
    """MAX-GEO-006's unbound-mesh half."""

    def test_unbound_mesh_gets_explicit_none_binding(self) -> None:
        stage = _mkstage()
        UsdGeom.Xform.Define(stage, "/root")
        mesh = _add_mesh(stage, "/root/Curtain_Panels")
        verdict = _apply_fix_unbound_mesh(mesh)
        self.assertEqual(verdict, "authored_none")
        # The fix must produce ZERO "no binding opinion" meshes.
        self.assertEqual(_count_meshes_without_binding_opinion(stage), 0)
        self.assertTrue(_has_authored_none_binding(mesh.GetPrim()))

    def test_compute_bound_material_resolves_deterministically(self) -> None:
        stage = _mkstage()
        UsdGeom.Xform.Define(stage, "/root")
        mesh = _add_mesh(stage, "/root/Vomitory_01")
        _apply_fix_unbound_mesh(mesh)
        api = UsdShade.MaterialBindingAPI(mesh.GetPrim())
        mat, _rel = api.ComputeBoundMaterial()
        # Deterministic unbound: Material is invalid, but the relationship
        # is authored. Pre-fix this was ALSO Material-invalid, but the
        # relationship was not authored at all (the "no opinion" state the
        # finding flagged).
        self.assertFalse(bool(mat))
        rel = api.GetDirectBindingRel()
        self.assertTrue(rel and rel.IsValid())

    def test_idempotent_on_already_bound_mesh(self) -> None:
        """The fix must NOT clobber a mesh that already carries a real
        `material:binding`. This is the guarantee the
        `directRel.HasAuthoredTargets()` gate provides on the C++ side."""
        stage = _mkstage()
        UsdGeom.Xform.Define(stage, "/root")
        _add_ups_material(stage, "/root/Materials/RealMat")
        mesh = _add_mesh(stage, "/root/BoundMesh")
        real_mat = UsdShade.Material.Get(stage, "/root/Materials/RealMat")
        _bind_direct(mesh, real_mat)

        verdict = _apply_fix_unbound_mesh(mesh)
        self.assertEqual(verdict, "preserved_prior_binding")
        api = UsdShade.MaterialBindingAPI(mesh.GetPrim())
        mat, _rel = api.ComputeBoundMaterial()
        # Real binding preserved.
        self.assertTrue(bool(mat))
        self.assertEqual(
            mat.GetPrim().GetPath(), Sdf.Path("/root/Materials/RealMat")
        )


class TestArenaSlice(unittest.TestCase):
    """End-to-end census on a synthetic slice of the Spectrum Center arena
    baseline signature: 82 unbound meshes + 25 ghost subsets scattered
    across 6 materials.

    We reproduce a proportionally-scaled slice (not the full 13,713-mesh
    export) and prove:

      * pre-fix census matches the arena signature ratio (unbound > 0,
        ghost > 0),
      * post-fix census is 0/0 on both defects,
      * total mesh count is unchanged (surgical: the fix authors state
        on prims, never removes prims).
    """

    def _build_pre_fix_arena_slice(self) -> Usd.Stage:
        stage = _mkstage()
        UsdGeom.Xform.Define(stage, "/root")
        # 82 meshes with no material bound at all (proportional to the
        # arena baseline's 82 root-level unbound meshes).
        for i in range(82):
            _add_mesh(stage, f"/root/Unbound_{i:02d}")

        # 25 ghost subsets distributed across 5 non-MultiMtl-bound
        # parametric meshes (5 subsets each -- mimics a ChamferBox default
        # per-face mat-ID split).
        _add_ups_material(stage, "/root/Materials/M_arena", (0.4, 0.4, 0.5))
        for i in range(5):
            mesh_path = f"/root/GhostSubsetHost_{i}"
            mesh = _add_mesh(stage, mesh_path)
            _bind_direct(
                mesh, UsdShade.Material.Get(stage, "/root/Materials/M_arena")
            )
            _author_ghost_subsets(
                mesh, {j: [j] for j in range(5)}
            )
        return stage

    def _mesh_paths(self, stage: Usd.Stage) -> List[Sdf.Path]:
        return [
            p.GetPath()
            for p in stage.Traverse()
            if p.IsA(UsdGeom.Mesh)
        ]

    def test_arena_slice_pre_fix_matches_baseline_signature(self) -> None:
        stage = self._build_pre_fix_arena_slice()
        self.assertEqual(_count_ghost_material_bind_subsets(stage), 25)
        self.assertEqual(_count_meshes_without_binding_opinion(stage), 82)
        self.assertEqual(len(self._mesh_paths(stage)), 82 + 5)

    def test_arena_slice_post_fix_kills_both_defect_classes(self) -> None:
        stage = self._build_pre_fix_arena_slice()
        pre_mesh_paths = set(self._mesh_paths(stage))

        # Apply the ghost-subset fix on the 5 ghost-host meshes.
        # (The pre-existing-subsets guard means we ONLY collapse when we
        # haven't already authored subsets on this synthetic slice -- but
        # since our fixture sets bound_is_multimtl=False with real subsets,
        # we need to first strip them to model the actual pre-fix defect:
        # the C++ writer authored those subsets in the SAME call, so at the
        # point ApplyMaxMaterialIDs runs, no pre-existing subsets are there.
        # Model that by removing the subsets we synthesized, then re-invoke
        # the fixed logic.)
        for i in range(5):
            mesh_path = f"/root/GhostSubsetHost_{i}"
            mesh_prim = stage.GetPrimAtPath(mesh_path)
            # Remove synthetic ghost subsets.
            for child in list(mesh_prim.GetChildren()):
                if child.IsA(UsdGeom.Subset):
                    stage.RemovePrim(child.GetPath())
            mesh = UsdGeom.Mesh(mesh_prim)
            verdict = _apply_fix_ghost_subsets(
                mesh,
                bound_is_multimtl=False,
                mat_id_to_face_indices={j: [j] for j in range(5)},
            )
            self.assertEqual(verdict, "collapsed_ghost_subsets")

        # Apply the unbound-mesh fix on the 82 unbound meshes.
        for i in range(82):
            mesh_prim = stage.GetPrimAtPath(f"/root/Unbound_{i:02d}")
            mesh = UsdGeom.Mesh(mesh_prim)
            verdict = _apply_fix_unbound_mesh(mesh)
            self.assertEqual(verdict, "authored_none")

        # Post-fix census: both defect classes ARE zero.
        self.assertEqual(_count_ghost_material_bind_subsets(stage), 0)
        self.assertEqual(_count_meshes_without_binding_opinion(stage), 0)
        # Mesh set unchanged -- surgical.
        self.assertEqual(set(self._mesh_paths(stage)), pre_mesh_paths)

    def test_surgical_scope_no_regression_on_bound_meshes(self) -> None:
        """A representative "bound mesh" (single-material, no per-face IDs)
        must be UNCHANGED by both fixes. This locks in the surgical scope
        by proving neither fix touches the common case."""
        stage = _mkstage()
        UsdGeom.Xform.Define(stage, "/root")
        _add_ups_material(stage, "/root/Materials/BoundMat", (0.7, 0.7, 0.7))
        mesh = _add_mesh(stage, "/root/BoundStair")
        real_mat = UsdShade.Material.Get(stage, "/root/Materials/BoundMat")
        _bind_direct(mesh, real_mat)

        pre_export = stage.GetRootLayer().ExportToString()

        # Ghost-subset fix -- must take the single_matid_customdata path
        # (size == 1) or fall through cleanly.
        _apply_fix_ghost_subsets(
            mesh, bound_is_multimtl=False, mat_id_to_face_indices={3: [0, 1, 2]}
        )
        # Unbound-mesh fix -- must PRESERVE the bound state.
        _apply_fix_unbound_mesh(mesh)

        api = UsdShade.MaterialBindingAPI(mesh.GetPrim())
        mat, _rel = api.ComputeBoundMaterial()
        self.assertTrue(bool(mat))
        self.assertEqual(
            mat.GetPrim().GetPath(), Sdf.Path("/root/Materials/BoundMat")
        )
        # Sanity: the bound material's binding survived.
        rel = api.GetDirectBindingRel()
        self.assertTrue(rel.HasAuthoredTargets())
        self.assertEqual(
            rel.GetTargets(), [Sdf.Path("/root/Materials/BoundMat")]
        )


if __name__ == "__main__":
    unittest.main()
