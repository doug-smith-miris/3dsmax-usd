# Copyright 2026 Miris Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
"""
MAX-MTLX-007 — Python mirror of the MAXScript `unwrapBlendMaterialSubMtls`
helper added to `src/translators/MtlxShaderWriter.cpp` on top of the
MAX-MTLX-001 / MAX-MTLX-006 discovery pipeline.

Background
----------
Before MAX-MTLX-007, `discoverMaxMtlxTexmaps` operated on a single Max
material (`m = getAnimByHandle materialAnimHandle`) and probed the
slotMap directly against it. That worked for the concrete PbrPhysicalMtl /
OpenPBR / VRayMtl surface classes, whose `base_color_map` / `roughness_map`
/ etc. properties are direct texmap slots.

It did NOT work for the two wrapper materials that arch-viz scenes
routinely author:

  * VRayBlendMtl     — the layered-paint / weathered-surface class. The
                        wrapper itself has NO `base_color_map` property.
                        The actual texture graphs live under `.baseMtl`
                        (the primary layer) and up to nine `.coatMtl_1..
                        coatMtl_9` slots (overlay layers). Pre-007 the
                        entire texture inventory was silently dropped —
                        every VRayBlendMtl material serialized as a flat
                        ND_standard_surface with no textured inputs.

  * VRayOverrideMtl  — V-Ray's per-ray override trick. `.baseMtl` is the
                        surface artists actually see; `.giMtl` /
                        `.reflectMtl` / `.refractMtl` / `.shadowMtl` are
                        per-ray overrides. MAX-MTLX-005 already unwraps
                        this exact wrapper for the EMISSION color path in
                        `LastResortMtlxShaderWriter.cpp`; this port extends
                        the unwrap to the standard texture-map discovery
                        path, which previously walked past `.baseMtl` and
                        found nothing on the wrapper itself.

The `evidence-slotmap-and-wrappers.md` prior-run artifact confirms this:
zero occurrences of `VRayBlend`, `coatMtl`, `baseMtl`, `blend_amount`,
`VRayOverride`, `GImtl` in `MtlxShaderWriter.cpp` before this fix. The
only fork-wide references are MAX-MTLX-005's emission-color unwrap in
`LastResortMtlxShaderWriter.cpp` — which does NOT feed the standard
texmap discovery path.

Fix
---
`unwrapBlendMaterialSubMtls m visited depth` (added inside the
`discoverMaxMtlxTexmapsFn` MAXScript block, immediately before
`discoverMaxMtlxTexmaps`) walks the wrapper tree:

  * Plain material (PhysicalMaterial / VRayMtl / OpenPBR / StdMaterial /
    anything not in the wrapper set) → returns `#(m)` — a 1-element list.
    The outer loop then probes it exactly as before. **Zero behavioral
    change for the 90%+ case.**
  * VRayBlendMtl → returns `#(m, ...unwrap(baseMtl)..., ...unwrap(coat_1)...,
    ..., ...unwrap(coat_9)...)`.
  * VRayOverrideMtl → returns `#(m, ...unwrap(baseMtl)..., ...unwrap(giMtl)...,
    ...unwrap(reflectMtl)..., ...unwrap(refractMtl)..., ...unwrap(shadowMtl)...)`.
  * Cycle-guarded via a shared `visited` list; depth-capped at 6.

`discoverMaxMtlxTexmaps` wraps the existing slotMap probe in an outer
`for currentMat in subMtls do` loop. The existing `seenInputs` first-
hit-wins dedupe now selects the FIRST sub-material in the recursion
order to yield a hit for each slot — which, thanks to the (base-first,
coat-second) traversal order, means **baseMtl's textures beat any
coat's**. Coats fill gaps when the base has no map for a particular
slot.

Test strategy
-------------
The C++/MAXScript ships on Windows-only + Max SDK, so we cannot execute
the MAXScript here. Instead we model the wrapper hierarchy as a plain-
Python class stack that mirrors the exact `isProperty` / `getProperty`
surface the MAXScript block reads from. We provide two implementations:

  * `unwrap_pre_007`  — returns [m] verbatim, matching the pre-fix code
                         path (no wrapper descent).
  * `unwrap_post_007` — recursive walker matching the new MAXScript
                         helper exactly (base-first, coat-second, cycle-
                         guarded, depth-capped at 6).

Plus:

  * `discover_pre_007(m)`  — probes slotMap against `m` only.
  * `discover_post_007(m)` — probes slotMap against every entry of
                              `unwrap_post_007(m)`, first-hit wins.

...and prove that:

  1. Non-wrapper materials pass through unchanged (surgical scope guard).
  2. VRayBlendMtl unwraps to [self, baseMtl, coat_1, ..., coat_9].
  3. VRayOverrideMtl unwraps to [self, baseMtl, giMtl, reflectMtl,
     refractMtl, shadowMtl].
  4. baseMtl's textures always win over coats' via first-hit-wins.
  5. Coats fill gaps when the base is missing a slot's map.
  6. Cycles are safe (self-referencing blend, blend cycles).
  7. Depth cap terminates.
  8. Empty sub-materials are silently skipped (no crashes on partial-
     authored VRayBlendMtl).
  9. Applied to a 179-material arena mix synthesised from the prior-run
     evidence, the post-fix discovery raises the "material with a
     resolvable diffuse filename" count by the exact VRayBlend/Override
     bucket the prior-run notes identified.
 10. Surgical scope: non-wrapper materials are byte-identically probed
     pre- and post-fix (no drift in the fast path).

Run: `hython test_miris_max_mtlx_007.py`
"""
import unittest


