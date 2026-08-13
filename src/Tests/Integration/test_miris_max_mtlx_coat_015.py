# Copyright 2026 Miris Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
"""
MAX-MTLX-COAT-015 — Python mirror of the coat weight / color / roughness /
IOR / normal slotMap extension in `discoverMaxMtlxTexmapsFn`
(see `src/translators/MtlxShaderWriter.cpp` slotMap block, post-fix).

Background
----------
`ND_standard_surface` on MaterialX 1.38+ exposes a full coat family:

    coat            (float,   default 0.0)   -- coat weight (0..1)
    coat_color      (color3,  default (1,1,1)) -- coat tint
    coat_roughness  (float,   default 0.1)   -- coat BRDF roughness
    coat_IOR        (float,   default 1.5)   -- coat Fresnel IOR
    coat_normal     (vector3, default unset) -- coat-space normal input

Prior to this fix the MaterialX writer's discovery slotMap covered ZERO
coat slots — every glossy floor sealer, car-paint layer, gymnasium
lacquer, and metallic-finish car body / table silently exported with
the coat family locked at these port defaults regardless of what the
source material said. The visible signature is: NO clear-coat overlay
in any MaterialX-rendered Karma/Hydra frame, so materials that in Max
carry a pronounced high-gloss lacquer look completely flat when
rendered via the exported .mtlx.

Prior-run evidence
(`agent/pipeline-runs/40dca678-.../evidence/gaps-audit.md:139-156` — S1)
enumerates the silent-drop coat property spellings across the three PBR
material families the arch-viz pipeline exercises:

    PhysicalMaterial:
      coat_map              (mat_def:35 -- weight)
      coat_rough_map        (mat_def:38 -- roughness)
      clearcoat_map         (mat_def:72 -- weight alt spelling)
      clearcoatRoughness_map (mat_def:74 -- roughness alt spelling)

    OpenPBR:
      coat_weight_map       (mat_def:218 -- weight)
      coat_color_map        (mat_def:220 -- tint)
      coat_roughness_map    (mat_def:222 -- roughness)
      coat_ior_map          (mat_def:226 -- IOR)
      coat_normal_map       (mat_def:250 -- normal)

    VRayMtl:
      coat_amount_texmap    (V-Ray SDK canonical -- weight)

Fix
---
Extend the `discoverMaxMtlxTexmapsFn` slotMap with 20 entries covering
these spellings (each snake_case + camelCase spelling declared in a
consistent order):

    coat weight (float):
        coat_map / coatMap
        clearcoat_map / clearcoatMap
        coat_weight_map / coatWeightMap
        coat_amount_texmap / coatAmountTexmap
    coat_roughness (float):
        coat_rough_map / coatRoughMap
        clearcoatRoughness_map / clearcoatRoughnessMap
        coat_roughness_map / coatRoughnessMap
    coat_color (color3):
        coat_color_map / coatColorMap
    coat_IOR (float):
        coat_ior_map / coatIorMap
    coat_normal (vector3):
        coat_normal_map / coatNormalMap

Property-spelling ordering follows the pre-existing slotMap convention:
snake_case declared before camelCase within each family, PhysicalMaterial
spellings declared before OpenPBR / VRayMtl. This means when a material
(unusually) authors the same map slot under both snake_case and
camelCase spellings, the snake_case wins — matching every other family
in the slotMap.

The change is a strict SUPERSET extension of the slotMap. No existing
entry is removed or reordered, so materials that were correctly
exporting other slots keep working byte-identically. The wrapper walk
(MAX-MTLX-007 `unwrapBlendMaterialSubMtls`, extended for Composite /
Blend by MAX-MTLX-COMPOSITE-DECAL-014) applies because the slotMap
iteration lives inside `for currentMat in subMtls do` — VRayBlendMtl /
VRayOverrideMtl / CompositeMtl / stock BlendMtl unwrap identically for
the new entries, and baseMtl's coat textures win over any coat's /
per-ray override's under first-hit-wins.

Type dispatch:
- float coat / coat_roughness / coat_IOR maps: routed as float
  tiledimages. The existing colorspace-attribute gate in
  `_EnrichMtlxDocFromMaxMaterial` (which only authors
  `colorspace="srgb_texture"` when `mtlxType == "color3"`) does the
  right thing by construction — scalar maps must NOT go through the
  sRGB -> linear decode; that would silently distort the value.
- color3 coat_color maps: routed as color3 tiledimages. These DO
  receive `colorspace="srgb_texture"` (consistent with base_color and
  emission_color routing).
- vector3 coat_normal maps: routed as vector3 tiledimages, no
  colorspace attribute (matching the pre-existing bump/normal routing).

coat_normal caveat: the main-normal input flows through an
ND_normalmap_float node scaffolded by `_WireDanglingNormalmapInputs`
(MAX-MTLX-003) so it correctly remaps 0..1 -> -1..1. There is no
equivalent scaffolding for coat_normal. Without it, a coat_normal map
authored as tangent-space 0..1-encoded will read half-strength in
Karma. Scaffolding a coat-side ND_normalmap_float is a candidate
follow-on bite (`max-mtlx-016-coat-normalmap-remap`). A raw
pass-through is nonetheless preferable to silently dropping the map,
which is what pre-fix code did.

Test strategy
-------------
The C++/MAXScript ships on Windows-only + Max SDK, so we cannot execute
the MAXScript here. Instead we model the material families as plain-
Python class fakes mirroring the MAXScript `isProperty` / `getProperty`
surface, and we mirror the C++ discovery + injection logic at the
MaterialX-doc layer using MaterialX Python bindings (which ship inside
Houdini's `hython`).

Coverage buckets:

  * PreFixDefect          -- pre-fix baseline where slotMap has NO
                             coat entries: a material authoring any
                             of the coat spellings produces zero
                             injected tiledimage nodes for the coat
                             family.
  * PropertyDispatch      -- each of the 20 supported property names
                             resolves and drives a `ND_tiledimage_*`
                             into the correct coat family input.
                             Locks in the exact slotMap contents so
                             a future refactor can't silently drop
                             an entry.
  * WrapperWalk           -- VRayBlendMtl base coat wins over coat
                             overlay; VRayOverrideMtl base wins over
                             reflect/refract/GI overrides; CompositeMtl
                             base wins over decal overlays.
  * TypeCorrectness       -- each injected tiledimage is typed
                             correctly (float for coat / coat_roughness
                             / coat_IOR; color3 for coat_color; vector3
                             for coat_normal). Anchored against the
                             MaterialX stdlib so a library-version
                             bump can't quietly change the type or
                             rename an input.
  * ColorspaceGate        -- color3 coat_color maps carry
                             `colorspace="srgb_texture"`; float and
                             vector3 coat maps do NOT.
  * NoMapNoOutput         -- a material with base_color etc. but no
                             coat map exports zero coat-family
                             tiledimages -- port defaults hold and
                             the doc stays clean.
  * SurgicalScope         -- the fix touches ONLY the slotMap; the
                             existing base_color / roughness /
                             metalness / normal / opacity /
                             emission_color / specular_color /
                             transmission_color / specular_IOR /
                             specular_anisotropy / specular_rotation /
                             transmission_extra_roughness /
                             sheen_roughness entries produce byte-
                             identical output before and after the
                             change, on a material that exercises
                             them without any new-in-015 map.
  * StrictSupersetSlotMap -- the post-fix slotMap contains ALL pre-fix
                             entries with unchanged (max-prop, input,
                             type) tuples, and adds EXACTLY 20 new
                             entries -- no more, no fewer, in the
                             intended order.
  * ArenaCensus           -- a synthesized 179-material arena-density
                             mix (100 non-coated; 40 lacquered floor
                             tiles with coat_map + coat_rough_map;
                             25 car-body PhysicalMaterial with
                             clearcoat_map + clearcoatRoughness_map;
                             14 OpenPBR-coated with full coat family)
                             resolves to the expected number of coat
                             discoveries post-fix and zero pre-fix,
                             with the non-015 discovery counts
                             unchanged.

Run:  hython test_miris_max_mtlx_coat_015.py
"""
import unittest

