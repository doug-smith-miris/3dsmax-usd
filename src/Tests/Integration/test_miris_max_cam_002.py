# Copyright 2026 Miris Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
"""
MAX-CAM-002 — USD-layer structural mirror of the C++ fix in
`src/translators/CameraWriter.cpp`.

Prior to this fix the CameraWriter authored a UsdGeomCamera.exposure
attribute ONLY when the source Max camera derived from
`MaxSDK::IPhysicalCamera` (the stock Autodesk Physical Camera). V-Ray
Physical Cameras are third-party plugin classes that do NOT derive from
`IPhysicalCamera`; the `dynamic_cast<MaxSDK::IPhysicalCamera*>` check on
line ~112 of CameraWriter.cpp returned nullptr for them, and every
V-Ray-authored camera in an exported scene fell into the "plain camera"
else-branch that has no exposure code at all.

Symptom on the Spectrum Center arena baseline (from the bug report):
every UsdGeomCamera prim (e.g. CAM_BOWL_COURTSIDE_AERIAL_V1) has
focalLength, focusDistance, horizontalAperture, verticalAperture — but
NO exposure, fStop, or shutter attribute. Karma / Hydra path-traced
renders come out ~40% too dim vs. the V-Ray ground truth because V-Ray
applies a per-camera exposure that Karma has no way to reproduce.

The fix probes the source camera via a MAXScript helper
(`discoverMaxCameraExposureFn`) that reads V-Ray's physical-camera
properties — `.f_number`, `.shutter_speed`, `.film_speed`, plus the
enable-flag `.exposure` and any direct `.exposure_value` override — and
computes a UsdGeomCamera.exposure value in stops via the same
photographic-EV formula the Autodesk `IPhysicalCamera.GetEffectiveEV()`
uses:

    EV_100 = log2(f_number^2 * shutter_speed * 100 / film_speed)

(V-Ray's `.shutter_speed` is inverse shutter time in Hz; e.g. 60 means
1/60 s, so `f_number^2 / t = f_number^2 * shutter_speed`.) When a
direct `.exposure_value` is present, that short-circuits the triple.
When `.exposure=false` (physical exposure disabled), NO value is
authored (the schema default 0 is the correct "no per-camera
exposure metadata" state). When the camera is a plain (non-physical)
camera with no probeable triple, NO value is authored either — the
existing "Use a physical camera to get best results" behavior is
preserved byte-identical.

The Python mirror is a validator (not a shipping code path). It:
  1) Builds a synthetic "pre-fix" USD stage with a UsdGeomCamera prim
     that has focalLength/aperture/focusDistance authored but NO
     exposure attribute (the defect the arena baseline exhibits).
  2) Applies the fix at the USD layer by running the same
     probe-parse-compute chain the C++ code walks, and calling
     `CreateExposureAttr().Set(...)` on the same camera.
  3) Asserts the fixed stage now has an exposure attribute whose value
     matches the intended photographic-EV formula, that the fix is a
     no-op for cameras with `exposure=false` OR no probeable triple,
     and that a direct `.exposure_value` short-circuits the triple.
"""
import math
import unittest
from typing import Dict, List, Optional, Tuple

from pxr import Sdf, Usd, UsdGeom


# ---------------------------------------------------------------------------
# Parser + compute — mirror of the C++ CameraExposureProbe /
# _ComputeCameraExposureFromProbe pair in CameraWriter.cpp.
# ---------------------------------------------------------------------------

def parse_camera_exposure_manifest(manifest: str) -> Dict[str, object]:
    """Parse a pipe-delimited manifest of the shape emitted by the
    MAXScript `discoverMaxCameraExposure` helper. Absent keys stay
    absent in the returned dict; present keys are coerced to the
    expected type."""
    result: Dict[str, object] = {}
    for line in manifest.split("\n"):
        line = line.strip()
        if not line or "|" not in line:
            continue
        key, val = line.split("|", 1)
        try:
            if key == "className":
                result[key] = val
            elif key == "exposureEnabled":
                result[key] = val.lower() not in ("false", "0")
            elif key == "exposureValue":
                result[key] = float(val)
            elif key == "fNumber":
                result[key] = float(val)
            elif key == "shutterSpeed":
                result[key] = float(val)
            elif key == "filmSpeed":
                result[key] = float(val)
        except ValueError:
            # Malformed value -> leave the key absent.
            continue
    return result


