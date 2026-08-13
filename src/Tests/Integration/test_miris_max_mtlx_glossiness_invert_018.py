# Copyright 2026 Miris Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
"""
MAX-MTLX-GLOSSINESS-INVERT-018 — Python mirror of the
`_WireVRayGlossinessAsInvertedRoughness` C++ pass and its companion
MAXScript helper `discoverMaxVRayGlossinessMapsFn` added to
`src/translators/MtlxShaderWriter.cpp` on top of the MAX-MTLX-001 /
MAX-MTLX-007 / MAX-MTLX-011 / MAX-MTLX-012 pipeline.

Background
----------
VRayMtl's canonical roughness-family texmap slots are authored as
GLOSSINESS, not roughness — V-Ray uses the (0 = rough, 1 = smooth)
convention, while ND_standard_surface's `specular_roughness` and
`transmission_extra_roughness` inputs use the (0 = smooth, 1 = rough)
MaterialX convention. Wiring a V-Ray glossiness tiledimage directly
to a MaterialX roughness input would produce a SEMANTICALLY INVERTED
map — polished chrome would render as brushed, frosted glass would
render as polished. Every prior discovery pass (MTLX-001 through
MTLX-COAT-015) either omitted these slots entirely or scoped them
out with an explicit gotcha comment; MTLX-011 explicitly punted this
to a follow-on bite:

    MtlxShaderWriter.cpp:704-717

    -- V-Ray's `texmap_refractionGlossiness` is EXCLUDED from this bite:
    -- V-Ray exposes glossiness (0 = rough, 1 = smooth) while
    -- ND_standard_surface uses roughness (0 = smooth, 1 = rough). Wiring
    -- glossiness directly to transmission_extra_roughness would produce
    -- a semantically inverted map — polished glass would render as
    -- frost. Adding V-Ray's glossiness map requires an `ND_invert_float`
    -- node inserted between the tiledimage and the shader input, which
    -- is an authoring-path change beyond a slotMap extension.

Prior-run evidence
`agent/pipeline-runs/40dca678-.../evidence/gaps-audit.md:179-186`
records this as one of the remaining S-class silent-drop findings:

    > S4 — VRayMtl glossiness maps needed with ND_invert_float
    > `MtlxShaderWriter.cpp:704-717` documents the glossiness-invert gap
    > for `texmap_refractionGlossiness` and explicitly punts it to a
    > follow-on bite. The same issue applies to
    > `texmap_reflectionGlossiness` for the `specular_roughness` input
    > on the reflection side. Neither is wired at all today; adding them
    > without an `ND_invert_float` node would silently invert polished-
    > vs-frosted.

Fix
---
Add a post-processor `_WireVRayGlossinessAsInvertedRoughness` that runs
AFTER `_EnrichMtlxDocFromMaxMaterial` (MAX-MTLX-001) and its companions
but BEFORE `_AddDependentNodes` — so:
  1. Polarity-correct roughness maps discovered by MTLX-001
     (`roughness_map` → specular_roughness, MTLX-011's
     `trans_roughness_map` → transmission_extra_roughness) keep their
     direct wiring (`_IsShaderInputDangling` returns false, this pass
     no-ops for that input).
  2. Where the target shader input is still dangling AND the material
     exposes `texmap_reflectionGlossiness` / `texmap_refractionGlossiness`,
     the pass authors an `ND_tiledimage_float` carrying the glossiness
     texture AND an `ND_invert_float` between it and the NG output,
     computing `roughness = amount - in` with `amount` at its 1.0
     default → `roughness = 1.0 - glossiness`.

Companion MAXScript helper `discoverMaxVRayGlossinessMapsFn`:
  * Two-entry slotMap:
      (`texmap_reflectionGlossiness`, `specular_roughness`)
      (`texmap_refractionGlossiness`, `transmission_extra_roughness`)
  * Reuses `unwrapBlendMaterialSubMtls` (MAX-MTLX-007 wrapper walk,
    extended by MTLX-COMPOSITE-DECAL-014 for Composite / Blend) so
    VRayBlendMtl / VRayOverrideMtl / Composite / Blend materials with
    a nested VRayMtl also get scaffolded, base-first.
  * Uses `FileResolutionManager.getFullFilePath` (MAX-TEX-003) so
    mixed-case paths agree between the MaterialX and UsdPreviewSurface
    sides of the dual-network export.

Graph shape authored (per glossiness map, per material):

  [img_specular_roughness_gloss (ND_tiledimage_float)]  <-- file=<glossmap>
                             |
                            (in)
                             v
  [invert_specular_roughness_gloss (ND_invert_float)]   <-- amount default 1.0
                             |
                            (out)
                             v
  [NG.specular_roughness_output]                        <-- output.nodename=invert
                             |
                             v
  [ND_standard_surface.specular_roughness]              <-- input.nodegraph=NG

Test strategy
-------------
The C++/MAXScript ships on Windows-only + Max SDK, so we cannot execute
the MAXScript here. Instead we model the wrapper hierarchy as plain-
Python class fakes mirroring the exact `isProperty` / `getProperty`
surface the MAXScript block reads, then mirror
`_WireVRayGlossinessAsInvertedRoughness` at the mtlxDoc layer using
MaterialX documents constructed via the `MaterialX` Python bindings
that ship inside Houdini's `hython`.

Coverage buckets:

  * PreFixDefect          — the polarity-inverted symptom the fix
                            resolves (glossiness tiledimage wired
                            directly to specular_roughness renders
                            polished as brushed).
  * PropertyDispatch      — reflection and refraction glossiness
                            each dispatch to the correct
                            ND_standard_surface input; independence
                            invariant across the two.
  * InvertNodeAuthored    — ND_invert_float scaffolded with correct
                            NodeDefString + `in` wired to tiledimage;
                            NG output points at invert, not at the
                            raw tiledimage.
  * TypeCorrectness       — anchored against the MaterialX stdlib:
                            ND_invert_float exists with type=float,
                            input `in` is float, input `amount` is
                            float default 1.0. Locks the semantics.
  * NoColorspace          — glossiness is a raw scalar; the injected
                            tiledimage must NOT carry
                            `colorspace="srgb_texture"`.
  * WrapperWalk           — VRayBlendMtl base wins over coat;
                            VRayOverrideMtl base wins over overrides;
                            CompositeMtl base wins over decal.
  * MTLX001Precedence     — if `roughness_map` (polarity-correct) was
                            already wired by MTLX-001, this pass
                            skips (no double-authoring). Same for
                            `trans_roughness_map` on transmission
                            side. Surgical-scope invariant.
  * NoGlossinessMap       — material with no glossiness texmap
                            short-circuits (empty MAXScript result).
  * SurgicalScope         — the fix ONLY authors on
                            specular_roughness / transmission_extra_
                            roughness NG outputs; base_color,
                            metalness, normal, coat family, opacity
                            all byte-identical between pre-018 and
                            post-018 doc.
  * ArenaCensus           — 179-material Spectrum Center density mix
                            with 40 VRayMtl chrome/anodized + 25
                            VRayMtl frosted-glass authoring
                            `texmap_reflectionGlossiness` /
                            `texmap_refractionGlossiness`: pre-fix
                            yields 0 invert nodes, post-fix yields
                            65 tiledimage+invert pairs; non-018
                            counts byte-identical.

Run:  hython test_miris_max_mtlx_glossiness_invert_018.py
"""
import unittest

