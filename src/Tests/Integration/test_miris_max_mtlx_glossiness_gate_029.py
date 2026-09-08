# Copyright 2026 Miris Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
"""
MAX-MTLX-GLOSSINESS-GATE-029 — MAX-MTLX-GLOSSINESS-INVERT-018 never ran.

Symptom
-------
A fresh export of the Spectrum Center interior, built from a tree that
already contained MAX-MTLX-GLOSSINESS-INVERT-018, still carried raw V-Ray
glossiness in the MaterialX half:

    FRESH  n=371  median specular_roughness 0.8500   292/371 above 0.5
    MASTER n=389  median specular_roughness 0.1500   202/389 below 0.2

MASTER is the hand-repaired file. 0.85 is a V-Ray glossiness reading
(1 = smooth); 0.15 is the same surface expressed as MaterialX roughness
(0 = smooth). Every polished surface in the fresh export was authored as
nearly fully rough. The repair pass that produced MASTER
(`generic-arena/tools/invert_gloss_to_roughness_mtlx.py`) says so in its
own docstring: "the MaterialX half's constant specular_roughness holds raw
V-Ray GLOSSINESS ... It is the exporter's, not ours."

Root cause
----------
`_WireVRayGlossinessAsInvertedRoughness` is gated on

    if (!_IsShaderInputDangling(mtlxDoc, shaderNode, mtlxInput)) {
        continue;
    }

and `_IsShaderInputDangling` answers `false` for an input that carries a
plain constant:

    if (!ng) {
        return input->getNodeName().empty()
            && input->getValueString().empty();   // <-- non-empty value
    }                                             //     => not dangling

The mtlxDoc this pass receives has already been through Autodesk's native
`MtlxIOUtil.ExportMtlxString`, whose VRayMtl -> ND_standard_surface
conversion stamps a constant into `specular_roughness` for essentially
every VRayMtl. So the guard fired on the common case and the pass
`continue`d without authoring anything. 018 shipped inert.

The guard's stated intent was narrower than its behaviour. Its comment
says it exists so a polarity-correct roughness MAP wired by MAX-MTLX-001
is not clobbered. `_IsShaderInputDangling` cannot express that: it answers
`false` both for "MTLX-001 wired a map here" (defer) and for "the native
path left a number here" (override). The second is the entire reason 018
exists.

Why the existing 018 mirror test passed anyway
----------------------------------------------
`test_miris_max_mtlx_glossiness_invert_018.py` passes -- 36 tests, OK --
because `_make_bare_doc()` builds an ND_standard_surface with NO inputs
authored, so `specular_roughness` is absent and `_IsShaderInputDangling`
returns `true` at its first line. Its own harness docstring names the
state it then never built a fixture for:

    "the mtlxDoc the real C++ receives has already been round-tripped
     through MtlxIOUtil.ExportMtlxString, so every ND_standard_surface
     input is present (either with a `value` opinion or a dangling
     `nodegraph`/`output` reference)"

Every fixture in that file is the second kind. This file supplies the
first, and `TestPreFixDefect` below fails against the old gate.

Fix
---
Add `_IsShaderInputConnected`, which asks the narrower question -- is
anything actually WIRED to this input -- and gate 018 on that instead:

  * `nodename` set                                  -> connected (defer)
  * `nodegraph` set, NodeGraph resolves, its output
    is driven by a node                             -> connected (defer)
  * `nodegraph` set but the NodeGraph does not exist -> NOT connected.
    That is the MAX-MTLX-001 dangling-reference defect, not a connection.
  * a plain `value` constant                        -> NOT connected (override)
  * input absent entirely                           -> NOT connected (author)

The override half of 018 was already written and waiting: the foot of its
loop does `shaderInput->removeAttribute("value")`. Only the gate stopped
it from being reached.

Scope
-----
This changes the gate on the glossiness texmap pass ONLY. It does not
touch `_IsShaderInputDangling` itself, which 8 other passes
(MAX-MTLX-001 and companions) depend on -- those passes inject a map into
an EMPTY slot and must keep declining to overwrite a native constant,
because for them the native constant is a legitimate value on the correct
polarity. Glossiness is the one family where the native constant is
authored on the wrong polarity and must be replaced.

It also does NOT fix the scalar case: a VRayMtl with a glossiness NUMBER
and no glossiness texmap produces no MAXScript line here, so this pass
never sees it. That is MAX-MTLX-GLOSSINESS-SCALAR-030.

Run:  hython test_miris_max_mtlx_glossiness_gate_029.py
"""
import unittest