def compute_camera_exposure(probe: Dict[str, object]) -> Optional[float]:
    """Mirror of `_ComputeCameraExposureFromProbe`. Returns the
    UsdGeomCamera.exposure value in stops when it can be derived,
    or None when the caller should leave the schema default in place.
    """
    if probe.get("exposureEnabled") is False:
        return None
    if "exposureValue" in probe:
        return float(probe["exposureValue"])
    f = probe.get("fNumber")
    s = probe.get("shutterSpeed")
    iso = probe.get("filmSpeed")
    if f is None or s is None or iso is None:
        return None
    if f <= 0.0 or s <= 0.0 or iso <= 0.0:
        return None
    return math.log2((f * f) * s * 100.0 / iso)


# ---------------------------------------------------------------------------
# Stage builders
# ---------------------------------------------------------------------------

CAMERA_PATH = "/root/cameras/CAM_BOWL_COURTSIDE_AERIAL_V1"


def build_pre_fix_stage() -> Usd.Stage:
    """Simulate the MAX-CAM-002 defect: a UsdGeomCamera prim with all
    the intrinsics the exporter DOES author (focal length, focus
    distance, aperture) but NO `exposure` attribute at all.
    """
    stage = Usd.Stage.CreateInMemory()
    root = UsdGeom.Xform.Define(stage, "/root")
    UsdGeom.Xform.Define(stage, "/root/cameras")
    cam = UsdGeom.Camera.Define(stage, CAMERA_PATH)
    cam.CreateFocalLengthAttr().Set(35.0)
    cam.CreateFocusDistanceAttr().Set(400.0)
    cam.CreateHorizontalApertureAttr().Set(36.0)
    cam.CreateVerticalApertureAttr().Set(24.0)
    stage.SetDefaultPrim(root.GetPrim())
    return stage


def apply_fix(stage: Usd.Stage, manifest: str) -> Optional[float]:
    """Apply MAX-CAM-002 at the USD layer: probe -> compute -> author.
    Returns the authored exposure value, or None when the fix was a
    no-op (matches the shipping C++ path)."""
    probe = parse_camera_exposure_manifest(manifest)
    value = compute_camera_exposure(probe)
    if value is None:
        return None
    cam = UsdGeom.Camera(stage.GetPrimAtPath(CAMERA_PATH))
    cam.CreateExposureAttr().Set(value)
    return value


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestPreFixDefect(unittest.TestCase):
    """The pre-fix stage models the arena baseline: cameras present, but
    no exposure attribute anywhere. Lock the fingerprint in."""

    def test_camera_present_but_no_exposure_attribute(self):
        stage = build_pre_fix_stage()
        cam_prim = stage.GetPrimAtPath(CAMERA_PATH)
        self.assertTrue(cam_prim.IsValid())
        cam = UsdGeom.Camera(cam_prim)
        # Intrinsics exist:
        self.assertAlmostEqual(cam.GetFocalLengthAttr().Get(), 35.0)
        # Exposure attribute is NOT authored (matches the .usda grep in the bug):
        self.assertFalse(cam.GetExposureAttr().IsAuthored())


class TestManifestParser(unittest.TestCase):
    """Anchor the manifest wire format so a MAXScript helper change is
    caught by these tests (rather than only by a Windows-box rebuild)."""

    def test_parses_full_vray_physical_camera_manifest(self):
        manifest = (
            "className|VRayPhysicalCamera\n"
            "exposureEnabled|true\n"
            "fNumber|8.0\n"
            "shutterSpeed|60.0\n"
            "filmSpeed|100.0\n"
        )
        probe = parse_camera_exposure_manifest(manifest)
        self.assertEqual(probe["className"], "VRayPhysicalCamera")
        self.assertIs(probe["exposureEnabled"], True)
        self.assertAlmostEqual(probe["fNumber"], 8.0)
        self.assertAlmostEqual(probe["shutterSpeed"], 60.0)
        self.assertAlmostEqual(probe["filmSpeed"], 100.0)

    def test_exposure_enabled_false_parses(self):
        manifest = "className|VRayPhysicalCamera\nexposureEnabled|false\n"
        probe = parse_camera_exposure_manifest(manifest)
        self.assertIs(probe["exposureEnabled"], False)

    def test_malformed_number_field_left_absent(self):
        # A rogue non-numeric fNumber must NOT crash the parser; the
        # field is simply absent from the probe and the compute step
        # short-circuits to None (no exposure authored).
        manifest = "fNumber|not-a-number\nshutterSpeed|60\nfilmSpeed|100\n"
        probe = parse_camera_exposure_manifest(manifest)
        self.assertNotIn("fNumber", probe)

    def test_empty_manifest_yields_empty_probe(self):
        self.assertEqual(parse_camera_exposure_manifest(""), {})