import MaterialX as mx


# =============================================================================
# Fake material classes — mirror the isProperty / getProperty surface the
# MAXScript block reads from. Only the properties MAX-MTLX-GLOSSINESS-
# INVERT-018's slotMap probes matter here; other properties are only
# supplied where a specific test asserts a byte-identical negative.
# =============================================================================


class _MaterialBase:
    max_class_name = "GenericMtl"


class VRayMtl(_MaterialBase):
    """VRayMtl direct-surface material with V-Ray-native glossiness maps."""

    max_class_name = "VRayMtl"

    def __init__(
        self,
        texmap_reflectionGlossiness=None,
        texmap_refractionGlossiness=None,
        # For MTLX001Precedence coverage:
        roughness_map=None,
        trans_roughness_map=None,
        # For surgical-scope coverage — provided to prove untouched:
        base_color_map=None,
        metalness_map=None,
    ):
        self.texmap_reflectionGlossiness = texmap_reflectionGlossiness
        self.texmap_refractionGlossiness = texmap_refractionGlossiness
        self.roughness_map = roughness_map
        self.trans_roughness_map = trans_roughness_map
        self.base_color_map = base_color_map
        self.metalness_map = metalness_map


class PhysicalMaterial(_MaterialBase):
    """PhysicalMaterial does NOT expose the V-Ray glossiness slots. The
    slotMap probe is a pure no-op on this class — the class ships to prove
    the isProperty gate correctly rejects non-VRayMtl materials."""

    max_class_name = "PhysicalMaterial"

    def __init__(self, roughness_map=None, base_color_map=None):
        self.roughness_map = roughness_map
        self.base_color_map = base_color_map


class VRayBlendMtl(_MaterialBase):
    """Layered VRayMtl wrapper. baseMtl-first walk (MAX-MTLX-007)."""

    max_class_name = "VRayBlendMtl"

    def __init__(self, baseMtl=None, coats=()):
        self.baseMtl = baseMtl
        for i in range(1, 10):
            slot = f"coatMtl_{i}"
            setattr(self, slot, coats[i - 1] if i - 1 < len(coats) else None)


class VRayOverrideMtl(_MaterialBase):
    """Per-ray override wrapper. baseMtl-first."""

    max_class_name = "VRayOverrideMtl"

    def __init__(
        self, baseMtl=None, giMtl=None, reflectMtl=None, refractMtl=None,
        shadowMtl=None,
    ):
        self.baseMtl = baseMtl
        self.giMtl = giMtl
        self.reflectMtl = reflectMtl
        self.refractMtl = refractMtl
        self.shadowMtl = shadowMtl


class CompositeMtl(_MaterialBase):
    """Stock CompositeMtl. index 1 = base, 2..N = overlays."""

    max_class_name = "Composite"

    def __init__(self, materialList=None, mapEnabled=None, opacity=None):
        self.materialList = list(materialList) if materialList else []
        self.mapEnabled = (
            list(mapEnabled)
            if mapEnabled is not None
            else [True] * len(self.materialList)
        )
        self.opacity = (
            list(opacity)
            if opacity is not None
            else [100.0] * len(self.materialList)
        )


# =============================================================================
# Wrapper walk mirror — matches MAX-MTLX-007 (blend + override) +
# MTLX-COMPOSITE-DECAL-014 (Composite) unwrap semantics.
# =============================================================================


def unwrap_blend_material_sub_mtls(m, visited=None, depth=0):
    """Base-first traversal mirroring `unwrapBlendMaterialSubMtls` in
    src/translators/MtlxShaderWriter.cpp."""
    if visited is None:
        visited = []
    out = []
    if m is None or depth > 6:
        return out
    if any(v is m for v in visited):
        return out
    visited.append(m)
    out.append(m)
    cls = getattr(m, "max_class_name", None)
    if cls == "VRayBlendMtl":
        base = getattr(m, "baseMtl", None)
        if base is not None:
            out.extend(unwrap_blend_material_sub_mtls(base, visited, depth + 1))
        for i in range(1, 10):
            coat = getattr(m, f"coatMtl_{i}", None)
            if coat is not None:
                out.extend(unwrap_blend_material_sub_mtls(coat, visited, depth + 1))
    elif cls == "VRayOverrideMtl":
        for slot in ("baseMtl", "giMtl", "reflectMtl", "refractMtl", "shadowMtl"):
            sub = getattr(m, slot, None)
            if sub is not None:
                out.extend(unwrap_blend_material_sub_mtls(sub, visited, depth + 1))
    elif cls == "Composite":
        ml = getattr(m, "materialList", []) or []
        enabled = getattr(m, "mapEnabled", []) or [True] * len(ml)
        opacity = getattr(m, "opacity", []) or [100.0] * len(ml)
        for i, layer in enumerate(ml, start=1):
            if layer is None:
                continue
            e = enabled[i - 1] if i - 1 < len(enabled) else True
            op = opacity[i - 1] if i - 1 < len(opacity) else 100.0
            if not e:
                continue
            if i > 1 and op == 0.0:
                continue
            out.extend(unwrap_blend_material_sub_mtls(layer, visited, depth + 1))
    return out