import MaterialX as mx


# =============================================================================
# Fakes — the isProperty / getProperty surface discoverMaxVRayGlossinessMapsFn
# reads. Only the glossiness slots matter to this fix ID.
# =============================================================================


class VRayMtl:
    max_class_name = "VRayMtl"

    def __init__(
        self,
        texmap_reflectionGlossiness=None,
        texmap_refractionGlossiness=None,
    ):
        self.texmap_reflectionGlossiness = texmap_reflectionGlossiness
        self.texmap_refractionGlossiness = texmap_refractionGlossiness


_GLOSSINESS_SLOT_MAP = (
    ("texmap_reflectionGlossiness", "specular_roughness"),
    ("texmap_refractionGlossiness", "transmission_extra_roughness"),
)


def discover_vray_glossiness_maps(m):
    """Mirrors `discoverMaxVRayGlossinessMapsFn`, single-material walk."""
    result = []
    if m is None:
        return result
    seen = set()
    for prop_name, mtlx_input in _GLOSSINESS_SLOT_MAP:
        tex = getattr(m, prop_name, None)
        if not tex:
            continue
        if mtlx_input in seen:
            continue
        seen.add(mtlx_input)
        result.append((mtlx_input, tex))
    return result


# =============================================================================
# Gate mirrors — the OLD gate and the NEW gate, side by side.
# =============================================================================


def _get_input_node_graph(doc, shader_node, input_name):
    """Mirror of `_GetInputNodeGraph`."""
    inp = shader_node.getInput(input_name)
    if inp is None:
        return None
    ng_name = inp.getNodeGraphString()
    if not ng_name:
        return None
    return doc.getNodeGraph(ng_name)


def _is_shader_input_dangling(doc, shader_node, input_name):
    """Mirror of `_IsShaderInputDangling` — the OLD gate. Kept here so the
    regression this fix ID closes is asserted, not just described."""
    inp = shader_node.getInput(input_name)
    if inp is None:
        return True
    ng = _get_input_node_graph(doc, shader_node, input_name)
    if ng is None:
        return not inp.getNodeName() and not inp.getValueString()
    out_name = inp.getOutputString() or (input_name + "_output")
    out = ng.getOutput(out_name)
    if out is None:
        return True
    return not out.getNodeName() and not out.getNodeGraphString()


def _is_shader_input_connected(doc, shader_node, input_name):
    """Mirror of `_IsShaderInputConnected` — the NEW gate."""
    inp = shader_node.getInput(input_name)
    if inp is None:
        return False
    if inp.getNodeName():
        return True
    ng = _get_input_node_graph(doc, shader_node, input_name)
    if ng is None:
        return False
    out_name = inp.getOutputString() or (input_name + "_output")
    out = ng.getOutput(out_name)
    if out is None:
        return False
    return bool(out.getNodeName()) or bool(out.getNodeGraphString())


# =============================================================================
# _WireVRayGlossinessAsInvertedRoughness mirror, parameterised on the gate so
# both can be run against identical fixtures.
# =============================================================================


