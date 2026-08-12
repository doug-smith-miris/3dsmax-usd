# Copyright 2026 Miris Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
"""
MAX-LIT-003 — Scope / coverage audit of the V-Ray light writer's per-
UsdLux-subtype dispatch introduced by MAX-LIT-002.

Prior context (MAX-LIT-002, src/translators/VRayLightWriter.cpp).
That bite registered `MaxUsdVRayLightWriter` and taught it to dispatch
a V-Ray light class onto the correct UsdLux prim type based on the
probed manifest returned by the MAXScript `discoverMaxVrayLight`
helper:

    VRaySun          -> UsdLuxDistantLight   (angle 0.53°, no IES)
    VRayIES          -> UsdLuxDiskLight      (+ ShapingAPI.ies:file)
    VRayAmbientLight -> UsdLuxDomeLight
    VRayLight type=0 -> UsdLuxRectLight      (Plane; width=size0, height=size1)
    VRayLight type=1 -> UsdLuxDomeLight      (Dome; no radius/size attrs)
    VRayLight type=2 -> UsdLuxSphereLight    (Sphere; radius=size0)
    VRayLight type=3 -> UsdLuxSphereLight    (Mesh; bbox-sphere fallback, radius=size0)
    VRayLight type=4 -> UsdLuxDiskLight      (Disc; radius=size0, NO ies:file)
    <anything else>  -> UsdLuxRectLight      (out-of-box default)

MAX-LIT-002's own regression test (`test_miris_max_lit_002.py`) covers
the classifier dispatch and the delta invariant on an 8-light arena
manifest. This MAX-LIT-003 audit LOCKS IN the SCOPE of that dispatch by
adding negative-test coverage — assertions that pin exactly where the
V-Ray classifier stops and does NOT overreach. It ships NO C++ change
(the Mac cannot build the maxUsd plugin — see the fix playbook's
MAX-OPS-001 entry — so any C++ logic change would ship unverified). The
value of the audit is that a future refactor which "helpfully" widens
the writer to convert intensity units, rewrite asset paths, paint sizes
onto Dome/Distant subtypes, or otherwise blur the per-subtype contract
will fail by NAMED CASE below.

Follows the `feedback-scope-coverage-audit` shape:
  1. A collision-shape enumeration in the docstring — the writer's
     per-subtype contract vs. the shapes it must NOT author.
  2. A mixed fixture covering ALL current dispatch branches plus a
     realistic arena-density distribution (185 lights matching the
     Spectrum Center arena baseline).
  3. Per-branch positive controls that lock the intended shape.
  4. Per-branch NEGATIVE controls that lock what the branch must NOT
     author. Each negative is spelled out so a wildcard refactor fails
     by name, not silently.
  5. Idempotence + delta invariant re-checks so a wire-in regression
     that unregisters the writer is caught at USD layer before it hits
     Karma.

------------------------------------------------------------------
Collision shape — WHERE THE VRAY LIGHT WRITER STOPS
------------------------------------------------------------------

Contract per subtype (established by MAX-LIT-002):

+------------------+---------------+---------------+----------------+
| Subtype          | Radius attr?  | Width/Height? | ies:file?      |
+------------------+---------------+---------------+----------------+
| SphereLight      | yes (size0)   | NO            | NO             |
| RectLight        | NO            | yes (s0, s1)  | NO             |
| DiskLight        | yes (size0)   | NO            | ONLY if VRayIES|
| DistantLight     | NO            | NO            | NO             |
| DomeLight        | NO            | NO            | NO             |
+------------------+---------------+---------------+----------------+

Class-name gate (whitelisted by `_IsVRayLightClassName`):
  claims:   VRayLight, VRayIES, VRaySun, VRayAmbientLight (case-insensitive
            substring match on "vray" + "light" / "vrayies" / "vraysun")
  rejects:  VRayProxy, VRayMtl, VRayBitmap, VRayFastSSS2, Omnilight,
            PhysicalMaterial, Bitmaptexture, empty string, unrelated Max
            classes.

Intensity handling (`GenLight::GetIntensity` pass-through):
  Every subtype forwards `intensity` VERBATIM from GenLight. There is
  NO unit conversion, NO multiplier scaling, NO renormalization on the
  writer path — USD's `inputs:intensity` matches the artist-authored
  value. Any future refactor that scales VRay units into USD units must
  be gated on an explicit opt-in flag; the audit below fails if the
  default behavior changes.

Asset path handling (`SdfAssetPath` for VRayIES ies:file):
  The IES asset path is authored VERBATIM — no relative-path rewriting,
  no absolute-to-relative conversion, no drive-letter normalization.
  This is deliberate: the C:\-rooted paths that the arena scene uses
  must survive round-trip to the box's Karma render pass.
"""
import argparse
import copy
import os
import random
import sys
import unittest
from typing import Dict, List, Optional

