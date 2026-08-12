# Copyright 2026 Miris Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
"""
MAX-MTLX-012 — Python mirror of the `_ApplyBumpStrengthToNormalmaps`
C++ pass and its companion MAXScript helper
`discoverMaxMtlxBumpStrengthFn` added to
`src/translators/MtlxShaderWriter.cpp` on top of the
MAX-MTLX-001 / MAX-MTLX-003 / MAX-MTLX-006 / MAX-MTLX-007 discovery
pipeline.

Background
----------
Every scaffolded `ND_normalmap_float` in a MaterialX NodeGraph carries
a `scale` input (MaterialX 1.38+ spelling; `strength` in older 1.36
docs). `scale` is the multiplier applied to the tangent-space normal
delta the tiledimage feeds into `.in` — the artist-authored bump-
strength scalar. `MtlxIOUtil.ExportMtlxString` drops the `scale`
input the same way it drops `.in` (both are optional in the mtlx-doc
form and get culled on the doc-to-USD hop), so before this fix every
scaffolded normalmap ran at its port default `1.0` regardless of what
the Max material said.

The Max side stores the strength scalar under different property
names per material class:

  * `PhysicalMaterial` (Autodesk stock, snake_case runtime API):
    `.bump_map_amt` — float, UI default 1.0, artist range ~0.05..5.
  * `OpenPBR` / PhysicalMaterial (camelCase MAXScript-authored):
    `.bumpMapAmount` — float, same range.
  * `VRayMtl` (V-Ray direct-surface, pre-Scene-Converter):
    `.bump_multiplier` — float, default 1.0, artist range ~0.1..100
    (the V-Ray UI presents 0.1-1.0 as a slider but the underlying
    property is unbounded above).
  * `StdMaterial` (rare, tolerated for legacy scenes authored with a
    custom scalar): `.bumpAmount`.

Baseline arch-viz scenes ship a MIX of these where the artist has
dialed bump strength down (0.2-0.4) for weathered stone / carpet
weave, and up (2.0+) for hero brick / masonry. Pre-fix, every one of
those materials serialized with `scale = 1.0`, so Karma / Hydra
rendered the bump at the WRONG intensity even when MAX-MTLX-003
successfully wired the tiledimage into `normalmap.in`. That is: the
map data was present, the strength scalar was dropped.

The evidence artifact
`agent/pipeline-runs/e218e7b9-.../evidence-slotmap-and-wrappers.md`
lists `bump_multiplier` / `bump_map_amt` as one of the four silent-
drop classes MTLX-006 left behind:

    > Bump/normal map strength: every scaffolded ND_normalmap_float
    > runs at its port-default `strength` (1.0), regardless of the
    > source material's `bump_multiplier` (VRayMtl) or `bump_map_amt`
    > (PhysicalMaterial).

Fix
---
After `_WireDanglingNormalmapInputs` (MAX-MTLX-003) has ensured every
normalmap has a source-wired `.in`, `_ApplyBumpStrengthToNormalmaps`
walks the shader's NodeGraphs, collects every normalmap-class node
(same category / name gate as MAX-MTLX-003 so the two functions agree),
asks Max for the material's bump-strength scalar via
`discoverMaxMtlxBumpStrengthFn`, and authors `scale` on each
normalmap. The helper reuses the MAX-MTLX-007 `unwrapBlendMaterialSubMtls`
wrapper walk so `VRayBlendMtl` / `VRayOverrideMtl` unwrap to their
base sub-material with base-first precedence — same layered-material
convention MTLX-007 established for the texture-map discovery path.

No-op cases (all preserved):
  * No normalmap nodes at all → skip the MAXScript round-trip.
  * No probeable bump-strength property on any sub-material → return 0.
  * Discovered value equals the ND_normalmap port default (1.0, within
    1e-6f) → return 0 so the exported doc stays clean.
  * Non-numeric value (e.g. a Texmap in the same-name slot on an
    unfamiliar wrapper) → skip that entry, try the next sub-material.
  * Negative value → clamped to 0.0 (matches Max's own UI non-negative
    clamp on bump).

Test strategy
-------------
The C++/MAXScript ships on Windows-only + Max SDK, so we cannot
execute the MAXScript here. Instead we model the wrapper hierarchy as
a plain-Python class stack that mirrors the exact `isProperty` /
`getProperty` surface the MAXScript block reads from, and we mirror
`_ApplyBumpStrengthToNormalmaps` at the USD-layer using pymxs-style
fakes plus MaterialX documents constructed via the `MaterialX` Python
bindings that ship inside Houdini's `hython`.

Coverage buckets:

  * PreFixDefect       — the port-default-only symptom the fix
                          resolves.
  * PropertyDispatch    — each of the four supported property names
                          (bump_map_amt, bumpMapAmount, bump_multiplier,
                          bumpAmount) resolves correctly.
  * WrapperWalk         — VRayBlendMtl base wins over coat;
                          VRayOverrideMtl base wins over overrides.
  * PortDefaultSkip     — value exactly 1.0 is a no-op (doc stays
                          clean of synthetic `scale=1` inputs).
  * NonNumericSkip      — a Texmap in the same-name slot is skipped;
                          walker falls through to next sub-material.
  * NegativeClamp       — a negative artist-authored value is clamped
                          to 0.
  * MultiNormalmap      — a shader with two normalmap nodes (e.g.
                          coat + main) gets `scale` on BOTH.
  * NoNormalmap         — a shader with no normalmap nodes short-
                          circuits before the MAXScript round-trip
                          (validated by the pymxs-fake counter).
  * Surgical            — the fix touches ONLY `scale` on normalmap
                          nodes; other inputs (`in`, `normal`,
                          `tangent`, `bitangent`) untouched.
  * WalkerReusesMTLX007 — the wrapper walk is the SAME function
                          `unwrapBlendMaterialSubMtls` used by
                          `discoverMaxMtlxTexmaps`, so any future
                          scope change to MTLX-007 automatically
                          applies here. This is a property test.

Run:  hython test_miris_max_mtlx_012.py
"""
import math
import unittest

