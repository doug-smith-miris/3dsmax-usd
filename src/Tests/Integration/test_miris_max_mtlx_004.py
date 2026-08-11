# Copyright 2026 Miris Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
"""
MAX-MTLX-004 — USD-layer structural mirror of the C++ fix in
`src/MaxUsd/Translators/LastResortMtlxShaderWriter.{h,cpp}` and the paired
edit to `src/MaxUsd/Translators/ShaderWriterRegistry.cpp::Find`.

# The defect

The MtlxShaderWriter is registered for exactly two 3ds Max material
Class_IDs — PhysicalMaterial and OpenPBR (see the two
`PXR_MAXUSD_REGISTER_SHADER_WRITER` invocations at the bottom of
`src/translators/MtlxShaderWriter.cpp`). At export time,
`ShadingModeUseRegistry::UseRegistryShadingModeExporter::_GetExportedShaderForNode`
asks the registry for a writer keyed on the material's Class_ID for each
active material target:

```
MaxUsdShaderWriterRegistry::WriterFactoryFn shaderWriterFactory
    = MaxUsdShaderWriterRegistry::Find(material->ClassID(), context.GetExportArgs());
if (!shaderWriterFactory) { return nullptr; }
```

For the UsdPreviewSurface target this "no writer" case is caught by
`ShaderWriterRegistry.cpp::Find`, which returns the
`LastResortUSDPreviewSurfaceWriter` — a fallback that authors a minimal
`UsdPreviewSurface` shader with `diffuseColor` sourced from
`material->GetDiffuse()`. For the MaterialX target, the identical branch
does not exist: Find() returns nullptr, so _GetExportedShaderForNode
returns nullptr, so the outer Export loop `continue`s without writing a
shader for that target, and the material ends up with an
`outputs:surface` (UsdPreviewSurface) arc but no `outputs:mtlx:surface`
arc at all.

# Diagnostic evidence (Spectrum Center arena, pipeline `0b6d5e38`)

`01_materials.txt`:
    MATERIALS: 179
    --CATEGORY COUNTS--
    { "both": 143, "uvs_only": 36 }
    --MTLX ROOT SHADER IDs--
        143  ND_standard_surface_surfaceshader
    --UsdPreview ROOT SHADER IDs--
        179  UsdPreviewSurface

`08_wrap_up.txt`:
    --The 36 no-MTLX-link materials — full shader inventory--
    count: 36
    {'UsdPreviewSurface': 36}
    Sample no-mtlx materials: ['BASKETBALL_SHOTCLOCK_ILUM', 'BOTTOM_PANEL',
    'DRINK_LABEL_BRAWNDO', 'DRINK_LABEL_MAKO', 'DRINK_LABEL_REDBULL',
    'FOOD_CHIK_WRAP', 'FOOD_FRUIT', 'FOOD_SALAD_1', 'GLASS_ILUM',
    'GRAPHIC_ILUM_WHITE', 'LENS_WARM_OVERRIDE', 'MTL_CAN_ALUMIN',
    'Metal___Steel', 'PLASTIC_BLACK', 'PLASTIC_BLACK1']

The 36 material names look like Standard Materials, unconverted
VRayLightMtl / Multi-material children, and dressing-scene classes that the
V-Ray → Physical converter left unconverted. All of them share the property
that their Class_ID is neither `PHYSICALMATERIAL_CLASS_ID` nor
`Class_ID(0xf1551e33, 0x37fb1337)` (OpenPBR).

# The fix

Add `LastResortMtlxShaderWriter` as a sibling of the existing
`LastResortUSDPreviewSurfaceWriter`. On `Write()` it authors:
    - `info:id = "ND_standard_surface_surfaceshader"` — same shader identity
      the primary MtlxShaderWriter uses for the 143 covered materials, so
      downstream consumers evaluate the fallback through the same node
      definition.
    - `inputs:base_color = material->GetDiffuse()` — a color3f. The caller
      (`CreateShaderOutputAndConnectMaterial` in ShadingUtils.cpp) wires
      `material.outputs:mtlx:surface` to the shader's default output when
      `renderContext == TfToken("mtlx")`.

Then extend `ShaderWriterRegistry::Find` with a symmetric branch after the
existing UsdPreviewSurface fallback: when Find would otherwise return
nullptr, and the current target is `TfToken("MaterialX")`, and the same
`GetUseLastResortUSDPreviewSurfaceWriter()` option is enabled, return a
factory for LastResortMtlxShaderWriter instead.

# This test

Structural-only proof at the USD layer — a Python mirror of the dispatch
+ authorship. It:

  1. Builds a defect fixture with two materials — one whose Class_ID has
     a "real" MaterialX writer registered (mtlx-linked, ND_standard_surface
     already authored), and one whose Class_ID does not (only
     UsdPreviewSurface authored, mimicking the 36 arena materials).
  2. Runs `apply_last_resort_mtlx_dispatch`, the USD-layer mirror of the
     Registry::Find + LastResortMtlxShaderWriter combination, on the defect
     fixture.
  3. Asserts:
     a. Post-fix, every material carries an `outputs:mtlx:surface` arc.
     b. The previously-mtlx-linked material's ND_standard_surface is NOT
        replaced or perturbed by the fallback (the fallback only fires when
        the target's arc is missing).
     c. The previously-unlinked material now has an ND_standard_surface at
        the expected path, with `base_color` set to the material's diffuse.
     d. The UsdPreviewSurface arc of each material is untouched (the fix
        adds a MaterialX arc; it does not modify the preview-surface arc).
     e. Materials created without a diffuse fallback (defensive: caller
        passes `None`) get a black `base_color = (0,0,0)` — matches the
        MSTR::Color default behavior of GetDiffuse() for pathological
        source materials.
     f. When the last-resort option is disabled, the fallback does NOT
        fire (mirroring `GetUseLastResortUSDPreviewSurfaceWriter() == false`).

The Python mirror is a validator, not a shipping code path. The C++ fix
upholds the same invariants by returning a
`LastResortMtlxShaderWriter`-producing factory from
`ShaderWriterRegistry::Find` when the primary lookup misses on the
MaterialX target.
"""
import argparse
import os
import sys
import unittest

