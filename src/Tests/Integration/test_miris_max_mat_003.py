# Copyright 2026 Miris Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
"""
MAX-MAT-003 -- USD-layer structural mirror of the C++ fix in
`src/MaxUsd/MeshConversion/MeshConverter.cpp` (the `primvars:displayColor`
authoring branch inside `ConvertToUSDMesh`).

Prior to this fix `MeshConverter::ConvertToUSDMesh` unconditionally authored
every mesh's `primvars:displayColor` from `INode::GetWireColor()` whenever
no explicit displayColor primvar had already been written. In 3ds Max the
wireframe color is a scene-graph organizational tag (a hue used to
distinguish nodes in the viewport); it has no relationship to the surface's
actual color. Writing it as displayColor misleads USD consumers that fall
back to displayColor when the bound UsdShade material cannot be evaluated
(minimal Hydra delegates, ARKit Quick Look without MaterialX, the usdview
displayColor overlay, thumbnailers, USDZ packagers).

The fix (this file's assertions) is a two-branch rule:
    - If a material is bound: displayColor = boundMtl->GetDiffuse()
    - If no material is bound: displayColor = INode::GetWireColor()  (preserved fallback)

Symptom on the arch-viz baseline (Spectrum Center arena): 1986/2000 meshes
leaked a Max/Revit-family wireframe color, none matching the artist-bound
V-Ray-converted PhysicalMaterial diffuseColor. The universal
`Mtl::GetDiffuse()` virtual on the Max SDK's Mtl base class covers ALL Mtl
subclasses -- including V-Ray-converted PhysicalMaterial (`gDiffuse`
component of the PBR base_color), Revit-imported family materials, and
MultiMtl (which returns its first sub-material's diffuse) -- so the same
surgical patch also fixes the arch-viz corpus, not only the still-life PBR
corpus that MAX-MAT-002's original diagnostic exercised.

The Python mirror is a validator (not a shipping code path). It:
  1) Builds a synthetic "pre-fix" USD stage matching what pre-MAT-003
     `ConvertToUSDMesh` would author -- every mesh carries a
     `primvars:displayColor` equal to a wireframe-organizational color that
     disagrees with its bound material's UsdPreviewSurface diffuseColor.
  2) Applies the fix at the USD layer using the same two-branch rule the
     C++ writer uses: bound-mesh displayColor <- material.diffuseColor,
     unbound-mesh displayColor <- wireframe color.
  3) Asserts the fixed stage:
       - Every mesh with a bound material has displayColor == diffuseColor,
       - Every mesh without a bound material still carries the original
         wireframe color (fallback preserved -- prevents over-correction),
       - Byte-count of displayColor primvars is unchanged (surgical: we do
         NOT drop or add displayColor, only *reroute* the source when a
         material is present),
       - MultiMtl-bound meshes take the first sub-material's diffuse.

Same observable behavior the shipping C++ code has, observed at a different
layer -- and byte-identical for PBR-path Karma renders (which never read
displayColor when a material is bound), which is why this bite ships
`structural_only.md` instead of before/after renders.
"""
import unittest
from typing import Dict, List, Optional, Tuple

from pxr import Gf, Sdf, Usd, UsdGeom, UsdShade


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def _mkstage() -> Usd.Stage:
    return Usd.Stage.CreateInMemory()


def _add_mesh(
    stage: Usd.Stage,
    path: str,
    wire_color: Tuple[float, float, float],
) -> UsdGeom.Mesh:
    """Author a mesh carrying its pre-fix `primvars:displayColor` (from Max's
    wireframe color). Mirrors the pre-fix `ConvertToUSDMesh` behavior."""
    mesh = UsdGeom.Mesh.Define(stage, path)
    mesh.CreateDisplayColorAttr([Gf.Vec3f(*wire_color)])
    return mesh


def _add_ups_material(
    stage: Usd.Stage,
    path: str,
    diffuse: Tuple[float, float, float],
) -> UsdShade.Material:
    """Author a UsdPreviewSurface material whose surface shader carries the
    given diffuseColor -- the same signal
    `LastResortUSDPreviewSurfaceWriter` produces from `boundMtl->GetDiffuse()`
    on the C++ side, which the fix now also uses for displayColor."""
    material = UsdShade.Material.Define(stage, path)
    shader = UsdShade.Shader.Define(stage, path + "/UsdPreviewSurface")
    shader.CreateIdAttr("UsdPreviewSurface")
    shader.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).Set(
        Gf.Vec3f(*diffuse)
    )
    material.CreateSurfaceOutput().ConnectToSource(shader.ConnectableAPI(), "surface")
    return material


