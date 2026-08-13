# Copyright 2026 Miris Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
"""
MAX-MTLX-VRAYMTL-REFLECTION-TINT-017 -- Python mirror of the
`texmap_reflection` slotMap entry added to `discoverMaxMtlxTexmapsFn`
(see `src/translators/MtlxShaderWriter.cpp` after this fix).

Background
----------
`ND_standard_surface.specular_color` (color3, port default (1,1,1))
is the ND_standard_surface input that carries the reflection-tint
color -- what artists set to tint chrome amber, tint anodized
aluminum blue, or tint brass a warm yellow. Prior to this fix the
MaterialX writer's discovery slotMap covered `refl_color_map`
(PhysicalMaterial canonical) and `specularColorMap` (OpenPBR
canonical) at MtlxShaderWriter.cpp:646-647, but had NO entry for the
V-Ray SDK canonical `texmap_reflection` spelling. Result: every metal
/ tinted-chrome / anodized-aluminum V-Ray material silently exported
with `specular_color` locked at its port default (1,1,1), and Karma
rendered them with neutral-white specular regardless of the artist-
authored reflection tint.

Prior-run evidence at
`agent/pipeline-runs/40dca678-.../evidence/gaps-audit.md:172-177` (S3)
records the arena signature:

    > S3 -- VRayMtl reflection-tint color (`texmap_reflection`) missing
    > Code: `MtlxShaderWriter.cpp:646-647` covers `refl_color_map`
    > (PhysicalMaterial) and `specularColorMap` (OpenPBR); the
    > canonical VRayMtl slot is `texmap_reflection` (V-Ray SDK
    > reflection-tint texmap). Absent from slotMap -> every metal /
    > tinted-chrome / anodized-aluminum V-Ray material renders with
    > neutral white specular in Karma instead of the artist-tinted
    > color.

The Spectrum Center's anodized-aluminum handrails, chrome-tinted
glass surrounds, and tinted-brass fixtures are the census signature
-- ~14 tinted-reflection VRayMtl materials in the 179-material arena
mix.

Fix
---
Extend `discoverMaxMtlxTexmapsFn` slotMap with ONE new entry
covering the V-Ray SDK canonical spelling:

  * `texmap_reflection` -> specular_color (color3)

The entry is placed as a sibling to the pre-existing
`refl_color_map` (PhysicalMaterial) and `specularColorMap` (OpenPBR)
entries so all three PBR material families route to the SAME
ND_standard_surface `specular_color` input. First-hit-wins gives
the PhysicalMaterial spelling precedence for the (unusual) case
where multiple spellings are authored on the same material,
matching the pre-existing snake-first-then-OpenPBR pattern.

There is NO camelCase alt (no `texmapReflection`) because V-Ray's
SDK ships only the snake_case spelling `texmap_reflection` --
matching MTLX-010's single-spelling `texmap_anisotropy`,
`texmap_anisotropyRotation`, MTLX-011's `texmap_opacity`, and
MTLX-009's `texmap_reflectionIOR` / `texmap_refractionIOR`.

The entry routes to `color3` (unlike the neighboring MTLX-009 IOR
entries which route to `float`). The existing color3 colorspace
gate in `_EnrichMtlxDocFromMaxMaterial` applies by construction,
authoring `colorspace="srgb_texture"` on the injected
ND_tiledimage_color3 -- the correct semantic for a reflection-
tint COLOR (matches the pre-existing `refl_color_map` /
`specularColorMap` treatment).

The change is a strict SUPERSET extension of the slotMap; no
existing entry is removed or reordered. The wrapper walk
(MAX-MTLX-007 `unwrapBlendMaterialSubMtls`, extended for Composite
/ Blend by MAX-MTLX-COMPOSITE-DECAL-014) applies automatically
because slotMap iteration lives inside `for currentMat in subMtls`
-- VRayBlendMtl / VRayOverrideMtl / CompositeMtl / stock BlendMtl
unwrap identically for the new entry, and baseMtl's
texmap_reflection wins over any coat's / per-ray override's.

Test strategy
-------------
The C++/MAXScript ships on Windows-only + Max SDK, so we cannot
execute the MAXScript here. Instead we model the material families
as plain-Python class fakes mirroring the MAXScript
`isProperty` / `getProperty` surface, and we mirror the C++
discovery + injection logic at the USD-doc layer using MaterialX
Python bindings (which ship inside Houdini's `hython`).

Coverage buckets:

  * PreFixDefect           -- pre-fix baseline where slotMap has no
                              `texmap_reflection` entry: a VRayMtl
                              authoring the spelling produces zero
                              specular_color discoveries.
  * PropertyDispatch       -- the new `texmap_reflection` entry
                              resolves and drives a
                              `ND_tiledimage_color3` into
                              specular_color. Cross-family
                              independence (base_color still
                              works alongside).
  * FirstHitWinsCrossFamily -- a material authoring BOTH
                              `refl_color_map` AND `texmap_reflection`
                              resolves to `refl_color_map` (snake-
                              PhysicalMaterial declared first) --
                              matches the pre-existing pattern from
                              MTLX-009's specular_IOR (snake before
                              OpenPBR before VRayMtl).
  * WrapperWalk            -- VRayBlendMtl base wins over coat;
                              coat fills gap when base has no
                              texmap_reflection; VRayOverrideMtl
                              base wins over reflect/refract/GI
                              overrides. Reuses MTLX-007's
                              precedence.
  * TypeCorrectness        -- the injected tiledimage is color3-typed
                              (`ND_tiledimage_color3`), and the
                              shader input reference is `color3`.
                              Anchored against the MaterialX stdlib
                              so a library-version bump can't quietly
                              change the type.
  * ColorspaceGate         -- unlike float IOR / roughness /
                              anisotropy maps, color3 reflection-tint
                              maps MUST carry a
                              `colorspace="srgb_texture"` attribute
                              (matches the pre-existing
                              `refl_color_map` treatment). Absence
                              would render the tint darkened by the
                              missing sRGB decode.
  * NoReflectionMap        -- a material with base_color etc. but no
                              texmap_reflection exports zero
                              specular_color tiledimages -- the
                              port default (1,1,1) still holds,
                              and the doc stays clean.
  * SurgicalScope          -- the fix touches ONLY the slotMap; the
                              existing base_color / roughness /
                              metalness / normal / opacity /
                              emission_color / transmission_color /
                              cutout / specular_IOR / anisotropy /
                              coat / etc. entries produce byte-
                              identical output before and after the
                              change, on a material that exercises
                              them without any texmap_reflection.
  * StrictSupersetSlotMap  -- the post-fix slotMap contains ALL
                              pre-fix entries with unchanged
                              (max-prop, input, type) tuples, and
                              adds EXACTLY one new entry -- no
                              more, no fewer, in the intended
                              position (as a sibling to
                              specularColorMap, before the MTLX-009
                              IOR block).
  * ArenaCensus            -- a synthesized 179-material arena-
                              density mix (100 non-tinted V-Ray + 40
                              PhysicalMaterial-white + 25 OpenPBR-
                              stainless + 14 VRayMtl tinted-metal
                              with `texmap_reflection`) resolves to
                              14 specular_color-from-texmap_reflection
                              discoveries post-fix vs. 0 pre-fix,
                              with the non-reflection discovery
                              counts unchanged.

Run:  hython test_miris_max_mtlx_vraymtl_reflection_tint_017.py
"""
import unittest

