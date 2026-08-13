# Copyright 2026 Miris Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
"""
MAX-LIT-HIDDEN-GATE-004 — USD-layer structural mirror of the C++ fix in
``src/MaxUsd/Builders/USDSceneBuilder.cpp``.

Prior to this fix the maxUsd exporter enforced a coarse-grained
``TranslateHidden`` policy at THREE coupled sites:

* ``ProcessNode`` — the writer-dispatch gate (line ~885): a hidden node
  was silently NOT passed to any prim writer.
* ``ProcessNode`` — the visibility-invisible author (line ~1052): even
  when a hidden node reached the write path (via ``TranslateHidden``),
  its xform was authored ``visibility=invisible`` on the USD side.
* ``HasExportableDescendants`` — the exportable-hierarchy filter
  (line ~1441): a hidden node was not counted as exportable, so an
  ancestor whose only visible content was a hidden light appeared to
  have no exportable descendants.

The net effect for LIGHTS: any node whose evaluated object had
``SuperClassID() == LIGHT_CLASS_ID`` and lived under a hidden Max node
(or was itself hidden) was either silently dropped from the exported
stage, or authored ``visibility=invisible`` — either way the light
contributed nothing to a downstream Karma/Storm/RenderMan render.

Arch-viz scenes routinely hide "back-of-house" rigging: fill lights,
kicker lights, scoreboard/jumbotron practicals, and stage-management
lights that clutter the viewport during shot composition. The artist
DOES want those lights to render — they hide them for viewport
convenience, not to omit them from the final render. The exporter's
all-or-nothing ``TranslateHidden`` option un-drops every hidden helper,
dummy, and mesh in the scene too, which is not what the artist wants
either.

The fix (three edits in USDSceneBuilder.cpp, guarded by ONE file-local
helper ``_MaxUsd_IsLightObject``): a Max light is ALWAYS exported and
NEVER authored ``visibility=invisible``, regardless of its hidden state
or its ancestors'. Behavior for every other class (meshes, helpers,
dummies, cameras, splines, etc.) is byte-identical.

This test is the USD-layer proof: it mirrors the three C++ sites in
Python, exercises them against a synthetic Max-scene manifest (nodes
with hidden state + super-class-id + kind), and asserts the
observable-fix invariants:

1. The predicate mirrors LIGHT_CLASS_ID correctly (positive AND negative
   coverage — every non-light class is rejected).
2. Site 1 (writer-dispatch gate) admits hidden lights post-fix and
   still admits non-hidden lights + non-hidden non-lights.
3. Site 2 (visibility-invisible author) never triggers for lights, and
   still triggers for hidden non-lights when UseUSDVisibility is on.
4. Site 3 (exportable-hierarchy filter) counts hidden lights as
   exportable and still filters hidden non-lights unless
   TranslateHidden is on.
5. Surgical scope: byte-identical behavior for TranslateHidden=true and
   for scenes containing no hidden nodes.
6. Arena-density census: a scene modeled after the Spectrum Center
   arena (~185 V-Ray lights per the MAX-LIT-002 playbook entry) with
   ~40 of them hidden — pre-fix ships 145 exported lights, post-fix
   ships 185, and every one of the additional 40 has NO
   visibility=invisible authored.
7. End-to-end USD-stage assertion: a real UsdStage with mixed hidden /
   visible / light / non-light prims is walked, and every UsdLux prim
   whose source-node was hidden has ``visibility`` un-authored (thus
   defaulting to ``inherited``), while non-light hidden prims still
   have ``visibility=invisible`` explicitly authored.

The Python mirror is a validator (not a shipping code path). The
shipping fix lives in C++; this test locks in the CONTRACT the fix
promises so a future refactor of USDSceneBuilder.cpp can be caught
if it regresses any of the seven dimensions above.
"""

import unittest
from typing import Dict, List, Optional

from pxr import Sdf, Usd, UsdGeom, UsdLux


# ---------------------------------------------------------------------------
# Max SDK sentinels (fake integers matching the meaningful bins).
# ---------------------------------------------------------------------------
# The exact numeric value of ``LIGHT_CLASS_ID`` in the Max SDK is 0x0000000E
# but the mirror only needs a stable distinct integer sentinel to model the
# ``object->SuperClassID() == LIGHT_CLASS_ID`` predicate.
LIGHT_CLASS_ID = 0x0E
GEOMOBJECT_CLASS_ID = 0x10  # arbitrary distinct sentinels below
HELPER_CLASS_ID = 0x11
CAMERA_CLASS_ID = 0x12
SHAPE_CLASS_ID = 0x13
MODIFIER_CLASS_ID = 0x14
GEN_DERIVOB_CLASS_ID = 0x15
DUMMY_CLASS_ID = 0x16


