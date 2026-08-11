# Copyright 2026 Miris Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
"""
MAX-MTLX-001 — USD-layer structural mirror of the C++ fix in
`src/translators/MtlxShaderWriter.cpp::_EnrichMtlxDocFromMaxMaterial`.

The MaxUsd MaterialX shader writer used to hand the exporter an in-memory
MaterialX doc where every `NG_<name>` NodeGraph declared its outputs
(`base_color_output`, `specular_roughness_output`, etc.) but contained no
interior `<tiledimage>` nodes — the Bitmap/VRayBitmap map slots dropped
between `MtlxIOUtil.ExportMtlxString` and the C++ walker. Every exported
material ended up with `ND_standard_surface_surfaceshader` + declared but
dangling NodeGraph outputs, so downstream Karma / Hydra rendered a flat
untextured surface.

This test locks in the fix's *observable* at the USD layer without needing
a rebuilt maxUsd plugin: it constructs the pre-fix defect in-memory (a
Material with an ND_standard_surface whose `base_color` input references a
NodeGraph output that has no source, plus an ND_normalmap_float whose `in`
input is dangling), runs the same enrichment logic the C++ patch performs
against the MaterialX::Document, and asserts that the resulting stage has
the expected ND_tiledimage_* USD Shader prims wired through the NodeGraph.

The Python mirror is used as a validator (not a shipping code path) — it
verifies that "if the mtlxDoc contains these tiledimage nodes, the walker
emits these USD shaders", which is the invariant the C++ fix upholds by
INJECTING the mtlxDoc nodes. Same effect, observed at a different layer.
"""
import argparse
import os
import sys
import unittest

from pxr import Sdf, Usd, UsdShade


# ND_standard_surface input name -> (MaterialX type, ND_tiledimage node id)
# Mirrors the slotMap in MtlxShaderWriter.cpp::discoverMaxMtlxTexmapsFn.
NDS_INPUT_MAP = {
    "base_color":         ("color3",  "ND_tiledimage_color3"),
    "specular_roughness": ("float",   "ND_tiledimage_float"),
    "metalness":          ("float",   "ND_tiledimage_float"),
    "emission_color":     ("color3",  "ND_tiledimage_color3"),
    "specular_color":     ("color3",  "ND_tiledimage_color3"),
    "transmission_color": ("color3",  "ND_tiledimage_color3"),
    "opacity":            ("float",   "ND_tiledimage_float"),
    # `normal` is special-cased: it always routes through ND_normalmap_float,
    # whose `in` input is the vector3 tiledimage.
    "normal":             ("vector3", "ND_tiledimage_vector3"),
}

TYPE_TO_SDF = {
    "color3":  Sdf.ValueTypeNames.Color3f,
    "float":   Sdf.ValueTypeNames.Float,
    "vector3": Sdf.ValueTypeNames.Float3,
}


def _resolve_connection(stage, attr):
    """Return (source_prim, source_output_name) for a connected attribute,
    or (None, None) if the attribute has no connections."""
    conns = attr.GetConnections()
    if not conns:
        return None, None
    src_path = conns[0]
    # source path is something like /root/mtl/M/NG_M.outputs:base_color_output
    if src_path.IsPropertyPath():
        prim = stage.GetPrimAtPath(src_path.GetPrimPath())
        prop_name = src_path.name
        if prop_name.startswith("outputs:"):
            prop_name = prop_name[len("outputs:"):]
        return prim, prop_name
    return None, None


def _shader_input_source(shader, input_name):
    """Return the UsdShadeOutput this input is connected to, or None."""
    inp = shader.GetInput(input_name)
    if not inp:
        return None
    stage = shader.GetPrim().GetStage()
    prim, out_name = _resolve_connection(stage, inp.GetAttr())
    if not prim or not out_name:
        return None
    if prim.IsA(UsdShade.NodeGraph):
        return UsdShade.NodeGraph(prim).GetOutput(out_name)
    if prim.IsA(UsdShade.Shader):
        return UsdShade.Shader(prim).GetOutput(out_name)
    return None


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


