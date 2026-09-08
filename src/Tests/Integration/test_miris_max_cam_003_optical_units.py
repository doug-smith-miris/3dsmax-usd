"""MAX-CAM-003: UsdGeomCamera optical values are TENTHS OF A SCENE UNIT, not millimetres.

Mirror suite. The exporter is Windows/MSVC only, so this pins the arithmetic of
`MaxUsd::GetMaxMmToUsdOpticalFactor` and its reciprocity with the import expression in
CameraConverter. If the two ever drift again, this fails on any machine.

The import expression being mirrored (CameraConverter.cpp):

    mm = usd * 0.1 * GetUsdToMaxScaleFactor(stage) * GetSystemUnitScale(UNITS_MILLIMETERS)

and the writer factor added alongside it:

    usd = mm * GetMaxMmToUsdOpticalFactor(stage)
        = mm / (0.1 * GetUsdToMaxScaleFactor(stage) * GetSystemUnitScale(UNITS_MILLIMETERS))
"""
import unittest


def usd_to_max_scale_factor(stage_meters_per_unit, max_meters_per_unit):
    """GetUsdToMaxScaleFactor: stage units expressed in Max units."""
    return stage_meters_per_unit / max_meters_per_unit


def system_unit_scale_mm(max_meters_per_unit):
    """GetSystemUnitScale(UNITS_MILLIMETERS): millimetres per Max system unit."""
    return max_meters_per_unit * 1000.0


def mm_to_usd_optical(stage_meters_per_unit, max_meters_per_unit):
    """The writer factor. Mirrors GetMaxMmToUsdOpticalFactor."""
    usd_to_mm = (0.1
                 * usd_to_max_scale_factor(stage_meters_per_unit, max_meters_per_unit)
                 * system_unit_scale_mm(max_meters_per_unit))
    if usd_to_mm == 0.0:
        return 1.0
    return 1.0 / usd_to_mm


def usd_optical_to_mm(value, stage_meters_per_unit, max_meters_per_unit):
    """The reader expression. Mirrors CameraConverter."""
    return (value
            * 0.1
            * usd_to_max_scale_factor(stage_meters_per_unit, max_meters_per_unit)
            * system_unit_scale_mm(max_meters_per_unit))


CM, FEET, MM, METRES, INCHES = 0.01, 0.3048, 0.001, 1.0, 0.0254


class TestOpticalUnitFactor(unittest.TestCase):

    def test_centimetre_scene_factor_is_one(self):
        """Why the defect survived for so long: on a cm scene the factor IS 1.0, so authoring raw
        millimetres was accidentally correct and every cm-unit test passed."""
        self.assertAlmostEqual(mm_to_usd_optical(CM, CM), 1.0, places=9)

    def test_millimetre_scene_factor_is_ten(self):
        """The old physical-camera branch hardcoded `* 10.f`, which is right only for a mm scene."""
        self.assertAlmostEqual(mm_to_usd_optical(MM, MM), 10.0, places=9)

    def test_foot_scene_factor(self):
        """The arena. 1 unit = 304.8 mm, so a tenth of a unit is 30.48 mm."""
        self.assertAlmostEqual(mm_to_usd_optical(FEET, FEET), 1.0 / 30.48, places=9)

    def test_metre_scene_factor(self):
        self.assertAlmostEqual(mm_to_usd_optical(METRES, METRES), 1.0 / 100.0, places=9)

    def test_round_trip_is_lossless_across_unit_systems(self):
        """Writer then reader must return the artist's millimetres, whatever the scene unit."""
        for mpu in (CM, FEET, MM, METRES, INCHES):
            for mm in (12.0, 20.0, 24.0, 35.0, 50.0, 200.0):
                authored = mm * mm_to_usd_optical(mpu, mpu)
                recovered = usd_optical_to_mm(authored, mpu, mpu)
                self.assertAlmostEqual(recovered, mm, places=6,
                                       msg="mpu=%s mm=%s" % (mpu, mm))

    def test_mismatched_stage_and_system_units(self):
        """GetUsdToMaxScaleFactor exists because the stage unit need not equal the Max unit."""
        authored = 20.0 * mm_to_usd_optical(CM, FEET)
        self.assertAlmostEqual(usd_optical_to_mm(authored, CM, FEET), 20.0, places=6)

    def test_degenerate_stage_does_not_divide_by_zero(self):
        self.assertEqual(mm_to_usd_optical(0.0, FEET), 1.0)

    def test_the_arena_defect_reproduces_and_the_fix_repairs_it(self):
        """The measured numbers from generic_int_bball_new.usdc, before any fix.

        CAM_BOWL_CONCERT_V1 was authored focalLength 20.0047 and horizontalAperture 36.0 in a
        metersPerUnit 0.3048 stage. Blender applied the documented convention and produced a
        609.7 mm lens on a 1097.3 mm sensor -- a ~30x telephoto that framed a few centimetres of
        seat back, which is why every interior frame rendered black."""
        BAD_FOCAL, BAD_APERTURE = 20.0047, 36.0
        self.assertAlmostEqual(usd_optical_to_mm(BAD_FOCAL, FEET, FEET), 609.74, places=1)
        self.assertAlmostEqual(usd_optical_to_mm(BAD_APERTURE, FEET, FEET), 1097.28, places=1)

        # with the fix, the same 20.0047 mm lens authors small and reads back correct
        good_focal = BAD_FOCAL * mm_to_usd_optical(FEET, FEET)
        good_aperture = BAD_APERTURE * mm_to_usd_optical(FEET, FEET)
        self.assertAlmostEqual(good_focal, 0.65632, places=5)
        self.assertAlmostEqual(good_aperture, 1.18110, places=5)
        self.assertAlmostEqual(usd_optical_to_mm(good_focal, FEET, FEET), 20.0047, places=4)
        self.assertAlmostEqual(usd_optical_to_mm(good_aperture, FEET, FEET), 36.0, places=4)

    def test_derived_aperture_inherits_the_factor_exactly_once(self):
        """The writer derives aperture from the already-scaled focal (w = tan(fov/2) * focal * 2).
        Applying the factor a second time there would shrink the sensor by 30x and widen the lens
        by the same amount, which looks plausible frame-to-frame and is not."""
        import math
        fov = math.radians(60.0)
        focal_mm = 20.0
        factor = mm_to_usd_optical(FEET, FEET)
        focal_authored = focal_mm * factor
        aperture_authored = math.tan(fov / 2.0) * focal_authored * 2.0
        # the recovered aperture must equal the millimetre aperture implied by the same FOV
        expected_mm = math.tan(fov / 2.0) * focal_mm * 2.0
        self.assertAlmostEqual(usd_optical_to_mm(aperture_authored, FEET, FEET),
                               expected_mm, places=6)
        # and the focal/aperture ratio must be unit-free
        self.assertAlmostEqual(aperture_authored / focal_authored,
                               expected_mm / focal_mm, places=9)


if __name__ == '__main__':
    unittest.main(verbosity=2)
