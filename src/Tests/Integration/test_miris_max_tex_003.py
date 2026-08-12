# Copyright 2026 Miris Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
"""
MAX-TEX-003 — USD-layer structural mirror of the C++ fix in
`src/translators/MtlxShaderWriter.cpp::discoverMaxMtlxTexmapsFn`.

Background
----------
The MaxUsd exporter authors a **dual-network** material when
`AllMaterialTargets = #("UsdPreviewSurface","MaterialX")`:

* `outputs:surface`      -> `UsdUVTexture` on `UsdPreviewSurface`
* `outputs:mtlx:surface` -> `ND_tiledimage_*` feeding `ND_standard_surface`

Both networks describe the SAME underlying Bitmap slot on the source
Max material, so their `file` inputs must serialize to the SAME string
for the exported USD to be portable to case-sensitive filesystems
(Linux/ARM render farms — Karma, Hydra, RenderMan).

Pre-MAX-TEX-003 defect
----------------------
The two networks resolved the Bitmap's on-disk path via different
accessors:

* UsdPreviewSurface (Python `scripts/materials/usd_material_writer.py`
  -> `usd_utils.get_file_path_mxs`) routes the filename through
  `RT.FileResolutionManager.getFullFilePath(&filename, BITMAP)`, which
  returns the ACTUAL on-disk case-preserving path. Result:
  `.../Visualization/01-3D/02-Textures/Sports/BASKETBALL-BACKBOARD_DIF.png`
  (mixed-case, matching the on-disk filesystem).

* MaterialX (C++ `MtlxShaderWriter.cpp::discoverMaxMtlxTexmapsFn`, added
  by MAX-MTLX-001) took the raw `tex.filename` /
  `tex.bitmap.filename` string from MAXScript — which some ingest
  pipelines (V-Ray Scene Converter, batch import scripts) normalize
  to lowercase — WITHOUT re-resolving it. Result:
  `.../visualization/01-3d/02-textures/sports/basketball-backboard_dif.png`
  (all-lowercase, no longer matching the on-disk filesystem).

The 12 UsdUVTexture / 12 ND_tiledimage pairs on the Spectrum Center
arena export diverged in exactly this way, so on macOS/Linux render
hosts EXACTLY ONE branch's paths 404'd — the render is silently broken
for MaterialX-aware renderers whenever they read the mtlx side.

The fix
-------
`discoverMaxMtlxTexmapsFn` now routes the discovered filename through
`FileResolutionManager.getFullFilePath &fname #bitmap` immediately after
each `fname = ...` branch (Bitmaptexture, `.filename`-carrying wrappers,
`.bitmap.filename` on VRayBitmap). Both networks now share the resolver,
so both `file` inputs serialize identical, case-preserving strings.
Falls back to the raw fname if the resolver returns false (missing-on-
disk case), preserving the legacy dangling-file authoring behavior.

What this test locks in
-----------------------
Pure USD-layer assertions — no maxUsd plugin rebuild required. The
tests construct a synthetic post-export dual-network stage, apply the
equivalent-of-the-MAXScript-resolver normalization at the USD layer,
and assert:

1. The pre-fix defect (all-lowercase ND_tiledimage vs. mixed-case
   UsdUVTexture on the same material) is observable — the two `file`
   inputs disagree character-for-character.
2. After normalizing both networks through the same file-path resolver
   (mirroring what the C++ MAXScript now does), the two file inputs
   agree byte-for-byte.
3. When the resolver can't find the file on disk (dangling ref), the
   fallback path preserves the raw as-authored fname (legacy behavior).
4. On a multi-material stage that mirrors the arena census (12 dual
   pairs), every pair agrees post-normalization.
5. Surgical scope: normalization touches ONLY `inputs:file` on
   ND_tiledimage_* and UsdUVTexture shaders. Other prims, other
   attributes, and unaffected shaders are left byte-identical.

Runs under Houdini's `hython`:
    hython test_miris_max_tex_003.py
"""
import argparse
import os
import sys
import unittest

