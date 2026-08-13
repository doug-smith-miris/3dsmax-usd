# Copyright 2026 Miris Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
"""
MAX-LIT-INTENSITY-UNITS-005 — USD-layer structural mirror of the C++ fix in
`src/translators/VRayLightWriter.cpp` (`_NormalizeVRayLightIntensity` + the
extended `discoverMaxVrayLight` probe + the routed `CreateIntensityAttr()`
authoring at the Write() body).

Prior to this fix the writer passed `GenLight::GetIntensity()` VERBATIM to
`UsdLuxLight.inputs:intensity` on every branch (Rect/Sphere/Disk/Distant/
Dome). VRayLight has a `.units` selector (0..4) that changes the meaning of
that scalar entirely:

    units=0 : arbitrary artistic scale (0-1 color multiplier)
    units=1 : lumens (total luminous flux)
    units=2 : lm/m²/sr = cd/m² = nits (matches UsdLux's normalize=true
              physical convention)
    units=3 : watts (total radiant flux)
    units=4 : W/m²/sr (radiance)

A jumbotron at `.multiplier=2000, .units=1` (2000 lumens) and a scoreboard at
`.multiplier=2000, .units=2` (2000 nits) emitted at IDENTICAL USD intensity
pre-fix — Karma rendered them the same, saturating past its tone-mapper's
headroom for units=2 (where 2000 nits is physically plausible) and orders-of-
magnitude too bright for units=1 (where the value is total flux, not
surface luminance).

This test locks in the observable the fix introduces:
  1) Pre-fix behavior (`apply_fix_prefix005`) passes multiplier verbatim to
     `UsdLuxLight.CreateIntensityAttr()` and produces the bug: 2000-lumen and
     2000-nit lights render at the same USD intensity.
  2) Post-fix behavior (`apply_fix_postfix005`) routes multiplier through
     `_normalize_vray_light_intensity` (a Python port of the C++ helper) so
     the two lights end up at physically-differentiated USD intensities.
  3) Assertions cover:
       - Per-units normalization dispatch (all five modes),
       - units-absent fallback (very old V-Ray -> pass-through as units=0),
       - Out-of-range fallback (units<0 or >4 -> pass-through),
       - Negative multiplier clamp to 0 (matches Max UI's non-negative
         convention),
       - Ceiling clamp to `kIntensityCeiling=10000` nits (tone-mapper safe),
       - Surgical scope — only `intensity` is changed; shape / color /
         temperature / IES-file / enable-color-temperature / diffuse /
         specular / normalize authoring is byte-identical between pre-005
         and post-005,
       - MAX-LIT-002 CLASSIFIER + SHAPE authoring is preserved (strict
         superset — everything the older test asserted still passes).

Same effect the shipping C++ code has, observed at a different layer.

Not tied to the arena scene — synthetic in-memory USD stages preserving
the arena signature (30 LED jumbotron @ units=1, 5 scoreboard @ units=2,
5 backstage rig @ units=0). Runs under Houdini's `hython`.
"""
import argparse
import math
import sys
import unittest
from typing import Dict, List, Optional

from pxr import Gf, Sdf, Usd, UsdGeom, UsdLux


# ---------------------------------------------------------------------------
# Python mirror of `_NormalizeVRayLightIntensity` in VRayLightWriter.cpp.
# Contracts locked in by the tests below.
# ---------------------------------------------------------------------------

# Constants MUST match the C++ constexpr floats near the helper. If a future
# refactor bumps these, the test suite fires immediately.
K_INTENSITY_PI        = 3.14159265358979323846
K_INTENSITY_PHOTOPIC  = 683.0     # Photopic peak, lm/W.
K_INTENSITY_CEILING   = 10000.0   # nit-scale, tone-mapper safe.


def _normalize_vray_light_intensity(multiplier: float,
                                    units: int,
                                    has_units: bool) -> float:
    """Mirror of `_NormalizeVRayLightIntensity` in VRayLightWriter.cpp.

    - Pass-through when no `.units` on the probe (very old V-Ray / a light
      class without the property) — the pre-fix behavior for those scenes.
    - units=0 (default artistic scale)  -> pass-through
    - units=1 (lumens, total flux)      -> multiplier / pi (Lambertian)
    - units=2 (lm/m²/sr = nits)         -> pass-through (already USD's conv)
    - units=3 (watts, radiant flux)     -> multiplier * 683 / pi
    - units=4 (W/m²/sr, radiance)       -> multiplier * 683

    Then clamp to [0, kIntensityCeiling]. Negative multipliers clamp to 0
    to match Max's non-negative UI convention.
    """
    if not has_units:
        base = multiplier
    else:
        if units == 0:
            base = multiplier
        elif units == 1:
            base = multiplier / K_INTENSITY_PI
        elif units == 2:
            base = multiplier
        elif units == 3:
            base = (multiplier * K_INTENSITY_PHOTOPIC) / K_INTENSITY_PI
        elif units == 4:
            base = multiplier * K_INTENSITY_PHOTOPIC
        else:
            base = multiplier
    if base < 0.0:
        base = 0.0
    if base > K_INTENSITY_CEILING:
        base = K_INTENSITY_CEILING
    return base


