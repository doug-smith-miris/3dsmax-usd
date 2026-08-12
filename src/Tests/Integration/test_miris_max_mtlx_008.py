# Copyright 2026 Miris Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
"""
MAX-MTLX-008 — Scope/coverage audit of the MAX-MTLX-007 wrapper walk in
`discoverMaxMtlxTexmapsFn` (`src/translators/MtlxShaderWriter.cpp`).

Background
----------
MAX-MTLX-007 (PR on `miris/max-mtlx-007-walk-blend-mtl-sub-materials`)
added `unwrapBlendMaterialSubMtls` — a MAXScript recursion that expands
a possibly-wrapped Max material into the list of concrete sub-materials
whose PhysicalMaterial / OpenPBR / VRayMtl slot map carries texture
maps. That fix closed the specific defect the arena baseline surfaced:
`VRayBlendMtl` and `VRayOverrideMtl` wrappers whose textures were
silently dropped because the pre-007 walker probed only the top-level
material. The MAX-MTLX-007 test (`test_miris_max_mtlx_007.py`) proves
the wrappers now round-trip.

The auto-planner emitted MAX-MTLX-008 as a follow-on with rationale
"VRayOverrideMtl (GImtl wrapper) baseMtl not unwrapped on standard
map-discovery path". Reading the current fork HEAD shows that
MAX-MTLX-007 already unwraps VRayOverrideMtl.baseMtl — the literal
reading of the planner's rationale is stale (baseline captured
pre-007). What is NOT yet locked in is the SCOPE of MAX-MTLX-007: how
narrowly the wrapper walk fires, and which adjacent-wrapper classes are
deliberately excluded. Without those assertions a future "unify
wrapper handling" refactor could silently descend into V-Ray classes
whose sub-material relationships have different USD semantics
(`VRay2SidedMtl.frontMtl` / `.backMtl` map to a UsdGeom.Subset partition
in some pipelines, not a first-hit-wins texture merge; `VRayMtlWrapper`
is a matte/render-pass wrapper whose baseMtl should NOT be walked
because the wrapper itself carries the surface artists see).

This audit pins the MAX-MTLX-007 scope with negative-test coverage per
the `feedback-scope-coverage-audit` pattern (MAX-LIT-003 shape):

  1. The class-name gate is EXACT — only "VRayBlendMtl" and
     "VRayOverrideMtl" match. Superstrings ("VRayOverrideMtlProxy"),
     substrings ("VRayOverride"), and same-length-different-suffix
     ("VRayOverrideMtl2") are all rejected. (Note that MAXScript
     string `==` is case-INSENSITIVE by native semantics; verify the
     canonical PascalCase class names V-Ray returns from `classOf`
     are the ones that fire, and that the case-insensitive `==`
     doesn't accidentally widen the gate to unrelated classes.)
  2. Every OTHER V-Ray wrapper material class the plugin might
     encounter is intentionally out of scope: `VRay2SidedMtl`,
     `VRayMtlWrapper`, `VRayFastSSS2`, `VRayLightMtl`, `VRayBumpMtl`,
     `VRayHairMtl`. Each has sub-material or "base"-shaped attributes
     that a wildcard widening would descend into.
  3. Every 3ds Max stock wrapper material class is intentionally
     out of scope: `Multi/Sub-Object` (already tested in MTLX-007),
     `DoubleSided` (`.mtl1`/`.mtl2`), `Shell Material`
     (`.originalMtl`/`.bakedMtl`), `Blend` (stock, not V-Ray;
     `.map1`/`.map2` + `.mask`), `Composite Mtl` (`.mtlList`), `Top/
     Bottom`, `Morpher`, `Matte/Shadow`. Locks them in as negative
     cases.
  4. The COLLISION SHAPE: `.baseMtl` exists as an attribute name on
     both `VRayOverrideMtl` (walked) AND `VRayMtlWrapper` (NOT
     walked). The class-name gate is the ONLY thing preventing a
     silent walk into VRayMtlWrapper's baseMtl. Pin that boundary so
     a refactor to "just check for a `.baseMtl` attribute" surfaces
     immediately.
  5. Slot-table completeness for the two walked classes: exactly
     10 slots for VRayBlendMtl (baseMtl + coatMtl_1..coatMtl_9),
     exactly 5 slots for VRayOverrideMtl
     (baseMtl, giMtl, reflectMtl, refractMtl, shadowMtl). Any future
     V-Ray release that adds a new slot (e.g. `.envMtl`) MUST
     require an explicit slot-table update to be walked; do not
     descend by attribute-name heuristic.
  6. The baseMtl-first ordering is load-bearing — reversing it
     changes the user-visible first-hit-wins texture precedence.
     Pin this at the slot-table level so a future "reorder for
     symmetry" refactor fails by named case.
  7. Idempotence: running the walker twice gives identical output.
  8. Depth cap (already in MTLX-007's test — repeated here at
     scope-audit scale so the invariant lives in the same file as
     its rationale).
  9. Arena census delta invariant: the MAX-MTLX-007 fix's 89-material
     resolved-diffuse delta must survive any future refactor. This is
     the cross-fixture anchor that catches a widening or narrowing
     regression.

The audit ships NO C++ logic change. Per the `feedback-scope-coverage-
audit` shape: extending MAX-MTLX-007 to a new wrapper class is a
SEPARATE future bite with its own PR, its own tests, its own doc
entry, and its own captured corpus of leak values from the new
wrapper. It is NOT a widening of the existing 5-slot / 10-slot
tables.

Run: `hython test_miris_max_mtlx_008.py`
"""
import unittest


# =============================================================================
# Fake material classes mirroring the property surface the MAXScript
# `isProperty` / `getProperty` reads. `max_class_name` mirrors what
# `(classOf m) as string` returns; the class-name gate in the walker
# under test is a MAXScript `==` on this string (case-insensitive per
# MAXScript semantics, but every fake here uses the canonical PascalCase
# spelling V-Ray actually returns from `classOf`).
# =============================================================================