from pxr import Sdf, Usd, UsdShade


# ---------------------------------------------------------------------------
# Resolver mirror
# ---------------------------------------------------------------------------
# Mirrors 3ds Max's FileResolutionManager.getFullFilePath(&filename, #bitmap):
# looks up the filename on the "filesystem" (a dict for the test), returns the
# actual on-disk case-preserving path when found. Returns (False, "")
# otherwise — matching the MAXScript signature where the byref parameter is
# left untouched when the resolver fails.


class DiskResolver:
    """A test double for RT.FileResolutionManager.getFullFilePath.

    Constructed with a set of on-disk paths (in their true case). Given any
    case-insensitively-equal path string, returns the ACTUAL on-disk path.
    Mirrors the case-preservation semantics of Max's FileResolutionManager
    on a case-insensitive filesystem — which is what a Windows origin host
    looks like from the exporter's point of view.
    """

    def __init__(self, on_disk_paths):
        self._by_lower = {p.lower(): p for p in on_disk_paths}

    def resolve(self, path):
        """Return (ok: bool, resolved: str).

        `ok` is True and `resolved` is the on-disk case when found; False and
        `path` unchanged otherwise (matches the MAXScript convention where
        callers keep the original `fname` on resolver failure).
        """
        key = path.lower() if path else ""
        if key in self._by_lower:
            return True, self._by_lower[key]
        return False, path


# ---------------------------------------------------------------------------
# USD-layer application of the MAX-TEX-003 fix
# ---------------------------------------------------------------------------
# The fix lives in the MAXScript helper `discoverMaxMtlxTexmapsFn`, which
# runs at export time inside 3dsmaxbatch. This function is the same
# invariant applied at the USD layer: for every ND_tiledimage_* shader and
# UsdUVTexture shader on the stage, normalize `inputs:file` through the
# supplied resolver. Idempotent, order-independent, and touches nothing
# else on the stage.


ND_TILED_IMAGE_IDS = (
    "ND_tiledimage_color3",
    "ND_tiledimage_float",
    "ND_tiledimage_vector3",
    "ND_image_color3",
    "ND_image_float",
    "ND_image_vector3",
)


def _iter_texture_shaders(stage):
    """Yield (UsdShade.Shader, shader_id) for every ND_tiledimage / UsdUVTexture
    on the stage. Skips prims that are not Shader-typed or that have no id."""
    for prim in stage.Traverse():
        if not prim.IsA(UsdShade.Shader):
            continue
        shader = UsdShade.Shader(prim)
        id_attr = shader.GetIdAttr()
        if not id_attr:
            continue
        idv = id_attr.Get()
        if idv in ND_TILED_IMAGE_IDS or idv == "UsdUVTexture":
            yield shader, idv


def _read_asset_path_str(inp):
    """Return the raw string inside a Sdf.AssetPath-typed input, or the raw
    value for a plain-string input. Empty string when unset."""
    if not inp:
        return ""
    val = inp.Get()
    if val is None:
        return ""
    if isinstance(val, Sdf.AssetPath):
        return val.path
    return str(val)


def _normalize_texture_file_paths(stage, resolver):
    """Apply the MAX-TEX-003 invariant across every texture shader on the
    stage: route `inputs:file` through the resolver and rewrite when it
    resolves. Returns a dict {shader_prim_path: (before, after)} for every
    input actually rewritten.

    Idempotent — a second call on a stage already normalized is a no-op
    because the resolver returns the already-canonical path.
    """
    changes = {}
    for shader, _id in _iter_texture_shaders(stage):
        file_inp = shader.GetInput("file")
        before = _read_asset_path_str(file_inp)
        if not before:
            continue
        ok, resolved = resolver.resolve(before)
        if not ok:
            # Fallback branch: keep the raw as-authored fname untouched.
            continue
        if resolved == before:
            continue
        # Preserve the Sdf.ValueTypeName (Asset vs. String) by re-Setting
        # through the same accessor.
        if isinstance(file_inp.Get(), Sdf.AssetPath):
            file_inp.Set(Sdf.AssetPath(resolved))
        else:
            file_inp.Set(resolved)
        changes[shader.GetPrim().GetPath().pathString] = (before, resolved)
    return changes


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------

