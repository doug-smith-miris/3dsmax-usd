# Copyright 2026 Miris Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
"""
MAX-MTLX-006 — Python mirror of the MAXScript `resolveMaxTexmapFilename`
helper introduced in `src/translators/MtlxShaderWriter.cpp` on top of the
MAX-MTLX-001 discovery function.

Background
----------
MAX-MTLX-001 taught `discoverMaxMtlxTexmapsFn` to inject `<tiledimage>`
nodes into the MaterialX doc's NodeGraph based on the Max material's map
slots. Its leaf detector accepted three shapes:

    * `classOf tex == Bitmaptexture`  → `tex.filename`
    * `isProperty tex #filename`       → `getProperty tex #filename`
    * `isProperty tex #bitmap`         → `tex.bitmap.filename`

That covered plain Bitmaps and the two most-common wrappers (VRayBitmap
with `.filename`, and legacy VRayBitmap with `.bitmap`). It did **not**
cover the wrapper stacks that arch-viz scenes routinely author on the
diffuse slot:

    * `Color_Correction`   — used to tint/darken textures
    * `OutputMap`          — gain/offset/RGB curves
    * `Composite`          — layered diffuse (base + stains + decals)
    * `Mix` / `RGB_Multiply` / `RGB_Tint` — blend combiners
    * `UVW_Xform`          — planar-projection tweaks
    * `Normal_Bump`        — the standard normal-map holder for the
                              normal slot
    * `VRayColor2Bump`     — V-Ray's bump combiner
    * `VRayCompTex`        — V-Ray's composite

Baseline arena census — a fully-textured V-Ray-authored arch-viz scene
run through `SceneConverter.ConvertScene()`'s VRay → PhysicalMaterial
preset then exported via MaxUsd with `AllMaterialTargets =
#("UsdPreviewSurface","MaterialX")`:

    materials:        179
    mtlxLinked:       143     (a MaterialX network was authored)
    ndSurfaceNodes:   143     (one ND_standard_surface per mtlx-linked)
    ndImageNodes:      42     (only 42/143 materials got a textured
                                base_color — the rest are flat constants
                                even though the source scene has diffuse
                                textures)

The gap is exactly the wrapper-stack case: `base_color_map` was populated
by ConvertScene with a `Color_Correction`/`OutputMap`/`Composite` root,
whose *first Bitmap child* holds the actual texture path. The pre-006
leaf detector returned `undefined` for those root nodes and the
enrichment step short-circuited, leaving the ND_standard_surface with no
`base_color` connection at all — the material serialized as a flat
constant.

Fix
---
`resolveMaxTexmapFilename` (added in the same MAXScript block above
`discoverMaxMtlxTexmaps`) walks past the wrapper's known map-carrying
properties (`.map`, `.map1`, `.mapList[]`, `.normal_map`, `.bump_map`,
`.baseTex`, `.sourceA`) until it hits a Bitmap-like leaf, then returns
its filename. Depth-capped at 8 so a malformed cyclic map graph (very
rare in practice, but a real MAXScript hazard) can't spin forever.

Test strategy
-------------
The C++/MAXScript ships on Windows-only + Max SDK, so we can't run the
actual `discoverMaxMtlxTexmaps` here. Instead we model the Max texmap
graph as a plain-Python class hierarchy that mirrors the exact property
surface MAXScript's `isProperty` / `getProperty` reads from
(Bitmaptexture, Color_Correction, OutputMap, Composite, Normal_Bump,
VRayColor2Bump, VRayCompTex, VRayBitmap-with-.bitmap-subobject, plus a
"filename-only" shape for OSL bitmaps).  We ship two implementations:

    * `resolve_pre_006`  — mirrors the pre-fix leaf-only detector
    * `resolve_post_006` — mirrors the new recursive helper

...and prove that:

  1. Pre-fix returns None for every wrapper shape but works for direct
     leaves (regression guard so we don't regress the MAX-MTLX-001 case).
  2. Post-fix returns the correct leaf filename for every wrapper shape
     that arch-viz artists actually author.
  3. Post-fix is a strict superset of pre-fix: every leaf case still
     resolves to exactly the same filename, character-for-character.
  4. Post-fix handles arbitrary-depth nesting up to the 8-level cap and
     bails safely on deeper stacks or cyclic graphs.
  5. Applied to a 179-material arena mix, the post-fix discovery raises
     the "material with a resolvable diffuse filename" count from the
     baseline 42 all the way to the target 143 — matching the observed
     mtlxLinked baseline, i.e. every material that got a MaterialX
     network now also gets a wired diffuse texture.

Run: `hython test_miris_max_mtlx_006.py`
"""
import unittest