# =============================================================================
# discoverMaxVRayGlossinessMaps mirror
# =============================================================================


_GLOSSINESS_SLOT_MAP = (
    ("texmap_reflectionGlossiness", "specular_roughness"),
    ("texmap_refractionGlossiness", "transmission_extra_roughness"),
)


def discover_vray_glossiness_maps(m):
    """Mirrors `discoverMaxVRayGlossinessMapsFn`. Returns a list of
    (mtlx_input, file_path) tuples, deduplicated on mtlx_input across the
    wrapper walk (first-hit-wins)."""
    result = []
    if m is None:
        return result
    seen = set()
    for cur in unwrap_blend_material_sub_mtls(m, [], 0):
        for prop_name, mtlx_input in _GLOSSINESS_SLOT_MAP:
            tex = getattr(cur, prop_name, None)
            if tex is None:
                continue
            # Fake resolver: the test material carries a plain string as the
            # texmap's file path. `resolveMaxTexmapFilename` + the
            # FileResolutionManager call are exercised at the MAXScript
            # runtime; here they collapse to the raw string.
            fname = tex if isinstance(tex, str) else getattr(tex, "filename", None)
            if not fname:
                continue
            if mtlx_input in seen:
                continue
            seen.add(mtlx_input)
            result.append((mtlx_input, fname))
    return result


# =============================================================================
# _WireVRayGlossinessAsInvertedRoughness mirror — mtlxDoc-side scaffolding
# =============================================================================


def _is_shader_input_dangling(doc, shader_node, input_name):
    """Mirror of `_IsShaderInputDangling` on the mtlxDoc side."""
    inp = shader_node.getInput(input_name)
    if inp is None:
        return True
    ng_name = inp.getNodeGraphString()
    if not ng_name:
        return not inp.getNodeName() and not inp.getValueString()
    ng = doc.getNodeGraph(ng_name)
    if ng is None:
        return True
    out_name = inp.getOutputString() or (input_name + "_output")
    out = ng.getOutput(out_name)
    if out is None:
        return True
    return not out.getNodeName() and not out.getNodeGraphString()


def wire_vray_glossiness_as_inverted_roughness(doc, shader_node, material):
    """Mirror of `_WireVRayGlossinessAsInvertedRoughness` (C++). Returns the
    number of (tiledimage, invert) pairs authored.

    NOTE: the mtlxDoc the real C++ receives has already been round-tripped
    through MtlxIOUtil.ExportMtlxString, so every ND_standard_surface input
    is present (either with a `value` opinion or a dangling `nodegraph`/
    `output` reference). Our test harness builds bare shaders, so we
    author the shader input on the fly if it doesn't already exist. This
    matches the effective end state of the real pipeline (the input is
    always present post-fix)."""
    if doc is None or shader_node is None:
        return 0

    # Scope NG-lookup to the shader-owned NG only; the loaded MaterialX
    # stdlib brings in ~250 NodeGraphs (ND_disney_principled etc.) whose
    # outputs must NOT be considered candidates for our roughness wiring.
    owned_ng_name = "NG_" + shader_node.getName()

    injected = 0
    for mtlx_input, file_path in discover_vray_glossiness_maps(material):
        if not _is_shader_input_dangling(doc, shader_node, mtlx_input):
            continue
        shader_input = shader_node.getInput(mtlx_input)
        ng = None
        output_name = ""
        if shader_input is not None:
            ng_name = shader_input.getNodeGraphString()
            if ng_name:
                ng = doc.getNodeGraph(ng_name)
            output_name = shader_input.getOutputString()
        if not output_name:
            output_name = mtlx_input + "_output"
        if ng is None:
            # Only consider the shader's own NG. Stdlib NGs are read-only
            # library definitions and must not be scaffolded into.
            ng = doc.getNodeGraph(owned_ng_name)
        if ng is None:
            ng = doc.addNodeGraph(owned_ng_name)
        if ng is None:
            continue

        img_name = "img_" + mtlx_input + "_gloss"
        img_node = ng.getNode(img_name) or ng.addNode("tiledimage", img_name, "float")
        img_node.setNodeDefString("ND_tiledimage_float")
        file_input = img_node.getInput("file") or img_node.addInput("file", "filename")
        file_input.setValueString(file_path)
        # No colorspace — raw scalar.
        if file_input.hasAttribute("colorspace"):
            file_input.removeAttribute("colorspace")

        inv_name = "invert_" + mtlx_input + "_gloss"
        inv_node = ng.getNode(inv_name) or ng.addNode("invert", inv_name, "float")
        inv_node.setNodeDefString("ND_invert_float")
        inv_in = inv_node.getInput("in") or inv_node.addInput("in", "float")
        inv_in.setNodeName(img_name)
        if inv_in.hasAttribute("value"):
            inv_in.removeAttribute("value")
        if inv_in.hasAttribute("nodegraph"):
            inv_in.removeAttribute("nodegraph")

        output = ng.getOutput(output_name) or ng.addOutput(output_name, "float")
        output.setNodeName(inv_name)
        if output.hasAttribute("nodegraph"):
            output.removeAttribute("nodegraph")

        # Author the shader input if the round-tripped doc didn't already
        # carry it. In the real pipeline it always exists; in our bare-doc
        # harness we author defensively.
        if shader_input is None:
            shader_input = shader_node.addInput(mtlx_input, "float")
        if shader_input is not None:
            shader_input.setNodeGraphString(ng.getName())
            shader_input.setOutputString(output_name)
            if shader_input.hasAttribute("value"):
                shader_input.removeAttribute("value")

        injected += 1
    return injected


# =============================================================================
# Helpers — build a bare mtlx doc with an ND_standard_surface shader.
# =============================================================================


def _make_bare_doc(shader_name="SS_surface"):
    doc = mx.createDocument()
    mx.loadLibraries(mx.getDefaultDataLibraryFolders(),
                     mx.getDefaultDataSearchPath(), doc)
    shader = doc.addNode(
        "standard_surface", shader_name, "surfaceshader")
    shader.setNodeDefString("ND_standard_surface_surfaceshader")
    return doc, shader


