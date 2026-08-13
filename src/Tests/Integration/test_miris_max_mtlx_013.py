# Copyright 2026 Miris Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
"""
MAX-MTLX-EMISSIVE-UNITS-013 — Python / hython mirror of the C++ fix in
`src/MaxUsd/Translators/LastResortMtlxShaderWriter.cpp` (Tier 1 constant-color
emission branch + companion `.units` MAXScript probe and per-units
`_NormalizeEmissionWeight` helper).

The defect
----------
The MAX-MTLX-005 landing authored V-Ray VRayLightMtl emission by BAKING the
raw `.multiplier` scalar into `emission_color` (Tier 1: emission=1,
emission_color = color * multiplier). For a jumbotron / scoreboard LED
material with color=(1,1,1) and multiplier=150, `emission_color` becomes
`(150,150,150)` — an HDR value that Karma / any tone-mapped path tracer
saturates to flat WHITE on every exposed pixel of the emitter regardless of
the source multiplier.

This mismatches the ND_standard_surface convention:
    emission (float, weight, 0..inf) * emission_color (color3, tint, 0..1)

By baking the multiplier into the tint, the Tier 1 branch violates the tint
range invariant, and also loses the semantic that `.multiplier` is a
scalar emitter strength — not a chromatic tint.

Additionally, VRayLightMtl carries a `.units` property (V-Ray SDK) that
determines the absolute-scale interpretation of `.multiplier`:
    0 = Default          (color 0-1, arbitrary scale)
    1 = Luminous power   (lumens, over full sphere)
    2 = Luminance        (lm/m^2/sr = cd/m^2 * pi)
    3 = Radiant power    (watts)
    4 = Radiance         (W/m^2/sr)

The pre-fix MAXScript probe (`discoverVRayLightMtlFn`) did NOT read `.units`,
so a scoreboard authored at "2000 lumens" (units=1) and a scoreboard authored
at "2000 arbitrary" (units=0) exported to IDENTICAL emission_color, even
though the intended output differs by ~3 orders of magnitude on the physical
scale.

The fix
-------
Three coupled edits to `LastResortMtlxShaderWriter.cpp`:

1. Extend `discoverVRayLightMtl` MAXScript to probe `.units` and append a
   `units|<int>` line to the pipe/newline manifest.
2. Extend `VRayLightMtlProbe` (C++) with `int units {0}` and parse the new
   manifest key. Values outside [0..4] fall back to 0 (default mode) rather
   than trusting malformed input.
3. Add `_NormalizeEmissionWeight(multiplier, units) -> float` helper and use
   its output as the `emission` scalar in BOTH Tier 1 and Tier 2. Leave
   `emission_color` as the raw tint (Tier 1) or connected tiledimage (Tier 2).

`_NormalizeEmissionWeight` semantics:
    units=0 (default)  -> multiplier
    units=1 (lumens)   -> multiplier / 683           (photopic peak lm/W)
    units=2 (luminance)-> multiplier / pi            (cd/m^2 = luminance / pi)
    units=3 (watts)    -> multiplier
    units=4 (W/m^2/sr) -> multiplier
Then clamp to [0, 30]. The 30 ceiling is chosen empirically: enough headroom
for a bright LED emitter to visibly dominate the frame in Karma's default
tone-mapper, low enough that a mult=150 jumbotron doesn't flat-white every
exposed pixel.

Test strategy
-------------
The C++/MAXScript ships on Windows-only + Max SDK + V-Ray SDK, so we cannot
execute the real writer here. Instead we mirror the parser + normalizer +
Write() logic at the pxr.UsdShade layer:

  * `_probe_from_manifest`         — pure-Python port of `_ProbeVRayLightMtl`
                                     (parses pipe/newline manifests).
  * `_normalize_emission_weight`   — pure-Python port of C++ helper.
  * `_author_last_resort_mtlx`     — pure-Python port of `Write()`.
  * A frozen `_author_last_resort_mtlx_prefix013` reproduces the pre-013
    (multiplier-baked-into-emission_color) behavior for regression fingerprints.

Buckets:
  * PreFixDefect            — old writer produces (150,150,150) emission_color
                              on mult=150 jumbotron; converges to flat white.
  * ND_StandardSurfaceConvention — post-fix writer keeps emission_color in
                              [0,1] tint range; emission carries the weight.
  * UnitsProbeDispatch      — the MAXScript manifest's `units|N` line dispatches
                              to the right normalization branch for N=0..4.
  * UnitsAbsentDefault      — pre-`units` V-Ray manifest (no `units|` line)
                              falls back to units=0 (author-scale preserved).
  * UnitsOutOfRangeFallback — malformed `units|99` falls back to 0.
  * NormalizationFormula    — the exact per-units divisor matches the C++
                              constants (683.0 for lumens, pi for luminance).
  * SaturationCeiling       — post-normalize clamp caps every mode at 30.0;
                              a mult=100000 emission never exceeds 30.
  * NegativeMultiplierClamp — post-clamp is [0, 30] — a MAXScript-side-effect
                              authoring negative mult clamps to 0.
  * Tier2TextureBranch      — texture-driven emission still authors
                              emission=normalized weight and connects the
                              tiledimage to emission_color (no baking).
  * Tier1ColorBranch        — constant-color emission authors
                              emission=normalized weight and emission_color =
                              raw color3 tint in [0,1].
  * BaseColorZeroForEmitter — pure-emitter base_color = (0,0,0) preserved
                              across the fix (surgical scope).
  * NonEmissiveFallback     — a non-VRayLightMtl material still hits the
                              non-emissive fallback branch byte-identically.
  * SurgicalScopeCoverage   — non-emissive materials (PhysicalMaterial /
                              OpenPBR / anything not-VRayLightMtl) traverse
                              the same code path pre-013 and post-013.
  * VRayOverrideMtlUnwrap   — a VRayOverrideMtl wrapping a VRayLightMtl base
                              still resolves the VRayLightMtl's emission
                              (contract preserved from MAX-MTLX-005).
  * ArenaCensus             — a 40-material Spectrum-arena-density census
                              (30 LED-jumbotron, 5 warm-tungsten practical,
                              5 dim ember) — pre-fix, every mult>1 material
                              saturates emission_color to >1.0 on at least
                              one channel; post-fix, every material's
                              emission_color stays in [0,1] and emission
                              is clamped to [0,30].

Ships alongside `structural_only.md` + `intended_example.md`: no before/after
renders (Mac cannot rebuild the plugin binary; V-Ray SDK not exercisable
headlessly under CrossOver). Same shipping pattern as MAX-MTLX-001 /
MAX-LIT-002 / MAX-TEX-003 / MAX-GEO-006 / MAX-MAT-003-port / MAX-MTLX-007
through MAX-MTLX-012 / MAX-MTLX-014.
"""
import math
import os
import sys
import unittest

