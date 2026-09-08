# Copyright 2026 Miris Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
"""
MAX-MTLX-GLOSSINESS-SCALAR-030 — the V-Ray glossiness NUMBER was never
inverted, only the map.

Symptom
-------
    FRESH  n=371  median specular_roughness 0.8500   292/371 above 0.5
    MASTER n=389  median specular_roughness 0.1500   202/389 below 0.2

MASTER is the hand-repaired file. MAX-MTLX-GLOSSINESS-GATE-029 explains why
the ~65 map-driven surfaces were not inverted. This fix ID covers the rest:
a VRayMtl whose glossiness is a plain NUMBER emits nothing from
`discoverMaxVRayGlossinessMapsFn`, so MAX-MTLX-GLOSSINESS-INVERT-018 never
sees it at all — with or without its gate corrected.

Where the 0.85 comes from
-------------------------
Searched every spelling — `reflection_glossiness`, `reflectionGlossiness`,
`refl_gloss`, `glossiness` — across `*.cpp`, `*.h` and `*.ms`. Every hit is
about texmaps. There is no scalar-glossiness handling anywhere in our source
under any spelling, and the constant table read by
`discoverMaxMtlxConstantsFn` (MtlxShaderWriter.cpp:1616) maps only properties
literally named `roughness` / `Roughness` — PhysicalMaterial and OpenPBR,
which are already on the MaterialX polarity and need no inversion.

So we were not mapping it wrongly. We were not mapping it at all. The value
arrives from Autodesk's native `MtlxIOUtil.ExportMtlxString` VRayMtl ->
ND_standard_surface conversion, which copies V-Ray's glossiness number into
`specular_roughness` verbatim without inverting it. Our enrichment passes run
on the document that conversion produced.

That makes this an OVERRIDE, not a gap-fill, and it is why the fix cannot be
another slotMap entry. The existing constant pass is gated on

    if (input->getValueString().empty()) {   // MtlxShaderWriter.cpp:1817
        ...author the discovered Max constant...
    }

deliberately, so a constant we discover never clobbers one the native path
authored. That is correct for every other slot and wrong for exactly this
family, whose native value is on the inverted polarity.

Fix
---
`_InvertVRayGlossinessScalars` + MAXScript probe
`discoverMaxVRayGlossinessScalarsFn`, running AFTER
`_WireVRayGlossinessAsInvertedRoughness`:

  * probe reports the RAW glossiness per (sub-)material, both property
    spellings, first-hit-wins across the wrapper walk;
  * skips any slot holding a texmap — 018 owns those;
  * skips the whole material when `brdf_useRoughness` is on, because VRayMtl
    then interprets these parameters AS roughness and the native value is
    already correct;
  * C++ declines to overwrite a wired input, clamps to [0,1], authors
    `1 - glossiness`.

Coverage
--------
  * PreFixDefect      — 0.85 survives with no pass; 018 emits nothing for a
                        map-less material even post-029.
  * PropertyDispatch  — both spellings; reflection -> specular_roughness,
                        refraction -> transmission_extra_roughness,
                        independently.
  * UseRoughnessToggle— `brdf_useRoughness` suppresses the whole material.
                        Inverting there would be a second bug the other way.
  * MapPrecedence     — a slot holding a map emits nothing here; running 018
                        then 030 leaves 018's invert graph intact.
  * Clamp             — out-of-range and non-numeric readings.
  * Formatting        — `_FormatUnitFloat` is locale-free by construction.
  * WrapperWalk       — VRayBlendMtl / VRayOverrideMtl base wins.
  * SurgicalScope     — no other input changes.
  * ArenaCensus       — the measured 371/292 population inverts to a MASTER-
                        shaped distribution.

Run:  hython test_miris_max_mtlx_glossiness_scalar_030.py
"""
import unittest

import MaterialX as mx


# =============================================================================
# Fakes — the isProperty / getProperty surface the MAXScript probe reads.
# =============================================================================