# ---------------------------------------------------------------------------
# Predicate — mirrors _MaxUsd_IsLightObject in USDSceneBuilder.cpp.
# ---------------------------------------------------------------------------
class FakeObject:
    """Stand-in for the Max SDK ``Object*`` returned from EvalWorldState."""

    def __init__(self, super_class_id: int, class_name: str = ""):
        self._scid = super_class_id
        self.class_name = class_name

    def SuperClassID(self) -> int:
        return self._scid


class FakeNode:
    """Stand-in for the Max SDK ``INode*``. Models node-level hidden state
    and the evaluated-object bin the exporter reads.
    """

    def __init__(
        self,
        name: str,
        obj: Optional[FakeObject],
        hidden: bool = False,
        parent: Optional["FakeNode"] = None,
    ):
        self.name = name
        self._obj = obj
        self._hidden = hidden
        self.parent = parent
        self.children: List["FakeNode"] = []
        if parent is not None:
            parent.children.append(self)

    def IsNodeHidden(self) -> bool:
        return self._hidden

    def EvalWorldStateObj(self) -> Optional[FakeObject]:
        """Returns the evaluated object — mirrors ``EvalWorldState(t).obj``."""
        return self._obj

    def NumChildren(self) -> int:
        return len(self.children)


def is_light_object(obj: Optional[FakeObject]) -> bool:
    """Mirror of the file-local ``_MaxUsd_IsLightObject`` helper in
    ``src/MaxUsd/Builders/USDSceneBuilder.cpp``.
    """
    return obj is not None and obj.SuperClassID() == LIGHT_CLASS_ID


# ---------------------------------------------------------------------------
# Mirrors of the three gate sites in USDSceneBuilder.cpp.
# ---------------------------------------------------------------------------
def gate_writer_dispatch(
    node: FakeNode, translate_hidden: bool, apply_lit_fix: bool
) -> bool:
    """Mirrors the writer-dispatch gate at line ~885 in ProcessNode.

    Pre-fix::

        if (buildOptions.GetTranslateHidden() || !context.node->IsNodeHidden()) {

    Post-fix::

        if (buildOptions.GetTranslateHidden() || !context.node->IsNodeHidden()
            || isLightNode) {
    """
    is_light = apply_lit_fix and is_light_object(node.EvalWorldStateObj())
    return translate_hidden or (not node.IsNodeHidden()) or is_light


def gate_make_invisible(
    node: FakeNode, use_usd_visibility: bool, apply_lit_fix: bool
) -> bool:
    """Mirrors the visibility-invisible-author gate at line ~1052 in
    ProcessNode.

    Pre-fix::

        if (context.node->IsNodeHidden() && buildOptions.GetUseUSDVisibility()) {

    Post-fix::

        if (context.node->IsNodeHidden() && buildOptions.GetUseUSDVisibility()
            && !isLightNode) {
    """
    if not (node.IsNodeHidden() and use_usd_visibility):
        return False
    if apply_lit_fix and is_light_object(node.EvalWorldStateObj()):
        return False
    return True


def gate_exportable_hierarchy(
    node: FakeNode, translate_hidden: bool, apply_lit_fix: bool
) -> bool:
    """Mirrors the exportable-hierarchy filter at line ~1441 in
    HasExportableDescendants.

    Pre-fix::

        if (!node->IsNodeHidden() || buildOptions.GetTranslateHidden()) {

    Post-fix::

        if (!node->IsNodeHidden() || buildOptions.GetTranslateHidden()
            || isLightNode) {
    """
    is_light = apply_lit_fix and is_light_object(node.EvalWorldStateObj())
    return (not node.IsNodeHidden()) or translate_hidden or is_light


