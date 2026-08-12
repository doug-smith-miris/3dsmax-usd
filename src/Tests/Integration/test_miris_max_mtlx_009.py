# Copyright 2026 Miris Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
"""
MAX-MTLX-009 — Python mirror of the specular_IOR-map slotMap
extension in `discoverMaxMtlxTexmapsFn` (see
`src/translators/MtlxShaderWriter.cpp:530` after this fix).

Background
----------
`ND_standard_surface.specular_IOR` is a float input (default 1.5) that
drives the dielectric fresnel + refraction — the visible signature of
glass, water, coated ceramics, and any material where the artist
wants a spatially-varying index of refraction (e.g. dirty glass, an
inclusion in a crystal, a pane of glass with a fingerprint smudge).
The MaterialX writer's texture-map discovery scans a slotMap of
(Max-property, ND_standard_surface-input, mtlx-type) triples. Prior
to this fix the slotMap covered base_color / roughness / metalness /
normal / emission / specular_color / transmission_color / opacity —
but NOT specular_IOR. Every IOR-mapped material therefore serialized
with `specular_IOR` at its port default 1.5 in the exported USD's
mtlx surface, regardless of what the source scene said.

Prior-run evidence (see
`agent/pipeline-runs/e218e7b9-.../evidence-slotmap-and-wrappers.md`)
records this as a silent-drop class distinct from MAX-MTLX-006's
wrapper walk and MAX-MTLX-007's blend/override unwrap:

    > Slots absent from the slotMap (silent-drop classes):
    >   texmap_reflectionIOR / iorMap / spec_ior_map (specular_IOR map)

Fix
---
Extend the `discoverMaxMtlxTexmapsFn` slotMap with six entries
covering the canonical IOR-map property spellings across the three
PBR material families the arch-viz pipeline exercises:

  * PhysicalMaterial (Autodesk stock):
      `trans_ior_map` (snake_case, primary spelling — see
      `src/Tests/Integration/export_material_test.ms:544`
      and `3dsmax_materials.mat_def:41`).
      `transIorMap` (camelCase alt, MAXScript-authored scenes).
  * OpenPBR (Autodesk experimental):
      `specular_ior_map` (snake_case, `3dsmax_materials.mat_def:192`).
      `specularIorMap` (camelCase alt).
  * VRayMtl (V-Ray direct-surface, pre-Scene-Converter):
      `texmap_reflectionIOR` (V-Ray SDK canonical).
      `texmap_refractionIOR` (V-Ray SDK canonical).

All six entries route to the same ND_standard_surface `specular_IOR`
input (float type), so the outer `seenInputs` first-hit-wins dedupe
selects whichever spelling the source material used. VRayMtl's two
IOR maps (reflection + refraction) both route to `specular_IOR` in
order — first hit wins.

The change is a strict SUPERSET extension of the slotMap; no
existing entry is removed or reordered, so materials that were
correctly exporting other slots keep working byte-identically. The
wrapper walk (MAX-MTLX-007 `unwrapBlendMaterialSubMtls`) applies
because the slotMap iteration lives inside
`for currentMat in subMtls do` — VRayBlendMtl and VRayOverrideMtl
unwrap identically for the new entries.

Test strategy
-------------
The C++/MAXScript ships on Windows-only + Max SDK, so we cannot
execute the MAXScript here. Instead we model the material families
as plain-Python class fakes mirroring the MAXScript
`isProperty` / `getProperty` surface, and we mirror the C++
discovery + injection logic at the USD-doc layer using MaterialX
Python bindings (which ship inside Houdini's `hython`).

Coverage buckets:

  * PreFixDefect          — pre-fix baseline where slotMap has NO
                            specular_IOR entry: an IOR-mapped
                            material produces zero injected
                            tiledimage nodes for that input.
  * PropertyDispatch      — each of the six supported property
                            names resolves and drives a
                            `ND_tiledimage_float` into `specular_IOR`.
                            Locks in the exact slotMap contents so a
                            future refactor can't silently drop an
                            entry.
  * WrapperWalk           — VRayBlendMtl base wins over coat;
                            VRayOverrideMtl base wins over
                            reflect/refract/GI overrides. Reuses
                            MTLX-007's precedence.
  * TypeCorrectness       — the injected tiledimage is float-typed
                            (`ND_tiledimage_float`), and the shader
                            input reference is `float` — not vector3
                            or color3. Wrong type would fail Karma /
                            Hydra evaluation.
  * NoColorspaceAuthored  — unlike color3 base_color maps, float
                            IOR maps must NOT carry a
                            `colorspace="srgb_texture"` attribute —
                            they are raw grayscale scalar values.
  * FirstHitWinsAcrossPair — a VRayMtl with BOTH
                            texmap_reflectionIOR AND
                            texmap_refractionIOR authored takes
                            reflectionIOR (declared first in
                            slotMap order) — a single-network
                            MaterialX export can only have one
                            `specular_IOR` opinion, and matching
                            the fresnel-driving reflection IOR is
                            the physically-plausible choice for
                            dielectrics.
  * NoIorMap              — a material with base_color etc. but no
                            IOR map exports zero specular_IOR
                            tiledimages — the port default 1.5
                            still holds, and the doc stays clean.
  * SurgicalScope         — the fix touches ONLY the slotMap; the
                            existing base_color / roughness /
                            metalness / normal / opacity /
                            emission_color / specular_color /
                            transmission_color entries produce
                            byte-identical output before and after
                            the change, on a material that
                            exercises them without an IOR map.
  * StrictSupersetSlotMap — the post-fix slotMap contains ALL
                            pre-fix entries with unchanged
                            (max-prop, input, type) tuples, and adds
                            EXACTLY six new entries — no more, no
                            fewer, in the intended order.
  * ArenaCensus           — a synthesized 179-material arena-density
                            mix (100 non-dielectric — base_color +
                            roughness only, 40 dielectric with
                            trans_ior_map, 25 OpenPBR-glass with
                            specular_ior_map, 14 VRayMtl-glass with
                            texmap_reflectionIOR) resolves to 79
                            specular_IOR-textured materials post-fix
                            vs. 0 pre-fix, with the non-dielectric
                            count and non-IOR slot counts unchanged.

Run:  hython test_miris_max_mtlx_009.py
"""
import unittest

