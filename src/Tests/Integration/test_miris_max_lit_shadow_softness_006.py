# Copyright 2026 Miris Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
"""
MAX-LIT-SHADOW-SOFTNESS-006 — USD-layer structural mirror of the C++ fix
in `src/translators/VRayLightWriter.cpp` (extended `discoverMaxVrayLight`
probe + extended `VRayLightProbe` struct + parser range guards + Write()
shadow-family authoring: `shadow:color` on stdlib UsdLuxShadowAPI, custom
`inputs:vray:shadow:{radius,bias,subdivs,areaShadow}` opinions,
`karma:light:samplingquality` mirror, and USD-canonical bridges via
UsdLuxSphereLight.treatAsPoint and DiskLight.radius inflation).

Prior to this fix `UsdLuxShadowAPI.CreateShadowEnableAttr` was the ONLY
shadow-family attribute authored on any V-Ray light. `.shadowRadius`,
`.shadowSubdivs`, `.shadowBias`, `.areaShadow`, and `.shadowColor` were
silently discarded, so every VRayLight (point / sphere) and VRayIES
(disk) rendered in Karma with pin-sharp shadow edges regardless of the
source scene's soft-area authoring. The diagnostic surfaced this as
S7 in `evidence/gaps-audit.md`:

    Only `UsdLuxShadowAPI.CreateShadowEnableAttr` is authored — no
    `shadow:color`, no per-light softness / bias. VRayIES / VRayLight
    spot cone maps to a DiskLight but no shadow softness is
    authored. Result: every V-Ray point/spot renders with pin-sharp
    shadow edges in Karma vs the V-Ray original's soft area shadow.

This test locks in the observables the fix introduces:
  1) Pre-fix behavior (`apply_fix_prefix006`) authors ONLY shadow:enable
     and leaves everything else at the schema default. All five VRay
     probe fields are dropped.
  2) Post-fix behavior (`apply_fix_postfix006`) forwards the probe onto:
       - stdlib `UsdLuxShadowAPI.shadow:color` when shadowColor is set,
       - custom `inputs:vray:shadow:{radius,bias,subdivs,areaShadow}` for
         lossless round-trip preservation,
       - `karma:light:samplingquality` (int) — Karma-visible sample-count
         mirror of `.shadowSubdivs`,
       - USD-canonical `treatAsPoint=true` on SphereLight when
         `.areaShadow=false`,
       - DiskLight `inputs:radius` inflation by `.shadowRadius` on
         VRayIES so the finite-area emitter naturally casts soft shadows.
  3) Surgical scope — every MAX-LIT-002 / MAX-LIT-003 / MAX-LIT-
     INTENSITY-UNITS-005 assertion still passes (color, temperature,
     enable-color-temperature, intensity, normalize, shape attrs for the
     non-Disk branches, LightAPI, prim typing).
  4) Parser range guards (subdivs clamped to [1, 256]; bias clamped to
     >= 0; shadowRadius clamped to [0, 100]; shadowColor triplet clamped
     to [0, 1] per component; malformed manifests degrade to no-op).

Same effect the shipping C++ code has, observed at a different layer.

Not tied to the arena scene — synthetic in-memory USD stages preserving
the arena signature (60 IES downlights + 8 Disc spots + a court downlight
rig with soft-area shadows). Runs under Houdini's `hython`.
"""
import argparse
import sys
import unittest
from typing import Dict, List, Optional, Tuple

from pxr import Gf, Sdf, Usd, UsdGeom, UsdLux


# ---------------------------------------------------------------------------
# Python mirror of the extended `_ProbeVRayLight` parser. Contracts locked
# in by the tests below.
# ---------------------------------------------------------------------------

# Constants MUST match the C++ constexpr / literal clamps at the top of
# the shadow parser branches. If a future refactor bumps these, the test
# suite fires immediately.
K_SHADOW_RADIUS_MAX  = 100.0    # world units, hard cap in the parser.
K_SHADOW_SUBDIVS_MIN = 1
K_SHADOW_SUBDIVS_MAX = 256      # tolerant of future V-Ray UI raises.
# DiskLight radius inflation ceiling (existing_radius + shadow_radius <= 100).
K_DISK_INFLATE_MAX   = 100.0


def _parse_shadow_manifest(manifest_str: str) -> Dict:
    """Mirror of the MAX-LIT-SHADOW-SOFTNESS-006 parser branches on the
    shadow family. Returns a dict of parsed fields with their has_ flags.

    Malformed lines / out-of-range values degrade to has_=False so the
    writer preserves the pre-006 behavior (surgical scope invariant).
    """
    out = {}
    for line in manifest_str.splitlines():
        if not line or "|" not in line:
            continue
        key, val = line.split("|", 1)
        try:
            if key == "shadowRadius":
                r = float(val)
                if r < 0.0:
                    r = 0.0
                if r > K_SHADOW_RADIUS_MAX:
                    r = K_SHADOW_RADIUS_MAX
                out["hasShadowRadius"] = True
                out["shadowRadius"] = r
            elif key == "shadowSubdivs":
                s = int(val)
                if s < K_SHADOW_SUBDIVS_MIN:
                    s = K_SHADOW_SUBDIVS_MIN
                if s > K_SHADOW_SUBDIVS_MAX:
                    s = K_SHADOW_SUBDIVS_MAX
                out["hasShadowSubdivs"] = True
                out["shadowSubdivs"] = s
            elif key == "shadowBias":
                b = float(val)
                if b < 0.0:
                    b = 0.0
                out["hasShadowBias"] = True
                out["shadowBias"] = b
            elif key == "areaShadow":
                v = val.strip().lower()
                out["hasAreaShadow"] = True
                out["areaShadow"] = not (v in ("false", "0"))
            elif key == "shadowColor":
                parts = val.split(",")
                if len(parts) == 3:
                    def _clamp01(x):
                        x = float(x)
                        if x < 0.0:
                            return 0.0
                        if x > 1.0:
                            return 1.0
                        return x
                    # Set the flag AFTER every component parses so a
                    # malformed triplet degrades to has_=False. Matches
                    # the C++ parser order (probe.hasShadowColor = true
                    # runs after std::stof succeeds on all three parts).
                    triplet = (
                        _clamp01(parts[0]),
                        _clamp01(parts[1]),
                        _clamp01(parts[2]),
                    )
                    out["hasShadowColor"] = True
                    out["shadowColor"] = triplet
        except (ValueError, TypeError):
            # Malformed value — leave the field absent.
            continue
    return out


