# Copyright 2026 Miris Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
"""
MAX-LIT-AFFECT-025 — a V-Ray light's per-channel gating must reach USD.

Background
----------
A VRayLight can be excluded from diffuse, from specular, and from reflections independently, each
with its own contribution multiplier. `VRayLightWriter` never read any of it: `CreateDiffuseAttr`
and `CreateSpecularAttr` appeared ONLY in the off-state branch (disabled light -> zero both), and
the MAXScript probe fetched neither `affect_*` nor `*_contribution`. Every light therefore lit
channels V-Ray does not let it light.

Measured on the Spectrum Center arena (185 VRayLights, probe 2026-08-21):

    affect_specular = false      49
    affect_diffuse = false       48
    affect_reflections = false  168
    non-unity contribution      167

The load-bearing case is LT-BOWL_FILL_BBALL: a 62.5x35ft `invisible` fill directly over the court,
`affect_diffuse=true` / `affect_specular=false` / `affect_reflections=false`. Diffuse-only in V-Ray.
Exported as a plain RectLight it lit specular as well, which did TWO things at once:

  * over-brightened the court (measured 1.84x the V-Ray reference), and
  * replaced the court's clear-coat reflections with a broad dull sheen, because the specular
    response of a 2187 sq ft source is a wide low-contrast smear rather than crisp highlights.

Those were reported as two separate defects ("court too bright", "court missing its gloss"). They
are one bug. Note what was RULED OUT first, so nobody re-treads it: the court's clear coat is
genuinely 0.0 in Max (`.coating=0.0`, so `coat=0.0` is a faithful translation, NOT the
MAX-MTLX-COAT-015 texmap-only gap); its diffuse map carries `colorSpace='srgb_texture'` correctly;
its Color_Correction wrapper is neutral (`gammaRGB=0.95`, everything else default); and the
roughness map is mean 0.50, so a glossiness/roughness inversion is nearly a no-op on it.

Run: `hython test_miris_max_lit_affect_025.py`
"""
import unittest


def affect_to_usd(affect_diffuse=True, affect_specular=True,
                  diffuse_contribution=1.0, specular_contribution=1.0,
                  has_affect=True, is_on=True):
    """Mirror of the writer: (inputs:diffuse, inputs:specular).

    `has_affect` false means the light class exposed none of these properties, in which case
    nothing is authored and UsdLux defaults (1.0/1.0) apply -- the pre-025 behaviour.
    `is_on` false is the pre-existing off-state branch, which runs AFTER and must win.
    """
    if not is_on:
        return (0.0, 0.0)
    if not has_affect:
        return (1.0, 1.0)
    d = max(0.0, diffuse_contribution) if affect_diffuse else 0.0
    s = max(0.0, specular_contribution) if affect_specular else 0.0
    return (d, s)


class AffectGating(unittest.TestCase):

    def test_the_court_fill_light_becomes_diffuse_only(self):
        """LT-BOWL_FILL_BBALL, the light that caused both court symptoms."""
        self.assertEqual(
            affect_to_usd(affect_diffuse=True, affect_specular=False,
                          diffuse_contribution=1.0, specular_contribution=1.0),
            (1.0, 0.0))

    def test_specular_contribution_is_ignored_when_the_channel_is_off(self):
        """V-Ray keeps the multiplier at 1.0 while the checkbox is off; honouring the multiplier
        instead of the checkbox would put the specular smear straight back."""
        self.assertEqual(
            affect_to_usd(affect_specular=False, specular_contribution=1.0)[1], 0.0)
        self.assertEqual(
            affect_to_usd(affect_specular=False, specular_contribution=5.0)[1], 0.0)

    def test_diffuse_only_and_specular_only_are_both_expressible(self):
        """48 lights in the arena are specular-only (affect_diffuse=false) and 49 are
        diffuse-only. Both directions must survive the round trip."""
        self.assertEqual(affect_to_usd(affect_diffuse=False, affect_specular=True), (0.0, 1.0))
        self.assertEqual(affect_to_usd(affect_diffuse=True, affect_specular=False), (1.0, 0.0))

    def test_non_unity_contributions_pass_through(self):
        """167 of 185 lights carry a non-unity contribution. Dropping these was silently
        re-weighting most of the scene."""
        self.assertEqual(
            affect_to_usd(diffuse_contribution=0.5, specular_contribution=0.25),
            (0.5, 0.25))

    def test_negative_contribution_clamps_to_zero(self):
        """UsdLux treats these as scale factors; a negative would subtract light."""
        self.assertEqual(
            affect_to_usd(diffuse_contribution=-1.0, specular_contribution=-3.0),
            (0.0, 0.0))

    def test_a_light_class_without_the_properties_is_byte_identical_to_before(self):
        """The surgical-scope invariant: probing a light that exposes no affect_* properties must
        author nothing, so scenes that never used the feature are unchanged by this fix."""
        self.assertEqual(affect_to_usd(has_affect=False), (1.0, 1.0))

    def test_the_off_state_still_wins(self):
        """The disabled-light branch runs after this one. A light that is OFF must end at zero even
        if its gating says both channels are fully on -- otherwise MAX-LIT-* off-state handling
        regresses."""
        self.assertEqual(
            affect_to_usd(affect_diffuse=True, affect_specular=True,
                          diffuse_contribution=1.0, specular_contribution=1.0, is_on=False),
            (0.0, 0.0))

    def test_reflections_cannot_be_expressed_and_we_do_not_pretend_otherwise(self):
        """UsdLux has no reflection-only control, and 168 of the arena's lights set
        affect_reflections=false. Policy: when specular is ON but reflections are OFF, KEEP
        specular -- dropping it would lose a highlight V-Ray does render. This pins the policy so a
        future 'tidy-up' does not silently fold reflections into specular."""
        d, s = affect_to_usd(affect_specular=True, specular_contribution=1.0)
        self.assertEqual(s, 1.0,
                         "specular must survive affect_reflections=false; the residual "
                         "over-brightness in reflections needs a renderer-specific attribute")


if __name__ == "__main__":
    unittest.main(verbosity=2)