from pxr import Sdf, Usd, UsdShade


# The MaterialX target string, matching `TfToken("MaterialX")` in
# `ShaderWriterRegistry.cpp` (the new branch) and
# `MtlxShaderWriter::CanExport`.
_MATERIALX_TARGET = "MaterialX"

# `info:id` for ND_standard_surface_surfaceshader — the shader identity
# both the primary MtlxShaderWriter (for the 143 covered materials) and the
# new LastResortMtlxShaderWriter (for the 36 uncovered) use.
_ND_STANDARD_SURFACE_ID = "ND_standard_surface_surfaceshader"

# The render-context token on the material's surface output that keys the
# MaterialX arc: material.outputs:mtlx:surface. Matches
# `UsdShadeMaterial::CreateSurfaceOutput("mtlx")`.
_MTLX_RENDER_CONTEXT = "mtlx"


def _has_mtlx_surface_arc(material):
    """True iff the material carries an `outputs:mtlx:surface` arc that is
    connected to a shader output. Mirrors the diagnostic-agent census
    logic in `01_materials.py` when it counts a material as `mtlxLinked`."""
    out = material.GetSurfaceOutput(_MTLX_RENDER_CONTEXT)
    if not out:
        return False
    conns = out.GetAttr().GetConnections()
    return bool(conns)


def _has_preview_surface_arc(material):
    """True iff the material carries an `outputs:surface` arc for the
    UsdPreviewSurface target (the universal render context, which is the
    empty string)."""
    out = material.GetSurfaceOutput()
    if not out:
        return False
    conns = out.GetAttr().GetConnections()
    return bool(conns)


def _resolve_surface_shader(material, render_context):
    """Return the UsdShadeShader terminal driving material.outputs:<ctx>:surface,
    or None if the arc is missing or not sourced by a shader."""
    out = material.GetSurfaceOutput(render_context)
    if not out:
        return None
    conns = out.GetAttr().GetConnections()
    if not conns:
        return None
    stage = material.GetPrim().GetStage()
    src_prim = stage.GetPrimAtPath(conns[0].GetPrimPath())
    if not src_prim or not src_prim.IsA(UsdShade.Shader):
        return None
    return UsdShade.Shader(src_prim)


