# Copyright 2026 Miris Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
"""
MAX-MTLX-002 — USD-layer structural mirror of the C++ fix in
`src/translators/MtlxShaderWriter.cpp::_PruneDanglingNodeGraphOutputs`.

Once MAX-MTLX-001 has wired every Bitmap-backed input into an
ND_tiledimage_*, the MaterialX writer still leaves some ND_standard_surface
inputs referencing a NodeGraph output whose interior source
`MtlxIOUtil.ExportMtlxString` dropped and no Bitmap map slot exists to
restore. Baseline on the Spectrum Center arena, per
`03_ng_dangling.txt` in pipeline run `0b6d5e38`:

    74  base_color
    28  specular_roughness
     7  opacity
     7  specular_color
     1  transmission_color
    ---
   117 total dangling NG outputs across 81 distinct materials
   107 remaining after MAX-MTLX-001's 6-material sample enrichment
   (arena-wide MAX-MTLX-001 numbers pending a Windows-box arena re-export)

Karma / Hydra evaluates a connection to a source-less NodeGraph output as
zero, so every affected material renders black-on-that-input even though
the artist authored a solid color on the Max side. The C++ fix walks the
shader inputs, drops the NG reference on any that still resolve to a
dangling output, and sets a constant value read from the Max material's
PhysicalMaterial / OpenPBR property. The now-orphaned NG output is also
removed so the exported USD does not carry declared-but-unused ports.

This test locks in the fix's *observable* at the USD layer without needing
a rebuilt maxUsd plugin: it constructs a defect fixture (an
ND_standard_surface whose `base_color`, `specular_roughness`, and
`specular_color` inputs reference dangling NG outputs), runs a Python
mirror of the pruning logic against the USD stage, and asserts:

    1. Every previously-dangling connection is gone.
    2. The constant value from the Max material appears on the shader
       input directly.
    3. The orphaned NG output is removed from the NodeGraph.
    4. Bitmap-wired inputs (MAX-MTLX-001) survive untouched.

The Python mirror is a validator, not a shipping code path. The C++ fix
upholds the same invariants by mutating the in-memory MaterialX::Document
*before* the walker (`_AddDependentNodes`) touches it, so the effect is
observed on the exported USD.
"""
import argparse
import os
import sys
import unittest

from pxr import Sdf, Usd, UsdShade


# Same NDS_INPUT_MAP shape as test_miris_max_mtlx_001.py's for consistency,
# subset to the inputs that appear in MAX-MTLX-002's dangling-output census.
CONSTANT_INPUT_TYPES = {
    "base_color":         Sdf.ValueTypeNames.Color3f,
    "specular_roughness": Sdf.ValueTypeNames.Float,
    "specular_color":     Sdf.ValueTypeNames.Color3f,
    "transmission_color": Sdf.ValueTypeNames.Color3f,
    "emission_color":     Sdf.ValueTypeNames.Color3f,
    "metalness":          Sdf.ValueTypeNames.Float,
    "opacity":            Sdf.ValueTypeNames.Float,
    "transmission":       Sdf.ValueTypeNames.Float,
}


def _resolve_connection(stage, attr):
    """Return (source_prim, source_output_name) for a connected attribute,
    or (None, None) if the attribute has no connections. Mirrors the helper
    of the same name in test_miris_max_mtlx_001.py."""
    conns = attr.GetConnections()
    if not conns:
        return None, None
    src_path = conns[0]
    if src_path.IsPropertyPath():
        prim = stage.GetPrimAtPath(src_path.GetPrimPath())
        prop_name = src_path.name
        if prop_name.startswith("outputs:"):
            prop_name = prop_name[len("outputs:"):]
        return prim, prop_name
    return None, None


def _nodegraph_output_has_source(nodegraph, output_name):
    """True iff the named NG output has a connectedSource on its attribute."""
    out = nodegraph.GetOutput(output_name)
    if not out:
        return False
    return bool(out.GetConnectedSource())