class TestExposureFormula(unittest.TestCase):
    """The photographic-EV formula the C++ code uses:

        EV_100 = log2(f_number^2 * shutter_speed * 100 / film_speed)

    where `shutter_speed` follows V-Ray's convention of 1/N seconds.
    """

    def test_reference_daylight_camera(self):
        # Typical daylight portrait: f/8, 1/60 s, ISO 100.
        # EV_100 = log2(64 * 60 * 100 / 100) = log2(3840) ~ 11.906
        probe = {"fNumber": 8.0, "shutterSpeed": 60.0, "filmSpeed": 100.0}
        self.assertAlmostEqual(compute_camera_exposure(probe), math.log2(3840.0), places=6)

    def test_low_light_indoor_camera(self):
        # Wide aperture, slow shutter, high ISO: f/2, 1/30 s, ISO 800.
        # EV_100 = log2(4 * 30 * 100 / 800) = log2(15) ~ 3.907
        probe = {"fNumber": 2.0, "shutterSpeed": 30.0, "filmSpeed": 800.0}
        self.assertAlmostEqual(compute_camera_exposure(probe), math.log2(15.0), places=6)

    def test_iso_doubling_lowers_exposure_by_one_stop(self):
        # Doubling ISO (100 -> 200) with identical aperture/shutter
        # subtracts one photographic stop.
        base = {"fNumber": 8.0, "shutterSpeed": 60.0, "filmSpeed": 100.0}
        hot = {"fNumber": 8.0, "shutterSpeed": 60.0, "filmSpeed": 200.0}
        delta = compute_camera_exposure(base) - compute_camera_exposure(hot)
        self.assertAlmostEqual(delta, 1.0, places=6)

    def test_aperture_wider_lowers_exposure_by_two_stops_per_root_two(self):
        # f/8 -> f/4 (two stops wider) drops EV by 2.
        base = {"fNumber": 8.0, "shutterSpeed": 60.0, "filmSpeed": 100.0}
        wide = {"fNumber": 4.0, "shutterSpeed": 60.0, "filmSpeed": 100.0}
        delta = compute_camera_exposure(base) - compute_camera_exposure(wide)
        self.assertAlmostEqual(delta, 2.0, places=6)

    def test_shutter_faster_raises_exposure(self):
        # 1/60 -> 1/125 halves shutter time; EV rises by ~log2(125/60) ~ 1.06.
        slow = {"fNumber": 8.0, "shutterSpeed": 60.0, "filmSpeed": 100.0}
        fast = {"fNumber": 8.0, "shutterSpeed": 125.0, "filmSpeed": 100.0}
        delta = compute_camera_exposure(fast) - compute_camera_exposure(slow)
        self.assertAlmostEqual(delta, math.log2(125.0 / 60.0), places=6)


class TestDirectExposureValueShortCircuit(unittest.TestCase):
    """When the V-Ray camera authored a direct `.exposure_value` (some
    versions expose one), that MUST win over the f/shutter/ISO triple.
    Mirrors the branch order in `_ComputeCameraExposureFromProbe`."""

    def test_exposure_value_wins_over_triple(self):
        probe = {
            "exposureValue": 12.5,
            "fNumber": 8.0,
            "shutterSpeed": 60.0,
            "filmSpeed": 100.0,
        }
        self.assertAlmostEqual(compute_camera_exposure(probe), 12.5)

    def test_negative_exposure_value_is_authored_verbatim(self):
        # Some scenes use negative EV overrides for interior/night looks.
        probe = {"exposureValue": -2.5}
        self.assertAlmostEqual(compute_camera_exposure(probe), -2.5)