def _build_dual_network_material(
    stage,
    mat_path,
    preview_file,   # str: path to author on UsdUVTexture.inputs:file
    mtlx_file,      # str: path to author on ND_tiledimage.inputs:file
    mtlx_type="color3",
    mtlx_input="base_color",
):
    """Build a synthetic post-export dual-network material.

    Structure mirrors what the MaxUsd exporter emits when
    AllMaterialTargets = #("UsdPreviewSurface","MaterialX"):

        <mat_path>/       Material  (outputs:surface + outputs:mtlx:surface)
          UsdUVTexture     Shader   (id=UsdUVTexture, inputs:file=<preview_file>)
          NG_<name>/       NodeGraph
            img_<input>    Shader   (id=ND_tiledimage_<type>, inputs:file=<mtlx_file>)
          <name>           Shader   (id=ND_standard_surface_surfaceshader)

    The two file inputs are set INDEPENDENTLY so the test can exercise
    the divergent-case pre-fix state.
    """
    mat = UsdShade.Material.Define(stage, mat_path)
    mat_prim_path = mat.GetPrim().GetPath()

    # UsdPreviewSurface side: NodeGraph wrapping a UsdUVTexture.
    ps_ng = UsdShade.NodeGraph.Define(stage, f"{mat_path}/NG_ps")
    uv_shader = UsdShade.Shader.Define(stage, f"{ps_ng.GetPath()}/UVTex")
    uv_shader.CreateIdAttr("UsdUVTexture")
    uv_file = uv_shader.CreateInput("file", Sdf.ValueTypeNames.Asset)
    uv_file.Set(Sdf.AssetPath(preview_file))
    uv_out = uv_shader.CreateOutput("rgb", Sdf.ValueTypeNames.Float3)
    ps_out = ps_ng.CreateOutput("diffuseColor_output",
                                Sdf.ValueTypeNames.Float3)
    ps_out.ConnectToSource(uv_out)

    ps_shader = UsdShade.Shader.Define(stage, f"{mat_path}/PS")
    ps_shader.CreateIdAttr("UsdPreviewSurface")
    ps_diffuse = ps_shader.CreateInput("diffuseColor",
                                       Sdf.ValueTypeNames.Color3f)
    ps_diffuse.ConnectToSource(ps_out)
    ps_surface = ps_shader.CreateOutput("surface", Sdf.ValueTypeNames.Token)
    mat.CreateSurfaceOutput().ConnectToSource(ps_surface)

    # MaterialX side: NodeGraph wrapping an ND_tiledimage feeding an
    # ND_standard_surface.
    mtlx_ng = UsdShade.NodeGraph.Define(stage, f"{mat_path}/NG_mtlx")
    type_to_sdf = {
        "color3":  Sdf.ValueTypeNames.Color3f,
        "float":   Sdf.ValueTypeNames.Float,
        "vector3": Sdf.ValueTypeNames.Float3,
    }
    sdf_type = type_to_sdf[mtlx_type]
    tiled_id = f"ND_tiledimage_{mtlx_type}"
    img = UsdShade.Shader.Define(stage,
                                 f"{mtlx_ng.GetPath()}/img_{mtlx_input}")
    img.CreateIdAttr(tiled_id)
    img_file = img.CreateInput("file", Sdf.ValueTypeNames.Asset)
    img_file.Set(Sdf.AssetPath(mtlx_file))
    if mtlx_type == "color3":
        img_file.GetAttr().SetColorSpace("srgb_texture")
    img_out = img.CreateOutput("out", sdf_type)
    ng_out = mtlx_ng.CreateOutput(f"{mtlx_input}_output", sdf_type)
    ng_out.ConnectToSource(img_out)

    std = UsdShade.Shader.Define(stage, f"{mat_path}/M")
    std.CreateIdAttr("ND_standard_surface_surfaceshader")
    std_in = std.CreateInput(mtlx_input, sdf_type)
    std_in.ConnectToSource(ng_out)
    std_surface = std.CreateOutput("surface", Sdf.ValueTypeNames.Token)
    mat.CreateSurfaceOutput("mtlx").ConnectToSource(std_surface)

    return uv_shader, img


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestMaxTex003PreFixDefect(unittest.TestCase):
    """The pre-fix defect: the two networks author divergent file-path case."""

    def test_pre_fix_paths_disagree(self):
        """UsdUVTexture and its dual-network ND_tiledimage sister carry
        different file-path strings (mixed case vs. all-lowercase), reproducing
        the arena's 12/12 divergence.
        """
        stage = Usd.Stage.CreateInMemory()
        stage.DefinePrim("/root", "Xform")
        preview_path = (
            "P:/Visualization/01-3D/02-Textures/Sports/"
            "BASKETBALL-BACKBOARD_DIF.png")
        mtlx_path = (
            "p:/visualization/01-3d/02-textures/sports/"
            "basketball-backboard_dif.png")
        uv, img = _build_dual_network_material(
            stage, "/root/mtl/BackboardMat", preview_path, mtlx_path)

        uv_file = _read_asset_path_str(uv.GetInput("file"))
        img_file = _read_asset_path_str(img.GetInput("file"))
        self.assertEqual(uv_file, preview_path)
        self.assertEqual(img_file, mtlx_path)
        self.assertNotEqual(
            uv_file, img_file,
            "Pre-fix state: the two networks must diverge character-for-"
            "character to reproduce the defect.")


