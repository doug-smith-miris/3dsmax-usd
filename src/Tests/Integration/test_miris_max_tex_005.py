# Copyright 2026 Miris Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
"""
MAX-TEX-005 — the exporter authored texture paths to files that did not
exist, and said nothing.

Symptom
-------
On the Spectrum Center interior, 1,286 of 8,667 crowd meshes rendered flat
grey. Measured by the repair pass that diagnosed it: a colour crowd texture
has per-channel means of 63.6 / 52.8 / 51.1, while the grey these 1,286 got
reads 129.97 on all three channels — a uniform mid-grey, the signature of a
texture that never loaded.

The Max scene asks for `rp_<name>_rigged_<nnn>_dif.jpg`. The delivered
texture library ships `RP-FAN-<NAME>_<nnn>_TC0<n>.jpg`. Nothing resolves.

What is and is not the exporter's fault
---------------------------------------
The exporter authored the path the scene asked for. That is correct
behaviour, and correcting the name would be inventing a mapping: deciding
that `rp_alice_rigged_003_dif.jpg` means `RP-FAN-ALICE_003_TC01.jpg` is a
guess about someone else's naming scheme, and the repair that did it is
asset-specific by nature. It is not generalisable and does not belong in the
exporter.

What IS the exporter's fault is that it authored a path to a file that was
not there and reported nothing, so the mismatch surfaced in renders days
later rather than in the export log on the day. That is generalisable, and
it is this fix ID.

Fix
---
`discoverMaxMissingTexturesFn` + `_WarnOnUnresolvableTextures`, running
BEFORE MAX-MTLX-001's enrichment so the warning is emitted even if a later
pass bails.

Deliberately slot-list-free: it walks `getPropNames` and tests each value
with `isKindOf ... textureMap`, so it covers every texmap-bearing property
on every material class — including slots our own slotMaps have never heard
of, which are exactly the ones most likely to go unnoticed. It uses the same
`FileResolutionManager.getFullFilePath` call as MAX-TEX-003, so a file Max
can find through its own resolver is NOT reported; only a genuine absence
is.

It authors nothing into the MaterialX document. Diagnostics only. The worst
case for a false positive is one spurious warning line — which is why the
generic property walk is an acceptable risk here and would not be in a pass
that writes.

Coverage
--------
  * FanDiffuseCase   — the measured mismatch is reported, and the repair's
                       renamed file is not.
  * SlotAgnostic     — a texmap in a property no slotMap knows is still
                       reported; non-texmap properties are ignored.
  * ResolverRespect  — a file Max's resolver finds elsewhere is silent.
  * Dedupe           — one line per distinct (slot, path).
  * WrapperWalk      — nested VRayBlendMtl / VRayOverrideMtl sub-materials.
  * Quiet            — a fully-resolving material produces no output at all,
                       so the common case costs one empty MAXScript return.
  * AuthorsNothing   — the MaterialX document is byte-identical afterwards.

Run:  hython test_miris_max_tex_005.py
"""
import unittest

import MaterialX as mx


# =============================================================================
# Fake filesystem + Max resolver
# =============================================================================


class FakeDisk:
    """`doesFileExist` plus `FileResolutionManager.getFullFilePath`. The
    resolver may relocate a file Max knows about; existence is then checked
    against the RESOLVED path, matching MAX-TEX-003's ordering."""

    def __init__(self, present=(), resolves=None):
        self.present = set(present)
        self.resolves = dict(resolves or {})

    def resolve(self, path):
        """Returns (ok, resolved)."""
        if path in self.resolves:
            return True, self.resolves[path]
        return False, path

    def exists(self, path):
        return path in self.present


# =============================================================================
# Fake Max materials
# =============================================================================


class Texmap:
    """Stands in for anything `isKindOf v textureMap` accepts."""

    def __init__(self, filename):
        self.filename = filename


class MaxMaterial:
    """Arbitrary property bag; `getPropNames` is its declared order."""

    max_class_name = "VRayMtl"

    def __init__(self, **props):
        self._order = list(props.keys())
        for k, v in props.items():
            setattr(self, k, v)

    def get_prop_names(self):
        return list(self._order)