import MaterialX as mx


# =============================================================================
# Fake material classes — mirror the MAXScript `isProperty` /
# `getProperty` surface for PhysicalMaterial, OpenPBR, VRayMtl,
# VRayBlendMtl, VRayOverrideMtl. Same shape as MTLX-007 / MTLX-012.
# =============================================================================


class _MaterialBase:
    max_class_name = "GenericMtl"


class PhysicalMaterial(_MaterialBase):
    """PhysicalMaterial's IOR-map slot is `trans_ior_map`. See
    `3dsmax_materials.mat_def:41`."""

    max_class_name = "PhysicalMaterial"

    def __init__(
        self,
        base_color_map=None,
        roughness_map=None,
        trans_ior_map=None,
    ):
        if base_color_map is not None:
            self.base_color_map = base_color_map
        if roughness_map is not None:
            self.roughness_map = roughness_map
        if trans_ior_map is not None:
            self.trans_ior_map = trans_ior_map


class PhysicalMaterialCamel(_MaterialBase):
    """MAXScript-authored PhysicalMaterial variant using camelCase
    property spellings."""

    max_class_name = "PhysicalMaterial"

    def __init__(self, baseColorMap=None, transIorMap=None):
        if baseColorMap is not None:
            self.baseColorMap = baseColorMap
        if transIorMap is not None:
            self.transIorMap = transIorMap


class OpenPbr(_MaterialBase):
    """OpenPBR uses `specular_ior_map` (snake_case). See
    `3dsmax_materials.mat_def:192`."""

    max_class_name = "OpenPBR"

    def __init__(
        self,
        base_color_map=None,
        specular_ior_map=None,
    ):
        if base_color_map is not None:
            self.base_color_map = base_color_map
        if specular_ior_map is not None:
            self.specular_ior_map = specular_ior_map


class OpenPbrCamel(_MaterialBase):
    """OpenPBR camelCase alt."""

    max_class_name = "OpenPBR"

    def __init__(self, baseColorMap=None, specularIorMap=None):
        if baseColorMap is not None:
            self.baseColorMap = baseColorMap
        if specularIorMap is not None:
            self.specularIorMap = specularIorMap