class TestMaxTex003Normalization(unittest.TestCase):
    """Applying the resolver normalization brings the two networks into
    agreement."""

    def setUp(self):
        # The actual on-disk file has this canonical case (Windows origin FS).
        self.canonical = (
            "P:/Visualization/01-3D/02-Textures/Sports/"
            "BASKETBALL-BACKBOARD_DIF.png")
        # V-Ray Scene Converter lowercased the string on the mtlx side.
        self.mtlx_lower = self.canonical.lower()
        self.resolver = DiskResolver([self.canonical])

    def _build_defect_stage(self):
        stage = Usd.Stage.CreateInMemory()
        stage.DefinePrim("/root", "Xform")
        uv, img = _build_dual_network_material(
            stage, "/root/mtl/BackboardMat",
            self.canonical, self.mtlx_lower)
        return stage, uv, img

    def test_normalization_converges_both_networks_on_canonical_case(self):
        stage, uv, img = self._build_defect_stage()
        changes = _normalize_texture_file_paths(stage, self.resolver)

        uv_file = _read_asset_path_str(uv.GetInput("file"))
        img_file = _read_asset_path_str(img.GetInput("file"))
        self.assertEqual(uv_file, self.canonical)
        self.assertEqual(img_file, self.canonical)
        self.assertEqual(
            uv_file, img_file,
            "Post-fix: both networks must serialize identical file-path "
            "strings.")
        # Only the ND_tiledimage side needed rewriting (the UV side was
        # already canonical) — surgical: we don't touch inputs that don't
        # need changing.
        self.assertEqual(
            {p.split("/")[-1] for p in changes.keys()},
            {"img_base_color"})

    def test_normalization_is_idempotent(self):
        stage, uv, img = self._build_defect_stage()
        _normalize_texture_file_paths(stage, self.resolver)
        # Second run should be a no-op — both file inputs are already canonical.
        second_pass = _normalize_texture_file_paths(stage, self.resolver)
        self.assertEqual(second_pass, {},
                         "Second normalization pass must be a no-op.")

    def test_fallback_preserves_raw_when_resolver_fails(self):
        """When FileResolutionManager can't find the file on disk (dangling
        reference), the MAXScript branch keeps the raw fname untouched.
        Assert the USD-layer normalization mirrors that fallback so
        exports that legitimately point at a missing asset ship an authored
        path (not empty)."""
        stage = Usd.Stage.CreateInMemory()
        stage.DefinePrim("/root", "Xform")
        dangling = "p:/nope/not_on_disk_missing_texture.png"
        _, img = _build_dual_network_material(
            stage, "/root/mtl/DanglingMat", dangling, dangling)

        changes = _normalize_texture_file_paths(stage, self.resolver)
        self.assertEqual(changes, {},
                         "Resolver returned False → no rewrite.")
        # And the pre-existing string is still in the input.
        img_file = _read_asset_path_str(img.GetInput("file"))
        self.assertEqual(img_file, dangling)

    def test_resolver_is_case_preserving_on_hit(self):
        """The MAX-TEX-003 fix explicitly preserves author case — not
        lowercase or uppercase. Lock this in so a future 'clean-up' PR
        can't sneak in a `path.lower()` behind the resolver."""
        ok, resolved = self.resolver.resolve(self.mtlx_lower)
        self.assertTrue(ok)
        self.assertEqual(resolved, self.canonical)


