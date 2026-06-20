--
-- Copyright 2026 Autodesk
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
-- Covers MAX-UNIT-001 (catalog): when NormalizeStageMetersPerUnit is true, the exporter
-- authors `metersPerUnit = 1.0` AND applies a compensating uniform xformOp:scale on the
-- export root prim so the physical size of the geometry is preserved. When false (the
-- default), the legacy behavior is unchanged: `metersPerUnit` follows the source 3ds Max
-- system unit and no extra scale is added to the root prim.

FileIn "helpers/usd_test_utils.mxs"

-- Make sure the USD plugin is properly loaded.
setupUsdPlugins()

-- Some useful python modules for USD validation.
pyUsd = python.import("pxr.Usd")
pyUsdGeom = python.import("pxr.UsdGeom")

output_prefix = pathConfig.appendPath (getDir #temp) "/EXPORT_NORMALIZE_METERSPERUNIT_TEST_"
deleteTestFiles(output_prefix + "*.*")

struct export_normalizeStageMetersPerUnit_test
(
    private
    initialUnits,
    initialScale,
    exportOptions = USDExporter.CreateOptions(),

    -- Round to a sensible tolerance to compare floats with USD's serialized precision.
    function approxEqual a b tol:1e-9 = (
        return (abs (a - b)) <= tol
    ),

    -- Read the uniform component out of the root prim's scale xformOp, if any. The
    -- exporter appends a suffixed op (xformOp:scale:metersNormalization) when the
    -- normalize flag is on; when off, no such op is authored by the exporter (the prim
    -- may still carry other ops from the scene, which we ignore here).
    function getNormalizationScale stage rootPath = (
        local prim = stage.GetPrimAtPath(rootPath)
        if not prim.IsValid() then return undefined
        local xformable = pyUsdGeom.Xformable(prim)
        local ops = xformable.GetOrderedXformOps()
        for op in ops do (
            local opName = op.GetOpName() as string
            if matchPattern opName pattern:"*scale:metersNormalization*" then (
                local v = op.Get()
                return v[1]
            )
        )
        return undefined
    ),

    public
    function setup = (
        quickReset()
        exportOptions.FileFormat = #ascii

        initialUnits = units.SystemType
        initialScale = units.SystemScale

        -- Make sure the option starts at the documented default for each test.
        exportOptions.NormalizeStageMetersPerUnit = false
    ),

    -- 1. The default must remain backwards-compatible: NormalizeStageMetersPerUnit
    --    starts false, and the exporter authors metersPerUnit from the scene's system
    --    unit. Inches -> 0.0254. No compensating scale on the root prim.
    function testDefaultOffPreservesLegacyBehavior = (
        --given
        units.SystemType = #Inches
        units.SystemScale = 1

        --when
        local exportPath = (output_prefix + "default_off_inches.usda")
        USDExporter.ExportFile exportPath exportOptions:exportOptions
        local stage = pyUsd.Stage.Open(exportPath)
        local mpu = pyUsdGeom.GetStageMetersPerUnit(stage)
        local scaleComp = getNormalizationScale stage "/root"

        --then
        assert_float_equal 0.0254 mpu
        assert_equal undefined scaleComp \
            message:"No metersNormalization scale op should be authored when the option is off."
    ),

    -- 2. Toggling NormalizeStageMetersPerUnit to true while the source is INCHES must
    --    author metersPerUnit = 1.0 AND a uniform scale of 0.0254 on the root prim.
    function testInchesNormalizedToMeters = (
        --given
        units.SystemType = #Inches
        units.SystemScale = 1
        exportOptions.NormalizeStageMetersPerUnit = true

        --when
        local exportPath = (output_prefix + "normalize_inches.usda")
        USDExporter.ExportFile exportPath exportOptions:exportOptions
        local stage = pyUsd.Stage.Open(exportPath)
        local mpu = pyUsdGeom.GetStageMetersPerUnit(stage)
        local scaleComp = getNormalizationScale stage "/root"

        --then
        assert_float_equal 1.0 mpu
        assert_true (scaleComp != undefined) \
            message:"Expected a metersNormalization xformOp:scale on /root."
        assert_true (approxEqual scaleComp 0.0254 tol:1e-6) \
            message:("Expected root scale 0.0254, got " + (scaleComp as string))
    ),

    -- 3. Toggling NormalizeStageMetersPerUnit to true while the source is already METERS
    --    must produce metersPerUnit = 1.0 AND an identity scale (1.0) on the root prim.
    --    No-op semantically, but the metadata is now consistent.
    function testMetersNormalizedIsIdentity = (
        --given
        units.SystemType = #Meters
        units.SystemScale = 1
        exportOptions.NormalizeStageMetersPerUnit = true

        --when
        local exportPath = (output_prefix + "normalize_meters.usda")
        USDExporter.ExportFile exportPath exportOptions:exportOptions
        local stage = pyUsd.Stage.Open(exportPath)
        local mpu = pyUsdGeom.GetStageMetersPerUnit(stage)
        local scaleComp = getNormalizationScale stage "/root"

        --then
        assert_float_equal 1.0 mpu
        assert_true (scaleComp != undefined) \
            message:"Expected a metersNormalization xformOp:scale on /root (identity)."
        assert_true (approxEqual scaleComp 1.0 tol:1e-9) \
            message:("Expected root scale 1.0, got " + (scaleComp as string))
    ),

    -- 4. Centimeters (a common Unreal/game-engine system unit). Normalized export should
    --    produce metersPerUnit = 1.0 and scale = 0.01 on /root.
    function testCentimetersNormalizedToMeters = (
        --given
        units.SystemType = #Centimeters
        units.SystemScale = 1
        exportOptions.NormalizeStageMetersPerUnit = true

        --when
        local exportPath = (output_prefix + "normalize_centimeters.usda")
        USDExporter.ExportFile exportPath exportOptions:exportOptions
        local stage = pyUsd.Stage.Open(exportPath)
        local mpu = pyUsdGeom.GetStageMetersPerUnit(stage)
        local scaleComp = getNormalizationScale stage "/root"

        --then
        assert_float_equal 1.0 mpu
        assert_true (scaleComp != undefined) \
            message:"Expected a metersNormalization xformOp:scale on /root."
        assert_true (approxEqual scaleComp 0.01 tol:1e-9) \
            message:("Expected root scale 0.01, got " + (scaleComp as string))
    ),

    function teardown = (
        units.SystemType = initialUnits
        units.SystemScale = initialScale
    ),

    Tests = #(
        testDefaultOffPreservesLegacyBehavior,
        testInchesNormalizedToMeters,
        testMetersNormalizedIsIdentity,
        testCentimetersNormalizedToMeters
    )
)

runUsdTestFixture export_normalizeStageMetersPerUnit_test (getThisScriptFilename())