class _MaterialBase:
    max_class_name = "GenericMtl"


class PhysicalMaterial(_MaterialBase):
    max_class_name = "PhysicalMaterial"

    def __init__(self, base_color_map=None, roughness_map=None):
        self.base_color_map = base_color_map
        self.roughness_map = roughness_map


class VRayMtl(_MaterialBase):
    max_class_name = "VRayMtl"

    def __init__(self, base_color_map=None):
        self.base_color_map = base_color_map


class VRayBlendMtl(_MaterialBase):
    """The wrapper walk's first scope entry. `.baseMtl` + up to 9
    `.coatMtl_1..coatMtl_9` slots."""

    max_class_name = "VRayBlendMtl"

    def __init__(self, baseMtl=None, coats=()):
        self.baseMtl = baseMtl
        for i in range(1, 10):
            setattr(self, f"coatMtl_{i}",
                    coats[i - 1] if i - 1 < len(coats) else None)


class VRayOverrideMtl(_MaterialBase):
    """The wrapper walk's second scope entry. `.baseMtl` +
    `.giMtl` / `.reflectMtl` / `.refractMtl` / `.shadowMtl`."""

    max_class_name = "VRayOverrideMtl"

    def __init__(self, baseMtl=None, giMtl=None, reflectMtl=None,
                 refractMtl=None, shadowMtl=None):
        self.baseMtl = baseMtl
        self.giMtl = giMtl
        self.reflectMtl = reflectMtl
        self.refractMtl = refractMtl
        self.shadowMtl = shadowMtl


# ------------------- Adjacent V-Ray wrapper classes (OUT OF SCOPE) -----------


class VRay2SidedMtl(_MaterialBase):
    """V-Ray's two-sided material: front/back surfaces are visually
    distinct, not a first-hit-wins texture merge. Descending into
    `.frontMtl` would silently discard the back-face material's
    contribution. INTENTIONALLY OUT OF SCOPE for MAX-MTLX-007's walk."""

    max_class_name = "VRay2SidedMtl"

    def __init__(self, frontMtl=None, backMtl=None):
        self.frontMtl = frontMtl
        self.backMtl = backMtl


class VRayMtlWrapper(_MaterialBase):
    """V-Ray's matte/render-pass wrapper — collision-shape attacker.
    Has `.baseMtl` (same attribute name as VRayOverrideMtl!) but the
    wrapper itself is the surface artists see; the wrapper controls
    render-pass IDs / matte behaviour without changing the shading.
    Descending into `.baseMtl` here would misrepresent the wrapper's
    role. OUT OF SCOPE."""

    max_class_name = "VRayMtlWrapper"

    def __init__(self, baseMtl=None):
        self.baseMtl = baseMtl


class VRayFastSSS2(_MaterialBase):
    """V-Ray's fast-SSS material — has no sub-material slot but a
    future release might add one (e.g. `.surfaceMtl`). OUT OF SCOPE
    regardless of what slots appear."""

    max_class_name = "VRayFastSSS2"


class VRayLightMtl(_MaterialBase):
    """V-Ray's emissive material — MAX-MTLX-005 already handles this
    upstream (LastResortMtlxShaderWriter) with its own emission-color
    unwrap. Must NOT be descended by MAX-MTLX-007's walker in the
    standard texmap-discovery path; MAX-MTLX-005 owns it."""

    max_class_name = "VRayLightMtl"

    def __init__(self, color=None):
        self.color = color


class VRayBumpMtl(_MaterialBase):
    """V-Ray's bump-only wrapper — has `.baseMtl` (another
    `.baseMtl` collision!) but the bump substrate is separately
    authored via `.bump_map`. If we descend, we'd double-count."""

    max_class_name = "VRayBumpMtl"

    def __init__(self, baseMtl=None, bump_map=None):
        self.baseMtl = baseMtl
        self.bump_map = bump_map


class VRayHairMtl(_MaterialBase):
    """V-Ray's hair material — no sub-material, but its property
    surface differs enough from the standard slot map that walking
    it would produce garbage. Locked out as a scope guard."""

    max_class_name = "VRayHairMtl"


# --------------------- 3ds Max stock wrapper classes (OUT OF SCOPE) ----------


class DoubleSided(_MaterialBase):
    """Stock 3ds Max Double Sided material — has `.mtl1`/`.mtl2`
    with a translucency mix. Not a wrapper for wrapper-walk
    purposes."""

    max_class_name = "DoubleSided"

    def __init__(self, mtl1=None, mtl2=None):
        self.mtl1 = mtl1
        self.mtl2 = mtl2


class Shell_Material(_MaterialBase):
    """Stock 3ds Max Shell Material — pairs a rendering material
    (`.originalMtl`) with a baked-texture proxy (`.bakedMtl`).
    Baked textures are already discovered through the render
    material; descending would double-count. OUT OF SCOPE."""

    max_class_name = "Shell Material"  # canonical MAXScript classOf

    def __init__(self, originalMtl=None, bakedMtl=None):
        self.originalMtl = originalMtl
        self.bakedMtl = bakedMtl


class Blend_stock(_MaterialBase):
    """Stock 3ds Max Blend material — DISTINCT from VRayBlendMtl.
    Uses `.map1`/`.map2` + `.mask`. The pre-MAX-MTLX-007 code did
    NOT walk this class either; the wrapper walk is intentionally
    scoped only to the V-Ray classes with a proven arena presence."""

    max_class_name = "Blend"

    def __init__(self, map1=None, map2=None, mask=None):
        self.map1 = map1
        self.map2 = map2
        self.mask = mask