class BrokenPropNames(MaxMaterial):
    """Some plugins throw from `getPropNames`. The probe must survive it."""

    def get_prop_names(self):
        raise RuntimeError("plugin refused to enumerate properties")


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


def unwrap_blend_material_sub_mtls(m, visited=None, depth=0):
    """Mirror of `unwrapBlendMaterialSubMtls`, base first."""
    if visited is None:
        visited = []
    out = []
    if m is None or depth > 8 or id(m) in visited:
        return out
    visited.append(id(m))
    if isinstance(m, (VRayBlendMtl, VRayOverrideMtl)):
        for slot in ("baseMtl", "coatMtl_0", "giMtl"):
            child = getattr(m, slot, None)
            if child is not None:
                out.extend(unwrap_blend_material_sub_mtls(child, visited, depth + 1))
        return out
    out.append(m)
    return out


# =============================================================================
# discoverMaxMissingTextures mirror
# =============================================================================


def collect_missing_texmaps_for_mtl(mat, acc, disk):
    """Mirror of `collectMissingTexmapsForMtl`."""
    try:
        prop_names = mat.get_prop_names()
    except Exception:
        return acc
    for pn in prop_names:
        try:
            v = getattr(mat, pn, None)
        except Exception:
            v = None
        if v is None or not isinstance(v, Texmap):
            continue
        fname = v.filename
        if not fname:
            continue
        ok, resolved = disk.resolve(fname)
        if not (ok and resolved):
            resolved = fname
        if not disk.exists(resolved):
            entry = "%s|%s" % (pn, fname)
            if entry not in acc:           # appendIfUnique
                acc.append(entry)
    return acc


def discover_max_missing_textures(m, disk):
    """Mirror of `discoverMaxMissingTexturesFn`. Returns the raw lines."""
    if m is None:
        return []
    acc = []
    for cur in unwrap_blend_material_sub_mtls(m):
        acc = collect_missing_texmaps_for_mtl(cur, acc, disk)
    return acc


def warn_on_unresolvable_textures(shader, material, disk, sink):
    """Mirror of `_WarnOnUnresolvableTextures`. Authors nothing; appends one
    message per missing file to `sink` and returns the count."""
    if shader is None:
        return 0
    reported = 0
    for line in discover_max_missing_textures(material, disk):
        if "|" not in line:
            continue
        prop_name, file_path = line.split("|", 1)
        file_path = file_path.rstrip("\r\n ")
        if not prop_name or not file_path:
            continue
        sink.append(
            "MAX-TEX-005: material '%s' slot '%s' references a texture that "
            "does not exist: %s." % (shader.getName(), prop_name, file_path))
        reported += 1
    return reported


def _make_bare_doc(shader_name="SS_surface"):
    doc = mx.createDocument()
    mx.loadLibraries(mx.getDefaultDataLibraryFolders(),
                     mx.getDefaultDataSearchPath(), doc)
    shader = doc.addNode("standard_surface", shader_name, "surfaceshader")
    shader.setNodeDefString("ND_standard_surface_surfaceshader")
    return doc, shader


# The measured case.
REQUESTED = r"Y:\arena\crowd\rp_alice_rigged_003_dif.jpg"
DELIVERED = r"Y:\arena\crowd\RP-FAN-ALICE_003_TC01.jpg"


# =============================================================================