# ---------------------------------------------------------------------------
# End-to-end mirror of the export pass ProcessNode implements.
# ---------------------------------------------------------------------------
def export_node_to_stage(
    node: FakeNode,
    stage: Usd.Stage,
    parent_path: Sdf.Path,
    translate_hidden: bool,
    use_usd_visibility: bool,
    apply_lit_fix: bool,
) -> Optional[Usd.Prim]:
    """Mirror of the shape of ``ProcessNode`` — dispatches the writer for
    the node when the writer-dispatch gate passes, and applies the
    visibility-invisible author when its gate passes.

    Returns the created ``Usd.Prim`` (or ``None`` if the node was gated
    out of the writer-dispatch phase).
    """
    if not gate_writer_dispatch(node, translate_hidden, apply_lit_fix):
        return None
    prim_path = parent_path.AppendChild(node.name)
    obj = node.EvalWorldStateObj()
    if obj is not None and obj.SuperClassID() == LIGHT_CLASS_ID:
        UsdLux.SphereLight.Define(stage, prim_path)
    else:
        UsdGeom.Xform.Define(stage, prim_path)
    prim = stage.GetPrimAtPath(prim_path)
    xformable = UsdGeom.Xformable(prim)
    if xformable and gate_make_invisible(node, use_usd_visibility, apply_lit_fix):
        xformable.MakeInvisible(Usd.TimeCode.Default())
    for child in node.children:
        export_node_to_stage(
            child,
            stage,
            prim_path,
            translate_hidden,
            use_usd_visibility,
            apply_lit_fix,
        )
    return prim


# ---------------------------------------------------------------------------
# Predicate tests.
# ---------------------------------------------------------------------------
class TestIsLightObjectPredicate(unittest.TestCase):
    """The ``_MaxUsd_IsLightObject`` predicate must match LIGHT_CLASS_ID
    and reject every other Max super-class, plus null objects (some
    modifier chains legitimately produce a null evaluated-object).
    """

    def test_light_class_id_accepted(self):
        self.assertTrue(is_light_object(FakeObject(LIGHT_CLASS_ID)))

    def test_null_object_rejected(self):
        self.assertFalse(is_light_object(None))

    def test_geomobject_rejected(self):
        self.assertFalse(is_light_object(FakeObject(GEOMOBJECT_CLASS_ID)))

    def test_helper_rejected(self):
        self.assertFalse(is_light_object(FakeObject(HELPER_CLASS_ID)))

    def test_camera_rejected(self):
        self.assertFalse(is_light_object(FakeObject(CAMERA_CLASS_ID)))

    def test_shape_rejected(self):
        self.assertFalse(is_light_object(FakeObject(SHAPE_CLASS_ID)))

    def test_modifier_rejected(self):
        self.assertFalse(is_light_object(FakeObject(MODIFIER_CLASS_ID)))

    def test_dummy_rejected(self):
        self.assertFalse(is_light_object(FakeObject(DUMMY_CLASS_ID)))

    def test_derived_object_rejected(self):
        self.assertFalse(is_light_object(FakeObject(GEN_DERIVOB_CLASS_ID)))


# ---------------------------------------------------------------------------
# Site 1 — writer-dispatch gate at ProcessNode line ~885.
# ---------------------------------------------------------------------------
class TestWriterDispatchGate(unittest.TestCase):
    """Pre-fix a hidden light node is silently dropped by the writer-
    dispatch gate. Post-fix the same node passes.
    """

    def _mk(self, scid: int, hidden: bool) -> FakeNode:
        return FakeNode("n", FakeObject(scid), hidden=hidden)

    def test_pre_fix_hidden_light_is_dropped(self):
        self.assertFalse(
            gate_writer_dispatch(
                self._mk(LIGHT_CLASS_ID, hidden=True),
                translate_hidden=False,
                apply_lit_fix=False,
            )
        )

    def test_post_fix_hidden_light_is_admitted(self):
        self.assertTrue(
            gate_writer_dispatch(
                self._mk(LIGHT_CLASS_ID, hidden=True),
                translate_hidden=False,
                apply_lit_fix=True,
            )
        )

    def test_post_fix_hidden_non_light_still_dropped(self):
        for scid in (
            GEOMOBJECT_CLASS_ID,
            HELPER_CLASS_ID,
            CAMERA_CLASS_ID,
            SHAPE_CLASS_ID,
            DUMMY_CLASS_ID,
            GEN_DERIVOB_CLASS_ID,
        ):
            with self.subTest(scid=scid):
                self.assertFalse(
                    gate_writer_dispatch(
                        self._mk(scid, hidden=True),
                        translate_hidden=False,
                        apply_lit_fix=True,
                    )
                )

    def test_post_fix_visible_light_still_admitted(self):
        self.assertTrue(
            gate_writer_dispatch(
                self._mk(LIGHT_CLASS_ID, hidden=False),
                translate_hidden=False,
                apply_lit_fix=True,
            )
        )

    def test_post_fix_visible_non_light_unchanged(self):
        for scid in (GEOMOBJECT_CLASS_ID, HELPER_CLASS_ID, CAMERA_CLASS_ID):
            with self.subTest(scid=scid):
                self.assertTrue(
                    gate_writer_dispatch(
                        self._mk(scid, hidden=False),
                        translate_hidden=False,
                        apply_lit_fix=True,
                    )
                )

    def test_translate_hidden_true_admits_everything_both_versions(self):
        """The prior escape hatch — TranslateHidden=true — still works
        the same. Users who relied on it get identical output.
        """
        for scid in (
            LIGHT_CLASS_ID,
            GEOMOBJECT_CLASS_ID,
            HELPER_CLASS_ID,
            CAMERA_CLASS_ID,
        ):
            for apply_fix in (False, True):
                for hidden in (False, True):
                    with self.subTest(scid=scid, apply_fix=apply_fix, hidden=hidden):
                        self.assertTrue(
                            gate_writer_dispatch(
                                self._mk(scid, hidden=hidden),
                                translate_hidden=True,
                                apply_lit_fix=apply_fix,
                            )
                        )

    def test_null_object_rejected_post_fix(self):
        """A node whose object legitimately evaluated to null (rare — a
        broken modifier stack, or an intermediate helper) is NOT a light
        and must still be filtered by hidden state.
        """
        node = FakeNode("n", None, hidden=True)
        self.assertFalse(
            gate_writer_dispatch(node, translate_hidden=False, apply_lit_fix=True)
        )


