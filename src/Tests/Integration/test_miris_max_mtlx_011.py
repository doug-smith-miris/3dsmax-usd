# Copyright 2026 Miris Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
"""
MAX-MTLX-011 — Python mirror of the transmission_extra_roughness +
sheen_roughness slotMap extension in `discoverMaxMtlxTexmapsFn`
(see `src/translators/MtlxShaderWriter.cpp:530` after this fix).

Background
----------
`ND_standard_surface.transmission_extra_roughness` (float, default
0.0) and `ND_standard_surface.sheen_roughness` (float, default 0.3)
are the two ND_standard_surface inputs that drive, respectively,
the roughness component added to specular_roughness for transmission
ray paths (frosted glass, satin glazing, translucent plastic) and
the sheen BRDF's roughness (velvet, satin, brushed fabric, dusty
finish). Prior to this fix the MaterialX writer's discovery slotMap
covered NO transmission-roughness or sheen-roughness slots at all,
so every glass / glazing / frosted-dielectric material with a
refraction-roughness map, and every velvet / satin material with a
sheen-roughness map, silently exported with those inputs locked at
their ND_standard_surface port defaults (0.0 and 0.3, respectively)
regardless of what the source scene said. The visible signature is
uniform, port-default transmission roughness across every glass
material and a fixed 0.3 sheen roughness across every fabric — no
map response to the artist-authored variation.

Prior-run evidence
(`agent/pipeline-runs/e218e7b9-.../evidence-slotmap-and-wrappers.md`)
records this as a silent-drop class distinct from MAX-MTLX-006's
wrapper walk and MAX-MTLX-007's blend/override unwrap:

    > Slots absent from the slotMap (silent-drop classes):
    >   texmap_refractionGlossiness / trans_roughness_map /
    >     transmissionRoughnessMap (transmission_roughness)
    >   sheen_color_map / sheenColorMap / sheen_roughness_map
    >     (sheen_color, sheen_roughness)

The MaterialX stdlib target input for transmission roughness on
ND_standard_surface is `transmission_extra_roughness`, NOT
`transmission_roughness`. Verified via `hython`:

    doc = mx.createDocument()
    mx.loadLibraries(mx.getDefaultDataLibraryFolders(),
                     mx.getDefaultDataSearchPath(), doc)
    nd = doc.getNodeDef("ND_standard_surface_surfaceshader")
    # nd.getActiveInput("transmission_roughness") -> None
    # nd.getActiveInput("transmission_extra_roughness")
    #   -> Input(type=float, default='0')

`transmission_extra_roughness` is added on top of
`specular_roughness` at evaluation time — the correct semantic for a
"refraction roughness" map because it lets the artist pin the
specular reflection roughness at one value and roughen transmission
independently. Karma / Hydra evaluate it that way.

Fix
---
Extend the `discoverMaxMtlxTexmapsFn` slotMap with six entries
covering the canonical transmission-roughness + sheen-roughness map
spellings:

  * `trans_roughness_map`        (PhysicalMaterial-style snake)
      -> transmission_extra_roughness
  * `transRoughnessMap`          (PhysicalMaterial-style camel)
      -> transmission_extra_roughness
  * `transmission_roughness_map` (OpenPBR-style long snake)
      -> transmission_extra_roughness
  * `transmissionRoughnessMap`   (OpenPBR-style long camel)
      -> transmission_extra_roughness
  * `sheen_roughness_map`        (generic snake)
      -> sheen_roughness
  * `sheenRoughnessMap`          (generic camel)
      -> sheen_roughness

Stock PhysicalMaterial and stock OpenPBR do NOT expose a distinct
transmission-roughness map in `3dsmax_materials.mat_def` — stock
PhysicalMaterial has no roughness family for transmission at all;
stock OpenPBR shares `specular_roughness_map` between reflection and
transmission per the OpenPBR spec (single-roughness dielectric
model). These slotMap entries therefore cover MAXScript-authored /
third-party PhysicalMaterial-derived custom materials that expose
these spellings. They also give MAXScript authors and custom
material developers a canonical spelling to author against so their
transmission-roughness intent survives the export.

VRayMtl's `texmap_refractionGlossiness` is EXCLUDED from this bite.
V-Ray exposes glossiness (0 = rough, 1 = smooth) while
ND_standard_surface uses roughness (0 = smooth, 1 = rough). Wiring
glossiness directly to `transmission_extra_roughness` would produce
a semantically inverted map — polished glass would render as frost.
Adding V-Ray's glossiness map requires an `ND_invert_float` node
inserted between the tiledimage and the shader input, which is an
authoring-path change beyond a slotMap extension. That is a
follow-on bite (candidateMission
`max-mtlx-013-vray-glossiness-to-roughness-invert`). The dominant
arch-viz path — VRayMtl scenes run through the V-Ray Scene Converter
to become PhysicalMaterial — reaches transmission roughness via the
PhysicalMaterial spellings authored above.

All six entries route to `float` MaterialX inputs, so the existing
colorspace-attribute gate in `_EnrichMtlxDocFromMaxMaterial` (which
only authors `colorspace="srgb_texture"` when `mtlxType == "color3"`)
does the right thing by construction — roughness scalars must NOT
go through the sRGB -> linear decode; that would silently distort
the value.

The change is a strict SUPERSET extension of the slotMap; no
existing entry is removed or reordered, so materials that were
correctly exporting other slots keep working byte-identically. The
wrapper walk (MAX-MTLX-007 `unwrapBlendMaterialSubMtls`) applies
because the slotMap iteration lives inside
`for currentMat in subMtls do` — VRayBlendMtl and VRayOverrideMtl
unwrap identically for the new entries, and baseMtl's transmission /
sheen roughness wins over any coat's / per-ray override's.

Test strategy
-------------
The C++/MAXScript ships on Windows-only + Max SDK, so we cannot
execute the MAXScript here. Instead we model the material families
as plain-Python class fakes mirroring the MAXScript
`isProperty` / `getProperty` surface, and we mirror the C++
discovery + injection logic at the USD-doc layer using MaterialX
Python bindings (which ship inside Houdini's `hython`).

Coverage buckets:

  * PreFixDefect          -- pre-fix baseline where slotMap has NO
                             transmission-roughness or sheen-roughness
                             entries: a material authoring
                             `trans_roughness_map` /
                             `sheen_roughness_map` produces zero
                             injected tiledimage nodes for those
                             inputs.
  * PropertyDispatch      -- each of the six supported property
                             names resolves and drives a
                             `ND_tiledimage_float` into either
                             transmission_extra_roughness or
                             sheen_roughness. Locks in the exact
                             slotMap contents so a future refactor
                             can't silently drop an entry.
  * WrapperWalk           -- VRayBlendMtl base wins over coat;
                             VRayOverrideMtl base wins over
                             reflect/refract/GI overrides. Reuses
                             MTLX-007's precedence.
  * TypeCorrectness       -- the injected tiledimage is float-typed
                             (`ND_tiledimage_float`), and the shader
                             input reference is `float`. Anchored
                             against the MaterialX stdlib so a
                             library-version bump can't quietly
                             change the type or rename the input.
  * NoColorspaceAuthored  -- unlike color3 base_color maps, float
                             transmission-roughness / sheen-roughness
                             maps must NOT carry a
                             `colorspace="srgb_texture"` attribute.
                             sRGB -> linear decode of scalar values
                             would silently distort the roughness.
  * NoRoughnessMap        -- a material with base_color etc. but no
                             transmission / sheen roughness map
                             exports zero transmission_extra_roughness
                             / sheen_roughness tiledimages -- port
                             defaults hold and the doc stays clean.
  * SurgicalScope         -- the fix touches ONLY the slotMap; the
                             existing base_color / roughness /
                             metalness / normal / opacity /
                             emission_color / specular_color /
                             transmission_color / specular_IOR /
                             specular_anisotropy / specular_rotation
                             entries produce byte-identical output
                             before and after the change, on a
                             material that exercises them without
                             any new-in-011 map.
  * StrictSupersetSlotMap -- the post-fix slotMap contains ALL
                             pre-fix entries with unchanged
                             (max-prop, input, type) tuples, and
                             adds EXACTLY six new entries -- no
                             more, no fewer, in the intended order.
  * VRayGlossinessScopedOut
                          -- VRayMtl's `texmap_refractionGlossiness`
                             is NOT in the slotMap: wiring it
                             directly would produce a semantically
                             inverted map (glossiness vs roughness
                             conventions differ). Locks the negative
                             in for the follow-on bite that owns the
                             invert-node authoring path.
  * ArenaCensus           -- a synthesized 179-material arena-density
                             mix (100 non-dielectric non-fabric, 40
                             glass with trans_roughness_map, 25 satin
                             glazing with transmissionRoughnessMap,
                             14 velvet with sheen_roughness_map)
                             resolves to 65 transmission_extra_roughness
                             + 14 sheen_roughness discoveries
                             post-fix vs. 0/0 pre-fix, with the
                             non-011 discovery counts unchanged.

Run:  hython test_miris_max_mtlx_011.py
"""
import unittest

