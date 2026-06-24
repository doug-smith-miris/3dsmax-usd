--
-- Copyright 2026 Miris Inc. (fork: 3dsmax-usd)
-- Copyright 2023 Autodesk
--
-- Licensed under the Apache License, Version 2.0 (the "License");
-- you may not use this file except in compliance with the License.
-- You may obtain a copy of the License at
--
--     http://www.apache.org/licenses/LICENSE-2.0
--
-- Unless required by applicable law or agreed to in writing, software
-- distributed under the License is distributed on an "AS IS" BASIS,
-- WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
-- See the License for the specific language governing permissions and
-- limitations under the License.
--
-- MAX-LIT-001 regression coverage for the legacy standard-light writer
-- (`src/translators/LegacyLightWriter.cpp`). Before that writer existed,
-- 3ds Max's installed plugin matched only LightScape (photometric)
-- lights and silently dropped every legacy standard light (Omnilight,
-- Skylight, etc.) on USD export. The catalog entry MAX-LIT-001 has the
-- full description; these tests pin down the structural and authored-
-- value invariants the writer must hold.

FileIn "helpers/usd_test_utils.mxs"

setupUsdPlugins()

pyUsd = python.import("pxr.Usd")
pyUsdGeom = python.import("pxr.UsdGeom")
pyUsdLux = python.import("pxr.UsdLux")