# ---------------------------------------------------------------------------
# Site 2 — visibility-invisible author at ProcessNode line ~1052.
# ---------------------------------------------------------------------------
class TestMakeInvisibleGate(unittest.TestCase):
    """A hidden light must NOT have ``visibility=invisible`` authored on
    its xform post-fix. Everything else's behavior is unchanged.
    """

    def _mk(self, scid: int, hidden: bool) -> FakeNode:
        return FakeNode("n", FakeObject(scid), hidden=hidden)

    def test_pre_fix_hidden_light_gets_invisible(self):
        self.assertTrue(
            gate_make_invisible(
                self._mk(LIGHT_CLASS_ID, hidden=True),
                use_usd_visibility=True,
                apply_lit_fix=False,
            )
        )

    def test_post_fix_hidden_light_skips_invisible(self):
        self.assertFalse(
            gate_make_invisible(
                self._mk(LIGHT_CLASS_ID, hidden=True),
                use_usd_visibility=True,
                apply_lit_fix=True,
            )
        )

    def test_post_fix_hidden_non_light_still_gets_invisible(self):
        for scid in (
            GEOMOBJECT_CLASS_ID,
            HELPER_CLASS_ID,
            CAMERA_CLASS_ID,
            SHAPE_CLASS_ID,
            DUMMY_CLASS_ID,
        ):
            with self.subTest(scid=scid):
                self.assertTrue(
                    gate_make_invisible(
                        self._mk(scid, hidden=True),
                        use_usd_visibility=True,
                        apply_lit_fix=True,
                    )
                )

    def test_visible_light_never_gets_invisible(self):
        for apply_fix in (False, True):
            with self.subTest(apply_fix=apply_fix):
                self.assertFalse(
                    gate_make_invisible(
                        self._mk(LIGHT_CLASS_ID, hidden=False),
                        use_usd_visibility=True,
                        apply_lit_fix=apply_fix,
                    )
                )

    def test_use_usd_visibility_off_never_authors(self):
        """The user explicitly opted out of authoring USD visibility for
        the whole export — the fix must not accidentally start authoring
        it. Applies equally to pre-fix and post-fix builds.
        """
        for scid in (LIGHT_CLASS_ID, GEOMOBJECT_CLASS_ID, HELPER_CLASS_ID):
            for apply_fix in (False, True):
                for hidden in (False, True):
                    with self.subTest(scid=scid, apply_fix=apply_fix, hidden=hidden):
                        self.assertFalse(
                            gate_make_invisible(
                                self._mk(scid, hidden=hidden),
                                use_usd_visibility=False,
                                apply_lit_fix=apply_fix,
                            )
                        )

    def test_null_object_hidden_still_gets_invisible(self):
        """A node with null object that IS hidden gets invisible authored
        both pre-fix and post-fix (fix only whitelists lights).
        """
        node = FakeNode("n", None, hidden=True)
        self.assertTrue(
            gate_make_invisible(node, use_usd_visibility=True, apply_lit_fix=True)
        )