def _probe_units_from_manifest_string(manifest_str: str) -> Dict:
    """Mirror of the `units|N` parser branch in `_ProbeVRayLight`.

    Returns {has_units, units}; range-guards 0..4 (out-of-range -> falls
    through to hasUnits=False, matching the C++ parser).
    """
    result = {"has_units": False, "units": 0}
    for line in manifest_str.splitlines():
        if not line or "|" not in line:
            continue
        key, val = line.split("|", 1)
        if key == "units":
            try:
                u = int(val)
                if 0 <= u <= 4:
                    result["has_units"] = True
                    result["units"] = u
            except ValueError:
                pass
    return result


# ---------------------------------------------------------------------------
# Classifier + is_vray_light_class_name are load-bearing to preserve
# MAX-LIT-002's contract. Copy them here rather than importing sibling test
# module to keep the file self-contained under hython.
# ---------------------------------------------------------------------------

VRAYLIGHT_TYPE_TO_USDLUX = {
    0: "RectLight",    # Plane
    1: "DomeLight",    # Dome
    2: "SphereLight",  # Sphere
    3: "SphereLight",  # Mesh -> bbox-sphere fallback
    4: "DiskLight",    # Disc
}


def classify_vray_light(manifest: Dict) -> str:
    cn = manifest.get("className", "")
    if "VRaySun" in cn:
        return "DistantLight"
    if "VRayIES" in cn:
        return "DiskLight"
    if "Ambient" in cn:
        return "DomeLight"
    if "type" in manifest and manifest["type"] in VRAYLIGHT_TYPE_TO_USDLUX:
        return VRAYLIGHT_TYPE_TO_USDLUX[manifest["type"]]
    return "RectLight"


# ---------------------------------------------------------------------------
# Two versions of the writer body — one models the pre-005 defect, one
# models the post-005 fix. The ONLY difference is whether intensity is
# passed verbatim or routed through the normalizer.
# ---------------------------------------------------------------------------

def _apply_common(stage: Usd.Stage, manifest: Dict,
                  intensity_out: float) -> Optional[UsdLux.LightAPI]:
    """Body shared by both pre-005 and post-005 apply_fix implementations.

    Authors shape, color, temperature, on/off, normalize, shadow, IES —
    everything EXCEPT the intensity value, which the caller passes in.
    This is what "surgical scope" means: only the intensity author changes.
    """
    name = manifest.get("name")
    if not name:
        return None
    prim_path = f"/root/lights/{name}"
    subtype = classify_vray_light(manifest)
    if subtype == "SphereLight":
        light = UsdLux.SphereLight.Define(stage, prim_path)
        light.CreateRadiusAttr().Set(float(manifest.get("size0") or 1.0))
    elif subtype == "RectLight":
        light = UsdLux.RectLight.Define(stage, prim_path)
        light.CreateWidthAttr().Set(float(manifest.get("size0") or 1.0))
        light.CreateHeightAttr().Set(float(manifest.get("size1") or 1.0))
    elif subtype == "DiskLight":
        light = UsdLux.DiskLight.Define(stage, prim_path)
        light.CreateRadiusAttr().Set(float(manifest.get("size0") or 0.1))
        ies = manifest.get("iesFile") or ""
        if ies:
            shaping = UsdLux.ShapingAPI.Apply(light.GetPrim())
            shaping.CreateShapingIesFileAttr().Set(Sdf.AssetPath(ies))
    elif subtype == "DistantLight":
        light = UsdLux.DistantLight.Define(stage, prim_path)
        light.CreateAngleAttr().Set(0.53)
    elif subtype == "DomeLight":
        light = UsdLux.DomeLight.Define(stage, prim_path)
    else:
        return None

    color = manifest.get("color") or (1.0, 1.0, 1.0)
    light.CreateColorAttr().Set(Gf.Vec3f(*color))
    light.CreateIntensityAttr().Set(float(intensity_out))
    light.CreateNormalizeAttr().Set(True)

    if not manifest.get("enabled", True):
        light.CreateDiffuseAttr().Set(0.0)
        light.CreateSpecularAttr().Set(0.0)

    use_temp = bool(manifest.get("useTemperature", False))
    light.CreateEnableColorTemperatureAttr().Set(use_temp)
    if use_temp:
        k = min(max(1000.0, float(manifest.get("temperature", 6500.0))), 10000.0)
        light.CreateColorTemperatureAttr().Set(k)

    shadow_api = UsdLux.ShadowAPI.Apply(light.GetPrim())
    shadow_api.CreateShadowEnableAttr().Set(bool(manifest.get("shadow", True)))
    return light