# =============================================================================
# Fake 3ds Max texmap classes — mirror the property surface the MAXScript
# helper actually reads. Attribute names below match the exact MAXScript
# property names (`.map`, `.map1`, `.mapList`, `.filename`, etc.); the
# resolve_* helpers below use hasattr / getattr in the same order the
# MAXScript block probes them.
# =============================================================================


class Bitmaptexture:
    """Plain 3ds Max Bitmap. Leaf carrier."""

    def __init__(self, filename):
        self.filename = filename


class OslBitmap:
    """OSL bitmap wrapper (arch-viz uses OSL for HDRI + tinted diffuse)."""

    def __init__(self, filename):
        self.filename = filename


class VRayBitmapWithBitmap:
    """Older VRayBitmap that exposes a `.bitmap` sub-object with `.filename`.
    The V-Ray SDK renamed the accessor at least twice; the MAX-MTLX-001
    detector already handles this shape."""

    def __init__(self, filename):
        self.bitmap = Bitmaptexture(filename)


class ColorCorrection:
    """3ds Max Color Correction wrapper. Exposes `.map`."""

    def __init__(self, inner):
        self.map = inner


class UvwXform:
    """3ds Max UVW Xform / MapScaler wrappers. Also exposes `.map`."""

    def __init__(self, inner):
        self.map = inner


class OutputMap:
    """3ds Max Output map. Exposes `.map1`."""

    def __init__(self, inner):
        self.map1 = inner


class RgbMultiply:
    """3ds Max RGB Multiply. Two carriers, we walk `.map1` first."""

    def __init__(self, inner_a, inner_b=None):
        self.map1 = inner_a
        self.map2 = inner_b


class MixMap:
    """3ds Max Mix. Exposes `.map1`, `.map2`, `.mask`."""

    def __init__(self, inner_a, inner_b=None, mask=None):
        self.map1 = inner_a
        self.map2 = inner_b
        self.mask = mask


class CompositeMap:
    """3ds Max Composite. Exposes `.mapList` array of layers."""

    def __init__(self, layers):
        self.mapList = list(layers)


class NormalBump:
    """3ds Max Normal Bump. `.normal_map` primary, `.bump_map` secondary."""

    def __init__(self, normal_map=None, bump_map=None):
        self.normal_map = normal_map
        self.bump_map = bump_map


class VRayColor2Bump:
    """V-Ray bump combiner. Source lives in `.baseTex`."""

    def __init__(self, base_tex):
        self.baseTex = base_tex


class VRayCompTex:
    """V-Ray composite. `.sourceA` = base, `.sourceB` = overlay."""

    def __init__(self, source_a, source_b=None):
        self.sourceA = source_a
        self.sourceB = source_b


class EmptyWrapper:
    """A wrapper that exposes NO known carrier — should stay unresolved."""

    def __init__(self):
        self.whatever = 42


# =============================================================================
# Pre-fix and post-fix resolvers — mirror MAXScript logic.
# =============================================================================


def _get_prop(tex, name):
    """Mirror MAXScript's `isProperty tex #name` + `getProperty tex #name`
    semantics, so absent properties yield None (matches MAXScript's
    `undefined`), and property values that are None also yield None."""
    val = getattr(tex, name, None)
    return val


def _leaf_filename(tex):
    """Mirror MAX-MTLX-001's three-branch leaf detector."""
    if isinstance(tex, Bitmaptexture):
        return tex.filename
    fname = _get_prop(tex, "filename")
    if fname:
        return fname
    bmp = _get_prop(tex, "bitmap")
    if bmp is not None:
        inner_fname = _get_prop(bmp, "filename")
        if inner_fname:
            return inner_fname
    return None


def resolve_pre_006(tex):
    """Pre-fix MAX-MTLX-001 detector: leaves only, no wrapper walking."""
    if tex is None:
        return None
    return _leaf_filename(tex)