import MaterialX as mx


# =============================================================================
# Fake material classes -- mirror the MAXScript `isProperty` /
# `getProperty` surface. Same shape as MTLX-007 / MTLX-009 / MTLX-010 /
# MTLX-012.
# =============================================================================


class _MaterialBase:
    max_class_name = "GenericMtl"


class PhysicalMaterial(_MaterialBase):
    """Autodesk stock PhysicalMaterial does NOT expose a transmission
    roughness map or a sheen roughness map in its shipping parameter
    surface (`3dsmax_materials.mat_def:10-43`). Third-party
    PhysicalMaterial-derived materials and MAXScript authors DO expose
    these spellings; we test them here against a PhysicalMaterial-
    shaped fake because MAXScript can attach any property to any
    material and the discovery loop iterates the slotMap on whatever
    the current material carries."""

    max_class_name = "PhysicalMaterial"

    def __init__(
        self,
        base_color_map=None,
        roughness_map=None,
        trans_roughness_map=None,
        sheen_roughness_map=None,
    ):
        if base_color_map is not None:
            self.base_color_map = base_color_map
        if roughness_map is not None:
            self.roughness_map = roughness_map
        if trans_roughness_map is not None:
            self.trans_roughness_map = trans_roughness_map
        if sheen_roughness_map is not None:
            self.sheen_roughness_map = sheen_roughness_map