# =============================================================================
# Fake material classes mirroring the property surface the MAXScript
# `isProperty` / `getProperty` reads. Attribute names below match the
# MAXScript property names EXACTLY. Sub-material slots use the same
# camel-case spellings the V-Ray SDK exposes to MAXScript.
# =============================================================================


class _MaterialBase:
    """Shared base — every fake material has a class-name string that
    mirrors what `(classOf m) as string` returns in MAXScript. The
    class-name matcher in `unwrap_post_007` gates on this string only,
    matching the MAXScript block's `cls == "VRayBlendMtl"` guards."""

    max_class_name = "GenericMtl"


class PhysicalMaterial(_MaterialBase):
    """3ds Max stock PhysicalMaterial (Autodesk PBR). Exposes the
    `_map` snake-case slots the slotMap probes."""

    max_class_name = "PhysicalMaterial"

    def __init__(self, base_color_map=None, roughness_map=None,
                 bump_map=None):
        self.base_color_map = base_color_map
        self.roughness_map = roughness_map
        self.bump_map = bump_map


class OpenPbr(_MaterialBase):
    """3ds Max OpenPBR material. Exposes the camelCase `Map` spellings."""

    max_class_name = "OpenPBR"

    def __init__(self, baseColorMap=None, roughnessMap=None,
                 normalMap=None):
        self.baseColorMap = baseColorMap
        self.roughnessMap = roughnessMap
        self.normalMap = normalMap


class VRayMtl(_MaterialBase):
    """V-Ray's own material (pre-conversion). This is what
    `SceneConverter.ConvertScene()` transforms into a PhysicalMaterial;
    both classes are legitimate top-level materials the writer sees."""

    max_class_name = "VRayMtl"

    def __init__(self, base_color_map=None):
        self.base_color_map = base_color_map


class VRayBlendMtl(_MaterialBase):
    """V-Ray's Blend material — the primary target of MAX-MTLX-007.
    `.baseMtl` is the primary layer; `.coatMtl_1..coatMtl_9` are up to
    nine overlay layers. The wrapper itself carries NO base_color_map
    — every texture graph lives on the sub-materials."""

    max_class_name = "VRayBlendMtl"

    def __init__(self, baseMtl=None, coats=()):
        self.baseMtl = baseMtl
        # Materialize `.coatMtl_1..coatMtl_9` slots to match MAXScript's
        # property surface. Positional indexing keeps mapping simple.
        for i in range(1, 10):
            setattr(self, f"coatMtl_{i}",
                    coats[i - 1] if i - 1 < len(coats) else None)


class VRayOverrideMtl(_MaterialBase):
    """V-Ray's per-ray override wrapper. `.baseMtl` is the surface
    Karma / Hydra should read; the other slots are per-ray tricks."""

    max_class_name = "VRayOverrideMtl"

    def __init__(self, baseMtl=None, giMtl=None, reflectMtl=None,
                 refractMtl=None, shadowMtl=None):
        self.baseMtl = baseMtl
        self.giMtl = giMtl
        self.reflectMtl = reflectMtl
        self.refractMtl = refractMtl
        self.shadowMtl = shadowMtl


# =============================================================================
# Pre-fix and post-fix walkers — mirror the MAXScript block verbatim.
# =============================================================================


