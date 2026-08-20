# Copyright 2026 Miris Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
"""
MAX-LIT-GEOLIGHT-022 — geometry lights must carry their emitter's own intensity, and every V-Ray
unit conversion must go through ONE converter.

Background
----------
`MaxUsdMeshWriter` marks a mesh as a UsdLux geometry light when its material tree contains a
`VRayLightMtl`, so Karma importance-samples the emissive faces as real lights instead of relying on
brute-force emissive-surface hits. It applied `UsdLuxMeshLightAPI` and stopped there, on the
assumption that the renderer would derive emission from the bound material.

It does not, and cannot, in the common arch-viz case: the emissive faces are a Multi/Sub-Object
SUBSET, so the light prim has no directly bound material. Measured on the Spectrum Center arena:

    240 prims carry MeshLightAPI
    `inputs:intensity` authored on ZERO of them   -> schema default 1.0
    230 of 240 have no directly bound material
    Karma logged one `__geolight_emission` value for the entire render

The affected fixtures are the ones that light the seating bowl: 221 recessed ceiling hexagons and
12 `BOWL_LIGHT_BANK` units at Y 107.8 ft. With them contributing ~nothing, indirect light measured
~3x too weak relative to direct light against a V-Ray reference of the same camera (court 1.8x
brighter than the reference while roof soffits were 0.55-0.65x).

A second, related defect: the units conversion existed TWICE — once in `VRayLightWriter` for light
objects, once in `LastResortMtlxShaderWriter` for light materials — each with a comment instructing
the reader to keep the two in sync. They drifted, which is how a geometry light could emit at 1.0
while its own emissive surface carried 190.

Fix
---
1. `VRayUnits.h` holds one `UnitsToNits(multiplier, units, hasUnits)`; both writers call it.
2. `MeshWriter` finds the `VRayLightMtl` in the tree, probes its colour + multiplier via MAXScript
   (V-Ray's SDK cannot be linked, and property sets differ per build), and authors
   `inputs:intensity` + `inputs:color` on the light.
3. The geometry light uses the **units=0 gain**, matching the emissive-surface path, so a light and
   the surface it represents cannot disagree.

Test strategy
-------------
The C++ needs Windows + the Max SDK, so this mirrors the converter and the intensity decision in
plain Python and drives them with the REAL multipliers read off the arena scene
(`C:\\suts\\export_log.txt` VRayLightMtl probe, 2026-08-19).

Run: `hython test_miris_max_lit_geolight_022.py`
"""
import unittest

PI = 3.14159265358979323846
PHOTOPIC_K = 683.0
CEILING = 10000.0
UNITS0_GAIN = 3.8


def units_to_nits(multiplier, units, has_units, compensate_exposure=False,
                  ceiling=CEILING):
    """Mirror of MaxUsdVRay::UnitsToNits in src/translators/VRayUnits.h."""
    base = multiplier
    if has_units:
        if units == 0:
            base = multiplier if compensate_exposure else multiplier * UNITS0_GAIN
        elif units == 1:
            base = multiplier / PI
        elif units == 2:
            base = multiplier
        elif units == 3:
            base = (multiplier * PHOTOPIC_K) / PI
        elif units == 4:
            base = multiplier * PHOTOPIC_K
    return max(0.0, min(ceiling, base))


# The 16 distinct VRayLightMtl materials in the arena, as probed on the box.
# name -> (multiplier, (r,g,b) 0-255)
ARENA_LIGHT_MTLS = {
    "LENS-ON_WARM":              (50.0,  (225, 202, 181)),
    "LENS-OVERRIDE":             (2.0,   (200, 125, 59)),
    "VOM-SOUTH_GRAPIC_ILUM":     (1.25,  (0, 0, 0)),
    "VOM-GRAPHIC_EAST_ILUM":     (1.5,   (0, 0, 0)),
    "GRAPHIC-ILUM_WHITE":        (1.5,   (210, 210, 210)),
    "SCOREBOARD-VIDEO":          (1.0,   (0, 0, 0)),
    "TOP-PANEL":                 (2.0,   (0, 0, 0)),
    "BOTTOM-PANEL":              (1.2,   (0, 0, 0)),
    "BASKETBALL-SHOTCLOCK_ILUM": (14.0,  (65, 0, 0)),
    "SPOTLIGHT-LENS":            (75.0,  (240, 240, 240)),
    "SPOTLIGHT-LENS_OVERIDE":    (2.0,   (240, 240, 240)),
    "GLASS-ILUM":                (50.0,  (0, 0, 0)),
    "GLASS-ILUM_OVERRIDE":       (2.0,   (0, 0, 0)),
    "WIRE-GLOW":                 (150.0, (200, 87, 43)),
    "WIRE-GLOW_OVERRIDE":        (2.0,   (180, 89, 39)),
    "TV-SCREEN":                 (1.0,   (0, 0, 0)),
}