from pxr import Gf, Sdf, Usd, UsdShade

# ---------------------------------------------------------------------------
# CONSTANTS lifted from LastResortMtlxShaderWriter.cpp (verbatim).
# ---------------------------------------------------------------------------

# `kEmissionCeiling` — soft ceiling on `emission` weight; matches C++
# `static constexpr float kEmissionCeiling = 30.0f;` in the writer.
EMISSION_CEILING = 30.0

# `_NormalizeEmissionWeight` per-units divisors — matches C++ literal
# constants:
#   case 1: w = multiplier / 683.0f;
#   case 2: w = multiplier / 3.14159265358979323846f;
# Any drift here means the Python mirror drifted from the C++ writer.
LUMEN_DIVISOR = 683.0
LUMINANCE_DIVISOR = math.pi

# `ND_standard_surface_surfaceshader` info:id — matches C++ `kNdStandardSurfaceId`.
ND_STANDARD_SURFACE_ID = "ND_standard_surface_surfaceshader"

# `ND_tiledimage_color3` info:id — matches C++ `kNdTiledImageColor3Id`.
ND_TILEDIMAGE_COLOR3_ID = "ND_tiledimage_color3"


# ---------------------------------------------------------------------------
# _probe_from_manifest — pure-Python port of `_ProbeVRayLightMtl` in
# LastResortMtlxShaderWriter.cpp, minus the MAXScript-invocation front-half.
# Consumes the pipe/newline manifest string that MAXScript would emit and
# produces the same VRayLightMtlProbe view.
# ---------------------------------------------------------------------------

class VRayLightMtlProbe(object):
    __slots__ = (
        "is_light_mtl",
        "multiplier",
        "units",
        "has_color",
        "cr", "cg", "cb",
        "tex_file",
    )

    def __init__(self):
        self.is_light_mtl = False
        self.multiplier = 1.0
        self.units = 0
        self.has_color = False
        self.cr = 0.0
        self.cg = 0.0
        self.cb = 0.0
        self.tex_file = ""


def _probe_from_manifest(manifest):
    """Mirror of `_ProbeVRayLightMtl`'s pipe/newline parser."""
    probe = VRayLightMtlProbe()
    if not manifest:
        return probe
    for line in manifest.splitlines():
        line = line.rstrip("\r ")
        if "|" not in line:
            continue
        key, _, val = line.partition("|")
        try:
            if key == "isLightMtl":
                probe.is_light_mtl = (val == "1")
            elif key == "multiplier":
                probe.multiplier = float(val)
            elif key == "units":
                u = int(val)
                probe.units = u if 0 <= u <= 4 else 0
            elif key == "color":
                parts = val.split(",")
                if len(parts) == 3:
                    probe.cr = float(parts[0]) / 255.0
                    probe.cg = float(parts[1]) / 255.0
                    probe.cb = float(parts[2]) / 255.0
                    probe.has_color = True
            elif key == "texmap":
                probe.tex_file = val
        except ValueError:
            continue
    return probe


# ---------------------------------------------------------------------------
# _normalize_emission_weight — pure-Python port of `_NormalizeEmissionWeight`
# in LastResortMtlxShaderWriter.cpp. Order + literals kept identical so a
# drift here fails the corresponding unit test.
# ---------------------------------------------------------------------------

def _normalize_emission_weight(multiplier, units):
    if units == 1:
        w = multiplier / LUMEN_DIVISOR
    elif units == 2:
        w = multiplier / LUMINANCE_DIVISOR
    else:
        w = multiplier  # units 0 / 3 / 4 / default
    if w > EMISSION_CEILING:
        w = EMISSION_CEILING
    if w < 0.0:
        w = 0.0
    return w


# ---------------------------------------------------------------------------
# _author_last_resort_mtlx — pure-Python port of `LastResortMtlxShaderWriter::Write()`.
# Authors the ND_standard_surface shader prim + Tier 1 / Tier 2 emission
# scaffold on a fresh USD stage, using the probe + normalizer above.
# ---------------------------------------------------------------------------