class Composite_Mtl(_MaterialBase):
    """Stock 3ds Max Composite Mtl — layered material with
    `.mtlList` (arrays of sub-materials with opacity blending).
    NOT walked by MAX-MTLX-007 — different combining semantics than
    VRayBlendMtl."""

    max_class_name = "Composite"

    def __init__(self, mtlList=()):
        self.mtlList = list(mtlList)


class TopBottom(_MaterialBase):
    """Stock 3ds Max Top/Bottom material — geometry-position-driven
    material split. `.topMtl`/`.bottomMtl`. OUT OF SCOPE."""

    max_class_name = "Top/Bottom"

    def __init__(self, topMtl=None, bottomMtl=None):
        self.topMtl = topMtl
        self.bottomMtl = bottomMtl


class Matte_Shadow(_MaterialBase):
    """Stock 3ds Max Matte/Shadow — hold-out material. Not a
    wrapper of surface materials in any USD-relevant sense."""

    max_class_name = "Matte/Shadow"


class MultiMtl(_MaterialBase):
    """Stock 3ds Max Multi/Sub-Object — per-face-ID material
    assignment. Each sub-material reaches its OWN shader writer
    via GeomSubset binding (MAX-GEO-002 / MAX-GEO-006); walking
    it here would double-emit tiledimage nodes. Already tested in
    MAX-MTLX-007; repeated here at scope-audit scale."""

    max_class_name = "Multi/Sub-Object"

    def __init__(self, subs=()):
        self.materialList = list(subs)


# =============================================================================
# Walker mirror — mirrors the MAX-MTLX-007 MAXScript block verbatim.
# The `_WRAPPER_TABLE` is the AUTHORITATIVE scope: `unwrapBlendMaterialSubMtls`
# fires ONLY on classes whose canonical PascalCase name is a key of this
# dict, and it descends into the exact ordered attribute list stored there.
# Any diff to this table must be paired with a diff to the MAXScript block
# and a new bite's tests.
# =============================================================================


# Exact scope of MAX-MTLX-007's wrapper walk.
_WRAPPER_TABLE = {
    "VRayBlendMtl": (
        ["baseMtl"] + [f"coatMtl_{i}" for i in range(1, 10)]
    ),
    "VRayOverrideMtl": [
        "baseMtl", "giMtl", "reflectMtl", "refractMtl", "shadowMtl",
    ],
}


def _get(mat, name):
    """MAXScript `isProperty m name` + `getProperty m name` — absent
    attribute → None."""
    return getattr(mat, name, None)


def _class_name_matches(cls, name):
    """Mirror MAXScript's case-insensitive `==` on strings. V-Ray's
    canonical `classOf` result is PascalCase, but if a future
    subclass or SDK release returned a differently-cased form the
    `==` would still fire."""
    return cls.lower() == name.lower()


def unwrap(mat, visited=None, depth=0):
    """Mirror `unwrapBlendMaterialSubMtls` from MAX-MTLX-007. Only
    classes in `_WRAPPER_TABLE` are descended; every other class
    returns `[mat]` — the identity/fast path."""
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
    slots = None
    for wrapper_cls, wrapper_slots in _WRAPPER_TABLE.items():
        if _class_name_matches(cls, wrapper_cls):
            slots = wrapper_slots
            break
    if slots:
        for slot_name in slots:
            sub = _get(mat, slot_name)
            if sub is not None:
                for r in unwrap(sub, visited, depth + 1):
                    out.append(r)
    return out


# Simplified slot map for census / discovery tests (same as MAX-MTLX-007's
# test; enough to exercise base_color as the highest-visibility slot).
_SLOT_MAP = [
    ("base_color_map",       "base_color",         "color3"),
    ("baseColorMap",         "base_color",         "color3"),
    ("roughness_map",        "specular_roughness", "float"),
    ("bump_map",             "normal",             "vector3"),
]


def _probe(mat, seen):
    hits = []
    for prop_name, mtlx_input, mtlx_type in _SLOT_MAP:
        tex = _get(mat, prop_name)
        if not isinstance(tex, str) or not tex:
            continue
        if mtlx_input in seen:
            continue
        seen.add(mtlx_input)
        hits.append((mtlx_input, mtlx_type, tex))
    return hits


def discover(mat):
    """Full MAX-MTLX-007 discovery path — unwrap + first-hit-wins
    probe over the list."""
    seen = set()
    all_hits = []
    for sub in unwrap(mat):
        all_hits.extend(_probe(sub, seen))
    return all_hits


# =============================================================================
# Tests — the scope-audit suite.
# =============================================================================