# Values the exporter's emissive-SURFACE path already writes, read out of the exported USD.
# These pin the conversion the geometry light must match.
OBSERVED_SURFACE_EMISSION = {
    "GLASS-ILUM": 190.0,          # mult 50
    "LENS-ON_WARM": 190.0,        # mult 50
    "WIRE-GLOW": 570.0,           # mult 150
    "BASKETBALL-SHOTCLOCK_ILUM": 53.2,   # mult 14
    "GRAPHIC-ILUM_WHITE": 5.7,    # mult 1.5
    "VOM-GRAPHIC_EAST_ILUM": 5.7, # mult 1.5
    "BOTTOM-PANEL": 4.56,         # mult 1.2
    "TOP-PANEL": 7.6,             # mult 2.0
    "SCOREBOARD-VIDEO": 3.8,      # mult 1.0
}


class TestConverter(unittest.TestCase):
    def test_units_table(self):
        self.assertAlmostEqual(units_to_nits(10, 0, True), 38.0, places=6)
        self.assertAlmostEqual(units_to_nits(10, 1, True), 10 / PI, places=6)
        self.assertAlmostEqual(units_to_nits(10, 2, True), 10.0, places=6)
        self.assertAlmostEqual(units_to_nits(10, 3, True), 6830 / PI, places=6)
        self.assertAlmostEqual(units_to_nits(10, 4, True), 6830.0, places=6)

    def test_no_units_passes_through(self):
        """Older V-Ray with no `.units` keeps pre-013 behaviour."""
        self.assertAlmostEqual(units_to_nits(7.5, 0, False), 7.5, places=6)

    def test_negative_clamps_and_ceiling_applies(self):
        self.assertEqual(units_to_nits(-5, 0, True), 0.0)
        self.assertEqual(units_to_nits(1e9, 4, True), CEILING)

    def test_single_implementation_invariant(self):
        """The whole point of VRayUnits.h. A light object, a light material's surface, and a
        geometry light given the same multiplier and units must produce the SAME number — the
        duplicated copies previously drifted."""
        for mult in (1.0, 1.5, 14.0, 50.0, 150.0):
            a = units_to_nits(mult, 0, True)   # light object path
            b = units_to_nits(mult, 0, True)   # emissive surface path
            c = units_to_nits(mult, 0, True)   # geometry light path
            self.assertEqual(a, b)
            self.assertEqual(b, c)