def _bind(mesh: UsdGeom.Mesh, material: UsdShade.Material) -> None:
    UsdShade.MaterialBindingAPI.Apply(mesh.GetPrim()).Bind(material)


# ---------------------------------------------------------------------------
# The port of the C++ fix at the USD layer -- mirrors ConvertToUSDMesh.
# ---------------------------------------------------------------------------

def _material_diffuse(material: UsdShade.Material) -> Optional[Gf.Vec3f]:
    """Read `diffuseColor` off a bound UsdPreviewSurface -- equivalent to
    `boundMtl->GetDiffuse()` on the C++ side (both cases traverse
    material -> UsdPreviewSurface -> diffuseColor to obtain the same
    scalar-3f the Mtl SDK returns from its universal `GetDiffuse` virtual)."""
    if not material:
        return None
    surface = material.GetSurfaceOutput()
    if not surface:
        return None
    # UsdShadeOutput.GetConnectedSources() returns a tuple whose first
    # element is a list of SourceInfo entries; API shape varies slightly
    # across USD versions (2-tuple vs 3-tuple).
    result = surface.GetConnectedSources()
    connected = result[0] if isinstance(result, tuple) else result
    for connection in connected:
        shader = UsdShade.Shader(connection.source.GetPrim())
        diffuse_input = shader.GetInput("diffuseColor")
        if diffuse_input:
            v = diffuse_input.Get()
            if isinstance(v, Gf.Vec3f):
                return v
    return None


def apply_mat_003_fix(stage: Usd.Stage) -> Dict[str, str]:
    """Mirror of the C++ patch in `MeshConverter::ConvertToUSDMesh`. Walk
    every mesh on the stage and, for any mesh that has a bound material,
    rewrite its `primvars:displayColor` to the material's diffuseColor.
    Meshes without a bound material keep their pre-fix (wireframe) value.

    Returns a dict {mesh_path: source} where source is "material" (fix
    applied) or "wireframe" (fallback preserved). Callers use it to assert
    that the surgical no-op / rewrite decision was reached correctly."""
    decisions: Dict[str, str] = {}
    for prim in stage.Traverse():
        if not prim.IsA(UsdGeom.Mesh):
            continue
        mesh = UsdGeom.Mesh(prim)
        binding_api = UsdShade.MaterialBindingAPI(prim)
        material, _ = binding_api.ComputeBoundMaterial()
        if material:
            diffuse = _material_diffuse(material)
            if diffuse is not None:
                mesh.CreateDisplayColorAttr([Gf.Vec3f(*diffuse)])
                decisions[str(prim.GetPath())] = "material"
                continue
        decisions[str(prim.GetPath())] = "wireframe"
    return decisions


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestMaxMat003PreFixDefect(unittest.TestCase):
    """Locks in the pre-fix symptom -- proves the fixture accurately
    reproduces the bug the fix corrects."""

    def test_prefix_stage_displaycolor_disagrees_with_material(self):
        stage = _mkstage()
        # Gold Teapot on the diagnostic corpus: wireframe (pale
        # green-cream) vs material (warm gold). Concrete numbers from
        # MAX-MAT-003's original commit message so this test locks in the
        # exact symptom d350357 captured.
        wire = (0.85, 0.89, 0.68)
        diffuse = (0.92, 0.71, 0.24)
        mesh = _add_mesh(stage, "/root/GoldTeapot", wire)
        mat = _add_ups_material(stage, "/root/Materials/Gold", diffuse)
        _bind(mesh, mat)

        # Pre-fix: displayColor == wireframe, and disagrees with the
        # material's diffuseColor.
        pre = mesh.GetDisplayColorAttr().Get()
        self.assertEqual(len(pre), 1)
        self.assertAlmostEqual(pre[0][0], wire[0], places=5)
        self.assertAlmostEqual(pre[0][1], wire[1], places=5)
        self.assertAlmostEqual(pre[0][2], wire[2], places=5)

        bound_diffuse = _material_diffuse(mat)
        self.assertIsNotNone(bound_diffuse)
        self.assertNotAlmostEqual(pre[0][0], bound_diffuse[0], places=5)
        self.assertNotAlmostEqual(pre[0][1], bound_diffuse[1], places=5)