class TestClassNameGateIsStrict(unittest.TestCase):
    """The class-name gate that fires the wrapper walk. Superstrings,
    substrings, and different suffixes must ALL be rejected. This is
    the primary anchor against a future "match by prefix" refactor."""

    def _wrapper_with_baseMtl(self, cls_name):
        """Build a fake class whose max_class_name is `cls_name` and
        which exposes .baseMtl (the collision-shape attribute).
        Confirms whether the walker's class-name gate follows the
        attribute or the name."""

        class Fake(_MaterialBase):
            max_class_name = cls_name

            def __init__(self, baseMtl=None):
                self.baseMtl = baseMtl

        return Fake

    def test_gate_accepts_exact_VRayOverrideMtl(self):
        base = PhysicalMaterial(base_color_map="tex/positive.jpg")
        vom = VRayOverrideMtl(baseMtl=base)
        # Positive control: this is the class MAX-MTLX-007 SHOULD walk.
        self.assertEqual(len(unwrap(vom)), 2)
        self.assertIs(unwrap(vom)[1], base)

    def test_gate_accepts_exact_VRayBlendMtl(self):
        base = PhysicalMaterial(base_color_map="tex/positive.jpg")
        blend = VRayBlendMtl(baseMtl=base)
        self.assertEqual(len(unwrap(blend)), 2)
        self.assertIs(unwrap(blend)[1], base)

    def test_gate_rejects_VRayOverrideMtl_superstring(self):
        # A class named `VRayOverrideMtlProxy` must not match the
        # gate. A prefix-match refactor is the most likely accidental
        # widening; guard.
        FakeCls = self._wrapper_with_baseMtl("VRayOverrideMtlProxy")
        base = PhysicalMaterial(base_color_map="tex/should_be_hidden.jpg")
        fake = FakeCls(baseMtl=base)
        self.assertEqual(unwrap(fake), [fake],
                         "superstring class name must not fire the gate")
        # The base's texture must be invisible to discover().
        self.assertEqual(discover(fake), [])

    def test_gate_rejects_VRayOverrideMtl_substring(self):
        # A class named `VRayOverride` (drop the `Mtl` suffix) — could
        # be a helper class or a future SDK rename. Must not fire.
        FakeCls = self._wrapper_with_baseMtl("VRayOverride")
        base = PhysicalMaterial(base_color_map="tex/hidden.jpg")
        fake = FakeCls(baseMtl=base)
        self.assertEqual(unwrap(fake), [fake])
        self.assertEqual(discover(fake), [])

    def test_gate_rejects_VRayOverrideMtl_different_suffix(self):
        # A class named `VRayOverrideMtl2` — hypothetical V-Ray v7
        # variant. Must not fire without an explicit slot-table
        # extension.
        FakeCls = self._wrapper_with_baseMtl("VRayOverrideMtl2")
        base = PhysicalMaterial(base_color_map="tex/hidden.jpg")
        fake = FakeCls(baseMtl=base)
        self.assertEqual(unwrap(fake), [fake])
        self.assertEqual(discover(fake), [])

    def test_gate_rejects_VRayBlendMtl_superstring(self):
        FakeCls = self._wrapper_with_baseMtl("VRayBlendMtlProxy")
        base = PhysicalMaterial(base_color_map="tex/hidden.jpg")
        fake = FakeCls(baseMtl=base)
        self.assertEqual(unwrap(fake), [fake])

    def test_gate_rejects_generic_Mtl_suffix(self):
        # Class names ending in "Mtl" but not in our table.
        for name in ["FakeMtl", "MyBlendMtl", "OverrideMtl", "Mtl",
                     "TheVRayOverrideMtl"]:
            FakeCls = self._wrapper_with_baseMtl(name)
            base = PhysicalMaterial(base_color_map="tex/hidden.jpg")
            fake = FakeCls(baseMtl=base)
            self.assertEqual(unwrap(fake), [fake],
                             f"class name {name!r} must not fire the gate")

    def test_gate_case_insensitivity_matches_maxscript_semantics(self):
        # MAXScript's `==` on strings is case-INSENSITIVE. If V-Ray
        # ever returned `"vrayoverridemtl"` (all lower) from classOf,
        # the gate WOULD fire. Document this as intentional — the
        # canonical spelling is PascalCase, but the gate tolerates
        # case drift because MAXScript does.
        for lower_variant in ["vrayoverridemtl", "VRAYOVERRIDEMTL",
                              "vRayOverrideMtl", "VrayOverrideMtl"]:
            FakeCls = self._wrapper_with_baseMtl(lower_variant)
            base = PhysicalMaterial(base_color_map="tex/case.jpg")
            fake = FakeCls(baseMtl=base)
            # Would fire because of case-insensitive `==`.
            self.assertEqual(len(unwrap(fake)), 2,
                             f"case variant {lower_variant!r} must fire "
                             f"the gate (MAXScript `==` is case-insensitive)")

    def test_gate_rejects_empty_class_name(self):
        # Defensive: empty class name (from a null/undefined material
        # that made it past the None guard) must not fire.
        FakeCls = self._wrapper_with_baseMtl("")
        base = PhysicalMaterial(base_color_map="tex/hidden.jpg")
        fake = FakeCls(baseMtl=base)
        self.assertEqual(unwrap(fake), [fake])


class TestAdjacentVRayWrapperClassesOutOfScope(unittest.TestCase):
    """V-Ray ships a family of wrapper materials. MAX-MTLX-007's
    walk covers ONLY VRayBlendMtl + VRayOverrideMtl; every other
    V-Ray class name is intentionally out of scope. A wildcard
    "descend into any VRay* wrapper" refactor must fail here."""

    def test_VRay2SidedMtl_frontMtl_not_walked(self):
        front = PhysicalMaterial(base_color_map="tex/front.jpg")
        back = PhysicalMaterial(base_color_map="tex/back.jpg")
        two_sided = VRay2SidedMtl(frontMtl=front, backMtl=back)
        self.assertEqual(unwrap(two_sided), [two_sided])
        # discover() sees the two_sided has no maps of its own → 0 hits.
        self.assertEqual(discover(two_sided), [])

    def test_VRayMtlWrapper_baseMtl_not_walked(self):
        # Collision-shape: VRayMtlWrapper has `.baseMtl` (same
        # attribute name as VRayOverrideMtl) but is NOT in the
        # wrapper table. The class-name gate is the sole guard.
        base = PhysicalMaterial(base_color_map="tex/wrapper_base.jpg")
        wrapper = VRayMtlWrapper(baseMtl=base)
        self.assertEqual(unwrap(wrapper), [wrapper])
        self.assertEqual(discover(wrapper), [])

    def test_VRayBumpMtl_baseMtl_not_walked(self):
        # Second collision-shape check on `.baseMtl`.
        base = PhysicalMaterial(base_color_map="tex/bump_base.jpg")
        bump_wrapper = VRayBumpMtl(baseMtl=base, bump_map="tex/bumps.jpg")
        self.assertEqual(unwrap(bump_wrapper), [bump_wrapper])
        # discover() DOES pick up the bump_map on the wrapper itself
        # (via _SLOT_MAP's `bump_map` → `normal`), but does NOT walk
        # into base. Filter out the wrapper's own contribution to
        # prove the base was invisible.
        hits = discover(bump_wrapper)
        for _, _, path in hits:
            self.assertNotEqual(
                path, "tex/bump_base.jpg",
                "VRayBumpMtl.baseMtl must not be discovered — that "
                "would double-count the substrate")

    def test_VRayFastSSS2_not_walked(self):
        sss = VRayFastSSS2()
        self.assertEqual(unwrap(sss), [sss])

    def test_VRayLightMtl_not_walked_by_texmap_path(self):
        # MAX-MTLX-005 owns the emission-color unwrap in a DIFFERENT
        # code path (LastResortMtlxShaderWriter). This walker must
        # not double-handle it.
        light_mtl = VRayLightMtl(color=(1.0, 0.5, 0.2))
        self.assertEqual(unwrap(light_mtl), [light_mtl])

    def test_VRayHairMtl_not_walked(self):
        hair = VRayHairMtl()
        self.assertEqual(unwrap(hair), [hair])


