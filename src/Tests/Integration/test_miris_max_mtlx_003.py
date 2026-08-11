# Copyright 2026 Miris Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
"""
MAX-MTLX-003 — USD-layer structural mirror of the C++ fix in
`src/translators/MtlxShaderWriter.cpp::_WireDanglingNormalmapInputs`.

The MaterialX writer scaffolds an `ND_normalmap_float` sub-shader whenever
the source Physical/VRayMtl material carries a normal map on the normal
slot. `MtlxIOUtil.ExportMtlxString` reliably emits both the NG's
`normal_output` (wired to the normalmap's `out`) and the `ND_normalmap_float`
node itself, but drops the ND_tiledimage_vector3 that should feed the
normalmap's `in` input. MAX-MTLX-001 only re-wires the tiledimage → normalmap
when the SHADER'S `normal` input is itself dangling; it skips the case where
the NG's `normal_output` DOES connect to the normalmap, but the normalmap's
own `in` port is the dangling one.

Baseline diagnostic from the Spectrum Center arena
(`08_wrap_up.txt`, pipeline `0b6d5e38`):

    --ND_normalmap_float inputs source--
    {'ND_tiledimage_vector3': 4}
    ND_normalmap_float with constant/default in: 41

So 4 of 45 scaffolded normalmap nodes have `in` properly wired; the other
41 fall through to the port default (i.e. no normal mapping). Downstream
Karma / Hydra evaluates the normalmap with a default-zeroed tangent-space
vector, so those materials render as if they had no normal detail at all.

The C++ fix walks every normalmap-class node in every NodeGraph belonging
to the current shader, checks whether the `in` input is dangling, and — if
`discoverMaxMtlxTexmaps` finds a normal Bitmap slot on the Max material —
injects the ND_tiledimage_vector3 and wires the normalmap's `in` to it.
Sits after `_EnrichMtlxDocFromMaxMaterial` (MAX-MTLX-001) and
`_PruneDanglingNodeGraphOutputs` (MAX-MTLX-002) so it operates on the
final post-enrichment doc.

This test locks in the fix's *observable* at the USD layer without needing
a rebuilt maxUsd plugin: it constructs a defect fixture (an
ND_standard_surface whose `normal` input is wired to
NG.normal_output → ND_normalmap_float, but the normalmap's `in` is
dangling), runs a Python mirror of the wiring logic against the USD stage,
and asserts:

    1. Every previously-dangling normalmap.in now sources an
       ND_tiledimage_vector3.
    2. The tiledimage carries the file path we discovered on the Max side.
    3. Materials that ALREADY had normalmap.in wired (the 4-of-45 case)
       survive untouched.
    4. Materials whose Max side has NO normal Bitmap slot leave the
       normalmap.in dangling (no bogus wire authored).
    5. Non-normal branches of the shader network (MAX-MTLX-001 + MAX-MTLX-002
       co-tenants) are unaffected.

The Python mirror is a validator, not a shipping code path. The C++ fix
upholds the same invariants by mutating the in-memory MaterialX::Document
before the walker (`_AddDependentNodes`) touches it.
"""
import argparse
import os
import sys
import unittest

from pxr import Sdf, Usd, UsdShade


def _resolve_connection(stage, attr):
    """Return (source_prim, source_output_name) for a connected attribute,
    or (None, None) if the attribute has no connections."""
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


def _nodegraph_output_source(nodegraph, output_name):
    """Return the shader Output feeding this NodeGraph output, or None."""
    out = nodegraph.GetOutput(output_name)
    if not out:
        return None
    stage = nodegraph.GetPrim().GetStage()
    prim, out_name = _resolve_connection(stage, out.GetAttr())
    if not prim or not out_name:
        return None
    if prim.IsA(UsdShade.Shader):
        return UsdShade.Shader(prim).GetOutput(out_name)
    if prim.IsA(UsdShade.NodeGraph):
        return UsdShade.NodeGraph(prim).GetOutput(out_name)
    return None


def _find_normalmap_shaders_in_nodegraph(nodegraph):
    """Return every ND_normalmap_float shader parented under this
    NodeGraph. Multiple normalmaps in one NG is unusual but not disallowed;
    keep the API generic so the mirror matches the C++ walk."""
    result = []
    for child in nodegraph.GetPrim().GetChildren():
        if not child.IsA(UsdShade.Shader):
            continue
        shader = UsdShade.Shader(child)
        id_attr = shader.GetIdAttr()
        if id_attr and id_attr.Get() == "ND_normalmap_float":
            result.append(shader)
    return result


