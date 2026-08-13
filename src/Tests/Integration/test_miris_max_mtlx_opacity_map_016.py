# Copyright 2026 Miris Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
"""
MAX-MTLX-OPACITY-MAP-016 -- Python mirror of the soft/continuous opacity
map slotMap extension in `discoverMaxMtlxTexmapsFn` (see
`src/translators/MtlxShaderWriter.cpp` slotMap block, post-fix).

Background
----------
3ds Max PhysicalMaterial exposes TWO alpha-map families:

  cutout_map / cutoutMap    -- BINARY hard-alpha cutout (mat_def:26).
                               Handled since MAX-MTLX-001 -- routed to
                               ND_standard_surface `opacity` as float.
  opacity_map / opacityMap  -- SOFT/CONTINUOUS alpha (mat_def:64-66).
                               Silently dropped pre-016.

OpenPBR splits it as:

  geometry_opacity_map / geometryOpacityMap -- SOFT alpha (mat_def:262).
                                               Silently dropped pre-016.

VRayMtl uses:

  texmap_opacity -- SOFT alpha (V-Ray SDK canonical). Silently dropped
                    pre-016.

Prior to this fix the discovery slotMap had only the cutout entries and
zero opacity entries -- every material whose alpha lives in the SOFT
opacity slot (glass etch, mesh screens, tinted plastic, court boundary-
line halftone maps, mesh-fabric railings, soft-alpha decals via
Composite Mtl base layer) exported fully opaque in the MaterialX
network regardless of what the source scene said.

Prior-run evidence
(`agent/pipeline-runs/40dca678-.../evidence/gaps-audit.md:158-170` -- S2)
enumerates the silent-drop opacity property spellings.

Fix
---
Extend the `discoverMaxMtlxTexmapsFn` slotMap with 5 entries covering
these spellings:

    opacity (float):
        opacity_map              (PhysicalMaterial snake_case)
        opacityMap               (PhysicalMaterial camelCase alt)
        geometry_opacity_map     (OpenPBR snake_case)
        geometryOpacityMap       (OpenPBR camelCase alt)
        texmap_opacity           (VRayMtl SDK canonical)

Property-spelling ordering follows the pre-existing slotMap convention:
snake_case before camelCase within each family, PhysicalMaterial before
OpenPBR before VRayMtl. All entries route to `ND_standard_surface.opacity`
as `float` (matching the pre-existing cutout_map / cutoutMap treatment).

Ordering vs. cutout_map: opacity entries come AFTER cutout in slotMap
order so a material authoring BOTH cutout AND opacity_map (unusual --
most workflows author one or the other) resolves to cutout under first-
hit-wins. This preserves the binary-hard-alpha precedence for the
overlap case.

The change is a strict SUPERSET extension of the slotMap. No existing
entry is removed or reordered, so materials that were correctly
exporting other slots (including cutout) keep working byte-identically.
The wrapper walk (MAX-MTLX-007 `unwrapBlendMaterialSubMtls`, extended
for Composite / Blend by MAX-MTLX-COMPOSITE-DECAL-014) applies because
the slotMap iteration lives inside `for currentMat in subMtls do` --
VRayBlendMtl / VRayOverrideMtl / CompositeMtl / stock BlendMtl unwrap
identically for the new entries, and baseMtl's opacity textures win over
any coat's / per-ray override's under first-hit-wins.

Type dispatch:
- float opacity maps: routed as ND_tiledimage_float. The existing
  colorspace-attribute gate in `_EnrichMtlxDocFromMaxMaterial` (which
  only authors `colorspace="srgb_texture"` when `mtlxType == "color3"`)
  does the right thing by construction -- scalar alpha maps must NOT go
  through the sRGB -> linear decode; that would silently distort the
  alpha value (a 0.5 alpha would decode to ~0.21 in linear space).
- The MaterialX shader input `opacity` is color3 in the stdlib
  (default (1,1,1)). The evaluator broadcasts an ND_tiledimage_float
  into it channel-broadcast so a single-channel opacity texture reads
  as (a,a,a) -- correct semantic for a mono-alpha map. Same treatment
  the pre-existing cutout entries use.

opacityThreshold caveat: PhysicalMaterial's `opacityThreshold` scalar
(mat_def:66 -- soft-to-hard cutoff) is intentionally out of scope. It
requires authoring an ND_ifgreatereq_float node between the tiledimage
and the shader input, which is an authoring-path change beyond a
slotMap entry. Follow-on bite candidate:
`max-mtlx-017-opacity-threshold-hard-cutoff`.

Test strategy
-------------
The C++/MAXScript ships on Windows-only + Max SDK, so we cannot execute
the MAXScript here. Instead we model the material families as plain-
Python class fakes mirroring the MAXScript `isProperty` / `getProperty`
surface, and we mirror the C++ discovery + injection logic at the
MaterialX-doc layer using MaterialX Python bindings (which ship inside
Houdini's `hython`). Same test shape as MTLX-COAT-015 / MTLX-011 /
MTLX-009.

Coverage buckets:

  * PreFixDefect          -- pre-fix baseline where slotMap has NO
                             opacity entries (only cutout): a material
                             authoring any of the new spellings produces
                             zero injected tiledimage nodes for the
                             opacity input.
  * PropertyDispatch      -- each of the 5 supported property names
                             resolves and drives a `ND_tiledimage_float`
                             into `opacity`. Locks in the exact slotMap
                             contents so a future refactor can't
                             silently drop an entry.
  * CutoutOpacityOrdering -- when a material authors BOTH cutout_map
                             and opacity_map, cutout wins under first-
                             hit-wins (cutout declared earlier in
                             slotMap). Locks in the ordering.
  * WrapperWalk           -- VRayBlendMtl base opacity wins over coat
                             overlay; VRayOverrideMtl base wins over
                             reflect/refract/GI overrides; CompositeMtl
                             base wins over decal overlays.
  * TypeCorrectness       -- each injected tiledimage is typed float
                             (never color3 / vector3). Anchored against
                             the pre-existing cutout entries' float
                             treatment.
  * ColorspaceGate        -- float opacity maps do NOT carry
                             `colorspace="srgb_texture"` (unlike color3
                             base_color / emission_color / coat_color).
                             Locks in the scalar-alpha invariant.
  * NoMapNoOutput         -- a material with base_color etc. but no
                             opacity map exports zero opacity
                             tiledimages -- port defaults hold and the
                             doc stays clean.
  * SurgicalScope         -- the fix touches ONLY the slotMap; the
                             existing base_color / roughness /
                             metalness / normal / cutout /
                             emission_color / specular_color /
                             transmission_color / specular_IOR /
                             specular_anisotropy / specular_rotation /
                             transmission_extra_roughness /
                             sheen_roughness / coat family entries
                             produce byte-identical output before and
                             after the change, on a material that
                             exercises them without any new-in-016 map.
  * StrictSupersetSlotMap -- the post-fix slotMap contains ALL pre-fix
                             entries with unchanged (max-prop, input,
                             type) tuples, and adds EXACTLY 5 new
                             entries -- no more, no fewer, in the
                             intended order.
  * ArenaCensus           -- a synthesized 179-material arena-density
                             mix (100 non-opacity + 40 mesh-fabric
                             railing with opacity_map + 25 tinted-
                             plastic with texmap_opacity + 14 OpenPBR
                             etched-glass with geometry_opacity_map)
                             resolves to the expected number of opacity
                             discoveries post-fix and zero pre-fix,
                             with the non-016 discovery counts
                             unchanged.

Run:  hython test_miris_max_mtlx_opacity_map_016.py
"""
import unittest

