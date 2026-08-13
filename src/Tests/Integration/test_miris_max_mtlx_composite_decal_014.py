# Copyright 2026 Miris Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
"""
MAX-MTLX-COMPOSITE-DECAL-014 — Python mirror of the MAXScript
`unwrapBlendMaterialSubMtls` helper AFTER extending it to descend into
stock 3ds Max Composite Mtl and stock Blend material, on top of
MAX-MTLX-001 / MAX-MTLX-006 / MAX-MTLX-007 / MAX-MTLX-008 pipeline in
`src/translators/MtlxShaderWriter.cpp`.

Background
----------
MAX-MTLX-007 established `unwrapBlendMaterialSubMtls m visited depth` as
the wrapper walk that expands a possibly-wrapped material into the
ordered list of concrete sub-materials the slotMap probes. Its initial
scope was VRayBlendMtl (base + up to 9 coats) and VRayOverrideMtl (base
+ 4 per-ray overrides). Everything else — including stock 3ds Max
Composite Mtl, stock Blend, Top/Bottom, DoubleSided, Shell, and
Matte/Shadow — was INTENTIONALLY excluded per the comment block at
`MtlxShaderWriter.cpp:494-499`. The audit at MAX-MTLX-008 locked that
scope in.

The Spectrum Center arena's court-graphics materials break under that
scope. The arch-viz authoring pattern is:

    Composite (stock)                                <-- picked as leaf
      ├─ base = concrete court PhysicalMaterial       <-- NOT walked
      └─ layer[0] = DECAL_HORNETS_LOGO / painted-lines
                     PhysicalMaterial + opacity map   <-- NOT walked

Same for bicolor floor tiles:

    Blend (stock)                                    <-- picked as leaf
      ├─ .map1 = tile PhysicalMaterial A              <-- NOT walked
      └─ .map2 = tile PhysicalMaterial B              <-- NOT walked

Pre-014 the outer discovery loop treated the wrapper as a leaf, probed
its (empty) slot map, found nothing, and the material fell through to
`LastResortMtlxShaderWriter` — authoring a flat ND_standard_surface with
no textured inputs. Court concrete rendered untextured gray; the DECAL_
/ LOGO_ / painted-line layers never appeared.

Fix
---
Extend `unwrapBlendMaterialSubMtls` with two new class branches:

  * `cls == "Composite"` — iterate `.materialList` (up to N slots, index
    1 is the base, 2..N are overlays stacked upward). Base first so its
    texture graph wins the first-hit-wins slotMap dedupe. Skip any layer
    with `.mapEnabled[i] == false` OR (for i > 1) `.opacity[i] == 0`
    ("per-layer opacity respect").
  * `cls == "Blend"` — walk `.map1` (base) first, then `.map2` (overlay).
    Respect `.mapNEnabled` flags. `.mask` / `.mixAmount` are not gates
    on traversal (they control render-time mix, not whether a sub-
    material's textures participate in discovery).

Test strategy
-------------
The C++/MAXScript ships on Windows-only + Max SDK, so we cannot execute
the MAXScript here. Instead we mirror the wrapper hierarchy as a plain-
Python class stack that mirrors the exact `isProperty` / `getProperty`
surface the MAXScript block reads. Two implementations:

  * `unwrap_pre_014`  — the MAX-MTLX-007/008 walker (VRayBlendMtl +
    VRayOverrideMtl only). Composite / Blend fall through as leaves.
  * `unwrap_post_014` — the extended walker after this bite (adds
    Composite + Blend branches; VRayBlend/Override branches unchanged).

And the corresponding `discover_pre_014` / `discover_post_014` outer
loops around a first-hit-wins slotMap dedupe.

Assertions:

  1. Non-wrapper materials (PhysicalMaterial / OpenPBR / VRayMtl /
     StdMaterial) still return a 1-element list pre AND post-014.
  2. VRayBlendMtl and VRayOverrideMtl behavior is byte-identical
     between pre-014 and post-014 (surgical scope invariant — this bite
     does not touch MAX-MTLX-007's paths).
  3. Composite Mtl unwraps to [self, base-tree, layer-tree(2)...,
     layer-tree(N)...] with per-layer gates applied.
  4. Blend material unwraps to [self, map1-tree, map2-tree].
  5. First-hit-wins: base's textures beat any layer's / map2's; layers
     fill gaps.
  6. Per-layer opacity respect: opacity-0 overlay is skipped;
     mapEnabled=false layer is skipped; base layer is always walked
     regardless of its opacity (it IS the surface).
  7. Stock classes still explicitly excluded (Top/Bottom, DoubleSided,
     Shell, Matte/Shadow, Multi/Sub-Object) are NOT descended.
  8. Cycle safety: Composite pointing at itself terminates; deeply
     nested Blend-of-Composite-of-Blend terminates at the depth cap.
  9. Pre-fix defect fingerprint: the court-decal fixture drops every
     texture pre-014; the bicolor-floor fixture drops every texture
     pre-014.
 10. Post-fix resolution: the same fixtures produce the expected slot
     hits post-014.
 11. Arena census: a 179-material synthetic mix (100 plain + 40
     court-graphics Composite + 25 bicolor-floor Blend + 14 nested
     Blend-of-Composite) — the "resolvable diffuse" count rises from 100
     pre-fix to 179 post-fix, with the 100 plain-material bucket byte-
     identical.

Run: `hython test_miris_max_mtlx_composite_decal_014.py`
"""
import unittest