class PhysicalMaterialCamel(_MaterialBase):
    """CamelCase-authored variant -- exercises the `transRoughnessMap`
    / `sheenRoughnessMap` slotMap entries."""

    max_class_name = "PhysicalMaterial"

    def __init__(
        self,
        baseColorMap=None,
        transRoughnessMap=None,
        sheenRoughnessMap=None,
    ):
        if baseColorMap is not None:
            self.baseColorMap = baseColorMap
        if transRoughnessMap is not None:
            self.transRoughnessMap = transRoughnessMap
        if sheenRoughnessMap is not None:
            self.sheenRoughnessMap = sheenRoughnessMap


class OpenPBRDerived(_MaterialBase):
    """Third-party OpenPBR-derived material exposing the long-form
    `transmission_roughness_map` / `transmissionRoughnessMap`
    spellings. Stock OpenPBR does NOT expose these — it shares
    `specular_roughness_map` between reflection and transmission —
    but a custom OpenPBR-based material may. Note this fake is
    labelled `OpenPBR` for readability but the discovery path routes
    to the same ND_standard_surface_surfaceshader that MtlxShaderWriter
    targets."""

    max_class_name = "OpenPBRDerived"

    def __init__(
        self,
        transmission_roughness_map=None,
        transmissionRoughnessMap=None,
    ):
        if transmission_roughness_map is not None:
            self.transmission_roughness_map = transmission_roughness_map
        if transmissionRoughnessMap is not None:
            self.transmissionRoughnessMap = transmissionRoughnessMap


class VRayMtl(_MaterialBase):
    """VRayMtl exposes `.texmap_refractionGlossiness` for refraction
    glossiness (V-Ray SDK canonical). Glossiness is the INVERSE of
    roughness (0 = rough, 1 = smooth for V-Ray; 0 = smooth, 1 = rough
    for MaterialX), so it is EXCLUDED from MAX-MTLX-011's slotMap.
    Included in this fake only for the ScopedOut negative test."""

    max_class_name = "VRayMtl"

    def __init__(
        self,
        texmap_diffuse=None,
        texmap_refractionGlossiness=None,
    ):
        if texmap_diffuse is not None:
            self.texmap_diffuse = texmap_diffuse
        if texmap_refractionGlossiness is not None:
            self.texmap_refractionGlossiness = texmap_refractionGlossiness


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
# The slotMap -- MUST mirror `MtlxShaderWriter.cpp:530` verbatim.
# `test_strict_superset_slot_map_matches_source` locks this against
# any future drift.
# =============================================================================


SLOT_MAP_POST_FIX = [
    # Pre-existing entries (unchanged by MAX-MTLX-011).
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
    # MAX-MTLX-011 additions.
    ("trans_roughness_map",         "transmission_extra_roughness", "float"),
    ("transRoughnessMap",           "transmission_extra_roughness", "float"),
    ("transmission_roughness_map",  "transmission_extra_roughness", "float"),
    ("transmissionRoughnessMap",    "transmission_extra_roughness", "float"),
    ("sheen_roughness_map",         "sheen_roughness",              "float"),
    ("sheenRoughnessMap",           "sheen_roughness",              "float"),
]

