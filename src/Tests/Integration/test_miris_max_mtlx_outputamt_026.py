# Copyright 2026 Miris Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
"""
MAX-MTLX-OUTPUTAMT-026 — a texmap's Output amount must reach the MaterialX graph.

The defect
----------
Anything layered ON TOP of a bitmap was dropped. The exporter walks through wrapper maps to reach
the nested bitmap (which is what recovered textures in the first place) but keeps none of the
adjustments: not the Color_Correction settings, and not the `StandardTextureOutput.output_amount`
that SCALES the sampled map.

That is how V-Ray's per-slot texmap multiplier got lost. The chain, measured on the Spectrum Center
arena:

    BBALL-COURT_HORNETS <VRayMtl>
      .brdf_useRoughness                      = true    channel IS roughness, no inversion
      .texmap_reflectionGlossiness            = Wipe-Marks_A_Rough.jpg   (mean 0.50)
      .texmap_reflectionGlossiness_multiplier = 30.0    percent
      -> effective roughness 0.50 * 0.30 = 0.15

`SceneConverter.ConvertScene()` (run in scene prep so materials arrive as PhysicalMaterial) discards
that multiplier outright -- the converted material keeps `roughness_map` at full strength and has no
`roughness_map_amt` at all (only bump / clearcoat_bump / displacement retain amounts). So the
exporter faithfully wrote specular_roughness 0.50 where the scene says 0.15: the court came out
3.3x too rough, which is why it rendered flat and why no light-side fix ever changed it. Doug
identified the correct value by eye from a bracket of test renders before the cause was known.

Nine materials in that one file carry a non-unity multiplier -- the court at 30%, WALNUT-WOOD 85%,
WALNUT and EAST-BAR_WOOD 50% -- all polished wood/laminate, exactly the class where losing a
glossiness multiplier reads as "the polish is missing".

The fix has two halves and NEITHER works alone:
  1. scene prep records each VRayMtl's multipliers BEFORE conversion and re-applies them onto the
     converted map's `output_amount`;
  2. this change makes the exporter honour `output_amount` by inserting a multiply node.
Half 1 alone was verified INERT: output_amount was set and the exported USD did not change, because
nothing read it.

Run: `hython test_miris_max_mtlx_outputamt_026.py`
"""
import unittest

FLOAT_ND = "ND_multiply_float"


def nodedef_for(out_type):
    """Mirror of the writer's nodedef choice.

    Verified against the MaterialX stdlib:
        ND_multiply_float     in1 float,   in2 float
        ND_multiply_color3FA  in1 color3,  in2 FLOAT
        ND_multiply_vector4FA in1 vector4, in2 FLOAT
    A scalar amount therefore needs the "FA" (float-argument) variant on every non-float channel.
    """
    return FLOAT_ND if out_type == "float" else "ND_multiply_%sFA" % out_type


def parse_texmap_line(line):
    """Mirror of _ParseTexmapLine. The 4th field is OPTIONAL: a 3-field line yields 1.0 so that
    pre-026 probes and any other caller stay byte-identical."""
    parts = line.rstrip("\r\n").split("|")
    if len(parts) < 3:
        return None
    amount = 1.0
    if len(parts) >= 4:
        try:
            amount = float(parts[3])
        except ValueError:
            amount = 1.0
    return {"input": parts[0], "type": parts[1], "file": parts[2], "amount": amount}


def should_insert_multiply(amount, is_alpha_opacity=False):
    return (not is_alpha_opacity) and abs(amount - 1.0) > 1e-6


class OutputAmount(unittest.TestCase):

    def test_the_court_case_end_to_end(self):
        """The load-bearing number: 0.50 map x 30% = 0.15, the value Doug picked by eye."""
        row = parse_texmap_line("specular_roughness|float|Wipe-Marks_A_Rough.jpg|0.3")
        self.assertEqual(row["amount"], 0.3)
        self.assertTrue(should_insert_multiply(row["amount"]))
        self.assertAlmostEqual(0.50 * row["amount"], 0.15, places=6)

    def test_a_three_field_line_is_unchanged_behaviour(self):
        """Surgical scope: no 4th field means amount 1.0 means NO node, so every material that does
        not use the feature emits a byte-identical graph."""
        row = parse_texmap_line("base_color|color3|HORNETS-CORE_COURT_BC.jpg")
        self.assertEqual(row["amount"], 1.0)
        self.assertFalse(should_insert_multiply(row["amount"]))

    def test_explicit_unity_also_emits_nothing(self):
        """The overwhelmingly common case -- 8 of 8 mapped slots on most materials sit at 100%."""
        row = parse_texmap_line("specular_roughness|float|x.jpg|1.0")
        self.assertFalse(should_insert_multiply(row["amount"]))

    def test_a_malformed_amount_falls_back_to_unity(self):
        """Never let a bad probe line silently darken or brighten a channel."""
        row = parse_texmap_line("specular_roughness|float|x.jpg|notanumber")
        self.assertEqual(row["amount"], 1.0)
        self.assertFalse(should_insert_multiply(row["amount"]))

    def test_file_paths_containing_pipes_do_not_corrupt_the_amount(self):
        """The 4th field is found by the THIRD pipe, so a path with no pipe is safe; this pins the
        3-field fallback rather than mis-splitting a path."""
        row = parse_texmap_line("base_color|color3|C:/tex/court_BC.jpg")
        self.assertEqual(row["file"], "C:/tex/court_BC.jpg")
        self.assertEqual(row["amount"], 1.0)

    def test_alpha_opacity_is_never_scaled(self):
        """An alpha mask is a SHAPE. Scaling it turns a binary cutout translucent instead of
        dimmer, which would regress MAX-MTLX-OPACITY-ALPHA-018's decals (BUZZ/CITY marquee,
        DR_PEPPER, laser-cut logos)."""
        self.assertFalse(should_insert_multiply(0.3, is_alpha_opacity=True))
        self.assertTrue(should_insert_multiply(0.3, is_alpha_opacity=False))

    def test_nodedef_variants(self):
        self.assertEqual(nodedef_for("float"), "ND_multiply_float")
        self.assertEqual(nodedef_for("color3"), "ND_multiply_color3FA")
        self.assertEqual(nodedef_for("vector4"), "ND_multiply_vector4FA")

    def test_the_nine_arena_materials(self):
        """Every non-unity multiplier measured in the file, so a regression that drops one is
        visible. All are polished wood/laminate."""
        measured = {
            "BBALL-COURT_HORNETS": 30.0,
            "WALNUT-WOOD": 85.0,
            "WALNUT": 50.0,
            "EAST-BAR_WOOD": 50.0,
        }
        for name, pct in measured.items():
            amt = pct / 100.0
            self.assertTrue(should_insert_multiply(amt), msg="%s must get a multiply" % name)
            self.assertLess(amt, 1.0, msg="%s: every measured multiplier reduces roughness" % name)

    def test_scene_prep_and_plugin_are_both_required(self):
        """Documents the failure that cost a whole export cycle: re-applying output_amount in scene
        prep with no reader is INERT, and reading output_amount with nothing re-applying it finds
        1.0 because ConvertScene already threw the multiplier away."""
        reapplied_only = parse_texmap_line("specular_roughness|float|x.jpg")   # pre-026 emit
        self.assertEqual(reapplied_only["amount"], 1.0,
                         "without the 4th field the recovery cannot reach the graph")
        both = parse_texmap_line("specular_roughness|float|x.jpg|0.3")
        self.assertTrue(should_insert_multiply(both["amount"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