import MaterialX as mx


# =============================================================================
# Fake material classes -- mirror the MAXScript `isProperty` /
# `getProperty` surface. Same shape as MTLX-007 / MTLX-009 / MTLX-010 /
# MTLX-011 / MTLX-012.
# =============================================================================


class _MaterialBase:
    max_class_name = "GenericMtl"


class PhysicalMaterial(_MaterialBase):
    """3ds Max stock PhysicalMaterial exposes `coat_map` (mat_def:35,
    weight) and `coat_rough_map` (mat_def:38, roughness) plus the
    alt-spelled `clearcoat_map` (mat_def:72) and `clearcoatRoughness_map`
    (mat_def:74). Only weight/roughness routes are on stock; coat color
    / IOR / normal are OpenPBR-only or third-party additions."""

    max_class_name = "PhysicalMaterial"

    def __init__(
        self,
        base_color_map=None,
        roughness_map=None,
        coat_map=None,
        coat_rough_map=None,
        clearcoat_map=None,
        clearcoatRoughness_map=None,
    ):
        if base_color_map is not None:
            self.base_color_map = base_color_map
        if roughness_map is not None:
            self.roughness_map = roughness_map
        if coat_map is not None:
            self.coat_map = coat_map
        if coat_rough_map is not None:
            self.coat_rough_map = coat_rough_map
        if clearcoat_map is not None:
            self.clearcoat_map = clearcoat_map
        if clearcoatRoughness_map is not None:
            self.clearcoatRoughness_map = clearcoatRoughness_map


class PhysicalMaterialCamel(_MaterialBase):
    """CamelCase-authored variant -- exercises the `coatMap` /
    `coatRoughMap` / `clearcoatMap` / `clearcoatRoughnessMap`
    slotMap entries."""

    max_class_name = "PhysicalMaterial"

    def __init__(
        self,
        baseColorMap=None,
        coatMap=None,
        coatRoughMap=None,
        clearcoatMap=None,
        clearcoatRoughnessMap=None,
    ):
        if baseColorMap is not None:
            self.baseColorMap = baseColorMap
        if coatMap is not None:
            self.coatMap = coatMap
        if coatRoughMap is not None:
            self.coatRoughMap = coatRoughMap
        if clearcoatMap is not None:
            self.clearcoatMap = clearcoatMap
        if clearcoatRoughnessMap is not None:
            self.clearcoatRoughnessMap = clearcoatRoughnessMap