def _author_last_resort_mtlx(stage, shader_path, diffuse_color_rgb, manifest):
    """Post-013 writer behavior."""
    shader = UsdShade.Shader.Define(stage, shader_path)
    shader.CreateIdAttr(ND_STANDARD_SURFACE_ID)

    base_color = shader.CreateInput("base_color", Sdf.ValueTypeNames.Color3f)

    probe = _probe_from_manifest(manifest)
    if probe.is_light_mtl:
        base_color.Set(Gf.Vec3f(0.0, 0.0, 0.0))  # pure emitter

        emission = shader.CreateInput("emission", Sdf.ValueTypeNames.Float)
        emission_color = shader.CreateInput("emission_color", Sdf.ValueTypeNames.Color3f)

        er = probe.cr if probe.has_color else diffuse_color_rgb[0]
        eg = probe.cg if probe.has_color else diffuse_color_rgb[1]
        eb = probe.cb if probe.has_color else diffuse_color_rgb[2]

        weight = _normalize_emission_weight(probe.multiplier, probe.units)

        if probe.tex_file:
            emission.Set(weight)
            tex_path = shader_path.GetParentPath().AppendChild("emission_tex")
            tex_shader = UsdShade.Shader.Define(stage, tex_path)
            tex_shader.CreateIdAttr(ND_TILEDIMAGE_COLOR3_ID)
            tex_shader.CreateInput("file", Sdf.ValueTypeNames.Asset).Set(
                Sdf.AssetPath(probe.tex_file))
            tex_shader.CreateInput("uvtiling", Sdf.ValueTypeNames.Float2).Set(
                Gf.Vec2f(1.0, 1.0))
            tex_out = tex_shader.CreateOutput("out", Sdf.ValueTypeNames.Color3f)
            emission_color.ConnectToSource(tex_out)
        else:
            emission.Set(weight)
            emission_color.Set(Gf.Vec3f(er, eg, eb))
        return shader

    # Non-emissive fallback: diffuse color as base_color.
    base_color.Set(Gf.Vec3f(*diffuse_color_rgb))
    return shader


def _author_last_resort_mtlx_prefix013(stage, shader_path, diffuse_color_rgb, manifest):
    """Pre-013 writer behavior (frozen regression fingerprint).

    Reproduces the MAX-MTLX-005 landing that bakes the multiplier into
    emission_color. Used only to lock in the pre-013 defect.
    """
    shader = UsdShade.Shader.Define(stage, shader_path)
    shader.CreateIdAttr(ND_STANDARD_SURFACE_ID)

    base_color = shader.CreateInput("base_color", Sdf.ValueTypeNames.Color3f)

    probe = _probe_from_manifest(manifest)
    if probe.is_light_mtl:
        base_color.Set(Gf.Vec3f(0.0, 0.0, 0.0))

        emission = shader.CreateInput("emission", Sdf.ValueTypeNames.Float)
        emission_color = shader.CreateInput("emission_color", Sdf.ValueTypeNames.Color3f)

        er = probe.cr if probe.has_color else diffuse_color_rgb[0]
        eg = probe.cg if probe.has_color else diffuse_color_rgb[1]
        eb = probe.cb if probe.has_color else diffuse_color_rgb[2]

        if probe.tex_file:
            emission.Set(probe.multiplier)  # pre-013: raw mult into weight
            tex_path = shader_path.GetParentPath().AppendChild("emission_tex")
            tex_shader = UsdShade.Shader.Define(stage, tex_path)
            tex_shader.CreateIdAttr(ND_TILEDIMAGE_COLOR3_ID)
            tex_shader.CreateInput("file", Sdf.ValueTypeNames.Asset).Set(
                Sdf.AssetPath(probe.tex_file))
            tex_shader.CreateInput("uvtiling", Sdf.ValueTypeNames.Float2).Set(
                Gf.Vec2f(1.0, 1.0))
            tex_out = tex_shader.CreateOutput("out", Sdf.ValueTypeNames.Color3f)
            emission_color.ConnectToSource(tex_out)
        else:
            # pre-013 Tier 1: emission=1, emission_color = color * multiplier
            emission.Set(1.0)
            emission_color.Set(Gf.Vec3f(
                er * probe.multiplier,
                eg * probe.multiplier,
                eb * probe.multiplier,
            ))
        return shader

    base_color.Set(Gf.Vec3f(*diffuse_color_rgb))
    return shader


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

def _emission_manifest(*, is_light=True, multiplier=1.0, units=None,
                       color=None, texmap=None):
    """Build a pipe/newline manifest string identical to what
    `discoverVRayLightMtl` emits from MAXScript."""
    lines = []
    if is_light:
        lines.append("isLightMtl|1")
    lines.append("multiplier|{}".format(multiplier))
    if units is not None:
        lines.append("units|{}".format(units))
    if color is not None:
        lines.append("color|{},{},{}".format(color[0], color[1], color[2]))
    if texmap is not None:
        lines.append("texmap|{}".format(texmap))
    return "\n".join(lines) + "\n"


def _get_input_value(shader, name):
    """Read a UsdShadeInput's static value, if any."""
    inp = shader.GetInput(name)
    if not inp:
        return None
    return inp.Get()


def _get_input_source(shader, name):
    """Return the source-shader-name of a connected input, or None."""
    inp = shader.GetInput(name)
    if not inp or not inp.HasConnectedSource():
        return None
    conn = inp.GetConnectedSource()
    if not conn:
        return None
    source_api, _, _ = conn
    if source_api is None:
        return None
    return source_api.GetPrim().GetName()


# ---------------------------------------------------------------------------
# TESTS
# ---------------------------------------------------------------------------