# =============================================================================
# Fake material classes mirroring the property surface the MAXScript
# `isProperty` / `getProperty` reads. Attribute names below match the
# MAXScript property names EXACTLY.
# =============================================================================


class _MaterialBase:
    """Shared base — every fake material has a class-name string that
    mirrors what `(classOf m) as string` returns in MAXScript."""

    max_class_name = "GenericMtl"


class PhysicalMaterial(_MaterialBase):
    max_class_name = "PhysicalMaterial"

    def __init__(self, base_color_map=None, roughness_map=None,
                 bump_map=None, cutout_map=None):
        self.base_color_map = base_color_map
        self.roughness_map = roughness_map
        self.bump_map = bump_map
        self.cutout_map = cutout_map


class OpenPbr(_MaterialBase):
    max_class_name = "OpenPBR"

    def __init__(self, baseColorMap=None):
        self.baseColorMap = baseColorMap


class VRayMtl(_MaterialBase):
    max_class_name = "VRayMtl"

    def __init__(self, base_color_map=None):
        self.base_color_map = base_color_map


class VRayBlendMtl(_MaterialBase):
    """From MAX-MTLX-007 — still exercised here to prove the new
    Composite/Blend paths do not disturb VRayBlend's behavior."""

    max_class_name = "VRayBlendMtl"

    def __init__(self, baseMtl=None, coats=()):
        self.baseMtl = baseMtl
        for i in range(1, 10):
            setattr(self, f"coatMtl_{i}",
                    coats[i - 1] if i - 1 < len(coats) else None)


class VRayOverrideMtl(_MaterialBase):
    max_class_name = "VRayOverrideMtl"

    def __init__(self, baseMtl=None, giMtl=None, reflectMtl=None,
                 refractMtl=None, shadowMtl=None):
        self.baseMtl = baseMtl
        self.giMtl = giMtl
        self.reflectMtl = reflectMtl
        self.refractMtl = refractMtl
        self.shadowMtl = shadowMtl


class CompositeMtl(_MaterialBase):
    """Stock 3ds Max Composite Mtl. Mirrors the MAXScript property
    surface exactly:
      - `.materialList` — array of sub-materials (index 1 is the base,
        2..N are overlays). MAXScript arrays are 1-indexed but we store
        them 0-indexed in Python; the walker translates when it reads
        `.count` and iterates by 1-based index.
      - `.mapEnabled` — parallel bool array; a false entry disables
        that layer.
      - `.opacity` — parallel float array; a 0.0 entry disables
        overlays (base still walks).
    """

    max_class_name = "Composite"

    def __init__(self, materialList=(), mapEnabled=None, opacity=None):
        self.materialList = list(materialList)
        # MAXScript's `.mapEnabled` defaults to all-true. Model that by
        # letting mapEnabled be either None (all-true) or a parallel list.
        self.mapEnabled = (list(mapEnabled)
                           if mapEnabled is not None else None)
        # Same convention for `.opacity` — None means "all-100 (default)".
        self.opacity = (list(opacity)
                        if opacity is not None else None)


class BlendMtl(_MaterialBase):
    """Stock 3ds Max Blend material. Mirrors the MAXScript property
    surface exactly:
      - `.map1` — base sub-material.
      - `.map2` — overlay sub-material.
      - `.map1Enabled` / `.map2Enabled` — per-side enable flag.
      - `.mask` / `.mixAmount` — render-time mix inputs, NOT gates on
        traversal.
    """

    max_class_name = "Blend"

    def __init__(self, map1=None, map2=None, map1Enabled=True,
                 map2Enabled=True, mask=None, mixAmount=50.0):
        self.map1 = map1
        self.map2 = map2
        self.map1Enabled = map1Enabled
        self.map2Enabled = map2Enabled
        self.mask = mask
        self.mixAmount = mixAmount


class MultiMtl(_MaterialBase):
    """Multi/Sub-Object. INTENTIONALLY not descended by the wrapper walk
    (MAX-GEO-002/006 owns the GeomSubset partition). Used here for a
    negative test."""

    max_class_name = "Multi/Sub-Object"

    def __init__(self, materialList=()):
        self.materialList = list(materialList)


class DoubleSidedMtl(_MaterialBase):
    """Stock DoubleSided. INTENTIONALLY excluded — front/back are
    visually distinct; a first-hit-wins texture merge would smear one
    face's map onto the other. Used here for a negative test."""

    max_class_name = "DoubleSided"

    def __init__(self, mtl1=None, mtl2=None):
        self.mtl1 = mtl1
        self.mtl2 = mtl2


class TopBottomMtl(_MaterialBase):
    """Stock Top/Bottom. INTENTIONALLY excluded — texture precedence
    depends on face orientation, not wrapping order."""

    max_class_name = "TopBottom"

    def __init__(self, topMaterial=None, bottomMaterial=None):
        self.topMaterial = topMaterial
        self.bottomMaterial = bottomMaterial


class ShellMtl(_MaterialBase):
    """Stock Shell Material. INTENTIONALLY excluded."""

    max_class_name = "Shell Material"

    def __init__(self, originalMaterial=None, bakedMaterial=None):
        self.originalMaterial = originalMaterial
        self.bakedMaterial = bakedMaterial


class MatteShadowMtl(_MaterialBase):
    """Stock Matte/Shadow. INTENTIONALLY excluded — carries no surface
    textures the discovery loop can use."""

    max_class_name = "Matte/Shadow"