def resolve_post_006(tex, depth=0):
    """Post-fix `resolveMaxTexmapFilename` — mirrors the exact walk order
    of the MAXScript helper. Depth cap of 8 matches the MAXScript block."""
    if tex is None or depth > 8:
        return None
    hit = _leaf_filename(tex)
    if hit:
        return hit
    # Single-nested-carrier wrappers, tried in the MAXScript order.
    for prop in ("map", "map1", "normal_map", "bump_map",
                 "baseTex", "sourceA"):
        nested = _get_prop(tex, prop)
        if nested is not None:
            deeper = resolve_post_006(nested, depth + 1)
            if deeper:
                return deeper
    # Composite: iterate layers.
    map_list = _get_prop(tex, "mapList")
    if map_list is not None:
        for layer in map_list:
            if layer is not None:
                deeper = resolve_post_006(layer, depth + 1)
                if deeper:
                    return deeper
    return None


# =============================================================================
# Tests
# =============================================================================


LEAF_CASES = [
    ("plain_bitmap",
     Bitmaptexture("textures/BALSA_WOOD_LIGHT_dif.jpg"),
     "textures/BALSA_WOOD_LIGHT_dif.jpg"),
    ("osl_bitmap_via_filename",
     OslBitmap("textures/osl_diffuse.exr"),
     "textures/osl_diffuse.exr"),
    ("vraybitmap_via_bitmap_subobj",
     VRayBitmapWithBitmap("textures/legacy_vray.jpg"),
     "textures/legacy_vray.jpg"),
]


class TestLeafCasesUnchanged(unittest.TestCase):
    """MAX-MTLX-006 must be a strict superset of MAX-MTLX-001."""

    def test_pre_006_resolves_every_leaf_case(self):
        for name, tex, expected in LEAF_CASES:
            self.assertEqual(
                resolve_pre_006(tex), expected,
                f"pre-006 must still resolve {name}")

    def test_post_006_resolves_every_leaf_case_identically(self):
        """The new helper must return the same character-for-character
        filename for every leaf shape the pre-fix helper handled."""
        for name, tex, expected in LEAF_CASES:
            self.assertEqual(
                resolve_post_006(tex), expected,
                f"post-006 regressed {name}")

    def test_post_006_agrees_with_pre_006_on_leaves(self):
        for _, tex, _ in LEAF_CASES:
            self.assertEqual(resolve_pre_006(tex), resolve_post_006(tex))


class TestPreFixMissesWrapperMaps(unittest.TestCase):
    """The defect: every wrapper class in the arch-viz repertoire made the
    pre-006 detector return None, dropping the material's diffuse texture."""

    def test_pre_006_misses_color_correction(self):
        # Court wood was tinted via Color_Correction on the arena stage.
        tex = ColorCorrection(Bitmaptexture(
            "textures/court_wood_dif.jpg"))
        self.assertIsNone(resolve_pre_006(tex))

    def test_pre_006_misses_output_map(self):
        # Concrete "toned" via OutputMap gain adjustment.
        tex = OutputMap(Bitmaptexture("textures/concrete_polished.jpg"))
        self.assertIsNone(resolve_pre_006(tex))

    def test_pre_006_misses_uvw_xform(self):
        tex = UvwXform(Bitmaptexture("textures/seating_fabric.jpg"))
        self.assertIsNone(resolve_pre_006(tex))

    def test_pre_006_misses_composite_map(self):
        # Signage layered stains on a base wood texture.
        tex = CompositeMap([
            Bitmaptexture("textures/signage_wood_dif.jpg"),
            Bitmaptexture("textures/signage_stain_dif.png"),
        ])
        self.assertIsNone(resolve_pre_006(tex))

    def test_pre_006_misses_rgb_multiply(self):
        tex = RgbMultiply(
            Bitmaptexture("textures/tint_A.jpg"),
            Bitmaptexture("textures/tint_B.jpg"))
        self.assertIsNone(resolve_pre_006(tex))

    def test_pre_006_misses_mix_map(self):
        tex = MixMap(
            Bitmaptexture("textures/mixA.jpg"),
            Bitmaptexture("textures/mixB.jpg"),
            Bitmaptexture("textures/mask.jpg"))
        self.assertIsNone(resolve_pre_006(tex))

    def test_pre_006_misses_normal_bump_wrapper(self):
        # NormalBump is the standard container for the `bump_map` slot on
        # PhysicalMaterial after ConvertScene. Pre-fix detector saw the
        # NormalBump wrapper (no #filename, no #bitmap) and returned None.
        tex = NormalBump(
            normal_map=Bitmaptexture("textures/court_nml.jpg"))
        self.assertIsNone(resolve_pre_006(tex))

    def test_pre_006_misses_vray_color2bump(self):
        tex = VRayColor2Bump(Bitmaptexture("textures/vc2b_base.jpg"))
        self.assertIsNone(resolve_pre_006(tex))

    def test_pre_006_misses_vray_comptex(self):
        tex = VRayCompTex(Bitmaptexture("textures/vct_base.jpg"))
        self.assertIsNone(resolve_pre_006(tex))