class TestMaxTex003ArenaCensus(unittest.TestCase):
    """Multi-material census — the 12 dual-pair signature of the Spectrum
    Center arena baseline. Every pair converges after normalization."""

    ARENA_TEXTURES = [
        # (mat name, mtlx input, canonical on-disk case)
        ("BackboardMat", "base_color",
         "P:/Visualization/01-3D/02-Textures/Sports/"
         "BASKETBALL-BACKBOARD_DIF.png"),
        ("CourtFloorMat", "base_color",
         "P:/Visualization/01-3D/02-Textures/Sports/"
         "BASKETBALL-COURT-FLOOR_DIF.png"),
        ("HoopMat", "base_color",
         "P:/Visualization/01-3D/02-Textures/Sports/"
         "BASKETBALL-HOOP-METAL_DIF.png"),
        ("NetMat", "opacity",
         "P:/Visualization/01-3D/02-Textures/Sports/"
         "BASKETBALL-NET_MASK.png"),
        ("PaddingMat", "base_color",
         "P:/Visualization/01-3D/02-Textures/Sports/"
         "BASKETBALL-PADDING_DIF.png"),
        ("StanchionMat", "specular_roughness",
         "P:/Visualization/01-3D/02-Textures/Metals/STANCHION_ROUGH.png"),
        ("SeatFabricMat", "base_color",
         "P:/Visualization/01-3D/02-Textures/Fabrics/SEAT-FABRIC_DIF.png"),
        ("ConcreteMat", "specular_roughness",
         "P:/Visualization/01-3D/02-Textures/Concrete/CONCRETE_ROUGH.png"),
        ("LEDBoardMat", "emission_color",
         "P:/Visualization/01-3D/02-Textures/LED/LED-BOARD_EMIT.png"),
        ("GlassRailMat", "transmission_color",
         "P:/Visualization/01-3D/02-Textures/Glass/RAIL-GLASS_TRANS.png"),
        ("MetalTrimMat", "metalness",
         "P:/Visualization/01-3D/02-Textures/Metals/TRIM_METAL.png"),
        ("SignageMat", "base_color",
         "P:/Visualization/01-3D/02-Textures/Signage/SIGN_DIF.png"),
    ]

    def _build_arena_stage(self):
        stage = Usd.Stage.CreateInMemory()
        stage.DefinePrim("/root", "Xform")
        # Preview side gets canonical case; mtlx side gets lowercase — the
        # pre-fix divergence.
        for name, mtlx_input, canonical in self.ARENA_TEXTURES:
            mtlx_type = {
                "base_color":         "color3",
                "specular_roughness": "float",
                "metalness":          "float",
                "emission_color":     "color3",
                "transmission_color": "color3",
                "opacity":            "float",
            }[mtlx_input]
            _build_dual_network_material(
                stage, f"/root/mtl/{name}",
                canonical, canonical.lower(),
                mtlx_type=mtlx_type, mtlx_input=mtlx_input)
        return stage

    def _pair_agreement_census(self, stage):
        """For every material, compute (uv_file, img_file, agrees)."""
        rows = []
        for mat_prim in stage.Traverse():
            if not mat_prim.IsA(UsdShade.Material):
                continue
            uv_files = []
            img_files = []
            for shader, idv in _iter_texture_shaders(stage):
                shader_path = shader.GetPrim().GetPath().pathString
                if not shader_path.startswith(
                        mat_prim.GetPath().pathString + "/"):
                    continue
                val = _read_asset_path_str(shader.GetInput("file"))
                if idv == "UsdUVTexture":
                    uv_files.append(val)
                else:
                    img_files.append(val)
            if uv_files and img_files:
                rows.append((mat_prim.GetPath().pathString,
                             uv_files[0], img_files[0],
                             uv_files[0] == img_files[0]))
        return rows

    def test_pre_fix_census_all_pairs_diverge(self):
        stage = self._build_arena_stage()
        rows = self._pair_agreement_census(stage)
        self.assertEqual(len(rows), 12,
                         "Fixture must have 12 dual-network pairs "
                         "(arena signature).")
        for path, uv, img, agrees in rows:
            self.assertFalse(agrees,
                             f"{path}: pre-fix must show divergence — "
                             f"uv={uv!r} vs img={img!r}")

    def test_post_fix_census_all_pairs_agree(self):
        stage = self._build_arena_stage()
        resolver = DiskResolver(
            [canonical for _, _, canonical in self.ARENA_TEXTURES])
        _normalize_texture_file_paths(stage, resolver)

        rows = self._pair_agreement_census(stage)
        self.assertEqual(len(rows), 12)
        for path, uv, img, agrees in rows:
            self.assertTrue(
                agrees,
                f"{path}: post-fix must show byte-for-byte agreement — "
                f"uv={uv!r} vs img={img!r}")

    def test_post_fix_all_paths_are_canonical_case(self):
        """The convergence isn't 'both lowercased' or 'both uppercased';
        the resolver returns the actual on-disk case, matching the
        UsdPreviewSurface writer's behavior. Lock in case-preservation."""
        stage = self._build_arena_stage()
        canonicals = {canonical
                      for _, _, canonical in self.ARENA_TEXTURES}
        resolver = DiskResolver(list(canonicals))
        _normalize_texture_file_paths(stage, resolver)

        for shader, _id in _iter_texture_shaders(stage):
            val = _read_asset_path_str(shader.GetInput("file"))
            self.assertIn(
                val, canonicals,
                f"{shader.GetPrim().GetPath()}: post-fix file must equal a "
                f"canonical on-disk path (case-preserved), got {val!r}")