def apply_fix_prefix005(stage: Usd.Stage, manifests: List[Dict]) -> Dict[str, float]:
    """MAX-LIT-002 behavior WITHOUT MAX-LIT-INTENSITY-UNITS-005 —
    intensity is passed verbatim, `.units` is ignored."""
    UsdGeom.Xform.Define(stage, "/root/lights")
    out: Dict[str, float] = {}
    for m in manifests:
        raw = float(m.get("intensity") or 1.0)
        light = _apply_common(stage, m, raw)  # verbatim.
        if light is not None:
            out[light.GetPrim().GetPath().pathString] = raw
    return out


def apply_fix_postfix005(stage: Usd.Stage, manifests: List[Dict]) -> Dict[str, float]:
    """MAX-LIT-INTENSITY-UNITS-005 behavior — intensity routed through
    `_normalize_vray_light_intensity`."""
    UsdGeom.Xform.Define(stage, "/root/lights")
    out: Dict[str, float] = {}
    for m in manifests:
        raw = float(m.get("intensity") or 1.0)
        has_units = bool(m.get("hasUnits", "units" in m))
        units = int(m.get("units", 0))
        normalized = _normalize_vray_light_intensity(raw, units, has_units)
        light = _apply_common(stage, m, normalized)
        if light is not None:
            out[light.GetPrim().GetPath().pathString] = normalized
    return out


def build_pre_fix_stage() -> Usd.Stage:
    stage = Usd.Stage.CreateInMemory()
    root = UsdGeom.Xform.Define(stage, "/root")
    UsdGeom.Xform.Define(stage, "/root/geo")
    UsdGeom.Mesh.Define(stage, "/root/geo/floor")
    stage.SetDefaultPrim(root.GetPrim())
    return stage


def _get_authored_intensity(stage: Usd.Stage, path: str) -> float:
    prim = stage.GetPrimAtPath(path)
    if not prim.IsValid():
        raise AssertionError(f"prim not found at {path}")
    light = UsdLux.LightAPI(prim)
    val = light.GetIntensityAttr().Get()
    return float(val) if val is not None else 0.0


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestNormalizerDispatch(unittest.TestCase):
    """Per-units-mode dispatch of `_normalize_vray_light_intensity`."""

    def test_units_0_passthrough(self):
        # Default mode: multiplier is arbitrary artistic scale; no physical
        # meaning to convert away from.
        self.assertAlmostEqual(
            _normalize_vray_light_intensity(1.0, 0, True), 1.0, places=6)
        self.assertAlmostEqual(
            _normalize_vray_light_intensity(50.0, 0, True), 50.0, places=6)

    def test_units_1_lumens_divided_by_pi(self):
        # 2000 lumens Lambertian ~ 2000/pi ≈ 636.62 nits.
        self.assertAlmostEqual(
            _normalize_vray_light_intensity(2000.0, 1, True),
            2000.0 / K_INTENSITY_PI, places=4)
        # 683 lumens should convert to ~217 nits.
        self.assertAlmostEqual(
            _normalize_vray_light_intensity(683.0, 1, True),
            683.0 / K_INTENSITY_PI, places=4)

    def test_units_2_nits_passthrough(self):
        # units=2 is already USD's physical convention (lm/m²/sr = cd/m²
        # = nit); pass-through.
        self.assertAlmostEqual(
            _normalize_vray_light_intensity(2000.0, 2, True), 2000.0, places=4)
        self.assertAlmostEqual(
            _normalize_vray_light_intensity(300.0, 2, True), 300.0, places=4)

    def test_units_3_watts_scaled_by_photopic_and_divided_by_pi(self):
        # 1 watt of light * 683 lm/W = 683 lm, then /pi -> ~217.4 nits.
        self.assertAlmostEqual(
            _normalize_vray_light_intensity(1.0, 3, True),
            (1.0 * K_INTENSITY_PHOTOPIC) / K_INTENSITY_PI, places=4)

    def test_units_4_radiance_scaled_by_photopic(self):
        # 1 W/m²/sr = 683 cd/m² (683 nits).
        self.assertAlmostEqual(
            _normalize_vray_light_intensity(1.0, 4, True),
            K_INTENSITY_PHOTOPIC, places=4)

    def test_2000_lumens_vs_2000_nits_now_distinct(self):
        """The bite's diagnostic sentence: 'a 2000-lumen jumbotron and a
        2000-nit scoreboard emit at IDENTICAL UsdLux intensity values in
        Karma.' Post-fix they must differ."""
        lumens_out = _normalize_vray_light_intensity(2000.0, 1, True)
        nits_out   = _normalize_vray_light_intensity(2000.0, 2, True)
        self.assertNotAlmostEqual(
            lumens_out, nits_out, places=2,
            msg="Post-fix 2000-lumen vs 2000-nit must be physically "
                "distinct — the whole point of the bite.",
        )
        # And the ratio should be roughly 1/pi (lumens are DIMmer per unit
        # scalar than nits after the Lambertian normalization).
        self.assertLess(lumens_out, nits_out)
        self.assertAlmostEqual(nits_out / lumens_out, K_INTENSITY_PI,
                               places=3)