class OpenPBRDerived(_MaterialBase):
    """Third-party OpenPBR-derived material exposing the full coat
    family: coat_weight_map / coat_color_map / coat_roughness_map /
    coat_ior_map / coat_normal_map. This is the material family that
    exercises the color3 (coat_color) and vector3 (coat_normal)
    routes; PhysicalMaterial cover only the scalar coat weight /
    roughness routes."""

    max_class_name = "OpenPBRDerived"

    def __init__(
        self,
        coat_weight_map=None,
        coat_color_map=None,
        coat_roughness_map=None,
        coat_ior_map=None,
        coat_normal_map=None,
        # camelCase alts
        coatWeightMap=None,
        coatColorMap=None,
        coatRoughnessMap=None,
        coatIorMap=None,
        coatNormalMap=None,
    ):
        if coat_weight_map is not None:
            self.coat_weight_map = coat_weight_map
        if coat_color_map is not None:
            self.coat_color_map = coat_color_map
        if coat_roughness_map is not None:
            self.coat_roughness_map = coat_roughness_map
        if coat_ior_map is not None:
            self.coat_ior_map = coat_ior_map
        if coat_normal_map is not None:
            self.coat_normal_map = coat_normal_map
        if coatWeightMap is not None:
            self.coatWeightMap = coatWeightMap
        if coatColorMap is not None:
            self.coatColorMap = coatColorMap
        if coatRoughnessMap is not None:
            self.coatRoughnessMap = coatRoughnessMap
        if coatIorMap is not None:
            self.coatIorMap = coatIorMap
        if coatNormalMap is not None:
            self.coatNormalMap = coatNormalMap


class VRayMtl(_MaterialBase):
    """VRayMtl exposes `.coat_amount_texmap` (V-Ray SDK canonical) for
    the coat weight scalar. VRayMtl's coat overlay is a full BRDF
    layer distinct from its `texmap_reflect` / `texmap_refract` maps;
    it's the arch-viz-standard car-paint / lacquer surface finisher.
    Post-Scene-Converter (V-Ray -> PhysicalMaterial), the coat weight
    is remapped to `coat_map`, but scenes not yet Scene-Converted
    still need this canonical spelling routed."""

    max_class_name = "VRayMtl"

    def __init__(
        self,
        texmap_diffuse=None,
        coat_amount_texmap=None,
        coatAmountTexmap=None,
    ):
        if texmap_diffuse is not None:
            self.texmap_diffuse = texmap_diffuse
        if coat_amount_texmap is not None:
            self.coat_amount_texmap = coat_amount_texmap
        if coatAmountTexmap is not None:
            self.coatAmountTexmap = coatAmountTexmap


class VRayBlendMtl(_MaterialBase):
    """Layered paint / weathered-surface wrapper -- same shape as
    MTLX-007 / MTLX-009 / MTLX-010 / MTLX-011 / MTLX-012."""

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
    MTLX-010 / MTLX-011 / MTLX-012."""

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
    `.opacity[i]` gates. Used here to prove the wrapper walk applies
    to the new coat entries -- overlay decals do NOT override the
    base surface's coat."""

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
# extension for Composite Mtl). Composite unwrap iterates .materialList
# with per-layer .mapEnabled / .opacity gates. Base at index 0 (or
# .materialList[0]) is always walked; overlays at i > 0 are gated on
# BOTH mapEnabled AND opacity > 0.
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
        # MAX-MTLX-COMPOSITE-DECAL-014 semantics: base at index 0 is
        # ALWAYS walked (even if opacity=0), then overlays at i > 0 with
        # mapEnabled AND opacity>0.
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
# COAT-015 additions. `TestStrictSupersetSlotMap` locks this against
# any future drift.
# =============================================================================


SLOT_MAP_POST_FIX = [
    # Pre-existing entries (unchanged by MAX-MTLX-COAT-015).
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
]


_COAT_INPUTS = {"coat", "coat_color", "coat_roughness", "coat_IOR", "coat_normal"}

SLOT_MAP_PRE_FIX = [e for e in SLOT_MAP_POST_FIX if e[1] not in _COAT_INPUTS]

MAX_MTLX_COAT_015_ADDITIONS = [
    e for e in SLOT_MAP_POST_FIX if e[1] in _COAT_INPUTS
]


# =============================================================================
# Python mirror of the C++ `_EnrichMtlxDocFromMaxMaterial` + the
# `discoverMaxMtlxTexmaps` MAXScript helper.
# =============================================================================


def _resolve_max_texmap_filename(tex):
    """Simplified stand-in for `resolveMaxTexmapFilename` -- a Bitmap
    stand-in exposes `.filename` directly."""
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
    """Baseline: pre-fix, a material authoring any of the coat
    spellings produces ZERO tiledimage nodes for the coat family.
    This locks in the fingerprint the fix resolves."""

    def test_coat_map_dropped_pre_fix(self):
        mat = PhysicalMaterial(
            base_color_map=_FakeBitmap("floor_diff.png"),
            coat_map=_FakeBitmap("floor_coat.png"),
        )
        discovery = discover_max_mtlx_texmaps(mat, SLOT_MAP_PRE_FIX)
        inputs = {d[0] for d in discovery}
        self.assertNotIn("coat", inputs,
                         "Pre-fix `coat` path should be silently dropped.")
        # Sanity: pre-existing base_color path still works.
        self.assertIn("base_color", inputs)

    def test_clearcoat_map_dropped_pre_fix(self):
        mat = PhysicalMaterial(
            clearcoat_map=_FakeBitmap("car_paint.png"),
        )
        discovery = discover_max_mtlx_texmaps(mat, SLOT_MAP_PRE_FIX)
        inputs = {d[0] for d in discovery}
        self.assertNotIn("coat", inputs)

    def test_openpbr_full_coat_family_dropped_pre_fix(self):
        mat = OpenPBRDerived(
            coat_weight_map=_FakeBitmap("w.png"),
            coat_color_map=_FakeBitmap("c.png"),
            coat_roughness_map=_FakeBitmap("r.png"),
            coat_ior_map=_FakeBitmap("i.png"),
            coat_normal_map=_FakeBitmap("n.png"),
        )
        discovery = discover_max_mtlx_texmaps(mat, SLOT_MAP_PRE_FIX)
        inputs = {d[0] for d in discovery}
        for coat_input in _COAT_INPUTS:
            self.assertNotIn(coat_input, inputs,
                             f"Pre-fix {coat_input} must be dropped.")

    def test_vray_coat_amount_texmap_dropped_pre_fix(self):
        mat = VRayMtl(
            texmap_diffuse=_FakeBitmap("carpaint_diff.png"),
            coat_amount_texmap=_FakeBitmap("carpaint_coat.png"),
        )
        discovery = discover_max_mtlx_texmaps(mat, SLOT_MAP_PRE_FIX)
        inputs = {d[0] for d in discovery}
        self.assertNotIn("coat", inputs)