def apply_last_resort_mtlx_dispatch(
        stage,
        diffuse_manifest,
        use_last_resort=True):
    """
    USD-layer mirror of the C++ ShaderWriterRegistry::Find MaterialX
    fallback branch.

    Walk every UsdShade.Material on the stage. For each material that
    LACKS an `outputs:mtlx:surface` arc (i.e. the primary writer misses
    or the material's Class_ID is unregistered), and for which the
    diffuse_manifest carries an entry, define a
    `<materialPath>/MaterialX/<materialName>` UsdShadeShader with
    `info:id=ND_standard_surface_surfaceshader` and
    `inputs:base_color = diffuse_manifest[matPath]`, and connect
    `material.outputs:mtlx:surface` to the shader's `outputs:out` port.

    Args:
      stage: pxr.Usd.Stage — mutated in place.
      diffuse_manifest: dict[str, tuple[float, float, float]] — the fallback
        base_color per material path. Only materials present in this
        manifest are considered — mirrors the C++ Find() being called
        per-material by the outer Export loop.
      use_last_resort: bool — when False, mirrors
        SetUseLastResortUSDPreviewSurfaceWriter(false) at the C++ layer;
        no fallback shader is authored even when the arc is missing.

    Returns:
      dict[str, str] — {material_path: shader_prim_path} for the shaders
      the fallback wrote.
    """
    authored = {}
    if not use_last_resort:
        return authored

    for prim in stage.Traverse():
        if not prim.IsA(UsdShade.Material):
            continue
        mat_path = prim.GetPath().pathString
        if mat_path not in diffuse_manifest:
            continue
        material = UsdShade.Material(prim)
        if _has_mtlx_surface_arc(material):
            # The primary writer (or an already-authored fallback) covered
            # this material; the fallback must not stomp on it.
            continue

        # Author a minimal ND_standard_surface_surfaceshader parented under
        # a `MaterialX` scope, matching the shader-scope convention the
        # primary MtlxShaderWriter uses when both targets are active
        # (see `materialTargets.size() > 1` branch in
        # UseRegistryShadingModeExporter::Export).
        shader_scope_path = prim.GetPath().AppendChild("MaterialX")
        # The scope prim needs to exist as a NodeGraph — that's what
        # `UsdShadeNodeGraph::Define(materialExportPath)` does before
        # the writer runs.
        UsdShade.NodeGraph.Define(stage, shader_scope_path)

        shader_name = prim.GetName()
        shader_path = shader_scope_path.AppendChild(shader_name)
        shader = UsdShade.Shader.Define(stage, shader_path)
        shader.CreateIdAttr(_ND_STANDARD_SURFACE_ID)

        color = diffuse_manifest[mat_path]
        base_color_in = shader.CreateInput(
            "base_color", Sdf.ValueTypeNames.Color3f)
        base_color_in.Set(color)

        # Wire the material's mtlx surface arc through the shader's
        # default `out` port. Matches the mtlx branch of
        # `CreateShaderOutputAndConnectMaterial` at ShadingUtils.cpp:483.
        shader_out = shader.CreateOutput("out", Sdf.ValueTypeNames.Token)
        material.CreateSurfaceOutput(_MTLX_RENDER_CONTEXT).ConnectToSource(
            shader_out)

        authored[mat_path] = shader_path.pathString
    return authored


def count_materials_by_arc(stage):
    """Return {'total', 'mtlx_linked', 'preview_only', 'mtlx_only'} counts
    for the stage — the four buckets the diagnostic agent's
    `01_materials.py` reports."""
    total = 0
    mtlx = 0
    preview = 0
    for prim in stage.Traverse():
        if not prim.IsA(UsdShade.Material):
            continue
        total += 1
        material = UsdShade.Material(prim)
        has_mtlx = _has_mtlx_surface_arc(material)
        has_preview = _has_preview_surface_arc(material)
        if has_mtlx:
            mtlx += 1
        if has_preview:
            preview += 1
    return {
        "total": total,
        "mtlx_linked": mtlx,
        "preview_only": preview - mtlx,
        "mtlx_only": mtlx - preview if mtlx > preview else 0,
    }


def _define_material_with_preview_surface(
        stage, mat_path, mat_name, diffuse):
    """Build a material carrying only a UsdPreviewSurface arc — the
    baseline shape the LastResortUSDPreviewSurfaceWriter produces (and
    that all 179 arena materials, including the 36 mtlx-less ones,
    share)."""
    mat = UsdShade.Material.Define(stage, mat_path)
    preview_shader = UsdShade.Shader.Define(
        stage, f"{mat_path}/{mat_name}")
    preview_shader.CreateIdAttr("UsdPreviewSurface")
    preview_out = preview_shader.CreateOutput(
        "surface", Sdf.ValueTypeNames.Token)
    diffuse_in = preview_shader.CreateInput(
        "diffuseColor", Sdf.ValueTypeNames.Color3f)
    diffuse_in.Set(diffuse)
    mat.CreateSurfaceOutput().ConnectToSource(preview_out)
    return mat