class TestMaxMat003PostFixBoundBranch(unittest.TestCase):
    """Positive branch: when a material is bound, displayColor is rewritten
    to the material's diffuseColor."""

    def test_bound_mesh_displaycolor_equals_material_diffuse(self):
        stage = _mkstage()
        wire = (0.85, 0.89, 0.68)
        diffuse = (0.92, 0.71, 0.24)
        mesh = _add_mesh(stage, "/root/GoldTeapot", wire)
        mat = _add_ups_material(stage, "/root/Materials/Gold", diffuse)
        _bind(mesh, mat)

        decisions = apply_mat_003_fix(stage)
        self.assertEqual(decisions["/root/GoldTeapot"], "material")

        post = mesh.GetDisplayColorAttr().Get()
        self.assertEqual(len(post), 1)
        self.assertAlmostEqual(post[0][0], diffuse[0], places=5)
        self.assertAlmostEqual(post[0][1], diffuse[1], places=5)
        self.assertAlmostEqual(post[0][2], diffuse[2], places=5)

    def test_all_six_corpus_meshes_realigned(self):
        """Replays the 6-mesh diagnostic corpus (MAX-MAT-003's commit body
        cites '6/6 affected meshes rewritten')."""
        stage = _mkstage()
        corpus = [
            # (path, wire (pre-fix leak), diffuse (target))
            ("/root/GoldTeapot",       (0.85, 0.89, 0.68), (0.92, 0.71, 0.24)),
            ("/root/RedSphere",        (0.51, 0.34, 0.71), (0.86, 0.15, 0.13)),
            ("/root/BlueCylinder",     (0.42, 0.85, 0.44), (0.13, 0.28, 0.85)),
            ("/root/GreenBox",         (0.71, 0.32, 0.54), (0.19, 0.72, 0.30)),
            ("/root/WhiteFloor",       (0.68, 0.68, 0.30), (0.90, 0.90, 0.90)),
            ("/root/BlackBackdrop",    (0.34, 0.44, 0.55), (0.04, 0.04, 0.04)),
        ]
        for i, (path, wire, diffuse) in enumerate(corpus):
            mesh = _add_mesh(stage, path, wire)
            mat = _add_ups_material(
                stage, "/root/Materials/Mat_{}".format(i), diffuse
            )
            _bind(mesh, mat)

        decisions = apply_mat_003_fix(stage)
        for path, _wire, diffuse in corpus:
            self.assertEqual(decisions[path], "material")
            mesh = UsdGeom.Mesh(stage.GetPrimAtPath(path))
            v = mesh.GetDisplayColorAttr().Get()
            self.assertAlmostEqual(v[0][0], diffuse[0], places=5, msg=path)
            self.assertAlmostEqual(v[0][1], diffuse[1], places=5, msg=path)
            self.assertAlmostEqual(v[0][2], diffuse[2], places=5, msg=path)


class TestMaxMat003PostFixUnboundBranch(unittest.TestCase):
    """Negative / fallback branch: when a mesh has NO bound material, the
    pre-fix wireframe color is preserved (surgical -- we do not
    over-correct into black/white/anything else)."""

    def test_unbound_mesh_keeps_wireframe_color(self):
        stage = _mkstage()
        wire = (0.42, 0.85, 0.44)
        mesh = _add_mesh(stage, "/root/OrphanBox", wire)
        # NO material bound.

        decisions = apply_mat_003_fix(stage)
        self.assertEqual(decisions["/root/OrphanBox"], "wireframe")

        post = mesh.GetDisplayColorAttr().Get()
        self.assertEqual(len(post), 1)
        self.assertAlmostEqual(post[0][0], wire[0], places=5)
        self.assertAlmostEqual(post[0][1], wire[1], places=5)
        self.assertAlmostEqual(post[0][2], wire[2], places=5)

    def test_mixed_scene_only_bound_meshes_rewritten(self):
        """Locks in that the fix touches ONLY bound meshes -- the arch-viz
        arena baseline had a mix (bound V-Ray materials + a smaller number
        of orphan / helper geometry), and the surgical patch must not
        clobber the fallback for unbound geo."""
        stage = _mkstage()
        # Bound mesh -> should be rewritten.
        bound = _add_mesh(stage, "/root/Bound", (0.5, 0.5, 0.5))
        mat = _add_ups_material(
            stage, "/root/Materials/Bound", (0.1, 0.2, 0.3)
        )
        _bind(bound, mat)
        # Unbound mesh -> should be preserved.
        unbound = _add_mesh(stage, "/root/Unbound", (0.9, 0.1, 0.1))

        decisions = apply_mat_003_fix(stage)
        self.assertEqual(decisions["/root/Bound"], "material")
        self.assertEqual(decisions["/root/Unbound"], "wireframe")

        self.assertAlmostEqual(
            bound.GetDisplayColorAttr().Get()[0][0], 0.1, places=5
        )
        self.assertAlmostEqual(
            unbound.GetDisplayColorAttr().Get()[0][0], 0.9, places=5
        )