class TestGeometryLightIntensity(unittest.TestCase):
    def test_matches_the_surface_values_the_exporter_already_writes(self):
        """The load-bearing assertion: the converter must reproduce the emissive-surface values the
        exporter writes, so a light and its own surface cannot disagree.

        HISTORICAL NOTE (MAX-LIT-COMPENSATE-023): OBSERVED_SURFACE_EMISSION was captured from the
        2026-08-19 export, BEFORE compensateExposure was honoured — every one of those materials has
        compensateExposure=true, so the values in a current export are these divided by
        kUnits0Gain (GLASS_ILUM 190 -> 50, SCOREBOARD_VIDEO 3.8 -> 1.0, verified in the export).
        The assertion is therefore pinning the UNCOMPENSATED conversion, which is still exactly
        right for an emitter that does not compensate. Do not "update" these numbers to the new
        export values without also passing compensate_exposure=True below, or the test will silently
        stop checking the gain at all."""
        for name, expected in OBSERVED_SURFACE_EMISSION.items():
            mult = ARENA_LIGHT_MTLS[name][0]
            self.assertAlmostEqual(units_to_nits(mult, 0, True, False), expected, places=2,
                                   msg=f"{name}: mult {mult} should convert to {expected}")

    def test_passthrough_would_underlight_by_the_gain(self):
        """Guards the mistake this fix nearly shipped with. VRayLightMtl exposes no `units`, so
        hasUnits=False is a tempting reading — but it makes the light 3.8x dimmer than its own
        surface."""
        for name in ("LENS-ON_WARM", "WIRE-GLOW", "GRAPHIC-ILUM_WHITE"):
            mult = ARENA_LIGHT_MTLS[name][0]
            wrong = units_to_nits(mult, 0, False)
            right = units_to_nits(mult, 0, True)
            self.assertAlmostEqual(right / wrong, UNITS0_GAIN, places=6)

    def test_the_true_range_is_wide_so_a_uniform_value_cannot_work(self):
        """Why per-emitter values are required rather than one calibrated number. A uniform stand-in
        was measured to balance the average while widening the per-region spread from 1.25x to
        4.21x."""
        vals = [units_to_nits(m, 0, True) for m, _c in ARENA_LIGHT_MTLS.values()]
        self.assertGreater(max(vals) / min(vals), 100.0)
        self.assertAlmostEqual(max(vals), 570.0, places=2)   # WIRE-GLOW
        self.assertAlmostEqual(min(vals), 3.8, places=2)     # SCOREBOARD-VIDEO / TV-SCREEN

    def test_colour_is_normalised_from_max_0_255(self):
        """UsdLux inputs:color is 0-1; Max colours are 0-255. Forgetting this makes every light
        255x too saturated rather than obviously broken."""
        r, g, b = ARENA_LIGHT_MTLS["LENS-ON_WARM"][1]
        self.assertAlmostEqual(r / 255.0, 0.882, places=3)
        self.assertAlmostEqual(g / 255.0, 0.792, places=3)
        self.assertAlmostEqual(b / 255.0, 0.710, places=3)

    def test_black_colour_emitters_are_texture_driven(self):
        """EIGHT of the sixteen materials have colour (0,0,0) — they are texture-driven displays,
        their brightness coming from a map rather than the colour swatch. MeshWriter deliberately
        routes TEXTURED VRayLightMtl to the emission surface shader instead of a geometry light,
        because a geometry light samples one averaged colour and would render the videoboard as a
        flat white panel."""
        black = [n for n, (_m, c) in ARENA_LIGHT_MTLS.items() if c == (0, 0, 0)]
        self.assertIn("SCOREBOARD-VIDEO", black)
        self.assertIn("TV-SCREEN", black)
        self.assertEqual(len(black), 8)

    def test_glass_ilum_is_textured_so_it_never_becomes_a_geometry_light(self):
        """Consequence worth pinning, found while correcting the count above.

        GLASS-ILUM is the suite glazing — multiplier 50, i.e. the joint-highest emitter in the
        scene alongside LENS-ON_WARM — but its colour is (0,0,0) and it is driven by a Gradient
        texmap. The textured-emitter guard therefore excludes it from becoming a geometry light, so
        it only ever contributes as an emissive surface, which Karma barely importance-samples.

        That means MAX-LIT-GEOLIGHT-022 does NOT fix the suite/skybox glass. Raising the recessed
        cans and bowl banks will not light the suites, and anyone reading a suite render as evidence
        for or against this fix would be misreading it. The textured-emitter case needs its own
        treatment (a light whose colour comes from the map's average, or a rect light fitted to the
        glazing) and is deliberately out of scope here."""
        mult, colour = ARENA_LIGHT_MTLS["GLASS-ILUM"]
        self.assertEqual(mult, 50.0)
        self.assertEqual(colour, (0, 0, 0))
        self.assertAlmostEqual(units_to_nits(mult, 0, True), 190.0, places=2)


# ---------------------------------------------------------------------------------------------
# MAX-LIT-COMPENSATE-023 — compensateExposure, and the light/surface agreement it broke.
# ---------------------------------------------------------------------------------------------
SURFACE_CEILING = 1000.0   # kEmissionCeiling: an emission WEIGHT, not a nit intensity