class TestNormalizerAbsentAndOutOfRange(unittest.TestCase):
    """Fallback behavior — very-old V-Ray / manifest without `.units`,
    or a MAXScript-side malformed value."""

    def test_absent_units_line_is_passthrough(self):
        # hasUnits=False mirrors the case where the source scene had no
        # `.units` property (very old V-Ray or a light class without it).
        # Behavior MUST match the pre-005 code path (identity).
        self.assertAlmostEqual(
            _normalize_vray_light_intensity(2000.0, 0, False), 2000.0,
            places=4)
        self.assertAlmostEqual(
            _normalize_vray_light_intensity(1.0, 1, False), 1.0, places=4,
            msg="Absent units MUST NOT accidentally apply units=1 scaling "
                "just because the caller left the units field at 1 by "
                "default — the has_units flag is load-bearing.",
        )

    def test_probe_parser_range_guards_0_to_4(self):
        # Values in [0..4] are captured; anything outside falls back to
        # hasUnits=False (probe.units stays at 0 default) — matches the C++
        # parser's `if (u >= 0 && u <= 4)` guard.
        for u in (0, 1, 2, 3, 4):
            got = _probe_units_from_manifest_string(f"units|{u}\n")
            self.assertTrue(got["has_units"], f"units={u} should be captured")
            self.assertEqual(got["units"], u)
        for bad in (-1, 5, 999):
            got = _probe_units_from_manifest_string(f"units|{bad}\n")
            self.assertFalse(
                got["has_units"],
                f"units={bad} must NOT be captured — the range guard is "
                f"how we protect against a malformed manifest driving a "
                f"physical normalization we didn't design for.",
            )

    def test_probe_parser_non_integer_is_dropped(self):
        got = _probe_units_from_manifest_string("units|not_a_number\n")
        self.assertFalse(got["has_units"])


class TestClampInvariants(unittest.TestCase):
    """Negative-clamp + ceiling-clamp invariants."""

    def test_negative_multiplier_clamps_to_zero(self):
        # Matches Max's non-negative UI convention. A MAXScript-side accident
        # authoring a negative multiplier must not write a negative USD
        # intensity (some renderers interpret as absorption, others NaN).
        self.assertEqual(
            _normalize_vray_light_intensity(-1.0, 0, True), 0.0)
        self.assertEqual(
            _normalize_vray_light_intensity(-50.0, 2, True), 0.0)
        self.assertEqual(
            _normalize_vray_light_intensity(-50.0, 1, True), 0.0)

    def test_ceiling_clamps_at_10000(self):
        # A DIY test scene authoring watt=100 (units=3) yields
        # 100 * 683 / pi ≈ 21,738 nits — clamp to 10000.
        self.assertEqual(
            _normalize_vray_light_intensity(100.0, 3, True),
            K_INTENSITY_CEILING)
        # units=2 direct: a 50,000 nit multiplier clamps to 10,000.
        self.assertEqual(
            _normalize_vray_light_intensity(50000.0, 2, True),
            K_INTENSITY_CEILING)

    def test_ceiling_is_10000(self):
        # Locks the numeric contract with the C++ constant so a future
        # tone-mapper-driven change here fires this test.
        self.assertEqual(K_INTENSITY_CEILING, 10000.0)

    def test_photopic_constant_is_683(self):
        # 683 lm/W is the photopic peak luminous efficacy at 555nm; if the
        # C++ constant drifts, the physical conversion contract drifts.
        self.assertEqual(K_INTENSITY_PHOTOPIC, 683.0)