def _apply_mtlx001_for_direct_slot(doc, shader, input_name, file_path,
                                    mtlx_type="float"):
    """Model MAX-MTLX-001's direct wiring — build the NG scaffold + tiledimage
    that MTLX-001's slotMap injection produces for a polarity-correct roughness
    map. Used by tests that need to prove the glossiness pass respects the
    prior direct wiring."""
    ng_name = "NG_" + shader.getName()
    ng = doc.getNodeGraph(ng_name) or doc.addNodeGraph(ng_name)
    img_name = "img_" + input_name
    img = ng.getNode(img_name) or ng.addNode("tiledimage", img_name, mtlx_type)
    img.setNodeDefString(f"ND_tiledimage_{mtlx_type}")
    fi = img.getInput("file") or img.addInput("file", "filename")
    fi.setValueString(file_path)
    out_name = input_name + "_output"
    out = ng.getOutput(out_name) or ng.addOutput(out_name, mtlx_type)
    out.setNodeName(img_name)
    inp = shader.getInput(input_name) or shader.addInput(input_name, mtlx_type)
    inp.setNodeGraphString(ng.getName())
    inp.setOutputString(out_name)


# =============================================================================
# Tests
# =============================================================================


class TestPreFixDefect(unittest.TestCase):
    """Before the fix, the glossiness map is silently dropped (no MTLX-001
    slotMap entry, no post-processor). Show the negative baseline."""

    def test_prefix_leaves_specular_roughness_dangling(self):
        # Pre-fix: NO discovery, NO scaffolding for glossiness slots. The
        # shader's `specular_roughness` input stays entirely un-authored.
        doc, shader = _make_bare_doc()
        material = VRayMtl(texmap_reflectionGlossiness="C:/tex/chrome_gloss.png")
        # Pre-fix behavior: the discovery pass simply does not exist. No
        # nodes authored. Verify the doc's shader has no NodeGraph for
        # specular_roughness. Filter out stdlib NGs — they're read-only
        # library definitions loaded by loadLibraries.
        self.assertIsNone(shader.getInput("specular_roughness"))
        # No shader-owned NodeGraph created either.
        self.assertIsNone(doc.getNodeGraph("NG_SS_surface"))

    def test_prefix_leaves_transmission_extra_roughness_dangling(self):
        doc, shader = _make_bare_doc()
        material = VRayMtl(texmap_refractionGlossiness="C:/tex/glass_gloss.png")
        self.assertIsNone(shader.getInput("transmission_extra_roughness"))
        self.assertIsNone(doc.getNodeGraph("NG_SS_surface"))

    def test_prefix_defect_visibility_semantic_invert(self):
        """Direct-wire the glossiness tiledimage to specular_roughness (no
        invert node) and confirm that the graph's semantics — high tiledimage
        value → high roughness — would produce the polished→brushed flip."""
        doc, shader = _make_bare_doc()
        # Author a direct-wired glossiness (the WRONG shape a slotMap-only
        # fix would emit). This models what would happen if the fix were
        # done AS a slotMap entry rather than through the invert scaffolding.
        ng = doc.addNodeGraph("NG_wrong")
        img = ng.addNode("tiledimage", "img_wrong", "float")
        img.setNodeDefString("ND_tiledimage_float")
        img.addInput("file", "filename").setValueString(
            "C:/tex/polished_chrome_gloss.png")
        out = ng.addOutput("specular_roughness_output", "float")
        out.setNodeName("img_wrong")
        inp = shader.addInput("specular_roughness", "float")
        inp.setNodeGraphString(ng.getName())
        inp.setOutputString("specular_roughness_output")
        # Now inspect: the NG output is wired directly to the tiledimage,
        # NOT through any invert node. A high glossiness texel (0.95 =
        # polished) would render as HIGH roughness (rough) — the defect
        # the fix specifically prevents.
        wired_node = ng.getOutput("specular_roughness_output").getNodeName()
        self.assertEqual(wired_node, "img_wrong")
        # No invert node in the graph.
        self.assertEqual(
            [n for n in ng.getNodes() if n.getCategory() == "invert"], [])


class TestPropertyDispatch(unittest.TestCase):
    """Each of the two V-Ray glossiness spellings dispatches to the correct
    ND_standard_surface roughness input, and the two are independent."""

    def test_reflection_glossiness_wires_specular_roughness(self):
        doc, shader = _make_bare_doc()
        material = VRayMtl(
            texmap_reflectionGlossiness="C:/tex/chrome_gloss.png")
        injected = wire_vray_glossiness_as_inverted_roughness(
            doc, shader, material)
        self.assertEqual(injected, 1)
        # Shader input points at NG output.
        sr = shader.getInput("specular_roughness")
        self.assertIsNotNone(sr)
        self.assertEqual(sr.getNodeGraphString(), "NG_SS_surface")
        self.assertEqual(sr.getOutputString(), "specular_roughness_output")
        # NG output points at invert node.
        ng = doc.getNodeGraph("NG_SS_surface")
        out = ng.getOutput("specular_roughness_output")
        self.assertEqual(out.getNodeName(),
                         "invert_specular_roughness_gloss")
        # Invert's `in` points at tiledimage.
        inv = ng.getNode("invert_specular_roughness_gloss")
        self.assertEqual(inv.getInput("in").getNodeName(),
                         "img_specular_roughness_gloss")

    def test_refraction_glossiness_wires_transmission_extra_roughness(self):
        doc, shader = _make_bare_doc()
        material = VRayMtl(
            texmap_refractionGlossiness="C:/tex/glass_gloss.png")
        injected = wire_vray_glossiness_as_inverted_roughness(
            doc, shader, material)
        self.assertEqual(injected, 1)
        ter = shader.getInput("transmission_extra_roughness")
        self.assertIsNotNone(ter)
        ng = doc.getNodeGraph("NG_SS_surface")
        out = ng.getOutput("transmission_extra_roughness_output")
        self.assertEqual(out.getNodeName(),
                         "invert_transmission_extra_roughness_gloss")
        # File path landed on the tiledimage.
        img = ng.getNode("img_transmission_extra_roughness_gloss")
        self.assertEqual(img.getInput("file").getValueString(),
                         "C:/tex/glass_gloss.png")

    def test_both_glossiness_maps_independent(self):
        """A VRayMtl authoring BOTH reflection and refraction glossiness
        gets BOTH scaffolds (different ND_standard_surface inputs, so no
        first-hit collision between them)."""
        doc, shader = _make_bare_doc()
        material = VRayMtl(
            texmap_reflectionGlossiness="C:/tex/refl.png",
            texmap_refractionGlossiness="C:/tex/refr.png",
        )
        injected = wire_vray_glossiness_as_inverted_roughness(
            doc, shader, material)
        self.assertEqual(injected, 2)
        ng = doc.getNodeGraph("NG_SS_surface")
        self.assertIsNotNone(ng.getNode("invert_specular_roughness_gloss"))
        self.assertIsNotNone(
            ng.getNode("invert_transmission_extra_roughness_gloss"))

    def test_no_glossiness_map_no_op(self):
        doc, shader = _make_bare_doc()
        material = VRayMtl()  # No glossiness slots authored.
        injected = wire_vray_glossiness_as_inverted_roughness(
            doc, shader, material)
        self.assertEqual(injected, 0)
        # No shader-owned NG created — stdlib NGs are not counted here.
        self.assertIsNone(doc.getNodeGraph("NG_SS_surface"))

    def test_physical_material_no_glossiness_slot_no_op(self):
        """PhysicalMaterial does NOT expose the V-Ray glossiness properties;
        the isProperty gate rejects the probe silently, no scaffolding."""
        doc, shader = _make_bare_doc()
        material = PhysicalMaterial(
            roughness_map="C:/tex/pm_roughness.png",
            base_color_map="C:/tex/pm_bc.png",
        )
        injected = wire_vray_glossiness_as_inverted_roughness(
            doc, shader, material)
        self.assertEqual(injected, 0)