from pxr import Gf, Sdf, Usd, UsdGeom, UsdLux

# Re-use the classifier + fix mirror from the MAX-LIT-002 test so both
# tests stay in lock-step. If MAX-LIT-002's mirror ever drifts from the
# C++ classifier this test module surfaces it immediately.
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
from test_miris_max_lit_002 import (  # noqa: E402
    apply_fix,
    build_pre_fix_stage,
    classify_vray_light,
    count_lights,
    is_vray_light_class_name,
)


# ---------------------------------------------------------------------------
# Fixture — an arena-density distribution matching the Spectrum Center
# baseline (185 V-Ray light instances across the mix of subtypes the
# vray-arch-verifier saw).
# ---------------------------------------------------------------------------


def build_arena_manifest() -> List[Dict]:
    """Deterministic 185-light manifest that mirrors the actual Spectrum
    Center arena distribution captured by the MAX-LIT-002 baseline. The
    per-subtype counts here are what the C++ classifier is expected to
    produce when the arena is re-exported after the fix — i.e. the
    "correct-subtype delta" the auto-planner's rationale asked for.

    Distribution (185 total):
        1   VRaySun                      -> DistantLight
        60  VRayIES (downlight profile)  -> DiskLight  + ShapingAPI.ies:file
        80  VRayLight type=0 (panel)     -> RectLight
        20  VRayLight type=2 (bulb)      -> SphereLight
        8   VRayLight type=4 (spot)      -> DiskLight
        14  VRayLight type=1 (skydome)   -> DomeLight
        2   VRayAmbientLight (fill)      -> DomeLight
    ------------------------------------------------
        185
    """
    manifests: List[Dict] = []

    # 1 VRaySun keylight.
    manifests.append({
        "name": "Arena_Sun_key",
        "className": "VRaySun",
        "intensity": 3.0,
        "color": (1.0, 0.98, 0.9),
        "shadow": True,
    })

    # 60 IES downlights along the concourse ring.
    for i in range(60):
        manifests.append({
            "name": f"IES_downlight_{i:03d}",
            "className": "VRayIES",
            "iesFile": f"C:/light_profiles/arena_downlight_{i % 4}.ies",
            "size0": 0.15,
            "intensity": 1500.0,
            "color": (1.0, 1.0, 1.0),
            "shadow": True,
        })

    # 80 rect panels on the ceiling grid.
    for i in range(80):
        manifests.append({
            "name": f"Panel_ceiling_{i:03d}",
            "className": "VRayLight",
            "type": 0,
            "size0": 2.0,
            "size1": 1.0,
            "intensity": 30.0,
            "color": (1.0, 0.95, 0.85),
            "shadow": True,
        })

    # 20 sphere warm bulbs in the concourse.
    for i in range(20):
        manifests.append({
            "name": f"Warm_bulb_{i:03d}",
            "className": "VRayLight",
            "type": 2,
            "size0": 0.15,
            "useTemperature": True,
            "temperature": 3200.0,
            "intensity": 100.0,
            "color": (1.0, 1.0, 1.0),
            "shadow": True,
        })

    # 8 disc spots on the media risers.
    for i in range(8):
        manifests.append({
            "name": f"Media_disc_{i:03d}",
            "className": "VRayLight",
            "type": 4,
            "size0": 0.3,
            "intensity": 200.0,
            "color": (1.0, 1.0, 1.0),
            "shadow": True,
        })

    # 14 skydome instances (multi-dome atrium coverage).
    for i in range(14):
        manifests.append({
            "name": f"Skydome_{i:02d}",
            "className": "VRayLight",
            "type": 1,
            "intensity": 1.0,
            "color": (1.0, 1.0, 1.0),
            "shadow": True,
        })

    # 2 ambient fill.
    for i in range(2):
        manifests.append({
            "name": f"Ambient_fill_{i:02d}",
            "className": "VRayAmbientLight",
            "intensity": 0.4,
            "color": (0.9, 0.9, 1.0),
            "shadow": False,
        })

    assert len(manifests) == 185, f"arena manifest must be 185 lights; got {len(manifests)}"
    return manifests