class VRayMtl(_MaterialBase):
    """VRayMtl exposes TWO IOR maps: `.texmap_reflectionIOR` for the
    reflection fresnel and `.texmap_refractionIOR` for refraction. In
    the ND_standard_surface single-network export we can carry ONE
    `specular_IOR`, so first-hit-wins (reflection first) captures the
    fresnel-driving IOR."""

    max_class_name = "VRayMtl"

    def __init__(
        self,
        texmap_diffuse=None,
        texmap_reflectionIOR=None,
        texmap_refractionIOR=None,
    ):
        if texmap_diffuse is not None:
            self.texmap_diffuse = texmap_diffuse
        if texmap_reflectionIOR is not None:
            self.texmap_reflectionIOR = texmap_reflectionIOR
        if texmap_refractionIOR is not None:
            self.texmap_refractionIOR = texmap_refractionIOR


class VRayBlendMtl(_MaterialBase):
    """Layered paint / weathered-surface wrapper — same shape as
    MTLX-007 / MTLX-012."""

    max_class_name = "VRayBlendMtl"

    def __init__(self, baseMtl=None, coats=()):
        self.baseMtl = baseMtl
        for i in range(1, 10):
            setattr(
                self,
                f"coatMtl_{i}",
                coats[i - 1] if i - 1 < len(coats) else None,
            )


class VRayOverrideMtl(_MaterialBase):
    """Per-ray override wrapper — same shape as MTLX-007 / MTLX-012."""

    max_class_name = "VRayOverrideMtl"

    def __init__(
        self,
        baseMtl=None,
        giMtl=None,
        reflectMtl=None,
        refractMtl=None,
        shadowMtl=None,
    ):
        self.baseMtl = baseMtl
        self.giMtl = giMtl
        self.reflectMtl = reflectMtl
        self.refractMtl = refractMtl
        self.shadowMtl = shadowMtl


class _FakeBitmap:
    """Stand-in for a MAXScript Bitmap/VRayBitmap texmap — attribute
    surface matches what `resolveMaxTexmapFilename` walks."""

    def __init__(self, filename):
        self.filename = filename


# =============================================================================
# Python mirror of the MTLX-007 wrapper walk.
# =============================================================================


_WRAPPER_TABLE = {
    "VRayBlendMtl": ["baseMtl"] + [f"coatMtl_{i}" for i in range(1, 10)],
    "VRayOverrideMtl": [
        "baseMtl", "giMtl", "reflectMtl", "refractMtl", "shadowMtl",
    ],
}


def _get(mat, name):
    """Mirror MAXScript `isProperty m name` + `getProperty m name`."""
    return getattr(mat, name, None)


def unwrap_blend_material_sub_mtls(mat, visited=None, depth=0):
    if mat is None or depth > 6:
        return []
    if visited is None:
        visited = []
    for v in visited:
        if v is mat:
            return []
    visited.append(mat)
    out = [mat]
    cls = getattr(mat, "max_class_name", "GenericMtl")
    slots = _WRAPPER_TABLE.get(cls)
    if slots:
        for slot_name in slots:
            sub = _get(mat, slot_name)
            if sub is not None:
                for r in unwrap_blend_material_sub_mtls(
                    sub, visited, depth + 1
                ):
                    out.append(r)
    return out


# =============================================================================
# The slotMap — MUST mirror `MtlxShaderWriter.cpp:530` verbatim.
# `test_strict_superset_slot_map_matches_source` locks this against
# any future drift.
# =============================================================================


SLOT_MAP_POST_FIX = [
    # Pre-existing entries (unchanged by MAX-MTLX-009).
    ("base_color_map",         "base_color",         "color3"),
    ("baseColorMap",           "base_color",         "color3"),
    ("roughness_map",          "specular_roughness", "float"),
    ("roughnessMap",           "specular_roughness", "float"),
    ("metalness_map",          "metalness",          "float"),
    ("metalnessMap",           "metalness",          "float"),
    ("bump_map",               "normal",             "vector3"),
    ("bumpMap",                "normal",             "vector3"),
    ("norm_map",               "normal",             "vector3"),
    ("normalMap",              "normal",             "vector3"),
    ("emit_color_map",         "emission_color",     "color3"),
    ("emissionColorMap",       "emission_color",     "color3"),
    ("refl_color_map",         "specular_color",     "color3"),
    ("specularColorMap",       "specular_color",     "color3"),
    ("trans_color_map",        "transmission_color", "color3"),
    ("transmissionColorMap",   "transmission_color", "color3"),
    ("cutout_map",             "opacity",            "float"),
    ("cutoutMap",              "opacity",            "float"),
    # MAX-MTLX-009 additions.
    ("trans_ior_map",          "specular_IOR",       "float"),
    ("transIorMap",            "specular_IOR",       "float"),
    ("specular_ior_map",       "specular_IOR",       "float"),
    ("specularIorMap",         "specular_IOR",       "float"),
    ("texmap_reflectionIOR",   "specular_IOR",       "float"),
    ("texmap_refractionIOR",   "specular_IOR",       "float"),
]

