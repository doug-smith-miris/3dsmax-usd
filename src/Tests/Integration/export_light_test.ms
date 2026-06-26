--
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

FileIn "helpers/usd_test_utils.mxs"

-- Make sure the USD plugin is properly loaded.
setupUsdPlugins()

-- Some useful python modules for USD validation.
pyUsd = python.import("pxr.Usd")
pyUsdGeom = python.import("pxr.UsdGeom")
pyUsdLux = python.import("pxr.UsdLux")
pyGf = python.import("pxr.Gf")

output_prefix = pathConfig.appendPath (getDir #temp) "/EXPORT_LIGHT_TEST_"
deleteTestFiles(output_prefix + "*.*")

struct export_light_test
(  
    private 
    exportOptions = USDExporter.CreateOptions(),
    initialUnits,
    initialScale,

    public 
    function setup =
    (
        quickReset()
        exportOptions.FileFormat = #ascii
        exportOptions.RootPrimPath = "/"

        -- properly set system units in order to get expected results from tests
        initialUnits = units.SystemType
        units.SystemType = #inches
        initialScale = units.SystemScale
        units.SystemScale = 1
    ),
    
    -- Tests to validate the USD light type created from a given 3ds Max Photometric light type at export
    -- A USD light is created based on the Photometric Light Shape and its selected light distribution type
    -- Each photometric light shape has a 'target' sibling type (i.e. Point Target, Sphere Target, etc.)
    -- Below is the expected conversion grid :
    ----------------------------------------------------------------------------------------------------
    --                                            3ds Max Photometric Light Shapes
    --                              Point      Sphere     Disc        Line      Cylinder    Rectangle
    -----------------------------------------|--------|-----------|-----------|----------|--------------
    --              |  diffuse  |  DiskLight |   n/a  | DiskLight | RectLight |    n/a   |  RectLight
    --              |-----------|------------|--------|-----------|-----------|----------|--------------
    -- Distribution | spherical |            SphereLight          |           CylinderLight
    --              |-----------|------------|--------|-----------|-----------|----------|--------------
    --     Type     |    Spot   |  DiskLight |   n/a  | DiskLight | RectLight |    n/a   |  RectLight
    --              |-----------|------------|--------|-----------|-----------|----------|--------------
    --              |  Web/IES  |  DiskLight | Sphere | DiskLight | RectLight | Cylinder |  RectLight
    ----------------------------------------------------------------------------------------------------   
    function photometric_light_general_attributes_export_test = (
        local exportPath = output_prefix + "test_photometric_light_gen_attr_default_export.usda"
        local exportOptions = USDExporter.CreateOptions()
        exportOptions.FileFormat = #ascii
        exportOptions.RootPrimPath = "/"
        exportOptions.Lights = true

        -- taking the point light as a general sample for the attributes that affects all photometric lights
        local pointLight = free_light()
        pointLight.type = #Free_Point
        pointLight.distribution = 2 -- uniform hemispherical/diffuse
        -- use default attributes on photometric lights

        USDExporter.ExportFile exportPath exportOptions:exportOptions
        local stage = pyUsd.Stage.Open(exportPath)
        
        usd_light_path = "/" + pointLight.name
        usd_light = pyUsdLux.BoundableLightBase.Get stage usd_light_path
        
        local lightDiffuseAttr = usd_light.GetDiffuseAttr()
        assert_float_equal 1.0 (lightDiffuseAttr.Get()) message:(pointLight.name + " light is expected to have an effect of this light on the diffuse response of materials by default.")
        local lightSpecularAttr = usd_light.GetSpecularAttr()
        assert_float_equal 1.0 (lightSpecularAttr.Get()) message:(pointLight.name + " light is expected to have an effect of this light on the specular response of materials by default.")
        
        local lightNormalizeAttr = usd_light.GetNormalizeAttr()
        assert_true (lightNormalizeAttr.Get()) message:(pointLight.name + " light is expected to use normalized intensity.")
        local lightEnableColorTemperatureAttr = usd_light.GetEnableColorTemperatureAttr()
        assert_false (lightEnableColorTemperatureAttr.Get()) message:(pointLight.name + " light is not expected to use color temprature (Kelvin) by default.")
            
        -- The default color of the 'free_light' is equal to (1.0, 1.0, 1.0)
        local lightColorAttr = usd_light.GetColorAttr()
        local lightColorValue = lightColorAttr.Get()
        assert_float_equal 1.0 lightColorValue[1] message:"The light color value should default at 1.0"
        assert_float_equal 1.0 lightColorValue[2] message:"The light color value should default at 1.0"
        assert_float_equal 1.0 lightColorValue[3] message:"The light color value should default at 1.0"

        local lightShadowAPI = pyUsdLux.ShadowAPI(usd_light)
        local lightShadowAPI_ShadowEnableAttr = lightShadowAPI.GetShadowEnableAttr()
        assert_true (lightShadowAPI_ShadowEnableAttr.Get()) message:(pointLight.name + " light is expected to enable shadows to be cast by the light.")

        -- The default shadow color of the 'free_light' is equal to (0.0, 0.0, 0.0)
        local lightShadowAPI_ShadowColorAttr = lightShadowAPI.GetShadowColorAttr()
        local lightShadowAPI_ShadowColor = lightShadowAPI_ShadowColorAttr.Get()
        assert_float_equal 0.0 lightShadowAPI_ShadowColor[1] message:(pointLight.name + " light shadow color value (R) should default at 0.0")
        assert_float_equal 0.0 lightShadowAPI_ShadowColor[2] message:(pointLight.name + " light shadow color value (G) should default at 0.0")
        assert_float_equal 0.0 lightShadowAPI_ShadowColor[3] message:(pointLight.name + " light shadow color value (B) should default at 0.0")

        -- now changing the default settings
        pointLight.affectDiffuse = 0
        pointLight.affectSpecular = 0
        pointLight.useKelvin = true
        pointLight.shadowColor = color 255 255 255
        pointLight.baseobject.castShadows = false
        pointLight.on = false -- turning the light off zeroes out diffuse and specular in USD
            
        local exportPath = output_prefix + "test_photometric_light_gen_attr_export.usda"
        USDExporter.ExportFile exportPath exportOptions:exportOptions
        local stage = pyUsd.Stage.Open(exportPath)
        
        usd_light_path = "/" + pointLight.name
        usd_light = pyUsdLux.BoundableLightBase.Get stage usd_light_path
        
        lightDiffuseAttr = usd_light.GetDiffuseAttr()
        assert_float_equal 0.0 (lightDiffuseAttr.Get()) message:(pointLight.name + " light is not expected to have an effect of this light on the diffuse response of materials.")
        lightSpecularAttr = usd_light.GetSpecularAttr()
        assert_float_equal 0.0 (lightSpecularAttr.Get()) message:(pointLight.name + " light is not expected to have an effect of this light on the specular response of materials")
        
        -- keep normalized intensity on light (always)
        lightNormalizeAttr = usd_light.GetNormalizeAttr()
        assert_true (lightNormalizeAttr.Get()) message:(pointLight.name + " light is expected to use normalized intensity.")

        lightEnableColorTemperatureAttr = usd_light.GetEnableColorTemperatureAttr()
        assert_true (lightEnableColorTemperatureAttr.Get()) message:(pointLight.name + " light is expected to use color temprature (Kelvin).")

        lightShadowAPI = pyUsdLux.ShadowAPI(usd_light)
        lightShadowAPI_ShadowEnableAttr = lightShadowAPI.GetShadowEnableAttr()
        assert_false (lightShadowAPI_ShadowEnableAttr.Get()) message:(pointLight.name + " light is not expected to enable shadows to be cast by the light.")

        -- The shadow color of the 'free_light' is equal to (1.0, 1.0, 1.0)
        lightShadowAPI_ShadowColorAttr = lightShadowAPI.GetShadowColorAttr()
        lightShadowAPI_ShadowColor = lightShadowAPI_ShadowColorAttr.Get()
        assert_float_equal 1.0 lightShadowAPI_ShadowColor[1] message:(pointLight.name + " light shadow color value (R) should be at 1.0")
        assert_float_equal 1.0 lightShadowAPI_ShadowColor[2] message:(pointLight.name + " light shadow color value (G) should be at 1.0")
        assert_float_equal 1.0 lightShadowAPI_ShadowColor[3] message:(pointLight.name + " light shadow color value (B) should be at 1.0")
    ),

    -- Test that when the photometric light "On" property is set to false,
    -- both diffuse and specular are exported as 0 in USD, regardless of the
    -- affectDiffuse/affectSpecular settings.
    function photometric_light_on_off_export_test = (
        local exportPath = output_prefix + "test_photometric_light_on_off_export.usda"
        local exportOptions = USDExporter.CreateOptions()
        exportOptions.FileFormat = #ascii
        exportOptions.RootPrimPath = "/"
        exportOptions.Lights = true

        -- Create a point light with default diffuse/specular settings (both enabled)
        local pointLight = free_light()
        pointLight.type = #Free_Point
        pointLight.distribution = 2 -- uniform hemispherical/diffuse

        -- Verify defaults: affectDiffuse and affectSpecular are on
        assert_true pointLight.affectDiffuse message:"affectDiffuse should default to true"
        assert_true pointLight.affectSpecular message:"affectSpecular should default to true"

        -- Turn the light off
        pointLight.on = false

        USDExporter.ExportFile exportPath exportOptions:exportOptions
        local stage = pyUsd.Stage.Open(exportPath)

        local usd_light_path = "/" + pointLight.name
        local usd_light = pyUsdLux.BoundableLightBase.Get stage usd_light_path

        -- When the light is off, both diffuse and specular should be 0
        local lightDiffuseAttr = usd_light.GetDiffuseAttr()
        assert_float_equal 0.0 (lightDiffuseAttr.Get()) message:(pointLight.name + " light with 'on' set to false should export diffuse as 0.")
        local lightSpecularAttr = usd_light.GetSpecularAttr()
        assert_float_equal 0.0 (lightSpecularAttr.Get()) message:(pointLight.name + " light with 'on' set to false should export specular as 0.")

        -- Now turn the light back on and verify diffuse/specular are at their defaults (1.0)
        pointLight.on = true

        local exportPathOn = output_prefix + "test_photometric_light_on_off_on_export.usda"
        USDExporter.ExportFile exportPathOn exportOptions:exportOptions
        local stageOn = pyUsd.Stage.Open(exportPathOn)

        local usd_light_on = pyUsdLux.BoundableLightBase.Get stageOn usd_light_path

        local lightDiffuseAttrOn = usd_light_on.GetDiffuseAttr()
        assert_float_equal 1.0 (lightDiffuseAttrOn.Get()) message:(pointLight.name + " light with 'on' set to true should export diffuse as 1.0.")
        local lightSpecularAttrOn = usd_light_on.GetSpecularAttr()
        assert_float_equal 1.0 (lightSpecularAttrOn.Get()) message:(pointLight.name + " light with 'on' set to true should export specular as 1.0.")
    ),

    -- MAX-LIT-002 surgical-preservation regression: pin both surgical bounds
    -- of the IES profile authoring branch in PhotometricLightWriter::Write
    -- (lines 274-329 of PhotometricLightWriter.cpp, post-MAX-LIT-002 comment
    -- block). The existing happy-path tests for each light-type at lines
    -- 260, 364, 483, 616, 716, 873 toggle `distribution = 3` (web/IES) but
    -- never set `.webFile`, so the entire shaping:ies:file authoring branch
    -- is uncovered by the suite. A regression that dropped the
    -- `asset.GetId() != kInvalidId` check would emit shaping:ies:file = "";
    -- a regression that always-authored shaping:ies:file when .webFile was
    -- non-empty (regardless of distribution) would produce a USD light that
    -- paradoxically carried both shape-driven distribution AND an IES
    -- profile. Both bounds are exercised here.
    function photometric_light_ies_file_export_test = (
        local exportOptions = USDExporter.CreateOptions()
        exportOptions.FileFormat = #ascii
        exportOptions.RootPrimPath = "/"
        exportOptions.Lights = true

        -- Synthesize a minimal-valid IESNA LM-63 profile in the temp dir.
        -- The writer references the file by absolute path; it does not
        -- need to be a renderer-faithful profile for the test, only a
        -- file the AssetManager can register so asset.GetId() != kInvalidId.
        local iesPath = pathConfig.appendPath (getDir #temp) "/EXPORT_LIGHT_TEST_profile_audit.ies"
        local iesFile = createfile iesPath
        format "IESNA:LM-63-2002\n" to:iesFile
        format "TILT=NONE\n" to:iesFile
        format "1 1000.0 1.0 5 1 1 1 0.0 0.0 0.0\n" to:iesFile
        format "1.0 1.0 0.0\n" to:iesFile
        format "0.0 22.5 45.0 67.5 90.0\n" to:iesFile
        format "0.0\n" to:iesFile
        format "1000.0 800.0 600.0 200.0 0.0\n" to:iesFile
        close iesFile

        local pointLight = free_light()
        pointLight.type = #Free_Point
        pointLight.distribution = 3 -- web/IES
        pointLight.webFile = iesPath

        -- Surgical bound 1: distribution == WEB_DIST AND webFile set ->
        -- shaping:ies:file IS authored with the resolved file path.
        local exportPathWeb = output_prefix + "test_photometric_light_ies_file_web_export.usda"
        USDExporter.ExportFile exportPathWeb exportOptions:exportOptions
        local stageWeb = pyUsd.Stage.Open(exportPathWeb)
        local usd_light_path = "/" + pointLight.name
        local usdLightWeb = pyUsd.Prim.GetPrimAtPath stageWeb usd_light_path
        local shapingAPIWeb = pyUsdLux.ShapingAPI(usdLightWeb)
        local iesFileAttrWeb = shapingAPIWeb.GetShapingIesFileAttr()
        assert_true (iesFileAttrWeb.IsAuthored()) message:(pointLight.name + " with distribution=Web AND webFile set should author shaping:ies:file")
        local iesFileValueWeb = iesFileAttrWeb.Get()
        -- USD reports SdfAssetPath; the resolved/absolute path string round-trips.
        local iesFileAuthoredPath = iesFileValueWeb.path
        assert_true (findString iesFileAuthoredPath "profile_audit.ies" != undefined) \
            message:("shaping:ies:file expected to round-trip the .webFile asset path (audit IES filename); got: " + iesFileAuthoredPath)

        -- Surgical bound 2: distribution != WEB_DIST -> shaping:ies:file
        -- MUST NOT be authored, even though .webFile is still set on the
        -- source light. The Max source preserves the picked file across
        -- distribution toggles; it is only ACTIVE while distribution = WEB.
        pointLight.distribution = 0 -- isotropic spherical
        local exportPathIsotropic = output_prefix + "test_photometric_light_ies_file_isotropic_export.usda"
        USDExporter.ExportFile exportPathIsotropic exportOptions:exportOptions
        local stageIso = pyUsd.Stage.Open(exportPathIsotropic)
        local usdLightIso = pyUsd.Prim.GetPrimAtPath stageIso usd_light_path
        local shapingAPIIso = pyUsdLux.ShapingAPI(usdLightIso)
        local iesFileAttrIso = shapingAPIIso.GetShapingIesFileAttr()
        assert_false (iesFileAttrIso.IsAuthored()) \
            message:(pointLight.name + " with distribution=Isotropic should NOT author shaping:ies:file even though .webFile is set (surgical bound: gate is on distribution first, not on webFile presence)")

        -- Surgical bound 3: distribution == WEB_DIST AND .webFile is empty ->
        -- shaping:ies:file MUST NOT be authored (no spurious empty asset path).
        -- A second free_light constructed without a webFile (.webFile defaults
        -- to undefined) confirms the asset-id check fires correctly.
        local pointLightNoFile = free_light()
        pointLightNoFile.type = #Free_Point
        pointLightNoFile.distribution = 3 -- web/IES, but no file picked
        local exportPathEmpty = output_prefix + "test_photometric_light_ies_file_empty_export.usda"
        USDExporter.ExportFile exportPathEmpty exportOptions:exportOptions
        local stageEmpty = pyUsd.Stage.Open(exportPathEmpty)
        local usdLightEmptyPath = "/" + pointLightNoFile.name
        local usdLightEmpty = pyUsd.Prim.GetPrimAtPath stageEmpty usdLightEmptyPath
        local shapingAPIEmpty = pyUsdLux.ShapingAPI(usdLightEmpty)
        local iesFileAttrEmpty = shapingAPIEmpty.GetShapingIesFileAttr()
        assert_false (iesFileAttrEmpty.IsAuthored()) \
            message:(pointLightNoFile.name + " with distribution=Web and NO webFile should NOT author shaping:ies:file (surgical bound: asset.GetId() != kInvalidId check)")

        deleteFile iesPath
    ),

    -- MAX-LIT-001 writer-registry bottom-bound audit: pin the silent-drop
    -- behavior the PhotometricLightWriter::CanExport gate enforces for every
    -- non-LIGHTSCAPE_LIGHT_CLASS light (Omnilight, Skylight, legacy Spot,
    -- legacy Directional, mr_Sky/Daylight, etc.). The audit's value is making
    -- the bottom bound observable so a future "extend PhotometricLightWriter
    -- to legacy lights" widening lands with a per-class attribute-translation
    -- + decision-table update rather than silently authoring spurious
    -- default-intensity UsdLuxSphereLight / UsdLuxDomeLight prims for every
    -- legacy light position. See PhotometricLightWriter.cpp ::
    -- CanExport (post-MAX-LIT-001 comment block) for the surgical bounds
    -- enumeration.
    --
    -- The positive control (the Free_Point Photometric light) pins that
    -- the gate still fires for in-scope lights -- a refactor that
    -- destabilised the LIGHTSCAPE_LIGHT_CLASS branch surfaces here
    -- alongside the negative cases.
    function photometric_light_writer_legacy_dropout_audit_test = (
        local exportOptions = USDExporter.CreateOptions()
        exportOptions.FileFormat = #ascii
        exportOptions.RootPrimPath = "/"
        exportOptions.Lights = true

        -- Positive control: a Photometric Free_Point light. The
        -- LIGHTSCAPE_LIGHT_CLASS branch returns ContextSupport::Fallback
        -- so this light is authored as the correct UsdLux* per the
        -- PhotometricLightWriter::GetPrimType conversion grid.
        local photometricLight = free_light()
        photometricLight.name = "PhotometricPositiveControl"
        photometricLight.type = #Free_Point
        photometricLight.distribution = 2 -- uniform hemispherical/diffuse

        -- Negative case A: legacy Omnilight (OMNI_LIGHT_CLASS_ID;
        -- inherits LightObject but NOT LightscapeLight). The
        -- !IsSubClassOf(LIGHTSCAPE_LIGHT_CLASS) branch returns
        -- ContextSupport::Unsupported; no other registered PrimWriter
        -- claims a LightObject; MaxUsdPrimWriterRegistry::FindWriter
        -- returns nullptr; the light is silently dropped from the
        -- exported stage.
        local legacyOmni = omniLight()
        legacyOmni.name = "LegacyOmniNegativeCase"
        legacyOmni.pos = [200, 0, 100]
        legacyOmni.multiplier = 1.0
        legacyOmni.rgb = color 255 255 255

        -- Negative case B: legacy Skylight (the "Standard > Skylight"
        -- class, SKYLIGHT_CLASS_ID; also LightObject-not-LightscapeLight).
        -- Same writer-registry decision path -> silent drop.
        local legacySky = Skylight()
        legacySky.name = "LegacySkylightNegativeCase"
        legacySky.pos = [-200, 0, 100]

        local exportPath = output_prefix + "test_legacy_lights_dropout_audit_export.usda"
        USDExporter.ExportFile exportPath exportOptions:exportOptions

        local stage = pyUsd.Stage.Open(exportPath)

        -- Surgical bound 1 (positive control): the LIGHTSCAPE_LIGHT_CLASS
        -- branch fires; the photometric light IS authored as a UsdLux
        -- prim. A regression that broke the IsSubClassOf check (e.g.
        -- inverted the ternary, or returned Unsupported unconditionally)
        -- would fail this assertion alongside the negative cases.
        local photometricPath = "/" + photometricLight.name
        local photometricPrim = pyUsd.Prim.GetPrimAtPath stage photometricPath
        assert_true (photometricPrim.IsValid()) \
            message:(photometricLight.name + " is the positive control: a Free_Point Photometric light MUST be authored as a UsdLux* prim (the LIGHTSCAPE_LIGHT_CLASS gate fires for in-scope lights). If this assertion fails, the writer-registry gate has regressed for in-scope lights, not just for legacy lights.")
        local photometricLight_usd = pyUsdLux.BoundableLightBase.Get stage photometricPath
        assert_true (photometricLight_usd.GetIntensityAttr().IsValid()) \
            message:(photometricLight.name + " positive control should expose UsdLux BoundableLightBase intensity attribute.")

        -- Surgical bound 2 (negative case A): the legacy Omnilight is
        -- silently dropped. NO prim at its path. A wildcard "extend to
        -- LightObject" widening would author a UsdLuxSphereLight prim
        -- here -- this assertion is what catches that widening by
        -- named case.
        local legacyOmniPath = "/" + legacyOmni.name
        local legacyOmniPrim = pyUsd.Prim.GetPrimAtPath stage legacyOmniPath
        assert_false (legacyOmniPrim.IsValid()) \
            message:(legacyOmni.name + " is a negative case: a legacy Omnilight (LightObject not LightscapeLight) MUST be silently dropped under the current MAX-LIT-001 baseline. If this assertion fails, either (a) a writer has been added that claims legacy LightObjects -- which requires a corresponding doc entry + decision-table update + per-attribute audit, NOT just a wildcard widening of the existing photometric gate; or (b) the writer-registry has gained a default fallback that silently authors spurious UsdLux prims for unmapped nodes -- which would break the round-trip semantics across every node type, not just lights.")

        -- Surgical bound 3 (negative case B): the legacy Skylight is
        -- also silently dropped. Same writer-registry decision path.
        local legacySkyPath = "/" + legacySky.name
        local legacySkyPrim = pyUsd.Prim.GetPrimAtPath stage legacySkyPath
        assert_false (legacySkyPrim.IsValid()) \
            message:(legacySky.name + " is a negative case: a legacy Skylight (LightObject not LightscapeLight) MUST be silently dropped under the current MAX-LIT-001 baseline. If this assertion fails, see the legacy Omnilight failure message for the two ways the writer-registry could have shifted.")

        -- Surgical bound 4 (option still works on this branch): the
        -- TranslateLights short-circuit suppresses ALL light export,
        -- including the photometric. Same export call with
        -- exportOptions.Lights = false MUST drop the photometric
        -- positive control too -- this pins that the option gate fires
        -- BEFORE the class-id gate, and that the legacy-drop is NOT
        -- caused by an upstream option-state difference.
        exportOptions.Lights = false
        local exportPathNoLights = output_prefix + "test_legacy_lights_dropout_audit_no_lights_export.usda"
        USDExporter.ExportFile exportPathNoLights exportOptions:exportOptions
        local stageNoLights = pyUsd.Stage.Open(exportPathNoLights)
        local photometricPrimNoLights = pyUsd.Prim.GetPrimAtPath stageNoLights photometricPath
        assert_false (photometricPrimNoLights.IsValid()) \
            message:(photometricLight.name + " with exportOptions.Lights = false MUST also be dropped (the TranslateLights short-circuit fires before the class-id gate). If the photometric survives a Lights=false export, the option gate has regressed and the legacy-drop assertions above are no longer a clean MAX-LIT-001 baseline.")
    ),

    -- MAX-LIT-003 attribute-translation spec audit: extends the
    -- MAX-LIT-001 dropout audit to assert that, even when the source
    -- legacy Omnilight + Skylight carry NON-DEFAULT artist customizations
    -- (multiplier ≠ 1, rgb ≠ white, castShadows ≠ default, on != true),
    -- the writer-registry STILL silently drops the lights AND the
    -- customizations are lost along with the prim with no diagnostic.
    --
    -- The MAX-LIT-001 regression covers the default-attribute drop case
    -- (omniLight() + Skylight() at default settings -> no prim). This
    -- test pins the attribute-loss surface: if a future widening lands
    -- (PhotometricLightWriter::CanExport extended to LightObject without
    -- per-class GetPrimType + per-class attribute translation), the
    -- customizations below would be silently flattened to default
    -- intensity/white/shadow-on. The spec at
    --   /Users/d.smith/MirisProjects/Agent\ Builder/agent/arch-builds/
    --     4d931178-3f57-4ff7-bba5-edb7bf90a35e/
    --       validate_legacy_light_attribute_spec_surgical.py
    -- catalogs the per-class attribute translation table the future
    -- writers MUST honor (Omnilight.multiplier -> UsdLux.intensity,
    -- Omnilight.rgb -> UsdLux.color, Skylight.skyColor -> UsdLux.color
    -- (color-only branch), Skylight.castShadows -> shadow:enable).
    -- This test pins that under the CURRENT baseline, the customizations
    -- are lost; when a writer lands per the spec, the assertions below
    -- flip to "prim exists at the path AND each attribute matches the
    -- spec".
    function photometric_light_writer_legacy_attribute_dropout_audit_test = (
        local exportOptions = USDExporter.CreateOptions()
        exportOptions.FileFormat = #ascii
        exportOptions.RootPrimPath = "/"
        exportOptions.Lights = true

        -- Positive control: a Photometric Free_Point still authored
        -- correctly. Same precedent as the MAX-LIT-001 audit; pinned
        -- here so the per-class-attribute negative cases are not
        -- confused with an upstream writer-registry regression.
        local photometricLight = free_light()
        photometricLight.name = "PhotometricPositiveControl_NonDefaults"
        photometricLight.type = #Free_Point
        photometricLight.distribution = 2 -- uniform hemispherical/diffuse

        -- Negative case A: legacy Omnilight with NON-DEFAULT customizations
        -- the spec preserves -- multiplier = 0.3 (dim), rgb = warm tungsten,
        -- castShadows = false (no shadow).
        local legacyOmni = omniLight()
        legacyOmni.name = "LegacyOmni_NonDefault_Customizations"
        legacyOmni.pos = [200, 0, 100]
        legacyOmni.multiplier = 0.3
        legacyOmni.rgb = color 255 140 51   -- warm tungsten in 0-255
        legacyOmni.baseobject.castShadows = false

        -- Negative case B: legacy Skylight with NON-DEFAULT customizations
        -- -- multiplier = 0.4 (dim), skyColor = cool dusk, castShadows = false.
        -- Per the MAX-LIT-003 spec, the future writer would author
        -- UsdLuxDomeLight at intensity=0.4, color=(0.20, 0.42, 0.85),
        -- shadow:enable=false; under the CURRENT baseline, ALL of those
        -- are silently lost together with the prim.
        local legacySky = Skylight()
        legacySky.name = "LegacySky_NonDefault_Customizations"
        legacySky.pos = [-200, 0, 100]
        legacySky.multiplier = 0.4
        -- Skylight uses .rgb to drive the sky color when no map is bound.
        legacySky.rgb = color 51 107 217    -- cool dusk in 0-255
        legacySky.baseobject.castShadows = false

        local exportPath = output_prefix + "test_legacy_lights_attribute_dropout_audit_export.usda"
        USDExporter.ExportFile exportPath exportOptions:exportOptions

        local stage = pyUsd.Stage.Open(exportPath)

        -- Positive-control surgical bound: the LIGHTSCAPE_LIGHT_CLASS
        -- branch fires; the photometric light IS authored at its path.
        local photometricPath = "/" + photometricLight.name
        local photometricPrim = pyUsd.Prim.GetPrimAtPath stage photometricPath
        assert_true (photometricPrim.IsValid()) \
            message:(photometricLight.name + " positive control should be authored as a UsdLux prim under MAX-LIT-003 baseline.")

        -- Surgical bound A1 (Omnilight prim dropped): the legacy Omnilight
        -- is silently dropped even WITH non-default multiplier + rgb +
        -- castShadows. A wildcard widening that landed without per-class
        -- attribute translation would author a UsdLuxSphereLight at this
        -- path -- this assertion catches that widening even when the
        -- spurious prim authored is NOT default white (the spec validator
        -- catches the per-attribute regression on the prim).
        local legacyOmniPath = "/" + legacyOmni.name
        local legacyOmniPrim = pyUsd.Prim.GetPrimAtPath stage legacyOmniPath
        assert_false (legacyOmniPrim.IsValid()) \
            message:(legacyOmni.name + " is the MAX-LIT-003 negative case for Omnilight: a legacy Omnilight with non-default multiplier (0.3), non-default rgb (warm tungsten), AND castShadows=false MUST still be silently dropped under the current writer-registry baseline. If this assertion fails, a per-class writer has landed -- the spec validator at validate_legacy_light_attribute_spec_surgical.py catalogs the attribute translation table this writer MUST honor (intensity from multiplier, color from rgb, shadow:enable from castShadows). Flipping this assertion to assert_true requires also updating the per-attribute assertions in this test to the spec's expected values.")

        -- Surgical bound A2 (Omnilight customizations are silently lost):
        -- in the current baseline there is no observable round-trip channel
        -- for the multiplier / color / shadow customizations of legacy
        -- Omnilights. A regression that silently authored a UsdLuxSphereLight
        -- with DEFAULT unit-intensity / white-color / shadow-on at the
        -- legacy Omnilight's path (the wildcard widening counterfactual)
        -- would pass A1 above only if the writer also dropped the prim --
        -- but then the customizations are doubly lost (no prim + no
        -- round-trip channel). The customDataByKey check below pins that
        -- no spec channel (e.g. customData['3dsmax:legacy_omnilight:multiplier'])
        -- exists either: the current baseline carries the customizations
        -- in ZERO channels.
        local legacyOmniCustomData = pyUsd.Prim.GetCustomDataByKey legacyOmniPrim "3dsmax:legacy_omnilight:multiplier"
        assert_true (legacyOmniCustomData == undefined) \
            message:(legacyOmni.name + " legacy Omnilight customizations should NOT be smuggled into customData under the current baseline -- the MAX-LIT-003 spec defines customData[3dsmax:legacy_omnilight:*] as the round-trip channel for non-translatable attributes (far attenuation), NOT for attributes that have direct UsdLux equivalents (multiplier, rgb, castShadows). If this assertion fails, the writer is leaking artist customizations into a non-spec channel.")

        -- Surgical bound B1 (Skylight prim dropped): symmetric to A1 for
        -- the Skylight case. A wildcard widening that authored a
        -- UsdLuxDomeLight without per-attribute translation would fail here.
        local legacySkyPath = "/" + legacySky.name
        local legacySkyPrim = pyUsd.Prim.GetPrimAtPath stage legacySkyPath
        assert_false (legacySkyPrim.IsValid()) \
            message:(legacySky.name + " is the MAX-LIT-003 negative case for Skylight: a legacy Skylight with non-default multiplier (0.4), non-default rgb (cool dusk), AND castShadows=false MUST still be silently dropped under the current writer-registry baseline. The MAX-LIT-003 spec catalogs the dome-side translation table (intensity from multiplier, color from skyColor, shadow:enable from castShadows). When a Skylight writer lands, flip this assertion to assert_true and add per-attribute checks per the spec.")

        -- Surgical bound B2 (Skylight customizations are silently lost):
        -- symmetric to A2 for the dome path. Pins that no
        -- customData['3dsmax:legacy_skylight:*'] round-trip channel exists
        -- in the current baseline.
        local legacySkyCustomData = pyUsd.Prim.GetCustomDataByKey legacySkyPrim "3dsmax:legacy_skylight:multiplier"
        assert_true (legacySkyCustomData == undefined) \
            message:(legacySky.name + " legacy Skylight customizations should NOT be smuggled into customData under the current baseline. Same reasoning as the Omnilight customData check above.")
    ),

    -- MAX-LIT-002 surgical-preservation regression: pin both branches of
    -- the useKelvin dichotomy in PhotometricLightWriter::Write (lines
    -- 481-535 of PhotometricLightWriter.cpp, post-MAX-LIT-002 comment
    -- block).
    --
    -- The existing static-frame happy-path test asserts that
    -- enableColorTemperatureAttr is true/false but never the
    -- colorTemperature VALUE, never the filter-only color signal under
    -- useKelvin == true, never the combined-product color signal under
    -- useKelvin == false, and never the out-of-range Kelvin clamp.
    -- This test pins all four bounds.
    function photometric_light_kelvin_filter_color_round_trip_test = (
        local exportOptions = USDExporter.CreateOptions()
        exportOptions.FileFormat = #ascii
        exportOptions.RootPrimPath = "/"
        exportOptions.Lights = true

        local pointLight = free_light()
        pointLight.type = #Free_Point
        pointLight.distribution = 2 -- uniform hemispherical/diffuse

        -- Light color and filter color are intentionally distinct AND
        -- non-white so the difference between filter-only and combined-
        -- product authoring is observable in the USD output.
        pointLight.rgb = color 128 32 230            -- magenta-ish light color
        pointLight.filterColor = color 255 230 191   -- warm-orange filter
        pointLight.useKelvin = true
        pointLight.kelvin = 3200.0

        -- Surgical bound 1 (useKelvin == true):
        --   * enableColorTemperatureAttr == true
        --   * colorTemperatureAttr == 3200 (in-range, no clamp)
        --   * inputs:color == filterColor ONLY (the magenta lightColor
        --     is intentionally dropped)
        local exportPathK = output_prefix + "test_photometric_light_kelvin_round_trip_export.usda"
        USDExporter.ExportFile exportPathK exportOptions:exportOptions
        local stageK = pyUsd.Stage.Open(exportPathK)
        local usd_light_path = "/" + pointLight.name
        local usdLightK = pyUsdLux.BoundableLightBase.Get stageK usd_light_path

        local enableCTAttrK = usdLightK.GetEnableColorTemperatureAttr()
        assert_true (enableCTAttrK.Get()) message:(pointLight.name + " useKelvin=true should set enableColorTemperatureAttr=true")
        local colorTempAttrK = usdLightK.GetColorTemperatureAttr()
        assert_true (colorTempAttrK.IsAuthored()) message:(pointLight.name + " useKelvin=true should author colorTemperatureAttr")
        assert_float_equal 3200.0 (colorTempAttrK.Get()) message:(pointLight.name + " colorTemperatureAttr value should equal source kelvin (3200) when in [1000, 10000]")

        -- inputs:color must be the FILTER colour only -- the magenta lightColor
        -- must NOT be combined in on the useKelvin branch.
        local colorAttrK = usdLightK.GetColorAttr()
        local colorValueK = colorAttrK.Get()
        local expectedFilterR = 255.0 / 255.0
        local expectedFilterG = 230.0 / 255.0
        local expectedFilterB = 191.0 / 255.0
        assert_float_equal expectedFilterR colorValueK[1] message:(pointLight.name + " useKelvin=true: inputs:color R must be filterColor R only, not lightColor*filterColor")
        assert_float_equal expectedFilterG colorValueK[2] message:(pointLight.name + " useKelvin=true: inputs:color G must be filterColor G only")
        assert_float_equal expectedFilterB colorValueK[3] message:(pointLight.name + " useKelvin=true: inputs:color B must be filterColor B only")

        -- Surgical bound 2 (useKelvin == false):
        --   * enableColorTemperatureAttr == false
        --   * colorTemperatureAttr NOT authored
        --   * inputs:color == lightColor * filterColor (combined product;
        --     intentionally lossy on round-trip)
        pointLight.useKelvin = false
        local exportPathNoK = output_prefix + "test_photometric_light_no_kelvin_round_trip_export.usda"
        USDExporter.ExportFile exportPathNoK exportOptions:exportOptions
        local stageNoK = pyUsd.Stage.Open(exportPathNoK)
        local usdLightNoK = pyUsdLux.BoundableLightBase.Get stageNoK usd_light_path

        local enableCTAttrNoK = usdLightNoK.GetEnableColorTemperatureAttr()
        assert_false (enableCTAttrNoK.Get()) message:(pointLight.name + " useKelvin=false should set enableColorTemperatureAttr=false")

        local colorAttrNoK = usdLightNoK.GetColorAttr()
        local colorValueNoK = colorAttrNoK.Get()
        local expectedCombinedR = (128.0 / 255.0) * (255.0 / 255.0)
        local expectedCombinedG = (32.0  / 255.0) * (230.0 / 255.0)
        local expectedCombinedB = (230.0 / 255.0) * (191.0 / 255.0)
        assert_float_equal expectedCombinedR colorValueNoK[1] message:(pointLight.name + " useKelvin=false: inputs:color R must be lightColor R * filterColor R (combined)")
        assert_float_equal expectedCombinedG colorValueNoK[2] message:(pointLight.name + " useKelvin=false: inputs:color G must be lightColor G * filterColor G (combined)")
        assert_float_equal expectedCombinedB colorValueNoK[3] message:(pointLight.name + " useKelvin=false: inputs:color B must be lightColor B * filterColor B (combined)")

        -- Surgical bound 3 (Kelvin clamp): out-of-range Kelvin is clamped
        -- to [1000, 10000]. The original Max value is preserved in the
        -- emitted Log::Warn message; here we only assert the USD value
        -- is the clamped one.
        pointLight.useKelvin = true
        pointLight.kelvin = 12000.0  -- above USD spec max
        local exportPathClampHi = output_prefix + "test_photometric_light_kelvin_clamp_high_export.usda"
        USDExporter.ExportFile exportPathClampHi exportOptions:exportOptions
        local stageClampHi = pyUsd.Stage.Open(exportPathClampHi)
        local usdLightClampHi = pyUsdLux.BoundableLightBase.Get stageClampHi usd_light_path
        local colorTempAttrClampHi = usdLightClampHi.GetColorTemperatureAttr()
        assert_float_equal 10000.0 (colorTempAttrClampHi.Get()) message:(pointLight.name + " kelvin=12000 should be clamped to USD-spec max 10000 on export")

        pointLight.kelvin = 500.0  -- below USD spec min
        local exportPathClampLo = output_prefix + "test_photometric_light_kelvin_clamp_low_export.usda"
        USDExporter.ExportFile exportPathClampLo exportOptions:exportOptions
        local stageClampLo = pyUsd.Stage.Open(exportPathClampLo)
        local usdLightClampLo = pyUsdLux.BoundableLightBase.Get stageClampLo usd_light_path
        local colorTempAttrClampLo = usdLightClampLo.GetColorTemperatureAttr()
        assert_float_equal 1000.0 (colorTempAttrClampLo.Get()) message:(pointLight.name + " kelvin=500 should be clamped to USD-spec min 1000 on export")
    ),

    function photometric_point_light_attributes_export_test = (
        local exportOptions = USDExporter.CreateOptions()
        exportOptions.FileFormat = #ascii
        exportOptions.RootPrimPath = "/"
        exportOptions.Lights = true

        local pointLight = free_light()
        pointLight.type = #Free_Point
        pointLight.distribution = 2 -- uniform hemispherical/diffuse

        local exportPath = output_prefix + "test_photometric_point_light_diffuse_export.usda"
        USDExporter.ExportFile exportPath exportOptions:exportOptions
        local stage = pyUsd.Stage.Open(exportPath)
        
        usd_light_path = "/" + pointLight.name

        local objPrim = stage.GetPrimAtPath usd_light_path
        assert_true (objPrim.IsA(pyUsdLux.DiskLight)) message:(pointLight.name + " light was not properly exported to a USD LuxDiskLight.")

        usd_light = pyUsdLux.DiskLight.Get stage usd_light_path
        
        lightRadiusAttr = usd_light.GetRadiusAttr()
        assert_float_equal 0.001 (lightRadiusAttr.Get()) message:(pointLight.name + " light radius should always be set to`'0.001'")

        -- Now export when point light is using spherical light distribution
        pointLight.distribution = 0

        local exportPath = output_prefix + "test_photometric_point_light_spherical_export.usda"
        USDExporter.ExportFile exportPath exportOptions:exportOptions
        local stage = pyUsd.Stage.Open(exportPath)
        
        local objPrim = stage.GetPrimAtPath usd_light_path
        assert_true (objPrim.IsA(pyUsdLux.SphereLight)) message:(pointLight.name + " light was not properly exported to a USD LuxSphereLight.")

        usd_light = pyUsdLux.SphereLight.Get stage usd_light_path
        
        lightRadiusAttr = usd_light.GetRadiusAttr()
        assert_float_equal 0.001 (lightRadiusAttr.Get()) message:(pointLight.name + " light radius should always be set to`'0.001'")
        lightTreatAsPointAttr = usd_light.GetTreatAsPointAttr()
        assert_true (lightTreatAsPointAttr.Get()) message:(pointLight.name + " light should have its 'treatAsPoint' attribute active")

        -- Now export when point light is using spot light distribution
        pointLight.distribution = 1 -- spot

        local exportPath = output_prefix + "test_photometric_point_light_spot_export.usda"
        USDExporter.ExportFile exportPath exportOptions:exportOptions
        local stage = pyUsd.Stage.Open(exportPath)
        
        local objPrim = stage.GetPrimAtPath usd_light_path
        assert_true (objPrim.IsA(pyUsdLux.DiskLight)) message:(pointLight.name + " light was not properly exported to a USD LuxDiskLight.")

        usd_light = pyUsdLux.DiskLight.Get stage usd_light_path
        
        lightRadiusAttr = usd_light.GetRadiusAttr()
        assert_float_equal 0.001 (lightRadiusAttr.Get()) message:(pointLight.name + " light radius should always be set to`'0.001'")

        -- Now export when point light is using web light distribution
        pointLight.distribution = 3 -- web/IES

        local exportPath = output_prefix + "test_photometric_point_light_web_export.usda"
        USDExporter.ExportFile exportPath exportOptions:exportOptions
        local stage = pyUsd.Stage.Open(exportPath)
        
        local objPrim = stage.GetPrimAtPath usd_light_path
        assert_true (objPrim.IsA(pyUsdLux.DiskLight)) message:(pointLight.name + " light was not properly exported to a USD LuxDiskLight.")

        usd_light = pyUsdLux.DiskLight.Get stage usd_light_path
        
        lightRadiusAttr = usd_light.GetRadiusAttr()
        assert_float_equal 0.001 (lightRadiusAttr.Get()) message:(pointLight.name + " light radius should always be set to`'0.001'")
    ),

    function photometric_point_light_attributes_animation_export_test = (
        local exportOptions = USDExporter.CreateOptions()
        exportOptions.FileFormat = #ascii
        exportOptions.RootPrimPath = "/"
        exportOptions.Lights = true

        local pointLight = free_light()
        pointLight.type = #Free_Point
        pointLight.distribution = 2 -- uniform hemispherical/diffuse
        pointLight.useKelvin = true
        pointLight.useMultiplier = true
        
        with animate on 
        (
            -- Animate some attributes
            at time 10 move pointLight [0,0,10]
            at time 10 pointLight.kelvin = 5600
            -- when using color temperature, the filter color is the only attribute
            -- the composes the USD light color
            at time 10 pointLight.filterColor = color 155 155 155
            at time 10 pointLight.multiplier = 50  -- dimming 50%; influence intensity
        )
        
        -- Testing #animationRange as TimeMode
        -- Configure the time slider to go from 2 to 8.        
        local rangeStart = 2
        local rangeEnd = 8
        
        animationRange = interval rangeStart rangeEnd
        local exportPath = output_prefix + "test_photometric_point_light_animationrange_export.usda"
        exportOptions.TimeMode = #animationRange
        
        USDExporter.ExportFile exportPath exportOptions:exportOptions
        local stage = pyUsd.Stage.Open(exportPath)
        
        usd_light_path = "/" + pointLight.name
        usd_light = pyUsdLux.DiskLight.Get stage usd_light_path
        
        local xformable = pyUsdGeom.Xformable(usd_light)
        local lightRadiusAttr = usd_light.GetRadiusAttr()
        local lightColorAttr = usd_light.GetColorAttr()
        local lightColorTemperatureAttr = usd_light.GetColorTemperatureAttr()
        local lightIntensityAttr = usd_light.GetIntensityAttr()
        -- hardcoding expected value since the computation of the USD intensity is not easy
        expectedIntensity = #(4616.2656,4343.5747,4012.4502,3652.109,3291.7673,2960.6433,2687.9521)

        for timeCode in rangeStart to rangeEnd do (
            local t = at time timeCode pointLight.transform
            assert_true (compareUsdAndMaxTransforms (xformable.GetLocalTransformation(timeCode)) t) message:("The light's transform is wrong when exporting with #animationRange at t = " + timeCode as string)
            assert_float_equal 0.001 (lightRadiusAttr.Get(timeCode)) message:(pointLight.name + " light radius should always be set to`'0.001'")

            local c = at time timeCode pointLight.filterColor
            local lightColorValue = lightColorAttr.Get(timeCode)
            assert_float_equal (c.r/255.0) lightColorValue[1] message:"The light color value (R) should follow light filter color value"
            assert_float_equal (c.g/255.0) lightColorValue[2] message:"The light color value (G) should follow light filter color value"
            assert_float_equal (c.b/255.0) lightColorValue[3] message:"The light color value (B) should follow light filter color value"
            
            local k = at time timeCode pointLight.kelvin
            assert_float_equal k (lightColorTemperatureAttr.Get(timeCode)) message:"The light color temperature value should follow animation settings"
                
            assert_float_equal expectedIntensity[timeCode-1] (lightIntensityAttr.Get(timeCode)) message:"The light intensity should follow dimmed value from animated light settings"
        )
    ),

    function photometric_sphere_light_attributes_export_test = (
        local exportOptions = USDExporter.CreateOptions()
        exportOptions.FileFormat = #ascii
        exportOptions.RootPrimPath = "/"
        exportOptions.Lights = true

        local sphereLight = free_light()
        sphereLight.type = #Free_Sphere
        sphereLight.distribution = 0 -- uniform spherical

        local exportPath = output_prefix + "test_photometric_sphere_light_spherical_export.usda"
        USDExporter.ExportFile exportPath exportOptions:exportOptions
        local stage = pyUsd.Stage.Open(exportPath)
        
        usd_light_path = "/" + sphereLight.name
        
        local objPrim = stage.GetPrimAtPath usd_light_path
        assert_true (objPrim.IsA(pyUsdLux.SphereLight)) message:(sphereLight.name + " light was not properly exported to a USD LuxSphereLight.")

        usd_light = pyUsdLux.SphereLight.Get stage usd_light_path
        
        lightRadiusAttr = usd_light.GetRadiusAttr()
        assert_float_equal sphereLight.radius (lightRadiusAttr.Get()) message:(sphereLight.name + " light radius is not as specified.")

        -- Now export when point light is using web light distribution
        sphereLight.distribution = 3 -- web/IES
        sphereLight.radius = 10

        local exportPath = output_prefix + "test_photometric_sphere_light_web_export.usda"
        USDExporter.ExportFile exportPath exportOptions:exportOptions
        local stage = pyUsd.Stage.Open(exportPath)
        
        local objPrim = stage.GetPrimAtPath usd_light_path
        assert_true (objPrim.IsA(pyUsdLux.SphereLight)) message:(sphereLight.name + " light was not properly exported to a USD LuxSphereLight.")

        usd_light = pyUsdLux.SphereLight.Get stage usd_light_path
        
        lightRadiusAttr = usd_light.GetRadiusAttr()
        assert_float_equal sphereLight.radius (lightRadiusAttr.Get()) message:(sphereLight.name + " light radius is not as specified.")
    ),

    function photometric_sphere_light_attributes_animation_export_test = (
        local exportOptions = USDExporter.CreateOptions()
        exportOptions.FileFormat = #ascii
        exportOptions.RootPrimPath = "/"
        exportOptions.Lights = true

        local sphereLight = free_light()
        sphereLight.type = #Free_Sphere
        sphereLight.distribution = 0 -- uniform spherical

        with animate on 
        (
            -- Animate some attributes
            at time 10 move sphereLight [0,0,10]
            at time 10 sphereLight.radius = 40
            -- see point light animation export for general animatable attributs export
        )
        
        -- Testing #animationRange as TimeMode
        -- Configure the time slider to go from 2 to 8.        
        local rangeStart = 2
        local rangeEnd = 8
        
        animationRange = interval rangeStart rangeEnd
        local exportPath = output_prefix + "photometric_sphere_light_attributes_animation_export_test.usda"
        exportOptions.TimeMode = #animationRange
        
        USDExporter.ExportFile exportPath exportOptions:exportOptions
        local stage = pyUsd.Stage.Open(exportPath)
        
        usd_light_path = "/" + sphereLight.name
        
        local objPrim = stage.GetPrimAtPath usd_light_path
        assert_true (objPrim.IsA(pyUsdLux.SphereLight)) message:(sphereLight.name + " light was not properly exported to a USD LuxSphereLight.")

        usd_light = pyUsdLux.SphereLight.Get stage usd_light_path
        
        local xformable = pyUsdGeom.Xformable(usd_light)
        lightRadiusAttr = usd_light.GetRadiusAttr()
        for timeCode in rangeStart to rangeEnd do (
            local t = at time timeCode sphereLight.transform
            assert_true (compareUsdAndMaxTransforms (xformable.GetLocalTransformation(timeCode)) t) message:("The light's transform is wrong when exporting with #animationRange at t = " + timeCode as string)
            local r = at time timeCode sphereLight.radius
            assert_float_equal r (lightRadiusAttr.Get(timeCode)) message:(sphereLight.name + " light radius is not as specified.")
        )
    ),

    function photometric_disk_light_attributes_export_test = (
        local exportOptions = USDExporter.CreateOptions()
        exportOptions.FileFormat = #ascii
        exportOptions.RootPrimPath = "/"
        exportOptions.Lights = true

        local diskLight = free_light()
        diskLight.type = #Free_Disc
        diskLight.distribution = 2 -- uniform hemispherical/diffuse

        local exportPath = output_prefix + "test_photometric_disk_light_diffuse_export.usda"
        USDExporter.ExportFile exportPath exportOptions:exportOptions
        local stage = pyUsd.Stage.Open(exportPath)
        
        usd_light_path = "/" + diskLight.name

        local objPrim = stage.GetPrimAtPath usd_light_path
        assert_true (objPrim.IsA(pyUsdLux.DiskLight)) message:(diskLight.name + " light was not properly exported to a USD LuxDiskLight.")

        usd_light = pyUsdLux.DiskLight.Get stage usd_light_path
        
        lightRadiusAttr = usd_light.GetRadiusAttr()
        assert_float_equal diskLight.radius (lightRadiusAttr.Get()) message:(diskLight.name + " light radius is not as specified.")

        -- Now export when disk light is using spherical light distribution
        diskLight.distribution = 0
        diskLight.radius = 20

        local exportPath = output_prefix + "test_photometric_disk_light_spherical_export.usda"
        USDExporter.ExportFile exportPath exportOptions:exportOptions
        local stage = pyUsd.Stage.Open(exportPath)
        
        local objPrim = stage.GetPrimAtPath usd_light_path
        assert_true (objPrim.IsA(pyUsdLux.SphereLight)) message:(diskLight.name + " light was not properly exported to a USD LuxSphereLight.")

        usd_light = pyUsdLux.SphereLight.Get stage usd_light_path
        
        lightRadiusAttr = usd_light.GetRadiusAttr()
        assert_float_equal diskLight.radius (lightRadiusAttr.Get()) message:(diskLight.name + " light radius is not as specified.")

        -- Now export when disk light is using spot light distribution
        diskLight.distribution = 1 -- spot

        local exportPath = output_prefix + "test_photometric_disk_light_spot_export.usda"
        USDExporter.ExportFile exportPath exportOptions:exportOptions
        local stage = pyUsd.Stage.Open(exportPath)
        
        local objPrim = stage.GetPrimAtPath usd_light_path
        assert_true (objPrim.IsA(pyUsdLux.DiskLight)) message:(diskLight.name + " light was not properly exported to a USD LuxDiskLight.")

        usd_light = pyUsdLux.DiskLight.Get stage usd_light_path
        
        lightRadiusAttr = usd_light.GetRadiusAttr()
        assert_float_equal diskLight.radius (lightRadiusAttr.Get()) message:(diskLight.name + " light radius is not as specified.")

        -- Now export when disk light is using web light distribution
        diskLight.distribution = 3 -- web/IES

        local exportPath = output_prefix + "test_photometric_disk_light_web_export.usda"
        USDExporter.ExportFile exportPath exportOptions:exportOptions
        local stage = pyUsd.Stage.Open(exportPath)
        
        local objPrim = stage.GetPrimAtPath usd_light_path
        assert_true (objPrim.IsA(pyUsdLux.DiskLight)) message:(diskLight.name + " light was not properly exported to a USD LuxDiskLight.")

        usd_light = pyUsdLux.DiskLight.Get stage usd_light_path
        
        lightRadiusAttr = usd_light.GetRadiusAttr()
        assert_float_equal diskLight.radius (lightRadiusAttr.Get()) message:(diskLight.name + " light radius is not as specified.")
    ),

    function photometric_disk_light_attributes_animation_export_test = (
        local exportOptions = USDExporter.CreateOptions()
        exportOptions.FileFormat = #ascii
        exportOptions.RootPrimPath = "/"
        exportOptions.Lights = true

        local diskLight = free_light()
        diskLight.type = #Free_Disc
        diskLight.distribution = 2 -- uniform hemispherical/diffuse

        with animate on 
        (
            -- Animate some attributes
            at time 10 move diskLight [0,0,10]
            at time 10 diskLight.radius = 50
            -- see point light animation export for general animatable attributs export
        )
        
        -- Testing #animationRange as TimeMode
        -- Configure the time slider to go from 2 to 8.        
        local rangeStart = 2
        local rangeEnd = 8
        
        animationRange = interval rangeStart rangeEnd
        local exportPath = output_prefix + "test_photometric_sphere_light_animationrange_export.usda"
        exportOptions.TimeMode = #animationRange
        USDExporter.ExportFile exportPath exportOptions:exportOptions
        local stage = pyUsd.Stage.Open(exportPath)
        
        usd_light_path = "/" + diskLight.name

        local objPrim = stage.GetPrimAtPath usd_light_path
        assert_true (objPrim.IsA(pyUsdLux.DiskLight)) message:(diskLight.name + " light was not properly exported to a USD LuxDiskLight.")

        usd_light = pyUsdLux.DiskLight.Get stage usd_light_path

        local xformable = pyUsdGeom.Xformable(usd_light)
        lightRadiusAttr = usd_light.GetRadiusAttr()
        for timeCode in rangeStart to rangeEnd do (
            local t = at time timeCode diskLight.transform
            assert_true (compareUsdAndMaxTransforms (xformable.GetLocalTransformation(timeCode)) t) message:("The light's transform is wrong when exporting with #animationRange at t = " + timeCode as string)
            local r = at time timeCode diskLight.radius
            assert_float_equal r (lightRadiusAttr.Get(timeCode)) message:(diskLight.name + " light radius is not as specified.")
        )
    ),

    function photometric_line_light_attributes_export_test = (
        local exportOptions = USDExporter.CreateOptions()
        exportOptions.FileFormat = #ascii
        exportOptions.RootPrimPath = "/"
        exportOptions.Lights = true

        local lineLight = free_light()
        lineLight.type = #Free_Line
        lineLight.distribution = 2 -- uniform hemispherical/diffuse

        local exportPath = output_prefix + "test_photometric_line_light_diffuse_export.usda"
        USDExporter.ExportFile exportPath exportOptions:exportOptions
        local stage = pyUsd.Stage.Open(exportPath)
        
        usd_light_path = "/" + lineLight.name

        local objPrim = stage.GetPrimAtPath usd_light_path
        assert_true (objPrim.IsA(pyUsdLux.RectLight)) message:(lineLight.name + " light was not properly exported to a USD LuxRectLight.")

        usd_light = pyUsdLux.RectLight.Get stage usd_light_path
        
        lightWidthAttr = usd_light.GetWidthAttr()
        assert_float_equal 0.1 (lightWidthAttr.Get()) message:(lineLight.name + " light width should always be set to`'0.1'")
        lightHeightAttr = usd_light.GetHeightAttr()
        assert_float_equal lineLight.length (lightHeightAttr.Get()) message:(lineLight.name + " light height is not as specified with 3ds Max light length.")

        -- Now export when point light is using spherical light distribution
        lineLight.distribution = 0
        lineLight.length = 40  -- changing default length

        local exportPath = output_prefix + "test_photometric_line_light_spherical_export.usda"
        USDExporter.ExportFile exportPath exportOptions:exportOptions
        local stage = pyUsd.Stage.Open(exportPath)
        
        local objPrim = stage.GetPrimAtPath usd_light_path
        -- the cylinder light needs to be reoriented for USD
        -- 3ds Max light main axis is the y-axis and USD expects it on the x-axis
        assert_true (pyUsd.Prim.IsValid(objPrim)) message:(lineLight.name + " light was not properly exported to a USD prim.")
        assert_true (objPrim.IsA(pyUsdGeom.Xform)) message:(lineLight.name + " light needs its xform transfo.")

        usd_light_path = "/" + lineLight.name + "/" + lineLight.name + "_Light"
        objPrim = stage.GetPrimAtPath usd_light_path
        assert_true (objPrim.IsA(pyUsdLux.CylinderLight)) message:(lineLight.name + " light was not properly exported to a USD LuxCylinderLight.")

        usd_light = pyUsdLux.CylinderLight.Get stage usd_light_path
        
        lightRadiusAttr = usd_light.GetRadiusAttr()
        assert_float_equal 0.001 (lightRadiusAttr.Get()) message:(lineLight.name + " light radius should always be set to`'0.001'")
        lightTreatAsLineAttr = usd_light.GetTreatAsLineAttr()
        assert_true (lightTreatAsLineAttr.Get()) message:(lineLight.name + " light should have its 'treatAsLine' attribute active")
        lightLengthAttr = usd_light.GetLengthAttr()
        assert_float_equal lineLight.length (lightLengthAttr.Get()) message:(lineLight.name + " light length is not as specified.")

        -- Now export when point light is using spot light distribution
        lineLight.distribution = 1 -- spot

        local exportPath = output_prefix + "test_photometric_line_light_spot_export.usda"
        USDExporter.ExportFile exportPath exportOptions:exportOptions
        local stage = pyUsd.Stage.Open(exportPath)
        
        usd_light_path = "/" + lineLight.name
        local objPrim = stage.GetPrimAtPath usd_light_path
        assert_true (objPrim.IsA(pyUsdLux.RectLight)) message:(lineLight.name + " light was not properly exported to a USD LuxRectLight.")

        usd_light = pyUsdLux.RectLight.Get stage usd_light_path
        
        lightWidthAttr = usd_light.GetWidthAttr()
        assert_float_equal 0.1 (lightWidthAttr.Get()) message:(lineLight.name + " light radius should always be set to`'0.001'")
        lightHeightAttr = usd_light.GetHeightAttr()
        assert_float_equal lineLight.length (lightHeightAttr.Get()) message:(lineLight.name + " light height is not as specified with 3ds Max light length.")

        -- Now export when point light is using web light distribution
        lineLight.distribution = 3 -- web/IES

        local exportPath = output_prefix + "test_photometric_line_light_web_export.usda"
        USDExporter.ExportFile exportPath exportOptions:exportOptions
        local stage = pyUsd.Stage.Open(exportPath)
        
        local objPrim = stage.GetPrimAtPath usd_light_path
        assert_true (objPrim.IsA(pyUsdLux.RectLight)) message:(lineLight.name + " light was not properly exported to a USD LuxRectLight.")

        usd_light = pyUsdLux.RectLight.Get stage usd_light_path
        
        lightWidthAttr = usd_light.GetWidthAttr()
        assert_float_equal 0.1 (lightWidthAttr.Get()) message:(lineLight.name + " light width should always be set to`'0.1'")
        lightHeightAttr = usd_light.GetHeightAttr()
        assert_float_equal lineLight.length (lightHeightAttr.Get()) message:(lineLight.name + " light height is not as specified with 3ds Max light length.")
    ),

    function photometric_line_light_attributes_animation_export_test = (
        local exportOptions = USDExporter.CreateOptions()
        exportOptions.FileFormat = #ascii
        exportOptions.RootPrimPath = "/"
        exportOptions.Lights = true

        local lineLight = free_light()
        lineLight.type = #Free_Line
        lineLight.distribution = 2 -- uniform hemispherical/diffuse

        with animate on 
        (
            -- Animate some attributes
            at time 10 move lineLight [0,0,10]
            at time 10 lineLight.length = 120
            -- see point light animation export for general animatable attributs export
        )
        
        -- Testing #animationRange as TimeMode
        -- Configure the time slider to go from 2 to 8.        
        local rangeStart = 2
        local rangeEnd = 8
        
        animationRange = interval rangeStart rangeEnd
        local exportPath = output_prefix + "test_photometric_line_light_animationrange_export.usda"
        exportOptions.TimeMode = #animationRange
        USDExporter.ExportFile exportPath exportOptions:exportOptions
        local stage = pyUsd.Stage.Open(exportPath)
        
        usd_light_path = "/" + lineLight.name

        local objPrim = stage.GetPrimAtPath usd_light_path
        assert_true (objPrim.IsA(pyUsdLux.RectLight)) message:(lineLight.name + " light was not properly exported to a USD LuxRectLight.")

        usd_light = pyUsdLux.RectLight.Get stage usd_light_path
        
        local xformable = pyUsdGeom.Xformable(usd_light)
        lightWidthAttr = usd_light.GetWidthAttr()
        lightHeightAttr = usd_light.GetHeightAttr()
        for timeCode in rangeStart to rangeEnd do (
            local t = at time timeCode lineLight.transform
            assert_true (compareUsdAndMaxTransforms (xformable.GetLocalTransformation(timeCode)) t) message:("The light's transform is wrong when exporting with #animationRange at t = " + timeCode as string)
            assert_float_equal 0.1 (lightWidthAttr.Get(timeCode)) message:(lineLight.name + " light width should always be set to`'0.1'")
            local l = at time timeCode lineLight.length
            assert_float_equal l (lightHeightAttr.Get(timeCode)) message:(lineLight.name + " light height is not as specified with 3ds Max light length.")
        )
    ),

    function photometric_cylinder_light_attributes_export_test = (
        local exportOptions = USDExporter.CreateOptions()
        exportOptions.FileFormat = #ascii
        exportOptions.RootPrimPath = "/"
        exportOptions.Lights = true

        local cylinderLight = free_light()
        cylinderLight.type = #Free_Cylinder

        cylinderLight.distribution = 0
        cylinderLight.length = 40  -- changing default length

        local exportPath = output_prefix + "test_photometric_cylinder_light_spherical_export.usda"
        USDExporter.ExportFile exportPath exportOptions:exportOptions
        local stage = pyUsd.Stage.Open(exportPath)
        
        local usd_light_path = "/" + cylinderLight.name
        local objPrim = stage.GetPrimAtPath usd_light_path
        -- the cylinder light needs to be reoriented for USD
        -- 3ds Max light main axis is the y-axis and USD expects it on the x-axis
        assert_true (pyUsd.Prim.IsValid(objPrim)) message:(cylinderLight.name + " light was not properly exported to a USD prim.")
        assert_true (objPrim.IsA(pyUsdGeom.Xform)) message:(cylinderLight.name + " light needs its xform transfo.")

        usd_light_path = "/" + cylinderLight.name + "/" + cylinderLight.name + "_Light"
        objPrim = stage.GetPrimAtPath usd_light_path
        assert_true (objPrim.IsA(pyUsdLux.CylinderLight)) message:(cylinderLight.name + " light was not properly exported to a USD LuxCylinderLight.")

        usd_light = pyUsdLux.CylinderLight.Get stage usd_light_path
        
        lightRadiusAttr = usd_light.GetRadiusAttr()
        assert_float_equal cylinderLight.radius (lightRadiusAttr.Get()) message:(cylinderLight.name + " light radius is not as specified.")
        lightLengthAttr = usd_light.GetLengthAttr()
        assert_float_equal cylinderLight.length (lightLengthAttr.Get()) message:(cylinderLight.name + " light length is not as specified.")

        -- Now export when point light is using web light distribution
        cylinderLight.distribution = 3 -- web/IES

        local exportPath = output_prefix + "test_photometric_cylinder_light_web_export.usda"
        USDExporter.ExportFile exportPath exportOptions:exportOptions
        local stage = pyUsd.Stage.Open(exportPath)
        
        usd_light_path = "/" + cylinderLight.name
        objPrim = stage.GetPrimAtPath usd_light_path
        -- the cylinder light needs to be reoriented for USD
        -- 3ds Max light main axis is the y-axis and USD expects it on the x-axis
        assert_true (pyUsd.Prim.IsValid(objPrim)) message:(cylinderLight.name + " light was not properly exported to a USD prim.")
        assert_true (objPrim.IsA(pyUsdGeom.Xform)) message:(cylinderLight.name + " light needs its xform transfo.")

        usd_light_path = "/" + cylinderLight.name + "/" + cylinderLight.name + "_Light"
        local objPrim = stage.GetPrimAtPath usd_light_path
        assert_true (objPrim.IsA(pyUsdLux.CylinderLight)) message:(cylinderLight.name + " light was not properly exported to a USD LuxCylinderLight.")

        usd_light = pyUsdLux.CylinderLight.Get stage usd_light_path
        
        lightRadiusAttr = usd_light.GetRadiusAttr()
        assert_float_equal cylinderLight.radius (lightRadiusAttr.Get()) message:(cylinderLight.name + " light radius is not as specified.")
        lightLengthAttr = usd_light.GetLengthAttr()
        assert_float_equal cylinderLight.length (lightLengthAttr.Get()) message:(cylinderLight.name + " light length is not as specified.")
    ),

    function photometric_cylinder_light_attributes_animation_export_test = (
        local exportOptions = USDExporter.CreateOptions()
        exportOptions.FileFormat = #ascii
        exportOptions.RootPrimPath = "/"
        exportOptions.Lights = true

        local cylinderLight = free_light()
        cylinderLight.type = #Free_Cylinder

        cylinderLight.distribution = 0
        cylinderLight.length = 40  -- changing default length

        with animate on 
        (
            -- Animate some attributes
            at time 10 move cylinderLight [0,0,10]
            at time 10 rotate cylinderLight (eulerangles 0 0 45)
            at time 10 cylinderLight.length = 100
            at time 10 cylinderLight.radius = 15
            -- see point light animation export for general animatable attributs export
        )
        
        -- Testing #animationRange as TimeMode
        -- Configure the time slider to go from 2 to 8.        
        local rangeStart = 2
        local rangeEnd = 8
        
        animationRange = interval rangeStart rangeEnd
        local exportPath = output_prefix + "test_photometric_cylinder_light_animationrange_export.usda"
        exportOptions.TimeMode = #animationRange
        USDExporter.ExportFile exportPath exportOptions:exportOptions
        local stage = pyUsd.Stage.Open(exportPath)
        
        local usd_light_path = "/" + cylinderLight.name
        local objPrim = stage.GetPrimAtPath usd_light_path
        -- the cylinder light needs to be reoriented for USD
        -- 3ds Max light main axis is the y-axis and USD expects it on the x-axis
        assert_true (pyUsd.Prim.IsValid(objPrim)) message:(cylinderLight.name + " light was not properly exported to a USD prim.")
        assert_true (objPrim.IsA(pyUsdGeom.Xform)) message:(cylinderLight.name + " light needs its xform transfo.")
        local xformable = pyUsdGeom.Xformable(objPrim)
        for timeCode in rangeStart to rangeEnd do (
            local t = at time timeCode cylinderLight.transform
            assert_true (compareUsdAndMaxTransforms (xformable.GetLocalTransformation(timeCode)) t) message:("The light's transform is wrong when exporting with #animationRange at t = " + timeCode as string)
        )

        usd_light_path = "/" + cylinderLight.name + "/" + cylinderLight.name + "_Light"
        objPrim = stage.GetPrimAtPath usd_light_path
        assert_true (objPrim.IsA(pyUsdLux.CylinderLight)) message:(cylinderLight.name + " light was not properly exported to a USD LuxCylinderLight.")

        usd_light = pyUsdLux.CylinderLight.Get stage usd_light_path
        
        lightRadiusAttr = usd_light.GetRadiusAttr()
        lightLengthAttr = usd_light.GetLengthAttr()
        for timeCode in rangeStart to rangeEnd do (
            local r = at time timeCode cylinderLight.radius
            assert_float_equal r (lightRadiusAttr.Get(timeCode)) message:(cylinderLight.name + " light radius is not as specified.")
            local l = at time timeCode cylinderLight.length
            assert_float_equal l (lightLengthAttr.Get(timeCode)) message:(cylinderLight.name + " light length is not as specified.")
        )
    ),

    function photometric_rect_light_attributes_export_test = (
        local exportOptions = USDExporter.CreateOptions()
        exportOptions.FileFormat = #ascii
        exportOptions.RootPrimPath = "/"
        exportOptions.Lights = true

        local rectLight = free_light()
        rectLight.type = #Free_Rectangle
        rectLight.distribution = 2 -- uniform hemispherical/diffuse

        local exportPath = output_prefix + "test_photometric_rect_light_diffuse_export.usda"
        USDExporter.ExportFile exportPath exportOptions:exportOptions
        local stage = pyUsd.Stage.Open(exportPath)
        
        usd_light_path = "/" + rectLight.name

        local objPrim = stage.GetPrimAtPath usd_light_path
        assert_true (objPrim.IsA(pyUsdLux.RectLight)) message:(rectLight.name + " light was not properly exported to a USD LuxRectLight.")

        usd_light = pyUsdLux.RectLight.Get stage usd_light_path
        
        lightWidthAttr = usd_light.GetWidthAttr()
        assert_float_equal rectLight.width (lightWidthAttr.Get()) message:(rectLight.name + " light width is not as specified.")
        lightHeightAttr = usd_light.GetHeightAttr()
        assert_float_equal rectLight.length (lightHeightAttr.Get()) message:(rectLight.name + " light height is not as specified.")

        -- Now export when point light is using spherical light distribution
        rectLight.distribution = 0
        rectLight.length = 40  -- changing default length

        local exportPath = output_prefix + "test_photometric_rect_light_spherical_export.usda"
        USDExporter.ExportFile exportPath exportOptions:exportOptions
        local stage = pyUsd.Stage.Open(exportPath)
        
        usd_light_path = "/" + rectLight.name
        objPrim = stage.GetPrimAtPath usd_light_path
        -- the cylinder light needs to be reoriented for USD
        -- 3ds Max light main axis is the y-axis and USD expects it on the x-axis
        assert_true (pyUsd.Prim.IsValid(objPrim)) message:(rectLight.name + " light was not properly exported to a USD prim.")
        assert_true (objPrim.IsA(pyUsdGeom.Xform)) message:(rectLight.name + " light needs its xform transfo.")

        usd_light_path = "/" + rectLight.name + "/" + rectLight.name + "_Light"
        local objPrim = stage.GetPrimAtPath usd_light_path
        assert_true (objPrim.IsA(pyUsdLux.CylinderLight)) message:(rectLight.name + " light was not properly exported to a USD LuxCylinderLight.")

        usd_light = pyUsdLux.CylinderLight.Get stage usd_light_path
        
        lightRadiusAttr = usd_light.GetRadiusAttr()
        assert_float_equal (rectLight.width / 2.0) (lightRadiusAttr.Get()) message:(rectLight.name + " light radius should be set to half the light width")
        lightLengthAttr = usd_light.GetLengthAttr()
        assert_float_equal rectLight.length (lightLengthAttr.Get()) message:(rectLight.name + " light length is not as specified.")

        -- Now export when point light is using spot light distribution
        rectLight.distribution = 1 -- spot

        local exportPath = output_prefix + "test_photometric_rect_light_spot_export.usda"
        USDExporter.ExportFile exportPath exportOptions:exportOptions
        local stage = pyUsd.Stage.Open(exportPath)
        
        usd_light_path = "/" + rectLight.name
        local objPrim = stage.GetPrimAtPath usd_light_path
        assert_true (objPrim.IsA(pyUsdLux.RectLight)) message:(rectLight.name + " light was not properly exported to a USD LuxRectLight.")

        usd_light = pyUsdLux.RectLight.Get stage usd_light_path
        
        lightWidthAttr = usd_light.GetWidthAttr()
        assert_float_equal rectLight.width (lightWidthAttr.Get()) message:(rectLight.name + " light width is not as specified.")
        lightHeightAttr = usd_light.GetHeightAttr()
        assert_float_equal rectLight.length (lightHeightAttr.Get()) message:(rectLight.name + " light height is not as specified.")

        -- Now export when point light is using web light distribution
        rectLight.distribution = 3 -- web/IES

        local exportPath = output_prefix + "test_photometric_rect_light_web_export.usda"
        USDExporter.ExportFile exportPath exportOptions:exportOptions
        local stage = pyUsd.Stage.Open(exportPath)
        
        local objPrim = stage.GetPrimAtPath usd_light_path
        assert_true (objPrim.IsA(pyUsdLux.RectLight)) message:(rectLight.name + " light was not properly exported to a USD LuxRectLight.")

        usd_light = pyUsdLux.RectLight.Get stage usd_light_path
        
        lightWidthAttr = usd_light.GetWidthAttr()
        assert_float_equal rectLight.width (lightWidthAttr.Get()) message:(rectLight.name + " light width is not as specified.")
        lightHeightAttr = usd_light.GetHeightAttr()
        assert_float_equal rectLight.length (lightHeightAttr.Get()) message:(rectLight.name + " light height is not as specified.")
    ),

    function photometric_rect_light_attributes_animation_export_test = (
        local exportOptions = USDExporter.CreateOptions()
        exportOptions.FileFormat = #ascii
        exportOptions.RootPrimPath = "/"
        exportOptions.Lights = true

        local rectLight = free_light()
        rectLight.type = #Free_Rectangle
        rectLight.distribution = 2 -- uniform hemispherical/diffuse

        with animate on 
        (
            -- Animate some attributes
            at time 10 move rectLight [0,0,20]
            at time 10 rotate rectLight (eulerangles 0 0 -45)
            at time 10 rectLight.length = 100
            at time 10 rectLight.width = 35
            -- see point light animation export for general animatable attributs export
        )
        
        -- Testing #animationRange as TimeMode
        -- Configure the time slider to go from 2 to 8.        
        local rangeStart = 2
        local rangeEnd = 8
        
        animationRange = interval rangeStart rangeEnd
        local exportPath = output_prefix + "test_photometric_rect_light_animationrange_export.usda"
        exportOptions.TimeMode = #animationRange
        USDExporter.ExportFile exportPath exportOptions:exportOptions
        local stage = pyUsd.Stage.Open(exportPath)
        
        usd_light_path = "/" + rectLight.name

        local objPrim = stage.GetPrimAtPath usd_light_path
        assert_true (objPrim.IsA(pyUsdLux.RectLight)) message:(rectLight.name + " light was not properly exported to a USD LuxRectLight.")

        usd_light = pyUsdLux.RectLight.Get stage usd_light_path
        
        local xformable = pyUsdGeom.Xformable(usd_light)
        lightWidthAttr = usd_light.GetWidthAttr()
        lightHeightAttr = usd_light.GetHeightAttr()
        for timeCode in rangeStart to rangeEnd do (
            local t = at time timeCode rectLight.transform
            assert_true (compareUsdAndMaxTransforms (xformable.GetLocalTransformation(timeCode)) t) message:("The light's transform is wrong when exporting with #animationRange at t = " + timeCode as string)
            local w = at time timeCode rectLight.width
            assert_float_equal w (lightWidthAttr.Get(timeCode)) message:(rectLight.name + " light width is not as specified.")
            local l = at time timeCode rectLight.length
            assert_float_equal l (lightHeightAttr.Get(timeCode)) message:(rectLight.name + " light height is not as specified.")
        )
    ),

    function photometric_sun_positioner_export_test = (
        local exportOptions = USDExporter.CreateOptions()
        exportOptions.FileFormat = #ascii
        exportOptions.RootPrimPath = "/"
        exportOptions.Lights = true

        local sun_light = sun_positioner()

        local exportPath = output_prefix + "test_photometric_sun_positioner_export.usda"
        USDExporter.ExportFile exportPath exportOptions:exportOptions
        local stage = pyUsd.Stage.Open(exportPath)
        
        usd_light_path = "/" + sun_light.name

        local objPrim = stage.GetPrimAtPath usd_light_path
        -- the SunPositioner light needs to orient the prim's z-axis as the sun direction
        -- the direction is given based on the instance position
        assert_true (pyUsd.Prim.IsValid(objPrim)) message:(sun_light.name + " light was not properly exported to a USD prim.")
        assert_true (objPrim.IsA(pyUsdGeom.Xform)) message:(sun_light.name + " light needs its xform transfo.")

        usd_light_path = "/" + sun_light.name + "/" + sun_light.name + "_Light"
        
        local objPrim = stage.GetPrimAtPath usd_light_path
        assert_true (objPrim.IsA(pyUsdLux.DistantLight)) message:(sun_light.name + " light was not properly exported to a USD LuxDistantLight.")

        -- by default, the environment map is not installed
        local usd_light = pyUsdLux.DistantLight.Get stage usd_light_path
        local lightColorAttr = usd_light.GetColorAttr()
        assert_false (lightColorAttr.IsAuthored()) message:(sun_light.name + " light should not have its color set")
        local lightIntensityAttr = usd_light.GetIntensityAttr()
        assert_float_equal 50000.f (lightIntensityAttr.Get()) message:(sun_light.name + " light must have its intensity set to '50,000.f' (USD default)")
            
        -- note: unable to create a default environment map thru scripting
        -- using a pre-configured file to have a proper test environment
        quickReset()
        loadmaxfile ((getFilenamePath (getSourceFileName())) + "data\\" + "light-sun-envmap.max") useFileUnits:true
        -- sun positioner entity name
        light_name = "MySun"
        -- light object orientation and position
        -- rotx:-13.471  roty:-41.297  rotz:82.434  (Euler ZYX)
        -- posx:-10  posy:10  posz:10
        -- sun direction is almost oriented on the z-axis (world space)
        local expectedTransform = (pyGf.Matrix4d 0.0989244  0.744752  0.659969  0  -0.943778  0.280452  -0.175015  0  -0.315432  -0.605551  0.730624  0 -10 10 10 1)

        local exportPath = output_prefix + "test_photometric_sun_positioner_envmap_export.usda"
        USDExporter.ExportFile exportPath exportOptions:exportOptions
        local stage = pyUsd.Stage.Open(exportPath)

        usd_light_path = "/" + light_name

        local objPrim = stage.GetPrimAtPath usd_light_path
        -- the SunPositioner light needs to orient the prim's z-axis as the sun direction
        -- the direction is given based on the instance position
        assert_true (pyUsd.Prim.IsValid(objPrim)) message:(light_name + " light was not properly exported to a USD prim.")
        assert_true (objPrim.IsA(pyUsdGeom.Xform)) message:(light_name + " light needs its xform transfo.")
        local xformable = pyUsdGeom.Xformable(objPrim)
        local exportedTransform = xformable.GetLocalTransformation()
        assert_true(compare2dMatrix exportedTransform expectedTransform 4 4) message:"Incorrect sun transform."
        
        usd_light_path = "/" + light_name + "/" + light_name + "_Light"
        
        local objPrim = stage.GetPrimAtPath usd_light_path
        assert_true (objPrim.IsA(pyUsdLux.DistantLight)) message:(light_name + " light was not properly exported to a USD LuxDistantLight.")
        
        local usd_light = pyUsdLux.DistantLight.Get stage usd_light_path
        local lightColorAttr = usd_light.GetColorAttr()
        assert_true (lightColorAttr.IsAuthored()) message:(light_name + " light should have its color set")
        local lightIntensityAttr = usd_light.GetIntensityAttr()
        assert_float_equal 3.f (lightIntensityAttr.Get()) message:(light_name + " light must have its intensity set to '3.f'")
        local light_imageable = pyUsdGeom.Imageable (usd_light)
        local expectedTransform = (pyGf.Matrix4d 0.280316078 0.94320776 -0.00018235648  0  -0.94320214988 0.2803153115  0.0032611217 0 0.0032298288407 -0.0007666744969 0.99999486414 0 -10 10 10 1)
        local exportedWorldTransform = (light_imageable.ComputeLocalToWorldTransform(0))
        assert_true(compare2dMatrix exportedWorldTransform expectedTransform 4 4) message:"Incorrect sun direction in world."
    ),

    -- Helper function to check if USD version supports spline/curves export (24.11+)
    -- Returns true if supported, false otherwise (and prints skip message)
    fn supportsSplineCurves testName = (
        usdVersion = pyUsd.GetVersion()
        local supportCurves = (usdVersion[2] > 24)
        if not supportCurves then (
            format "Skipping %: USD version % does not support curves\n" testName usdVersion
        )
        return supportCurves
    ),

    -- Helper function to create export options for spline animation tests
    fn createSplineExportOptions animationType:#curves = (
        local exportOptions = USDExporter.CreateOptions()
        exportOptions.FileFormat = #ascii
        exportOptions.RootPrimPath = "/"
        exportOptions.Lights = true
        exportOptions.TimeMode = #frameRange
        exportOptions.StartFrame = 0
        exportOptions.EndFrame = 10
        exportOptions.AnimationType = animationType
        return exportOptions
    ),

    -- Helper function to get the spline knot count for an attribute, returns -1 if no spline
    fn getSplineKnotCount prim attrName = (
        local attr = prim.GetAttribute(attrName)
        if attr == undefined or not attr.IsValid() then (
            return -1
        )
        local spline = attr.GetSpline()
        if spline == undefined then (
            return -1
        )
        local knots = spline.GetKnots()
        return knots.count
    ),

    -- Helper function to get the spline knot value at a specific index, returns undefined if not found
    fn getSplineKnotValue prim attrName knotIndex = (
        local attr = prim.GetAttribute(attrName)
        if attr == undefined or not attr.IsValid() then (
            return undefined
        )
        local spline = attr.GetSpline()
        if spline == undefined then (
            return undefined
        )
        local knots = spline.GetKnots()
        local knot = knots[knotIndex]
        if knot == undefined then (
            return undefined
        )
        return knot.GetValue()
    ),

    -- Helper function to validate spline knot count with detailed message
    fn assertSplineKnotCount prim attrName expectedKnotCount lightName = (
        local actualCount = getSplineKnotCount prim attrName
        local message = lightName + " attribute '" + attrName + "' spline knot count: expected " + (expectedKnotCount as string) + ", got " + (actualCount as string)
        assert_equal expectedKnotCount actualCount message:message
    ),

    -- Helper function to validate spline knot value with detailed message
    fn assertSplineKnotValue prim attrName knotIndex expectedValue lightName tolerance:0.001 = (
        local actualValue = getSplineKnotValue prim attrName knotIndex
        local message = lightName + " attribute '" + attrName + "' at knot " + (knotIndex as string) + ": expected " + (expectedValue as string) + ", got " + (if actualValue != undefined then (actualValue as string) else "undefined")
        if actualValue == undefined then (
            assert_true false message:message
        ) else (
            local isClose = (abs(actualValue - expectedValue) < tolerance)
            assert_true isClose message:message
        )
    ),

    -- Test exporting disk light attributes as USD splines
    function photometric_disk_light_spline_animation_export_test = (
        if not (supportsSplineCurves "photometric_disk_light_spline_animation_export_test") then return undefined

        local exportOptions = createSplineExportOptions()
        local diskLight = free_light()
        diskLight.type = #Free_Disc
        diskLight.distribution = 2 -- uniform hemispherical/diffuse (exports as DiskLight)
        diskLight.radius = 10

        with animate on (
            at time 0 diskLight.radius = 10
            at time 5 diskLight.radius = 25
            at time 10 diskLight.radius = 50
        )

        local exportPath = output_prefix + "test_photometric_disk_light_spline_export.usda"
        USDExporter.ExportFile exportPath exportOptions:exportOptions
        local stage = pyUsd.Stage.Open(exportPath)

        local usd_light_path = "/" + diskLight.name
        local objPrim = stage.GetPrimAtPath usd_light_path
        assert_true (objPrim.IsA(pyUsdLux.DiskLight)) message:(diskLight.name + " light was not properly exported to a USD LuxDiskLight.")

        -- Validate that radius attribute has a spline with 3 knots
        assertSplineKnotCount objPrim "inputs:radius" 3 diskLight.name

        -- Validate the spline knot values
        assertSplineKnotValue objPrim "inputs:radius" 1 10.0 diskLight.name
        assertSplineKnotValue objPrim "inputs:radius" 6 25.0 diskLight.name
        assertSplineKnotValue objPrim "inputs:radius" 11 50.0 diskLight.name
    ),

    -- Test exporting sphere light attributes as USD splines
    function photometric_sphere_light_spline_animation_export_test = (
        if not (supportsSplineCurves "photometric_sphere_light_spline_animation_export_test") then return undefined

        local exportOptions = createSplineExportOptions()

        local sphereLight1 = free_light()
        sphereLight1.type = #Free_Sphere
        sphereLight1.distribution = 0 -- uniform spherical (exports as SphereLight)
        sphereLight1.radius = 5
        sphereLight1.useKelvin = true
        sphereLight1.kelvin = 3000
        sphereLight1.useMultiplier = false -- test intensity without dimmer

        local sphereLight2 = free_light()
        sphereLight2.type = #Free_Sphere
        sphereLight2.distribution = 0 -- uniform spherical (exports as SphereLight)
        sphereLight2.radius = 8
        sphereLight2.useKelvin = false -- use RGB color instead of Kelvin
        sphereLight2.useMultiplier = true -- enable dimmer to test combined intensity+multiplier spline

        with animate on (
            -- Animate radius
            at time 0 sphereLight1.radius = 5
            at time 5 sphereLight1.radius = 15
            at time 10 sphereLight1.radius = 30
            at time 0 sphereLight2.radius = 8
            at time 5 sphereLight2.radius = 16
            at time 10 sphereLight2.radius = 24
            -- Animate color temperature (Kelvin)
            at time 0 sphereLight1.kelvin = 3000
            at time 5 sphereLight1.kelvin = 5500
            at time 10 sphereLight1.kelvin = 8000
            -- Animate intensity
            at time 0 sphereLight1.intensity = 500
            at time 5 sphereLight1.intensity = 1000
            at time 10 sphereLight1.intensity = 2000
            at time 0 sphereLight2.intensity = 1000
            at time 5 sphereLight2.intensity = 2000
            at time 10 sphereLight2.intensity = 1000
            -- Animate multiplier (dimmer percentage)
            at time 0 sphereLight2.multiplier = 100
            at time 5 sphereLight2.multiplier = 50
            at time 10 sphereLight2.multiplier = 100
        )

        local exportPath = output_prefix + "test_photometric_sphere_light_spline_export.usda"
        USDExporter.ExportFile exportPath exportOptions:exportOptions
        local stage = pyUsd.Stage.Open(exportPath)

        local usd_light_path1 = "/" + sphereLight1.name
        local usd_light_path2 = "/" + sphereLight2.name
        local objPrim1 = stage.GetPrimAtPath usd_light_path1
        local objPrim2 = stage.GetPrimAtPath usd_light_path2
        assert_true (objPrim1.IsA(pyUsdLux.SphereLight))
        assert_true (objPrim2.IsA(pyUsdLux.SphereLight))

        -- Validate radius attribute spline
        assertSplineKnotCount objPrim1 "inputs:radius" 3 sphereLight1.name
        assertSplineKnotValue objPrim1 "inputs:radius" 1 5.0 sphereLight1.name
        assertSplineKnotValue objPrim1 "inputs:radius" 6 15.0 sphereLight1.name
        assertSplineKnotValue objPrim1 "inputs:radius" 11 30.0 sphereLight1.name

        -- Validate colorTemperature attribute spline (useKelvin=true)
        assertSplineKnotCount objPrim1 "inputs:colorTemperature" 3 sphereLight1.name
        assertSplineKnotValue objPrim1 "inputs:colorTemperature" 1 3000.0 sphereLight1.name
        assertSplineKnotValue objPrim1 "inputs:colorTemperature" 6 5500.0 sphereLight1.name
        assertSplineKnotValue objPrim1 "inputs:colorTemperature" 11 8000.0 sphereLight1.name

        assertSplineKnotCount objPrim1 "inputs:intensity" 3 sphereLight1.name

        local usd_light_path2 = "/" + sphereLight2.name
        local objPrim2 = stage.GetPrimAtPath usd_light_path2
        assert_true (objPrim2.IsA(pyUsdLux.SphereLight))

        -- Validate enableColorTemperature is false (useKelvin=false)
        local usdLight2 = pyUsdLux.BoundableLightBase.Get stage usd_light_path2
        local enableColorTempAttr2 = usdLight2.GetEnableColorTemperatureAttr()
        local enableColorTemp2 = enableColorTempAttr2.Get()
        assert_false enableColorTemp2

        -- Validate radius attribute spline
        assertSplineKnotCount objPrim2 "inputs:radius" 3 sphereLight2.name
        assertSplineKnotValue objPrim2 "inputs:radius" 1 8.0 sphereLight2.name
        assertSplineKnotValue objPrim2 "inputs:radius" 6 16.0 sphereLight2.name
        assertSplineKnotValue objPrim2 "inputs:radius" 11 24.0 sphereLight2.name

        -- Validate intensity attribute has a spline (useMultiplier=true, combined intensity+multiplier spline)
        local intensityKnotCount = getSplineKnotCount objPrim2 "inputs:intensity"
        assert_true (intensityKnotCount > 0) message:(sphereLight2.name + " should have intensity spline when useMultiplier=true, got " + (intensityKnotCount as string) + " knots")
    ),

    -- Test exporting cylinder light attributes as USD splines
    function photometric_cylinder_light_spline_animation_export_test = (
        if not (supportsSplineCurves "photometric_cylinder_light_spline_animation_export_test") then return undefined

        local exportOptions = createSplineExportOptions()
        local cylinderLight = free_light()
        cylinderLight.type = #Free_Cylinder
        cylinderLight.distribution = 0 -- uniform spherical (exports as CylinderLight)
        cylinderLight.length = 20
        cylinderLight.radius = 3
        cylinderLight.intensity = 2000

        with animate on (
            at time 0 (
                cylinderLight.length = 20
                cylinderLight.radius = 3
            )
            at time 5 (
                cylinderLight.length = 40
                cylinderLight.radius = 6
            )
            at time 10 (
                cylinderLight.length = 80
                cylinderLight.radius = 12
            )
        )

        local exportPath = output_prefix + "test_photometric_cylinder_light_spline_export.usda"
        USDExporter.ExportFile exportPath exportOptions:exportOptions
        local stage = pyUsd.Stage.Open(exportPath)

        -- Cylinder lights need xform transformation, so the light is under an xform prim
        local usd_light_path = "/" + cylinderLight.name + "/" + cylinderLight.name + "_Light"
        local objPrim = stage.GetPrimAtPath usd_light_path
        assert_true (objPrim.IsA(pyUsdLux.CylinderLight)) message:(cylinderLight.name + " light was not properly exported to a USD LuxCylinderLight.")

        -- Validate that length attribute has a spline with 3 knots
        assertSplineKnotCount objPrim "inputs:length" 3 cylinderLight.name
        -- Validate that radius attribute has a spline with 3 knots
        assertSplineKnotCount objPrim "inputs:radius" 3 cylinderLight.name

        -- Validate the length spline knot values
        assertSplineKnotValue objPrim "inputs:length" 1 20.0 cylinderLight.name
        assertSplineKnotValue objPrim "inputs:length" 6 40.0 cylinderLight.name
        assertSplineKnotValue objPrim "inputs:length" 11 80.0 cylinderLight.name

        -- Validate the radius spline knot values
        assertSplineKnotValue objPrim "inputs:radius" 1 3.0 cylinderLight.name
        assertSplineKnotValue objPrim "inputs:radius" 6 6.0 cylinderLight.name
        assertSplineKnotValue objPrim "inputs:radius" 11 12.0 cylinderLight.name

        -- Validate that static intensity is present (not animated, so no spline, but should have a value)
        local usdLight = pyUsdLux.CylinderLight.Get stage usd_light_path
        local intensityAttr = usdLight.GetIntensityAttr()
        assert_true (intensityAttr.IsValid()) message:(cylinderLight.name + " should have a valid intensity attribute.")
        assert_true (intensityAttr.IsAuthored()) message:(cylinderLight.name + " static intensity should be authored even with spline export.")
        local intensityValue = intensityAttr.Get()
        assert_true (intensityValue > 0) message:(cylinderLight.name + " static intensity should have a value > 0, got " + (intensityValue as string))
    ),

    -- Test exporting rectangle light attributes as USD splines
    function photometric_rect_light_spline_animation_export_test = (
        if not (supportsSplineCurves "photometric_rect_light_spline_animation_export_test") then return undefined

        local exportOptions = createSplineExportOptions()
        local rectLight = free_light()
        rectLight.type = #Free_Rectangle
        rectLight.distribution = 2 -- uniform hemispherical/diffuse (exports as RectLight)
        rectLight.width = 10
        rectLight.length = 15

        with animate on (
            at time 0 (
                rectLight.width = 10
                rectLight.length = 15
            )
            at time 5 (
                rectLight.width = 20
                rectLight.length = 30
            )
            at time 10 (
                rectLight.width = 40
                rectLight.length = 60
            )
        )

        local exportPath = output_prefix + "test_photometric_rect_light_spline_export.usda"
        USDExporter.ExportFile exportPath exportOptions:exportOptions
        local stage = pyUsd.Stage.Open(exportPath)

        local usd_light_path = "/" + rectLight.name
        local objPrim = stage.GetPrimAtPath usd_light_path
        assert_true (objPrim.IsA(pyUsdLux.RectLight)) message:(rectLight.name + " light was not properly exported to a USD LuxRectLight.")

        -- Validate that width attribute has a spline with 3 knots
        assertSplineKnotCount objPrim "inputs:width" 3 rectLight.name
        -- Validate that height attribute has a spline with 3 knots
        assertSplineKnotCount objPrim "inputs:height" 3 rectLight.name

        -- Validate the width spline knot values
        assertSplineKnotValue objPrim "inputs:width" 1 10.0 rectLight.name
        assertSplineKnotValue objPrim "inputs:width" 6 20.0 rectLight.name
        assertSplineKnotValue objPrim "inputs:width" 11 40.0 rectLight.name

        -- Validate the height spline knot values (maps from 3ds Max 'length')
        assertSplineKnotValue objPrim "inputs:height" 1 15.0 rectLight.name
        assertSplineKnotValue objPrim "inputs:height" 6 30.0 rectLight.name
        assertSplineKnotValue objPrim "inputs:height" 11 60.0 rectLight.name
    ),

    -- Test exporting spotlight beam angle as USD spline
    function photometric_spotlight_beam_angle_spline_animation_export_test = (
        if not (supportsSplineCurves "photometric_spotlight_beam_angle_spline_animation_export_test") then return undefined

        local exportOptions = createSplineExportOptions()
        local spotLight = free_light()
        spotLight.type = #Free_Point
        spotLight.distribution = 1 -- spotlight distribution (exports as DiskLight with ShapingAPI)
        spotLight.beamAngle = 30
        spotLight.intensity = 1500

        with animate on (
            at time 0 spotLight.beamAngle = 30
            at time 5 spotLight.beamAngle = 45
            at time 10 spotLight.beamAngle = 60
        )

        local exportPath = output_prefix + "test_photometric_spotlight_beam_angle_spline_export.usda"
        USDExporter.ExportFile exportPath exportOptions:exportOptions
        local stage = pyUsd.Stage.Open(exportPath)

        local usd_light_path = "/" + spotLight.name
        local objPrim = stage.GetPrimAtPath usd_light_path
        assert_true (objPrim.IsA(pyUsdLux.DiskLight)) message:(spotLight.name + " light was not properly exported to a USD LuxDiskLight.")

        -- Validate that shaping:cone:angle attribute has a spline with 3 knots
        assertSplineKnotCount objPrim "inputs:shaping:cone:angle" 3 spotLight.name

        -- Validate the shaping:cone:angle spline knot values
        assertSplineKnotValue objPrim "inputs:shaping:cone:angle" 1 30.0 spotLight.name
        assertSplineKnotValue objPrim "inputs:shaping:cone:angle" 6 45.0 spotLight.name
        assertSplineKnotValue objPrim "inputs:shaping:cone:angle" 11 60.0 spotLight.name

        -- Validate that static intensity is present (not animated, so no spline, but should have a value)
        local usdLight = pyUsdLux.DiskLight.Get stage usd_light_path
        local intensityAttr = usdLight.GetIntensityAttr()
        assert_true (intensityAttr.IsValid()) message:(spotLight.name + " should have a valid intensity attribute.")
        assert_true (intensityAttr.IsAuthored()) message:(spotLight.name + " static intensity should be authored even with spline export.")
        local intensityValue = intensityAttr.Get()
        assert_true (intensityValue > 0) message:(spotLight.name + " static intensity should have a value > 0, got " + (intensityValue as string))
    ),

    -- Test exporting line light attributes (as RectLight) with USD splines
    function photometric_line_light_spline_animation_export_test = (
        if not (supportsSplineCurves "photometric_line_light_spline_animation_export_test") then return undefined

        local exportOptions = createSplineExportOptions()
        local lineLight = free_light()
        lineLight.type = #Free_Line
        lineLight.distribution = 2 -- uniform hemispherical/diffuse (exports as RectLight)
        lineLight.length = 30
        lineLight.intensity = 1800

        with animate on (
            at time 0 lineLight.length = 30
            at time 5 lineLight.length = 60
            at time 10 lineLight.length = 120
        )

        local exportPath = output_prefix + "test_photometric_line_light_spline_export.usda"
        USDExporter.ExportFile exportPath exportOptions:exportOptions
        local stage = pyUsd.Stage.Open(exportPath)

        local usd_light_path = "/" + lineLight.name
        local objPrim = stage.GetPrimAtPath usd_light_path
        assert_true (objPrim.IsA(pyUsdLux.RectLight)) message:(lineLight.name + " light was not properly exported to a USD LuxRectLight.")

        -- Validate that height attribute has a spline with 3 knots (line light length maps to height)
        assertSplineKnotCount objPrim "inputs:height" 3 lineLight.name

        -- Validate the height spline knot values
        assertSplineKnotValue objPrim "inputs:height" 1 30.0 lineLight.name
        assertSplineKnotValue objPrim "inputs:height" 6 60.0 lineLight.name
        assertSplineKnotValue objPrim "inputs:height" 11 120.0 lineLight.name

        -- Validate that static intensity is present (not animated, so no spline, but should have a value)
        local usdLight = pyUsdLux.RectLight.Get stage usd_light_path
        local intensityAttr = usdLight.GetIntensityAttr()
        assert_true (intensityAttr.IsValid()) message:(lineLight.name + " should have a valid intensity attribute.")
        assert_true (intensityAttr.IsAuthored()) message:(lineLight.name + " static intensity should be authored even with spline export.")
        local intensityValue = intensityAttr.Get()
        assert_true (intensityValue > 0) message:(lineLight.name + " static intensity should have a value > 0, got " + (intensityValue as string))
    ),

    function teardown = (
        units.SystemType = initialUnits
        units.SystemScale = initialScale
    ),

    Tests = #(
        photometric_light_general_attributes_export_test,
        photometric_light_on_off_export_test,
        photometric_light_writer_legacy_dropout_audit_test,
        photometric_light_writer_legacy_attribute_dropout_audit_test,
        photometric_light_ies_file_export_test,
        photometric_light_kelvin_filter_color_round_trip_test,
        photometric_point_light_attributes_export_test,
        photometric_point_light_attributes_animation_export_test,
        photometric_sphere_light_attributes_export_test,
        photometric_sphere_light_attributes_animation_export_test,
        photometric_disk_light_attributes_export_test,
        photometric_disk_light_attributes_animation_export_test,
        photometric_line_light_attributes_export_test,
        photometric_line_light_attributes_animation_export_test,
        photometric_cylinder_light_attributes_export_test,
        photometric_cylinder_light_attributes_animation_export_test,
        photometric_rect_light_attributes_export_test,
        photometric_rect_light_attributes_animation_export_test,
        photometric_sun_positioner_export_test,
        -- Spline/Curves animation tests (require USD 24.11+)
        photometric_disk_light_spline_animation_export_test,
        photometric_sphere_light_spline_animation_export_test,
        photometric_cylinder_light_spline_animation_export_test,
        photometric_rect_light_spline_animation_export_test,
        photometric_spotlight_beam_angle_spline_animation_export_test,
        photometric_line_light_spline_animation_export_test
    )
)

runUsdTestFixture export_light_test (getThisScriptFilename())