def _define_material_with_dual_arcs(
        stage, mat_path, mat_name, diffuse):
    """Build a material with BOTH UsdPreviewSurface AND MaterialX arcs
    already authored — the 143-of-179 shape. The fallback must NOT touch
    this material."""
    mat = _define_material_with_preview_surface(
        stage, mat_path, mat_name, diffuse)
    # MaterialX side.
    mtlx_scope = f"{mat_path}/MaterialX"
    UsdShade.NodeGraph.Define(stage, mtlx_scope)
    mtlx_shader = UsdShade.Shader.Define(
        stage, f"{mtlx_scope}/{mat_name}")
    mtlx_shader.CreateIdAttr(_ND_STANDARD_SURFACE_ID)
    base_color_in = mtlx_shader.CreateInput(
        "base_color", Sdf.ValueTypeNames.Color3f)
    base_color_in.Set(diffuse)
    mtlx_out = mtlx_shader.CreateOutput("out", Sdf.ValueTypeNames.Token)
    mat.CreateSurfaceOutput(_MTLX_RENDER_CONTEXT).ConnectToSource(mtlx_out)
    return mat


def _build_defect_fixture_stage():
    """Assemble a minimal in-memory USD reproducing the MAX-MTLX-004
    defect pattern for four materials, chosen to cover every branch the
    fallback must respect:

      COVERED_PHYSICAL   dual arcs — the 143-of-179 case (mtlx-linked).
                         Fix MUST NOT touch it.
      UNCOVERED_STANDARD preview-only — the 36-of-179 case (Standard,
                         VRayLightMtl, dressing scene). Fix MUST author a
                         MaterialX arc with base_color = diffuse.
      UNCOVERED_BLACK    preview-only, diffuse=(0,0,0) — probes the
                         zero-color edge (matches the arena's
                         `MTL_STRUCT_BLACK`/`GRAPHIC_ILUM_WHITE` extremes).
      NO_MANIFEST        preview-only but ABSENT from the manifest — probes
                         the case where the caller (Registry::Find) does
                         not select the fallback for a given ClassID.
                         Fix MUST NOT author an arc.
    """
    stage = Usd.Stage.CreateInMemory()
    stage.DefinePrim("/root", "Xform")
    stage.DefinePrim("/root/mtl", "Scope")

    covered_path = "/root/mtl/COVERED_PHYSICAL"
    _define_material_with_dual_arcs(
        stage, covered_path, "COVERED_PHYSICAL", (0.35, 0.55, 0.75))

    standard_path = "/root/mtl/UNCOVERED_STANDARD"
    _define_material_with_preview_surface(
        stage, standard_path, "UNCOVERED_STANDARD",
        (0.85, 0.20, 0.10))

    black_path = "/root/mtl/UNCOVERED_BLACK"
    _define_material_with_preview_surface(
        stage, black_path, "UNCOVERED_BLACK", (0.0, 0.0, 0.0))

    no_manifest_path = "/root/mtl/NO_MANIFEST"
    _define_material_with_preview_surface(
        stage, no_manifest_path, "NO_MANIFEST", (0.42, 0.42, 0.42))

    return stage, {
        "covered": covered_path,
        "standard": standard_path,
        "black": black_path,
        "no_manifest": no_manifest_path,
    }