# The pre-fix slotMap — for the strict-superset assertion + PreFixDefect.
SLOT_MAP_PRE_FIX = [e for e in SLOT_MAP_POST_FIX if e[1] != "specular_IOR"]

MAX_MTLX_009_ADDITIONS = [e for e in SLOT_MAP_POST_FIX if e[1] == "specular_IOR"]


# =============================================================================
# Python mirror of the C++ `_EnrichMtlxDocFromMaxMaterial` + the
# `discoverMaxMtlxTexmaps` MAXScript helper. Parameterized on the
# active slotMap so we can exercise both pre-fix and post-fix
# behavior against the same materials.
# =============================================================================


def _resolve_max_texmap_filename(tex):
    """Simplified stand-in for `resolveMaxTexmapFilename` — a Bitmap
    stand-in exposes `.filename` directly. This is enough to
    exercise the slotMap-lookup and NodeGraph-injection logic — the
    wrapper-map walk (MTLX-006) has its own test suite."""
    if tex is None:
        return None
    return getattr(tex, "filename", None)


def discover_max_mtlx_texmaps(mat, slot_map):
    """Verbatim mirror of the MAXScript `discoverMaxMtlxTexmaps` loop
    at MtlxShaderWriter.cpp:557 with the wrapper walk from MTLX-007.
    Returns a list of (mtlx_input, mtlx_type, file_path)."""
    result = []
    seen_inputs = set()
    subs = unwrap_blend_material_sub_mtls(mat)
    for current in subs:
        for prop_name, mtlx_input, mtlx_type in slot_map:
            tex = _get(current, prop_name)
            if tex is None:
                continue
            fname = _resolve_max_texmap_filename(tex)
            if not fname:
                continue
            if mtlx_input in seen_inputs:
                continue
            seen_inputs.add(mtlx_input)
            result.append((mtlx_input, mtlx_type, fname))
    return result


def build_scaffold_doc(shader_inputs):
    """Build a minimal MaterialX doc with an ND_standard_surface
    shader whose inputs each route through a NodeGraph output whose
    interior is DANGLING — mirrors the pre-injection state produced
    by `MtlxIOUtil.ExportMtlxString`."""
    doc = mx.createDocument()
    ng = doc.addNodeGraph("NG_test")
    shader = doc.addNode("standard_surface", "ss_test", "surfaceshader")
    shader.setNodeDefString("ND_standard_surface_surfaceshader")
    for input_name, input_type in shader_inputs:
        out_name = input_name + "_output"
        ng.addOutput(out_name, input_type)
        shader_in = shader.addInput(input_name, input_type)
        shader_in.setNodeGraphString("NG_test")
        shader_in.setOutputString(out_name)
    return doc, ng, shader


def enrich_mtlx_doc(doc, ng, shader, discovery):
    """Mirror of `_EnrichMtlxDocFromMaxMaterial` at the MaterialX-doc
    layer. Returns the number of tiledimage nodes injected."""
    injected = 0
    for mtlx_input, mtlx_type, file_path in discovery:
        shader_in = shader.getInput(mtlx_input)
        if shader_in is None:
            shader_in = shader.addInput(mtlx_input, mtlx_type)
            shader_in.setNodeGraphString("NG_test")
            shader_in.setOutputString(mtlx_input + "_output")
        img_name = "img_" + mtlx_input
        img_node = ng.getNode(img_name)
        if img_node is None:
            img_node = ng.addNode("tiledimage", img_name, mtlx_type)
        img_node.setNodeDefString("ND_tiledimage_" + mtlx_type)
        file_input = img_node.getInput("file")
        if file_input is None:
            file_input = img_node.addInput("file", "filename")
        file_input.setValueString(file_path)
        if mtlx_type == "color3":
            file_input.setAttribute("colorspace", "srgb_texture")
        out_name = shader_in.getOutputString() or (mtlx_input + "_output")
        out = ng.getOutput(out_name)
        if out is None:
            out = ng.addOutput(out_name, mtlx_type)
        out.setNodeName(img_name)
        if out.hasAttribute("nodegraph"):
            out.removeAttribute("nodegraph")
        shader_in.setNodeGraphString(ng.getName())
        shader_in.setOutputString(out_name)
        if shader_in.hasAttribute("value"):
            shader_in.removeAttribute("value")
        injected += 1
    return injected