def _normalmap_in_is_dangling(nm_shader):
    """True iff the normalmap's `in` input has no source AND no value
    string. Matches the C++ `_WireDanglingNormalmapInputs` predicate."""
    nm_in = nm_shader.GetInput("in")
    if not nm_in:
        return True
    if nm_in.GetAttr().GetConnections():
        return False
    val = nm_in.Get()
    return val is None


def wire_dangling_normalmap_inputs(stage, normal_manifest):
    """
    Apply the MAX-MTLX-003 fix at the USD layer.

    `normal_manifest` maps material prim path (str) -> {"normal": file_path}
    (or an empty / normal-less dict for materials whose Max side has no
    discoverable normal Bitmap; those must be left with a dangling
    normalmap.in). The function walks each mtlx-linked material, finds any
    ND_normalmap_float whose `in` input is dangling, and injects an
    ND_tiledimage_vector3 wired into it.

    Returns a dict of {material_path: count_of_wires_by_this_call}.
    """
    wires = {}
    for mat_path, slot_files in normal_manifest.items():
        mat_prim = stage.GetPrimAtPath(mat_path)
        if not mat_prim or not mat_prim.IsA(UsdShade.Material):
            continue

        # Enumerate every NodeGraph that lives under this material — the
        # MaterialX / UsdPreviewSurface dual-target authoring places them
        # in different scopes, so we walk descendants rather than only
        # immediate children.
        for prim in Usd.PrimRange(mat_prim):
            if not prim.IsA(UsdShade.NodeGraph):
                continue
            ng = UsdShade.NodeGraph(prim)
            for nm_shader in _find_normalmap_shaders_in_nodegraph(ng):
                if not _normalmap_in_is_dangling(nm_shader):
                    continue

                file_path = slot_files.get("normal")
                if not file_path:
                    # No discoverable normal Bitmap on the Max side — leave
                    # the normalmap.in at its port default. Matches the
                    # C++ conservative no-op when `discoverMaxMtlxTexmaps`
                    # returns nothing for the normal slot.
                    continue

                # Reuse an existing img_normal in the same NG if one is
                # there (defensive), otherwise define a fresh
                # ND_tiledimage_vector3.
                img_path = ng.GetPath().AppendChild("img_normal")
                img_prim = stage.GetPrimAtPath(img_path)
                if img_prim and img_prim.IsValid():
                    img = UsdShade.Shader(img_prim)
                else:
                    img = UsdShade.Shader.Define(stage, img_path)
                    img.CreateIdAttr("ND_tiledimage_vector3")
                file_inp = img.GetInput("file")
                if not file_inp:
                    file_inp = img.CreateInput(
                        "file", Sdf.ValueTypeNames.Asset)
                file_inp.Set(file_path)
                img_out = img.GetOutput("out")
                if not img_out:
                    img_out = img.CreateOutput(
                        "out", Sdf.ValueTypeNames.Float3)

                # Wire normalmap.in → img.out.
                nm_in = nm_shader.GetInput("in")
                if not nm_in:
                    nm_in = nm_shader.CreateInput(
                        "in", Sdf.ValueTypeNames.Float3)
                nm_in.ConnectToSource(img_out)

                wires[mat_path] = wires.get(mat_path, 0) + 1
    return wires


def count_dangling_normalmap_inputs(stage):
    """Total number of ND_normalmap_float shaders on the stage whose `in`
    input is dangling. Mirrors the diagnostic report line
    'ND_normalmap_float with constant/default in: 41'."""
    total = 0
    for prim in stage.Traverse():
        if not prim.IsA(UsdShade.Shader):
            continue
        shader = UsdShade.Shader(prim)
        id_attr = shader.GetIdAttr()
        if not id_attr or id_attr.Get() != "ND_normalmap_float":
            continue
        if _normalmap_in_is_dangling(shader):
            total += 1
    return total


def count_normalmap_shaders(stage):
    """Total number of ND_normalmap_float shaders on the stage. This is
    the invariant that MUST NOT change across the fix — the fix wires an
    input, it does not add or remove normalmap nodes."""
    total = 0
    for prim in stage.Traverse():
        if not prim.IsA(UsdShade.Shader):
            continue
        shader = UsdShade.Shader(prim)
        id_attr = shader.GetIdAttr()
        if id_attr and id_attr.Get() == "ND_normalmap_float":
            total += 1
    return total