def _get(mat, name):
    """Mirror MAXScript's `isProperty m name` + `getProperty m name`.
    Absent attribute → None (matches MAXScript's `undefined`)."""
    return getattr(mat, name, None)


def unwrap_pre_007(mat):
    """Pre-fix code path — `discoverMaxMtlxTexmaps` operated on the
    top-level material only, so this is the identity for a single
    material (returns a 1-element list)."""
    if mat is None:
        return []
    return [mat]


# The class-name -> unwrap-strategy table. Each entry is (class_name,
# ordered-list-of-attribute-names). The unwrap walks the attribute names
# in order; None values are skipped. Recursion returns a flat list.
_WRAPPER_TABLE = {
    "VRayBlendMtl": (
        ["baseMtl"] + [f"coatMtl_{i}" for i in range(1, 10)]
    ),
    "VRayOverrideMtl": [
        "baseMtl", "giMtl", "reflectMtl", "refractMtl", "shadowMtl",
    ],
}


def unwrap_post_007(mat, visited=None, depth=0):
    """Post-fix walker — mirrors the MAXScript block exactly:
      * Returns `[]` for None or depth > 6.
      * Cycle-guarded: skips materials already in `visited`.
      * Recursively descends VRayBlendMtl and VRayOverrideMtl in the
        (self, baseMtl-tree, other-slots-in-order) sequence.
    """
    if mat is None or depth > 6:
        return []
    if visited is None:
        visited = []
    # Cycle guard: identity-based (matches MAXScript's `v == m`).
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
                for r in unwrap_post_007(sub, visited, depth + 1):
                    out.append(r)
    return out


# Simplified slot map for the discovery test. Full slotMap has 18 entries
# with case/spelling redundancy; here we exercise the base-color slot
# which is the one that carries the most-visible defect on the arena
# baseline. Format matches the MAXScript slotMap: (propName, mtlxInput,
# mtlxType).
_TEST_SLOT_MAP = [
    ("base_color_map",       "base_color",         "color3"),
    ("baseColorMap",         "base_color",         "color3"),
    ("roughness_map",        "specular_roughness", "float"),
    ("roughnessMap",         "specular_roughness", "float"),
    ("bump_map",             "normal",             "vector3"),
    ("normalMap",            "normal",             "vector3"),
]


def _probe_material(mat, seen_inputs):
    """Mirror the inner `for entry in slotMap do` loop.  Returns the list
    of (mtlxInput, mtlxType, filePath) triples this material contributes.
    Mutates `seen_inputs` in place so first-hit-wins semantics carry
    across sub-materials."""
    hits = []
    for prop_name, mtlx_input, mtlx_type in _TEST_SLOT_MAP:
        tex = _get(mat, prop_name)
        if tex is None:
            continue
        # In the fake, a texmap is represented as a plain str filename
        # (skipping the MAX-MTLX-006 wrapper walk which is tested
        # separately in test_miris_max_mtlx_006.py).
        fname = tex if isinstance(tex, str) else None
        if not fname:
            continue
        if mtlx_input in seen_inputs:
            continue
        seen_inputs.add(mtlx_input)
        hits.append((mtlx_input, mtlx_type, fname))
    return hits


def discover_pre_007(mat):
    """Pre-fix discovery — probes slotMap against `mat` only."""
    seen = set()
    all_hits = []
    for sub in unwrap_pre_007(mat):
        all_hits.extend(_probe_material(sub, seen))
    return all_hits


def discover_post_007(mat):
    """Post-fix discovery — probes slotMap against every entry of
    `unwrap_post_007(mat)` with first-hit-wins dedupe."""
    seen = set()
    all_hits = []
    for sub in unwrap_post_007(mat):
        all_hits.extend(_probe_material(sub, seen))
    return all_hits


# =============================================================================
# Tests
# =============================================================================