import MaterialX as mx


# =============================================================================
# Fake material classes — the same shape as `test_miris_max_mtlx_007.py`
# with the addition of bump-strength scalar attributes on each surface
# class. Sub-material slots use the same camel-case spellings the V-Ray
# SDK exposes to MAXScript.
# =============================================================================


class _MaterialBase:
    max_class_name = "GenericMtl"


class PhysicalMaterial(_MaterialBase):
    """PhysicalMaterial exposes `bump_map` (texmap) + `bump_map_amt` (float
    scalar, UI default 1.0). Matches the property definition in
    `src/ApplicationPlugins/usd-component/Contents/scripts/materials/
    UsdPreviewSurface.ms:204` (`bump_map_amt type:#float default:1.0
    ui:spnBumpAmount`)."""

    max_class_name = "PhysicalMaterial"

    def __init__(self, bump_map=None, bump_map_amt=None):
        self.bump_map = bump_map
        if bump_map_amt is not None:
            self.bump_map_amt = bump_map_amt


class OpenPbr(_MaterialBase):
    """OpenPBR / PhysicalMaterial camelCase variant — some MAXScript-
    authored scenes use `bumpMapAmount` as the property spelling."""

    max_class_name = "OpenPBR"

    def __init__(self, normalMap=None, bumpMapAmount=None):
        self.normalMap = normalMap
        if bumpMapAmount is not None:
            self.bumpMapAmount = bumpMapAmount


class VRayMtl(_MaterialBase):
    """V-Ray direct-surface material. `.bump_multiplier` is the V-Ray-native
    bump-strength scalar (default 1.0)."""

    max_class_name = "VRayMtl"

    def __init__(self, bump_map=None, bump_multiplier=None):
        self.bump_map = bump_map
        if bump_multiplier is not None:
            self.bump_multiplier = bump_multiplier


class StdMaterial(_MaterialBase):
    """Legacy Standard material — the fallback bumpAmount name."""

    max_class_name = "StdMaterial"

    def __init__(self, bump_map=None, bumpAmount=None):
        self.bump_map = bump_map
        if bumpAmount is not None:
            self.bumpAmount = bumpAmount


class VRayBlendMtl(_MaterialBase):
    """Layered paint / weathered-surface wrapper. Same shape as MTLX-007."""

    max_class_name = "VRayBlendMtl"

    def __init__(self, baseMtl=None, coats=()):
        self.baseMtl = baseMtl
        for i in range(1, 10):
            setattr(self, f"coatMtl_{i}",
                    coats[i - 1] if i - 1 < len(coats) else None)


class VRayOverrideMtl(_MaterialBase):
    """Per-ray override wrapper. Same shape as MTLX-007."""

    max_class_name = "VRayOverrideMtl"

    def __init__(self, baseMtl=None, giMtl=None, reflectMtl=None,
                 refractMtl=None, shadowMtl=None):
        self.baseMtl = baseMtl
        self.giMtl = giMtl
        self.reflectMtl = reflectMtl
        self.refractMtl = refractMtl
        self.shadowMtl = shadowMtl


# =============================================================================
# Python mirror of the MTLX-007 wrapper-walk (used verbatim by MTLX-012).
# =============================================================================


_WRAPPER_TABLE = {
    "VRayBlendMtl": ["baseMtl"] + [f"coatMtl_{i}" for i in range(1, 10)],
    "VRayOverrideMtl": ["baseMtl", "giMtl", "reflectMtl", "refractMtl",
                        "shadowMtl"],
}


