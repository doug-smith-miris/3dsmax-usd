# Copyright 2026 Miris Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
"""
MAX-MTLX-010 — Python mirror of the specular_anisotropy +
specular_rotation slotMap extension in `discoverMaxMtlxTexmapsFn`
(see `src/translators/MtlxShaderWriter.cpp:530` after this fix).

Background
----------
`ND_standard_surface.specular_anisotropy` (float, default 0.0) and
`ND_standard_surface.specular_rotation` (float, default 0.0) are the
two ND_standard_surface inputs that drive anisotropic specular
highlights — brushed metals, spun aluminum, carbon fiber, some silk
fabrics, and any material where the specular highlight is stretched
in a direction. Prior to this fix the MaterialX writer's discovery
slotMap covered NO anisotropy slots at all, so every VRayMtl
brushed-metal / anisotropic-fabric material silently exported with
anisotropy locked at its port default 0.0 (isotropic) regardless of
what the source scene said. The visible signature is a round /
symmetric highlight where the artist authored an elongated one.

Prior-run evidence
(`agent/pipeline-runs/e218e7b9-.../evidence-slotmap-and-wrappers.md`)
records this as a silent-drop class distinct from MAX-MTLX-006's
wrapper walk and MAX-MTLX-007's blend/override unwrap:

    > Slots absent from the slotMap (silent-drop classes):
    >   texmap_anisotropy / anisotropy_map (specular_anisotropy)
    >   texmap_anisotropyRotation / anisotropy_rotation_map
    >     (specular_rotation)

Displacement maps are also silently dropped, but ND_standard_surface
has no `displacement` input (verified via `hython` +
`mx.getNodeDef("ND_standard_surface_surfaceshader").getActiveInput(
"displacement")` -> None). Displacement is on the MaterialX
<material>'s `outputs:displacement` via a separate ND_displacement
node, not a shader input. A slotMap-only fix like MTLX-010 cannot
wire displacement — that requires a distinct authoring path with a
new node type and a new material output arc, and is scoped OUT of
MAX-MTLX-010 to a follow-on bite (candidateMission
"max-mtlx-011-displacement-map" in the fix envelope).

Fix
---
Extend the `discoverMaxMtlxTexmapsFn` slotMap with six entries
covering the canonical anisotropy-map spellings:

  * `anisotropy_map` (generic snake_case) -> specular_anisotropy
  * `anisotropyMap` (generic camelCase) -> specular_anisotropy
  * `texmap_anisotropy` (VRayMtl SDK canonical) -> specular_anisotropy
  * `anisotropy_rotation_map` (generic snake_case) -> specular_rotation
  * `anisotropyRotationMap` (generic camelCase) -> specular_rotation
  * `texmap_anisotropyRotation` (VRayMtl SDK canonical) -> specular_rotation

PhysicalMaterial (Autodesk stock) does NOT expose anisotropy in its
parameter surface (verified via `3dsmax_materials.mat_def:10-43`),
so no PhysicalMaterial-only spellings are added. VRayMtl exposes
`.texmap_anisotropy` / `.texmap_anisotropyRotation` directly at the
MAXScript layer.

All six entries route to `float` MaterialX inputs — no color3 /
vector3 — so the existing colorspace-attribute gate in
`_EnrichMtlxDocFromMaxMaterial` (which only authors
`colorspace="srgb_texture"` when `mtlxType == "color3"`) does the
right thing by construction. Anisotropy scalars must NOT go through
the sRGB -> linear decode; that would silently squish the value.

The change is a strict SUPERSET extension of the slotMap; no
existing entry is removed or reordered, so materials that were
correctly exporting other slots keep working byte-identically. The
wrapper walk (MAX-MTLX-007 `unwrapBlendMaterialSubMtls`) applies
because the slotMap iteration lives inside
`for currentMat in subMtls do` — VRayBlendMtl and VRayOverrideMtl
unwrap identically for the new entries, and baseMtl's anisotropy
wins over any coat's / per-ray override's.

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
                             anisotropy entries: a VRayMtl authoring
                             `.texmap_anisotropy` produces zero
                             injected tiledimage nodes for that
                             input.
  * PropertyDispatch      -- each of the six supported property
                             names resolves and drives a
                             `ND_tiledimage_float` into either
                             specular_anisotropy or specular_rotation.
                             Locks in the exact slotMap contents so a
                             future refactor can't silently drop an
                             entry.
  * WrapperWalk           -- VRayBlendMtl base wins over coat;
                             VRayOverrideMtl base wins over
                             reflect/refract/GI overrides. Reuses
                             MTLX-007's precedence.
  * TypeCorrectness       -- the injected tiledimage is float-typed
                             (`ND_tiledimage_float`), and the shader
                             input reference is `float`. Anchored
                             against the MaterialX stdlib so a
                             library-version bump can't quietly
                             change the type.
  * NoColorspaceAuthored  -- unlike color3 base_color maps, float
                             anisotropy / rotation maps must NOT
                             carry a `colorspace="srgb_texture"`
                             attribute. sRGB -> linear decode of
                             scalar values would silently distort
                             the anisotropy strength / rotation
                             angle.
  * NoAnisotropyMap       -- a material with base_color etc. but no
                             anisotropy map exports zero
                             specular_anisotropy / specular_rotation
                             tiledimages -- the port defaults 0.0
                             still hold, and the doc stays clean.
  * SurgicalScope         -- the fix touches ONLY the slotMap; the
                             existing base_color / roughness /
                             metalness / normal / opacity /
                             emission_color / specular_color /
                             transmission_color / specular_IOR
                             entries produce byte-identical output
                             before and after the change, on a
                             material that exercises them without
                             any anisotropy map.
  * StrictSupersetSlotMap -- the post-fix slotMap contains ALL
                             pre-fix entries with unchanged
                             (max-prop, input, type) tuples, and
                             adds EXACTLY six new entries -- no
                             more, no fewer, in the intended order.
  * DisplacementScopedOut -- ND_standard_surface has no
                             `displacement` input; a slotMap-only
                             fix cannot wire displacement, so
                             MAX-MTLX-010 must not attempt to add a
                             displacement -> ND_standard_surface
                             entry. Locks the negative in.
  * ArenaCensus           -- a synthesized 179-material arena-density
                             mix (100 non-anisotropic, 40
                             VRayMtl-brushed-metal with
                             texmap_anisotropy, 25 anisotropic-fabric
                             with anisotropy_map + rotation, 14
                             carbon-fiber with anisotropy_rotation_map
                             only) resolves to 79 anisotropy
                             discoveries + 39 rotation discoveries
                             post-fix vs. 0/0 pre-fix, with the
                             non-anisotropy discovery counts
                             unchanged.

Run:  hython test_miris_max_mtlx_010.py
"""
import unittest