def prune_dangling_nodegraph_outputs(stage, constant_manifest):
    """
    Apply the MAX-MTLX-002 fix at the USD layer.

    `constant_manifest` maps material prim path (str) -> dict of
    {mtlx_input_name: value} where the value is a triple for color3
    inputs or a scalar for float inputs. The function walks each
    mtlx-linked material, finds ND_standard_surface shaders with inputs
    connected to a source-less NodeGraph output, breaks the connection,
    authors the constant value directly on the shader input, and removes
    the orphaned NG output.

    Returns a dict of {material_path: count_of_prunes_by_this_call}.
    """
    prunes = {}
    for mat_path, constants in constant_manifest.items():
        mat_prim = stage.GetPrimAtPath(mat_path)
        if not mat_prim or not mat_prim.IsA(UsdShade.Material):
            continue

        # Find the ND_standard_surface shader (may be nested under
        # `MaterialX/` for dual-target exports).
        std_shader = None
        for child in mat_prim.GetChildren():
            if child.IsA(UsdShade.Shader):
                shader = UsdShade.Shader(child)
                id_attr = shader.GetIdAttr()
                if id_attr and id_attr.Get() == "ND_standard_surface_surfaceshader":
                    std_shader = shader
                    break
            for grand in child.GetChildren():
                if grand.IsA(UsdShade.Shader):
                    shader = UsdShade.Shader(grand)
                    id_attr = shader.GetIdAttr()
                    if id_attr and id_attr.Get() == "ND_standard_surface_surfaceshader":
                        std_shader = shader
                        break
            if std_shader:
                break
        if not std_shader:
            continue

        std_prim = std_shader.GetPrim()
        # Locate the sibling NodeGraph.
        ng = None
        for sibling in std_prim.GetParent().GetChildren():
            if sibling.IsA(UsdShade.NodeGraph):
                ng = UsdShade.NodeGraph(sibling)
                break

        count = 0
        for input_name in list(CONSTANT_INPUT_TYPES.keys()):
            std_in = std_shader.GetInput(input_name)
            if not std_in:
                continue
            # Only consider inputs currently connected. A bare-value input
            # is already correctly authored and needs no fix.
            conns = std_in.GetAttr().GetConnections()
            if not conns:
                continue

            # Is the connection to a source-less NG output?
            src_prim, out_name = _resolve_connection(stage, std_in.GetAttr())
            if not src_prim or not src_prim.IsA(UsdShade.NodeGraph):
                continue
            src_ng = UsdShade.NodeGraph(src_prim)
            if _nodegraph_output_has_source(src_ng, out_name):
                # Not dangling — MAX-MTLX-001 or the exporter wired it.
                continue

            # Break the connection. Author-time equivalent of the C++
            # removeAttribute("nodegraph") / removeAttribute("output").
            std_in.GetAttr().ClearConnections()

            # Restore the constant value from the manifest.
            const_val = constants.get(input_name)
            if const_val is not None:
                if input_name.endswith("_color") or input_name == "base_color":
                    # Color3f — expect a 3-tuple.
                    std_in.Set(tuple(const_val))
                else:
                    std_in.Set(float(const_val))

            # Remove the orphaned NG output.
            if src_ng:
                out = src_ng.GetOutput(out_name)
                if out:
                    src_ng.GetPrim().RemoveProperty(out.GetAttr().GetName())

            count += 1

        if count:
            prunes[mat_path] = count
    return prunes


def count_dangling_nodegraph_outputs(stage):
    """Count NodeGraph outputs across the stage that have no connectedSource."""
    total = 0
    for prim in stage.Traverse():
        if not prim.IsA(UsdShade.NodeGraph):
            continue
        ng = UsdShade.NodeGraph(prim)
        for out in ng.GetOutputs():
            if not out.GetConnectedSource():
                total += 1
    return total