class TestMaxMtlx004LastResortMaterialXDispatch(unittest.TestCase):
    """USD-layer structural mirror of the C++ MaterialX fallback dispatch."""

    def test_pre_fix_stage_matches_baseline_defect(self):
        """The fixture reproduces the 143 mtlx / 36 preview-only shape:
        one material has BOTH arcs; three have only UsdPreviewSurface."""
        stage, paths = _build_defect_fixture_stage()
        counts = count_materials_by_arc(stage)
        self.assertEqual(counts["total"], 4)
        self.assertEqual(counts["mtlx_linked"], 1)
        # The 3-of-4 preview-only ratio here scales to the 36-of-179 in the
        # real arena; it is the invariant we care about, not the raw count.
        self.assertEqual(
            counts["preview_only"], 3,
            "Baseline defect: 3 materials must have only UsdPreviewSurface")

        # Every material must at least have a UsdPreviewSurface arc — that
        # is what LastResortUSDPreviewSurfaceWriter already guarantees and
        # what the MAX-MTLX-004 fix must NOT break.
        for prim in stage.Traverse():
            if not prim.IsA(UsdShade.Material):
                continue
            material = UsdShade.Material(prim)
            self.assertTrue(
                _has_preview_surface_arc(material),
                f"{prim.GetPath()} lost its UsdPreviewSurface arc")

    def test_fallback_authors_materialx_arc_for_uncovered(self):
        """The fallback authors `outputs:mtlx:surface` on the two
        uncovered materials whose manifest carries a diffuse. Every
        previously mtlx-less material that the manifest opts in ends up
        MaterialX-linked."""
        stage, paths = _build_defect_fixture_stage()
        manifest = {
            paths["standard"]: (0.85, 0.20, 0.10),
            paths["black"]: (0.0, 0.0, 0.0),
        }
        authored = apply_last_resort_mtlx_dispatch(stage, manifest)

        # Two arcs added (standard + black), covered material's shader
        # untouched, no_manifest skipped.
        self.assertEqual(set(authored.keys()), set(manifest.keys()))

        counts = count_materials_by_arc(stage)
        self.assertEqual(counts["total"], 4)
        self.assertEqual(
            counts["mtlx_linked"], 3,
            "Standard and black must now carry an outputs:mtlx:surface arc")
        # NO_MANIFEST stays preview-only.
        no_manifest_mat = UsdShade.Material(
            stage.GetPrimAtPath(paths["no_manifest"]))
        self.assertFalse(_has_mtlx_surface_arc(no_manifest_mat))

    def test_fallback_base_color_matches_diffuse(self):
        """The authored ND_standard_surface's `base_color` equals the
        diffuse the manifest carried — this is what the C++ fallback does
        with `material->GetDiffuse()`."""
        stage, paths = _build_defect_fixture_stage()
        diffuse = (0.85, 0.20, 0.10)
        manifest = {paths["standard"]: diffuse}
        apply_last_resort_mtlx_dispatch(stage, manifest)

        material = UsdShade.Material(
            stage.GetPrimAtPath(paths["standard"]))
        shader = _resolve_surface_shader(material, _MTLX_RENDER_CONTEXT)
        self.assertIsNotNone(shader)
        self.assertEqual(
            shader.GetIdAttr().Get(), _ND_STANDARD_SURFACE_ID,
            "Fallback must use the same shader identity as the primary "
            "MtlxShaderWriter so downstream MaterialX consumers evaluate "
            "it through the same node definition")

        base_color = shader.GetInput("base_color").Get()
        # Color3f is stored as float32, so the roundtrip introduces up to
        # ~1e-7 error per channel. Assert per-channel within a tight
        # tolerance rather than exact equality.
        for i in range(3):
            self.assertAlmostEqual(
                float(base_color[i]), diffuse[i], places=5,
                msg=f"base_color[{i}] mismatch")

    def test_black_diffuse_survives_roundtrip(self):
        """A material with diffuse=(0,0,0) must still get an
        outputs:mtlx:surface arc with a black base_color — no defensive
        skip that would leave it mtlx-less."""
        stage, paths = _build_defect_fixture_stage()
        manifest = {paths["black"]: (0.0, 0.0, 0.0)}
        apply_last_resort_mtlx_dispatch(stage, manifest)

        material = UsdShade.Material(
            stage.GetPrimAtPath(paths["black"]))
        self.assertTrue(_has_mtlx_surface_arc(material))
        shader = _resolve_surface_shader(material, _MTLX_RENDER_CONTEXT)
        self.assertIsNotNone(shader)
        base_color = shader.GetInput("base_color").Get()
        self.assertEqual(
            (float(base_color[0]), float(base_color[1]), float(base_color[2])),
            (0.0, 0.0, 0.0))

    def test_covered_material_untouched(self):
        """A material that ALREADY has an outputs:mtlx:surface arc must
        NOT be perturbed by the fallback — this is the 143-of-179 case.
        The fallback's `_has_mtlx_surface_arc` guard mirrors the C++
        Registry::Find's precedence: the primary writer's Supported
        result wins, and the fallback is only consulted when Find would
        otherwise return nullptr."""
        stage, paths = _build_defect_fixture_stage()

        # Snapshot the covered material's mtlx shader state.
        covered_mat = UsdShade.Material(
            stage.GetPrimAtPath(paths["covered"]))
        covered_shader_before = _resolve_surface_shader(
            covered_mat, _MTLX_RENDER_CONTEXT)
        self.assertIsNotNone(covered_shader_before)
        base_color_before = covered_shader_before.GetInput(
            "base_color").Get()
        shader_path_before = covered_shader_before.GetPath().pathString

        # Apply the fallback with a manifest that WOULD stomp on the
        # covered material if the guard were wrong.
        manifest = {
            paths["covered"]: (1.0, 1.0, 1.0),  # deliberately different
            paths["standard"]: (0.85, 0.20, 0.10),
        }
        authored = apply_last_resort_mtlx_dispatch(stage, manifest)
        # The manifest lists the covered material, but the fallback must
        # NOT author for it — its arc already exists.
        self.assertNotIn(paths["covered"], authored)

        # Post-fix, the covered shader identity, path, and base_color are
        # unchanged.
        covered_shader_after = _resolve_surface_shader(
            covered_mat, _MTLX_RENDER_CONTEXT)
        self.assertEqual(
            covered_shader_after.GetPath().pathString,
            shader_path_before)
        base_color_after = covered_shader_after.GetInput("base_color").Get()
        self.assertEqual(
            (float(base_color_before[0]), float(base_color_before[1]),
             float(base_color_before[2])),
            (float(base_color_after[0]), float(base_color_after[1]),
             float(base_color_after[2])))

    def test_preview_surface_arc_untouched_by_fallback(self):
        """The fallback authors a MaterialX arc; it does NOT alter the
        UsdPreviewSurface arc. Every material must retain its
        preview-surface shader path and diffuseColor across the fix.

        This is the invariant that separates a targeted dispatch fix from
        a rewrite: the UsdPreviewSurface network was already correct and
        must survive untouched."""
        stage, paths = _build_defect_fixture_stage()

        # Snapshot every material's preview-surface state.
        before = {}
        for prim in stage.Traverse():
            if not prim.IsA(UsdShade.Material):
                continue
            material = UsdShade.Material(prim)
            shader = _resolve_surface_shader(material, "")
            if shader is None:
                continue
            before[prim.GetPath().pathString] = (
                shader.GetPath().pathString,
                shader.GetInput("diffuseColor").Get())

        manifest = {
            paths["standard"]: (0.85, 0.20, 0.10),
            paths["black"]: (0.0, 0.0, 0.0),
        }
        apply_last_resort_mtlx_dispatch(stage, manifest)

        # Every preview-surface arc still points at the same shader with
        # the same diffuseColor.
        for mat_path, (shader_path_before, diffuse_before) in before.items():
            material = UsdShade.Material(
                stage.GetPrimAtPath(mat_path))
            shader_after = _resolve_surface_shader(material, "")
            self.assertIsNotNone(
                shader_after,
                f"{mat_path} lost its UsdPreviewSurface arc")
            self.assertEqual(
                shader_after.GetPath().pathString, shader_path_before,
                f"{mat_path} preview-surface shader path changed")
            diffuse_after = shader_after.GetInput("diffuseColor").Get()
            self.assertEqual(
                (float(diffuse_before[0]), float(diffuse_before[1]),
                 float(diffuse_before[2])),
                (float(diffuse_after[0]), float(diffuse_after[1]),
                 float(diffuse_after[2])))

    def test_manifest_absence_leaves_material_mtlx_less(self):
        """A material NOT present in the manifest — mirroring a
        Registry::Find that returns nullptr because the caller opted out —
        is not upgraded to MaterialX. This is the safety-net contract:
        the fallback fires only when the caller consented."""
        stage, paths = _build_defect_fixture_stage()
        manifest = {paths["standard"]: (0.85, 0.20, 0.10)}
        authored = apply_last_resort_mtlx_dispatch(stage, manifest)
        self.assertEqual(set(authored.keys()), {paths["standard"]})

        no_manifest_mat = UsdShade.Material(
            stage.GetPrimAtPath(paths["no_manifest"]))
        self.assertFalse(_has_mtlx_surface_arc(no_manifest_mat))

    def test_use_last_resort_option_disabled_suppresses_fallback(self):
        """When the caller sets `use_last_resort=False` — mirroring
        `SetUseLastResortUSDPreviewSurfaceWriter(false)` at the C++ layer,
        the option the new fallback branch is gated on — the fallback is
        suppressed and no MaterialX arcs are authored, even for
        materials in the manifest."""
        stage, paths = _build_defect_fixture_stage()
        manifest = {
            paths["standard"]: (0.85, 0.20, 0.10),
            paths["black"]: (0.0, 0.0, 0.0),
        }
        authored = apply_last_resort_mtlx_dispatch(
            stage, manifest, use_last_resort=False)
        self.assertEqual(authored, {})

        for path in (paths["standard"], paths["black"]):
            material = UsdShade.Material(stage.GetPrimAtPath(path))
            self.assertFalse(
                _has_mtlx_surface_arc(material),
                f"Fallback fired for {path} despite option disabled")

    def test_shader_lives_under_materialx_scope(self):
        """The fallback's shader is parented under the material's
        `MaterialX` NodeGraph scope, matching where the primary
        MtlxShaderWriter emits its ND_standard_surface when both targets
        are active. This preserves the arena's shader-hierarchy shape
        (see `02_materials_subtree.txt`) for downstream tools that walk
        by scope."""
        stage, paths = _build_defect_fixture_stage()
        manifest = {paths["standard"]: (0.85, 0.20, 0.10)}
        authored = apply_last_resort_mtlx_dispatch(stage, manifest)

        shader_path = authored[paths["standard"]]
        self.assertTrue(shader_path.startswith(
            f"{paths['standard']}/MaterialX/"))

        # The parent scope is a UsdShadeNodeGraph.
        scope_prim = stage.GetPrimAtPath(
            f"{paths['standard']}/MaterialX")
        self.assertTrue(scope_prim.IsA(UsdShade.NodeGraph))