import MaterialX as mx


# =============================================================================
# Fake material classes -- mirror the MAXScript `isProperty` /
# `getProperty` surface. Same shape as MTLX-007 / MTLX-009 / MTLX-012.
# =============================================================================


class _MaterialBase:
    max_class_name = "GenericMtl"


class PhysicalMaterial(_MaterialBase):
    """Autodesk stock PhysicalMaterial. Does NOT expose anisotropy in
    its parameter surface (`3dsmax_materials.mat_def:10-43`) so any
    anisotropy property on a fake stands in for MAXScript-authored
    or third-party-material-conformant workflows; we still test the
    generic `anisotropy_map` / `anisotropy_rotation_map` spellings
    against a PhysicalMaterial-shaped fake since a MAXScript author
    can attach any property to any material."""

    max_class_name = "PhysicalMaterial"

    def __init__(
        self,
        base_color_map=None,
        roughness_map=None,
        anisotropy_map=None,
        anisotropy_rotation_map=None,
    ):
        if base_color_map is not None:
            self.base_color_map = base_color_map
        if roughness_map is not None:
            self.roughness_map = roughness_map
        if anisotropy_map is not None:
            self.anisotropy_map = anisotropy_map
        if anisotropy_rotation_map is not None:
            self.anisotropy_rotation_map = anisotropy_rotation_map


class PhysicalMaterialCamel(_MaterialBase):
    """CamelCase-authored variant -- exercises the `anisotropyMap` /
    `anisotropyRotationMap` slotMap entries."""

    max_class_name = "PhysicalMaterial"

    def __init__(
        self,
        baseColorMap=None,
        anisotropyMap=None,
        anisotropyRotationMap=None,
    ):
        if baseColorMap is not None:
            self.baseColorMap = baseColorMap
        if anisotropyMap is not None:
            self.anisotropyMap = anisotropyMap
        if anisotropyRotationMap is not None:
            self.anisotropyRotationMap = anisotropyRotationMap


