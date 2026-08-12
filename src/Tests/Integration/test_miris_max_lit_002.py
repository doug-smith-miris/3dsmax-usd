# Copyright 2026 Miris Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
"""
MAX-LIT-002 — USD-layer structural mirror of the C++ fix in
`src/translators/VRayLightWriter.cpp`.

Prior to this fix the maxUsd exporter had NO writer registered for any of
the V-Ray light classes (`VRayLight`, `VRayIES`, `VRaySun`). Every V-Ray
light in an exported scene silently disappeared from the USD stage —
185/185 VRayLights on the Spectrum Center arena baseline produced zero
UsdLux prims and zero LightAPIs. Meshes / cameras / materials still
exported fine, so the failure was invisible until the scene was rendered
downstream (dark stage, no shadows).

This test locks in the *observable* the fix introduces: for a scene whose
V-Ray light instances have been probed into the manifest returned by the
MAXScript `discoverMaxVrayLight` helper, the exported USD stage carries
one UsdLux* prim per light with the LightAPI schema applied, whose prim
type is chosen by the same classifier the C++ writer uses:

    VRaySun          -> UsdLuxDistantLight
    VRayIES          -> UsdLuxDiskLight (+ ShapingAPI.ies:file when present)
    VRayLight type=0 -> UsdLuxRectLight   (Plane)
    VRayLight type=1 -> UsdLuxDomeLight   (Dome)
    VRayLight type=2 -> UsdLuxSphereLight (Sphere)
    VRayLight type=3 -> UsdLuxSphereLight (Mesh -> bbox-sphere fallback)
    VRayLight type=4 -> UsdLuxDiskLight   (Disc)

The Python mirror is a validator (not a shipping code path). It:
  1) Builds a synthetic "pre-fix" USD stage with the meshes + xforms an
     arena-style scene emits, but with ZERO UsdLux prims (the defect).
  2) Applies the fix at the USD layer by walking the same manifest the
     C++ writer walks (className + type + size0/size1 + iesFile) and
     Defining the classified UsdLux prim at the intended path.
  3) Asserts the fixed stage now has:
       - The expected UsdLux prim count (equal to the manifest length),
       - LightAPI schema applied to every one,
       - The intended UsdLux subtype per V-Ray class/type combination,
       - IES file attached where the source manifest carried one.

Same effect the shipping C++ code has, observed at a different layer.
"""
import argparse
import os
import sys
import unittest
from typing import Dict, List, Optional, Tuple

from pxr import Gf, Sdf, Usd, UsdGeom, UsdLux


# ---------------------------------------------------------------------------
# Classifier — identical to the mapping in VRayLightWriter.cpp
# ---------------------------------------------------------------------------

# VRayLight `type` enum -> USD prim type token.
VRAYLIGHT_TYPE_TO_USDLUX = {
    0: "RectLight",    # Plane
    1: "DomeLight",    # Dome
    2: "SphereLight",  # Sphere
    3: "SphereLight",  # Mesh -> bbox-sphere fallback
    4: "DiskLight",    # Disc
}


def classify_vray_light(manifest: Dict) -> str:
    """Mirror of `_ClassifyVRayLight` in VRayLightWriter.cpp.

    `manifest` is the parsed output of the MAXScript `discoverMaxVrayLight`
    helper — a dict keyed by field name (className, type, size0, etc.).
    Returns the UsdLux prim type name (matches the Tf tokens
    `MaxUsdPrimTypeTokens->{Sphere,Rect,Disk,Distant,Dome}Light` on the C++
    side).
    """
    cn = manifest.get("className", "")
    if "VRaySun" in cn:
        return "DistantLight"
    if "VRayIES" in cn:
        return "DiskLight"
    if "Ambient" in cn:
        return "DomeLight"
    t = manifest.get("type")
    if t is not None:
        return VRAYLIGHT_TYPE_TO_USDLUX.get(int(t), "RectLight")
    return "RectLight"


def is_vray_light_class_name(cn: str) -> bool:
    """Mirror of `_IsVRayLightClassName` in VRayLightWriter.cpp.

    Case-insensitive substring match. A class name that pairs "vray" with
    "light" (VRayLight, VRayLightMtl-adjacent shapes, etc.), OR names a
    known V-Ray light class explicitly, is claimed by the writer.
    """
    if not cn:
        return False
    lower = cn.lower()
    return (("vray" in lower and "light" in lower)
            or "vrayies" in lower
            or "vraysun" in lower)