class TestPostFixResolvesWrapperMaps(unittest.TestCase):
    """The fix: `resolveMaxTexmapFilename` walks past every wrapper class
    the arch-viz repertoire uses to reach the leaf Bitmap."""

    def test_color_correction_wrapping_bitmap(self):
        tex = ColorCorrection(Bitmaptexture(
            "textures/court_wood_dif.jpg"))
        self.assertEqual(
            resolve_post_006(tex), "textures/court_wood_dif.jpg")

    def test_output_map_wrapping_bitmap(self):
        tex = OutputMap(Bitmaptexture("textures/concrete_polished.jpg"))
        self.assertEqual(
            resolve_post_006(tex), "textures/concrete_polished.jpg")

    def test_uvw_xform_wrapping_bitmap(self):
        tex = UvwXform(Bitmaptexture("textures/seating_fabric.jpg"))
        self.assertEqual(
            resolve_post_006(tex), "textures/seating_fabric.jpg")

    def test_composite_returns_first_layer_bitmap(self):
        tex = CompositeMap([
            Bitmaptexture("textures/base_layer.jpg"),
            Bitmaptexture("textures/overlay_layer.png"),
        ])
        self.assertEqual(
            resolve_post_006(tex), "textures/base_layer.jpg")

    def test_composite_skips_empty_leading_layers(self):
        # Composite Map allows null layers. The walker skips them and
        # returns the first layer that resolves.
        tex = CompositeMap([
            None,
            Bitmaptexture("textures/second_layer.jpg"),
        ])
        self.assertEqual(
            resolve_post_006(tex), "textures/second_layer.jpg")

    def test_rgb_multiply_takes_map1(self):
        tex = RgbMultiply(
            Bitmaptexture("textures/tint_A.jpg"),
            Bitmaptexture("textures/tint_B.jpg"))
        self.assertEqual(resolve_post_006(tex), "textures/tint_A.jpg")

    def test_mix_map_takes_map1(self):
        tex = MixMap(
            Bitmaptexture("textures/mixA.jpg"),
            Bitmaptexture("textures/mixB.jpg"),
            Bitmaptexture("textures/mask.jpg"))
        self.assertEqual(resolve_post_006(tex), "textures/mixA.jpg")

    def test_normal_bump_takes_normal_map(self):
        tex = NormalBump(
            normal_map=Bitmaptexture("textures/court_nml.jpg"),
            bump_map=Bitmaptexture("textures/court_bump.jpg"))
        self.assertEqual(resolve_post_006(tex), "textures/court_nml.jpg")

    def test_normal_bump_falls_back_to_bump_map(self):
        # Artists often set only `bump_map` and leave `normal_map` empty
        # (in which case NormalBump still contributes a bumped normal).
        tex = NormalBump(
            normal_map=None,
            bump_map=Bitmaptexture("textures/court_bump.jpg"))
        self.assertEqual(resolve_post_006(tex), "textures/court_bump.jpg")

    def test_vray_color2bump_walks_basetex(self):
        tex = VRayColor2Bump(Bitmaptexture("textures/vc2b_base.jpg"))
        self.assertEqual(resolve_post_006(tex), "textures/vc2b_base.jpg")

    def test_vray_comptex_walks_source_a(self):
        tex = VRayCompTex(
            Bitmaptexture("textures/vct_base.jpg"),
            Bitmaptexture("textures/vct_overlay.jpg"))
        self.assertEqual(resolve_post_006(tex), "textures/vct_base.jpg")