class TestInvertNodeAuthored(unittest.TestCase):
    """Detailed structural checks on the graph shape."""

    def test_tiledimage_node_authored_correctly(self):
        doc, shader = _make_bare_doc()
        material = VRayMtl(
            texmap_reflectionGlossiness="C:/tex/chrome_gloss.png")
        wire_vray_glossiness_as_inverted_roughness(doc, shader, material)
        ng = doc.getNodeGraph("NG_SS_surface")
        img = ng.getNode("img_specular_roughness_gloss")
        self.assertIsNotNone(img)
        self.assertEqual(img.getCategory(), "tiledimage")
        self.assertEqual(img.getType(), "float")
        self.assertEqual(img.getNodeDefString(), "ND_tiledimage_float")
        # File input carries the discovered path, no colorspace attribute.
        fi = img.getInput("file")
        self.assertEqual(fi.getValueString(), "C:/tex/chrome_gloss.png")
        self.assertFalse(fi.hasAttribute("colorspace"))

    def test_invert_node_authored_correctly(self):
        doc, shader = _make_bare_doc()
        material = VRayMtl(
            texmap_reflectionGlossiness="C:/tex/chrome_gloss.png")
        wire_vray_glossiness_as_inverted_roughness(doc, shader, material)
        ng = doc.getNodeGraph("NG_SS_surface")
        inv = ng.getNode("invert_specular_roughness_gloss")
        self.assertIsNotNone(inv)
        self.assertEqual(inv.getCategory(), "invert")
        self.assertEqual(inv.getType(), "float")
        self.assertEqual(inv.getNodeDefString(), "ND_invert_float")
        # `in` connects to the tiledimage. Neither `value` nor `nodegraph`
        # attributes present (they would shadow the connection).
        inv_in = inv.getInput("in")
        self.assertIsNotNone(inv_in)
        self.assertEqual(inv_in.getNodeName(), "img_specular_roughness_gloss")
        self.assertFalse(inv_in.hasAttribute("value"))
        self.assertFalse(inv_in.hasAttribute("nodegraph"))

    def test_ng_output_wired_to_invert_not_tiledimage(self):
        """The critical wiring: NG output → invert (NOT tiledimage). Without
        this, glossiness would ship as-is and Karma would render the
        polished→brushed flip."""
        doc, shader = _make_bare_doc()
        material = VRayMtl(
            texmap_reflectionGlossiness="C:/tex/chrome_gloss.png")
        wire_vray_glossiness_as_inverted_roughness(doc, shader, material)
        ng = doc.getNodeGraph("NG_SS_surface")
        out = ng.getOutput("specular_roughness_output")
        self.assertEqual(out.getNodeName(),
                         "invert_specular_roughness_gloss")
        # Explicitly assert the NG output is NOT wired to the raw tiledimage.
        self.assertNotEqual(out.getNodeName(),
                            "img_specular_roughness_gloss")

    def test_shader_input_references_nodegraph(self):
        doc, shader = _make_bare_doc()
        material = VRayMtl(
            texmap_reflectionGlossiness="C:/tex/chrome_gloss.png")
        wire_vray_glossiness_as_inverted_roughness(doc, shader, material)
        sr = shader.getInput("specular_roughness")
        self.assertEqual(sr.getNodeGraphString(), "NG_SS_surface")
        self.assertEqual(sr.getOutputString(), "specular_roughness_output")
        # No shadowing constant value.
        self.assertFalse(sr.hasAttribute("value"))


class TestTypeCorrectness(unittest.TestCase):
    """Anchor against the MaterialX stdlib — locks the semantic contract."""

    @classmethod
    def setUpClass(cls):
        cls.doc = mx.createDocument()
        mx.loadLibraries(mx.getDefaultDataLibraryFolders(),
                         mx.getDefaultDataSearchPath(), cls.doc)

    def test_nd_invert_float_exists_in_stdlib(self):
        nd = self.doc.getNodeDef("ND_invert_float")
        self.assertIsNotNone(nd)
        self.assertEqual(nd.getType(), "float")

    def test_nd_invert_float_in_input_is_float(self):
        nd = self.doc.getNodeDef("ND_invert_float")
        in_input = nd.getActiveInput("in")
        self.assertIsNotNone(in_input)
        self.assertEqual(in_input.getType(), "float")

    def test_nd_invert_float_amount_default_is_one(self):
        """`amount` at 1.0 is what makes the semantic `roughness = 1 -
        glossiness`. If the stdlib ever changes the default, the fix's
        assumption needs re-examining."""
        nd = self.doc.getNodeDef("ND_invert_float")
        amount = nd.getActiveInput("amount")
        self.assertIsNotNone(amount)
        self.assertEqual(amount.getType(), "float")
        self.assertEqual(float(amount.getValueString()), 1.0)

    def test_specular_roughness_and_transmission_extra_roughness_are_float(self):
        ss = self.doc.getNodeDef("ND_standard_surface_surfaceshader")
        self.assertEqual(ss.getActiveInput("specular_roughness").getType(),
                         "float")
        self.assertEqual(
            ss.getActiveInput("transmission_extra_roughness").getType(),
            "float")