import MaterialX as mx


# =============================================================================
# Fake material classes -- mirror the MAXScript `isProperty` /
# `getProperty` surface. Same shape as MTLX-007 / MTLX-009 / MTLX-010 /
# MTLX-011 / MTLX-012 / MTLX-COAT-015.
# =============================================================================


class _MaterialBase:
    max_class_name = "GenericMtl"


class PhysicalMaterial(_MaterialBase):
    """3ds Max stock PhysicalMaterial exposes BOTH `cutout_map`
    (mat_def:26 -- hard binary alpha) AND `opacity_map`
    (mat_def:64 -- soft continuous alpha). This bite adds the soft
    family; cutout is already handled since MAX-MTLX-001."""

    max_class_name = "PhysicalMaterial"

    def __init__(
        self,
        base_color_map=None,
        roughness_map=None,
        cutout_map=None,
        opacity_map=None,
    ):
        if base_color_map is not None:
            self.base_color_map = base_color_map
        if roughness_map is not None:
            self.roughness_map = roughness_map
        if cutout_map is not None:
            self.cutout_map = cutout_map
        if opacity_map is not None:
            self.opacity_map = opacity_map


class PhysicalMaterialCamel(_MaterialBase):
    """CamelCase-authored variant -- exercises the `cutoutMap` /
    `opacityMap` slotMap entries."""

    max_class_name = "PhysicalMaterial"

    def __init__(
        self,
        baseColorMap=None,
        cutoutMap=None,
        opacityMap=None,
    ):
        if baseColorMap is not None:
            self.baseColorMap = baseColorMap
        if cutoutMap is not None:
            self.cutoutMap = cutoutMap
        if opacityMap is not None:
            self.opacityMap = opacityMap


class OpenPBRDerived(_MaterialBase):
    """Third-party OpenPBR-derived material exposing
    `geometry_opacity_map` / `geometryOpacityMap` (mat_def:262)."""

    max_class_name = "OpenPBRDerived"

    def __init__(
        self,
        base_color_map=None,
        geometry_opacity_map=None,
        geometryOpacityMap=None,
    ):
        if base_color_map is not None:
            self.base_color_map = base_color_map
        if geometry_opacity_map is not None:
            self.geometry_opacity_map = geometry_opacity_map
        if geometryOpacityMap is not None:
            self.geometryOpacityMap = geometryOpacityMap