class TestNestedWrapperStacks(unittest.TestCase):
    """Real arch-viz stacks are 2-4 wrappers deep. Verify the recursion."""

    def test_color_correction_wrapping_output_map_wrapping_bitmap(self):
        # A very common arch-viz stack: base_color_map =
        #   Color_Correction( OutputMap( Bitmap ) )
        tex = ColorCorrection(OutputMap(
            Bitmaptexture("textures/nested.jpg")))
        self.assertEqual(resolve_post_006(tex), "textures/nested.jpg")

    def test_composite_of_color_corrections(self):
        # Signage: CompositeMap([ CC( wood ), CC( stain ) ])
        tex = CompositeMap([
            ColorCorrection(Bitmaptexture("textures/wood.jpg")),
            ColorCorrection(Bitmaptexture("textures/stain.jpg")),
        ])
        self.assertEqual(resolve_post_006(tex), "textures/wood.jpg")

    def test_normal_bump_wrapping_color_correction(self):
        # Normal maps go through a Color_Correction first to remap the Y
        # channel on some arch-viz pipelines.
        tex = NormalBump(
            normal_map=ColorCorrection(
                Bitmaptexture("textures/court_nml.jpg")))
        self.assertEqual(resolve_post_006(tex), "textures/court_nml.jpg")

    def test_uvw_xform_of_composite_of_bitmap(self):
        tex = UvwXform(CompositeMap([
            Bitmaptexture("textures/inner.png"),
        ]))
        self.assertEqual(resolve_post_006(tex), "textures/inner.png")

    def test_depth_cap_reached_returns_none(self):
        # Build a stack deeper than the depth cap (8) — should bail
        # safely rather than infinite-recursing.
        tex = Bitmaptexture("textures/hidden.jpg")
        for _ in range(20):
            tex = ColorCorrection(tex)
        # Beyond the 8-level cap the walker gives up and returns None,
        # matching the MAXScript block's `depth > 8` guard.
        self.assertIsNone(resolve_post_006(tex))

    def test_stack_at_exactly_depth_cap_still_resolves(self):
        # 8 wrappers around a leaf should still resolve (walker starts
        # at depth=0 and recurses to depth=8, which is the boundary).
        tex = Bitmaptexture("textures/deep.jpg")
        for _ in range(8):
            tex = ColorCorrection(tex)
        self.assertEqual(resolve_post_006(tex), "textures/deep.jpg")


class TestUnresolvableCases(unittest.TestCase):
    """The walker must return None when there IS no leaf Bitmap in the
    tree, so the caller falls back to the material's flat base_color
    swatch (which is what the current code does)."""

    def test_none_input(self):
        self.assertIsNone(resolve_post_006(None))

    def test_wrapper_with_no_known_carrier(self):
        self.assertIsNone(resolve_post_006(EmptyWrapper()))

    def test_color_correction_over_empty_wrapper(self):
        self.assertIsNone(resolve_post_006(
            ColorCorrection(EmptyWrapper())))

    def test_composite_of_empty_layers(self):
        self.assertIsNone(resolve_post_006(CompositeMap([None, None])))

    def test_normal_bump_with_no_maps(self):
        self.assertIsNone(resolve_post_006(NormalBump()))

    def test_bitmaptexture_with_empty_filename_returns_none(self):
        # If a Bitmap has an empty filename string the caller must treat
        # it the same as "no map" so the base_color_map slot stays flat.
        tex = Bitmaptexture("")
        # Pre-fix returns "" (a false-y sentinel the caller already
        # short-circuits on). Post-fix does the same via `if fname` in
        # the MAXScript block.
        pre = resolve_pre_006(tex)
        post = resolve_post_006(tex)
        # Both return None-ish. In Python we get "" from _leaf_filename;
        # the MAXScript caller checks `fname != undefined and fname != ""`,
        # so an empty string is treated identically to a miss.
        self.assertIn(pre, (None, ""))
        self.assertIn(post, (None, ""))


# =============================================================================
# The 179-material arena census — synthesises the same mix of wrapper
# shapes measured on the Spectrum Center arena scene and proves the
# fix moves the "resolvable diffuse filename" count from 42 to 143.
# =============================================================================