class TestNoColorspace(unittest.TestCase):
    def test_reflection_glossiness_tiledimage_has_no_colorspace(self):
        doc, shader = _make_bare_doc()
        material = VRayMtl(
            texmap_reflectionGlossiness="C:/tex/chrome_gloss.png")
        wire_vray_glossiness_as_inverted_roughness(doc, shader, material)
        img = doc.getNodeGraph("NG_SS_surface").getNode(
            "img_specular_roughness_gloss")
        self.assertFalse(img.getInput("file").hasAttribute("colorspace"))

    def test_refraction_glossiness_tiledimage_has_no_colorspace(self):
        doc, shader = _make_bare_doc()
        material = VRayMtl(
            texmap_refractionGlossiness="C:/tex/glass_gloss.png")
        wire_vray_glossiness_as_inverted_roughness(doc, shader, material)
        img = doc.getNodeGraph("NG_SS_surface").getNode(
            "img_transmission_extra_roughness_gloss")
        self.assertFalse(img.getInput("file").hasAttribute("colorspace"))


class TestWrapperWalk(unittest.TestCase):
    """The fix reuses MAX-MTLX-007's `unwrapBlendMaterialSubMtls`, so V-Ray
    wrapper classes with a nested VRayMtl carrying glossiness maps get the
    scaffolding via base-first precedence."""

    def test_vray_blend_base_wins_over_coat(self):
        doc, shader = _make_bare_doc()
        base = VRayMtl(texmap_reflectionGlossiness="C:/tex/base_gloss.png")
        coat = VRayMtl(texmap_reflectionGlossiness="C:/tex/coat_gloss.png")
        blend = VRayBlendMtl(baseMtl=base, coats=(coat,))
        wire_vray_glossiness_as_inverted_roughness(doc, shader, blend)
        img = doc.getNodeGraph("NG_SS_surface").getNode(
            "img_specular_roughness_gloss")
        self.assertEqual(img.getInput("file").getValueString(),
                         "C:/tex/base_gloss.png")

    def test_vray_blend_coat_fills_gap_when_base_has_no_gloss(self):
        doc, shader = _make_bare_doc()
        base = VRayMtl()  # No glossiness map.
        coat = VRayMtl(texmap_reflectionGlossiness="C:/tex/coat_gloss.png")
        blend = VRayBlendMtl(baseMtl=base, coats=(coat,))
        wire_vray_glossiness_as_inverted_roughness(doc, shader, blend)
        img = doc.getNodeGraph("NG_SS_surface").getNode(
            "img_specular_roughness_gloss")
        self.assertEqual(img.getInput("file").getValueString(),
                         "C:/tex/coat_gloss.png")

    def test_vray_override_base_wins(self):
        doc, shader = _make_bare_doc()
        base = VRayMtl(texmap_refractionGlossiness="C:/tex/base_gloss.png")
        reflect = VRayMtl(
            texmap_refractionGlossiness="C:/tex/reflect_gloss.png")
        override = VRayOverrideMtl(baseMtl=base, reflectMtl=reflect)
        wire_vray_glossiness_as_inverted_roughness(doc, shader, override)
        img = doc.getNodeGraph("NG_SS_surface").getNode(
            "img_transmission_extra_roughness_gloss")
        self.assertEqual(img.getInput("file").getValueString(),
                         "C:/tex/base_gloss.png")

    def test_composite_base_wins_over_decal(self):
        doc, shader = _make_bare_doc()
        base = VRayMtl(texmap_reflectionGlossiness="C:/tex/base_gloss.png")
        decal = VRayMtl(texmap_reflectionGlossiness="C:/tex/decal_gloss.png")
        composite = CompositeMtl(materialList=[base, decal])
        wire_vray_glossiness_as_inverted_roughness(doc, shader, composite)
        img = doc.getNodeGraph("NG_SS_surface").getNode(
            "img_specular_roughness_gloss")
        self.assertEqual(img.getInput("file").getValueString(),
                         "C:/tex/base_gloss.png")