class TestMaxTex003SurgicalScope(unittest.TestCase):
    """Prove the fix has surgical scope — it changes nothing besides the
    `inputs:file` attribute value on ND_tiledimage_* and UsdUVTexture
    shaders."""

    def _build_scoped_stage(self):
        stage = Usd.Stage.CreateInMemory()
        stage.DefinePrim("/root", "Xform")
        canonical = "P:/Textures/Diffuse.png"
        _build_dual_network_material(
            stage, "/root/mtl/M", canonical, canonical.lower())
        # Add a decoy prim that must not be touched: a mesh, a camera,
        # and an unrelated PrimvarReader shader.
        stage.DefinePrim("/root/geom/Mesh1", "Mesh")
        stage.DefinePrim("/root/cam", "Camera")
        decoy_reader = UsdShade.Shader.Define(
            stage, "/root/mtl/M/NG_mtlx/PrimvarReader")
        decoy_reader.CreateIdAttr("UsdPrimvarReader_float2")
        decoy_reader.CreateInput(
            "varname", Sdf.ValueTypeNames.Token).Set("st")
        return stage, DiskResolver([canonical])

    def test_only_texture_shader_file_inputs_are_touched(self):
        stage, resolver = self._build_scoped_stage()

        # Snapshot every attribute value that is NOT a texture-shader file
        # input, both before and after.
        def _snapshot():
            snap = {}
            for prim in stage.Traverse():
                for attr in prim.GetAttributes():
                    if attr.GetName() == "inputs:file" and prim.IsA(
                            UsdShade.Shader):
                        idv_attr = UsdShade.Shader(prim).GetIdAttr()
                        idv = idv_attr.Get() if idv_attr else None
                        if idv in ND_TILED_IMAGE_IDS or idv == "UsdUVTexture":
                            continue
                    snap[(prim.GetPath().pathString, attr.GetName())] = \
                        attr.Get()
            return snap

        before = _snapshot()
        _normalize_texture_file_paths(stage, resolver)
        after = _snapshot()
        self.assertEqual(
            before, after,
            "Normalization must touch ONLY inputs:file on ND_tiledimage_* "
            "and UsdUVTexture shaders — everything else must be identical.")

    def test_shader_id_attributes_are_untouched(self):
        """Regression guard: normalization must not accidentally re-Set the
        `info:id` attribute (which is what identifies the shader as a
        tiledimage / UsdUVTexture)."""
        stage, resolver = self._build_scoped_stage()

        pre_ids = {
            shader.GetPrim().GetPath().pathString: idv
            for shader, idv in _iter_texture_shaders(stage)
        }
        _normalize_texture_file_paths(stage, resolver)
        post_ids = {
            shader.GetPrim().GetPath().pathString: idv
            for shader, idv in _iter_texture_shaders(stage)
        }
        self.assertEqual(pre_ids, post_ids)

    def test_color_space_metadata_survives_normalization(self):
        """The C++ MAX-MTLX-001 fix explicitly sets colorspace=srgb_texture
        on color3 tiledimage file inputs. Normalization must not clobber
        that."""
        stage, resolver = self._build_scoped_stage()

        img = UsdShade.Shader(
            stage.GetPrimAtPath("/root/mtl/M/NG_mtlx/img_base_color"))
        pre_colorspace = img.GetInput("file").GetAttr().GetColorSpace()
        self.assertEqual(pre_colorspace, "srgb_texture")

        _normalize_texture_file_paths(stage, resolver)

        post_colorspace = img.GetInput("file").GetAttr().GetColorSpace()
        self.assertEqual(post_colorspace, "srgb_texture",
                         "Post-fix must preserve srgb_texture colorspace "
                         "on ND_tiledimage_color3 inputs.")