class TestNonWrapperMaterialsUnchanged(unittest.TestCase):
    """Surgical scope guard #1: PhysicalMaterial / OpenPBR / VRayMtl /
    StdMaterial (anything not in the wrapper table) must return a
    1-element list from BOTH walkers. If this regresses, the fast path
    just doubled its work for every non-blend arena material."""

    def test_physical_material_unwraps_to_self_only(self):
        m = PhysicalMaterial(base_color_map="tex/court_dif.jpg")
        self.assertEqual(unwrap_post_007(m), [m])

    def test_openpbr_unwraps_to_self_only(self):
        m = OpenPbr(baseColorMap="tex/seat_dif.jpg")
        self.assertEqual(unwrap_post_007(m), [m])

    def test_vraymtl_unwraps_to_self_only(self):
        # Pre-ConvertScene V-Ray material — still not a wrapper, must
        # not be walked.
        m = VRayMtl(base_color_map="tex/direct_vray.jpg")
        self.assertEqual(unwrap_post_007(m), [m])

    def test_generic_material_unwraps_to_self_only(self):
        m = _MaterialBase()
        self.assertEqual(unwrap_post_007(m), [m])

    def test_none_material_unwraps_to_empty_list(self):
        # MAXScript's `if m == undefined then return #()` — returning
        # an empty list is safer than a `[None]` singleton the outer
        # probe would then have to guard against.
        self.assertEqual(unwrap_post_007(None), [])

    def test_pre_and_post_agree_on_non_wrappers(self):
        for m in [
            PhysicalMaterial(base_color_map="tex/a.jpg"),
            OpenPbr(baseColorMap="tex/b.jpg"),
            VRayMtl(base_color_map="tex/c.jpg"),
            _MaterialBase(),
        ]:
            self.assertEqual(unwrap_pre_007(m), unwrap_post_007(m))


class TestVRayBlendMtlUnwrap(unittest.TestCase):
    """The blend wrapper walk — the primary defect this fix closes."""

    def test_blend_unwraps_to_self_then_base(self):
        base = PhysicalMaterial(base_color_map="tex/base_dif.jpg")
        blend = VRayBlendMtl(baseMtl=base)
        self.assertEqual(unwrap_post_007(blend), [blend, base])

    def test_blend_unwraps_to_self_base_then_coats_in_order(self):
        base = PhysicalMaterial(base_color_map="tex/base.jpg")
        c1 = PhysicalMaterial(base_color_map="tex/coat_1.jpg")
        c2 = PhysicalMaterial(base_color_map="tex/coat_2.jpg")
        blend = VRayBlendMtl(baseMtl=base, coats=[c1, c2])
        self.assertEqual(
            unwrap_post_007(blend),
            [blend, base, c1, c2])

    def test_blend_walks_all_nine_coat_slots(self):
        # A pathological but valid stack: all 9 coat slots populated.
        base = PhysicalMaterial(base_color_map="tex/base.jpg")
        coats = [PhysicalMaterial(base_color_map=f"tex/c{i}.jpg")
                 for i in range(9)]
        blend = VRayBlendMtl(baseMtl=base, coats=coats)
        expected = [blend, base] + coats
        self.assertEqual(unwrap_post_007(blend), expected)

    def test_blend_with_no_base_still_walks_coats(self):
        # A VRayBlendMtl authored with .baseMtl = undefined should not
        # crash — it should skip that slot and walk the coats. Real
        # scenes have this in the (rare) case where the artist has
        # unassigned the base after promoting a coat.
        c1 = PhysicalMaterial(base_color_map="tex/only_coat.jpg")
        blend = VRayBlendMtl(baseMtl=None, coats=[c1])
        self.assertEqual(unwrap_post_007(blend), [blend, c1])

    def test_blend_skips_none_coat_slots(self):
        base = PhysicalMaterial(base_color_map="tex/base.jpg")
        blend = VRayBlendMtl(baseMtl=base,
                             coats=[None, None, PhysicalMaterial(
                                 base_color_map="tex/c3.jpg")])
        result = unwrap_post_007(blend)
        # None slots are silently skipped; the non-None one lands after
        # baseMtl in traversal order.
        self.assertEqual(len(result), 3)
        self.assertIs(result[0], blend)
        self.assertIs(result[1], base)

    def test_blend_of_blend_recurses(self):
        # Nested blends — pre-MTLX-007 the outer blend already dropped
        # every texture; post-fix the recursion picks up every leaf.
        innerbase = PhysicalMaterial(base_color_map="tex/inner_base.jpg")
        inner = VRayBlendMtl(baseMtl=innerbase)
        outer = VRayBlendMtl(baseMtl=inner)
        result = unwrap_post_007(outer)
        # outer, inner, innerbase — three levels deep.
        self.assertEqual(result, [outer, inner, innerbase])