# =============================================================================
# Pre-014 walker (MAX-MTLX-007/008 scope) and post-014 walker.
# =============================================================================


def _get(mat, name):
    """Mirror MAXScript's `isProperty m name` + `getProperty m name`."""
    return getattr(mat, name, None)


# Pre-014 wrapper table — only the two MAX-MTLX-007 classes.
_WRAPPER_TABLE_PRE = {
    "VRayBlendMtl": (
        ["baseMtl"] + [f"coatMtl_{i}" for i in range(1, 10)]
    ),
    "VRayOverrideMtl": [
        "baseMtl", "giMtl", "reflectMtl", "refractMtl", "shadowMtl",
    ],
}


def unwrap_pre_014(mat, visited=None, depth=0):
    """MAX-MTLX-007/008 walker — no Composite / Blend descent."""
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
    slots = _WRAPPER_TABLE_PRE.get(cls)
    if slots:
        for slot_name in slots:
            sub = _get(mat, slot_name)
            if sub is not None:
                for r in unwrap_pre_014(sub, visited, depth + 1):
                    out.append(r)
    return out


def unwrap_post_014(mat, visited=None, depth=0):
    """MAX-MTLX-COMPOSITE-DECAL-014 walker — adds Composite + Blend."""
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
    # VRayBlendMtl branch — unchanged from MAX-MTLX-007.
    if cls == "VRayBlendMtl":
        base = _get(mat, "baseMtl")
        if base is not None:
            for r in unwrap_post_014(base, visited, depth + 1):
                out.append(r)
        for i in range(1, 10):
            coat = _get(mat, f"coatMtl_{i}")
            if coat is not None:
                for r in unwrap_post_014(coat, visited, depth + 1):
                    out.append(r)
    # VRayOverrideMtl branch — unchanged from MAX-MTLX-007.
    elif cls == "VRayOverrideMtl":
        for sn in ("baseMtl", "giMtl", "reflectMtl", "refractMtl",
                   "shadowMtl"):
            sub = _get(mat, sn)
            if sub is not None:
                for r in unwrap_post_014(sub, visited, depth + 1):
                    out.append(r)
    # NEW — MAX-MTLX-COMPOSITE-DECAL-014: stock Composite Mtl.
    elif cls == "Composite":
        ml = _get(mat, "materialList")
        if ml is not None:
            mEnabled = _get(mat, "mapEnabled")
            mOpacity = _get(mat, "opacity")
            n = len(ml)
            for i in range(1, n + 1):
                layer = ml[i - 1]
                if layer is None:
                    continue
                enabled = True
                if mEnabled is not None and i <= len(mEnabled):
                    if mEnabled[i - 1] is False:
                        enabled = False
                op = 100.0
                if mOpacity is not None and i <= len(mOpacity):
                    op = float(mOpacity[i - 1])
                if not enabled:
                    continue
                if i > 1 and op == 0.0:
                    continue
                for r in unwrap_post_014(layer, visited, depth + 1):
                    out.append(r)
    # NEW — MAX-MTLX-COMPOSITE-DECAL-014: stock Blend material.
    elif cls == "Blend":
        m1 = _get(mat, "map1")
        m1_enabled = _get(mat, "map1Enabled")
        if m1 is not None and m1_enabled is not False:
            for r in unwrap_post_014(m1, visited, depth + 1):
                out.append(r)
        m2 = _get(mat, "map2")
        m2_enabled = _get(mat, "map2Enabled")
        if m2 is not None and m2_enabled is not False:
            for r in unwrap_post_014(m2, visited, depth + 1):
                out.append(r)
    return out


# =============================================================================
# Slot map + discovery — mirrors the MAXScript for slotMap iteration.
# =============================================================================


_TEST_SLOT_MAP = [
    ("base_color_map",       "base_color",         "color3"),
    ("baseColorMap",         "base_color",         "color3"),
    ("roughness_map",        "specular_roughness", "float"),
    ("bump_map",             "normal",             "vector3"),
    ("cutout_map",           "opacity",            "float"),
]


def _probe_material(mat, seen_inputs):
    hits = []
    for prop_name, mtlx_input, mtlx_type in _TEST_SLOT_MAP:
        tex = _get(mat, prop_name)
        if tex is None:
            continue
        fname = tex if isinstance(tex, str) else None
        if not fname:
            continue
        if mtlx_input in seen_inputs:
            continue
        seen_inputs.add(mtlx_input)
        hits.append((mtlx_input, mtlx_type, fname))
    return hits


def discover_pre_014(mat):
    seen = set()
    all_hits = []
    for sub in unwrap_pre_014(mat):
        all_hits.extend(_probe_material(sub, seen))
    return all_hits


def discover_post_014(mat):
    seen = set()
    all_hits = []
    for sub in unwrap_post_014(mat):
        all_hits.extend(_probe_material(sub, seen))
    return all_hits


# =============================================================================
# Tests
# =============================================================================