def _build_defect_fixture_stage():
    """
    Build a minimal in-memory USD reproducing the MAX-MTLX-003 defect
    pattern for two materials:

      - DEFECT_MAT: has an ND_normalmap_float whose `in` is dangling
        (the 41-of-45 case). Also carries a base_color already wired
        through an ND_tiledimage_color3 to prove non-normal branches
        are not disturbed.
      - WIRED_MAT: has an ND_normalmap_float whose `in` is already
        wired to an ND_tiledimage_vector3 (the 4-of-45 case). The fix
        MUST leave it alone.

    Together they cover the two co-tenant scenarios described in the
    baseline diagnostic.
    """
    stage = Usd.Stage.CreateInMemory()
    stage.DefinePrim("/root", "Xform")
    stage.DefinePrim("/root/mtl", "Scope")

    # --- Material 1: dangling normalmap.in ---
    mat_path = "/root/mtl/DEFECT_MAT"
    mat = UsdShade.Material.Define(stage, mat_path)
    ng = UsdShade.NodeGraph.Define(stage, f"{mat_path}/NG_DEFECT_MAT")

    # Baseline: NG.normal_output connects to a normalmap whose `in` is
    # dangling.
    normal_output = ng.CreateOutput(
        "normal_output", Sdf.ValueTypeNames.Float3)
    nm = UsdShade.Shader.Define(stage, f"{ng.GetPath()}/nm")
    nm.CreateIdAttr("ND_normalmap_float")
    nm_out = nm.CreateOutput("out", Sdf.ValueTypeNames.Float3)
    normal_output.ConnectToSource(nm_out)
    # Intentionally NOT creating nm.GetInput("in") — that is the defect.

    # Non-normal branch: base_color already wired through a tiledimage
    # (MAX-MTLX-001 co-tenant). This must not be disturbed by our fix.
    base_output = ng.CreateOutput(
        "base_color_output", Sdf.ValueTypeNames.Color3f)
    base_img = UsdShade.Shader.Define(stage, f"{ng.GetPath()}/img_base_color")
    base_img.CreateIdAttr("ND_tiledimage_color3")
    base_file = base_img.CreateInput("file", Sdf.ValueTypeNames.Asset)
    base_file.Set("textures/DEFECT_base.png")
    base_img_out = base_img.CreateOutput("out", Sdf.ValueTypeNames.Color3f)
    base_output.ConnectToSource(base_img_out)

    # ND_standard_surface referencing the NG outputs.
    std = UsdShade.Shader.Define(stage, f"{mat_path}/DEFECT_MAT")
    std.CreateIdAttr("ND_standard_surface_surfaceshader")
    normal_in = std.CreateInput("normal", Sdf.ValueTypeNames.Float3)
    normal_in.ConnectToSource(normal_output)
    base_in = std.CreateInput("base_color", Sdf.ValueTypeNames.Color3f)
    base_in.ConnectToSource(base_output)
    std_out = std.CreateOutput("surface", Sdf.ValueTypeNames.Token)
    mat.CreateSurfaceOutput("mtlx").ConnectToSource(std_out)

    # --- Material 2: normalmap.in already wired ---
    wired_path = "/root/mtl/WIRED_MAT"
    wired_mat = UsdShade.Material.Define(stage, wired_path)
    wired_ng = UsdShade.NodeGraph.Define(
        stage, f"{wired_path}/NG_WIRED_MAT")
    wired_normal_output = wired_ng.CreateOutput(
        "normal_output", Sdf.ValueTypeNames.Float3)
    wired_nm = UsdShade.Shader.Define(stage, f"{wired_ng.GetPath()}/nm")
    wired_nm.CreateIdAttr("ND_normalmap_float")
    wired_nm_out = wired_nm.CreateOutput("out", Sdf.ValueTypeNames.Float3)
    wired_normal_output.ConnectToSource(wired_nm_out)
    # Pre-wired ND_tiledimage_vector3 → nm.in — the 4-of-45 case.
    wired_img = UsdShade.Shader.Define(
        stage, f"{wired_ng.GetPath()}/img_normal_preexisting")
    wired_img.CreateIdAttr("ND_tiledimage_vector3")
    wired_img_file = wired_img.CreateInput("file", Sdf.ValueTypeNames.Asset)
    wired_img_file.Set("textures/WIRED_normal.png")
    wired_img_out = wired_img.CreateOutput(
        "out", Sdf.ValueTypeNames.Float3)
    wired_nm_in = wired_nm.CreateInput("in", Sdf.ValueTypeNames.Float3)
    wired_nm_in.ConnectToSource(wired_img_out)

    wired_std = UsdShade.Shader.Define(stage, f"{wired_path}/WIRED_MAT")
    wired_std.CreateIdAttr("ND_standard_surface_surfaceshader")
    wired_normal_in = wired_std.CreateInput(
        "normal", Sdf.ValueTypeNames.Float3)
    wired_normal_in.ConnectToSource(wired_normal_output)
    wired_std_out = wired_std.CreateOutput(
        "surface", Sdf.ValueTypeNames.Token)
    wired_mat.CreateSurfaceOutput("mtlx").ConnectToSource(wired_std_out)

    return stage, mat_path, wired_path