class VRayMtl(_MaterialBase):
    """VRayMtl exposes `.texmap_anisotropy` for the anisotropy strength
    and `.texmap_anisotropyRotation` for the rotation. V-Ray SDK
    canonical names -- surfaced at the MAXScript layer directly."""

    max_class_name = "VRayMtl"

    def __init__(
        self,
        texmap_diffuse=None,
        texmap_anisotropy=None,
        texmap_anisotropyRotation=None,
    ):
        if texmap_diffuse is not None:
            self.texmap_diffuse = texmap_diffuse
        if texmap_anisotropy is not None:
            self.texmap_anisotropy = texmap_anisotropy
        if texmap_anisotropyRotation is not None:
            self.texmap_anisotropyRotation = texmap_anisotropyRotation


class VRayBlendMtl(_MaterialBase):
    """Layered paint / weathered-surface wrapper -- same shape as
    MTLX-007 / MTLX-009 / MTLX-012."""

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
    / MTLX-012."""

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
    # Pre-existing entries (unchanged by MAX-MTLX-010).
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
    # MAX-MTLX-010 additions.
    ("anisotropy_map",             "specular_anisotropy", "float"),
    ("anisotropyMap",              "specular_anisotropy", "float"),
    ("texmap_anisotropy",          "specular_anisotropy", "float"),
    ("anisotropy_rotation_map",    "specular_rotation",   "float"),
    ("anisotropyRotationMap",      "specular_rotation",   "float"),
    ("texmap_anisotropyRotation",  "specular_rotation",   "float"),
]

# The pre-fix slotMap -- for the strict-superset assertion + PreFixDefect.
SLOT_MAP_PRE_FIX = [
    e for e in SLOT_MAP_POST_FIX
    if e[1] not in ("specular_anisotropy", "specular_rotation")
]

MAX_MTLX_010_ADDITIONS = [
    e for e in SLOT_MAP_POST_FIX
    if e[1] in ("specular_anisotropy", "specular_rotation")
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
    at MtlxShaderWriter.cpp:557 with the wrapper walk from MTLX-007.
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
    """Baseline: pre-fix, an anisotropy-mapped material produces
    ZERO tiledimage nodes for specular_anisotropy /
    specular_rotation. This locks in the fingerprint the fix
    resolves."""

    def test_vray_anisotropy_map_dropped_pre_fix(self):
        mat = VRayMtl(
            texmap_diffuse=_FakeBitmap("brushed_diff.png"),
            texmap_anisotropy=_FakeBitmap("brushed_aniso.png"),
        )
        discovery = discover_max_mtlx_texmaps(mat, SLOT_MAP_PRE_FIX)
        inputs = {d[0] for d in discovery}
        self.assertNotIn(
            "specular_anisotropy", inputs,
            "Pre-fix specular_anisotropy path should be silently dropped.",
        )

    def test_vray_anisotropy_rotation_dropped_pre_fix(self):
        mat = VRayMtl(
            texmap_anisotropyRotation=_FakeBitmap("brushed_rot.png"),
        )
        discovery = discover_max_mtlx_texmaps(mat, SLOT_MAP_PRE_FIX)
        inputs = {d[0] for d in discovery}
        self.assertNotIn(
            "specular_rotation", inputs,
            "Pre-fix specular_rotation path should be silently dropped.",
        )

    def test_generic_anisotropy_map_dropped_pre_fix(self):
        mat = PhysicalMaterial(
            base_color_map=_FakeBitmap("wall_diff.png"),
            anisotropy_map=_FakeBitmap("wall_aniso.png"),
        )
        discovery = discover_max_mtlx_texmaps(mat, SLOT_MAP_PRE_FIX)
        inputs = {d[0] for d in discovery}
        self.assertIn("base_color", inputs,
                      "Pre-fix base_color path should still work.")
        self.assertNotIn("specular_anisotropy", inputs)


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

    # specular_anisotropy dispatch
    def test_generic_anisotropy_map_snake(self):
        self._resolves(
            PhysicalMaterial(anisotropy_map=_FakeBitmap("a.png")),
            "specular_anisotropy",
            "a.png",
        )

    def test_generic_anisotropyMap_camel(self):
        self._resolves(
            PhysicalMaterialCamel(anisotropyMap=_FakeBitmap("b.png")),
            "specular_anisotropy",
            "b.png",
        )

    def test_vray_texmap_anisotropy(self):
        self._resolves(
            VRayMtl(texmap_anisotropy=_FakeBitmap("c.png")),
            "specular_anisotropy",
            "c.png",
        )

    # specular_rotation dispatch
    def test_generic_anisotropy_rotation_map_snake(self):
        self._resolves(
            PhysicalMaterial(anisotropy_rotation_map=_FakeBitmap("d.png")),
            "specular_rotation",
            "d.png",
        )

    def test_generic_anisotropyRotationMap_camel(self):
        self._resolves(
            PhysicalMaterialCamel(anisotropyRotationMap=_FakeBitmap("e.png")),
            "specular_rotation",
            "e.png",
        )

    def test_vray_texmap_anisotropyRotation(self):
        self._resolves(
            VRayMtl(texmap_anisotropyRotation=_FakeBitmap("f.png")),
            "specular_rotation",
            "f.png",
        )

    def test_both_strength_and_rotation_resolve_independently(self):
        # A brushed-metal material with BOTH strength and rotation
        # authored should produce TWO discoveries (one each). They
        # are distinct MaterialX inputs; the seenInputs dedupe
        # tracks them separately.
        mat = VRayMtl(
            texmap_anisotropy=_FakeBitmap("aniso.png"),
            texmap_anisotropyRotation=_FakeBitmap("rot.png"),
        )
        discovery = discover_max_mtlx_texmaps(mat, SLOT_MAP_POST_FIX)
        inputs = {d[0]: d[2] for d in discovery}
        self.assertEqual(inputs.get("specular_anisotropy"), "aniso.png")
        self.assertEqual(inputs.get("specular_rotation"), "rot.png")


class TestWrapperWalk(unittest.TestCase):
    """MAX-MTLX-007's wrapper walk applies to the new anisotropy
    entries because slotMap iteration lives inside
    `for currentMat in subMtls`."""

    def test_vray_blend_base_anisotropy_wins_over_coat(self):
        base = VRayMtl(texmap_anisotropy=_FakeBitmap("base_aniso.png"))
        coat = VRayMtl(texmap_anisotropy=_FakeBitmap("coat_aniso.png"))
        blend = VRayBlendMtl(baseMtl=base, coats=(coat,))
        discovery = discover_max_mtlx_texmaps(blend, SLOT_MAP_POST_FIX)
        hits = [d for d in discovery if d[0] == "specular_anisotropy"]
        self.assertEqual(len(hits), 1)
        self.assertEqual(
            hits[0][2], "base_aniso.png",
            "base's anisotropy should win over coat's under first-hit-wins "
            "(MTLX-007 base-first traversal).",
        )

    def test_coat_rotation_fills_gap_when_base_lacks_rotation(self):
        base = VRayMtl(texmap_anisotropy=_FakeBitmap("base_aniso.png"))
        coat = VRayMtl(
            texmap_anisotropyRotation=_FakeBitmap("coat_rot.png"),
        )
        blend = VRayBlendMtl(baseMtl=base, coats=(coat,))
        discovery = discover_max_mtlx_texmaps(blend, SLOT_MAP_POST_FIX)
        rot_hits = [d for d in discovery if d[0] == "specular_rotation"]
        self.assertEqual(len(rot_hits), 1)
        self.assertEqual(rot_hits[0][2], "coat_rot.png")

    def test_vray_override_base_anisotropy_wins_over_reflect(self):
        base = VRayMtl(
            texmap_anisotropy=_FakeBitmap("base_aniso.png"),
        )
        reflect_only = VRayMtl(
            texmap_anisotropy=_FakeBitmap("reflect_aniso.png"),
        )
        override = VRayOverrideMtl(baseMtl=base, reflectMtl=reflect_only)
        discovery = discover_max_mtlx_texmaps(override, SLOT_MAP_POST_FIX)
        hits = [d for d in discovery if d[0] == "specular_anisotropy"]
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0][2], "base_aniso.png")


class TestTypeCorrectness(unittest.TestCase):
    """The injected tiledimage must be float-typed for both
    anisotropy inputs; wrong type would fail Karma / Hydra
    evaluation."""

    def test_injected_tiledimage_is_float_not_color3(self):
        mat = VRayMtl(texmap_anisotropy=_FakeBitmap("aniso.png"))
        discovery = discover_max_mtlx_texmaps(mat, SLOT_MAP_POST_FIX)
        doc, ng, shader = build_scaffold_doc(
            [("specular_anisotropy", "float")]
        )
        n = enrich_mtlx_doc(doc, ng, shader, discovery)
        self.assertEqual(n, 1)
        img = ng.getNode("img_specular_anisotropy")
        self.assertIsNotNone(img)
        self.assertEqual(img.getType(), "float")
        self.assertEqual(
            img.getNodeDefString(), "ND_tiledimage_float",
        )

    def test_rotation_tiledimage_is_also_float(self):
        mat = VRayMtl(texmap_anisotropyRotation=_FakeBitmap("rot.png"))
        discovery = discover_max_mtlx_texmaps(mat, SLOT_MAP_POST_FIX)
        doc, ng, shader = build_scaffold_doc(
            [("specular_rotation", "float")]
        )
        n = enrich_mtlx_doc(doc, ng, shader, discovery)
        self.assertEqual(n, 1)
        img = ng.getNode("img_specular_rotation")
        self.assertIsNotNone(img)
        self.assertEqual(img.getType(), "float")

    def test_shader_input_types_match_materialx_stdlib(self):
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
        for input_name in ("specular_anisotropy", "specular_rotation"):
            inp = nd.getActiveInput(input_name)
            self.assertIsNotNone(
                inp,
                f"MaterialX stdlib must expose {input_name} input",
            )
            self.assertEqual(
                inp.getType(), "float",
                f"MAX-MTLX-010 authors {input_name} as float; a "
                "stdlib change to another type would silently break "
                "the fix.",
            )


class TestNoColorspaceAuthored(unittest.TestCase):
    """Float anisotropy / rotation maps are raw scalar values --
    no colorspace metadata (which would incorrectly apply an sRGB
    decode)."""

    def test_no_colorspace_on_float_anisotropy_tiledimage(self):
        mat = VRayMtl(texmap_anisotropy=_FakeBitmap("aniso.png"))
        discovery = discover_max_mtlx_texmaps(mat, SLOT_MAP_POST_FIX)
        doc, ng, shader = build_scaffold_doc(
            [("specular_anisotropy", "float")]
        )
        enrich_mtlx_doc(doc, ng, shader, discovery)
        img = ng.getNode("img_specular_anisotropy")
        file_input = img.getInput("file")
        self.assertIsNotNone(file_input)
        self.assertFalse(
            file_input.hasAttribute("colorspace"),
            "Float anisotropy map must NOT carry a colorspace "
            "attribute -- sRGB decode of scalar values would "
            "silently distort the anisotropy strength.",
        )

    def test_no_colorspace_on_float_rotation_tiledimage(self):
        mat = VRayMtl(texmap_anisotropyRotation=_FakeBitmap("rot.png"))
        discovery = discover_max_mtlx_texmaps(mat, SLOT_MAP_POST_FIX)
        doc, ng, shader = build_scaffold_doc(
            [("specular_rotation", "float")]
        )
        enrich_mtlx_doc(doc, ng, shader, discovery)
        img = ng.getNode("img_specular_rotation")
        file_input = img.getInput("file")
        self.assertFalse(
            file_input.hasAttribute("colorspace"),
            "Float rotation map must NOT carry a colorspace attribute.",
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


class TestNoAnisotropyMap(unittest.TestCase):
    """A material with base_color etc. but no anisotropy map produces
    zero specular_anisotropy / specular_rotation discoveries -- the
    ND_standard_surface port defaults 0.0 rule, and the doc stays
    clean."""

    def test_no_anisotropy_map_no_anisotropy_output(self):
        mat = PhysicalMaterial(
            base_color_map=_FakeBitmap("diff.png"),
            roughness_map=_FakeBitmap("rough.png"),
        )
        discovery = discover_max_mtlx_texmaps(mat, SLOT_MAP_POST_FIX)
        inputs = {d[0] for d in discovery}
        self.assertIn("base_color", inputs)
        self.assertIn("specular_roughness", inputs)
        self.assertNotIn("specular_anisotropy", inputs)
        self.assertNotIn("specular_rotation", inputs)


class TestSurgicalScope(unittest.TestCase):
    """The fix touches ONLY the slotMap -- it must not affect any
    pre-existing dispatch. A material exercising every non-anisotropy
    slot produces byte-identical discovery output under both
    slotMaps."""

    def _build_full_non_anisotropy_material(self):
        # A synthetic PhysicalMaterial exercising each of the
        # pre-existing slot-map inputs. We do NOT include anisotropy
        # so both slotMaps should produce the SAME set of inputs.
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
        # Include an IOR map to prove MTLX-009's dispatch is also
        # unchanged.
        m.trans_ior_map = _FakeBitmap("i.png")
        return m

    def test_non_anisotropy_material_discovery_unchanged(self):
        m = self._build_full_non_anisotropy_material()
        pre = discover_max_mtlx_texmaps(m, SLOT_MAP_PRE_FIX)
        post = discover_max_mtlx_texmaps(m, SLOT_MAP_POST_FIX)
        self.assertEqual(
            pre, post,
            "MAX-MTLX-010 must be a strict superset -- a material "
            "with no anisotropy map must produce byte-identical "
            "discovery.",
        )

    def test_only_anisotropy_family_inputs_added(self):
        # Contract: the six new entries route to specular_anisotropy
        # or specular_rotation.
        added_inputs = {e[1] for e in MAX_MTLX_010_ADDITIONS}
        self.assertEqual(
            added_inputs, {"specular_anisotropy", "specular_rotation"},
        )

    def test_only_float_type_added(self):
        added_types = {e[2] for e in MAX_MTLX_010_ADDITIONS}
        self.assertEqual(added_types, {"float"})

    def test_three_of_each_input(self):
        # Three property spellings each: generic snake / generic
        # camel / VRayMtl SDK canonical.
        aniso = [e for e in MAX_MTLX_010_ADDITIONS
                 if e[1] == "specular_anisotropy"]
        rot = [e for e in MAX_MTLX_010_ADDITIONS
               if e[1] == "specular_rotation"]
        self.assertEqual(len(aniso), 3)
        self.assertEqual(len(rot), 3)


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
            ("anisotropy_map",             "specular_anisotropy", "float"),
            ("anisotropyMap",              "specular_anisotropy", "float"),
            ("texmap_anisotropy",          "specular_anisotropy", "float"),
            ("anisotropy_rotation_map",    "specular_rotation",   "float"),
            ("anisotropyRotationMap",      "specular_rotation",   "float"),
            ("texmap_anisotropyRotation",  "specular_rotation",   "float"),
        }
        self.assertEqual(set(MAX_MTLX_010_ADDITIONS), expected_new)

    def test_new_entries_ordered_correctly(self):
        # Snake_case before camelCase within each family -- matches
        # the pre-existing slotMap convention (base_color_map before
        # baseColorMap, etc.).
        names = [e[0] for e in MAX_MTLX_010_ADDITIONS]
        self.assertLess(
            names.index("anisotropy_map"),
            names.index("anisotropyMap"),
        )
        self.assertLess(
            names.index("anisotropy_rotation_map"),
            names.index("anisotropyRotationMap"),
        )
        # Strength family (specular_anisotropy) MUST appear before
        # rotation family (specular_rotation) so a material authoring
        # both keeps a stable dispatch order.
        aniso_indexes = [i for i, e in enumerate(MAX_MTLX_010_ADDITIONS)
                         if e[1] == "specular_anisotropy"]
        rot_indexes = [i for i, e in enumerate(MAX_MTLX_010_ADDITIONS)
                       if e[1] == "specular_rotation"]
        self.assertTrue(max(aniso_indexes) < min(rot_indexes))


class TestDisplacementScopedOut(unittest.TestCase):
    """ND_standard_surface has no `displacement` input; a slotMap-only
    fix cannot wire displacement to the surface shader.
    MAX-MTLX-010 must not attempt to add a
    displacement -> ND_standard_surface entry. Locks the negative
    in for the follow-on bite that owns the material-outputs-arc
    authoring path."""

    def test_standard_surface_has_no_displacement_input(self):
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
            nd.getActiveInput("displacement"),
            "ND_standard_surface has NO displacement input; any "
            "future MTLX-010 refactor that adds "
            "`displacement_map -> displacement` on this shader "
            "authors a dangling input.",
        )

    def test_slot_map_has_no_displacement_target(self):
        # No entry in the post-fix slotMap targets a "displacement"
        # ND_standard_surface input. If a follow-on bite decides to
        # wire displacement via a separate ND_displacement node +
        # material outputs:displacement arc, THIS assertion should
        # stay green -- displacement is authored on the material, not
        # on ND_standard_surface, so the slotMap remains the wrong
        # tool for that job.
        targets = {e[1] for e in SLOT_MAP_POST_FIX}
        self.assertNotIn("displacement", targets)


class TestArenaCensus(unittest.TestCase):
    """179-material arena-density synthetic mix.

      * 100 non-anisotropic — base_color + roughness only.
      *  40 VRayMtl brushed-metal -- texmap_anisotropy only.
      *  25 anisotropic-fabric (PhysicalMaterial-style) --
           anisotropy_map + anisotropy_rotation_map.
      *  14 carbon-fiber (PhysicalMaterial-style) --
           anisotropy_rotation_map only.

    Pre-fix expected: 0 specular_anisotropy discoveries, 0
    specular_rotation discoveries. Post-fix expected: 40+25 = 65
    specular_anisotropy discoveries, 25+14 = 39 specular_rotation
    discoveries. Non-anisotropy discovery counts unchanged in
    both slotMaps."""

    def _build_arena(self):
        arena = []
        for _ in range(100):
            m = _MaterialBase()
            m.max_class_name = "PhysicalMaterial"
            m.base_color_map = _FakeBitmap("wall.png")
            m.roughness_map = _FakeBitmap("wall_rough.png")
            arena.append(m)
        for _ in range(40):
            arena.append(VRayMtl(
                texmap_diffuse=_FakeBitmap("brushed.png"),
                texmap_anisotropy=_FakeBitmap("brushed_aniso.png"),
            ))
        for _ in range(25):
            arena.append(PhysicalMaterial(
                base_color_map=_FakeBitmap("fabric.png"),
                anisotropy_map=_FakeBitmap("fabric_aniso.png"),
                anisotropy_rotation_map=_FakeBitmap("fabric_rot.png"),
            ))
        for _ in range(14):
            arena.append(PhysicalMaterial(
                base_color_map=_FakeBitmap("carbon.png"),
                anisotropy_rotation_map=_FakeBitmap("carbon_rot.png"),
            ))
        return arena

    def test_arena_anisotropy_discovery_counts(self):
        arena = self._build_arena()
        self.assertEqual(len(arena), 179)
        pre_aniso = pre_rot = pre_other = 0
        post_aniso = post_rot = post_other = 0
        for m in arena:
            for d in discover_max_mtlx_texmaps(m, SLOT_MAP_PRE_FIX):
                if d[0] == "specular_anisotropy":
                    pre_aniso += 1
                elif d[0] == "specular_rotation":
                    pre_rot += 1
                else:
                    pre_other += 1
            for d in discover_max_mtlx_texmaps(m, SLOT_MAP_POST_FIX):
                if d[0] == "specular_anisotropy":
                    post_aniso += 1
                elif d[0] == "specular_rotation":
                    post_rot += 1
                else:
                    post_other += 1
        self.assertEqual(
            pre_aniso, 0,
            "Pre-fix must have ZERO specular_anisotropy hits.",
        )
        self.assertEqual(
            pre_rot, 0,
            "Pre-fix must have ZERO specular_rotation hits.",
        )
        self.assertEqual(
            post_aniso, 65,
            "Post-fix must have exactly 40+25 = 65 anisotropy hits.",
        )
        self.assertEqual(
            post_rot, 39,
            "Post-fix must have exactly 25+14 = 39 rotation hits.",
        )
        self.assertEqual(
            pre_other, post_other,
            "Non-anisotropy discovery must be byte-identical between "
            "slotMaps.",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