output_prefix = pathConfig.appendPath (getDir #temp) "/EXPORT_LEGACY_LIGHT_TEST_"
deleteTestFiles(output_prefix + "*.*")

struct export_legacy_light_test
(
    private
    initialUnits,
    initialScale,

    public
    function setup =
    (
        quickReset()
        initialUnits = units.SystemType
        units.SystemType = #inches
        initialScale = units.SystemScale
        units.SystemScale = 1
    ),

    -- MAX-LIT-001 happy path: a single Omnilight on the scene exports
    -- to a UsdLuxSphereLight at the named path, with `treatAsPoint =
    -- true` and a tiny radius (matching the convention
    -- PhotometricLightWriter uses for photometric point lights — see
    -- PhotometricLightWriter.cpp:196). Previously the same scene
    -- exported zero UsdLux prims at all.
    function legacy_omnilight_exports_to_sphere_treat_as_point_test = (
        local exportPath = output_prefix + "test_omnilight_basic.usda"
        local exportOptions = USDExporter.CreateOptions()
        exportOptions.FileFormat = #ascii
        exportOptions.RootPrimPath = "/"
        exportOptions.Lights = true

        local omni = omniLight()
        omni.rgb = color 255 200 100  -- warm tint
        omni.multiplier = 1.5
        omni.castShadows = true

        USDExporter.ExportFile exportPath exportOptions:exportOptions
        local stage = pyUsd.Stage.Open(exportPath)

        local usd_light_path = "/" + omni.name
        local objPrim = stage.GetPrimAtPath usd_light_path
        assert_true (pyUsd.Prim.IsValid objPrim) message:(omni.name + " did not export to any USD prim (MAX-LIT-001 regression).")
        assert_true (objPrim.IsA(pyUsdLux.SphereLight)) message:(omni.name + " did not export as UsdLuxSphereLight.")

        local sphereLight = pyUsdLux.SphereLight.Get stage usd_light_path
        local radiusAttr = sphereLight.GetRadiusAttr()
        assert_float_equal 0.001 (radiusAttr.Get()) message:(omni.name + " sphere radius should pin to 0.001 (treat-as-point convention).")
        local treatAsPointAttr = sphereLight.GetTreatAsPointAttr()
        assert_true (treatAsPointAttr.Get()) message:(omni.name + " sphere should set treatAsPoint = true.")

        -- Authored common attributes survive the writer's hop into USD.
        local lightAPI = pyUsdLux.LightAPI(sphereLight)
        local colorAttr = lightAPI.GetColorAttr()
        local colorValue = colorAttr.Get()
        assert_float_equal (255.0 / 255.0) colorValue[1] message:"Omnilight color R should round-trip."
        assert_float_equal (200.0 / 255.0) colorValue[2] message:"Omnilight color G should round-trip."
        assert_float_equal (100.0 / 255.0) colorValue[3] message:"Omnilight color B should round-trip."

        local normalizeAttr = lightAPI.GetNormalizeAttr()
        assert_true (normalizeAttr.Get()) message:(omni.name + " should default to normalized intensity, like photometric light writer.")
    ),

    -- MAX-LIT-001 negative-state gate: the `on` flag, when off, has to
    -- collapse the USD light to zero contribution. We mirror the
    -- PhotometricLightWriter's intensity-zeroing approach (see
    -- PhotometricLightWriter.cpp:246-258); the smallest knob with no
    -- USD-side ambiguity is `intensity = 0`. `inputs:color` is left
    -- intact so an importer can still recover the artist's authored
    -- tint.
    function legacy_omnilight_off_zeros_intensity_test = (
        local exportPath = output_prefix + "test_omnilight_off.usda"
        local exportOptions = USDExporter.CreateOptions()
        exportOptions.FileFormat = #ascii
        exportOptions.RootPrimPath = "/"
        exportOptions.Lights = true

        local omni = omniLight()
        omni.on = false  -- "Light On" unchecked in the UI

        USDExporter.ExportFile exportPath exportOptions:exportOptions
        local stage = pyUsd.Stage.Open(exportPath)

        local usd_light_path = "/" + omni.name
        local sphereLight = pyUsdLux.SphereLight.Get stage usd_light_path
        local lightAPI = pyUsdLux.LightAPI(sphereLight)
        local intensityAttr = lightAPI.GetIntensityAttr()
        assert_float_equal 0.0 (intensityAttr.Get()) message:(omni.name + " was disabled in 3ds Max; USD intensity must be 0.")
        local colorAttr = lightAPI.GetColorAttr()
        assert_true (colorAttr.IsAuthored()) message:(omni.name + " color should still be authored on a disabled light (intensity carries the disable).")
    ),

    -- MAX-LIT-001 happy path for the legacy Skylight, which is the
    -- second-most-common dropped class in the catalog. Maps to a
    -- UsdLuxDomeLight; we don't author the texture/IES sky model in
    -- this bite — those are tracked as a follow-on candidate mission.
    function legacy_skylight_exports_to_dome_test = (
        local exportPath = output_prefix + "test_skylight_basic.usda"
        local exportOptions = USDExporter.CreateOptions()
        exportOptions.FileFormat = #ascii
        exportOptions.RootPrimPath = "/"
        exportOptions.Lights = true

        local sky = skylight()
        sky.rgb = color 180 200 255  -- cool overcast tint
        sky.multiplier = 0.9

        USDExporter.ExportFile exportPath exportOptions:exportOptions
        local stage = pyUsd.Stage.Open(exportPath)

        local usd_light_path = "/" + sky.name
        local objPrim = stage.GetPrimAtPath usd_light_path
        assert_true (pyUsd.Prim.IsValid objPrim) message:(sky.name + " skylight did not export to any USD prim (MAX-LIT-001 regression).")
        assert_true (objPrim.IsA(pyUsdLux.DomeLight)) message:(sky.name + " skylight did not export as UsdLuxDomeLight.")

        local domeLight = pyUsdLux.DomeLight.Get stage usd_light_path
        local lightAPI = pyUsdLux.LightAPI(domeLight)
        local colorAttr = lightAPI.GetColorAttr()
        local colorValue = colorAttr.Get()
        assert_float_equal (180.0 / 255.0) colorValue[1] message:"Skylight color R should round-trip."
        assert_float_equal (200.0 / 255.0) colorValue[2] message:"Skylight color G should round-trip."
        assert_float_equal (255.0 / 255.0) colorValue[3] message:"Skylight color B should round-trip."
    ),

    -- Direct regression for the original MAX-LIT-001 fingerprint: a
    -- scene that contains BOTH an Omnilight and a Skylight (the two
    -- legacy classes named in the catalog) must produce TWO UsdLux*
    -- prims, not zero. Before the writer landed, the test would
    -- find none.
    function legacy_lights_round_trip_count_test = (
        local exportPath = output_prefix + "test_legacy_lights_count.usda"
        local exportOptions = USDExporter.CreateOptions()
        exportOptions.FileFormat = #ascii
        exportOptions.RootPrimPath = "/"
        exportOptions.Lights = true

        local omni = omniLight()
        local sky = skylight()

        USDExporter.ExportFile exportPath exportOptions:exportOptions
        local stage = pyUsd.Stage.Open(exportPath)

        local luxCount = 0
        for prim in stage.Traverse() do (
            if prim.IsA(pyUsdLux.LightAPI) or prim.HasAPI(pyUsdLux.LightAPI) then luxCount += 1
        )
        assert_true (luxCount >= 2) message:("Expected at least 2 UsdLux* prims (one Omnilight, one Skylight); found " + (luxCount as string) + " — MAX-LIT-001 regression.")
    ),

    -- TranslateLights = false must skip legacy lights, exactly like it
    -- skips photometric lights. This is the surgical guard that keeps
    -- the new writer from polluting exports the user explicitly opted
    -- the lights out of.
    function legacy_lights_respect_translate_lights_option_test = (
        local exportPath = output_prefix + "test_legacy_lights_skipped.usda"
        local exportOptions = USDExporter.CreateOptions()
        exportOptions.FileFormat = #ascii
        exportOptions.RootPrimPath = "/"
        exportOptions.Lights = false  -- the surgical gate

        local omni = omniLight()
        local sky = skylight()

        USDExporter.ExportFile exportPath exportOptions:exportOptions
        local stage = pyUsd.Stage.Open(exportPath)

        for prim in stage.Traverse() do (
            assert_false (prim.IsA(pyUsdLux.SphereLight)) message:("With Lights=false, SphereLight " + (prim.GetName() as string) + " must not be authored.")
            assert_false (prim.IsA(pyUsdLux.DomeLight)) message:("With Lights=false, DomeLight " + (prim.GetName() as string) + " must not be authored.")
        )
    ),

    function teardown = (
        units.SystemType = initialUnits
        units.SystemScale = initialScale
    ),

    Tests = #(
        legacy_omnilight_exports_to_sphere_treat_as_point_test,
        legacy_omnilight_off_zeros_intensity_test,
        legacy_skylight_exports_to_dome_test,
        legacy_lights_round_trip_count_test,
        legacy_lights_respect_translate_lights_option_test
    )
)

runUsdTestFixture export_legacy_light_test (getThisScriptFilename())