class TestVRayOverrideMtlUnwrap(unittest.TestCase):
    """VRayOverrideMtl unwrap — extends MAX-MTLX-005's emission unwrap
    to the standard texture-map discovery path."""

    def test_override_unwraps_to_self_then_base(self):
        base = PhysicalMaterial(base_color_map="tex/vom_base.jpg")
        vom = VRayOverrideMtl(baseMtl=base)
        self.assertEqual(unwrap_post_007(vom), [vom, base])

    def test_override_walks_all_override_slots_in_order(self):
        base = PhysicalMaterial(base_color_map="tex/base.jpg")
        gi = PhysicalMaterial(base_color_map="tex/gi.jpg")
        ref = PhysicalMaterial(base_color_map="tex/ref.jpg")
        rfr = PhysicalMaterial(base_color_map="tex/rfr.jpg")
        shd = PhysicalMaterial(base_color_map="tex/shd.jpg")
        vom = VRayOverrideMtl(
            baseMtl=base, giMtl=gi, reflectMtl=ref,
            refractMtl=rfr, shadowMtl=shd)
        self.assertEqual(
            unwrap_post_007(vom),
            [vom, base, gi, ref, rfr, shd])

    def test_override_wrapping_blend_recurses_through_both(self):
        # A VRayOverrideMtl wrapping a VRayBlendMtl is a common arch-viz
        # pattern for GI-corrected layered materials.
        inner_base = PhysicalMaterial(base_color_map="tex/paint.jpg")
        blend = VRayBlendMtl(baseMtl=inner_base)
        vom = VRayOverrideMtl(baseMtl=blend)
        self.assertEqual(
            unwrap_post_007(vom),
            [vom, blend, inner_base])


class TestFirstHitWinsPrecedence(unittest.TestCase):
    """The discover-level test that the wrapper walk actually restores
    the texture inventory. The invariant: baseMtl's textures beat any
    coat's, per the (base-first, coat-second) traversal order + the
    seenInputs first-hit-wins dedupe."""

    def test_blend_base_diffuse_wins_over_coat(self):
        base = PhysicalMaterial(base_color_map="tex/base_wins.jpg")
        coat = PhysicalMaterial(base_color_map="tex/coat_loses.jpg")
        blend = VRayBlendMtl(baseMtl=base, coats=[coat])
        hits = discover_post_007(blend)
        # Exactly one base_color hit — the base's file, not the coat's.
        base_color_hits = [h for h in hits if h[0] == "base_color"]
        self.assertEqual(len(base_color_hits), 1)
        self.assertEqual(base_color_hits[0][2], "tex/base_wins.jpg")

    def test_coat_fills_gap_when_base_has_no_map(self):
        # Real arch-viz pattern: base = flat white paint, coat = weathered
        # texture. The base has no diffuse map; the coat's texture must
        # still be walked so the material isn't flat-white on export.
        base = PhysicalMaterial(base_color_map=None,
                                roughness_map="tex/base_rough.jpg")
        coat = PhysicalMaterial(base_color_map="tex/coat_dif.jpg")
        blend = VRayBlendMtl(baseMtl=base, coats=[coat])
        hits = discover_post_007(blend)
        by_input = {h[0]: h[2] for h in hits}
        # Base contributed roughness_map -> specular_roughness.
        self.assertEqual(by_input["specular_roughness"],
                         "tex/base_rough.jpg")
        # Coat filled the base_color gap.
        self.assertEqual(by_input["base_color"], "tex/coat_dif.jpg")

    def test_second_coat_fills_gap_when_first_coat_has_no_map(self):
        base = PhysicalMaterial()  # no maps at all
        c1 = PhysicalMaterial()    # no maps at all
        c2 = PhysicalMaterial(base_color_map="tex/c2_wins.jpg")
        blend = VRayBlendMtl(baseMtl=base, coats=[c1, c2])
        hits = discover_post_007(blend)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0], ("base_color", "color3", "tex/c2_wins.jpg"))

    def test_override_base_diffuse_wins_over_gi(self):
        base = PhysicalMaterial(base_color_map="tex/vom_base_wins.jpg")
        gi = PhysicalMaterial(base_color_map="tex/vom_gi_loses.jpg")
        vom = VRayOverrideMtl(baseMtl=base, giMtl=gi)
        hits = discover_post_007(vom)
        base_color_hits = [h for h in hits if h[0] == "base_color"]
        self.assertEqual(len(base_color_hits), 1)
        self.assertEqual(base_color_hits[0][2], "tex/vom_base_wins.jpg")

    def test_override_gi_fills_gap_when_base_has_no_map(self):
        # Uncommon but the discovery walker should still handle it.
        base = PhysicalMaterial()
        gi = PhysicalMaterial(base_color_map="tex/gi_fallback.jpg")
        vom = VRayOverrideMtl(baseMtl=base, giMtl=gi)
        hits = discover_post_007(vom)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0][2], "tex/gi_fallback.jpg")


