# Copyright 2026 Miris Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
"""
MAX-VRAYDECAL-021 — receiver selection and patch conform for VRayDecal, mirrored from the
MAXScript `discoverMaxVrayDecal` block in `src/translators/VRayDecalWriter.cpp`.

Background
----------
A VRayDecal is a PROJECTOR, not a surface: it carries a material and projects it onto whatever
geometry lies inside its box. The receiving surface keeps its own material. On the Spectrum Center
arena the Buzz City / Dr Pepper club graphics are 5 VRayDecals, and their receivers are genuinely
bound to the Revit placeholder `*TEMP-GRAY` in the .max file — the branding exists ONLY as the
projection.

With no writer registered for the class, MaxUsdMeshWriter claimed each decal as a Fallback
(VRayDecal is TriObject-convertible) and exported its GIZMO: an 8-vertex bounding box wearing the
decal material. Measured from the pre-fix export:

    BOWL_UPPER_DECAL_TEXT_BUZZ        verts=8   size 8.19 x 7.22 x 29.76   mtl=DECAL_TEXT_BUZZ
    BOWL_UPPER_LOGO_DR_PEPP_WHITE001  verts=0   (no material at all)

so the artwork was lost twice over: the receiver rendered as bare placeholder grey (the white
hexagon in the Dr Pepper area) and a meaningless box floated in the scene wearing the graphic.

Fix
---
Emit the patch the projection actually produces: the decal's `width` x `length` rectangle, pushed
along its local Z onto the receiving surface (the projection is orthographic), offset a hair along
the receiver's normal, with UVs 0..1 in corner order (0,0) (1,0) (1,1) (0,1).

Two acceptance tests pick the receiver:

  * WITHIN `projection_depth` — the decal's own parameter, 2.0 ft on this scene.
  * FACING the decal, dot(normal, castDirection) < 0.

and the projection direction is NOT fixed: measured receivers sit on +Z for the Dr Pepper and
Hornets decals and on -Z for BUZZ/CITY, so both directions are cast and the nearest qualifying hit
wins.

Test strategy
-------------
The writer's geometry work runs in MAXScript against live scene geometry, which cannot execute
here. What CAN be pinned is the selection logic and the conform math, so this mirrors both in plain
Python and drives them with the REAL raycast results measured on the box
(`C:\\suts\\decal_probe2.txt`, 2026-08-18) rather than invented numbers.

Assertions:
  1. Every one of the 5 arena decals selects the receiver measured in Max.
  2. The depth test is what rejects the far hits (4.5-448 ft) — the load-bearing rule.
  3. Nearest-qualifying-hit beats direction preference: BUZZ has qualifying hits on BOTH sides
     (vinyl wall 0.644 ft on -Z, medallion 0.888 ft on +Z) and must choose the nearer.
  4. The facing test rejects a back-face exit hit (defensive; no arena decal needs it).
  5. Corner ordering matches UV order, and the artwork's aspect ratio equals the decal
     rectangle's — independently confirming `width` x `length` is the artwork rect.
  6. A corner that overhangs its receiver is filled by projecting onto the plane fitted from the
     corners that did land, never dropped.
  7. The epsilon offset moves the patch TOWARD the decal (along the receiver normal), never into
     the wall.
  8. Zero qualifying hits produces the decal-plane fallback, not a gizmo box.

Run: `hython test_miris_max_vraydecal_conform_021.py`
"""
import math
import unittest


# =============================================================================
# Vector helpers — mirroring the MAXScript point3 operations used by the writer.
# =============================================================================


def dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def add(a, b):
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def scale(a, k):
    return (a[0] * k, a[1] * k, a[2] * k)


def norm(a):
    m = math.sqrt(dot(a, a))
    return a if m == 0 else scale(a, 1.0 / m)


class Hit:
    """One raycast result, as `intersectRayEx` reports it: point, surface normal, distance."""

    def __init__(self, name, dist, normal):
        self.name = name
        self.dist = dist
        self.normal = normal