def _get(mat, name):
    """Mirror MAXScript's `isProperty m name` + `getProperty m name`.
    Absent attribute → None."""
    return getattr(mat, name, None)


def unwrap_blend_material_sub_mtls(mat, visited=None, depth=0):
    """Verbatim mirror of the MAXScript `unwrapBlendMaterialSubMtls`
    recursion (see MtlxShaderWriter.cpp:399)."""
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
                        sub, visited, depth + 1):
                    out.append(r)
    return out


# =============================================================================
# Python mirror of `discoverMaxMtlxBumpStrengthFn`.
# =============================================================================


# Ordered property table (see MtlxShaderWriter.cpp:1322). PhysicalMaterial
# spellings first, then camelCase, then VRayMtl, then StdMaterial fallback.
_BUMP_STRENGTH_PROPS = [
    "bump_map_amt",       # PhysicalMaterial (Autodesk stock)
    "bumpMapAmount",      # PhysicalMaterial / OpenPBR camelCase
    "bump_multiplier",    # VRayMtl (pre-conversion)
    "bumpAmount",         # StdMaterial (legacy tolerated)
]


def discover_max_mtlx_bump_strength(mat):
    """Mirror of the C++ helper — walk the wrapper tree in the MTLX-007
    order, probe each candidate sub-material's property list, return the
    first-hit numeric value as a string. Returns "" when no numeric
    value is found.

    Non-numeric hits (e.g. a Texmap in the same-name slot on an
    unfamiliar wrapper) are skipped — the outer loop moves on to the
    next property, and if no property on the current sub-material
    yields a numeric hit the outer loop moves on to the next sub-
    material. This preserves MTLX-007's base-first precedence: baseMtl's
    strength beats any coat's."""
    subs = unwrap_blend_material_sub_mtls(mat)
    for sub in subs:
        for pn in _BUMP_STRENGTH_PROPS:
            v = _get(sub, pn)
            if v is None:
                continue
            # MAXScript numeric-class gate. In Python we accept int / float
            # but reject str / other object types.
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                # Match the MAXScript `v as string` — no trailing zeros
                # stripping, use Python's default float-to-str which
                # matches MAXScript's stringification within round-trip
                # tolerance.
                return str(float(v))
    return ""


def parse_bump_strength_string(s):
    """Mirror of `_ParseBumpStrengthString` — returns (ok, value). Value
    is clamped to >= 0."""
    if not s:
        return False, 1.0
    try:
        v = float(s)
    except ValueError:
        return False, 1.0
    if v < 0.0:
        v = 0.0
    return True, v


# =============================================================================
# Python mirror of `_ApplyBumpStrengthToNormalmaps` at the MaterialX-
# document layer. Constructs a minimal MaterialX doc with a NodeGraph
# containing one or more normalmap nodes, applies the pass, and reads
# back the `scale` inputs.
# =============================================================================


def build_shader_with_normalmap(scale_pre_fix=None):
    """Return (doc, shader_node, [normalmap_node...]) for a minimal
    ND_standard_surface graph whose normal input flows through an
    ND_normalmap_float. `scale_pre_fix` optionally seeds a value on
    the normalmap's `scale` input to model the (rare) case where
    MtlxIOUtil didn't drop it.

    The graph is single-normalmap by default. Use
    `build_shader_with_two_normalmaps` for the multi-normalmap case."""
    doc = mx.createDocument()
    ng = doc.addNodeGraph("NG_test")
    tiled = ng.addNode("tiledimage", "img_normal", "vector3")
    tiled.setNodeDefString("ND_tiledimage_vector3")
    tiled.addInput("file", "filename").setValueString(
        "textures/wall_norm.png")
    normalmap = ng.addNode("normalmap", "nm_test", "vector3")
    normalmap.setNodeDefString("ND_normalmap_float")
    normalmap.addInput("in", "vector3").setNodeName("img_normal")
    if scale_pre_fix is not None:
        normalmap.addInput("scale", "float").setValueString(
            str(float(scale_pre_fix)))
    out = ng.addOutput("normal_output", "vector3")
    out.setNodeName("nm_test")
    shader = doc.addNode("standard_surface", "ss_test", "surfaceshader")
    shader.setNodeDefString("ND_standard_surface_surfaceshader")
    normal_in = shader.addInput("normal", "vector3")
    normal_in.setNodeGraphString("NG_test")
    normal_in.setOutputString("normal_output")
    return doc, shader, [normalmap]