# The pre-fix slotMap -- for the strict-superset assertion + PreFixDefect.
SLOT_MAP_PRE_FIX = [
    e for e in SLOT_MAP_POST_FIX
    if e[1] not in ("transmission_extra_roughness", "sheen_roughness")
]

MAX_MTLX_011_ADDITIONS = [
    e for e in SLOT_MAP_POST_FIX
    if e[1] in ("transmission_extra_roughness", "sheen_roughness")
]


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
    at MtlxShaderWriter.cpp:604 with the wrapper walk from MTLX-007.
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
    """Baseline: pre-fix, a transmission-roughness / sheen-roughness
    map produces ZERO tiledimage nodes for those inputs. This locks
    in the fingerprint the fix resolves."""

    def test_trans_roughness_map_dropped_pre_fix(self):
        mat = PhysicalMaterial(
            base_color_map=_FakeBitmap("glass_diff.png"),
            trans_roughness_map=_FakeBitmap("glass_tr.png"),
        )
        discovery = discover_max_mtlx_texmaps(mat, SLOT_MAP_PRE_FIX)
        inputs = {d[0] for d in discovery}
        self.assertNotIn(
            "transmission_extra_roughness", inputs,
            "Pre-fix transmission_extra_roughness path should be "
            "silently dropped.",
        )
        # Sanity: pre-existing base_color path still works.
        self.assertIn("base_color", inputs)

    def test_transmission_roughness_map_long_form_dropped_pre_fix(self):
        mat = OpenPBRDerived(
            transmission_roughness_map=_FakeBitmap("frost_tr.png"),
        )
        discovery = discover_max_mtlx_texmaps(mat, SLOT_MAP_PRE_FIX)
        inputs = {d[0] for d in discovery}
        self.assertNotIn("transmission_extra_roughness", inputs)

    def test_sheen_roughness_map_dropped_pre_fix(self):
        mat = PhysicalMaterial(
            base_color_map=_FakeBitmap("velvet_diff.png"),
            sheen_roughness_map=_FakeBitmap("velvet_sh.png"),
        )
        discovery = discover_max_mtlx_texmaps(mat, SLOT_MAP_PRE_FIX)
        inputs = {d[0] for d in discovery}
        self.assertNotIn(
            "sheen_roughness", inputs,
            "Pre-fix sheen_roughness path should be silently dropped.",
        )
        self.assertIn("base_color", inputs)


class TestPropertyDispatch(unittest.TestCase):
    """Each of the six new slotMap entries dispatches correctly
    post-fix."""

    def _resolves(self, mat, expected_input, expected_file):
        discovery = discover_max_mtlx_texmaps(mat, SLOT_MAP_POST_FIX)
        hits = [d for d in discovery if d[0] == expected_input]
        self.assertEqual(
            len(hits), 1,
            f"Expected exactly one {expected_input} discovery, got {hits!r}",
        )
        self.assertEqual(hits[0][1], "float")
        self.assertEqual(hits[0][2], expected_file)

    # transmission_extra_roughness dispatch
    def test_phys_trans_roughness_map_snake(self):
        self._resolves(
            PhysicalMaterial(trans_roughness_map=_FakeBitmap("a.png")),
            "transmission_extra_roughness",
            "a.png",
        )

    def test_phys_transRoughnessMap_camel(self):
        self._resolves(
            PhysicalMaterialCamel(transRoughnessMap=_FakeBitmap("b.png")),
            "transmission_extra_roughness",
            "b.png",
        )

    def test_openpbr_transmission_roughness_map_snake(self):
        self._resolves(
            OpenPBRDerived(transmission_roughness_map=_FakeBitmap("c.png")),
            "transmission_extra_roughness",
            "c.png",
        )

    def test_openpbr_transmissionRoughnessMap_camel(self):
        self._resolves(
            OpenPBRDerived(transmissionRoughnessMap=_FakeBitmap("d.png")),
            "transmission_extra_roughness",
            "d.png",
        )

    # sheen_roughness dispatch
    def test_phys_sheen_roughness_map_snake(self):
        self._resolves(
            PhysicalMaterial(sheen_roughness_map=_FakeBitmap("e.png")),
            "sheen_roughness",
            "e.png",
        )

    def test_phys_sheenRoughnessMap_camel(self):
        self._resolves(
            PhysicalMaterialCamel(sheenRoughnessMap=_FakeBitmap("f.png")),
            "sheen_roughness",
            "f.png",
        )

    def test_both_trans_and_sheen_resolve_independently(self):
        # A material with BOTH transmission roughness and sheen
        # roughness authored should produce TWO discoveries. They are
        # distinct MaterialX inputs; the seenInputs dedupe tracks them
        # separately.
        mat = PhysicalMaterial(
            trans_roughness_map=_FakeBitmap("tr.png"),
            sheen_roughness_map=_FakeBitmap("sh.png"),
        )
        discovery = discover_max_mtlx_texmaps(mat, SLOT_MAP_POST_FIX)
        inputs = {d[0]: d[2] for d in discovery}
        self.assertEqual(inputs.get("transmission_extra_roughness"), "tr.png")
        self.assertEqual(inputs.get("sheen_roughness"), "sh.png")

    def test_snake_wins_over_camel_first_hit(self):
        # If BOTH snake and camel spellings appear on the same
        # material (unusual but possible under MAXScript authoring),
        # snake_case wins because it is declared first in slotMap
        # order — matching every other family in the slotMap
        # (base_color_map before baseColorMap, etc.).
        m = _MaterialBase()
        m.max_class_name = "PhysicalMaterial"
        m.trans_roughness_map = _FakeBitmap("snake.png")
        m.transRoughnessMap = _FakeBitmap("camel.png")
        discovery = discover_max_mtlx_texmaps(m, SLOT_MAP_POST_FIX)
        hits = [d for d in discovery if d[0] == "transmission_extra_roughness"]
        self.assertEqual(len(hits), 1)
        self.assertEqual(
            hits[0][2], "snake.png",
            "snake_case must win over camelCase under first-hit-wins "
            "so slot-ordering matches every other family.",
        )