class TestExposureEnabledGate(unittest.TestCase):
    """When V-Ray's `.exposure` bool is false, the camera renders
    without physical exposure compensation and we MUST NOT author a
    value on USD — the schema default 0 is the correct
    'no per-camera exposure metadata' state."""

    def test_exposure_disabled_returns_none(self):
        probe = {
            "exposureEnabled": False,
            "fNumber": 8.0,
            "shutterSpeed": 60.0,
            "filmSpeed": 100.0,
        }
        self.assertIsNone(compute_camera_exposure(probe))

    def test_exposure_disabled_wins_over_direct_value(self):
        # `.exposure=false` gates everything, including a direct
        # `.exposure_value` — matches the C++ ordering.
        probe = {"exposureEnabled": False, "exposureValue": 15.0}
        self.assertIsNone(compute_camera_exposure(probe))

    def test_exposure_enabled_true_is_pass_through(self):
        probe = {
            "exposureEnabled": True,
            "fNumber": 8.0,
            "shutterSpeed": 60.0,
            "filmSpeed": 100.0,
        }
        value = compute_camera_exposure(probe)
        self.assertIsNotNone(value)
        self.assertAlmostEqual(value, math.log2(3840.0), places=6)


class TestPlainCameraNoOp(unittest.TestCase):
    """Plain (non-physical) cameras and any camera that lacks a
    probeable triple MUST NOT get an exposure attribute authored.
    The existing 'Use a physical camera to get best results' warning
    branch is preserved byte-identical."""

    def test_empty_probe_yields_none(self):
        self.assertIsNone(compute_camera_exposure({}))

    def test_partial_triple_missing_iso_yields_none(self):
        probe = {"fNumber": 8.0, "shutterSpeed": 60.0}
        self.assertIsNone(compute_camera_exposure(probe))

    def test_partial_triple_missing_shutter_yields_none(self):
        probe = {"fNumber": 8.0, "filmSpeed": 100.0}
        self.assertIsNone(compute_camera_exposure(probe))

    def test_partial_triple_missing_fnumber_yields_none(self):
        probe = {"shutterSpeed": 60.0, "filmSpeed": 100.0}
        self.assertIsNone(compute_camera_exposure(probe))

    def test_zero_fnumber_yields_none(self):
        # Divide-by-zero / log2(0) hardening.
        probe = {"fNumber": 0.0, "shutterSpeed": 60.0, "filmSpeed": 100.0}
        self.assertIsNone(compute_camera_exposure(probe))

    def test_zero_shutter_speed_yields_none(self):
        probe = {"fNumber": 8.0, "shutterSpeed": 0.0, "filmSpeed": 100.0}
        self.assertIsNone(compute_camera_exposure(probe))

    def test_zero_film_speed_yields_none(self):
        probe = {"fNumber": 8.0, "shutterSpeed": 60.0, "filmSpeed": 0.0}
        self.assertIsNone(compute_camera_exposure(probe))


class TestEndToEndFixOnStage(unittest.TestCase):
    """Prove the observable the shipping fix introduces: after
    applying the fix on the pre-fix stage, the UsdGeomCamera prim
    carries an exposure attribute whose value matches the formula."""

    def test_stage_gains_exposure_after_fix(self):
        stage = build_pre_fix_stage()
        # V-Ray physical camera manifest that the arena scene emits.
        manifest = (
            "className|VRayPhysicalCamera\n"
            "exposureEnabled|true\n"
            "fNumber|8.0\n"
            "shutterSpeed|60.0\n"
            "filmSpeed|100.0\n"
        )
        authored = apply_fix(stage, manifest)
        self.assertAlmostEqual(authored, math.log2(3840.0), places=6)

        cam = UsdGeom.Camera(stage.GetPrimAtPath(CAMERA_PATH))
        self.assertTrue(cam.GetExposureAttr().IsAuthored())
        self.assertAlmostEqual(cam.GetExposureAttr().Get(), math.log2(3840.0), places=6)

    def test_stage_untouched_when_exposure_disabled(self):
        stage = build_pre_fix_stage()
        manifest = (
            "className|VRayPhysicalCamera\n"
            "exposureEnabled|false\n"
            "fNumber|8.0\n"
            "shutterSpeed|60.0\n"
            "filmSpeed|100.0\n"
        )
        self.assertIsNone(apply_fix(stage, manifest))
        cam = UsdGeom.Camera(stage.GetPrimAtPath(CAMERA_PATH))
        # Schema default preserved:
        self.assertFalse(cam.GetExposureAttr().IsAuthored())

    def test_stage_untouched_for_plain_camera(self):
        stage = build_pre_fix_stage()
        # A stock Autodesk "Free Camera" wouldn't expose any of these
        # probeable properties; the manifest is essentially empty.
        manifest = "className|FreeCamera\n"
        self.assertIsNone(apply_fix(stage, manifest))
        cam = UsdGeom.Camera(stage.GetPrimAtPath(CAMERA_PATH))
        self.assertFalse(cam.GetExposureAttr().IsAuthored())

    def test_stage_intrinsics_untouched_by_fix(self):
        """Surgical-scope invariant: the fix ONLY authors the exposure
        attribute. Every other camera intrinsic (focal length,
        aperture, focus distance) must stay byte-identical to the
        pre-fix state."""
        stage = build_pre_fix_stage()
        manifest = (
            "exposureEnabled|true\n"
            "fNumber|8.0\n"
            "shutterSpeed|60.0\n"
            "filmSpeed|100.0\n"
        )
        apply_fix(stage, manifest)
        cam = UsdGeom.Camera(stage.GetPrimAtPath(CAMERA_PATH))
        self.assertAlmostEqual(cam.GetFocalLengthAttr().Get(), 35.0)
        self.assertAlmostEqual(cam.GetFocusDistanceAttr().Get(), 400.0)
        self.assertAlmostEqual(cam.GetHorizontalApertureAttr().Get(), 36.0)
        self.assertAlmostEqual(cam.GetVerticalApertureAttr().Get(), 24.0)