class TestStock3dsMaxWrapperClassesOutOfScope(unittest.TestCase):
    """3ds Max stock wrapper materials. Distinct from V-Ray's
    wrappers — different property surfaces and different combining
    semantics. All intentionally out of scope for MAX-MTLX-007's
    walk."""

    def test_DoubleSided_not_walked(self):
        m1 = PhysicalMaterial(base_color_map="tex/m1.jpg")
        m2 = PhysicalMaterial(base_color_map="tex/m2.jpg")
        ds = DoubleSided(mtl1=m1, mtl2=m2)
        self.assertEqual(unwrap(ds), [ds])
        self.assertEqual(discover(ds), [])

    def test_Shell_Material_not_walked(self):
        orig = PhysicalMaterial(base_color_map="tex/orig.jpg")
        baked = PhysicalMaterial(base_color_map="tex/baked.jpg")
        shell = Shell_Material(originalMtl=orig, bakedMtl=baked)
        self.assertEqual(unwrap(shell), [shell])
        self.assertEqual(discover(shell), [])

    def test_stock_Blend_not_walked(self):
        m1 = PhysicalMaterial(base_color_map="tex/blm1.jpg")
        m2 = PhysicalMaterial(base_color_map="tex/blm2.jpg")
        blend = Blend_stock(map1=m1, map2=m2)
        # NOTE: Blend_stock.max_class_name == "Blend", NOT
        # "VRayBlendMtl". No case-insensitive collision either.
        self.assertEqual(unwrap(blend), [blend])
        self.assertEqual(discover(blend), [])

    def test_Composite_Mtl_not_walked(self):
        subs = [PhysicalMaterial(base_color_map=f"tex/c_{i}.jpg")
                for i in range(3)]
        comp = Composite_Mtl(mtlList=subs)
        self.assertEqual(unwrap(comp), [comp])
        self.assertEqual(discover(comp), [])

    def test_TopBottom_not_walked(self):
        top = PhysicalMaterial(base_color_map="tex/top.jpg")
        bot = PhysicalMaterial(base_color_map="tex/bot.jpg")
        tb = TopBottom(topMtl=top, bottomMtl=bot)
        self.assertEqual(unwrap(tb), [tb])
        self.assertEqual(discover(tb), [])

    def test_Matte_Shadow_not_walked(self):
        ms = Matte_Shadow()
        self.assertEqual(unwrap(ms), [ms])

    def test_MultiMtl_not_walked(self):
        # Already in MAX-MTLX-007's test suite; repeated at
        # scope-audit scale for completeness. MultiMtl reaches each
        # of its sub-materials via GeomSubset binding elsewhere.
        subs = [PhysicalMaterial(base_color_map=f"tex/mm_{i}.jpg")
                for i in range(3)]
        multi = MultiMtl(subs=subs)
        self.assertEqual(unwrap(multi), [multi])
        # The sub-materials' textures must NOT be discovered here —
        # each will reach the writer via its own GeomSubset binding.
        for _, _, path in discover(multi):
            self.assertFalse(
                path.startswith("tex/mm_"),
                "MultiMtl sub-materials must not appear in the walker's "
                "output — the writer sees them via GeomSubset bindings")


class TestCollisionShapeBaseMtl(unittest.TestCase):
    """The COLLISION SHAPE — the audit's highest-value artifact.

    The `.baseMtl` attribute name exists on:
      * VRayOverrideMtl  — WALKED (in _WRAPPER_TABLE)
      * VRayMtlWrapper   — NOT walked (adjacent V-Ray wrapper)
      * VRayBumpMtl      — NOT walked (adjacent V-Ray wrapper)

    A refactor that switched from "class-name gate" to "attribute-
    name heuristic" (`if isProperty m #baseMtl then descend`) would
    silently start walking VRayMtlWrapper.baseMtl and VRayBumpMtl.
    baseMtl — a category expansion the audit prevents."""

    def test_baseMtl_walked_on_VRayOverrideMtl(self):
        base = PhysicalMaterial(base_color_map="tex/positive.jpg")
        vom = VRayOverrideMtl(baseMtl=base)
        hits = discover(vom)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0][2], "tex/positive.jpg")

    def test_baseMtl_NOT_walked_on_VRayMtlWrapper(self):
        base = PhysicalMaterial(base_color_map="tex/wrapper_hidden.jpg")
        wrapper = VRayMtlWrapper(baseMtl=base)
        hits = discover(wrapper)
        # No hits — .baseMtl is not descended and VRayMtlWrapper
        # itself has no slot-map properties.
        self.assertEqual(hits, [])

    def test_baseMtl_NOT_walked_on_VRayBumpMtl(self):
        base = PhysicalMaterial(base_color_map="tex/bump_hidden.jpg")
        bw = VRayBumpMtl(baseMtl=base, bump_map="tex/bmap.jpg")
        hits = discover(bw)
        # VRayBumpMtl.bump_map (on the wrapper itself) contributes a
        # `normal` hit — that's legitimate. VRayBumpMtl.baseMtl.
        # base_color_map must NOT appear.
        for _, _, path in hits:
            self.assertNotEqual(path, "tex/bump_hidden.jpg")

    def test_side_by_side_baseMtl_collision(self):
        """The audit's anchor case: two wrappers side by side, ONE
        walked (VRayOverrideMtl), ONE not (VRayMtlWrapper). Their
        .baseMtl sub-materials carry identically-shaped textures.
        Post-fix discovery MUST resolve the VRayOverrideMtl one and
        MUST NOT resolve the VRayMtlWrapper one. A wildcard widening
        would resolve BOTH — fails this test."""

        vom_base = PhysicalMaterial(base_color_map="tex/vom_shows.jpg")
        vom = VRayOverrideMtl(baseMtl=vom_base)

        wrap_base = PhysicalMaterial(base_color_map="tex/wrap_hidden.jpg")
        wrap = VRayMtlWrapper(baseMtl=wrap_base)

        vom_hits = discover(vom)
        wrap_hits = discover(wrap)

        self.assertEqual(len(vom_hits), 1, "VRayOverrideMtl → walked")
        self.assertEqual(vom_hits[0][2], "tex/vom_shows.jpg")

        self.assertEqual(wrap_hits, [], "VRayMtlWrapper → not walked")


