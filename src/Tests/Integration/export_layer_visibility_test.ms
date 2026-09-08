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

fileIn "helpers/usd_test_utils.mxs"

-- Make sure the USD plugin is properly loaded.
setupUsdPlugins()

pyUsd = python.import("pxr.Usd")
pyUsdGeom = python.import("pxr.UsdGeom")

output_prefix = pathConfig.appendPath (getDir #temp) "/EXPORT_LAYER_VISIBILITY_TEST_"
deleteTestFiles(output_prefix)

-- MAX-VIS-027. 3ds Max hides objects two ways: per-object, and by switching a LAYER off.
-- INode::IsNodeHidden() only knows about the first -- inode.h documents it as covering "the
-- node hidden attribute and the 'Hide By Category' flags", with layers absent. The exporter
-- called it bare, so an object hidden only by its layer exported with no visibility authored
-- at all. On the generic arena that leaked 11 prims (4 interior, 7 exterior) that the artist
-- had switched off.
struct export_layer_visibility_test
(
    private
    exportOptions = USDExporter.CreateOptions(),

    function makeLayer name =
    (
        local existing = LayerManager.getLayerFromName name
        if existing != undefined then LayerManager.deleteLayerByName name
        LayerManager.newLayerFromName name
    ),

    public
    function setup =
    (
        exportOptions.FileFormat = #ascii
        exportOptions.RootPrimPath = "/"
        exportOptions.UseUSDVisibility = true
        quickReset()
    ),

    function test_layer_hidden_exports_invisible = (
        testFilePath = output_prefix + "test_layer_hidden.usda"

        local onBox = box name:"onLayerBox"
        local offBox = box name:"offLayerBox"

        local layerOn = makeLayer "vis_layer_on"
        local layerOff = makeLayer "vis_layer_off"
        layerOn.addNode onBox
        layerOff.addNode offBox
        layerOff.on = false

        -- The point of the test: the object's own hidden flag is untouched. Only the layer is
        -- off, which is precisely the case IsNodeHidden() cannot see.
        assert_false offBox.isHidden message:"Test setup is wrong: the object itself must not be hidden."
        assert_false (isNodeHidden offBox) message:"Test setup assumes isNodeHidden() is blind to layers."

        USDExporter.ExportFile testFilePath exportOptions:exportOptions

        stage = pyUsd.Stage.Open(testFilePath)

        local offPrim = stage.GetPrimAtPath("/offLayerBox")
        assert_true (offPrim.IsValid()) message:"The layer-hidden object should still be exported, just invisible."
        local offImageable = pyUsdGeom.Imageable(offPrim)
        assert_equal "invisible" (offImageable.ComputeVisibility()) \
            message:"An object on a switched-off layer must export as invisible."
        assert_true (offImageable.GetVisibilityAttr().HasAuthoredValue()) \
            message:"Visibility must be authored, not merely inherited from a default."

        -- And the control: a sibling on a layer that is on must be untouched.
        local onImageable = pyUsdGeom.Imageable(stage.GetPrimAtPath("/onLayerBox"))
        assert_equal "inherited" (onImageable.ComputeVisibility()) \
            message:"An object on a visible layer must not be made invisible."
    ),

    function test_nested_layer_hidden_exports_invisible = (
        testFilePath = output_prefix + "test_nested_layer_hidden.usda"

        local b = box name:"nestedLayerBox"
        local parentLayer = makeLayer "vis_parent_layer"
        local childLayer = makeLayer "vis_child_layer"
        childLayer.setParent parentLayer
        childLayer.addNode b

        -- The child layer is on; only its PARENT is off. Layers nest, so the object is still
        -- hidden in the viewport, and the walk up the layer chain has to catch that.
        parentLayer.on = false
        assert_true childLayer.on message:"Test setup is wrong: the child layer must stay on."

        USDExporter.ExportFile testFilePath exportOptions:exportOptions

        stage = pyUsd.Stage.Open(testFilePath)
        local imageable = pyUsdGeom.Imageable(stage.GetPrimAtPath("/nestedLayerBox"))
        assert_equal "invisible" (imageable.ComputeVisibility()) \
            message:"An object whose ancestor layer is off must export as invisible."
    ),

    function test_layer_visibility_respects_option = (
        -- GetUseUSDVisibility() gates the whole behaviour. With it off, nothing should be
        -- authored -- the option is respected, not overridden.
        testFilePath = output_prefix + "test_option_off.usda"

        local opts = USDExporter.CreateOptions()
        opts.FileFormat = #ascii
        opts.RootPrimPath = "/"
        opts.UseUSDVisibility = false

        local b = box name:"optionOffBox"
        local layerOff = makeLayer "vis_option_layer"
        layerOff.addNode b
        layerOff.on = false

        USDExporter.ExportFile testFilePath exportOptions:opts

        stage = pyUsd.Stage.Open(testFilePath)
        local imageable = pyUsdGeom.Imageable(stage.GetPrimAtPath("/optionOffBox"))
        assert_false (imageable.GetVisibilityAttr().HasAuthoredValue()) \
            message:"With UseUSDVisibility off, no visibility should be authored for a layer-hidden object."
    ),

    function teardown = (
    ),

    tests = #(test_layer_hidden_exports_invisible,
        test_nested_layer_hidden_exports_invisible,
        test_layer_visibility_respects_option)
)

runUsdTestFixture export_layer_visibility_test (getThisScriptFilename())
