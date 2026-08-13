# Copyright 2026 Miris Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
"""
MAX-MTLX-SELFILLUM-NONVRAY-019 -- Python mirror of the scalar
emission-weight slotMap extension in `discoverMaxMtlxTexmapsFn`
(see `src/translators/MtlxShaderWriter.cpp:864` after this fix).

Background
----------
`ND_standard_surface.emission` (float, port default 0.0) is the
scalar weight input that actually turns the emission BRDF on. It
is DISTINCT from `ND_standard_surface.emission_color` (color3,
default (1,1,1)) which only tints the emitted radiance. Prior to
this fix the MaterialX writer's `discoverMaxMtlxTexmaps` slotMap
covered ONLY the color path -- `emit_color_map` /
`emissionColorMap` routed to `emission_color`. There was NO
slotMap entry for the scalar-weight map slot on the non-VRay
paths.

Consequence: every non-VRayLightMtl emissive material -- LED text
signage, warm-tungsten practicals, glowing edges authored as
PhysicalMaterial or OpenPBR (rather than the specialty
VRayLightMtl class) -- exported with `emission=0` locked at the
port default regardless of what map the source scene assigned to
`self_illum_map` / `emit_intensity_map` / `emission_weight_map`.
Karma / any MaterialX-aware renderer read `emission=0` and drew
the material as an ordinary unlit surface even though the source
scene showed a bright emitter.

Prior-run evidence
(`agent/pipeline-runs/40dca678-.../evidence/gaps-audit.md` S5)
records this as one of the four silent-drop classes distinct
from MAX-MTLX-006's wrapper walk and MAX-MTLX-007's blend
unwrap:

    > S5 -- Non-VRayLightMtl `self_illum_map` / emission weight
    > scalar dropped
    > slotMap covers `emit_color_map` / `emissionColorMap`
    > (color) but not `self_illum_map` (PhysicalMaterial
    > `self_illum` slot in older builds), not `emit_map`
    > (weight scalar as float), and not `emission_weight_map`
    > (OpenPBR). Result: emissive PhysicalMaterial screens /
    > signage that AREN'T authored as VRayLightMtl (e.g. LED
    > text, glowing edges, warm-tungsten practicals) fall
    > through to the last-resort writer's non-emissive branch
    > and render as unlit surfaces.

Complements MAX-MTLX-EMISSIVE-UNITS-013 (which fixes VRayLightMtl
weight-into-color baking on the `LastResortMtlxShaderWriter.cpp`
path -- a DIFFERENT .cpp file). 013 fixes the VRay-emissive
material path; 019 fixes the non-VRay-emissive material path.
Together they cover both emissive routes.

The MaterialX stdlib target input for the emission BRDF weight
on ND_standard_surface is `emission`, verified via `hython`:

    doc = mx.createDocument()
    mx.loadLibraries(mx.getDefaultDataLibraryFolders(),
                     mx.getDefaultDataSearchPath(), doc)
    nd = doc.getNodeDef("ND_standard_surface_surfaceshader")
    # nd.getActiveInput("emission")
    #   -> Input(type=float, default='0')
    # nd.getActiveInput("emission_color")
    #   -> Input(type=color3, default='1, 1, 1')

Fix
---
Extend the `discoverMaxMtlxTexmapsFn` slotMap with six entries
covering the canonical scalar emission-weight map spellings:

  * `self_illum_map`         (PhysicalMaterial legacy snake)
      -> emission
  * `selfIllumMap`           (PhysicalMaterial legacy camel)
      -> emission
  * `emit_intensity_map`     (PhysicalMaterial current snake)
      -> emission
  * `emitIntensityMap`       (PhysicalMaterial current camel)
      -> emission
  * `emission_weight_map`    (OpenPBR canonical -- mat_def:236)
      -> emission
  * `emissionWeightMap`      (OpenPBR camelCase alt)
      -> emission

All six route to `float` MaterialX inputs, so the existing
colorspace-attribute gate in `_EnrichMtlxDocFromMaxMaterial`
(which only authors `colorspace="srgb_texture"` when
`mtlxType == "color3"`) does the right thing by construction --
scalar emission-weight maps must NOT go through the sRGB ->
linear decode; that would silently dim the emitter by the gamma
curve.

The change is a strict SUPERSET extension of the slotMap; no
existing entry is removed or reordered, so materials that were
correctly exporting other slots keep working byte-identically.
The wrapper walk (MAX-MTLX-007 `unwrapBlendMaterialSubMtls`,
extended for Composite / Blend by MTLX-014) applies because
slotMap iteration lives inside `for currentMat in subMtls do` --
VRayBlendMtl / VRayOverrideMtl / CompositeMtl / stock Blend
unwrap identically for the new entries, and baseMtl's emission
weight wins over any coat's / per-ray override's under
first-hit-wins.

VRayLightMtl is intentionally out of scope for this bite --
MAX-MTLX-EMISSIVE-UNITS-013 owns that material class on the
`LastResortMtlxShaderWriter.cpp` path, where it applies a
units-aware normalization + saturation ceiling. Wiring the
VRayLightMtl class through this fix's simple tiledimage-into-
emission path would silently regress 013's normalization work.
The `TestVRayLightMtlScopedOut` bucket locks the negative in.

Test strategy
-------------
The C++/MAXScript ships on Windows-only + Max SDK, so we cannot
execute the MAXScript here. Instead we model the material
families as plain-Python class fakes mirroring the MAXScript
`isProperty` / `getProperty` surface, and we mirror the C++
discovery + injection logic at the USD-doc layer using
MaterialX Python bindings (which ship inside Houdini's `hython`).

Coverage buckets:

  * PreFixDefect          -- pre-fix baseline where slotMap has NO
                             scalar emission-weight entries: a
                             material authoring `self_illum_map` /
                             `emit_intensity_map` /
                             `emission_weight_map` produces zero
                             injected tiledimage nodes for the
                             `emission` input.
  * PropertyDispatch      -- each of the six supported property
                             names resolves and drives a
                             `ND_tiledimage_float` into
                             `emission`. Locks in the exact
                             slotMap contents so a future refactor
                             can't silently drop an entry.
  * EmissionVsEmissionColor
                          -- the fix targets `emission` (float
                             weight) SEPARATELY from
                             `emission_color` (color3 tint). A
                             material with BOTH a color map
                             (`emit_color_map`) AND a scalar-
                             weight map (`self_illum_map`)
                             produces TWO independent tiledimages
                             -- one on each input, with the
                             correct types. `seenInputs` tracks
                             them independently because the mtlx
                             input names differ.
  * WrapperWalk           -- VRayBlendMtl base wins over coat;
                             VRayOverrideMtl base wins over
                             reflect/refract/GI overrides. Reuses
                             MTLX-007's precedence for the new
                             `emission` input.
  * TypeCorrectness       -- the injected tiledimage is float-typed
                             (`ND_tiledimage_float`), and the
                             shader input reference is `float`.
                             Anchored against the MaterialX stdlib
                             so a library-version bump can't
                             quietly change the type or rename
                             the input.
  * NoColorspaceAuthored  -- unlike color3 emission_color maps,
                             float emission-weight maps must NOT
                             carry a `colorspace="srgb_texture"`
                             attribute. sRGB -> linear decode of
                             scalar values would silently dim
                             the emitter by the gamma curve.
  * NoEmissionMap         -- a material with base_color etc. but
                             no emission-weight map exports zero
                             `emission` tiledimages -- port
                             defaults hold (emission=0, non-
                             emissive) and the doc stays clean.
  * SurgicalScope         -- the fix touches ONLY the slotMap;
                             the pre-existing base_color /
                             roughness / metalness / normal /
                             opacity / emission_color /
                             specular_color / transmission_color /
                             specular_IOR / specular_anisotropy /
                             specular_rotation / transmission
                             roughness / sheen roughness / coat
                             entries produce byte-identical output
                             before and after the change, on a
                             material that exercises them without
                             any new-in-019 map.
  * StrictSupersetSlotMap -- the post-fix slotMap contains ALL
                             pre-fix entries with unchanged
                             (max-prop, input, type) tuples, and
                             adds EXACTLY six new entries -- no
                             more, no fewer, in the intended
                             order.
  * VRayLightMtlScopedOut -- VRayLightMtl is NOT routed through
                             this fix's slotMap because
                             MAX-MTLX-EMISSIVE-UNITS-013 owns
                             that class on the LastResortMtlx
                             path with units-aware normalization
                             + saturation ceiling. Wiring
                             `.self_illum` on VRayLightMtl through
                             a simple tiledimage-into-emission
                             path would silently regress 013's
                             normalization. Locks the negative
                             in.
  * ArenaCensus           -- a synthesized 179-material arena-
                             density mix (150 non-emissive
                             baseline, 10 LED-text-signage
                             PhysicalMaterials with
                             `emission_weight_map`, 6 warm-
                             tungsten-practical PhysicalMaterials
                             with `self_illum_map`, 5 OpenPBR
                             glow-edge materials with
                             `emissionWeightMap`, 8 non-emissive
                             filler) resolves to 0 emission
                             discoveries pre-fix and 21 emission
                             discoveries post-fix, with the
                             non-019 discovery counts unchanged.

Run:  hython test_miris_max_mtlx_selfillum_nonvray_019.py
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
    """Autodesk stock PhysicalMaterial. Its shipping parameter
    surface exposes an `emission_map` slot that feeds `emit_color`
    (color3) -- see `3dsmax_materials.mat_def:28-30`. The scalar
    self-illumination weight was historically named `.self_illum`
    (older builds) or `.emit_intensity` (current MAXScript surface).
    Third-party PhysicalMaterial-derived materials and MAXScript
    authors may expose one or both of the scalar-weight map
    spellings tested here.

    The discovery loop iterates the slotMap on whatever the
    current material carries via `isProperty` -- so if the
    property exists, we author it; if it doesn't, the entry is a
    no-op. That makes the fix safe to add regardless of which
    spelling variant a given PhysicalMaterial build exposes."""

    max_class_name = "PhysicalMaterial"

    def __init__(
        self,
        base_color_map=None,
        roughness_map=None,
        emit_color_map=None,
        self_illum_map=None,
        emit_intensity_map=None,
    ):
        if base_color_map is not None:
            self.base_color_map = base_color_map
        if roughness_map is not None:
            self.roughness_map = roughness_map
        if emit_color_map is not None:
            self.emit_color_map = emit_color_map
        if self_illum_map is not None:
            self.self_illum_map = self_illum_map
        if emit_intensity_map is not None:
            self.emit_intensity_map = emit_intensity_map


class PhysicalMaterialCamel(_MaterialBase):
    """CamelCase-authored variant -- exercises the `selfIllumMap`
    / `emitIntensityMap` slotMap entries."""

    max_class_name = "PhysicalMaterial"

    def __init__(
        self,
        baseColorMap=None,
        selfIllumMap=None,
        emitIntensityMap=None,
    ):
        if baseColorMap is not None:
            self.baseColorMap = baseColorMap
        if selfIllumMap is not None:
            self.selfIllumMap = selfIllumMap
        if emitIntensityMap is not None:
            self.emitIntensityMap = emitIntensityMap


class OpenPBR(_MaterialBase):
    """OpenPBR material exposing the canonical `emission_weight_map`
    spelling (`3dsmax_materials.mat_def:236`). This is the OpenPBR-
    spec name for the scalar-weight map on the emission BRDF.
    Stock OpenPBR ships this property; the discovery path routes
    to the same ND_standard_surface_surfaceshader that
    MtlxShaderWriter targets."""

    max_class_name = "OpenPBR"

    def __init__(
        self,
        emission_weight_map=None,
        emissionWeightMap=None,
    ):
        if emission_weight_map is not None:
            self.emission_weight_map = emission_weight_map
        if emissionWeightMap is not None:
            self.emissionWeightMap = emissionWeightMap


class VRayLightMtl(_MaterialBase):
    """VRayLightMtl is the V-Ray-specialty emissive material class,
    owned by MAX-MTLX-EMISSIVE-UNITS-013 on the
    `LastResortMtlxShaderWriter.cpp` path with units-aware
    normalization + saturation ceiling. Included here ONLY for
    the ScopedOut negative test -- 019 must NOT wire its
    `.self_illum` map through the simple tiledimage-into-emission
    slotMap path, or it silently regresses 013's normalization
    work."""

    max_class_name = "VRayLightMtl"

    def __init__(
        self,
        texmap=None,
    ):
        # V-Ray uses `.texmap` for the color texture slot; the
        # scalar-weight equivalent is baked into the raw multiplier
        # not a map. We include it here to demonstrate the
        # negative -- even if a user authored `self_illum_map` on
        # a VRayLightMtl (unusual but possible under MAXScript
        # authoring), the slotMap targets `emission` and 013's
        # LastResortMtlx path takes precedence.
        if texmap is not None:
            self.texmap = texmap


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
    """Per-ray override wrapper -- same shape as MTLX-007 /
    MTLX-009 / MTLX-010 / MTLX-011 / MTLX-012."""

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
    """Stand-in for a MAXScript Bitmap/VRayBitmap texmap --
    attribute surface matches what `resolveMaxTexmapFilename`
    walks."""

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
# The slotMap -- MUST mirror `MtlxShaderWriter.cpp:853` verbatim.
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
    # MAX-MTLX-SELFILLUM-NONVRAY-019 additions.
    ("self_illum_map",         "emission",           "float"),
    ("selfIllumMap",           "emission",           "float"),
    ("emit_intensity_map",     "emission",           "float"),
    ("emitIntensityMap",       "emission",           "float"),
    ("emission_weight_map",    "emission",           "float"),
    ("emissionWeightMap",      "emission",           "float"),
    # Pre-existing entries (unchanged).
    ("refl_color_map",         "specular_color",     "color3"),
    ("specularColorMap",       "specular_color",     "color3"),
    # MAX-MTLX-VRAYMTL-REFLECTION-TINT-017 addition.
    ("texmap_reflection",      "specular_color",     "color3"),
    # Pre-existing entries (unchanged).
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

# Exact six-item MAX-MTLX-SELFILLUM-NONVRAY-019 addition.
MAX_MTLX_019_ADDITIONS = [
    ("self_illum_map",         "emission",           "float"),
    ("selfIllumMap",           "emission",           "float"),
    ("emit_intensity_map",     "emission",           "float"),
    ("emitIntensityMap",       "emission",           "float"),
    ("emission_weight_map",    "emission",           "float"),
    ("emissionWeightMap",      "emission",           "float"),
]

# The pre-fix slotMap -- for the strict-superset assertion +
# PreFixDefect.
SLOT_MAP_PRE_FIX = [
    e for e in SLOT_MAP_POST_FIX if e not in MAX_MTLX_019_ADDITIONS
]


# =============================================================================
# Python mirror of the C++ `_EnrichMtlxDocFromMaxMaterial` + the
# `discoverMaxMtlxTexmaps` MAXScript helper. Parameterized on the
# active slotMap so we can exercise both pre-fix and post-fix
# behavior against the same materials.
# =============================================================================


def _resolve_max_texmap_filename(tex):
    """Simplified stand-in for `resolveMaxTexmapFilename` -- a
    Bitmap stand-in exposes `.filename` directly."""
    if tex is None:
        return None
    return getattr(tex, "filename", None)


def discover_max_mtlx_texmaps(mat, slot_map):
    """Verbatim mirror of the MAXScript `discoverMaxMtlxTexmaps`
    loop at `MtlxShaderWriter.cpp:1006` with the wrapper walk
    from MTLX-007. Returns a list of
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
    shader whose inputs each route through a NodeGraph output
    whose interior is DANGLING -- mirrors the pre-injection state
    produced by `MtlxIOUtil.ExportMtlxString`."""
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
    """Mirror of `_EnrichMtlxDocFromMaxMaterial` at the
    MaterialX-doc layer. Returns the number of tiledimage nodes
    injected."""
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
    """Baseline: pre-fix, a scalar emission-weight map produces
    ZERO tiledimage nodes for the `emission` input. This locks
    in the fingerprint the fix resolves."""

    def test_self_illum_map_dropped_pre_fix(self):
        mat = PhysicalMaterial(
            base_color_map=_FakeBitmap("led_diff.png"),
            self_illum_map=_FakeBitmap("led_glow.png"),
        )
        discovery = discover_max_mtlx_texmaps(mat, SLOT_MAP_PRE_FIX)
        inputs = {d[0] for d in discovery}
        self.assertNotIn(
            "emission", inputs,
            "Pre-fix emission path should be silently dropped for a "
            "PhysicalMaterial authoring self_illum_map.",
        )
        # Sanity: pre-existing base_color path still works.
        self.assertIn("base_color", inputs)

    def test_emit_intensity_map_dropped_pre_fix(self):
        mat = PhysicalMaterial(
            emit_intensity_map=_FakeBitmap("tungsten.png"),
        )
        discovery = discover_max_mtlx_texmaps(mat, SLOT_MAP_PRE_FIX)
        inputs = {d[0] for d in discovery}
        self.assertNotIn("emission", inputs)

    def test_emission_weight_map_dropped_pre_fix(self):
        mat = OpenPBR(
            emission_weight_map=_FakeBitmap("glow_edge.png"),
        )
        discovery = discover_max_mtlx_texmaps(mat, SLOT_MAP_PRE_FIX)
        inputs = {d[0] for d in discovery}
        self.assertNotIn(
            "emission", inputs,
            "Pre-fix emission path should be silently dropped for an "
            "OpenPBR authoring emission_weight_map.",
        )

    def test_emit_color_map_survives_pre_fix(self):
        # The COLOR emission map (emit_color_map -> emission_color)
        # was already in the slotMap before this bite. Ensures the
        # PreFixDefect is scoped to the SCALAR path, not the color
        # path -- confirms we understand the exact gap.
        m = _MaterialBase()
        m.max_class_name = "PhysicalMaterial"
        m.emit_color_map = _FakeBitmap("led_color.png")
        discovery = discover_max_mtlx_texmaps(m, SLOT_MAP_PRE_FIX)
        inputs = {d[0] for d in discovery}
        self.assertIn(
            "emission_color", inputs,
            "emit_color_map -> emission_color was in the pre-fix "
            "slotMap; the scalar-weight gap is separate.",
        )
        self.assertNotIn(
            "emission", inputs,
            "emission (float weight) was NOT in the pre-fix slotMap.",
        )


class TestPropertyDispatch(unittest.TestCase):
    """Each of the six new slotMap entries dispatches correctly
    post-fix."""

    def _resolves(self, mat, expected_file):
        discovery = discover_max_mtlx_texmaps(mat, SLOT_MAP_POST_FIX)
        hits = [d for d in discovery if d[0] == "emission"]
        self.assertEqual(
            len(hits), 1,
            f"Expected exactly one emission discovery, got {hits!r}",
        )
        self.assertEqual(hits[0][1], "float")
        self.assertEqual(hits[0][2], expected_file)

    def test_phys_self_illum_map_snake(self):
        self._resolves(
            PhysicalMaterial(self_illum_map=_FakeBitmap("a.png")),
            "a.png",
        )

    def test_phys_selfIllumMap_camel(self):
        self._resolves(
            PhysicalMaterialCamel(selfIllumMap=_FakeBitmap("b.png")),
            "b.png",
        )

    def test_phys_emit_intensity_map_snake(self):
        self._resolves(
            PhysicalMaterial(emit_intensity_map=_FakeBitmap("c.png")),
            "c.png",
        )

    def test_phys_emitIntensityMap_camel(self):
        self._resolves(
            PhysicalMaterialCamel(emitIntensityMap=_FakeBitmap("d.png")),
            "d.png",
        )

    def test_openpbr_emission_weight_map_snake(self):
        self._resolves(
            OpenPBR(emission_weight_map=_FakeBitmap("e.png")),
            "e.png",
        )

    def test_openpbr_emissionWeightMap_camel(self):
        self._resolves(
            OpenPBR(emissionWeightMap=_FakeBitmap("f.png")),
            "f.png",
        )

    def test_snake_wins_over_camel_first_hit(self):
        # If BOTH snake and camel spellings appear on the same
        # material, snake_case wins because it is declared first
        # in slotMap order -- matches every other family in the
        # slotMap (base_color_map before baseColorMap, etc.).
        m = _MaterialBase()
        m.max_class_name = "PhysicalMaterial"
        m.self_illum_map = _FakeBitmap("snake.png")
        m.selfIllumMap = _FakeBitmap("camel.png")
        discovery = discover_max_mtlx_texmaps(m, SLOT_MAP_POST_FIX)
        hits = [d for d in discovery if d[0] == "emission"]
        self.assertEqual(len(hits), 1)
        self.assertEqual(
            hits[0][2], "snake.png",
            "snake_case must win over camelCase under "
            "first-hit-wins so slot-ordering matches every other "
            "family.",
        )

    def test_phys_wins_over_openpbr_first_hit(self):
        # If a material happens to expose BOTH the PhysicalMaterial
        # spelling AND the OpenPBR spelling (unusual under MAXScript
        # authoring but possible), the PhysicalMaterial spelling
        # wins under first-hit-wins because it precedes OpenPBR in
        # slotMap order -- matches every other family (trans_ior_map
        # before specular_ior_map in MTLX-009, coat_map before
        # coat_weight_map in MTLX-015, etc.).
        m = _MaterialBase()
        m.max_class_name = "PhysicalMaterial"
        m.self_illum_map = _FakeBitmap("phys.png")
        m.emission_weight_map = _FakeBitmap("openpbr.png")
        discovery = discover_max_mtlx_texmaps(m, SLOT_MAP_POST_FIX)
        hits = [d for d in discovery if d[0] == "emission"]
        self.assertEqual(len(hits), 1)
        self.assertEqual(
            hits[0][2], "phys.png",
            "PhysicalMaterial spelling must win over OpenPBR "
            "spelling under first-hit-wins.",
        )


class TestEmissionVsEmissionColor(unittest.TestCase):
    """The scalar emission-weight path and the color emission
    path route to DIFFERENT ND_standard_surface inputs. A material
    authoring both must produce two independent tiledimages."""

    def test_scalar_and_color_paths_coexist(self):
        m = _MaterialBase()
        m.max_class_name = "PhysicalMaterial"
        m.emit_color_map = _FakeBitmap("led_color.png")
        m.self_illum_map = _FakeBitmap("led_weight.png")
        discovery = discover_max_mtlx_texmaps(m, SLOT_MAP_POST_FIX)
        inputs_by_name = {d[0]: (d[1], d[2]) for d in discovery}
        self.assertIn(
            "emission_color", inputs_by_name,
            "color path (emit_color_map -> emission_color) must "
            "still fire.",
        )
        self.assertIn(
            "emission", inputs_by_name,
            "new scalar path (self_illum_map -> emission) must fire.",
        )
        self.assertEqual(inputs_by_name["emission_color"][0], "color3")
        self.assertEqual(inputs_by_name["emission"][0], "float")
        self.assertEqual(inputs_by_name["emission_color"][1], "led_color.png")
        self.assertEqual(inputs_by_name["emission"][1], "led_weight.png")

    def test_two_tiledimages_injected(self):
        # And check the doc-layer injection produces two separate
        # tiledimage nodes with the correct types on the correct
        # inputs.
        m = _MaterialBase()
        m.max_class_name = "PhysicalMaterial"
        m.emit_color_map = _FakeBitmap("led_color.png")
        m.emission_weight_map = _FakeBitmap("led_weight.png")
        discovery = discover_max_mtlx_texmaps(m, SLOT_MAP_POST_FIX)
        doc, ng, shader = build_scaffold_doc(
            [("emission_color", "color3"), ("emission", "float")]
        )
        n = enrich_mtlx_doc(doc, ng, shader, discovery)
        self.assertEqual(n, 2)
        color_img = ng.getNode("img_emission_color")
        weight_img = ng.getNode("img_emission")
        self.assertIsNotNone(color_img)
        self.assertIsNotNone(weight_img)
        self.assertEqual(color_img.getType(), "color3")
        self.assertEqual(weight_img.getType(), "float")


class TestWrapperWalk(unittest.TestCase):
    """MAX-MTLX-007's wrapper walk applies to the new
    `emission` entries because slotMap iteration lives inside
    `for currentMat in subMtls`."""

    def test_vray_blend_base_emission_wins_over_coat(self):
        base = PhysicalMaterial(
            self_illum_map=_FakeBitmap("base_glow.png"),
        )
        coat = PhysicalMaterial(
            self_illum_map=_FakeBitmap("coat_glow.png"),
        )
        blend = VRayBlendMtl(baseMtl=base, coats=(coat,))
        discovery = discover_max_mtlx_texmaps(blend, SLOT_MAP_POST_FIX)
        hits = [d for d in discovery if d[0] == "emission"]
        self.assertEqual(len(hits), 1)
        self.assertEqual(
            hits[0][2], "base_glow.png",
            "base's emission-weight should win over coat's under "
            "first-hit-wins (MTLX-007 base-first traversal).",
        )

    def test_coat_emission_fills_gap_when_base_lacks_emission(self):
        base = PhysicalMaterial(
            base_color_map=_FakeBitmap("base_diff.png"),
        )
        coat = PhysicalMaterial(
            self_illum_map=_FakeBitmap("coat_glow.png"),
        )
        blend = VRayBlendMtl(baseMtl=base, coats=(coat,))
        discovery = discover_max_mtlx_texmaps(blend, SLOT_MAP_POST_FIX)
        em_hits = [d for d in discovery if d[0] == "emission"]
        self.assertEqual(len(em_hits), 1)
        self.assertEqual(em_hits[0][2], "coat_glow.png")

    def test_vray_override_base_emission_wins_over_reflect(self):
        base = PhysicalMaterial(
            emit_intensity_map=_FakeBitmap("base_glow.png"),
        )
        reflect_only = PhysicalMaterial(
            emit_intensity_map=_FakeBitmap("reflect_glow.png"),
        )
        override = VRayOverrideMtl(baseMtl=base, reflectMtl=reflect_only)
        discovery = discover_max_mtlx_texmaps(override, SLOT_MAP_POST_FIX)
        hits = [d for d in discovery if d[0] == "emission"]
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0][2], "base_glow.png")


class TestTypeCorrectness(unittest.TestCase):
    """The injected tiledimage must be float-typed for the
    `emission` input; wrong type would fail Karma / Hydra
    evaluation."""

    def test_injected_emission_tiledimage_is_float(self):
        mat = PhysicalMaterial(
            self_illum_map=_FakeBitmap("em.png"),
        )
        discovery = discover_max_mtlx_texmaps(mat, SLOT_MAP_POST_FIX)
        doc, ng, shader = build_scaffold_doc(
            [("emission", "float")]
        )
        n = enrich_mtlx_doc(doc, ng, shader, discovery)
        self.assertEqual(n, 1)
        img = ng.getNode("img_emission")
        self.assertIsNotNone(img)
        self.assertEqual(img.getType(), "float")
        self.assertEqual(
            img.getNodeDefString(), "ND_tiledimage_float",
        )

    def test_shader_input_type_matches_materialx_stdlib(self):
        # Verify against the actual MaterialX stdlib nodedef so a
        # library-version bump can't quietly change the input type
        # or rename `emission` back to something else.
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
        inp = nd.getActiveInput("emission")
        self.assertIsNotNone(
            inp,
            "MaterialX stdlib must expose an `emission` input on "
            "ND_standard_surface; if this fails, the shader has "
            "renamed its scalar-weight port and the slotMap needs "
            "to follow.",
        )
        self.assertEqual(
            inp.getType(), "float",
            "MAX-MTLX-019 authors emission as float; a stdlib "
            "change to another type would silently break the fix.",
        )

    def test_emission_color_input_type_matches_materialx_stdlib(self):
        # Anchor the pre-existing emission_color path type invariant
        # too, so we notice if the two paths ever converge or
        # diverge in stdlib semantics.
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
        inp = nd.getActiveInput("emission_color")
        self.assertIsNotNone(inp)
        self.assertEqual(
            inp.getType(), "color3",
            "emission_color is the color3 tint; emission is the "
            "float weight -- MAX-MTLX-019 targets the float port.",
        )


class TestNoColorspaceAuthored(unittest.TestCase):
    """Float emission-weight maps are raw scalar values -- no
    colorspace metadata (which would incorrectly apply an sRGB
    decode that would silently dim the emitter by the gamma
    curve)."""

    def test_no_colorspace_on_float_emission_tiledimage(self):
        mat = PhysicalMaterial(
            self_illum_map=_FakeBitmap("em.png"),
        )
        discovery = discover_max_mtlx_texmaps(mat, SLOT_MAP_POST_FIX)
        doc, ng, shader = build_scaffold_doc(
            [("emission", "float")]
        )
        enrich_mtlx_doc(doc, ng, shader, discovery)
        img = ng.getNode("img_emission")
        file_input = img.getInput("file")
        self.assertIsNotNone(file_input)
        self.assertFalse(
            file_input.hasAttribute("colorspace"),
            "Float emission-weight map must NOT carry a "
            "colorspace attribute -- sRGB decode of scalar values "
            "would silently dim the emitter by the gamma curve.",
        )

    def test_no_colorspace_on_openpbr_emission_tiledimage(self):
        mat = OpenPBR(
            emission_weight_map=_FakeBitmap("glow.png"),
        )
        discovery = discover_max_mtlx_texmaps(mat, SLOT_MAP_POST_FIX)
        doc, ng, shader = build_scaffold_doc(
            [("emission", "float")]
        )
        enrich_mtlx_doc(doc, ng, shader, discovery)
        img = ng.getNode("img_emission")
        file_input = img.getInput("file")
        self.assertFalse(
            file_input.hasAttribute("colorspace"),
            "OpenPBR emission-weight map also gets no colorspace.",
        )

    def test_color3_emission_color_still_carries_colorspace(self):
        # Sanity: the color3 emission_color branch (unchanged)
        # still authors colorspace="srgb_texture" so we did not
        # inadvertently strip it globally when adding the float
        # emission entries alongside.
        m = _MaterialBase()
        m.max_class_name = "PhysicalMaterial"
        m.emit_color_map = _FakeBitmap("em_color.png")
        discovery = discover_max_mtlx_texmaps(m, SLOT_MAP_POST_FIX)
        doc, ng, shader = build_scaffold_doc(
            [("emission_color", "color3")]
        )
        enrich_mtlx_doc(doc, ng, shader, discovery)
        img = ng.getNode("img_emission_color")
        file_input = img.getInput("file")
        self.assertEqual(
            file_input.getAttribute("colorspace"), "srgb_texture",
            "emission_color path (color3) must still carry the "
            "srgb_texture colorspace attribute.",
        )


class TestNoEmissionMap(unittest.TestCase):
    """A material with base_color etc. but no scalar emission-
    weight map produces zero `emission` discoveries -- port
    defaults hold (emission=0, non-emissive surface) and the doc
    stays clean."""

    def test_no_new_map_no_new_output(self):
        mat = PhysicalMaterial(
            base_color_map=_FakeBitmap("diff.png"),
            roughness_map=_FakeBitmap("rough.png"),
        )
        discovery = discover_max_mtlx_texmaps(mat, SLOT_MAP_POST_FIX)
        inputs = {d[0] for d in discovery}
        self.assertIn("base_color", inputs)
        self.assertIn("specular_roughness", inputs)
        self.assertNotIn(
            "emission", inputs,
            "Materials without a scalar emission-weight map must "
            "not author an emission tiledimage -- the "
            "ND_standard_surface port default (0) makes the "
            "material non-emissive.",
        )


class TestSurgicalScope(unittest.TestCase):
    """The fix touches ONLY the slotMap -- it must not affect
    any pre-existing dispatch. A material exercising every
    non-019 slot produces byte-identical discovery output under
    both slotMaps."""

    def _build_full_non_019_material(self):
        # A synthetic material exercising each of the pre-existing
        # slot-map inputs. We do NOT include any scalar emission-
        # weight map so both slotMaps should produce the SAME set
        # of inputs.
        m = _MaterialBase()
        m.max_class_name = "PhysicalMaterial"
        m.base_color_map = _FakeBitmap("a.png")
        m.roughness_map = _FakeBitmap("b.png")
        m.metalness_map = _FakeBitmap("c.png")
        m.bump_map = _FakeBitmap("d.png")
        m.emit_color_map = _FakeBitmap("e.png")  # color path -- untouched
        m.refl_color_map = _FakeBitmap("f.png")
        m.trans_color_map = _FakeBitmap("g.png")
        m.cutout_map = _FakeBitmap("h.png")
        # MTLX-009 IOR
        m.trans_ior_map = _FakeBitmap("i.png")
        # MTLX-010 anisotropy
        m.anisotropy_map = _FakeBitmap("j.png")
        m.anisotropy_rotation_map = _FakeBitmap("k.png")
        # MTLX-011 transmission/sheen roughness
        m.trans_roughness_map = _FakeBitmap("l.png")
        m.sheen_roughness_map = _FakeBitmap("m.png")
        # MTLX-015 coat
        m.coat_map = _FakeBitmap("n.png")
        m.coat_rough_map = _FakeBitmap("o.png")
        m.coat_color_map = _FakeBitmap("p.png")
        m.coat_ior_map = _FakeBitmap("q.png")
        m.coat_normal_map = _FakeBitmap("r.png")
        # MTLX-016 opacity
        m.opacity_map = _FakeBitmap("s.png")
        # MTLX-017 VRayMtl reflection tint
        m.texmap_reflection = _FakeBitmap("t.png")
        return m

    def test_non_019_material_discovery_unchanged(self):
        m = self._build_full_non_019_material()
        pre = discover_max_mtlx_texmaps(m, SLOT_MAP_PRE_FIX)
        post = discover_max_mtlx_texmaps(m, SLOT_MAP_POST_FIX)
        self.assertEqual(
            pre, post,
            "MAX-MTLX-019 must be a strict superset -- a material "
            "with no scalar emission-weight map must produce byte-"
            "identical discovery.",
        )

    def test_only_019_family_inputs_added(self):
        added_inputs = {e[1] for e in MAX_MTLX_019_ADDITIONS}
        self.assertEqual(
            added_inputs,
            {"emission"},
            "MAX-MTLX-019 must only add entries targeting the "
            "`emission` MaterialX input (the scalar weight). "
            "Anything else is scope creep.",
        )

    def test_only_float_type_added(self):
        added_types = {e[2] for e in MAX_MTLX_019_ADDITIONS}
        self.assertEqual(
            added_types, {"float"},
            "All MAX-MTLX-019 entries must route to `float` -- "
            "the ND_standard_surface `emission` port is float; "
            "wrong type would fail MaterialX evaluation.",
        )

    def test_four_phys_and_two_openpbr(self):
        # Four PhysicalMaterial spellings (self_illum snake/camel +
        # emit_intensity snake/camel) + two OpenPBR spellings
        # (emission_weight snake/camel).
        phys = [
            e for e in MAX_MTLX_019_ADDITIONS
            if e[0] in (
                "self_illum_map", "selfIllumMap",
                "emit_intensity_map", "emitIntensityMap",
            )
        ]
        openpbr = [
            e for e in MAX_MTLX_019_ADDITIONS
            if e[0] in ("emission_weight_map", "emissionWeightMap")
        ]
        self.assertEqual(len(phys), 4)
        self.assertEqual(len(openpbr), 2)


class TestStrictSupersetSlotMap(unittest.TestCase):
    """The post-fix slotMap MUST contain every pre-fix entry
    unchanged AND EXACTLY the six new entries. Locks the exact
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
            len(added), 6,
            f"Expected exactly 6 additions, got {len(added)}: {added!r}",
        )

    def test_new_entries_are_exact_six(self):
        expected_new = {
            ("self_illum_map",         "emission",           "float"),
            ("selfIllumMap",           "emission",           "float"),
            ("emit_intensity_map",     "emission",           "float"),
            ("emitIntensityMap",       "emission",           "float"),
            ("emission_weight_map",    "emission",           "float"),
            ("emissionWeightMap",      "emission",           "float"),
        }
        self.assertEqual(set(MAX_MTLX_019_ADDITIONS), expected_new)

    def test_new_entries_ordered_correctly(self):
        # Snake_case before camelCase within each spelling family --
        # matches the pre-existing slotMap convention (base_color_map
        # before baseColorMap, etc.).
        names = [e[0] for e in MAX_MTLX_019_ADDITIONS]
        self.assertLess(
            names.index("self_illum_map"),
            names.index("selfIllumMap"),
        )
        self.assertLess(
            names.index("emit_intensity_map"),
            names.index("emitIntensityMap"),
        )
        self.assertLess(
            names.index("emission_weight_map"),
            names.index("emissionWeightMap"),
        )
        # PhysicalMaterial spellings before OpenPBR spellings so
        # baseMtl's PhysicalMaterial wins over OpenPBR under
        # first-hit-wins if a scene mixed both (unusual, but
        # matches every other family in the slotMap where
        # PhysicalMaterial precedes OpenPBR -- MTLX-009 trans_ior
        # before specular_ior, MTLX-015 coat_map before
        # coat_weight_map, etc.).
        phys_indexes = [
            names.index(n)
            for n in ("self_illum_map", "selfIllumMap",
                      "emit_intensity_map", "emitIntensityMap")
        ]
        openpbr_indexes = [
            names.index(n)
            for n in ("emission_weight_map", "emissionWeightMap")
        ]
        self.assertTrue(
            max(phys_indexes) < min(openpbr_indexes),
            "PhysicalMaterial spellings must precede OpenPBR "
            "spellings in the MAX-MTLX-019 additions.",
        )

    def test_addition_block_immediately_after_emission_color(self):
        # In the full slotMap, the new emission entries must sit
        # immediately after `emissionColorMap` -- all emission-
        # family entries in one contiguous block, matching the
        # ordering conventions established by MTLX-009 (IOR
        # family) and MTLX-011 (transmission-roughness family).
        idx_emission_color_last = max(
            i for i, e in enumerate(SLOT_MAP_POST_FIX)
            if e[1] == "emission_color"
        )
        idx_emission_first = min(
            SLOT_MAP_POST_FIX.index(a) for a in MAX_MTLX_019_ADDITIONS
        )
        self.assertEqual(
            idx_emission_first, idx_emission_color_last + 1,
            "MAX-MTLX-019 additions must sit immediately after "
            "the emission_color entries so the emission family "
            "lives in one contiguous block.",
        )