class CompensateExposure(unittest.TestCase):
    """`compensateExposure` on a VRayLightMtl means the material already accounts for camera
    exposure. kUnits0Gain exists to stand in for exactly that brightening, so applying both
    double-counts it.

    Measured on the arena against the V-Ray baseline, before the fix:
        jumbotron  (SCOREBOARD_VIDEO, mult 1.0, compensate=true)  3.2x too bright
        suite glass (GLASS_ILUM,      mult 50,  compensate=true)  5.16x too bright
    against a gain of 3.8 -- the gain applied where it should not have been.
    """

    def test_compensated_units0_skips_the_gain(self):
        self.assertEqual(units_to_nits(1.0, 0, True, True), 1.0)
        self.assertEqual(units_to_nits(50.0, 0, True, True), 50.0)

    def test_uncompensated_units0_still_applies_the_gain(self):
        # the pre-existing behaviour must be untouched for emitters that do NOT compensate
        self.assertAlmostEqual(units_to_nits(1.0, 0, True, False), 3.8)
        self.assertAlmostEqual(units_to_nits(50.0, 0, True, False), 190.0)

    def test_the_correction_is_exactly_the_gain(self):
        for mult in (1.0, 1.5, 2.0, 50.0, 150.0):
            self.assertAlmostEqual(
                units_to_nits(mult, 0, True, False) / units_to_nits(mult, 0, True, True),
                UNITS0_GAIN, places=5,
                msg="compensateExposure must divide out precisely kUnits0Gain and nothing else")

    def test_compensate_does_not_touch_physical_units(self):
        """For lumens/watts/radiance the multiplier is an absolute physical quantity; exposure is
        the camera's business, not the value's. Gating to units=0 is deliberate."""
        for units in (1, 2, 3, 4):
            self.assertEqual(units_to_nits(100.0, units, True, True),
                             units_to_nits(100.0, units, True, False),
                             msg="units=%d must be unaffected by compensateExposure" % units)

    def test_default_branch_is_unreachable_from_the_surface_probe(self):
        """Splitting `case 0: default:` into two branches changed behaviour for out-of-range units:
        it used to get the gain, now it passes through. That is inert ONLY because the probe clamps
        (`probe.units = (u >= 0 && u <= 4) ? u : 0`). If that clamp is ever removed, every emissive
        surface silently loses the 3.8x gain -- so pin the clamp here, not just the converter."""
        def probe_clamp(u):
            return u if 0 <= u <= 4 else 0
        for raw in (-1, 5, 99, 0x7fffffff):
            self.assertEqual(probe_clamp(raw), 0)
            self.assertAlmostEqual(units_to_nits(2.0, probe_clamp(raw), True, False), 7.6)

    def test_light_and_its_own_surface_now_agree(self):
        """The regression that motivated the shared converter: MeshWriter read compensateExposure,
        logged it, and passed it nowhere -- so one VRayLightMtl produced a geometry-light intensity
        and an emissive-surface weight 3.8x apart. Verified in the export: GLASS_ILUM surface
        emission was 50 while 223 meshlight prims carried intensity 190."""
        for mult, comp in ((1.0, True), (50.0, True), (150.0, True), (5.7, False), (2.0, False)):
            surface = units_to_nits(mult, 0, True, comp, SURFACE_CEILING)
            light = units_to_nits(mult, 0, True, comp, CEILING)
            self.assertEqual(surface, light,
                             msg="mult=%s compensate=%s: surface %s != light %s"
                                 % (mult, comp, surface, light))

    def test_the_two_ceilings_are_the_only_permitted_difference(self):
        """An emission weight and a light intensity clamp on different scales, so the ceiling is a
        parameter. Below both ceilings the two paths must be bit-identical; above the lower one they
        may legitimately diverge."""
        self.assertEqual(units_to_nits(1e6, 4, True, False, SURFACE_CEILING), SURFACE_CEILING)
        self.assertEqual(units_to_nits(1e6, 4, True, False, CEILING), CEILING)
        # and the arena's real values are all comfortably below the lower ceiling
        for mult in (1.0, 1.5, 2.0, 5.7, 50.0, 150.0):
            self.assertLess(units_to_nits(mult, 0, True, False, SURFACE_CEILING), SURFACE_CEILING)


if __name__ == "__main__":
    unittest.main(verbosity=2)