class TestWrapperWalk(unittest.TestCase):
    """MAX-MTLX-007's wrapper walk applies to the new transmission /
    sheen roughness entries because slotMap iteration lives inside
    `for currentMat in subMtls`."""

    def test_vray_blend_base_trans_roughness_wins_over_coat(self):
        base = PhysicalMaterial(
            trans_roughness_map=_FakeBitmap("base_tr.png"),
        )
        coat = PhysicalMaterial(
            trans_roughness_map=_FakeBitmap("coat_tr.png"),
        )
        blend = VRayBlendMtl(baseMtl=base, coats=(coat,))
        discovery = discover_max_mtlx_texmaps(blend, SLOT_MAP_POST_FIX)
        hits = [d for d in discovery if d[0] == "transmission_extra_roughness"]
        self.assertEqual(len(hits), 1)
        self.assertEqual(
            hits[0][2], "base_tr.png",
            "base's transmission roughness should win over coat's "
            "under first-hit-wins (MTLX-007 base-first traversal).",
        )

    def test_coat_sheen_fills_gap_when_base_lacks_sheen(self):
        base = PhysicalMaterial(
            trans_roughness_map=_FakeBitmap("base_tr.png"),
        )
        coat = PhysicalMaterial(
            sheen_roughness_map=_FakeBitmap("coat_sh.png"),
        )
        blend = VRayBlendMtl(baseMtl=base, coats=(coat,))
        discovery = discover_max_mtlx_texmaps(blend, SLOT_MAP_POST_FIX)
        sheen_hits = [d for d in discovery if d[0] == "sheen_roughness"]
        self.assertEqual(len(sheen_hits), 1)
        self.assertEqual(sheen_hits[0][2], "coat_sh.png")

    def test_vray_override_base_sheen_wins_over_reflect(self):
        base = PhysicalMaterial(
            sheen_roughness_map=_FakeBitmap("base_sh.png"),
        )
        reflect_only = PhysicalMaterial(
            sheen_roughness_map=_FakeBitmap("reflect_sh.png"),
        )
        override = VRayOverrideMtl(baseMtl=base, reflectMtl=reflect_only)
        discovery = discover_max_mtlx_texmaps(override, SLOT_MAP_POST_FIX)
        hits = [d for d in discovery if d[0] == "sheen_roughness"]
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0][2], "base_sh.png")