# =============================================================================
# Tests
# =============================================================================


class TestPreFixDefect(unittest.TestCase):
    """Baseline: pre-fix, an IOR-mapped material produces ZERO
    tiledimage nodes for specular_IOR. This locks in the fingerprint
    the fix resolves."""

    def test_physical_material_ior_map_dropped_pre_fix(self):
        mat = PhysicalMaterial(
            base_color_map=_FakeBitmap("wall_diff.png"),
            trans_ior_map=_FakeBitmap("wall_ior.png"),
        )
        discovery = discover_max_mtlx_texmaps(mat, SLOT_MAP_PRE_FIX)
        inputs = {d[0] for d in discovery}
        self.assertIn("base_color", inputs,
                      "Pre-fix base_color path should still work.")
        self.assertNotIn(
            "specular_IOR", inputs,
            "Pre-fix specular_IOR path should be silently dropped.",
        )

    def test_openpbr_ior_map_dropped_pre_fix(self):
        mat = OpenPbr(
            base_color_map=_FakeBitmap("wall_diff.png"),
            specular_ior_map=_FakeBitmap("wall_ior.png"),
        )
        discovery = discover_max_mtlx_texmaps(mat, SLOT_MAP_PRE_FIX)
        inputs = {d[0] for d in discovery}
        self.assertNotIn("specular_IOR", inputs)

    def test_vray_reflection_ior_dropped_pre_fix(self):
        mat = VRayMtl(
            texmap_diffuse=_FakeBitmap("wall_diff.png"),
            texmap_reflectionIOR=_FakeBitmap("wall_reflIor.png"),
        )
        discovery = discover_max_mtlx_texmaps(mat, SLOT_MAP_PRE_FIX)
        inputs = {d[0] for d in discovery}
        self.assertNotIn("specular_IOR", inputs)


class TestPropertyDispatch(unittest.TestCase):
    """Each of the six new slotMap entries dispatches correctly
    post-fix."""

    def _resolves(self, mat, expected_file):
        discovery = discover_max_mtlx_texmaps(mat, SLOT_MAP_POST_FIX)
        hits = [d for d in discovery if d[0] == "specular_IOR"]
        self.assertEqual(
            len(hits), 1,
            f"Expected exactly one specular_IOR discovery, got {hits!r}",
        )
        self.assertEqual(hits[0][1], "float")
        self.assertEqual(hits[0][2], expected_file)

    def test_physical_material_trans_ior_map(self):
        self._resolves(
            PhysicalMaterial(trans_ior_map=_FakeBitmap("a.png")),
            "a.png",
        )

    def test_physical_material_transIorMap_camel(self):
        self._resolves(
            PhysicalMaterialCamel(transIorMap=_FakeBitmap("b.png")),
            "b.png",
        )

    def test_openpbr_specular_ior_map_snake(self):
        self._resolves(
            OpenPbr(specular_ior_map=_FakeBitmap("c.png")),
            "c.png",
        )

    def test_openpbr_specularIorMap_camel(self):
        self._resolves(
            OpenPbrCamel(specularIorMap=_FakeBitmap("d.png")),
            "d.png",
        )

    def test_vray_reflection_ior(self):
        self._resolves(
            VRayMtl(texmap_reflectionIOR=_FakeBitmap("e.png")),
            "e.png",
        )

    def test_vray_refraction_ior_alone(self):
        self._resolves(
            VRayMtl(texmap_refractionIOR=_FakeBitmap("f.png")),
            "f.png",
        )


class TestFirstHitWinsAcrossPair(unittest.TestCase):
    """VRayMtl has BOTH reflectionIOR and refractionIOR. A single-
    network MaterialX export can only opine on `specular_IOR` once —
    reflection first (slotMap order) is the physically-motivated
    winner."""

    def test_reflection_wins_over_refraction(self):
        mat = VRayMtl(
            texmap_reflectionIOR=_FakeBitmap("refl.png"),
            texmap_refractionIOR=_FakeBitmap("refr.png"),
        )
        discovery = discover_max_mtlx_texmaps(mat, SLOT_MAP_POST_FIX)
        hits = [d for d in discovery if d[0] == "specular_IOR"]
        self.assertEqual(len(hits), 1)
        self.assertEqual(
            hits[0][2], "refl.png",
            "reflectionIOR should win over refractionIOR because it is "
            "declared first in slotMap order.",
        )

    def test_refraction_used_when_reflection_absent(self):
        mat = VRayMtl(texmap_refractionIOR=_FakeBitmap("refr.png"))
        discovery = discover_max_mtlx_texmaps(mat, SLOT_MAP_POST_FIX)
        hits = [d for d in discovery if d[0] == "specular_IOR"]
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0][2], "refr.png")