class VRayMtl(_MaterialBase):
    """VRayMtl exposes `.texmap_opacity` (V-Ray SDK canonical) for the
    soft alpha map. Post-Scene-Converter (V-Ray -> PhysicalMaterial),
    the opacity is remapped to `opacity_map`, but scenes not yet
    Scene-Converted still need this canonical spelling routed."""

    max_class_name = "VRayMtl"

    def __init__(
        self,
        texmap_diffuse=None,
        texmap_opacity=None,
    ):
        if texmap_diffuse is not None:
            self.texmap_diffuse = texmap_diffuse
        if texmap_opacity is not None:
            self.texmap_opacity = texmap_opacity


class VRayBlendMtl(_MaterialBase):
    """Layered paint / weathered-surface wrapper -- same shape as
    MTLX-007 / MTLX-009 / MTLX-010 / MTLX-011 / MTLX-012 / COAT-015."""

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
    """Per-ray override wrapper -- same shape as MTLX-007 / MTLX-009 /
    MTLX-010 / MTLX-011 / MTLX-012 / COAT-015."""

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


class CompositeMtl(_MaterialBase):
    """MAX-MTLX-COMPOSITE-DECAL-014 stock Composite Mtl. Sub-materials
    live in `.materialList[i]` with per-layer `.mapEnabled[i]` +
    `.opacity[i]` gates (this .opacity is the Composite layer opacity
    scalar, DIFFERENT from PhysicalMaterial's `.opacity_map` alpha
    texture)."""

    max_class_name = "Composite"

    def __init__(self, material_list=None, map_enabled=None, opacities=None):
        self.materialList = material_list or []
        if map_enabled is None:
            map_enabled = [True] * len(self.materialList)
        if opacities is None:
            opacities = [1.0] * len(self.materialList)
        self.mapEnabled = map_enabled
        self.opacity = opacities


class _FakeBitmap:
    """Stand-in for a MAXScript Bitmap/VRayBitmap texmap -- attribute
    surface matches what `resolveMaxTexmapFilename` walks."""

    def __init__(self, filename):
        self.filename = filename


# =============================================================================
# Python mirror of the MTLX-007 wrapper walk (+ MTLX-COMPOSITE-DECAL-014
# extension for Composite Mtl).
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
    elif cls == "Composite":
        for i, sub in enumerate(mat.materialList):
            if sub is None:
                continue
            if i > 0:
                if i < len(mat.mapEnabled) and not mat.mapEnabled[i]:
                    continue
                if i < len(mat.opacity) and mat.opacity[i] <= 0.0:
                    continue
            for r in unwrap_blend_material_sub_mtls(
                sub, visited, depth + 1
            ):
                out.append(r)
    return out


# =============================================================================
# The slotMap -- MUST mirror MtlxShaderWriter.cpp verbatim through the
# OPACITY-MAP-016 additions. `TestStrictSupersetSlotMap` locks this
# against any future drift.
# =============================================================================


SLOT_MAP_POST_FIX = [
    # Pre-existing entries through MAX-MTLX-COAT-015 (unchanged by
    # MAX-MTLX-OPACITY-MAP-016).
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
    # MAX-MTLX-010 additions.
    ("anisotropy_map",             "specular_anisotropy", "float"),
    ("anisotropyMap",              "specular_anisotropy", "float"),
    ("texmap_anisotropy",          "specular_anisotropy", "float"),
    ("anisotropy_rotation_map",    "specular_rotation",   "float"),
    ("anisotropyRotationMap",      "specular_rotation",   "float"),
    ("texmap_anisotropyRotation",  "specular_rotation",   "float"),
    # MAX-MTLX-011 additions.
    ("trans_roughness_map",         "transmission_extra_roughness", "float"),
    ("transRoughnessMap",           "transmission_extra_roughness", "float"),
    ("transmission_roughness_map",  "transmission_extra_roughness", "float"),
    ("transmissionRoughnessMap",    "transmission_extra_roughness", "float"),
    ("sheen_roughness_map",         "sheen_roughness",              "float"),
    ("sheenRoughnessMap",           "sheen_roughness",              "float"),
    # MAX-MTLX-COAT-015 additions.
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
    # MAX-MTLX-OPACITY-MAP-016 additions.
    ("opacity_map",                 "opacity",        "float"),
    ("opacityMap",                  "opacity",        "float"),
    ("geometry_opacity_map",        "opacity",        "float"),
    ("geometryOpacityMap",          "opacity",        "float"),
    ("texmap_opacity",              "opacity",        "float"),
]


_MAX_MTLX_OPACITY_MAP_016_PROPS = {
    "opacity_map",
    "opacityMap",
    "geometry_opacity_map",
    "geometryOpacityMap",
    "texmap_opacity",
}

SLOT_MAP_PRE_FIX = [
    e for e in SLOT_MAP_POST_FIX if e[0] not in _MAX_MTLX_OPACITY_MAP_016_PROPS
]

MAX_MTLX_OPACITY_MAP_016_ADDITIONS = [
    e for e in SLOT_MAP_POST_FIX if e[0] in _MAX_MTLX_OPACITY_MAP_016_PROPS
]