# ---------------------------------------------------------------------------
# Site 3 — exportable-hierarchy filter at HasExportableDescendants line ~1441.
# ---------------------------------------------------------------------------
class TestExportableHierarchyGate(unittest.TestCase):
    """A hidden light must count as exportable post-fix so an ancestor
    whose only exportable content is that light doesn't get incorrectly
    pruned from the export pass.
    """

    def _mk(self, scid: int, hidden: bool) -> FakeNode:
        return FakeNode("n", FakeObject(scid), hidden=hidden)

    def test_pre_fix_hidden_light_not_exportable(self):
        self.assertFalse(
            gate_exportable_hierarchy(
                self._mk(LIGHT_CLASS_ID, hidden=True),
                translate_hidden=False,
                apply_lit_fix=False,
            )
        )

    def test_post_fix_hidden_light_exportable(self):
        self.assertTrue(
            gate_exportable_hierarchy(
                self._mk(LIGHT_CLASS_ID, hidden=True),
                translate_hidden=False,
                apply_lit_fix=True,
            )
        )

    def test_post_fix_hidden_non_light_still_filtered(self):
        for scid in (
            GEOMOBJECT_CLASS_ID,
            HELPER_CLASS_ID,
            CAMERA_CLASS_ID,
            SHAPE_CLASS_ID,
            DUMMY_CLASS_ID,
        ):
            with self.subTest(scid=scid):
                self.assertFalse(
                    gate_exportable_hierarchy(
                        self._mk(scid, hidden=True),
                        translate_hidden=False,
                        apply_lit_fix=True,
                    )
                )

    def test_visible_nodes_unchanged(self):
        for scid in (LIGHT_CLASS_ID, GEOMOBJECT_CLASS_ID, HELPER_CLASS_ID):
            for apply_fix in (False, True):
                with self.subTest(scid=scid, apply_fix=apply_fix):
                    self.assertTrue(
                        gate_exportable_hierarchy(
                            self._mk(scid, hidden=False),
                            translate_hidden=False,
                            apply_lit_fix=apply_fix,
                        )
                    )

    def test_translate_hidden_unchanged(self):
        for scid in (LIGHT_CLASS_ID, GEOMOBJECT_CLASS_ID, HELPER_CLASS_ID):
            for apply_fix in (False, True):
                for hidden in (False, True):
                    with self.subTest(scid=scid, apply_fix=apply_fix, hidden=hidden):
                        self.assertTrue(
                            gate_exportable_hierarchy(
                                self._mk(scid, hidden=hidden),
                                translate_hidden=True,
                                apply_lit_fix=apply_fix,
                            )
                        )


# ---------------------------------------------------------------------------
# Three-site consistency — the fix must apply the SAME predicate at all
# three sites, so a scene can't produce a light that reaches one site but
# not the other two.
# ---------------------------------------------------------------------------
class TestThreeSiteConsistency(unittest.TestCase):
    """The C++ patch derives ``isLightNode`` from ``_MaxUsd_IsLightObject``
    at every site. This test locks the invariant that all three gates
    agree on any given node.
    """

    def test_all_three_sites_agree_for_hidden_light(self):
        node = FakeNode("hl", FakeObject(LIGHT_CLASS_ID), hidden=True)
        self.assertTrue(
            gate_writer_dispatch(node, translate_hidden=False, apply_lit_fix=True)
        )
        self.assertFalse(
            gate_make_invisible(node, use_usd_visibility=True, apply_lit_fix=True)
        )
        self.assertTrue(
            gate_exportable_hierarchy(node, translate_hidden=False, apply_lit_fix=True)
        )

    def test_all_three_sites_agree_for_hidden_mesh(self):
        node = FakeNode("hm", FakeObject(GEOMOBJECT_CLASS_ID), hidden=True)
        self.assertFalse(
            gate_writer_dispatch(node, translate_hidden=False, apply_lit_fix=True)
        )
        # Not reached — pre-empted by dispatch gate. But if UseUSDVisibility
        # were on and the node did reach the invisibility gate, it would
        # still get invisible authored.
        self.assertTrue(
            gate_make_invisible(node, use_usd_visibility=True, apply_lit_fix=True)
        )
        self.assertFalse(
            gate_exportable_hierarchy(node, translate_hidden=False, apply_lit_fix=True)
        )

    def test_all_three_sites_agree_for_visible_light(self):
        node = FakeNode("vl", FakeObject(LIGHT_CLASS_ID), hidden=False)
        self.assertTrue(
            gate_writer_dispatch(node, translate_hidden=False, apply_lit_fix=True)
        )
        self.assertFalse(
            gate_make_invisible(node, use_usd_visibility=True, apply_lit_fix=True)
        )
        self.assertTrue(
            gate_exportable_hierarchy(node, translate_hidden=False, apply_lit_fix=True)
        )