class TestNonWrapperMaterialsUnchanged(unittest.TestCase):
    """Surgical scope guard: PhysicalMaterial / OpenPBR / VRayMtl /
    StdMaterial must return a 1-element list from BOTH walkers. This is
    the 90%+ fast path — any regression here doubles work on every non-
    wrapper arena material."""

    def test_physical_material_unwraps_to_self_only(self):
        m = PhysicalMaterial(base_color_map="tex/floor.jpg")
        self.assertEqual(unwrap_post_014(m), [m])

    def test_openpbr_unwraps_to_self_only(self):
        m = OpenPbr(baseColorMap="tex/seat.jpg")
        self.assertEqual(unwrap_post_014(m), [m])

    def test_vraymtl_unwraps_to_self_only(self):
        m = VRayMtl(base_color_map="tex/vray_direct.jpg")
        self.assertEqual(unwrap_post_014(m), [m])

    def test_none_material_unwraps_to_empty_list(self):
        self.assertEqual(unwrap_post_014(None), [])

    def test_pre_and_post_agree_on_non_wrappers(self):
        for m in [
            PhysicalMaterial(base_color_map="tex/a.jpg"),
            OpenPbr(baseColorMap="tex/b.jpg"),
            VRayMtl(base_color_map="tex/c.jpg"),
            _MaterialBase(),
        ]:
            self.assertEqual(unwrap_pre_014(m), unwrap_post_014(m))


class TestVRayWrappersUnchanged(unittest.TestCase):
    """Surgical scope: MAX-MTLX-007's VRayBlendMtl + VRayOverrideMtl
    behavior must be byte-identical between pre-014 and post-014. This
    bite adds Composite/Blend branches; it must NOT touch the V-Ray
    branches."""

    def test_vray_blend_unchanged(self):
        base = PhysicalMaterial(base_color_map="tex/base.jpg")
        coat = PhysicalMaterial(base_color_map="tex/coat.jpg")
        blend = VRayBlendMtl(baseMtl=base, coats=[coat])
        self.assertEqual(unwrap_pre_014(blend), unwrap_post_014(blend))

    def test_vray_override_unchanged(self):
        base = PhysicalMaterial(base_color_map="tex/base.jpg")
        gi = PhysicalMaterial(base_color_map="tex/gi.jpg")
        vom = VRayOverrideMtl(baseMtl=base, giMtl=gi)
        self.assertEqual(unwrap_pre_014(vom), unwrap_post_014(vom))

    def test_nested_vray_blend_of_override_unchanged(self):
        # Real arch-viz pattern: layered paint wrapped in GI-corrected
        # override. Must recurse identically pre and post.
        inner_base = PhysicalMaterial(base_color_map="tex/paint.jpg")
        blend = VRayBlendMtl(baseMtl=inner_base)
        vom = VRayOverrideMtl(baseMtl=blend)
        self.assertEqual(unwrap_pre_014(vom), unwrap_post_014(vom))


class TestCompositeMtlUnwrap(unittest.TestCase):
    """The Composite Mtl walk — one of two primary defects this fix
    closes (court-line decals over a concrete court PhysicalMaterial
    base)."""

    def test_composite_unwraps_to_self_then_base(self):
        base = PhysicalMaterial(base_color_map="tex/concrete_court.jpg")
        comp = CompositeMtl(materialList=[base])
        self.assertEqual(unwrap_post_014(comp), [comp, base])

    def test_composite_base_walked_before_overlays(self):
        # Court decal: base = concrete court, overlay = painted lines.
        # Base first so its texture wins first-hit-wins over the overlay.
        base = PhysicalMaterial(base_color_map="tex/concrete_court.jpg")
        decal = PhysicalMaterial(base_color_map="tex/court_lines.jpg")
        comp = CompositeMtl(materialList=[base, decal])
        self.assertEqual(unwrap_post_014(comp), [comp, base, decal])

    def test_composite_walks_all_overlay_slots_in_order(self):
        # A stack with 3 overlays (base + logo + text + boundary lines).
        base = PhysicalMaterial(base_color_map="tex/concrete.jpg")
        logo = PhysicalMaterial(base_color_map="tex/hornets_logo.jpg")
        text = PhysicalMaterial(base_color_map="tex/text_buzz.jpg")
        boundary = PhysicalMaterial(base_color_map="tex/court_line.jpg")
        comp = CompositeMtl(materialList=[base, logo, text, boundary])
        self.assertEqual(
            unwrap_post_014(comp),
            [comp, base, logo, text, boundary])

    def test_composite_with_null_base_skips_slot(self):
        # An unassigned base slot shouldn't crash — should skip and walk
        # the surviving overlays.
        overlay = PhysicalMaterial(base_color_map="tex/only_overlay.jpg")
        comp = CompositeMtl(materialList=[None, overlay])
        # Slot 1 (base) is None → skipped. Slot 2 (overlay, i > 1) is
        # walked (default opacity 100).
        self.assertEqual(unwrap_post_014(comp), [comp, overlay])

    def test_composite_skips_disabled_overlay_layer(self):
        # An overlay with mapEnabled[i] = false is skipped.
        base = PhysicalMaterial(base_color_map="tex/base.jpg")
        disabled = PhysicalMaterial(base_color_map="tex/disabled.jpg")
        enabled = PhysicalMaterial(base_color_map="tex/enabled.jpg")
        comp = CompositeMtl(
            materialList=[base, disabled, enabled],
            mapEnabled=[True, False, True])
        self.assertEqual(unwrap_post_014(comp), [comp, base, enabled])

    def test_composite_skips_opacity_zero_overlay_layer(self):
        # An overlay with opacity == 0 is skipped ("per-layer opacity
        # respect"). Dead layer, no pixels.
        base = PhysicalMaterial(base_color_map="tex/base.jpg")
        dead = PhysicalMaterial(base_color_map="tex/dead.jpg")
        live = PhysicalMaterial(base_color_map="tex/live.jpg")
        comp = CompositeMtl(
            materialList=[base, dead, live],
            opacity=[100.0, 0.0, 100.0])
        self.assertEqual(unwrap_post_014(comp), [comp, base, live])

    def test_composite_base_walked_even_when_base_opacity_is_zero(self):
        # The base (index 1) is the surface an unmasked pixel resolves
        # to; walk it regardless of its opacity setting.
        base = PhysicalMaterial(base_color_map="tex/base.jpg")
        overlay = PhysicalMaterial(base_color_map="tex/overlay.jpg")
        comp = CompositeMtl(
            materialList=[base, overlay],
            opacity=[0.0, 100.0])
        # Base at slot 1 is walked despite opacity=0; overlay at slot 2
        # is walked with default gating.
        self.assertEqual(unwrap_post_014(comp), [comp, base, overlay])

    def test_composite_with_all_overlays_dead_still_walks_base(self):
        base = PhysicalMaterial(base_color_map="tex/base.jpg")
        d1 = PhysicalMaterial(base_color_map="tex/d1.jpg")
        d2 = PhysicalMaterial(base_color_map="tex/d2.jpg")
        comp = CompositeMtl(
            materialList=[base, d1, d2],
            mapEnabled=[True, False, False])
        self.assertEqual(unwrap_post_014(comp), [comp, base])

    def test_composite_of_composite_recurses(self):
        # A Composite whose base is itself a Composite (motivating case:
        # a court sub-composition mounted onto a floor composition).
        innerbase = PhysicalMaterial(base_color_map="tex/inner_base.jpg")
        innerdec = PhysicalMaterial(base_color_map="tex/inner_dec.jpg")
        inner = CompositeMtl(materialList=[innerbase, innerdec])
        outerdec = PhysicalMaterial(base_color_map="tex/outer_dec.jpg")
        outer = CompositeMtl(materialList=[inner, outerdec])
        result = unwrap_post_014(outer)
        self.assertEqual(
            result, [outer, inner, innerbase, innerdec, outerdec])