class TestFanDiffuseCase(unittest.TestCase):

    def test_the_requested_name_is_reported(self):
        disk = FakeDisk(present=[DELIVERED])
        mat = MaxMaterial(texmap_diffuse=Texmap(REQUESTED))
        self.assertEqual(["texmap_diffuse|" + REQUESTED],
                         discover_max_missing_textures(mat, disk))

    def test_the_delivered_name_is_silent(self):
        """After the asset-side rename the exporter must stop complaining."""
        disk = FakeDisk(present=[DELIVERED])
        mat = MaxMaterial(texmap_diffuse=Texmap(DELIVERED))
        self.assertEqual([], discover_max_missing_textures(mat, disk))

    def test_the_message_names_material_slot_and_path(self):
        doc, shader = _make_bare_doc("crowd_fan_alice")
        disk = FakeDisk(present=[DELIVERED])
        sink = []
        n = warn_on_unresolvable_textures(
            shader, MaxMaterial(texmap_diffuse=Texmap(REQUESTED)), disk, sink)
        self.assertEqual(1, n)
        self.assertEqual(1, len(sink))
        for token in ("crowd_fan_alice", "texmap_diffuse", REQUESTED):
            self.assertIn(token, sink[0])

    def test_the_exporter_does_not_guess_the_rename(self):
        """The point of the fix: report, never substitute. Nothing in the
        output offers the delivered filename as a correction."""
        disk = FakeDisk(present=[DELIVERED])
        doc, shader = _make_bare_doc()
        sink = []
        warn_on_unresolvable_textures(
            shader, MaxMaterial(texmap_diffuse=Texmap(REQUESTED)), disk, sink)
        self.assertNotIn(DELIVERED, sink[0])

    def test_the_1286_crowd_population(self):
        disk = FakeDisk(present=[])
        total = 0
        for i in range(1286):
            mat = MaxMaterial(texmap_diffuse=Texmap(
                r"Y:\arena\crowd\rp_fan_rigged_%04d_dif.jpg" % i))
            total += len(discover_max_missing_textures(mat, disk))
        self.assertEqual(1286, total)


class TestSlotAgnostic(unittest.TestCase):

    def test_a_slot_no_slotmap_knows_is_still_reported(self):
        """`texmap_translucency` appears in no slotMap in MtlxShaderWriter.cpp.
        A slot-list-driven probe would miss it; the property walk does not."""
        disk = FakeDisk(present=[])
        mat = MaxMaterial(texmap_translucency=Texmap("missing_translucency.png"))
        self.assertEqual(["texmap_translucency|missing_translucency.png"],
                         discover_max_missing_textures(mat, disk))

    def test_non_texmap_properties_are_ignored(self):
        disk = FakeDisk(present=[])
        mat = MaxMaterial(reflection_glossiness=0.85,
                          brdf_useRoughness=False,
                          name="CHROME_RAIL",
                          twoSided=True)
        self.assertEqual([], discover_max_missing_textures(mat, disk))

    def test_a_texmap_with_no_filename_is_ignored(self):
        """A procedural map (Noise, Checker) has no file to be missing."""
        disk = FakeDisk(present=[])
        mat = MaxMaterial(texmap_diffuse=Texmap(""))
        self.assertEqual([], discover_max_missing_textures(mat, disk))

    def test_every_missing_slot_is_reported_not_just_the_first(self):
        disk = FakeDisk(present=[])
        mat = MaxMaterial(texmap_diffuse=Texmap("a.png"),
                          texmap_bump=Texmap("b.png"),
                          texmap_reflectionGlossiness=Texmap("c.png"))
        self.assertEqual(3, len(discover_max_missing_textures(mat, disk)))

    def test_a_material_that_refuses_to_enumerate_is_survived(self):
        disk = FakeDisk(present=[])
        mat = BrokenPropNames(texmap_diffuse=Texmap("a.png"))
        self.assertEqual([], discover_max_missing_textures(mat, disk))


class TestResolverRespect(unittest.TestCase):
    """MAX-TEX-003 resolves through Max's own resolver. A file the resolver
    relocates is present, not missing — reporting it would be noise."""

    def test_resolver_relocation_is_silent(self):
        moved = r"D:\reloc\tex\brick.png"
        disk = FakeDisk(present=[moved], resolves={"brick.png": moved})
        mat = MaxMaterial(texmap_diffuse=Texmap("brick.png"))
        self.assertEqual([], discover_max_missing_textures(mat, disk))

    def test_resolver_relocation_to_a_still_missing_file_is_reported(self):
        disk = FakeDisk(present=[], resolves={"brick.png": r"D:\gone\brick.png"})
        mat = MaxMaterial(texmap_diffuse=Texmap("brick.png"))
        self.assertEqual(["texmap_diffuse|brick.png"],
                         discover_max_missing_textures(mat, disk))

    def test_the_reported_path_is_the_one_the_scene_asked_for(self):
        """Report what the artist typed, not a resolver artefact — that is the
        string they can search the scene for."""
        disk = FakeDisk(present=[], resolves={"brick.png": r"D:\gone\brick.png"})
        mat = MaxMaterial(texmap_diffuse=Texmap("brick.png"))
        line = discover_max_missing_textures(mat, disk)[0]
        self.assertTrue(line.endswith("|brick.png"))