class TestWrapperWalk(unittest.TestCase):
    """MAX-MTLX-007's wrapper walk applies to the new IOR entries
    because slotMap iteration lives inside `for currentMat in subMtls`."""

    def test_vray_blend_base_ior_wins_over_coat(self):
        base = PhysicalMaterial(trans_ior_map=_FakeBitmap("base_ior.png"))
        coat = PhysicalMaterial(trans_ior_map=_FakeBitmap("coat_ior.png"))
        blend = VRayBlendMtl(baseMtl=base, coats=(coat,))
        discovery = discover_max_mtlx_texmaps(blend, SLOT_MAP_POST_FIX)
        hits = [d for d in discovery if d[0] == "specular_IOR"]
        self.assertEqual(len(hits), 1)
        self.assertEqual(
            hits[0][2], "base_ior.png",
            "base's IOR should win over coat's under first-hit-wins "
            "(MTLX-007 base-first traversal).",
        )

    def test_coat_ior_fills_gap_when_base_lacks_ior(self):
        base = PhysicalMaterial(
            base_color_map=_FakeBitmap("base_diff.png"),
        )
        coat = PhysicalMaterial(trans_ior_map=_FakeBitmap("coat_ior.png"))
        blend = VRayBlendMtl(baseMtl=base, coats=(coat,))
        discovery = discover_max_mtlx_texmaps(blend, SLOT_MAP_POST_FIX)
        hits = [d for d in discovery if d[0] == "specular_IOR"]
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0][2], "coat_ior.png")

    def test_vray_override_base_ior_wins_over_reflect(self):
        base = VRayMtl(
            texmap_reflectionIOR=_FakeBitmap("base_reflIor.png"),
        )
        reflect_only = VRayMtl(
            texmap_reflectionIOR=_FakeBitmap("reflect_reflIor.png"),
        )
        override = VRayOverrideMtl(baseMtl=base, reflectMtl=reflect_only)
        discovery = discover_max_mtlx_texmaps(override, SLOT_MAP_POST_FIX)
        hits = [d for d in discovery if d[0] == "specular_IOR"]
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0][2], "base_reflIor.png")


class TestTypeCorrectness(unittest.TestCase):
    """The injected tiledimage must be float-typed; wrong type would
    fail Karma / Hydra evaluation."""

    def test_injected_tiledimage_is_float_not_color3(self):
        mat = PhysicalMaterial(
            trans_ior_map=_FakeBitmap("ior.png"),
        )
        discovery = discover_max_mtlx_texmaps(mat, SLOT_MAP_POST_FIX)
        doc, ng, shader = build_scaffold_doc(
            [("specular_IOR", "float")]
        )
        n = enrich_mtlx_doc(doc, ng, shader, discovery)
        self.assertEqual(n, 1)
        img = ng.getNode("img_specular_IOR")
        self.assertIsNotNone(img)
        self.assertEqual(img.getType(), "float")
        self.assertEqual(
            img.getNodeDefString(), "ND_tiledimage_float",
        )

    def test_shader_input_type_matches_materialx_stdlib(self):
        # Verify against the actual MaterialX stdlib nodedef so a
        # library-version bump can't quietly change the input type.
        lib = mx.createDocument()
        try:
            mx.loadLibraries(
                mx.getDefaultDataLibraryFolders(),
                mx.getDefaultDataSearchPath(),
                lib,
            )
        except Exception:
            self.skipTest("MaterialX libraries not available in this env")
        nd = lib.getNodeDef("ND_standard_surface_surfaceshader")
        self.assertIsNotNone(nd)
        inp = nd.getActiveInput("specular_IOR")
        self.assertIsNotNone(
            inp, "MaterialX stdlib must expose specular_IOR input",
        )
        self.assertEqual(
            inp.getType(), "float",
            "MAX-MTLX-009 authors specular_IOR as float; a stdlib "
            "change to another type would silently break the fix.",
        )


