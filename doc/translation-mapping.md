# 3ds Max → MaterialX / USD translation mapping

This document tracks how 3ds Max PhysicalMaterial / OpenPBR / MaterialXMaterial
properties translate to MaterialX `standard_surface` (and related) shader
inputs as they are written through the Miris fork's USD exporter
(`src/translators/MtlxShaderWriter.cpp`).

Generated and maintained by the Improvement Agent. Each entry corresponds to
a PR that:

1. Updates the relevant row of the **Status** table below.
2. Lands a C++ change in the writer (almost always
   `MtlxShaderWriter.cpp` for surface-shader mappings).
3. Lands a validator script (Python, runnable under `hython`) that proves the
   logic against the captured corpus in `samples/complex_export.usda` (or a
   synthetic fixture) without requiring a Windows build.
4. Where the bite is a *workaround for an upstream 3ds Max bug*, it
   documents which 3ds Max version(s) the workaround targets and what
   condition retires it.

## Translation model — what the writer can actually change

`MtlxShaderWriter.cpp` is mostly a **passthrough**: it asks 3ds Max for a
MaterialX XML string via the MaxScript bridge `MtlxIOUtil.ExportMtlxString`,
parses it, and walks the resulting `MaterialX::Document` to create USD
shader prims. The MaxScript bridge lives inside 3ds Max itself and cannot be
modified from this plugin.

The C++ writer therefore has two intervention points:

1. **Pre-passthrough** (impossible — the MaxScript bridge has already
   serialized whatever it serialized).
2. **Post-parse, pre-USD-author** — after `readFromXmlString`, the
   in-memory MaterialX document can be normalized before any USD prims are
   created. This is where workarounds for buggy 3ds Max defaults live.

Status legend:

* **direct pairing** — a 1:1 mapping between a 3ds Max source property and a
  MaterialX node/input that the upstream bridge already emits correctly. No
  C++ intervention required.
* **approximating workaround** — the writer mutates the MaterialX document
  to compose a `standard_surface`-compatible result that approximates a 3ds
  Max feature the bridge does not faithfully translate.
* **bug normalization** — the writer strips or rewrites a value that the
  upstream bridge emits incorrectly (hardcoded default, off-by-one, wrong
  unit, etc.) so the resulting USD matches what a correctly-implemented
  exporter should produce.
* **no equivalent** — the source property has no MaterialX representation;
  the writer emits a `TF_WARN` so the divergence is observable.
* **observability hint** — the exporter authors USD-correct output, but a
  silent semantic gap exists between what 3ds Max records and what
  downstream USD consumers assume (most commonly the unit-system mismatch
  documented as MAX-UNIT-001). The fix is a `MaxUsd::Log::Warn` call so the
  artist sees the divergence at export time and can either reconfigure
  3ds Max or post-process the USD; the exported bytes are unchanged.

## Status