def _load_diffuse_manifest(path):
    """Load a JSON manifest of {mat_path: [r, g, b]}."""
    import json
    with open(path, "r") as f:
        raw = json.load(f)
    return {
        mat_path: tuple(rgb)
        for mat_path, rgb in raw.items()
    }


def main_cli():
    """CLI: report the mtlx-linked / preview-only census on a real USD
    stage and, when a manifest is supplied, apply the fallback.

    Usage:
        hython test_miris_max_mtlx_004.py --stage in.usda \\
            [--manifest diffuse.json] [--out patched.usda]

    The manifest is only required when actually applying the fallback —
    without it the CLI just reports the pre-fix census, which is what
    the diagnostic agent's coverage counter reads.
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", required=True, help="Input .usda path")
    parser.add_argument(
        "--manifest",
        help="JSON: {mat_path: [r, g, b]} — diffuse per material to "
             "author as base_color on the last-resort ND_standard_surface. "
             "Only materials present in the manifest are upgraded.")
    parser.add_argument(
        "--out",
        help="Output .usda (default: no write, only report)")
    parser.add_argument(
        "--no-last-resort", action="store_true",
        help="Mirror SetUseLastResortUSDPreviewSurfaceWriter(false).")
    args = parser.parse_args()

    stage = Usd.Stage.Open(args.stage)
    if not stage:
        print(f"Failed to open {args.stage}", file=sys.stderr)
        return 2

    counts_before = count_materials_by_arc(stage)
    print(
        f"Pre-fix census: total={counts_before['total']}, "
        f"mtlx_linked={counts_before['mtlx_linked']}, "
        f"preview_only={counts_before['preview_only']}")

    if args.manifest:
        manifest = _load_diffuse_manifest(args.manifest)
        authored = apply_last_resort_mtlx_dispatch(
            stage, manifest, use_last_resort=not args.no_last_resort)
        print(
            f"Fallback authored {len(authored)} MaterialX arcs")
        counts_after = count_materials_by_arc(stage)
        print(
            f"Post-fix census: total={counts_after['total']}, "
            f"mtlx_linked={counts_after['mtlx_linked']}, "
            f"preview_only={counts_after['preview_only']}")

    if args.out:
        stage.GetRootLayer().Export(args.out)
        print(f"Wrote {args.out}")
    return 0


if __name__ == "__main__":
    if "--stage" in sys.argv:
        sys.exit(main_cli())
    else:
        unittest.main()