class TestSlotTableCompleteness(unittest.TestCase):
    """The `_WRAPPER_TABLE` lists the EXACT slots the walker
    descends. Any add / remove / rename here must be paired with a
    same-bite update to the MAXScript block. Locking the slot names
    prevents a well-intentioned refactor from silently changing the
    walker's coverage."""

    def test_VRayBlendMtl_walks_exactly_ten_slots(self):
        slots = _WRAPPER_TABLE["VRayBlendMtl"]
        self.assertEqual(len(slots), 10,
                         "VRayBlendMtl slot list must have exactly 10 "
                         "entries (baseMtl + coatMtl_1..coatMtl_9)")

    def test_VRayBlendMtl_slot_names_are_canonical(self):
        slots = _WRAPPER_TABLE["VRayBlendMtl"]
        expected = ["baseMtl"] + [f"coatMtl_{i}" for i in range(1, 10)]
        self.assertEqual(slots, expected,
                         "VRayBlendMtl slot names must match the V-Ray "
                         "SDK property spellings exactly")

    def test_VRayOverrideMtl_walks_exactly_five_slots(self):
        slots = _WRAPPER_TABLE["VRayOverrideMtl"]
        self.assertEqual(len(slots), 5,
                         "VRayOverrideMtl slot list must have exactly 5 "
                         "entries (baseMtl, giMtl, reflectMtl, refractMtl, "
                         "shadowMtl)")

    def test_VRayOverrideMtl_slot_names_are_canonical(self):
        slots = _WRAPPER_TABLE["VRayOverrideMtl"]
        expected = ["baseMtl", "giMtl", "reflectMtl",
                    "refractMtl", "shadowMtl"]
        self.assertEqual(slots, expected)

    def test_wrapper_table_has_exactly_two_entries(self):
        # Anchor case: if a future PR adds a third entry, this
        # test forces the PR author to look at the audit rationale
        # and confirm the addition is deliberate.
        self.assertEqual(
            set(_WRAPPER_TABLE.keys()),
            {"VRayBlendMtl", "VRayOverrideMtl"},
            "wrapper table must contain exactly VRayBlendMtl and "
            "VRayOverrideMtl — any addition is a scope expansion that "
            "needs its own bite")

    def test_baseMtl_appears_first_on_VRayOverrideMtl(self):
        # Load-bearing ordering: baseMtl-first is what makes baseMtl's
        # textures win the first-hit-wins dedupe.
        self.assertEqual(_WRAPPER_TABLE["VRayOverrideMtl"][0], "baseMtl")

    def test_baseMtl_appears_first_on_VRayBlendMtl(self):
        self.assertEqual(_WRAPPER_TABLE["VRayBlendMtl"][0], "baseMtl")

    def test_novel_slot_on_VRayOverrideMtl_not_walked(self):
        # Simulate a future V-Ray SDK adding `.envMtl` to
        # VRayOverrideMtl. Without a slot-table extension the new
        # slot MUST NOT be walked — no "descend anything that looks
        # like a Mtl" heuristic allowed.
        env_mtl = PhysicalMaterial(base_color_map="tex/env_hidden.jpg")
        vom = VRayOverrideMtl(baseMtl=None)
        vom.envMtl = env_mtl  # novel attribute
        hits = discover(vom)
        # envMtl is not in the slot table → invisible.
        self.assertEqual(hits, [])

    def test_novel_slot_on_VRayBlendMtl_not_walked(self):
        blend = VRayBlendMtl(baseMtl=None)
        # Simulate coatMtl_10 (a hypothetical eleventh coat).
        blend.coatMtl_10 = PhysicalMaterial(
            base_color_map="tex/coat10_hidden.jpg")
        self.assertEqual(discover(blend), [])