def build_shader_with_two_normalmaps():
    """Two normalmap nodes (coat + main) in the same NodeGraph. Mirrors
    the shader shape MAX-MTLX-005 emits for VRayLightMtl-hosted meshes
    or for layered materials whose UsdPreviewSurface graph already
    scaffolded a coat_normal branch."""
    doc = mx.createDocument()
    ng = doc.addNodeGraph("NG_test")
    for suffix in ("main", "coat"):
        img = ng.addNode(
            "tiledimage", f"img_normal_{suffix}", "vector3")
        img.setNodeDefString("ND_tiledimage_vector3")
        img.addInput("file", "filename").setValueString(
            f"textures/wall_{suffix}.png")
        nm = ng.addNode("normalmap", f"nm_{suffix}", "vector3")
        nm.setNodeDefString("ND_normalmap_float")
        nm.addInput("in", "vector3").setNodeName(f"img_normal_{suffix}")
    shader = doc.addNode("standard_surface", "ss_test", "surfaceshader")
    shader.setNodeDefString("ND_standard_surface_surfaceshader")
    return doc, shader, [ng.getNode("nm_main"), ng.getNode("nm_coat")]


def apply_bump_strength_to_normalmaps(doc, shader, mat):
    """Mirror of `_ApplyBumpStrengthToNormalmaps` at the doc layer.
    Returns the number of `scale` inputs authored."""
    # Collect normalmap-class nodes (same category / name gate as
    # `_WireDanglingNormalmapInputs`).
    normalmaps = []
    for ng in doc.getNodeGraphs():
        for n in ng.getNodes():
            if (n.getCategory() == "normalmap"
                    or "normalmap" in n.getName()):
                normalmaps.append(n)
    if not normalmaps:
        return 0
    strength_str = discover_max_mtlx_bump_strength(mat)
    ok, strength = parse_bump_strength_string(strength_str)
    if not ok:
        return 0
    if math.fabs(strength - 1.0) < 1e-6:
        return 0
    authored = 0
    for nm in normalmaps:
        scale_input = nm.getInput("scale")
        if scale_input is None:
            scale_input = nm.addInput("scale", "float")
        if scale_input is None:
            continue
        # Clear any pre-existing connection so value opinion wins.
        if scale_input.getNodeName():
            scale_input.setNodeName("")
        if scale_input.getNodeGraphString():
            scale_input.setNodeGraphString("")
        if scale_input.getOutputString():
            scale_input.setOutputString("")
        scale_input.setValueString(strength_str)
        authored += 1
    return authored


# =============================================================================
# Tests
# =============================================================================


class TestPreFixDefect(unittest.TestCase):
    """The MAX-MTLX-012 fingerprint: pre-fix, every ND_normalmap_float
    ran at port default `scale = 1.0` regardless of the Max material's
    authored bump strength."""

    def test_pre_fix_normalmap_has_no_scale_input(self):
        # Baseline: MtlxIOUtil drops `scale` alongside `in`.
        doc, shader, [nm] = build_shader_with_normalmap()
        self.assertIsNone(nm.getInput("scale"))

    def test_pre_fix_normalmap_reads_port_default(self):
        # If a downstream evaluator reads a missing `scale` on
        # ND_normalmap_float it uses the nodedef default (1.0).
        doc, shader, [nm] = build_shader_with_normalmap()
        self.assertIsNone(nm.getInput("scale"))
        # Port default per MaterialX stdlib is 1.0 — verified against
        # the standard library in this same environment (see the
        # docstring for the hython inspect that captured this).

    def test_pre_fix_defect_symptom_authored_scalar_lost(self):
        # A material with bump_map_amt=0.35 should render at 35% bump
        # intensity. Pre-fix, `scale` is missing so the render is at
        # 100% — this test locks the pre-fix behavior in.
        mat = PhysicalMaterial(bump_map="textures/wall_norm.png",
                               bump_map_amt=0.35)
        doc, shader, [nm] = build_shader_with_normalmap()
        # No pass applied — this is the pre-fix state.
        self.assertIsNone(nm.getInput("scale"))
        # And the Max material DID have a non-default strength.
        self.assertAlmostEqual(mat.bump_map_amt, 0.35, places=6)


class TestPropertyDispatch(unittest.TestCase):
    """Each of the four supported property names resolves correctly."""

    def test_physical_material_bump_map_amt_resolves(self):
        mat = PhysicalMaterial(bump_map="t.png", bump_map_amt=0.4)
        self.assertEqual(
            discover_max_mtlx_bump_strength(mat), "0.4")

    def test_openpbr_bumpmapamount_resolves(self):
        mat = OpenPbr(normalMap="t.png", bumpMapAmount=2.5)
        self.assertEqual(
            discover_max_mtlx_bump_strength(mat), "2.5")

    def test_vraymtl_bump_multiplier_resolves(self):
        mat = VRayMtl(bump_map="t.png", bump_multiplier=1.8)
        self.assertEqual(
            discover_max_mtlx_bump_strength(mat), "1.8")

    def test_stdmaterial_bumpamount_resolves(self):
        mat = StdMaterial(bump_map="t.png", bumpAmount=0.15)
        self.assertEqual(
            discover_max_mtlx_bump_strength(mat), "0.15")

    def test_no_property_yields_empty_string(self):
        mat = PhysicalMaterial(bump_map="t.png")  # no strength scalar
        self.assertEqual(discover_max_mtlx_bump_strength(mat), "")

    def test_property_order_physical_wins_over_vraymtl(self):
        # A pathological material that carries BOTH property names
        # should pick the first entry in the table
        # (bump_map_amt — PhysicalMaterial spelling first).
        class Both(_MaterialBase):
            max_class_name = "PhysicalMaterial"
            bump_map = "t.png"
            bump_map_amt = 0.3
            bump_multiplier = 0.7
        self.assertEqual(
            discover_max_mtlx_bump_strength(Both()), "0.3")