class TestDedupe(unittest.TestCase):

    def test_the_same_slot_and_path_reported_once_across_the_walk(self):
        disk = FakeDisk(present=[])
        shared = Texmap("missing.png")
        mat = VRayBlendMtl(baseMtl=MaxMaterial(texmap_diffuse=shared),
                           coatMtl_0=MaxMaterial(texmap_diffuse=shared))
        self.assertEqual(["texmap_diffuse|missing.png"],
                         discover_max_missing_textures(mat, disk))

    def test_the_same_path_in_different_slots_reported_separately(self):
        disk = FakeDisk(present=[])
        mat = MaxMaterial(texmap_diffuse=Texmap("m.png"),
                          texmap_bump=Texmap("m.png"))
        self.assertEqual(2, len(discover_max_missing_textures(mat, disk)))


class TestWrapperWalk(unittest.TestCase):

    def test_blend_sub_materials_are_walked(self):
        disk = FakeDisk(present=[])
        mat = VRayBlendMtl(baseMtl=MaxMaterial(texmap_diffuse=Texmap("base.png")),
                           coatMtl_0=MaxMaterial(texmap_diffuse=Texmap("coat.png")))
        found = discover_max_missing_textures(mat, disk)
        self.assertEqual(2, len(found))
        self.assertTrue(found[0].endswith("base.png"), "base first")

    def test_override_sub_materials_are_walked(self):
        disk = FakeDisk(present=[])
        mat = VRayOverrideMtl(baseMtl=MaxMaterial(texmap_diffuse=Texmap("b.png")),
                              giMtl=MaxMaterial(texmap_diffuse=Texmap("g.png")))
        self.assertEqual(2, len(discover_max_missing_textures(mat, disk)))

    def test_a_cycle_terminates(self):
        disk = FakeDisk(present=[])
        a = VRayBlendMtl()
        a.baseMtl = a
        self.assertEqual([], discover_max_missing_textures(a, disk))


class TestQuiet(unittest.TestCase):
    """The common case must cost nothing and say nothing."""

    def test_a_fully_resolving_material_is_silent(self):
        disk = FakeDisk(present=["a.png", "b.png"])
        mat = MaxMaterial(texmap_diffuse=Texmap("a.png"), texmap_bump=Texmap("b.png"))
        self.assertEqual([], discover_max_missing_textures(mat, disk))

    def test_a_material_with_no_texmaps_at_all_is_silent(self):
        self.assertEqual([], discover_max_missing_textures(
            MaxMaterial(reflection_glossiness=0.85), FakeDisk()))

    def test_no_material_is_silent(self):
        self.assertEqual([], discover_max_missing_textures(None, FakeDisk()))


class TestAuthorsNothing(unittest.TestCase):
    """This pass is diagnostics only. It must be impossible for it to change
    an export — that is what makes the generic property walk an acceptable
    risk here."""

    def test_document_is_byte_identical_afterwards(self):
        doc, shader = _make_bare_doc()
        shader.addInput("specular_roughness", "float").setValueString("0.15")
        before = mx.writeToXmlString(doc)

        disk = FakeDisk(present=[])
        sink = []
        n = warn_on_unresolvable_textures(
            shader,
            MaxMaterial(texmap_diffuse=Texmap(REQUESTED),
                        texmap_bump=Texmap("missing_bump.png")),
            disk, sink)

        self.assertEqual(2, n)
        self.assertEqual(2, len(sink))
        self.assertEqual(before, mx.writeToXmlString(doc))


if __name__ == "__main__":
    unittest.main(verbosity=2)