def enrich_stage_with_tiledimage(stage, texmap_manifest):
    """
    Apply the MAX-MTLX-001 fix at the USD layer.

    `texmap_manifest` maps material prim path (str) -> dict of
    {mtlx_input_name: file_path}.  The function walks each mtlx-linked
    material, finds ND_standard_surface shaders with dangling inputs whose
    NodeGraph output has no source (or, for `normal`, whose ND_normalmap_float
    has a dangling `in`), and injects the ND_tiledimage_* USD Shader prim
    that the C++ MtlxShaderWriter now emits by pre-populating the mtlxDoc.

    Returns a dict of {material_path: count_of_injections_by_this_call}.
    """
    injections = {}
    for mat_path, slot_files in texmap_manifest.items():
        mat_prim = stage.GetPrimAtPath(mat_path)
        if not mat_prim or not mat_prim.IsA(UsdShade.Material):
            continue
        mat = UsdShade.Material(mat_prim)
        # Find the ND_standard_surface shader for this material.
        std_shader = None
        for child in mat_prim.GetChildren():
            if child.IsA(UsdShade.Shader):
                shader = UsdShade.Shader(child)
                id_attr = shader.GetIdAttr()
                if id_attr and id_attr.Get() == "ND_standard_surface_surfaceshader":
                    std_shader = shader
                    break
            # Descend into MaterialX/ scope
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
        # Locate the sibling NodeGraph (typically `NG_<matname>` under the
        # same parent scope as the ND_standard_surface).
        ng = None
        for sibling in std_prim.GetParent().GetChildren():
            if sibling.IsA(UsdShade.NodeGraph):
                ng = UsdShade.NodeGraph(sibling)
                break
        if not ng:
            # No NG scaffold present — the C++ fix creates one; mirror here.
            ng_path = std_prim.GetParent().GetPath().AppendChild(
                "NG_" + std_prim.GetName())
            ng = UsdShade.NodeGraph.Define(stage, ng_path)

        count = 0
        for mtlx_input, file_path in slot_files.items():
            if mtlx_input not in NDS_INPUT_MAP:
                continue
            mtlx_type, ndi_id = NDS_INPUT_MAP[mtlx_input]
            sdf_type = TYPE_TO_SDF[mtlx_type]
            output_name = mtlx_input + "_output"

            # Is the shader input dangling? For `normal`, dangling means
            # either the NG output has no source, OR the NG output points at
            # an ND_normalmap_float whose `in` input has no source (the
            # baseline MAX-MTLX-001 case for the 45 normal-mapped materials).
            src = _shader_input_source(std_shader, mtlx_input)
            if src:
                ng_out_src = _nodegraph_output_source(ng, output_name)
                if ng_out_src:
                    if mtlx_input != "normal":
                        # Already wired — nothing to inject.
                        continue
                    # For normal, peek through the ND_normalmap_float.
                    nm_prim = ng_out_src.GetPrim()
                    nm_id = None
                    if nm_prim and nm_prim.IsA(UsdShade.Shader):
                        id_attr = UsdShade.Shader(nm_prim).GetIdAttr()
                        nm_id = id_attr.Get() if id_attr else None
                    if nm_id != "ND_normalmap_float":
                        continue
                    nm_shader = UsdShade.Shader(nm_prim)
                    nm_in = nm_shader.GetInput("in")
                    if nm_in:
                        _, in_src_name = _resolve_connection(
                            stage, nm_in.GetAttr())
                        if in_src_name:
                            # normalmap.in already sourced — skip.
                            continue

            # Inject the ND_tiledimage node inside the NodeGraph.
            img_path = ng.GetPath().AppendChild("img_" + mtlx_input)
            img = UsdShade.Shader.Define(stage, img_path)
            img.CreateIdAttr(ndi_id)
            file_inp = img.CreateInput("file", Sdf.ValueTypeNames.Asset)
            file_inp.Set(file_path)
            if mtlx_type == "color3":
                file_inp.GetAttr().SetColorSpace("srgb_texture")
            img_out = img.CreateOutput("out", sdf_type)

            # Create / reuse the NodeGraph output declaration.
            ng_out = ng.GetOutput(output_name)
            if not ng_out:
                ng_out = ng.CreateOutput(output_name, sdf_type)

            if mtlx_input == "normal":
                # Route through ND_normalmap_float — either an existing one
                # inside the NG or a fresh one we create.
                nm_shader = None
                for child in ng.GetPrim().GetChildren():
                    if child.IsA(UsdShade.Shader):
                        sh = UsdShade.Shader(child)
                        idv = sh.GetIdAttr().Get() if sh.GetIdAttr() else None
                        if idv == "ND_normalmap_float":
                            nm_shader = sh
                            break
                if not nm_shader:
                    nm_path = ng.GetPath().AppendChild("nm_" + mtlx_input)
                    nm_shader = UsdShade.Shader.Define(stage, nm_path)
                    nm_shader.CreateIdAttr("ND_normalmap_float")
                nm_in = nm_shader.GetInput("in")
                if not nm_in:
                    nm_in = nm_shader.CreateInput("in", sdf_type)
                nm_in.ConnectToSource(img_out)
                nm_out = nm_shader.GetOutput("out")
                if not nm_out:
                    nm_out = nm_shader.CreateOutput("out", sdf_type)
                ng_out.ConnectToSource(nm_out)
            else:
                ng_out.ConnectToSource(img_out)

            # Ensure the shader input references this NodeGraph output.
            std_in = std_shader.GetInput(mtlx_input)
            if not std_in:
                std_in = std_shader.CreateInput(mtlx_input, sdf_type)
            std_in.ConnectToSource(ng_out)

            count += 1
        if count:
            injections[mat_path] = count
    return injections