class TestArenaCensusInvariant(unittest.TestCase):
    """Reconstruct the arena baseline's camera population (many
    V-Ray physical cameras, zero exposures authored pre-fix) and
    show the fix flips the census from 0/N to N/N exposures without
    touching camera counts or intrinsics.

    This is the `coverageVerdict` invariant `vray-arch-verifier.ts`
    consumes: `exposureAttrs_post > exposureAttrs_pre` and
    `cameraCount` unchanged.
    """

    def _build_arena_stage(self, cam_count: int) -> Usd.Stage:
        stage = Usd.Stage.CreateInMemory()
        UsdGeom.Xform.Define(stage, "/root")
        UsdGeom.Xform.Define(stage, "/root/cameras")
        for i in range(cam_count):
            path = f"/root/cameras/CAM_BOWL_COURTSIDE_AERIAL_V{i + 1}"
            cam = UsdGeom.Camera.Define(stage, path)
            cam.CreateFocalLengthAttr().Set(35.0)
            cam.CreateHorizontalApertureAttr().Set(36.0)
            cam.CreateVerticalApertureAttr().Set(24.0)
        return stage

    def _count_cameras_and_exposures(self, stage: Usd.Stage) -> Tuple[int, int]:
        cams = 0
        exposures = 0
        for prim in stage.Traverse():
            if prim.GetTypeName() == "Camera":
                cams += 1
                cam = UsdGeom.Camera(prim)
                if cam.GetExposureAttr().IsAuthored():
                    exposures += 1
        return cams, exposures

    def test_arena_census_pre_and_post_fix(self):
        # Arena baseline scale: 8 V-Ray physical cameras, all with the
        # same "daylight portrait" settings that produced the 40%-dim
        # Karma output.
        stage = self._build_arena_stage(cam_count=8)
        pre_cams, pre_exp = self._count_cameras_and_exposures(stage)
        self.assertEqual(pre_cams, 8)
        self.assertEqual(pre_exp, 0)

        manifest = (
            "className|VRayPhysicalCamera\n"
            "exposureEnabled|true\n"
            "fNumber|8.0\n"
            "shutterSpeed|60.0\n"
            "filmSpeed|100.0\n"
        )
        probe = parse_camera_exposure_manifest(manifest)
        value = compute_camera_exposure(probe)
        self.assertIsNotNone(value)
        for prim in stage.Traverse():
            if prim.GetTypeName() == "Camera":
                UsdGeom.Camera(prim).CreateExposureAttr().Set(value)

        post_cams, post_exp = self._count_cameras_and_exposures(stage)
        # Camera count invariant:
        self.assertEqual(post_cams, pre_cams)
        # Coverage delta: 0 -> 8 exposures authored.
        self.assertEqual(post_exp, 8)
        self.assertGreater(post_exp, pre_exp)


if __name__ == "__main__":
    unittest.main()