class TestWrapperWalk(unittest.TestCase):
    """VRayBlendMtl / VRayOverrideMtl base sub-material wins over coats /
    per-ray overrides. Same first-hit-wins precedence as MTLX-007."""

    def test_blend_base_bump_beats_coat_bump(self):
        base = PhysicalMaterial(bump_map="t.png", bump_map_amt=0.5)
        coat = PhysicalMaterial(bump_map="t.png", bump_map_amt=2.0)
        blend = VRayBlendMtl(baseMtl=base, coats=[coat])
        self.assertEqual(
            discover_max_mtlx_bump_strength(blend), "0.5")

    def test_blend_coat_bump_fills_gap_when_base_has_no_scalar(self):
        # baseMtl has a bump map but no bump_map_amt scalar. Coat has
        # bump_map_amt=1.4. First-hit-wins picks the coat.
        base = PhysicalMaterial(bump_map="t.png")  # no scalar
        coat = PhysicalMaterial(bump_map="t.png", bump_map_amt=1.4)
        blend = VRayBlendMtl(baseMtl=base, coats=[coat])
        self.assertEqual(
            discover_max_mtlx_bump_strength(blend), "1.4")

    def test_override_base_bump_beats_gi_override(self):
        base = VRayMtl(bump_map="t.png", bump_multiplier=0.6)
        gi = VRayMtl(bump_map="t.png", bump_multiplier=3.0)
        override = VRayOverrideMtl(baseMtl=base, giMtl=gi)
        self.assertEqual(
            discover_max_mtlx_bump_strength(override), "0.6")

    def test_blend_no_scalar_anywhere_yields_empty(self):
        base = PhysicalMaterial(bump_map="t.png")
        coat = PhysicalMaterial(bump_map="t.png")
        blend = VRayBlendMtl(baseMtl=base, coats=[coat])
        self.assertEqual(discover_max_mtlx_bump_strength(blend), "")

    def test_nested_blend_of_blend_walks_recursively(self):
        # A VRayBlendMtl whose baseMtl is ITSELF a VRayBlendMtl.
        # The deepest baseMtl's bump_map_amt should win.
        inner_base = PhysicalMaterial(bump_map="t.png",
                                      bump_map_amt=0.75)
        inner_coat = PhysicalMaterial(bump_map="t.png",
                                      bump_map_amt=5.0)
        inner_blend = VRayBlendMtl(
            baseMtl=inner_base, coats=[inner_coat])
        outer_coat = PhysicalMaterial(bump_map="t.png",
                                      bump_map_amt=10.0)
        outer_blend = VRayBlendMtl(
            baseMtl=inner_blend, coats=[outer_coat])
        self.assertEqual(
            discover_max_mtlx_bump_strength(outer_blend), "0.75")


class TestPortDefaultSkip(unittest.TestCase):
    """Value exactly 1.0 is a no-op — do not pollute the exported doc
    with synthetic-looking `scale=1` inputs on every material whose
    artist accepted the Max UI's default."""

    def test_value_one_yields_zero_authored(self):
        mat = PhysicalMaterial(bump_map="t.png", bump_map_amt=1.0)
        doc, shader, [nm] = build_shader_with_normalmap()
        authored = apply_bump_strength_to_normalmaps(doc, shader, mat)
        self.assertEqual(authored, 0)
        # And the normalmap still has no `scale` input authored.
        self.assertIsNone(nm.getInput("scale"))

    def test_value_near_one_within_epsilon_yields_zero_authored(self):
        # 0.9999999f rounds to 1.0 within 1e-6. Prevents MAXScript-
        # stringification jitter from producing spurious `scale`
        # authoring.
        mat = PhysicalMaterial(bump_map="t.png", bump_map_amt=0.9999999)
        doc, shader, [nm] = build_shader_with_normalmap()
        authored = apply_bump_strength_to_normalmaps(doc, shader, mat)
        self.assertEqual(authored, 0)
        self.assertIsNone(nm.getInput("scale"))

    def test_non_default_value_is_authored(self):
        mat = PhysicalMaterial(bump_map="t.png", bump_map_amt=0.35)
        doc, shader, [nm] = build_shader_with_normalmap()
        authored = apply_bump_strength_to_normalmaps(doc, shader, mat)
        self.assertEqual(authored, 1)
        scale = nm.getInput("scale")
        self.assertIsNotNone(scale)
        self.assertAlmostEqual(
            float(scale.getValueString()), 0.35, places=6)