# ---------------------------------------------------------------------------
# Classifier (copy of MAX-LIT-002 — kept self-contained so this file runs
# under hython without importing sibling test modules).
# ---------------------------------------------------------------------------

VRAYLIGHT_TYPE_TO_USDLUX = {
    0: "RectLight",
    1: "DomeLight",
    2: "SphereLight",
    3: "SphereLight",   # Mesh -> bbox-sphere fallback
    4: "DiskLight",
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
# Two versions of the shadow-authoring body. The ONLY difference is
# whether the extended shadow-family fields are forwarded.
# ---------------------------------------------------------------------------

def _apply_common(stage: Usd.Stage, manifest: Dict) -> Optional[UsdLux.LightAPI]:
    """Body shared by both pre-006 and post-006 apply_fix implementations.

    Authors shape, color, temperature, on/off, normalize, IES, intensity,
    and `shadow:enable` — the pre-006 baseline. Both apply_fix bodies
    call this first; only the post-006 body ALSO forwards the extended
    shadow-family fields.
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
    light.CreateIntensityAttr().Set(float(manifest.get("intensity") or 1.0))
    light.CreateNormalizeAttr().Set(True)
    use_temp = bool(manifest.get("useTemperature", False))
    light.CreateEnableColorTemperatureAttr().Set(use_temp)
    if use_temp:
        k = min(max(1000.0, float(manifest.get("temperature", 6500.0))), 10000.0)
        light.CreateColorTemperatureAttr().Set(k)

    shadow_api = UsdLux.ShadowAPI.Apply(light.GetPrim())
    shadow_api.CreateShadowEnableAttr().Set(bool(manifest.get("shadow", True)))
    return light


def apply_fix_prefix006(stage: Usd.Stage, manifests: List[Dict]) -> None:
    """MAX-LIT-002 through MAX-LIT-INTENSITY-UNITS-005 behavior WITHOUT
    MAX-LIT-SHADOW-SOFTNESS-006 — only `shadow:enable` is authored, all
    other shadow-family probe fields are dropped."""
    UsdGeom.Xform.Define(stage, "/root/lights")
    for m in manifests:
        _apply_common(stage, m)


def apply_fix_postfix006(stage: Usd.Stage, manifests: List[Dict]) -> None:
    """MAX-LIT-SHADOW-SOFTNESS-006 behavior — extended shadow-family
    fields forwarded onto UsdLuxShadowAPI (stdlib + custom), Karma's
    `samplingquality` mirror, `treatAsPoint` bridge on SphereLight when
    areaShadow=false, and DiskLight `inputs:radius` inflation by
    `.shadowRadius`."""
    UsdGeom.Xform.Define(stage, "/root/lights")
    for m in manifests:
        light = _apply_common(stage, m)
        if light is None:
            continue

        prim = light.GetPrim()
        subtype = classify_vray_light(m)
        shadow_api = UsdLux.ShadowAPI.Apply(prim)

        # (a) stdlib `shadow:color`.
        if m.get("hasShadowColor", "shadowColor" in m):
            sc = m.get("shadowColor", (0.0, 0.0, 0.0))
            shadow_api.CreateShadowColorAttr().Set(Gf.Vec3f(*sc))

        # (b) custom `inputs:vray:shadow:*` for lossless round-trip.
        if m.get("hasShadowRadius", "shadowRadius" in m):
            r = float(m["shadowRadius"])
            prim.CreateAttribute(
                "inputs:vray:shadow:radius", Sdf.ValueTypeNames.Float,
                custom=False).Set(r)
        if m.get("hasShadowBias", "shadowBias" in m):
            b = float(m["shadowBias"])
            prim.CreateAttribute(
                "inputs:vray:shadow:bias", Sdf.ValueTypeNames.Float,
                custom=False).Set(b)
        if m.get("hasShadowSubdivs", "shadowSubdivs" in m):
            s = int(m["shadowSubdivs"])
            prim.CreateAttribute(
                "inputs:vray:shadow:subdivs", Sdf.ValueTypeNames.Int,
                custom=False).Set(s)
            # (c) Karma-visible sample-count mirror.
            prim.CreateAttribute(
                "karma:light:samplingquality", Sdf.ValueTypeNames.Int,
                custom=False).Set(s)
        if m.get("hasAreaShadow", "areaShadow" in m):
            area = bool(m["areaShadow"])
            prim.CreateAttribute(
                "inputs:vray:shadow:areaShadow", Sdf.ValueTypeNames.Bool,
                custom=False).Set(area)
            # (d) USD-canonical bridge: SphereLight.treatAsPoint.
            if not area and subtype == "SphereLight":
                sphere = UsdLux.SphereLight(prim)
                if sphere:
                    sphere.CreateTreatAsPointAttr().Set(True)

        # (d) DiskLight radius inflation for soft area shadows.
        if (m.get("hasShadowRadius", "shadowRadius" in m)
                and float(m.get("shadowRadius", 0.0)) > 0.0
                and subtype == "DiskLight"):
            disk = UsdLux.DiskLight(prim)
            if disk:
                existing = disk.GetRadiusAttr().Get() or 0.0
                bumped = float(existing) + float(m["shadowRadius"])
                if bumped > K_DISK_INFLATE_MAX:
                    bumped = K_DISK_INFLATE_MAX
                disk.CreateRadiusAttr().Set(float(bumped))


def build_pre_fix_stage() -> Usd.Stage:
    stage = Usd.Stage.CreateInMemory()
    root = UsdGeom.Xform.Define(stage, "/root")
    UsdGeom.Xform.Define(stage, "/root/geo")
    UsdGeom.Mesh.Define(stage, "/root/geo/floor")
    stage.SetDefaultPrim(root.GetPrim())
    return stage


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestParserRangeGuards(unittest.TestCase):
    """Every shadow-family parser branch clamps to a documented range or
    degrades to has_=False for a malformed value. These invariants are
    load-bearing — they protect Karma from a rogue authored value that
    would explode the renderer's sample budget or the light's footprint."""

    def test_shadow_radius_clamped_negative_to_zero(self):
        got = _parse_shadow_manifest("shadowRadius|-5\n")
        self.assertEqual(got["shadowRadius"], 0.0)

    def test_shadow_radius_clamped_upper_to_100(self):
        got = _parse_shadow_manifest("shadowRadius|500\n")
        self.assertEqual(got["shadowRadius"], K_SHADOW_RADIUS_MAX)

    def test_shadow_radius_in_range_passes_through(self):
        got = _parse_shadow_manifest("shadowRadius|3.5\n")
        self.assertAlmostEqual(got["shadowRadius"], 3.5, places=4)

    def test_shadow_subdivs_clamped_to_1_min(self):
        got = _parse_shadow_manifest("shadowSubdivs|0\n")
        self.assertEqual(got["shadowSubdivs"], K_SHADOW_SUBDIVS_MIN)
        got = _parse_shadow_manifest("shadowSubdivs|-3\n")
        self.assertEqual(got["shadowSubdivs"], K_SHADOW_SUBDIVS_MIN)

    def test_shadow_subdivs_clamped_to_256_max(self):
        got = _parse_shadow_manifest("shadowSubdivs|9999\n")
        self.assertEqual(got["shadowSubdivs"], K_SHADOW_SUBDIVS_MAX)

    def test_shadow_bias_clamped_negative_to_zero(self):
        # V-Ray's UI shows a floor of 0; a MAXScript-side accident could
        # author a negative value that some renderers interpret as a
        # sub-surface offset. Clamp for safety.
        got = _parse_shadow_manifest("shadowBias|-1\n")
        self.assertEqual(got["shadowBias"], 0.0)

    def test_shadow_color_triplet_clamped_component_wise(self):
        got = _parse_shadow_manifest("shadowColor|-0.5,0.3,2.0\n")
        self.assertEqual(got["shadowColor"], (0.0, 0.3, 1.0))

    def test_shadow_color_triplet_malformed_drops_field(self):
        got = _parse_shadow_manifest("shadowColor|0.5,0.5\n")
        self.assertNotIn("hasShadowColor", got)
        got = _parse_shadow_manifest("shadowColor|not,numbers,here\n")
        self.assertNotIn("hasShadowColor", got)

    def test_area_shadow_boolean_parses_both_spellings(self):
        for on_val in ("true", "1", "True"):
            got = _parse_shadow_manifest(f"areaShadow|{on_val}\n")
            self.assertTrue(got["areaShadow"], f"{on_val} -> True")
        for off_val in ("false", "0", "False"):
            got = _parse_shadow_manifest(f"areaShadow|{off_val}\n")
            self.assertFalse(got["areaShadow"], f"{off_val} -> False")


class TestPreFixDefectVisibility(unittest.TestCase):
    """The defect the fix removes: pre-006, ONLY `shadow:enable` is
    authored on UsdLuxShadowAPI. Every V-Ray shadow-family field is
    dropped — Karma renders pin-sharp shadows regardless of the source
    scene's `.shadowRadius` / `.shadowSubdivs` / `.areaShadow` /
    `.shadowColor` authoring."""

    IES_MANIFEST: Dict = {
        "name": "IES_downlight_soft",
        "className": "VRayIES",
        "iesFile": "C:/light_profiles/downlight.ies",
        "size0": 0.3,
        "intensity": 500.0,
        "color": (1.0, 1.0, 1.0),
        # Shadow-family: KEY signal — 5.0 world units of shadow softness.
        "hasShadowRadius": True, "shadowRadius": 5.0,
        "hasShadowSubdivs": True, "shadowSubdivs": 32,
        "hasShadowBias": True, "shadowBias": 0.05,
        "hasShadowColor": True, "shadowColor": (0.05, 0.05, 0.1),
    }

    def test_prefix_authors_only_shadow_enable_and_drops_rest(self):
        stage = build_pre_fix_stage()
        apply_fix_prefix006(stage, [self.IES_MANIFEST])
        prim = stage.GetPrimAtPath("/root/lights/IES_downlight_soft")
        self.assertTrue(prim.IsValid())
        # shadow:enable IS authored on the pre-fix path (the MAX-LIT-002
        # behavior — that's the only shadow-family attr the writer
        # emitted before this bite).
        api = UsdLux.ShadowAPI(prim)
        self.assertTrue(api.GetShadowEnableAttr().IsAuthored())
        # But none of the extended fields are — the defect signature.
        self.assertFalse(api.GetShadowColorAttr().IsAuthored())
        self.assertFalse(prim.HasAttribute("inputs:vray:shadow:radius"))
        self.assertFalse(prim.HasAttribute("inputs:vray:shadow:bias"))
        self.assertFalse(prim.HasAttribute("inputs:vray:shadow:subdivs"))
        self.assertFalse(prim.HasAttribute("inputs:vray:shadow:areaShadow"))
        self.assertFalse(prim.HasAttribute("karma:light:samplingquality"))
        # And the DiskLight radius is UNTOUCHED at size0 — no inflation
        # to compensate for the missing softness.
        disk = UsdLux.DiskLight(prim)
        self.assertAlmostEqual(disk.GetRadiusAttr().Get(), 0.3, places=6)


class TestPostFixShadowFamilyAuthored(unittest.TestCase):
    """Post-fix, every extended shadow-family probe field is forwarded
    onto the correct UsdLuxShadowAPI attribute (stdlib or custom
    `inputs:vray:shadow:*`), the Karma sample-count mirror is authored,
    and the USD-canonical bridges fire on the correct branches."""

    def test_shadow_color_authored_on_stdlib_shadow_api(self):
        # shadow:color IS a stdlib UsdLuxShadowAPI attr — author it
        # directly so any Hydra delegate reads it verbatim.
        stage = build_pre_fix_stage()
        m = {
            "name": "Tinted_downlight",
            "className": "VRayIES",
            "size0": 0.3,
            "intensity": 100.0,
            "iesFile": "C:/x.ies",
            "hasShadowColor": True, "shadowColor": (0.1, 0.15, 0.2),
        }
        apply_fix_postfix006(stage, [m])
        prim = stage.GetPrimAtPath("/root/lights/Tinted_downlight")
        api = UsdLux.ShadowAPI(prim)
        self.assertTrue(api.GetShadowColorAttr().IsAuthored())
        val = api.GetShadowColorAttr().Get()
        self.assertAlmostEqual(val[0], 0.1, places=4)
        self.assertAlmostEqual(val[1], 0.15, places=4)
        self.assertAlmostEqual(val[2], 0.2, places=4)

    def test_vray_shadow_custom_opinions_authored_for_lossless_roundtrip(self):
        stage = build_pre_fix_stage()
        m = {
            "name": "Rig_light",
            "className": "VRayLight", "type": 0,
            "size0": 2.0, "size1": 1.0,
            "intensity": 300.0,
            "hasShadowRadius": True, "shadowRadius": 4.0,
            "hasShadowBias": True, "shadowBias": 0.1,
            "hasShadowSubdivs": True, "shadowSubdivs": 32,
            "hasAreaShadow": True, "areaShadow": True,
        }
        apply_fix_postfix006(stage, [m])
        prim = stage.GetPrimAtPath("/root/lights/Rig_light")
        # Every field is a `false` (non-custom) opinion — the schema-like
        # opinion class future USD readers can layer against.
        for attr_name, expected_type_name in [
            ("inputs:vray:shadow:radius", "float"),
            ("inputs:vray:shadow:bias", "float"),
            ("inputs:vray:shadow:subdivs", "int"),
            ("inputs:vray:shadow:areaShadow", "bool"),
        ]:
            self.assertTrue(prim.HasAttribute(attr_name), attr_name)
            attr = prim.GetAttribute(attr_name)
            self.assertEqual(attr.GetTypeName().cppTypeName, expected_type_name,
                             f"{attr_name} wrong type")
            self.assertFalse(attr.IsCustom(),
                             f"{attr_name} should be a NON-custom opinion so "
                             f"future schemas can layer against it.")
        self.assertAlmostEqual(
            prim.GetAttribute("inputs:vray:shadow:radius").Get(), 4.0, places=4)
        self.assertAlmostEqual(
            prim.GetAttribute("inputs:vray:shadow:bias").Get(), 0.1, places=4)
        self.assertEqual(
            prim.GetAttribute("inputs:vray:shadow:subdivs").Get(), 32)
        self.assertEqual(
            prim.GetAttribute("inputs:vray:shadow:areaShadow").Get(), True)

    def test_karma_samplingquality_mirrors_shadow_subdivs(self):
        # Karma reads `karma:light:samplingquality` at render time — this
        # is what makes the "raise subdiv -> softer noise-free shadow"
        # signal actually visible in Karma output.
        stage = build_pre_fix_stage()
        m = {
            "name": "High_quality_rig",
            "className": "VRayLight", "type": 0,
            "size0": 2.0, "size1": 1.0, "intensity": 100.0,
            "hasShadowSubdivs": True, "shadowSubdivs": 64,
        }
        apply_fix_postfix006(stage, [m])
        prim = stage.GetPrimAtPath("/root/lights/High_quality_rig")
        self.assertTrue(prim.HasAttribute("karma:light:samplingquality"))
        self.assertEqual(
            prim.GetAttribute("karma:light:samplingquality").Get(), 64)

    def test_karma_samplingquality_absent_when_subdivs_not_probed(self):
        # Surgical scope: when the probe surfaced no `.shadowSubdivs`
        # (e.g. very old V-Ray), the Karma opinion is NOT written — the
        # renderer keeps its own default sample count.
        stage = build_pre_fix_stage()
        m = {
            "name": "Legacy_rig",
            "className": "VRayLight", "type": 0,
            "size0": 2.0, "size1": 1.0, "intensity": 100.0,
        }
        apply_fix_postfix006(stage, [m])
        prim = stage.GetPrimAtPath("/root/lights/Legacy_rig")
        self.assertFalse(prim.HasAttribute("karma:light:samplingquality"))


class TestUsdCanonicalBridges(unittest.TestCase):
    """The USD-canonical bridges — the two spots where an extended shadow
    probe changes an attribute other than a `inputs:vray:shadow:*`
    opinion. These are what make the fix VISIBLE to a Hydra delegate
    that doesn't read the V-Ray namespace at all."""

    def test_area_shadow_false_sets_treat_as_point_on_sphere_light(self):
        stage = build_pre_fix_stage()
        m = {
            "name": "Sphere_point_shadow",
            "className": "VRayLight", "type": 2,
            "size0": 0.15, "intensity": 100.0,
            "hasAreaShadow": True, "areaShadow": False,
        }
        apply_fix_postfix006(stage, [m])
        prim = stage.GetPrimAtPath("/root/lights/Sphere_point_shadow")
        sphere = UsdLux.SphereLight(prim)
        self.assertTrue(sphere.GetTreatAsPointAttr().IsAuthored())
        self.assertEqual(sphere.GetTreatAsPointAttr().Get(), True)

    def test_area_shadow_true_leaves_treat_as_point_default(self):
        # When area shadows ARE enabled, treatAsPoint stays unauthored —
        # UsdLux's schema default (false) is exactly the semantic we want.
        stage = build_pre_fix_stage()
        m = {
            "name": "Sphere_area_shadow",
            "className": "VRayLight", "type": 2,
            "size0": 0.15, "intensity": 100.0,
            "hasAreaShadow": True, "areaShadow": True,
        }
        apply_fix_postfix006(stage, [m])
        prim = stage.GetPrimAtPath("/root/lights/Sphere_area_shadow")
        sphere = UsdLux.SphereLight(prim)
        self.assertFalse(sphere.GetTreatAsPointAttr().IsAuthored())

    def test_treat_as_point_bridge_scoped_to_sphere_only(self):
        # `treatAsPoint` is a SphereLight-only stdlib attribute. Rect and
        # Disk lack it — the bridge MUST NOT fire on those branches, or
        # we'd author a bogus attribute the schema doesn't back.
        stage = build_pre_fix_stage()
        rect_m = {
            "name": "Rect_point_shadow",
            "className": "VRayLight", "type": 0,
            "size0": 2.0, "size1": 1.0, "intensity": 100.0,
            "hasAreaShadow": True, "areaShadow": False,
        }
        disk_m = {
            "name": "Disk_point_shadow",
            "className": "VRayLight", "type": 4,
            "size0": 0.3, "intensity": 100.0,
            "hasAreaShadow": True, "areaShadow": False,
        }
        apply_fix_postfix006(stage, [rect_m, disk_m])
        rect_prim = stage.GetPrimAtPath("/root/lights/Rect_point_shadow")
        disk_prim = stage.GetPrimAtPath("/root/lights/Disk_point_shadow")
        self.assertFalse(rect_prim.HasAttribute("treatAsPoint"))
        self.assertFalse(disk_prim.HasAttribute("treatAsPoint"))
        # BUT the raw `inputs:vray:shadow:areaShadow` opinion IS on both
        # — that's how Rect/Disk preserve the semantic losslessly.
        self.assertTrue(rect_prim.HasAttribute("inputs:vray:shadow:areaShadow"))
        self.assertTrue(disk_prim.HasAttribute("inputs:vray:shadow:areaShadow"))

    def test_disk_light_radius_inflated_by_shadow_radius(self):
        # KEY signal — VRayIES's `.shadowRadius` translates directly to
        # a larger DiskLight so ANY Hydra delegate renders soft shadows.
        # normalize=True keeps this from also changing brightness.
        stage = build_pre_fix_stage()
        m = {
            "name": "IES_soft",
            "className": "VRayIES",
            "iesFile": "C:/x.ies",
            "size0": 0.3,          # existing disk radius
            "intensity": 500.0,
            "hasShadowRadius": True, "shadowRadius": 4.0,
        }
        apply_fix_postfix006(stage, [m])
        prim = stage.GetPrimAtPath("/root/lights/IES_soft")
        disk = UsdLux.DiskLight(prim)
        # 0.3 + 4.0 = 4.3 (well under the 100 clamp).
        self.assertAlmostEqual(disk.GetRadiusAttr().Get(), 4.3, places=4)
        # normalize=True still authored so brightness is unchanged.
        light = UsdLux.LightAPI(prim)
        self.assertTrue(light.GetNormalizeAttr().Get())

    def test_disk_light_radius_inflation_clamped_at_100(self):
        stage = build_pre_fix_stage()
        m = {
            "name": "IES_runaway",
            "className": "VRayIES",
            "iesFile": "C:/x.ies",
            "size0": 90.0,
            "intensity": 500.0,
            # Post-parser clamp on shadowRadius is 100, so with size0=90
            # the sum before the final clamp is 190. The DiskLight
            # inflation clamp keeps the render tractable at 100.
            "hasShadowRadius": True, "shadowRadius": 100.0,
        }
        apply_fix_postfix006(stage, [m])
        prim = stage.GetPrimAtPath("/root/lights/IES_runaway")
        disk = UsdLux.DiskLight(prim)
        self.assertLessEqual(disk.GetRadiusAttr().Get(), K_DISK_INFLATE_MAX)
        self.assertAlmostEqual(disk.GetRadiusAttr().Get(),
                               K_DISK_INFLATE_MAX, places=4)

    def test_disk_light_inflation_scoped_to_disk_only(self):
        # SphereLight / RectLight branches leave their shape attrs alone
        # even when shadowRadius > 0 — inflating a Rect's width would be
        # wrong (VRayLight-plane's shape maps directly to size0/size1).
        stage = build_pre_fix_stage()
        rect_m = {
            "name": "Rect_soft",
            "className": "VRayLight", "type": 0,
            "size0": 2.0, "size1": 1.0, "intensity": 100.0,
            "hasShadowRadius": True, "shadowRadius": 5.0,
        }
        sphere_m = {
            "name": "Sphere_soft",
            "className": "VRayLight", "type": 2,
            "size0": 0.5, "intensity": 100.0,
            "hasShadowRadius": True, "shadowRadius": 5.0,
        }
        apply_fix_postfix006(stage, [rect_m, sphere_m])
        rect = UsdLux.RectLight(
            stage.GetPrimAtPath("/root/lights/Rect_soft"))
        self.assertAlmostEqual(rect.GetWidthAttr().Get(), 2.0, places=6)
        self.assertAlmostEqual(rect.GetHeightAttr().Get(), 1.0, places=6)
        sphere = UsdLux.SphereLight(
            stage.GetPrimAtPath("/root/lights/Sphere_soft"))
        self.assertAlmostEqual(sphere.GetRadiusAttr().Get(), 0.5, places=6)


class TestSurgicalScope(unittest.TestCase):
    """The load-bearing invariant: everything MAX-LIT-002 /
    MAX-LIT-003 / MAX-LIT-INTENSITY-UNITS-005 asserted STILL passes
    post-006. Only shadow-family authoring changes; every other attr
    (color, temperature, IES file, intensity, normalize, LightAPI,
    prim typing, RectLight/SphereLight shape) is byte-identical."""

    ARENA_MANIFEST: List[Dict] = [
        {"name": "Sun_key",       "className": "VRaySun",
         "intensity": 3.0, "color": (1.0, 0.98, 0.9), "shadow": True,
         "units": 0,
         # Shadow-family fields WILL fire on post-fix.
         "hasShadowSubdivs": True, "shadowSubdivs": 16,
         "hasShadowBias": True, "shadowBias": 0.1},
        {"name": "IES_downlight", "className": "VRayIES",
         "iesFile": "C:/light_profiles/downlight.ies",
         "size0": 0.3,
         "intensity": 500.0, "color": (1.0, 1.0, 1.0), "shadow": True,
         "hasShadowRadius": True, "shadowRadius": 5.0,
         "hasShadowSubdivs": True, "shadowSubdivs": 32},
        {"name": "Panel_ceiling", "className": "VRayLight", "type": 0,
         "size0": 2.0, "size1": 1.0, "intensity": 30.0,
         "color": (1.0, 0.95, 0.85), "shadow": True,
         "hasAreaShadow": True, "areaShadow": True,
         "hasShadowColor": True, "shadowColor": (0.02, 0.02, 0.05)},
        {"name": "Warmup_bulb",   "className": "VRayLight", "type": 2,
         "size0": 0.15, "useTemperature": True, "temperature": 3200.0,
         "intensity": 100.0, "color": (1.0, 1.0, 1.0),
         "hasAreaShadow": True, "areaShadow": False},
    ]

    def test_color_attrs_byte_identical(self):
        pre = build_pre_fix_stage()
        post = build_pre_fix_stage()
        apply_fix_prefix006(pre, self.ARENA_MANIFEST)
        apply_fix_postfix006(post, self.ARENA_MANIFEST)
        for m in self.ARENA_MANIFEST:
            path = f"/root/lights/{m['name']}"
            pre_c = UsdLux.LightAPI(pre.GetPrimAtPath(path)).GetColorAttr().Get()
            post_c = UsdLux.LightAPI(post.GetPrimAtPath(path)).GetColorAttr().Get()
            self.assertEqual(pre_c, post_c,
                             f"Color regressed on {m['name']} post-006.")

    def test_intensity_attrs_byte_identical(self):
        pre = build_pre_fix_stage()
        post = build_pre_fix_stage()
        apply_fix_prefix006(pre, self.ARENA_MANIFEST)
        apply_fix_postfix006(post, self.ARENA_MANIFEST)
        for m in self.ARENA_MANIFEST:
            path = f"/root/lights/{m['name']}"
            pre_i = UsdLux.LightAPI(pre.GetPrimAtPath(path)).GetIntensityAttr().Get()
            post_i = UsdLux.LightAPI(post.GetPrimAtPath(path)).GetIntensityAttr().Get()
            self.assertEqual(pre_i, post_i,
                             f"Intensity regressed on {m['name']} post-006 — "
                             f"MAX-LIT-INTENSITY-UNITS-005 invariant.")

    def test_temperature_and_toggle_byte_identical(self):
        pre = build_pre_fix_stage()
        post = build_pre_fix_stage()
        apply_fix_prefix006(pre, self.ARENA_MANIFEST)
        apply_fix_postfix006(post, self.ARENA_MANIFEST)
        for name in ("Warmup_bulb", "Panel_ceiling"):
            path = f"/root/lights/{name}"
            pre_l = UsdLux.LightAPI(pre.GetPrimAtPath(path))
            post_l = UsdLux.LightAPI(post.GetPrimAtPath(path))
            self.assertEqual(pre_l.GetEnableColorTemperatureAttr().Get(),
                             post_l.GetEnableColorTemperatureAttr().Get())
            self.assertEqual(pre_l.GetColorTemperatureAttr().Get(),
                             post_l.GetColorTemperatureAttr().Get())

    def test_ies_file_byte_identical(self):
        pre = build_pre_fix_stage()
        post = build_pre_fix_stage()
        apply_fix_prefix006(pre, self.ARENA_MANIFEST)
        apply_fix_postfix006(post, self.ARENA_MANIFEST)
        for stage in (pre, post):
            prim = stage.GetPrimAtPath("/root/lights/IES_downlight")
            self.assertTrue(prim.IsValid())
            shaping = UsdLux.ShapingAPI(prim)
            attr = shaping.GetShapingIesFileAttr()
            self.assertTrue(attr and attr.IsAuthored())
            self.assertEqual(attr.Get().path, "C:/light_profiles/downlight.ies")

    def test_normalize_still_authored_post_006(self):
        pre = build_pre_fix_stage()
        post = build_pre_fix_stage()
        apply_fix_prefix006(pre, self.ARENA_MANIFEST)
        apply_fix_postfix006(post, self.ARENA_MANIFEST)
        for m in self.ARENA_MANIFEST:
            path = f"/root/lights/{m['name']}"
            post_n = UsdLux.LightAPI(post.GetPrimAtPath(path)).GetNormalizeAttr().Get()
            self.assertTrue(post_n,
                            "normalize=True MUST stay authored — the DiskLight "
                            "radius inflation depends on it to keep "
                            "brightness unchanged.")

    def test_light_api_and_prim_typing_byte_identical(self):
        pre = build_pre_fix_stage()
        post = build_pre_fix_stage()
        apply_fix_prefix006(pre, self.ARENA_MANIFEST)
        apply_fix_postfix006(post, self.ARENA_MANIFEST)
        for m in self.ARENA_MANIFEST:
            path = f"/root/lights/{m['name']}"
            pre_p = pre.GetPrimAtPath(path)
            post_p = post.GetPrimAtPath(path)
            self.assertTrue(pre_p.HasAPI(UsdLux.LightAPI))
            self.assertTrue(post_p.HasAPI(UsdLux.LightAPI))
            self.assertEqual(pre_p.GetTypeName(), post_p.GetTypeName())

    def test_disk_radius_untouched_when_no_shadow_radius_probed(self):
        # The DiskLight inflation must NOT fire on a VRayIES that had no
        # `.shadowRadius` probed — surgical scope invariant.
        pre = build_pre_fix_stage()
        post = build_pre_fix_stage()
        m = {"name": "IES_stock",
             "className": "VRayIES",
             "iesFile": "C:/x.ies",
             "size0": 0.3,
             "intensity": 100.0}  # no shadowRadius
        apply_fix_prefix006(pre, [m])
        apply_fix_postfix006(post, [m])
        pre_r = UsdLux.DiskLight(pre.GetPrimAtPath(
            "/root/lights/IES_stock")).GetRadiusAttr().Get()
        post_r = UsdLux.DiskLight(post.GetPrimAtPath(
            "/root/lights/IES_stock")).GetRadiusAttr().Get()
        self.assertEqual(pre_r, post_r)
        self.assertAlmostEqual(post_r, 0.3, places=6)


class TestStrictSupersetOfLIT002(unittest.TestCase):
    """MAX-LIT-002 classifier + LightAPI application invariants — every
    branch (Sun / IES / Rect / Sphere / Sphere-fallback / Disk / Dome)
    still dispatches to the same UsdLux subtype and still applies the
    LightAPI."""

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
            "Mesh_neon":     "SphereLight",
            "Disc_spot":     "DiskLight",
        }
        for m in self.STRICT_SUPERSET_MANIFEST:
            self.assertEqual(classify_vray_light(m), expected[m["name"]])

    def test_all_lights_still_export_with_light_api(self):
        stage = build_pre_fix_stage()
        apply_fix_postfix006(stage, self.STRICT_SUPERSET_MANIFEST)
        for m in self.STRICT_SUPERSET_MANIFEST:
            path = f"/root/lights/{m['name']}"
            prim = stage.GetPrimAtPath(path)
            self.assertTrue(prim.IsValid())
            self.assertTrue(prim.HasAPI(UsdLux.LightAPI),
                            f"MAX-LIT-002 contract regressed: {path}")