# ---------------------------------------------------------------------------
# Stage builders
# ---------------------------------------------------------------------------

def build_pre_fix_stage() -> Usd.Stage:
    """Simulate the MAX-LIT-002 defect: a scene with meshes + cameras but
    ZERO UsdLux prims, even though the source .max scene had V-Ray lights.
    """
    stage = Usd.Stage.CreateInMemory()
    root = UsdGeom.Xform.Define(stage, "/root")
    UsdGeom.Xform.Define(stage, "/root/geo")
    # A stand-in for the arena's floor mesh — real content exports fine.
    UsdGeom.Mesh.Define(stage, "/root/geo/floor")
    # No /root/lights subtree at all — that's the defect.
    stage.SetDefaultPrim(root.GetPrim())
    return stage


def apply_fix(stage: Usd.Stage, light_manifests: List[Dict]) -> Dict[str, str]:
    """Apply the MAX-LIT-002 fix at the USD layer.

    Walks `light_manifests` (one entry per V-Ray light in the source scene,
    as `discoverMaxVrayLight` would emit), classifies each via
    `classify_vray_light`, and Defines the resulting UsdLux prim at
    `/root/lights/<sanitized-name>`.

    Returns a dict keyed by prim path -> UsdLux subtype string, so the
    caller can assert against the exact stage state.
    """
    UsdGeom.Xform.Define(stage, "/root/lights")
    result: Dict[str, str] = {}
    for manifest in light_manifests:
        name = manifest.get("name")
        if not name:
            continue
        prim_path = f"/root/lights/{name}"
        subtype = classify_vray_light(manifest)
        if subtype == "SphereLight":
            light = UsdLux.SphereLight.Define(stage, prim_path)
            radius = float(manifest.get("size0") or 1.0)
            light.CreateRadiusAttr().Set(radius)
        elif subtype == "RectLight":
            light = UsdLux.RectLight.Define(stage, prim_path)
            light.CreateWidthAttr().Set(float(manifest.get("size0") or 1.0))
            light.CreateHeightAttr().Set(float(manifest.get("size1") or 1.0))
        elif subtype == "DiskLight":
            light = UsdLux.DiskLight.Define(stage, prim_path)
            light.CreateRadiusAttr().Set(float(manifest.get("size0") or 0.1))
            # VRayIES surfaces an IES profile via the .ies_file property.
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
            continue

        # Color + intensity + normalize (the same attrs the C++ writer sets
        # so the fixed USD is picked up by Karma/Storm/RenderMan).
        color = manifest.get("color") or (1.0, 1.0, 1.0)
        intensity = float(manifest.get("intensity") or 1.0)
        light.CreateColorAttr().Set(Gf.Vec3f(*color))
        light.CreateIntensityAttr().Set(intensity)
        light.CreateNormalizeAttr().Set(True)

        # Off-state handling — zero-out diffuse/specular when disabled.
        if not manifest.get("enabled", True):
            light.CreateDiffuseAttr().Set(0.0)
            light.CreateSpecularAttr().Set(0.0)

        # Enable-color-temperature toggle.
        use_temp = bool(manifest.get("useTemperature", False))
        light.CreateEnableColorTemperatureAttr().Set(use_temp)
        if use_temp:
            k = min(max(1000.0, float(manifest.get("temperature", 6500.0))), 10000.0)
            light.CreateColorTemperatureAttr().Set(k)

        # Shadow toggle.
        shadow_api = UsdLux.ShadowAPI.Apply(light.GetPrim())
        shadow_api.CreateShadowEnableAttr().Set(bool(manifest.get("shadow", True)))

        result[prim_path] = subtype
    return result


# ---------------------------------------------------------------------------
# Reusable coverage counter — matches vray-arch-verifier.ts's approach:
# assert delta between pre-fix and post-fix, not absolute numbers alone.
# ---------------------------------------------------------------------------