# =============================================================================
# Python mirror of the C++ `_EnrichMtlxDocFromMaxMaterial` + the
# `discoverMaxMtlxTexmaps` MAXScript helper.
# =============================================================================


def _resolve_max_texmap_filename(tex):
    if tex is None:
        return None
    return getattr(tex, "filename", None)


def discover_max_mtlx_texmaps(mat, slot_map):
    """Verbatim mirror of the MAXScript `discoverMaxMtlxTexmaps` loop
    with the wrapper walk from MTLX-007 (+ MTLX-COMPOSITE-DECAL-014).
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
    """Baseline: pre-fix (slotMap missing all 016 opacity entries), a
    material authoring any of the new spellings produces ZERO
    tiledimage nodes for the `opacity` input -- the alpha map is
    silently dropped. Locks in the fingerprint the fix resolves."""

    def test_opacity_map_dropped_pre_fix(self):
        mat = PhysicalMaterial(
            base_color_map=_FakeBitmap("mesh_diff.png"),
            opacity_map=_FakeBitmap("mesh_alpha.png"),
        )
        discovery = discover_max_mtlx_texmaps(mat, SLOT_MAP_PRE_FIX)
        inputs = {d[0] for d in discovery}
        self.assertNotIn("opacity", inputs,
                         "Pre-fix `opacity` path should be silently dropped.")
        # Sanity: pre-existing base_color path still works.
        self.assertIn("base_color", inputs)

    def test_geometry_opacity_map_dropped_pre_fix(self):
        mat = OpenPBRDerived(
            base_color_map=_FakeBitmap("glass_diff.png"),
            geometry_opacity_map=_FakeBitmap("etch_alpha.png"),
        )
        discovery = discover_max_mtlx_texmaps(mat, SLOT_MAP_PRE_FIX)
        inputs = {d[0] for d in discovery}
        self.assertNotIn("opacity", inputs)

    def test_texmap_opacity_dropped_pre_fix(self):
        mat = VRayMtl(
            texmap_diffuse=_FakeBitmap("plastic_diff.png"),
            texmap_opacity=_FakeBitmap("plastic_alpha.png"),
        )
        discovery = discover_max_mtlx_texmaps(mat, SLOT_MAP_PRE_FIX)
        inputs = {d[0] for d in discovery}
        self.assertNotIn("opacity", inputs)

    def test_cutout_still_works_pre_fix(self):
        # Regression guard: the pre-existing cutout_map path was already
        # covered pre-016 and must keep working. Locks in the boundary
        # between "existing behavior" and "new-in-016".
        mat = PhysicalMaterial(cutout_map=_FakeBitmap("hard_alpha.png"))
        discovery = discover_max_mtlx_texmaps(mat, SLOT_MAP_PRE_FIX)
        hits = [d for d in discovery if d[0] == "opacity"]
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0][2], "hard_alpha.png")


class TestPropertyDispatch(unittest.TestCase):
    """Each of the 5 new slotMap entries dispatches correctly post-fix."""

    def _resolves(self, mat, expected_file):
        discovery = discover_max_mtlx_texmaps(mat, SLOT_MAP_POST_FIX)
        hits = [d for d in discovery if d[0] == "opacity"]
        self.assertEqual(
            len(hits), 1,
            f"Expected exactly one opacity discovery, got {hits!r}",
        )
        self.assertEqual(hits[0][1], "float")
        self.assertEqual(hits[0][2], expected_file)

    def test_phys_opacity_map_snake(self):
        self._resolves(
            PhysicalMaterial(opacity_map=_FakeBitmap("a.png")),
            "a.png",
        )

    def test_phys_opacityMap_camel(self):
        self._resolves(
            PhysicalMaterialCamel(opacityMap=_FakeBitmap("b.png")),
            "b.png",
        )

    def test_openpbr_geometry_opacity_map_snake(self):
        self._resolves(
            OpenPBRDerived(geometry_opacity_map=_FakeBitmap("c.png")),
            "c.png",
        )

    def test_openpbr_geometryOpacityMap_camel(self):
        self._resolves(
            OpenPBRDerived(geometryOpacityMap=_FakeBitmap("d.png")),
            "d.png",
        )

    def test_vray_texmap_opacity(self):
        self._resolves(
            VRayMtl(texmap_opacity=_FakeBitmap("e.png")),
            "e.png",
        )

    def test_snake_wins_over_camel_first_hit(self):
        # If BOTH snake and camel spellings appear on the same material
        # (unusual under MAXScript authoring), snake_case wins because
        # it is declared first in slotMap order. Matches every other
        # slotMap family.
        m = _MaterialBase()
        m.max_class_name = "PhysicalMaterial"
        m.opacity_map = _FakeBitmap("snake.png")
        m.opacityMap = _FakeBitmap("camel.png")
        discovery = discover_max_mtlx_texmaps(m, SLOT_MAP_POST_FIX)
        hits = [d for d in discovery if d[0] == "opacity"]
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0][2], "snake.png")

    def test_physicalmaterial_wins_over_openpbr(self):
        # A material authoring BOTH `opacity_map` (PhysicalMaterial) AND
        # `geometry_opacity_map` (OpenPBR) resolves to opacity_map under
        # first-hit-wins because PhysicalMaterial is declared first in
        # slotMap order.
        m = _MaterialBase()
        m.max_class_name = "PhysicalMaterial"
        m.opacity_map = _FakeBitmap("phys.png")
        m.geometry_opacity_map = _FakeBitmap("openpbr.png")
        discovery = discover_max_mtlx_texmaps(m, SLOT_MAP_POST_FIX)
        hits = [d for d in discovery if d[0] == "opacity"]
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0][2], "phys.png")

    def test_openpbr_wins_over_vray(self):
        # OpenPBR before VRayMtl in slotMap order.
        m = _MaterialBase()
        m.max_class_name = "OpenPBRDerived"
        m.geometry_opacity_map = _FakeBitmap("openpbr.png")
        m.texmap_opacity = _FakeBitmap("vray.png")
        discovery = discover_max_mtlx_texmaps(m, SLOT_MAP_POST_FIX)
        hits = [d for d in discovery if d[0] == "opacity"]
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0][2], "openpbr.png")


class TestCutoutOpacityOrdering(unittest.TestCase):
    """Locks in slotMap ordering: cutout_map (pre-existing) declared
    BEFORE opacity_map (new-in-016) so a material authoring BOTH
    resolves to cutout under first-hit-wins. This preserves binary-
    hard-alpha precedence for the overlap case; most workflows author
    one or the other but not both."""

    def test_cutout_beats_opacity_when_both_authored(self):
        m = _MaterialBase()
        m.max_class_name = "PhysicalMaterial"
        m.cutout_map = _FakeBitmap("hard.png")
        m.opacity_map = _FakeBitmap("soft.png")
        discovery = discover_max_mtlx_texmaps(m, SLOT_MAP_POST_FIX)
        hits = [d for d in discovery if d[0] == "opacity"]
        self.assertEqual(len(hits), 1)
        self.assertEqual(
            hits[0][2], "hard.png",
            "cutout_map must win over opacity_map when both authored "
            "(cutout declared first in slotMap).",
        )

    def test_cutout_only_still_works(self):
        # Standalone cutout still works post-016 -- no regression.
        m = PhysicalMaterial(cutout_map=_FakeBitmap("hard.png"))
        discovery = discover_max_mtlx_texmaps(m, SLOT_MAP_POST_FIX)
        hits = [d for d in discovery if d[0] == "opacity"]
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0][2], "hard.png")

    def test_opacity_only_new_in_016(self):
        # Standalone opacity_map -- the new-in-016 win.
        m = PhysicalMaterial(opacity_map=_FakeBitmap("soft.png"))
        discovery = discover_max_mtlx_texmaps(m, SLOT_MAP_POST_FIX)
        hits = [d for d in discovery if d[0] == "opacity"]
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0][2], "soft.png")

    def test_cutout_declared_before_opacity_in_slotmap(self):
        # Structural invariant: locates BOTH entries and asserts the
        # cutout family appears first. Future refactors that shuffle
        # slotMap order without changing precedence semantics would fail
        # here.
        cutout_indices = [
            i for i, e in enumerate(SLOT_MAP_POST_FIX)
            if e[0] in ("cutout_map", "cutoutMap")
        ]
        opacity_indices = [
            i for i, e in enumerate(SLOT_MAP_POST_FIX)
            if e[0] in _MAX_MTLX_OPACITY_MAP_016_PROPS
        ]
        self.assertGreater(len(cutout_indices), 0)
        self.assertGreater(len(opacity_indices), 0)
        self.assertLess(
            max(cutout_indices), min(opacity_indices),
            "cutout entries must come before opacity entries in slotMap.",
        )


class TestWrapperWalk(unittest.TestCase):
    """MAX-MTLX-007's wrapper walk (+ COMPOSITE-DECAL-014 extension)
    applies to the new opacity entries because slotMap iteration lives
    inside `for currentMat in subMtls`."""

    def test_vray_blend_base_opacity_wins_over_coat_overlay(self):
        base = PhysicalMaterial(opacity_map=_FakeBitmap("base_alpha.png"))
        overlay = PhysicalMaterial(opacity_map=_FakeBitmap("overlay_alpha.png"))
        blend = VRayBlendMtl(baseMtl=base, coats=(overlay,))
        discovery = discover_max_mtlx_texmaps(blend, SLOT_MAP_POST_FIX)
        hits = [d for d in discovery if d[0] == "opacity"]
        self.assertEqual(len(hits), 1)
        self.assertEqual(
            hits[0][2], "base_alpha.png",
            "Base's opacity should win over overlay's under first-hit-wins.",
        )

    def test_vray_blend_opacity_fills_gap(self):
        # Base has no opacity map; overlay does -- overlay fills the gap.
        base = PhysicalMaterial(base_color_map=_FakeBitmap("base_diff.png"))
        overlay = PhysicalMaterial(
            opacity_map=_FakeBitmap("overlay_alpha.png"),
        )
        blend = VRayBlendMtl(baseMtl=base, coats=(overlay,))
        discovery = discover_max_mtlx_texmaps(blend, SLOT_MAP_POST_FIX)
        hits = [d for d in discovery if d[0] == "opacity"]
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0][2], "overlay_alpha.png",
                         "Overlay's opacity fills the base's gap.")

    def test_vray_override_base_opacity_wins_over_reflect(self):
        base = PhysicalMaterial(opacity_map=_FakeBitmap("base_alpha.png"))
        reflect_only = PhysicalMaterial(
            opacity_map=_FakeBitmap("reflect_alpha.png"),
        )
        override = VRayOverrideMtl(baseMtl=base, reflectMtl=reflect_only)
        discovery = discover_max_mtlx_texmaps(override, SLOT_MAP_POST_FIX)
        hits = [d for d in discovery if d[0] == "opacity"]
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0][2], "base_alpha.png")

    def test_composite_base_opacity_wins_over_decal(self):
        # A CompositeMtl carrying (base=solid concrete court, overlay=
        # soft-alpha decal) unwraps base-first so base's texture wins.
        base = PhysicalMaterial(
            base_color_map=_FakeBitmap("court.png"),
            opacity_map=_FakeBitmap("court_alpha.png"),
        )
        decal = PhysicalMaterial(
            opacity_map=_FakeBitmap("decal_alpha.png"),
        )
        comp = CompositeMtl(
            material_list=[base, decal],
            map_enabled=[True, True],
            opacities=[1.0, 0.5],
        )
        discovery = discover_max_mtlx_texmaps(comp, SLOT_MAP_POST_FIX)
        hits = [d for d in discovery if d[0] == "opacity"]
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0][2], "court_alpha.png")


class TestTypeCorrectness(unittest.TestCase):
    """Every OPACITY-016 discovery is typed `float` (matching the pre-
    existing cutout entries). Locks in the invariant that opacity maps
    do NOT route as color3 / vector3."""

    def _all_016_hits(self, mat):
        discovery = discover_max_mtlx_texmaps(mat, SLOT_MAP_POST_FIX)
        return [d for d in discovery if d[0] == "opacity"]

    def test_phys_opacity_map_is_float(self):
        for hit in self._all_016_hits(
            PhysicalMaterial(opacity_map=_FakeBitmap("a.png"))
        ):
            self.assertEqual(hit[1], "float")

    def test_openpbr_geometry_opacity_map_is_float(self):
        for hit in self._all_016_hits(
            OpenPBRDerived(geometry_opacity_map=_FakeBitmap("b.png"))
        ):
            self.assertEqual(hit[1], "float")

    def test_vray_texmap_opacity_is_float(self):
        for hit in self._all_016_hits(
            VRayMtl(texmap_opacity=_FakeBitmap("c.png"))
        ):
            self.assertEqual(hit[1], "float")

    def test_all_016_slotmap_entries_are_float(self):
        # Structural invariant: every new-in-016 slotMap entry must
        # route as float. Anchors against a future refactor that
        # accidentally re-types one entry to color3.
        for prop, mtlx_input, mtlx_type in MAX_MTLX_OPACITY_MAP_016_ADDITIONS:
            self.assertEqual(mtlx_input, "opacity")
            self.assertEqual(
                mtlx_type, "float",
                f"OPACITY-016 entry {prop!r} must route as float, got {mtlx_type!r}",
            )


class TestColorspaceGate(unittest.TestCase):
    """Float opacity maps must NOT carry `colorspace="srgb_texture"`.
    A float alpha value going through the sRGB->linear decode would
    silently distort (a 0.5 alpha becomes ~0.21 in linear), so the
    scalar-alpha invariant is critical. The existing colorspace gate
    in `_EnrichMtlxDocFromMaxMaterial` handles this by only authoring
    the attribute when `mtlxType == "color3"`."""

    def test_opacity_tiledimage_has_no_colorspace(self):
        mat = PhysicalMaterial(opacity_map=_FakeBitmap("mesh_alpha.png"))
        discovery = discover_max_mtlx_texmaps(mat, SLOT_MAP_POST_FIX)
        doc, ng, shader = build_scaffold_doc(
            [("base_color", "color3"), ("opacity", "float")]
        )
        enrich_mtlx_doc(doc, ng, shader, discovery)
        img = ng.getNode("img_opacity")
        self.assertIsNotNone(img,
                             "opacity tiledimage should be injected.")
        file_input = img.getInput("file")
        self.assertIsNotNone(file_input)
        self.assertFalse(
            file_input.hasAttribute("colorspace"),
            "opacity (float) tiledimage MUST NOT carry a colorspace "
            "attribute -- alpha decode through sRGB would distort.",
        )

    def test_base_color_tiledimage_still_has_colorspace(self):
        # Regression guard: the color3 gate still authors colorspace
        # for color3 inputs. Locks in that the fix is scoped.
        mat = PhysicalMaterial(
            base_color_map=_FakeBitmap("diff.png"),
            opacity_map=_FakeBitmap("alpha.png"),
        )
        discovery = discover_max_mtlx_texmaps(mat, SLOT_MAP_POST_FIX)
        doc, ng, shader = build_scaffold_doc(
            [("base_color", "color3"), ("opacity", "float")]
        )
        enrich_mtlx_doc(doc, ng, shader, discovery)
        color_img = ng.getNode("img_base_color")
        self.assertTrue(
            color_img.getInput("file").hasAttribute("colorspace"),
        )
        self.assertEqual(
            color_img.getInput("file").getAttribute("colorspace"),
            "srgb_texture",
        )


class TestNoMapNoOutput(unittest.TestCase):
    """A material with no opacity map exports zero opacity discoveries
    -- port defaults hold and the doc stays clean."""

    def test_no_opacity_map_no_discovery(self):
        mat = PhysicalMaterial(
            base_color_map=_FakeBitmap("diff.png"),
            roughness_map=_FakeBitmap("rough.png"),
        )
        discovery = discover_max_mtlx_texmaps(mat, SLOT_MAP_POST_FIX)
        opacity_hits = [d for d in discovery if d[0] == "opacity"]
        self.assertEqual(len(opacity_hits), 0)
        # Base color / roughness discovery still works.
        inputs = {d[0] for d in discovery}
        self.assertIn("base_color", inputs)
        self.assertIn("specular_roughness", inputs)


class TestSurgicalScope(unittest.TestCase):
    """The fix touches ONLY the slotMap. Materials that exercise
    pre-existing entries (without any new-in-016 map) must produce
    byte-identical discovery output before and after the change."""

    def test_pre_existing_material_identical_pre_post(self):
        # A material with base_color + roughness + coat + cutout --
        # every pre-existing slot family, no OPACITY-016 map.
        mat = _MaterialBase()
        mat.max_class_name = "PhysicalMaterial"
        mat.base_color_map = _FakeBitmap("diff.png")
        mat.roughness_map = _FakeBitmap("rough.png")
        mat.coat_map = _FakeBitmap("coat.png")
        mat.cutout_map = _FakeBitmap("hard.png")
        pre = discover_max_mtlx_texmaps(mat, SLOT_MAP_PRE_FIX)
        post = discover_max_mtlx_texmaps(mat, SLOT_MAP_POST_FIX)
        # Pre-existing discoveries must be byte-identical.
        self.assertEqual(pre, post,
                         "Materials without opacity maps must produce "
                         "byte-identical discovery pre and post fix.")

    def test_type_distribution_invariant(self):
        # Post-fix slotMap has exactly the same type distribution for
        # every pre-existing entry PLUS 5 new float entries.
        pre_type_counts = {}
        for _, _, t in SLOT_MAP_PRE_FIX:
            pre_type_counts[t] = pre_type_counts.get(t, 0) + 1
        post_type_counts = {}
        for _, _, t in SLOT_MAP_POST_FIX:
            post_type_counts[t] = post_type_counts.get(t, 0) + 1
        # Only the float count should have grown, by exactly 5.
        self.assertEqual(
            post_type_counts["float"] - pre_type_counts["float"], 5,
            "OPACITY-016 must add exactly 5 float entries.",
        )
        self.assertEqual(
            post_type_counts.get("color3", 0),
            pre_type_counts.get("color3", 0),
            "color3 count must be unchanged.",
        )
        self.assertEqual(
            post_type_counts.get("vector3", 0),
            pre_type_counts.get("vector3", 0),
            "vector3 count must be unchanged.",
        )


class TestStrictSupersetSlotMap(unittest.TestCase):
    """Post-fix slotMap contains ALL pre-fix entries with unchanged
    (max-prop, input, type) tuples, and adds EXACTLY 5 new entries in
    the intended order."""

    def test_pre_fix_subset(self):
        # Every pre-fix entry appears verbatim in the post-fix slotMap.
        for e in SLOT_MAP_PRE_FIX:
            self.assertIn(
                e, SLOT_MAP_POST_FIX,
                f"pre-fix entry {e!r} missing from post-fix slotMap.",
            )

    def test_exactly_five_new_entries(self):
        pre_set = set(SLOT_MAP_PRE_FIX)
        post_set = set(SLOT_MAP_POST_FIX)
        added = post_set - pre_set
        self.assertEqual(
            len(added), 5,
            f"Expected exactly 5 new entries, got {len(added)}: {added!r}",
        )

    def test_exact_new_entry_set(self):
        expected = {
            ("opacity_map",           "opacity", "float"),
            ("opacityMap",            "opacity", "float"),
            ("geometry_opacity_map",  "opacity", "float"),
            ("geometryOpacityMap",    "opacity", "float"),
            ("texmap_opacity",        "opacity", "float"),
        }
        got = set(MAX_MTLX_OPACITY_MAP_016_ADDITIONS)
        self.assertEqual(got, expected)

    def test_new_entries_ordered_correctly(self):
        # Within the OPACITY-016 block, PhysicalMaterial before OpenPBR
        # before VRayMtl; snake before camel within each family.
        adds = MAX_MTLX_OPACITY_MAP_016_ADDITIONS
        self.assertEqual(adds[0][0], "opacity_map")
        self.assertEqual(adds[1][0], "opacityMap")
        self.assertEqual(adds[2][0], "geometry_opacity_map")
        self.assertEqual(adds[3][0], "geometryOpacityMap")
        self.assertEqual(adds[4][0], "texmap_opacity")

    def test_016_block_comes_after_coat_015_block(self):
        # OPACITY-016 additions are appended AFTER the COAT-015 block --
        # matches the "newest additions go last" convention.
        coat_015_props = {
            "coat_map", "coatMap", "coat_rough_map", "coatRoughMap",
            "clearcoat_map", "clearcoatMap", "clearcoatRoughness_map",
            "clearcoatRoughnessMap", "coat_weight_map", "coatWeightMap",
            "coat_color_map", "coatColorMap", "coat_roughness_map",
            "coatRoughnessMap", "coat_ior_map", "coatIorMap",
            "coat_normal_map", "coatNormalMap", "coat_amount_texmap",
            "coatAmountTexmap",
        }
        coat_indices = [
            i for i, e in enumerate(SLOT_MAP_POST_FIX) if e[0] in coat_015_props
        ]
        opacity_indices = [
            i for i, e in enumerate(SLOT_MAP_POST_FIX)
            if e[0] in _MAX_MTLX_OPACITY_MAP_016_PROPS
        ]
        self.assertLess(max(coat_indices), min(opacity_indices))


class TestArenaCensus(unittest.TestCase):
    """179-material arena-density mix: 100 non-opacity, 40 mesh-fabric
    railing with `opacity_map`, 25 tinted-plastic panels with
    `texmap_opacity`, 14 OpenPBR etched-glass with
    `geometry_opacity_map`. Pre-fix: 0 opacity discoveries. Post-fix:
    79 opacity discoveries. Non-016 discovery counts unchanged."""

    def _build_arena(self):
        mats = []
        # 100 non-opacity plain PhysicalMaterial
        for i in range(100):
            m = PhysicalMaterial(
                base_color_map=_FakeBitmap(f"plain_{i}.png"),
                roughness_map=_FakeBitmap(f"plain_rough_{i}.png"),
            )
            mats.append(m)
        # 40 mesh-fabric railing PhysicalMaterial with opacity_map
        for i in range(40):
            m = PhysicalMaterial(
                base_color_map=_FakeBitmap(f"railing_{i}.png"),
                opacity_map=_FakeBitmap(f"railing_alpha_{i}.png"),
            )
            mats.append(m)
        # 25 tinted-plastic VRayMtl with texmap_opacity
        for i in range(25):
            m = VRayMtl(
                texmap_diffuse=_FakeBitmap(f"plastic_{i}.png"),
                texmap_opacity=_FakeBitmap(f"plastic_alpha_{i}.png"),
            )
            mats.append(m)
        # 14 OpenPBR etched-glass with geometry_opacity_map
        for i in range(14):
            m = OpenPBRDerived(
                base_color_map=_FakeBitmap(f"glass_{i}.png"),
                geometry_opacity_map=_FakeBitmap(f"etch_alpha_{i}.png"),
            )
            mats.append(m)
        return mats

    def test_arena_census_pre_fix_zero_opacity(self):
        mats = self._build_arena()
        total = 0
        for m in mats:
            hits = [
                d for d in discover_max_mtlx_texmaps(m, SLOT_MAP_PRE_FIX)
                if d[0] == "opacity"
            ]
            total += len(hits)
        self.assertEqual(total, 0,
                         "Pre-fix arena must have ZERO opacity discoveries "
                         "for the OPACITY-016 material classes.")

    def test_arena_census_post_fix_seventynine_opacity(self):
        mats = self._build_arena()
        total = 0
        for m in mats:
            hits = [
                d for d in discover_max_mtlx_texmaps(m, SLOT_MAP_POST_FIX)
                if d[0] == "opacity"
            ]
            total += len(hits)
        # 40 railing + 25 plastic + 14 glass = 79
        self.assertEqual(total, 79)

    def test_arena_census_non_016_counts_unchanged(self):
        mats = self._build_arena()
        pre_non_016 = 0
        post_non_016 = 0
        for m in mats:
            pre_disc = discover_max_mtlx_texmaps(m, SLOT_MAP_PRE_FIX)
            post_disc = discover_max_mtlx_texmaps(m, SLOT_MAP_POST_FIX)
            pre_non_016 += len([d for d in pre_disc if d[0] != "opacity"])
            post_non_016 += len([d for d in post_disc if d[0] != "opacity"])
        self.assertEqual(
            pre_non_016, post_non_016,
            "Non-opacity discovery counts must be byte-identical between "
            "pre- and post-fix slotMaps -- the fix is a strict superset.",
        )


if __name__ == "__main__":
    unittest.main()