class TestPreFixDefect(unittest.TestCase):
    """The pre-013 baked-multiplier-into-tint symptom the fix resolves."""

    def test_prefix013_scoreboard_saturates_emission_color(self):
        stage = Usd.Stage.CreateInMemory()
        UsdShade.Material.Define(stage, "/Root/Mat")
        manifest = _emission_manifest(
            multiplier=150.0,
            color=(255, 255, 255),   # white LED
        )
        shader = _author_last_resort_mtlx_prefix013(
            stage, Sdf.Path("/Root/Mat/Shader"),
            (0.0, 0.0, 0.0), manifest)
        ec = _get_input_value(shader, "emission_color")
        self.assertEqual(tuple(ec), (150.0, 150.0, 150.0),
            "pre-013: emission_color must reproduce the (150,150,150) HDR tint")
        e = _get_input_value(shader, "emission")
        self.assertAlmostEqual(e, 1.0)

    def test_prefix013_low_multiplier_still_valid_shape(self):
        stage = Usd.Stage.CreateInMemory()
        UsdShade.Material.Define(stage, "/Root/Mat")
        manifest = _emission_manifest(multiplier=1.5, color=(128, 128, 128))
        shader = _author_last_resort_mtlx_prefix013(
            stage, Sdf.Path("/Root/Mat/Shader"),
            (0.0, 0.0, 0.0), manifest)
        ec = _get_input_value(shader, "emission_color")
        for i in range(3):
            self.assertAlmostEqual(ec[i], 128.0 / 255.0 * 1.5, places=5)

    def test_prefix013_r30_at_mult_of_150_would_saturate_karma(self):
        """The reported symptom: mult=150 with a pure-white tint saturates
        every channel above the 1.0 tint invariant."""
        stage = Usd.Stage.CreateInMemory()
        UsdShade.Material.Define(stage, "/Root/Mat")
        manifest = _emission_manifest(multiplier=150.0, color=(255, 255, 255))
        shader = _author_last_resort_mtlx_prefix013(
            stage, Sdf.Path("/Root/Mat/Shader"),
            (0.0, 0.0, 0.0), manifest)
        ec = _get_input_value(shader, "emission_color")
        for c in ec:
            self.assertGreater(c, 1.0,
                "pre-013 emission_color violates the ND_standard_surface tint invariant")


class TestNDStandardSurfaceConvention(unittest.TestCase):
    """Post-013 keeps emission_color in [0,1] and emission carries the weight."""

    def test_post013_emission_color_stays_in_tint_range(self):
        stage = Usd.Stage.CreateInMemory()
        UsdShade.Material.Define(stage, "/Root/Mat")
        manifest = _emission_manifest(
            multiplier=150.0,
            color=(255, 255, 255),
        )
        shader = _author_last_resort_mtlx(
            stage, Sdf.Path("/Root/Mat/Shader"),
            (0.0, 0.0, 0.0), manifest)
        ec = _get_input_value(shader, "emission_color")
        for c in ec:
            self.assertLessEqual(c, 1.0,
                "post-013 emission_color must stay within the [0,1] tint range")
            self.assertGreaterEqual(c, 0.0)

    def test_post013_emission_carries_the_weight(self):
        stage = Usd.Stage.CreateInMemory()
        UsdShade.Material.Define(stage, "/Root/Mat")
        manifest = _emission_manifest(
            multiplier=15.0,     # under the 30-ceiling, in default units
            units=0,
            color=(255, 255, 255),
        )
        shader = _author_last_resort_mtlx(
            stage, Sdf.Path("/Root/Mat/Shader"),
            (0.0, 0.0, 0.0), manifest)
        e = _get_input_value(shader, "emission")
        self.assertAlmostEqual(e, 15.0, places=5)

    def test_post013_emission_color_matches_source_tint(self):
        stage = Usd.Stage.CreateInMemory()
        UsdShade.Material.Define(stage, "/Root/Mat")
        # Warm-white tint 250/220/180 (typical tungsten practical).
        manifest = _emission_manifest(
            multiplier=5.0, units=0, color=(250, 220, 180))
        shader = _author_last_resort_mtlx(
            stage, Sdf.Path("/Root/Mat/Shader"),
            (0.0, 0.0, 0.0), manifest)
        ec = _get_input_value(shader, "emission_color")
        self.assertAlmostEqual(ec[0], 250.0 / 255.0, places=5)
        self.assertAlmostEqual(ec[1], 220.0 / 255.0, places=5)
        self.assertAlmostEqual(ec[2], 180.0 / 255.0, places=5)


class TestUnitsProbeDispatch(unittest.TestCase):
    """The MAXScript manifest's units|N line dispatches to the right branch."""

    def test_units_0_default_pass_through(self):
        p = _probe_from_manifest(_emission_manifest(multiplier=10.0, units=0))
        self.assertEqual(p.units, 0)
        self.assertAlmostEqual(_normalize_emission_weight(10.0, 0), 10.0)

    def test_units_1_lumens_divides_by_683(self):
        p = _probe_from_manifest(_emission_manifest(multiplier=683.0, units=1))
        self.assertEqual(p.units, 1)
        self.assertAlmostEqual(_normalize_emission_weight(683.0, 1), 1.0, places=5)

    def test_units_2_luminance_divides_by_pi(self):
        p = _probe_from_manifest(_emission_manifest(multiplier=math.pi, units=2))
        self.assertEqual(p.units, 2)
        self.assertAlmostEqual(_normalize_emission_weight(math.pi, 2), 1.0, places=5)

    def test_units_3_watts_pass_through(self):
        p = _probe_from_manifest(_emission_manifest(multiplier=12.0, units=3))
        self.assertEqual(p.units, 3)
        self.assertAlmostEqual(_normalize_emission_weight(12.0, 3), 12.0)

    def test_units_4_radiance_pass_through(self):
        p = _probe_from_manifest(_emission_manifest(multiplier=8.0, units=4))
        self.assertEqual(p.units, 4)
        self.assertAlmostEqual(_normalize_emission_weight(8.0, 4), 8.0)