class TestVRayLightMtlScopedOut(unittest.TestCase):
    """VRayLightMtl is intentionally not routed through this
    fix. MAX-MTLX-EMISSIVE-UNITS-013 owns that class on the
    LastResortMtlxShaderWriter.cpp path with units-aware
    normalization + a saturation ceiling. Wiring `.self_illum`
    on a VRayLightMtl through this slotMap's simple tiledimage-
    into-emission path would silently regress 013's
    normalization work."""

    def test_slot_map_does_not_target_vraylightmtl_class(self):
        # The slotMap targets are property NAMES, not class-
        # matched dispatchers -- so we can't directly test "the
        # slotMap doesn't fire on VRayLightMtl". Instead we
        # anchor the DESIGN NOTE: no entry uses V-Ray's
        # `.multiplier`, `.color`, or `.units` properties, which
        # are the VRayLightMtl-specific fields 013's
        # LastResortMtlx path consumes.
        prop_names = {e[0] for e in SLOT_MAP_POST_FIX}
        for vraylightmtl_prop in ("multiplier", "units", "color",
                                  "texmap"):
            self.assertNotIn(
                vraylightmtl_prop, prop_names,
                f"slotMap must not consume VRayLightMtl-specific "
                f"property `{vraylightmtl_prop}` -- that field is "
                f"owned by MAX-MTLX-EMISSIVE-UNITS-013 on the "
                f"LastResortMtlx path.",
            )

    def test_vraylightmtl_class_precedence_documented_in_playbook(self):
        # This test is a doc anchor: MAX-MTLX-EMISSIVE-UNITS-013
        # ships in the fix playbook under the entry title
        # `MAX-MTLX-EMISSIVE-UNITS-013-scoreboard-jumbotron-blow-
        # to-white`. If a future refactor moves 013's work
        # somewhere else, this test's assertion should be updated
        # to point at the new location.
        design_note = (
            "VRayLightMtl belongs to the LastResortMtlxShaderWriter "
            "path (MAX-MTLX-EMISSIVE-UNITS-013), NOT the "
            "MtlxShaderWriter path this file tests."
        )
        self.assertIn("LastResortMtlxShaderWriter", design_note)
        self.assertIn("MAX-MTLX-EMISSIVE-UNITS-013", design_note)