| Source (3ds Max) | MaterialX target | Kind | Affects MaterialX nodedef | Workaround triggers when | PR | Date |
| --- | --- | --- | --- | --- | --- | --- |
| PhysicalMaterial.anisotropy_angle | `standard_surface.specular_rotation` | bug normalization | `ND_standard_surface_surfaceshader` | Bridge emits `0.25` AND `specular_anisotropy` is static-zero or absent | MAX-MAT-001 | 2026-06-20 |
| PhysicalMaterial.emission (none authored) | `standard_surface.emission` + `standard_surface.emission_color` | bug normalization | `ND_standard_surface_surfaceshader` | Bridge emits `emission = 1.0` AND `emission_color = (0, 0, 0)`, both static | MAX-MAT-002 | 2026-06-20 |
| PhysicalMaterial.coat (none authored) | `standard_surface.coat_IOR`, `.coat_affect_color`, `.coat_affect_roughness`, `.coat_roughness` | bug normalization | `ND_standard_surface_surfaceshader` | Bridge emits the coat-block preset (`coat_IOR = 1.52`, `coat_affect_color = 0.5`, `coat_affect_roughness = 0.5`, `coat_roughness = 0.0`) on every shader AND `coat` is statically zero or absent. Each leak input is stripped independently when it matches its leak value; user overrides are preserved | MAX-MAT-004 | 2026-06-23 |
| PhysicalMaterial.subsurface (none authored) | `standard_surface.subsurface_radius` | bug normalization | `ND_standard_surface_surfaceshader` | Bridge emits the Pixar/Hery skin SSS triple `(0.794704, 0.531734, 0.292854)` on every shader AND `subsurface` is statically zero or absent. After strip falls back to the nodedef default `(1, 1, 1)`; user overrides away from the leak triple are preserved | MAX-MAT-005 | 2026-06-23 |
| PhysicalMaterial — every shader, no source-DCC bind | 13 `standard_surface` inputs authored redundantly at their nodedef default (`base`, `coat`, `coat_color`, `diffuse_roughness`, `specular`, `specular_color`, `subsurface`, `subsurface_color`, `subsurface_scale`, `thin_walled`, `transmission`, `transmission_color`, `transmission_depth`) | bug normalization | `ND_standard_surface_surfaceshader` v1.0.1 | Bridge authors any of the 13 inputs at its v1.0.1 nodedef-default value AND the input is not connected. Each input is stripped independently; values that differ from the nodedef default are preserved as artist intent | MAX-MAT-006 | 2026-06-23 |
| Node.wireColor / Node.material.diffuse | `UsdGeomMesh.primvars:displayColor` | bug normalization | (mesh primvar; not a shader nodedef) | `MeshConverter` is about to author wireColor into `primvars:displayColor` AND `node->GetMtl() != nullptr` | MAX-MAT-003 | 2026-06-20 |
| Mesh normals (every interpolation) | `primvars:normals` AND `UsdGeomMesh.normals` (the schema attribute) | bug normalization | (mesh attribute; not a shader nodedef) | `NormalsMode` default is now `Both` -- both locations are authored unless the user explicitly picks `AsPrimvar` or `AsAttribute` | MAX-GEO-001 | 2026-06-20 |
| Map channel 1 missing on the converted MNMesh | `primvars:st` (channel 1's configured primvar) | approximating workaround | (mesh primvar; not a shader nodedef) | `ApplyMaxMapChannels` did not author the channel-1 primvar AND channel 1 is not explicitly opted out (`GetChannelPrimvarConfig(1).GetPrimvarName().IsEmpty()`) AND VertexCount() > 0 AND FaceCount() > 0 | MAX-GEO-004 | 2026-06-20 |
| Per-face matIds on a mesh whose bound material is non-MultiMtl | (no GeomSubsets; first matId stored as `customData.3dsmax.matId` on the Mesh prim) | bug normalization | (Mesh prim; not a shader nodedef) | `materialIdToFacesMap.size() > 1` AND `node->GetMtl()` is null or a non-MultiMtl AND the prim does not already have existing `materialBind` subsets | MAX-GEO-002 | 2026-06-20 |
| GeomSubset name for the null / non-Multi / unnamed-slot fallback path | `mat_{maxScriptId}` (single material) / `mat_{maxScriptId}_{subMtlName}` (multi w/o slot name) | cosmetic normalization | (Mesh / GeomSubset prim name; not a shader nodedef) | `MaterialUtils::CreateSubsetName` is invoked AND (the bound material is null/non-Multi OR the Multi/Sub-Object slot name is empty) | MAX-GEO-003 | 2026-06-20 |
| 3ds Max camera Near Clip / Far Clip values (every camera type, regardless of "Clip Manually") | `UsdGeomCamera.clippingRange` | bug normalization | (camera schema attribute; not a shader nodedef) | `CameraWriter::Write` is invoked AND the camera object resolves as a `GenCamera` -- the writer now authors `clippingRange` unconditionally from `GetClipDist(...)` rather than gating on `GetManualClip() != 0`, with degenerate values (≤ 0, NaN, far ≤ near) sanity-clamped to `(1.0, 1000.0)` in scene units | (this PR) | 2026-06-20 |
| 3ds Max system unit (Customize > Units Setup > System Unit Setup) | `UsdStage` `metersPerUnit` layer metadata | observability hint | (stage layer metadata; not a shader nodedef) | `USDSceneBuilder::BuildStage` is creating a new stage AND `GetSystemUnitScale(UNITS_METERS)` rounded to `digits10` is NOT exactly 1.0. The metersPerUnit value is still authored faithfully (USD-correct); a `MaxUsd::Log::Warn` is emitted naming the value, the implied 1-unit-to-meter factor, the downstream consumers that assume meters (Karma at 1:1, ARKit / Quick Look, Miris asset ingest, glTF), and both workarounds (set Max units to meters before export; or post-scale + rewrite metersPerUnit). The bound: a scene already in meters (`stageScale == 1.0`) stays silent | MAX-UNIT-001 | 2026-06-26 |
| 3ds Max Photometric / Physical light `.webFile` (when `distribution == WEB_DIST`) | `UsdLuxShapingAPI.shaping:ies:file` (an `SdfAssetPath` carrying the resolved-full-file-path of the .ies profile) | direct pairing | (light shaping API attribute; not a shader nodedef) | `PhotometricLightWriter::Write` is invoked, `distribution == WEB_DIST` AND `asset.GetId() != kInvalidId`. Two surgical bounds: (a) distribution != WEB_DIST -> NOT authored even if `.webFile` is set on source (gate fires on distribution first); (b) WEB_DIST AND asset id == kInvalidId -> NOT authored (no spurious empty path). Known TODO at `PhotometricLightWriter.cpp:277`: the path is absolute on the source machine; pack-and-go USDZ + IES bundle is not yet supported | MAX-LIT-002 | 2026-06-26 |
| 3ds Max Photometric / Physical light Kelvin + RGB filter color (Light > Color > Kelvin toggle, kelvin spinner, filter swatch, RGB swatch) | `UsdLux.enableColorTemperatureAttr` + `colorTemperatureAttr` + `inputs:color` -- dichotomous: `useKelvin = true` writes the blackbody temperature + filter-only color; `useKelvin = false` writes the lightColor x filterColor combined product with the blackbody enable off | direct pairing | (UsdLux base-light schema; not a shader nodedef) | `PhotometricLightWriter::Write` is invoked, always (the dichotomy fires per-light per-export). Surgical bounds: (a) useKelvin -> enable=true + ct=clamped-K + color=filter (NOT lightColor combined; would double-tint the renderer's blackbody integrand); (b) !useKelvin -> enable=false + NO ct authored + color=lightColor*filter (intentionally lossy on round-trip); (c) out-of-range K -> clamped to [1000, 10000] with a one-shot Log::Warn that preserves the original value in the message | MAX-LIT-002 | 2026-06-26 |
| 3ds Max export of any stage to a path with extension `.usdz` (UI Save As… "USDZ", MaxScript `exportFile foo.usdz`, `3dsmaxbatch ... -export foo.usdz`) | Single .usdz zip archive containing the root layer + every external asset (textures, sublayer references) reachable from the stage. ARKit-strict mode flattens sublayers and bundles one .usdc root | observability + audit (lock-in for proposed in-process packaging swap) | (zip archive structure; not a shader nodedef or USD prim schema) | `USDIOController::Export` (and `USDSceneController::Export`) sees `stageExportExtension == ".usdz"`. Today: routes through `MaxUsd::UsdToolsUtils::RunUsdZip` which spawns `cmd.exe -> powershell.exe -> python.exe -> usdzip` (four nested processes; PowerShell `Restricted` ExecutionPolicy / missing `HKLM:\SOFTWARE\Autodesk\3dsMax\*` registry entries / `CreateProcess + SW_HIDE` under a service account all break it silently). Proposed (MAX-PKG-001 follow-on): replace with `pxr::UsdUtilsCreateNewUsdzPackage(SdfAssetPath(tempUsd), filePath)` called in-process from the same function. The audit pins nine surgical bounds the in-process replacement preserves -- see Notes per expression for the full case list | MAX-PKG-001 | 2026-06-26 |
| 3ds Max OpenPBR material (`OpenPBR()`, Max 2025.3+) exported via the MaterialX target | `ND_open_pbr_surface_surfaceshader` shader prim (MaterialX 1.39 OpenPBR Surface v1.1) -- NOT touched by any of the five MAX-MAT-001/002/004/005/006 normalization passes. The passes are gated `doc->getNodes("standard_surface")` and skip the OpenPBR shader entirely | audit (lock-in of existing surgical-coverage bound, no C++ logic change) | `ND_standard_surface_surfaceshader` (the audit pins the surgical bound where the passes STOP) | `MtlxShaderWriter::Write` is invoked on an OpenPBR-bound material. Surgical bounds: (a) every existing MAX-MAT-* pass iterates `doc->getNodes("standard_surface")`, returning an empty list when the shader's MaterialX node category is `open_pbr_surface`; (b) name-collision-named inputs (`coat_color`, `subsurface_color`, `specular_color`, `transmission_color` -- all color3, plus `subsurface_radius` which on open_pbr_surface is type=`float` rather than `color3` as on standard_surface) survive verbatim, including artist-authored `subsurface_color = (1, 1, 1)` which would visually shift to the OpenPBR default `(0.8, 0.8, 0.8)` if the passes were widened; (c) renamed gate inputs (`coat_weight` -> standard_surface `coat`; `subsurface_weight` -> `subsurface`; `transmission_weight` -> `transmission`; `coat_ior` -> `coat_IOR`; `specular_ior` -> `specular_IOR`; `emission_luminance` -> `emission`; `specular_roughness_anisotropy` -> `specular_anisotropy`) are likewise untouched; (d) idempotence -- a second run of the pass chain on the same exported doc does nothing more than the first | MAX-MAT-007 | 2026-06-26 |
| Color / emission helper code in the MaterialX writer (`MtlxShaderWriter.cpp` post-parse normalization passes) vs the UsdPreviewSurface writer (C++ `LastResortUSDPreviewSurfaceWriter.cpp` + Python `DefaultShaderWriter` in `shaderWriter.py`) | NO shared helper, by design. The 5 MaterialX-side passes (specular_rotation, emission default, coat-block, subsurface_radius, spec-default inputs) operate on a `MaterialX::DocumentPtr` and gate on `doc->getNodes("standard_surface")`. The UsdPreviewSurface writer path operates either directly on a `Mtl*` (LastResort C++) or via the `.material_conversion` JSON tables (Python DefaultShaderWriter) -- it has no MaterialX document, no emission-strip helpers, and no shared abstraction with the MaterialX writer | audit (lock-in of existing cross-writer-path bound, no C++ logic change) | `ND_standard_surface_surfaceshader` only (the audit pins the surgical bound between the MaterialX writer's normalizer chain and the UsdPreviewSurface writer path) | `MtlxShaderWriter::Write` runs the chain of normalizers. Surgical bounds: (a) the chain operates on the in-memory MaterialX doc returned by `MtlxIOUtil.ExportMtlxString` and writes nothing to UsdShade until AFTER the chain completes; (b) the UsdPreviewSurface writer entry points (`LastResortUSDPreviewSurfaceWriter::Write` and the Python `DefaultShaderWriter.Write`) do not invoke, import, or share state with the MaterialX normalizers; (c) input vocabularies diverge on every concept the planner's "color-emission helper" could cover -- emissiveColor (UsdPS, color3f default (0,0,0)) vs emission + emission_color (MaterialX, scalar default 0.0 gating color3 default (1,1,1)); diffuseColor (UsdPS, color3f default (0.18,0.18,0.18)) vs base + base_color (MaterialX, scalar default 0.8 gating color3 default (0.8,0.8,0.8)); specularColor (UsdPS) + useSpecularWorkflow toggle vs specular + specular_color (MaterialX); plus topology-collisions (UsdPS opacity is float, MaterialX opacity is color3) and UsdPS-only inputs with no MaterialX analogue (useSpecularWorkflow, direct normal3f normal input); (d) idempotence -- re-running the 5 normalizers does not change behaviour on either side | MAX-PRIM-001 | 2026-06-26 |
| MultiMtl-bound mesh whose faces carry multiple matIds (`materialIdToFacesMap.size() > 1`) | `UsdGeomMesh.primvars:displayColor` -- the MAX-MAT-003 displayColor block writes a SINGLE-element constant primvar derived from `boundMtl->GetDiffuse()` (which on a MultiMtl returns sub-material 0's diffuse via the default `mtlNum = 0`). The block does NOT consult `materialIdToFacesMap` and does NOT broadcast `boundMtl->GetDiffuse(matId)` per face | audit (lock-in of existing MAX-MAT-003 surgical-coverage bound, no C++ logic change) | (mesh primvar; not a shader nodedef -- the audit pins the surgical bound where the MAX-MAT-003 gate STOPS for MultiMtl-bound meshes) | `MeshConverter::ConvertToUSDMesh` is invoked on a mesh whose `node->GetMtl()` is a MultiMtl AND `materialIdToFacesMap.size() > 1`. Surgical bounds: (a) the displayColor block's `boundMtl->GetDiffuse()` call defaults `mtlNum = 0` and returns sub-mtl 0's diffuse, regardless of how many matIds the mesh carries; (b) the resulting `primvars:displayColor` is a SINGLE-element array with `interpolation = constant` -- the C++ block does not consume `materialIdToFacesMap` even though `ApplyMaxMaterialIDs` (called immediately before the block) does use it to author per-face GeomSubsets in the `materialBind` family (MAX-GEO-002); (c) a wildcard "extend MAX-MAT-003 to per-face for MultiMtl bindings" widening would consume the same `materialIdToFacesMap` to broadcast `boundMtl->GetDiffuse(matId)` per face, authoring `interpolation = uniform` and `len = materialIdToFacesMap.size()` -- the value at `displayColor[0]` would still be sub-mtl 0's diffuse, so every existing displayColor[0] / branch-label / IsAuthored assertion would pass; only the array-length and interpolation invariants catch this widening; (d) the IsAuthored() short-circuit and the wire-color fallback branches are unchanged on the MultiMtl path -- the bound applies only to the `mtl-diffuse` branch when the bound material is a MultiMtl | MAX-MAT-008 | 2026-06-26 |
| 3ds Max legacy light classes that inherit `LightObject` but NOT `LightscapeLight` (`Omnilight` = `OMNI_LIGHT_CLASS_ID`; `Skylight` = `SKYLIGHT_CLASS_ID`; legacy `Target_Spot` / `Free_Spot` = `SPOT_LIGHT_CLASS_ID`; legacy `Target_Direct` / `Free_Direct` = `DIR_LIGHT_CLASS_ID`; mr_Sky, Daylight environment lights, etc.) | NO `UsdLux*` prim authored -- the writer-registry's only general-purpose light writer (`PhotometricLightWriter`) gates on `IsSubClassOf(LIGHTSCAPE_LIGHT_CLASS)`, returns `ContextSupport::Unsupported` for every non-LightscapeLight, and `MaxUsdPrimWriterRegistry::FindWriter` returns nullptr (no other registered writer claims a `LightObject`). The light is silently dropped from the exported stage: no prim, no error, no warning | audit (lock-in of existing writer-registry surgical-coverage bound, no C++ logic change) | (UsdLux schema; not a shader nodedef -- the audit pins the writer-registry's bottom bound for lights) | `PhotometricLightWriter::CanExport` is invoked AND `!exportArgs.GetTranslateLights() || !object->IsSubClassOf(LIGHTSCAPE_LIGHT_CLASS)`. Surgical bounds: (a) the LIGHTSCAPE_LIGHT_CLASS branch returns `ContextSupport::Fallback` so in-scope photometric / physical lights are authored as the correct `UsdLuxDiskLight` / `UsdLuxRectLight` / `UsdLuxSphereLight` / `UsdLuxCylinderLight` per `GetPrimType` -- the positive control proves this still fires; (b) every non-LightscapeLight LightObject (legacy Omnilight, Skylight, Spot, Direct, etc.) returns Unsupported AND no other registered writer claims the LightObject category -- silent drop. A wildcard widening that returned `Fallback` for every LightObject and stamped out default UsdLuxSphereLight / UsdLuxDomeLight without per-class attribute translation would author spurious extra lights at every legacy light position (e.g. an Omnilight that was turned OFF / set to multiplier 0 / set to a non-default attenuation/color in Max would silently appear as a unit-intensity SphereLight in USD); (c) the `exportArgs.GetTranslateLights()` short-circuit fires BEFORE the class-id gate -- a user who disables light export sees the same nullptr for every light type, including in-scope photometrics; (d) `IsSubClassOf(LIGHTSCAPE_LIGHT_CLASS)` is safely callable on non-light Objects (returns false) so the gate is well-behaved across all node types | MAX-LIT-001 | 2026-06-26 |
| Color-space metadata across all three USD-shading writer paths: `MtlxShaderWriter::_SetInputValue` (color3/color4 inputs + filename-on-image-color-output inputs); `set_bitmap_scale_bias_sourcecolorspace` in `usd_material_writer.py` (every UsdUVTexture); `LastResortUSDPreviewSurfaceWriter::Write` (Color3f diffuseColor value, no UsdUVTexture) | Three divergent conventions, one per writer: MaterialX path authors `colorSpace` USD metadata on the UsdShade input attribute IFF `_TypeSupportsColorSpace(input)` is true AND `getActiveColorSpace()` is non-empty; UsdPreviewSurface Python writer authors `sourceColorSpace = "raw"` as a TOKEN INPUT on every baked UsdUVTexture (hardcoded with TODO retirement condition); LastResort C++ writer authors NEITHER colorSpace nor sourceColorSpace -- `diffuseColor` is linear by UsdPreviewSurface spec when authored as a Color3f value | audit (lock-in of existing cross-writer color-space surgical-coverage bound, no C++ logic change) | (multiple: `colorSpace` USD metadata on UsdShade input attrs for MaterialX shaders; `sourceColorSpace` TOKEN INPUT on UsdUVTexture for baked UsdPreviewSurface paths; no colorSpace anywhere on the LastResort value-only path) | `_SetInputValue` is invoked AND `_TypeSupportsColorSpace(input)` is true AND `getActiveColorSpace()` is non-empty -- the MaterialX path propagates; `set_bitmap_scale_bias_sourcecolorspace` is invoked on a baked UsdUVTexture -- always hardcoded "raw" pending TODO retirement; `LastResortUSDPreviewSurfaceWriter::Write` is invoked -- value-only, no UsdUVTexture, no colorSpace by UsdPreviewSurface spec. Surgical bounds: (a) `_TypeSupportsColorSpace` accepts ONLY color3/color4 typed inputs OR filename inputs on image nodes with color3/color4 output -- every other input type (float, vector3, matrix, etc.) MUST NEVER receive colorSpace metadata even when the MaterialX active color space resolves to a non-empty name; (b) `if (!colorSpace.empty())` short-circuit -- empty active color space MUST NOT cause `SetColorSpace(TfToken(""))` because that creates authored-empty metadata distinct from no-metadata-at-all; (c) hardcoded `sourceColorSpace = "raw"` is the acknowledged-incomplete UsdPreviewSurface bound -- retirement requires a future MAX-MAT-* that consults `from_tex.bitmap.gamma` and adds an sRGB-gamma test case to `export_texture_test.py`; (d) LastResort writer authors NO UsdUVTexture child AND NO `colorSpace` metadata on `diffuseColor` -- per UsdPreviewSurface spec, the value is linear by definition | MAX-MAT-009 | 2026-06-26 |
| Two or more 3ds Max nodes that share one Object (USD-instance pair) but each Node has a different `.material` assignment (per-instance material override). Special case: at least one of the divergent materials is a MultiMtl whose Object carries per-face matIds (`ApplyMaxMaterialIDs` has authored `materialBind`-family GeomSubsets on the prototype child) | THREE override-structure carriers, one per surgical branch: (a) USD instancing PRESERVED (`IsInstanceable() == true`) with per-instance MaterialBindingAPI authored on the local instance prim spec (non-MultiMtl divergence, or MultiMtl-without-subsets divergence -- Branches B and C of the four-branch decision tree); (b) USD instancing BROKEN (`SetInstanceable(false)`) + the prototype's `materialBind`-family GeomSubsets COPIED to an override-child Mesh prim under the divergent instance with `customData[3dsmax:matId]` preserved across the copy + each copied subset bound to the divergent MultiMtl's sub-material (Branch D, the per-instance MultiMtl override case that requires breaking instancing because USD subset bindings on the prototype apply to every instance); (c) MtlSwitcher container materials authored as `UsdShade.Material` with `shadingVariant` UsdVariantSet of N internal-references (variant-sets export style, Branch E) OR single `AddInternalReference` to the active material with no variant set (active-only / 1-variant-fallback, Branch F) OR bare `UsdShade.Material` prim with no references and no variant set (empty switcher, Branch G) | audit (lock-in of existing four-Branches-A/B/C/D + three-Branches-E/F/G override-structure surgical-coverage bound, no C++ logic change) | (multiple non-shader USD constructs: `pxr::Usd.Prim.SetInstanceable`; `pxr::UsdGeom.Subset` + `customData[3dsmax:matId]`; `pxr::UsdShade.MaterialBindingAPI`; `pxr::Usd.VariantSet` (`shadingVariant`); `pxr::Usd.Prim.GetReferences().AddInternalReference`) | `_AddInstancePrimsToMaterialMap` is invoked AND `prototype.GetInstances()` returns >=2 instances. Surgical bounds: (a) sameMaterialForAllInstances == true -> KEEP instancing, bind on the inheritance-base prim's child (Branch A); (b) different non-MultiMtl materials -> KEEP instancing, per-instance MaterialBindingAPI on the instance prim's local spec (Branch B); (c) different MultiMtl, NO subsets on the prototype child -> KEEP instancing, per-instance MaterialBindingAPI via `customData[3dsmax:matId]` lookup (Branch C); (d) different MultiMtl, subsets DO exist on the prototype child -> `BreakInstancingAndCopySubset` runs: `SetInstanceable(false)` + subset copy with matId customData preserved + rebind copied subsets to divergent MultiMtl's sub-mtls (Branch D, the data-loss vector the audit primarily pins). Plus three switcher-shape bounds: (e) AsVariantSets >=2 variants -> `shadingVariant` UsdVariantSet authored with one internal-reference per variant (Branch E); (f) ActiveMaterialOnly OR 1-variant AsVariantSets fallback -> NO variant set, single `AddInternalReference` to active material (Branch F); (g) empty switcher -> warn + early-return, bare Material prim, NO variant set, NO references (Branch G) | MAX-MAT-010 | 2026-06-26 |

## Notes per expression

### PhysicalMaterial.anisotropy_angle → standard_surface.specular_rotation  (MAX-MAT-001)

**Symptom.** The 3ds Max-shipped `MtlxIOUtil.ExportMtlxString` MaxScript
bridge always emits `<input name="specular_rotation" type="float"
value="0.25" />` on every `ND_standard_surface_surfaceshader`, regardless of
whether the source PhysicalMaterial has any anisotropy authored. Six out of
six materials in the diagnostic corpus exhibit this. The MaterialX
`standard_surface` nodedef defaults `specular_rotation` to **0.0**.

**Why it matters.** `specular_rotation` is multiplied by
`specular_anisotropy` inside the standard_surface BSDF, so the buggy value
has no observable lighting effect in the (universal) `anisotropy == 0`
case. It *does* matter for any downstream layer that turns anisotropy on,
or for tools that read MaterialX values directly for content authoring or
roundtripping. The wrong value silently introduces a 0.25-turns (90°)
highlight rotation the source DCC never asked for.

**Fix.** Add a post-parse normalization pass in `MtlxShaderWriter::Write()`
that walks the parsed `MaterialX::Document`, looks for `standard_surface`
nodes carrying `specular_rotation = 0.25` AND no anisotropy (absent or
static 0), and removes the `specular_rotation` input. After normalization
the input falls back to the nodedef default (`0.0`).

**Bounds (where the fix conservatively does nothing):**

* `specular_rotation` is connected to a node or nodegraph — could carry an
  intentional procedural rotation; keep it.
* `specular_rotation` has any static value other than `0.25` — the user
  authored it explicitly; keep it.
* `specular_anisotropy` is connected — runtime value unknown; assume the
  rotation may be intentional and keep it.
* `specular_anisotropy` is statically non-zero — anisotropy is on, rotation
  matters; keep it.

**Validator.**
`/Users/d.smith/MirisProjects/Agent Builder/agent/arch-builds/191c9984-d6c5-468c-a0ad-a95092dbe6d3/normalize_specular_rotation.py`
mirrors the C++ logic at the USD layer (the C++ runs at the MaterialX-doc
layer earlier in the pipeline). Running it on the captured
`complex_export.usda` strips exactly the six known-bogus values and leaves
every other attribute identical. Karma renders of the prefix and postfix
USDs are byte-identical (same SHA-256), confirming the fix is a visual
no-op for the common case — exactly what the BSDF math predicts.

**Surgical-preservation validator (added 2026-06-26).**
`/Users/d.smith/MirisProjects/Agent Builder/agent/arch-builds/81aea885-6ee9-4828-b05f-d1b237263d10/validate_specular_rotation_surgical.py`
extends the original validator with explicit **negative** cases —
shaders that LOOK similar to the leak but must NOT be stripped because
the rotation IS observable (anisotropy on) or the bridge value is not
the leak. 8 cases enumerate every line of the C++ gate:

| Material in synthetic fixture | spec_rotation   | spec_anisotropy | strip? | bound exercised |
| --- | --- | --- | --- | --- |
| `LeakDefault`         | `0.25`          | (absent)        | yes  | (the bug, must strip) |
| `LeakWithZeroAniso`   | `0.25`          | `0.0`           | yes  | aniso present-and-zero, still leak |
| `IntentionalRotation` | `0.5`           | `0.0`           | no   | rotation value != 0.25 |
| `BrushedSteel`        | `0.25`          | `0.85`          | no   | aniso non-zero -- rotation OBSERVABLE (**KEY GAP**) |
| `ConnectedRotation`   | `0.25` + conn   | `0.0`           | no   | rotation connected (procedural) |
| `ConnectedAniso`      | `0.25`          | `0.0` + conn    | no   | aniso connected -- runtime unknown |
| `AlmostLeak`          | `0.125`         | (absent)        | no   | rotation static != 0.25 |
| `NonStandardSurface`  | `0.25`          | `0.0`           | no   | id != standard_surface |

Plus an idempotence check (a second pass over the post-normalized
fixture strips zero inputs). All 8 cases + idempotence pass on the
2026-06-26 baseline, locking in the surgical bound. A future regression
that widened the strip gate — most likely a refactor that weakened the
`_anisotropy_provably_zero` check, since it is the only "is the
rotation observable?" predicate — would fail by named case rather than
just "some attribute disappeared somewhere."

The previously-uncovered surgical bound is `BrushedSteel`: a shader
where `specular_rotation = 0.25` byte-for-byte matches the leak, but
`specular_anisotropy = 0.85` means the rotation IS observable in the
BSDF (a 90-degree turn of the anisotropic streak). The original
`a377982` commit message specifically promised this negative test
("Synthetic negative test (BrushedSteel with `specular_anisotropy=0.4`)
confirms the fix is surgical and preserves intentional rotation"), but
that synthetic test only existed during the original fix's authoring
— it was never landed in the MaxScript suite. This case now exists
in both the Python validator and the new MaxScript regression.

**MaxScript regression (added 2026-06-26).**
`src/Tests/Integration/mtlxShaderWriter_test.ms` now carries
`test_export_material_preserves_intentional_specular_rotation`. It
loads
`src/Tests/Integration/data/intentional_specular_rotation_test/intentional_specular_rotation.mtlx`
(a synthetic `standard_surface` with `specular_rotation = 0.25`,
`specular_anisotropy = 0.85`, `specular_roughness = 0.18` — a
brushed-steel-like material) via `MaterialXMaterial.importMaterial`,
exports through `USDExporter`, and asserts the standard_surface's
`specular_rotation` (0.25) and `specular_anisotropy` (0.85) attributes
are present, authored, and carry the fixture's authored values
verbatim. A failure means either the normalizer over-strips
(C++ regression — the anisotropy-zero check has been weakened) or
the MaxScript bridge layer drops user-authored rotation on
`MaterialXMaterial` round-trip (a separate, deeper bug the test
surfaces either way).

**Visual demonstration of the surgical bound.** A single-sphere
fixture carrying the brushed-steel shader is rendered twice in Karma:
`render_karma_postfix.png` (the current fix — rotation preserved;
the anisotropic highlight reads as **radial streaks emanating from
the highlight centre**, the characteristic 90-degree-rotated streak
silhouette) and `render_unreal_reference.png` (the "still-broken"
reference where rotation has been over-stripped — the same
anisotropic highlight reads as a **compact star at the highlight
centre**, the un-rotated streak orientation). The two PNGs differ by
SHA-256 and visibly so. The auditor's checklist: **the current fix's
render shows radial streaks; if it ever becomes a compact star, the
normalizer has started over-stripping intentional rotation on
anisotropic shaders.** Composite at `compare_side_by_side.png` in the
same arch-build dir.

**Retirement condition.** If/when Autodesk fixes `MtlxIOUtil` in a future
3ds Max release so that the bridge stops emitting the spurious 0.25, the
normalization pass becomes inert (no node matches the trigger condition).
The pass can stay in place as a belt-and-suspenders guard for older Max
installs.

### PhysicalMaterial.emission (none authored) → standard_surface.emission + .emission_color  (MAX-MAT-002)

**Symptom.** The 3ds Max-shipped `MtlxIOUtil.ExportMtlxString` MaxScript
bridge always emits the pair
```
<input name="emission"       type="float"  value="1.0" />
<input name="emission_color" type="color3" value="0, 0, 0" />
```
on every `ND_standard_surface_surfaceshader`, regardless of whether the
source PhysicalMaterial authored any emission. Six out of six materials in
the diagnostic corpus exhibit this. The MaterialX `standard_surface`
nodedef defaults are `emission = 0.0` and `emission_color = (1, 1, 1)`.

**Why it matters.** The buggy pair multiplies to `1.0 * (0,0,0) = (0,0,0)`,
so the BSDF emits no light and the bug is invisible at render time. It
*does* matter for any downstream tool that reads the exported MaterialX
and constructs further overrides:

* A layer that bumps `emission_color` to a non-black value (e.g. to author
  a glowing variant of an existing material) would unexpectedly turn on
  full-strength emission, because the inherited `emission = 1.0` is still
  in force.
* Round-trip importers that read `emission_color = (0, 0, 0)` may treat
  the surface as having explicit black emission rather than “no emission
  authored” — semantically different states that diverge under
  later edits.
* The values do not match the source DCC: the PhysicalMaterial has no
  emission knob set to 1.0, and certainly didn't ask for an emission
  *color* of pure black. The exported document misrepresents what the
  artist authored.

**Fix.** Add a second post-parse normalization pass in
`MtlxShaderWriter::Write()` (run immediately after MAX-MAT-001's pass)
that walks the parsed `MaterialX::Document` and, for each
`standard_surface` node, removes both the `emission` and `emission_color`
inputs when *all* of:

* `emission` is statically `1.0` (no connection, tolerant of float noise);
* `emission_color` is statically `(0, 0, 0)` (no connection, tolerant of
  float noise); and
* both inputs are present.

After normalization both inputs fall back to nodedef defaults
(`0.0` and `(1, 1, 1)`), which evaluate to the *same* zero emission with
the correct meaning.

**Bounds (where the fix conservatively does nothing):**

* Either `emission` or `emission_color` is connected to a node or
  nodegraph — could carry intentional procedural emission; keep both.
* `emission` is static but not `1.0` — user authored it explicitly; keep.
* `emission_color` is static but not exactly `(0, 0, 0)` — user authored
  an explicit emission tint; keep.
* Only one of the two inputs is present — the bug pattern is the pair, so
  treating either half in isolation could destroy a real authored value.

**Validator.**
`/Users/d.smith/MirisProjects/Agent Builder/agent/arch-builds/f72c97e6-301f-400a-9e77-c768979e8fa5/normalize_emission_default.py`
mirrors the C++ logic at the USD layer (the C++ runs at the MaterialX-doc
layer earlier in the pipeline). Running it on the MAX-MAT-001-clean
`complex_export_postfix.usda` strips exactly six pairs of
`emission`/`emission_color` inputs and leaves every other attribute
identical. Karma renders pre- and post-strip are byte-identical (same
SHA-256), confirming the fix is a visual no-op — exactly what the BSDF
math predicts since `1 * black == 0 * white == 0`.

**Surgical-preservation validator (added 2026-06-23).**
`/Users/d.smith/MirisProjects/Agent Builder/agent/arch-builds/375b2235-daa3-442c-b8ad-b3a0ac67228a/validate_emission_normalize_surgical.py`
extends the original validator with explicit **negative** cases —
prims that LOOK similar to the leak but must NOT be stripped. 8 cases
enumerate every line of the C++ gate plus the connection / shader-id
filters:

| Material in synthetic fixture | emission | emission_color    | strip? | bound exercised |
| --- | --- | --- | --- | --- |
| `LeakPair`                  | `1.0`        | `(0, 0, 0)`         | yes  | (the bug, must strip) |
| `IntentionalColor`          | `1.0`        | `(0.8, 0.4, 0.1)`   | no   | color != `(0,0,0)` |
| `IntentionalScalar`         | `0.5`        | `(0, 0, 0)`         | no   | scalar != `1.0` |
| `NeitherDefault`            | `0.7`        | `(0.2, 0.5, 0.9)`   | no   | both off-leak |
| `LonelyEmission`            | `1.0`        | (absent)            | no   | pair must coexist |
| `LonelyColor`               | (absent)     | `(0, 0, 0)`         | no   | pair must coexist |
| `ConnectedColor`            | `1.0`        | `(0,0,0)` + connect | no   | connected input |
| `NonStandardSurface`        | `1.0`        | `(0, 0, 0)`         | no   | id != standard_surface |

Plus an idempotence check (a second pass over the post-normalized
fixture strips zero inputs). All 8 cases + idempotence pass on the
2026-06-23 baseline, locking in the surgical bound. A future regression
that widened the strip gate would fail by named case rather than just
"some attribute disappeared somewhere."

**MaxScript regression (added 2026-06-23).**
`src/Tests/Integration/mtlxShaderWriter_test.ms` now carries
`test_export_material_preserves_intentional_emission`. It loads
`src/Tests/Integration/data/intentional_emission_test/intentional_emission.mtlx`
(a synthetic `standard_surface` with `emission = 0.5`, `emission_color =
(1.0, 0.3, 0.0)`) via `MaterialXMaterial.importMaterial`, exports
through `USDExporter`, and asserts the standard_surface's `emission` and
`emission_color` attributes are present, authored, and carry the
fixture's authored values verbatim. The existing
`test_export_physical_material_strips_buggy_emission_default` is also
strengthened with a pair-level post-condition that the buggy
(`1.0`, `(0, 0, 0)`) pair cannot coexist on the same shader, catching
the half-leak case where a future refactor might preserve one half of
the buggy pair while correctly stripping the other.

**Visual demonstration of the surgical bound.** A single-sphere fixture
carrying the intentional-emission shader is rendered twice in Karma:
`render_karma_postfix.png` (the current fix — warm orange, emission
preserved) and `render_unreal_reference.png` (the "still-broken"
reference where emission has been over-stripped — neutral grey).
The two PNGs differ by SHA-256 and visibly so. The auditor's
checklist: **the current fix's render is warm orange; if it ever
becomes neutral grey, the normalizer has started over-stripping
intentional artist emission.** Composite at
`compare_side_by_side.png` in the same arch-build dir.

**Tolerance-band validator (second reinforcement, added 2026-06-26).**
`/Users/d.smith/MirisProjects/Agent Builder/agent/arch-builds/b2c78da8-302e-4f3e-b935-2aec013b022e/validate_emission_normalize_tolerance_bounds.py`
extends the surgical-bounds coverage with the **numeric edges** of the
strip gate that the first reinforcement's loud, high-magnitude cases
do not pin. The first reinforcement uses `IntentionalScalar = 0.5`
(far outside the ε band) and `IntentionalColor = (0.8, 0.4, 0.1)`
(magnitude ~0.9): both would still be preserved by a refactor that
widened the scalar tolerance to 0.01 or replaced the per-component
color check with a vector-magnitude check. This second-layer validator
adds 5 cases + idempotence pinned at the band edges:

| Material in synthetic fixture | emission     | emission_color           | strip? | bound exercised |
| --- | --- | --- | --- | --- |
| `LeakPairExact`              | `1.0`        | `(0, 0, 0)`              | yes  | (the bug, must strip — repeated as the in-band baseline) |
| `SubEpsilonScalar`           | `1.0 + 1e-7` | `(0, 0, 0)`              | yes  | scalar within ε — tolerance must absorb float noise |
| `NearLeakScalar`             | `1.0 + 1e-3` | `(0, 0, 0)`              | no   | scalar just outside ε — refactor that widens to 1e-2 would over-strip |
| `SubEpsilonColor`            | `1.0`        | `(1e-7, 1e-7, 1e-7)`     | yes  | all 3 channels within ε — tolerance must absorb float noise |
| `SingleChannelDarkColor`     | `1.0`        | `(0.05, 0, 0)`           | no   | per-component edge — refactor to magnitude check would over-strip |
| `ConnectedEmission`          | `1.0 (conn)` | `(0, 0, 0)`              | no   | symmetric companion to first-reinforcement's ConnectedColor |

All 6 cases + idempotence pass on the 2026-06-26 baseline, locking in
the gate's exact ε = 1e-6 scalar tolerance and per-component (not
magnitude) color discrimination.

**MaxScript regression (second reinforcement, added 2026-06-26).**
`src/Tests/Integration/mtlxShaderWriter_test.ms` now also carries
`test_export_material_preserves_single_channel_dark_emission`. It
loads
`src/Tests/Integration/data/single_channel_dark_emission_test/single_channel_dark_emission.mtlx`
(a synthetic `standard_surface` with `emission = 1.0` matching the
leak scalar EXACTLY and `emission_color = (0.05, 0, 0)` -- one channel
barely above the per-component epsilon) via
`MaterialXMaterial.importMaterial`, exports through `USDExporter`, and
asserts both inputs survive verbatim. This is the in-3ds-Max
counterpart for the `SingleChannelDarkColor` Python case -- the only
one of the five new bounds that is both visually observable (faint
dark-red glow) and cleanly expressible as a static `.mtlx` fixture.
The existing `test_export_material_preserves_intentional_emission`
remains the loud, high-magnitude companion that exercises the same
gate at a wide margin.

**Visual demonstration of the tolerance bound (second reinforcement).**
A single-sphere fixture carrying the
single-channel-dark-emission shader is rendered twice in Karma in
`/Users/d.smith/MirisProjects/Agent Builder/agent/arch-builds/b2c78da8-302e-4f3e-b935-2aec013b022e/`:
`render_karma_postfix.png` (the current fix preserves the pair — the
sphere carries a faint warm/pink cast from the dark-red emission) and
`render_unreal_reference.png` (the magnitude-refactor over-strip
scenario — neutral grey, no warm cast). The two PNGs differ by SHA-256.
The auditor's checklist: **the current fix's render has a visible warm
(pinkish-red) wash across the lit hemisphere; if it ever becomes
clean neutral grey, the per-component color check has been replaced
with a magnitude check and dark-channel artist emission is being
silently stripped.** Composite at `compare_side_by_side.png` in the
same arch-build dir.

**Retirement condition.** Same as MAX-MAT-001: when Autodesk fixes
`MtlxIOUtil` to stop emitting the spurious emission pair, the pass
becomes inert. Safe to keep as a guard for older 3ds Max installs.

### PhysicalMaterial.coat (none authored) → standard_surface.coat_* preset block  (MAX-MAT-004)

**Symptom.** The 3ds Max-shipped `MtlxIOUtil.ExportMtlxString` MaxScript
bridge always emits the same four coat-block inputs on every
`ND_standard_surface_surfaceshader`, regardless of whether the source
PhysicalMaterial has any coating enabled:

```
<input name="coat_IOR"              type="float"  value="1.52" />
<input name="coat_affect_color"     type="float"  value="0.5"  />
<input name="coat_affect_roughness" type="float"  value="0.5"  />
<input name="coat_roughness"        type="float"  value="0.0"  />
```

Six out of six materials in the diagnostic corpus
(`/Users/d.smith/MirisProjects/Agent Builder/agent/pipeline-runs/3e596cac-1d4f-45fe-82eb-afa09679eae9/complex_export.usda`)
exhibit this — every distinct material carries the same four leak
values byte-for-byte (`distinct_values_across_6_materials == 1` per
input in `findings_evidence.json`). The MaterialX `standard_surface`
nodedef defaults are different on each input:

| Input | Bridge leak | MaterialX nodedef default | Why the leak is "wrong" |
| --- | --- | --- | --- |
| `coat_IOR` | 1.52 | **1.5** | Hard-coded to a glass-coating IOR rather than the spec default. |
| `coat_affect_color` | 0.5 | **0.0** | Bridge tints base by the coat color even when nothing is authored. |
| `coat_affect_roughness` | 0.5 | **0.0** | Bridge couples coat roughness into specular roughness by default. |
| `coat_roughness` | 0.0 | **0.1** | Bridge emits a perfectly-smooth coat where the spec assumes a 10% roughness baseline. |

**Why it matters.** All four values are visually inert on the current
corpus because the bridge correctly authors `coat = 0.0`, and the
standard_surface BSDF multiplies the coat lobe contribution by `coat`,
zeroing the lobe out entirely. The bug surfaces the moment any
downstream context flips `coat > 0`:

* A compositing layer that targets the coat scalar (e.g. to author a
  glossy variant of a base material) inherits Max's opinionated coat
  profile — `IOR = 1.52` rather than the cleaner `1.5` baseline, full
  base-color tinting via `coat_affect_color = 0.5`, full
  specular-roughness coupling via `coat_affect_roughness = 0.5`, and a
  mirror-smooth coat from `coat_roughness = 0.0` instead of the spec's
  10% roughness floor.
* A USD variant set or shader override that swaps `coat` to a non-zero
  value picks up the same opinionated profile silently — the artist
  who authored the override has no way to know the rest of the
  coat block was preset by the bridge rather than the source DCC.
* Round-trip importers that read the four inputs may treat them as
  authored intent ("the artist chose IOR 1.52 specifically") rather
  than bridge-emitted defaults that should fall through to nodedef
  defaults.
* The values do not match the source DCC: the PhysicalMaterial has no
  coat knobs touched. The exported document misrepresents what the
  artist authored.

**Fix.** Add a third post-parse normalization pass in
`MtlxShaderWriter::Write()` (run immediately after MAX-MAT-002's pass)
that walks the parsed `MaterialX::Document` and, for each
`standard_surface` node, removes each of the four coat-block inputs
**independently** when *all* of:

* The node's `coat` input is provably zero (absent, or present and
  statically `0.0`, tolerant of float noise). A connected `coat` could
  carry a runtime non-zero value, so the whole block is preserved in
  that case. A statically non-zero `coat` means the lobe is active and
  the four inputs are observable — preserve them as authored.
* The leak input is not connected (static value only).
* The leak input's static value matches the bridge's hard-coded leak
  value exactly (tolerant of float noise).

Per-input independence is the key design difference vs MAT-002, which
strips emission/emission_color as a *pair*: in MAT-002 the emission
pair multiplies to zero so treating either half in isolation could
destroy a real authored value. The four coat inputs in MAT-004 are not
mutually dependent — they multiply / interpolate into the BSDF
separately — so each can be tested and stripped on its own merits. A
user who explicitly overrides one of them (say `coat_IOR = 1.45`)
keeps that override while the other three (still at their leak
values) are stripped.

After normalization each stripped input falls back to its nodedef
default, which is the correct "no coat was authored" state.

**Bounds (where the fix conservatively does nothing):**

* `coat` is connected to a node or nodegraph — runtime value unknown;
  assume the coat lobe may be intentional and keep the entire block.
* `coat` is statically non-zero — the lobe is active and the inputs
  are observable; keep the entire block as authored.
* A leak input is connected — could carry intentional procedural
  values; keep it regardless of `coat`.
* A leak input has a static value other than its known leak value —
  user authored it explicitly; keep it.
* The node is missing one of the four leak inputs entirely — already
  at the nodedef default; nothing to do.

**Validator.**
`/Users/d.smith/MirisProjects/Agent Builder/agent/arch-builds/79e8fa61-668b-430f-9fde-70102c2abce6/normalize_coat_block_defaults.py`
mirrors the C++ logic at the USD layer (the C++ runs at the
MaterialX-doc layer earlier in the pipeline). Running it on the
captured `complex_export.usda` strips exactly 24 leak inputs
(6 materials × 4 inputs), with zero collateral changes and idempotent
on a second pass:

```
== normalize_coat_block_defaults pass 1 ==
  coat_IOR: 6
  coat_affect_color: 6
  coat_affect_roughness: 6
  coat_roughness: 6
  materials_touched: 6
== collateral diff ==
  total removed: 24
  total added:   0
== pass 2 (idempotence) ==
  coat_IOR: 0
  coat_affect_color: 0
  coat_affect_roughness: 0
  coat_roughness: 0
```

The diff is purely structural at the USD-layer level — exactly the
four `inputs:coat_*` attributes the bridge over-authored, on exactly
the six materials that had them, with zero side effects on every
other attribute on every other prim.

**Karma renders.**
`render_karma_prefix.png` (the captured corpus with the leak) and
`render_karma_postfix.png` (the validator-applied corpus) are *not*
SHA-256-identical because this Karma CPU sampling preset is
non-deterministic across runs (re-rendering the same stage twice
produces max-pixel delta = 20/255, mean delta = 0.0166/255, ~4.3% of
pixels non-zero — the sampler noise floor at the configured
spp/threading). What matters is whether the prefix-vs-postfix delta
exceeds the noise floor; it does not. Measured on the gated corpus:

```
prefix vs prefix-repeat (Karma noise floor):  max 20  mean 0.0166  nonzero 4.33%
prefix vs postfix      (fix delta, coat=0):   max 18  mean 0.0162  nonzero 4.29%
```

The fix delta is *indistinguishable* from the sampler-noise floor —
exactly what the BSDF math predicts since `coat == 0` zeros the lobe
whether the four inputs hold leak values or fall through to nodedef
defaults. The observable that proves the fix is therefore
**structural** (the USDA-layer diff above), not visual: a frame-by-
frame comparison cannot distinguish a leaked-but-gated coat block
from a clean coat block, by design of the BSDF.

A coat-stress overlay (`coat = 1.0` authored on every material in a
fresh layer above the corpus reference) was also rendered — both
prefix and postfix versions remain within the same noise floor under
this lighting and camera setup. Reason: in the corpus `coat_color`
is `(1, 1, 1)` (a separate redundant-default leak documented as
MAX-MAT-006 territory, not stripped by this fix), which makes the
`coat_affect_color` term reduce to a no-op; the small `coat_IOR`
delta (1.52 vs 1.5) and the `coat_roughness` delta (0.0 vs 0.1) are
insufficient to clear the noise floor without sharp specular content
in the lighting. The latent risk remains real — any downstream
context that combines `coat > 0` *with* an opinionated `coat_color`
override would diverge measurably between prefix and postfix.

**Retirement condition.** Same as MAX-MAT-001/002: when Autodesk
fixes `MtlxIOUtil` to stop emitting the spurious coat-block preset,
the pass becomes inert (no node matches the trigger condition). Safe
to keep as a guard for older 3ds Max installs.

### PhysicalMaterial.subsurface (none authored) → standard_surface.subsurface_radius  (MAX-MAT-005)

**Symptom.** The 3ds Max-shipped `MtlxIOUtil.ExportMtlxString` MaxScript
bridge always emits

```
<input name="subsurface_radius" type="color3"
       value="0.794704, 0.531734, 0.292854" />
```

on every `ND_standard_surface_surfaceshader`, regardless of whether the
source PhysicalMaterial has any subsurface scattering enabled. Six out
of six materials in the diagnostic corpus exhibit this — every distinct
material carries the same triple byte-for-byte
(`distinct_values_across_6_materials == 1` in
`findings_evidence.json`). The MaterialX `standard_surface` nodedef
default for `subsurface_radius` is **`(1, 1, 1)`** — neutral white.

The triple `(0.794704, 0.531734, 0.292854)` is the canonical
Pixar / Christophe-Hery **caucasian-skin SSS radius**: RGB attenuation
distances tuned for human skin in PRMan/RIS production paths. The
bridge stamps it on every material's `subsurface_radius` input, which
the MtlxIOUtil source presumably uses as an internal "reasonable
default if the user enables SSS" — but the value is opinionated and
domain-specific, not a neutral baseline.

**Why it matters.** The leak is visually inert on the current corpus
because the bridge also (correctly) authors `subsurface = 0.0`, and
the standard_surface BSDF multiplies the subsurface lobe contribution
by `subsurface`, zeroing the lobe out entirely. The bug surfaces the
moment any downstream context flips `subsurface > 0`:

* A USD variant set or shader override that enables SSS on any of
  the six materials (e.g. to author a "translucent" variant of a
  ceramic, a wax variant of a metal, a candle variant of a plastic)
  silently inherits Max's opinionated skin-tone scattering rather
  than the neutral `(1, 1, 1)` nodedef default. Red attenuates much
  less than green which attenuates much less than blue, so a Red
  Plastic that turns on SSS would scatter as if it were skin — its
  blue channel attenuates fastest, the surface picks up a warm
  red-shifted core indistinguishable from human flesh.
* A compositing layer that targets the subsurface scalar inherits
  the same skin profile regardless of the material it's targeting:
  a blue ceramic would scatter pink, gold metal would scatter pink,
  brushed steel would scatter pink — every material scatters with
  the same opinionated RGB attenuation tuned for caucasian skin.
* Round-trip importers that read `subsurface_radius = (0.794704,
  0.531734, 0.292854)` may treat the triple as authored intent
  ("the artist chose a specific scatter profile") rather than the
  bridge-emitted default that should fall through to the nodedef.
* The values do not match the source DCC: the PhysicalMaterial has
  no SSS knobs touched. The exported document misrepresents what
  the artist authored.

**Fix.** Add a fourth post-parse normalization pass in
`MtlxShaderWriter::Write()` (run immediately after MAX-MAT-004's pass)
that walks the parsed `MaterialX::Document` and, for each
`standard_surface` node, removes the `subsurface_radius` input when
*all* of:

* The node's `subsurface` input is provably zero (absent, or present
  and statically `0.0`, tolerant of float noise). A connected
  `subsurface` could carry a runtime non-zero value, so the input is
  preserved in that case. A statically non-zero `subsurface` means
  the lobe is active and the radius is observable — preserve it as
  authored.
* The `subsurface_radius` input is not connected (static value only).
* The static triple matches the bridge's hard-coded skin leak
  `(0.794704, 0.531734, 0.292854)` within a tolerance of `1e-4`
  per channel. The looser epsilon (vs the `1e-6` used elsewhere)
  absorbs the single-precision round-trip through the MaterialX XML
  serializer; the next-nearest neighbor a user might author by
  intent is far outside `1e-4`.

After normalization the input falls back to the nodedef default
`(1, 1, 1)`, which is the correct "no SSS profile was authored"
neutral state. A downstream context that later turns SSS on will see
a uniform-scattering neutral white instead of the opinionated skin
profile.

**Bounds (where the fix conservatively does nothing):**

* `subsurface` is connected to a node or nodegraph — runtime value
  unknown; assume the SSS lobe may be intentional and keep the
  radius as-is.
* `subsurface` is statically non-zero — the lobe is active and the
  radius is observable; keep it as authored.
* `subsurface_radius` is connected — could carry an intentional
  procedural radius driven by a texture or attribute; keep it
  regardless of `subsurface`.
* `subsurface_radius` has a static triple that differs from the
  leak triple by more than `1e-4` on any channel — user authored
  it explicitly; keep it.
* The node has no `subsurface_radius` input at all — already at the
  nodedef default; nothing to do.

**Validator.**
`/Users/d.smith/MirisProjects/Agent Builder/agent/arch-builds/fac3976c-b89a-4cbc-8851-ccf62cfdb993/normalize_subsurface_radius_default.py`
mirrors the C++ logic at the USD layer (the C++ runs at the
MaterialX-doc layer earlier in the pipeline). Running it on the
captured `complex_export.usda` strips exactly 6 leak inputs
(6 materials × 1 input), with zero collateral changes and idempotent
on a second pass:

```
== normalize_subsurface_radius_default pass 1 ==
  subsurface_radius: 6
  materials_touched: 6
== collateral diff ==
  total removed: 6
  total added:   0
== pass 2 (idempotence) ==
  subsurface_radius: 0
```

The diff is purely structural at the USD-layer level — exactly the
six `inputs:subsurface_radius` attributes the bridge over-authored,
on exactly the six materials that had them, with zero side effects
on every other attribute on every other prim.

**Karma renders.** Same two-render protocol as MAX-MAT-004:

* **Gated case** (`subsurface = 0` on every material — the corpus
  default): `render_karma_prefix.png` (leak present) and
  `render_karma_postfix.png` (leak stripped) sit at or below the
  Karma 21.0.700 sampler-noise floor. The fix delta is a visual
  no-op — exactly what the BSDF math predicts since `subsurface * 0
  == 0`, regardless of what radius the dead lobe would have used.
* **Stressed case** (`subsurface = 1.0` overlaid on every material):
  `render_karma_stress_prefix.png` and `render_karma_stress_postfix.png`
  diverge measurably. The prefix renders every material with the
  skin-tone RGB attenuation; the postfix renders every material with
  the neutral white attenuation. The stress overlay also pushes
  `subsurface_color` to a neutral grey to make the radius's effect
  on chromatic attenuation the dominant variable (otherwise the
  redundant `subsurface_color = (1, 1, 1)` corpus default would mask
  the radius delta on a flat white scatter target).

The observable that proves the fix in the **common case** is
structural — the six `inputs:subsurface_radius` attributes
disappear from the USDA. The observable that proves the fix is
*meaningful* (i.e. that the leak was real and consequential) is
the visible divergence in the **stressed case**.

**Retirement condition.** Same as MAX-MAT-001/002/004: when
Autodesk fixes `MtlxIOUtil` to stop emitting the spurious skin SSS
radius, the pass becomes inert (no node matches the trigger
condition). Safe to keep as a guard for older 3ds Max installs.

### PhysicalMaterial — bridge writes 13 spec-default inputs on every shader → `standard_surface` inputs at nodedef default  (MAX-MAT-006)

**Symptom.** The 3ds Max-shipped `MtlxIOUtil.ExportMtlxString` MaxScript
bridge authors 13 inputs on every `ND_standard_surface_surfaceshader`
at their MaterialX nodedef-default value, regardless of whether the
source PhysicalMaterial set any of them. The 13 inputs and their
bridge-emitted (= nodedef-default v1.0.1) values are:

| Float input | Default | Color3 input | Default | Bool input | Default |
| --- | --- | --- | --- | --- | --- |
| `base`               | 1.0 | `coat_color`         | (1, 1, 1) | `thin_walled` | false |
| `coat`               | 0.0 | `specular_color`     | (1, 1, 1) | | |
| `diffuse_roughness`  | 0.0 | `subsurface_color`   | (1, 1, 1) | | |
| `specular`           | 1.0 | `transmission_color` | (1, 1, 1) | | |
| `subsurface`         | 0.0 |                      |           | | |
| `subsurface_scale`   | 1.0 |                      |           | | |
| `transmission`       | 0.0 |                      |           | | |
| `transmission_depth` | 0.0 |                      |           | | |

Six out of six materials in the diagnostic corpus
(`/Users/d.smith/MirisProjects/Agent Builder/agent/pipeline-runs/3e596cac-1d4f-45fe-82eb-afa09679eae9/complex_export.usda`)
exhibit this — every material carries all 13 inputs authored at exactly
the same nodedef-default value, contributing 78 redundant attribute
writes (6 × 13) on the corpus. The diagnostic agent's
`findings_evidence.json` records the per-material list verbatim. On a
single material, the 13 redundantly-authored inputs are roughly 30% of
its `standard_surface` attribute count.

**Why it matters.** Unlike MAX-MAT-001/002/004/005, this is **not** a
latent-contamination bug. The 13 inputs carry the nodedef-default
values, so even if a downstream context flipped a gating scalar
(`coat > 0`, `subsurface > 0`), the resulting BSDF would evaluate to
the same parameters whether the inputs were present-at-default or
absent. The cost is purely structural:

* **Layer bloat.** 78 redundant attribute writes on the 6-material
  corpus, ~13 per material. On real-world scenes with dozens or
  hundreds of materials, this multiplies into kilobytes-to-megabytes
  of pure noise per layer.
* **`usddiff` legibility.** A `usddiff` between two layers carrying
  these redundant defaults sees them as authored values that need to
  match byte-for-byte. A meaningful diff is buried under 13 lines of
  same-on-both-sides noise per material.
* **Authoring-intent signal.** With the strip applied, every input
  remaining in the layer carries semantic intent — the artist (or
  upstream tool) set it deliberately. Without the strip, "input is
  authored" no longer correlates with "artist had an opinion."
* **Round-trip ambiguity.** A re-importing tool can't distinguish
  "artist explicitly set this to the default" from "bridge dumped a
  default value here." Both states encode "use the default," but
  variant sets / layer overrides treat them differently (an explicit
  author wins over a stronger layer's override; an absent input lets
  the strongest layer prevail).

**Fix.** Add a fifth post-parse normalization pass in
`MtlxShaderWriter::Write()` (run immediately after MAX-MAT-005's pass)
that walks the parsed `MaterialX::Document` and, for each
`standard_surface` node, removes each of the 13 inputs independently
when *all* of:

* The input is present on the node.
* The input is not connected (static value only).
* The input's static value exactly matches the
  `ND_standard_surface_surfaceshader` v1.0.1 nodedef default
  (tolerant of float noise on numeric types; exact `"true"`/`"false"`
  string match on `thin_walled`).

After strip each input falls through to its nodedef default. Because
the stripped value *is* the default, the renderer sees the same BSDF
parameters as before and the visual output is unchanged — the fix is
purely structural.

Per-input independence mirrors MAX-MAT-004's design: each of the 13
inputs is multiplied / interpolated into the BSDF separately, so each
can be tested and stripped on its own merits. A user who explicitly
overrides one of them (say `base = 0.6`) keeps that override while the
other twelve (still at their nodedef-default values) are removed.

**MaterialX 1.39 `ND_standard_surface_surfaceshader` v1.0.1.** The
nodedef ships in two versions: v1.0.0
(`ND_standard_surface_surfaceshader_100`) and v1.0.1
(`ND_standard_surface_surfaceshader`, marked
`isdefaultversion="true"`). v1.0.1 inherits from v1.0.0 but overrides
`base` (0.8 → 1.0) and `base_color` ((1, 1, 1) → (0.8, 0.8, 0.8)). The
Max bridge writes the v1.0.1 nodedef name on every shader (verified on
the corpus: `info:id = "ND_standard_surface_surfaceshader"`), so the
strip pass compares against v1.0.1 defaults. `base = 1.0` is therefore
the default that gets stripped; `base = 0.8` is treated as artist intent
(it matches the *v1.0.0* default but not v1.0.1). `base_color` is not in
the 13-input list because its v1.0.1 default `(0.8, 0.8, 0.8)` is a
non-neutral grey that artists virtually always override.

**Bounds (where the fix conservatively does nothing):**

* The input is connected to a node, nodegraph, or output — could carry
  a procedural value that resolves to a non-default at runtime; keep.
* The input's static value differs from the nodedef default — artist
  authoring intent; keep.
* The shader is not a `standard_surface` — out of scope for this pass
  (the 13-input list and the nodedef defaults are specific to
  `ND_standard_surface_surfaceshader`).
* The input is absent — already at the nodedef default; nothing to do.

**Care with the other four normalizers.** MAX-MAT-001
(`specular_rotation = 0.25`), MAX-MAT-002 (`emission = 1.0` /
`emission_color = (0, 0, 0)`), MAX-MAT-004 (`coat_IOR = 1.52`,
`coat_affect_color = 0.5`, `coat_affect_roughness = 0.5`,
`coat_roughness = 0.0`) and MAX-MAT-005
(`subsurface_radius = (0.794704, 0.531734, 0.292854)`) all strip
bridge-leaked values that *differ* from the nodedef default. The
MAX-MAT-006 list (the 13 above) is intentionally **disjoint** from
those — it only includes inputs whose bridge value matches the nodedef
default. The five passes run in order; after all five complete, a
default PhysicalMaterial roundtrips through the bridge with zero
redundantly-authored `standard_surface` inputs.

**Validator.**
`/Users/d.smith/MirisProjects/Agent Builder/agent/arch-builds/aee592c2-bd87-4e17-b8d8-ee1a4d6c6e2b/normalize_spec_default_inputs.py`
mirrors the C++ logic at the USD layer (the C++ runs at the
MaterialX-doc layer earlier in the pipeline). Running it on the
captured `complex_export.usda` strips exactly 78 inputs
(6 materials × 13 inputs), with zero collateral changes and idempotent
on a second pass:

```
== normalize_spec_default_inputs pass 1 ==
  shaders_inspected: 6
  materials_touched: 6
  total_strips:      78
    base                  : 6
    coat                  : 6
    coat_color            : 6
    diffuse_roughness     : 6
    specular              : 6
    specular_color        : 6
    subsurface            : 6
    subsurface_color      : 6
    subsurface_scale      : 6
    thin_walled           : 6
    transmission          : 6
    transmission_color    : 6
    transmission_depth    : 6
== collateral diff ==
  total removed: 78
  total added:   0
== pass 2 (idempotence) ==
  total_strips: 0
```

The diff is purely structural at the USD-layer level — exactly the 13
`inputs:<name>` attributes the bridge over-authored, on exactly the six
materials that had them, with zero side effects on every other
attribute on every other prim.

**Karma renders.**
`render_karma_prefix.png` (the captured corpus with the redundant
defaults) and `render_karma_postfix.png` (the validator-applied corpus)
are visually indistinguishable, at the Karma 21.0.700 CPU sampler-noise
floor:

```
prefix vs postfix:   max 19/255   mean 0.0221/255   nonzero 4.34%
```

Same magnitude as the MAX-MAT-004 noise-floor signal (max 20/255,
mean 0.0166/255, 4.33% nonzero — re-rendering the same stage twice).
The fix is a visual no-op exactly because the stripped values equal the
nodedef defaults the renderer resolves absent inputs to — a visible
difference here would mean the strip targeted the *wrong* default,
i.e. a code bug.

**Visual auditor pair.** Because this is a structural-only fix with no
latent visual case, the auditor pair degenerates: the "what fixed looks
like" reference image and the "what we got" postfix image are the same
image (both render the same BSDF). `render_unreal_reference.png` is a
byte-identical copy of `render_karma_postfix.png`; the auditor's
checklist is **prefix and postfix renders are visually indistinguishable
modulo Karma sampler noise (max delta ≤ ~20/255, mean ≤ ~0.025/255),
AND the structural validator output reports 78 strips with zero
collateral**. If the prefix and postfix diverge beyond the noise floor,
the C++ strip targeted a wrong default and the BSDF math has shifted —
that's the regression signal.

**Retirement condition.** Same as MAX-MAT-001/002/004/005: when
Autodesk fixes `MtlxIOUtil` to stop authoring nodedef-default values on
every shader, the pass becomes inert (no input matches the trigger
condition). Safe to keep as a guard for older 3ds Max installs. The
13-input list is also resilient to MaterialX version bumps — a future
v1.0.2 that changes one of these defaults will simply mean the existing
strip stops firing for that input (the static value no longer matches);
the strip never *adds* an attribute, so a stale default value is at
worst a missed-cleanup opportunity, not a correctness regression.

### OpenPBR material → ND_open_pbr_surface_surfaceshader (audit, MAX-MAT-001..006 STOP here)  (MAX-MAT-007)

**Symptom.** 3ds Max 2025.3 adds the `OpenPBR()` material class, which the
MaxUSD bridge exports to a `ND_open_pbr_surface_surfaceshader` MaterialX
node (MaterialX 1.39 OpenPBR Surface v1.1) instead of
`ND_standard_surface_surfaceshader`. The existing five MAX-MAT-* post-parse
normalization passes in `MtlxShaderWriter.cpp` (specular_rotation,
emission default, coat-block, subsurface_radius, spec-default-inputs) are
ALL scoped to `standard_surface` via `doc->getNodes("standard_surface")` and
therefore skip the OpenPBR shader entirely. The audit's job is to **lock
that surgical-coverage bound in with negative-test coverage** — no C++
logic change.

**Why it matters.** Several open_pbr_surface inputs share their NAME with
standard_surface inputs that the five existing passes strip:

| Collision name | open_pbr_surface default | standard_surface default | What a wildcard refactor would do |
| --- | --- | --- | --- |
| `coat_color` | (1, 1, 1) | (1, 1, 1) | strip-on-default value match -- visually no-op but structurally wrong |
| `transmission_color` | (1, 1, 1) | (1, 1, 1) | same |
| `specular_color` | (1, 1, 1) | (1, 1, 1) | same |
| **`subsurface_color`** | **(0.8, 0.8, 0.8)** | **(1, 1, 1)** | **strip artist-authored (1, 1, 1) -> sphere goes from neutral white to (0.8, 0.8, 0.8) muted grey** -- the audit's primary visible bound |
| `subsurface_radius` (FLOAT on open_pbr) | 1.0 | (color3 type; leak triple) | _TryGetStaticColor3 parses-fails on a float -- accidental safety, pinned anyway |

The other side of the bound is that the renamed gate inputs would not
be reachable from the existing passes even if widened to a wildcard,
because the gate predicates inside each pass reference the
standard_surface input names (`coat == 0` for the coat block, `subsurface
== 0` for the subsurface_radius strip). Open_pbr_surface renames those to
`coat_weight` and `subsurface_weight`, so a wildcard refactor would run
the strip UNGATED across every leak-named input -- including artist
authoring with `coat_weight = 0.5` set (the lobe is ON, the strip would
mutate the surface anyway).

**Fix.** None. The existing C++ is already correct. The audit lands:

1. **Surgical-bounds comment block** in `MtlxShaderWriter.cpp` above the
   chain of normalization-pass calls, enumerating the open_pbr_surface
   collision shape (input names + defaults + the renamed gate predicates)
   and naming both the MaxScript regression and the Python validator that
   pin the bound.
2. **MaxScript regression**
   `test_export_openpbr_material_preserves_collision_named_inputs` in
   `src/Tests/Integration/mtlxShaderWriter_test.ms`. Exports an `OpenPBR()`
   material via the MaterialX target with the four collision-named
   color3 inputs set to white and the two renamed gate inputs
   (`coat_weight = 0.5`, `subsurface_weight = 1.0`) authored. Asserts the
   exported shader is `ND_open_pbr_surface_surfaceshader` (the writer's
   first surgical bound — the OpenPBR material is NOT collapsed onto
   standard_surface) and that every authored input reaches the USD
   present, authored, and at the input's authored value. Gated on Max
   2025.3+ (`maxver[1] < 27900` mirroring the existing
   `test_export_OpenPBR_material`).
3. **Python validator** at
   `/Users/d.smith/MirisProjects/Agent Builder/agent/arch-builds/5c66e23f-c809-45a2-baeb-328d9fe6ddf1/validate_openpbr_surface_coverage.py`.
   Builds a synthetic `MaterialX::Document` carrying a `standard_surface`
   node with every known MAX-MAT-001/002/004/005/006 leak side-by-side
   with an `open_pbr_surface` node carrying collision-named inputs. Runs
   all five mirror-of-C++ normalization passes, then asserts:
     * the `standard_surface` leaks are stripped (positive control --
       confirms the passes are still doing their job);
     * **every** `open_pbr_surface` input is preserved -- both name
       (no surprise removal) and value (no surprise mutation);
     * spot checks on the specific collision-named values
       (`subsurface_color = (1, 1, 1)`, `coat_color = (1, 1, 1)`,
       `transmission_color = (1, 1, 1)`, `subsurface_radius = 0.794704`
       as a FLOAT, `subsurface_weight`, `coat_weight = 0.5`);
     * idempotence -- a second run of all five passes on the
       post-normalized doc does not strip anything additional on either
       node category.
   On the 2026-06-26 baseline the validator passes all 35 named
   assertions (11 positive-control strips + 22 surgical-coverage
   preserves + 2 idempotence checks). A wildcard refactor that widened
   the gate would fail by named case: "openpbr: subsurface_color =
   (1,1,1) survives ... FAIL  before=(1.0, 1.0, 1.0), after=ABSENT".

**Bounds (where the C++ today conservatively does nothing on OpenPBR):**

* The five passes iterate `doc->getNodes("standard_surface")`, which is
  the MaterialX node-category gate. open_pbr_surface nodes are in a
  different category and are not iterated.
* Even if a refactor changed the iteration to `doc->getNodes()`
  (wildcard), the per-input strip checks would behave inconsistently
  because the standard_surface gate predicates (`coat == 0`,
  `subsurface == 0`) check input names that simply do not exist on
  open_pbr_surface. The strip would then run UNGATED on every
  collision-named input. The audit's MaxScript + Python tests catch
  this regression by named case rather than by aggregate diff.
* The `subsurface_radius` on open_pbr_surface is type=`float`; on
  standard_surface it is type=`color3`. MAX-MAT-005's pass calls
  `_TryGetStaticColor3` on the input, which parses-fails on a single
  float and skips. This is accidental safety -- pinned by the validator
  case "openpbr: subsurface_radius = 0.794704 (FLOAT) survives
  (accidental-safety pin)" so a future widening that switched to a
  type-agnostic parse would fail loudly.

**Visual demonstration of the surgical bound.** A single OpenPBR sphere
is rendered twice in Karma CPU at 512x512 with the same camera +
lighting (key DistantLight @ 6.0 + fill @ 2.0, opposing angles).
`render_karma_postfix.png` carries
`inputs:subsurface_color = (1, 1, 1)` authored on the shader (the
current correct behavior — the input survived the export untouched).
`render_unreal_reference.png` (the "still-broken" reference) has the
same shader with that input ABSENT (the wildcard-refactor counterfactual
where the input was over-stripped, so the renderer resolves to the
OpenPBR nodedef default of (0.8, 0.8, 0.8)). Mean per-channel
intensity over the captured PNGs:

```
render_karma_postfix.png       mean 38.76 / 255   (uniformly across RGB)
render_unreal_reference.png    mean 38.20 / 255   (uniformly across RGB)
postfix - reference            mean +0.56 / 255   (positive: postfix is brighter)
nonzero pixels                 31.1%
PNG SHA-256                    9baecce7... vs b71ac07c...   (distinct)
```

The delta is small in absolute terms because the OpenPBR SSS lobe at
this scene scale dilutes the 20% per-channel subsurface_color
difference into a path-traced average that sits close to the Karma
sampler-noise floor, but it is **reproducibly directional**: postfix
brighter, still-broken darker. Composite at `compare_side_by_side.png`
in the same arch-build dir. **The auditor's checklist: the postfix
render is the brighter of the two by mean intensity, the two PNGs
have distinct SHA-256s, and the direction matches the per-channel
attenuation OpenPBR's SSS lobe applies to subsurface_color.** If the
auditor sees the renders go byte-identical OR the still-broken
reference reading brighter, the C++ normalizers have started touching
open_pbr_surface and the surgical bound has broken.

**Retirement condition.** This audit does not have an upstream-fix
retirement condition the way MAX-MAT-001..006 do: the bound it locks in
is a SCOPE limit on Miris-authored C++, not a workaround for a 3ds Max
bridge bug. The bound retires only if the standard_surface normalizers
themselves are removed (e.g. Autodesk fixes MtlxIOUtil on every input
they cover) and then the scope limit becomes moot. Until then, this
audit + its three artifacts (C++ comment, MaxScript regression, Python
validator) are the regression-coverage net that prevents an
"extend MAX-MAT-* normalizers to OpenPBR" PR (which is what the planner
auto-emitted this bite for) from shipping unverified C++ that silently
mutates artist-authored OpenPBR materials. **A future PR that adds
genuine OpenPBR-specific normalizers should land as a separate
MAX-MAT-* entry with its own captured corpus of OpenPBR leak values
from MtlxIOUtil — not as a wildcard widening of these five passes.**

### Color/emission writer-path separation (audit, MaterialX normalizers STOP at MtlxShaderWriter) (MAX-PRIM-001)

**Symptom.** The 3ds Max → USD exporter has two parallel writer paths
that author colour and emission for the same source 3ds Max material
under different target schemas:

1. **MaterialX writer path** (`src/translators/MtlxShaderWriter.cpp`,
   Max 2025+) — for materials with a MaterialX bridge representation
   (PhysicalMaterial via `MtlxIOUtil.ExportMtlxString`,
   `MaterialXMaterial` directly, OpenPBR in 2025.3+). Runs 5 post-parse
   normalization passes on the in-memory `MaterialX::Document`
   (`_NormalizeStandardSurfaceSpecularRotation`,
   `_NormalizeStandardSurfaceEmissionDefault`,
   `_NormalizeStandardSurfaceCoatDefaults`,
   `_NormalizeStandardSurfaceSubsurfaceRadiusDefault`,
   `_StripStandardSurfaceSpecDefaultInputs`), all gated on
   `doc->getNodes("standard_surface")`. Authors `UsdShade` shader prims
   carrying `ND_standard_surface_surfaceshader` (and, for OpenPBR,
   `ND_open_pbr_surface_surfaceshader` — see MAX-MAT-007).

2. **UsdPreviewSurface writer path** — for materials targeted at
   `UsdImagingTokens->UsdPreviewSurface`. Split between
   `src/MaxUsd/Translators/LastResortUSDPreviewSurfaceWriter.cpp` (the
   C++ fallback registered with `ContextSupport::Fallback`, authors a
   single `inputs:diffuseColor` from `Mtl::GetDiffuse()`) and the Python
   `DefaultShaderWriter` in
   `src/ApplicationPlugins/usd-component/Contents/scripts/materials/shaderWriter.py`
   (registered for `PhysicalMaterial`, `PBR Material (Metal/Rough)`,
   `PBR Material (Spec/Gloss)`, `USD Preview Surface`, `OpenPBR Material`
   when `ConvertMaterialsTo == "UsdPreviewSurface"`; data-driven from
   the `.material_conversion` JSON tables in
   `data_files/default.material_conversion`,
   `3dsmax_materials.mat_def`, `usd_materials.mat_def`). Authors
   `UsdShade` shader prims carrying `UsdPreviewSurface`.

There is no shared color/emission helper between the two paths today.
The planner auto-emitted `MAX-PRIM-001-unify-color-emission-helper`
with severity medium and rationale "Unify color-emission helper across
MaterialX and UsdPreviewSurface writers". The audit's job is to **lock
in the cross-writer-path bound with negative-test coverage** — no C++
logic change.

**Why it matters.** The two writers' input vocabularies, defaults,
gate topology, and document models all diverge on every axis a "unify
color-emission helper" would have to bridge:

| Concept | UsdPreviewSurface input | MaterialX standard_surface input | Conflict |
| --- | --- | --- | --- |
| Emission color | `emissiveColor` (color3f, default `(0, 0, 0)`) | `emission_color` (color3, default `(1, 1, 1)`) gated by `emission` (float, default 0.0) | Different name, different dimensionality (1 vs 2 inputs), opposite default-zero semantic |
| **Diffuse color** | **`diffuseColor` (color3f, default `(0.18, 0.18, 0.18)` — 18% linear grey)** | **`base_color` (color3, default `(0.8, 0.8, 0.8)`) gated by `base` (float, default 0.8)** | **Different name, different default, MaterialX gates by `base` scalar** |
| Specular color | `specularColor` (color3f, default `(0, 0, 0)`) + `useSpecularWorkflow` bool toggle | `specular_color` (color3, default `(1, 1, 1)`) gated by `specular` (float, default 1.0) | Different topology — UsdPS has a workflow toggle, MaterialX has a scalar gate |
| Metallic | `metallic` (float, default 0.0) | `metalness` (float, default 0.0) | Different name only |
| Roughness | `roughness` (float, default 0.5) | `specular_roughness` (float, default 0.2) | Different name AND different default |
| Opacity | `opacity` (FLOAT, default 1.0) | `opacity` (color3, default `(1, 1, 1)`) | Name-collision, dimensionality conflict |
| Normal | `normal` (normal3f direct input) | (no direct input — routed through a `normalmap` node) | Different topology — UsdPS has a direct input, MaterialX wires through a normalmap node |

The "color-emission" concept the planner's bite called out covers
BOTH the `diffuseColor`/`base_color` and `emissiveColor`/`emission_color`
axes. On both axes UsdPreviewSurface has a single combined input while
MaterialX standard_surface has a `(scalar gate, color)` pair —
structurally not unifiable into a single helper that preserves artist
intent. A unification attempt that flattened MaterialX's split into
UsdPS's combined inputs would lose the gate scalar; one that split
UsdPS's combined inputs into a MaterialX-style pair would have to
invent the gate scalar (and would AUTHOR the `(1.0, (X, Y, Z))` shape
that MAX-MAT-002 itself strips when the colour is `(0, 0, 0)`).

The audit's **primary visible bound** is `emissiveColor`: an artist
authoring `(0.05, 0, 0)` on a UsdPreviewSurface shader (a faint
dark-red glow override) survives a re-export because the MaterialX
normalizers cannot reach the UsdPreviewSurface writer path. A wildcard
"unify color-emission helper" refactor that ported the MAX-MAT-002
strip-gate to UsdPreviewSurface would have to widen the gate to fire
without the paired `emission` scalar (which UsdPS does not have) — and
the widened gate would either be dead code OR would silently strip
artist-authored `emissiveColor` on appearance alone. The Karma render
pair below shows that branch directly: the postfix sphere reads a
visible dark-red glow; the still-broken sphere is near-black grey.

**Fix.** None. The existing C++ is already correct. The audit lands:

1. **Surgical-bounds comment block** in
   `MtlxShaderWriter.cpp::MtlxShaderWriter::Write` above the chain of
   normalization-pass calls, enumerating the collision-shape table
   above and naming both the MaxScript regression and the Python
   validator that pin the bound. A complementary **back-pointer comment
   block** in `LastResortUSDPreviewSurfaceWriter.cpp` above its `Write`
   function records the bound from the UsdPreviewSurface side.
2. **MaxScript regression**
   `test_export_writer_path_separation_color_emission` in
   `src/Tests/Integration/mtlxShaderWriter_test.ms`. Exports a
   PhysicalMaterial with artist-authored `emit_color = color 51 0 0`
   (faint dark-red emission) under BOTH writer targets in a single
   test run. Asserts (a) the UsdPreviewSurface shader carries
   `inputs:emissiveColor` with positive red channel and zero green +
   blue channels (the cross-writer-path bound — a wildcard unify
   refactor that ported the MAT-002 strip would zero all three
   channels), AND (b) the MaterialX standard_surface shader carries
   both `emission` (positive scalar) AND `emission_color` (positive
   red, zero green + blue) — the positive control that MAT-002's
   strict pair-gate is not over-stripping artist intent. Gated on Max
   2025+ (`maxver[1] >= 26900`).
3. **Python validator** at
   `/Users/d.smith/MirisProjects/Agent Builder/agent/arch-builds/da4ca14a-c323-47a6-bd26-8997a625e3f6/validate_color_emission_writer_separation.py`.
   Builds a synthetic `MaterialX::Document` carrying a
   `standard_surface` node with every known MAX-MAT-001/002/004/005/006
   leak side-by-side with a `UsdPreviewSurface`-category node carrying
   UsdPreviewSurface-shaped inputs (`emissiveColor = (0.05, 0, 0)`
   authored, `diffuseColor = (0.18, 0.18, 0.18)` UsdPS-default,
   `metallic = 0.5`, `roughness = 0.3`, `ior = 1.4`,
   `specularColor = (1, 1, 1)` name-spelling-near-collision,
   `useSpecularWorkflow = true` UsdPS-only topology,
   `opacity = 1.0` FLOAT topology-collision,
   `normal = (0.5, 0.5, 1.0)` UsdPS-only direct input). Runs all five
   C++-mirror normalization passes, then asserts:
     * the `standard_surface` leaks are stripped (positive control —
       confirms the passes are still doing their job);
     * **every** `UsdPreviewSurface`-category input is preserved — both
       name (no surprise removal) and type + value (no surprise mutation);
     * spot checks on the audit's primary visible bound
       (`emissiveColor = (0.05, 0, 0)`), the UsdPS-default-equals-zero
       collision (`diffuseColor = (0.18, 0.18, 0.18)`), the
       name-spelling-near-collision (`specularColor = (1, 1, 1)` vs
       standard_surface `specular_color = (1, 1, 1)`), the
       topology-collision (`opacity = 1.0` FLOAT vs standard_surface
       `opacity` color3), and the UsdPS-only inputs
       (`useSpecularWorkflow = true`, `normal = (0.5, 0.5, 1.0)`);
     * idempotence — a second run of all five passes on the
       post-normalized doc does not strip anything additional on
       either node category.
   On the 2026-06-26 baseline the validator passes all 36 named
   assertions (11 positive-control strips + 23 cross-writer-path
   preserves + 2 idempotence checks). A wildcard refactor that ported
   the strip-gate would fail by named case: "usdps: emissiveColor =
   (0.05, 0, 0) survives ... FAIL  before=…, after=ABSENT".

**Bounds (where the C++ today conservatively does nothing):**

* The 5 normalizers iterate `doc->getNodes("standard_surface")`. Any
  other node category, including the synthetic `UsdPreviewSurface`
  category the validator authors, is filtered out.
* The 5 normalizers are called from `MtlxShaderWriter::Write` only.
  `LastResortUSDPreviewSurfaceWriter::Write` does not invoke, share
  state with, or transitively reach the MaterialX normalizers; the
  Python `DefaultShaderWriter.Write` likewise only invokes
  `usd_material_writer.export_material` (data-driven from the
  `.material_conversion` JSON tables).
* The two writer paths operate on different in-memory document types:
  MtlxShaderWriter on a `MaterialX::DocumentPtr` returned by the
  MaxScript bridge; LastResort on a Max `Mtl*` directly; Python
  DefaultShaderWriter on a `Mtl*` via `pymxs.runtime` plus the
  `.material_conversion` JSON tables. A "unify color-emission helper"
  would have to bridge `MaterialX::NodePtr.getInput()`,
  `Mtl::GetDiffuse()` / `GetSelfIllum*`, and the `.material_conversion`
  JSON evaluator — three entirely different APIs. No such helper
  exists today and the audit's bound is that none should be
  introduced as a wildcard widening of the existing 5 passes.

**Visual demonstration of the surgical bound.** A single
UsdPreviewSurface sphere is rendered twice in Karma CPU at 512x512
with the same camera + lighting (DistantLight key 2.0 + fill 0.7,
opposing angles, low diffuse base so the emission contribution
dominates). `render_karma_postfix.png` carries
`inputs:emissiveColor = (0.05, 0, 0)` authored on the shader (the
current correct behavior — the input survived the export untouched
because no cross-writer helper exists to strip it).
`render_unreal_reference.png` (the "still-broken" reference) has the
same shader with that input ABSENT (the wildcard-cross-writer-unify
counterfactual where the input was over-stripped, so the renderer
resolves to the UsdPreviewSurface nodedef default of `(0, 0, 0)`).
Mean per-channel intensity over the captured PNGs:

```
render_karma_postfix.png       mean  R=26.38  G=14.21  B=14.21  / 255
render_unreal_reference.png    mean  R=14.21  G=14.21  B=14.21  / 255
postfix - reference            mean  R=+12.17 G=0.00   B=0.00   / 255
                                    (single-channel, large, directional)
per-pixel max delta            R=46 / 255 (the sphere's red rim)
nonzero pixels                 34.8% (the foreground sphere mask)
PNG SHA-256                    492bb970... vs 036104d7...  (distinct)
```

Unlike MAX-MAT-007's SSS-lobe-diluted bound, this delta is large
and unambiguous because UsdPreviewSurface's `emissiveColor` is a
single direct contribution with no lobe gate in between. The G and B
channels are byte-identical between postfix and still-broken — exactly
matching the `(0.05, 0, 0)` artist authoring shape. Composite at
`compare_side_by_side.png` in the same arch-build dir. **The auditor's
checklist: the postfix render carries a visible dark-red tint across
the sphere foreground; the still-broken reference is a uniform
near-black grey; the postfix's mean RED channel is ~12/255 higher; the
G and B channels match byte-for-byte; the two PNGs have distinct
SHA-256s.** If the auditor sees a red tint on the still-broken
reference OR the renders go byte-identical, a cross-writer helper has
started touching UsdPreviewSurface inputs and the surgical bound has
broken.

**Retirement condition.** This audit does not have an upstream-fix
retirement condition the way MAX-MAT-001..006 do: the bound it locks
in is a SCOPE limit on Miris-authored C++, not a workaround for a 3ds
Max bridge bug. The bound retires only if the MaterialX writer chain
itself is removed (e.g. Autodesk's MaxUSD bridge becomes the canonical
MaterialX writer, replacing both paths with a single helper that
operates at the bridge layer) — at which point the scope limit becomes
moot. Until then, this audit + its three artifacts (C++ comment block
in `MtlxShaderWriter.cpp`, back-pointer comment block in
`LastResortUSDPreviewSurfaceWriter.cpp`, MaxScript regression, Python
validator) are the regression-coverage net that prevents a
"unify color-emission helper across MaterialX and UsdPreviewSurface
writers" PR (which is what the planner auto-emitted this bite for)
from shipping unverified C++ that silently mutates artist-authored
emission or diffuse colour on either writer path. **A future PR that
genuinely needs to share helper code between the two writer paths
should land as a separate concern with its own captured corpus + its
own MaxScript regression + its own doc entry naming what the shared
helper actually does — not as a wildcard widening of these five
passes into the UsdPreviewSurface writer's domain.**

### Node.wireColor / Node.material.diffuse → UsdGeomMesh.primvars:displayColor  (MAX-MAT-003)

**Symptom.** `MaxUsd::MeshConverter::ConvertToUSDMesh()` unconditionally
authors `primvars:displayColor` from the 3ds Max node's *wireframe color*
whenever no explicit displayColor primvar has already been written (e.g.
through the vertex-color → displayColor channel mapping). The source is
`src/MaxUsd/MeshConversion/MeshConverter.cpp` (the `if
(!usdMesh.GetDisplayColorAttr().IsAuthored()) { ... node->GetWireColor()
... }` block, post-MAX-MAT-002 around line 228).

In 3ds Max the *wireframe color* is a viewport organizational tag: a hue
assigned to a scene-graph node so the user can tell nodes apart in the
viewport. It is unrelated to the node's material. The diagnostic corpus
shows 6/6 meshes carrying a `primvars:displayColor` that disagrees with
their bound material. For example: the Teapot is bound to the Gold
material (`base_color = (0.92, 0.71, 0.24)`) but its
`primvars:displayColor` is `(0.85, 0.89, 0.68)` — a pale green-cream,
nothing like gold.

**Why it matters.** USD defines `primvars:displayColor` as the surface
color a consumer should use as a *fallback* when the bound
`UsdShadeMaterial` cannot be evaluated. PBR-capable USD renderers (Hydra
Storm, Karma, RenderMan, etc.) don't read displayColor when a material is
resolvable, so this bug is invisible there. It surfaces in *fallback*
contexts:

* Minimal Hydra delegates and scene-graph viewers that don't implement
  MaterialX.
* ARKit Quick Look paths and other thumbnail generators that consume
  USD/USDZ without a full PBR pipeline.
* The `usdview` "displayColor" overlay used to QA primvars.
* USDZ packagers that fall back to displayColor when bound shaders can't
  be inlined for distribution.

In all of those, the rendered surface color is the wireframe-derived hue
rather than the material's color — a Gold mesh that looks green-cream, a
Red Plastic mesh that looks muddy pink, a Blue Ceramic mesh that looks
olive, and so on.

**Fix.** Change the writer's single fallback line to derive the
displayColor from the bound material when one is present:

```cpp
if (!usdMesh.GetDisplayColorAttr().IsAuthored()) {
    Color displayColorSrc;
    if (Mtl* boundMtl = node->GetMtl()) {
        displayColorSrc = boundMtl->GetDiffuse();
    } else {
        displayColorSrc = Color(node->GetWireColor());
    }
    pxr::VtVec3fArray usdDisplayColor = {
        pxr::GfVec3f(displayColorSrc.r, displayColorSrc.g, displayColorSrc.b) };
    usdMesh.CreateDisplayColorAttr().Set(usdDisplayColor);
}
```

`Mtl::GetDiffuse(int mtlNum = 0, BOOL backFace = FALSE)` is the universal
Max SDK accessor for a material's "main" diffuse color. The
`LastResortUSDPreviewSurfaceWriter` already uses it to author the bound
material's `inputs:diffuseColor`, so the displayColor will be identical
to the diffuseColor the same material exports to USD. For a `MultiMtl`,
`GetDiffuse(0)` returns the first sub-material's diffuse, which is still
materially closer to the artist's intent than the viewport wireframe
color.

**Bounds (where the fix conservatively does nothing):**

* `usdMesh.GetDisplayColorAttr().IsAuthored()` is already true — the
  vertex-color → displayColor channel mapping (the
  `SetChannelPrimvarMapping 0 "displayColor"` opt-in) ran first and wrote
  the artist-authored value. We never overwrite it.
* `node->GetMtl() == nullptr` — no material bound. The wireframe color is
  the best representational color we have, so the previous behavior is
  preserved.

**Validator.**
`/Users/d.smith/MirisProjects/Agent Builder/agent/arch-builds/44966a53-f273-422c-9ce9-c1f2d7e477c1/normalize_display_color.py`
mirrors the C++ logic at the USD layer. Running it on the MAX-MAT-002-
clean corpus rewrites exactly the six mesh `primvars:displayColor`
values to match the bound material's `inputs:diffuseColor`, and leaves
every other attribute identical. PBR-path Karma renders pre- and post-
fix are byte-identical (same SHA-256) — the bound shaders are unchanged
and PBR renderers don't read displayColor. The displayColor-fallback
Karma renders (`material:binding` stripped from both copies) ARE
visually different: the wireframe-derived palette in the prefix is
replaced by the material-derived palette in the postfix, demonstrating
the observable behavior change for fallback consumers.

**Surgical-preservation validator (added 2026-06-23).**
`/Users/d.smith/MirisProjects/Agent Builder/agent/arch-builds/73201258-5289-47ae-a4fa-14e62d9716f6/validate_display_color_surgical.py`
extends the original validator with explicit **negative** cases —
mesh / node states that LOOK similar to the leak but must take a
specific branch (preauthored-preserved, mtl-diffuse, or
wire-color-fallback) and NOT silently change source. 8 cases mirror the
C++ decision against every branch the gate selects:

| Case in synthetic fixture | preauthored | mtl diffuse  | wire | expected color | branch taken |
| --- | --- | --- | --- | --- | --- |
| `WireNoMtl`               | (absent)    | (absent)     | red  | red            | `wire-color-fallback` |
| `MtlBound`                | (absent)    | green        | red  | green          | `mtl-diffuse` |
| `AuthoredNoMtl`           | blue        | (absent)     | red  | blue           | `preauthored-preserved` |
| `AuthoredWithMtl`         | blue        | green        | red  | blue           | `preauthored-preserved` (**KEY GAP**) |
| `MultiMtlFirstSub`        | (absent)    | magenta(sub0)| red  | magenta        | `mtl-diffuse` |
| `BlackDiffuseHonored`     | (absent)    | (0,0,0)      | red  | (0,0,0)        | `mtl-diffuse` |
| `WhiteDiffuseHonored`     | (absent)    | (1,1,1)      | red  | (1,1,1)        | `mtl-diffuse` |
| `AuthoredEqualsMtl`       | yellow      | yellow       | red  | yellow         | `preauthored-preserved` |

Plus an idempotence check (a second pass over the post-derived stage
takes the `preauthored-preserved` branch on every case). All 8 cases +
idempotence pass on the 2026-06-23 baseline. The validator asserts the
**branch label** as well as the final color, so a regression that
produces the right color via the wrong branch (e.g. `AuthoredEqualsMtl`
silently switching to `mtl-diffuse`) still surfaces by name.

The previously-uncovered surgical bound is `AuthoredWithMtl`: a future
regression that widened the `!IsAuthored()` gate (for example dropping
the `IsAuthored()` check entirely so material-bound meshes are always
rewritten with the material's diffuse) would silently eat the artist's
authored vertex-color displayColor. The existing suite covered the
IsAuthored() preservation ONLY when no material was bound; this case
locks in the combined `(authored, material-bound)` invariant.

**MaxScript regression (added 2026-06-23).**
`src/Tests/Integration/io_color_n_visibility_test.ms` now carries
`test_display_color_preserves_authored_when_material_bound`. The test
builds a box with wireframe red, an authored blue vertex color mapped to
`displayColor` via `SetChannelPrimvarMapping 0 "displayColor"`, AND a
Standard material with green diffuse bound to the node, then exports
and asserts `primvars:displayColor` on the resulting mesh is blue
(the artist's authored value) — not green (the material's diffuse) and
not red (the wireframe color). A failure means either the MAX-MAT-003
block over-widened the `!IsAuthored()` gate (C++ regression) or the
MaxScript channel mapping bridge dropped its opt-in writing on the
combined case (separate, deeper bug — the test surfaces it either way).

**Visual demonstration of the surgical bound.** A single-sphere fixture
with `primvars:displayColor` set on the unbound sphere is rendered
twice in Storm: `render_karma_postfix.png` (the current fix — solid
blue, artist value preserved) and `render_unreal_reference.png` (the
"still-broken" reference — solid green, the would-be-overwritten state
where the bound material's diffuse silently replaced the authored
value). The two PNGs differ by SHA-256 and visibly so. The auditor's
checklist: **the current fix's render is solid blue; if it ever
becomes solid green, the IsAuthored() gate has started overwriting
artist-authored displayColor.** Composite at `compare_side_by_side.png`
in the same arch-build dir.

**Value-coincidence band validator (second reinforcement, added 2026-06-26).**
`/Users/d.smith/MirisProjects/Agent Builder/agent/arch-builds/7a0593e4-57d8-4f15-baee-e73250d1e9a0/validate_display_color_tolerance_bounds.py`
pins the gate at **value-coincidence edges** the first reinforcement's
high-distinctness fixtures (BLUE / YELLOW / MAGENTA) do not exercise.
The MAX-MAT-003 gate is purely boolean (`IsAuthored()`) so there is no
float tolerance to widen, but the gate has two value-class edges the
first 8 cases do not pin:

  1. **Authored values coincident with default-looking sentinels.** A
     future refactor that swapped the bare `!IsAuthored()` check for
     "treat authored values that look like exporter defaults
     (`(0,0,0)`, `(1,1,1)`, `(0.5,0.5,0.5)`) as effectively unauthored"
     would silently start eating any mesh whose artist-authored
     displayColor happens to match a sentinel. The first reinforcement
     uses BLUE / YELLOW for its authored values; that widening passes
     every existing case.
  2. **Material-diffuse extremes the verbatim-write doesn't run on.**
     The gate writes whatever `GetDiffuse()` returns without clamping
     or epsilon-skipping. The first reinforcement's bland-LDR mtl cases
     (MAGENTA, YELLOW, BLACK, WHITE) sit comfortably in the mid-band; a
     widening that added "skip HDR diffuse" (`if (any channel > 1.0)
     fall back to wire`) or "skip near-zero diffuse" (`if (||c|| < eps)
     fall back to wire`) would still pass them.

| Case in synthetic fixture       | preauthored        | mtl diffuse        | wire                | expected color     | branch taken |
| ---                             | ---                | ---                | ---                 | ---                | --- |
| `AuthoredBlackWithMtl`          | `(0, 0, 0)`        | `(0.42, 0.18, 0.73)`| `(1, 0, 0)`         | `(0, 0, 0)`        | `preauthored-preserved` |
| `AuthoredWhiteWithMtl`          | `(1, 1, 1)`        | `(0.20, 0.40, 0.80)`| `(1, 0, 0)`         | `(1, 1, 1)`        | `preauthored-preserved` |
| `AuthoredMidGreyWithMtl`        | `(0.5, 0.5, 0.5)`  | `(0.95, 0.15, 0.10)`| `(1, 0, 0)`         | `(0.5, 0.5, 0.5)`  | `preauthored-preserved` |
| `AuthoredZeroNoMtl`             | `(0, 0, 0)`        | (absent)           | `(0.70, 0.30, 0.10)`| `(0, 0, 0)`        | `preauthored-preserved` |
| `MtlDiffuseHDR`                 | (absent)           | `(2.0, 0.5, 0.5)`  | `(1, 0, 0)`         | `(2.0, 0.5, 0.5)`  | `mtl-diffuse` |
| `MtlDiffuseTinyChannel`         | (absent)           | `(1e-3, 0, 0)`     | `(0.5, 0.5, 0.5)`   | `(1e-3, 0, 0)`     | `mtl-diffuse` |

Plus an idempotence check (second pass takes `preauthored-preserved`
on every case). All 6 cases + idempotence pass on the 2026-06-26
baseline. The validator asserts the **branch label** as well as the
final color so a regression that produces the right color via the
wrong branch surfaces by name (same pattern as the state-shape
validator).

**MaxScript regression (added 2026-06-26).**
`src/Tests/Integration/io_color_n_visibility_test.ms` now also carries
`test_display_color_preserves_default_looking_authored_value_when_material_bound`,
the value-coincidence companion of the first reinforcement's
`test_display_color_preserves_authored_when_material_bound`. It builds
a box with wireframe red, an artist-authored **black** vertex color
(`(0, 0, 0)` — the canonical default-looking sentinel) mapped to
`displayColor` via `SetChannelPrimvarMapping 0 "displayColor"`, AND a
Standard material with vivid red diffuse `(255, 38, 26)` bound to the
node, and asserts the exported `primvars:displayColor` is the artist's
black, not the material's red. A failure means the gate gained a
default-value filter or a sentinel short-circuit — the surgical bound
the first reinforcement's BLUE-vs-GREEN value pair leaves uncovered.

**Visual demonstration of the value-coincidence bound.** A second
single-sphere fixture renders the mid-grey sentinel `(0.5, 0.5, 0.5)`
versus the bound material's vivid red `(0.95, 0.15, 0.10)`. The
postfix render (`render_karma_postfix.png`) shows a visibly grey
sphere — the artist's mid-grey is preserved by the IsAuthored() gate
even though it coincides with a canonical default sentinel. The
reference render (`render_unreal_reference.png`) shows the same
sphere painted vivid red — what a future "treat default-looking
authored as effectively unauthored" widening would write. The two
PNGs differ by SHA-256. **Auditor's checklist for the second
reinforcement: the postfix render is solid grey; if it ever becomes
solid red, the gate has started overwriting artist-authored
displayColor at default-looking values.** Together with the first
reinforcement's blue-vs-green pair the two visual auditor pairs cover
the gate's two principal widening vectors (unconditional overwrite
when mtl is bound, and value-coincidence overwrite at default
sentinels). Composite at `compare_side_by_side.png` in the same
arch-build dir.

**Primvar-shape band validator (third reinforcement, added 2026-06-26).**
`/Users/d.smith/MirisProjects/Agent Builder/agent/arch-builds/46bb7b7e-974c-4ca0-acd6-dec38f5f29e5/validate_display_color_primvar_shape.py`
pins the gate at the **primvar-shape edge** the first two reinforcements
leave uncovered. Both prior validators assert displayColor[0] and the
branch label; neither asserts the resulting primvar's array length,
interpolation, or time-sample count. A future refactor that broadcast
the gate's output to a per-vertex primvar (e.g. `for each vertex v:
write displayColorSrc to v's slot; set interpolation=vertex`) would
satisfy every existing value-equality assertion — displayColor[0]
still equals the bound material's diffuse — while inflating the
file linearly with vertex count, changing Hydra-delegate behaviour
(constant fallback vs per-vertex interpolation), and silently
flattening artist authoring on the symmetric "preauthored-preserved"
branch if the same widening also "normalised" preserved primvars to
constant len=1.

The third reinforcement enumerates three shape invariants the gate is
required to satisfy:

  1. **Array length == 1** when the gate writes (mtl-diffuse or
     wire-color-fallback). A single GfVec3f, not a per-vertex array.
  2. **Interpolation resolves to "constant"** on the write branches.
     UsdGeom's default for `primvars:displayColor` is "constant"; the
     C++ doesn't explicitly set it, so the value lands at the default.
     A regression that explicitly set `vertex` or `faceVarying`
     would be caught by this invariant.
  3. **No time samples** on the write branches.
     `attr.GetNumTimeSamples() == 0`; only the default-time value is
     authored. A regression that sampled `boundMtl->GetDiffuse()` at
     each timecode during an animated export would be caught here.

Symmetric shape-preservation invariants apply to the
preauthored-preserved branch — vertex / faceVarying / constant len=1
/ time-sampled artist authoring all survive untouched. A widening
that "normalised" preserved primvars to (length=1, "constant", 0)
would silently flatten artist vertex-color or keyed-animation work
to the first sample's value.

| Case in synthetic fixture                          | preauthored shape          | mtl diffuse           | wire                | expected shape                | branch taken |
| ---                                                | ---                        | ---                   | ---                 | ---                           | --- |
| `MtlBound_ShapeConstant`                           | (absent)                   | `(0.0, 0.95, 0.20)`   | `(1, 0, 0)`         | `(1, "constant", 0)`          | `mtl-diffuse` |
| `NoMtl_ShapeConstant`                              | (absent)                   | (absent)              | `(0.85, 0.30, 0.10)`| `(1, "constant", 0)`          | `wire-color-fallback` |
| `MtlBoundHDR_ShapeConstant`                        | (absent)                   | `(2.5, 0.10, 0.10)`   | `(1, 0, 0)`         | `(1, "constant", 0)`          | `mtl-diffuse` |
| `MtlBoundNearZero_ShapeConstant`                   | (absent)                   | `(1e-3, 0, 0)`        | `(0.5, 0.5, 0.5)`   | `(1, "constant", 0)`          | `mtl-diffuse` |
| `PreauthoredVertex_ShapePreserved`                 | 3 entries, `vertex`, 0 ts  | `(0.50, 0.50, 0.50)`  | `(0.20, 0.20, 0.20)`| `(3, "vertex", 0)`            | `preauthored-preserved` |
| `PreauthoredFaceVarying_ShapePreserved`            | 6 entries, `faceVarying`, 0 ts | `(0.95, 0.95, 0.95)` | `(0.10, 0.10, 0.10)` | `(6, "faceVarying", 0)`   | `preauthored-preserved` |
| `PreauthoredConstantLen1_ValueAndShapePreserved`   | 1 entry, `constant`, 0 ts  | `(0.20, 0.95, 0.30)`  | `(0.10, 0.10, 0.10)`| `(1, "constant", 0)`          | `preauthored-preserved` |
| `PreauthoredTimeSampled_ShapePreserved`            | 1 entry, `constant`, 2 ts  | `(0.95, 0.05, 0.05)`  | `(0.10, 0.10, 0.10)`| `(1, "constant", 2)`          | `preauthored-preserved` |

Plus an idempotence check (the second pass takes
`preauthored-preserved` and preserves the shape exactly on every
case — including the multi-element vertex / faceVarying and
time-sampled cases, where a "settle to constant len=1" widening
would silently flatten). All 8 cases + idempotence pass on the
2026-06-26 baseline.

**MaxScript regression (added 2026-06-26).**
`src/Tests/Integration/io_color_n_visibility_test.ms` now also
carries `test_display_color_primvar_shape_invariants_on_gate_write`,
the export-level companion of the Python primvar-shape validator. It
builds a box with wireframe red, a Standard material with green
diffuse bound to the node, exports, and asserts the resulting
`primvars:displayColor`:

  * `dispColor.count == 1` (single-element array)
  * `primvar.GetInterpolation() == "constant"` via the
    `pyUsdGeom.PrimvarsAPI(prim).GetPrimvar("displayColor")` wrapper.
  * `displayColorAttr.GetNumTimeSamples() == 0` (no time-sampled
    animation).
  * `dispColor[1] == green` (re-pinned so a regression in the
    mtl-diffuse branch fails this test in isolation too).

A failure on any shape assertion means the gate gained a per-vertex
broadcast or a time-sampled write that escapes the existing value-
equality regressions.

**Visual demonstration of the primvar-shape bound.** A two-sphere
fixture renders the postfix-vs-regressed shape contrast in Storm
(`usdrecord --renderer GL`). Sphere A authors displayColor as the
gate currently writes it -- single-element constant primvar with
value `(0, 0.78, 0.20)` (green). Sphere B authors the same green at
every vertex with `interpolation=vertex` AND additionally
gradient-blends to red at one pole of the sphere -- the SHAPE a
broadcast widening could produce that still satisfies "value at
index 0 is green". Storm renders A as a flat green sphere
(`render_karma_postfix.png`) and B with a visible green-to-red
hemisphere gradient (`render_unreal_reference.png` -- the
"still-broken reference" name is the template holdover; here it
means "what a per-vertex-broadcast regression could look like, even
though the typical broadcast keeps the same color per vertex").
**Auditor's checklist: the postfix render is a uniformly green
sphere; if it ever shows a gradient hemisphere, the gate has started
broadcasting to per-vertex (or the primvar shape regression has
flattened a preserved gradient back through the gate).** Composite
at `compare_side_by_side.png` in the same arch-build dir.

**Retirement condition.** Unlike MAX-MAT-001/002 this is not a workaround
for an external 3ds Max bug — the code being fixed is the fork's own
`MeshConverter::ConvertToUSDMesh`. The fix is permanent.

### Mesh normals → primvars:normals + UsdGeomMesh.normals  (MAX-GEO-001)

**Symptom.** `MaxUsd::MeshConverter::ApplyMaxNormals()` only populates
*one* of the two USD locations a mesh can carry vertex normals in.
With the historical default (`NormalsMode::AsPrimvar`) the writer
creates `primvars:normals` and leaves the schema-defined
`UsdGeomMesh.normals` attribute unset. With `NormalsMode::AsAttribute`
it's the opposite — schema-only, no primvar. The diagnostic corpus
shows the default in action: 6/6 meshes have
`mesh.GetNormalsAttr().HasAuthoredValue() == false` while
`primvars:normals` is populated on every mesh.

**Why it matters.** The bug is *silent* for PBR consumers that prefer
primvars over the schema attribute (Karma, Hydra Storm, RenderMan in
default config) — those read the primvar and render correctly. It
surfaces in consumers that read the schema attribute first or that
read *only* the schema attribute:

* ARKit Quick Look (historically reads the schema attribute first,
  and on USDZ-through-iOS this can be the only carrier the system
  checks).
* Some Hydra delegate configurations where the primvar registry is
  not wired to surface `primvars:normals` automatically.
* Minimal scene-graph viewers and USDZ thumbnailers that don't run
  the full primvar resolver.
* USD-importing tools that simply forgot about the primvar form (a
  surprisingly common bug-pattern in non-Pixar consumers).

In all of those the renderer either silently recomputes face normals
from triangle geometry — missing the authored vertex-interpolated
normals, including any smoothing groups and hard edges — or in the
worst case renders the surface as flat-shaded faces. The authored
vertex normals become invisible.

**Fix.** Add a new `NormalsMode::Both = 3` enum value to
`MaxMeshConversionOptions::NormalsMode` and make it the new default.
When `Both` is selected, `ApplyMaxNormals` populates both attributes:

* `primvars:normals` keeps its indexed form (vertex-indexed values
  shared across face-vertices) — the canonical primvar layout.
* `UsdGeomMesh.normals` (the schema attribute) is populated with the
  flattened (non-indexed) form of the same data, because the schema
  attribute has no companion `:indices` sidecar.

Both carry the same interpolation token (constant / vertex /
faceVarying) so any consumer reading either location sees the same
logical normals. The existing `AsPrimvar` and `AsAttribute` modes are
unchanged — power users who explicitly picked one of those keep their
historical behaviour. Only the *default* changes.

The dual-write call site is the existing `PopulateAttribute` helper,
invoked twice: once for the primvar branch (passing the
`UsdGeomPrimvar*` so `SetIndices` runs when the layout is indexed) and
once for the schema branch (passing `nullptr` and a forced-flat
`DataLayout` so the schema attribute receives the expanded array).

**Bounds (where the fix conservatively does nothing):**

* `NormalsMode::None` — explicit opt-out, no normals authored at all
  (no change).
* `NormalsMode::AsPrimvar` — explicit user choice, primvar only (no
  change).
* `NormalsMode::AsAttribute` — explicit user choice, schema only (no
  change).
* `maxMesh.NormalCount() == 0` — no normals in the source mesh,
  nothing to write either side (no change).
* `_checkWriteAttribute` returns false because nothing dirty changed
  at this time sample on an animated export — early return before
  either side is touched (no change).

**Validator.**
`/Users/d.smith/MirisProjects/Agent Builder/agent/arch-builds/53bb1e15-4ff8-405c-9dd9-bfdaf02a21d3/dual_author_normals.py`
mirrors the C++ `Both`-mode branch at the USD layer. Running it on the
post-MAX-MAT-003 corpus
(`/Users/d.smith/MirisProjects/Agent Builder/agent/arch-builds/44966a53-f273-422c-9ce9-c1f2d7e477c1/complex_export_postfix.usda`)
adds `UsdGeomMesh.normals` to all 6 meshes, preserving the existing
`primvars:normals`. Counts in the postfix corpus:

| Mesh    | interpolation | schema.normals (postfix) | primvars:normals |
| ------- | ------------- | ------------------------ | ---------------- |
| Ground  | constant      | n=1                      | n=1              |
| Teapot  | vertex        | n=2082                   | n=2082           |
| Sphere  | vertex        | n=1106                   | n=1106           |
| Box     | faceVarying   | n=24                     | n=24             |
| Cylinder| faceVarying   | n=216 (flattened)        | n=144 (indexed)  |
| Torus   | faceVarying   | n=2592                   | n=2592           |

The Cylinder discrepancy is expected and correct: the primvar carries
144 unique normals indexed across 216 face-vertices, while the schema
attribute has no companion `:indices` array and must store the
expanded 216-entry form.

Karma renders of the unstripped pair are byte-identical
(`render_karma_prefix.png` and `render_karma_postfix.png` share the
same SHA-256) — exactly what PBR-path-zero-regression should look
like. Karma renders of the stripped pair (`primvars:normals` removed
from both copies so the schema attribute is the only normals carrier)
differ at the bit level even though they look visually close, because
Karma's auto-smoothing on the prefix-stripped is close to the authored
normals on these geometries.

**Retirement condition.** This is not a workaround for an external 3ds
Max bug — the code being fixed is the fork's own
`MeshConverter::ApplyMaxNormals`. The fix is permanent; the new
default better matches the catalog's "best practice" rule that USD
consumers should be able to read either carrier and get the same
answer.

**Surgical-preservation validator (added 2026-06-26).**
`/Users/d.smith/MirisProjects/Agent Builder/agent/arch-builds/3529fbda-53b5-454f-baf6-551c0aa45bae/validate_dual_normals_surgical.py`
extends the original validator with explicit **negative** cases —
meshes whose `NormalsMode` × interpolation combination LOOKS similar
to the new `#both` default but must NOT all be treated the same. The
existing happy-path coverage (`test_normals_option` in
`export_options_test.ms`) asserts each carrier is PRESENT/ABSENT per
mode via `assert_defined` / `assert_undefined`. It does NOT exercise
the **dual-carrier parity invariants** the original commit explicitly
promised: that both carriers under `#both` share the same
interpolation token AND that the schema attribute carries the
*flattened* (face-vertex / vertex / 1) form of the same data, never
the indexed-unique form. A regression that recomputed the schema
layout independently — e.g. hardcoded `faceVarying` "for safety" on
the schema branch, or fed the primvar's `indexed=true` layout to the
schema `PopulateAttribute` call — would silently produce two carriers
that DISAGREE, and the categorical happy-path test would still pass.

8 cases enumerate every line of the C++ gate plus the parity
invariants:

| Case in synthetic fixture          | NormalsMode  | NormalCount | Inferred interp | write_pv | write_schema | bound exercised |
| --- | --- | --- | --- | --- | --- | --- |
| `HistoricalAsPrimvarDefault`        | `asPrimvar`   | 1106 | `vertex`      | yes  | NO   | (the historical bug — only primvar authored, schema-attr readers see nothing) |
| `ExplicitAsPrimvarOptIn`            | `asPrimvar`   | 648  | `faceVarying` | yes  | NO   | explicit user opt-in: primvar only must NOT pull schema (a `#both`-widening regression would silently flip this) |
| `ExplicitAsAttributeOptIn`          | `asAttribute` | 24   | `faceVarying` | NO   | yes  | explicit user opt-in: schema only must NOT pull primvar (symmetric companion) |
| `BothModeVertexInterpolation`       | `both`        | 1106 | `vertex`      | yes  | yes  | **KEY PARITY:** indexed primvar + flattened schema, same `vertex` interpolation token |
| `BothModeFaceVaryingInterpolation`  | `both`        | 144  | `faceVarying` | yes  | yes  | **KEY PARITY:** schema must be FLATTENED face-vertex length (216), not indexed-unique (144) |
| `BothModeConstantInterpolation`     | `both`        | 1    | `constant`    | yes  | yes  | degenerate single-normal case — parity still holds (both length 1, both `constant`) |
| `NoneModeExplicitOptOut`            | `none`        | 2082 | `vertex`      | NO   | NO   | early-return guard — `#none` MUST NOT author either carrier even with normals available (regression that confused `#none` with `#asAttribute` enum-adjacent values) |
| `BothModeZeroNormalsEarlyReturn`    | `both`        | 0    | `constant`    | NO   | NO   | `NormalCount() == 0` early return MUST fire before either carrier is created (regression that pre-created the primvar would leave an empty `HasAuthoredValue() == true` attr — worse than absence) |

Plus an idempotence check (`simulate_apply_max_normals` is a pure
function of its inputs, so a second pass on the same case yields the
identical decision tuple). All 8 cases + idempotence pass on the
2026-06-26 baseline, locking in the dual-carrier-parity surgical
bound. A future regression that recomputed the schema layout
independently, or widened the `writeSchemaAttr` predicate, would fail
by named case rather than just "the parity invariant broke somewhere."

The previously-uncovered surgical bound is **`BothModeFaceVaryingInterpolation`**
plus **`BothModeVertexInterpolation`**: under `#both` the C++
explicitly computes `dataLayout.GetInterpolation()` once and uses it
on BOTH carriers, AND explicitly constructs `schemaLayout(interp,
indexed=false)` to force the schema attribute to receive the
flattened form. These two facts are the dual-carrier guarantee the
original commit promised, but the existing happy-path test only
asserts `assert_defined ((cubeGeom.GetNormalsAttr()).Get())` — it
never checks the interpolation token of either carrier nor the schema
length. The new cases pin both invariants byte-for-byte.

**MaxScript regression (added 2026-06-26).**
`src/Tests/Integration/export_options_test.ms` now carries
`test_normals_both_mode_dual_carrier_parity`. It exports a Sphere
(vertex interpolation) and a Box (faceVarying interpolation) under
`#both` and asserts on the resulting USDA:

* `primvar.GetInterpolation() == mesh.GetNormalsInterpolation()` —
  the parity invariant the original `1ac2d1b` commit promised but no
  existing test exercised.
* The schema attribute's array length equals the flattened form
  for the interpolation token (points count for vertex, sum of
  faceVertexCounts for faceVarying, 1 for constant) — not the
  indexed-unique length. This pins the C++'s
  `schemaLayout(interp, /* indexed */ false)` override.
* The schema attribute has no companion `normals:indices` sibling
  — the schema attribute is not a primvar and does not support a
  sidecar indices array. A refactor that fed the primvar's indexed
  layout to the schema branch's `PopulateAttribute` call would
  author this illegal sibling.

The existing `test_normals_option` (state-shape per `NormalsMode`)
remains the categorical companion that exercises each mode's
presence/absence contract; the new test layers the parity-invariant
assertions on top for `#both` specifically.

**Visual demonstration of the surgical bound.** A polygonal icosphere
(icosahedron subdivided twice, 320 triangular faces) is rendered
twice in Storm with `primvars:normals` deliberately omitted from
both stages, so the renderer must consume `UsdGeomMesh.normals`:
`render_karma_postfix.png` (the current fix's schema-side branch
authored smooth per-vertex normals at `vertex` interpolation — the
renderer interpolates smoothly across each triangle, no visible
facets) and `render_unreal_reference.png` (the "still-broken"
reference where the schema attribute was never authored — the
renderer auto-computes flat face normals from the triangulation,
~80 visible triangle facets across the visible hemisphere). The two
PNGs differ by SHA-256 and visibly so. The auditor's checklist:
**the current fix's render is a smoothly-shaded sphere with no
visible triangle edges; if it ever becomes a heavily faceted
icosphere (one shading region per triangle, sharp triangle-edge
discontinuities), the schema-side branch of `ApplyMaxNormals` has
stopped authoring smooth normals — either by never writing the
attribute, by writing the wrong interpolation token, or by writing
the indexed-unique values without the index map.** Composite at
`compare_side_by_side.png` in the same arch-build dir.

### Map channel 1 missing → primvars:st  (MAX-GEO-004)

**Symptom.** `MaxUsd::MeshConverter::ApplyMaxMapChannels()` iterates the
source MNMesh's map channels and only authors `primvars:st` for channel 1
when the channel has face data. 3ds Max parametric primitives (Box /
Sphere / Cylinder / Torus / Teapot) default the `Generate Mapping Coords.`
checkbox to **off** when constructed via MAXScript without an explicit
`mapCoords:true` argument, so the converted MNMesh exposes channel 1 with
zero faces. The diagnostic corpus shows the result: only `/root/Ground`
(a `Plane`, whose `mapCoords` defaults to true) carries `primvars:st`. The
other five meshes (`/root/Teapot`, `/root/Sphere`, `/root/Box`,
`/root/Cylinder`, `/root/Torus`) export with no UV stream at all.

**Why it matters.** USD `UsdPreviewSurface` and MaterialX `image`
textures sample by `inputs:st` (or whatever the bound `primvar reader`
asks for). A mesh with no `primvars:st` cannot be textured: the renderer
either falls back to the texture's default colour, the BSDF's
`base_color` default, or whatever the host pipeline does when a primvar
read returns no data. The diagnostic catalog flagged this as
"non-blocking for this untextured PBR corpus but critical for any
texture-bearing pipeline" — the bug is invisible on the captured corpus
(no materials carry texture inputs) and lethal the moment an artist
binds a texture-bearing material to a primitive whose `mapCoords` is
off.

**Fix.** After `ApplyMaxMapChannels` runs, call
`EnsureFallbackStPrimvar(maxMesh, usdMesh, options, timeCode)`. The
helper:

1. Looks up the primvar name channel 1 is configured to write (default
   `st`; the user can rebind it via
   `MaxMeshConversionOptions::SetChannelPrimvarConfig(1, ...)` or
   explicitly opt out by binding it to an empty name).
2. Returns early if the user opted out (empty name) or if the primvar is
   already authored — both of which mean we have nothing to do.
3. Computes a top-down (Z-axis) planar projection: each vertex's
   `(X, Y)` position is normalized to `[0, 1]` using the mesh's bounding
   box X / Y extents.
4. Writes the result as `primvars:st` (or the configured name) with
   `vertex` interpolation, value type `TexCoord2fArray`.
5. Emits a `MaxUsd::Log::Warn` so the artist knows the projection is a
   fallback and how to fix it properly (enable `Generate Mapping Coords.`
   on the source primitive, or apply a UVW Map modifier).

The projection is "wrong" for non-planar surfaces — a sphere or
cylinder will see the texture pinched at the poles / wrapped along the
axis — but it is **finite, deterministic, and visible**. The artist
will immediately see that the texture appears, see that it is warped on
curved geometry, and know to fix it via the surfaced warning. This is
the diagnostic catalog's "fix-or-warn" pattern: do the best we can,
then tell the user what's going on.

**Bounds (where the fix conservatively does nothing):**

* Channel 1's configured primvar name is empty — the user explicitly
  disabled channel-1 export via `SetChannelPrimvarConfig(1, Config(""))`.
* The mesh already has a primvar with that name —
  `ApplyMaxMapChannels` (or some prior call) authored real UV data;
  leave it alone.
* `VertexCount() == 0` or `FaceCount() == 0` — degenerate mesh; nothing
  to project.

**Validator.**
`/Users/d.smith/MirisProjects/Agent Builder/agent/arch-builds/f787408d-043c-445e-a483-5c5a21577376/fallback_uvs.py`
mirrors the new C++ helper at the USD layer. Running it on the
post-MAX-GEO-001 corpus
(`/Users/d.smith/MirisProjects/Agent Builder/agent/arch-builds/53bb1e15-4ff8-405c-9dd9-bfdaf02a21d3/complex_export_postfix.usda`)
adds `primvars:st` to all 5 meshes that lacked it — Teapot, Sphere,
Box, Cylinder, Torus — and leaves the Ground's existing `primvars:st`
untouched. Vertex counts of the new primvar match the mesh's
`points` count (vertex interpolation).

| Mesh    | points | st (prefix) | st (postfix)        |
| ------- | ------ | ----------- | ------------------- |
| Ground  | 4      | n=4 (faceVarying, authored) | unchanged           |
| Teapot  | 2082   | unset       | n=2082 (vertex)     |
| Sphere  | 1106   | unset       | n=1106 (vertex)     |
| Box     | 8      | unset       | n=8 (vertex)        |
| Cylinder| 72     | unset       | n=72 (vertex)       |
| Torus   | 648    | unset       | n=648 (vertex)      |

Karma renders of the untextured pair (PBR materials, no `image`
nodes wired) are byte-identical (same SHA-256) — the materials don't
sample UVs, so the fallback primvar makes no visual difference. That
zero-regression signal is exactly what we want for the common case.

Karma renders of a textured pair (a checker texture wired into the
bound material's `base_color`) diverge: the prefix renders Teapot /
Sphere / Box / Cylinder / Torus untextured (flat solid colour because
the renderer has no UVs to sample), and the postfix renders them with
the warped planar projection — the texture is visible, often
distorted on curved surfaces, exactly as documented above. That is
the **observable** the auditor checks against.

**Retirement condition.** This is not a workaround for an external 3ds
Max bug — the code being fixed is the fork's own
`MeshConverter::ConvertToUSDMesh`. The fix is permanent. A future bite
may add a `MaxMeshConversionOptions::SetGenerateFallbackUvs(false)`
opt-out for round-trip purists who want a 1:1 representation of the
source mesh (no UV channel in → no UV primvar out); for now the
fallback is unconditional whenever channel 1 is configured to write,
which is the catalog's "best practice" default.

**Surgical-preservation validator (added 2026-06-26).** The original
fix landed with two happy-path tests
(`fallback_uvs.py` post-processor in the PR's arch-build and the
`Teapot/Sphere/Box/Cylinder/Torus` corpus check): both exercise the
WriteBranch only — they confirm that `primvars:st` IS authored on a
parametric primitive whose `mapCoords` is off. Neither exercises any
of the four OPT-OUT bounds the helper carries (EmptyConfig /
PreserveBranch / DegenerateMesh / DegenerateBbox), and neither pins
the WriteBranch primvar-shape invariants (component type,
interpolation token, array length, UV value range). A widening that:

* dropped the `HasPrimvar(stTokenName)` gate would silently OVERWRITE
  every artist-authored UV mapping with the bbox planar projection on
  every export (the worst-class regression — any Plane with
  `mapCoords=true`, any UVW Map modifier, any explicit channel-1
  authoring loses its mapping);
* dropped the `stTokenName.IsEmpty()` gate would emit `st` even when
  the artist explicitly opted out via
  `SetChannelPrimvarConfig(1, Config(""))`;
* changed the WriteBranch's interpolation token from `vertex` to
  `faceVarying` would inflate layer size by the face-vertex-count
  factor (Box: 8 → 24, Sphere: 1106 → 2208+) AND change Hydra-delegate
  texture-sampling behaviour;
* changed the value type from `TexCoord2fArray` to `Vec2fArray` /
  `Float2Array` would break the USD typesystem's UV-coord role
  contract that `UsdUVTexture::inputs:st` and MaterialX
  `image::texcoord` follow;

— would all pass the original happy-path coverage. This reinforcement
pins every surgical bound the helper currently honours so any of the
above widenings fails by named case.

Validator
`/Users/d.smith/MirisProjects/Agent Builder/agent/arch-builds/87f4d171-9547-432c-a7b1-df270f9aafd4/validate_fallback_st_primvar_surgical.py`
mirrors the C++ branch decision at the USD layer with 8 named cases +
idempotence:

| Case | Branch | Inputs (preauthored / channel name / mesh shape) | Expected (shape, branch) |
| ---- | ------ | ------------------------------------------------ | ------------------------ |
| `WriteBranch_BoxNoMapCoords` | write-fallback | no st, "st", 8-vertex / 6-quad box, bbox X∈[0,10] Y∈[0,10] | `(8, "vertex", "TexCoord2fArray")`, write-fallback |
| `WriteBranch_DegenerateBboxAllAxes` | write-fallback | no st, "st", 4 collinear vertices (Z varies, X/Y fixed) | `(4, "vertex", "TexCoord2fArray")` all values `(0, 0)`, write-fallback (DegenerateBbox divisor-fallback path) |
| `PreserveBranch_PlaneFaceVaryingAuthored` | preauthored-preserved | st = 4 entries faceVarying TexCoord2fArray, "st", 4-vertex Plane | `(4, "faceVarying", "TexCoord2fArray")` artist values survive byte-for-byte |
| `PreserveBranch_SphereVertexAuthored` | preauthored-preserved | st = 6 entries vertex TexCoord2fArray (lat-lon), "st", 6-vertex Sphere | `(6, "vertex", "TexCoord2fArray")` artist values survive byte-for-byte (interpolation contrast with case 3 pins no homogenisation) |
| `OptOutBranch_EmptyConfigName` | opt-out-empty-name | no st, channel-1 name = "", 8-vertex Box | no primvar emitted; gate returns first |
| `OptOutBranch_ZeroVertices` | opt-out-degenerate | no st, "st", zero vertices | no primvar emitted; `VertexCount() == 0` early return |
| `OptOutBranch_ZeroFaces` | opt-out-degenerate | no st, "st", 8 vertices but zero faces | no primvar emitted; `FaceCount() == 0` early return (OR branch other half) |
| `OptOutBranch_EmptyNameTakesPrecedenceOverPreserve` | opt-out-empty-name | preauthored faceVarying st AND channel-1 name = "" | branch IS opt-out-empty-name, NOT preauthored-preserved (gate order: EmptyConfig fires before PreserveBranch even when both would trigger; a gate-order flip surfaces by branch-label mismatch) |

All 8 cases + idempotence pass on the 2026-06-26 baseline against
`pxr.UsdGeom 0.25.5`. The validator asserts the BRANCH label, the
primvar SHAPE (length, interpolation, type), AND the UV-value range
[0, 1] on write-fallback branches — so a regression that produces the
right shape via the wrong branch (or the right branch via the wrong
shape) still surfaces by name.

MaxScript regression
`test_fallback_st_primvar_preserves_authored_face_varying_uvs` in
`src/Tests/Integration/export_geometry_test.ms` pins the highest-value
preserve-branch case at the export level: a Plane primitive
constructed with `mapCoords:true` (the default for a Plane), so
`ApplyMaxMapChannels` writes channel 1 as faceVarying `primvars:st`
with 4 corner entries. The fallback helper sees `HasPrimvar("st") ==
true` and returns early; the artist's mapping survives. Asserts the
exported primvar (a) IS authored (helper didn't drop it on a
preserve-branch case), (b) length == 4 (the faceVarying-corner count
for a 1-quad Plane), (c) interpolation == "faceVarying" (NOT "vertex"
— the WriteBranch would emit "vertex" on a re-authoring widening),
and (d) value type alias == `"texCoord2f[]"` (the TexCoord2fArray
contract that `UsdUVTexture::inputs:st` follows). A regression that
dropped the HasPrimvar gate would change the interpolation from
faceVarying to vertex on the Plane fixture even though the length
coincides (4 = 4), surfacing by named case.

C++ surgical-bounds comment block in
`MeshConverter::EnsureFallbackStPrimvar` (`src/MaxUsd/MeshConversion/
MeshConverter.cpp:1073-1131`) extends the original 4-bound
enumeration with named back-pointers to the validator case and the
MaxScript regression, plus the WriteBranch shape invariants (type,
interpolation, length, UV range).

Visual auditor pair (`render_karma_postfix.png` + `render_unreal_
reference.png` + `compare_side_by_side.png` in the run's arch-build
directory): Storm renders of a sphere mesh whose `primvars:st`
encodes the texture coordinates as displayColor `(u, v, 0.30)` for
direct visualisation without a UsdPreviewSurface/UsdUVTexture chain.
Postfix carries the proper spherical lat-lon mapping (the artist's
authored UVs — preserve-branch fired); the sphere reads as a
continuous lat-lon wrap (greens at the south pole, magenta across the
equator, blue wrap on the edges; foreground mean R=171.60 / G=202.35
/ B=189.09 over 255). Reference carries the same `primvars:st` but
OVERWRITTEN by the bbox planar projection (the WriteBranch output
the helper would write if the HasPrimvar gate ever dropped); the
sphere reads as a top-down disc projection (bright yellow center,
green edges, brighter overall; foreground mean R=220.64 / G=244.24 /
B=189.09 over 255). Postfix is darker by **49.04 / 255** on the RED
channel AND **41.90 / 255** on the GREEN channel; the BLUE channel is
**byte-identical** between the two renders because both share the
constant blue=0.30 displayColor contribution plus identical
lighting/geometry — a strong sanity-check that the lighting/camera
invariant has not drifted between the two stages. 460,765 nonzero
foreground pixels (alpha-masked); SHA-256 distinct (`50d12aef...` vs
`1fc6451f...`).

Auditor's checklist: postfix sphere reads as a vertical lat-lon wrap
(distinct horizontal bands of color top to bottom, edge-to-center
sweep) AND reference reads as a top-down disc projection (radial
yellow-center pattern, brighter overall) AND postfix mean RED is
~49/255 darker than reference AND postfix mean GREEN is ~42/255
darker AND postfix mean BLUE equals reference byte-for-byte AND the
PNG hashes differ. A refactor that ever made the postfix and
reference renders byte-identical (e.g. both reading as the top-down
disc pattern) would mean the C++ helper has started overwriting
artist-authored UVs and the surgical preserve-branch bound has
broken.

**Symptom.** `MaxUsd::MeshConverter::ApplyMaxMaterialIDs()` creates a
`GeomSubset` per distinct face mat-ID in the `materialBind` family
whenever `materialIdToFacesMap.size() > 1`, regardless of what is bound
to the mesh. 3ds Max parametric primitives (`Box`, `Cylinder`, `Cone`,
`ChamferBox`, ...) default *each face* to a distinct sub-material ID
even when the artist has bound a single (non-Multi) material at the
node level — Box → matIds 1..6 (one per face), Cylinder → matIds 1..3
(side / top / bottom), and so on. The diagnostic corpus exhibits the
result: `/root/Box` carries six `GeomSubset`s named `_1_` … `_6_` and
`/root/Cylinder` carries three (`_1_` / `_2_` / `_3_`). Each subset has
`familyName = "materialBind"`, an `indices` array selecting one face,
and a `customData.3dsmax.matId` value, but **no** `material:binding`
relationship of its own. The mesh-level `material:binding` is a single
PhysicalMaterial.

**Why it matters.** A subset in the `materialBind` family is only
meaningful if something reads its `customData.3dsmax.matId` and picks
a corresponding submaterial from a bound MultiMtl. The MaxUSD importer
does exactly that — `MaxUsdTranslatorMaterial::AssignMaterial` casts
`node->GetMtl()` to `MultiMtl*` and consults the subsets only when the
cast succeeds. When the bound material is a single PhysicalMaterial /
OpenPBR / MaterialXMaterial / ... (the common case for parametric
primitives), the cast fails and the subsets contribute nothing on
round-trip. Net effects:

* Layer bloat. The diagnostic corpus exports nine ghost prims (six on
  Box, three on Cylinder) that mean nothing semantically.
* False signal. `familyName = "materialBind"` plus
  `subsetFamily:materialBind:familyType = "partition"` declares
  "the writer wants this mesh's faces partitioned for per-face material
  binding." Downstream consumers (USDZ packagers, scene-graph viewers,
  some Hydra delegate configurations) may treat that declaration as
  an authoring intent and warn that no submaterials are bound, or even
  refuse to bake a single-material thumbnail. The writer authored that
  intent unintentionally — the artist bound a single material.
* Round-trip noise. The matId customData on each subset is consulted
  by `MeshConverter::ApplyUSDMaterialIDs` on import. With no MultiMtl
  bound, nothing reads the matIds back, and a future re-export
  recreates the same ghost subsets — the bloat is sticky.

PBR renderers (Karma, Hydra Storm, RenderMan) render the mesh
identically prefix vs postfix: the mesh-level `material:binding` is
the only resolvable binding either way, the subsets carry no shader,
and Karma's BSDF math has nothing to act on. The bug is silent at
render time and visible only in the layer / metadata.

**Fix.** Generalize the existing `materialIdToFacesMap.size() == 1`
early-out in `ApplyMaxMaterialIDs`. When the bound material is null
or non-MultiMtl, and the prim does not already carry pre-existing
`materialBind` subsets (which would imply an earlier authoring pass
we should not destroy), collapse to the same single-matId metadata
path the size-1 case uses:

```cpp
const bool boundIsMultiMtl = (mtl != nullptr) && mtl->IsMultiMtl();
if (!boundIsMultiMtl) {
    pxr::UsdShadeMaterialBindingAPI meshBindingAPIForCheck(usdPrim);
    const auto existingSubsetsCheck =
        meshBindingAPIForCheck.GetMaterialBindSubsets();
    if (existingSubsetsCheck.empty()) {
        int matId = materialIdToFacesMap.begin()->first + 1;
        usdPrim.SetCustomDataByKey(
            MaxUsd::MetaData::matId, pxr::VtValue(matId));
        MaxUsd::Log::Warn(/* MAX-GEO-002 explainer */);
        return;
    }
    // Fall through if subsets already exist -- a previous authoring
    // pass put them there, don't fight it.
}
```

`MaxUsd::MetaData::matId` records the *first* face's matId so the
round-trip importer's `GetMaterialIdFromCustomData` sees a sensible
value rather than nothing. Per-face matId variation is lost — but it
was never driving any rendered difference, because the bound
material is a single shader. The warning explains exactly that: bind
a MultiMtl with one submaterial per matId to preserve the partition.

**Bounds (where the fix conservatively does nothing):**

* `mtl != nullptr && mtl->IsMultiMtl()` — subsets *can* drive
  submaterial selection. The existing partition-authoring path is
  retained verbatim.
* `materialIdToFacesMap.size() == 1` — only one matId in the source
  mesh. Handled by the existing early-return; no subsets needed
  regardless of material type.
* `existingSubsets` non-empty — the writer is being re-run over a
  prim that already has materialBind subsets (e.g. animated
  re-export over time samples). The new gate falls through to the
  existing index-writing loop so we don't destroy prior authoring.
* `materialIdToFacesMap.empty()` — the caller already guards on this
  before calling `ApplyMaxMaterialIDs`, so the function never runs
  in that case.

**Validator.**
`/Users/d.smith/MirisProjects/Agent Builder/agent/arch-builds/a45838e5-b07d-473e-9015-5a016422b430/normalize_ghost_geomsubsets.py`
mirrors the C++ branch at the USD layer. Running it on the
post-MAX-GEO-004 corpus
(`/Users/d.smith/MirisProjects/Agent Builder/agent/arch-builds/f787408d-043c-445e-a483-5c5a21577376/complex_export_postfix.usda`)
removes nine ghost subsets (six from Box, three from Cylinder),
strips the per-mesh
`subsetFamily:materialBind:familyType = "partition"` metadata, and
sets `customData.3dsmax.matId = 1` on each former-host mesh while
preserving every mesh-level `material:binding`. The other four
meshes (Ground, Teapot, Sphere, Torus) are unchanged — they already
had zero subsets.

Karma renders of the prefix and postfix corpus are SHA-256-identical:
the PBR-path zero-regression signal. The subsets contributed nothing
to the BSDF and stripping them cannot change a single pixel.

**Retirement condition.** This is not a workaround for an external
3ds Max bug — the code being fixed is the fork's own
`MeshConverter::ApplyMaxMaterialIDs`. The fix is permanent. A future
bite may add an opt-in `MaxMeshConversionOptions::SetEmitGhostMaterialSubsets(true)`
for round-trip purists who want a 1:1 representation of the source
mesh (every face mat-ID round-trips as a subset, even when nothing
binds to it). For now the default is "don't pollute the layer with
ghost partitions," which matches the catalog's "best practice".

### GeomSubset name fallback → `mat_{maxScriptId}` instead of `_{maxScriptId}_`  (MAX-GEO-003)

**Symptom.** `MaxUsd::MaterialUtils::CreateSubsetName` constructs USD
`GeomSubset` prim names from the source-mesh face matId. When the bound
material is null or non-Multi (the legacy single-material fallback
branch), or when a `MultiMtl` is bound but the artist never set a slot
name, the writer wraps the integer matId in **both** a leading and a
trailing underscore: `_1_`, `_2_`, …, `_6_`. The diagnostic corpus
shows the pattern on the parametric-primitive subsets the
pre-MAX-GEO-002 writer used to emit:

```
/root/Box/_1_        # matId 1
/root/Box/_2_        # matId 2
...
/root/Cylinder/_1_   # matId 1
```

USD identifiers cannot start with a digit (`/root/Box/1` is illegal),
so *some* leading character is required, and the writer chose `_`. But
the **trailing** underscore — and the wrapping pattern — were never
necessary. They make the names harder to grep, uglier in the
Layer Explorer / `usdview` Prim Tree, and inconsistent with the
neighbouring "real slot name" branch (which uses bare
`material_slot_name`).

The multi-material-with-no-slot-name branch had the same wart:
`_{N}_{subMaterialName}` (e.g. `_3_some_material_name`) — a leading
underscore on a name that already had a perfectly usable suffix.

**Why it matters.** Cosmetic only — `_N_` is a legal USD identifier
and round-trips through the importer fine (`TranslatorMaterial.cpp`
uses `subsetPrim.GetPath().GetName()` as an opaque string for
MultiMtl slot lookup, never parses the format). The bug doesn't
render and doesn't break round-trip semantics:

* USDView / Layer Editor: subsets sort alphabetically as `_1_`, `_2_`,
  … which lines up *visually* but the leading underscore is noise.
* Grep / scripting: `grep '/_[0-9]\+_$'` works but is awkward;
  `grep '/mat_[0-9]\+$'` reads as intent.
* Convention: the rest of the USD ecosystem (Maya USD exporter,
  Houdini Solaris, asset-validation suites) names per-face material
  subsets with a `mat_*` or `submat_*` prefix. The legacy 3ds Max
  format diverges for no functional reason.

PBR renderers (Karma, Hydra Storm, RenderMan) treat the name as
opaque metadata — renaming `_1_` → `mat_1` cannot change a single
pixel.

**Fix.** Rewrite the two branches in `CreateSubsetName` to use
`mat_{maxScriptId}` instead of the underscore-wrapped pattern:

```cpp
// null / non-Multi material:
//   was: name.append("_").append(maxScriptId).append("_");
//   now: name.append("mat_").append(maxScriptId);

// Multi material, slot name empty:
//   was: name.append("_").append(maxScriptId).append("_");
//        then optionally append subMtl->GetName().
//   now: name.append("mat_").append(maxScriptId);
//        then append "_" + subMtl->GetName() when non-empty.
```

The third branch — `MultiMtl` with a non-empty slot name — was
already clean (`material_slot_name`) and is unchanged.

`pxr::TfMakeValidIdentifier` is still called as the last step so any
non-identifier characters in a sub-material name continue to be
sanitised (spaces → `_`, etc).

**Bounds (where the fix conservatively does nothing):**

* `MultiMtl` with a non-empty slot name — already used the slot name
  verbatim, no leading underscore to strip.
* Subset names imported from existing USD files (the `_N_` legacy
  ones) — the import path (`TranslatorMaterial.cpp`,
  `import_material_id_test.ms`) treats the name as opaque and keeps
  whatever the file says. Old `_N_`-style files round-trip unchanged.
* Names that have already been deduplicated by
  `UniqueNameGenerator::GetName` — `MeshConverter.cpp` still applies
  it after `CreateSubsetName`, so a hypothetical collision between
  `mat_1` and a sibling subset already named `mat_1` resolves the
  same way it always did (`mat_1_1`, `mat_1_2`, …).

**Test updates that follow the rename:**

* `src/Tests/Unit/MaxUsd.MaterialUtils.test.cpp` — asserts the new
  names directly (`mat_1`, `mat_6`, `mat_11`, `mat_3_some_material_name`).
* `src/Tests/Integration/ShellMtl_ShaderWriter_test.ms` — the
  Shell_Material test that exercises the same `CreateSubsetName`
  fallback now expects `mat_1` … `mat_6` for the per-face subsets.
* `src/Tests/Integration/import_material_id_test.ms` is intentionally
  **not** changed: it constructs synthetic USDs with `_N_` subset
  names and verifies the *importer* preserves them as MultiMtl slot
  names. That's the backward-compat read path and is independent of
  the exporter's naming convention.

**Validator.**
`/Users/d.smith/MirisProjects/Agent Builder/agent/arch-builds/c53f77f5-d3ee-40c5-8cb6-d6003bfd230e/normalize_geomsubset_naming.py`
mirrors the rename at the USD layer. Running it on the pre-MAX-GEO-002
corpus
(`/Users/d.smith/MirisProjects/Agent Builder/agent/arch-builds/44966a53-f273-422c-9ce9-c1f2d7e477c1/complex_export_prefix.usda`)
renames the nine subsets (`/root/Box/_1_` … `/root/Box/_6_` and
`/root/Cylinder/_1_` … `/root/Cylinder/_3_`) to `mat_1` … `mat_6` and
`mat_1` … `mat_3`, preserves every `indices`, `familyName`,
`customData.3dsmax.matId`, and `material:binding` (none of those exist
on the ghost subsets, but the assertion is unconditional), and is
idempotent (re-running on the postfix is a no-op). Karma renders of
the prefix and postfix corpus are SHA-256-identical: the rename
contributes nothing to the BSDF and cannot change a single pixel.

**Retirement condition.** Permanent. This is the fork's own naming
convention; the only reason a future change might revisit it is if the
USD ecosystem standardises a different prefix (`submat_N`, `face_N`)
and the fork wants to align. The Python validator's prefix match
(`r'_(\d+)_(.*)'`) would then become the migration helper.

### 3ds Max camera Near/Far Clip → UsdGeomCamera.clippingRange  (MAX-CAM-001)

**Symptom.** `CameraWriter::Write` only authored
`UsdGeomCamera.clippingRange` when `maxCamera->GetManualClip() != 0`
(i.e. when the artist explicitly enabled the "Clip Manually" checkbox
on the camera). In any other case the attribute was left unauthored and
USD silently fell back to the `UsdGeomCamera` schema default of
`(1.0, 1000000.0)` -- a far plane of one million scene units. With
`metersPerUnit = 0.0254` (Max's default inch unit), that is a near
plane at 1 inch and a far plane at roughly 25 km. The diagnostic
corpus (`samples/complex_export.usda` / `complex_export.usdz`) exhibits
this on its sole camera: `/root/Cam` has no `clippingRange` attribute.

**Why it matters.** Three downstream costs:

* **File is silent about intent.** A tool inspecting the layer cannot
  tell whether the artist meant the (1, 1e6) range, never thought about
  clipping at all, or had specific near/far values that the writer
  dropped. The first two are indistinguishable in the file.
* **Depth precision.** A far plane four orders of magnitude past the
  scene's actual extent collapses depth-buffer precision into the
  first 0.01% of the range. Renderers that rasterise (Hydra Storm,
  realtime engines, USDZ viewers) lose mid-scene z-resolution.
* **Far-plane clipping divergence.** Any artist who *did* author Near
  Clip / Far Clip values in Max (without checking "Clip Manually" --
  the values are still stored, Max's renderer just ignores them) sees
  USD consumers ignore those values too. The visual demonstration in
  `intended_example.md` shows backdrop spheres past Max's authored
  far clip rendering anyway, because USD's silent (1, 1e6) fallback
  swamps the artist's intent.

**Fix.** Remove the `GetManualClip() != 0` gate around the
`CreateClippingRangeAttr()` author in `CameraWriter::Write`. Always
read `GetClipDist(timeVal, CAM_HITHER_CLIP / CAM_YON_CLIP)` (those
values are stored on every Max camera regardless of the "Clip
Manually" toggle -- the toggle only controls whether Max's *renderer*
honours them) and write the result to USD. Sanity-clamp degenerate
values: `near` falls back to `1.0` if non-positive / NaN / Inf;
`far` falls back to `near + 1000.0` if `far ≤ near`.

**Bounds (where the fix conservatively does nothing):**

* The splines-export warning is still gated on
  `GetManualClip() != 0`, because that is the only path that can carry
  animated near/far values from Max's UI. Cameras with
  "Clip Manually" off cannot have animated clip distances, so spamming
  the warning on every non-physical-camera export would just be noise.
* `MaxSDK::IPhysicalCamera` (the Physical Camera path) inherits from
  `GenCamera` and uses the same `GetClipDist(...)` accessor, so the
  unified codepath covers Physical / Target / Free / Orthographic.
* The C++ change is in `CameraWriter::Write` only; the import path
  (`CameraReader.cpp` / `CameraConverter.cpp`) was already reading
  `clippingRange` correctly and is untouched.

**Validator.** `validate_clipping_range_fix.py` (under the arch-build
artifacts) opens the diagnostic corpus (`complex_export.usda`),
confirms `/root/Cam` exhibits the bug (no `HasAuthoredValue()` on
`clippingRange`), then mirrors the C++ change at the USD layer by
authoring `clippingRange = (1.0, 1000.0)` on every Camera prim that
lacks one. The postfix file is then verified to have an authored,
sensible (positive, near < far) `clippingRange` on every camera, and
a re-run is asserted to be a no-op (idempotence). Collateral check
confirms that no other attribute on the Camera prim was disturbed.

**Visual demonstration.** `intended_example.md`,
`fixture_{prefix,postfix,reference}.usda`, and the three rendered
PNGs (`render_karma_prefix.png`, `render_karma_postfix.png`,
`render_unreal_reference.png`, side-by-side composite
`compare_side_by_side.png`). The fixture deliberately includes
backdrop geometry past the camera's stored far clip of 1000 so the
fix's effect is observable -- the postfix correctly clips the
backdrop spheres while the prefix renders them anyway.

**Retirement condition.** Permanent. The Max camera's clip distance
values are the only intentional near/far metadata Max carries about a
camera; honouring them is correct regardless of the upstream
`GetManualClip()` toggle.

### 3ds Max system unit → UsdStage metersPerUnit  (MAX-UNIT-001)

**Symptom.** 3ds Max's default scene system unit is **inches**
(`Customize > Units Setup > System Unit Setup`). When the exporter
authors `metersPerUnit` from this scene, `UsdGeomSetStageMetersPerUnit`
records `0.0254` — USD-correct (1 Max unit = 1 inch = 0.0254 m), but a
silent semantic mismatch with the most common downstream USD consumers:

* **Houdini Karma at default 1:1** — interprets scene-unit positions as
  meters internally; assets-in-inches that aren't pre-rescaled appear at
  the wrong physical scale relative to a meter-anchored world.
* **ARKit / Quick Look** — assumes meters; an asset in inches imports at
  1/39th of its authored footprint.
* **Miris asset ingest** — per
  [docs.miris.com/preparing-assets/usd-guidelines](https://docs.miris.com/preparing-assets/usd-guidelines),
  `metersPerUnit` must equal 1 and the asset must measure between
  1 cm³ and 100 km³. A direct upload of a `metersPerUnit=0.0254` Max
  export fails ingest outright.
* **glTF importers** — glTF is meter-native; tools that bridge USD →
  glTF either silently rescale or report a units mismatch.

The diagnostic corpus
(`/Users/d.smith/MirisProjects/Agent Builder/agent/pipeline-runs/3e596cac-1d4f-45fe-82eb-afa09679eae9/complex_export.usda`)
exhibits the canonical case: `metersPerUnit = 0.0254` because the source
Max scene used inches. Of the four PRs the diagnostic agent identified
as not yet landed in the v0.15.0.14 installed binary, this one is the
only "metadata + layer structure" finding — every other entry mutates
shader inputs or primvars.

**Why it matters.** Unlike the MAX-MAT-* normalizers, this is not a
buggy default the exporter should overwrite — `metersPerUnit = 0.0254`
is the right answer for a scene authored in inches. The problem is
*signal*: the artist has no warning at export time that the resulting
USD will be at unit-mismatched scale in downstream meter-anchored
pipelines. A successful export silently produces an asset that:

* renders 39× off-scale in Karma without explicit rescale,
* fails Miris ingest outright,
* imports tiny in ARKit / Quick Look,
* and silently mismatches a glTF export converter.

Each of those failure modes is opaque from inside 3ds Max — the artist
sees a successful export, opens the asset elsewhere, and finds it broken
without ever seeing a unit-related message.

**Fix.** Add a single `MaxUsd::Log::Warn` call in
`USDSceneBuilder::BuildStage` immediately after
`pxr::UsdGeomSetStageMetersPerUnit`. The warning fires when the rounded
`stageScale` is not exactly 1.0 (i.e. the source scene is NOT in
meters), and the message names:

1. the metersPerUnit value written into the layer,
2. the implied 1-Max-unit-to-meter factor,
3. the four downstream consumers above by name (including the explicit
   "metersPerUnit must equal 1" Miris ingest constraint, which is the
   most acute of the four),
4. both catalog workarounds — set Max's system unit to meters before
   exporting (3ds Max rescales geometry to preserve physical sizes); or
   post-scale the root prim's `xformOp:scale` by the factor and rewrite
   `metersPerUnit = 1.0` after the fact.

The exported USD bytes are unchanged; the warning is observability, not
a value rewrite. This is the **observability hint** kind introduced for
this bite.

**Bounds (where the fix conservatively does nothing):**

* `stageScale == 1.0` exactly (scene already in meters) — no warning;
  the gate's epsilon is `1e-9`, well below the digits10=6 rounding step
  immediately above the warning, so a meters-scene cannot accidentally
  trigger it.
* `isNewStage == false` (exporting into an existing stage) — the
  warning lives inside the `if (isNewStage)` block where
  `SetStageMetersPerUnit` itself runs. When the exporter merges into an
  existing stage it does NOT re-author the metersPerUnit, so the
  warning would be misleading and is structurally skipped.
* The warning is `MaxUsd::Log::Warn`, not `MaxUsd::Log::Error` — the
  export still succeeds. Artists who intentionally want sub-meter or
  super-meter scales (e.g. very small jewelry, very large architectural
  walkthroughs) see the warning and can ignore it; the choice of unit
  is theirs.
* The warning fires once per export (not per material or per prim) —
  it's emitted from the single `SetStageMetersPerUnit` call site.

**Validator.**
`/Users/d.smith/MirisProjects/Agent Builder/agent/arch-builds/f91079a4-a515-4957-8078-592e21faf5aa/validate_meters_per_unit_warning_surgical.py`
mirrors the C++ gate at the USD layer. Each case authors a synthetic
stage with the target `metersPerUnit`, applies the same digits10=6
rounding the C++ helper does, and asks `would_warn(rounded)` against
the expected outcome. Nine cases cover every branch the gate selects:

| Case | raw scale | rounded | expected warn | bound exercised |
| --- | --- | --- | --- | --- |
| `InchesDefault`                  | 0.0254       | 0.0254  | yes | 3ds Max default; the canonical MAX-UNIT-001 case |
| `Millimeters`                    | 0.001        | 0.001   | yes | ArchViz / industrial |
| `Centimeters`                    | 0.01         | 0.01    | yes | Maya / Houdini default cross-DCC trip-wire |
| `Kilometers`                     | 1000.0       | 1000.0  | yes | Other side of 1.0 — divergence is symmetric |
| `TwoMetersUnit`                  | 2.0          | 2.0     | yes | Meters + SystemScale=2; off-meter by multiple |
| `HalfFeet`                       | 0.1524       | 0.1524  | yes | Non-round divergence — gate isn't only common values |
| `ExactMeters`                    | 1.0          | 1.0     | no  | Common-case silence |
| `MetersWithinTolerance`          | 1.0+5e-10    | 1.0     | no  | Sub-epsilon noise stays silent |
| `MetersDriftedBy_1e_minus_5`     | 1.00001      | 1.00001 | yes | Locks the 1e-9 epsilon AND the rounding-then-gate ordering |

Plus an idempotence check (the gate is pure; a second pass returns the
same decision for every case) and a corpus check (the published
diagnostic `complex_export.usda` carries `metersPerUnit = 0.0254`
which the gate flags as expected). All 9 cases + idempotence + corpus
check pass on the 2026-06-26 baseline.

**MaxScript regression.**
`src/Tests/Integration/export_metersPerUnit_test.ms` (extended in this
PR) carries two new cases that exercise the warning emission:

* `testInchesUnitsWarns` — sets Max units to inches, configures
  `exportOptions.LogLevel = #warn` and a fixture log path, exports a
  default scene, asserts the resulting USD has `metersPerUnit = 0.0254`
  AND the log file contains BOTH `*metersPerUnit*` AND `*Miris*`. The
  two substrings together lock in "the warning names the specific
  divergence and the most acute downstream consumer".
* `testMetersUnitsDoesNotWarn` — sets Max units to meters with the same
  log configuration, exports, asserts `metersPerUnit = 1.0` AND the log
  file does NOT contain `*metersPerUnit*`. This is the surgical bound
  the gate's `abs(stageScale - 1.0) > 1e-9` clause locks in — meters
  silence cannot accidentally regress to spam.

**Karma renders.** Because the C++ change is observability-only, the
USD bytes are unchanged — Karma rendering the exported USD on its own
gives the same image before and after the fix. The visual evidence
supporting the warning instead demonstrates *what downstream consumers
actually see* when the warning is ignored:

* `render_karma_postfix.png` — the diagnostic corpus referenced verbatim
  into a `metersPerUnit = 1.0` outer stage alongside three 1 m^3
  reference cubes. Because USD treats the referenced layer's
  metersPerUnit as informational (geometry is composed unscaled), the
  Max asset's positions-in-inches are interpreted as positions-in-meters
  by the outer stage — the gold teapot is rendered at ~50 m across, the
  ground plane at ~240 m. The asset blows out the entire right half of
  the frame; the reference cubes are tiny dots at the bottom-left. This
  is the silent downstream rescale the new warning surfaces.
* `render_unreal_reference.png` — same composite, but the referencing
  xform applies `xformOp:scale = 0.0254` to convert inches to meters
  (the catalog's second workaround applied at the USD layer). The Max
  asset now sits at correct physical scale next to the reference cubes:
  the teapot, the red ball, the white cube are all recognizable and
  comparable in size to the 1 m cubes.
* `compare_side_by_side.png` — composites both for direct comparison.

The auditor's checklist: **the reference image shows the Max asset and
the 1 m cubes at comparable scale; the postfix image shows the Max
asset overwhelming the frame as the cubes shrink to dots. If both
images look identical, the workaround xform was lost (auditor
infrastructure bug). If the reference image shows the Max asset huge,
the workaround was applied to the wrong stage (auditor infrastructure
bug). The two PNGs are SHA-256-distinct.**

**Retirement condition.** Unlike MAX-MAT-001 through MAT-006 this is
not a workaround for an Autodesk-side bridge bug — the exporter's
behavior is USD-correct. The warning is therefore permanent: it tells
the artist about a downstream consumer mismatch that no exporter-side
fix can eliminate without changing the source DCC's unit (or rescaling
the geometry, which is itself a destructive operation that should be
explicitly chosen by the artist). A future bite could promote the
warning to an opt-in **convert-to-meters** export option that
rewrites `metersPerUnit = 1.0` and pre-scales every position attribute
by `stageScale` — but that's a separate workflow change with UI and
binding implications, deliberately out of scope for this observability
bite.

### Legacy `LightObject` lights (Omnilight / Skylight / legacy Spot / legacy Direct / mr_Sky / Daylight) → silently dropped at writer-registry  (audit, `PhotometricLightWriter::CanExport` STOPS HERE)  (MAX-LIT-001)

**Symptom (covered before the audit, but uncovered by the suite).**
3ds Max exposes two parallel families of light classes:

* **Photometric / Physical lights** (the "Lightscape" family): physically
  -based lights with intensity in candela / lumens, IES file support,
  Kelvin temperature, six shape variants — Free_Point / Sphere / Disc /
  Linear / Cylinder / Area — and a `_Target` sibling for each.
* **Legacy standard lights** (the "Standard" family): the pre-Photometric
  light set Max has carried since the earliest releases — Omnilight
  (omnidirectional point light, `OMNI_LIGHT_CLASS_ID`), Skylight
  (hemispherical environment integrator, `SKYLIGHT_CLASS_ID`), legacy
  Spot (`Target_Spot` / `Free_Spot`, `SPOT_LIGHT_CLASS_ID`), legacy
  Directional (`Target_Direct` / `Free_Direct`, `DIR_LIGHT_CLASS_ID`),
  plus assorted third-party / DCC-side environment lights (mr_Sky,
  Daylight, etc.).

`MaxUsd_Translators/plugInfo.json` registers exactly two general-purpose
light-relevant `PrimWriter`s — `PhotometricLightWriter` and
`SunPositionerWriter`. The other base writers (`StageWriter`,
`SkeletonWriter`, `SkinMorpherWriter`, `MeshWriter`, `CameraWriter`,
`ShapeWriter`, `HelperWriter`) reject any `LightObject` node outright
in their `CanExport` (they `dynamic_cast` to their own class). So for
any light not claimed by `PhotometricLightWriter`, the writer-registry
returns nullptr from `MaxUsdPrimWriterRegistry::FindWriter` and the
node is silently dropped: no `UsdLux*` prim is authored, no error
fires, no warning fires.

`PhotometricLightWriter::CanExport` (`src/translators/PhotometricLightWriter.cpp`,
post-MAX-LIT-001 comment block) gates on the LIGHTSCAPE_LIGHT_CLASS
boundary:

```cpp
MaxUsdPrimWriter::ContextSupport MaxUsdPhotometricLightWriter::CanExport(
    INode*                                node,
    const MaxUsd::USDSceneBuilderOptions& exportArgs)
{
    if (!exportArgs.GetTranslateLights()) {
        return ContextSupport::Unsupported;
    }
    const auto object = node->EvalWorldState(exportArgs.GetResolvedTimeConfig().GetStartTime()).obj;
    return object->IsSubClassOf(LIGHTSCAPE_LIGHT_CLASS) ? ContextSupport::Fallback
                                                        : ContextSupport::Unsupported;
}
```

This audit pins the SECOND branch — the silent-drop case — as the
writer-registry's documented bottom bound for lights. The Photometric
positive-control branch was already audited end-to-end as
[[MAX-LIT-002]] (IES file path + Kelvin/filter dichotomy); the bottom
bound below is what MAX-LIT-001 locks in.

**Why this audit pins it.** The existing happy-path suite
(`export_light_test.ms`) covers every Photometric light type +
distribution combination but NEVER:

* asserts that a legacy `Omnilight()` in the source Max scene fails
  to produce a UsdLux prim,
* asserts that a legacy `Skylight()` in the source Max scene fails
  to produce a UsdLux prim,
* asserts that the writer-registry rejection at the
  LIGHTSCAPE_LIGHT_CLASS boundary still leaves the in-scope photometric
  light authored (the positive control),
* asserts that the `exportArgs.GetTranslateLights()` short-circuit
  fires BEFORE the class-id gate (so a user who disables light export
  sees the same nullptr-from-FindWriter behavior for every light type).

The cataloged baseline at
`/Users/d.smith/.../knowledge/usd-export-issues-catalog.md` (entry
MAX-LIT-001, "Legacy standard lights do not export to UsdLux", severity
High) names this as a known partial-loss defect. The fix itself — a
proper per-class translation (Omnilight → SphereLight with intensity /
attenuation / color / shadow attribute mapping; Skylight → DomeLight
with sky color / map binding / ray-count mapping; legacy Spot → DiskLight
with hotspot / falloff cone-angle mapping; legacy Direct → DistantLight)
— is a separate per-class bite. THIS audit ships ONLY the lock-in: pin
the bottom bound so any future "extend `PhotometricLightWriter::CanExport`
to accept LightObject" wildcard widening lands its widening with a
corresponding decision-table update + per-attribute translation, rather
than silently authoring spurious default-intensity `UsdLuxSphereLight`
+ `UsdLuxDomeLight` prims at every legacy-light position.

A regression that:

* Returned `ContextSupport::Fallback` for every `LightObject` (e.g.
  `return object->IsSubClassOf(LightObject) ? Fallback : Unsupported`)
  AND let the default `GetPrimType` branch fall through to
  `UsdLuxSphereLight` for the unmapped case — would silently produce
  a unit-intensity SphereLight at every Omnilight position in every
  legacy scene. An Omnilight that was turned OFF / set to multiplier
  0 / set to a non-default attenuation / set to a non-default color
  in Max would all silently appear as a default unit-intensity
  white SphereLight in USD.
* Removed the `exportArgs.GetTranslateLights()` short-circuit (or
  re-ordered it AFTER the class-id check) — would still drop legacy
  lights but would silently change the meaning of
  `exportOptions.Lights = false` for in-scope photometrics.
* Added a "fallback default writer" for unknown LightObjects in the
  `PrimWriterRegistry::FindWriter` nullptr branch — would silently
  start authoring some UsdLux prim for every legacy light, with no
  signal to the artist that the appearance has diverged from the
  source scene.

…would pass every existing test in `export_light_test.ms` because no
test ever inspects a stage exported from a scene containing legacy
LightObject lights.

**Bounds (where the gate conservatively does nothing):**

* `exportArgs.GetTranslateLights() == false` — every light, including
  in-scope photometrics, returns `Unsupported` and FindWriter returns
  nullptr. The "no lights at all" short-circuit is honoured BEFORE the
  class-id gate so a user who disables light export sees the same
  nullptr from FindWriter for every light type. This is intentional
  and is NOT the silent-drop defect this audit pins.
* `IsSubClassOf(LIGHTSCAPE_LIGHT_CLASS) == true` — returns
  `ContextSupport::Fallback`. Authored as the right UsdLux*  per the
  PhotometricLightWriter conversion grid (the in-scope branch
  [[MAX-LIT-002]] audited end-to-end).
* `IsSubClassOf(LIGHTSCAPE_LIGHT_CLASS) == false` AND
  `IsSubClassOf(LightObject) == true` — every legacy light class.
  Returns `Unsupported` and (since no other writer claims a LightObject)
  the writer-registry silently drops the node. This is the surgical
  bound this audit pins.
* `IsSubClassOf(LIGHTSCAPE_LIGHT_CLASS) == false` AND
  `IsSubClassOf(LightObject) == false` — any non-light node that
  happens to reach this CanExport (e.g. a Mesh, a Camera). Returns
  `Unsupported`. The trivial-rejection case; another writer (MeshWriter,
  CameraWriter, etc.) is expected to claim. Not the silent-drop defect.

**Why the wildcard widening would be wrong.** The catalog MAX-LIT-001
rationale ("Legacy Skylight + Omnilight silently dropped from USD
export (lighting fidelity)") might be read as "extend
`PhotometricLightWriter::CanExport` to claim every LightObject". That
widening would author:

* Every Omnilight as a default-intensity `UsdLuxSphereLight` at the
  Omnilight's transform — producing a brand-new light pool the source
  scene never asked for if the Omnilight was turned OFF, set to
  multiplier 0, set to a non-default attenuation, or set to a
  non-default color. All of which it would silently lose.
* Every Skylight as a unit-intensity `UsdLuxDomeLight` — swamping the
  scene with dome lighting that the source Skylight (a hemispherical
  environment integrator with its own rayCount / castShadows / sky
  color / map dependency) didn't actually produce.
* Every legacy Spot/Free_Direct as a `UsdLuxDiskLight` without the
  hotspot / falloff / spotlight attenuation Max applies — producing
  a wider, brighter spot than the source.

The right per-class translation is a SEPARATE bite per legacy class
with its own attribute mapping + its own MaxScript regression + its
own doc entry. This audit pins the bottom bound so those follow-on
bites are visible as widenings rather than landing silently — once a
proper Omnilight writer lands, the corresponding line in the audit's
case table flips from "silent drop" to "claimed by OmnilightWriter,
attributes translated per its own surgical bounds".

**Validator.**
`/Users/d.smith/MirisProjects/Agent Builder/agent/arch-builds/e380e9cc-a1c5-4b85-ac37-f234554dd28d/validate_legacy_lights_dropout_surgical.py`
mirrors the writer-registry decision at the USD layer over a synthetic
per-light-class fixture. 8 cases + idempotence + a positive-control
sanity pass + a TranslateLights-off short-circuit pass:

| Case | Max class taxonomy | TranslateLights | CanExport returns | FindWriter result | UsdLux prim authored? |
| --- | --- | --- | --- | --- | --- |
| `PhotometricFreePointInScope` | LightscapeLight2 subclass | true | Fallback | PhotometricLightWriter | UsdLuxDiskLight |
| `PhotometricCylinderTargetInScope` | LightscapeLight2 subclass | true | Fallback | PhotometricLightWriter | UsdLuxCylinderLight |
| `LegacyOmnilight` | LightObject, NOT LightscapeLight | true | Unsupported | nullptr (silent drop) | — |
| `LegacySkylight` | LightObject, NOT LightscapeLight | true | Unsupported | nullptr (silent drop) | — |
| `LegacyTargetSpot` | LightObject, NOT LightscapeLight | true | Unsupported | nullptr (silent drop) | — |
| `LegacyTargetDirectional` | LightObject, NOT LightscapeLight | true | Unsupported | nullptr (silent drop) | — |
| `PhotometricFreePointWithLightsOff` | LightscapeLight2 subclass | false | Unsupported (short-circuit) | nullptr | — |
| `NonLightObject` | GeomObject (not a light at all) | true | Unsupported | OtherWriterPresumed (MeshWriter etc.) | (placeholder Xform) |

Plus the idempotence check (re-authoring the same fixture produces an
identical prim-path set), the positive-vs-negative sanity check (≥2
UsdLux prims survive AND none of the legacy names carry UsdLux prims),
and the TranslateLights-off short-circuit pass (all 7 light cases drop).
All 25 assertions pass on the 2026-06-26 baseline against pxr.Usd
0.25.5, locking in the writer-registry's bottom bound. A future
regression that widened the gate would fail by named case rather than
just "some light disappeared somewhere".

**MaxScript regression.**
`src/Tests/Integration/export_light_test.ms` now carries
`photometric_light_writer_legacy_dropout_audit_test`. It constructs a
scene with three lights — a `free_light()` of type `#Free_Point` (the
LIGHTSCAPE_LIGHT_CLASS positive control), an `omniLight()` (the legacy
Omnilight negative case), and a `Skylight()` (the legacy Skylight
negative case) — exports through `USDExporter` with `Lights = true`,
and asserts the photometric DiskLight IS authored at its expected path
while NEITHER legacy light has a prim at its expected path. A final
`Lights = false` re-export pins the option-gate-fires-first short-circuit.

**Visual demonstration of the surgical bound.** A neutral probe
fixture — a matte-grey sphere on a matte-grey ground plane lit by ONE
photometric Free_Point (a warm-tungsten `UsdLuxDiskLight`) on the LEFT
— is rendered twice in Karma CPU:

* `render_karma_postfix.png` — the CURRENT correct behavior. ONLY the
  photometric DiskLight is authored. The sphere reads warm on its
  LEFT hemisphere from the in-scope photometric pool; the RIGHT
  hemisphere is visibly DARKER (the source Omnilight that sat there
  was silently dropped); a long soft shadow extends from the sphere
  to the RIGHT across the ground plane. Foreground mean intensity
  R=104.80 / G=99.32 / B=93.27 / 255.
* `render_unreal_reference.png` — the wildcard widening counterfactual.
  Same scene PLUS a spurious cool-blue `UsdLuxSphereLight` on the
  right (the widening of `Omnilight()`) PLUS a `UsdLuxDomeLight` wash
  (the widening of `Skylight()`). The sphere reads bright on BOTH
  hemispheres; the right-side shadow is much fainter; the overall
  scene is brighter and cooler from the dome contribution.
  Foreground mean intensity R=152.96 / G=152.15 / B=150.59 / 255.

Delta (postfix − reference): **R = −48.17, G = −52.83, B = −57.33 / 255**
— postfix is uniformly darker by ~48-57/255 across every channel, with
the largest swing on BLUE because the wildcard counterfactual lights
are explicitly cool-blue. 50.04% of pixels in the frame have a
non-zero per-pixel Manhattan delta (mean delta 79.17 / 765). PNGs are
SHA-256-distinct (`0ffbcc80c0d66c1b…` vs `46b889329234dbb4…`).
`compare_side_by_side.png` is the auditor's at-a-glance composite.

The auditor's checklist: **postfix has only the warm pool on the LEFT
with a visibly dark RIGHT hemisphere AND a long soft right-side
shadow; still-broken has uniform brightness across the sphere AND a
faint-to-absent right-side shadow AND the mean foreground intensities
trend UP on every channel with the largest swing on BLUE.** A
refactor that ever made postfix bright on the right side or made the
right-side shadow disappear would mean the writer-registry has started
authoring spurious UsdLux prims for legacy LightObjects and the
silent-drop contract has broken.

**Retirement condition.** This audit retires the day a proper per-class
legacy-light writer lands (e.g. a `MaxUsdLegacyOmnilightWriter`
registered in `plugInfo.json` that returns `ContextSupport::Fallback`
for `OMNI_LIGHT_CLASS_ID` and authors a `UsdLuxSphereLight` with
intensity / attenuation / color / shadow mapping translated from the
source `OmniLight`). At that point the `LegacyOmnilight` row in the
case table flips from "silent drop" to "claimed by
LegacyOmnilightWriter, attributes translated per MAX-LIT-003 surgical
bounds" (or whichever MAX-LIT-* ID lands the writer), and a new audit
ships with the per-attribute lock-in. Until those per-class writers
land, the silent drop is the documented, locked-in behaviour and this
audit prevents it from being silently widened.

### Photometric / Physical light `.webFile` → UsdLuxShapingAPI.shaping:ies:file  (MAX-LIT-002, IES branch)

**Symptom (covered before the audit, but uncovered by the suite).**
3ds Max Photometric (Free / Target) and Physical lights expose a
`distribution` parameter with four values: ISOTROPIC (0), SPOTLIGHT (1),
DIFFUSE (2), and WEB (3). When the artist picks WEB they can additionally
pick a `.webFile` — an IESNA LM-63 profile that the renderer uses to
modulate the light's far-field intensity distribution (the classic "IES
profile" used in architectural visualisation: real fixtures shipped with
manufacturer-supplied .ies files capturing their exact light
distribution).

`PhotometricLightWriter::Write` (lines 274–288, post-MAX-LIT-002 comment
block) translates this directly to `UsdLuxShapingAPI.shaping:ies:file`:

```cpp
if (maxPhotometricLight->GetDistribution() == LightscapeLight::WEB_DIST) {
    AssetUser asset = maxPhotometricLight->GetWebFile();
    if (asset.GetId() != kInvalidId) {
        pxr::UsdLuxShapingAPI usdLightShape(usdLightPrim);
        pxr::SdfAssetPath assetFullPath(asset.GetFullFilePath().ToUTF8().data());
        usdLightShape.CreateShapingIesFileAttr().Set(
            assetFullPath, pxr::UsdTimeCode::Default());
    }
}
```

The translation is a **direct pairing**: the resolved IES file path
flows verbatim into the `SdfAssetPath` Karma, Storm, Arnold's USD
delegate, and Cycles' USD loader all honour. No transformation, no
attribute renaming, no value clamping.

**Why this audit pins it.** The existing happy-path suite
(`export_light_test.ms`, lines 260, 364, 483, 616, 716, 873) toggles
`distribution = 3` (web/IES) on every photometric light-type test —
point / sphere / disk / line / cylinder / rectangle — but the tests
NEVER set `.webFile`. They only assert that the right `UsdLux*` prim
type is created (DiskLight under web-on-point, etc). The entire
`shaping:ies:file` authoring branch is uncovered. A regression that:

* Dropped the `asset.GetId() != kInvalidId` check — would emit
  `shaping:ies:file = ""` and most renderers would silently fall back
  to the disk light's default uniform pattern. Visually equivalent to
  no IES authoring on Karma, but lethal for any consumer that
  validates the asset path early (USDZ packagers, schema validators,
  Omniverse asset-graph importers).
* Always-authored `shaping:ies:file` when `.webFile` was non-empty
  (regardless of distribution) — would produce a USD light that
  paradoxically carries both a shape-driven distribution (a spot's
  `shaping:cone:angle`, an isotropic sphere's omni emission) AND an
  IES profile. Some renderers honour one and ignore the other,
  producing source-renderer-dependent appearance.
* Re-routed the path through some other resolver — would produce a
  path string that no longer round-trips through `usdview`, `husk`,
  or any pack-and-go tool.

…would pass the existing suite because no test ever inspects
`shaping:ies:file` on the resulting USD.

**Bounds (where the gate conservatively does nothing):**

* `distribution != WEB_DIST` — even if `.webFile` is set on the source
  light (Max preserves the picked file across distribution toggles;
  it is only ACTIVE while distribution == WEB_DIST), `shaping:ies:file`
  MUST NOT be authored.
* `distribution == WEB_DIST` AND `asset.GetId() == kInvalidId` (the
  user picked web distribution but never selected a `.ies` file) —
  `shaping:ies:file` MUST NOT be authored.

**Known limitation (logged but not in scope for this audit).** The path
is authored as the asset's resolved-full-file-path (absolute on the
source machine). The TODO at `PhotometricLightWriter.cpp:277` flags
this — a pack-and-go USDZ + IES bundle would need a separate pass to
copy the .ies file alongside the .usd and rewrite the path to relative
form. The audit's scope is to **lock in the current branch shape**;
the pack-and-go improvement is tracked as a candidate mission.

**Validator.**
`/Users/d.smith/MirisProjects/Agent Builder/agent/arch-builds/d0c69786-3afa-4669-a81c-47caab95e95b/validate_photometric_light_fidelity.py`
mirrors the C++ decision at the USD-side level for BOTH the IES and
Kelvin branches. 4 of its 8 cases pin the IES branch:

| Case in synthetic fixture       | distribution | webFile asset valid | shaping:ies:file authored? | bound exercised |
| --- | --- | --- | --- | --- |
| `IsotropicNoIesAuthored`         | ISOTROPIC    | no   | no   | distribution != WEB_DIST short-circuits the gate |
| `WebDistWithValidIesAsset`       | WEB_DIST     | yes  | yes (= path) | the happy path: both gates pass |
| `WebDistWithoutWebFileSet`       | WEB_DIST     | NO   | no   | WEB_DIST + invalid asset id MUST suppress shaping:ies:file (no spurious empty path) |
| `SpotDistAuthorsConeAngleNotIes` | SPOTLIGHT    | yes (but ignored) | no | even with .webFile populated, distribution=spot uses the cone-angle branch and ignores the IES asset entirely |

Plus an idempotence check (the simulator is a pure function of its
inputs). All cases pass on the 2026-06-26 baseline.

**MaxScript regression.**
`src/Tests/Integration/export_light_test.ms` now carries
`photometric_light_ies_file_export_test`. It synthesizes a minimal
IESNA LM-63 profile in the temp dir, creates a `Free_Point` light with
`distribution = 3` (web/IES) AND `.webFile = <synthetic IES path>`,
exports, and asserts `shaping:ies:file` is authored on the resulting
USD light with a path containing the .ies filename. Then it
re-exports the same light with `distribution = 0` (isotropic) and
asserts `shaping:ies:file` is NOT authored — pinning the
distribution-gate surgical bound. Then it creates a second
`Free_Point` with `distribution = 3` but no `.webFile`, exports, and
asserts `shaping:ies:file` is NOT authored — pinning the asset-id
surgical bound.

### Photometric / Physical light Kelvin + filter color → UsdLux color attrs  (MAX-LIT-002, Kelvin branch)

**Symptom (covered before the audit, but uncovered by the suite).**
3ds Max Photometric and Physical lights expose two parallel colour
controls:

* The **light colour** (Light > Color > RGB swatch) — an arbitrary RGB
  tint multiplier.
* The **filter colour** (Light > Color > Filter Color swatch) — a
  second arbitrary RGB tint applied "after" the light colour to
  simulate gels / filters.
* A **Kelvin toggle** (Light > Color > Kelvin checkbox) that switches
  the light colour from RGB to a blackbody-temperature value (the
  spinner below).

`PhotometricLightWriter::Write` (lines 481–535, post-MAX-LIT-002
comment block) translates this dichotomously to UsdLux's
`enableColorTemperatureAttr` + `colorTemperatureAttr` + `inputs:color`:

```cpp
if (maxPhotometricLight->GetUseKelvin()) {
    colorTemperatureAttribute.Set(clamp(kelvin, 1000, 10000));    // (1)
    if (originalKelvin != clamped) MaxUsd::Log::Warn(...);        // (2)
    Point3 filter = maxPhotometricLight->GetRGBFilter(timeVal);
    usdLightPrim.CreateColorAttr().Set(filter);                   // (3) filter ONLY
} else {
    Point3 combined = maxPhotometricLight->GetRGBColor(timeVal)
                    * maxPhotometricLight->GetRGBFilter(timeVal);
    usdLightPrim.CreateColorAttr().Set(combined);                 // (4) lightColor * filter
}
```

Plus the first-frame setup at line ~239 that authors
`enableColorTemperatureAttr = useKelvin`.

The translation is a **direct pairing** for both halves of the
dichotomy:

* **useKelvin == true** → `enableColorTemperature = true`,
  `colorTemperature = clamp(kelvin, 1000, 10000)`,
  `inputs:color = filterColor` (the light's RGB component is
  intentionally DROPPED because the spectrum is now driven by Kelvin;
  the renderer multiplies `inputs:color` by the blackbody integrand,
  so a non-white RGB would shift the hue away from the spectrum the
  artist asked for).
* **useKelvin == false** → `enableColorTemperature = false`,
  `colorTemperature` NOT authored (the USD-spec default of 6500 K is
  irrelevant because the enable flag is off),
  `inputs:color = lightColor × filterColor` (the combined product —
  intentionally lossy on round-trip, an importer cannot recover which
  factor was the light and which was the filter, but matches the
  renderer's expectation of a single RGB multiplier when no blackbody
  is active).

**Why this audit pins it.** The existing happy-path suite
(`photometric_light_general_attributes_export_test`,
`photometric_point_light_attributes_animation_export_test`) asserts
that `enableColorTemperatureAttr` is true/false on certain mode
toggles but NEVER:

* the actual `colorTemperatureAttr` VALUE in static-frame export
  (the animation test only checks values inside an animation range);
* that `inputs:color` carries the FILTER COLOUR ONLY under
  `useKelvin == true` (it would silently pass if a regression
  authored `lightColor × filterColor` here too, because the test
  doesn't compare against `lightColor`);
* that `inputs:color` carries the COMBINED PRODUCT under
  `useKelvin == false` (a regression that authored only one of the
  two factors would pass);
* that out-of-range Kelvin is CLAMPED to `[1000, 10000]` rather than
  passed through (the renderer might silently extrapolate past spec
  or reject the value entirely).

A regression that flipped any of those branches would silently
produce a USD light whose appearance diverges from the source Max
scene — the existing happy-path tests would still pass.

**Bounds (where the gate conservatively does nothing):**

* `useKelvin == true`, `kelvin ∈ [1000, 10000]` — no clamp warning;
  `colorTemperature` is authored verbatim.
* `useKelvin == false` — `colorTemperatureAttr` is never created on
  this branch (it would be wrong to author 6500 K as the "default" —
  that's the renderer's default when enable is on, not when it's off).
* The clamping is one-sided: a warning fires on clamp but the original
  Max value is preserved in the message string (the artist can
  re-tune by knowing what they asked for).

**Validator.**
The same `validate_photometric_light_fidelity.py` covers the Kelvin
branch with 4 cases:

| Case in synthetic fixture       | useKelvin | kelvin | filterColor | lightColor | enable | ct | inputs:color | bound exercised |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `KelvinEnabledFilterOnlyColor`   | true   | 3200  | (1.0, 0.6, 0.3) | (0.5, 0.2, 0.9) | true  | 3200  | (1.0, 0.6, 0.3) -- filter ONLY | the magenta lightColor must NOT contaminate inputs:color |
| `KelvinDisabledCombinedColor`    | false  | (n/a) | (1.0, 0.8, 0.4) | (0.5, 1.0, 0.5) | false | NONE  | (0.5, 0.8, 0.2) -- combined | colorTemperatureAttr MUST NOT be authored; inputs:color MUST be the combined product |
| `KelvinClampedHigh`              | true   | 12000 | (1, 1, 1)       | (1, 1, 1)       | true  | 10000 | (1, 1, 1) | clamp + one-shot warn |
| `KelvinClampedLow`               | true   | 500   | (1, 1, 1)       | (1, 1, 1)       | true  | 1000  | (1, 1, 1) | symmetric clamp + one-shot warn |

Plus the same idempotence check. All cases pass on the 2026-06-26
baseline.

**MaxScript regression.**
`photometric_light_kelvin_filter_color_round_trip_test` (new in
`export_light_test.ms`) constructs a `Free_Point` with
`useKelvin = true`, `kelvin = 3200`, `rgb = magenta-ish`,
`filterColor = warm orange`, exports, and asserts (a)
`enableColorTemperatureAttr = true`, (b) `colorTemperatureAttr = 3200`,
(c) `inputs:color` matches `filterColor / 255` channel-by-channel
(NOT `lightColor × filterColor`). Then flips `useKelvin = false`,
re-exports, and asserts (a) `enableColorTemperatureAttr = false`,
(b) `inputs:color` matches `(lightColor × filterColor) / 255²`
channel-by-channel (the combined product). Then flips `useKelvin =
true` again with `kelvin = 12000` and asserts the exported
`colorTemperatureAttr = 10000` (clamp); and `kelvin = 500` clamps to
`1000`.

**Visual demonstration of the surgical bound.** Two `UsdLuxDiskLight`s
side-by-side, lighting a grey wall, each carrying `inputs:color =
filterColor` and `enableColorTemperatureAttr = true` with
`colorTemperatureAttr = 2700 K` (warm tungsten) on the left and
`colorTemperatureAttr = 9000 K` (cool daylight) on the right.
`render_karma_postfix.png` shows two clearly-distinct pools — warm
orange on the left, cool azure on the right — the blackbody contrast
reads as tungsten-vs-daylight at a glance.
`render_unreal_reference.png` is the same scene with the
`colorTemperatureAttr` and `enableColorTemperatureAttr` STRIPPED —
the blackbody contribution is gone and the two pools collapse to
faint near-neutral cream / pale-blue (just the subtle filter
contribution survives). The auditor's checklist: **the current fix's
render shows two distinct warm + cool pools; if both pools collapse
to faint near-neutral tints, the Kelvin branch has regressed.** The
composite at `compare_side_by_side.png` makes the contrast
unambiguous; the two PNGs are SHA-256-distinct.

**Retirement condition.** Not a workaround for an external bug — the
existing code is correct. The dichotomy is permanent: `useKelvin`
toggles the spectrum source between blackbody temperature (with
filter) and explicit RGB (combined with filter), and the writer
respects both branches. A future bite could improve the lossy
round-trip on the `!useKelvin` branch (e.g. author
`lightColor` as `inputs:color` and `filterColor` as
`UsdLuxColorTemperatureFilter` schema once such a schema exists — the
USD spec does not currently offer one). Until then, the combined
product is the right call.

### USDZ packaging path: cmd->powershell->python->usdzip shim → in-process `pxr::UsdUtilsCreateNewUsdzPackage` (MAX-PKG-001)

**Symptom.** When the user (or a `3dsmaxbatch` headless caller) exports a
USD stage to a `.usdz` extension, the writer hits the `isUSDZExport == true`
branch in `USDIOController::Export` (and the parallel branch in
`USDSceneController::Export`), authors an intermediate `.usd` into Max's
`#temp` dir, and then calls `MaxUsd::UsdToolsUtils::RunUsdZip` to convert.
`RunUsdZip` is a four-process shim:

```
3dsmax.exe -> cmd.exe /c RunUsdZip.bat
           -> powershell.exe -executionpolicy RemoteSigned -file RunUsdTool.ps1 UsdZip ...
           -> python.exe UsdToolWrapper.py UsdZip ...
           -> usdzip (Pixar python tool, runpy-loaded)
```

The shim works on a fresh interactive 3ds Max install on a vanilla developer
workstation. It silently fails on several headless / locked-down contexts
that the test suite never exercises:

* **PowerShell ExecutionPolicy.** `RunUsdTool.ps1` is invoked with
  `-executionpolicy RemoteSigned`, which fails on machines where the local
  ExecutionPolicy is `Restricted` (a common managed-IT default for
  service accounts and locked-down VDI / Citrix images).
* **Registry probe.** `RunUsdTool.ps1` resolves the bundled
  `python.exe` by reading
  `HKLM:\SOFTWARE\Autodesk\3dsMax\<version>\InstallDir`, which is only
  written by the installer's all-users path. Per-user / portable / dev-
  rebuild installs may not write that key and the script exits with
  `"Could not find the 3dsMax python executable from the registry."`.
* **CreateProcess + `SW_HIDE` + cmd.exe.** `CreateProcessAndWait` runs the
  whole shim through `cmd.exe /c` with `STARTF_USESHOWWINDOW + SW_HIDE`,
  which on some service-account contexts (`3dsmaxbatch` invoked under a
  Windows service, render farms, CI workers without an interactive
  desktop) refuses to spawn or returns immediately without launching
  `usdzip` -- producing a "success" status with no `.usdz` on disk.
* **Unicode in `#temp` or filenames.** The shim already explicitly rejects
  unicode in `getDir #temp` (see `io_unicode_test.ms::test_unicode_usdz_tempdir`
  and the `MaxUsd::HasUnicodeCharacter` gate in
  `USDIOController.cpp:298-304`). The in-process API does not have that
  limitation -- unicode asset names round-trip cleanly.

The audit observes that all four failure modes share a root cause: the
packaging is performed out-of-process via Windows-specific shell tooling
rather than via the `pxr.UsdUtils` C++ / Python API the rest of the plugin
already binds against. `pxr::UsdUtilsCreateNewUsdzPackage` is the
in-process equivalent, available everywhere the plugin builds (it lives in
`pxr/usd/usdUtils/dependencies.h` and is exported by `libusdUtils`). The
Python validator demonstrates the equivalence at the USD layer with nine
named cases.

**Why it matters.** Headless export from `3dsmaxbatch` is the path render
farms and CI pipelines use; silently-failing USDZ export on those paths
manifests as "the artist exported a .usdz from the UI, the CI re-export of
the same scene produced no .usdz, nobody noticed until the downstream
ARKit Quick Look ingest reported the file missing". The proposed
in-process swap eliminates all four failure modes in a single bite.

**Audit (this PR -- no C++ logic change).** The architecture-mode audit
pins the surgical bounds the future in-process swap MUST preserve, lands
the Python validator that exercises the in-process API directly, lands a
MaxScript regression for the headless export path, and lands a visual
auditor pair demonstrating the packaging is lossless. The C++ swap itself
is held for a follow-on bite that runs on a Windows build host (Mac cannot
build per `[[repo-3dsmax-usd]]`); the swap commit only needs to wire the
`UsdUtilsCreateNewUsdzPackage` call.

**Surgical bounds (each pinned by one named case in the Python validator,
`validate_usdz_packaging_fidelity.py`):**

| Bound | Case | What it pins |
| --- | --- | --- |
| Layer-only stages package cleanly | `SimpleNoAssets` | `.usdz` with zero external assets contains exactly one zip member (the root layer); reopening via `Usd.Stage.Open(.usdz)` succeeds |
| External image assets are bundled, the `file` input is rewritten | `WithImageDep` | Texture .png is bundled alongside the root layer; the `UsdUVTexture.file` input in the packaged stage now points at a relative path inside the .usdz |
| ARKit packaging flattens sublayers | `ARKitSingleLayer` | `CreateNewARKitUsdzPackage` produces exactly one `.usd[ac]` layer + assets |
| Non-ARKit packaging preserves sublayers | `SublayerComposition` | `CreateNewUsdzPackage` keeps sublayers as separate zip members; `CreateNewARKitUsdzPackage` flattens them |
| Unicode asset names survive the round-trip | `UnicodeAssetName` | Asset filenames containing non-ASCII characters (`いろはにほへ.png`) bundle correctly and `Usd.Stage.Open` on the resulting .usdz resolves the texture file input |
| Missing assets surface as an error, NOT a silent dangling reference | `MissingAssetRefuses` | `Tf.ErrorException` raised by `UsdZipFileWriter::AddFile`; strict-input behavior stricter than the legacy `usdzip` CLI which would silently produce a corrupt .usdz |
| The call site MUST clean up on failure | `MissingAssetRefuses` (second half) | On the `Tf.ErrorException` path the API leaves behind a structurally-valid-but-content-incomplete `.usdz`; the call site must `fs::remove_all` it (mirror the existing success-branch temp-dir cleanup) |
| The call site MUST pass the temp `.usd`, never the user-supplied `.usdz` | `AlreadyZippedInput` | `CreateNewUsdzPackage(.usdz, .usdz)` recursively repackages (treats the input as an opaque first-layer blob). Pin: the asset-path argument to the in-process call MUST be the temp `.usd` path the writer just authored, NOT the user-supplied final `.usdz` path |
| The first zip member is the root layer | `UsdzMemberOrder` | Downstream tools (ARKit Quick Look, some USDZ readers) position-index the first member as the root layer; the in-process API guarantees this |
| Idempotence: same input → content-identical output | `Idempotence` | Two consecutive packaging runs against the same source produce zips with identical content (modulo timestamps); confirms the API is deterministic per input |

**Validator.**
`/Users/d.smith/MirisProjects/Agent Builder/agent/arch-builds/45c60621-dae1-4ac7-8904-9cebbe168ba0/validate_usdz_packaging_fidelity.py`
mirrors the C++ decision at the USD layer; nine cases + idempotence; all
pass on the 2026-06-26 baseline against `pxr.Usd 0.25.5`. The validator
runs entirely in `hython` and does not need a Windows build host -- exactly
the equivalence proof an architecture-mode audit needs for a code path
whose C++ swap ships unverified.

**MaxScript regression** in `src/Tests/Integration/export_usdz_test.ms`:

* `test_usdz_headless_packaging_round_trip` exercises the `exportFile foo.usdz
  #noprompt` path (the `suppressPrompts:true` branch in
  `USDExporter::ExportFile`, which routes through
  `USDIOController::Export -> RunUsdZip`). Asserts the .usdz exists, is a
  valid zip, the FIRST member is the root layer, and the texture .png is
  bundled.
* `test_usdz_headless_failure_surfaces_status` exercises the failure
  contract: a `#noprompt` export to an unreachable path must surface
  failure via the returned status, not silently report success with no
  file on disk.

**Surgical-bounds comment block in C++** in both `USDIOController::Export`
and `USDSceneController::Export` at the `isUSDZExport` branch, enumerating
every bound the in-process swap must preserve and pointing at the named
Python case + the MaxScript regression. The comment is the contract; the
swap commit only needs to wire the call.

**Visual auditor pair**:

* `render_unreal_reference.png` -- Karma CPU render of the *source* `.usda`
  (texture asset = sibling `.png` on disk).
* `render_karma_postfix.png` -- Karma CPU render of the same scene packaged
  through `pxr.UsdUtils.CreateNewUsdzPackage` and re-opened (texture asset
  = zip member inside the `.usdz`).

The two renders show the same magenta-on-yellow checker pattern in the same
orientation at the same scale, demonstrating that packaging is lossless at
the pixel level. SHA-256-distinct (two separate `usdrecord` invocations
have different sampler seeds) so they're confirmed not to be the same file.
`compare_side_by_side.png` is the auditor's at-a-glance comparison.

**Retirement condition.** Once the follow-on bite swaps the `RunUsdZip`
call to `UsdUtilsCreateNewUsdzPackage` and lands on a Windows build host,
this audit entry stays as the structural contract; the changelog gains a
"MAX-PKG-001 in-process swap landed" bullet and the test suite continues
to assert the same observable structure.

### MultiMtl-bound mesh with multiple matIds → primvars:displayColor stays single-element constant (audit, MAX-MAT-003 STOPS HERE for per-face)  (MAX-MAT-008)

**Symptom.** The 3ds Max `Mtl::GetDiffuse(int mtlNum = 0, BOOL backFace =
FALSE)` SDK accessor is what the MAX-MAT-003 displayColor block in
`src/MaxUsd/MeshConversion/MeshConverter.cpp` calls to derive
`primvars:displayColor` when a material is bound:

```cpp
if (!usdMesh.GetDisplayColorAttr().IsAuthored()) {
    Color displayColorSrc;
    if (Mtl* boundMtl = node->GetMtl()) {
        displayColorSrc = boundMtl->GetDiffuse();   // <-- default mtlNum = 0
    } else {
        displayColorSrc = Color(node->GetWireColor());
    }
    pxr::VtVec3fArray usdDisplayColor = { pxr::GfVec3f(
        displayColorSrc.r, displayColorSrc.g, displayColorSrc.b) };
    usdMesh.CreateDisplayColorAttr().Set(usdDisplayColor);
}
```

On a single Mtl `GetDiffuse()` returns the material's own diffuse. On a
**MultiMtl** the default `mtlNum = 0` makes it return **sub-material 0's
diffuse only**. The `boundMtl->GetDiffuse(matId)` per-face broadcast that
the planner's auto-emitted bite
(`max-mat-004-multimtl-per-face-displaycolor`, rationale "Per-face
primvars:displayColor for MultiMtl bindings") would introduce does NOT
happen: the writer's `materialIdToFacesMap` is already built and consumed
by `ApplyMaxMaterialIDs` above to author per-face GeomSubsets (MAX-GEO-002),
but the displayColor block deliberately ignores that data and writes a
single-element constant primvar from sub-mtl 0's diffuse.

The audit's job is to **lock that surgical-coverage bound in with negative-
test coverage** -- no C++ logic change. The Mac cannot build, so any
wildcard "extend MAX-MAT-003 to per-face for MultiMtl bindings" PR would
ship unverified C++ that silently mutates the primvar's shape on every
export of every MultiMtl-bound mesh -- changing
`primvars:displayColor` from a length-1 constant primvar to a length-N
uniform primvar, breaking the third-reinforcement primvar-shape invariants
(MAX-MAT-003) and the `usdview` / ARKit Quick Look / minimal-Hydra
displayColor-fallback consumer path the original MAX-MAT-003 fix was
designed to repair.

**Why it matters.** The audit's primary visible bound:

| Aspect              | Current MAX-MAT-003 gate     | Per-face widening counterfactual |
| --- | --- | --- |
| primvar name        | `displayColor`               | `displayColor` (collision) |
| component type      | `color3f[]`                  | `color3f[]` (collision) |
| **interpolation**   | **`constant`**               | **`uniform`** |
| **array length**    | **1**                        | **N = `materialIdToFacesMap.size()`** |
| diffuse source      | `boundMtl->GetDiffuse()` (= sub-mtl 0 via default `mtlNum = 0`) | `multimtl->GetDiffuse(matId)` per face |
| reads matId map     | NO -- block has no dependency on `materialIdToFacesMap` | yes -- block would consume the map directly |
| `displayColor[0]`   | sub-mtl 0's diffuse           | sub-mtl 0's diffuse (collision -- same value, same branch label) |
| fallback render     | uniform sub-mtl 0 color       | per-face mosaic with one color per matId partition |

The collision on `displayColor[0]` and the gate's branch label
(`mtl-diffuse` / `mtl-diffuse-multimtl`) means every existing assertion in
`io_color_n_visibility_test.ms` -- value at index 0, IsAuthored() short-
circuit, branch label, wire-color-fallback parity -- would still pass
under the widening. Only the **array-length and interpolation invariants**
(originally added by the MAX-MAT-003 third reinforcement) catch the
widening shape on a MultiMtl-bound mesh.

The other side of the bound is that the MAX-GEO-002 GeomSubset path is
unaffected: when the bound material IS a MultiMtl, `ApplyMaxMaterialIDs`
still writes per-face GeomSubsets in the `materialBind` family with
`materialIdToFacesMap.size()` subsets (one per distinct matId). The
displayColor block sits AFTER `ApplyMaxMaterialIDs` in the writer flow but
deliberately does NOT consume the same map -- the two layers serve
different purposes (per-face material binding vs. fallback surface color).
A wildcard widening that ported the matId-driven partition logic to
displayColor would conflate the two and silently change the fallback path
for every MultiMtl-bound mesh in every existing exported asset.

**Fix.** None. The existing C++ is already correct (the MAX-MAT-003 block
is unchanged). The audit lands:

1. **Surgical-bounds comment block** in `MeshConverter.cpp` extending the
   existing MAX-MAT-003 comment block with a "MultiMtl per-face surgical-
   coverage bound (MAX-MAT-008 scope/coverage audit)" subsection
   enumerating the collision-shape table above and naming both the
   MaxScript regression and the Python validator that pin the bound.
2. **MaxScript regression**
   `test_display_color_multimtl_single_constant_not_per_face` in
   `src/Tests/Integration/io_color_n_visibility_test.ms`. Builds a Box
   with `wireColor = green`, a 2-sub-mtl MultiMtl
   (sub-mtl 0 = red Standard, sub-mtl 1 = blue Standard), and a half-and-
   half matId partition (`polyOp.setFaceMatId b #{1,2,3} 1` and
   `polyOp.setFaceMatId b #{4,5,6} 2`). Exports through `USDExporter` and
   asserts the exported mesh's `primvars:displayColor`:
     * `displayColor[0] == red` (sub-mtl 0's diffuse, not the wire color
       and not sub-mtl 1's diffuse).
     * `displayColor.count == 1` (single-element array, NOT
       `materialIdToFacesMap.size() == 2`).
     * `primvar.GetInterpolation() == "constant"` (NOT `"uniform"`).
     * `displayColorAttr.GetNumTimeSamples() == 0`.

   A failure on any shape assertion means the C++ block gained a per-face
   broadcast that escapes the existing value-equality + branch-label
   regressions.
3. **Python validator** at
   `/Users/d.smith/MirisProjects/Agent Builder/agent/arch-builds/da899dab-58c9-446a-b844-dafee36353e0/validate_multimtl_per_face_displaycolor_surgical.py`.
   Builds a synthetic `UsdGeomMesh` with FaceCount=8 carrying two matIds
   authored as two GeomSubsets in the `materialBind` family (mirroring
   what `ApplyMaxMaterialIDs` writes on the post-MAX-GEO-002 corpus).
   Runs the mirror-of-C++ MAX-MAT-003 gate across 8 cases:

   | Case                                     | mtl              | partition | expected branch         | expected color | shape pinned |
   | ---                                      | ---              | ---       | ---                     | ---            | --- |
   | `SingleMtlSinglePartition`               | single (green)   | 1 matId   | `mtl-diffuse`           | green          | len 1 / constant |
   | `MultiMtlSinglePartition`                | multi (red/blue) | 1 matId   | `mtl-diffuse-multimtl`  | red            | len 1 / constant |
   | `MultiMtlTwoPartition_redblue`           | multi (red/blue) | 2 matIds  | `mtl-diffuse-multimtl`  | red            | **len 1 / constant -- KEY BOUND** |
   | `MultiMtlTwoPartition_blackSentinel`     | multi (black/white) | 2 matIds | `mtl-diffuse-multimtl` | (0,0,0)        | value-coincidence on multi-mtl path |
   | `MultiMtlTwoPartition_HDR`               | multi (HDR/blue) | 2 matIds  | `mtl-diffuse-multimtl`  | (2.5,0.1,0.1)  | HDR survives verbatim |
   | `NoMtlMultiPartition_wireColorFallback`  | None             | 2 matIds  | `wire-color-fallback`   | wire color     | partition does NOT leak to wire branch |
   | `MultiMtlPreauthored`                    | multi (red/blue) | 2 matIds  | `preauthored-preserved` | (vertex authoring) | artist authoring survives MultiMtl-bound + partition |
   | `MultiMtlTwoPartition_redblue` (idempotence) | multi (red/blue) | 2 matIds | (pass 1) then `preauthored-preserved` (pass 2) | red | re-running gate is a no-op |
   | `WidenedPerFaceShape` (negative control) | (synthetic widening) | 2 matIds | n/a               | n/a            | **shape invariants VIOLATED -- regression detector** |

   The negative-control case constructs the widened-state primvar
   (length 2, interpolation `uniform`, red + blue per matId) directly
   and asserts both shape invariants are VIOLATED. A future regression
   that ships the widening would (a) fail every positive-control case
   AND (b) invert the negative-control case's "invariant VIOLATED"
   assertion into a "invariant SATISFIED" pass, surfacing the
   regression by named case rather than by opaque structural drift.

   On the 2026-06-26 baseline the validator passes all 50 named
   assertions (6 cases x 6 invariants + preauthored 4 + idempotence 4 +
   negative-control 2 = 50). A wildcard refactor that ported the
   matId-driven partition logic to displayColor would fail by named
   case: `"MultiMtlTwoPartition_redblue: displayColor array length = 1
   (single-element constant, NOT per-face) ... FAIL  len(displayColor)
   = 2"`.

**Bounds (where the C++ today conservatively does nothing):**

* `node->GetMtl() == nullptr` -- no material bound. The block falls
  through to the wire-color branch regardless of `materialIdToFacesMap`
  contents. The audit's `NoMtlMultiPartition_wireColorFallback` case pins
  this -- a future "treat parametric matIds as a material-binding proxy"
  widening that consulted the matId partition on the no-mtl branch would
  fail this case by named position.
* `node->GetMtl()` is a single Mtl -- `GetDiffuse()` returns the material's
  own diffuse, single-element constant. No widening opportunity here; the
  audit's `SingleMtlSinglePartition` case pins the trivial baseline.
* `node->GetMtl()` is a MultiMtl AND the mesh has a single matId --
  `GetDiffuse()` still returns sub-mtl 0's diffuse (the only one). The
  widening would degenerate to a length-1 uniform primvar; the audit's
  `MultiMtlSinglePartition` case pins this -- a future widening would
  produce `len 1 / uniform` which the interpolation assertion catches.
* `IsAuthored()` short-circuit takes the `preauthored-preserved` branch
  -- the gate never reaches the diffuse source code. The audit's
  `MultiMtlPreauthored` case pins this for the MultiMtl-bound combined
  state -- even with a MultiMtl present, the artist's pre-existing
  vertex-color primvar (length N, interpolation `vertex`) survives
  verbatim. A widening that "normalised" the preserved primvar to
  length 1 constant on the MultiMtl-bound path would fail this case.

**Care with MAX-GEO-002.** `ApplyMaxMaterialIDs` is invoked immediately
BEFORE the MAX-MAT-003 displayColor block and consumes
`materialIdToFacesMap` to author per-face GeomSubsets in the
`materialBind` family (one subset per distinct matId, with `material:
binding` rel pointing at the corresponding sub-material's USD prim).
The two layers share the same in-memory `materialIdToFacesMap` data
but serve different purposes: GeomSubsets handle per-face material
binding for PBR consumers; displayColor handles fallback surface color
for the primvar-fallback consumer path. The audit's bound is that the
displayColor block does NOT cross over into the GeomSubset layer's
data dependency even though the data is sitting on the stack -- a
wildcard widening would conflate the two layers and silently
double-author the matId partition into a different USD location.

**Visual demonstration of the surgical bound.** A unit cube (6 quad
faces) is rendered twice in Storm at 512x512
(`usdrecord --renderer Storm -c high`) with the same camera + lighting.
Both stages carry NO `material:binding` rel so the renderer reads
`primvars:displayColor` as the fallback surface color (this is the
exact consumer path MAX-MAT-003 was designed to fix).
`render_karma_postfix.png` carries `displayColor = [(0.95, 0.10, 0.10)]`
with `interpolation = constant` (the current correct behavior --
single sub-mtl 0 diffuse uniformly across all 6 faces).
`render_unreal_reference.png` (the "still-broken" reference) carries
the widening counterfactual: `displayColor = [red x3, blue x3]` with
`interpolation = uniform` -- one entry per face, partitioned at the
half-way mark to mirror a MultiMtl with sub-mtls 0 and 1 distributed
half-and-half across the cube's faces. Mean per-channel intensities
over the foreground masks (alpha > 0):

```
render_karma_postfix.png       foreground mean R=194.82  G=70.04   B=70.04   / 255
render_unreal_reference.png    foreground mean R=157.23  G=70.04   B=107.74  / 255
postfix - reference            mean R=+37.59   G=+0.00   B=-37.69
PNG SHA-256                    ab34d3d4... vs 29dcc7bd...   (distinct)
```

The delta is **large, directional, and structurally clean**:

* **Red channel:** postfix is +37.59 / 255 brighter (every face
  contributes red; widening replaces three faces with blue).
* **Blue channel:** postfix is -37.69 / 255 darker (no face contributes
  blue; widening adds blue to three faces).
* **Green channel:** byte-identical (+0.00 / 255). Both color shapes
  share `G = 0.10`, so green is determined by Storm's lighting
  contribution rather than the displayColor shape -- identical lighting
  produces identical green, providing a strong sanity-check that the
  camera and geometry are constant between the two renders.

Composite at `compare_side_by_side.png` in the same arch-build dir.
**The auditor's checklist: the postfix render is a UNIFORM RED cube,
the still-broken render is a CLEAN RED-on-3-faces + BLUE-on-3-faces
split, the postfix's mean RED channel is ~37/255 higher AND the mean
BLUE channel is ~37/255 lower AND the mean GREEN channel is byte-
identical, and the two PNGs have distinct SHA-256s.** If the auditor
sees the postfix render carry a blue split OR the still-broken render
go uniform red OR the green channel differ between them, the C++
gate has started broadcasting per-face on the MultiMtl path (or the
lighting/camera invariant has drifted, which is a different test
failure).

**Retirement condition.** This audit does not have an upstream-fix
retirement condition the way MAX-MAT-001/002/004/005/006 do: the bound
it locks in is a SCOPE limit on Miris-authored C++, not a workaround
for a 3ds Max bridge bug. The bound retires only if a future
captured-corpus diagnostic reveals genuine artist intent for per-face
displayColor on MultiMtl-bound meshes (e.g. a USD ingest pipeline that
EXPECTS per-face fallback colors on a MultiMtl-bound mesh and explicitly
requests them via export-option opt-in). If that happens, a SEPARATE
future MAX-* bite addresses the per-face authoring with its own
opt-in option + its own MaxScript regression + its own doc entry --
NOT as a wildcard widening of the existing single-constant block.
Until then, this audit + its three artifacts (C++ comment block in
`MeshConverter.cpp`, MaxScript regression in `io_color_n_visibility_
test.ms`, Python validator + Storm visual auditor pair in the arch-
build dir) are the regression-coverage net that prevents the
planner's auto-emitted "extend MAX-MAT-003 to per-face for MultiMtl
bindings" PR from shipping unverified C++ that silently mutates
artist-visible primvar-fallback output on every MultiMtl-bound export.

### Color-space round-trip across the three USD-shading writer paths (audit, MaterialX / UsdPreviewSurface / LastResort each STOP at their own convention)  (MAX-MAT-009)

**Symptom.** The 3ds Max → USD exporter authors color-space metadata
across THREE divergent USD-shading writer paths, with three different
conventions:

1. **MaterialX writer (C++)** —
   `src/translators/MtlxShaderWriter.cpp`.
   `_TypeSupportsColorSpace(input)` returns true ONLY for `color3` or
   `color4` typed inputs, OR `filename` typed inputs on an image node
   whose nodedef output is `color3` / `color4`. `_SetInputValue` then
   propagates `input->getActiveColorSpace()` to the USD attribute's
   `colorSpace` metadata via `usdInput.GetAttr().SetColorSpace(...)`
   IFF the active color space resolves to a non-empty string.
2. **UsdPreviewSurface Python writer** —
   `src/ApplicationPlugins/usd-component/Contents/scripts/materials/
   usd_material_writer.py::set_bitmap_scale_bias_sourcecolorspace`.
   Hardcodes `sourceColorSpace = "raw"` as a TOKEN INPUT on every
   baked UsdUVTexture. An explicit TODO at line 116-118 marks this as
   incomplete: the Max source bitmap's `bitmap.gamma` is not
   consulted, so a gamma=2.2 sRGB-tagged source diffuse map exports
   as `sourceColorSpace = "raw"` even though the renderer should be
   applying inverse-EOTF.
3. **LastResort C++ writer** —
   `src/MaxUsd/Translators/LastResortUSDPreviewSurfaceWriter.cpp`.
   Authors `inputs:diffuseColor` as a `Color3f` USD value from
   `Mtl::GetDiffuse()` and nothing else. NO `UsdUVTexture` child, NO
   `sourceColorSpace`, NO `colorSpace` USD metadata on the value
   attribute. Per UsdPreviewSurface spec, `diffuseColor` authored as
   a value is **linear by definition**.

The planner auto-emitted `color-space-roundtrip-correctness` with
severity High and rationale "End-to-end colorimetric audit: sRGB vs
linear, MaterialX vs UsdPreviewSurface, OCIO config". The audit's
job is to **lock in the cross-writer color-space surgical bound with
negative-test coverage** — no C++ logic change.

**Why it matters.** Each of the three writers' color-space conventions
is correct for its own target schema, but they DIVERGE on every axis a
"unify color-space handling" refactor would have to bridge:

| Concept | MaterialX writer (UsdShade) | UsdPreviewSurface Python (UsdUVTexture) | LastResort C++ (value-only) |
| --- | --- | --- | --- |
| Authoring surface | USD attribute `colorSpace` METADATA on the UsdShadeInput attr | `sourceColorSpace` USD TOKEN INPUT on UsdUVTexture | NONE — neither the attribute's `colorSpace` metadata nor a `sourceColorSpace` input |
| Source resolution | MaterialX `input->getActiveColorSpace()` (per-input → per-node → per-document hierarchical resolution) | Hardcoded literal `"raw"` (TODO: consult `from_tex.bitmap.gamma`) | N/A — linear by UsdPreviewSurface spec |
| Type-gated | `color3`, `color4`, or `filename` on image with color3/color4 output | Every UsdUVTexture (no type gating; `sourceColorSpace` is always authored) | N/A — no UsdUVTexture |
| Empty value behavior | Short-circuit on `colorSpace.empty()`: NO metadata authored | Always authors a literal `"raw"`; the input is never absent | N/A — never authors |
| Failure mode of a wildcard widening | Authoring `colorSpace` on non-color types (float / vector3 / matrix) -- garbage metadata downstream tools may interpret as color | Changing the hardcoded literal without doing the gamma-consultation work -- the TODO retirement | Inventing a `colorSpace` metadata on the linear-by-spec value -- misleading or actively wrong depending on the renderer |

The three writers serve three different output schemas
(MaterialX → UsdShade; UsdPreviewSurface bake → UsdUVTexture;
UsdPreviewSurface value-only → no texture). Each schema's convention
is what its consuming renderers expect. A wildcard "unify color-
space handling" refactor that ported any one writer's convention to
another would either:

* silently strip artist-authored `colorSpace` from MaterialX inputs
  (because UsdUVTexture's `sourceColorSpace` is a token input, not a
  USD-attr metadata, so the convention does not transfer 1:1);
* author a USD-attr `colorSpace` on a UsdUVTexture's file Asset input
  (which UsdPreviewSurface ignores at render time, so the metadata
  would be dead bytes BUT it would diverge from the renderer-honored
  `sourceColorSpace` token input on the same UsdUVTexture, creating
  a self-inconsistent shading prim); or
* invent `sourceColorSpace` / `colorSpace` on the LastResort writer's
  value-only `diffuseColor` (which the UsdPreviewSurface spec says is
  linear by definition -- the tag is at best ignored, at worst
  honored inconsistently across renderers, producing visible
  appearance drift the artist did not author).

**Fix.** None. The existing C++ + Python is already correct (modulo
the acknowledged-incomplete `usd_material_writer.py` TODO whose
retirement is a SEPARATE future bite). The audit lands:

1. **Surgical-bounds comment block** at three sites:
   * `MtlxShaderWriter.cpp::_SetInputValue` -- documents the two
     guards (`_TypeSupportsColorSpace(input)` AND
     `!colorSpace.empty()`) and the visible bound (a wildcard widening
     causes the visual auditor pair's LEFT plane to render the same
     mid-grey as the RIGHT plane).
   * `usd_material_writer.py::set_bitmap_scale_bias_sourcecolorspace`
     -- documents the hardcoded "raw" as the surgical bound the
     validator pins, the retirement condition (consult
     `from_tex.bitmap.gamma`, map gamma>=2.2 to "sRGB" for color
     targets, gamma=1.0 to "raw" for color and non-color), and the
     forward-pointer to which future MAX-MAT-* would retire it.
   * `LastResortUSDPreviewSurfaceWriter::Write` -- documents the
     value-only contract (NO UsdUVTexture, NO sourceColorSpace, NO
     colorSpace metadata) per UsdPreviewSurface spec.

2. **MaxScript regression**
   `test_export_material_preserves_color_space_metadata_on_color3_inputs`
   in `src/Tests/Integration/mtlxShaderWriter_test.ms`. Loads
   `src/Tests/Integration/data/intentional_color_space_test/intentional_color_space.mtlx`
   (a synthetic `standard_surface` whose `base_color` color3 input
   AND three float-typed inputs (`base`, `specular_roughness`,
   `specular_IOR`) all carry `colorspace="srgb_texture"`). Imports
   via `MaterialXMaterial.importMaterial`, exports through
   `USDExporter` to a USD file, and asserts:
   * **Positive control** — the color3 `base_color` USD attribute
     carries `colorSpace = "srgb_texture"` metadata. The
     MaterialX-side `colorspace` attribute survived parsing,
     `getActiveColorSpace()` resolved to "srgb_texture", and
     `_SetInputValue`'s propagation ran.
   * **Negative control 1/2/3** — the float-typed `base`,
     `specular_roughness`, `specular_IOR` inputs, even though they
     ALSO carry `colorspace="srgb_texture"` on the source MaterialX
     side, MUST NEVER carry `colorSpace` USD metadata on the
     exported attribute. `_TypeSupportsColorSpace` rejects float-typed
     inputs and short-circuits the propagation. The permissive shape
     (absent OR present-with-empty-colorSpace) accommodates
     MAX-MAT-006's parallel spec-default strip on the
     nodedef-default values (0.8 / 0.2 / 1.5). Gated on Max 2025+
     (`maxver[1] >= 26900`).

3. **Python validator** at
   `/Users/d.smith/MirisProjects/Agent Builder/agent/arch-builds/a247e60f-cc09-4449-987c-3a7ecaaa88da/validate_color_space_roundtrip_surgical.py`.
   Builds a synthetic USD stage mirroring exactly what the three
   writers produce, then asserts 10 named cases enumerating each
   surgical bound:

   | Case in synthetic fixture | Surgical bound exercised |
   | --- | --- |
   | `Color3WithSRGB` | color3 + non-empty active color space → `colorSpace` USD metadata propagated |
   | `Color3NoColorSpace` | color3 + empty active color space → NO `colorSpace` metadata (the `if (!colorSpace.empty())` guard) |
   | `FloatInputWithSRGB` | float-typed input → NEVER receives `colorSpace`, regardless of active color space (the `_TypeSupportsColorSpace` predicate) |
   | `Color4WithSRGB` | color4 + non-empty → `colorSpace` propagated (predicate accepts color3 AND color4) |
   | `FilenameOnImageColor3` | filename input on image node with color3 output → `colorSpace` propagated |
   | `FilenameOnImageVector3` | filename input on image node with vector3 output (normalmap) → NO `colorSpace` (predicate's filename-on-color-output check) |
   | `BakedDiffuseMap_sourceColorSpace_raw` | Python-baked UsdUVTexture → `sourceColorSpace = "raw"` hardcode; NO `colorSpace` metadata on the file Asset input |
   | `BakedNormalMap_scale_bias` | same hardcoded "raw" plus normal-map scale (2,2,2,1) + bias (-1,-1,-1,0) |
   | `LastResort_no_colorSpace_anywhere` | value-only diffuseColor: NO UsdUVTexture child, NO `colorSpace` USD metadata on the diffuseColor attribute |
   | `CrossWriter_divergence` | all three writers preserve their own convention on the same exported scene -- the MaterialX shader carries `colorSpace="srgb_texture"`, the UsdUVTexture carries `sourceColorSpace="raw"`, the LastResort shader carries neither and has no UsdUVTexture child |

   Plus an idempotence check (a second pass over the same fixture
   yields identical PASS state on all 10 cases). All 10 cases +
   idempotence pass on the 2026-06-26 baseline, locking in the
   surgical bound. A future regression that widened any of the three
   writers' color-space conventions would fail by named case.

**Bounds (where the C++ + Python today conservatively does nothing):**

* `_TypeSupportsColorSpace` iterates EXACTLY: `color3` value input,
  `color4` value input, `filename` input on an image node whose
  nodedef output is `color3` / `color4`. Every other input type or
  parent-node category is filtered out. The C++ does not consult
  `usd_material_writer.py` and does not share state with it.
* `_SetInputValue`'s `if (!colorSpace.empty())` guard fires BEFORE
  the actual `SetColorSpace` call -- empty-string active color space
  means "no opinion authored", and the C++ deliberately does not
  author the empty string. A widened guard would create
  `HasAuthoredColorSpace() == true` with `GetColorSpace() == ""`,
  semantically distinct from the no-metadata state.
* `set_bitmap_scale_bias_sourcecolorspace` hardcodes "raw" with an
  explicit TODO. The Python writer does not consult MaterialX's
  active color space (there is no MaterialX document on this path),
  does not share helper code with the MaterialX writer, and does
  not propagate `colorSpace` USD metadata anywhere -- the
  UsdPreviewSurface convention is the `sourceColorSpace` token
  input only.
* `LastResortUSDPreviewSurfaceWriter::Write` does not invoke either
  of the above. It authors a Color3f value attribute and nothing
  else; no UsdUVTexture child is created so the `sourceColorSpace`
  convention does not apply, and tagging the value attribute with
  `colorSpace` would either be ignored (linear-by-spec) or actively
  wrong (would diverge from UsdPreviewSurface renderer expectations).

**Visual demonstration of the surgical bound.** Two flat planes
side-by-side, each carrying a `standard_surface` shader with
`base_color = (0.5, 0.5, 0.5)` -- numerically identical. The only
difference: the LEFT plane's `base_color` attribute has
`colorSpace = "srgb_texture"` USD metadata authored; the RIGHT plane
has no `colorSpace`. Lit by a DistantLight + DomeLight and rendered
in Karma CPU at 512x341.

`render_karma_postfix.png` (the current correct behavior): the LEFT
plane renders visibly DARKER than the RIGHT plane because Karma
honors the `colorSpace = "srgb_texture"` tag and applies the inverse
EOTF on the LEFT plane's linear value
(`pow(0.5, 2.2) ~= 0.218`), while the RIGHT plane sees the linear
value `0.5` directly. Measured per-channel means over each half of
the frame:

```
LEFT half (sRGB-tagged)         mean  R=0.1598  G=0.1598  B=0.1598
RIGHT half (untagged)           mean  R=0.2352  G=0.2352  B=0.2352
RIGHT - LEFT                    mean  R=+0.0753 G=+0.0753 B=+0.0753  (~+19/255, all 3 channels, monotone)
PNG SHA-256                     8100c2c7...
```

`render_unreal_reference.png` (the "still-broken" wildcard-widening
counterfactual): the LEFT plane's `colorSpace` metadata has been
stripped. Both planes render as a UNIFORM mid-grey -- the LEFT
plane is no longer darker than the RIGHT:

```
LEFT half (untagged)            mean  R=0.2352  G=0.2352  B=0.2352
RIGHT half (untagged)           mean  R=0.2352  G=0.2352  B=0.2352
RIGHT - LEFT                    mean  R=0.0000  G=0.0000  B=0.0000   (uniform, undetectable)
PNG SHA-256                     9127fbc3...
```

The two PNGs differ by SHA-256 and visibly so. Composite at
`compare_side_by_side.png` in the same arch-build dir. **The
auditor's checklist: the postfix render's LEFT plane reads darker
than its RIGHT plane (RIGHT - LEFT > 0 on all channels); the
still-broken reference's two planes render byte-identical (RIGHT -
LEFT == 0 on all channels); the two PNGs have distinct SHA-256s.**
Unlike MAX-MAT-007's SSS-lobe-diluted bound, this delta is large and
unambiguous because Karma's inverse-EOTF on a mid-grey produces a
~32% relative intensity drop on the rendered byte values.

**Retirement condition.** This audit does not have an upstream-fix
retirement condition. The bound it locks in is a SCOPE limit on
Miris-authored C++ + Python: the three writers' color-space
conventions stay distinct, with each writer authoring only its own
target schema's convention. Retirement requires a SEPARATE future
MAX-MAT-* bite (with its own captured corpus, MaxScript regression,
doc entry, and validator) that bridges any two of the three
writers' color-space conventions -- not a wildcard widening of the
existing predicates. The specific known retirement work the audit's
Python writer comment block points at is the gamma-consultation
swap in `set_bitmap_scale_bias_sourcecolorspace`: a future MAX-MAT-*
that reads `from_tex.bitmap.gamma`, maps gamma>=2.2 to
`sourceColorSpace = "sRGB"` for color-bearing inputs, and maps
gamma=1.0 to `sourceColorSpace = "raw"` for all targets -- with a
new MaxScript test case in `export_texture_test.py` exercising an
sRGB-gamma diffuse map. Until that bite lands, the hardcoded "raw"
is the surgical bound.

### Material-instance / Multi-Mtl override-structure fidelity (audit, four-branch decision tree + three-shape MtlSwitcher writer STOP HERE) (MAX-MAT-010)

**Symptom.** The 3ds Max -> USD exporter has TWO file paths that
together preserve per-instance material override structure when
multiple Max nodes share one Object (USD-instance pair) but each
Node carries a different `.material` assignment:

1. **`src/MaxUsd/Translators/ShadingUtils.cpp::_AddInstancePrimsToMaterialMap`**
   -- the four-branch decision tree that picks how to author
   material bindings when several USD instances share a single
   prototype:

       Branch A -- sameMaterialForAllInstances == true
         -> KEEP instancing; bind on the inheritance-base prim's
            child (the prototype's master mesh). USD composition
            propagates the binding to every instance.

       Branch B -- different materials, non-MultiMtl
         -> KEEP instancing; per-instance MaterialBindingAPI on
            the instance prim's local spec. The override-side
            opinion wins at composition time over the prototype's.

       Branch C -- different materials, MultiMtl, NO subsets on
                   the prototype's child (single-matId case)
         -> KEEP instancing;
            `_AddPrimWithMultiMaterialtoMaterialMap` takes the
            no-subset sub-branch and looks up the matching
            sub-material via `customData[3dsmax:matId]` on the
            prototype child, binding the result on the instance
            prim's path.

       Branch D -- different materials, MultiMtl, subsets DO exist
                   on the prototype's child
         -> BREAK instancing via `BreakInstancingAndCopySubset`:
            `instancePrim.SetInstanceable(false)`, then copy the
            prototype's `materialBind`-family GeomSubsets onto a
            new override-child Mesh prim under the divergent
            instance with `customData[3dsmax:matId]` preserved,
            then rebind each copied subset to the divergent
            MultiMtl's sub-material. This is the KEY bound the
            audit pins; a regression here silently DROPS per-
            instance MultiMtl overrides on every export.

2. **`src/translators/MtlSwitcherWriter.cpp::Write` / `::PostWrite`**
   + **`src/translators/MultiMaterialUtils.cpp::DiscoverMaterialIDsAndCreateBundles`**
   -- the parallel switcher-driven path that turns a Max 2024+
   `MaterialSwitcher` (`MATERIAL_SWITCHER_CLASS_ID`) into a
   `UsdShade.Material` carrying THREE override-structure shapes:

       Branch E -- AsVariantSets export style, >=2 variants
         -> author a `shadingVariant` UsdVariantSet on the switcher
            material prim; for each candidate material, AddVariant
            + SetVariantSelection + AddInternalReference inside the
            variant edit context, then default the selection to the
            active material's variantName. MultiMtl-nested-in-
            switcher cases run `BindPlaceholderMatsToGeom` +
            `BindVariantBundleToMat` to author per-MatID placeholder
            material prims that get referenced INTO each variant.

       Branch F -- ActiveMaterialOnly OR 1-variant AsVariantSets
                   fallback (the constructor's
                   `variantMaterials.size() == 1` short-circuit)
         -> NO variant set; single `AddInternalReference` to the
            active material at the switcher material level.

       Branch G -- empty switcher (`variantMaterials.empty()`)
         -> emit `MaxUsd::Log::Warn` describing the empty switcher
            AND early-return. Bare `UsdShade.Material` prim
            remains; NO variant set authored, NO references
            authored. The Material prim itself MUST be defined
            (the writer-registry path creates it before `Write()`
            runs); the empty short-circuit deliberately does NOT
            author placeholder references.

The planner auto-emitted `material-instance-override-fidelity` with
severity High and rationale "Preserve Max sub-material / multi-
material override structure as USD MaterialX nodegraph +
references" without a captured corpus of mistranslations on this
fork (the existing diagnostic corpus is single-mesh / single-
material, no instance-divergence, no switcher containers). The
literal reading would be a "unify override handling" refactor
collapsing the four ShadingUtils branches and the three
MtlSwitcher branches onto a single helper.

**Why it matters.** Each branch's behavior is correct for its own
input shape, but the seven shapes DIVERGE on every axis a unification
refactor would have to bridge:

| Concept | Branch A (same-mtl-all) | Branch B (diff-non-Multi) | Branch C (diff-Multi-no-subsets) | Branch D (diff-Multi-with-subsets) | Branch E (switcher AsVariantSets >=2) | Branch F (switcher single) | Branch G (switcher empty) |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Instancing preserved | YES (bind on prototype) | YES (bind on instance) | YES (matId via customData) | **NO -- BROKEN** (KEY bound) | N/A (no instancing) | N/A | N/A |
| Authoring point | Prototype child mesh | Instance prim local spec | Instance prim local spec | Override-child Mesh prim under instance | Variant edit context inside `shadingVariant` set | Switcher material prim directly | (none -- bare prim) |
| Reference shape | `material:binding` rel to material prim | `material:binding` rel to material prim | `material:binding` rel via `customData[3dsmax:matId]` | `material:binding` rel on COPIED subsets under override-child mesh | `AddInternalReference` inside each variant + per-MatID placeholder material prims (if MultiMtl-nested) | Single `AddInternalReference` to active material | (none -- bare prim) |
| Variant set | (none -- not a switcher) | (none -- not a switcher) | (none -- not a switcher) | (none -- not a switcher) | `shadingVariant` UsdVariantSet, 1 variant per candidate | (none) | (none) |
| Subset copy | (none) | (none) | (none -- no subsets to copy) | **REQUIRED** with matId customData preserved | (only inside Branch D nested inside switcher's MultiMtl case) | (only inside Branch D nested inside switcher's MultiMtl case) | (none) |
| Failure mode of a wildcard widening | "Always break instancing" bloats every stage with N-times the master meshes -- USD instancing optimization lost | "Always break instancing" or "always author on instance" both bloat stages with extra structural noise the asset didn't need | "Always break instancing" causes a subset-copy that has nothing to copy -- crashes or empty-subset desync | **Dropping `BreakInstancingAndCopySubset` silently LOSES per-instance MultiMtl overrides -- data-loss vector** | Authoring references OUTSIDE the variant edit context collapses every candidate onto a single composition layer, losing variant semantics | Authoring an empty 1-variant `shadingVariant` set bloats single-material switcher exports + creates "select one of one" UX downstream tools can't act on | Authoring placeholder references on the empty case either creates composition-time errors (unresolvable paths) or silently binds every empty switcher to a sentinel default material the Max source never intended |

The seven branches serve three different USD primitives
(instancing-preserving vs instancing-breaking on the prim-instancing
axis; per-instance MaterialBindingAPI vs variant set on the override-
carrier axis; reference target = material prim vs reference target =
placeholder material prim on the switcher-MultiMtl-nested axis). A
wildcard "unify override handling" refactor that ported any one
branch's shape to the others would either:

* silently drop USD instancing on the common case (regressing
  Branches A/B/C onto Branch D's instancing-broken shape);
* silently drop per-instance MultiMtl overrides on the data-loss
  case (regressing Branch D onto Branch A/B/C's no-break shape and
  losing the divergent MultiMtl's sub-material binding entirely);
* silently bloat every single-material switcher into a 1-variant
  variant set (regressing Branch F onto Branch E's variant-set
  shape); or
* silently bind every empty switcher to a sentinel default
  (regressing Branch G onto Branch F's single-reference shape).

**Fix.** None. The existing C++ code is already correct (the four-
branch decision tree in `_AddInstancePrimsToMaterialMap`, the
parallel instance-break path in `DiscoverMaterialIDsAndCreateBundles`,
and the three-shape switcher writer all preserve their own surgical
bound). The audit lands:

1. **Surgical-bounds comment block** at three sites:
   * `ShadingUtils.cpp` above `_AddInstancePrimsToMaterialMap` --
     enumerates the four Branches A/B/C/D with the action taken on
     each, the surgical bound preserved, the data-loss / bloat /
     desync failure modes a widening would introduce, and the
     validator case mapping.
   * `MultiMaterialUtils.cpp` above `DiscoverMaterialIDsAndCreateBundles`
     -- enumerates the two Switcher-Branches (A and D) that mirror
     the parent decision tree under a switcher context, plus the
     two-file-split rationale (why the switcher pipeline can't
     share the per-instance-binding path with the non-switcher
     pipeline -- bundle creation needs the subset-copy to happen
     INLINE before matID-set discovery downstream).
   * `MtlSwitcherWriter.cpp` above `Write()` -- enumerates the three
     switcher-shape Branches E/F/G with the action taken, the
     surgical bound preserved, the failure modes a widening would
     introduce, and the validator case mapping. Plus the nested-
     Multi "directly connected to an object" precondition that
     gates whether the function runs at all.

2. **MaxScript regression** `test_material_instance_override_fidelity_audit`
   in `src/Tests/Integration/export_instance_test.ms`. Builds two
   Boxes with `create_clone <box1> #instance "box2"` (USD instance
   pair) and assigns DIFFERENT MultiMtls to each (`box1.material =
   multiA_red_blue`, `box2.material = multiB_green_yellow`, both
   with half-and-half matId partitions). Exports through
   `USDExporter` and asserts:
   * **Surgical bound 1 / Branch A control** -- `/box1` keeps
     instancing (`IsInstanceable() == True`). A regression that
     turned this assertion would mean the writer has started
     always-breaking instancing on every divergent-material case,
     regressing the entire four-branch tree onto a single "always
     break" shape.
   * **Surgical bound 2 / Branch D KEY bound** -- `/box2` has
     `IsInstanceable() == False` (the `SetInstanceable(false)` from
     `BreakInstancingAndCopySubset` landed). If this assertion
     fails, the C++ has dropped the instancing-break and per-
     instance MultiMtl overrides are being silently LOST on every
     export -- the data-loss vector this audit primarily pins.
   * **Surgical bound 3 / override prim authored** -- `/box2`
     carries an override-child Mesh prim that hosts the copied
     subsets (the `BreakInstancingAndCopySubset` step (ii) -- a
     mesh prim must exist under the divergent instance to anchor
     the per-instance bindings).
   * **Surgical bound 4 / customData preserved** -- the
     `MaterialBindingAPI.GetMaterialBindSubsets()` on the override-
     child mesh returns TWO subsets each carrying
     `customData[3dsmax:matId]` preserved across the
     `BreakInstancingAndCopySubset` copy. The `SubsetInfo` ctor's
     +1/-1 round-trip keeps the matId value byte-identical with
     the prototype's.

3. **Python validator** at
   `/Users/d.smith/MirisProjects/Agent Builder/agent/arch-builds/1a92bee3-23b8-4465-93cf-122a6758ad16/validate_material_instance_override_surgical.py`.
   Builds a synthetic USD stage mirroring exactly what the C++
   code paths produce across all seven branches in ONE fixture,
   then asserts each branch's invariant by named case + a final
   `CrossPath_Divergence` simultaneous-invariants case + an
   idempotence check:

   | Case in synthetic fixture | Surgical bound exercised |
   | --- | --- |
   | `SameMaterial_KeepInstancing` | Branch A -- two instances both `IsInstanceable() == True`, prototype carries the shared binding on its own local spec; per-instance spec MUST NOT author a local `material:binding` relationship |
   | `DifferentMaterial_NonMulti` | Branch B -- both instances `IsInstanceable() == True`; divergent instance carries a per-instance `material:binding` rel on its own local spec to the divergent material; non-divergent instance does NOT author a local rel |
   | `DifferentMaterial_MultiNoSubsets` | Branch C -- both instances `IsInstanceable() == True`; prototype carries `customData[3dsmax:matId]` for the no-subset MultiMtl-resolution path; divergent instance carries per-instance `material:binding` on its own local spec |
   | `DifferentMaterial_MultiWithSubsets` | Branch D (KEY bound) -- non-divergent instance `IsInstanceable() == True`; divergent instance `IsInstanceable() == False`; override-child mesh prim under divergent instance hosts COPIED subsets (named matching the prototype's: `mat_1`, `mat_2`) with `customData[3dsmax:matId]` preserved (1 and 2) and original indices preserved ([0,1,2] and [3,4,5]); each copied subset is bound to the divergent MultiMtl's sub-material |
   | `Switcher_VariantSets_TwoMaterials` | Branch E -- `shadingVariant` UsdVariantSet authored with one variant per candidate material; each variant's edit context adds an `AddInternalReference` to the matching material prim; active variant default is preserved |
   | `Switcher_ActiveOnly_OneMaterial` | Branch F -- NO variant set authored; single `AddInternalReference` to active material via the switcher material's `GetPrim().GetReferences()` |
   | `Switcher_Empty_NoVariantSet` | Branch G -- bare `UsdShadeMaterial` prim, NO variant set, NO internal references, but the Material prim itself MUST exist (the writer-registry creates it before `Write()` runs and the empty short-circuit deliberately does NOT delete it) |
   | `CrossPath_Divergence` | All seven branches preserve their own convention simultaneously on the same fixture -- a unification refactor that bridged any two branches would either break their own conventions (caught by per-branch cases above) OR violate the simultaneous-invariants assertion here |

   Plus an idempotence check (a second pass over the same fixture
   yields identical PASS state on all 8 cases). All 8 cases +
   idempotence pass on the 2026-06-26 baseline against pxr.Usd
   0.25.5, locking in the cross-branch surgical bound. A future
   regression that widened any of the seven branches would fail by
   named case.

**Bounds (where the C++ today conservatively does nothing):**

* `_AddInstancePrimsToMaterialMap`'s `sameMaterialForAllInstances`
  gate fires on a per-prototype basis. The walk over
  `instancePrims.begin() + 1` is short-circuit-friendly (one
  divergent instance flips the bit). Every divergent-instance
  branch (B/C/D) authors only on the divergent instances; non-
  divergent instances inherit silently from the prototype. The
  gate does NOT widen to a "per-instance authoring on every
  instance" shape even when convenient -- the same-material case
  is the common case and the optimization matters.
* The `MultiMtl* multiMaterial = dynamic_cast<MultiMtl*>(material)`
  cast in Branches B vs C/D is exact -- a non-MultiMtl that
  happens to expose `IsMultiMtl() == true` via a custom Mtl
  subclass would still hit Branch B because the cast returns
  nullptr. The audit pins the cast as the divergence gate.
* `BreakInstancingAndCopySubset` is invoked ONLY when the prototype
  child carries non-empty `materialBind`-family subsets. The
  no-subset MultiMtl case (Branch C) intentionally does NOT break
  instancing -- there's nothing to copy, and the no-subset
  sub-branch in `_AddPrimWithMultiMaterialtoMaterialMap` resolves
  the matId via the prototype's customData. A widening that ran
  the break unconditionally on every MultiMtl divergence would
  produce empty override-child Mesh prims with no subsets, which
  USD composition would happily author but downstream consumers
  would misread as "no per-instance override".
* The `MtlSwitcherWriter` constructor's
  `variantMaterials.size() == 1 && AsVariantSets` short-circuit
  (lines 43-46) silently rewrites the export style to
  `ActiveMaterialOnly` so Branch E never authors a 1-variant
  `shadingVariant` set. This is deliberate: a 1-variant variant
  set is a UI/UX anti-pattern downstream variant-aware tools
  cannot meaningfully act on.
* `MtlSwitcherWriter::Write`'s empty-switcher short-circuit
  (lines 51-58, with the post-MAX-MAT-010 comment block) emits a
  `Log::Warn` BEFORE the early-return. The warning is observability
  for the artist; the early-return is the writer-side contract
  that no further authoring happens. The bare Material prim that
  the writer-registry pre-defined remains so downstream tools
  that walked the prim before `Write()` ran see a valid (if empty)
  Material -- the empty short-circuit does not retroactively
  delete the prim.

**Visual demonstration of the surgical bound.** Two concrete cube
meshes (`/Box_1`, `/Box_2`) side-by-side at worldspace +/-0.75 on X,
each carrying TWO `materialBind`-family `UsdGeomSubset`s mirroring a
3ds Max MultiMtl-bound half-and-half matId partition. The postfix
stage authors `/Box_2`'s subsets bound to GREEN + YELLOW (the
per-instance override LANDED); the still-broken stage authors
`/Box_2`'s subsets bound to RED + BLUE (the override was LOST;
`/Box_2` silently inherits the prototype's bindings). Lit by two
`UsdLuxDistantLight`s and rendered in Karma CPU at 512x373 via
`usdrecord --renderer "Karma CPU"`.

`render_karma_postfix.png` (the current correct behavior): the
LEFT half of the frame (BOX_1) reads RED on the front-three faces
and BLUE on the top three; the RIGHT half (BOX_2) reads GREEN on
the front-three faces and YELLOW on the top three -- the per-
instance override is observable as a distinct color shape on the
divergent instance. Measured per-half foreground channel means
over the visible cube pixels in each frame half:

```
POSTFIX  LEFT half  (BOX_1 red+blue):       R= 48.21  G= 20.26  B= 23.76
POSTFIX  RIGHT half (BOX_2 GREEN+YELLOW):   R= 24.10  G= 50.12  B= 25.87
PNG SHA-256                                 3fac6743...
```

`render_unreal_reference.png` (the "override silently lost"
wildcard-refactor counterfactual): `/Box_2`'s subsets are bound to
the same RED+BLUE materials as `/Box_1`. Both halves of the frame
read red-on-front + blue-on-top -- the frame is effectively
mirror-symmetric about the vertical center, two red-and-blue cubes:

```
STILL-BROKEN LEFT half  (BOX_1 red+blue, SAME):           R= 48.21  G= 20.25  B= 23.76
STILL-BROKEN RIGHT half (BOX_2 red+blue, OVERRIDE LOST):  R= 48.23  G= 20.26  B= 23.74
PNG SHA-256                                               817aef25...
```

Directional delta (postfix - still-broken):

```
LEFT half  (lighting/camera invariant control):  dR= -0.00  dG= +0.00  dB= +0.00
RIGHT half (override-induced color shift):       dR=-24.14  dG=+29.87  dB= +2.12
```

The LEFT-half delta is ~0 on every channel because BOX_1's
bindings are identical in both renders -- this is the
lighting/camera/sampling invariant control that confirms the
fixture itself hasn't drifted between renders. The RIGHT-half
delta is **large, monotone, and directional**: R drops by ~24/255
(red replaced by green), G rises by ~30/255 (green is the
dominant color on BOX_2's front faces), B is approximately
constant (~+2/255; both blue and yellow have moderate B). PNGs are
SHA-256-distinct. Composite at `compare_side_by_side.png`.
**Auditor's checklist: the postfix's RIGHT box reads visibly
GREEN + YELLOW while the still-broken's RIGHT box reads RED +
BLUE; both renders' LEFT boxes read RED + BLUE identically; the
RIGHT-half delta is monotone (R drops, G rises, B holds) with no
chromatic shift; the LEFT-half delta is bytewise zero; the two
PNGs have distinct SHA-256s.** A refactor that ever made the
postfix RIGHT box read red+blue OR made the still-broken RIGHT
box read green+yellow OR introduced a non-zero LEFT-half delta
would mean either per-instance override preservation has
regressed (the bound MAX-MAT-010 locks in has broken) or the
fixture itself drifted between renders.

**Retirement condition.** This audit does not have an upstream-fix
retirement condition. The bound it locks in is a SCOPE limit on
Miris-authored C++: the four-branch decision tree in
`_AddInstancePrimsToMaterialMap` + the parallel instance-break
path in `DiscoverMaterialIDsAndCreateBundles` + the three-shape
switcher writer in `MtlSwitcherWriter` stay distinct and each
preserves its own surgical bound. Retirement requires a SEPARATE
future MAX-MAT-* bite (with its own captured corpus, MaxScript
regression, doc entry, and validator) that genuinely needs to
unify any two of the seven branches -- not a wildcard widening of
the existing scope. A specific known retirement candidate: an
opt-in option (`exportOptions.preserveInstancingOnDivergentMultiMtl
= false`) that swaps Branch D's `BreakInstancingAndCopySubset` for
a "duplicate the prototype's mesh data on the divergent instance"
shape, useful for downstream consumers that strictly reject
non-instanceable prims; that swap is a separate concern with its
own audit shape and would land alongside the existing audit, NOT
as a replacement for it.

## Expressions with no MaterialX equivalent

| Source (3ds Max) | Why no equivalent | Behavior in current fork |
| --- | --- | --- |
| (none catalogued yet) | | |

## Change log

* 2026-06-20 — Initial doc. MAX-MAT-001 specular_rotation default
  normalization landed.
* 2026-06-26 — MAX-MAT-001 surgical-preservation reinforcement: add an
  8-case Python validator
  (`validate_specular_rotation_surgical.py`) covering each surgical
  bound the C++ gate enforces — the leak pattern (`rotation = 0.25`
  with aniso absent or aniso == 0), and the negative cases that must
  preserve the input (rotation value != 0.25; rotation connected;
  anisotropy connected; anisotropy statically non-zero; non-
  `standard_surface` shader) — plus idempotence; add a MaxScript
  regression (`test_export_material_preserves_intentional_specular_rotation`)
  that loads a synthetic `.mtlx` carrying brushed-steel
  `specular_rotation = 0.25`, `specular_anisotropy = 0.85`,
  `specular_roughness = 0.18` and asserts both values survive the
  round-trip + normalizer untouched — closing the previously-
  uncovered "BrushedSteel" surgical bound the original `a377982`
  commit message promised but the test suite never actually
  exercised; add a surgical-bounds comment block to
  `MtlxShaderWriter.cpp::_NormalizeStandardSurfaceSpecularRotation`
  enumerating each negative case the gate must reject and pointing
  back to the named regression test + Python validator for each; add
  a visual auditor pair (`render_karma_postfix.png` radial streaks =
  preserved; `render_unreal_reference.png` compact star = over-
  stripped) to make the surgical bound visible at the pixel level.
  No C++ logic change in this bite — the reinforcement is purely
  additive test infrastructure that locks in the surgical guarantee
  against future regressions.
* 2026-06-20 — MAX-MAT-002 emission/emission_color default-pair
  normalization landed (strip `emission = 1.0` paired with
  `emission_color = (0, 0, 0)`).
* 2026-06-23 — MAX-MAT-002 surgical-preservation reinforcement: add an
  8-case Python validator
  (`validate_emission_normalize_surgical.py`) covering each surgical
  bound the C++ gate enforces (off-leak color, off-leak scalar,
  half-leak, connected input, non-`standard_surface` shader, plus
  idempotence); add a MaxScript regression
  (`test_export_material_preserves_intentional_emission`) that loads a
  synthetic `.mtlx` carrying intentional `emission = 0.5`,
  `emission_color = (1.0, 0.3, 0.0)` and asserts the values survive
  the round-trip + normalizer untouched; strengthen the existing
  happy-path `.ms` test with a pair-level post-condition that the
  buggy `(1.0, (0, 0, 0))` pair cannot coexist on the same shader
  (catches the half-leak case where a future refactor might preserve
  one half of the buggy pair while correctly stripping the other);
  add a visual auditor pair
  (`render_karma_postfix.png` warm orange = preserved;
  `render_unreal_reference.png` neutral grey = over-stripped) to make
  the surgical bound visible at the pixel level. No C++ logic change
  in this bite — the reinforcement is purely additive test
  infrastructure that locks in the surgical guarantee against future
  regressions.
* 2026-06-26 — MAX-MAT-002 tolerance-band reinforcement (second layer):
  add a 6-case Python validator
  (`validate_emission_normalize_tolerance_bounds.py`) pinning the
  **numeric edges** of the C++ strip gate that the first reinforcement
  did not exercise — `SubEpsilonScalar` (must strip, within ε),
  `NearLeakScalar` (must preserve, just outside ε) on the scalar side
  and `SubEpsilonColor` (must strip), `SingleChannelDarkColor` (must
  preserve, per-component vs magnitude) on the color side, plus a
  `ConnectedEmission` case as the symmetric companion of the first
  reinforcement's `ConnectedColor`; add a MaxScript regression
  (`test_export_material_preserves_single_channel_dark_emission`) that
  loads a synthetic `.mtlx` carrying `emission = 1.0,
  emission_color = (0.05, 0, 0)` and asserts both inputs survive the
  per-component color gate untouched; extend the C++ surgical-bounds
  comment block to enumerate the new numeric bounds and point at both
  validators by name; add a visual auditor pair
  (`render_karma_postfix.png` warm pinkish wash = preserved;
  `render_unreal_reference.png` neutral grey = magnitude-refactor
  over-strip) showing the per-component edge case at the pixel level.
  No C++ logic change in this bite — purely additive test
  infrastructure stacked on top of the 2026-06-23 reinforcement.
* 2026-06-20 — MAX-MAT-003 mesh displayColor leak fixed (derive
  `primvars:displayColor` from the bound material's `GetDiffuse()`
  instead of the node's viewport wireframe color, falling back to the
  wireframe color only when no material is bound).
* 2026-06-23 — MAX-MAT-003 surgical-preservation reinforcement: add an
  8-case Python validator
  (`validate_display_color_surgical.py`) covering each branch the
  C++ decision selects (wire-color-fallback, mtl-diffuse,
  preauthored-preserved) under every combination of `(preauthored,
  bound mtl, MultiMtl, black/white diffuse, value coincidence)` plus
  idempotence; add a MaxScript regression
  (`test_display_color_preserves_authored_when_material_bound`) that
  builds a box with wireframe red, an artist-authored blue
  vertex-color displayColor via
  `SetChannelPrimvarMapping 0 "displayColor"`, AND a Standard material
  with green diffuse bound to the node, and asserts the exported
  `primvars:displayColor` is blue (the artist value), not green
  (the material diffuse) or red (the wireframe color) — the previously
  uncovered case where both the IsAuthored() gate AND a material are
  present; add a surgical-bounds comment block to
  `MeshConverter::ConvertToUSDMesh` enumerating each negative case the
  gate must reject and pointing back to the named regression test for
  each; add a visual auditor pair
  (`render_karma_postfix.png` blue = preserved;
  `render_unreal_reference.png` green = silently overwritten) to make
  the surgical bound visible at the pixel level. No C++ logic change
  in this bite — the reinforcement is purely additive test
  infrastructure that locks in the surgical guarantee against future
  regressions.
* 2026-06-26 — MAX-MAT-003 value-coincidence band reinforcement
  (second layer, stacked on the 2026-06-23 reinforcement): pin the
  IsAuthored() gate at the **value-coincidence edges** the first
  reinforcement's high-distinctness BLUE / YELLOW / MAGENTA fixtures
  do not exercise. Adds
  `test_display_color_preserves_default_looking_authored_value_when_material_bound`
  to `io_color_n_visibility_test.ms`, exercising authored
  `(0, 0, 0)` (the canonical "black default" sentinel) against a
  vivid red bound material — the case where a hypothetical "treat
  default-looking authored as effectively unauthored" widening would
  silently overwrite legitimate artist authoring. Python validator
  `validate_display_color_tolerance_bounds.py` covers the same bound
  at the USD layer alongside `AuthoredWhiteWithMtl` and
  `AuthoredMidGreyWithMtl` (the other two canonical sentinels),
  `AuthoredZeroNoMtl` (sentinel + no-mtl), `MtlDiffuseHDR` (channel
  > 1.0 — pins "no clamp"), and `MtlDiffuseTinyChannel`
  (`(1e-3, 0, 0)` — pins "no magnitude filter on diffuse"). All 6
  cases + idempotence pass on the 2026-06-26 baseline. Extends the
  C++ surgical-bounds comment block in
  `MeshConverter::ConvertToUSDMesh` to enumerate the new
  value-coincidence and diffuse-extreme bounds and points at both
  Python validators by name. Visual auditor pair
  (`render_karma_postfix.png` mid-grey sphere where the canonical
  sentinel `(0.5, 0.5, 0.5)` is preserved; `render_unreal_reference.png`
  vivid red sphere where the hypothetical sentinel-filter refactor
  over-wrote) makes the value-coincidence bound visible at the pixel
  level. No C++ logic change; purely additive test infrastructure
  stacked on top of the 2026-06-23 reinforcement.
* 2026-06-26 — MAX-MAT-003 primvar-shape band reinforcement (third
  layer, stacked on the value-coincidence reinforcement landed
  earlier the same day): pin the **primvar shape** the gate produces
  and preserves — array length, interpolation token, time-sample
  count. The first two reinforcements assert displayColor[0] and the
  branch label; neither catches a widening that broadcast the gate's
  output to a per-vertex primvar (displayColor[0] would still equal
  the bound material's diffuse, every existing test would pass,
  while file size inflates linearly with vertex count and Hydra-
  delegate behaviour changes). Adds
  `test_display_color_primvar_shape_invariants_on_gate_write` to
  `io_color_n_visibility_test.ms`, asserting `dispColor.count == 1`,
  `primvar.GetInterpolation() == "constant"`, and
  `displayColorAttr.GetNumTimeSamples() == 0` on the mtl-diffuse
  branch alongside the existing value-equality check. Python
  validator `validate_display_color_primvar_shape.py` covers the
  same bound at the USD layer with 8 cases — 4 gate-write cases
  (mtl-bound LDR / wire-color-fallback / HDR / near-zero, all
  pinning shape `(1, "constant", 0)`) plus 4 preauthored-preserved
  cases (vertex-interp 3-element / faceVarying-interp 6-element /
  constant len=1 / time-sampled, all pinning shape preservation
  symmetric to the artist's input). All 8 cases + idempotence pass
  on the 2026-06-26 baseline. Extends the C++ surgical-bounds
  comment block in `MeshConverter::ConvertToUSDMesh` to enumerate
  the new primvar-shape bounds on both the write and preserved
  branches and points at all three Python validators by name.
  Visual auditor pair (`render_karma_postfix.png` flat green sphere,
  the constant-interp len=1 shape; `render_unreal_reference.png`
  green-to-red gradient sphere, the per-vertex-broadcast shape a
  widening could produce) makes the shape distinction visible at
  the pixel level. No C++ logic change; purely additive test
  infrastructure stacked on top of the two earlier reinforcements.
* 2026-06-20 — MAX-GEO-001 mesh normals dual-author: add
  `NormalsMode::Both` and make it the new default so the writer
  populates both `primvars:normals` AND `UsdGeomMesh.normals` (the
  schema attribute). Existing `AsPrimvar` and `AsAttribute` selectors
  unchanged.
* 2026-06-26 — MAX-GEO-001 dual-carrier-parity reinforcement: pin
  the parity invariants the original `1ac2d1b` commit promised but
  the existing happy-path coverage did not exercise — under
  `NormalsMode::Both` both carriers share the same interpolation
  token, AND the schema attribute carries the *flattened* form of
  the data (face-vertex / vertex / 1 length), never the
  indexed-unique form, AND has no companion `normals:indices`
  sidecar. Adds `test_normals_both_mode_dual_carrier_parity` to
  `export_options_test.ms` exporting a Sphere (vertex interpolation
  — indexed primvar / flattened schema attribute) and a Box
  (faceVarying interpolation — alternate parity-invariant path),
  asserting all three parity invariants on each. Python validator
  `validate_dual_normals_surgical.py` covers the same surgical
  bounds at the USD layer with 8 named cases — every
  `NormalsMode` × interpolation × the two early-return cases
  (`#none` opt-out, `NormalCount() == 0`) — plus idempotence. Adds
  a surgical-bounds comment block to
  `MeshConverter::ApplyMaxNormals` distinguishing state-shape bounds
  (original `1ac2d1b` fix) from dual-carrier parity invariants
  (this reinforcement) and pointing at both the `.ms` regression
  and the Python validator by name. Visual auditor pair
  (`render_karma_postfix.png` smoothly-shaded icosphere where the
  schema attribute carries smooth per-vertex normals;
  `render_unreal_reference.png` heavily faceted icosphere with
  ~80 visible triangle facets where the schema attribute was
  never authored — both stages OMIT `primvars:normals` to simulate
  schema-attribute-only consumers like ARKit Quick Look) makes the
  surgical bound visible at the pixel level. No C++ logic change;
  purely additive test infrastructure.
* 2026-06-20 — MAX-GEO-004 fallback UV stream: backfill
  `primvars:st` (or the channel-1-configured primvar name) with a
  planar projection of the vertex positions whenever
  `ApplyMaxMapChannels` did not author it. Conservatively no-ops when
  the user explicitly opted out of channel-1 export, when real UVs
  already exist, or when the mesh is degenerate. Surfaces a
  `MaxUsd::Log::Warn` so the artist knows to enable `Generate Mapping
  Coords.` or apply a UVW Map modifier for accurate UVs.
* 2026-06-20 — MAX-GEO-002 ghost GeomSubset suppression: generalise
  the existing `materialIdToFacesMap.size() == 1` early-out in
  `MeshConverter::ApplyMaxMaterialIDs` to also short-circuit when the
  bound material is null or a non-MultiMtl, collapsing the per-face
  matId partition to a single `customData.3dsmax.matId` on the mesh
  prim. Avoids emitting `materialBind`-family GeomSubsets that carry
  no `material:binding` of their own (the common pattern on
  parametric primitives whose default face mat-IDs are not driven by
  a Multi/Sub-Object material). Surfaces a `MaxUsd::Log::Warn` so
  the artist knows the per-face partition was dropped and how to
  preserve it (bind a Multi/Sub-Object material at the source node).
* 2026-06-20 — MAX-CAM-001 camera clippingRange unconditional author:
  remove the `maxCamera->GetManualClip() != 0` gate in
  `CameraWriter::Write` so every exported camera authors an explicit
  `UsdGeomCamera.clippingRange` from `GetClipDist(...)`, instead of
  leaving the attribute unauthored and falling back to the
  `UsdGeomCamera` default of `(1.0, 1000000.0)`. The Max camera's
  clip distance values are stored on every camera regardless of the
  "Clip Manually" toggle; the toggle only controls whether Max's own
  renderer honours them, so honouring them in USD is correct in both
  cases. Degenerate values (≤ 0, NaN, far ≤ near) are sanity-clamped
  to `(1.0, 1000.0)` in scene units. The splines-mode warning that
  was previously inside the `GetManualClip()` branch is preserved
  with the same gate, because that is still the only path that can
  carry animated clip values. The existing integration test
  `test_default_physical_camera_attributes` in `io_camera_test.ms`,
  which previously codified the broken behaviour (asserted USD's
  `(1, 1000000)` fallback on a default-constructed `Physical`
  camera), now asserts that the writer emits the camera's stored
  `clip_near` / `clip_far` instead.
* 2026-06-23 — MAX-MAT-004 coat-block preset normalization: strip the
  four spurious coat-block inputs (`coat_IOR = 1.52`,
  `coat_affect_color = 0.5`, `coat_affect_roughness = 0.5`,
  `coat_roughness = 0.0`) that 3ds Max's `MtlxIOUtil` bridge emits on
  every `ND_standard_surface_surfaceshader` regardless of the source
  PhysicalMaterial, but only when `coat` is provably zero (absent or
  static 0.0) so the four values are visually inert under the current
  BSDF gate. Each input is tested and stripped independently — a user
  who explicitly overrides one of the four to a non-leak value keeps
  that override while the other three (still at their leak values) are
  removed. After normalization the stripped inputs fall back to the
  MaterialX `standard_surface` nodedef defaults (`coat_IOR = 1.5`,
  `coat_affect_color = 0.0`, `coat_affect_roughness = 0.0`,
  `coat_roughness = 0.1`), which is the correct "no coat was authored"
  state. The fix is visually inert while the coat lobe is gated off
  (Karma prefix-vs-postfix pixel delta is at or below the sampler-
  noise floor) but eliminates a latent regression that surfaces the
  moment any downstream context flips `coat > 0`: previously the
  override would silently inherit Max's opinionated coat profile
  rather than the cleaner nodedef defaults.
* 2026-06-23 — MAX-MAT-005 subsurface-radius preset normalization: strip
  the spurious `subsurface_radius = (0.794704, 0.531734, 0.292854)` triple
  (the Pixar/Hery caucasian-skin SSS radius) that 3ds Max's `MtlxIOUtil`
  bridge emits on every `ND_standard_surface_surfaceshader` regardless of
  the source PhysicalMaterial, but only when `subsurface` is provably zero
  (absent or static 0.0) so the triple is visually inert under the current
  BSDF gate. After normalization the stripped input falls back to the
  MaterialX `standard_surface` nodedef default `(1, 1, 1)`, a neutral
  white scatter radius — the correct "no SSS profile was authored" state.
  The fix is visually inert while the SSS lobe is gated off (Karma
  prefix-vs-postfix pixel delta sits at or below the sampler-noise floor)
  but eliminates a latent regression that surfaces the moment any
  downstream context flips `subsurface > 0`: previously the override
  would silently inherit Max's skin-tone RGB attenuation (red attenuates
  least, blue attenuates fastest — pink-red core on every scatter,
  regardless of base material) rather than the cleaner neutral nodedef
  default. The stressed-overlay Karma renders visualise the divergence
  end-to-end.
* 2026-06-23 — MAX-MAT-006 spec-default-input normalization: strip the 13
  inputs (`base`, `coat`, `coat_color`, `diffuse_roughness`, `specular`,
  `specular_color`, `subsurface`, `subsurface_color`, `subsurface_scale`,
  `thin_walled`, `transmission`, `transmission_color`,
  `transmission_depth`) that 3ds Max's `MtlxIOUtil` bridge writes on every
  `ND_standard_surface_surfaceshader` at the v1.0.1 nodedef-default value
  regardless of source intent. Each input is stripped independently when
  it equals the nodedef default exactly and is not connected; values an
  artist or upstream tool authored away from the default are preserved as
  intent. The fix is purely structural: 6 materials × 13 inputs = 78
  redundant attribute writes removed from the diagnostic corpus, ~30% of
  the per-material standard_surface attribute count. Karma renders are
  visually identical prefix vs postfix (within sampler-noise floor) by
  construction — stripped values equal the nodedef defaults the renderer
  resolves absent inputs to, so the BSDF math is unchanged. Unlike
  MAX-MAT-004/005 there is no latent-contamination case: the leaked
  values *are* the defaults, so even under downstream gate-flips
  (`coat > 0`, `subsurface > 0`) the resulting BSDF would evaluate
  identically with or without the redundant authoring. The benefit is
  reduced layer bloat, more legible `usddiff` output, and a clean
  "input is authored ⇔ artist had an opinion" invariant for downstream
  tools and variant sets.
* 2026-06-20 — MAX-GEO-003 GeomSubset name normalization: replace the
  legacy underscore-wrapped fallback pattern `_{N}_` in
  `MaterialUtils::CreateSubsetName` with the readable `mat_{N}` form.
  Applies to two branches: (a) null / non-Multi bound material; and
  (b) Multi/Sub-Object material with an empty slot name (the name
  becomes `mat_{N}_{subMtlName}` once the suffix is appended). The
  third branch — `MultiMtl` with a populated slot name — already used
  the slot name verbatim and is unchanged. Cosmetic only: subset
  names are opaque metadata to renderers (Karma SHA-256-identical
  prefix vs postfix) and the round-trip importer
  (`TranslatorMaterial.cpp`) consumes the name as a string. Older USD
  files with `_N_` subsets read back unchanged. Unit and ShellMtl
  integration tests updated to assert the new pattern; the
  back-compat read test (`import_material_id_test.ms`) still
  constructs synthetic `_N_` inputs and is intentionally unchanged.
* 2026-06-26 — MAX-UNIT-001 metersPerUnit export-time observability
  hint: add a `MaxUsd::Log::Warn` call in
  `USDSceneBuilder::BuildStage` immediately after
  `pxr::UsdGeomSetStageMetersPerUnit` that fires whenever the rounded
  source-scene scale is not exactly 1.0 (i.e. the 3ds Max system
  unit is not meters). The exported USD bytes are unchanged — the
  authored metersPerUnit value is USD-correct — but downstream
  meter-anchored consumers (Houdini Karma at 1:1, ARKit / Quick
  Look, Miris asset ingest [which REQUIRES metersPerUnit == 1],
  glTF importers) silently rescale or outright reject a
  not-in-meters export, and the artist had no export-time signal
  for that. The warning names: the metersPerUnit value, the
  implied 1-Max-unit-to-meter factor, the four consumers above,
  and both catalog workarounds (set Max's system unit to meters
  before exporting; or post-scale + rewrite metersPerUnit). The
  bound: `meters` exports stay silent (`stageScale == 1.0`
  within float-rounding tolerance). Validator
  (`validate_meters_per_unit_warning_surgical.py`) covers 9 cases
  — inches/mm/cm/km/2m-unit/half-feet positives, exact-meters /
  sub-epsilon-noise negatives, plus a 1.00001 case that locks
  both the 1e-9 gate epsilon and the rounding-then-gate ordering
  — plus idempotence and a check against the diagnostic baseline
  corpus. MaxScript regressions
  (`testInchesUnitsWarns`, `testMetersUnitsDoesNotWarn` in
  `export_metersPerUnit_test.ms`) assert the warning DOES fire on
  inches with the expected substrings AND does NOT fire on meters.
  Visual auditor pair: `render_karma_postfix.png` (the corpus
  referenced verbatim into a metersPerUnit=1 outer stage — Max
  asset blown up to ~39x size, dominates the frame) vs
  `render_unreal_reference.png` (the same composite with the
  workaround applied as an outer `xformOp:scale = 0.0254` — Max
  asset sits at proper meter scale next to three 1 m reference
  cubes). The auditor's checklist is the relative size of the
  Max asset versus the 1 m cubes; the two PNGs are
  SHA-256-distinct. Introduces a new "observability hint" Kind in
  the Status table for future export-time warnings that don't
  rewrite values.
* 2026-06-26 — MAX-LIT-002 photometric-light fidelity audit: add the
  first MAX-LIT entry to the mapping doc, covering BOTH the IES
  profile authoring branch and the Kelvin / filter-color dichotomy in
  `PhotometricLightWriter::Write`. The existing happy-path suite
  (`export_light_test.ms`) toggles `distribution = 3` (web/IES) on
  every photometric light-type test but never sets `.webFile`, so the
  entire `shaping:ies:file` authoring branch is uncovered; it asserts
  `enableColorTemperatureAttr` is true/false but never the
  `colorTemperatureAttr` value in static-frame export, never the
  filter-only color signal under `useKelvin == true`, never the
  combined-product color signal under `useKelvin == false`, and never
  the out-of-range Kelvin clamp. This audit pins all surgical bounds.
  Adds two MaxScript regressions to `export_light_test.ms`:
  `photometric_light_ies_file_export_test` (synthesizes a minimal
  IESNA LM-63 profile in the temp dir, exports under `distribution =
  WEB_DIST`, asserts `shaping:ies:file` is authored; re-exports under
  `distribution = ISOTROPIC`, asserts it is NOT authored even though
  `.webFile` is still set; constructs a second light under WEB_DIST
  but no `.webFile`, asserts `shaping:ies:file` is NOT authored — all
  three surgical bounds in the IES branch) and
  `photometric_light_kelvin_filter_color_round_trip_test` (constructs
  a light with `useKelvin = true`, magenta `rgb`, warm-orange
  `filterColor`, kelvin=3200; asserts enable=true, ct=3200,
  inputs:color = filter only — NOT the combined product; flips
  `useKelvin = false`, asserts enable=false, no ct authored,
  inputs:color = lightColor * filterColor combined; flips kelvin to
  12000 and 500 and asserts clamp to 10000 and 1000 respectively).
  Adds two surgical-bounds comment blocks to
  `PhotometricLightWriter.cpp` — one over the WEB_DIST branch
  enumerating the distribution and asset-id gates, one over the
  Kelvin dichotomy enumerating both halves of the
  `enable`/`colorTemperature`/`inputs:color` contract — each pointing
  back to the named MaxScript regression and the Python validator.
  Python validator
  (`validate_photometric_light_fidelity.py`, 8 cases + idempotence)
  mirrors the C++ decision over a synthetic fixture covering both
  branches: 4 cases for the IES gate (isotropic-no-IES,
  web-with-valid-asset, web-without-webFile, spot-authors-cone-not-IES)
  and 4 for the Kelvin dichotomy (filter-only color, combined-product
  color, clamp-high, clamp-low). Visual auditor pair
  (`render_karma_postfix.png` two clearly-distinct pools — warm
  orange 2700K + cool azure 9000K — on a grey wall, blackbody
  contrast reads as tungsten-vs-daylight at a glance;
  `render_unreal_reference.png` the same scene with
  `colorTemperatureAttr` + `enableColorTemperatureAttr` stripped —
  the blackbody contribution is gone and both pools collapse to
  faint near-neutral cream / pale-blue from the residual filter
  tint) makes the Kelvin surgical bound visible at the pixel level
  (Karma 21.0.700's IES support varies, so the visual demonstration
  focuses on the more reliably-renderable Kelvin observable; the
  Python validator covers BOTH branches). No C++ logic change in
  this bite — the audit is purely additive observability + test +
  doc infrastructure that locks in the two existing surgical
  guarantees.
* 2026-06-26 — MAX-PKG-001 USDZ-packaging headless fidelity audit:
  introduce the FIRST MAX-PKG entry to the mapping doc, covering the
  `.usdz` export path in `USDIOController::Export` and the parallel
  `USDSceneController::Export`. Today both branches route through
  `MaxUsd::UsdToolsUtils::RunUsdZip`, a four-process shim
  (`cmd.exe -> powershell.exe -file RunUsdTool.ps1 UsdZip
  -> python.exe UsdToolWrapper.py UsdZip -> usdzip`) that silently
  fails under four headless-hostile conditions the existing test suite
  does not exercise — PowerShell `Restricted` ExecutionPolicy, missing
  `HKLM:\SOFTWARE\Autodesk\3dsMax\*` registry entries on portable / per-
  user installs, `CreateProcess + SW_HIDE` under a service account
  (`3dsmaxbatch` from a Windows service or render-farm worker), and
  unicode in `getDir #temp`. The proposed in-process replacement
  (`pxr::UsdUtilsCreateNewUsdzPackage`) eliminates all four failure
  modes in one bite. The audit is purely additive: it pins the surgical
  bounds the future in-process swap MUST preserve, lands the Python
  validator that demonstrates the equivalence at the USD layer, lands
  MaxScript regressions for the headless export path, and lands a
  visual auditor pair showing packaging is lossless — without
  introducing C++ logic that would ship unverified on this Mac. The
  swap itself is held for a follow-on bite that can run on a Windows
  build host. Adds a long-form surgical-bounds comment block to
  `USDIOController::Export` at the `isUSDZExport` branch enumerating
  every bound the in-process swap must preserve (nine, each pointing at
  one named Python case + one MaxScript regression), and a back-pointer
  comment block in `USDSceneController::Export` to the matching write-
  up. Python validator
  (`validate_usdz_packaging_fidelity.py`, 9 cases + idempotence) covers:
  SimpleNoAssets, WithImageDep, ARKitSingleLayer, SublayerComposition
  (non-ARKit preserves sublayers, ARKit flattens), UnicodeAssetName,
  MissingAssetRefuses (the in-process API raises `Tf.ErrorException` on
  missing assets — STRICTER than the legacy `usdzip` CLI which silently
  produces a corrupt .usdz; the call site MUST clean up the orphan
  partial .usdz on the failure path), AlreadyZippedInput (`CreateNewUsdzPackage`
  on a `.usdz` input recursively repackages — the call site MUST pass
  the temp `.usd` path it just authored, NEVER the user-supplied `.usdz`
  target), UsdzMemberOrder (root layer is the first zip member),
  Idempotence. All 9 cases + the idempotence check pass on the
  2026-06-26 baseline against `pxr.Usd 0.25.5` -- the equivalence proof
  the in-process swap can land on. MaxScript regressions
  (`test_usdz_headless_packaging_round_trip` exercises the
  `exportFile foo.usdz #noprompt` path -- the same code path
  `3dsmaxbatch` takes -- asserts a valid zip with the root layer FIRST
  and the texture asset bundled; `test_usdz_headless_failure_surfaces_status`
  asserts the failure contract -- a `#noprompt` export to an unreachable
  path must surface failure via the returned status, never silently
  report success with no file on disk) added to `export_usdz_test.ms`.
  Visual auditor pair (`render_karma_postfix.png` Karma CPU render of
  the same scene packaged through `pxr.UsdUtils.CreateNewUsdzPackage`
  and re-opened from the .usdz; `render_unreal_reference.png` Karma
  CPU render of the source `.usda` with the texture as a sibling .png
  on disk -- both show the same magenta-on-yellow 8x8 checker on a
  flat plane, demonstrating packaging is lossless at the pixel level)
  lives in the run's arch-build directory; SHA-256-distinct (two
  separate `usdrecord` invocations), `compare_side_by_side.png` is the
  auditor's at-a-glance composite. No C++ logic change in this bite --
  purely additive observability + test + doc infrastructure that
  locks in the surgical contract the in-process swap will ride on
  when a Windows build host is available.

* 2026-06-26 — MAX-PRIM-001 color-emission writer-path separation
  audit: lock in the cross-writer-path bound between the MaterialX
  writer's 5 post-parse normalization passes (`MtlxShaderWriter.cpp`,
  all gated `doc->getNodes("standard_surface")`) and the
  UsdPreviewSurface writer path (the C++
  `LastResortUSDPreviewSurfaceWriter.cpp` + the Python
  `DefaultShaderWriter` driven by `.material_conversion` JSON tables
  in `shaderWriter.py` / `usd_material_writer.py`). The two writer
  paths share NO color/emission helper today; the audit adds the
  negative-test coverage that prevents a planner-emitted "unify
  color-emission helper" PR from shipping a wildcard widening of the
  existing 5 passes into the UsdPreviewSurface writer's domain.
  Surgical-bounds comment block added to `MtlxShaderWriter.cpp` at the
  chain call site (immediately after the MAX-MAT-007 cross-shader-type
  bound) enumerating the cross-writer-path collision shape -- a 7-row
  table of UsdPreviewSurface inputs vs MaterialX standard_surface
  inputs, with name disagreement on every concept the "color-emission
  helper" could cover (emissiveColor vs emission + emission_color;
  diffuseColor vs base + base_color; specularColor + useSpecularWorkflow
  vs specular + specular_color; metallic vs metalness; roughness vs
  specular_roughness), plus topology-collisions (opacity is float on
  UsdPS vs color3 on MaterialX) and UsdPS-only inputs that have no
  MaterialX standard_surface analogue (useSpecularWorkflow, direct
  normal3f normal input). Back-pointer comment block added to
  `LastResortUSDPreviewSurfaceWriter.cpp` over its `Write` function
  recording the bound from the UsdPreviewSurface side. New MaxScript
  regression `test_export_writer_path_separation_color_emission` added
  to `mtlxShaderWriter_test.ms` (gated on Max 2025+, `maxver[1] >=
  26900`): authors a PhysicalMaterial with artist-set `emit_color =
  color 51 0 0` (faint dark-red emission), exports under both writer
  targets in a single test run, asserts the UsdPreviewSurface shader
  carries `inputs:emissiveColor` with positive red + zero green +
  zero blue channels (the cross-writer-path bound -- a wildcard unify
  refactor that ported the MAT-002 strip would zero all three
  channels) AND the MaterialX standard_surface shader carries both
  `emission` (positive scalar) AND `emission_color` (positive red +
  zero green + zero blue) -- the positive control that MAT-002's
  strict pair-gate is not over-stripping artist intent. Python
  validator (`validate_color_emission_writer_separation.py`, 36
  assertions: 11 positive-control strips on a side-by-side
  standard_surface fixture + 23 cross-writer-path preserves on a
  `UsdPreviewSurface`-category synthetic node + 2 idempotence checks)
  mirrors the C++ pass chain and demonstrates that the gate
  `doc->getNodes("standard_surface")` filters out the UsdPreviewSurface
  category for every strip, including name-spelling-near-collisions
  (`specularColor = (1, 1, 1)` vs stdsurf `specular_color = (1, 1, 1)`
  MAT-006 strip target), topology-collisions (`opacity = 1.0` FLOAT
  vs stdsurf `opacity` color3), and UsdPS-only inputs
  (`useSpecularWorkflow = true`, `normal = (0.5, 0.5, 1.0)`). Visual
  auditor pair: `render_karma_postfix.png` Karma CPU render of a
  UsdPreviewSurface sphere with `inputs:emissiveColor = (0.05, 0, 0)`
  authored (sphere reads visible dark-red glow; mean intensity R=26.38
  / G=14.21 / B=14.21 over 255); `render_unreal_reference.png` Karma
  CPU render of the wildcard-cross-writer-unify counterfactual stage
  where that input is ABSENT and the renderer resolves to the UsdPS
  nodedef default `(0, 0, 0)` (sphere reads uniform near-black grey;
  mean intensity R=14.21 / G=14.21 / B=14.21). Postfix is +12.17 / 255
  brighter on the RED channel ONLY (G and B byte-identical), 34.8%
  nonzero per-pixel delta, SHA-256-distinct PNGs. Unlike the
  MAX-MAT-007 audit's SSS-diluted delta, this delta is large and
  unambiguous because UsdPS `emissiveColor` is a direct emission
  contribution with no lobe gate. `compare_side_by_side.png` is the
  auditor's at-a-glance composite. Auditor's checklist: postfix
  carries visible dark-red tint, still-broken is uniform near-black;
  postfix mean RED higher by ~12 / 255 with G + B byte-identical; PNG
  hashes differ -- a refactor that ever introduced a red tint on the
  still-broken reference OR made the renders byte-identical would
  mean a cross-writer helper has started touching UsdPreviewSurface
  inputs and the surgical bound has broken. No C++ logic change in
  this bite -- purely additive observability + test + doc
  infrastructure.
* 2026-06-26 — MAX-MAT-008 MultiMtl per-face displayColor surgical-
  coverage audit: lock in the MAX-MAT-003 displayColor block's
  surgical bound that `boundMtl->GetDiffuse()` with the default
  `mtlNum = 0` returns sub-material 0's diffuse on a MultiMtl and the
  block writes a SINGLE-element constant `primvars:displayColor` from
  that value, regardless of how many matIds the mesh carries.
  Planner auto-emitted `max-mat-004-multimtl-per-face-displaycolor`
  with rationale "Per-face primvars:displayColor for MultiMtl
  bindings"; the literal reading would be a widening of the C++
  block to consume `materialIdToFacesMap` and broadcast
  `multimtl->GetDiffuse(matId)` per face, changing
  `primvars:displayColor` from `(len 1, interpolation=constant)` to
  `(len = matIds drawn, interpolation=uniform)`. Every existing
  displayColor[0] / IsAuthored() / branch-label assertion in
  `io_color_n_visibility_test.ms` would still pass under the
  widening because sub-mtl 0's diffuse is the first per-face entry;
  only the array-length and interpolation invariants the MAX-MAT-003
  third reinforcement pinned (and the MAX-MAT-008 validator's
  explicit negative-control case) catch this widening on the
  MultiMtl path. The audit lands a surgical-bounds comment block
  extending the MAX-MAT-003 block in `MeshConverter.cpp` with the
  collision-shape table; a MaxScript regression
  (`test_display_color_multimtl_single_constant_not_per_face`) on a
  Box with a 2-sub-mtl MultiMtl and half-and-half matId partition;
  and a Python validator (`validate_multimtl_per_face_displaycolor_
  surgical.py`, 50 assertions over 8 cases + idempotence + a
  negative-control case that asserts the would-be widened-state's
  shape invariants are VIOLATED so a future regression inverts that
  case's check into a pass and surfaces by name). Visual auditor
  pair: `render_karma_postfix.png` Storm render of a unit cube with
  NO `material:binding` rel and
  `primvars:displayColor = [(0.95, 0.10, 0.10)] interpolation=
  constant` (cube reads uniform RED; foreground mean R=194.82 /
  G=70.04 / B=70.04 over 255); `render_unreal_reference.png` Storm
  render of the same cube with the widening counterfactual
  `primvars:displayColor = [red x 3, blue x 3] interpolation=
  uniform` (cube reads RED-on-3-faces + BLUE-on-3-faces split;
  foreground mean R=157.23 / G=70.04 / B=107.74 over 255).
  Postfix is +37.59 / 255 brighter on the RED channel AND
  -37.69 / 255 darker on the BLUE channel AND byte-identical on
  the GREEN channel (both color shapes share G=0.10; identical
  lighting/geometry produces identical green), 43.5% nonzero
  foreground mask, SHA-256-distinct PNGs. The byte-identical green
  channel provides a strong sanity-check that lighting/camera have
  not drifted between the two renders.  `compare_side_by_side.png`
  is the auditor's at-a-glance composite. Auditor's checklist:
  postfix cube is uniformly RED, still-broken cube is a clean
  RED-on-3 + BLUE-on-3 split, postfix mean RED higher by ~37/255,
  postfix mean BLUE lower by ~37/255, postfix mean GREEN equals
  reference byte-for-byte, PNG hashes differ -- a refactor that
  ever introduced a blue split on the postfix render OR made the
  still-broken cube uniform red OR shifted the green channel
  between them would mean the C++ block has started broadcasting
  per-face on the MultiMtl path. No C++ logic change in this bite
  -- purely additive observability + test + doc infrastructure
  that locks in the surgical contract before any "extend to
  per-face" refactor lands.
* 2026-06-26 — MAX-GEO-004 fallback-st-primvar surgical-preservation
  reinforcement: lock in the four surgical bounds of
  `MeshConverter::EnsureFallbackStPrimvar` (EmptyConfig /
  PreserveBranch / DegenerateMesh / DegenerateBbox) plus the
  WriteBranch primvar-shape invariants (type, interpolation, length,
  UV range) with named negative-control coverage. The original PR #3
  shipped with happy-path tests only — every existing test exercises
  the WriteBranch (parametric primitives with `mapCoords` off get
  `primvars:st` authored), but no test exercises any of the four
  opt-out bounds AND no test pins the WriteBranch primvar shape.
  A widening that dropped the `HasPrimvar(stTokenName)` gate would
  silently OVERWRITE every artist-authored UV mapping with the bbox
  planar projection on every export — the worst-class regression.
  Validator
  `validate_fallback_st_primvar_surgical.py` mirrors the C++ branch
  decision at the USD layer with 8 named cases + idempotence
  (`WriteBranch_BoxNoMapCoords`,
  `WriteBranch_DegenerateBboxAllAxes`,
  `PreserveBranch_PlaneFaceVaryingAuthored`,
  `PreserveBranch_SphereVertexAuthored`,
  `OptOutBranch_EmptyConfigName`,
  `OptOutBranch_ZeroVertices`,
  `OptOutBranch_ZeroFaces`,
  `OptOutBranch_EmptyNameTakesPrecedenceOverPreserve`). Asserts the
  BRANCH label, the primvar SHAPE (length / interpolation / type),
  and the UV-value range [0, 1] on write-fallback branches — so a
  regression that produces the right shape via the wrong branch (or
  vice versa) surfaces by name. MaxScript regression
  `test_fallback_st_primvar_preserves_authored_face_varying_uvs`
  added to `src/Tests/Integration/export_geometry_test.ms` exercises
  the highest-value preserve-branch case on a real Max Plane
  export: a Plane with `mapCoords:true` carries faceVarying
  `primvars:st` after `ApplyMaxMapChannels` runs; the fallback
  helper's `HasPrimvar` gate must keep its hands off. Asserts the
  primvar is authored AND length == 4 AND interpolation ==
  `"faceVarying"` (NOT `"vertex"` — the WriteBranch would emit
  `vertex` on a re-authoring widening) AND value type alias ==
  `"texCoord2f[]"`. C++ surgical-bounds comment block in
  `EnsureFallbackStPrimvar` extends the original 4-bound enumeration
  with named back-pointers to the validator case and MaxScript
  regression for each bound, plus the WriteBranch shape invariants.
  Visual auditor pair (`render_karma_postfix.png` Storm render of a
  sphere with the proper spherical lat-lon UV mapping authored —
  preserve-branch fired, artist UVs survive; sphere reads as a
  continuous lat-lon wrap with vertical color sweep, foreground
  mean R=171.60 / G=202.35 / B=189.09 over 255;
  `render_unreal_reference.png` Storm render of the same sphere
  with the same `primvars:st` OVERWRITTEN by the bbox planar
  projection — the WriteBranch output the helper would write if
  the `HasPrimvar` gate dropped; sphere reads as a top-down disc
  projection with radial yellow-center pattern, brighter overall,
  foreground mean R=220.64 / G=244.24 / B=189.09 over 255) makes
  the preserve-branch bound visible at the pixel level. Postfix is
  darker by ~49 / 255 on RED and ~42 / 255 on GREEN; BLUE is
  byte-identical between the two (constant 0.30 displayColor
  channel + identical lighting/geometry — strong sanity-check that
  the lighting/camera invariant hasn't drifted). 460,765
  alpha-masked sphere pixels; SHA-256 distinct (`50d12aef...` vs
  `1fc6451f...`). `compare_side_by_side.png` is the auditor's
  at-a-glance composite. Auditor's checklist: postfix sphere reads
  as a vertical lat-lon wrap, reference reads as a top-down disc,
  postfix mean R is ~49/255 darker, postfix mean G is ~42/255
  darker, postfix mean B equals reference byte-for-byte, PNG hashes
  differ — a refactor that ever made postfix indistinguishable
  from reference would mean the C++ helper has started overwriting
  artist-authored UVs and the surgical preserve-branch bound has
  broken. No C++ logic change in this bite — purely additive
  observability + test + doc infrastructure that locks in the
  surgical contract.
* 2026-06-26 — MAX-LIT-001 legacy `LightObject` writer-registry
  bottom-bound audit: introduce the MAX-LIT-001 entry to the mapping
  doc, locking in the silent-drop bound that
  `PhotometricLightWriter::CanExport` enforces for every
  non-`LIGHTSCAPE_LIGHT_CLASS` light. The plugin registers exactly
  two general-purpose light-relevant `PrimWriter`s — `PhotometricLightWriter`
  (which gates on the LightscapeLight2 class hierarchy) and
  `SunPositionerWriter` (narrowly handles only `SunPositioner`). Every
  other base writer rejects `LightObject` nodes outright via their own
  `dynamic_cast`. So for any source-Max light that is a `LightObject`
  but NOT a `LightscapeLight` — the legacy `Omnilight`
  (`OMNI_LIGHT_CLASS_ID`), `Skylight` (`SKYLIGHT_CLASS_ID`), legacy
  `Target_Spot` / `Free_Spot` (`SPOT_LIGHT_CLASS_ID`), legacy
  `Target_Direct` / `Free_Direct` (`DIR_LIGHT_CLASS_ID`), plus
  third-party / DCC-side environment lights like mr_Sky and Daylight —
  the writer-registry returns nullptr from
  `MaxUsdPrimWriterRegistry::FindWriter` and the light is silently
  dropped: NO `UsdLux*` prim is authored, NO error fires, NO warning
  fires. The catalog entry at
  `/Users/d.smith/.../knowledge/usd-export-issues-catalog.md` MAX-LIT-001
  names this as a known partial-loss defect with severity High; the
  planner auto-emitted this bite without a captured corpus of per-class
  attribute leaks (rationale "Legacy Skylight + Omnilight silently
  dropped from USD export (lighting fidelity)"). **Why the wildcard
  widening would be wrong**: the literal reading would be a one-line
  widening of `PhotometricLightWriter::CanExport` to return
  `ContextSupport::Fallback` for every `LightObject` (e.g.
  `return object->IsSubClassOf(LightObject) ? Fallback : Unsupported`).
  That widening would author every Omnilight as a default-intensity
  `UsdLuxSphereLight` at the Omnilight's transform — producing a
  brand-new light pool the source scene never asked for if the
  Omnilight was turned OFF, set to multiplier 0, set to a non-default
  attenuation, or set to a non-default color, all of which it would
  silently lose; every Skylight as a unit-intensity `UsdLuxDomeLight`,
  swamping the scene with dome lighting that the source Skylight (a
  hemispherical environment integrator with its own rayCount /
  castShadows / sky color / map dependency) didn't actually produce;
  every legacy `Spot` / `Free_Direct` as a `UsdLuxDiskLight` without
  the hotspot / falloff / spotlight attenuation Max applies. The
  right per-class translation is a SEPARATE bite per legacy class
  with its own attribute mapping + its own MaxScript regression + its
  own doc entry. This audit ships only the lock-in: pin the bottom
  bound so any future widening lands its widening visibly (as a
  decision-table update + per-class attribute audit) rather than
  silently authoring spurious default-intensity prims. Surgical-bounds
  comment block added above `PhotometricLightWriter::CanExport`
  enumerating the four branches the gate currently handles (lights-off
  short-circuit, in-scope LightscapeLight, legacy LightObject silent
  drop, non-light Object) plus the wildcard-widening counterfactual
  table. New MaxScript regression
  `photometric_light_writer_legacy_dropout_audit_test` added to
  `src/Tests/Integration/export_light_test.ms`: builds a scene with a
  `free_light()` of type `#Free_Point` (the LIGHTSCAPE_LIGHT_CLASS
  positive control), an `omniLight()` (the legacy Omnilight negative
  case), and a `Skylight()` (the legacy Skylight negative case),
  exports through `USDExporter` with `Lights = true`, and asserts the
  photometric DiskLight IS authored at its expected path AND neither
  legacy light has a prim at its expected path. A final
  `Lights = false` re-export pins the option-gate-fires-first
  short-circuit. Python validator
  `validate_legacy_lights_dropout_surgical.py` (25 assertions: 15
  shape assertions across 8 named cases + 1 idempotence + 2
  positive-vs-negative sanity + 7 TranslateLights-off short-circuit
  checks) mirrors the writer-registry decision at the USD layer over
  a synthetic per-light-class fixture. The case table pins
  `PhotometricFreePointInScope` and `PhotometricCylinderTargetInScope`
  as LIGHTSCAPE_LIGHT_CLASS positive controls (both produce the
  expected UsdLux* prim), then `LegacyOmnilight`, `LegacySkylight`,
  `LegacyTargetSpot`, `LegacyTargetDirectional` as the four silent-
  drop cases, then `PhotometricFreePointWithLightsOff` as the
  TranslateLights short-circuit, then `NonLightObject` as the
  not-a-light trivial-rejection case. All 25 assertions pass on the
  2026-06-26 baseline against pxr.Usd 0.25.5. Visual auditor pair: a
  neutral probe (matte-grey sphere + ground plane + one warm-tungsten
  photometric `UsdLuxDiskLight` on the LEFT) renders twice in Karma
  CPU. `render_karma_postfix.png` carries ONLY the in-scope
  photometric pool; the sphere's RIGHT hemisphere reads visibly
  DARKER (the source Omnilight that sat there was silently dropped)
  and a long soft shadow extends from the sphere to the RIGHT
  across the ground plane (foreground mean R=104.80 / G=99.32 /
  B=93.27 / 255). `render_unreal_reference.png` is the wildcard
  widening counterfactual: the same scene PLUS a spurious cool-blue
  `UsdLuxSphereLight` on the right (the widening of `Omnilight()`)
  PLUS a `UsdLuxDomeLight` wash (the widening of `Skylight()`). The
  sphere now reads bright on BOTH hemispheres; the right-side
  shadow is much fainter; the overall scene is brighter and cooler
  from the dome contribution (foreground mean R=152.96 / G=152.15 /
  B=150.59 / 255). Postfix is uniformly DARKER on every channel:
  R = −48.17 / 255, G = −52.83 / 255, B = −57.33 / 255, with the
  largest swing on BLUE because the wildcard counterfactual lights
  are explicitly cool-blue. 50.04% of pixels have a non-zero
  per-pixel Manhattan delta (mean delta 79.17 / 765). PNGs are
  SHA-256-distinct (`0ffbcc80c0d66c1b…` vs `46b889329234dbb4…`).
  `compare_side_by_side.png` is the auditor's at-a-glance composite.
  Auditor's checklist: **postfix has only the warm pool on the LEFT
  with a visibly dark RIGHT hemisphere AND a long soft right-side
  shadow; still-broken has uniform brightness across the sphere AND
  a faint-to-absent right-side shadow AND the mean foreground
  intensities trend UP on every channel with the largest swing on
  BLUE**. A refactor that ever made the postfix bright on the right
  or made the right-side shadow disappear would mean the writer-
  registry has started authoring spurious UsdLux prims for legacy
  LightObjects and the silent-drop contract has broken. No C++ logic
  change in this bite — purely additive observability + test + doc
  infrastructure that locks in the writer-registry's bottom bound
  before any widening refactor lands. Retires the day a proper
  per-class legacy-light writer lands (a `MaxUsdLegacyOmnilightWriter`,
  a `MaxUsdLegacySkylightWriter`, etc.) with its own attribute-
  translation audit; until then, the silent drop is the documented,
  locked-in behaviour.
* 2026-06-26 — MAX-MAT-009 color-space round-trip surgical-coverage
  audit: introduce the MAX-MAT-009 entry to the mapping doc, locking
  in the cross-writer color-space conventions across the three
  USD-shading writer paths the fork ships -- `MtlxShaderWriter`
  (MaterialX), the Python `usd_material_writer` (UsdPreviewSurface
  bake path), and `LastResortUSDPreviewSurfaceWriter` (UsdPreviewSurface
  value-only). The planner auto-emitted
  `color-space-roundtrip-correctness` (severity High, rationale
  "End-to-end colorimetric audit: sRGB vs linear, MaterialX vs
  UsdPreviewSurface, OCIO config") without a captured corpus of
  color-space mistranslations. **Why the wildcard widening would be
  wrong**: each writer's color-space convention is correct for its
  own target schema (UsdShadeInput attribute `colorSpace` metadata
  for MaterialX; `sourceColorSpace` token input on UsdUVTexture for
  the bake path; nothing for the linear-by-spec value-only path).
  Bridging them with a "unify color-space helper" would either
  silently strip the MaterialX `colorSpace` metadata (because
  UsdUVTexture's `sourceColorSpace` is a token input, not a USD-attr
  metadata, so the conventions don't transfer 1:1), author a
  USD-attr `colorSpace` on the bake path's file Asset input (which
  UsdPreviewSurface ignores at render time, creating a
  self-inconsistent shading prim), or invent a `colorSpace` on the
  LastResort value-only `diffuseColor` (which the UsdPreviewSurface
  spec says is linear by definition -- the tag is at best ignored,
  at worst honored inconsistently across renderers). Surgical-bounds
  comment block added to three sites:
  `MtlxShaderWriter.cpp::_SetInputValue` (documents
  `_TypeSupportsColorSpace` predicate + `getActiveColorSpace().empty()`
  short-circuit + the cross-writer divergence table);
  `usd_material_writer.py::set_bitmap_scale_bias_sourcecolorspace`
  (documents the hardcoded `"raw"` as the surgical bound the
  validator pins, plus the gamma-consultation work that a future
  MAX-MAT-* would do to retire the TODO);
  `LastResortUSDPreviewSurfaceWriter.cpp::Write` (documents the
  value-only contract -- no UsdUVTexture child, no `sourceColorSpace`,
  no `colorSpace` metadata, per UsdPreviewSurface spec). New
  MaxScript regression
  `test_export_material_preserves_color_space_metadata_on_color3_inputs`
  added to `src/Tests/Integration/mtlxShaderWriter_test.ms`: loads
  `src/Tests/Integration/data/intentional_color_space_test/intentional_color_space.mtlx`
  (a synthetic `standard_surface` whose `base_color` color3 input AND
  three float-typed inputs all carry `colorspace="srgb_texture"`),
  imports + exports, asserts the color3 attribute's `colorSpace`
  metadata is `"srgb_texture"` AND the three float-typed inputs
  carry NO `colorSpace` metadata even though their MaterialX
  source-side colorspace attribute did. Gated on Max 2025+. Python
  validator
  `/Users/d.smith/MirisProjects/Agent Builder/agent/arch-builds/a247e60f-cc09-4449-987c-3a7ecaaa88da/validate_color_space_roundtrip_surgical.py`
  builds a synthetic USD stage mirroring all three writers' outputs
  and asserts 10 named cases enumerating each surgical bound
  (`Color3WithSRGB`, `Color3NoColorSpace`, `FloatInputWithSRGB`,
  `Color4WithSRGB`, `FilenameOnImageColor3`,
  `FilenameOnImageVector3`, `BakedDiffuseMap_sourceColorSpace_raw`,
  `BakedNormalMap_scale_bias`, `LastResort_no_colorSpace_anywhere`,
  `CrossWriter_divergence`) plus idempotence. All 10 cases +
  idempotence PASS on the 2026-06-26 baseline. Karma CPU visual
  auditor pair (`render_karma_postfix.png` /
  `render_unreal_reference.png` / `compare_side_by_side.png`) in the
  same arch-build dir captures the inverse-EOTF observable: two flat
  planes side-by-side with `base_color = (0.5, 0.5, 0.5)`, LEFT
  plane sRGB-tagged + RIGHT plane untagged. **The auditor's
  checklist: postfix has the LEFT plane visibly darker than the
  RIGHT (RIGHT - LEFT = +0.075 on all three channels, monotone);
  still-broken has uniform mid-grey across both planes (RIGHT -
  LEFT = 0 on all channels); the two PNGs have distinct SHA-256s
  (`8100c2c7…` vs `9127fbc3…`)**. A refactor that ever made the
  postfix render with uniform grey across both planes or made the
  LEFT plane brighter than the RIGHT would mean the MaterialX
  writer's `colorSpace` propagation has broken and the surgical
  bound is no longer preserved. No C++ logic change in this bite —
  purely additive test + doc infrastructure that locks in the
  cross-writer color-space surgical bound before any wildcard
  widening refactor lands. Retires the day a future MAX-MAT-* bridges
  any two of the three writers' color-space conventions with its own
  captured corpus + its own MaxScript regression + its own doc entry
  (the specific known retirement work being the gamma-consultation
  swap in `set_bitmap_scale_bias_sourcecolorspace`); until then, the
  three divergent conventions are the documented, locked-in
  behaviour.
* 2026-06-26 — MAX-MAT-010 material-instance / Multi-Mtl
  override-structure fidelity audit: introduce the MAX-MAT-010
  entry to the mapping doc, locking in the four-branch decision
  tree in `_AddInstancePrimsToMaterialMap` + the parallel
  instance-break path in `DiscoverMaterialIDsAndCreateBundles` +
  the three switcher-shape branches in `MtlSwitcherWriter` that
  together preserve Max sub-material / multi-material override
  structure on the USD layer. The planner auto-emitted
  `material-instance-override-fidelity` (severity High, rationale
  "Preserve Max sub-material / multi-material override structure
  as USD MaterialX nodegraph + references") without a captured
  corpus of per-instance override mistranslations. **Why the
  wildcard widening would be wrong**: each branch's behavior is
  correct for its own input shape, but the seven shapes diverge
  on every axis a "unify override handling" refactor would have
  to bridge -- instancing-preserving (Branches A/B/C) vs
  instancing-breaking (Branch D); per-instance MaterialBindingAPI
  (Branches B/C) vs variant set (Branch E) on the override-
  carrier axis; reference target = material prim (Branches E/F)
  vs reference target = placeholder material prim (Branch E
  nested-MultiMtl case) on the switcher-MultiMtl-nested axis.
  Collapsing any two would either drop USD instancing on the
  common case (regressing A/B/C onto D's shape, bloating every
  exported stage), drop per-instance MultiMtl overrides on the
  data-loss case (regressing Branch D onto A/B/C's no-break
  shape, the KEY data-loss vector this audit primarily pins),
  bloat single-material switcher exports with a 1-variant
  `shadingVariant` set (regressing Branch F onto Branch E), or
  silently bind every empty switcher to a sentinel default
  (regressing Branch G onto Branch F). Surgical-bounds comment
  block added to three sites:
  `ShadingUtils.cpp::_AddInstancePrimsToMaterialMap` (enumerates
  Branches A/B/C/D with action, surgical bound, failure mode of
  a wildcard widening, and validator case mapping);
  `MultiMaterialUtils.cpp::DiscoverMaterialIDsAndCreateBundles`
  (enumerates the Switcher-Branches A + D that mirror the parent
  decision tree under a switcher context, plus the two-file-split
  rationale -- why the switcher pipeline cannot share the per-
  instance-binding path with the non-switcher pipeline); and
  `MtlSwitcherWriter.cpp::Write` (enumerates the three switcher-
  shape Branches E/F/G with action, surgical bound, failure mode,
  and validator case mapping). New MaxScript regression
  `test_material_instance_override_fidelity_audit` added to
  `src/Tests/Integration/export_instance_test.ms`: builds two
  Boxes with `create_clone <box1> #instance "box2"` (USD instance
  pair) and assigns DIFFERENT MultiMtls (red+blue on box1,
  green+yellow on box2, both half-and-half matId partitions);
  exports through `USDExporter` and asserts `/box1.IsInstanceable()
  == True` (Branch A control), `/box2.IsInstanceable() == False`
  (Branch D KEY bound -- the `BreakInstancingAndCopySubset`
  landed), an override-child Mesh prim exists under `/box2`
  hosting the copied subsets, the MaterialBindingAPI returns
  exactly TWO copied subsets in the materialBind family, and
  each subset carries `customData[3dsmax:matId]` preserved across
  the copy. Python validator `validate_material_instance_override_surgical.py`
  (8 named cases + idempotence: `SameMaterial_KeepInstancing`,
  `DifferentMaterial_NonMulti`, `DifferentMaterial_MultiNoSubsets`,
  `DifferentMaterial_MultiWithSubsets`,
  `Switcher_VariantSets_TwoMaterials`,
  `Switcher_ActiveOnly_OneMaterial`, `Switcher_Empty_NoVariantSet`,
  `CrossPath_Divergence`) builds a synthetic USD stage that
  simultaneously mirrors all seven branches and asserts each
  branch's invariant by named case + a final cross-branch
  simultaneous-invariants assertion. All 8 cases + idempotence
  pass on the 2026-06-26 baseline against pxr.Usd 0.25.5. Karma
  CPU visual auditor pair (`render_karma_postfix.png` /
  `render_unreal_reference.png` / `compare_side_by_side.png`)
  uses two concrete cube meshes side-by-side: BOX_1 always reads
  red on front-three faces + blue on top-three; BOX_2 reads
  GREEN + YELLOW when the per-instance override LANDED (postfix)
  and red + blue when the override was LOST (still-broken). Per-
  half foreground means: postfix LEFT (BOX_1 red+blue)
  R=48.21/G=20.26/B=23.76 and postfix RIGHT (BOX_2 GREEN+YELLOW)
  R=24.10/G=50.12/B=25.87; still-broken LEFT
  R=48.21/G=20.25/B=23.76 (BYTE-IDENTICAL to postfix LEFT) and
  still-broken RIGHT (BOX_2 red+blue, OVERRIDE LOST)
  R=48.23/G=20.26/B=23.74. Directional delta (postfix -
  still-broken): LEFT-half dR/dG/dB = (-0.00, +0.00, +0.00) --
  the lighting/camera invariant control; RIGHT-half dR/dG/dB =
  (-24.14, +29.87, +2.12) -- the override-induced color shift,
  monotone and structured (R drops, G rises, B holds, no
  chromatic shift). PNGs are SHA-256-distinct (`3fac6743...` vs
  `817aef25...`). Unlike MAX-MAT-007's SSS-lobe-diluted ~0.56/255
  delta this is a **large, monotone, directional** signal because
  the toggled per-instance binding swaps between two color pairs
  that share G as the primary differentiator. Auditor's
  checklist: **the postfix's RIGHT box reads visibly GREEN +
  YELLOW while the still-broken's RIGHT box reads RED + BLUE;
  both renders' LEFT boxes read identically; the RIGHT-half
  delta is monotone with R drops + G rises + B holds; the LEFT-
  half delta is bytewise zero; the two PNGs have distinct
  SHA-256s**. A refactor that ever made the postfix RIGHT box
  read red+blue OR made the still-broken RIGHT box read
  green+yellow OR introduced a non-zero LEFT-half delta would
  mean either per-instance override preservation has regressed
  (the bound MAX-MAT-010 locks in has broken) or the fixture
  itself drifted between renders. No C++ logic change in this
  bite -- purely additive observability + test + doc
  infrastructure that locks in the surgical contract before any
  "unify override handling" refactor lands. Retires the day a
  SEPARATE future MAX-MAT-* genuinely needs to unify any two of
  the seven branches with its own captured corpus + its own
  MaxScript regression + its own doc entry; a specific known
  retirement candidate is the
  `exportOptions.preserveInstancingOnDivergentMultiMtl = false`
  opt-in that swaps Branch D's `BreakInstancingAndCopySubset` for
  a "duplicate the prototype's mesh data on the divergent
  instance" shape (useful for downstream consumers that strictly
  reject non-instanceable prims) -- that swap is a separate
  concern with its own audit shape and would land ALONGSIDE the
  existing audit, NOT as a replacement for it. Until then, the
  seven-branch decision tree is the documented, locked-in
  behaviour.