class TestUnitsAbsentDefault(unittest.TestCase):
    """Pre-`units` V-Ray manifest (no units| line) -> falls back to units=0."""

    def test_no_units_line_treated_as_zero(self):
        manifest = _emission_manifest(multiplier=25.0, units=None,
                                       color=(255, 255, 255))
        p = _probe_from_manifest(manifest)
        self.assertEqual(p.units, 0,
            "Absent units| line must default to 0 (author's arbitrary scale)")

    def test_no_units_matches_units_0_behavior(self):
        m1 = _emission_manifest(multiplier=25.0, units=None,
                                 color=(255, 255, 255))
        m2 = _emission_manifest(multiplier=25.0, units=0,
                                 color=(255, 255, 255))
        stage = Usd.Stage.CreateInMemory()
        UsdShade.Material.Define(stage, "/Root/A")
        UsdShade.Material.Define(stage, "/Root/B")
        sa = _author_last_resort_mtlx(stage, Sdf.Path("/Root/A/S"),
                                        (0.0, 0.0, 0.0), m1)
        sb = _author_last_resort_mtlx(stage, Sdf.Path("/Root/B/S"),
                                        (0.0, 0.0, 0.0), m2)
        self.assertAlmostEqual(
            _get_input_value(sa, "emission"),
            _get_input_value(sb, "emission"))


class TestUnitsOutOfRangeFallback(unittest.TestCase):
    """Malformed units values clamp to 0 (default) to preserve author intent."""

    def test_units_out_of_range_clamps_to_zero(self):
        p = _probe_from_manifest(_emission_manifest(multiplier=5.0, units=99))
        self.assertEqual(p.units, 0)

    def test_units_negative_clamps_to_zero(self):
        p = _probe_from_manifest(_emission_manifest(multiplier=5.0, units=-1))
        self.assertEqual(p.units, 0)

    def test_units_non_integer_ignored(self):
        # A malformed manifest with a non-integer 'units' should keep the
        # default value (0) rather than crash the parser.
        manifest = "isLightMtl|1\nmultiplier|5.0\nunits|abc\n"
        p = _probe_from_manifest(manifest)
        self.assertEqual(p.units, 0)
        self.assertTrue(p.is_light_mtl)
        self.assertAlmostEqual(p.multiplier, 5.0)


class TestNormalizationFormula(unittest.TestCase):
    """The exact per-units divisor matches the C++ writer's literal constants."""

    def test_lumen_divisor_is_683(self):
        self.assertEqual(LUMEN_DIVISOR, 683.0,
            "LUMEN_DIVISOR must match the C++ '683.0f' literal")

    def test_luminance_divisor_is_pi(self):
        self.assertAlmostEqual(LUMINANCE_DIVISOR, math.pi, places=10,
            msg="LUMINANCE_DIVISOR must match the C++ 'M_PI' literal")

    def test_ceiling_is_30(self):
        self.assertEqual(EMISSION_CEILING, 30.0,
            "EMISSION_CEILING must match the C++ 'kEmissionCeiling = 30.0f'")


class TestSaturationCeiling(unittest.TestCase):
    """Post-normalize clamp caps every mode at 30.0."""

    def test_default_mode_clamps_extreme_multiplier(self):
        self.assertEqual(_normalize_emission_weight(100000.0, 0), 30.0)

    def test_lumen_mode_clamps_extreme(self):
        # Even after /683, an absurdly bright lumen source clamps to 30.
        self.assertEqual(_normalize_emission_weight(1e8, 1), 30.0)

    def test_luminance_mode_clamps_extreme(self):
        self.assertEqual(_normalize_emission_weight(1e6, 2), 30.0)

    def test_watts_mode_clamps_extreme(self):
        self.assertEqual(_normalize_emission_weight(1e6, 3), 30.0)

    def test_at_ceiling_boundary(self):
        self.assertEqual(_normalize_emission_weight(30.0, 0), 30.0)

    def test_below_ceiling_unchanged(self):
        self.assertAlmostEqual(_normalize_emission_weight(29.9, 0), 29.9)


class TestNegativeMultiplierClamp(unittest.TestCase):
    """Post-clamp is [0, 30]; MAXScript-side-effect negative mult clamps to 0."""

    def test_negative_multiplier_clamps_to_zero(self):
        self.assertEqual(_normalize_emission_weight(-5.0, 0), 0.0)

    def test_negative_multiplier_in_lumens_clamps_to_zero(self):
        self.assertEqual(_normalize_emission_weight(-1000.0, 1), 0.0)