class TestBlendMtlUnwrap(unittest.TestCase):
    """The stock Blend walk — the other primary defect this fix closes
    (bicolor floor tiles + two-tone plastics)."""

    def test_blend_unwraps_to_self_then_map1(self):
        base = PhysicalMaterial(base_color_map="tex/tile_A.jpg")
        blend = BlendMtl(map1=base)
        self.assertEqual(unwrap_post_014(blend), [blend, base])

    def test_blend_map1_walked_before_map2(self):
        # map1-first so first-hit-wins keeps map1's textures winning.
        m1 = PhysicalMaterial(base_color_map="tex/tile_A.jpg")
        m2 = PhysicalMaterial(base_color_map="tex/tile_B.jpg")
        blend = BlendMtl(map1=m1, map2=m2)
        self.assertEqual(unwrap_post_014(blend), [blend, m1, m2])

    def test_blend_with_null_map1_walks_map2(self):
        m2 = PhysicalMaterial(base_color_map="tex/only_m2.jpg")
        blend = BlendMtl(map1=None, map2=m2)
        self.assertEqual(unwrap_post_014(blend), [blend, m2])

    def test_blend_skips_disabled_map1(self):
        m1 = PhysicalMaterial(base_color_map="tex/off.jpg")
        m2 = PhysicalMaterial(base_color_map="tex/on.jpg")
        blend = BlendMtl(map1=m1, map2=m2, map1Enabled=False)
        self.assertEqual(unwrap_post_014(blend), [blend, m2])

    def test_blend_skips_disabled_map2(self):
        m1 = PhysicalMaterial(base_color_map="tex/on.jpg")
        m2 = PhysicalMaterial(base_color_map="tex/off.jpg")
        blend = BlendMtl(map1=m1, map2=m2, map2Enabled=False)
        self.assertEqual(unwrap_post_014(blend), [blend, m1])

    def test_blend_mixamount_does_not_gate_traversal(self):
        # mixAmount controls render-time mix ratio, not whether the
        # sub-materials' textures participate in discovery.
        m1 = PhysicalMaterial(base_color_map="tex/tile_A.jpg")
        m2 = PhysicalMaterial(base_color_map="tex/tile_B.jpg")
        for mix in (0.0, 25.0, 50.0, 75.0, 100.0):
            blend = BlendMtl(map1=m1, map2=m2, mixAmount=mix)
            self.assertEqual(unwrap_post_014(blend), [blend, m1, m2])

    def test_blend_of_composite_recurses(self):
        # A Blend whose map1 is a Composite (bicolor court where each
        # side is layered).
        cb = PhysicalMaterial(base_color_map="tex/cb.jpg")
        cd = PhysicalMaterial(base_color_map="tex/cd.jpg")
        comp = CompositeMtl(materialList=[cb, cd])
        other = PhysicalMaterial(base_color_map="tex/other.jpg")
        blend = BlendMtl(map1=comp, map2=other)
        self.assertEqual(
            unwrap_post_014(blend), [blend, comp, cb, cd, other])