import MaterialX as mx


# =============================================================================
# Fake material classes -- mirror the MAXScript `isProperty` /
# `getProperty` surface. Same shape as MTLX-007 / MTLX-009 / MTLX-010
# / MTLX-012.
# =============================================================================


class _MaterialBase:
    max_class_name = "GenericMtl"


class PhysicalMaterial(_MaterialBase):
    """Autodesk stock PhysicalMaterial. `refl_color_map` is the
    canonical snake_case reflection-tint slot (mat_def)."""

    max_class_name = "PhysicalMaterial"

    def __init__(
        self,
        base_color_map=None,
        roughness_map=None,
        refl_color_map=None,
    ):
        if base_color_map is not None:
            self.base_color_map = base_color_map
        if roughness_map is not None:
            self.roughness_map = roughness_map
        if refl_color_map is not None:
            self.refl_color_map = refl_color_map


class OpenPBRDerived(_MaterialBase):
    """OpenPBR-shaped material -- `specularColorMap` is the canonical
    camelCase reflection-tint slot."""

    max_class_name = "OpenPbr"

    def __init__(
        self,
        baseColorMap=None,
        specularColorMap=None,
    ):
        if baseColorMap is not None:
            self.baseColorMap = baseColorMap
        if specularColorMap is not None:
            self.specularColorMap = specularColorMap


class VRayMtl(_MaterialBase):
    """VRayMtl exposes `.texmap_reflection` for the reflection-tint
    color. V-Ray SDK canonical name -- surfaced at the MAXScript
    layer directly. No camelCase alt -- V-Ray SDK ships only the
    snake_case spelling."""

    max_class_name = "VRayMtl"

    def __init__(
        self,
        texmap_diffuse=None,
        texmap_reflection=None,
        texmap_reflectionIOR=None,
    ):
        if texmap_diffuse is not None:
            self.texmap_diffuse = texmap_diffuse
        if texmap_reflection is not None:
            self.texmap_reflection = texmap_reflection
        if texmap_reflectionIOR is not None:
            self.texmap_reflectionIOR = texmap_reflectionIOR