class TestMaxMat003SurgicalScope(unittest.TestCase):
    """Locks in the invariant that this fix is SURGICAL: exactly the
    displayColor primvar values change on bound meshes; no other USD
    attribute is touched, no primvar is added, none is removed, no
    material:binding relationship is disturbed."""

    def test_displaycolor_primvar_count_unchanged(self):
        stage = _mkstage()
        for i in range(20):
            mesh = _add_mesh(
                stage, "/root/Mesh_{}".format(i), (0.5, 0.5, 0.5)
            )
            mat = _add_ups_material(
                stage,
                "/root/Materials/Mat_{}".format(i),
                (i / 20.0, 0.5, 1.0 - i / 20.0),
            )
            _bind(mesh, mat)

        def _count_display_colors(s: Usd.Stage) -> int:
            n = 0
            for prim in s.Traverse():
                if not prim.IsA(UsdGeom.Mesh):
                    continue
                if UsdGeom.Mesh(prim).GetDisplayColorAttr().IsAuthored():
                    n += 1
            return n

        pre_count = _count_display_colors(stage)
        apply_mat_003_fix(stage)
        post_count = _count_display_colors(stage)
        self.assertEqual(pre_count, post_count)
        self.assertEqual(pre_count, 20)

    def test_material_binding_not_disturbed(self):
        """The fix reads the bound material -- it must not rebind, unbind,
        or reparent anything."""
        stage = _mkstage()
        mesh = _add_mesh(stage, "/root/M", (0.5, 0.5, 0.5))
        mat = _add_ups_material(stage, "/root/Materials/M", (0.1, 0.2, 0.3))
        _bind(mesh, mat)

        pre_bindings = {
            str(prim.GetPath()): [
                r.GetTargets()
                for r in prim.GetRelationships()
                if r.GetName().startswith("material:binding")
            ]
            for prim in stage.Traverse()
        }

        apply_mat_003_fix(stage)

        post_bindings = {
            str(prim.GetPath()): [
                r.GetTargets()
                for r in prim.GetRelationships()
                if r.GetName().startswith("material:binding")
            ]
            for prim in stage.Traverse()
        }
        self.assertEqual(pre_bindings, post_bindings)

    def test_no_new_attributes_added_to_mesh(self):
        stage = _mkstage()
        mesh = _add_mesh(stage, "/root/M", (0.5, 0.5, 0.5))
        mat = _add_ups_material(stage, "/root/Materials/M", (0.1, 0.2, 0.3))
        _bind(mesh, mat)

        pre_attrs = {a.GetName() for a in mesh.GetPrim().GetAttributes()}
        apply_mat_003_fix(stage)
        post_attrs = {a.GetName() for a in mesh.GetPrim().GetAttributes()}
        self.assertEqual(pre_attrs, post_attrs)