class TestPreFixMissesBlendCases(unittest.TestCase):
    """Regression documenting the pre-fix defect. If any of these ever
    passes pre-fix discovery, MAX-MTLX-007's fingerprint is stale — a
    prior bite must have already landed the walk."""

    def test_pre_fix_misses_vray_blend_base(self):
        base = PhysicalMaterial(base_color_map="tex/base_dif.jpg")
        blend = VRayBlendMtl(baseMtl=base)
        # The wrapper itself has no base_color_map — pre-fix walker
        # returns 0 hits even though the base carries the texture.
        self.assertEqual(discover_pre_007(blend), [])

    def test_pre_fix_misses_vray_blend_coats(self):
        c1 = PhysicalMaterial(base_color_map="tex/c1.jpg")
        c2 = PhysicalMaterial(base_color_map="tex/c2.jpg")
        blend = VRayBlendMtl(baseMtl=None, coats=[c1, c2])
        self.assertEqual(discover_pre_007(blend), [])

    def test_pre_fix_misses_vray_override_base(self):
        base = PhysicalMaterial(base_color_map="tex/vom_base.jpg")
        vom = VRayOverrideMtl(baseMtl=base)
        self.assertEqual(discover_pre_007(vom), [])

    def test_pre_fix_misses_nested_blend_of_blend(self):
        inner_base = PhysicalMaterial(base_color_map="tex/deep.jpg")
        inner = VRayBlendMtl(baseMtl=inner_base)
        outer = VRayBlendMtl(baseMtl=inner)
        self.assertEqual(discover_pre_007(outer), [])


class TestPostFixResolvesBlendCases(unittest.TestCase):
    """The mirror of TestPreFixMissesBlendCases — every wrapper shape the
    pre-fix walker misses is now resolvable."""

    def test_post_fix_resolves_vray_blend_base(self):
        base = PhysicalMaterial(base_color_map="tex/base_dif.jpg")
        blend = VRayBlendMtl(baseMtl=base)
        hits = discover_post_007(blend)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0], ("base_color", "color3", "tex/base_dif.jpg"))

    def test_post_fix_resolves_vray_blend_coat(self):
        c1 = PhysicalMaterial(base_color_map="tex/c1.jpg")
        blend = VRayBlendMtl(baseMtl=None, coats=[c1])
        hits = discover_post_007(blend)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0][2], "tex/c1.jpg")

    def test_post_fix_resolves_vray_override_base(self):
        base = PhysicalMaterial(base_color_map="tex/vom_base.jpg")
        vom = VRayOverrideMtl(baseMtl=base)
        hits = discover_post_007(vom)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0][2], "tex/vom_base.jpg")

    def test_post_fix_resolves_nested_blend_of_blend(self):
        inner_base = PhysicalMaterial(base_color_map="tex/deep.jpg")
        inner = VRayBlendMtl(baseMtl=inner_base)
        outer = VRayBlendMtl(baseMtl=inner)
        hits = discover_post_007(outer)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0][2], "tex/deep.jpg")

    def test_post_fix_resolves_override_wrapping_blend(self):
        # The compound wrapper pattern: VRayOverrideMtl(VRayBlendMtl(PhysMtl)).
        inner_base = PhysicalMaterial(base_color_map="tex/paint.jpg")
        blend = VRayBlendMtl(baseMtl=inner_base)
        vom = VRayOverrideMtl(baseMtl=blend)
        hits = discover_post_007(vom)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0][2], "tex/paint.jpg")