def _build_defect_fixture_stage():
    """
    Build a minimal in-memory USD reproducing the MAX-MTLX-002 defect
    pattern for a material with:

      - a solid-color `base_color` (74/117 dangling outputs in the arena
        baseline)
      - a solid `specular_roughness` (28/117)
      - a solid `specular_color` (7/117)
      - and — as a MAX-MTLX-001 co-tenant that must survive the prune
        untouched — a Bitmap-wired `normal` routed through
        ND_normalmap_float + ND_tiledimage_vector3.

    The union covers every shader-input kind in the census plus the
    "already-wired" case that MAX-MTLX-002 must not disturb.
    """
    stage = Usd.Stage.CreateInMemory()
    stage.DefinePrim("/root", "Xform")

    mat_path = "/root/mtl/DEFECT_MAT"
    mat = UsdShade.Material.Define(stage, mat_path)
    ng = UsdShade.NodeGraph.Define(stage, f"{mat_path}/NG_DEFECT_MAT")

    # Dangling NG outputs — declared but source-less.
    ng.CreateOutput("base_color_output", Sdf.ValueTypeNames.Color3f)
    ng.CreateOutput("specular_roughness_output", Sdf.ValueTypeNames.Float)
    ng.CreateOutput("specular_color_output", Sdf.ValueTypeNames.Color3f)
    # Not dangling — a MAX-MTLX-001-style wired output. This must survive.
    normal_output = ng.CreateOutput("normal_output", Sdf.ValueTypeNames.Float3)
    nm = UsdShade.Shader.Define(stage, f"{ng.GetPath()}/nm")
    nm.CreateIdAttr("ND_normalmap_float")
    img = UsdShade.Shader.Define(stage, f"{ng.GetPath()}/img_normal")
    img.CreateIdAttr("ND_tiledimage_vector3")
    img_file = img.CreateInput("file", Sdf.ValueTypeNames.Asset)
    img_file.Set("textures/DEFECT_normal.png")
    img_out = img.CreateOutput("out", Sdf.ValueTypeNames.Float3)
    nm_in = nm.CreateInput("in", Sdf.ValueTypeNames.Float3)
    nm_in.ConnectToSource(img_out)
    nm_out = nm.CreateOutput("out", Sdf.ValueTypeNames.Float3)
    normal_output.ConnectToSource(nm_out)

    # ND_standard_surface — inputs reference the NG outputs.
    std = UsdShade.Shader.Define(stage, f"{mat_path}/DEFECT_MAT")
    std.CreateIdAttr("ND_standard_surface_surfaceshader")
    for name, sdf_type in [
        ("base_color", Sdf.ValueTypeNames.Color3f),
        ("specular_roughness", Sdf.ValueTypeNames.Float),
        ("specular_color", Sdf.ValueTypeNames.Color3f),
        ("normal", Sdf.ValueTypeNames.Float3),
    ]:
        inp = std.CreateInput(name, sdf_type)
        inp.ConnectToSource(ng.GetOutput(name + "_output"))
    std_out = std.CreateOutput("surface", Sdf.ValueTypeNames.Token)
    mat.CreateSurfaceOutput("mtlx").ConnectToSource(std_out)

    return stage, mat_path


