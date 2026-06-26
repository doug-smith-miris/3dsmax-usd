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
pyUsdTf = python.import("pxr.Tf")

output_prefix = pathConfig.appendPath (getDir #temp) "/EXPORT_GEOMETRY_TEST_"
deleteTestFiles(output_prefix + "*.*")

struct export_geometry_test
(  
    private 
    exportOptions = USDExporter.CreateOptions(),
    
    public 
    function setup =
    (
        quickReset()
        exportOptions.FileFormat = #ascii
        exportOptions.RootPrimPath = "/"
        exportOptions.UsdStagesAsReferences = false
    ),
    
    -- Test that the edge visibility of triangular meshes is adjusted for export so that
    -- the conversion to poly part of the USD export produces the right results if we are exporting
    -- poly meshes.
    function export_triobj_with_bad_edgevis_test = (
        local boxNode = box()
        boxNode.name = "box"
        local boxTriMesh = convertToMesh boxNode
        -- Set all edge invisible. This would usually break the conversion to Poly that happens 
        -- on export without additional work (which is what we are testing here!)
        for i = 1 to boxTriMesh.numfaces do (
            setEdgeVis boxTriMesh i 1 false
            setEdgeVis boxTriMesh i 2 false
            setEdgeVis boxTriMesh i 3 false
        )
        local exportPath = output_prefix + "test_hidden_tri_edges.usd"
        exportOptions.MeshFormat = #polyMesh
        USDExporter.ExportFile exportPath exportOptions:exportOptions
        local stage = pyUsd.Stage.Open(exportPath)
        local boxPrim = stage.GetPrimAtPath("/" + boxNode.name)
        assert_true (pyUsd.Prim.IsValid(boxPrim)) message:"The box was not properly exported to a USD prim."
        assert_true (boxPrim.IsA(pyUsdGeom.Mesh)) message:"The box should have been exported to a mesh prim."

        -- Make sure that prior to export, "corner" edges were set to visible.
        local mesh = pyUsdGeom.Mesh(boxPrim)
        local vertexCounts = ((boxPrim.GetAttribute("faceVertexCounts")).Get())
        assert_equal 6 vertexCounts.count messsage:"The box should have been exported to 6 faces."
            
        -- Make sure this was done in a copy, and did not affect the mesh in the scene.
        local edgesShouldBeHiddenError = "The mesh's edges should still be hidden"
        for i = 1 to boxTriMesh.numfaces do (
            assert_false (getEdgeVis boxTriMesh i 1) message:edgesShouldBeHiddenError
            assert_false (getEdgeVis boxTriMesh i 2) message:edgesShouldBeHiddenError
            assert_false (getEdgeVis boxTriMesh i 3) message:edgesShouldBeHiddenError
        )
    ),

    -- Make sure that all of max's geometric objects, which can be converted to either 
    -- triangular meshes or poly meshes are exported to USD.
    function export_all_geometric_types_test = (
        local allObjects = #()
        allObjects = for o in GeometryClass.classes where o.creatable collect o

        local expectedMeshObjects = #()
        for i = 1 to allObjects.count do
        (
            -- Align the objects in a grid for easy visual inspection...
            local x = (mod i 10) * 100
            local y = ((i / 10) as integer) * 100
            local obj = (allObjects[i] height:10 length:10 width:10)
            obj.pos.x = x
            obj.pos.y = y
            obj.pos.z = 0
    
            if ((canConvertTo obj Editable_mesh) or (canConvertTo obj Editable_poly)) then (
                append expectedMeshObjects obj                
            )
        )
        
        local exportPath = output_prefix + "export_all_geometric_objects.usd"
        USDExporter.ExportFile exportPath exportOptions:exportOptions
        local stage = pyUsd.Stage.Open(exportPath)
        root = stage.GetPrimAtPath("/")
        assert_equal expectedMeshObjects.count (root.GetAllChildren()).count message:"Unexpected prim count when exporting all geometric objects."
    
        for i = 1 to expectedMeshObjects.count do (
            local name = expectedMeshObjects[i].name
            local objPrim = stage.GetPrimAtPath("/" + pyUsdTf.MakeValidIdentifier(name))
            assert_true (pyUsd.Prim.IsValid(objPrim)) message:(name + " of class " + ((classOf expectedMeshObjects[i]) as string) + " was not properly exported to a USD mesh.")
            assert_true (objPrim.IsA(pyUsdGeom.Mesh)) message:(name + " of class " + ((classOf expectedMeshObjects[i]) as string) + " should have been exported to a mesh type prim.")
        )
    ),

    -- MAX-GEO-004 SURGICAL-PRESERVATION REINFORCEMENT: when the mesh
    -- ALREADY has `primvars:st` (the artist authored real UVs via
    -- `Generate Mapping Coords.` on the source primitive or a UVW Map
    -- modifier), the fallback helper `EnsureFallbackStPrimvar` MUST NOT
    -- touch the existing primvar. A widening that dropped the
    -- `HasPrimvar(stTokenName)` gate would overwrite the artist's UV
    -- mapping with the bbox planar projection -- silently warping any
    -- texture-bearing material bound to the mesh on every export.
    --
    -- The Python validator
    -- `validate_fallback_st_primvar_surgical.py` exercises 8 cases
    -- across all four surgical bounds (EmptyConfig / PreserveBranch /
    -- DegenerateMesh / DegenerateBbox) + the WriteBranch shape
    -- invariants + idempotence; this MaxScript test pins the MOST
    -- COMMON preserve-branch case (a Plane primitive, whose
    -- `mapCoords` defaults to true, so `ApplyMaxMapChannels` writes a
    -- faceVarying `primvars:st` that the fallback helper sees and
    -- skips) at the export level so a regression that escapes the
    -- Python mirror still fails inside Max.
    --
    -- The bound this test pins, in full:
    --   * The exported mesh has `primvars:st` (helper did NOT skip
    --     authoring entirely on a preserve-branch case).
    --   * The primvar's component type is `TexCoord2fArray` (matches
    --     the WriteBranch type AND the type ApplyMaxMapChannels
    --     writes -- a widening that homogenised to Float2Array on
    --     either path would surface here).
    --   * The primvar's interpolation is `faceVarying` (the shape
    --     `ApplyMaxMapChannels` writes for channel 1 on a Plane) --
    --     NOT `vertex` (what the fallback's WriteBranch would write).
    --     A widening that dropped the preserve gate and re-emitted
    --     would change interpolation to `vertex` AND change the array
    --     length from `numfaces * 4 = 4` to `numpoints = 4`. Length
    --     coincides for a single-quad Plane but interpolation does
    --     not -- this is the SHAPE assertion the bound rides on.
    --   * The primvar's array length matches the source channel's
    --     entry count (4 corners for a 1-quad Plane), which is the
    --     `faceVarying` length, NOT necessarily the vertex count.
    function test_fallback_st_primvar_preserves_authored_face_varying_uvs = (
        testFilePath = output_prefix + "test_fallback_st_primvar_preserves_authored_face_varying_uvs.usd"
        -- Plane primitive: `mapCoords` defaults to TRUE on a Plane
        -- (one of the few parametric primitives where this is the
        -- default), so `ApplyMaxMapChannels` writes channel 1 as
        -- `primvars:st` with faceVarying interpolation. The
        -- fallback helper sees HasPrimvar("st") == true and returns
        -- early -- preserving the artist UVs verbatim.
        local p = plane name:"planeWithMapCoords" length:5 width:5 \
            lengthsegs:1 widthsegs:1 mapCoords:true
        convertToPoly p

        USDExporter.ExportFile testFilePath exportOptions:exportOptions

        stage = pyUsd.Stage.Open(testFilePath)
        prim = stage.GetPrimAtPath("/" + p.name)
        assert_true (pyUsd.Prim.IsValid(prim)) message:"The Plane was not properly exported."
        assert_true (prim.IsA(pyUsdGeom.Mesh)) message:"The Plane should have been exported as a UsdGeomMesh."

        -- Helper to read the st primvar's shape from the exported file.
        local primvarsAPI = pyUsdGeom.PrimvarsAPI(prim)
        local stPrimvar = primvarsAPI.GetPrimvar("st")
        assert_true (stPrimvar.IsDefined()) \
            message:"Preserve-branch surgical bound: the Plane's primvars:st was DROPPED on export. A regression in EnsureFallbackStPrimvar's HasPrimvar gate or in ApplyMaxMapChannels' channel-1 authoring path is responsible. See MAX-GEO-004 in doc/translation-mapping.md."
        -- Length invariant: faceVarying st on a 1-quad Plane has 4
        -- entries (one per face-vertex corner). A regression that
        -- re-emitted via the fallback would either match this length
        -- (vertex on a 4-vertex Plane is also 4) OR change it
        -- (vertex on a 9-vertex Plane is 9, faceVarying is 16) --
        -- the length alone is not a sufficient discriminator for a
        -- 1-quad Plane, but interpolation is.
        local stValues = stPrimvar.Get()
        assert_equal 4 stValues.count \
            message:"Preserve-branch surgical bound: the Plane's primvars:st has unexpected length. Expected the original faceVarying-corner count (4), got the current count. A regression that re-emitted under WriteBranch (vertex interpolation, length = vertex count) on a Plane with > 1 segment would surface here too."
        -- Interpolation invariant: `faceVarying`, NOT `vertex`. THIS
        -- IS THE PRIMARY SURGICAL ASSERTION -- a widening that
        -- dropped the HasPrimvar gate and re-emitted via the
        -- WriteBranch would write interpolation=`vertex`, which a
        -- 1-quad Plane's 4 face-vertex-corners coincidentally
        -- matches by COUNT (4) but not by INTERPOLATION TOKEN.
        local interpolation = stPrimvar.GetInterpolation()
        assert_equal "faceVarying" interpolation \
            message:"Preserve-branch surgical bound: the Plane's primvars:st interpolation is the wrong token. Expected \"faceVarying\" (what ApplyMaxMapChannels writes); a regression that dropped the EnsureFallbackStPrimvar HasPrimvar gate and re-emitted under WriteBranch would change it to \"vertex\"."
        -- Type invariant: `TexCoord2fArray`, NOT `Vec2fArray` /
        -- `Float2Array`. The MaterialX role on the TexCoord2f type
        -- is what UsdUVTexture's `inputs:st` expects; a regression
        -- that changed the type on either the preserve or write
        -- branches would break the typesystem contract.
        -- (Uses the same `GetTypeName() as string` pattern as
        -- existing map_channel_export_test.ms assertions.)
        assert_equal "texCoord2f[]" (stPrimvar.GetTypeName() as string) \
            message:"Preserve-branch surgical bound: the Plane's primvars:st value type is the wrong type. Expected \"texCoord2f[]\" (the TexCoord2fArray alias); a regression that downgraded the type to Vec2fArray / Float2Array on either branch would surface here."
    ),

    function teardown = (
    ),

    Tests = #(export_triobj_with_bad_edgevis_test,
        export_all_geometric_types_test,
        test_fallback_st_primvar_preserves_authored_face_varying_uvs)
)

runUsdTestFixture export_geometry_test (getThisScriptFilename())