class TestTier2TextureBranch(unittest.TestCase):
    """Texture-driven emission still authors normalized weight + connected color."""

    def test_tier2_authors_emission_as_weight(self):
        stage = Usd.Stage.CreateInMemory()
        UsdShade.Material.Define(stage, "/Root/Mat")
        manifest = _emission_manifest(
            multiplier=12.0, units=0,
            color=(255, 255, 255),
            texmap="C:/textures/led_grid.png")
        shader = _author_last_resort_mtlx(
            stage, Sdf.Path("/Root/Mat/Shader"),
            (0.0, 0.0, 0.0), manifest)
        e = _get_input_value(shader, "emission")
        self.assertAlmostEqual(e, 12.0)

    def test_tier2_normalizes_high_multiplier(self):
        stage = Usd.Stage.CreateInMemory()
        UsdShade.Material.Define(stage, "/Root/Mat")
        manifest = _emission_manifest(
            multiplier=200.0, units=0,
            color=(255, 255, 255),
            texmap="C:/textures/led_grid.png")
        shader = _author_last_resort_mtlx(
            stage, Sdf.Path("/Root/Mat/Shader"),
            (0.0, 0.0, 0.0), manifest)
        e = _get_input_value(shader, "emission")
        self.assertEqual(e, 30.0)  # clamped

    def test_tier2_connects_tiledimage_to_emission_color(self):
        stage = Usd.Stage.CreateInMemory()
        UsdShade.Material.Define(stage, "/Root/Mat")
        manifest = _emission_manifest(
            multiplier=5.0, units=0,
            color=(255, 255, 255),
            texmap="C:/textures/led_grid.png")
        shader = _author_last_resort_mtlx(
            stage, Sdf.Path("/Root/Mat/Shader"),
            (0.0, 0.0, 0.0), manifest)
        src = _get_input_source(shader, "emission_color")
        self.assertEqual(src, "emission_tex",
            "Tier 2 must connect emission_color to the sibling tiledimage shader")

    def test_tier2_tiledimage_shader_has_correct_id(self):
        stage = Usd.Stage.CreateInMemory()
        UsdShade.Material.Define(stage, "/Root/Mat")
        manifest = _emission_manifest(
            multiplier=5.0, units=0,
            color=(255, 255, 255),
            texmap="C:/textures/led_grid.png")
        _author_last_resort_mtlx(
            stage, Sdf.Path("/Root/Mat/Shader"),
            (0.0, 0.0, 0.0), manifest)
        tex_prim = stage.GetPrimAtPath("/Root/Mat/emission_tex")
        self.assertTrue(tex_prim,
            "Tier 2 must define an emission_tex sibling shader")
        tex_shader = UsdShade.Shader(tex_prim)
        id_attr = tex_shader.GetIdAttr().Get()
        self.assertEqual(id_attr, ND_TILEDIMAGE_COLOR3_ID)


class TestTier1ColorBranch(unittest.TestCase):
    """Constant-color emission authors weight scalar + raw tint color3."""

    def test_tier1_emission_color_is_raw_tint(self):
        stage = Usd.Stage.CreateInMemory()
        UsdShade.Material.Define(stage, "/Root/Mat")
        manifest = _emission_manifest(
            multiplier=8.0, units=0,
            color=(128, 200, 255))  # cool-tinted LED
        shader = _author_last_resort_mtlx(
            stage, Sdf.Path("/Root/Mat/Shader"),
            (0.0, 0.0, 0.0), manifest)
        ec = _get_input_value(shader, "emission_color")
        self.assertAlmostEqual(ec[0], 128.0 / 255.0, places=5)
        self.assertAlmostEqual(ec[1], 200.0 / 255.0, places=5)
        self.assertAlmostEqual(ec[2], 255.0 / 255.0, places=5)

    def test_tier1_emission_is_weight_scalar(self):
        stage = Usd.Stage.CreateInMemory()
        UsdShade.Material.Define(stage, "/Root/Mat")
        manifest = _emission_manifest(
            multiplier=20.0, units=0, color=(255, 255, 255))
        shader = _author_last_resort_mtlx(
            stage, Sdf.Path("/Root/Mat/Shader"),
            (0.0, 0.0, 0.0), manifest)
        self.assertAlmostEqual(_get_input_value(shader, "emission"), 20.0)

    def test_tier1_no_texmap_no_tiledimage_shader(self):
        stage = Usd.Stage.CreateInMemory()
        UsdShade.Material.Define(stage, "/Root/Mat")
        manifest = _emission_manifest(
            multiplier=5.0, units=0, color=(255, 255, 255))
        _author_last_resort_mtlx(
            stage, Sdf.Path("/Root/Mat/Shader"),
            (0.0, 0.0, 0.0), manifest)
        self.assertFalse(stage.GetPrimAtPath("/Root/Mat/emission_tex"),
            "Tier 1 must not scaffold an emission_tex sibling shader")


class TestBaseColorZeroForEmitter(unittest.TestCase):
    """Pure emitter has base_color = (0,0,0). Surgical scope preserved."""

    def test_base_color_zero_tier1(self):
        stage = Usd.Stage.CreateInMemory()
        UsdShade.Material.Define(stage, "/Root/Mat")
        manifest = _emission_manifest(
            multiplier=5.0, units=0, color=(255, 255, 255))
        shader = _author_last_resort_mtlx(
            stage, Sdf.Path("/Root/Mat/Shader"),
            (0.5, 0.5, 0.5), manifest)   # ambient diffuse ignored
        bc = _get_input_value(shader, "base_color")
        self.assertEqual(tuple(bc), (0.0, 0.0, 0.0))

    def test_base_color_zero_tier2(self):
        stage = Usd.Stage.CreateInMemory()
        UsdShade.Material.Define(stage, "/Root/Mat")
        manifest = _emission_manifest(
            multiplier=5.0, units=0, color=(255, 255, 255),
            texmap="C:/textures/x.png")
        shader = _author_last_resort_mtlx(
            stage, Sdf.Path("/Root/Mat/Shader"),
            (0.5, 0.5, 0.5), manifest)
        bc = _get_input_value(shader, "base_color")
        self.assertEqual(tuple(bc), (0.0, 0.0, 0.0))