class TestFirstHitWinsPrecedence(unittest.TestCase):
    """Discover-level test: the wrapper walk actually restores the
    texture inventory, and base's textures win over overlays' via the
    (base-first, overlay-second) traversal + seenInputs dedupe."""

    def test_composite_base_diffuse_wins_over_overlay(self):
        base = PhysicalMaterial(base_color_map="tex/base_wins.jpg")
        overlay = PhysicalMaterial(base_color_map="tex/overlay_loses.jpg")
        comp = CompositeMtl(materialList=[base, overlay])
        hits = discover_post_014(comp)
        base_color_hits = [h for h in hits if h[0] == "base_color"]
        self.assertEqual(len(base_color_hits), 1)
        self.assertEqual(base_color_hits[0][2], "tex/base_wins.jpg")

    def test_composite_overlay_fills_gap_when_base_has_no_map(self):
        # Real arch-viz: base = flat concrete (no diffuse), overlay =
        # court graphics with diffuse. The overlay's texture must be
        # walked so the material isn't left textureless on export.
        base = PhysicalMaterial(base_color_map=None,
                                roughness_map="tex/concrete_rough.jpg")
        overlay = PhysicalMaterial(base_color_map="tex/court_lines.jpg")
        comp = CompositeMtl(materialList=[base, overlay])
        hits = discover_post_014(comp)
        by_input = {h[0]: h[2] for h in hits}
        # Base contributed roughness.
        self.assertEqual(by_input["specular_roughness"],
                         "tex/concrete_rough.jpg")
        # Overlay filled the base_color gap.
        self.assertEqual(by_input["base_color"], "tex/court_lines.jpg")

    def test_composite_second_overlay_fills_gap_from_disabled_first(self):
        base = PhysicalMaterial()  # empty
        o1 = PhysicalMaterial(base_color_map="tex/o1.jpg")
        o2 = PhysicalMaterial(base_color_map="tex/o2.jpg")
        # o1 disabled → o2 wins.
        comp = CompositeMtl(
            materialList=[base, o1, o2],
            mapEnabled=[True, False, True])
        hits = discover_post_014(comp)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0][2], "tex/o2.jpg")

    def test_blend_map1_diffuse_wins_over_map2(self):
        m1 = PhysicalMaterial(base_color_map="tex/tile_A.jpg")
        m2 = PhysicalMaterial(base_color_map="tex/tile_B.jpg")
        blend = BlendMtl(map1=m1, map2=m2)
        hits = discover_post_014(blend)
        base_color_hits = [h for h in hits if h[0] == "base_color"]
        self.assertEqual(len(base_color_hits), 1)
        self.assertEqual(base_color_hits[0][2], "tex/tile_A.jpg")

    def test_blend_map2_fills_gap_when_map1_has_no_diffuse(self):
        m1 = PhysicalMaterial(base_color_map=None,
                              roughness_map="tex/tile_A_rough.jpg")
        m2 = PhysicalMaterial(base_color_map="tex/tile_B_dif.jpg")
        blend = BlendMtl(map1=m1, map2=m2)
        hits = discover_post_014(blend)
        by_input = {h[0]: h[2] for h in hits}
        self.assertEqual(by_input["specular_roughness"],
                         "tex/tile_A_rough.jpg")
        self.assertEqual(by_input["base_color"], "tex/tile_B_dif.jpg")


class TestExcludedClassesNotDescended(unittest.TestCase):
    """Negative surgical scope: stock classes we deliberately continue
    to exclude must NOT be descended. These are the fingerprint tests
    that fail if a future refactor widens the gate."""

    def test_multi_sub_object_not_descended(self):
        # Owned by MAX-GEO-002/006 GeomSubset partition, NOT by this
        # wrapper walk.
        m1 = PhysicalMaterial(base_color_map="tex/face1.jpg")
        m2 = PhysicalMaterial(base_color_map="tex/face2.jpg")
        multi = MultiMtl(materialList=[m1, m2])
        self.assertEqual(unwrap_post_014(multi), [multi])

    def test_doublesided_not_descended(self):
        # Front / back are visually distinct; do NOT smear one face's
        # map onto the other.
        f = PhysicalMaterial(base_color_map="tex/front.jpg")
        b = PhysicalMaterial(base_color_map="tex/back.jpg")
        ds = DoubleSidedMtl(mtl1=f, mtl2=b)
        self.assertEqual(unwrap_post_014(ds), [ds])

    def test_topbottom_not_descended(self):
        # Precedence depends on face normal, not wrapping order — cannot
        # first-hit-wins.
        t = PhysicalMaterial(base_color_map="tex/top.jpg")
        b = PhysicalMaterial(base_color_map="tex/bot.jpg")
        tb = TopBottomMtl(topMaterial=t, bottomMaterial=b)
        self.assertEqual(unwrap_post_014(tb), [tb])

    def test_shell_not_descended(self):
        # Original / bake pair — bake side is a rendered output, not a
        # substrate texture.
        orig = PhysicalMaterial(base_color_map="tex/orig.jpg")
        baked = PhysicalMaterial(base_color_map="tex/baked.jpg")
        sh = ShellMtl(originalMaterial=orig, bakedMaterial=baked)
        self.assertEqual(unwrap_post_014(sh), [sh])

    def test_matte_shadow_not_descended(self):
        ms = MatteShadowMtl()
        self.assertEqual(unwrap_post_014(ms), [ms])