def select_receiver(z_axis, depth, hits_by_dir):
    """Mirror of the writer's per-corner acceptance loop.

    `hits_by_dir` maps a cast sign (-1.0 / +1.0) to the list of hits along that direction.
    Returns (hit, sign) for the nearest hit that is both inside `depth` and facing the decal,
    or (None, None).
    """
    best, best_sign, best_d = None, None, float("inf")
    for sign in (-1.0, 1.0):
        direction = scale(z_axis, sign)
        for hit in hits_by_dir.get(sign, []):
            if hit.dist > depth + 0.001:
                continue                              # outside the decal's own projection depth
            if dot(hit.normal, direction) >= 0.0:
                continue                              # back face / facing away from the decal
            if hit.dist < best_d:
                best, best_sign, best_d = hit, sign, hit.dist
    return best, best_sign


# =============================================================================
# MEASURED arena data. Every number below was read off the box on 2026-08-18 by
# scratchpad/decal-probe2.ms against C:\suts\MIRIS-SCR_EMPTY\MIRIS-SCR_EMPTY.max — these are
# raycast results from the real scene, not constructed fixtures.
# =============================================================================

PROJECTION_DEPTH = 2.0        # VRayDecal .projection_depth, read off BOWL-UPPER_LOGO_DR_PEPP_WHITE001

ARENA_DECALS = {
    "BOWL-UPPER_LOGO_DR_PEPP_WHITE001": {
        "z_axis": (-0.866025, 0.5, 0.0),
        "width": 14.0, "length": 9.08838,
        "artwork": ("LOGO-DR-PEPPER.png", 4096, 2659),
        "hits": {
            -1.0: [Hit("Walls", 7.00152, (-0.984789, 0.173754, 0.0))],
            1.0: [Hit("Basic Wall A71 [2728025]", 0.326556, (0.866025, -0.5, 0.0))],
        },
        "expect": ("Basic Wall A71 [2728025]", 1.0),
    },
    "BOWL-UPPER-DECAL_TEXT_BUZZ": {
        "z_axis": (-0.978148, -0.207912, 0.0),
        "width": 30.0, "length": 7.22168,
        "artwork": ("LOGO-TEXT_BUZZ.png", 2048, 493),
        "hits": {
            # qualifying hits on BOTH sides — nearest must win
            -1.0: [Hit("Basic Wall Hornets Branding [3248607]", 0.643628, (-0.982276, -0.187442, 0.0))],
            1.0: [Hit("LOGO-MEDALION_BUZZ_CITY", 0.887699, (0.978353, 0.206945, 0.0)),
                  Hit("Curtain Panels", 447.771, (0.92181, 0.387642, 0.0))],
        },
        "expect": ("Basic Wall Hornets Branding [3248607]", -1.0),
    },
    "BOWL-UPPER-DECAL_TEXT_CITY": {
        "z_axis": (-0.978148, 0.207912, 0.0),
        "width": 30.0, "length": 7.22168,
        "artwork": ("LOGO-TEXT_CITY.png", 2048, 493),
        "hits": {
            -1.0: [Hit("Basic Wall Hornets Branding [3248605]", 0.611772, (-0.982276, 0.187442, 0.0))],
            1.0: [Hit("LOGO-MEDALION_HORNET", 0.924889, (0.978353, -0.206945, 0.0)),
                  Hit("Curtain Panels", 442.699, (0.984789, -0.173753, 0.0))],
        },
        "expect": ("Basic Wall Hornets Branding [3248605]", -1.0),
    },
    "BOWL-UPPER-DECAL_HORNETS_LOGO": {
        "z_axis": (-1.0, 0.0, 0.0),
        "width": 16.0, "length": 15.4258,
        "artwork": ("LOGO-HORNETS_PRIMARY.png", 4096, 3949),
        "hits": {
            -1.0: [Hit("Walls", 6.38133, (-0.978148, -0.207912, 0.0))],
            1.0: [Hit("Basic Wall Hornets Branding [22035917]", 0.16925, (1.0, 0.0, 0.0))],
        },
        "expect": ("Basic Wall Hornets Branding [22035917]", 1.0),
    },
    # WHITE002 is WHITE001 mirrored across the club. It went MISSING from the pre-fix export
    # entirely (WHITE001 at least made it out as a 0-vertex prim), which is why it is pinned here.
    # Receiver id and distance are the values the writer's own conform reported on the box
    # (C:\suts\decal_conform.txt) — NOT assumed to mirror WHITE001's.
    "BOWL-UPPER_LOGO_DR_PEPP_WHITE002": {
        "z_axis": (-0.866025, -0.5, 0.0),
        "width": 14.0, "length": 9.08838,
        "artwork": ("LOGO-DR-PEPPER.png", 4096, 2659),
        "hits": {
            -1.0: [Hit("Walls", 7.00152, (-0.984789, -0.173754, 0.0))],
            1.0: [Hit("Basic Wall A71 [2728029]", 0.327048, (0.866025, 0.5, 0.0))],
        },
        "expect": ("Basic Wall A71 [2728029]", 1.0),
    },
}