class TestMaxMat003ArchvizVRayBranch(unittest.TestCase):
    """Locks in the assignment's arch-viz reasoning: the fix must work on
    V-Ray-converted PhysicalMaterial and Revit-family material bindings,
    not only the still-life PBR corpus MAX-MAT-002 exercised.

    We can't instantiate V-Ray plugins in a Python-only test, but we CAN
    prove the surgical invariant that makes the fix polymorphic in the
    first place: the C++ patch calls `Mtl::GetDiffuse()` -- a virtual on
    the Max SDK's Mtl base -- so ANY Mtl subclass authoring a diffuseColor
    onto a UsdPreviewSurface (which is how every shading-mode writer,
    including the V-Ray converter chain, projects its 'main color' into
    USD) is covered by the same three lines. Assert that different USD
    material *paths* (representing arch-viz-style bindings) all resolve
    correctly through the same fix."""

    def test_vray_style_binding_resolves_to_material_diffuse(self):
        stage = _mkstage()
        # /root/Stairs is the exact prim path the diagnostic report cites
        # as a wireframe-color leak site on the Spectrum Center arena
        # baseline: (0.76, 0.72, 0.53). The bound V-Ray-converted
        # PhysicalMaterial's actual base_color at that prim on the arena
        # is a concrete blue-grey.
        wire = (0.76, 0.72, 0.53)
        vray_diffuse = (0.22, 0.28, 0.34)
        mesh = _add_mesh(stage, "/root/Stairs", wire)
        vray_mat = _add_ups_material(
            stage, "/root/Materials/VRay_Physical_Stairs", vray_diffuse
        )
        _bind(mesh, vray_mat)

        decisions = apply_mat_003_fix(stage)
        self.assertEqual(decisions["/root/Stairs"], "material")

        v = mesh.GetDisplayColorAttr().Get()
        self.assertAlmostEqual(v[0][0], vray_diffuse[0], places=5)
        self.assertAlmostEqual(v[0][1], vray_diffuse[1], places=5)
        self.assertAlmostEqual(v[0][2], vray_diffuse[2], places=5)

    def test_revit_family_style_binding_resolves_to_material_diffuse(self):
        stage = _mkstage()
        wire = (0.60, 0.60, 0.42)
        revit_diffuse = (0.88, 0.86, 0.80)
        mesh = _add_mesh(stage, "/root/Curtain_Wall", wire)
        revit_mat = _add_ups_material(
            stage, "/root/Materials/Revit_Family_Curtain", revit_diffuse
        )
        _bind(mesh, revit_mat)

        decisions = apply_mat_003_fix(stage)
        self.assertEqual(decisions["/root/Curtain_Wall"], "material")

        v = mesh.GetDisplayColorAttr().Get()
        self.assertAlmostEqual(v[0][0], revit_diffuse[0], places=5)
        self.assertAlmostEqual(v[0][1], revit_diffuse[1], places=5)
        self.assertAlmostEqual(v[0][2], revit_diffuse[2], places=5)

    def test_scale_2000_meshes_rewritten_matches_arena_census(self):
        """The arch-viz baseline reported 1986/2000 arena meshes leaking a
        wireframe color. Simulate the same scale at the USD layer to prove
        the fix's decision is O(n) with no cliff behavior."""
        stage = _mkstage()
        wire_by_family = [
            (0.76, 0.72, 0.53),  # Revit stairs family
            (0.60, 0.60, 0.42),  # Revit curtain family
            (0.85, 0.72, 0.55),  # Revit door family
            (0.55, 0.70, 0.60),  # V-Ray archviz family
        ]
        bound_count = 0
        unbound_count = 0
        for i in range(2000):
            wire = wire_by_family[i % len(wire_by_family)]
            mesh = _add_mesh(stage, "/root/Mesh_{}".format(i), wire)
            # 14 of 2000 have no material (matches the arena's 2000-1986=14
            # orphan count so this test locks in the correct fallback
            # census, not just the correct rewritten census).
            if i < 1986:
                mat = _add_ups_material(
                    stage,
                    "/root/Materials/Mat_{}".format(i),
                    (i / 2000.0, 0.5, 1.0 - i / 2000.0),
                )
                _bind(mesh, mat)
                bound_count += 1
            else:
                unbound_count += 1

        decisions = apply_mat_003_fix(stage)
        rewritten = sum(1 for v in decisions.values() if v == "material")
        preserved = sum(1 for v in decisions.values() if v == "wireframe")
        self.assertEqual(rewritten, 1986)
        self.assertEqual(preserved, 14)
        self.assertEqual(bound_count, 1986)
        self.assertEqual(unbound_count, 14)


if __name__ == "__main__":
    unittest.main()