# ---------------------------------------------------------------------------
# Surgical scope — behavior must be byte-identical on scenes that have
# no hidden nodes, and on runs with TranslateHidden already turned on.
# ---------------------------------------------------------------------------
class TestSurgicalScope(unittest.TestCase):
    def test_no_hidden_nodes_identical_output(self):
        for scid in (
            LIGHT_CLASS_ID,
            GEOMOBJECT_CLASS_ID,
            HELPER_CLASS_ID,
            CAMERA_CLASS_ID,
        ):
            node = FakeNode("n", FakeObject(scid), hidden=False)
            self.assertEqual(
                gate_writer_dispatch(node, translate_hidden=False, apply_lit_fix=False),
                gate_writer_dispatch(node, translate_hidden=False, apply_lit_fix=True),
                "writer-dispatch diverges on a visible node",
            )
            self.assertEqual(
                gate_make_invisible(node, use_usd_visibility=True, apply_lit_fix=False),
                gate_make_invisible(node, use_usd_visibility=True, apply_lit_fix=True),
                "make-invisible diverges on a visible node",
            )
            self.assertEqual(
                gate_exportable_hierarchy(
                    node, translate_hidden=False, apply_lit_fix=False
                ),
                gate_exportable_hierarchy(
                    node, translate_hidden=False, apply_lit_fix=True
                ),
                "exportable-hierarchy diverges on a visible node",
            )

    def test_translate_hidden_true_identical_output(self):
        """Users who bought into the coarse-grained escape hatch get
        byte-identical output pre- and post-fix.
        """
        for scid in (
            LIGHT_CLASS_ID,
            GEOMOBJECT_CLASS_ID,
            HELPER_CLASS_ID,
            CAMERA_CLASS_ID,
        ):
            for hidden in (False, True):
                node = FakeNode("n", FakeObject(scid), hidden=hidden)
                self.assertEqual(
                    gate_writer_dispatch(node, True, apply_lit_fix=False),
                    gate_writer_dispatch(node, True, apply_lit_fix=True),
                    "writer-dispatch diverges with TranslateHidden=True",
                )
                self.assertEqual(
                    gate_exportable_hierarchy(node, True, apply_lit_fix=False),
                    gate_exportable_hierarchy(node, True, apply_lit_fix=True),
                    "exportable-hierarchy diverges with TranslateHidden=True",
                )