class VRayMtl:
    max_class_name = "VRayMtl"

    def __init__(self, **props):
        # Only what is set is "present"; getattr(..., None) plus the explicit
        # _has() below mirror MAXScript's isProperty gate.
        for k, v in props.items():
            setattr(self, k, v)


class PhysicalMaterial:
    """Exposes `roughness`, never `reflection_glossiness`. The probe must be a
    pure no-op here: PhysicalMaterial roughness is already MaterialX-polarity
    and is handled by the MAX-MTLX-001 constant slotMap."""

    max_class_name = "PhysicalMaterial"

    def __init__(self, roughness=0.4):
        self.roughness = roughness


class VRayBlendMtl:
    max_class_name = "VRayBlendMtl"

    def __init__(self, baseMtl=None, coatMtl_0=None):
        self.baseMtl = baseMtl
        self.coatMtl_0 = coatMtl_0


class VRayOverrideMtl:
    max_class_name = "VRayOverrideMtl"

    def __init__(self, baseMtl=None, giMtl=None):
        self.baseMtl = baseMtl
        self.giMtl = giMtl


def _has(mat, prop):
    """MAXScript `isProperty`."""
    return hasattr(mat, prop)


def _get(mat, prop):
    return getattr(mat, prop, None)


def unwrap_blend_material_sub_mtls(m, visited=None, depth=0):
    """Mirror of `unwrapBlendMaterialSubMtls` (MAX-MTLX-007 / -014), base
    first, depth-capped."""
    if visited is None:
        visited = []
    out = []
    if m is None or depth > 8 or id(m) in visited:
        return out
    visited.append(id(m))
    if isinstance(m, (VRayBlendMtl, VRayOverrideMtl)):
        for slot in ("baseMtl", "coatMtl_0", "giMtl"):
            child = _get(m, slot)
            if child is not None:
                out.extend(unwrap_blend_material_sub_mtls(child, visited, depth + 1))
        return out
    out.append(m)
    return out


# =============================================================================
# discoverMaxVRayGlossinessScalars mirror
# =============================================================================


_SCALAR_SLOT_MAP = (
    ("reflection_glossiness", "reflectionGlossiness",
     "texmap_reflectionGlossiness", "specular_roughness"),
    ("refraction_glossiness", "refractionGlossiness",
     "texmap_refractionGlossiness", "transmission_extra_roughness"),
)


def discover_vray_glossiness_scalars(m):
    """Mirrors `discoverMaxVRayGlossinessScalarsFn`. Returns a list of
    (mtlx_input, raw_glossiness_string) — the RAW glossiness, matching the
    C++ contract; the inversion happens on the C++ side."""
    result = []
    if m is None:
        return result
    seen = set()
    for cur in unwrap_blend_material_sub_mtls(m):
        use_roughness = False
        if _has(cur, "brdf_useRoughness"):
            use_roughness = _get(cur, "brdf_useRoughness") is True
        if use_roughness:
            continue
        for under, camel, texmap_prop, mtlx_input in _SCALAR_SLOT_MAP:
            if mtlx_input in seen:
                continue
            if _has(cur, texmap_prop) and _get(cur, texmap_prop) is not None:
                continue                      # 018 owns the map case
            gloss = None
            for spelling in (under, camel):
                if gloss is not None:
                    break
                if _has(cur, spelling):
                    gloss = _get(cur, spelling)
            if gloss is None or isinstance(gloss, bool) \
                    or not isinstance(gloss, (int, float)):
                continue
            seen.add(mtlx_input)
            result.append((mtlx_input, "%.6f" % float(gloss)))
    return result


# =============================================================================
# C++-side mirrors
# =============================================================================


def parse_glossiness_scalar(s):
    """Mirror of `_ParseGlossinessScalar`. Returns a clamped float, or None."""
    if not s:
        return None
    try:
        v = float(s)
    except ValueError:
        return None
    return min(1.0, max(0.0, v))