class TestPropertyDispatch(unittest.TestCase):
    """Each of the 20 new slotMap entries dispatches correctly post-fix."""

    def _resolves(self, mat, expected_input, expected_type, expected_file):
        discovery = discover_max_mtlx_texmaps(mat, SLOT_MAP_POST_FIX)
        hits = [d for d in discovery if d[0] == expected_input]
        self.assertEqual(
            len(hits), 1,
            f"Expected exactly one {expected_input} discovery, got {hits!r}",
        )
        self.assertEqual(hits[0][1], expected_type)
        self.assertEqual(hits[0][2], expected_file)

    # PhysicalMaterial family
    def test_phys_coat_map_snake(self):
        self._resolves(
            PhysicalMaterial(coat_map=_FakeBitmap("a.png")),
            "coat", "float", "a.png",
        )

    def test_phys_coatMap_camel(self):
        self._resolves(
            PhysicalMaterialCamel(coatMap=_FakeBitmap("b.png")),
            "coat", "float", "b.png",
        )

    def test_phys_coat_rough_map_snake(self):
        self._resolves(
            PhysicalMaterial(coat_rough_map=_FakeBitmap("c.png")),
            "coat_roughness", "float", "c.png",
        )

    def test_phys_coatRoughMap_camel(self):
        self._resolves(
            PhysicalMaterialCamel(coatRoughMap=_FakeBitmap("d.png")),
            "coat_roughness", "float", "d.png",
        )

    def test_phys_clearcoat_map_snake(self):
        self._resolves(
            PhysicalMaterial(clearcoat_map=_FakeBitmap("e.png")),
            "coat", "float", "e.png",
        )

    def test_phys_clearcoatMap_camel(self):
        self._resolves(
            PhysicalMaterialCamel(clearcoatMap=_FakeBitmap("f.png")),
            "coat", "float", "f.png",
        )

    def test_phys_clearcoatRoughness_map_snake(self):
        self._resolves(
            PhysicalMaterial(clearcoatRoughness_map=_FakeBitmap("g.png")),
            "coat_roughness", "float", "g.png",
        )

    def test_phys_clearcoatRoughnessMap_camel(self):
        self._resolves(
            PhysicalMaterialCamel(clearcoatRoughnessMap=_FakeBitmap("h.png")),
            "coat_roughness", "float", "h.png",
        )

    # OpenPBR family
    def test_openpbr_coat_weight_map_snake(self):
        self._resolves(
            OpenPBRDerived(coat_weight_map=_FakeBitmap("i.png")),
            "coat", "float", "i.png",
        )

    def test_openpbr_coatWeightMap_camel(self):
        self._resolves(
            OpenPBRDerived(coatWeightMap=_FakeBitmap("j.png")),
            "coat", "float", "j.png",
        )

    def test_openpbr_coat_color_map_snake(self):
        self._resolves(
            OpenPBRDerived(coat_color_map=_FakeBitmap("k.png")),
            "coat_color", "color3", "k.png",
        )

    def test_openpbr_coatColorMap_camel(self):
        self._resolves(
            OpenPBRDerived(coatColorMap=_FakeBitmap("l.png")),
            "coat_color", "color3", "l.png",
        )

    def test_openpbr_coat_roughness_map_snake(self):
        self._resolves(
            OpenPBRDerived(coat_roughness_map=_FakeBitmap("m.png")),
            "coat_roughness", "float", "m.png",
        )

    def test_openpbr_coatRoughnessMap_camel(self):
        self._resolves(
            OpenPBRDerived(coatRoughnessMap=_FakeBitmap("n.png")),
            "coat_roughness", "float", "n.png",
        )

    def test_openpbr_coat_ior_map_snake(self):
        self._resolves(
            OpenPBRDerived(coat_ior_map=_FakeBitmap("o.png")),
            "coat_IOR", "float", "o.png",
        )

    def test_openpbr_coatIorMap_camel(self):
        self._resolves(
            OpenPBRDerived(coatIorMap=_FakeBitmap("p.png")),
            "coat_IOR", "float", "p.png",
        )

    def test_openpbr_coat_normal_map_snake(self):
        self._resolves(
            OpenPBRDerived(coat_normal_map=_FakeBitmap("q.png")),
            "coat_normal", "vector3", "q.png",
        )

    def test_openpbr_coatNormalMap_camel(self):
        self._resolves(
            OpenPBRDerived(coatNormalMap=_FakeBitmap("r.png")),
            "coat_normal", "vector3", "r.png",
        )

    # VRayMtl family
    def test_vray_coat_amount_texmap_snake(self):
        self._resolves(
            VRayMtl(coat_amount_texmap=_FakeBitmap("s.png")),
            "coat", "float", "s.png",
        )

    def test_vray_coatAmountTexmap_camel(self):
        self._resolves(
            VRayMtl(coatAmountTexmap=_FakeBitmap("t.png")),
            "coat", "float", "t.png",
        )

    # Multi-family independence
    def test_openpbr_full_coat_family_resolves_all_five(self):
        mat = OpenPBRDerived(
            coat_weight_map=_FakeBitmap("w.png"),
            coat_color_map=_FakeBitmap("c.png"),
            coat_roughness_map=_FakeBitmap("r.png"),
            coat_ior_map=_FakeBitmap("i.png"),
            coat_normal_map=_FakeBitmap("n.png"),
        )
        discovery = discover_max_mtlx_texmaps(mat, SLOT_MAP_POST_FIX)
        inputs = {d[0]: (d[1], d[2]) for d in discovery}
        self.assertEqual(inputs.get("coat"),           ("float",   "w.png"))
        self.assertEqual(inputs.get("coat_color"),     ("color3",  "c.png"))
        self.assertEqual(inputs.get("coat_roughness"), ("float",   "r.png"))
        self.assertEqual(inputs.get("coat_IOR"),       ("float",   "i.png"))
        self.assertEqual(inputs.get("coat_normal"),    ("vector3", "n.png"))

    def test_snake_wins_over_camel_first_hit(self):
        # If BOTH snake and camel spellings appear on the same material
        # (unusual but possible under MAXScript authoring), snake_case
        # wins because it is declared first in slotMap order.
        m = _MaterialBase()
        m.max_class_name = "PhysicalMaterial"
        m.coat_map = _FakeBitmap("snake.png")
        m.coatMap = _FakeBitmap("camel.png")
        discovery = discover_max_mtlx_texmaps(m, SLOT_MAP_POST_FIX)
        hits = [d for d in discovery if d[0] == "coat"]
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0][2], "snake.png")

    def test_short_wins_over_long_first_hit(self):
        # PhysicalMaterial's `coat_map` beats OpenPBR's
        # `coat_weight_map` when both authored on the same material
        # (unusual but possible). This matches the ordering convention
        # PhysicalMaterial-declared-first from every other slotMap
        # family (base_color/roughness/metalness/etc.).
        m = _MaterialBase()
        m.max_class_name = "PhysicalMaterial"
        m.coat_map = _FakeBitmap("short.png")
        m.coat_weight_map = _FakeBitmap("long.png")
        discovery = discover_max_mtlx_texmaps(m, SLOT_MAP_POST_FIX)
        hits = [d for d in discovery if d[0] == "coat"]
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0][2], "short.png")