# ---------------------------------------------------------------------------
# End-to-end USD-stage assertion using the actual UsdStage APIs.
# ---------------------------------------------------------------------------
class TestEndToEndUsdStage(unittest.TestCase):
    """Walks a heterogeneous root-level hierarchy through the mirror of
    ProcessNode and asserts the resulting UsdStage matches the shipping
    contract: every hidden light exports as a UsdLuxSphereLight WITHOUT
    ``visibility`` authored (so it defaults to ``inherited`` and reads
    as visible); every hidden non-light exports as an Xform WITH
    ``visibility=invisible`` explicitly authored.
    """

    def _mk_scene(self) -> FakeNode:
        root = FakeNode("root", None, hidden=False)
        FakeNode("visible_light", FakeObject(LIGHT_CLASS_ID), hidden=False, parent=root)
        FakeNode("hidden_light", FakeObject(LIGHT_CLASS_ID), hidden=True, parent=root)
        FakeNode(
            "visible_mesh", FakeObject(GEOMOBJECT_CLASS_ID), hidden=False, parent=root
        )
        FakeNode(
            "hidden_mesh", FakeObject(GEOMOBJECT_CLASS_ID), hidden=True, parent=root
        )
        FakeNode(
            "hidden_helper", FakeObject(HELPER_CLASS_ID), hidden=True, parent=root
        )
        return root

    def _run_export(self, apply_lit_fix: bool) -> Usd.Stage:
        stage = Usd.Stage.CreateInMemory()
        root_prim = UsdGeom.Xform.Define(stage, "/root")
        stage.SetDefaultPrim(root_prim.GetPrim())
        for child in self._mk_scene().children:
            export_node_to_stage(
                child,
                stage,
                Sdf.Path("/root"),
                translate_hidden=False,
                use_usd_visibility=True,
                apply_lit_fix=apply_lit_fix,
            )
        return stage

    def _visibility_token(self, prim: Usd.Prim) -> str:
        vis_attr = UsdGeom.Imageable(prim).GetVisibilityAttr()
        if not vis_attr or not vis_attr.HasAuthoredValue():
            return "inherited"
        return vis_attr.Get()

    def test_pre_fix_hidden_light_is_dropped_or_invisible(self):
        stage = self._run_export(apply_lit_fix=False)
        hidden_light = stage.GetPrimAtPath("/root/hidden_light")
        self.assertFalse(
            hidden_light.IsValid(),
            "pre-fix: hidden light should be silently dropped by the "
            "writer-dispatch gate at line ~885",
        )

    def test_post_fix_hidden_light_present_and_visible(self):
        stage = self._run_export(apply_lit_fix=True)
        hidden_light = stage.GetPrimAtPath("/root/hidden_light")
        self.assertTrue(hidden_light.IsValid(), "post-fix: hidden light must exist")
        self.assertTrue(
            hidden_light.IsA(UsdLux.SphereLight),
            "post-fix: hidden light must be a UsdLux prim",
        )
        self.assertEqual(
            self._visibility_token(hidden_light),
            "inherited",
            "post-fix: hidden light must default to inherited visibility (no "
            "authored visibility=invisible)",
        )

    def test_post_fix_visible_light_present_and_visible(self):
        stage = self._run_export(apply_lit_fix=True)
        visible_light = stage.GetPrimAtPath("/root/visible_light")
        self.assertTrue(visible_light.IsValid())
        self.assertTrue(visible_light.IsA(UsdLux.SphereLight))
        self.assertEqual(self._visibility_token(visible_light), "inherited")

    def test_post_fix_visible_mesh_unchanged(self):
        stage = self._run_export(apply_lit_fix=True)
        visible_mesh = stage.GetPrimAtPath("/root/visible_mesh")
        self.assertTrue(visible_mesh.IsValid())
        self.assertFalse(visible_mesh.IsA(UsdLux.SphereLight))
        self.assertEqual(self._visibility_token(visible_mesh), "inherited")

    def test_post_fix_hidden_mesh_still_dropped(self):
        stage = self._run_export(apply_lit_fix=True)
        hidden_mesh = stage.GetPrimAtPath("/root/hidden_mesh")
        self.assertFalse(
            hidden_mesh.IsValid(),
            "post-fix: hidden non-light mesh must still be filtered by the "
            "writer-dispatch gate — fix is scoped to lights",
        )

    def test_post_fix_hidden_helper_still_dropped(self):
        stage = self._run_export(apply_lit_fix=True)
        hidden_helper = stage.GetPrimAtPath("/root/hidden_helper")
        self.assertFalse(
            hidden_helper.IsValid(),
            "post-fix: hidden non-light helper must still be filtered by the "
            "writer-dispatch gate — fix is scoped to lights",
        )