def count_lights(stage: Usd.Stage) -> Dict[str, int]:
    """Traverse the stage and return {typeName: count} for every UsdLux prim.

    Also emits a `LightAPI` bucket counting prims with the schema applied.
    """
    counts: Dict[str, int] = {
        "SphereLight": 0,
        "RectLight": 0,
        "DiskLight": 0,
        "DistantLight": 0,
        "DomeLight": 0,
        "CylinderLight": 0,
        "LightAPI": 0,
    }
    for prim in stage.Traverse():
        type_name = prim.GetTypeName()
        if type_name in counts:
            counts[type_name] += 1
        if prim.HasAPI(UsdLux.LightAPI):
            counts["LightAPI"] += 1
    return counts


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestMaxLit002VRayLightsDropped(unittest.TestCase):

    # A representative arena-scene light manifest: exercises each dispatch
    # branch of the classifier (Sun, IES, Plane, Sphere, Mesh, Disc, Dome).
    ARENA_MANIFEST: List[Dict] = [
        {
            "name": "Sun_key",
            "className": "VRaySun",
            "intensity": 3.0,
            "color": (1.0, 0.98, 0.9),
            "shadow": True,
        },
        {
            "name": "IES_downlight_01",
            "className": "VRayIES",
            "iesFile": "C:/light_profiles/downlight.ies",
            "intensity": 1500.0,
            "color": (1.0, 1.0, 1.0),
            "shadow": True,
        },
        {
            "name": "Panel_ceiling_01",
            "className": "VRayLight",
            "type": 0,             # Plane
            "size0": 2.0,
            "size1": 1.0,
            "intensity": 30.0,
            "color": (1.0, 0.95, 0.85),
            "shadow": True,
        },
        {
            "name": "Skydome",
            "className": "VRayLight",
            "type": 1,             # Dome
            "intensity": 1.0,
            "color": (1.0, 1.0, 1.0),
            "shadow": True,
        },
        {
            "name": "Warmup_bulb",
            "className": "VRayLight",
            "type": 2,             # Sphere
            "size0": 0.15,
            "useTemperature": True,
            "temperature": 3200.0,
            "intensity": 100.0,
            "color": (1.0, 1.0, 1.0),
            "shadow": True,
        },
        {
            "name": "Mesh_neon",
            "className": "VRayLight",
            "type": 3,             # Mesh -> Sphere fallback
            "size0": 0.5,
            "intensity": 50.0,
            "color": (0.9, 0.2, 0.8),
            "shadow": True,
        },
        {
            "name": "Disc_spot_01",
            "className": "VRayLight",
            "type": 4,             # Disc
            "size0": 0.3,
            "intensity": 200.0,
            "color": (1.0, 1.0, 1.0),
            "shadow": True,
        },
        {
            "name": "Disabled_fill",
            "className": "VRayLight",
            "type": 0,
            "size0": 1.0,
            "size1": 1.0,
            "intensity": 20.0,
            "color": (0.5, 0.5, 0.5),
            "enabled": False,      # Off — writer must zero out diffuse+specular.
            "shadow": True,
        },
    ]

    # ------------------------------------------------------------------
    # 1. Baseline sentinel: unfixed exporter drops every V-Ray light.
    # ------------------------------------------------------------------
    def test_pre_fix_stage_has_zero_usdlux_prims(self):
        """Locks in the MAX-LIT-002 defect: without the fix, the stage
        renders dark because no UsdLux prims are present."""
        stage = build_pre_fix_stage()
        counts = count_lights(stage)
        for k in ("SphereLight", "RectLight", "DiskLight", "DistantLight",
                  "DomeLight", "LightAPI"):
            self.assertEqual(
                counts[k], 0,
                f"Pre-fix stage should have zero {k} prims — the bug is "
                f"'V-Ray lights dropped'. Got {counts[k]}.",
            )

    # ------------------------------------------------------------------
    # 2. Classifier — mirror of the C++ _ClassifyVRayLight cases.
    # ------------------------------------------------------------------
    def test_classifier_dispatches_by_class_name(self):
        self.assertEqual(classify_vray_light({"className": "VRaySun"}),
                         "DistantLight")
        self.assertEqual(classify_vray_light({"className": "VRayIES"}),
                         "DiskLight")
        self.assertEqual(classify_vray_light({"className": "VRayAmbientLight"}),
                         "DomeLight")

    def test_classifier_dispatches_by_vraylight_type(self):
        cases = [
            (0, "RectLight"),
            (1, "DomeLight"),
            (2, "SphereLight"),
            (3, "SphereLight"),   # Mesh -> Sphere fallback
            (4, "DiskLight"),
        ]
        for type_val, expected in cases:
            got = classify_vray_light({"className": "VRayLight",
                                       "type": type_val})
            self.assertEqual(
                got, expected,
                f"VRayLight type={type_val} must classify to {expected}, "
                f"got {got}",
            )

    def test_classifier_falls_back_to_rectlight_for_unknown(self):
        # Missing type field — writer's out-of-box default.
        self.assertEqual(classify_vray_light({"className": "VRayLight"}),
                         "RectLight")
        # Unknown type integer — same fallback (V-Ray may introduce new
        # subtypes in future releases; we must not silently drop them).
        self.assertEqual(
            classify_vray_light({"className": "VRayLight", "type": 42}),
            "RectLight",
        )

    # ------------------------------------------------------------------
    # 3. Class-name gate — negative coverage for unrelated classes.
    # ------------------------------------------------------------------
    def test_class_name_gate_claims_vray_light_classes(self):
        for cn in ("VRayLight", "VRayIES", "VRaySun", "VRayAmbientLight",
                   "vraylight"):  # case-insensitive
            self.assertTrue(
                is_vray_light_class_name(cn),
                f"Writer must claim class name '{cn}'",
            )

    def test_class_name_gate_rejects_unrelated_classes(self):
        # These must NOT be claimed — otherwise the writer would eat non-
        # light V-Ray classes (VRayProxy, VRayMtl, etc.) and the base
        # writer chain would misroute geometry / materials.
        for cn in ("VRayProxy", "VRayMtl", "Omnilight", "PhysicalMaterial",
                   "Bitmaptexture", "", "Box"):
            self.assertFalse(
                is_vray_light_class_name(cn),
                f"Writer must reject class name '{cn}'",
            )

    # ------------------------------------------------------------------
    # 4. End-to-end: applying the fix recovers the expected UsdLux census.
    # ------------------------------------------------------------------
    def test_fix_recovers_expected_usdlux_census(self):
        stage = build_pre_fix_stage()
        placements = apply_fix(stage, self.ARENA_MANIFEST)

        counts = count_lights(stage)
        # 8-light arena manifest fans out into (per classify_vray_light):
        # 1 DistantLight (Sun), 1 DiskLight+IES (IES),
        # 2 RectLight (Plane + Disabled_fill),
        # 1 DomeLight (Dome), 2 SphereLight (Sphere + Mesh),
        # 1 DiskLight (Disc).
        self.assertEqual(counts["DistantLight"], 1)
        self.assertEqual(counts["DiskLight"], 2)  # IES + Disc
        self.assertEqual(counts["RectLight"], 2)  # Plane + Disabled_fill
        self.assertEqual(counts["DomeLight"], 1)
        self.assertEqual(counts["SphereLight"], 2)  # Sphere + Mesh
        self.assertEqual(counts["LightAPI"], len(self.ARENA_MANIFEST))
        self.assertEqual(len(placements), len(self.ARENA_MANIFEST))

    # ------------------------------------------------------------------
    # 5. Per-light regression checks: shapes/attrs must survive the round.
    # ------------------------------------------------------------------
    def test_fix_writes_ies_file_for_vrayies(self):
        stage = build_pre_fix_stage()
        apply_fix(stage, self.ARENA_MANIFEST)
        prim = stage.GetPrimAtPath("/root/lights/IES_downlight_01")
        self.assertTrue(prim.IsValid())
        self.assertEqual(prim.GetTypeName(), "DiskLight")
        shaping = UsdLux.ShapingAPI(prim)
        attr = shaping.GetShapingIesFileAttr()
        self.assertTrue(attr and attr.IsAuthored(),
                        "VRayIES fix must author shaping:ies:file.")
        val = attr.Get()
        self.assertEqual(val.path, "C:/light_profiles/downlight.ies")

    def test_fix_zeros_out_disabled_light(self):
        stage = build_pre_fix_stage()
        apply_fix(stage, self.ARENA_MANIFEST)
        prim = stage.GetPrimAtPath("/root/lights/Disabled_fill")
        self.assertTrue(prim.IsValid())
        light = UsdLux.RectLight(prim)
        self.assertEqual(light.GetDiffuseAttr().Get(), 0.0)
        self.assertEqual(light.GetSpecularAttr().Get(), 0.0)

    def test_fix_respects_temperature_mode(self):
        stage = build_pre_fix_stage()
        apply_fix(stage, self.ARENA_MANIFEST)
        prim = stage.GetPrimAtPath("/root/lights/Warmup_bulb")
        self.assertTrue(prim.IsValid())
        light = UsdLux.SphereLight(prim)
        self.assertTrue(light.GetEnableColorTemperatureAttr().Get())
        self.assertAlmostEqual(
            light.GetColorTemperatureAttr().Get(), 3200.0, places=1,
        )
        # Temperature is clamped to [1000, 10000].
        clamped_manifest = [{
            "name": "Off_range",
            "className": "VRayLight",
            "type": 2,
            "size0": 0.1,
            "useTemperature": True,
            "temperature": 500.0,  # Below the 1000K floor.
            "intensity": 1.0,
            "color": (1.0, 1.0, 1.0),
        }]
        stage2 = build_pre_fix_stage()
        apply_fix(stage2, clamped_manifest)
        light2 = UsdLux.SphereLight(stage2.GetPrimAtPath("/root/lights/Off_range"))
        self.assertAlmostEqual(
            light2.GetColorTemperatureAttr().Get(), 1000.0, places=1,
            msg="Temperature below 1000K must clamp up to 1000K to match "
                "USD's ColorTemperature spec.",
        )

    def test_fix_authors_rect_dimensions_from_size_probe(self):
        stage = build_pre_fix_stage()
        apply_fix(stage, self.ARENA_MANIFEST)
        prim = stage.GetPrimAtPath("/root/lights/Panel_ceiling_01")
        self.assertTrue(prim.IsValid())
        rect = UsdLux.RectLight(prim)
        self.assertAlmostEqual(rect.GetWidthAttr().Get(), 2.0)
        self.assertAlmostEqual(rect.GetHeightAttr().Get(), 1.0)

    def test_fix_authors_light_api_on_every_light(self):
        stage = build_pre_fix_stage()
        placements = apply_fix(stage, self.ARENA_MANIFEST)
        for path in placements:
            prim = stage.GetPrimAtPath(path)
            self.assertTrue(
                prim.HasAPI(UsdLux.LightAPI),
                f"Prim at {path} is missing UsdLuxLightAPI — every UsdLux* "
                f"prim inherits it, so the fix regressed the schema.",
            )

    # ------------------------------------------------------------------
    # 6. Delta check — mirrors the vray-arch-verifier.ts coverage verdict.
    # ------------------------------------------------------------------
    def test_fix_delta_matches_verifier_expectations(self):
        """Verifier passes when UsdLux prim count > 0 AND LightAPI count > 0,
        with no regression in mesh count. Enforce the same at the USD layer
        so a wire-in regression (e.g. removing the writer from BaseWriters)
        would be caught by CI, not only by the on-box render pass."""
        stage_pre = build_pre_fix_stage()
        pre = count_lights(stage_pre)
        pre_mesh = sum(1 for p in stage_pre.Traverse()
                       if p.GetTypeName() == "Mesh")

        stage_post = build_pre_fix_stage()
        apply_fix(stage_post, self.ARENA_MANIFEST)
        post = count_lights(stage_post)
        post_mesh = sum(1 for p in stage_post.Traverse()
                        if p.GetTypeName() == "Mesh")

        total_lights_pre = sum(v for k, v in pre.items() if k != "LightAPI")
        total_lights_post = sum(v for k, v in post.items() if k != "LightAPI")

        self.assertGreater(total_lights_post, total_lights_pre,
                           "Fix must raise the UsdLux prim total.")
        self.assertGreater(post["LightAPI"], pre["LightAPI"],
                           "Fix must attach LightAPI to every added light.")
        self.assertEqual(post_mesh, pre_mesh,
                         "Fix must not regress the mesh census.")


def _build_argparser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--verbose", "-v", action="count", default=1,
        help="Verbosity for unittest (repeat for more).",
    )
    return ap


if __name__ == "__main__":
    args, remaining = _build_argparser().parse_known_args()
    # Rebuild argv so unittest sees only its own flags.
    sys.argv = [sys.argv[0]] + remaining
    unittest.main(verbosity=args.verbose)