class TestCycleAndDepthSafety(unittest.TestCase):
    """Terminate on pathological hierarchies. Real MAXScript arrays are
    reference types; an artist can (accidentally) point a slot back at
    a parent. The `visited` cycle guard + depth cap must handle both."""

    def test_composite_self_reference_terminates(self):
        # A Composite where the base slot points back at the composite
        # itself (pathological but constructible in MAXScript).
        comp = CompositeMtl(materialList=[])
        comp.materialList = [comp]
        # Walker terminates at the cycle guard and returns [comp].
        self.assertEqual(unwrap_post_014(comp), [comp])

    def test_blend_mutual_reference_terminates(self):
        # Two Blends pointing at each other's map1 slots.
        b1 = BlendMtl()
        b2 = BlendMtl()
        b1.map1 = b2
        b2.map1 = b1
        # Depth-first walk stops as soon as it revisits either.
        result = unwrap_post_014(b1)
        # Result contains b1 and b2 (each visited once), nothing more.
        self.assertEqual(set(id(x) for x in result), {id(b1), id(b2)})
        self.assertEqual(len(result), 2)

    def test_deep_nesting_terminates_at_depth_cap(self):
        # Nested Blend-of-Composite-of-Blend-of-... beyond depth 6.
        deep = PhysicalMaterial(base_color_map="tex/deep.jpg")
        for _ in range(20):
            deep = BlendMtl(map1=deep)
        result = unwrap_post_014(deep)
        # Terminates before the innermost PhysicalMaterial — depth cap
        # is 6, so at most 7 materials visited.
        self.assertLessEqual(len(result), 8)


class TestPreFixDefectFingerprint(unittest.TestCase):
    """Regression: pre-014 discovery MUST drop the arena fixtures'
    textures. If any of these ever passes pre-014, the fingerprint is
    stale and the bite's motivation is invalidated."""

    def test_pre_014_drops_court_decal_composite(self):
        # Court composite: concrete base + painted-lines decal overlay.
        base = PhysicalMaterial(base_color_map="tex/concrete_court.jpg",
                                roughness_map="tex/concrete_rough.jpg")
        decal = PhysicalMaterial(base_color_map="tex/court_lines.jpg",
                                 cutout_map="tex/court_lines_alpha.jpg")
        comp = CompositeMtl(materialList=[base, decal])
        hits = discover_pre_014(comp)
        # Composite itself has no slot-map properties, so pre-fix
        # discovery finds nothing.
        self.assertEqual(hits, [])

    def test_pre_014_drops_bicolor_blend_floor(self):
        m1 = PhysicalMaterial(base_color_map="tex/tile_A.jpg")
        m2 = PhysicalMaterial(base_color_map="tex/tile_B.jpg")
        blend = BlendMtl(map1=m1, map2=m2)
        hits = discover_pre_014(blend)
        self.assertEqual(hits, [])

    def test_pre_014_drops_nested_blend_of_composite(self):
        c_base = PhysicalMaterial(base_color_map="tex/cb.jpg")
        c_dec = PhysicalMaterial(base_color_map="tex/cd.jpg")
        comp = CompositeMtl(materialList=[c_base, c_dec])
        other = PhysicalMaterial(base_color_map="tex/other.jpg")
        blend = BlendMtl(map1=comp, map2=other)
        hits = discover_pre_014(blend)
        # Blend not descended pre-014, so composite + other never
        # reached.
        self.assertEqual(hits, [])


class TestPostFixResolution(unittest.TestCase):
    """Positive: post-014 discovery reaches the base's textures + fills
    from overlays / map2 where the base has gaps."""

    def test_post_014_recovers_court_decal_texture_stack(self):
        base = PhysicalMaterial(base_color_map="tex/concrete_court.jpg",
                                roughness_map="tex/concrete_rough.jpg")
        # Overlay carries the painted-lines diffuse AND an alpha
        # channel routed to opacity.
        decal = PhysicalMaterial(base_color_map="tex/court_lines.jpg",
                                 cutout_map="tex/court_lines_alpha.jpg")
        comp = CompositeMtl(materialList=[base, decal])
        hits = discover_post_014(comp)
        by_input = {h[0]: h[2] for h in hits}
        # Base wins the diffuse (base-first + first-hit-wins).
        self.assertEqual(by_input["base_color"], "tex/concrete_court.jpg")
        self.assertEqual(by_input["specular_roughness"],
                         "tex/concrete_rough.jpg")
        # Decal's alpha routed through cutout_map / opacity slot fills
        # the gap (base has no cutout_map).
        self.assertEqual(by_input["opacity"], "tex/court_lines_alpha.jpg")

    def test_post_014_recovers_bicolor_blend_floor(self):
        m1 = PhysicalMaterial(base_color_map="tex/tile_A.jpg",
                              roughness_map="tex/tile_A_rough.jpg")
        m2 = PhysicalMaterial(base_color_map="tex/tile_B.jpg",
                              bump_map="tex/tile_B_bump.jpg")
        blend = BlendMtl(map1=m1, map2=m2)
        hits = discover_post_014(blend)
        by_input = {h[0]: h[2] for h in hits}
        # map1 wins base_color; map1 supplies roughness; map2 fills the
        # bump slot the base didn't set.
        self.assertEqual(by_input["base_color"], "tex/tile_A.jpg")
        self.assertEqual(by_input["specular_roughness"],
                         "tex/tile_A_rough.jpg")
        self.assertEqual(by_input["normal"], "tex/tile_B_bump.jpg")

    def test_post_014_recovers_nested_blend_of_composite(self):
        c_base = PhysicalMaterial(base_color_map="tex/cb.jpg")
        c_dec = PhysicalMaterial(bump_map="tex/cd_bump.jpg")
        comp = CompositeMtl(materialList=[c_base, c_dec])
        other = PhysicalMaterial(roughness_map="tex/other_rough.jpg")
        blend = BlendMtl(map1=comp, map2=other)
        hits = discover_post_014(blend)
        by_input = {h[0]: h[2] for h in hits}
        # Composite's base contributes base_color; its overlay
        # contributes bump; the Blend's map2 fills roughness.
        self.assertEqual(by_input["base_color"], "tex/cb.jpg")
        self.assertEqual(by_input["normal"], "tex/cd_bump.jpg")
        self.assertEqual(by_input["specular_roughness"],
                         "tex/other_rough.jpg")