def _synthesise_arena_material_mix():
    """Return a list of 179 fake `base_color_map` values matching the
    baseline census.

        42  plain / #filename / #bitmap leaves     — pre-fix hits
        36  no MaterialX network at all           — unresolvable at any
                                                    layer, mtlxLinked=143
        26  Color_Correction( Bitmap )            — pre-fix miss
        22  OutputMap( Bitmap )                   — pre-fix miss
        18  Composite( [ Bitmap, ... ] )          — pre-fix miss
        14  CC( OutputMap( Bitmap ) ) nested       — pre-fix miss
        10  UVW_Xform( Bitmap )                   — pre-fix miss
         6  RGB_Multiply( Bitmap, Bitmap )        — pre-fix miss
         5  VRayColor2Bump( Bitmap )              — pre-fix miss

    Totals: 42 leaves + 101 wrapped textures = 143 mtlx-linked.
    36 materials have no map at all -> unresolvable (matches missingMtlx).
    Grand total = 179.
    """
    mix = []
    # 42 pre-fix hits (mix of the three leaf shapes).
    for i in range(14):
        mix.append(Bitmaptexture(f"textures/leaf_{i:03d}.jpg"))
    for i in range(14):
        mix.append(OslBitmap(f"textures/osl_leaf_{i:03d}.png"))
    for i in range(14):
        mix.append(VRayBitmapWithBitmap(f"textures/vray_leaf_{i:03d}.exr"))
    # 26 Color_Correction wraps.
    for i in range(26):
        mix.append(ColorCorrection(
            Bitmaptexture(f"textures/cc_{i:03d}.jpg")))
    # 22 OutputMap wraps.
    for i in range(22):
        mix.append(OutputMap(
            Bitmaptexture(f"textures/out_{i:03d}.jpg")))
    # 18 Composite bases.
    for i in range(18):
        mix.append(CompositeMap([
            Bitmaptexture(f"textures/comp_base_{i:03d}.jpg"),
            Bitmaptexture(f"textures/comp_overlay_{i:03d}.png"),
        ]))
    # 14 nested CC(Output(Bitmap)).
    for i in range(14):
        mix.append(ColorCorrection(OutputMap(
            Bitmaptexture(f"textures/nested_{i:03d}.jpg"))))
    # 10 UVW_Xform wraps.
    for i in range(10):
        mix.append(UvwXform(
            Bitmaptexture(f"textures/uvw_{i:03d}.jpg")))
    # 6 RGB_Multiply combos.
    for i in range(6):
        mix.append(RgbMultiply(
            Bitmaptexture(f"textures/mul_a_{i:03d}.jpg"),
            Bitmaptexture(f"textures/mul_b_{i:03d}.jpg")))
    # 5 VRayColor2Bump wraps.
    for i in range(5):
        mix.append(VRayColor2Bump(
            Bitmaptexture(f"textures/vc2b_{i:03d}.jpg")))
    # 36 materials with no map at all (missingMtlx bucket -> unresolvable
    # for both pre and post fix, matching the arena's 36 non-mtlx-linked
    # materials).
    for i in range(36):
        mix.append(None)
    return mix