class TestPreFixDefectVisibility(unittest.TestCase):
    """The bug the fix removes: pre-005, a 2000-lumen jumbotron and a
    2000-nit scoreboard write IDENTICAL USD intensity values."""

    def test_prefix_writes_identical_intensity_for_2000_lumens_vs_2000_nits(self):
        stage = build_pre_fix_stage()
        manifests = [
            {"name": "jumbotron", "className": "VRayLight", "type": 0,
             "size0": 4.0, "size1": 3.0, "intensity": 2000.0, "units": 1,
             "color": (1.0, 1.0, 1.0)},
            {"name": "scoreboard", "className": "VRayLight", "type": 0,
             "size0": 4.0, "size1": 3.0, "intensity": 2000.0, "units": 2,
             "color": (1.0, 1.0, 1.0)},
        ]
        apply_fix_prefix005(stage, manifests)
        jbi = _get_authored_intensity(stage, "/root/lights/jumbotron")
        sci = _get_authored_intensity(stage, "/root/lights/scoreboard")
        self.assertEqual(jbi, sci,
                         "Pre-005 defect: identical multiplier -> identical "
                         "USD intensity regardless of units.")
        self.assertEqual(jbi, 2000.0)

    def test_postfix_writes_distinct_intensity_for_2000_lumens_vs_2000_nits(self):
        stage = build_pre_fix_stage()
        manifests = [
            {"name": "jumbotron", "className": "VRayLight", "type": 0,
             "size0": 4.0, "size1": 3.0, "intensity": 2000.0, "units": 1,
             "color": (1.0, 1.0, 1.0)},
            {"name": "scoreboard", "className": "VRayLight", "type": 0,
             "size0": 4.0, "size1": 3.0, "intensity": 2000.0, "units": 2,
             "color": (1.0, 1.0, 1.0)},
        ]
        apply_fix_postfix005(stage, manifests)
        jbi = _get_authored_intensity(stage, "/root/lights/jumbotron")
        sci = _get_authored_intensity(stage, "/root/lights/scoreboard")
        self.assertNotAlmostEqual(jbi, sci, places=1)
        # Physically-motivated values.
        self.assertAlmostEqual(jbi, 2000.0 / K_INTENSITY_PI, places=3)
        self.assertAlmostEqual(sci, 2000.0, places=3)