def count_texture_shaders(stage):
    """Census of ND_tiledimage_* USD Shader prims on a stage."""
    counts = {}
    for prim in stage.Traverse():
        if not prim.IsA(UsdShade.Shader):
            continue
        idv = UsdShade.Shader(prim).GetIdAttr().Get()
        if not idv:
            continue
        if idv.startswith("ND_tiledimage_") or idv.startswith("ND_image_"):
            counts[idv] = counts.get(idv, 0) + 1
    return counts


def _build_defect_fixture_stage(stage_path):
    """
    Build a minimal in-memory USD that reproduces the MAX-MTLX-001 defect
    pattern: an ND_standard_surface with connected inputs whose NodeGraph
    outputs are declared but source-less, and an ND_normalmap_float whose
    `in` input is dangling.
    """
    stage = Usd.Stage.CreateInMemory()
    stage.DefinePrim("/root", "Xform")

    mat_path = "/root/mtl/DEFECT_MAT"
    mat = UsdShade.Material.Define(stage, mat_path)
    mat_scope = mat.GetPrim().GetPath()

    # Node graph scope (the C++ writer nests the ND_standard_surface directly
    # under the Material, alongside a MaterialX/NG_<name> for multi-target).
    ng = UsdShade.NodeGraph.Define(stage, f"{mat_path}/NG_DEFECT_MAT")

    # Declare the outputs that MtlxIOUtil generates but leave them dangling.
    ng.CreateOutput("base_color_output", Sdf.ValueTypeNames.Color3f)
    ng.CreateOutput("specular_roughness_output", Sdf.ValueTypeNames.Float)
    ng.CreateOutput("normal_output", Sdf.ValueTypeNames.Float3)

    # An ND_normalmap_float with no `in` connection (matches the 45-material
    # slice of the baseline where the normal-map inner shader IS present but
    # its input never gets an ND_tiledimage_vector3).
    nm = UsdShade.Shader.Define(stage, f"{ng.GetPath()}/nm")
    nm.CreateIdAttr("ND_normalmap_float")
    nm_out = nm.CreateOutput("out", Sdf.ValueTypeNames.Float3)
    # Wire normal_output through nm, mirroring the baseline defect.
    ng.GetOutput("normal_output").ConnectToSource(nm_out)

    # The ND_standard_surface shader that references the NG outputs.
    std = UsdShade.Shader.Define(stage, f"{mat_path}/DEFECT_MAT")
    std.CreateIdAttr("ND_standard_surface_surfaceshader")
    for name, sdf_type in [
        ("base_color", Sdf.ValueTypeNames.Color3f),
        ("specular_roughness", Sdf.ValueTypeNames.Float),
        ("normal", Sdf.ValueTypeNames.Float3),
    ]:
        inp = std.CreateInput(name, sdf_type)
        inp.ConnectToSource(ng.GetOutput(name + "_output"))
    std_out = std.CreateOutput("surface", Sdf.ValueTypeNames.Token)
    mat.CreateSurfaceOutput("mtlx").ConnectToSource(std_out)

    return stage, mat_path