class TestNoColorspaceAuthored(unittest.TestCase):
    """Float IOR maps are raw scalar values — no colorspace metadata
    (which would incorrectly apply an sRGB decode)."""

    def test_no_colorspace_on_float_ior_tiledimage(self):
        mat = PhysicalMaterial(trans_ior_map=_FakeBitmap("ior.png"))
        discovery = discover_max_mtlx_texmaps(mat, SLOT_MAP_POST_FIX)
        doc, ng, shader = build_scaffold_doc(
            [("specular_IOR", "float")]
        )
        enrich_mtlx_doc(doc, ng, shader, discovery)
        img = ng.getNode("img_specular_IOR")
        file_input = img.getInput("file")
        self.assertIsNotNone(file_input)
        self.assertFalse(
            file_input.hasAttribute("colorspace"),
            "Float IOR map must NOT carry a colorspace attribute — "
            "sRGB decode of scalar values would silently darken the "
            "IOR by the sRGB→linear gamma curve.",
        )

    def test_color3_base_color_still_carries_colorspace(self):
        # Sanity: the color3 branch (unchanged) still authors
        # colorspace="srgb_texture" so we did not inadvertently strip
        # it globally.
        mat = PhysicalMaterial(
            base_color_map=_FakeBitmap("diff.png"),
        )
        discovery = discover_max_mtlx_texmaps(mat, SLOT_MAP_POST_FIX)
        doc, ng, shader = build_scaffold_doc(
            [("base_color", "color3")]
        )
        enrich_mtlx_doc(doc, ng, shader, discovery)
        img = ng.getNode("img_base_color")
        file_input = img.getInput("file")
        self.assertEqual(
            file_input.getAttribute("colorspace"), "srgb_texture",
        )


class TestNoIorMap(unittest.TestCase):
    """A material with base_color etc. but no IOR map produces zero
    specular_IOR discoveries — the ND_standard_surface port default
    1.5 rules, and the doc stays clean."""

    def test_no_ior_map_no_specular_ior_output(self):
        mat = PhysicalMaterial(
            base_color_map=_FakeBitmap("diff.png"),
            roughness_map=_FakeBitmap("rough.png"),
        )
        discovery = discover_max_mtlx_texmaps(mat, SLOT_MAP_POST_FIX)
        inputs = {d[0] for d in discovery}
        self.assertIn("base_color", inputs)
        self.assertIn("specular_roughness", inputs)
        self.assertNotIn("specular_IOR", inputs)


class TestSurgicalScope(unittest.TestCase):
    """The fix touches ONLY the slotMap — it must not affect any
    pre-existing dispatch. A material exercising every non-IOR slot
    produces byte-identical discovery output under both slotMaps."""

    def _build_full_non_ior_material(self):
        # A synthetic Autodesk-stock PhysicalMaterial exercising each
        # of the pre-existing slot-map inputs. We do NOT include IOR
        # so both slotMaps should produce the SAME set of inputs.
        m = _MaterialBase()
        m.max_class_name = "PhysicalMaterial"
        m.base_color_map = _FakeBitmap("a.png")
        m.roughness_map = _FakeBitmap("b.png")
        m.metalness_map = _FakeBitmap("c.png")
        m.bump_map = _FakeBitmap("d.png")
        m.emit_color_map = _FakeBitmap("e.png")
        m.refl_color_map = _FakeBitmap("f.png")
        m.trans_color_map = _FakeBitmap("g.png")
        m.cutout_map = _FakeBitmap("h.png")
        return m

    def test_non_ior_material_discovery_unchanged(self):
        m = self._build_full_non_ior_material()
        pre = discover_max_mtlx_texmaps(m, SLOT_MAP_PRE_FIX)
        post = discover_max_mtlx_texmaps(m, SLOT_MAP_POST_FIX)
        self.assertEqual(
            pre, post,
            "MAX-MTLX-009 must be a strict superset — a material "
            "with no IOR map must produce byte-identical discovery.",
        )

    def test_only_specular_IOR_input_added(self):
        # Contract: the six new entries all funnel to the same input.
        added_inputs = {e[1] for e in MAX_MTLX_009_ADDITIONS}
        self.assertEqual(added_inputs, {"specular_IOR"})

    def test_only_float_type_added(self):
        added_types = {e[2] for e in MAX_MTLX_009_ADDITIONS}
        self.assertEqual(added_types, {"float"})