class TestMaxMtlx002Prune(unittest.TestCase):
    """USD-layer structural mirror of the MtlxShaderWriter C++ prune."""

    def test_pre_fix_stage_matches_baseline_defect(self):
        """The synthetic fixture reproduces MAX-MTLX-002's baseline:
        3 dangling NG outputs (base_color, specular_roughness,
        specular_color) plus 1 that's already wired (normal via
        MAX-MTLX-001)."""
        stage, mat_path = _build_defect_fixture_stage()

        # Exactly 3 dangling — the normal_output IS sourced via nm.
        self.assertEqual(count_dangling_nodegraph_outputs(stage), 3)

        # The shader's base_color / specular_roughness / specular_color
        # all point at NG outputs with no source.
        std = UsdShade.Shader(stage.GetPrimAtPath(f"{mat_path}/DEFECT_MAT"))
        ng = UsdShade.NodeGraph(
            stage.GetPrimAtPath(f"{mat_path}/NG_DEFECT_MAT"))
        for name in ("base_color", "specular_roughness", "specular_color"):
            inp = std.GetInput(name)
            self.assertTrue(bool(inp.GetAttr().GetConnections()))
            self.assertFalse(
                _nodegraph_output_has_source(ng, name + "_output"),
                f"{name}_output should start out dangling")

        # The normal input's NG output IS sourced (MAX-MTLX-001 co-tenant).
        self.assertTrue(_nodegraph_output_has_source(ng, "normal_output"))

    def test_prune_breaks_connections_and_sets_constants(self):
        """After pruning, every previously-dangling connection is gone and
        the constant value read from the Max side lives directly on the
        shader input."""
        stage, mat_path = _build_defect_fixture_stage()

        manifest = {
            mat_path: {
                "base_color": (0.4, 0.7, 0.2),        # a distinctive green
                "specular_roughness": 0.35,
                "specular_color": (0.1, 0.1, 0.1),   # slate specular
            }
        }
        result = prune_dangling_nodegraph_outputs(stage, manifest)
        self.assertEqual(result.get(mat_path), 3)

        # Zero dangling NG outputs remain — the orphaned outputs were
        # removed AND normal_output is still wired.
        self.assertEqual(count_dangling_nodegraph_outputs(stage), 0)

        std = UsdShade.Shader(stage.GetPrimAtPath(f"{mat_path}/DEFECT_MAT"))
        # Connections dropped, values set.
        for name, expected in (
            ("base_color", (0.4, 0.7, 0.2)),
            ("specular_roughness", 0.35),
            ("specular_color", (0.1, 0.1, 0.1)),
        ):
            inp = std.GetInput(name)
            self.assertEqual(
                inp.GetAttr().GetConnections(), [],
                f"{name} should have no connections after prune")
            val = inp.Get()
            if isinstance(expected, tuple):
                self.assertAlmostEqual(val[0], expected[0], places=5)
                self.assertAlmostEqual(val[1], expected[1], places=5)
                self.assertAlmostEqual(val[2], expected[2], places=5)
            else:
                self.assertAlmostEqual(val, expected, places=5)

    def test_bitmap_wired_input_survives_prune(self):
        """The MAX-MTLX-001 normal input (already sourced) must NOT be
        disturbed — this is the co-tenant regression check that keeps a
        fix from stomping on the other fix in the same writer."""
        stage, mat_path = _build_defect_fixture_stage()

        manifest = {
            mat_path: {
                "base_color": (0.4, 0.7, 0.2),
                "specular_roughness": 0.35,
                "specular_color": (0.1, 0.1, 0.1),
            }
        }
        prune_dangling_nodegraph_outputs(stage, manifest)

        # The normal_output is still wired through ND_normalmap_float.
        ng = UsdShade.NodeGraph(
            stage.GetPrimAtPath(f"{mat_path}/NG_DEFECT_MAT"))
        self.assertTrue(_nodegraph_output_has_source(ng, "normal_output"))

        # The ND_tiledimage_vector3 shader is still present.
        img = stage.GetPrimAtPath(f"{ng.GetPath()}/img_normal")
        self.assertTrue(img.IsValid())
        self.assertEqual(
            UsdShade.Shader(img).GetIdAttr().Get(), "ND_tiledimage_vector3")

        # And the ND_standard_surface's normal input still connects to
        # NG.normal_output.
        std = UsdShade.Shader(stage.GetPrimAtPath(f"{mat_path}/DEFECT_MAT"))
        normal_in = std.GetInput("normal")
        conns = normal_in.GetAttr().GetConnections()
        self.assertEqual(len(conns), 1)
        self.assertEqual(str(conns[0]),
                         f"{ng.GetPath()}.outputs:normal_output")

    def test_orphaned_outputs_removed_from_nodegraph(self):
        """The three dangling NG outputs are gone from the NodeGraph after
        pruning — no declared-but-unused ports left in the exported USD."""
        stage, mat_path = _build_defect_fixture_stage()

        manifest = {
            mat_path: {
                "base_color": (0.4, 0.7, 0.2),
                "specular_roughness": 0.35,
                "specular_color": (0.1, 0.1, 0.1),
            }
        }
        prune_dangling_nodegraph_outputs(stage, manifest)

        ng = UsdShade.NodeGraph(
            stage.GetPrimAtPath(f"{mat_path}/NG_DEFECT_MAT"))
        remaining = sorted(o.GetBaseName() for o in ng.GetOutputs())
        self.assertEqual(remaining, ["normal_output"])

    def test_no_op_when_shader_input_already_sourced(self):
        """If the shader input's NG output IS sourced (e.g. MAX-MTLX-001
        wired it), the prune must not touch it."""
        stage, mat_path = _build_defect_fixture_stage()

        # Pre-wire base_color through a tiledimage (MAX-MTLX-001-style).
        ng = UsdShade.NodeGraph(
            stage.GetPrimAtPath(f"{mat_path}/NG_DEFECT_MAT"))
        img = UsdShade.Shader.Define(stage, f"{ng.GetPath()}/img_base_color")
        img.CreateIdAttr("ND_tiledimage_color3")
        img_out = img.CreateOutput("out", Sdf.ValueTypeNames.Color3f)
        ng.GetOutput("base_color_output").ConnectToSource(img_out)

        manifest = {
            mat_path: {
                "base_color": (0.4, 0.7, 0.2),      # should NOT be applied
                "specular_roughness": 0.35,          # should be applied
                "specular_color": (0.1, 0.1, 0.1),  # should be applied
            }
        }
        result = prune_dangling_nodegraph_outputs(stage, manifest)
        # Only 2 prunes: base_color survived because it's wired.
        self.assertEqual(result.get(mat_path), 2)

        std = UsdShade.Shader(stage.GetPrimAtPath(f"{mat_path}/DEFECT_MAT"))
        base = std.GetInput("base_color")
        conns = base.GetAttr().GetConnections()
        self.assertEqual(len(conns), 1,
                         "base_color must remain connected to the sourced NG output")
        self.assertIsNone(
            base.Get(),
            "base_color must not have been overwritten with the constant")