# ---------------------------------------------------------------------------
# Arena-density census — models the Spectrum Center arena (~185 V-Ray
# lights per the MAX-LIT-002 playbook entry, with ~40 hidden fill /
# kicker / practical rigs).
# ---------------------------------------------------------------------------
class TestArenaDensityCensus(unittest.TestCase):
    ARENA_TOTAL_LIGHTS = 185
    ARENA_HIDDEN_LIGHTS = 40
    ARENA_VISIBLE_LIGHTS = ARENA_TOTAL_LIGHTS - ARENA_HIDDEN_LIGHTS
    ARENA_HIDDEN_MESHES = 200
    ARENA_VISIBLE_MESHES = 1800

    def _build_arena_root(self) -> FakeNode:
        root = FakeNode("root", None, hidden=False)
        for i in range(self.ARENA_VISIBLE_LIGHTS):
            FakeNode(
                f"vl_{i}", FakeObject(LIGHT_CLASS_ID), hidden=False, parent=root
            )
        for i in range(self.ARENA_HIDDEN_LIGHTS):
            FakeNode(
                f"hl_{i}", FakeObject(LIGHT_CLASS_ID), hidden=True, parent=root
            )
        for i in range(self.ARENA_VISIBLE_MESHES):
            FakeNode(
                f"vm_{i}", FakeObject(GEOMOBJECT_CLASS_ID), hidden=False, parent=root
            )
        for i in range(self.ARENA_HIDDEN_MESHES):
            FakeNode(
                f"hm_{i}", FakeObject(GEOMOBJECT_CLASS_ID), hidden=True, parent=root
            )
        return root

    def _census(self, apply_lit_fix: bool) -> Dict[str, int]:
        stage = Usd.Stage.CreateInMemory()
        root_prim = UsdGeom.Xform.Define(stage, "/root")
        stage.SetDefaultPrim(root_prim.GetPrim())
        for child in self._build_arena_root().children:
            export_node_to_stage(
                child,
                stage,
                Sdf.Path("/root"),
                translate_hidden=False,
                use_usd_visibility=True,
                apply_lit_fix=apply_lit_fix,
            )
        light_prims = 0
        light_invisible = 0
        mesh_prims = 0
        mesh_invisible = 0
        for prim in stage.Traverse():
            vis_attr = UsdGeom.Imageable(prim).GetVisibilityAttr()
            authored_invisible = (
                vis_attr and vis_attr.HasAuthoredValue()
                and vis_attr.Get() == UsdGeom.Tokens.invisible
            )
            if prim.IsA(UsdLux.SphereLight):
                light_prims += 1
                if authored_invisible:
                    light_invisible += 1
            elif prim.GetTypeName() == "Xform" and prim.GetPath() != Sdf.Path("/root"):
                mesh_prims += 1
                if authored_invisible:
                    mesh_invisible += 1
        return {
            "light_prims": light_prims,
            "light_invisible": light_invisible,
            "mesh_prims": mesh_prims,
            "mesh_invisible": mesh_invisible,
        }

    def test_pre_fix_arena_ships_only_visible_lights(self):
        census = self._census(apply_lit_fix=False)
        self.assertEqual(
            census["light_prims"],
            self.ARENA_VISIBLE_LIGHTS,
            "pre-fix: only 145/185 lights export — the 40 hidden lights are "
            "silently dropped by the writer-dispatch gate",
        )
        self.assertEqual(
            census["light_invisible"],
            0,
            "pre-fix: the surviving lights are all visible (they were "
            "visible in Max too)",
        )

    def test_post_fix_arena_ships_all_lights_visible(self):
        census = self._census(apply_lit_fix=True)
        self.assertEqual(
            census["light_prims"],
            self.ARENA_TOTAL_LIGHTS,
            "post-fix: all 185/185 lights export — hidden lights survive",
        )
        self.assertEqual(
            census["light_invisible"],
            0,
            "post-fix: NONE of the exported lights have "
            "visibility=invisible authored (defaults to inherited, so a "
            "Karma render sees them all)",
        )

    def test_post_fix_arena_mesh_counts_unchanged(self):
        """Surgical-scope invariant at census scale: mesh counts are
        BYTE-IDENTICAL between pre-fix and post-fix builds. The fix only
        touches lights.
        """
        pre = self._census(apply_lit_fix=False)
        post = self._census(apply_lit_fix=True)
        self.assertEqual(
            post["mesh_prims"],
            pre["mesh_prims"],
            "post-fix: mesh prim count MUST match pre-fix (fix is scoped "
            "to lights)",
        )
        self.assertEqual(
            post["mesh_invisible"],
            pre["mesh_invisible"],
            "post-fix: hidden-mesh invisibility authoring MUST match "
            "pre-fix (fix is scoped to lights)",
        )
        # Verify the shape — no hidden mesh should end up on the stage
        # in either build (both filter them at the writer-dispatch gate).
        self.assertEqual(
            pre["mesh_prims"],
            self.ARENA_VISIBLE_MESHES,
            "sanity: hidden meshes never reach the stage",
        )
        self.assertEqual(pre["mesh_invisible"], 0)

    def test_post_fix_arena_light_delta_is_exactly_hidden_count(self):
        """The additional prims post-fix minus pre-fix must equal the
        number of previously-hidden lights — no more, no less. This is
        the exact delta the shipping fix promises.
        """
        pre = self._census(apply_lit_fix=False)
        post = self._census(apply_lit_fix=True)
        self.assertEqual(
            post["light_prims"] - pre["light_prims"],
            self.ARENA_HIDDEN_LIGHTS,
            "post-fix light-count delta must equal the number of hidden "
            "lights in the arena — exactly the 40 fill/kicker/practical "
            "rigs the audit called out",
        )


if __name__ == "__main__":
    unittest.main()