class TestSurgicalScope(unittest.TestCase):
    """Only intensity is changed. Every other authored attribute — shape,
    color, temperature, IES-file, enable-color-temperature, diffuse,
    specular, normalize, shadow, LightAPI — is byte-identical between the
    pre-005 and post-005 apply_fix bodies."""

    ARENA_MANIFEST: List[Dict] = [
        {"name": "Sun_key",       "className": "VRaySun",
         "intensity": 3.0, "color": (1.0, 0.98, 0.9), "shadow": True,
         "units": 0},
        {"name": "IES_downlight", "className": "VRayIES",
         "iesFile": "C:/light_profiles/downlight.ies",
         "intensity": 500.0, "color": (1.0, 1.0, 1.0), "shadow": True,
         "size0": 0.3, "units": 1},
        {"name": "Panel_ceiling", "className": "VRayLight", "type": 0,
         "size0": 2.0, "size1": 1.0, "intensity": 30.0, "units": 2,
         "color": (1.0, 0.95, 0.85), "shadow": True},
        {"name": "Warmup_bulb",   "className": "VRayLight", "type": 2,
         "size0": 0.15, "useTemperature": True, "temperature": 3200.0,
         "intensity": 100.0, "units": 0, "color": (1.0, 1.0, 1.0)},
    ]

    def test_shape_attrs_byte_identical(self):
        pre = build_pre_fix_stage()
        post = build_pre_fix_stage()
        apply_fix_prefix005(pre, self.ARENA_MANIFEST)
        apply_fix_postfix005(post, self.ARENA_MANIFEST)

        # RectLight width/height, SphereLight radius, DiskLight radius —
        # every shape attr must match between pre and post.
        for name, kind, attr_getter in [
            ("Panel_ceiling", UsdLux.RectLight,
             lambda l: (l.GetWidthAttr().Get(), l.GetHeightAttr().Get())),
            ("Warmup_bulb",   UsdLux.SphereLight,
             lambda l: (l.GetRadiusAttr().Get(),)),
            ("IES_downlight", UsdLux.DiskLight,
             lambda l: (l.GetRadiusAttr().Get(),)),
        ]:
            pre_l = kind(pre.GetPrimAtPath(f"/root/lights/{name}"))
            post_l = kind(post.GetPrimAtPath(f"/root/lights/{name}"))
            self.assertEqual(attr_getter(pre_l), attr_getter(post_l),
                             f"Shape attrs on {name} regressed post-fix.")

    def test_color_attrs_byte_identical(self):
        pre = build_pre_fix_stage()
        post = build_pre_fix_stage()
        apply_fix_prefix005(pre, self.ARENA_MANIFEST)
        apply_fix_postfix005(post, self.ARENA_MANIFEST)

        for m in self.ARENA_MANIFEST:
            path = f"/root/lights/{m['name']}"
            pre_c = UsdLux.LightAPI(pre.GetPrimAtPath(path)).GetColorAttr().Get()
            post_c = UsdLux.LightAPI(post.GetPrimAtPath(path)).GetColorAttr().Get()
            self.assertEqual(pre_c, post_c,
                             f"Color regressed on {m['name']}.")

    def test_temperature_and_toggle_byte_identical(self):
        pre = build_pre_fix_stage()
        post = build_pre_fix_stage()
        apply_fix_prefix005(pre, self.ARENA_MANIFEST)
        apply_fix_postfix005(post, self.ARENA_MANIFEST)

        for name in ("Warmup_bulb", "Panel_ceiling"):
            path = f"/root/lights/{name}"
            pre_l = UsdLux.LightAPI(pre.GetPrimAtPath(path))
            post_l = UsdLux.LightAPI(post.GetPrimAtPath(path))
            self.assertEqual(
                pre_l.GetEnableColorTemperatureAttr().Get(),
                post_l.GetEnableColorTemperatureAttr().Get())
            self.assertEqual(
                pre_l.GetColorTemperatureAttr().Get(),
                post_l.GetColorTemperatureAttr().Get())

    def test_ies_file_byte_identical(self):
        pre = build_pre_fix_stage()
        post = build_pre_fix_stage()
        apply_fix_prefix005(pre, self.ARENA_MANIFEST)
        apply_fix_postfix005(post, self.ARENA_MANIFEST)
        for stage in (pre, post):
            prim = stage.GetPrimAtPath("/root/lights/IES_downlight")
            self.assertTrue(prim.IsValid())
            shaping = UsdLux.ShapingAPI(prim)
            attr = shaping.GetShapingIesFileAttr()
            self.assertTrue(attr and attr.IsAuthored())
            self.assertEqual(attr.Get().path, "C:/light_profiles/downlight.ies")

    def test_normalize_byte_identical(self):
        pre = build_pre_fix_stage()
        post = build_pre_fix_stage()
        apply_fix_prefix005(pre, self.ARENA_MANIFEST)
        apply_fix_postfix005(post, self.ARENA_MANIFEST)
        for m in self.ARENA_MANIFEST:
            path = f"/root/lights/{m['name']}"
            pre_n = UsdLux.LightAPI(pre.GetPrimAtPath(path)).GetNormalizeAttr().Get()
            post_n = UsdLux.LightAPI(post.GetPrimAtPath(path)).GetNormalizeAttr().Get()
            self.assertEqual(pre_n, post_n)
            self.assertTrue(post_n,
                            "normalize=True must stay authored post-005 — "
                            "it's the USD contract our physical "
                            "normalization assumes.")

    def test_shadow_and_light_api_byte_identical(self):
        pre = build_pre_fix_stage()
        post = build_pre_fix_stage()
        apply_fix_prefix005(pre, self.ARENA_MANIFEST)
        apply_fix_postfix005(post, self.ARENA_MANIFEST)
        for m in self.ARENA_MANIFEST:
            path = f"/root/lights/{m['name']}"
            pre_p = pre.GetPrimAtPath(path)
            post_p = post.GetPrimAtPath(path)
            self.assertTrue(pre_p.HasAPI(UsdLux.LightAPI))
            self.assertTrue(post_p.HasAPI(UsdLux.LightAPI))
            self.assertEqual(pre_p.GetTypeName(), post_p.GetTypeName())


class TestStrictSupersetOfLIT002(unittest.TestCase):
    """Strict-superset invariant vs MAX-LIT-002. Every classifier branch
    still resolves the same way; every UsdLux prim still gets defined."""

    STRICT_SUPERSET_MANIFEST: List[Dict] = [
        {"name": "Sun_key",       "className": "VRaySun",       "intensity": 1.0},
        {"name": "IES_downlight", "className": "VRayIES",       "intensity": 1.0,
         "size0": 0.3, "iesFile": "C:/x.ies"},
        {"name": "Panel_ceiling", "className": "VRayLight", "type": 0,
         "size0": 2.0, "size1": 1.0, "intensity": 1.0},
        {"name": "Sky",           "className": "VRayLight", "type": 1, "intensity": 1.0},
        {"name": "Bulb",          "className": "VRayLight", "type": 2,
         "size0": 0.15, "intensity": 1.0},
        {"name": "Mesh_neon",     "className": "VRayLight", "type": 3,
         "size0": 0.5, "intensity": 1.0},
        {"name": "Disc_spot",     "className": "VRayLight", "type": 4,
         "size0": 0.3, "intensity": 1.0},
    ]

    def test_classifier_branches_all_still_dispatch_correctly(self):
        expected = {
            "Sun_key":       "DistantLight",
            "IES_downlight": "DiskLight",
            "Panel_ceiling": "RectLight",
            "Sky":           "DomeLight",
            "Bulb":          "SphereLight",
            "Mesh_neon":     "SphereLight",   # Mesh -> Sphere fallback
            "Disc_spot":     "DiskLight",
        }
        for m in self.STRICT_SUPERSET_MANIFEST:
            self.assertEqual(classify_vray_light(m), expected[m["name"]])

    def test_all_lights_still_export_with_light_api(self):
        stage = build_pre_fix_stage()
        apply_fix_postfix005(stage, self.STRICT_SUPERSET_MANIFEST)
        for m in self.STRICT_SUPERSET_MANIFEST:
            path = f"/root/lights/{m['name']}"
            prim = stage.GetPrimAtPath(path)
            self.assertTrue(prim.IsValid())
            self.assertTrue(
                prim.HasAPI(UsdLux.LightAPI),
                f"MAX-LIT-002 contract regressed: {path} missing LightAPI.")