class TestMaxMtlx003NormalmapInWiring(unittest.TestCase):
    """USD-layer structural mirror of the MtlxShaderWriter C++ normalmap
    input wiring."""

    def test_pre_fix_stage_matches_baseline_defect(self):
        """The fixture reproduces the 41/45 baseline: DEFECT_MAT's
        normalmap.in is dangling; WIRED_MAT's is already sourced."""
        stage, mat_path, wired_path = _build_defect_fixture_stage()

        self.assertEqual(count_normalmap_shaders(stage), 2)
        self.assertEqual(count_dangling_normalmap_inputs(stage), 1)

        # DEFECT_MAT.nm.in has no connections and no value.
        defect_nm = UsdShade.Shader(
            stage.GetPrimAtPath(f"{mat_path}/NG_DEFECT_MAT/nm"))
        self.assertTrue(_normalmap_in_is_dangling(defect_nm))

        # WIRED_MAT.nm.in already sources ND_tiledimage_vector3.
        wired_nm = UsdShade.Shader(
            stage.GetPrimAtPath(f"{wired_path}/NG_WIRED_MAT/nm"))
        self.assertFalse(_normalmap_in_is_dangling(wired_nm))

    def test_wire_dangling_normalmap_in_from_manifest(self):
        """Applying the fix wires DEFECT_MAT's normalmap.in from the
        manifest's file path. Post-fix, zero dangling normalmap.in
        inputs remain and the normalmap count is unchanged (invariant:
        the fix wires an input, it does not add or remove
        ND_normalmap_float nodes)."""
        stage, mat_path, wired_path = _build_defect_fixture_stage()

        manifest = {
            mat_path: {"normal": "textures/DEFECT_normal.png"},
            wired_path: {"normal": "textures/WIRED_normal.png"},
        }
        result = wire_dangling_normalmap_inputs(stage, manifest)
        # Exactly one wire authored — WIRED_MAT is skipped because its
        # normalmap.in is already sourced.
        self.assertEqual(result.get(mat_path), 1)
        self.assertNotIn(wired_path, result)

        self.assertEqual(count_dangling_normalmap_inputs(stage), 0)
        self.assertEqual(count_normalmap_shaders(stage), 2)

        # DEFECT_MAT.nm.in now sources an ND_tiledimage_vector3 named
        # img_normal, carrying the file path from the manifest.
        defect_nm = UsdShade.Shader(
            stage.GetPrimAtPath(f"{mat_path}/NG_DEFECT_MAT/nm"))
        nm_in = defect_nm.GetInput("in")
        conns = nm_in.GetAttr().GetConnections()
        self.assertEqual(len(conns), 1)
        src_prim = stage.GetPrimAtPath(conns[0].GetPrimPath())
        self.assertEqual(src_prim.GetName(), "img_normal")
        src_shader = UsdShade.Shader(src_prim)
        self.assertEqual(
            src_shader.GetIdAttr().Get(), "ND_tiledimage_vector3")
        file_val = src_shader.GetInput("file").Get()
        self.assertEqual(file_val.path, "textures/DEFECT_normal.png")

    def test_pre_wired_material_survives_untouched(self):
        """WIRED_MAT's normalmap.in was already sourced from
        img_normal_preexisting — the fix MUST leave it alone (this is the
        4-of-45 co-tenant of the baseline that the fix must not disturb)."""
        stage, mat_path, wired_path = _build_defect_fixture_stage()

        manifest = {
            mat_path: {"normal": "textures/DEFECT_normal.png"},
            wired_path: {"normal": "textures/should-not-clobber.png"},
        }
        wire_dangling_normalmap_inputs(stage, manifest)

        # The pre-existing tiledimage is still there with its original
        # file path.
        wired_img = UsdShade.Shader(stage.GetPrimAtPath(
            f"{wired_path}/NG_WIRED_MAT/img_normal_preexisting"))
        self.assertTrue(wired_img.GetPrim().IsValid())
        file_val = wired_img.GetInput("file").Get()
        self.assertEqual(file_val.path, "textures/WIRED_normal.png")

        # No sneaky `img_normal` sibling was created next to it.
        sneaked = stage.GetPrimAtPath(
            f"{wired_path}/NG_WIRED_MAT/img_normal")
        self.assertFalse(sneaked.IsValid())

        # And normalmap.in still connects to the preexisting shader.
        wired_nm = UsdShade.Shader(
            stage.GetPrimAtPath(f"{wired_path}/NG_WIRED_MAT/nm"))
        nm_in = wired_nm.GetInput("in")
        conns = nm_in.GetAttr().GetConnections()
        self.assertEqual(len(conns), 1)
        self.assertEqual(
            stage.GetPrimAtPath(conns[0].GetPrimPath()).GetName(),
            "img_normal_preexisting")

    def test_no_normal_slot_leaves_normalmap_in_dangling(self):
        """If discoverMaxMtlxTexmaps returns no normal entry for a material
        (the Max side has no normal Bitmap), the fix must NOT invent a
        wire — leaving normalmap.in dangling matches the MAX-MTLX-001
        conservative no-op contract for un-discoverable slots."""
        stage, mat_path, wired_path = _build_defect_fixture_stage()

        # DEFECT_MAT's manifest carries no "normal" key.
        manifest = {mat_path: {}, wired_path: {}}
        result = wire_dangling_normalmap_inputs(stage, manifest)
        self.assertEqual(result, {})

        # normalmap.in stays dangling — the diagnostic-baseline state is
        # preserved when no data is discoverable.
        self.assertEqual(count_dangling_normalmap_inputs(stage), 1)

        # And no img_normal was invented under the NG.
        img = stage.GetPrimAtPath(f"{mat_path}/NG_DEFECT_MAT/img_normal")
        self.assertFalse(img.IsValid())

    def test_base_color_branch_untouched_by_fix(self):
        """The MAX-MTLX-001 co-tenant (base_color already wired through
        img_base_color) survives the MAX-MTLX-003 fix untouched — the
        writer must not stomp on non-normal branches."""
        stage, mat_path, wired_path = _build_defect_fixture_stage()

        manifest = {
            mat_path: {"normal": "textures/DEFECT_normal.png"},
        }
        wire_dangling_normalmap_inputs(stage, manifest)

        ng = UsdShade.NodeGraph(
            stage.GetPrimAtPath(f"{mat_path}/NG_DEFECT_MAT"))
        base_out_src = _nodegraph_output_source(ng, "base_color_output")
        self.assertIsNotNone(base_out_src)
        # Still sourced from img_base_color (ND_tiledimage_color3).
        self.assertEqual(
            base_out_src.GetPrim().GetName(), "img_base_color")