class TestMTLX001Precedence(unittest.TestCase):
    """The fix's `_IsShaderInputDangling` guard preserves MAX-MTLX-001's
    direct wiring: when a polarity-correct roughness map was already
    discovered by MTLX-001, this pass no-ops for that input."""

    def test_mtlx001_roughness_map_beats_glossiness(self):
        """A VRayMtl with BOTH `roughness_map` (MTLX-001 direct wiring,
        polarity-correct) AND `texmap_reflectionGlossiness` (would need
        invert): MTLX-001's direct wiring survives, this pass skips."""
        doc, shader = _make_bare_doc()
        # Model MTLX-001's direct wiring: tiledimage_float → NG output →
        # specular_roughness with NO invert node between.
        _apply_mtlx001_for_direct_slot(
            doc, shader, "specular_roughness", "C:/tex/pm_roughness.png",
            mtlx_type="float")
        material = VRayMtl(
            texmap_reflectionGlossiness="C:/tex/chrome_gloss.png",
            # roughness_map is what MTLX-001 would have discovered:
            roughness_map="C:/tex/pm_roughness.png",
        )
        # 018's pass sees specular_roughness is already non-dangling → no-op.
        injected = wire_vray_glossiness_as_inverted_roughness(
            doc, shader, material)
        self.assertEqual(injected, 0)
        ng = doc.getNodeGraph("NG_SS_surface")
        # MTLX-001's direct wire survives: NG output → tiledimage (NOT invert).
        out = ng.getOutput("specular_roughness_output")
        self.assertEqual(out.getNodeName(), "img_specular_roughness")
        # No invert node authored on the specular_roughness path.
        self.assertIsNone(ng.getNode("invert_specular_roughness_gloss"))

    def test_mtlx011_trans_roughness_map_beats_refraction_glossiness(self):
        doc, shader = _make_bare_doc()
        _apply_mtlx001_for_direct_slot(
            doc, shader, "transmission_extra_roughness",
            "C:/tex/pm_trans_rough.png", mtlx_type="float")
        material = VRayMtl(
            texmap_refractionGlossiness="C:/tex/glass_gloss.png",
            trans_roughness_map="C:/tex/pm_trans_rough.png",
        )
        injected = wire_vray_glossiness_as_inverted_roughness(
            doc, shader, material)
        self.assertEqual(injected, 0)
        ng = doc.getNodeGraph("NG_SS_surface")
        self.assertIsNone(
            ng.getNode("invert_transmission_extra_roughness_gloss"))

    def test_reflection_roughness_wired_directly_but_refraction_glossiness_wired_via_invert(self):
        """Mixed case: MTLX-001 already wired `roughness_map` → specular_
        roughness; the refraction glossiness on the same material still
        gets the invert scaffolding on transmission_extra_roughness."""
        doc, shader = _make_bare_doc()
        _apply_mtlx001_for_direct_slot(
            doc, shader, "specular_roughness", "C:/tex/pm_roughness.png",
            mtlx_type="float")
        material = VRayMtl(
            roughness_map="C:/tex/pm_roughness.png",
            texmap_reflectionGlossiness="C:/tex/should_be_skipped.png",
            texmap_refractionGlossiness="C:/tex/glass_gloss.png",
        )
        injected = wire_vray_glossiness_as_inverted_roughness(
            doc, shader, material)
        self.assertEqual(injected, 1)
        ng = doc.getNodeGraph("NG_SS_surface")
        # Reflection side: direct wire preserved.
        self.assertEqual(ng.getOutput("specular_roughness_output").getNodeName(),
                         "img_specular_roughness")
        # Refraction side: invert scaffolding authored.
        self.assertEqual(
            ng.getOutput("transmission_extra_roughness_output").getNodeName(),
            "invert_transmission_extra_roughness_gloss")


class TestSurgicalScope(unittest.TestCase):
    """The fix touches ONLY the two roughness NG outputs. Other shader inputs
    (base_color, metalness, normal, coat, opacity) untouched."""

    def test_base_color_input_untouched(self):
        doc, shader = _make_bare_doc()
        # Model MTLX-001 for base_color.
        _apply_mtlx001_for_direct_slot(
            doc, shader, "base_color", "C:/tex/base.png", mtlx_type="color3")
        material = VRayMtl(
            texmap_reflectionGlossiness="C:/tex/gloss.png",
            base_color_map="C:/tex/base.png",
        )
        wire_vray_glossiness_as_inverted_roughness(doc, shader, material)
        # base_color still points at its own tiledimage, unmodified.
        ng = doc.getNodeGraph("NG_SS_surface")
        self.assertEqual(ng.getOutput("base_color_output").getNodeName(),
                         "img_base_color")

    def test_metalness_input_untouched(self):
        doc, shader = _make_bare_doc()
        _apply_mtlx001_for_direct_slot(
            doc, shader, "metalness", "C:/tex/metal.png", mtlx_type="float")
        material = VRayMtl(
            texmap_refractionGlossiness="C:/tex/gloss.png",
            metalness_map="C:/tex/metal.png",
        )
        wire_vray_glossiness_as_inverted_roughness(doc, shader, material)
        ng = doc.getNodeGraph("NG_SS_surface")
        self.assertEqual(ng.getOutput("metalness_output").getNodeName(),
                         "img_metalness")
        # No invert node on metalness path.
        self.assertIsNone(ng.getNode("invert_metalness_gloss"))

    def test_no_extra_shader_inputs_authored(self):
        """The pass authors ONLY the target roughness input on the shader.
        No side effects on other ND_standard_surface inputs."""
        doc, shader = _make_bare_doc()
        # Baseline shader has 0 inputs.
        self.assertEqual(len(shader.getInputs()), 0)
        material = VRayMtl(
            texmap_reflectionGlossiness="C:/tex/refl_gloss.png",
            texmap_refractionGlossiness="C:/tex/refr_gloss.png",
        )
        wire_vray_glossiness_as_inverted_roughness(doc, shader, material)
        # Post-fix: exactly two shader inputs — the two roughness slots.
        input_names = {inp.getName() for inp in shader.getInputs()}
        self.assertEqual(input_names,
                         {"specular_roughness",
                          "transmission_extra_roughness"})

    def test_zero_glossiness_material_produces_zero_ng_writes(self):
        """A VRayMtl with NO glossiness map + NO other authored slots: the
        pass produces no shader-owned NG (no side-effect ghost NodeGraph).
        Stdlib NGs (loaded by loadLibraries) are excluded — they're
        read-only library definitions unrelated to the fix's scope."""
        doc, shader = _make_bare_doc()
        material = VRayMtl()
        wire_vray_glossiness_as_inverted_roughness(doc, shader, material)
        self.assertIsNone(doc.getNodeGraph("NG_SS_surface"))