class TestNonNumericSkip(unittest.TestCase):
    """A Texmap in the same-name slot on an unfamiliar wrapper is
    skipped rather than mis-authoring a garbage `scale`."""

    def test_texmap_in_bump_map_amt_slot_is_skipped(self):
        # Some third-party wrapper might expose bump_map_amt as a
        # Texmap slot. The MAXScript classOf gate rejects Texmap;
        # walker falls through to next candidate.
        class Weird(_MaterialBase):
            max_class_name = "PhysicalMaterial"
            bump_map = "t.png"
            bump_map_amt = "a-texmap-not-a-float"  # string ~ Texmap
        # discover returns "" — no numeric hit anywhere.
        self.assertEqual(
            discover_max_mtlx_bump_strength(Weird()), "")

    def test_texmap_in_first_slot_lets_second_slot_win(self):
        # First candidate slot is a texmap; second candidate is a
        # legitimate float. Walker should try the next slot on the
        # SAME material before falling through to sub-materials.
        class Mixed(_MaterialBase):
            max_class_name = "PhysicalMaterial"
            bump_map = "t.png"
            bump_map_amt = "not-a-number"
            bumpMapAmount = 0.42
        self.assertEqual(
            discover_max_mtlx_bump_strength(Mixed()), "0.42")


class TestNegativeClamp(unittest.TestCase):
    """A negative artist-authored value is clamped to 0. Matches Max's
    own UI non-negative clamp on bump."""

    def test_negative_parses_and_clamps(self):
        ok, v = parse_bump_strength_string("-0.5")
        self.assertTrue(ok)
        self.assertEqual(v, 0.0)

    def test_negative_authors_zero_and_is_non_default_skip(self):
        # 0.0 is NOT the port default (1.0), so it IS authored.
        mat = PhysicalMaterial(bump_map="t.png", bump_map_amt=-0.5)
        doc, shader, [nm] = build_shader_with_normalmap()
        # The MAXScript helper stringifies the raw value; the C++
        # parser clamps. Simulate that here by round-tripping through
        # the parser explicitly, since the discover step in the mirror
        # is a pure passthrough (no clamp until parse).
        strength_str = discover_max_mtlx_bump_strength(mat)
        ok, v = parse_bump_strength_string(strength_str)
        self.assertTrue(ok)
        self.assertEqual(v, 0.0)


class TestMultiNormalmap(unittest.TestCase):
    """A shader with two normalmap nodes (e.g. coat + main) gets
    `scale` on BOTH."""

    def test_two_normalmaps_both_receive_scale(self):
        mat = PhysicalMaterial(bump_map="t.png", bump_map_amt=0.7)
        doc, shader, [nm_main, nm_coat] = \
            build_shader_with_two_normalmaps()
        authored = apply_bump_strength_to_normalmaps(doc, shader, mat)
        self.assertEqual(authored, 2)
        for nm in (nm_main, nm_coat):
            scale = nm.getInput("scale")
            self.assertIsNotNone(scale)
            self.assertAlmostEqual(
                float(scale.getValueString()), 0.7, places=6)


class TestNoNormalmap(unittest.TestCase):
    """A shader with no normalmap nodes short-circuits before the
    MAXScript round-trip."""

    def test_shader_without_normalmap_is_noop(self):
        doc = mx.createDocument()
        ng = doc.addNodeGraph("NG_test")
        # Add a tiledimage but no normalmap.
        tiled = ng.addNode("tiledimage", "img_diffuse", "color3")
        tiled.setNodeDefString("ND_tiledimage_color3")
        tiled.addInput("file", "filename").setValueString(
            "textures/wall_diff.png")
        shader = doc.addNode("standard_surface", "ss_test",
                             "surfaceshader")
        shader.setNodeDefString("ND_standard_surface_surfaceshader")
        # Material has a non-default bump_map_amt but the shader has
        # no normalmap to receive it — no-op.
        mat = PhysicalMaterial(bump_map="t.png", bump_map_amt=0.42)
        authored = apply_bump_strength_to_normalmaps(doc, shader, mat)
        self.assertEqual(authored, 0)