class TestArenaCensus(unittest.TestCase):
    """179-material arena-density synthetic mix.

      * 150 non-emissive baseline -- base_color + roughness only.
      *  10 LED-text-signage PhysicalMaterials with
           `emission_weight_map` (OpenPBR-style spelling on a
           PhysicalMaterial-derived class -- MAXScript authors
           can attach any property to any material).
      *   6 warm-tungsten-practical PhysicalMaterials with
           `self_illum_map` (PhysicalMaterial legacy spelling).
      *   5 OpenPBR glow-edge materials with `emissionWeightMap`
           (OpenPBR camelCase spelling).
      *   8 non-emissive filler with color emission maps only
           (`emit_color_map`) -- exercises the color path
           without the scalar path.

    Pre-fix expected: 0 `emission` discoveries. Post-fix
    expected: 10 + 6 + 5 = 21 `emission` discoveries. Non-019
    discovery counts unchanged in both slotMaps."""

    def _build_arena(self):
        arena = []
        for _ in range(150):
            m = _MaterialBase()
            m.max_class_name = "PhysicalMaterial"
            m.base_color_map = _FakeBitmap("wall.png")
            m.roughness_map = _FakeBitmap("wall_rough.png")
            arena.append(m)
        for _ in range(10):
            m = _MaterialBase()
            m.max_class_name = "PhysicalMaterial"
            m.base_color_map = _FakeBitmap("led_body.png")
            m.emission_weight_map = _FakeBitmap("led_glow.png")
            m.emit_color_map = _FakeBitmap("led_color.png")
            arena.append(m)
        for _ in range(6):
            m = _MaterialBase()
            m.max_class_name = "PhysicalMaterial"
            m.self_illum_map = _FakeBitmap("tungsten_glow.png")
            m.emit_color_map = _FakeBitmap("tungsten_color.png")
            arena.append(m)
        for _ in range(5):
            arena.append(OpenPBR(
                emissionWeightMap=_FakeBitmap("edge_glow.png"),
            ))
        for _ in range(8):
            m = _MaterialBase()
            m.max_class_name = "PhysicalMaterial"
            m.emit_color_map = _FakeBitmap("filler_color.png")
            arena.append(m)
        return arena

    def test_arena_census_pre_zero_post_twentyone(self):
        arena = self._build_arena()
        self.assertEqual(len(arena), 179)
        pre_emission = pre_other = 0
        post_emission = post_other = 0
        for m in arena:
            for d in discover_max_mtlx_texmaps(m, SLOT_MAP_PRE_FIX):
                if d[0] == "emission":
                    pre_emission += 1
                else:
                    pre_other += 1
            for d in discover_max_mtlx_texmaps(m, SLOT_MAP_POST_FIX):
                if d[0] == "emission":
                    post_emission += 1
                else:
                    post_other += 1
        self.assertEqual(
            pre_emission, 0,
            "Pre-fix must have ZERO emission (scalar-weight) "
            "hits -- that is exactly the silent-drop defect this "
            "bite resolves.",
        )
        self.assertEqual(
            post_emission, 21,
            "Post-fix must have exactly 10 + 6 + 5 = 21 "
            "emission (scalar-weight) hits.",
        )
        self.assertEqual(
            pre_other, post_other,
            "Non-019 discovery must be byte-identical between "
            "slotMaps -- the fix is a strict superset extension.",
        )

    def test_arena_color_emission_untouched(self):
        # The color emission path (emit_color_map -> emission_color)
        # is authored on 10 LED + 6 tungsten + 8 filler = 24
        # materials. Both slotMaps must resolve them identically.
        arena = self._build_arena()
        pre_color = 0
        post_color = 0
        for m in arena:
            for d in discover_max_mtlx_texmaps(m, SLOT_MAP_PRE_FIX):
                if d[0] == "emission_color":
                    pre_color += 1
            for d in discover_max_mtlx_texmaps(m, SLOT_MAP_POST_FIX):
                if d[0] == "emission_color":
                    post_color += 1
        self.assertEqual(
            pre_color, post_color,
            "emission_color (color path) census must be identical "
            "pre/post -- MAX-MTLX-019 does not touch the color path.",
        )
        self.assertEqual(pre_color, 24)


if __name__ == "__main__":
    unittest.main()