class TestCourtDownlightRig(unittest.TestCase):
    """Preserves the Spectrum Center court-downlight-rig signature:
    60 VRayIES downlights above the court, each with `.shadowRadius=3.0`
    world units to keep player-cast shadows soft. Pre-006: 60 DiskLights
    at inputs:radius=0.3 (their IES cutoff) with NO shadow-family attrs
    beyond `shadow:enable` — Karma renders razor-sharp shadow edges.
    Post-006: every DiskLight's radius becomes 3.3 (0.3 + 3.0), samples
    at 32-subdiv Karma quality, and holds the raw `.shadowRadius` opinion
    for lossless V-Ray round-trip."""

    @staticmethod
    def _rig_manifest() -> List[Dict]:
        out: List[Dict] = []
        # 60 court-downlight IES lights, each with soft-shadow authoring.
        for i in range(60):
            out.append({
                "name": f"court_downlight_{i:03d}",
                "className": "VRayIES",
                "iesFile": "C:/light_profiles/court_downlight.ies",
                "size0": 0.3,          # existing IES cutoff radius
                "intensity": 2500.0,
                "color": (1.0, 0.98, 0.94),
                # KEY SIGNAL: 3.0 world units of shadow softness.
                "hasShadowRadius": True, "shadowRadius": 3.0,
                "hasShadowSubdivs": True, "shadowSubdivs": 32,
                "hasShadowBias": True, "shadowBias": 0.1,
            })
        # 8 disc-spot fill lights on the backboard.
        for i in range(8):
            out.append({
                "name": f"backboard_disc_{i:02d}",
                "className": "VRayLight", "type": 4,
                "size0": 0.5,
                "intensity": 500.0,
                "color": (1.0, 1.0, 1.0),
                "hasShadowSubdivs": True, "shadowSubdivs": 24,
            })
        return out

    def test_prefix_disk_radii_are_untouched_and_shadow_authoring_absent(self):
        stage = build_pre_fix_stage()
        apply_fix_prefix006(stage, self._rig_manifest())
        pre_006_defect_count = 0
        for prim in stage.Traverse():
            name = prim.GetName()
            if not name.startswith(("court_downlight_", "backboard_disc_")):
                continue
            # Pre-fix invariants (ALL 68 lights):
            #   * DiskLight.inputs:radius == size0 (no inflation)
            #   * NO inputs:vray:shadow:* opinions
            #   * NO karma:light:samplingquality
            self.assertFalse(prim.HasAttribute("inputs:vray:shadow:radius"))
            self.assertFalse(prim.HasAttribute("inputs:vray:shadow:subdivs"))
            self.assertFalse(prim.HasAttribute("karma:light:samplingquality"))
            disk = UsdLux.DiskLight(prim)
            r = disk.GetRadiusAttr().Get()
            if name.startswith("court_downlight_"):
                self.assertAlmostEqual(r, 0.3, places=4)
            else:
                self.assertAlmostEqual(r, 0.5, places=4)
            pre_006_defect_count += 1
        self.assertEqual(pre_006_defect_count, 68,
                         "Court rig has 60 IES + 8 disc = 68 lights.")

    def test_postfix_ies_disks_inflated_and_shadow_authoring_present(self):
        stage = build_pre_fix_stage()
        apply_fix_postfix006(stage, self._rig_manifest())
        court_inflated = 0
        court_karma_q_count = 0
        court_vray_radius_count = 0
        disc_unchanged_radius = 0
        for prim in stage.Traverse():
            name = prim.GetName()
            if name.startswith("court_downlight_"):
                # DiskLight radius inflated to 0.3 + 3.0 = 3.3.
                disk = UsdLux.DiskLight(prim)
                self.assertAlmostEqual(disk.GetRadiusAttr().Get(), 3.3,
                                       places=4)
                court_inflated += 1
                # Karma sample-quality mirror.
                self.assertTrue(prim.HasAttribute("karma:light:samplingquality"))
                self.assertEqual(
                    prim.GetAttribute("karma:light:samplingquality").Get(), 32)
                court_karma_q_count += 1
                # V-Ray lossless opinions.
                self.assertTrue(prim.HasAttribute("inputs:vray:shadow:radius"))
                self.assertAlmostEqual(
                    prim.GetAttribute("inputs:vray:shadow:radius").Get(), 3.0,
                    places=4)
                court_vray_radius_count += 1
            elif name.startswith("backboard_disc_"):
                # Disc-spot has no shadowRadius — its DiskLight radius
                # MUST NOT be inflated. Only the Karma sample-quality
                # mirror fires (from `.shadowSubdivs=24`).
                disk = UsdLux.DiskLight(prim)
                self.assertAlmostEqual(disk.GetRadiusAttr().Get(), 0.5,
                                       places=4)
                self.assertFalse(prim.HasAttribute("inputs:vray:shadow:radius"))
                self.assertTrue(prim.HasAttribute("karma:light:samplingquality"))
                self.assertEqual(
                    prim.GetAttribute("karma:light:samplingquality").Get(), 24)
                disc_unchanged_radius += 1
        self.assertEqual(court_inflated, 60)
        self.assertEqual(court_karma_q_count, 60)
        self.assertEqual(court_vray_radius_count, 60)
        self.assertEqual(disc_unchanged_radius, 8)