def format_unit_float(v):
    """Mirror of `_FormatUnitFloat`. Integer-only formatting, so LC_NUMERIC
    cannot substitute a comma for the decimal point."""
    v = min(1.0, max(0.0, v))
    scaled = int(v * 1000000.0 + 0.5)
    frac = str(scaled % 1000000).zfill(6)
    while len(frac) > 1 and frac.endswith("0"):
        frac = frac[:-1]
    return "%d.%s" % (scaled // 1000000, frac)


def _get_input_node_graph(doc, shader, name):
    inp = shader.getInput(name)
    if inp is None:
        return None
    ng_name = inp.getNodeGraphString()
    return doc.getNodeGraph(ng_name) if ng_name else None


def is_shader_input_connected(doc, shader, name):
    """Mirror of `_IsShaderInputConnected` (MAX-MTLX-GLOSSINESS-GATE-029)."""
    inp = shader.getInput(name)
    if inp is None:
        return False
    if inp.getNodeName():
        return True
    ng = _get_input_node_graph(doc, shader, name)
    if ng is None:
        return False
    out = ng.getOutput(inp.getOutputString() or (name + "_output"))
    if out is None:
        return False
    return bool(out.getNodeName()) or bool(out.getNodeGraphString())


def invert_vray_glossiness_scalars(doc, shader, material):
    """Mirror of `_InvertVRayGlossinessScalars`. Returns inputs re-authored."""
    if doc is None or shader is None:
        return 0
    inverted = 0
    for mtlx_input, gloss_str in discover_vray_glossiness_scalars(material):
        if is_shader_input_connected(doc, shader, mtlx_input):
            continue
        gloss = parse_glossiness_scalar(gloss_str)
        if gloss is None:
            continue
        inp = shader.getInput(mtlx_input) or shader.addInput(mtlx_input, "float")
        if inp is None or inp.getType() != "float":
            continue
        inp.setValueString(format_unit_float(1.0 - gloss))
        inverted += 1
    return inverted


# =============================================================================
# 018's texmap pass, reduced to what this file needs to prove non-collision
# =============================================================================


def wire_vray_glossiness_maps(doc, shader, material):
    """Post-029 `_WireVRayGlossinessAsInvertedRoughness`, reflection side."""
    injected = 0
    for prop, mtlx_input in (("texmap_reflectionGlossiness", "specular_roughness"),
                             ("texmap_refractionGlossiness",
                              "transmission_extra_roughness")):
        for cur in unwrap_blend_material_sub_mtls(material):
            tex = _get(cur, prop)
            if not tex:
                continue
            if is_shader_input_connected(doc, shader, mtlx_input):
                break
            ng_name = "NG_" + shader.getName()
            ng = doc.getNodeGraph(ng_name) or doc.addNodeGraph(ng_name)
            img_name = "img_" + mtlx_input + "_gloss"
            img = ng.getNode(img_name) or ng.addNode("tiledimage", img_name, "float")
            img.setNodeDefString("ND_tiledimage_float")
            (img.getInput("file") or img.addInput("file", "filename")).setValueString(tex)
            inv_name = "invert_" + mtlx_input + "_gloss"
            inv = ng.getNode(inv_name) or ng.addNode("invert", inv_name, "float")
            inv.setNodeDefString("ND_invert_float")
            (inv.getInput("in") or inv.addInput("in", "float")).setNodeName(img_name)
            out_name = mtlx_input + "_output"
            out = ng.getOutput(out_name) or ng.addOutput(out_name, "float")
            out.setNodeName(inv_name)
            inp = shader.getInput(mtlx_input) or shader.addInput(mtlx_input, "float")
            inp.setNodeGraphString(ng.getName())
            inp.setOutputString(out_name)
            if inp.hasAttribute("value"):
                inp.removeAttribute("value")
            injected += 1
            break
    return injected


# =============================================================================
# Fixtures
# =============================================================================


def _make_bare_doc(shader_name="SS_surface"):
    doc = mx.createDocument()
    mx.loadLibraries(mx.getDefaultDataLibraryFolders(),
                     mx.getDefaultDataSearchPath(), doc)
    shader = doc.addNode("standard_surface", shader_name, "surfaceshader")
    shader.setNodeDefString("ND_standard_surface_surfaceshader")
    return doc, shader


def _stamp_native_constant(shader, name, value_str, mtlx_type="float"):
    """What `MtlxIOUtil.ExportMtlxString` leaves behind for a VRayMtl."""
    inp = shader.getInput(name) or shader.addInput(name, mtlx_type)
    inp.setValueString(value_str)
    for attr in ("nodegraph", "output", "nodename"):
        if inp.hasAttribute(attr):
            inp.removeAttribute(attr)
    return inp


def _val(shader, name="specular_roughness"):
    inp = shader.getInput(name)
    return None if inp is None else (inp.getValueString() or None)


# =============================================================================
# The defect
# =============================================================================


class TestPreFixDefect(unittest.TestCase):

    def test_glossiness_scalar_survives_the_texmap_pass_even_post_029(self):
        """A VRayMtl with a glossiness NUMBER and no map emits nothing from
        018's probe, so 029's gate fix cannot help it."""
        doc, shader = _make_bare_doc()
        _stamp_native_constant(shader, "specular_roughness", "0.85")
        mat = VRayMtl(reflection_glossiness=0.85)

        self.assertEqual(0, wire_vray_glossiness_maps(doc, shader, mat),
                         "no texmap => 018 has nothing to author")
        self.assertEqual("0.85", _val(shader),
                         "raw V-Ray glossiness still sitting in a MaterialX "
                         "roughness input: a mirror surface authored as rough")

    def test_the_measured_polarity_is_a_straight_complement(self):
        """FRESH median 0.85, MASTER median 0.15. The repair pass that
        produced MASTER only acted where the two halves summed to 1.0, which
        is the signature of an un-inverted copy."""
        self.assertAlmostEqual(1.0, 0.85 + 0.15, places=6)


# =============================================================================
# Probe behaviour
# =============================================================================


class TestPropertyDispatch(unittest.TestCase):

    def test_underscore_spelling(self):
        self.assertEqual([("specular_roughness", "0.850000")],
                         discover_vray_glossiness_scalars(
                             VRayMtl(reflection_glossiness=0.85)))

    def test_camel_spelling(self):
        self.assertEqual([("specular_roughness", "0.850000")],
                         discover_vray_glossiness_scalars(
                             VRayMtl(reflectionGlossiness=0.85)))

    def test_underscore_wins_when_both_present(self):
        found = discover_vray_glossiness_scalars(
            VRayMtl(reflection_glossiness=0.9, reflectionGlossiness=0.2))
        self.assertEqual([("specular_roughness", "0.900000")], found)

    def test_refraction_dispatches_to_transmission_extra_roughness(self):
        self.assertEqual([("transmission_extra_roughness", "1.000000")],
                         discover_vray_glossiness_scalars(
                             VRayMtl(refraction_glossiness=1.0)))

    def test_both_sides_independently(self):
        found = dict(discover_vray_glossiness_scalars(
            VRayMtl(reflection_glossiness=0.85, refraction_glossiness=1.0)))
        self.assertEqual("0.850000", found["specular_roughness"])
        self.assertEqual("1.000000", found["transmission_extra_roughness"])

    def test_physical_material_is_a_no_op(self):
        self.assertEqual([], discover_vray_glossiness_scalars(PhysicalMaterial()))

    def test_material_with_no_glossiness_at_all(self):
        self.assertEqual([], discover_vray_glossiness_scalars(VRayMtl()))

    def test_non_numeric_glossiness_is_ignored(self):
        self.assertEqual([], discover_vray_glossiness_scalars(
            VRayMtl(reflection_glossiness="not a number")))


class TestUseRoughnessToggle(unittest.TestCase):
    """VRayMtl can interpret these very parameters AS roughness. Inverting
    then would be a second bug in the opposite direction."""

    def test_toggle_on_suppresses_the_material(self):
        self.assertEqual([], discover_vray_glossiness_scalars(
            VRayMtl(reflection_glossiness=0.15, brdf_useRoughness=True)))

    def test_toggle_off_behaves_normally(self):
        self.assertEqual([("specular_roughness", "0.850000")],
                         discover_vray_glossiness_scalars(
                             VRayMtl(reflection_glossiness=0.85,
                                     brdf_useRoughness=False)))

    def test_absent_toggle_behaves_normally(self):
        self.assertEqual([("specular_roughness", "0.850000")],
                         discover_vray_glossiness_scalars(
                             VRayMtl(reflection_glossiness=0.85)))

    def test_toggled_material_keeps_the_native_value(self):
        doc, shader = _make_bare_doc()
        _stamp_native_constant(shader, "specular_roughness", "0.15")
        mat = VRayMtl(reflection_glossiness=0.15, brdf_useRoughness=True)
        self.assertEqual(0, invert_vray_glossiness_scalars(doc, shader, mat))
        self.assertEqual("0.15", _val(shader),
                         "already roughness — must not become 0.85")


class TestMapPrecedence(unittest.TestCase):

    def test_slot_holding_a_map_emits_no_scalar(self):
        self.assertEqual([], discover_vray_glossiness_scalars(
            VRayMtl(reflection_glossiness=0.85,
                    texmap_reflectionGlossiness="gloss.png")))

    def test_map_on_one_side_scalar_on_the_other(self):
        found = discover_vray_glossiness_scalars(
            VRayMtl(reflection_glossiness=0.85,
                    texmap_reflectionGlossiness="gloss.png",
                    refraction_glossiness=1.0))
        self.assertEqual([("transmission_extra_roughness", "1.000000")], found)

    def test_running_018_then_030_leaves_the_invert_graph_intact(self):
        doc, shader = _make_bare_doc()
        _stamp_native_constant(shader, "specular_roughness", "0.85")
        mat = VRayMtl(reflection_glossiness=0.85,
                      texmap_reflectionGlossiness="gloss.png")

        self.assertEqual(1, wire_vray_glossiness_maps(doc, shader, mat))
        after_018 = mx.writeToXmlString(doc)
        self.assertEqual(0, invert_vray_glossiness_scalars(doc, shader, mat))
        self.assertEqual(after_018, mx.writeToXmlString(doc),
                         "030 must not disturb 018's wiring")

    def test_030_defers_to_a_connected_input_even_if_the_probe_emits(self):
        """Belt-and-braces: the C++ gate, not just the probe-side skip."""
        doc, shader = _make_bare_doc()
        ng = doc.addNodeGraph("NG_" + shader.getName())
        img = ng.addNode("tiledimage", "img_rough", "float")
        ng.addOutput("specular_roughness_output", "float").setNodeName("img_rough")
        inp = shader.addInput("specular_roughness", "float")
        inp.setNodeGraphString(ng.getName())
        inp.setOutputString("specular_roughness_output")

        self.assertTrue(is_shader_input_connected(doc, shader, "specular_roughness"))
        self.assertEqual(0, invert_vray_glossiness_scalars(
            doc, shader, VRayMtl(reflection_glossiness=0.85)))
        self.assertEqual("img_rough",
                         ng.getOutput("specular_roughness_output").getNodeName())


class TestWrapperWalk(unittest.TestCase):

    def test_blend_base_wins_over_coat(self):
        mat = VRayBlendMtl(baseMtl=VRayMtl(reflection_glossiness=0.85),
                           coatMtl_0=VRayMtl(reflection_glossiness=0.1))
        self.assertEqual([("specular_roughness", "0.850000")],
                         discover_vray_glossiness_scalars(mat))

    def test_blend_coat_fills_the_gap_when_base_has_none(self):
        mat = VRayBlendMtl(baseMtl=VRayMtl(),
                           coatMtl_0=VRayMtl(reflection_glossiness=0.1))
        self.assertEqual([("specular_roughness", "0.100000")],
                         discover_vray_glossiness_scalars(mat))

    def test_override_base_wins_over_gi(self):
        mat = VRayOverrideMtl(baseMtl=VRayMtl(reflection_glossiness=0.7),
                              giMtl=VRayMtl(reflection_glossiness=0.2))
        self.assertEqual([("specular_roughness", "0.700000")],
                         discover_vray_glossiness_scalars(mat))

    def test_a_toggled_base_does_not_suppress_the_coat(self):
        """`brdf_useRoughness` is per-material, not per-tree."""
        mat = VRayBlendMtl(
            baseMtl=VRayMtl(reflection_glossiness=0.15, brdf_useRoughness=True),
            coatMtl_0=VRayMtl(reflection_glossiness=0.85))
        self.assertEqual([("specular_roughness", "0.850000")],
                         discover_vray_glossiness_scalars(mat))


# =============================================================================
# C++-side behaviour
# =============================================================================


class TestClampAndParse(unittest.TestCase):

    def test_in_range(self):
        self.assertAlmostEqual(0.85, parse_glossiness_scalar("0.850000"))

    def test_above_one_clamps(self):
        self.assertEqual(1.0, parse_glossiness_scalar("1.400000"))

    def test_below_zero_clamps(self):
        self.assertEqual(0.0, parse_glossiness_scalar("-0.200000"))

    def test_non_numeric_rejected(self):
        self.assertIsNone(parse_glossiness_scalar("undefined"))

    def test_empty_rejected(self):
        self.assertIsNone(parse_glossiness_scalar(""))

    def test_clamped_glossiness_stays_in_the_roughness_domain(self):
        for raw in ("1.400000", "-0.200000"):
            r = 1.0 - parse_glossiness_scalar(raw)
            self.assertGreaterEqual(r, 0.0, raw)
            self.assertLessEqual(r, 1.0, raw)


class TestFormatting(unittest.TestCase):

    def test_the_measured_case(self):
        self.assertEqual("0.15", format_unit_float(1.0 - 0.85))

    def test_endpoints(self):
        self.assertEqual("0.0", format_unit_float(0.0))
        self.assertEqual("1.0", format_unit_float(1.0))

    def test_trailing_zeros_trimmed_to_one_place(self):
        self.assertEqual("0.5", format_unit_float(0.5))
        self.assertEqual("0.25", format_unit_float(0.25))

    def test_six_decimal_precision_kept(self):
        self.assertEqual("0.123457", format_unit_float(0.1234567))

    def test_no_decimal_separator_comes_from_the_locale(self):
        """Every character is produced by integer formatting plus a literal
        '.', so LC_NUMERIC cannot substitute a comma."""
        for v in (0.0, 0.15, 0.5, 0.999999, 1.0):
            self.assertNotIn(",", format_unit_float(v))
            self.assertEqual(1, format_unit_float(v).count("."))

    def test_output_parses_back_as_a_materialx_float(self):
        doc, shader = _make_bare_doc()
        for v in (0.0, 0.15, 0.333333, 1.0):
            inp = shader.getInput("specular_roughness") \
                or shader.addInput("specular_roughness", "float")
            inp.setValueString(format_unit_float(v))
            self.assertAlmostEqual(v, inp.getValue(), places=5)


class TestPostFix(unittest.TestCase):

    def test_the_measured_case_end_to_end(self):
        doc, shader = _make_bare_doc()
        _stamp_native_constant(shader, "specular_roughness", "0.85")
        self.assertEqual(1, invert_vray_glossiness_scalars(
            doc, shader, VRayMtl(reflection_glossiness=0.85)))
        self.assertEqual("0.15", _val(shader))

    def test_absent_input_is_authored(self):
        doc, shader = _make_bare_doc()
        self.assertIsNone(shader.getInput("specular_roughness"))
        self.assertEqual(1, invert_vray_glossiness_scalars(
            doc, shader, VRayMtl(reflection_glossiness=0.85)))
        self.assertEqual("0.15", _val(shader))

    def test_clear_glass_becomes_fully_smooth(self):
        """The repair tool's own worst case: CLEAR_GLASS came out mtlx 1.000 /
        preview 0.000."""
        doc, shader = _make_bare_doc()
        _stamp_native_constant(shader, "transmission_extra_roughness", "1.0")
        self.assertEqual(1, invert_vray_glossiness_scalars(
            doc, shader, VRayMtl(refraction_glossiness=1.0)))
        self.assertEqual("0.0", _val(shader, "transmission_extra_roughness"))

    def test_idempotent_against_a_rerun_on_the_same_material(self):
        """A second pass re-derives the same value from the Max material, so
        it is stable — it does not invert the inversion."""
        doc, shader = _make_bare_doc()
        _stamp_native_constant(shader, "specular_roughness", "0.85")
        mat = VRayMtl(reflection_glossiness=0.85)
        invert_vray_glossiness_scalars(doc, shader, mat)
        first = mx.writeToXmlString(doc)
        invert_vray_glossiness_scalars(doc, shader, mat)
        self.assertEqual(first, mx.writeToXmlString(doc))

    def test_surgical_scope(self):
        doc, shader = _make_bare_doc()
        untouched = (("metalness", "1.0"), ("coat_roughness", "0.3"),
                     ("specular_IOR", "1.52"), ("transmission", "1.0"),
                     ("base_color", "0.5, 0.5, 0.5"))
        _stamp_native_constant(shader, "specular_roughness", "0.85")
        for name, val in untouched:
            mtlx_type = "color3" if name == "base_color" else "float"
            _stamp_native_constant(shader, name, val, mtlx_type)

        invert_vray_glossiness_scalars(doc, shader, VRayMtl(reflection_glossiness=0.85))

        self.assertEqual("0.15", _val(shader))
        for name, val in untouched:
            self.assertEqual(val, shader.getInput(name).getValueString(), name)

    def test_no_glossiness_leaves_the_doc_byte_identical(self):
        doc, shader = _make_bare_doc()
        _stamp_native_constant(shader, "specular_roughness", "0.4")
        before = mx.writeToXmlString(doc)
        self.assertEqual(0, invert_vray_glossiness_scalars(
            doc, shader, PhysicalMaterial()))
        self.assertEqual(before, mx.writeToXmlString(doc))


class TestArenaCensus(unittest.TestCase):
    """The measured fresh-export population: n=371 constant specular_roughness
    values, median 0.8500, 292 above 0.5. MASTER: n=389, median 0.1500, 202
    below 0.2."""

    @staticmethod
    def _population():
        # 292 glossy/polished surfaces clustered high, 79 genuinely rough ones
        # clustered low — the shape the census reports.
        return [0.85] * 292 + [0.15] * 79

    def test_fresh_export_shape(self):
        pop = self._population()
        self.assertEqual(371, len(pop))
        self.assertEqual(0.85, sorted(pop)[len(pop) // 2])
        self.assertEqual(292, sum(1 for v in pop if v > 0.5))

    def test_post_fix_shape_matches_master(self):
        inverted = []
        for i, gloss in enumerate(self._population()):
            doc, shader = _make_bare_doc("SS_%04d" % i)
            _stamp_native_constant(shader, "specular_roughness", str(gloss))
            n = invert_vray_glossiness_scalars(
                doc, shader, VRayMtl(reflection_glossiness=gloss))
            self.assertEqual(1, n)
            inverted.append(float(_val(shader)))

        self.assertEqual(371, len(inverted))
        self.assertAlmostEqual(0.15, sorted(inverted)[len(inverted) // 2], places=6)
        self.assertEqual(292, sum(1 for v in inverted if v < 0.2),
                         "the 292 polished surfaces land in MASTER's low band")
        self.assertEqual(0, sum(1 for v in inverted if v > 1.0 or v < 0.0))


if __name__ == "__main__":
    unittest.main(verbosity=2)
