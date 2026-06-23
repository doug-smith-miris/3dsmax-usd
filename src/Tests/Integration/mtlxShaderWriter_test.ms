--
-- Copyright 2025 Autodesk
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

FileIn "helpers/usd_test_utils.mxs"

-- Make sure the USD plugin is properly loaded.
setupUsdPlugins()

-- Some useful python modules for USD validation.
pyUsd = python.import("pxr.Usd")
pyUsdGeom = python.import("pxr.UsdGeom")
pyUsdTf = python.import("pxr.Tf")
pyShade = python.import("pxr.UsdShade")
pysdf = python.import("pxr.Sdf")
pyGf = python.import("pxr.Gf")

output_prefix = pathConfig.appendPath (getDir #temp) "/MTLXSHADER_WRITER_TEST_"
deleteTestFiles(output_prefix + "*.*")

struct mtlx_shader_writer_test
( 
    function setup =
    (
        quickReset()
    ),
    
    function test_export_physical_material = (
        maxver = maxVersion()
        -- Don't test version before 2025
        if maxver[1] < 26900 then (
            return 0
        )

        local teapot001 = teapot name:"teapot001"

        local mtl1 = MaterialXMaterial()
        mtl1.name = "matTeapot001"
        local scriptDir = getFilenamePath (getThisScriptFilename())
        local mtlxDoc = (scriptDir + @"data\Iberian_Blue_Ceramic_Tiles_1k_8b\Iberian_Blue_Ceramic_Tiles.mtlx")
        mtl1.importMaterial mtlxDoc
        teapot001.material = mtl1
        mtl1.explode()
        local exportOptions = USDExporter.CreateOptions()
        exportOptions.FileFormat = #ascii
        exportOptions.AllMaterialTargets = #("MaterialX")

        local exportPath = output_prefix + "testExportPhysicalMaterial.usda"
        USDExporter.ExportFile exportPath exportOptions:exportOptions
        local stage = pyUsd.Stage.Open(exportPath)
            
        local mtlPrim = stage.GetPrimAtPath("/root/mtl/Iberian_Blue_Ceramic_Tiles")
        assert_true (mtlPrim.IsValid())
        -- Let's validate the Surface Shader
        local surfacePrim = stage.GetPrimAtPath("/root/mtl/Iberian_Blue_Ceramic_Tiles/Iberian_Blue_Ceramic_Tiles")
        assert_true (surfacePrim.IsValid())
        assert_true (surfacePrim.IsA(pyShade.Shader))
        local surfaceShader = pyShade.Shader(surfacePrim)
        assert_equal ((surfacePrim.GetAttribute "info:id").Get()) "ND_standard_surface_surfaceshader"
        assert_equal ((surfaceShader.GetInput "base").Get()) 0.8
        assert_equal ((((surfaceShader.GetInput "base_color").GetConnectedSource())[1].GetPath())) (pysdf.Path "/root/mtl/Iberian_Blue_Ceramic_Tiles/NG_Iberian_Blue_Ceramic_Tiles")
        assert_equal ((surfaceShader.GetInput "coat").Get()) 0
        assert_equal ((surfaceShader.GetInput "coat_affect_color").Get()) 0
        assert_equal ((surfaceShader.GetInput "coat_affect_roughness").Get()) 0
        assert_equal ((surfaceShader.GetInput "coat_color").Get()) (pyGf.Vec3f 1 1 1)
        assert_equal ((surfaceShader.GetInput "coat_IOR").Get()) 1.5
        assert_equal ((((surfaceShader.GetInput "coat_normal").GetConnectedSource())[1].GetPath())) (pysdf.Path "/root/mtl/Iberian_Blue_Ceramic_Tiles/NG_Iberian_Blue_Ceramic_Tiles")
        assert_equal ((surfaceShader.GetInput "coat_roughness").Get()) 0.1
        assert_equal ((surfaceShader.GetInput "diffuse_roughness").Get()) 0
        assert_equal ((surfaceShader.GetInput "emission").Get()) 0
        assert_equal ((surfaceShader.GetInput "emission_color").Get()) (pyGf.Vec3f 1 1 1)
        assert_equal ((((surfaceShader.GetInput "metalness").GetConnectedSource())[1].GetPath())) (pysdf.Path "/root/mtl/Iberian_Blue_Ceramic_Tiles/NG_Iberian_Blue_Ceramic_Tiles")
        assert_equal ((((surfaceShader.GetInput "normal").GetConnectedSource())[1].GetPath())) (pysdf.Path "/root/mtl/Iberian_Blue_Ceramic_Tiles/NG_Iberian_Blue_Ceramic_Tiles")
        assert_equal ((surfaceShader.GetInput "specular").Get()) 1
        assert_equal ((surfaceShader.GetInput "specular_color").Get()) (pyGf.Vec3f 1 1 1)
        assert_equal ((surfaceShader.GetInput "specular_IOR").Get()) 1.5
        assert_equal ((surfaceShader.GetInput "specular_rotation").Get()) 0
        assert_equal ((((surfaceShader.GetInput "specular_roughness").GetConnectedSource())[1].GetPath())) (pysdf.Path "/root/mtl/Iberian_Blue_Ceramic_Tiles/NG_Iberian_Blue_Ceramic_Tiles")
        assert_equal ((surfaceShader.GetInput "subsurface").Get()) 0
        assert_equal ((surfaceShader.GetInput "subsurface_color").Get()) (pyGf.Vec3f 1 1 1)
        assert_equal ((surfaceShader.GetInput "subsurface_radius").Get()) (pyGf.Vec3f 1 1 1)
        assert_equal ((surfaceShader.GetInput "subsurface_scale").Get()) 1
        assert_false ((surfaceShader.GetInput "thin_walled").Get())
        assert_equal ((surfaceShader.GetInput "transmission").Get()) 0
        assert_equal ((surfaceShader.GetInput "transmission_color").Get()) (pyGf.Vec3f 1 1 1)
        assert_equal ((surfaceShader.GetInput "transmission_depth").Get()) 0
	),

    function test_export_OpenPBR_material = (
        maxver = maxVersion()
        -- Don't test version before 2026
        -- OpenPBR is only 2025.3 and above
        if maxver[1] < 27900 then (
            return 0
        )

        local teapot001 = teapot name:"teapot001"
        local mtl1 = OpenPBR()
        mtl1.name = "matTeapot001"
        teapot001.material = mtl1
        local exportOptions = USDExporter.CreateOptions()
        exportOptions.FileFormat = #ascii
        exportOptions.AllMaterialTargets = #("MaterialX")

        local exportPath = output_prefix + "testOpenPBRMaterial.usda"
        USDExporter.ExportFile exportPath exportOptions:exportOptions
        local stage = pyUsd.Stage.Open(exportPath)

        local mtlPrim = stage.GetPrimAtPath("/root/mtl/matTeapot001")
        assert_true (mtlPrim.IsValid())
        -- Let's validate the OpenPBR Shader
        local shaderPrim = stage.GetPrimAtPath("/root/mtl/matTeapot001/matTeapot001")
        assert_true (shaderPrim.IsValid())
        assert_true (shaderPrim.IsA(pyShade.Shader))
        local shader = pyShade.Shader(shaderPrim)

        -- Validate shader attributes
        assert_equal ((shaderPrim.GetAttribute "info:id").Get()) "ND_open_pbr_surface_surfaceshader"
        assert_equal ((shader.GetInput "base_color").Get()) (pyGf.Vec3f 0.8 0.8 0.8)
        assert_equal ((shader.GetInput "base_diffuse_roughness").Get()) 0
        assert_equal ((shader.GetInput "base_metalness").Get()) 0
        assert_equal ((shader.GetInput "base_weight").Get()) 1
        assert_equal ((shader.GetInput "coat_color").Get()) (pyGf.Vec3f 1 1 1)
        assert_equal ((shader.GetInput "coat_darkening").Get()) 1
        assert_equal ((shader.GetInput "coat_ior").Get()) 1.6
        assert_equal ((shader.GetInput "coat_roughness").Get()) 0
        assert_equal ((shader.GetInput "coat_roughness_anisotropy").Get()) 0
        assert_equal ((shader.GetInput "coat_weight").Get()) 0
        assert_equal ((shader.GetInput "emission_color").Get()) (pyGf.Vec3f 1 1 1)
        assert_equal ((shader.GetInput "emission_luminance").Get()) 0
        assert_equal ((shader.GetInput "fuzz_color").Get()) (pyGf.Vec3f 1 1 1)
        assert_equal ((shader.GetInput "fuzz_roughness").Get()) 0.5
        assert_equal ((shader.GetInput "fuzz_weight").Get()) 0
        assert_false ((shader.GetInput "geometry_thin_walled").Get())
        assert_equal ((shader.GetInput "specular_color").Get()) (pyGf.Vec3f 1 1 1)
        assert_equal ((shader.GetInput "specular_ior").Get()) 1.5
        assert_equal ((shader.GetInput "specular_roughness").Get()) 0.3
        assert_equal ((shader.GetInput "specular_roughness_anisotropy").Get()) 0
        assert_equal ((shader.GetInput "specular_weight").Get()) 1
        assert_equal ((shader.GetInput "subsurface_color").Get()) (pyGf.Vec3f 0.8 0.8 0.8)
        assert_equal ((shader.GetInput "subsurface_radius").Get()) 1
        assert_equal ((shader.GetInput "subsurface_radius_scale").Get()) (pyGf.Vec3f 0.794704 0.531734 0.292854)
        assert_equal ((shader.GetInput "subsurface_scatter_anisotropy").Get()) 0
        assert_equal ((shader.GetInput "subsurface_weight").Get()) 0
        assert_equal ((shader.GetInput "thin_film_ior").Get()) 1.4
        assert_equal ((shader.GetInput "thin_film_thickness").Get()) 0.5
        assert_equal ((shader.GetInput "thin_film_weight").Get()) 0
        assert_equal ((shader.GetInput "transmission_color").Get()) (pyGf.Vec3f 1 1 1)
        assert_equal ((shader.GetInput "transmission_depth").Get()) 0
        assert_equal ((shader.GetInput "transmission_dispersion_abbe_number").Get()) 20
        assert_equal ((shader.GetInput "transmission_dispersion_scale").Get()) 0
        assert_equal ((shader.GetInput "transmission_scatter").Get()) (pyGf.Vec3f 0 0 0)
        assert_equal ((shader.GetInput "transmission_scatter_anisotropy").Get()) 0
        assert_equal ((shader.GetInput "transmission_weight").Get()) 0
    ),

    -- Validate that a material with a name starting with a digit exports properly.
    -- The MtlxShaderWriter must prepend '_' to the name when looking up the node
    -- in the MaterialX document, matching the behavior of createValidName + leading digit fix.
    function test_export_material_leading_digit_name = (
        maxver = maxVersion()
        -- OpenPBR is only 2025.3 and above
        if maxver[1] < 27900 then (
            return 0
        )

        local teapot001 = teapot name:"teapot001"
        local mtl1 = OpenPBR()
        mtl1.name = "1matTeapot001"
        teapot001.material = mtl1
        local exportOptions = USDExporter.CreateOptions()
        exportOptions.FileFormat = #ascii
        exportOptions.AllMaterialTargets = #("MaterialX")

        local exportPath = output_prefix + "testLeadingDigitMaterial.usda"
        USDExporter.ExportFile exportPath exportOptions:exportOptions
        local stage = pyUsd.Stage.Open(exportPath)

        -- The material name starts with a digit. TfMakeValidIdentifier replaces the
        -- leading digit with '_', so "1matTeapot001" becomes "_matTeapot001".
        local mtlPrim = stage.GetPrimAtPath("/root/mtl/_matTeapot001")
        assert_true (mtlPrim.IsValid())
        -- Validate the shader prim exists under the sanitized name
        local shaderPrim = stage.GetPrimAtPath("/root/mtl/_matTeapot001/_matTeapot001")
        assert_true (shaderPrim.IsValid())
        assert_true (shaderPrim.IsA(pyShade.Shader))
        local shader = pyShade.Shader(shaderPrim)
        assert_equal ((shaderPrim.GetAttribute "info:id").Get()) "ND_open_pbr_surface_surfaceshader"
    ),

    -- MAX-MAT-001 regression: PhysicalMaterial export must not carry the
    -- spurious specular_rotation = 0.25 default that 3ds Max's MtlxIOUtil
    -- bridge writes on every standard_surface. The post-parse normalization
    -- in MtlxShaderWriter strips the value when specular_anisotropy is zero
    -- (or absent), restoring the MaterialX nodedef default.
    function test_export_physical_material_strips_buggy_specular_rotation = (
        maxver = maxVersion()
        -- PhysicalMaterial MaterialX export path is 2025+.
        if maxver[1] < 26900 then (
            return 0
        )

        local teapot001 = teapot name:"teapot001"
        local mtl1 = PhysicalMaterial()
        mtl1.name = "matNoAniso"
        -- Anisotropy explicitly off (this is also the PhysicalMaterial default).
        mtl1.anisotropy = 0.0
        teapot001.material = mtl1

        local exportOptions = USDExporter.CreateOptions()
        exportOptions.FileFormat = #ascii
        exportOptions.AllMaterialTargets = #("MaterialX")

        local exportPath = output_prefix + "testPhysicalMaterialNoAniso.usda"
        USDExporter.ExportFile exportPath exportOptions:exportOptions
        local stage = pyUsd.Stage.Open(exportPath)

        local surfacePrim = stage.GetPrimAtPath("/root/mtl/matNoAniso/matNoAniso")
        assert_true (surfacePrim.IsValid())
        assert_true (surfacePrim.IsA(pyShade.Shader))
        assert_equal ((surfacePrim.GetAttribute "info:id").Get()) "ND_standard_surface_surfaceshader"

        -- The bug: bridge writes specular_rotation = 0.25 on every shader.
        -- After normalization the input must either be absent (cleanest) or
        -- explicitly 0.0 (the nodedef default). Both states fix the bug.
        local surfaceShader = pyShade.Shader(surfacePrim)
        local rotInput = surfaceShader.GetInput "specular_rotation"
        if rotInput != undefined then (
            local rotAttr = rotInput.GetAttr()
            if rotAttr.HasAuthoredValue() then (
                assert_equal (rotInput.Get()) 0
            )
        )
    ),

    -- MAX-MAT-002 regression: PhysicalMaterial export must not carry the
    -- spurious emission = 1.0 paired with emission_color = (0, 0, 0) that
    -- 3ds Max's MtlxIOUtil bridge writes on every standard_surface. The
    -- post-parse normalization in MtlxShaderWriter strips both inputs when
    -- the buggy pair is detected, falling back to the MaterialX nodedef
    -- defaults (emission = 0.0, emission_color = (1, 1, 1)).
    function test_export_physical_material_strips_buggy_emission_default = (
        maxver = maxVersion()
        -- PhysicalMaterial MaterialX export path is 2025+.
        if maxver[1] < 26900 then (
            return 0
        )

        local sphere001 = sphere name:"sphere001"
        local mtl1 = PhysicalMaterial()
        mtl1.name = "matNoEmission"
        -- A default PhysicalMaterial has no emission authored. The bridge
        -- bug surfaces regardless of this setting on the source side.
        sphere001.material = mtl1

        local exportOptions = USDExporter.CreateOptions()
        exportOptions.FileFormat = #ascii
        exportOptions.AllMaterialTargets = #("MaterialX")

        local exportPath = output_prefix + "testPhysicalMaterialNoEmission.usda"
        USDExporter.ExportFile exportPath exportOptions:exportOptions
        local stage = pyUsd.Stage.Open(exportPath)

        local surfacePrim = stage.GetPrimAtPath("/root/mtl/matNoEmission/matNoEmission")
        assert_true (surfacePrim.IsValid())
        assert_true (surfacePrim.IsA(pyShade.Shader))
        assert_equal ((surfacePrim.GetAttribute "info:id").Get()) "ND_standard_surface_surfaceshader"

        local surfaceShader = pyShade.Shader(surfacePrim)

        -- After normalization the buggy pair is stripped. Either the input
        -- is absent (cleanest), or it explicitly carries the nodedef
        -- default (emission = 0.0, emission_color = (1, 1, 1)). Both
        -- states fix the bug; what must NOT be present is the buggy pair.
        local emInput = surfaceShader.GetInput "emission"
        if emInput != undefined then (
            local emAttr = emInput.GetAttr()
            if emAttr.HasAuthoredValue() then (
                assert_equal (emInput.Get()) 0
            )
        )
        local emColorInput = surfaceShader.GetInput "emission_color"
        if emColorInput != undefined then (
            local emColorAttr = emColorInput.GetAttr()
            if emColorAttr.HasAuthoredValue() then (
                assert_equal (emColorInput.Get()) (pyGf.Vec3f 1 1 1)
            )
        )
    ),

    -- MAX-MAT-004 regression: PhysicalMaterial export must not carry the
    -- spurious coat-block preset that 3ds Max's MtlxIOUtil bridge writes on
    -- every standard_surface, gated by coat = 0.0. The post-parse
    -- normalization in MtlxShaderWriter strips coat_IOR = 1.52,
    -- coat_affect_color = 0.5, coat_affect_roughness = 0.5, and
    -- coat_roughness = 0.0 (when coat is statically zero or absent and the
    -- input matches the buggy hard-coded value), falling back to the
    -- MaterialX nodedef defaults (coat_IOR = 1.5, coat_affect_color = 0.0,
    -- coat_affect_roughness = 0.0, coat_roughness = 0.1).
    function test_export_physical_material_strips_buggy_coat_defaults = (
        maxver = maxVersion()
        -- PhysicalMaterial MaterialX export path is 2025+.
        if maxver[1] < 26900 then (
            return 0
        )

        local sphere001 = sphere name:"sphere001"
        local mtl1 = PhysicalMaterial()
        mtl1.name = "matNoCoat"
        -- A default PhysicalMaterial has coat off (coating amount = 0).
        -- The bridge bug surfaces regardless of this setting.
        sphere001.material = mtl1

        local exportOptions = USDExporter.CreateOptions()
        exportOptions.FileFormat = #ascii
        exportOptions.AllMaterialTargets = #("MaterialX")

        local exportPath = output_prefix + "testPhysicalMaterialNoCoat.usda"
        USDExporter.ExportFile exportPath exportOptions:exportOptions
        local stage = pyUsd.Stage.Open(exportPath)

        local surfacePrim = stage.GetPrimAtPath("/root/mtl/matNoCoat/matNoCoat")
        assert_true (surfacePrim.IsValid())
        assert_true (surfacePrim.IsA(pyShade.Shader))
        assert_equal ((surfacePrim.GetAttribute "info:id").Get()) "ND_standard_surface_surfaceshader"

        local surfaceShader = pyShade.Shader(surfacePrim)

        -- Gate: coat must be zero (the bridge always emits coat = 0 for a
        -- default PhysicalMaterial, so the normalization must have fired).
        local coatInput = surfaceShader.GetInput "coat"
        if coatInput != undefined then (
            local coatAttr = coatInput.GetAttr()
            if coatAttr.HasAuthoredValue() then (
                assert_equal (coatInput.Get()) 0
            )
        )

        -- After normalization each of the four leak inputs is either absent
        -- (cleanest) or explicitly set to its MaterialX nodedef default.
        -- Both states fix the bug; what must NOT be present is any of the
        -- buggy hard-coded values (1.52, 0.5, 0.5, 0.0 respectively, with
        -- coat_roughness's nodedef default being 0.1).
        local iorInput = surfaceShader.GetInput "coat_IOR"
        if iorInput != undefined then (
            local iorAttr = iorInput.GetAttr()
            if iorAttr.HasAuthoredValue() then (
                assert_equal (iorInput.Get()) 1.5
            )
        )
        local affectColorInput = surfaceShader.GetInput "coat_affect_color"
        if affectColorInput != undefined then (
            local affectColorAttr = affectColorInput.GetAttr()
            if affectColorAttr.HasAuthoredValue() then (
                assert_equal (affectColorInput.Get()) 0
            )
        )
        local affectRoughnessInput = surfaceShader.GetInput "coat_affect_roughness"
        if affectRoughnessInput != undefined then (
            local affectRoughnessAttr = affectRoughnessInput.GetAttr()
            if affectRoughnessAttr.HasAuthoredValue() then (
                assert_equal (affectRoughnessInput.Get()) 0
            )
        )
        local roughnessInput = surfaceShader.GetInput "coat_roughness"
        if roughnessInput != undefined then (
            local roughnessAttr = roughnessInput.GetAttr()
            if roughnessAttr.HasAuthoredValue() then (
                assert_equal (roughnessInput.Get()) 0.1
            )
        )
    ),

    -- MAX-MAT-005 regression: PhysicalMaterial export must not carry the
    -- spurious subsurface_radius = (0.794704, 0.531734, 0.292854) default
    -- (the Pixar/Hery caucasian-skin SSS radius preset) that 3ds Max's
    -- MtlxIOUtil bridge writes on every standard_surface, gated by
    -- subsurface = 0.0. The post-parse normalization in MtlxShaderWriter
    -- strips subsurface_radius (when subsurface is statically zero or
    -- absent and the input matches the buggy hard-coded triple), falling
    -- back to the MaterialX nodedef default (subsurface_radius = (1, 1, 1)).
    function test_export_physical_material_strips_buggy_subsurface_radius = (
        maxver = maxVersion()
        -- PhysicalMaterial MaterialX export path is 2025+.
        if maxver[1] < 26900 then (
            return 0
        )

        local sphere001 = sphere name:"sphere001"
        local mtl1 = PhysicalMaterial()
        mtl1.name = "matNoSss"
        -- A default PhysicalMaterial has subsurface off. The bridge bug
        -- surfaces regardless of this setting.
        sphere001.material = mtl1

        local exportOptions = USDExporter.CreateOptions()
        exportOptions.FileFormat = #ascii
        exportOptions.AllMaterialTargets = #("MaterialX")

        local exportPath = output_prefix + "testPhysicalMaterialNoSss.usda"
        USDExporter.ExportFile exportPath exportOptions:exportOptions
        local stage = pyUsd.Stage.Open(exportPath)

        local surfacePrim = stage.GetPrimAtPath("/root/mtl/matNoSss/matNoSss")
        assert_true (surfacePrim.IsValid())
        assert_true (surfacePrim.IsA(pyShade.Shader))
        assert_equal ((surfacePrim.GetAttribute "info:id").Get()) "ND_standard_surface_surfaceshader"

        local surfaceShader = pyShade.Shader(surfacePrim)

        -- Gate: subsurface must be zero (the bridge always emits
        -- subsurface = 0 for a default PhysicalMaterial, so the
        -- normalization must have fired).
        local subsurfaceInput = surfaceShader.GetInput "subsurface"
        if subsurfaceInput != undefined then (
            local subsurfaceAttr = subsurfaceInput.GetAttr()
            if subsurfaceAttr.HasAuthoredValue() then (
                assert_equal (subsurfaceInput.Get()) 0
            )
        )

        -- After normalization the subsurface_radius is either absent
        -- (cleanest) or explicitly set to the MaterialX nodedef default
        -- of (1, 1, 1). Both states fix the bug; what must NOT be
        -- present is the buggy skin SSS triple
        -- (0.794704, 0.531734, 0.292854).
        local radiusInput = surfaceShader.GetInput "subsurface_radius"
        if radiusInput != undefined then (
            local radiusAttr = radiusInput.GetAttr()
            if radiusAttr.HasAuthoredValue() then (
                assert_equal (radiusInput.Get()) (pyGf.Vec3f 1 1 1)
            )
        )
    ),

    function teardown = (
    ),

    Tests = #(
        test_export_physical_material,
        test_export_OpenPBR_material,
        test_export_material_leading_digit_name,
        test_export_physical_material_strips_buggy_specular_rotation,
        test_export_physical_material_strips_buggy_emission_default,
        test_export_physical_material_strips_buggy_coat_defaults,
        test_export_physical_material_strips_buggy_subsurface_radius
        )
)

runUsdTestFixture mtlx_shader_writer_test (getThisScriptFilename())