class TestMaxTex003ResolverAPIContract(unittest.TestCase):
    """Contract tests for the resolver mirror — these lock in what the
    MAXScript `FileResolutionManager.getFullFilePath &fname #bitmap`
    call is expected to do, so a future refactor can't shim in a
    lowercasing resolver behind the same interface."""

    def test_resolver_leaves_out_param_untouched_on_miss(self):
        """MAXScript convention: on `false` return, the byref parameter
        is not modified. Our mirror preserves that."""
        r = DiskResolver(["P:/Textures/OnDisk.png"])
        ok, out = r.resolve("P:/Nowhere/missing.png")
        self.assertFalse(ok)
        self.assertEqual(out, "P:/Nowhere/missing.png")

    def test_resolver_normalizes_case_of_hit(self):
        r = DiskResolver(["P:/Foo/Bar/BAZ.png"])
        ok, out = r.resolve("p:/foo/bar/baz.png")
        self.assertTrue(ok)
        self.assertEqual(out, "P:/Foo/Bar/BAZ.png")

    def test_resolver_hits_on_exact_match(self):
        r = DiskResolver(["P:/Foo/Bar/BAZ.png"])
        ok, out = r.resolve("P:/Foo/Bar/BAZ.png")
        self.assertTrue(ok)
        self.assertEqual(out, "P:/Foo/Bar/BAZ.png")