def wire_vray_glossiness(doc, shader_node, material, gate="connected"):
    """Mirror of `_WireVRayGlossinessAsInvertedRoughness`.

    `gate="dangling"` reproduces the shipped-but-inert pre-029 behaviour;
    `gate="connected"` is the post-029 behaviour."""
    if doc is None or shader_node is None:
        return 0

    owned_ng_name = "NG_" + shader_node.getName()
    injected = 0

    for mtlx_input, file_path in discover_vray_glossiness_maps(material):
        if gate == "dangling":
            if not _is_shader_input_dangling(doc, shader_node, mtlx_input):
                continue
        else:
            if _is_shader_input_connected(doc, shader_node, mtlx_input):
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
            ng = doc.getNodeGraph(owned_ng_name) or doc.addNodeGraph(owned_ng_name)
        if ng is None:
            continue

        img_name = "img_" + mtlx_input + "_gloss"
        img_node = ng.getNode(img_name) or ng.addNode("tiledimage", img_name, "float")
        img_node.setNodeDefString("ND_tiledimage_float")
        file_input = img_node.getInput("file") or img_node.addInput("file", "filename")
        file_input.setValueString(file_path)
        if file_input.hasAttribute("colorspace"):
            file_input.removeAttribute("colorspace")

        inv_name = "invert_" + mtlx_input + "_gloss"
        inv_node = ng.getNode(inv_name) or ng.addNode("invert", inv_name, "float")
        inv_node.setNodeDefString("ND_invert_float")
        inv_in = inv_node.getInput("in") or inv_node.addInput("in", "float")
        inv_in.setNodeName(img_name)
        for attr in ("value", "nodegraph"):
            if inv_in.hasAttribute(attr):
                inv_in.removeAttribute(attr)

        output = ng.getOutput(output_name) or ng.addOutput(output_name, "float")
        output.setNodeName(inv_name)
        if output.hasAttribute("nodegraph"):
            output.removeAttribute("nodegraph")

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
# Fixtures
# =============================================================================


def _make_bare_doc(shader_name="SS_surface"):
    doc = mx.createDocument()
    mx.loadLibraries(mx.getDefaultDataLibraryFolders(),
                     mx.getDefaultDataSearchPath(), doc)
    shader = doc.addNode("standard_surface", shader_name, "surfaceshader")
    shader.setNodeDefString("ND_standard_surface_surfaceshader")
    return doc, shader


def _stamp_native_constant(shader, input_name, value_str, mtlx_type="float"):
    """Model what `MtlxIOUtil.ExportMtlxString` leaves behind: the input is
    PRESENT and carries a plain constant, with no nodegraph and no output.

    This is the fixture state missing from every case in
    test_miris_max_mtlx_glossiness_invert_018.py, and the reason that file
    passes while the shipped exporter does not invert."""
    inp = shader.getInput(input_name) or shader.addInput(input_name, mtlx_type)
    inp.setValueString(value_str)
    for attr in ("nodegraph", "output", "nodename"):
        if inp.hasAttribute(attr):
            inp.removeAttribute(attr)
    return inp


def _wire_mtlx001_direct_map(doc, shader, input_name, file_path):
    """Model MAX-MTLX-001's polarity-correct roughness-map wiring: a real
    connection this pass must defer to."""
    ng_name = "NG_" + shader.getName()
    ng = doc.getNodeGraph(ng_name) or doc.addNodeGraph(ng_name)
    img_name = "img_" + input_name
    img = ng.getNode(img_name) or ng.addNode("tiledimage", img_name, "float")
    img.setNodeDefString("ND_tiledimage_float")
    (img.getInput("file") or img.addInput("file", "filename")).setValueString(file_path)
    out_name = input_name + "_output"
    out = ng.getOutput(out_name) or ng.addOutput(out_name, "float")
    out.setNodeName(img_name)
    inp = shader.getInput(input_name) or shader.addInput(input_name, "float")
    inp.setNodeGraphString(ng.getName())
    inp.setOutputString(out_name)
    if inp.hasAttribute("value"):
        inp.removeAttribute("value")
    return ng


def _roughness_state(doc, shader, input_name="specular_roughness"):
    """(constant_value_or_None, driving_node_name_or_None) for an input."""
    inp = shader.getInput(input_name)
    if inp is None:
        return (None, None)
    const = inp.getValueString() or None
    ng = _get_input_node_graph(doc, shader, input_name)
    driver = None
    if ng is not None:
        out = ng.getOutput(inp.getOutputString() or (input_name + "_output"))
        if out is not None:
            driver = out.getNodeName() or None
    return (const, driver)


GLOSS_MAP = r"Y:\denver\spectrum\tex\chrome_rail_refl_gloss.png"


# =============================================================================
# The defect
# =============================================================================