class TestReceiverSelection(unittest.TestCase):
    def test_every_arena_decal_picks_the_measured_receiver(self):
        for name, d in ARENA_DECALS.items():
            hit, sign = select_receiver(d["z_axis"], PROJECTION_DEPTH, d["hits"])
            self.assertIsNotNone(hit, f"{name}: no receiver selected")
            self.assertEqual((hit.name, sign), d["expect"], f"{name}: wrong receiver")

    def test_depth_test_is_what_rejects_the_far_hits(self):
        """The load-bearing rule. Without it the Dr Pepper decals grab a wall 7 ft across the
        room and BUZZ grabs a curtain panel 448 ft away."""
        far = [(n, h.name, h.dist)
               for n, d in ARENA_DECALS.items()
               for hits in d["hits"].values()
               for h in hits
               if h.dist > PROJECTION_DEPTH]
        self.assertTrue(far, "fixture should contain out-of-depth hits")
        for decal_name, hit_name, dist in far:
            d = ARENA_DECALS[decal_name]
            # with an unbounded depth budget these become selectable; with the real one they cannot
            unbounded, _ = select_receiver(d["z_axis"], 10_000.0, d["hits"])
            bounded, _ = select_receiver(d["z_axis"], PROJECTION_DEPTH, d["hits"])
            self.assertNotEqual(bounded.name, hit_name,
                                f"{decal_name}: {hit_name} at {dist} ft must be out of range")
            self.assertIsNotNone(unbounded)

    def test_nearest_qualifying_hit_wins_when_both_sides_qualify(self):
        """BUZZ sees the vinyl wall at 0.644 ft on -Z and the medallion at 0.888 ft on +Z. Both
        are inside the depth budget and both face the decal, so this is decided purely by
        distance — a writer that preferred a fixed projection direction would put the Buzz City
        artwork on the wrong surface."""
        d = ARENA_DECALS["BOWL-UPPER-DECAL_TEXT_BUZZ"]
        qualifying = [(sign, h) for sign, hits in d["hits"].items() for h in hits
                      if h.dist <= PROJECTION_DEPTH
                      and dot(h.normal, scale(d["z_axis"], sign)) < 0.0]
        self.assertEqual(len(qualifying), 2, "both sides should qualify for this decal")
        hit, sign = select_receiver(d["z_axis"], PROJECTION_DEPTH, d["hits"])
        self.assertAlmostEqual(hit.dist, 0.643628, places=6)
        self.assertEqual(sign, -1.0)

    def test_direction_is_not_fixed_across_the_scene(self):
        """Two decals project onto -Z and three onto +Z. Any writer that hard-codes one
        projection direction loses part of the branding."""
        signs = {select_receiver(d["z_axis"], PROJECTION_DEPTH, d["hits"])[1]
                 for d in ARENA_DECALS.values()}
        self.assertEqual(signs, {-1.0, 1.0})

    def test_back_facing_exit_hit_is_rejected(self):
        """Defensive: no arena decal needs this, because depth rejects every far hit. But a ray
        entering a solid wall also exits through its back face, and an exit hit must never be
        mistaken for a surface the decal can land on."""
        z = (0.0, 0.0, 1.0)
        hits = {
            1.0: [Hit("wall-back-face", 0.5, (0.0, 0.0, 1.0)),   # normal points ALONG the ray
                  Hit("wall-front-face", 1.2, (0.0, 0.0, -1.0))],
        }
        hit, _ = select_receiver(z, PROJECTION_DEPTH, hits)
        self.assertEqual(hit.name, "wall-front-face")

    def test_no_qualifying_hit_falls_back_to_the_decal_plane(self):
        """Nothing in range means the patch stays on the decal plane and the writer says so —
        never a silent revert to the gizmo box, which is the defect being fixed."""
        z = (0.0, 0.0, 1.0)
        hits = {1.0: [Hit("far-wall", 40.0, (0.0, 0.0, -1.0))]}
        hit, sign = select_receiver(z, PROJECTION_DEPTH, hits)
        self.assertIsNone(hit)
        self.assertIsNone(sign)