class TestStrictSupersetOfMtlx007(unittest.TestCase):
    """The MAX-MTLX-COMPOSITE-DECAL-014 walker must be a STRICT superset
    of the MAX-MTLX-007 walker. Any material the pre-014 walker resolves
    to a non-empty result must also resolve to the SAME result post-014
    (the two extended branches don't touch V-Ray classes / plain
    materials)."""

    def test_strict_superset_across_representative_shapes(self):
        cases = [
            PhysicalMaterial(base_color_map="tex/plain.jpg"),
            OpenPbr(baseColorMap="tex/openpbr.jpg"),
            VRayMtl(base_color_map="tex/vray.jpg"),
            VRayBlendMtl(
                baseMtl=PhysicalMaterial(base_color_map="tex/vb_b.jpg"),
                coats=[PhysicalMaterial(base_color_map="tex/vb_c.jpg")]),
            VRayOverrideMtl(
                baseMtl=PhysicalMaterial(base_color_map="tex/vo_b.jpg"),
                giMtl=PhysicalMaterial(base_color_map="tex/vo_gi.jpg")),
            _MaterialBase(),
            None,
            MultiMtl(materialList=[PhysicalMaterial(base_color_map="a")]),
            DoubleSidedMtl(mtl1=PhysicalMaterial(base_color_map="d")),
        ]
        for m in cases:
            self.assertEqual(
                unwrap_pre_014(m), unwrap_post_014(m),
                msg=f"pre/post disagree on non-014-scope case: {m!r}")


class TestArenaCensus(unittest.TestCase):
    """A 179-material synthetic mix reflecting the Spectrum Center arena
    density post-MTLX-007/008. Buckets:
      - 100 plain PhysicalMaterial (no wrapper)
      - 40 court-graphics Composite Mtl (base + one decal overlay)
      - 25 bicolor floor Blend (map1 + map2)
      - 14 nested Blend-of-Composite (glossy floor over decal court)

    Pre-014 the 40 + 25 + 14 = 79 wrapped materials all fall through as
    leaves, resolving zero diffuse slots. Post-014 all 79 resolve their
    base diffuse. The 100 plain-material bucket is byte-identical
    between pre-014 and post-014."""

    def _make_mix(self):
        plain = [
            PhysicalMaterial(base_color_map=f"tex/plain_{i}.jpg")
            for i in range(100)
        ]
        court = [
            CompositeMtl(materialList=[
                PhysicalMaterial(base_color_map=f"tex/court_base_{i}.jpg"),
                PhysicalMaterial(base_color_map=f"tex/court_decal_{i}.jpg",
                                 cutout_map=f"tex/court_alpha_{i}.jpg"),
            ])
            for i in range(40)
        ]
        bicolor = [
            BlendMtl(
                map1=PhysicalMaterial(base_color_map=f"tex/tile_A_{i}.jpg"),
                map2=PhysicalMaterial(base_color_map=f"tex/tile_B_{i}.jpg"))
            for i in range(25)
        ]
        nested = [
            BlendMtl(
                map1=CompositeMtl(materialList=[
                    PhysicalMaterial(
                        base_color_map=f"tex/glossy_base_{i}.jpg"),
                    PhysicalMaterial(
                        base_color_map=f"tex/glossy_dec_{i}.jpg"),
                ]),
                map2=PhysicalMaterial(base_color_map=f"tex/other_{i}.jpg"))
            for i in range(14)
        ]
        return plain, court, bicolor, nested

    def test_pre_014_drops_all_wrapped_materials(self):
        plain, court, bicolor, nested = self._make_mix()
        pre_resolved_wrapped = sum(
            1 for m in court + bicolor + nested if discover_pre_014(m))
        self.assertEqual(pre_resolved_wrapped, 0)

    def test_post_014_resolves_all_wrapped_materials(self):
        plain, court, bicolor, nested = self._make_mix()
        post_resolved_wrapped = sum(
            1 for m in court + bicolor + nested if discover_post_014(m))
        self.assertEqual(post_resolved_wrapped, 40 + 25 + 14)

    def test_plain_bucket_byte_identical_pre_and_post(self):
        plain, _, _, _ = self._make_mix()
        for m in plain:
            self.assertEqual(discover_pre_014(m), discover_post_014(m))

    def test_arena_delta_is_the_wrapped_bucket_size(self):
        plain, court, bicolor, nested = self._make_mix()
        every = plain + court + bicolor + nested
        pre_resolved = sum(1 for m in every if discover_pre_014(m))
        post_resolved = sum(1 for m in every if discover_post_014(m))
        # Pre-014: 100 plain resolve. Post-014: all 179 resolve. Delta
        # matches the wrapped bucket size the diagnostic saw.
        self.assertEqual(pre_resolved, 100)
        self.assertEqual(post_resolved, 179)
        self.assertEqual(post_resolved - pre_resolved,
                         len(court) + len(bicolor) + len(nested))


if __name__ == "__main__":
    unittest.main(verbosity=2)