class TestPreFixDefect(unittest.TestCase):
    """The old gate turns 018 into a no-op the moment MtlxIOUtil has stamped
    a constant — which it does for essentially every VRayMtl."""

    def test_native_constant_makes_input_not_dangling(self):
        doc, shader = _make_bare_doc()
        _stamp_native_constant(shader, "specular_roughness", "0.85")
        self.assertFalse(
            _is_shader_input_dangling(doc, shader, "specular_roughness"),
            "a plain constant reads as NOT dangling — this is what blocked 018")

    def test_old_gate_authors_nothing_and_leaves_glossiness_in_place(self):
        doc, shader = _make_bare_doc()
        _stamp_native_constant(shader, "specular_roughness", "0.85")
        mat = VRayMtl(texmap_reflectionGlossiness=GLOSS_MAP)

        self.assertEqual(0, wire_vray_glossiness(doc, shader, mat, gate="dangling"))
        const, driver = _roughness_state(doc, shader)
        self.assertEqual("0.85", const, "raw V-Ray glossiness survives untouched")
        self.assertIsNone(driver, "no invert node was authored")

    def test_old_gate_no_op_holds_on_the_transmission_side_too(self):
        doc, shader = _make_bare_doc()
        _stamp_native_constant(shader, "transmission_extra_roughness", "0.9")
        mat = VRayMtl(texmap_refractionGlossiness=GLOSS_MAP)
        self.assertEqual(0, wire_vray_glossiness(doc, shader, mat, gate="dangling"))
        const, driver = _roughness_state(doc, shader, "transmission_extra_roughness")
        self.assertEqual("0.9", const)
        self.assertIsNone(driver)

    def test_bare_input_absent_is_why_the_018_suite_passes(self):
        """No fixture in the 018 suite stamps a constant, so its gate check
        always short-circuits on `inp is None`."""
        doc, shader = _make_bare_doc()
        self.assertIsNone(shader.getInput("specular_roughness"))
        self.assertTrue(_is_shader_input_dangling(doc, shader, "specular_roughness"))
        mat = VRayMtl(texmap_reflectionGlossiness=GLOSS_MAP)
        self.assertEqual(1, wire_vray_glossiness(doc, shader, mat, gate="dangling"),
                         "the old gate works on a bare doc — only on a bare doc")


# =============================================================================
# The new gate
# =============================================================================


class TestConnectedPredicate(unittest.TestCase):

    def test_absent_input_is_not_connected(self):
        doc, shader = _make_bare_doc()
        self.assertFalse(_is_shader_input_connected(doc, shader, "specular_roughness"))

    def test_plain_constant_is_not_connected(self):
        doc, shader = _make_bare_doc()
        _stamp_native_constant(shader, "specular_roughness", "0.85")
        self.assertFalse(_is_shader_input_connected(doc, shader, "specular_roughness"))

    def test_mtlx001_wired_map_is_connected(self):
        doc, shader = _make_bare_doc()
        _wire_mtlx001_direct_map(doc, shader, "specular_roughness", "rough.png")
        self.assertTrue(_is_shader_input_connected(doc, shader, "specular_roughness"))

    def test_direct_nodename_is_connected(self):
        doc, shader = _make_bare_doc()
        ng_free = doc.addNode("tiledimage", "img_free", "float")
        inp = shader.addInput("specular_roughness", "float")
        inp.setNodeName(ng_free.getName())
        self.assertTrue(_is_shader_input_connected(doc, shader, "specular_roughness"))

    def test_nodegraph_reference_to_a_missing_graph_is_not_connected(self):
        """MtlxIOUtil emitting a `nodegraph` reference it never scaffolded is
        the MAX-MTLX-001 dangling-connection defect. Treating it as a
        connection would leave the input permanently unfixable."""
        doc, shader = _make_bare_doc()
        inp = shader.addInput("specular_roughness", "float")
        inp.setNodeGraphString("NG_that_does_not_exist")
        inp.setOutputString("specular_roughness_output")
        self.assertFalse(_is_shader_input_connected(doc, shader, "specular_roughness"))

    def test_nodegraph_with_undriven_output_is_not_connected(self):
        doc, shader = _make_bare_doc()
        ng = doc.addNodeGraph("NG_" + shader.getName())
        ng.addOutput("specular_roughness_output", "float")   # no nodename
        inp = shader.addInput("specular_roughness", "float")
        inp.setNodeGraphString(ng.getName())
        inp.setOutputString("specular_roughness_output")
        self.assertFalse(_is_shader_input_connected(doc, shader, "specular_roughness"))

    def test_nodegraph_missing_the_named_output_is_not_connected(self):
        doc, shader = _make_bare_doc()
        ng = doc.addNodeGraph("NG_" + shader.getName())
        inp = shader.addInput("specular_roughness", "float")
        inp.setNodeGraphString(ng.getName())
        inp.setOutputString("specular_roughness_output")
        self.assertFalse(_is_shader_input_connected(doc, shader, "specular_roughness"))

    def test_predicate_disagrees_with_dangling_exactly_on_the_constant(self):
        """The two predicates must differ on the native-constant case and
        agree everywhere else. This is the whole content of the fix."""
        cases = {}

        doc, shader = _make_bare_doc("A")
        _stamp_native_constant(shader, "specular_roughness", "0.85")
        cases["native constant"] = (
            not _is_shader_input_dangling(doc, shader, "specular_roughness"),
            _is_shader_input_connected(doc, shader, "specular_roughness"))

        doc, shader = _make_bare_doc("B")
        cases["absent"] = (
            not _is_shader_input_dangling(doc, shader, "specular_roughness"),
            _is_shader_input_connected(doc, shader, "specular_roughness"))

        doc, shader = _make_bare_doc("C")
        _wire_mtlx001_direct_map(doc, shader, "specular_roughness", "rough.png")
        cases["mtlx001 map"] = (
            not _is_shader_input_dangling(doc, shader, "specular_roughness"),
            _is_shader_input_connected(doc, shader, "specular_roughness"))

        self.assertEqual((True, False), cases["native constant"],
                         "old gate skips, new gate proceeds")
        self.assertEqual((False, False), cases["absent"])
        self.assertEqual((True, True), cases["mtlx001 map"])