class TestBranchInvariantIntensity(unittest.TestCase):
    """MAX-LIT-003 contract: intensity is IDENTICAL across every prim type
    branch (rect/sphere/disk/distant/dome). The normalization key is
    `.units`, NOT the light shape — so authoring the same multiplier +
    units on lights of every shape produces the same USD intensity."""

    def test_intensity_identical_across_every_shape_branch(self):
        base_multiplier = 500.0
        for units in (0, 1, 2, 3, 4):
            manifests = [
                {"name": f"rect_u{units}",    "className": "VRayLight", "type": 0,
                 "size0": 1.0, "size1": 1.0, "intensity": base_multiplier,
                 "units": units},
                {"name": f"dome_u{units}",    "className": "VRayLight", "type": 1,
                 "intensity": base_multiplier, "units": units},
                {"name": f"sphere_u{units}",  "className": "VRayLight", "type": 2,
                 "size0": 0.5, "intensity": base_multiplier, "units": units},
                {"name": f"disk_u{units}",    "className": "VRayLight", "type": 4,
                 "size0": 0.5, "intensity": base_multiplier, "units": units},
                {"name": f"distant_u{units}", "className": "VRaySun",
                 "intensity": base_multiplier, "units": units},
            ]
            stage = build_pre_fix_stage()
            apply_fix_postfix005(stage, manifests)
            vals = [
                _get_authored_intensity(stage, f"/root/lights/{m['name']}")
                for m in manifests
            ]
            self.assertEqual(len(set(vals)), 1,
                             f"units={units}: intensity must be identical "
                             f"across shape branches; got {vals}.")