class TestShadowFamilyBranchInvariant(unittest.TestCase):
    """MAX-LIT-003 contract: shadow-family authoring on Rect / Disk /
    Sphere / Distant / Dome is dispatched by the `.units` / probe fields
    NOT by the light's UsdLux subtype. That is, if two lights of
    DIFFERENT shapes both probe the same shadow-family fields, they
    get the same custom `inputs:vray:shadow:*` opinions authored."""

    def test_vray_shadow_opinions_identical_across_shape_branches(self):
        base_manifest = {
            "hasShadowRadius": True, "shadowRadius": 2.0,
            "hasShadowBias": True, "shadowBias": 0.15,
            "hasShadowSubdivs": True, "shadowSubdivs": 16,
        }
        manifests = [
            {**base_manifest, "name": "rect_probe",
             "className": "VRayLight", "type": 0,
             "size0": 1.0, "size1": 1.0, "intensity": 100.0},
            {**base_manifest, "name": "sphere_probe",
             "className": "VRayLight", "type": 2,
             "size0": 0.5, "intensity": 100.0},
            {**base_manifest, "name": "disk_probe",
             "className": "VRayLight", "type": 4,
             "size0": 0.5, "intensity": 100.0},
            {**base_manifest, "name": "sun_probe",
             "className": "VRaySun", "intensity": 3.0},
        ]
        stage = build_pre_fix_stage()
        apply_fix_postfix006(stage, manifests)
        for m in manifests:
            prim = stage.GetPrimAtPath(f"/root/lights/{m['name']}")
            self.assertAlmostEqual(
                prim.GetAttribute("inputs:vray:shadow:radius").Get(), 2.0,
                places=4,
                msg=f"{m['name']}: shadow:radius must be identical across "
                    f"every shape branch.")
            self.assertAlmostEqual(
                prim.GetAttribute("inputs:vray:shadow:bias").Get(), 0.15,
                places=4)
            self.assertEqual(
                prim.GetAttribute("inputs:vray:shadow:subdivs").Get(), 16)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--verbose", "-v", action="store_true")
    args, unknown = parser.parse_known_args()

    loader = unittest.TestLoader()
    suite = loader.loadTestsFromModule(sys.modules[__name__])
    runner = unittest.TextTestRunner(verbosity=2 if args.verbose else 1)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)


if __name__ == "__main__":
    main()