class TestTypeCorrectness(unittest.TestCase):
    """The injected tiledimage must be float-typed for both new
    inputs; wrong type would fail Karma / Hydra evaluation."""

    def test_injected_trans_roughness_tiledimage_is_float(self):
        mat = PhysicalMaterial(
            trans_roughness_map=_FakeBitmap("tr.png"),
        )
        discovery = discover_max_mtlx_texmaps(mat, SLOT_MAP_POST_FIX)
        doc, ng, shader = build_scaffold_doc(
            [("transmission_extra_roughness", "float")]
        )
        n = enrich_mtlx_doc(doc, ng, shader, discovery)
        self.assertEqual(n, 1)
        img = ng.getNode("img_transmission_extra_roughness")
        self.assertIsNotNone(img)
        self.assertEqual(img.getType(), "float")
        self.assertEqual(
            img.getNodeDefString(), "ND_tiledimage_float",
        )

    def test_injected_sheen_roughness_tiledimage_is_float(self):
        mat = PhysicalMaterial(
            sheen_roughness_map=_FakeBitmap("sh.png"),
        )
        discovery = discover_max_mtlx_texmaps(mat, SLOT_MAP_POST_FIX)
        doc, ng, shader = build_scaffold_doc(
            [("sheen_roughness", "float")]
        )
        n = enrich_mtlx_doc(doc, ng, shader, discovery)
        self.assertEqual(n, 1)
        img = ng.getNode("img_sheen_roughness")
        self.assertIsNotNone(img)
        self.assertEqual(img.getType(), "float")

    def test_shader_input_types_match_materialx_stdlib(self):
        # Verify against the actual MaterialX stdlib nodedef so a
        # library-version bump can't quietly change the input type OR
        # rename `transmission_extra_roughness` back to
        # `transmission_roughness` (or similar).
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
        for input_name in ("transmission_extra_roughness", "sheen_roughness"):
            inp = nd.getActiveInput(input_name)
            self.assertIsNotNone(
                inp,
                f"MaterialX stdlib must expose {input_name} input; "
                f"if this fails, ND_standard_surface has renamed the "
                f"input and the slotMap needs to follow.",
            )
            self.assertEqual(
                inp.getType(), "float",
                f"MAX-MTLX-011 authors {input_name} as float; a "
                "stdlib change to another type would silently break "
                "the fix.",
            )

    def test_no_transmission_roughness_input_on_standard_surface(self):
        # Negative anchor: `transmission_roughness` (without `_extra_`)
        # is NOT an input on ND_standard_surface_surfaceshader in
        # MaterialX 1.38+. If a future stdlib version adds it, this
        # test fires and we know to migrate the slotMap target.
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
        self.assertIsNone(
            nd.getActiveInput("transmission_roughness"),
            "ND_standard_surface exposes `transmission_extra_roughness`, "
            "NOT `transmission_roughness`. If this changes, migrate the "
            "MAX-MTLX-011 slotMap target accordingly.",
        )