# ---------------------------------------------------------------------------
# CLI: apply the invariant to a real USD (e.g. an arena export post-Windows-
# box rebuild) for operational verification once the DLL is rebuilt.
# ---------------------------------------------------------------------------

def _load_disk_index(path):
    """Load a JSON list of on-disk absolute paths (canonical case)."""
    import json
    with open(path, "r") as f:
        return json.load(f)


def _pair_divergence_census(stage):
    """Count dual-network pairs where UsdUVTexture and its mtlx sister
    disagree on file-path string. For each material with both an UV
    shader and an ND_tiledimage_*, note the two paths."""
    diverging = []
    agreeing = 0
    materials_examined = 0
    for mat_prim in stage.Traverse():
        if not mat_prim.IsA(UsdShade.Material):
            continue
        materials_examined += 1
        uv_files = []
        img_files = []
        for shader, idv in _iter_texture_shaders(stage):
            sp = shader.GetPrim().GetPath().pathString
            if not sp.startswith(mat_prim.GetPath().pathString + "/"):
                continue
            val = _read_asset_path_str(shader.GetInput("file"))
            if not val:
                continue
            if idv == "UsdUVTexture":
                uv_files.append(val)
            else:
                img_files.append(val)
        if uv_files and img_files:
            if uv_files[0] == img_files[0]:
                agreeing += 1
            else:
                diverging.append(
                    (mat_prim.GetPath().pathString,
                     uv_files[0], img_files[0]))
    return {
        "materials_examined": materials_examined,
        "dual_network_pairs": len(diverging) + agreeing,
        "diverging_count": len(diverging),
        "agreeing_count": agreeing,
        "diverging_sample": diverging[:10],
    }


def main_cli():
    """CLI: census a USD's dual-network file-path agreement + optionally
    normalize via a supplied on-disk index.

    Usage:
        hython test_miris_max_tex_003.py --stage in.usda \\
            [--disk-index paths.json] [--out normalized.usda]
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", required=True, help="Input USD path")
    parser.add_argument(
        "--disk-index",
        help="JSON list of on-disk canonical paths. When present, applies "
             "the MAX-TEX-003 invariant to the stage and re-runs the "
             "census on the result.")
    parser.add_argument("--out",
                        help="Output USD path for the normalized stage.")
    args = parser.parse_args()

    stage = Usd.Stage.Open(args.stage)
    if not stage:
        print(f"Failed to open {args.stage}", file=sys.stderr)
        return 2

    pre = _pair_divergence_census(stage)
    print("Pre-normalization census:")
    for k, v in pre.items():
        if k != "diverging_sample":
            print(f"  {k}: {v}")
    if pre["diverging_sample"]:
        print("  diverging_sample (first 10):")
        for path, uv, img in pre["diverging_sample"]:
            print(f"    {path}\n      uv=  {uv}\n      img= {img}")

    if args.disk_index:
        disk_paths = _load_disk_index(args.disk_index)
        resolver = DiskResolver(disk_paths)
        changes = _normalize_texture_file_paths(stage, resolver)
        print(f"\nApplied normalization: rewrote {len(changes)} inputs")

        post = _pair_divergence_census(stage)
        print("Post-normalization census:")
        for k, v in post.items():
            if k != "diverging_sample":
                print(f"  {k}: {v}")
        if post["diverging_sample"]:
            print("  STILL diverging (first 10):")
            for path, uv, img in post["diverging_sample"]:
                print(f"    {path}\n      uv=  {uv}\n      img= {img}")

    if args.out:
        stage.GetRootLayer().Export(args.out)
        print(f"\nWrote {args.out}")
    return 0


if __name__ == "__main__":
    if "--stage" in sys.argv:
        sys.exit(main_cli())
    else:
        unittest.main()