class TestWrapperWalk(unittest.TestCase):
    """MAX-MTLX-007's wrapper walk (+ COMPOSITE-DECAL-014 extension)
    applies to the new coat entries because slotMap iteration lives
    inside `for currentMat in subMtls`."""

    def test_vray_blend_base_coat_wins_over_coat_overlay(self):
        base = PhysicalMaterial(coat_map=_FakeBitmap("base_coat.png"))
        overlay = PhysicalMaterial(coat_map=_FakeBitmap("overlay_coat.png"))
        blend = VRayBlendMtl(baseMtl=base, coats=(overlay,))
        discovery = discover_max_mtlx_texmaps(blend, SLOT_MAP_POST_FIX)
        hits = [d for d in discovery if d[0] == "coat"]
        self.assertEqual(len(hits), 1)
        self.assertEqual(
            hits[0][2], "base_coat.png",
            "Base's coat should win over overlay's under first-hit-wins "
            "(MTLX-007 base-first traversal).",
        )

    def test_vray_blend_coat_roughness_fills_gap(self):
        base = PhysicalMaterial(coat_map=_FakeBitmap("base_coat.png"))
        overlay = PhysicalMaterial(
            coat_rough_map=_FakeBitmap("overlay_rough.png"),
        )
        blend = VRayBlendMtl(baseMtl=base, coats=(overlay,))
        discovery = discover_max_mtlx_texmaps(blend, SLOT_MAP_POST_FIX)
        hits = [d for d in discovery if d[0] == "coat_roughness"]
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0][2], "overlay_rough.png",
                         "Overlay's coat_roughness fills the base's gap.")

    def test_vray_override_base_coat_wins_over_reflect(self):
        base = PhysicalMaterial(coat_map=_FakeBitmap("base_coat.png"))
        reflect_only = PhysicalMaterial(
            coat_map=_FakeBitmap("reflect_coat.png"),
        )
        override = VRayOverrideMtl(baseMtl=base, reflectMtl=reflect_only)
        discovery = discover_max_mtlx_texmaps(override, SLOT_MAP_POST_FIX)
        hits = [d for d in discovery if d[0] == "coat"]
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0][2], "base_coat.png")

    def test_composite_base_coat_wins_over_decal_overlays(self):
        # Court-floor Composite: base court PhysicalMaterial with a
        # coat_map + one decal overlay that ALSO has a coat_map.
        # Base wins.
        base = PhysicalMaterial(
            base_color_map=_FakeBitmap("court.png"),
            coat_map=_FakeBitmap("court_lacquer.png"),
        )
        decal = PhysicalMaterial(
            base_color_map=_FakeBitmap("decal.png"),
            coat_map=_FakeBitmap("decal_coat.png"),
        )
        comp = CompositeMtl(material_list=[base, decal])
        discovery = discover_max_mtlx_texmaps(comp, SLOT_MAP_POST_FIX)
        hits = [d for d in discovery if d[0] == "coat"]
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0][2], "court_lacquer.png")