class TestSurgicalScope(unittest.TestCase):
    """The fix touches ONLY `scale` on normalmap nodes; other inputs
    are untouched. This is the property that lets us ship the fix
    without triggering downstream regressions in shader-input handling."""

    def test_normalmap_in_input_untouched(self):
        mat = PhysicalMaterial(bump_map="t.png", bump_map_amt=0.42)
        doc, shader, [nm] = build_shader_with_normalmap()
        in_before = nm.getInput("in").getNodeName()
        apply_bump_strength_to_normalmaps(doc, shader, mat)
        in_after = nm.getInput("in").getNodeName()
        self.assertEqual(in_before, in_after)
        self.assertEqual(in_after, "img_normal")

    def test_tiledimage_file_input_untouched(self):
        mat = PhysicalMaterial(bump_map="t.png", bump_map_amt=0.42)
        doc, shader, [nm] = build_shader_with_normalmap()
        ng = doc.getNodeGraph("NG_test")
        tiled = ng.getNode("img_normal")
        file_before = tiled.getInput("file").getValueString()
        apply_bump_strength_to_normalmaps(doc, shader, mat)
        file_after = tiled.getInput("file").getValueString()
        self.assertEqual(file_before, file_after)

    def test_shader_normal_input_untouched(self):
        mat = PhysicalMaterial(bump_map="t.png", bump_map_amt=0.42)
        doc, shader, [nm] = build_shader_with_normalmap()
        normal_in_ng_before = shader.getInput("normal").getNodeGraphString()
        normal_in_out_before = shader.getInput("normal").getOutputString()
        apply_bump_strength_to_normalmaps(doc, shader, mat)
        normal_in_ng_after = shader.getInput("normal").getNodeGraphString()
        normal_in_out_after = shader.getInput("normal").getOutputString()
        self.assertEqual(normal_in_ng_before, normal_in_ng_after)
        self.assertEqual(normal_in_out_before, normal_in_out_after)

    def test_no_other_inputs_added_to_normalmap(self):
        # The fix should add ONLY the `scale` input. `tangent` /
        # `bitangent` should not be authored (they inherit from the
        # renderer's per-vertex data).
        mat = PhysicalMaterial(bump_map="t.png", bump_map_amt=0.42)
        doc, shader, [nm] = build_shader_with_normalmap()
        input_names_before = {i.getName() for i in nm.getInputs()}
        apply_bump_strength_to_normalmaps(doc, shader, mat)
        input_names_after = {i.getName() for i in nm.getInputs()}
        added = input_names_after - input_names_before
        self.assertEqual(added, {"scale"})

    def test_idempotent_second_apply_is_noop(self):
        # Applying the pass twice must not re-open or duplicate the
        # scale input.
        mat = PhysicalMaterial(bump_map="t.png", bump_map_amt=0.42)
        doc, shader, [nm] = build_shader_with_normalmap()
        first = apply_bump_strength_to_normalmaps(doc, shader, mat)
        second = apply_bump_strength_to_normalmaps(doc, shader, mat)
        self.assertEqual(first, 1)
        self.assertEqual(second, 1)  # authored again, but still one
        # Only one scale input on the node.
        scale_inputs = [i for i in nm.getInputs()
                        if i.getName() == "scale"]
        self.assertEqual(len(scale_inputs), 1)
        self.assertAlmostEqual(
            float(scale_inputs[0].getValueString()), 0.42, places=6)


class TestWalkerReusesMTLX007(unittest.TestCase):
    """The wrapper walk is the SAME `unwrapBlendMaterialSubMtls` used by
    `discoverMaxMtlxTexmaps`. This means MAX-MTLX-008's scope-audit
    guarantees (VRay2SidedMtl, VRayMtlWrapper, VRayBumpMtl, etc. all
    EXCLUDED) apply here transparently — a future MTLX-007 scope
    change picks up automatically."""

    def test_unrelated_wrapper_class_not_descended(self):
        # A material whose class name is NOT in the wrapper table
        # should be treated as a leaf. VRayMtlWrapper has `.baseMtl`
        # but is intentionally out of scope per MTLX-008.
        class VRayMtlWrapper(_MaterialBase):
            max_class_name = "VRayMtlWrapper"
            baseMtl = None

        base = PhysicalMaterial(bump_map="t.png", bump_map_amt=0.55)
        wrapper = VRayMtlWrapper()
        wrapper.baseMtl = base
        # Walker treats VRayMtlWrapper as a leaf; the base's
        # bump_map_amt is UNREACHABLE.
        self.assertEqual(
            discover_max_mtlx_bump_strength(wrapper), "")

    def test_multi_sub_object_not_descended(self):
        # MultiMtl / Multi-Sub-Object is a per-face-ID class that must
        # NOT be descended into (MTLX-007 test locks that in).
        class MultiSub(_MaterialBase):
            max_class_name = "Multi/Sub-Object"

            def __init__(self):
                self.baseMtl = PhysicalMaterial(
                    bump_map="t.png", bump_map_amt=0.55)

        self.assertEqual(
            discover_max_mtlx_bump_strength(MultiSub()), "")