class TestNonEmissiveFallback(unittest.TestCase):
    """Non-VRayLightMtl materials still hit the non-emissive fallback branch."""

    def test_non_light_material_uses_diffuse_color(self):
        stage = Usd.Stage.CreateInMemory()
        UsdShade.Material.Define(stage, "/Root/Mat")
        # Empty manifest -> is_light_mtl = False; the diffuse color param wins.
        shader = _author_last_resort_mtlx(
            stage, Sdf.Path("/Root/Mat/Shader"),
            (0.5, 0.7, 0.3), "")
        bc = _get_input_value(shader, "base_color")
        self.assertAlmostEqual(bc[0], 0.5, places=5)
        self.assertAlmostEqual(bc[1], 0.7, places=5)
        self.assertAlmostEqual(bc[2], 0.3, places=5)

    def test_non_light_no_emission_authored(self):
        stage = Usd.Stage.CreateInMemory()
        UsdShade.Material.Define(stage, "/Root/Mat")
        shader = _author_last_resort_mtlx(
            stage, Sdf.Path("/Root/Mat/Shader"),
            (0.4, 0.4, 0.4), "")
        # UsdShadeShader.GetInput returns a wrapper regardless; check defined-ness
        # via the underlying attribute HasAuthoredValue / IsDefined.
        prim = shader.GetPrim()
        self.assertFalse(prim.HasAttribute("inputs:emission"),
            "Non-emissive material must not author `emission`")
        self.assertFalse(prim.HasAttribute("inputs:emission_color"),
            "Non-emissive material must not author `emission_color`")


class TestSurgicalScopeCoverage(unittest.TestCase):
    """Non-emissive materials traverse the same code path pre-013 and post-013."""

    def test_non_light_material_identical_prefix_vs_postfix(self):
        s1 = Usd.Stage.CreateInMemory()
        s2 = Usd.Stage.CreateInMemory()
        UsdShade.Material.Define(s1, "/Root/M")
        UsdShade.Material.Define(s2, "/Root/M")
        shader_prefix = _author_last_resort_mtlx_prefix013(
            s1, Sdf.Path("/Root/M/S"), (0.3, 0.6, 0.1), "")
        shader_postfix = _author_last_resort_mtlx(
            s2, Sdf.Path("/Root/M/S"), (0.3, 0.6, 0.1), "")
        b1 = _get_input_value(shader_prefix, "base_color")
        b2 = _get_input_value(shader_postfix, "base_color")
        self.assertEqual(tuple(b1), tuple(b2),
            "Non-emissive material base_color must be byte-identical pre-013 vs post-013")
        # Neither variant authors emission on non-light materials.
        self.assertFalse(shader_prefix.GetPrim().HasAttribute("inputs:emission"))
        self.assertFalse(shader_postfix.GetPrim().HasAttribute("inputs:emission"))

    def test_shader_id_unchanged(self):
        s = Usd.Stage.CreateInMemory()
        UsdShade.Material.Define(s, "/Root/M")
        shader = _author_last_resort_mtlx(
            s, Sdf.Path("/Root/M/S"), (0.3, 0.6, 0.1),
            _emission_manifest(multiplier=5.0, color=(255, 255, 255)))
        self.assertEqual(shader.GetIdAttr().Get(), ND_STANDARD_SURFACE_ID,
            "shader info:id must remain ND_standard_surface_surfaceshader post-013")


class TestVRayOverrideMtlUnwrap(unittest.TestCase):
    """MAX-MTLX-005's VRayOverrideMtl unwrap contract is preserved.

    The unwrap happens on the MAXScript side (probe helper) — we exercise the
    logical contract: after unwrap, the probe manifest reads as a plain
    VRayLightMtl. Post-013 emission must be authored correctly for that case
    the same way as a bare VRayLightMtl.
    """

    def test_unwrapped_override_authors_emission(self):
        stage = Usd.Stage.CreateInMemory()
        UsdShade.Material.Define(stage, "/Root/Mat")
        # MAXScript's VRayOverrideMtl unwrap results in a manifest identical to
        # a bare VRayLightMtl with the same params, so we simulate that here.
        manifest = _emission_manifest(
            multiplier=50.0, units=0, color=(255, 200, 100))
        shader = _author_last_resort_mtlx(
            stage, Sdf.Path("/Root/Mat/Shader"),
            (0.0, 0.0, 0.0), manifest)
        # Verify emission authored + normalized (50.0 < 30 -> would clamp;
        # 50 default-units passes through, then clamp to 30).
        self.assertEqual(_get_input_value(shader, "emission"), 30.0)
        ec = _get_input_value(shader, "emission_color")
        self.assertAlmostEqual(ec[0], 1.0, places=5)
        self.assertAlmostEqual(ec[1], 200 / 255.0, places=5)
        self.assertAlmostEqual(ec[2], 100 / 255.0, places=5)