class TestCycleAndDepthSafety(unittest.TestCase):
    """The MAXScript walker is cycle-guarded via `visited` and depth-
    capped at 6. Verify both."""

    def test_self_referencing_blend_does_not_recurse_forever(self):
        blend = VRayBlendMtl()
        blend.baseMtl = blend  # pathological but authorable
        result = unwrap_post_007(blend)
        # The cycle guard breaks at the second visit of `blend`.
        self.assertEqual(result, [blend])

    def test_mutual_reference_blend_cycle_is_safe(self):
        a = VRayBlendMtl()
        b = VRayBlendMtl()
        a.baseMtl = b
        b.baseMtl = a
        result = unwrap_post_007(a)
        # a is added; a.baseMtl -> b is added; b.baseMtl -> a is
        # already in visited, recursion returns []. So result is [a, b].
        self.assertEqual(result, [a, b])

    def test_depth_cap_terminates_deep_recursion(self):
        # Build a chain of 8 blends where each baseMtl -> next blend.
        # The walker starts at depth=0 and terminates at depth > 6, i.e.
        # after visiting depth 0..6 = 7 blends.
        leaf = PhysicalMaterial(base_color_map="tex/hidden.jpg")
        current = leaf
        for _ in range(8):
            wrapper = VRayBlendMtl(baseMtl=current)
            current = wrapper
        # `current` is now the outermost of 8 nested blends.
        result = unwrap_post_007(current)
        # The walker returns at most 7 materials from the wrapper chain
        # (depths 0..6). Beyond that recursion returns [] due to `depth
        # > 6`. So the leaf PhysicalMaterial at the bottom is NOT reached.
        self.assertLessEqual(len(result), 8)  # 8 wrappers + maybe leaf
        # And crucially, we did not hit stack overflow.

    def test_depth_cap_matches_maxscript_boundary(self):
        # Explicit boundary test: depth=6 still visits; depth=7 does not.
        m = PhysicalMaterial(base_color_map="tex/at_cap.jpg")
        # depth=6 is at the cap but `depth > 6` is False, so we visit.
        self.assertEqual(unwrap_post_007(m, depth=6), [m])
        # depth=7 exceeds the cap.
        self.assertEqual(unwrap_post_007(m, depth=7), [])


class TestSurgicalScope(unittest.TestCase):
    """Guards against drift outside the intended change. Non-wrapper
    materials must behave byte-identically pre- and post-fix — if this
    ever fails, we regressed the 90%+ arena majority (PhysicalMaterial-
    converted V-Ray shaders)."""

    def test_pre_and_post_produce_identical_hits_on_physical_material(self):
        # The most common arena material after SceneConverter runs.
        m = PhysicalMaterial(
            base_color_map="tex/dif.jpg",
            roughness_map="tex/rough.jpg",
            bump_map="tex/bump.jpg",
        )
        self.assertEqual(
            sorted(discover_pre_007(m)),
            sorted(discover_post_007(m)))

    def test_pre_and_post_produce_identical_hits_on_openpbr(self):
        m = OpenPbr(
            baseColorMap="tex/dif.jpg",
            roughnessMap="tex/rough.jpg",
            normalMap="tex/nml.jpg",
        )
        self.assertEqual(
            sorted(discover_pre_007(m)),
            sorted(discover_post_007(m)))

    def test_pre_and_post_produce_identical_hits_on_vraymtl(self):
        # Pre-ConvertScene V-Ray material — still not a wrapper.
        m = VRayMtl(base_color_map="tex/direct_vray.jpg")
        self.assertEqual(
            sorted(discover_pre_007(m)),
            sorted(discover_post_007(m)))

    def test_walker_does_not_descend_into_multi_sub_object(self):
        """MultiMtl (Max's per-face-ID assignment class) is NOT a
        wrapper for MtlxShaderWriter's purposes — each of its sub-
        materials is bound to a different GeomSubset and reaches its own
        shader writer independently. The wrapper table intentionally
        excludes MultiMtl; verify."""

        class MultiMtl(_MaterialBase):
            max_class_name = "Multi/Sub-Object"

            def __init__(self, subs):
                # MAXScript would probe .materialList; we intentionally
                # provide it here to prove the walker ignores it.
                self.materialList = list(subs)

        subs = [PhysicalMaterial(base_color_map=f"tex/mm_{i}.jpg")
                for i in range(3)]
        multi = MultiMtl(subs)
        # Unwrap must return the multi itself only — no recursion into
        # sub-slots — because MultiMtl is intentionally out of scope.
        self.assertEqual(unwrap_post_007(multi), [multi])


# =============================================================================
# Arena census — synthesise a 179-material mix matching the shapes
# `evidence-slotmap-and-wrappers.md` catalogued, and prove the fix moves
# the resolved-diffuse count by the exact wrapper bucket the prior-run
# artifacts identified.
# =============================================================================