# =============================================================================
# Post-fix behaviour
# =============================================================================


class TestPostFix(unittest.TestCase):

    def test_native_constant_is_replaced_by_the_invert_graph(self):
        doc, shader = _make_bare_doc()
        _stamp_native_constant(shader, "specular_roughness", "0.85")
        mat = VRayMtl(texmap_reflectionGlossiness=GLOSS_MAP)

        self.assertEqual(1, wire_vray_glossiness(doc, shader, mat))

        const, driver = _roughness_state(doc, shader)
        self.assertIsNone(const, "the stale glossiness constant must be removed")
        self.assertEqual("invert_specular_roughness_gloss", driver)

    def test_invert_node_is_fed_by_the_tiledimage(self):
        doc, shader = _make_bare_doc()
        _stamp_native_constant(shader, "specular_roughness", "0.85")
        wire_vray_glossiness(doc, shader, VRayMtl(texmap_reflectionGlossiness=GLOSS_MAP))

        ng = doc.getNodeGraph("NG_" + shader.getName())
        inv = ng.getNode("invert_specular_roughness_gloss")
        self.assertEqual("ND_invert_float", inv.getNodeDefString())
        self.assertEqual("img_specular_roughness_gloss", inv.getInput("in").getNodeName())
        img = ng.getNode("img_specular_roughness_gloss")
        self.assertEqual(GLOSS_MAP, img.getInput("file").getValueString())
        self.assertFalse(img.getInput("file").hasAttribute("colorspace"),
                         "glossiness is a raw scalar, never srgb_texture")

    def test_invert_semantics_are_one_minus_glossiness(self):
        """Anchor the polarity against the MaterialX stdlib rather than
        against our own comment: ND_invert_float computes amount - in, with
        amount defaulting to 1."""
        doc, _ = _make_bare_doc()
        nd = doc.getNodeDef("ND_invert_float")
        self.assertIsNotNone(nd)
        self.assertEqual("float", nd.getType())
        amount = nd.getInput("amount")
        self.assertIsNotNone(amount)
        self.assertEqual(1.0, float(amount.getValueString()))

    def test_mtlx001_map_still_wins(self):
        doc, shader = _make_bare_doc()
        _wire_mtlx001_direct_map(doc, shader, "specular_roughness", "rough.png")
        mat = VRayMtl(texmap_reflectionGlossiness=GLOSS_MAP)

        self.assertEqual(0, wire_vray_glossiness(doc, shader, mat))
        _, driver = _roughness_state(doc, shader)
        self.assertEqual("img_specular_roughness", driver,
                         "polarity-correct roughness map keeps its direct wiring")
        ng = doc.getNodeGraph("NG_" + shader.getName())
        self.assertIsNone(ng.getNode("invert_specular_roughness_gloss"))

    def test_both_sides_author_independently(self):
        doc, shader = _make_bare_doc()
        _stamp_native_constant(shader, "specular_roughness", "0.85")
        _stamp_native_constant(shader, "transmission_extra_roughness", "0.95")
        mat = VRayMtl(texmap_reflectionGlossiness="refl.png",
                      texmap_refractionGlossiness="refr.png")

        self.assertEqual(2, wire_vray_glossiness(doc, shader, mat))
        for name in ("specular_roughness", "transmission_extra_roughness"):
            const, driver = _roughness_state(doc, shader, name)
            self.assertIsNone(const, name)
            self.assertEqual("invert_" + name + "_gloss", driver, name)

    def test_idempotent(self):
        doc, shader = _make_bare_doc()
        _stamp_native_constant(shader, "specular_roughness", "0.85")
        mat = VRayMtl(texmap_reflectionGlossiness=GLOSS_MAP)
        wire_vray_glossiness(doc, shader, mat)
        first = mx.writeToXmlString(doc)
        wire_vray_glossiness(doc, shader, mat)
        self.assertEqual(first, mx.writeToXmlString(doc),
                         "a second pass over an already-fixed doc changes nothing")

    def test_surgical_scope_other_native_constants_untouched(self):
        doc, shader = _make_bare_doc()
        _stamp_native_constant(shader, "specular_roughness", "0.85")
        for name, val in (("metalness", "1.0"), ("coat_roughness", "0.3"),
                          ("specular_IOR", "1.52"), ("transmission", "1.0")):
            _stamp_native_constant(shader, name, val)

        wire_vray_glossiness(doc, shader, VRayMtl(texmap_reflectionGlossiness=GLOSS_MAP))

        for name, val in (("metalness", "1.0"), ("coat_roughness", "0.3"),
                          ("specular_IOR", "1.52"), ("transmission", "1.0")):
            self.assertEqual(val, shader.getInput(name).getValueString(), name)

    def test_material_with_no_glossiness_map_is_a_no_op(self):
        doc, shader = _make_bare_doc()
        _stamp_native_constant(shader, "specular_roughness", "0.85")
        before = mx.writeToXmlString(doc)
        self.assertEqual(0, wire_vray_glossiness(doc, shader, VRayMtl()))
        self.assertEqual(before, mx.writeToXmlString(doc),
                         "no glossiness texmap => this pass must not touch the "
                         "scalar; that case is MAX-MTLX-GLOSSINESS-SCALAR-030")


class TestArenaCensus(unittest.TestCase):
    """Scaled to the measured fresh-export population: 371 MaterialX surfaces
    carrying a constant specular_roughness, 292 of them above 0.5."""

    def test_old_gate_fixes_none_new_gate_fixes_all_mapped(self):
        mapped, old_fixed, new_fixed = 65, 0, 0
        for i in range(mapped):
            doc, shader = _make_bare_doc("SS_%03d" % i)
            _stamp_native_constant(shader, "specular_roughness", "0.85")
            old_fixed += wire_vray_glossiness(
                doc, shader, VRayMtl(texmap_reflectionGlossiness="g%03d.png" % i),
                gate="dangling")

            doc, shader = _make_bare_doc("SS_%03d" % i)
            _stamp_native_constant(shader, "specular_roughness", "0.85")
            new_fixed += wire_vray_glossiness(
                doc, shader, VRayMtl(texmap_reflectionGlossiness="g%03d.png" % i))

        self.assertEqual(0, old_fixed, "shipped behaviour: nothing inverted")
        self.assertEqual(mapped, new_fixed)


if __name__ == "__main__":
    unittest.main(verbosity=2)