def _load_normal_manifest(path):
    """Load a JSON manifest of {mat_path: {'normal': file_path}}."""
    import json
    with open(path, "r") as f:
        return json.load(f)


def _summarize_stage(stage):
    """Return (normalmap_total, normalmap_dangling) for a real stage."""
    return count_normalmap_shaders(stage), count_dangling_normalmap_inputs(
        stage)


def main_cli():
    """CLI: report and (optionally) wire dangling ND_normalmap_float.in
    inputs on a real USD stage.

    Usage:
        hython test_miris_max_mtlx_003.py --stage in.usda \\
            [--manifest normals.json] [--out wired.usda]
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", required=True, help="Input .usda path")
    parser.add_argument(
        "--manifest",
        help="JSON: {mat_path: {'normal': file_path}}. If omitted, only "
             "the pre-existing dangling normalmap.in census is reported.")
    parser.add_argument("--out", help="Output .usda (default: stdout summary)")
    args = parser.parse_args()

    stage = Usd.Stage.Open(args.stage)
    if not stage:
        print(f"Failed to open {args.stage}", file=sys.stderr)
        return 2

    total, dangling = _summarize_stage(stage)
    print(f"Pre-fix ND_normalmap_float count: {total}, "
          f"of which dangling `in`: {dangling}")

    if args.manifest:
        manifest = _load_normal_manifest(args.manifest)
        wires = wire_dangling_normalmap_inputs(stage, manifest)
        print(f"Fix applied: {sum(wires.values())} normalmap.in inputs "
              f"wired across {len(wires)} materials")
        post_total, post_dangling = _summarize_stage(stage)
        print(f"Post-fix ND_normalmap_float count: {post_total}, "
              f"of which dangling `in`: {post_dangling}")

    if args.out:
        stage.GetRootLayer().Export(args.out)
        print(f"Wrote {args.out}")
    return 0


if __name__ == "__main__":
    if "--stage" in sys.argv:
        sys.exit(main_cli())
    else:
        unittest.main()