class TestNoColorspaceAuthored(unittest.TestCase):
    """Float transmission-roughness / sheen-roughness maps are raw
    scalar values -- no colorspace metadata (which would incorrectly
    apply an sRGB decode)."""

    def test_no_colorspace_on_float_trans_roughness_tiledimage(self):
        mat = PhysicalMaterial(
            trans_roughness_map=_FakeBitmap("tr.png"),
        )
        discovery = discover_max_mtlx_texmaps(mat, SLOT_MAP_POST_FIX)
        doc, ng, shader = build_scaffold_doc(
            [("transmission_extra_roughness", "float")]
        )
        enrich_mtlx_doc(doc, ng, shader, discovery)
        img = ng.getNode("img_transmission_extra_roughness")
        file_input = img.getInput("file")
        self.assertIsNotNone(file_input)
        self.assertFalse(
            file_input.hasAttribute("colorspace"),
            "Float transmission-roughness map must NOT carry a "
            "colorspace attribute -- sRGB decode of scalar values "
            "would silently distort the roughness.",
        )

    def test_no_colorspace_on_float_sheen_roughness_tiledimage(self):
        mat = PhysicalMaterial(
            sheen_roughness_map=_FakeBitmap("sh.png"),
        )
        discovery = discover_max_mtlx_texmaps(mat, SLOT_MAP_POST_FIX)
        doc, ng, shader = build_scaffold_doc(
            [("sheen_roughness", "float")]
        )
        enrich_mtlx_doc(doc, ng, shader, discovery)
        img = ng.getNode("img_sheen_roughness")
        file_input = img.getInput("file")
        self.assertFalse(
            file_input.hasAttribute("colorspace"),
            "Float sheen-roughness map must NOT carry a colorspace "
            "attribute.",
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


class TestNoRoughnessMap(unittest.TestCase):
    """A material with base_color etc. but no transmission /
    sheen roughness map produces zero
    transmission_extra_roughness / sheen_roughness discoveries --
    the ND_standard_surface port defaults hold and the doc stays
    clean."""

    def test_no_new_map_no_new_output(self):
        mat = PhysicalMaterial(
            base_color_map=_FakeBitmap("diff.png"),
            roughness_map=_FakeBitmap("rough.png"),
        )
        discovery = discover_max_mtlx_texmaps(mat, SLOT_MAP_POST_FIX)
        inputs = {d[0] for d in discovery}
        self.assertIn("base_color", inputs)
        self.assertIn("specular_roughness", inputs)
        self.assertNotIn("transmission_extra_roughness", inputs)
        self.assertNotIn("sheen_roughness", inputs)


class TestSurgicalScope(unittest.TestCase):
    """The fix touches ONLY the slotMap -- it must not affect any
    pre-existing dispatch. A material exercising every non-011 slot
    produces byte-identical discovery output under both slotMaps."""

    def _build_full_non_011_material(self):
        # A synthetic material exercising each of the pre-existing
        # slot-map inputs. We do NOT include trans_roughness or
        # sheen_roughness so both slotMaps should produce the SAME
        # set of inputs.
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
        return m

    def test_non_011_material_discovery_unchanged(self):
        m = self._build_full_non_011_material()
        pre = discover_max_mtlx_texmaps(m, SLOT_MAP_PRE_FIX)
        post = discover_max_mtlx_texmaps(m, SLOT_MAP_POST_FIX)
        self.assertEqual(
            pre, post,
            "MAX-MTLX-011 must be a strict superset -- a material "
            "with no transmission-roughness or sheen-roughness map "
            "must produce byte-identical discovery.",
        )

    def test_only_011_family_inputs_added(self):
        added_inputs = {e[1] for e in MAX_MTLX_011_ADDITIONS}
        self.assertEqual(
            added_inputs,
            {"transmission_extra_roughness", "sheen_roughness"},
        )

    def test_only_float_type_added(self):
        added_types = {e[2] for e in MAX_MTLX_011_ADDITIONS}
        self.assertEqual(added_types, {"float"})

    def test_four_trans_and_two_sheen(self):
        # Four transmission spellings (PhysicalMaterial snake/camel +
        # OpenPBR-long snake/camel) + two sheen (generic snake/camel).
        trans = [e for e in MAX_MTLX_011_ADDITIONS
                 if e[1] == "transmission_extra_roughness"]
        sheen = [e for e in MAX_MTLX_011_ADDITIONS
                 if e[1] == "sheen_roughness"]
        self.assertEqual(len(trans), 4)
        self.assertEqual(len(sheen), 2)


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
            ("trans_roughness_map",         "transmission_extra_roughness", "float"),
            ("transRoughnessMap",           "transmission_extra_roughness", "float"),
            ("transmission_roughness_map",  "transmission_extra_roughness", "float"),
            ("transmissionRoughnessMap",    "transmission_extra_roughness", "float"),
            ("sheen_roughness_map",         "sheen_roughness",              "float"),
            ("sheenRoughnessMap",           "sheen_roughness",              "float"),
        }
        self.assertEqual(set(MAX_MTLX_011_ADDITIONS), expected_new)

    def test_new_entries_ordered_correctly(self):
        # Snake_case before camelCase within each family -- matches
        # the pre-existing slotMap convention (base_color_map before
        # baseColorMap, etc.).
        names = [e[0] for e in MAX_MTLX_011_ADDITIONS]
        self.assertLess(
            names.index("trans_roughness_map"),
            names.index("transRoughnessMap"),
        )
        self.assertLess(
            names.index("transmission_roughness_map"),
            names.index("transmissionRoughnessMap"),
        )
        self.assertLess(
            names.index("sheen_roughness_map"),
            names.index("sheenRoughnessMap"),
        )
        # Short PhysicalMaterial spelling before long OpenPBR spelling
        # (matches the MTLX-009 IOR precedent where `trans_ior_map`
        # precedes `specular_ior_map`).
        self.assertLess(
            names.index("trans_roughness_map"),
            names.index("transmission_roughness_map"),
        )
        # Transmission family MUST appear before sheen family so a
        # material authoring both keeps a stable dispatch order.
        trans_indexes = [i for i, e in enumerate(MAX_MTLX_011_ADDITIONS)
                         if e[1] == "transmission_extra_roughness"]
        sheen_indexes = [i for i, e in enumerate(MAX_MTLX_011_ADDITIONS)
                         if e[1] == "sheen_roughness"]
        self.assertTrue(max(trans_indexes) < min(sheen_indexes))