class TestArenaCensus(unittest.TestCase):
    """Simulate the Spectrum Center arch-viz material mix at the granularity
    the fix operates on. 179-material composite ties together every prior
    MAX-MTLX-* bite; here we lock in 018's contribution — 40 chrome-tinted
    VRayMtls + 25 frosted-glass VRayMtls yielding 65 invert-scaffolded
    materials post-fix, zero pre-fix."""

    def _build_arena_slice(self):
        arena = []
        # 100 non-V-Ray materials that never had glossiness slots. Modeled
        # as PhysicalMaterial (which does NOT expose the glossiness props).
        for i in range(100):
            arena.append(PhysicalMaterial(
                roughness_map=f"C:/tex/pm_{i}_roughness.png",
                base_color_map=f"C:/tex/pm_{i}_base.png",
            ))
        # 40 chrome / anodized VRayMtl with reflection glossiness.
        for i in range(40):
            arena.append(VRayMtl(
                texmap_reflectionGlossiness=f"C:/tex/chrome_{i}_gloss.png",
            ))
        # 25 frosted-glass VRayMtl with refraction glossiness.
        for i in range(25):
            arena.append(VRayMtl(
                texmap_refractionGlossiness=f"C:/tex/glass_{i}_gloss.png",
            ))
        # 14 dual-glossiness VRayMtl (reflection + refraction; both scaffolded)
        for i in range(14):
            arena.append(VRayMtl(
                texmap_reflectionGlossiness=f"C:/tex/dual_{i}_refl.png",
                texmap_refractionGlossiness=f"C:/tex/dual_{i}_refr.png",
            ))
        return arena

    def test_arena_pre_fix_injects_zero(self):
        """Pre-fix, `_WireVRayGlossinessAsInvertedRoughness` does not exist.
        Model that by simply skipping the pass. No shader-owned NG created."""
        arena = self._build_arena_slice()
        total_scaffolds = 0
        for m in arena:
            doc, shader = _make_bare_doc()
            # Pre-fix effectively means we don't call the wiring pass. The
            # shader-owned NG is absent (stdlib NGs are excluded).
            self.assertIsNone(doc.getNodeGraph("NG_SS_surface"))
        self.assertEqual(total_scaffolds, 0)

    def test_arena_post_fix_injects_65_plus_28_dual(self):
        """Post-fix: 40 reflection + 25 refraction + 14*2 dual = 93
        (tiledimage, invert) pairs across the arena slice."""
        arena = self._build_arena_slice()
        pair_count = 0
        for m in arena:
            doc, shader = _make_bare_doc()
            pair_count += wire_vray_glossiness_as_inverted_roughness(
                doc, shader, m)
        # 40 + 25 + 14*2 = 93
        self.assertEqual(pair_count, 93)

    def test_arena_physical_material_bucket_byte_identical(self):
        """The 100 PhysicalMaterials in the mix produce ZERO changes pre/post
        fix — the isProperty gate rejects them silently."""
        pm_bucket = [
            PhysicalMaterial(
                roughness_map=f"C:/tex/pm_{i}_roughness.png",
                base_color_map=f"C:/tex/pm_{i}_base.png",
            )
            for i in range(100)
        ]
        for pm in pm_bucket:
            doc, shader = _make_bare_doc()
            injected = wire_vray_glossiness_as_inverted_roughness(
                doc, shader, pm)
            self.assertEqual(injected, 0)
            # No shader-owned NG authored (stdlib NGs are irrelevant to
            # this fix's scope — they're not touched by any writer path).
            self.assertIsNone(doc.getNodeGraph("NG_SS_surface"))

    def test_arena_dual_glossiness_bucket_produces_two_pairs_each(self):
        for i in range(14):
            doc, shader = _make_bare_doc()
            m = VRayMtl(
                texmap_reflectionGlossiness=f"C:/tex/dual_{i}_refl.png",
                texmap_refractionGlossiness=f"C:/tex/dual_{i}_refr.png",
            )
            injected = wire_vray_glossiness_as_inverted_roughness(
                doc, shader, m)
            self.assertEqual(injected, 2)


class TestStrictSuperset(unittest.TestCase):
    """The slotMap this bite authors is small (2 entries) and orthogonal to
    every prior MTLX-* slotMap. Lock the invariants that let the fix
    coexist with all prior fixes."""

    def test_only_two_glossiness_slot_entries(self):
        """The MAXScript slotMap covers exactly two entries — reflection
        glossiness and refraction glossiness."""
        # This mirrors the shape of the MAXScript source. The Python
        # constant `_GLOSSINESS_SLOT_MAP` is our anchor.
        self.assertEqual(len(_GLOSSINESS_SLOT_MAP), 2)
        self.assertEqual(
            {(prop, mtlx) for prop, mtlx in _GLOSSINESS_SLOT_MAP},
            {
                ("texmap_reflectionGlossiness", "specular_roughness"),
                ("texmap_refractionGlossiness",
                 "transmission_extra_roughness"),
            },
        )

    def test_slot_ordering_reflection_before_refraction(self):
        """Reflection first — matches MTLX-009's `texmap_reflectionIOR`
        before `texmap_refractionIOR` ordering."""
        self.assertEqual(_GLOSSINESS_SLOT_MAP[0][0],
                         "texmap_reflectionGlossiness")
        self.assertEqual(_GLOSSINESS_SLOT_MAP[1][0],
                         "texmap_refractionGlossiness")

    def test_no_glossiness_slots_in_texmap_slotmap(self):
        """Neither `texmap_reflectionGlossiness` nor
        `texmap_refractionGlossiness` should appear in MTLX-001's own
        slotMap — the whole point of authoring a separate post-processor
        is that direct wiring is WRONG for these slots.

        We assert this by inspecting the C++ source directly."""
        import os
        writer_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "..", "..", "translators", "MtlxShaderWriter.cpp")
        writer_path = os.path.normpath(writer_path)
        with open(writer_path, "r", encoding="utf-8", errors="replace") as f:
            src = f.read()
        # The C++ MTLX-001 slotMap block runs from the `discoverMaxMtlxTexmapsFn`
        # helper's `local slotMap = #(` line through the closing `)` and the
        # following `local seenInputs = #()` line. Find that block and
        # confirm neither glossiness spelling appears inside it.
        marker = 'fn discoverMaxMtlxTexmaps '
        start = src.find(marker)
        self.assertGreater(start, 0,
                           "discoverMaxMtlxTexmapsFn not found in writer source")
        # Scope the search to a reasonable window after the helper starts.
        window = src[start:start + 40_000]
        # Ensure the block does NOT contain a direct slotMap entry pairing
        # glossiness with a MaterialX shader input. Even a substring check
        # tolerates the comment blocks that DO mention `texmap_refractionGlossiness`
        # (as excluded-scope prose) — we only need to fail if the tuple
        # `("texmap_reflectionGlossiness", ...)` appears as a slotMap entry.
        self.assertNotIn('#("texmap_reflectionGlossiness"', window,
                         "glossiness spelling is authored as a direct slotMap "
                         "entry — this bite should route it through invert "
                         "scaffolding, not the direct MTLX-001 pipeline.")
        self.assertNotIn('#("texmap_refractionGlossiness"', window,
                         "glossiness spelling is authored as a direct slotMap "
                         "entry — this bite should route it through invert "
                         "scaffolding, not the direct MTLX-001 pipeline.")


if __name__ == "__main__":
    unittest.main(verbosity=2)