class TestMaxMtlx001Enrichment(unittest.TestCase):
    """USD-layer structural mirror of the MtlxShaderWriter C++ enrichment."""

    def test_pre_fix_stage_matches_baseline_defect(self):
        """The synthetic fixture reproduces MAX-MTLX-001's baseline symptom:
        zero ND_tiledimage_* shaders exist, and the ND_standard_surface's
        connected inputs land on NodeGraph outputs with no source."""
        stage, mat_path = _build_defect_fixture_stage(None)

        # Zero tiledimage shaders.
        pre_counts = count_texture_shaders(stage)
        self.assertNotIn("ND_tiledimage_color3", pre_counts)
        self.assertNotIn("ND_tiledimage_float", pre_counts)
        self.assertNotIn("ND_tiledimage_vector3", pre_counts)

        # The shader's base_color goes to a NodeGraph output with no source.
        std = UsdShade.Shader(stage.GetPrimAtPath(f"{mat_path}/DEFECT_MAT"))
        src = _shader_input_source(std, "base_color")
        self.assertIsNotNone(src, "base_color should point at NG output")
        ng = UsdShade.NodeGraph(stage.GetPrimAtPath(f"{mat_path}/NG_DEFECT_MAT"))
        self.assertIsNone(_nodegraph_output_source(ng, "base_color_output"))

    def test_enrichment_injects_tiledimage_shaders(self):
        """Applying the enrichment adds ND_tiledimage_* shader prims that the
        C++ MtlxShaderWriter fix (`_EnrichMtlxDocFromMaxMaterial`) causes the
        walker to emit."""
        stage, mat_path = _build_defect_fixture_stage(None)
        manifest = {
            mat_path: {
                "base_color":         "textures/DEFECT_baseColor.png",
                "specular_roughness": "textures/DEFECT_rough.png",
                "normal":             "textures/DEFECT_normal.png",
            }
        }
        injected = enrich_stage_with_tiledimage(stage, manifest)
        self.assertEqual(injected.get(mat_path), 3)

        post_counts = count_texture_shaders(stage)
        self.assertEqual(post_counts.get("ND_tiledimage_color3"), 1)
        self.assertEqual(post_counts.get("ND_tiledimage_float"), 1)
        self.assertEqual(post_counts.get("ND_tiledimage_vector3"), 1)

    def test_shader_inputs_now_have_traceable_source(self):
        """After enrichment, every previously-dangling connected input on the
        ND_standard_surface has a resolvable ND_tiledimage_* upstream."""
        stage, mat_path = _build_defect_fixture_stage(None)
        manifest = {
            mat_path: {
                "base_color":         "textures/base.jpg",
                "specular_roughness": "textures/rough.jpg",
                "normal":             "textures/normal.jpg",
            }
        }
        enrich_stage_with_tiledimage(stage, manifest)

        std = UsdShade.Shader(stage.GetPrimAtPath(f"{mat_path}/DEFECT_MAT"))
        ng = UsdShade.NodeGraph(
            stage.GetPrimAtPath(f"{mat_path}/NG_DEFECT_MAT"))

        # base_color -> NG.base_color_output -> ND_tiledimage_color3
        ng_out = _nodegraph_output_source(ng, "base_color_output")
        self.assertIsNotNone(ng_out, "base_color_output must have a source now")
        upstream = UsdShade.Shader(ng_out.GetPrim())
        self.assertEqual(
            upstream.GetIdAttr().Get(), "ND_tiledimage_color3",
            "base_color_output should be sourced from an ND_tiledimage_color3")

        # normal -> NG.normal_output -> ND_normalmap_float
        # -> `in` -> ND_tiledimage_vector3
        nm_out = _nodegraph_output_source(ng, "normal_output")
        self.assertIsNotNone(nm_out)
        nm_shader = UsdShade.Shader(nm_out.GetPrim())
        self.assertEqual(nm_shader.GetIdAttr().Get(), "ND_normalmap_float")
        nm_in = nm_shader.GetInput("in")
        self.assertIsNotNone(nm_in)
        src, src_name, _ = nm_in.GetConnectedSource() or (None, None, None)
        self.assertIsNotNone(src, "ND_normalmap_float.in must have a source")
        self.assertEqual(
            UsdShade.Shader(src.GetPrim()).GetIdAttr().Get(),
            "ND_tiledimage_vector3")

        # File paths carried through.
        img_color = UsdShade.Shader(
            stage.GetPrimAtPath(f"{ng.GetPath()}/img_base_color"))
        file_val = img_color.GetInput("file").Get()
        # Sdf.AssetPath.path yields the raw string; str() would wrap in @@.
        self.assertEqual(file_val.path, "textures/base.jpg")

    def test_no_op_when_shader_input_already_sourced(self):
        """If the doc arrives fully populated (some day...), the enrichment
        must NOT overwrite the existing tiledimage / color connection."""
        stage, mat_path = _build_defect_fixture_stage(None)
        ng = UsdShade.NodeGraph(
            stage.GetPrimAtPath(f"{mat_path}/NG_DEFECT_MAT"))
        # Pre-populate base_color_output.
        pre_img_path = f"{ng.GetPath()}/pre_existing_img"
        pre_img = UsdShade.Shader.Define(stage, pre_img_path)
        pre_img.CreateIdAttr("ND_tiledimage_color3")
        pre_out = pre_img.CreateOutput("out", Sdf.ValueTypeNames.Color3f)
        ng.GetOutput("base_color_output").ConnectToSource(pre_out)

        manifest = {mat_path: {"base_color": "should-not-be-used.png"}}
        injected = enrich_stage_with_tiledimage(stage, manifest)
        # base_color already sourced -> no injection for that input.
        self.assertNotIn(mat_path, injected)

        # The pre-existing shader is untouched, and no img_base_color got
        # sneaked in beside it.
        img_base = stage.GetPrimAtPath(f"{ng.GetPath()}/img_base_color")
        self.assertFalse(img_base.IsValid(),
                         "Enrichment must not overwrite an existing source")