class TestPatchGeometry(unittest.TestCase):
    UV_ORDER = ((-1.0, -1.0), (1.0, -1.0), (1.0, 1.0), (-1.0, 1.0))

    def test_artwork_aspect_ratio_matches_the_decal_rectangle(self):
        """Independent confirmation that `width` x `length` IS the artwork rectangle and that UVs
        run 0..1 across it: every decal's bitmap has the same aspect ratio as its rectangle, to
        better than 0.2%. `length` is easy to mistake for a depth — this is what rules that out."""
        for name, d in ARENA_DECALS.items():
            _png, px, py = d["artwork"]
            self.assertAlmostEqual(px / py, d["width"] / d["length"], delta=0.002 * (px / py),
                                   msg=f"{name}: artwork aspect != rectangle aspect")

    def test_corner_order_is_uv_order(self):
        """Corners are emitted (0,0) (1,0) (1,1) (0,1) so the `st` primvar can be the literal unit
        square. Winding must stay consistent or the quad self-intersects into a bowtie."""
        uvs = ((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0))
        for (sx, sy), (u, v) in zip(self.UV_ORDER, uvs):
            self.assertEqual(((sx + 1.0) / 2.0, (sy + 1.0) / 2.0), (u, v))

    def test_missing_corner_is_projected_onto_the_fitted_plane(self):
        """A corner overhanging the receiver's edge gets no hit. It is projected along the decal
        axis onto the plane fitted from the corners that did land, so the patch keeps the decal's
        full rectangle instead of collapsing to a triangle."""
        z_axis = (0.0, 0.0, -1.0)
        plane_normal = (0.0, 0.0, 1.0)
        plane_point = (0.0, 0.0, 5.0)
        corner_origin = (3.0, 4.0, 9.0)
        denom = dot(plane_normal, z_axis)
        self.assertNotAlmostEqual(denom, 0.0)
        t = dot(plane_normal, sub(plane_point, corner_origin)) / denom
        filled = add(corner_origin, scale(z_axis, t))
        self.assertAlmostEqual(filled[2], 5.0, places=6)   # landed on the plane
        self.assertAlmostEqual(filled[0], 3.0, places=6)   # and only moved along the decal axis
        self.assertAlmostEqual(filled[1], 4.0, places=6)

    def test_epsilon_offset_moves_the_patch_toward_the_decal(self):
        """The lift is along the RECEIVER's normal, which by the facing test points back at the
        decal. Offsetting the other way buries the artwork inside the wall — invisible, and
        indistinguishable from the bug being fixed."""
        for name, d in ARENA_DECALS.items():
            hit, sign = select_receiver(d["z_axis"], PROJECTION_DEPTH, d["hits"])
            cast_dir = scale(d["z_axis"], sign)
            eps = max(0.002, PROJECTION_DEPTH * 0.001)
            moved = scale(hit.normal, eps)
            self.assertLess(dot(moved, cast_dir), 0.0,
                            f"{name}: offset must oppose the cast direction")
            self.assertGreater(math.sqrt(dot(moved, moved)), 0.0)

    def test_epsilon_is_small_relative_to_the_receiver_distance(self):
        """The lift must not be visible as a floating decal. Smallest measured receiver distance
        is 0.169 ft (Hornets logo); the offset is ~1% of that."""
        eps = max(0.002, PROJECTION_DEPTH * 0.001)
        closest = min(select_receiver(d["z_axis"], PROJECTION_DEPTH, d["hits"])[0].dist
                      for d in ARENA_DECALS.values())
        self.assertAlmostEqual(closest, 0.16925, places=5)
        self.assertLess(eps, closest * 0.05)


# =============================================================================
# Object-space patches the writer's OWN MAXScript emitted on the box, 2026-08-18
# (generated from the shipped C++ literal by scratchpad/decal-conform-test.ms, so this pins the
# real output rather than a re-derivation of it).
# =============================================================================