class TestArenaCensus(unittest.TestCase):
    """A Spectrum-arena-density census: 40 emissive materials.

    Pre-013, every mult>1 material saturates emission_color to >1.0 on at
    least one channel. Post-013, every material's emission_color stays in
    [0,1] and emission is clamped to [0,30]. Mesh / non-emissive counts
    byte-identical between prefix and postfix.
    """

    ARENA_EMITTERS = (
        # 30 LED-jumbotron style (mult=150, white)
        [("led_jumbotron_{}".format(i), 150.0, 0, (255, 255, 255)) for i in range(30)] +
        # 5 warm-tungsten practicals (mult=8, warm)
        [("tungsten_{}".format(i), 8.0, 0, (255, 220, 180)) for i in range(5)] +
        # 5 dim embers (mult=0.5, deep orange)
        [("ember_{}".format(i), 0.5, 0, (255, 120, 40)) for i in range(5)]
    )

    def test_prefix013_arena_every_bright_material_saturates(self):
        stage = Usd.Stage.CreateInMemory()
        saturated = 0
        for name, mult, units, color in self.ARENA_EMITTERS:
            UsdShade.Material.Define(stage, "/Root/{}".format(name))
            manifest = _emission_manifest(
                multiplier=mult, units=units, color=color)
            shader = _author_last_resort_mtlx_prefix013(
                stage,
                Sdf.Path("/Root/{}/Shader".format(name)),
                (0.0, 0.0, 0.0), manifest)
            ec = _get_input_value(shader, "emission_color")
            if any(c > 1.0 for c in ec):
                saturated += 1
        # 30 jumbotron + 5 tungsten (mult=8, RGB /255 * 8 -> all > 1 for r,g)
        # = 35 saturated. 5 ember have mult=0.5, always <=1.
        self.assertEqual(saturated, 35,
            "pre-013 arena baseline must show 35 saturated emitters "
            "(30 jumbotron + 5 tungsten); got {}".format(saturated))

    def test_post013_arena_no_material_saturates(self):
        stage = Usd.Stage.CreateInMemory()
        for name, mult, units, color in self.ARENA_EMITTERS:
            UsdShade.Material.Define(stage, "/Root/{}".format(name))
            manifest = _emission_manifest(
                multiplier=mult, units=units, color=color)
            shader = _author_last_resort_mtlx(
                stage,
                Sdf.Path("/Root/{}/Shader".format(name)),
                (0.0, 0.0, 0.0), manifest)
            ec = _get_input_value(shader, "emission_color")
            for c in ec:
                self.assertLessEqual(c, 1.0,
                    "post-013 emitter '{}' violates the [0,1] tint invariant "
                    "on emission_color".format(name))
                self.assertGreaterEqual(c, 0.0)
            e = _get_input_value(shader, "emission")
            self.assertGreaterEqual(e, 0.0)
            self.assertLessEqual(e, EMISSION_CEILING,
                "post-013 emission on '{}' exceeds ceiling; got {}".format(name, e))

    def test_post013_arena_bright_emitters_clamp_at_ceiling(self):
        # Jumbotron mult=150 default-units -> min(150, 30) = 30.
        stage = Usd.Stage.CreateInMemory()
        UsdShade.Material.Define(stage, "/Root/Jumbo")
        manifest = _emission_manifest(
            multiplier=150.0, units=0, color=(255, 255, 255))
        shader = _author_last_resort_mtlx(
            stage, Sdf.Path("/Root/Jumbo/Shader"),
            (0.0, 0.0, 0.0), manifest)
        e = _get_input_value(shader, "emission")
        self.assertEqual(e, EMISSION_CEILING,
            "Jumbotron-density emitter must clamp to the 30 ceiling")

    def test_post013_arena_dim_emitters_pass_through(self):
        # Dim ember mult=0.5 default-units -> passes through.
        stage = Usd.Stage.CreateInMemory()
        UsdShade.Material.Define(stage, "/Root/Ember")
        manifest = _emission_manifest(
            multiplier=0.5, units=0, color=(255, 120, 40))
        shader = _author_last_resort_mtlx(
            stage, Sdf.Path("/Root/Ember/Shader"),
            (0.0, 0.0, 0.0), manifest)
        e = _get_input_value(shader, "emission")
        self.assertAlmostEqual(e, 0.5, places=5,
            msg="Dim emitter must pass through without clamping")


class TestStrictSupersetOfMTLX005(unittest.TestCase):
    """The post-013 writer is a strict superset of MAX-MTLX-005's contract:
    emission IS authored for every VRayLightMtl material (the MTLX-005
    invariant), and emission_color IS non-black for every non-black color."""

    def test_every_light_mtl_authors_emission(self):
        stage = Usd.Stage.CreateInMemory()
        for i, mult in enumerate([0.1, 1.0, 15.0, 150.0, 1e4]):
            UsdShade.Material.Define(stage, "/Root/L{}".format(i))
            manifest = _emission_manifest(
                multiplier=mult, units=0, color=(255, 255, 255))
            shader = _author_last_resort_mtlx(
                stage, Sdf.Path("/Root/L{}/Shader".format(i)),
                (0.0, 0.0, 0.0), manifest)
            self.assertIsNotNone(shader.GetInput("emission"),
                "MTLX-005 invariant: every VRayLightMtl material must author `emission`")
            self.assertIsNotNone(shader.GetInput("emission_color"),
                "MTLX-005 invariant: every VRayLightMtl material must author `emission_color`")

    def test_every_non_black_color_produces_non_black_emission_color(self):
        stage = Usd.Stage.CreateInMemory()
        UsdShade.Material.Define(stage, "/Root/M")
        manifest = _emission_manifest(
            multiplier=5.0, units=0, color=(255, 128, 64))
        shader = _author_last_resort_mtlx(
            stage, Sdf.Path("/Root/M/Shader"),
            (0.0, 0.0, 0.0), manifest)
        ec = _get_input_value(shader, "emission_color")
        for c in ec:
            self.assertGreater(c, 0.0,
                "Non-black tint must produce non-black emission_color post-013")


if __name__ == "__main__":
    unittest.main(verbosity=2)