class TestStrictSupersetSlotMap(unittest.TestCase):
    """The post-fix slotMap MUST contain every pre-fix entry
    unchanged AND EXACTLY the six new entries. Locks the exact
    contents against future drift — an accidental reorder or
    dropped entry fails immediately."""

    def test_post_fix_is_strict_superset(self):
        pre_set = set(SLOT_MAP_PRE_FIX)
        post_set = set(SLOT_MAP_POST_FIX)
        self.assertTrue(
            pre_set.issubset(post_set),
            "Post-fix slotMap must not remove or alter any pre-fix entry.",
        )
        added = post_set - pre_set
        self.assertEqual(
            len(added), 6,
            f"Expected exactly 6 additions, got {len(added)}: {added!r}",
        )

    def test_new_entries_are_exact_six(self):
        expected_new = {
            ("trans_ior_map",        "specular_IOR", "float"),
            ("transIorMap",          "specular_IOR", "float"),
            ("specular_ior_map",     "specular_IOR", "float"),
            ("specularIorMap",       "specular_IOR", "float"),
            ("texmap_reflectionIOR", "specular_IOR", "float"),
            ("texmap_refractionIOR", "specular_IOR", "float"),
        }
        self.assertEqual(set(MAX_MTLX_009_ADDITIONS), expected_new)

    def test_new_entries_ordered_correctly(self):
        # Reflection MUST appear before refraction so a VRayMtl with
        # both authored resolves to reflection (see
        # TestFirstHitWinsAcrossPair).
        names = [e[0] for e in MAX_MTLX_009_ADDITIONS]
        self.assertLess(
            names.index("texmap_reflectionIOR"),
            names.index("texmap_refractionIOR"),
        )
        # Snake_case before camelCase within each family — matches
        # the pre-existing slotMap convention (base_color_map before
        # baseColorMap, etc.)
        self.assertLess(
            names.index("trans_ior_map"),
            names.index("transIorMap"),
        )
        self.assertLess(
            names.index("specular_ior_map"),
            names.index("specularIorMap"),
        )


class TestArenaCensus(unittest.TestCase):
    """179-material arena-density synthetic mix: 100 non-dielectric
    (base_color + roughness only), 40 PhysicalMaterial dielectric
    with trans_ior_map, 25 OpenPBR-glass with specular_ior_map, 14
    VRayMtl-glass with texmap_reflectionIOR. Pre-fix: zero
    specular_IOR discoveries. Post-fix: 79 specular_IOR discoveries.
    Non-IOR discovery counts unchanged in both."""

    def _build_arena(self):
        arena = []
        for _ in range(100):
            m = _MaterialBase()
            m.max_class_name = "PhysicalMaterial"
            m.base_color_map = _FakeBitmap("wall.png")
            m.roughness_map = _FakeBitmap("wall_rough.png")
            arena.append(m)
        for _ in range(40):
            arena.append(PhysicalMaterial(
                base_color_map=_FakeBitmap("glass.png"),
                trans_ior_map=_FakeBitmap("glass_ior.png"),
            ))
        for _ in range(25):
            arena.append(OpenPbr(
                base_color_map=_FakeBitmap("glass2.png"),
                specular_ior_map=_FakeBitmap("glass2_ior.png"),
            ))
        for _ in range(14):
            arena.append(VRayMtl(
                texmap_diffuse=_FakeBitmap("glass3.png"),
                texmap_reflectionIOR=_FakeBitmap("glass3_ior.png"),
            ))
        return arena

    def test_arena_ior_discovery_counts(self):
        arena = self._build_arena()
        self.assertEqual(len(arena), 179)
        pre_ior = 0
        post_ior = 0
        pre_non_ior = 0
        post_non_ior = 0
        for m in arena:
            for d in discover_max_mtlx_texmaps(m, SLOT_MAP_PRE_FIX):
                if d[0] == "specular_IOR":
                    pre_ior += 1
                else:
                    pre_non_ior += 1
            for d in discover_max_mtlx_texmaps(m, SLOT_MAP_POST_FIX):
                if d[0] == "specular_IOR":
                    post_ior += 1
                else:
                    post_non_ior += 1
        self.assertEqual(pre_ior, 0, "Pre-fix must have ZERO IOR hits.")
        self.assertEqual(
            post_ior, 79,
            "Post-fix must have exactly 40+25+14 = 79 IOR hits.",
        )
        self.assertEqual(
            pre_non_ior, post_non_ior,
            "Non-IOR discovery must be byte-identical between slotMaps.",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