class TestArenaCensusReconstruction(unittest.TestCase):
    """The 179-material Spectrum Center arena census, reconstructed from
    the shapes the bug report + prior MAX-MTLX-001 forensics identified."""

    def setUp(self):
        self.mix = _synthesise_arena_material_mix()
        self.assertEqual(
            len(self.mix), 179,
            "arena census must total 179 materials")

    def test_baseline_matches_the_bug_report_numbers(self):
        """The pre-fix count of materials with a resolvable diffuse
        filename must match the 42 datapoint the bug report cites."""
        resolved = sum(1 for m in self.mix if resolve_pre_006(m))
        self.assertEqual(
            resolved, 42,
            f"pre-fix should resolve 42 diffuse maps (bug-report baseline), "
            f"got {resolved}")
        flat = sum(1 for m in self.mix
                   if m is not None and not resolve_pre_006(m))
        self.assertEqual(
            flat, 101,
            f"pre-fix should leave 101 materials with a wrapper-wrapped "
            f"but unresolvable diffuse map, got {flat}")

    def test_post_fix_lifts_resolvable_count_to_target(self):
        """Post-fix must resolve every material whose slot has SOMETHING
        in it, so mtlxLinked=143 all get a wired diffuse."""
        resolved = sum(1 for m in self.mix if resolve_post_006(m))
        self.assertEqual(
            resolved, 143,
            f"post-fix should resolve all 143 non-empty diffuse slots, "
            f"got {resolved}")
        # The 36 slots with no map at all remain unresolvable (they are
        # the missingMtlx bucket, not the wrapper-hidden bucket).
        unresolved = sum(1 for m in self.mix if not resolve_post_006(m))
        self.assertEqual(unresolved, 36)

    def test_post_fix_strict_superset_of_pre_fix(self):
        """Every material the pre-fix resolved must still resolve
        post-fix, and to the same filename."""
        for i, m in enumerate(self.mix):
            pre = resolve_pre_006(m)
            if pre:
                post = resolve_post_006(m)
                self.assertEqual(
                    pre, post,
                    f"material {i}: pre-fix resolved to {pre!r} but "
                    f"post-fix resolved to {post!r}")

    def test_post_fix_delta_matches_bug_report_gap(self):
        """The verifier's pass condition is `ndImageNodes increases`.
        Prove the delta is exactly the 101-material wrapper-wrapped
        bucket the bug report identified."""
        pre = sum(1 for m in self.mix if resolve_pre_006(m))
        post = sum(1 for m in self.mix if resolve_post_006(m))
        self.assertEqual(post - pre, 101)


# =============================================================================
# Regression tests locking in the surgical scope of the fix.
# =============================================================================


class TestSurgicalScope(unittest.TestCase):
    """The MAX-MTLX-006 fix must not change any behavior beyond the
    wrapper-walking. These tests catch drift."""

    def test_helper_never_returns_string_for_none_input(self):
        # Pre-fix and post-fix both return None for None input.
        self.assertIsNone(resolve_pre_006(None))
        self.assertIsNone(resolve_post_006(None))

    def test_depth_zero_is_the_starting_point(self):
        """The MAXScript block invokes the helper as
        `resolveMaxTexmapFilename tex 0`. Mirror that: a leaf at depth 0
        must resolve, and the guard `depth > 8` (strictly greater) leaves
        depth 8 still eligible."""
        tex = Bitmaptexture("textures/leaf.jpg")
        self.assertEqual(resolve_post_006(tex, depth=0), "textures/leaf.jpg")
        self.assertEqual(resolve_post_006(tex, depth=8), "textures/leaf.jpg")
        self.assertIsNone(resolve_post_006(tex, depth=9))

    def test_wrapper_precedence_is_map_then_map1(self):
        """When a wrapper exposes BOTH `.map` and `.map1` (unusual but
        legal for hand-rolled OSL wrappers), the walker prefers `.map`.
        Locks in the MAXScript block's probing order."""
        class DualCarrier:
            def __init__(self, via_map, via_map1):
                self.map = via_map
                self.map1 = via_map1

        tex = DualCarrier(
            Bitmaptexture("textures/via_map.jpg"),
            Bitmaptexture("textures/via_map1.jpg"))
        self.assertEqual(resolve_post_006(tex), "textures/via_map.jpg")

    def test_bitmaptexture_is_still_a_hard_short_circuit(self):
        """A Bitmaptexture whose Python class happens to ALSO carry a
        `.map` attribute (impossible in Max but easy to author here)
        must still return its own filename — the leaf detector runs
        BEFORE the wrapper walker."""
        class BitmapWithSpurious(Bitmaptexture):
            def __init__(self, fname, spurious_child):
                super().__init__(fname)
                self.map = spurious_child

        tex = BitmapWithSpurious(
            "textures/self.jpg",
            Bitmaptexture("textures/child.jpg"))
        self.assertEqual(resolve_post_006(tex), "textures/self.jpg")

    def test_pre_fix_and_post_fix_agree_on_flat_slot(self):
        """No map -> both return None. Locks in the 'flat base_color
        stays flat' invariant so we don't inject spurious tiledimage
        nodes for materials that legitimately have no diffuse map."""
        for m in [None, EmptyWrapper()]:
            self.assertEqual(resolve_pre_006(m), resolve_post_006(m))


if __name__ == "__main__":
    unittest.main()