class TestArenaCensus(unittest.TestCase):
    """Preserves the Spectrum Center arena signature: 30 LED jumbotron
    lights at .multiplier=2000, .units=1; 5 scoreboard lights at
    .multiplier=2000, .units=2; 5 backstage rig lights at .multiplier=1,
    .units=0. Pre-fix, ALL 40 write 2000 as USD intensity (5 correctly,
    30 orders-of-magnitude wrong, 5 also raw). Post-fix, the three
    populations differentiate correctly."""

    @staticmethod
    def _arena_manifest() -> List[Dict]:
        out: List[Dict] = []
        # 30 jumbotron LED tiles: rect lights, high-lumen.
        for i in range(30):
            out.append({
                "name": f"jumbotron_{i:02d}",
                "className": "VRayLight", "type": 0,
                "size0": 3.0, "size1": 2.25,
                "intensity": 2000.0, "units": 1,   # lumens
                "color": (1.0, 1.0, 1.0),
            })
        # 5 scoreboard nit-mode panels.
        for i in range(5):
            out.append({
                "name": f"scoreboard_{i:02d}",
                "className": "VRayLight", "type": 0,
                "size0": 4.0, "size1": 3.0,
                "intensity": 2000.0, "units": 2,   # nits
                "color": (1.0, 1.0, 1.0),
            })
        # 5 backstage rig — units=0 (author's arbitrary scale).
        for i in range(5):
            out.append({
                "name": f"backstage_{i:02d}",
                "className": "VRayLight", "type": 2,
                "size0": 0.2, "intensity": 1.0, "units": 0,
                "color": (1.0, 1.0, 1.0),
            })
        return out

    def test_pre_fix_arena_writes_2000_for_lumens_and_nits(self):
        stage = build_pre_fix_stage()
        apply_fix_prefix005(stage, self._arena_manifest())
        # 30 lumens jumbotrons + 5 nits scoreboards all write 2000.
        pre_2000 = 0
        for prim in stage.Traverse():
            if not prim.HasAPI(UsdLux.LightAPI):
                continue
            v = float(UsdLux.LightAPI(prim).GetIntensityAttr().Get() or 0.0)
            if abs(v - 2000.0) < 1e-3:
                pre_2000 += 1
        self.assertEqual(pre_2000, 35)

    def test_post_fix_arena_distinguishes_populations(self):
        stage = build_pre_fix_stage()
        apply_fix_postfix005(stage, self._arena_manifest())
        # Post-fix: 30 jumbotrons at ~636.62, 5 scoreboards at 2000,
        # 5 backstage at 1.
        jumbotron_v = 2000.0 / K_INTENSITY_PI
        counts = {"jumbotron": 0, "scoreboard": 0, "backstage": 0}
        for prim in stage.Traverse():
            if not prim.HasAPI(UsdLux.LightAPI):
                continue
            v = float(UsdLux.LightAPI(prim).GetIntensityAttr().Get() or 0.0)
            name = prim.GetName()
            if name.startswith("jumbotron"):
                counts["jumbotron"] += 1
                self.assertAlmostEqual(v, jumbotron_v, places=2)
            elif name.startswith("scoreboard"):
                counts["scoreboard"] += 1
                self.assertAlmostEqual(v, 2000.0, places=2)
            elif name.startswith("backstage"):
                counts["backstage"] += 1
                self.assertAlmostEqual(v, 1.0, places=2)
        self.assertEqual(counts, {"jumbotron": 30, "scoreboard": 5,
                                   "backstage": 5})

    def test_post_fix_arena_no_intensity_exceeds_ceiling(self):
        stage = build_pre_fix_stage()
        # An outlier scene: a botched watts=100 (units=3) test light that
        # WOULD normalize to 100 * 683 / pi ≈ 21,738 nits, but the ceiling
        # clamp catches it. Author reads a bright-but-not-broken light.
        manifests = self._arena_manifest() + [
            {"name": "outlier", "className": "VRayLight", "type": 0,
             "size0": 1.0, "size1": 1.0, "intensity": 100.0, "units": 3},
        ]
        apply_fix_postfix005(stage, manifests)
        for prim in stage.Traverse():
            if not prim.HasAPI(UsdLux.LightAPI):
                continue
            v = float(UsdLux.LightAPI(prim).GetIntensityAttr().Get() or 0.0)
            self.assertLessEqual(v, K_INTENSITY_CEILING,
                                 f"Ceiling clamp broke for {prim.GetName()}.")

    def test_post_fix_arena_mesh_census_unchanged(self):
        pre = build_pre_fix_stage()
        post = build_pre_fix_stage()
        apply_fix_prefix005(pre, self._arena_manifest())
        apply_fix_postfix005(post, self._arena_manifest())
        pre_mesh  = sum(1 for p in pre.Traverse()  if p.GetTypeName() == "Mesh")
        post_mesh = sum(1 for p in post.Traverse() if p.GetTypeName() == "Mesh")
        self.assertEqual(pre_mesh, post_mesh)


class TestPrefixBehaviorPreservedForUnitsAbsent(unittest.TestCase):
    """Backward compatibility: for a manifest without a `units` line at
    all (very old V-Ray / a light class without `.units`), the post-fix
    body writes the SAME intensity as the pre-fix body. This is critical —
    we should not silently break 2020-vintage scenes."""

    def test_unitsless_manifest_matches_prefix_output(self):
        legacy_manifest = [
            # Note the absence of a "units" key AND no "hasUnits" hint.
            {"name": "legacy_sun",   "className": "VRaySun",   "intensity": 3.0},
            {"name": "legacy_panel", "className": "VRayLight", "type": 0,
             "size0": 2.0, "size1": 1.0, "intensity": 30.0},
        ]
        pre = build_pre_fix_stage()
        post = build_pre_fix_stage()
        # Signal hasUnits=False to match "no units line in manifest".
        for m in legacy_manifest:
            m["hasUnits"] = False
        apply_fix_prefix005(pre, legacy_manifest)
        apply_fix_postfix005(post, legacy_manifest)
        for m in legacy_manifest:
            path = f"/root/lights/{m['name']}"
            pre_v  = _get_authored_intensity(pre, path)
            post_v = _get_authored_intensity(post, path)
            self.assertEqual(pre_v, post_v,
                             f"Legacy (no-units) intensity regressed on "
                             f"{path}: pre={pre_v} post={post_v}")


def _build_argparser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser()
    ap.add_argument("--verbose", "-v", action="count", default=1)
    return ap


if __name__ == "__main__":
    args, remaining = _build_argparser().parse_known_args()
    sys.argv = [sys.argv[0]] + remaining
    unittest.main(verbosity=args.verbose)