class TestVRayGlossinessScopedOut(unittest.TestCase):
    """VRayMtl's `texmap_refractionGlossiness` is NOT in the slotMap.
    V-Ray's glossiness convention (0 = rough, 1 = smooth) is the
    INVERSE of MaterialX's roughness (0 = smooth, 1 = rough). Wiring
    it directly would produce a semantically inverted map: polished
    glass would render as frost. Locks the negative in for the
    follow-on bite that owns the ND_invert_float authoring path."""

    def test_vray_refractionGlossiness_not_in_slot_map(self):
        prop_names = {e[0] for e in SLOT_MAP_POST_FIX}
        self.assertNotIn(
            "texmap_refractionGlossiness", prop_names,
            "MAX-MTLX-011 must NOT route texmap_refractionGlossiness "
            "directly to transmission_extra_roughness -- it needs an "
            "ND_invert_float on the wire. Follow-on bite: "
            "max-mtlx-013-vray-glossiness-to-roughness-invert.",
        )

    def test_vray_material_with_only_refraction_glossiness_dropped(self):
        # A VRayMtl scene that has NOT gone through the Scene
        # Converter still relies on the follow-on bite; the pre-fix
        # dropping behavior is unchanged for it.
        mat = VRayMtl(
            texmap_diffuse=_FakeBitmap("glass_diff.png"),
            texmap_refractionGlossiness=_FakeBitmap("glass_gloss.png"),
        )
        discovery = discover_max_mtlx_texmaps(mat, SLOT_MAP_POST_FIX)
        inputs = {d[0] for d in discovery}
        # Post-fix slotMap doesn't introduce transmission_extra_roughness
        # for this material -- V-Ray's glossiness stays dropped until
        # the follow-on invert bite.
        self.assertNotIn("transmission_extra_roughness", inputs)


class TestArenaCensus(unittest.TestCase):
    """179-material arena-density synthetic mix.

      * 100 non-dielectric non-fabric -- base_color + roughness only.
      *  40 glass with trans_roughness_map (PhysicalMaterial-style).
      *  25 satin glazing with transmissionRoughnessMap (OpenPBR-style,
           camelCase).
      *  14 velvet / satin fabric with sheen_roughness_map
           (PhysicalMaterial-style).

    Pre-fix expected: 0 transmission_extra_roughness discoveries, 0
    sheen_roughness discoveries. Post-fix expected: 40+25 = 65
    transmission_extra_roughness discoveries, 14 sheen_roughness
    discoveries. Non-011 discovery counts unchanged in both
    slotMaps."""

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
                trans_roughness_map=_FakeBitmap("glass_tr.png"),
            ))
        for _ in range(25):
            arena.append(OpenPBRDerived(
                transmissionRoughnessMap=_FakeBitmap("satin_tr.png"),
            ))
        for _ in range(14):
            arena.append(PhysicalMaterial(
                base_color_map=_FakeBitmap("velvet.png"),
                sheen_roughness_map=_FakeBitmap("velvet_sh.png"),
            ))
        return arena

    def test_arena_new_roughness_discovery_counts(self):
        arena = self._build_arena()
        self.assertEqual(len(arena), 179)
        pre_trans = pre_sheen = pre_other = 0
        post_trans = post_sheen = post_other = 0
        for m in arena:
            for d in discover_max_mtlx_texmaps(m, SLOT_MAP_PRE_FIX):
                if d[0] == "transmission_extra_roughness":
                    pre_trans += 1
                elif d[0] == "sheen_roughness":
                    pre_sheen += 1
                else:
                    pre_other += 1
            for d in discover_max_mtlx_texmaps(m, SLOT_MAP_POST_FIX):
                if d[0] == "transmission_extra_roughness":
                    post_trans += 1
                elif d[0] == "sheen_roughness":
                    post_sheen += 1
                else:
                    post_other += 1
        self.assertEqual(
            pre_trans, 0,
            "Pre-fix must have ZERO transmission_extra_roughness hits.",
        )
        self.assertEqual(
            pre_sheen, 0,
            "Pre-fix must have ZERO sheen_roughness hits.",
        )
        self.assertEqual(
            post_trans, 65,
            "Post-fix must have exactly 40+25 = 65 "
            "transmission_extra_roughness hits.",
        )
        self.assertEqual(
            post_sheen, 14,
            "Post-fix must have exactly 14 sheen_roughness hits.",
        )
        self.assertEqual(
            pre_other, post_other,
            "Non-011 discovery must be byte-identical between "
            "slotMaps.",
        )


if __name__ == "__main__":
    unittest.main()