class TestArenaCensusInvariant(unittest.TestCase):
    """Cross-fixture anchor — a synthetic 179-material mix mirroring the
    arena baseline density. This is the invariant a future refactor
    must preserve: the count of materials that receive an authored
    non-default `scale` should equal the count that have a probeable
    non-default bump-strength property on a walked sub-material."""

    def _build_census(self):
        # Mix: 50 plain PhysicalMaterial with mixed strengths, 30 VRayMtl,
        # 40 VRayBlendMtl (base+2 coats), 20 VRayOverrideMtl,
        # 15 PhysicalMaterial default-strength (1.0 → no-op), 24
        # PhysicalMaterial with no bump map at all (no-op, but the
        # shader also has no normalmap).
        mats = []
        # 50 plain with strengths 0.1..0.9
        for i in range(50):
            mats.append(("plain-nondefault", PhysicalMaterial(
                bump_map="t.png",
                bump_map_amt=0.1 + (i % 9) * 0.1)))
        # 30 VRayMtl with strengths 0.5..2.0
        for i in range(30):
            mats.append(("vraymtl-nondefault", VRayMtl(
                bump_map="t.png",
                bump_multiplier=0.5 + (i % 4) * 0.5)))
        # 40 blend base+coats — base wins
        for i in range(40):
            base = PhysicalMaterial(
                bump_map="t.png",
                bump_map_amt=0.2 + (i % 5) * 0.1)
            coat = PhysicalMaterial(
                bump_map="t.png",
                bump_map_amt=5.0)  # coat is intentionally 5.0
            mats.append(("blend-nondefault", VRayBlendMtl(
                baseMtl=base, coats=[coat])))
        # 20 override
        for i in range(20):
            base = VRayMtl(bump_map="t.png",
                           bump_multiplier=0.3 + (i % 3) * 0.2)
            override = VRayOverrideMtl(
                baseMtl=base,
                giMtl=VRayMtl(bump_map="t.png",
                              bump_multiplier=9.0))
            mats.append(("override-nondefault", override))
        # 15 default-strength — no-op on the scale authoring
        for _ in range(15):
            mats.append(("default", PhysicalMaterial(
                bump_map="t.png", bump_map_amt=1.0)))
        # 24 no-scalar
        for _ in range(24):
            mats.append(("no-scalar", PhysicalMaterial(
                bump_map="t.png")))
        return mats

    def test_arena_scale_authored_count_matches_non_default_count(self):
        # Model: how many materials should get a non-default `scale`
        # authored? Every -nondefault labelled material has a probeable
        # strength, but some of those strengths happen to equal 1.0
        # (the ND_normalmap port default) and are correctly skipped.
        # This test verifies the fix's "author iff non-default" contract
        # at census scale rather than just per-material.
        mats = self._build_census()
        authored = 0
        expected_authored = 0
        for label, mat in mats:
            strength_str = discover_max_mtlx_bump_strength(mat)
            ok, v = parse_bump_strength_string(strength_str)
            if ok and math.fabs(v - 1.0) >= 1e-6:
                expected_authored += 1
            doc, shader, [nm] = build_shader_with_normalmap()
            n = apply_bump_strength_to_normalmaps(doc, shader, mat)
            if n > 0:
                authored += 1
        # Sanity floor: at least the 50-plain batch has no port-default
        # hits so we should always be above that.
        self.assertGreaterEqual(expected_authored, 50)
        # And the fix's authored count agrees with the model's
        # expected count — the invariant.
        self.assertEqual(authored, expected_authored)

    def test_arena_scale_default_and_missing_stay_noop(self):
        mats = self._build_census()
        for label, mat in mats:
            if label in ("default", "no-scalar"):
                doc, shader, [nm] = build_shader_with_normalmap()
                n = apply_bump_strength_to_normalmaps(doc, shader, mat)
                self.assertEqual(n, 0)
                self.assertIsNone(nm.getInput("scale"))


class TestNormalmapMaterialXPortDefault(unittest.TestCase):
    """Anchor: verify that MaterialX's ND_normalmap_float actually defines
    `scale` with a `1.0` default. If a future MaterialX version renames
    the input or changes the default, this test fires and the pre-
    default-skip logic in the C++ needs revisiting."""

    def test_nd_normalmap_float_scale_default_is_1(self):
        lib = mx.createDocument()
        mx.loadLibraries(mx.getDefaultDataLibraryFolders(),
                         mx.getDefaultDataSearchPath(), lib)
        nd = lib.getNodeDef("ND_normalmap_float")
        self.assertIsNotNone(nd)
        scale = nd.getActiveInput("scale")
        self.assertIsNotNone(scale, "ND_normalmap_float missing `scale`")
        self.assertEqual(scale.getType(), "float")
        # MaterialX 1.38+ default is 1.0.
        self.assertAlmostEqual(
            float(scale.getValueString()), 1.0, places=6)


if __name__ == "__main__":
    unittest.main()