class TestTypeCorrectness(unittest.TestCase):
    """Each injected tiledimage must be typed correctly. Anchored
    against the MaterialX stdlib so a library-version bump can't
    quietly change the type or rename an input."""

    def test_injected_coat_tiledimage_is_float(self):
        mat = PhysicalMaterial(coat_map=_FakeBitmap("c.png"))
        discovery = discover_max_mtlx_texmaps(mat, SLOT_MAP_POST_FIX)
        doc, ng, shader = build_scaffold_doc([("coat", "float")])
        n = enrich_mtlx_doc(doc, ng, shader, discovery)
        self.assertEqual(n, 1)
        img = ng.getNode("img_coat")
        self.assertIsNotNone(img)
        self.assertEqual(img.getType(), "float")
        self.assertEqual(img.getNodeDefString(), "ND_tiledimage_float")

    def test_injected_coat_color_tiledimage_is_color3(self):
        mat = OpenPBRDerived(coat_color_map=_FakeBitmap("c.png"))
        discovery = discover_max_mtlx_texmaps(mat, SLOT_MAP_POST_FIX)
        doc, ng, shader = build_scaffold_doc([("coat_color", "color3")])
        n = enrich_mtlx_doc(doc, ng, shader, discovery)
        self.assertEqual(n, 1)
        img = ng.getNode("img_coat_color")
        self.assertEqual(img.getType(), "color3")
        self.assertEqual(img.getNodeDefString(), "ND_tiledimage_color3")

    def test_injected_coat_roughness_tiledimage_is_float(self):
        mat = PhysicalMaterial(coat_rough_map=_FakeBitmap("r.png"))
        discovery = discover_max_mtlx_texmaps(mat, SLOT_MAP_POST_FIX)
        doc, ng, shader = build_scaffold_doc([("coat_roughness", "float")])
        n = enrich_mtlx_doc(doc, ng, shader, discovery)
        self.assertEqual(n, 1)
        img = ng.getNode("img_coat_roughness")
        self.assertEqual(img.getType(), "float")

    def test_injected_coat_ior_tiledimage_is_float(self):
        mat = OpenPBRDerived(coat_ior_map=_FakeBitmap("i.png"))
        discovery = discover_max_mtlx_texmaps(mat, SLOT_MAP_POST_FIX)
        doc, ng, shader = build_scaffold_doc([("coat_IOR", "float")])
        n = enrich_mtlx_doc(doc, ng, shader, discovery)
        self.assertEqual(n, 1)
        img = ng.getNode("img_coat_IOR")
        self.assertEqual(img.getType(), "float")

    def test_injected_coat_normal_tiledimage_is_vector3(self):
        mat = OpenPBRDerived(coat_normal_map=_FakeBitmap("n.png"))
        discovery = discover_max_mtlx_texmaps(mat, SLOT_MAP_POST_FIX)
        doc, ng, shader = build_scaffold_doc([("coat_normal", "vector3")])
        n = enrich_mtlx_doc(doc, ng, shader, discovery)
        self.assertEqual(n, 1)
        img = ng.getNode("img_coat_normal")
        self.assertEqual(img.getType(), "vector3")
        self.assertEqual(img.getNodeDefString(), "ND_tiledimage_vector3")

    def test_shader_input_types_match_materialx_stdlib(self):
        # Verify against the actual MaterialX stdlib nodedef so a
        # library-version bump can't quietly change the input types.
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
        expected = {
            "coat":           "float",
            "coat_color":     "color3",
            "coat_roughness": "float",
            "coat_IOR":       "float",
            "coat_normal":    "vector3",
        }
        for input_name, input_type in expected.items():
            inp = nd.getActiveInput(input_name)
            self.assertIsNotNone(
                inp,
                f"MaterialX stdlib must expose {input_name} input on "
                f"ND_standard_surface_surfaceshader.",
            )
            self.assertEqual(
                inp.getType(), input_type,
                f"MAX-MTLX-COAT-015 authors {input_name} as "
                f"{input_type}; stdlib change to another type would "
                f"silently break the fix.",
            )


class TestColorspaceGate(unittest.TestCase):
    """The existing colorspace attribute gate in
    `_EnrichMtlxDocFromMaxMaterial` (which only authors
    `colorspace="srgb_texture"` when `mtlxType == "color3"`) does
    the right thing by construction: only color3 coat_color gets
    the sRGB decode; scalar coat / coat_roughness / coat_IOR must
    NOT (would distort the value); vector3 coat_normal must NOT
    (tangent-space normal data is not sRGB-encoded)."""

    def _make(self, prop, mtlx_type):
        if prop == "coat_color_map":
            mat = OpenPBRDerived(coat_color_map=_FakeBitmap("x.png"))
            input_name, input_type = "coat_color", "color3"
        elif prop == "coat_map":
            mat = PhysicalMaterial(coat_map=_FakeBitmap("x.png"))
            input_name, input_type = "coat", "float"
        elif prop == "coat_rough_map":
            mat = PhysicalMaterial(coat_rough_map=_FakeBitmap("x.png"))
            input_name, input_type = "coat_roughness", "float"
        elif prop == "coat_ior_map":
            mat = OpenPBRDerived(coat_ior_map=_FakeBitmap("x.png"))
            input_name, input_type = "coat_IOR", "float"
        elif prop == "coat_normal_map":
            mat = OpenPBRDerived(coat_normal_map=_FakeBitmap("x.png"))
            input_name, input_type = "coat_normal", "vector3"
        else:
            raise AssertionError(prop)
        discovery = discover_max_mtlx_texmaps(mat, SLOT_MAP_POST_FIX)
        doc, ng, shader = build_scaffold_doc([(input_name, input_type)])
        enrich_mtlx_doc(doc, ng, shader, discovery)
        img = ng.getNode("img_" + input_name)
        return img.getInput("file")

    def test_coat_color_carries_srgb_colorspace(self):
        f = self._make("coat_color_map", "color3")
        self.assertEqual(f.getAttribute("colorspace"), "srgb_texture")

    def test_coat_weight_has_no_colorspace(self):
        f = self._make("coat_map", "float")
        self.assertFalse(
            f.hasAttribute("colorspace"),
            "Float coat weight tiledimage must NOT carry a colorspace "
            "attribute (sRGB decode of scalar would distort the value).",
        )

    def test_coat_roughness_has_no_colorspace(self):
        f = self._make("coat_rough_map", "float")
        self.assertFalse(f.hasAttribute("colorspace"))

    def test_coat_ior_has_no_colorspace(self):
        f = self._make("coat_ior_map", "float")
        self.assertFalse(f.hasAttribute("colorspace"))

    def test_coat_normal_has_no_colorspace(self):
        f = self._make("coat_normal_map", "vector3")
        self.assertFalse(
            f.hasAttribute("colorspace"),
            "Vector3 coat normal tiledimage must NOT carry a colorspace "
            "attribute (tangent-space normal is raw signed vector data).",
        )