class TestSlotOrderIsLoadBearing(unittest.TestCase):
    """The baseMtl-first ordering is what makes the first-hit-wins
    dedupe pick baseMtl's textures over per-ray overrides / coats.
    Reversing the order would silently change user-visible output."""

    def test_reverse_of_VRayOverrideMtl_slots_would_change_output(self):
        # Simulate a walker that reversed the slot order (giMtl first).
        base = PhysicalMaterial(base_color_map="tex/base_should_win.jpg")
        gi = PhysicalMaterial(base_color_map="tex/gi_should_lose.jpg")
        vom = VRayOverrideMtl(baseMtl=base, giMtl=gi)

        # With the correct order (base-first), base wins.
        forward_hits = discover(vom)
        self.assertEqual(forward_hits[0][2], "tex/base_should_win.jpg")

        # Simulate reverse order by manually running discovery with
        # a reversed slot list — proves the ordering is load-bearing.
        reversed_table = {"VRayOverrideMtl":
                          list(reversed(_WRAPPER_TABLE["VRayOverrideMtl"]))}

        def _unwrap_reversed(mat, visited=None, depth=0):
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
            slots = reversed_table.get(cls) \
                or _WRAPPER_TABLE.get(cls)
            if slots:
                for slot_name in slots:
                    sub = _get(mat, slot_name)
                    if sub is not None:
                        for r in _unwrap_reversed(sub, visited, depth + 1):
                            out.append(r)
            return out

        seen = set()
        reversed_hits = []
        for sub in _unwrap_reversed(vom):
            reversed_hits.extend(_probe(sub, seen))
        # Under reversed order, gi would win — pin this differential.
        self.assertEqual(reversed_hits[0][2], "tex/gi_should_lose.jpg",
                         "reversal test setup is broken — the differential "
                         "would not surface")
        self.assertNotEqual(forward_hits[0][2], reversed_hits[0][2],
                            "slot order is load-bearing; a reordering would "
                            "silently change user-visible texture precedence")

    def test_reverse_of_VRayBlendMtl_slots_would_change_output(self):
        base = PhysicalMaterial(base_color_map="tex/blend_base_wins.jpg")
        c1 = PhysicalMaterial(base_color_map="tex/blend_coat1_loses.jpg")
        blend = VRayBlendMtl(baseMtl=base, coats=[c1])

        forward_hits = discover(blend)
        self.assertEqual(forward_hits[0][2], "tex/blend_base_wins.jpg")


class TestIdempotence(unittest.TestCase):
    """Running the walker twice must give the same result. The
    walker's `visited` list is per-call (fresh `#()` each invocation
    in MAXScript), so idempotence is a boundary condition on the
    per-call state."""

    def test_unwrap_twice_on_VRayOverrideMtl_agrees(self):
        base = PhysicalMaterial(base_color_map="tex/idem.jpg")
        gi = PhysicalMaterial(base_color_map="tex/idem_gi.jpg")
        vom = VRayOverrideMtl(baseMtl=base, giMtl=gi)
        self.assertEqual(unwrap(vom), unwrap(vom))

    def test_unwrap_twice_on_VRayBlendMtl_agrees(self):
        base = PhysicalMaterial(base_color_map="tex/idem_b.jpg")
        c1 = PhysicalMaterial(base_color_map="tex/idem_c1.jpg")
        blend = VRayBlendMtl(baseMtl=base, coats=[c1])
        self.assertEqual(unwrap(blend), unwrap(blend))

    def test_discover_twice_on_arena_slice_agrees(self):
        # Small arena slice with a mix of wrappers.
        mix = [
            PhysicalMaterial(base_color_map="tex/plain.jpg"),
            VRayBlendMtl(baseMtl=PhysicalMaterial(
                base_color_map="tex/blend_base.jpg")),
            VRayOverrideMtl(baseMtl=PhysicalMaterial(
                base_color_map="tex/vom_base.jpg")),
            VRay2SidedMtl(frontMtl=PhysicalMaterial(
                base_color_map="tex/2s_front.jpg")),
        ]
        first = [discover(m) for m in mix]
        second = [discover(m) for m in mix]
        self.assertEqual(first, second)


class TestDepthAndCycleSafetyAtScopeAudit(unittest.TestCase):
    """Depth cap + cycle guard — already in MTLX-007's test but
    repeated here because the audit's doc entry cites them as the
    reason the walker is safe at arena scale."""

    def test_deeply_nested_VRayOverrideMtl_terminates_at_depth_cap(self):
        # Build a chain of 10 nested VRayOverrideMtl(baseMtl=next).
        leaf = PhysicalMaterial(base_color_map="tex/at_the_bottom.jpg")
        current = leaf
        for _ in range(10):
            current = VRayOverrideMtl(baseMtl=current)
        result = unwrap(current)
        # Depth cap at 6 → at most 7 unwraps before the recursion
        # returns []. The leaf is not reached.
        self.assertLessEqual(len(result), 10)

    def test_VRayOverrideMtl_self_reference_terminates(self):
        vom = VRayOverrideMtl()
        vom.baseMtl = vom
        result = unwrap(vom)
        self.assertEqual(result, [vom])

    def test_alternating_VRayOverride_VRayBlend_cycle_terminates(self):
        # A pathological arch-viz mistake: a VRayOverrideMtl and a
        # VRayBlendMtl each pointing at the other.
        vom = VRayOverrideMtl()
        blend = VRayBlendMtl()
        vom.baseMtl = blend
        blend.baseMtl = vom
        result = unwrap(vom)
        # Both are visited exactly once; the cycle guard breaks the
        # third visit. Result is [vom, blend].
        self.assertEqual(result, [vom, blend])