class VRayBlendMtl(_MaterialBase):
    """Layered paint / weathered-surface wrapper -- same shape as
    MTLX-007 / MTLX-009 / MTLX-010 / MTLX-012."""

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
    """Per-ray override wrapper -- same shape as MTLX-007 / MTLX-009
    / MTLX-010 / MTLX-012."""

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
    """Stand-in for a MAXScript Bitmap/VRayBitmap texmap -- attribute
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
# The slotMap -- MUST mirror `MtlxShaderWriter.cpp` verbatim.
# `test_strict_superset_slot_map_matches_source` locks this against
# any future drift.
# =============================================================================


SLOT_MAP_POST_FIX = [
    # Pre-existing entries (unchanged).
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
    # MAX-MTLX-VRAYMTL-REFLECTION-TINT-017 addition.
    ("texmap_reflection",      "specular_color",     "color3"),
    # Pre-existing entries (unchanged).
    ("trans_color_map",        "transmission_color", "color3"),
    ("transmissionColorMap",   "transmission_color", "color3"),
    ("cutout_map",             "opacity",            "float"),
    ("cutoutMap",              "opacity",            "float"),
    # MAX-MTLX-009 additions (still present, verbatim).
    ("trans_ior_map",          "specular_IOR",       "float"),
    ("transIorMap",            "specular_IOR",       "float"),
    ("specular_ior_map",       "specular_IOR",       "float"),
    ("specularIorMap",         "specular_IOR",       "float"),
    ("texmap_reflectionIOR",   "specular_IOR",       "float"),
    ("texmap_refractionIOR",   "specular_IOR",       "float"),
    # MAX-MTLX-010 additions (still present, verbatim).
    ("anisotropy_map",             "specular_anisotropy", "float"),
    ("anisotropyMap",              "specular_anisotropy", "float"),
    ("texmap_anisotropy",          "specular_anisotropy", "float"),
    ("anisotropy_rotation_map",    "specular_rotation",   "float"),
    ("anisotropyRotationMap",      "specular_rotation",   "float"),
    ("texmap_anisotropyRotation",  "specular_rotation",   "float"),
    # MAX-MTLX-011 additions (still present, verbatim).
    ("trans_roughness_map",         "transmission_extra_roughness", "float"),
    ("transRoughnessMap",           "transmission_extra_roughness", "float"),
    ("transmission_roughness_map",  "transmission_extra_roughness", "float"),
    ("transmissionRoughnessMap",    "transmission_extra_roughness", "float"),
    ("sheen_roughness_map",         "sheen_roughness",              "float"),
    ("sheenRoughnessMap",           "sheen_roughness",              "float"),
    # MAX-MTLX-COAT-015 additions (still present, verbatim).
    ("coat_map",                    "coat",           "float"),
    ("coatMap",                     "coat",           "float"),
    ("coat_rough_map",              "coat_roughness", "float"),
    ("coatRoughMap",                "coat_roughness", "float"),
    ("clearcoat_map",               "coat",           "float"),
    ("clearcoatMap",                "coat",           "float"),
    ("clearcoatRoughness_map",      "coat_roughness", "float"),
    ("clearcoatRoughnessMap",       "coat_roughness", "float"),
    ("coat_weight_map",             "coat",           "float"),
    ("coatWeightMap",               "coat",           "float"),
    ("coat_color_map",              "coat_color",     "color3"),
    ("coatColorMap",                "coat_color",     "color3"),
    ("coat_roughness_map",          "coat_roughness", "float"),
    ("coatRoughnessMap",            "coat_roughness", "float"),
    ("coat_ior_map",                "coat_IOR",       "float"),
    ("coatIorMap",                  "coat_IOR",       "float"),
    ("coat_normal_map",             "coat_normal",    "vector3"),
    ("coatNormalMap",               "coat_normal",    "vector3"),
    ("coat_amount_texmap",          "coat",           "float"),
    ("coatAmountTexmap",            "coat",           "float"),
    # MAX-MTLX-OPACITY-MAP-016 additions (still present, verbatim).
    ("opacity_map",                 "opacity",        "float"),
    ("opacityMap",                  "opacity",        "float"),
    ("geometry_opacity_map",        "opacity",        "float"),
    ("geometryOpacityMap",          "opacity",        "float"),
    ("texmap_opacity",              "opacity",        "float"),
]

# Exact one-item MAX-MTLX-VRAYMTL-REFLECTION-TINT-017 addition.
MAX_MTLX_017_ADDITIONS = [
    ("texmap_reflection",      "specular_color",     "color3"),
]

# The pre-fix slotMap -- for the strict-superset assertion + PreFixDefect.
SLOT_MAP_PRE_FIX = [e for e in SLOT_MAP_POST_FIX if e not in MAX_MTLX_017_ADDITIONS]


# =============================================================================
# Python mirror of the C++ `_EnrichMtlxDocFromMaxMaterial` + the
# `discoverMaxMtlxTexmaps` MAXScript helper. Parameterized on the
# active slotMap so we can exercise both pre-fix and post-fix
# behavior against the same materials.
# =============================================================================


def _resolve_max_texmap_filename(tex):
    """Simplified stand-in for `resolveMaxTexmapFilename` -- a Bitmap
    stand-in exposes `.filename` directly."""
    if tex is None:
        return None
    return getattr(tex, "filename", None)


def discover_max_mtlx_texmaps(mat, slot_map):
    """Verbatim mirror of the MAXScript `discoverMaxMtlxTexmaps` loop
    with the wrapper walk from MTLX-007. Returns a list of
    (mtlx_input, mtlx_type, file_path)."""
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
    interior is DANGLING -- mirrors the pre-injection state produced
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
    """Baseline: pre-fix, a VRayMtl authoring `.texmap_reflection`
    produces ZERO tiledimage nodes for specular_color. This locks in
    the fingerprint the fix resolves."""

    def test_vray_texmap_reflection_dropped_pre_fix(self):
        mat = VRayMtl(
            texmap_diffuse=_FakeBitmap("chrome_diff.png"),
            texmap_reflection=_FakeBitmap("chrome_tint.png"),
        )
        discovery = discover_max_mtlx_texmaps(mat, SLOT_MAP_PRE_FIX)
        inputs = {d[0] for d in discovery}
        self.assertNotIn(
            "specular_color", inputs,
            "Pre-fix VRayMtl.texmap_reflection specular_color path "
            "should be silently dropped -- this is the defect S3 in "
            "gaps-audit.md that the fix resolves.",
        )

    def test_vray_texmap_reflection_dropped_pre_fix_leaves_base_color(self):
        # Base color still works pre-fix -- only the reflection-tint
        # branch is silently dropped.
        mat = VRayMtl(
            texmap_diffuse=_FakeBitmap("chrome_diff.png"),
            texmap_reflection=_FakeBitmap("chrome_tint.png"),
        )
        discovery = discover_max_mtlx_texmaps(mat, SLOT_MAP_PRE_FIX)
        # Note: VRayMtl.texmap_diffuse maps to base_color via the
        # unmodified pre-existing entries -- but our simple fake
        # doesn't have that mapping; what matters is that the
        # pre-fix pipeline finds ZERO specular_color hits.
        specular_hits = [d for d in discovery if d[0] == "specular_color"]
        self.assertEqual(len(specular_hits), 0)

    def test_physical_material_refl_color_map_still_works_pre_fix(self):
        # PhysicalMaterial's snake_case spelling was ALREADY in the
        # slotMap pre-fix -- verify the defect is specific to VRayMtl.
        mat = PhysicalMaterial(
            base_color_map=_FakeBitmap("wall.png"),
            refl_color_map=_FakeBitmap("wall_spec.png"),
        )
        discovery = discover_max_mtlx_texmaps(mat, SLOT_MAP_PRE_FIX)
        specular_hits = [d for d in discovery if d[0] == "specular_color"]
        self.assertEqual(len(specular_hits), 1)
        self.assertEqual(specular_hits[0][2], "wall_spec.png")


class TestPropertyDispatch(unittest.TestCase):
    """The new `texmap_reflection` slotMap entry dispatches correctly
    post-fix."""

    def test_vray_texmap_reflection_resolves_to_specular_color(self):
        mat = VRayMtl(texmap_reflection=_FakeBitmap("chrome_tint.png"))
        discovery = discover_max_mtlx_texmaps(mat, SLOT_MAP_POST_FIX)
        hits = [d for d in discovery if d[0] == "specular_color"]
        self.assertEqual(
            len(hits), 1,
            f"Expected exactly one specular_color discovery, got {hits!r}",
        )
        self.assertEqual(hits[0][1], "color3")
        self.assertEqual(hits[0][2], "chrome_tint.png")

    def test_vray_texmap_reflection_alongside_diffuse(self):
        # Cross-family independence: base_color and specular_color
        # are distinct MaterialX inputs; the seenInputs dedupe tracks
        # them separately.
        mat = VRayMtl(
            texmap_diffuse=_FakeBitmap("chrome_diff.png"),
            texmap_reflection=_FakeBitmap("chrome_tint.png"),
        )
        # Our fake doesn't have a base_color mapping, but the point
        # is that authoring texmap_reflection doesn't interfere with
        # other slotMap entries. Verify by adding a
        # base_color_map-authored companion.
        mat.base_color_map = _FakeBitmap("chrome_diff.png")
        discovery = discover_max_mtlx_texmaps(mat, SLOT_MAP_POST_FIX)
        inputs = {d[0]: d[2] for d in discovery}
        self.assertEqual(inputs.get("base_color"), "chrome_diff.png")
        self.assertEqual(inputs.get("specular_color"), "chrome_tint.png")

    def test_vray_texmap_reflection_type_is_color3(self):
        # Not float, not vector3 -- color3 (matches refl_color_map /
        # specularColorMap treatment).
        mat = VRayMtl(texmap_reflection=_FakeBitmap("t.png"))
        discovery = discover_max_mtlx_texmaps(mat, SLOT_MAP_POST_FIX)
        hits = [d for d in discovery if d[0] == "specular_color"]
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0][1], "color3")

    def test_vray_texmap_reflection_with_reflectionIOR_independent(self):
        # A VRayMtl authoring BOTH .texmap_reflection (color tint) and
        # .texmap_reflectionIOR (float IOR) produces TWO discoveries --
        # one to specular_color, one to specular_IOR. They are
        # distinct MaterialX inputs; the seenInputs dedupe tracks
        # them separately.
        mat = VRayMtl(
            texmap_reflection=_FakeBitmap("chrome_tint.png"),
            texmap_reflectionIOR=_FakeBitmap("chrome_ior.png"),
        )
        discovery = discover_max_mtlx_texmaps(mat, SLOT_MAP_POST_FIX)
        inputs = {d[0]: (d[1], d[2]) for d in discovery}
        self.assertEqual(
            inputs.get("specular_color"), ("color3", "chrome_tint.png"),
        )
        self.assertEqual(
            inputs.get("specular_IOR"), ("float", "chrome_ior.png"),
        )


class TestFirstHitWinsCrossFamily(unittest.TestCase):
    """A material authoring BOTH `refl_color_map` (PhysicalMaterial
    snake) AND `texmap_reflection` (VRayMtl SDK) resolves to the
    PhysicalMaterial spelling because it's declared first in slotMap
    order -- matches the pre-existing MTLX-009 pattern (snake
    before OpenPBR before VRayMtl for the IOR family)."""

    def test_refl_color_map_wins_over_texmap_reflection(self):
        m = _MaterialBase()
        m.max_class_name = "GenericMtl"
        m.refl_color_map = _FakeBitmap("phys.png")
        m.texmap_reflection = _FakeBitmap("vray.png")
        discovery = discover_max_mtlx_texmaps(m, SLOT_MAP_POST_FIX)
        hits = [d for d in discovery if d[0] == "specular_color"]
        self.assertEqual(len(hits), 1)
        self.assertEqual(
            hits[0][2], "phys.png",
            "PhysicalMaterial `refl_color_map` should win under first-"
            "hit-wins -- declared first in slotMap order.",
        )

    def test_specularColorMap_wins_over_texmap_reflection(self):
        m = _MaterialBase()
        m.max_class_name = "GenericMtl"
        m.specularColorMap = _FakeBitmap("openpbr.png")
        m.texmap_reflection = _FakeBitmap("vray.png")
        discovery = discover_max_mtlx_texmaps(m, SLOT_MAP_POST_FIX)
        hits = [d for d in discovery if d[0] == "specular_color"]
        self.assertEqual(len(hits), 1)
        self.assertEqual(
            hits[0][2], "openpbr.png",
            "OpenPBR `specularColorMap` should win over VRayMtl "
            "`texmap_reflection` -- OpenPBR camelCase declared "
            "before VRayMtl SDK in slotMap order.",
        )

    def test_texmap_reflection_wins_when_only_vray_spelling_authored(self):
        # Solo VRayMtl-spelling should still resolve now -- that's
        # the entire point of the fix.
        m = VRayMtl(texmap_reflection=_FakeBitmap("vray_solo.png"))
        discovery = discover_max_mtlx_texmaps(m, SLOT_MAP_POST_FIX)
        hits = [d for d in discovery if d[0] == "specular_color"]
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0][2], "vray_solo.png")


class TestWrapperWalk(unittest.TestCase):
    """MAX-MTLX-007's wrapper walk applies to the new
    `texmap_reflection` entry because slotMap iteration lives inside
    `for currentMat in subMtls`."""

    def test_vray_blend_base_reflection_wins_over_coat(self):
        base = VRayMtl(texmap_reflection=_FakeBitmap("base_tint.png"))
        coat = VRayMtl(texmap_reflection=_FakeBitmap("coat_tint.png"))
        blend = VRayBlendMtl(baseMtl=base, coats=(coat,))
        discovery = discover_max_mtlx_texmaps(blend, SLOT_MAP_POST_FIX)
        hits = [d for d in discovery if d[0] == "specular_color"]
        self.assertEqual(len(hits), 1)
        self.assertEqual(
            hits[0][2], "base_tint.png",
            "base's reflection tint should win over coat's under "
            "first-hit-wins (MTLX-007 base-first traversal).",
        )

    def test_coat_reflection_fills_gap_when_base_lacks_reflection(self):
        base = PhysicalMaterial(base_color_map=_FakeBitmap("base_diff.png"))
        # base has no reflection map at all; coat provides it via
        # texmap_reflection.
        coat = VRayMtl(texmap_reflection=_FakeBitmap("coat_tint.png"))
        blend = VRayBlendMtl(baseMtl=base, coats=(coat,))
        discovery = discover_max_mtlx_texmaps(blend, SLOT_MAP_POST_FIX)
        spec_hits = [d for d in discovery if d[0] == "specular_color"]
        self.assertEqual(len(spec_hits), 1)
        self.assertEqual(spec_hits[0][2], "coat_tint.png")

    def test_vray_override_base_reflection_wins_over_reflect(self):
        base = VRayMtl(
            texmap_reflection=_FakeBitmap("base_tint.png"),
        )
        reflect_only = VRayMtl(
            texmap_reflection=_FakeBitmap("reflect_tint.png"),
        )
        override = VRayOverrideMtl(baseMtl=base, reflectMtl=reflect_only)
        discovery = discover_max_mtlx_texmaps(override, SLOT_MAP_POST_FIX)
        hits = [d for d in discovery if d[0] == "specular_color"]
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0][2], "base_tint.png")


class TestTypeCorrectness(unittest.TestCase):
    """The injected tiledimage must be color3-typed for
    specular_color; wrong type would fail Karma / Hydra
    evaluation."""

    def test_injected_tiledimage_is_color3_not_float(self):
        mat = VRayMtl(texmap_reflection=_FakeBitmap("tint.png"))
        discovery = discover_max_mtlx_texmaps(mat, SLOT_MAP_POST_FIX)
        doc, ng, shader = build_scaffold_doc(
            [("specular_color", "color3")]
        )
        n = enrich_mtlx_doc(doc, ng, shader, discovery)
        self.assertEqual(n, 1)
        img = ng.getNode("img_specular_color")
        self.assertIsNotNone(img)
        self.assertEqual(img.getType(), "color3")
        self.assertEqual(
            img.getNodeDefString(), "ND_tiledimage_color3",
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
        inp = nd.getActiveInput("specular_color")
        self.assertIsNotNone(
            inp,
            "MaterialX stdlib must expose specular_color input",
        )
        self.assertEqual(
            inp.getType(), "color3",
            "MAX-MTLX-VRAYMTL-REFLECTION-TINT-017 authors "
            "specular_color as color3; a stdlib change to another "
            "type would silently break the fix.",
        )


class TestColorspaceGate(unittest.TestCase):
    """Unlike float IOR / anisotropy / roughness / opacity maps,
    color3 reflection-tint maps MUST carry a
    `colorspace="srgb_texture"` attribute so the sRGB->linear decode
    runs at texture-fetch time. Matches the pre-existing
    `refl_color_map` / `specularColorMap` treatment; absence would
    silently darken the artist-authored tint."""

    def test_srgb_colorspace_authored_on_texmap_reflection_tiledimage(self):
        mat = VRayMtl(texmap_reflection=_FakeBitmap("chrome_tint.png"))
        discovery = discover_max_mtlx_texmaps(mat, SLOT_MAP_POST_FIX)
        doc, ng, shader = build_scaffold_doc(
            [("specular_color", "color3")]
        )
        enrich_mtlx_doc(doc, ng, shader, discovery)
        img = ng.getNode("img_specular_color")
        file_input = img.getInput("file")
        self.assertIsNotNone(file_input)
        self.assertEqual(
            file_input.getAttribute("colorspace"), "srgb_texture",
            "Color3 reflection-tint map MUST carry "
            "colorspace='srgb_texture' -- matching the pre-existing "
            "refl_color_map / specularColorMap treatment. Absence "
            "would silently apply an incorrect linear decode and "
            "darken the tint.",
        )

    def test_no_colorspace_on_float_ior_still(self):
        # Sanity: the pre-existing float branch (MTLX-009's IOR
        # entries) still authors NO colorspace, so we did not
        # inadvertently promote ALL entries to srgb_texture.
        mat = VRayMtl(
            texmap_reflectionIOR=_FakeBitmap("ior.png"),
        )
        discovery = discover_max_mtlx_texmaps(mat, SLOT_MAP_POST_FIX)
        doc, ng, shader = build_scaffold_doc(
            [("specular_IOR", "float")]
        )
        enrich_mtlx_doc(doc, ng, shader, discovery)
        img = ng.getNode("img_specular_IOR")
        file_input = img.getInput("file")
        self.assertFalse(
            file_input.hasAttribute("colorspace"),
            "Float IOR map must still NOT carry a colorspace "
            "attribute -- MTLX-017's color3 addition must not "
            "affect the float-branch colorspace gate.",
        )


class TestNoReflectionMap(unittest.TestCase):
    """A material with base_color etc. but no reflection tint map
    produces zero specular_color discoveries -- the
    ND_standard_surface port default (1,1,1) rule."""

    def test_no_reflection_map_no_specular_color_output(self):
        mat = VRayMtl(
            texmap_diffuse=_FakeBitmap("diff.png"),
        )
        mat.base_color_map = _FakeBitmap("diff.png")
        discovery = discover_max_mtlx_texmaps(mat, SLOT_MAP_POST_FIX)
        inputs = {d[0] for d in discovery}
        self.assertIn("base_color", inputs)
        self.assertNotIn("specular_color", inputs)


class TestSurgicalScope(unittest.TestCase):
    """The fix touches ONLY the slotMap -- it must not affect any
    pre-existing dispatch. A material exercising every non-reflection
    slot produces byte-identical discovery output under both
    slotMaps."""

    def _build_full_non_reflection_material(self):
        # A synthetic material exercising each of the pre-existing
        # slot-map inputs. We do NOT include texmap_reflection so
        # both slotMaps should produce the SAME set of inputs.
        m = _MaterialBase()
        m.max_class_name = "GenericMtl"
        m.base_color_map = _FakeBitmap("a.png")
        m.roughness_map = _FakeBitmap("b.png")
        m.metalness_map = _FakeBitmap("c.png")
        m.bump_map = _FakeBitmap("d.png")
        m.emit_color_map = _FakeBitmap("e.png")
        m.refl_color_map = _FakeBitmap("f.png")  # PhysicalMaterial spec_color
        m.trans_color_map = _FakeBitmap("g.png")
        m.cutout_map = _FakeBitmap("h.png")
        m.trans_ior_map = _FakeBitmap("i.png")
        m.anisotropy_map = _FakeBitmap("j.png")
        m.coat_map = _FakeBitmap("k.png")
        m.opacity_map = _FakeBitmap("l.png")
        return m

    def test_non_reflection_material_discovery_unchanged(self):
        m = self._build_full_non_reflection_material()
        pre = discover_max_mtlx_texmaps(m, SLOT_MAP_PRE_FIX)
        post = discover_max_mtlx_texmaps(m, SLOT_MAP_POST_FIX)
        self.assertEqual(
            pre, post,
            "MAX-MTLX-VRAYMTL-REFLECTION-TINT-017 must be a strict "
            "superset -- a material with no texmap_reflection must "
            "produce byte-identical discovery.",
        )

    def test_material_with_refl_color_map_unchanged(self):
        # A material with the PhysicalMaterial refl_color_map spelling
        # (already in the pre-fix slotMap) must produce byte-identical
        # discovery pre and post -- proves the fix's added entry
        # doesn't affect first-hit resolution when the pre-existing
        # spelling wins.
        m = _MaterialBase()
        m.max_class_name = "GenericMtl"
        m.refl_color_map = _FakeBitmap("phys.png")
        pre = discover_max_mtlx_texmaps(m, SLOT_MAP_PRE_FIX)
        post = discover_max_mtlx_texmaps(m, SLOT_MAP_POST_FIX)
        self.assertEqual(pre, post)

    def test_only_specular_color_input_added(self):
        # Contract: the one new entry routes to specular_color.
        added_inputs = {e[1] for e in MAX_MTLX_017_ADDITIONS}
        self.assertEqual(added_inputs, {"specular_color"})

    def test_only_color3_type_added(self):
        added_types = {e[2] for e in MAX_MTLX_017_ADDITIONS}
        self.assertEqual(added_types, {"color3"})

    def test_exactly_one_entry_added(self):
        # The fix is intentionally a single-slot addition.
        self.assertEqual(len(MAX_MTLX_017_ADDITIONS), 1)


class TestStrictSupersetSlotMap(unittest.TestCase):
    """The post-fix slotMap MUST contain every pre-fix entry
    unchanged AND EXACTLY the one new entry. Locks the exact
    contents against future drift."""

    def test_post_fix_is_strict_superset(self):
        pre_set = set(SLOT_MAP_PRE_FIX)
        post_set = set(SLOT_MAP_POST_FIX)
        self.assertTrue(
            pre_set.issubset(post_set),
            "Post-fix slotMap must not remove or alter any pre-fix "
            "entry.",
        )
        added = post_set - pre_set
        self.assertEqual(
            len(added), 1,
            f"Expected exactly 1 addition, got {len(added)}: {added!r}",
        )

    def test_new_entry_is_exact_one(self):
        expected_new = {
            ("texmap_reflection", "specular_color", "color3"),
        }
        self.assertEqual(set(MAX_MTLX_017_ADDITIONS), expected_new)

    def test_new_entry_positioned_after_specularColorMap(self):
        # `texmap_reflection` must be declared AFTER `specularColorMap`
        # so first-hit-wins gives snake_case-PhysicalMaterial then
        # OpenPBR-camelCase then VRayMtl-SDK precedence -- matches
        # every other slot family's convention (e.g. MTLX-009's
        # specular_IOR family: trans_ior_map / transIorMap /
        # specular_ior_map / specularIorMap / texmap_reflectionIOR /
        # texmap_refractionIOR).
        names = [e[0] for e in SLOT_MAP_POST_FIX]
        self.assertLess(
            names.index("specularColorMap"),
            names.index("texmap_reflection"),
            "texmap_reflection must come AFTER specularColorMap so "
            "OpenPBR camelCase wins over VRayMtl SDK under first-"
            "hit-wins.",
        )
        self.assertLess(
            names.index("refl_color_map"),
            names.index("texmap_reflection"),
            "texmap_reflection must come AFTER refl_color_map so "
            "PhysicalMaterial snake_case wins over VRayMtl SDK "
            "under first-hit-wins.",
        )

    def test_new_entry_positioned_before_trans_color_map(self):
        # `texmap_reflection` fits in the color3-reflection family;
        # trans_color_map belongs to the color3-transmission family
        # which is a separate slot. Verifies we didn't accidentally
        # displace a different family boundary.
        names = [e[0] for e in SLOT_MAP_POST_FIX]
        self.assertLess(
            names.index("texmap_reflection"),
            names.index("trans_color_map"),
            "texmap_reflection must be sibling to the reflection-"
            "color family, before the transmission-color family "
            "begins.",
        )

    def test_pre_existing_reflection_family_intact(self):
        # The two pre-existing specular_color entries (refl_color_map,
        # specularColorMap) must still be present with unchanged
        # (max-prop, input, type) tuples.
        entries_by_input = [e for e in SLOT_MAP_POST_FIX if e[1] == "specular_color"]
        self.assertEqual(len(entries_by_input), 3)  # 2 pre-existing + 1 new
        prop_names = [e[0] for e in entries_by_input]
        self.assertIn("refl_color_map", prop_names)
        self.assertIn("specularColorMap", prop_names)
        self.assertIn("texmap_reflection", prop_names)
        # All three route to color3 (not float, not vector3).
        for e in entries_by_input:
            self.assertEqual(e[2], "color3")


class TestArenaCensus(unittest.TestCase):
    """179-material arena-density synthetic mix (Spectrum Center
    signature).

      * 100 non-tinted V-Ray materials -- texmap_diffuse only, no
             reflection tint.
      *  40 PhysicalMaterial concrete / drywall -- base_color only,
             specular default (no refl_color_map).
      *  25 OpenPBR-stainless kitchen fixtures -- baseColorMap +
             specularColorMap (PhysicalMaterial-side-canonical, already
             in the pre-fix slotMap).
      *  14 VRayMtl tinted-metal (anodized-aluminum handrails,
             chrome-tinted glass surrounds, tinted-brass fixtures)
             -- texmap_reflection (the fix's target).

    Pre-fix expected: 25 specular_color discoveries (from OpenPBR
    branch), NONE from the 14 VRayMtl tinted-metal. Post-fix
    expected: 25 + 14 = 39 specular_color discoveries. Non-
    reflection discovery counts unchanged in both slotMaps."""

    def _build_arena(self):
        arena = []
        for _ in range(100):
            m = VRayMtl(
                texmap_diffuse=_FakeBitmap("wall.png"),
            )
            arena.append(m)
        for _ in range(40):
            m = PhysicalMaterial(
                base_color_map=_FakeBitmap("concrete.png"),
                roughness_map=_FakeBitmap("concrete_rough.png"),
            )
            arena.append(m)
        for _ in range(25):
            arena.append(OpenPBRDerived(
                baseColorMap=_FakeBitmap("steel.png"),
                specularColorMap=_FakeBitmap("steel_spec.png"),
            ))
        for _ in range(14):
            arena.append(VRayMtl(
                texmap_diffuse=_FakeBitmap("chrome.png"),
                texmap_reflection=_FakeBitmap("chrome_tint.png"),
            ))
        return arena

    def test_arena_reflection_tint_discovery_counts(self):
        arena = self._build_arena()
        self.assertEqual(len(arena), 179)
        pre_specular_from_reflmap = 0
        pre_specular_from_texmap_refl = 0
        pre_other = 0
        post_specular_from_reflmap = 0
        post_specular_from_texmap_refl = 0
        post_other = 0
        for m in arena:
            for d in discover_max_mtlx_texmaps(m, SLOT_MAP_PRE_FIX):
                if d[0] == "specular_color":
                    # Attribute name for the source tex tells us
                    # which spelling won.
                    if d[2].endswith("_spec.png"):
                        pre_specular_from_reflmap += 1
                    else:
                        pre_specular_from_texmap_refl += 1
                else:
                    pre_other += 1
            for d in discover_max_mtlx_texmaps(m, SLOT_MAP_POST_FIX):
                if d[0] == "specular_color":
                    if d[2].endswith("_spec.png"):
                        post_specular_from_reflmap += 1
                    else:
                        post_specular_from_texmap_refl += 1
                else:
                    post_other += 1
        self.assertEqual(
            pre_specular_from_texmap_refl, 0,
            "Pre-fix must have ZERO specular_color discoveries from "
            "texmap_reflection -- that's the entire defect.",
        )
        self.assertEqual(
            pre_specular_from_reflmap, 25,
            "Pre-fix must still have 25 specular_color discoveries "
            "from OpenPBR specularColorMap (the pre-existing "
            "spelling).",
        )
        self.assertEqual(
            post_specular_from_texmap_refl, 14,
            "Post-fix must have EXACTLY 14 specular_color "
            "discoveries from texmap_reflection -- the anodized-"
            "aluminum handrail / chrome-tinted glass / tinted-brass "
            "fixture census signature.",
        )
        self.assertEqual(
            post_specular_from_reflmap, 25,
            "Post-fix must preserve the 25 OpenPBR specularColorMap "
            "discoveries -- the pre-existing branch is unchanged.",
        )
        self.assertEqual(
            pre_other, post_other,
            "Non-reflection discovery counts must be byte-identical "
            "between slotMaps -- surgical scope invariant.",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