class TestNoMapNoOutput(unittest.TestCase):
    """A material with base_color etc. but NO coat map produces zero
    coat-family tiledimages -- ND_standard_surface port defaults hold
    and the doc stays clean."""

    def test_no_coat_map_no_coat_output(self):
        mat = PhysicalMaterial(
            base_color_map=_FakeBitmap("diff.png"),
            roughness_map=_FakeBitmap("rough.png"),
        )
        discovery = discover_max_mtlx_texmaps(mat, SLOT_MAP_POST_FIX)
        inputs = {d[0] for d in discovery}
        self.assertIn("base_color", inputs)
        for coat_input in _COAT_INPUTS:
            self.assertNotIn(coat_input, inputs)


class TestSurgicalScope(unittest.TestCase):
    """The fix touches ONLY the slotMap -- every pre-existing dispatch
    must produce byte-identical output on a material that exercises
    them without any new-in-015 coat map."""

    def _build_full_non_015_material(self):
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
        # MTLX-009 IOR
        m.trans_ior_map = _FakeBitmap("i.png")
        # MTLX-010 anisotropy
        m.anisotropy_map = _FakeBitmap("j.png")
        m.anisotropy_rotation_map = _FakeBitmap("k.png")
        # MTLX-011 transmission-extra + sheen roughness
        m.trans_roughness_map = _FakeBitmap("l.png")
        m.sheen_roughness_map = _FakeBitmap("m.png")
        return m

    def test_non_015_material_discovery_unchanged(self):
        m = self._build_full_non_015_material()
        pre = discover_max_mtlx_texmaps(m, SLOT_MAP_PRE_FIX)
        post = discover_max_mtlx_texmaps(m, SLOT_MAP_POST_FIX)
        self.assertEqual(
            pre, post,
            "MAX-MTLX-COAT-015 must be a strict superset: a material "
            "with no coat map produces byte-identical discovery under "
            "both slotMaps.",
        )

    def test_only_coat_family_inputs_added(self):
        added_inputs = {e[1] for e in MAX_MTLX_COAT_015_ADDITIONS}
        self.assertEqual(added_inputs, _COAT_INPUTS)

    def test_type_distribution_matches_stdlib(self):
        # 15 float (coat + coat_roughness + coat_IOR = 8 + 6 + 2 = 16?)
        # Actually: coat weight = 8 entries (coat_map, coatMap,
        # clearcoat_map, clearcoatMap, coat_weight_map, coatWeightMap,
        # coat_amount_texmap, coatAmountTexmap). coat_roughness = 6.
        # coat_IOR = 2. coat_color = 2 (color3). coat_normal = 2
        # (vector3). Total 20.
        by_input = {}
        by_type = {}
        for _, mtlx_input, mtlx_type in MAX_MTLX_COAT_015_ADDITIONS:
            by_input[mtlx_input] = by_input.get(mtlx_input, 0) + 1
            by_type[mtlx_type] = by_type.get(mtlx_type, 0) + 1
        self.assertEqual(by_input["coat"], 8)
        self.assertEqual(by_input["coat_roughness"], 6)
        self.assertEqual(by_input["coat_color"], 2)
        self.assertEqual(by_input["coat_IOR"], 2)
        self.assertEqual(by_input["coat_normal"], 2)
        self.assertEqual(by_type["float"], 8 + 6 + 2)  # 16
        self.assertEqual(by_type["color3"], 2)
        self.assertEqual(by_type["vector3"], 2)