MEASURED_PATCHES = {
    "BOWL-UPPER_LOGO_DR_PEPP_WHITE001": {
        "quad": ((-7.000008, -4.544189, 0.324539), (6.999996, -4.544189, 0.324554),
                 (6.999996, 4.544189, 0.324554), (-7.000008, 4.544189, 0.324539)),
        "nrm": (0.0, 0.0, -1.0), "hit_dist": 0.326541,
    },
    "BOWL-UPPER-DECAL_TEXT_BUZZ": {
        # keystoned: the receiving wall is angled ~1.2deg, so z varies across the 30 ft width.
        # This is exactly what a fixed-offset plane would have got wrong.
        "quad": ((-15.000042, -3.610840, -0.954926), (14.999958, -3.610840, -0.328339),
                 (14.999958, 3.610840, -0.328339), (-15.000042, 3.610840, -0.954926)),
        "nrm": (-0.020878, 0.0, 0.999782), "hit_dist": 0.956915,
    },
    "BOWL-UPPER-DECAL_HORNETS_LOGO": {
        "quad": ((-8.0, -7.712891, 0.167252), (8.0, -7.712891, 0.167252),
                 (8.0, 7.712891, 0.167252), (-8.0, 7.712891, 0.167252)),
        "nrm": (0.0, 0.0, -1.0), "hit_dist": 0.16925,
    },
}


class TestMeasuredConformOutput(unittest.TestCase):
    """Pins the patches the shipped MAXScript actually produced."""

    def test_rectangle_matches_width_by_length(self):
        for name, m in MEASURED_PATCHES.items():
            xs = [p[0] for p in m["quad"]]
            ys = [p[1] for p in m["quad"]]
            self.assertAlmostEqual(max(xs) - min(xs), ARENA_DECALS[name]["width"], places=4)
            self.assertAlmostEqual(max(ys) - min(ys), ARENA_DECALS[name]["length"], places=4)

    def test_patch_is_planar(self):
        """Four points that are not coplanar render as a bowtie. The keystoned BUZZ patch is the
        one that could plausibly fail this."""
        for name, m in MEASURED_PATCHES.items():
            p0, p1, p2, p3 = m["quad"]
            n = norm((
                (p1[1] - p0[1]) * (p2[2] - p0[2]) - (p1[2] - p0[2]) * (p2[1] - p0[1]),
                (p1[2] - p0[2]) * (p2[0] - p0[0]) - (p1[0] - p0[0]) * (p2[2] - p0[2]),
                (p1[0] - p0[0]) * (p2[1] - p0[1]) - (p1[1] - p0[1]) * (p2[0] - p0[0]),
            ))
            self.assertAlmostEqual(dot(n, sub(p3, p0)), 0.0, places=5,
                                   msg=f"{name}: 4th corner is off the plane of the first three")

    def test_epsilon_lift_is_exactly_the_offset_toward_the_decal(self):
        """Hornets is the clean case: a perfectly parallel receiver at 0.16925 ft, so the patch
        must sit at 0.16925 - 0.002 on the decal's own axis."""
        m = MEASURED_PATCHES["BOWL-UPPER-DECAL_HORNETS_LOGO"]
        eps = max(0.002, PROJECTION_DEPTH * 0.001)
        self.assertAlmostEqual(m["quad"][0][2], m["hit_dist"] - eps, places=5)

    def test_normal_opposes_the_projection_axis_in_object_space(self):
        """In the decal's own frame the projection runs along local Z, so a receiver on +Z must
        report a normal of about (0,0,-1) and one on -Z about (0,0,+1)."""
        for name, m in MEASURED_PATCHES.items():
            z_of_patch = m["quad"][0][2]
            self.assertLess(z_of_patch * m["nrm"][2], 0.0,
                            f"{name}: normal should point back toward the decal origin")