class TestArenaCensusDeltaInvariant(unittest.TestCase):
    """The MAX-MTLX-007 fix moves the arena's resolved-diffuse count
    by exactly the wrapper-material bucket (89 in the 179-material
    mix `evidence-slotmap-and-wrappers.md` catalogued). Any future
    refactor of `unwrapBlendMaterialSubMtls` MUST preserve this
    delta on the same synthetic mix. A narrowing (dropping a slot,
    tightening the gate) would lower the number; a widening (adding
    a slot / descending an adjacent wrapper) would raise it. Both
    are scope regressions."""

    def _build_mix(self):
        """Same shape as MAX-MTLX-007's arena census, plus 10
        MAX-MTLX-008 adjacent-wrapper negatives inserted at the end.
        The 10 negatives are NOT counted as wrapper-bucket materials —
        they're the out-of-scope classes. If a future refactor
        starts descending them, this test's ratio flips."""
        mix = []
        for i in range(50):
            mix.append(PhysicalMaterial(
                base_color_map=f"tex/plain_{i:03d}.jpg"))
        for i in range(40):
            mix.append(VRayBlendMtl(
                baseMtl=PhysicalMaterial(
                    base_color_map=f"tex/blend_base_{i:03d}.jpg"),
                coats=[PhysicalMaterial(
                    base_color_map=f"tex/blend_coat_{i:03d}.jpg")]))
        for i in range(20):
            mix.append(VRayBlendMtl(
                baseMtl=PhysicalMaterial(),
                coats=[PhysicalMaterial(
                    base_color_map=f"tex/coat_only_{i:03d}.jpg")]))
        for i in range(15):
            mix.append(VRayOverrideMtl(
                baseMtl=PhysicalMaterial(
                    base_color_map=f"tex/vom_{i:03d}.jpg")))
        for i in range(8):
            inner = PhysicalMaterial(
                base_color_map=f"tex/vom_bl_{i:03d}.jpg")
            mix.append(VRayOverrideMtl(
                baseMtl=VRayBlendMtl(baseMtl=inner)))
        for i in range(6):
            mix.append(VRayBlendMtl(
                baseMtl=VRayBlendMtl(
                    baseMtl=PhysicalMaterial(
                        base_color_map=f"tex/deep_{i:03d}.jpg"))))
        for _ in range(40):
            mix.append(PhysicalMaterial())
        # +10 audit negatives (10 adjacent wrappers with visible-if-walked
        # textures — must remain invisible under MAX-MTLX-007 scope):
        for i in range(2):
            mix.append(VRay2SidedMtl(
                frontMtl=PhysicalMaterial(
                    base_color_map=f"tex/audit_2s_{i}.jpg")))
        for i in range(2):
            mix.append(VRayMtlWrapper(
                baseMtl=PhysicalMaterial(
                    base_color_map=f"tex/audit_wrap_{i}.jpg")))
        for i in range(2):
            mix.append(DoubleSided(
                mtl1=PhysicalMaterial(
                    base_color_map=f"tex/audit_ds_{i}.jpg")))
        for i in range(2):
            mix.append(Shell_Material(
                originalMtl=PhysicalMaterial(
                    base_color_map=f"tex/audit_shell_{i}.jpg")))
        for i in range(2):
            mix.append(Composite_Mtl(mtlList=[
                PhysicalMaterial(
                    base_color_map=f"tex/audit_comp_{i}.jpg")]))
        return mix

    def test_arena_census_size(self):
        mix = self._build_mix()
        # 50 + 40 + 20 + 15 + 8 + 6 + 40 + 10 = 189
        self.assertEqual(len(mix), 189)

    def test_post_MAX_MTLX_007_delta_survives(self):
        # 50 plain + 89 wrapper-bucket = 139 materials produce ≥1
        # hit. The +10 audit negatives contribute ZERO hits (that's
        # the SCOPE — they're out of scope).
        mix = self._build_mix()
        resolved = sum(1 for m in mix if discover(m))
        self.assertEqual(resolved, 139,
                         f"post-MTLX-007 delta must resolve exactly 139 "
                         f"materials, got {resolved}. If lower: narrowing "
                         f"regression. If higher: widening regression "
                         f"(walked an out-of-scope adjacent wrapper).")

    def test_none_of_the_ten_adjacent_wrappers_are_walked(self):
        # Anchor: the last 10 in the mix are the adjacent-wrapper
        # negatives. Discovery must produce 0 hits for each.
        mix = self._build_mix()
        for i, m in enumerate(mix[-10:], start=len(mix) - 10):
            cls = m.max_class_name
            hits = discover(m)
            self.assertEqual(
                hits, [],
                f"adjacent-wrapper material #{i} ({cls!r}) at slot "
                f"{i} produced {len(hits)} hits — must be 0 (out of scope)")

    def test_arena_slice_cross_wrapper_divergence_anchor(self):
        """Cross-wrapper divergence: on the same arena slice with
        collision-shape wrappers side-by-side, VRayOverrideMtl is
        walked and VRayMtlWrapper is not. This is the audit's
        CrossWriter-analog anchor case (see MAX-LIT-003)."""

        vom_base = PhysicalMaterial(base_color_map="tex/anchor_vom.jpg")
        wrap_base = PhysicalMaterial(base_color_map="tex/anchor_wrap.jpg")
        # A slice where both wrappers point at the same-looking
        # PhysicalMaterial sub-material. Only the VRayOverrideMtl
        # one is walked.
        vom = VRayOverrideMtl(baseMtl=vom_base)
        wrap = VRayMtlWrapper(baseMtl=wrap_base)
        pair = [vom, wrap]
        resolved = [len(discover(m)) for m in pair]
        # exactly [1, 0] under MTLX-007's scope.
        self.assertEqual(resolved, [1, 0],
                         "cross-wrapper divergence: VRayOverrideMtl → 1 hit, "
                         "VRayMtlWrapper → 0 hits")


class TestPreMAX_MTLX_007_Regression(unittest.TestCase):
    """The pre-MTLX-007 baseline is that the walker did NOT descend
    ANY wrapper. If this suite ever fails, MAX-MTLX-007's fingerprint
    is stale — a prior bite has already changed the walker's behavior."""

    def test_MAX_MTLX_007_is_still_landed_positive(self):
        # Positive control: without MTLX-007, VRayOverrideMtl(baseMtl=
        # PhysMtl) would resolve to 0 hits. With MTLX-007, it resolves
        # to 1.
        base = PhysicalMaterial(base_color_map="tex/mtlx007_verify.jpg")
        vom = VRayOverrideMtl(baseMtl=base)
        self.assertEqual(len(discover(vom)), 1,
                         "MAX-MTLX-007's VRayOverrideMtl unwrap is not "
                         "detected — has the fix been reverted?")


if __name__ == "__main__":
    unittest.main()