def _load_texmap_manifest(path):
    """Load a JSON manifest of {mat_path: {mtlx_input: file_path}}."""
    import json
    with open(path, "r") as f:
        return json.load(f)


def _summarize_stage(stage):
    counts = count_texture_shaders(stage)
    total_dangling = 0
    for prim in stage.Traverse():
        if not prim.IsA(UsdShade.NodeGraph):
            continue
        ng = UsdShade.NodeGraph(prim)
        for out in ng.GetOutputs():
            if not out.GetConnectedSource():
                total_dangling += 1
    return counts, total_dangling


def main_cli():
    """CLI: apply the enrichment to an existing USD (e.g. the arena
    spectrum.usda) given a JSON manifest of Max map slots per material.

    Usage:
        hython test_miris_max_mtlx_001.py --stage in.usda \
            --manifest slots.json --out enriched.usda
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", required=True, help="Input .usda path")
    parser.add_argument(
        "--manifest",
        help="JSON: {mat_path: {mtlx_input: file_path}}. If omitted, only "
             "reports the pre-existing tiledimage / dangling-output census.")
    parser.add_argument("--out", help="Output .usda (default: stdout summary)")
    args = parser.parse_args()

    stage = Usd.Stage.Open(args.stage)
    if not stage:
        print(f"Failed to open {args.stage}", file=sys.stderr)
        return 2

    pre_counts, pre_dangling = _summarize_stage(stage)
    print(f"Pre-enrichment: tiledimage counts = {pre_counts}, "
          f"total NodeGraph outputs with no source = {pre_dangling}")

    if args.manifest:
        manifest = _load_texmap_manifest(args.manifest)
        injections = enrich_stage_with_tiledimage(stage, manifest)
        print(f"Enrichment applied: {sum(injections.values())} tiledimage "
              f"shaders injected across {len(injections)} materials")
        post_counts, post_dangling = _summarize_stage(stage)
        print(f"Post-enrichment: tiledimage counts = {post_counts}, "
              f"total NodeGraph outputs with no source = {post_dangling}")

    if args.out:
        stage.GetRootLayer().Export(args.out)
        print(f"Wrote {args.out}")
    return 0


if __name__ == "__main__":
    if "--stage" in sys.argv:
        sys.exit(main_cli())
    else:
        unittest.main()