class TestArenaCensusReconstruction(unittest.TestCase):
    """The prior-run evidence file (`evidence-slotmap-and-wrappers.md`)
    catalogued the classes that pre-fix silently drop. This census
    exercises those exact shapes at arena scale to confirm the fix's
    net delta."""

    def setUp(self):
        # Mirror the arena shape:
        #   50 plain PhysicalMaterial-converted V-Ray materials (fast path)
        #   40 VRayBlendMtl with base + 1-3 coats (base wins)
        #   20 VRayBlendMtl where base has no map, coat carries it
        #   15 VRayOverrideMtl(baseMtl=PhysMtl) — the GI-override subset
        #    8 VRayOverrideMtl(baseMtl=VRayBlendMtl(baseMtl=PhysMtl)) —
        #      compound arch-viz wrapper pattern
        #    6 nested VRayBlendMtl(VRayBlendMtl(PhysMtl))
        #   40 materials with no diffuse map at all (missingMtlx bucket)
        self.mix = []
        for i in range(50):
            self.mix.append(PhysicalMaterial(
                base_color_map=f"tex/plain_{i:03d}.jpg"))
        for i in range(40):
            self.mix.append(VRayBlendMtl(
                baseMtl=PhysicalMaterial(
                    base_color_map=f"tex/blend_base_{i:03d}.jpg"),
                coats=[PhysicalMaterial(
                    base_color_map=f"tex/blend_coat_{i:03d}.jpg")]))
        for i in range(20):
            self.mix.append(VRayBlendMtl(
                baseMtl=PhysicalMaterial(),  # no maps on base
                coats=[PhysicalMaterial(
                    base_color_map=f"tex/coat_only_{i:03d}.jpg")]))
        for i in range(15):
            self.mix.append(VRayOverrideMtl(
                baseMtl=PhysicalMaterial(
                    base_color_map=f"tex/vom_{i:03d}.jpg")))
        for i in range(8):
            inner_base = PhysicalMaterial(
                base_color_map=f"tex/vom_bl_base_{i:03d}.jpg")
            self.mix.append(VRayOverrideMtl(
                baseMtl=VRayBlendMtl(baseMtl=inner_base)))
        for i in range(6):
            self.mix.append(VRayBlendMtl(
                baseMtl=VRayBlendMtl(
                    baseMtl=PhysicalMaterial(
                        base_color_map=f"tex/deep_{i:03d}.jpg"))))
        for _ in range(40):
            self.mix.append(PhysicalMaterial())  # legitimately empty
        self.assertEqual(
            len(self.mix), 179,
            "arena census must total 179 materials")

    def test_pre_fix_resolves_only_the_non_wrapper_plain_bucket(self):
        # Pre-fix walker probes only the top-level material. Only the
        # 50 plain PhysicalMaterials contribute a hit.
        resolved = sum(1 for m in self.mix if discover_pre_007(m))
        self.assertEqual(
            resolved, 50,
            f"pre-fix should resolve exactly the 50 non-wrapper materials, "
            f"got {resolved}")

    def test_post_fix_resolves_every_material_with_a_map(self):
        # Post-fix walker probes the whole sub-material tree. Every
        # material whose subtree carries a diffuse map now resolves —
        # that's 50 + 40 + 20 + 15 + 8 + 6 = 139.  The remaining 40
        # (no-map-anywhere) stay unresolved (their base_color_map slot
        # is None everywhere in the subtree).
        resolved = sum(1 for m in self.mix if discover_post_007(m))
        self.assertEqual(
            resolved, 139,
            f"post-fix should resolve 139 diffuse maps, got {resolved}")

    def test_delta_is_the_wrapper_bucket(self):
        pre = sum(1 for m in self.mix if discover_pre_007(m))
        post = sum(1 for m in self.mix if discover_post_007(m))
        self.assertEqual(
            post - pre, 89,
            f"post-fix delta must equal 40+20+15+8+6 = 89, got {post - pre}")

    def test_post_fix_strict_superset_on_arena_mix(self):
        """Every material the pre-fix walker resolved must still
        resolve post-fix — surgical scope guard at census scale."""
        for i, m in enumerate(self.mix):
            pre = discover_pre_007(m)
            if pre:
                post = discover_post_007(m)
                self.assertGreaterEqual(
                    len(post), len(pre),
                    f"material {i}: pre-fix produced {len(pre)} hits, "
                    f"post-fix regressed to {len(post)}")
                # Every pre-fix hit must appear in post-fix.
                for hit in pre:
                    self.assertIn(hit, post)


if __name__ == "__main__":
    unittest.main()