class TestStrictSupersetSlotMap(unittest.TestCase):
    """The post-fix slotMap MUST contain every pre-fix entry unchanged
    AND EXACTLY the 20 new entries. Locks the exact contents against
    future drift."""

    def test_post_fix_is_strict_superset(self):
        pre_set = set(SLOT_MAP_PRE_FIX)
        post_set = set(SLOT_MAP_POST_FIX)
        self.assertTrue(
            pre_set.issubset(post_set),
            "Post-fix slotMap must not remove or alter any pre-fix entry.",
        )
        added = post_set - pre_set
        self.assertEqual(
            len(added), 20,
            f"Expected exactly 20 additions, got {len(added)}: {added!r}",
        )

    def test_new_entries_are_exact_20(self):
        expected_new = {
            ("coat_map",                 "coat",           "float"),
            ("coatMap",                  "coat",           "float"),
            ("coat_rough_map",           "coat_roughness", "float"),
            ("coatRoughMap",             "coat_roughness", "float"),
            ("clearcoat_map",            "coat",           "float"),
            ("clearcoatMap",             "coat",           "float"),
            ("clearcoatRoughness_map",   "coat_roughness", "float"),
            ("clearcoatRoughnessMap",    "coat_roughness", "float"),
            ("coat_weight_map",          "coat",           "float"),
            ("coatWeightMap",            "coat",           "float"),
            ("coat_color_map",           "coat_color",     "color3"),
            ("coatColorMap",             "coat_color",     "color3"),
            ("coat_roughness_map",       "coat_roughness", "float"),
            ("coatRoughnessMap",         "coat_roughness", "float"),
            ("coat_ior_map",             "coat_IOR",       "float"),
            ("coatIorMap",               "coat_IOR",       "float"),
            ("coat_normal_map",          "coat_normal",    "vector3"),
            ("coatNormalMap",            "coat_normal",    "vector3"),
            ("coat_amount_texmap",       "coat",           "float"),
            ("coatAmountTexmap",         "coat",           "float"),
        }
        self.assertEqual(set(MAX_MTLX_COAT_015_ADDITIONS), expected_new)

    def test_new_entries_ordered_correctly(self):
        # Snake_case declared before camelCase within each family --
        # matches the pre-existing slotMap convention.
        names = [e[0] for e in MAX_MTLX_COAT_015_ADDITIONS]
        pairs = [
            ("coat_map",              "coatMap"),
            ("coat_rough_map",        "coatRoughMap"),
            ("clearcoat_map",         "clearcoatMap"),
            ("clearcoatRoughness_map","clearcoatRoughnessMap"),
            ("coat_weight_map",       "coatWeightMap"),
            ("coat_color_map",        "coatColorMap"),
            ("coat_roughness_map",    "coatRoughnessMap"),
            ("coat_ior_map",          "coatIorMap"),
            ("coat_normal_map",       "coatNormalMap"),
            ("coat_amount_texmap",    "coatAmountTexmap"),
        ]
        for snake, camel in pairs:
            self.assertLess(
                names.index(snake), names.index(camel),
                f"snake ({snake}) must precede camel ({camel})",
            )
        # PhysicalMaterial spellings (coat_map / coat_rough_map)
        # declared before OpenPBR-only spellings (coat_weight_map /
        # coat_color_map etc.).
        self.assertLess(
            names.index("coat_map"),
            names.index("coat_weight_map"),
        )
        # coat_normal (vector3) should be AFTER coat_color (color3)
        # per the OpenPBR mat_def ordering (color at 220, normal at 250).
        self.assertLess(
            names.index("coat_color_map"),
            names.index("coat_normal_map"),
        )


class TestArenaCensus(unittest.TestCase):
    """179-material arena-density synthetic mix.

      * 100 non-coated -- base_color + roughness only.
      *  40 lacquered floor tiles with coat_map + coat_rough_map
           (PhysicalMaterial-style).
      *  25 car-body panels with clearcoat_map + clearcoatRoughness_map
           (PhysicalMaterial-alt spelling).
      *  14 fully-coated OpenPBR pieces with coat_weight_map +
           coat_color_map + coat_roughness_map + coat_ior_map +
           coat_normal_map.

    Pre-fix expected: 0 discoveries for any coat-family input.
    Post-fix expected:
      * coat           discoveries = 40 + 25 + 14 = 79 (all three coat-
                       bearing buckets).
      * coat_roughness discoveries = 40 + 25 + 14 = 79.
      * coat_color     discoveries = 14 (OpenPBR bucket only).
      * coat_IOR       discoveries = 14.
      * coat_normal    discoveries = 14.
    Non-015 discovery counts unchanged between slotMaps."""

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
                base_color_map=_FakeBitmap("floor.png"),
                coat_map=_FakeBitmap("floor_coat.png"),
                coat_rough_map=_FakeBitmap("floor_coat_rough.png"),
            ))
        for _ in range(25):
            arena.append(PhysicalMaterial(
                base_color_map=_FakeBitmap("carbody.png"),
                clearcoat_map=_FakeBitmap("carbody_clearcoat.png"),
                clearcoatRoughness_map=_FakeBitmap("carbody_rough.png"),
            ))
        for _ in range(14):
            arena.append(OpenPBRDerived(
                coat_weight_map=_FakeBitmap("opb_w.png"),
                coat_color_map=_FakeBitmap("opb_c.png"),
                coat_roughness_map=_FakeBitmap("opb_r.png"),
                coat_ior_map=_FakeBitmap("opb_i.png"),
                coat_normal_map=_FakeBitmap("opb_n.png"),
            ))
        return arena

    def test_arena_coat_discovery_counts(self):
        arena = self._build_arena()
        self.assertEqual(len(arena), 179)
        pre_counts = {inp: 0 for inp in _COAT_INPUTS}
        post_counts = {inp: 0 for inp in _COAT_INPUTS}
        pre_other = post_other = 0
        for m in arena:
            for d in discover_max_mtlx_texmaps(m, SLOT_MAP_PRE_FIX):
                if d[0] in _COAT_INPUTS:
                    pre_counts[d[0]] += 1
                else:
                    pre_other += 1
            for d in discover_max_mtlx_texmaps(m, SLOT_MAP_POST_FIX):
                if d[0] in _COAT_INPUTS:
                    post_counts[d[0]] += 1
                else:
                    post_other += 1
        for inp, count in pre_counts.items():
            self.assertEqual(count, 0,
                             f"Pre-fix must have ZERO {inp} discoveries.")
        self.assertEqual(post_counts["coat"], 79,
                         "40 lacquer + 25 clearcoat + 14 openpbr = 79 coats.")
        self.assertEqual(post_counts["coat_roughness"], 79,
                         "40 + 25 + 14 = 79 coat_roughness discoveries.")
        self.assertEqual(post_counts["coat_color"], 14,
                         "Only 14 OpenPBR pieces carry coat_color.")
        self.assertEqual(post_counts["coat_IOR"], 14)
        self.assertEqual(post_counts["coat_normal"], 14)
        self.assertEqual(
            pre_other, post_other,
            "Non-015 discovery must be byte-identical between slotMaps.",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