def _load_constants_manifest(path):
    """Load a JSON manifest of {mat_path: {mtlx_input: value}}."""
    import json
    with open(path, "r") as f:
        return json.load(f)


def _summarize_stage(stage):
    """Report per-input dangling counts on a real stage."""
    from collections import Counter
    dangling = Counter()
    for prim in stage.Traverse():
        if not prim.IsA(UsdShade.Shader):
            continue
        shader = UsdShade.Shader(prim)
        id_attr = shader.GetIdAttr()
        if not id_attr or id_attr.Get() != "ND_standard_surface_surfaceshader":
            continue
        for inp in shader.GetInputs():
            src_prim, out_name = _resolve_connection(
                prim.GetStage(), inp.GetAttr())
            if not src_prim or not src_prim.IsA(UsdShade.NodeGraph):
                continue
            src_ng = UsdShade.NodeGraph(src_prim)
            if not _nodegraph_output_has_source(src_ng, out_name):
                dangling[inp.GetBaseName()] += 1
    return dangling


def main_cli():
    """CLI: report and (optionally) prune dangling NG outputs on a real
    USD stage.

    Usage:
        hython test_miris_max_mtlx_002.py --stage in.usda \\
            [--manifest constants.json] [--out pruned.usda]
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", required=True, help="Input .usda path")
    parser.add_argument(
        "--manifest",
        help="JSON: {mat_path: {mtlx_input: value}}. If omitted, only "
             "the pre-existing dangling census is reported.")
    parser.add_argument("--out", help="Output .usda (default: stdout summary)")
    args = parser.parse_args()

    stage = Usd.Stage.Open(args.stage)
    if not stage:
        print(f"Failed to open {args.stage}", file=sys.stderr)
        return 2

    pre = _summarize_stage(stage)
    print(f"Pre-prune dangling by input: {dict(pre)}")
    print(f"Pre-prune total NG outputs with no source: "
          f"{count_dangling_nodegraph_outputs(stage)}")

    if args.manifest:
        manifest = _load_constants_manifest(args.manifest)
        prunes = prune_dangling_nodegraph_outputs(stage, manifest)
        print(f"Prune applied: {sum(prunes.values())} connections pruned "
              f"across {len(prunes)} materials")
        post = _summarize_stage(stage)
        print(f"Post-prune dangling by input: {dict(post)}")
        print(f"Post-prune total NG outputs with no source: "
              f"{count_dangling_nodegraph_outputs(stage)}")

    if args.out:
        stage.GetRootLayer().Export(args.out)
        print(f"Wrote {args.out}")
    return 0


if __name__ == "__main__":
    if "--stage" in sys.argv:
        sys.exit(main_cli())
    else:
        unittest.main()