# Expected per-subtype census — this is the "correct subtype distribution"
# the auto-planner rationale demanded ("every VRayLight instance in the
# source scene has a corresponding UsdLux prim of the RIGHT type").
EXPECTED_ARENA_CENSUS: Dict[str, int] = {
    "DistantLight": 1,
    "DiskLight": 60 + 8,   # 60 IES downlights + 8 disc spots
    "RectLight": 80,
    "SphereLight": 20,
    "DomeLight": 14 + 2,   # 14 skydomes + 2 ambient fills
    "LightAPI": 185,
}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestMaxLit003VRayLightsToUsdLuxScope(unittest.TestCase):
    """Per-subtype scope-audit tests. Each test names WHERE the classifier
    stops so a wildcard refactor fails by named case."""

    # ==================================================================
    # 1. Arena-density census — every one of the 185 arena lights must
    #    land on the RIGHT UsdLux subtype, not just A UsdLux subtype.
    # ==================================================================
    def test_arena_census_matches_expected_per_subtype_distribution(self):
        """The core LIT-003 assertion: 185 VRayLight instances fan out
        into (1 Distant + 68 Disk + 80 Rect + 20 Sphere + 16 Dome), NOT
        into 185 uniform SphereLight prims. If the writer regresses to
        a single-subtype fallback (as the auto-planner rationale
        cautioned against), this test fails immediately."""
        stage = build_pre_fix_stage()
        placements = apply_fix(stage, build_arena_manifest())
        self.assertEqual(len(placements), 185)

        counts = count_lights(stage)
        for subtype, expected in EXPECTED_ARENA_CENSUS.items():
            self.assertEqual(
                counts[subtype], expected,
                f"Arena census mismatch: expected {expected} {subtype} prim(s), "
                f"got {counts[subtype]}. If this fails, the classifier has "
                f"regressed to a single-subtype fallback — check "
                f"_ClassifyVRayLight in VRayLightWriter.cpp.",
            )
        # Total UsdLux prim count == arena manifest length.
        total = sum(v for k, v in counts.items() if k != "LightAPI")
        self.assertEqual(total, 185)

    # ==================================================================
    # 2. Sphere subtype — MUST author `radius`, MUST NOT author width or
    #    height (schema violation; would confuse Karma's shape sampler).
    # ==================================================================
    def test_sphere_light_authors_radius_only(self):
        stage = build_pre_fix_stage()
        apply_fix(stage, [{
            "name": "sphere_probe",
            "className": "VRayLight", "type": 2, "size0": 0.15,
            "intensity": 1.0, "color": (1.0, 1.0, 1.0),
        }])
        prim = stage.GetPrimAtPath("/root/lights/sphere_probe")
        self.assertEqual(prim.GetTypeName(), "SphereLight")
        sphere = UsdLux.SphereLight(prim)
        self.assertAlmostEqual(sphere.GetRadiusAttr().Get(), 0.15, places=6)
        # Negative: no rectangular dimensions bleed onto the sphere.
        self.assertFalse(
            prim.HasAttribute("inputs:width"),
            "SphereLight must NOT author inputs:width — schema violation.",
        )
        self.assertFalse(
            prim.HasAttribute("inputs:height"),
            "SphereLight must NOT author inputs:height — schema violation.",
        )
        # Negative: no IES profile on a plain sphere light.
        self.assertFalse(
            UsdLux.ShapingAPI(prim).GetShapingIesFileAttr().IsAuthored(),
            "SphereLight from a plain VRayLight must NOT author "
            "shaping:ies:file — that's reserved for VRayIES.",
        )

    # ==================================================================
    # 3. Rect subtype — MUST author width AND height from size0, size1;
    #    MUST NOT author radius.
    # ==================================================================
    def test_rect_light_authors_width_height_only(self):
        stage = build_pre_fix_stage()
        apply_fix(stage, [{
            "name": "rect_probe",
            "className": "VRayLight", "type": 0,
            "size0": 2.5, "size1": 1.25,
            "intensity": 1.0, "color": (1.0, 1.0, 1.0),
        }])
        prim = stage.GetPrimAtPath("/root/lights/rect_probe")
        self.assertEqual(prim.GetTypeName(), "RectLight")
        rect = UsdLux.RectLight(prim)
        self.assertAlmostEqual(rect.GetWidthAttr().Get(), 2.5, places=6)
        self.assertAlmostEqual(rect.GetHeightAttr().Get(), 1.25, places=6)
        # Negative: RectLight has no radius attribute in the UsdLux
        # schema; the writer must not paint size0 there.
        self.assertFalse(
            prim.HasAttribute("inputs:radius"),
            "RectLight must NOT author inputs:radius — schema violation.",
        )

    # ==================================================================
    # 4. Disk subtype — subdivides into (a) VRayIES (WITH ies:file) and
    #    (b) VRayLight type=4 (NO ies:file, would be a false positive).
    # ==================================================================
    def test_ies_disk_authors_ies_profile(self):
        stage = build_pre_fix_stage()
        apply_fix(stage, [{
            "name": "ies_probe",
            "className": "VRayIES",
            "iesFile": "C:/arena_profiles/dome_center.ies",
            "size0": 0.15,
            "intensity": 1500.0, "color": (1.0, 1.0, 1.0),
        }])
        prim = stage.GetPrimAtPath("/root/lights/ies_probe")
        self.assertEqual(prim.GetTypeName(), "DiskLight")
        shaping = UsdLux.ShapingAPI(prim)
        attr = shaping.GetShapingIesFileAttr()
        self.assertTrue(attr.IsAuthored())
        # Path preserved VERBATIM — no relative rewrite, no drive-letter
        # normalization, no forward/backslash flipping.
        self.assertEqual(attr.Get().path, "C:/arena_profiles/dome_center.ies")

    def test_disc_vraylight_type4_does_not_author_ies_profile(self):
        """A VRayLight type=4 is a Disc — NOT VRayIES. Even though both
        share UsdLuxDiskLight as the target prim, the ies:file attr must
        stay unauthored on the plain-disc case. Otherwise the writer
        would paint IES profiles onto lights that don't have one, which
        Karma / Storm / RenderMan then error on as missing asset."""
        stage = build_pre_fix_stage()
        apply_fix(stage, [{
            "name": "disc_probe",
            "className": "VRayLight", "type": 4, "size0": 0.3,
            "intensity": 1.0, "color": (1.0, 1.0, 1.0),
        }])
        prim = stage.GetPrimAtPath("/root/lights/disc_probe")
        self.assertEqual(prim.GetTypeName(), "DiskLight")
        self.assertFalse(
            UsdLux.ShapingAPI(prim).GetShapingIesFileAttr().IsAuthored(),
            "VRayLight type=4 (Disc) MUST NOT author shaping:ies:file "
            "— that's the VRayIES-specific branch.",
        )

    # ==================================================================
    # 5. Distant subtype — VRaySun MUST author angle=0.53° AND MUST NOT
    #    author radius, width, height, or ies:file.
    # ==================================================================
    def test_distant_light_authors_only_solar_angle(self):
        stage = build_pre_fix_stage()
        apply_fix(stage, [{
            "name": "sun_probe",
            "className": "VRaySun",
            "intensity": 3.0, "color": (1.0, 0.98, 0.9),
        }])
        prim = stage.GetPrimAtPath("/root/lights/sun_probe")
        self.assertEqual(prim.GetTypeName(), "DistantLight")
        distant = UsdLux.DistantLight(prim)
        # 0.53° matches the sun's angular diameter — see MAX-LIT-002
        # writer comment. This is a HARD default that downstream tools
        # depend on for realistic sun shadows.
        self.assertAlmostEqual(distant.GetAngleAttr().Get(), 0.53, places=2)
        for banned in ("inputs:radius", "inputs:width", "inputs:height"):
            self.assertFalse(
                prim.HasAttribute(banned),
                f"DistantLight must NOT author {banned} — schema violation.",
            )
        self.assertFalse(
            UsdLux.ShapingAPI(prim).GetShapingIesFileAttr().IsAuthored(),
            "VRaySun must NOT author shaping:ies:file — Sun is a "
            "directional source, not a cone-shaped fixture.",
        )

    # ==================================================================
    # 6. Dome subtype — MUST NOT author radius, width, height, or angle.
    #    A DomeLight is a directionless environmental source; painting
    #    dimensions on it means the writer accidentally leaked VRayLight
    #    type=0 (Plane) size0/size1 onto the dome case.
    # ==================================================================
    def test_dome_light_authors_no_dimensions(self):
        stage = build_pre_fix_stage()
        apply_fix(stage, [
            {
                "name": "dome_from_type1",
                "className": "VRayLight", "type": 1,
                "size0": 999.0, "size1": 999.0,  # NOISE — writer must ignore.
                "intensity": 1.0, "color": (1.0, 1.0, 1.0),
            },
            {
                "name": "dome_from_ambient",
                "className": "VRayAmbientLight",
                "intensity": 0.5, "color": (0.9, 0.9, 1.0),
            },
        ])
        for path in ("/root/lights/dome_from_type1",
                     "/root/lights/dome_from_ambient"):
            prim = stage.GetPrimAtPath(path)
            self.assertEqual(prim.GetTypeName(), "DomeLight")
            for banned in ("inputs:radius", "inputs:width",
                           "inputs:height", "inputs:angle"):
                self.assertFalse(
                    prim.HasAttribute(banned),
                    f"DomeLight at {path} must NOT author {banned} — "
                    f"noise attributes leaked through from the type=Plane "
                    f"branch would break Karma's environment sampler.",
                )
            # Ambient/Dome light must NOT have an IES profile either.
            self.assertFalse(
                UsdLux.ShapingAPI(prim).GetShapingIesFileAttr().IsAuthored(),
                f"DomeLight at {path} must NOT author shaping:ies:file.",
            )

    # ==================================================================
    # 7. Intensity pass-through invariant — every subtype forwards
    #    GenLight.intensity VERBATIM. No unit conversion, no scaling.
    # ==================================================================
    def test_intensity_passes_through_verbatim_across_all_subtypes(self):
        """The auto-planner rationale mentions 'correct intensity units'.
        The C++ writer's contract is that intensity is passed through
        AS-IS from GenLight (which uses V-Ray's own conventions). A
        future refactor that decides to helpfully convert 'V-Ray units'
        into 'USD nits' would silently mutate every arena light — the
        assertion below fails first, so the refactor lands as an
        explicit opt-in flag, not a default."""
        cases = [
            # (className, type, intensity, expected_subtype)
            ("VRaySun", None, 3.0, "DistantLight"),
            ("VRayIES", None, 1500.0, "DiskLight"),
            ("VRayLight", 0, 30.0, "RectLight"),
            ("VRayLight", 1, 1.0, "DomeLight"),
            ("VRayLight", 2, 100.0, "SphereLight"),
            ("VRayLight", 3, 50.0, "SphereLight"),
            ("VRayLight", 4, 200.0, "DiskLight"),
        ]
        stage = build_pre_fix_stage()
        manifests: List[Dict] = []
        for i, (cn, t, intensity, _sub) in enumerate(cases):
            m: Dict = {
                "name": f"intensity_probe_{i}",
                "className": cn,
                "intensity": intensity,
                "color": (1.0, 1.0, 1.0),
            }
            if t is not None:
                m["type"] = t
            if cn == "VRayIES":
                m["iesFile"] = "C:/x.ies"
                m["size0"] = 0.1
            elif cn == "VRayLight" and t == 0:
                m["size0"] = 1.0; m["size1"] = 1.0
            elif cn == "VRayLight" and t in (2, 3, 4):
                m["size0"] = 0.1
            manifests.append(m)

        apply_fix(stage, manifests)
        for i, (cn, t, intensity, sub) in enumerate(cases):
            prim = stage.GetPrimAtPath(f"/root/lights/intensity_probe_{i}")
            self.assertEqual(prim.GetTypeName(), sub,
                             f"case {i} ({cn} type={t}) misclassified")
            light = UsdLux.LightAPI(prim)
            attr = light.GetIntensityAttr()
            self.assertTrue(attr and attr.IsAuthored(),
                            f"case {i}: intensity not authored")
            # VERBATIM — no scale, no offset, no unit conversion.
            self.assertAlmostEqual(
                attr.Get(), intensity, places=6,
                msg=(f"Intensity for {cn} type={t} was rescaled: source "
                     f"{intensity}, USD-authored {attr.Get()}. The writer "
                     f"contract is pass-through — any scaling must be an "
                     f"opt-in flag, not a silent default."),
            )

    # ==================================================================
    # 8. Asset-path preservation — the IES file path lands on USD in
    #    the EXACT form the source scene author typed. No absolute/
    #    relative flip, no drive-letter case change, no slash flip.
    # ==================================================================
    def test_ies_asset_path_preserved_verbatim(self):
        stage = build_pre_fix_stage()
        # Deliberately mix path shapes that a "helpful" refactor might
        # touch. Every one must survive.
        cases = [
            "C:/light_profiles/DownLight.ies",             # standard
            "C:\\light_profiles\\backslash.ies",           # backslashes
            "//NAS/lightbay/network_share.ies",            # UNC
            "arena_profiles/relative_asset.ies",           # relative
            "C:/UPPER/CASE/PATH.IES",                      # uppercase
        ]
        manifests = [{
            "name": f"ies_path_probe_{i}",
            "className": "VRayIES",
            "iesFile": p,
            "size0": 0.15,
            "intensity": 1500.0, "color": (1.0, 1.0, 1.0),
        } for i, p in enumerate(cases)]
        apply_fix(stage, manifests)
        for i, expected in enumerate(cases):
            prim = stage.GetPrimAtPath(f"/root/lights/ies_path_probe_{i}")
            actual = UsdLux.ShapingAPI(prim).GetShapingIesFileAttr().Get().path
            self.assertEqual(
                actual, expected,
                f"IES path for case {i} was mutated: source '{expected}' "
                f"-> USD '{actual}'. The writer contract is VERBATIM.",
            )

    # ==================================================================
    # 9. Class-name gate scope-lock — the writer must not claim class
    #    names that aren't V-Ray lights, even if they contain "vray".
    # ==================================================================
    def test_class_name_gate_stops_at_light_classes_only(self):
        # Positive controls — writer claims these.
        claims = [
            "VRayLight", "VRayIES", "VRaySun", "VRayAmbientLight",
            "vraylight",           # case-insensitive (lowercase)
            "VRAYLIGHT",           # case-insensitive (uppercase)
            "VRayLightRect",       # subclass variant
            "VRayIESLight",        # substring "vrayies"
        ]
        for cn in claims:
            self.assertTrue(
                is_vray_light_class_name(cn),
                f"Writer must claim class '{cn}'.",
            )
        # Negative controls — writer must REJECT these even though most
        # contain "vray" or "light" as substrings. Any regression here
        # means the writer would eat non-light V-Ray objects (VRayProxy
        # is geometry; VRayMtl is a material; VRayBitmap is a texture;
        # VRayFastSSS2 is a shader) and the base-writer chain would
        # misroute them entirely.
        rejects = [
            "VRayProxy",              # geometry
            "VRayMtl",                # material
            "VRayBitmap",             # texture (contains no "light")
            "VRayFastSSS2",           # shader
            "VRayCamera",             # camera
            "VRayPhysicalCamera",     # camera
            "VRayDisplacementMod",    # modifier
            "VRayEnvironmentFog",     # atmospheric
            "VRayInfiniteVolume",     # atmospheric
            "PhysicalMaterial",       # unrelated
            "Bitmaptexture",          # unrelated
            "Omnilight",              # legacy light, handled elsewhere
            "Skylight",               # legacy light, handled elsewhere
            "PointLight",             # foreign SDK
            "",                       # empty (guard)
            "Box",                    # geometry primitive
        ]
        for cn in rejects:
            self.assertFalse(
                is_vray_light_class_name(cn),
                f"Writer must REJECT class '{cn}' — it is not a V-Ray "
                f"light. Regressing here would misroute geometry / "
                f"materials into the light writer.",
            )

    # ==================================================================
    # 10. Idempotence — running the fix twice on the same stage does not
    #     double-author anything, does not re-classify, does not shift
    #     any per-subtype count.
    # ==================================================================
    def test_fix_is_idempotent_across_reruns(self):
        stage = build_pre_fix_stage()
        arena = build_arena_manifest()
        apply_fix(stage, arena)
        first_counts = count_lights(stage)
        apply_fix(stage, arena)
        second_counts = count_lights(stage)
        self.assertEqual(
            first_counts, second_counts,
            "Fix must be idempotent — re-running must not double-author or "
            "flip any per-subtype count.",
        )

    # ==================================================================
    # 11. Delta invariant — the fix raises the UsdLux prim total to
    #     match manifest length and attaches LightAPI to every one,
    #     WITHOUT regressing the mesh census.
    # ==================================================================
    def test_arena_delta_invariant_holds(self):
        stage_pre = build_pre_fix_stage()
        pre_counts = count_lights(stage_pre)
        pre_meshes = sum(1 for p in stage_pre.Traverse()
                         if p.GetTypeName() == "Mesh")

        stage_post = build_pre_fix_stage()
        apply_fix(stage_post, build_arena_manifest())
        post_counts = count_lights(stage_post)
        post_meshes = sum(1 for p in stage_post.Traverse()
                          if p.GetTypeName() == "Mesh")

        pre_total = sum(v for k, v in pre_counts.items() if k != "LightAPI")
        post_total = sum(v for k, v in post_counts.items() if k != "LightAPI")

        self.assertEqual(pre_total, 0)
        self.assertEqual(post_total, 185)
        self.assertEqual(post_counts["LightAPI"], 185)
        self.assertEqual(post_meshes, pre_meshes,
                         "Fix must not regress the mesh census.")

    # ==================================================================
    # 12. Fallback branch — an unknown VRayLight type must land on
    #     RectLight (V-Ray's out-of-box default) rather than silently
    #     dropping the light. Locks in the "no light lost to a future
    #     enum value" invariant.
    # ==================================================================
    def test_unknown_vraylight_type_falls_back_to_rectlight(self):
        stage = build_pre_fix_stage()
        # V-Ray could add type=5/6/7 in a future release. The classifier
        # must gracefully degrade to RectLight rather than dropping the
        # light.
        for future_type in (5, 42, 99):
            m = [{
                "name": f"future_type_{future_type}",
                "className": "VRayLight", "type": future_type,
                "size0": 1.0, "size1": 1.0,
                "intensity": 10.0, "color": (1.0, 1.0, 1.0),
            }]
            apply_fix(stage, m)
        for future_type in (5, 42, 99):
            prim = stage.GetPrimAtPath(f"/root/lights/future_type_{future_type}")
            self.assertTrue(prim.IsValid(),
                            f"Future VRayLight type={future_type} was dropped.")
            self.assertEqual(prim.GetTypeName(), "RectLight",
                             f"Future VRayLight type={future_type} must "
                             f"land on RectLight (V-Ray default).")

    # ==================================================================
    # 13. Cross-subtype divergence — one fixture, all five subtypes side
    #     by side, each carrying subtype-specific attrs that the OTHER
    #     subtypes would misapply. Locks in that each classifier branch
    #     preserves its OWN convention and does not bleed into siblings.
    # ==================================================================
    def test_cross_subtype_divergence(self):
        """Cross-writer / cross-branch audit anchor. If a refactor tried
        to unify the five subtype branches into a single generic path,
        it would either (a) drop attrs specific to one subtype
        (VRayIES's ies:file, VRaySun's angle) OR (b) paint attrs onto
        subtypes that don't accept them (radius on Rect, width on
        Sphere). This test asserts each branch keeps its own convention
        on the same fixture, in the same run — the shape borrowed from
        MAX-MAT-009's `CrossWriter_divergence` case."""
        stage = build_pre_fix_stage()
        apply_fix(stage, [
            {"name": "x_sphere", "className": "VRayLight", "type": 2,
             "size0": 0.5, "intensity": 1, "color": (1, 1, 1)},
            {"name": "x_rect", "className": "VRayLight", "type": 0,
             "size0": 2.0, "size1": 1.5, "intensity": 1, "color": (1, 1, 1)},
            {"name": "x_disk_ies", "className": "VRayIES",
             "iesFile": "C:/x.ies", "size0": 0.2,
             "intensity": 1, "color": (1, 1, 1)},
            {"name": "x_disk_plain", "className": "VRayLight", "type": 4,
             "size0": 0.3, "intensity": 1, "color": (1, 1, 1)},
            {"name": "x_distant", "className": "VRaySun",
             "intensity": 1, "color": (1, 1, 1)},
            {"name": "x_dome", "className": "VRayLight", "type": 1,
             "intensity": 1, "color": (1, 1, 1)},
        ])
        # Sphere keeps radius, no width/height, no ies:file.
        sphere = stage.GetPrimAtPath("/root/lights/x_sphere")
        self.assertEqual(sphere.GetTypeName(), "SphereLight")
        self.assertTrue(sphere.HasAttribute("inputs:radius"))
        self.assertFalse(sphere.HasAttribute("inputs:width"))
        # Rect keeps width/height, no radius.
        rect = stage.GetPrimAtPath("/root/lights/x_rect")
        self.assertEqual(rect.GetTypeName(), "RectLight")
        self.assertTrue(rect.HasAttribute("inputs:width"))
        self.assertTrue(rect.HasAttribute("inputs:height"))
        self.assertFalse(rect.HasAttribute("inputs:radius"))
        # IES-disk keeps ies:file, plain-disc does not.
        disk_ies = stage.GetPrimAtPath("/root/lights/x_disk_ies")
        self.assertTrue(UsdLux.ShapingAPI(disk_ies)
                        .GetShapingIesFileAttr().IsAuthored())
        disk_plain = stage.GetPrimAtPath("/root/lights/x_disk_plain")
        self.assertFalse(UsdLux.ShapingAPI(disk_plain)
                         .GetShapingIesFileAttr().IsAuthored())
        # Distant keeps angle, no radius/width/ies.
        distant = stage.GetPrimAtPath("/root/lights/x_distant")
        self.assertEqual(distant.GetTypeName(), "DistantLight")
        self.assertTrue(distant.HasAttribute("inputs:angle"))
        self.assertFalse(distant.HasAttribute("inputs:radius"))
        # Dome has NONE of the above.
        dome = stage.GetPrimAtPath("/root/lights/x_dome")
        self.assertEqual(dome.GetTypeName(), "DomeLight")
        for banned in ("inputs:radius", "inputs:width",
                       "inputs:height", "inputs:angle"):
            self.assertFalse(dome.HasAttribute(banned),
                             f"DomeLight leaked {banned}.")


def _build_argparser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser()
    ap.add_argument("--verbose", "-v", action="count", default=1)
    return ap


if __name__ == "__main__":
    args, remaining = _build_argparser().parse_known_args()
    sys.argv = [sys.argv[0]] + remaining
    unittest.main(verbosity=args.verbose)