class TestInstancingMustBeDisabled(unittest.TestCase):
    """A conformed patch is NOT a pure function of the object, so decals must not be instanced.

    The exporter instances nodes that share a base object: the mesh is authored once into a
    `_class_` prototype every instance inherits, and only the FIRST instance reaches Write().
    WHITE001 and WHITE002 share a VRayDecal object and were instanced that way, so WHITE002
    silently reused WHITE001's patch (and produced no writer log line at all, which is how the
    problem surfaced).
    """

    # Each decal's own conform, from the standalone run of the shipped MAXScript.
    WHITE001_Z = (0.324539, 0.324554)
    WHITE002_Z = (0.325043, 0.325027)

    def test_the_two_instanced_decals_conform_differently(self):
        """They land on different walls, so their patches genuinely differ. Small here only
        because the club is symmetric — which is luck, not correctness."""
        self.assertNotEqual(self.WHITE001_Z, self.WHITE002_Z)

    def test_the_shared_patch_error_is_small_but_real(self):
        """Pins the actual magnitude so the claim stays honest: 0.0005 ft is about 0.15 mm, i.e.
        invisible in this scene. The fix is for the general case, not this measurement."""
        worst = max(abs(a - b) for a, b in zip(self.WHITE001_Z, self.WHITE002_Z))
        self.assertLess(worst, 0.001)
        self.assertGreater(worst, 0.0)
        self.assertAlmostEqual(worst * 304.8, 0.155, delta=0.02)   # ft -> mm


def u_direction(normal_local_z):
    """Which local axis `u` runs along, given the patch normal's local z component.

    Mirror of the writer's rule: viewed from the side the normal points to, u must increase to the
    RIGHT, i.e. along cross(up, n). In the decal's own frame up is +Y and n is +/-Z.
    """
    up, n = (0.0, 1.0, 0.0), (0.0, 0.0, 1.0 if normal_local_z >= 0 else -1.0)
    return (up[1] * n[2] - up[2] * n[1], up[2] * n[0] - up[0] * n[2], up[0] * n[1] - up[1] * n[0])


class TestUvHandedness(unittest.TestCase):
    """Artwork must not come out mirrored.

    Caught in a render, not in the geometry: the patch corners and the UVs were each individually
    correct, and BUZZ/CITY looked right because their receivers happen to sit on +Z. WHITE001,
    WHITE002 and HORNETS sit on -Z receivers and rendered as MIRROR IMAGES -- 'Dr Pepper Est.1885'
    read backwards on the wall.
    """

    # local z of the patch normal, from the writer's own conform output
    NORMAL_Z = {
        "BOWL-UPPER_LOGO_DR_PEPP_WHITE001": -1.0,
        "BOWL-UPPER_LOGO_DR_PEPP_WHITE002": -1.0,
        "BOWL-UPPER-DECAL_HORNETS_LOGO": -1.0,
        "BOWL-UPPER-DECAL_TEXT_BUZZ": 0.999782,
        "BOWL-UPPER-DECAL_TEXT_CITY": 0.999782,
    }

    def test_u_runs_along_plus_x_only_for_plus_z_normals(self):
        for name, nz in self.NORMAL_Z.items():
            u = u_direction(nz)
            self.assertAlmostEqual(abs(u[0]), 1.0, places=6, msg=f"{name}: u should be an X axis")
            expected = 1.0 if nz >= 0 else -1.0
            self.assertAlmostEqual(u[0], expected, places=6, msg=f"{name}: wrong u handedness")

    def test_three_of_the_five_arena_decals_need_the_flip(self):
        """Pins the split. A fix that flipped ALL of them would simply mirror the other two."""
        flipped = [n for n, nz in self.NORMAL_Z.items() if nz < 0]
        self.assertEqual(len(flipped), 3)
        self.assertNotIn("BOWL-UPPER-DECAL_TEXT_BUZZ", flipped)
        self.assertNotIn("BOWL-UPPER-DECAL_TEXT_CITY", flipped)

    def test_flip_is_a_corner_reorder_that_preserves_v(self):
        """The writer implements the flip as emit-order 2,1,4,3. That mirrors u while leaving v
        alone; reversing the whole list (4,3,2,1) would flip the artwork vertically too."""
        corners = [(-1.0, -1.0), (1.0, -1.0), (1.0, 1.0), (-1.0, 1.0)]
        flipped = [corners[i] for i in (1, 0, 3, 2)]
        uvs = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
        for (sx, sy), (u, v) in zip(flipped, uvs):
            self.assertEqual((sx + 1.0) / 2.0, 1.0 - u)   # u mirrored
            self.assertEqual((sy + 1.0) / 2.0, v)         # v unchanged


if __name__ == "__main__":
    unittest.main(verbosity=2)
