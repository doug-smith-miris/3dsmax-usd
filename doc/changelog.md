## Changelog


### Unreleased (Miris fork)

#### Fixes:
- [MAX-LIT-002] Photometric / Physical light fidelity audit: lock in two surgical bounds in `PhotometricLightWriter::Write` that the existing `export_light_test.ms` happy-path suite did not pin. (a) **IES file path round-trip**: when `distribution == WEB_DIST` AND `.webFile` is set (`asset.GetId() != kInvalidId`), `shaping:ies:file` is authored on the resulting `UsdLuxShapingAPI` with the resolved-full-file-path; under any other distribution OR when `.webFile` is empty, `shaping:ies:file` is NOT authored. The existing suite toggles `distribution = 3` (web/IES) on every light-type test but never sets `.webFile`, so the entire IES branch was uncovered. (b) **Kelvin / filter-color dichotomy**: when `useKelvin == true`, `enableColorTemperatureAttr = true` + `colorTemperatureAttr = clamp(kelvin, 1000, 10000)` + `inputs:color = filterColor` ONLY (the magenta lightColor is intentionally dropped to avoid double-tinting the renderer's blackbody integrand); when `useKelvin == false`, `enableColorTemperatureAttr = false` + `colorTemperatureAttr` NOT authored + `inputs:color = lightColor × filterColor` (combined product, intentionally lossy on round-trip); out-of-range Kelvin clamps to `[1000, 10000]` with a one-shot `Log::Warn` that preserves the original value in the message. The existing suite asserts `enableColorTemperatureAttr` true/false in some modes but never the `colorTemperatureAttr` VALUE in static-frame export, never the filter-only / combined-product color signal, and never the clamp. New MaxScript regressions `photometric_light_ies_file_export_test` (synthesises an in-temp IESNA LM-63 profile and pins all three surgical bounds in the IES branch) and `photometric_light_kelvin_filter_color_round_trip_test` (pins all four surgical bounds in the Kelvin branch) added to `export_light_test.ms`. Surgical-bounds comment blocks added to `PhotometricLightWriter.cpp` over each branch, pointing back to the named regression + Python validator. Python validator (`validate_photometric_light_fidelity.py`, 8 cases + idempotence) mirrors the C++ decision over a synthetic fixture. Visual auditor pair (`render_karma_postfix.png` warm-tungsten + cool-daylight pools on a grey wall, blackbody contrast clearly distinct; `render_unreal_reference.png` the same scene with the Kelvin attributes stripped — pools collapse to faint near-neutral filter tints) makes the Kelvin surgical bound visible at the pixel level. No C++ logic change in this bite — the audit is purely additive observability + test + doc infrastructure. Introduces the first MAX-LIT entry in `doc/translation-mapping.md` (two new rows in the Status table for the IES and Kelvin direct pairings, two new sections under "Notes per expression"). See `doc/translation-mapping.md`.
- [MAX-UNIT-001] Export units: surface the scene-unit / `metersPerUnit` divergence as a `MaxUsd::Log::Warn` at export time so 3ds Max artists working in inches (the Max default), millimetres, or centimetres see at export time that their USD will be at unit-mismatched scale to common meter-anchored downstream consumers. `USDSceneBuilder::BuildStage` continues to author `metersPerUnit` faithfully from the source scene's system unit — the resulting USD is USD-correct — but immediately after `pxr::UsdGeomSetStageMetersPerUnit` the writer now emits a single one-shot warning whenever the rounded stage-scale is not exactly 1.0. The message names the metersPerUnit value, the implied 1-Max-unit-to-meter factor, the four downstream consumers that silently rescale or reject not-in-meters exports (Houdini Karma at 1:1, ARKit / Quick Look, Miris asset ingest [which per docs.miris.com/preparing-assets/usd-guidelines REQUIRES `metersPerUnit == 1`], glTF importers), and both catalog workarounds (set 3ds Max's system unit to meters before exporting; or post-scale the root prim's `xformOp:scale` by the factor and rewrite `metersPerUnit = 1.0`). The bound: scenes already in meters (`stageScale == 1.0` within float-rounding tolerance) stay silent. The exporter bytes are unchanged — this is observability, not a value rewrite — but the artist now has an export-time signal for what previously was a fail-downstream-with-no-explanation defect. Introduces a new "observability hint" Kind in `doc/translation-mapping.md` for future export-time warnings that don't mutate the exported USD. Validator (`validate_meters_per_unit_warning_surgical.py`, 9 cases + idempotence + corpus check) and visual auditor pair (`render_karma_postfix.png` showing the Max corpus dominating the frame at ~39x scale next to three 1 m reference cubes; `render_unreal_reference.png` showing the same composite with the workaround applied at the USD layer — Max asset at correct meter scale) live in the run's arch-build directory. New MaxScript regressions `testInchesUnitsWarns` and `testMetersUnitsDoesNotWarn` in `export_metersPerUnit_test.ms`. See `doc/translation-mapping.md`.
- [MAX-CAM-001] Camera export: always author `UsdGeomCamera.clippingRange`. The writer previously gated `CreateClippingRangeAttr()` on `GenCamera::GetManualClip() != 0`, so any camera with "Clip Manually" off (the default for newly-created Free / Target / Physical cameras) exported with no `clippingRange` attribute and silently fell back to the `UsdGeomCamera` schema default of `(1.0, 1000000.0)` -- a near plane of 1 inch and a far plane of ~25 km at Max's default inch units. `CameraWriter::Write` now reads `GetClipDist(...)` unconditionally and authors the result on every exported camera. The clip distance values are stored on every Max camera regardless of the "Clip Manually" toggle; the toggle only controls whether Max's *renderer* honours them, so honouring them in USD is correct in both cases. Degenerate values (≤ 0, NaN, far ≤ near) are sanity-clamped to `(1.0, 1000.0)` in scene units. The splines-mode warning is preserved but still gated on `GetManualClip() != 0`. See `doc/translation-mapping.md`.
- [MAX-MAT-001] MaterialX export: strip the spurious `specular_rotation = 0.25` default that 3ds Max's `MtlxIOUtil` bridge writes on every `ND_standard_surface_surfaceshader`. The MaterialX nodedef default is 0.0, and the value has no visual effect when `specular_anisotropy` is zero; the spurious value is preserved by `MtlxShaderWriter` only when anisotropy is provably non-zero. See `doc/translation-mapping.md`.
- [MAX-MAT-001] MaterialX export — surgical-preservation reinforcement: lock in the existing normalizer's surgical guarantee with new regression tests. Adds `test_export_material_preserves_intentional_specular_rotation` to `mtlxShaderWriter_test.ms`, loading a synthetic `.mtlx` carrying a brushed-steel-like configuration (`specular_rotation = 0.25`, `specular_anisotropy = 0.85`, `specular_roughness = 0.18`) and asserting BOTH the rotation and anisotropy values survive the round-trip + normalizer untouched. This is the previously-uncovered surgical bound: the rotation value matches the leak byte-for-byte, but because anisotropy is on the rotation IS observable in the BSDF and stripping it would silently rewrite the artist's authored streak orientation. The original `a377982` commit message promised exactly this negative test ("Synthetic negative test (BrushedSteel with `specular_anisotropy=0.4`) confirms the fix is surgical and preserves intentional rotation") but the synthetic test only existed during authoring and was never landed in the suite. Also adds a surgical-bounds comment block to `MtlxShaderWriter.cpp::_NormalizeStandardSurfaceSpecularRotation` enumerating each negative case the gate must reject (aniso non-zero, aniso connected, rotation connected, rotation static != 0.25, non-`standard_surface` shader) with a back-pointer to the named regression for each. Python validator (`validate_specular_rotation_surgical.py`, 8 cases + idempotence) and visual auditor pair (`render_karma_postfix.png` showing radial streaks where the 90-degree rotation is preserved; `render_unreal_reference.png` showing a compact star where the rotation has been over-stripped) live in the run's arch-build directory. No C++ logic change; all surgical bounds were already enforced by `_NormalizeStandardSurfaceSpecularRotation`, but the BrushedSteel-with-rotation case had no negative-case test coverage. See `doc/translation-mapping.md`.
- [MAX-MAT-002] MaterialX export: strip the spurious `emission = 1.0` paired with `emission_color = (0, 0, 0)` that 3ds Max's `MtlxIOUtil` bridge writes on every `ND_standard_surface_surfaceshader`. The MaterialX nodedef defaults (`emission = 0.0`, `emission_color = (1, 1, 1)`) evaluate to the same zero emission with the correct semantic meaning; the spurious pair is only removed when both values match the buggy pattern exactly. See `doc/translation-mapping.md`.
- [MAX-MAT-002] MaterialX export — surgical-preservation reinforcement: lock in the existing normalizer's surgical guarantee with new regression tests. Adds `test_export_material_preserves_intentional_emission` to `mtlxShaderWriter_test.ms`, loading a synthetic `.mtlx` carrying intentional `emission = 0.5`, `emission_color = (1.0, 0.3, 0.0)` and asserting the values survive the round-trip + normalizer untouched. Strengthens the existing happy-path test with a pair-level post-condition that the buggy `(1.0, (0, 0, 0))` pair cannot coexist on the same shader (catches half-leak regressions). No C++ logic change; all surgical bounds were already enforced by `_NormalizeStandardSurfaceEmissionDefault`, but they had no negative-case test coverage in the suite. See `doc/translation-mapping.md`.
- [MAX-MAT-002] MaterialX export — tolerance-band reinforcement (second layer, stacked on the 2026-06-23 reinforcement): pin the **numeric edges** of the `_NormalizeStandardSurfaceEmissionDefault` strip gate that the first reinforcement's loud, high-magnitude fixtures (`IntentionalScalar = 0.5`, `IntentionalColor = (0.8, 0.4, 0.1)`) do not exercise. Adds `test_export_material_preserves_single_channel_dark_emission` to `mtlxShaderWriter_test.ms`, loading a synthetic `.mtlx` carrying `emission = 1.0` (matches the leak scalar EXACTLY) and `emission_color = (0.05, 0, 0)` (one channel just barely above the per-component epsilon — magnitude ~0.05, far below any reasonable magnitude threshold a refactor might pick). The surgical bound: the C++ gate's per-component (max-abs) color check must reject this pair on channel 0; a future refactor that switched to a vector-magnitude check (`||c|| < eps`) would silently strip dark-channel artist emission. Python validator `validate_emission_normalize_tolerance_bounds.py` covers the same bound at the USD layer alongside `SubEpsilonScalar` (`emission = 1.0 + 1e-7`, must strip — within ε), `NearLeakScalar` (`emission = 1.0 + 1e-3`, must preserve — just outside ε), `SubEpsilonColor` (`emission_color = (1e-7,)*3`, must strip — within ε on every channel), and `ConnectedEmission` (the symmetric companion of the first reinforcement's `ConnectedColor` — exercises the emission-side static-input check). All 6 cases + idempotence pass on the 2026-06-26 baseline. Extends the C++ surgical-bounds comment block in `MtlxShaderWriter.cpp` to enumerate the new numeric bounds and point at both Python validators by name. Visual auditor pair (`render_karma_postfix.png` faint pinkish wash where the per-component color check preserves the dark-red glow; `render_unreal_reference.png` clean neutral grey where the hypothetical magnitude-check refactor over-stripped) makes the per-component vs magnitude distinction visible at the pixel level. No C++ logic change; purely additive test infrastructure stacked on top of the 2026-06-23 reinforcement. See `doc/translation-mapping.md`.
- [MAX-MAT-005] MaterialX export: strip the spurious `subsurface_radius = (0.794704, 0.531734, 0.292854)` triple (the Pixar/Hery caucasian-skin SSS radius) that 3ds Max's `MtlxIOUtil` bridge writes on every `ND_standard_surface_surfaceshader`, but only when `subsurface` is provably zero (absent or static 0.0) so the triple is visually inert under the current BSDF gate. After normalization the stripped input falls back to the MaterialX `standard_surface` nodedef default `(1, 1, 1)`, a neutral white scatter radius — the correct "no SSS profile was authored" state. Visually inert while `subsurface == 0` (Karma prefix-vs-postfix pixel delta sits at or below the sampler-noise floor); eliminates a latent regression that surfaces the moment any downstream context flips `subsurface > 0` (USD overlays, variants, round-trip importers) and that previously inherited Max's opinionated skin-tone RGB attenuation — every material scattering with the same pink-red core indistinguishable from human flesh — rather than the neutral nodedef default. See `doc/translation-mapping.md`.
- [MAX-MAT-006] MaterialX export: strip 13 inputs (`base`, `coat`, `coat_color`, `diffuse_roughness`, `specular`, `specular_color`, `subsurface`, `subsurface_color`, `subsurface_scale`, `thin_walled`, `transmission`, `transmission_color`, `transmission_depth`) that 3ds Max's `MtlxIOUtil` bridge writes on every `ND_standard_surface_surfaceshader` at the MaterialX v1.0.1 nodedef-default value regardless of source intent. Each input is stripped independently when it equals the nodedef default exactly (tolerant of float noise on numeric types; exact `"true"`/`"false"` match on `thin_walled`) and is not connected; values authored away from the default are preserved as artist intent. The fix is purely structural — 78 redundant attribute writes removed from the 6-material diagnostic corpus (~30% of the per-material `standard_surface` attribute count) — and a visual no-op by construction: stripped values equal the nodedef defaults the renderer resolves absent inputs to, so the BSDF math is unchanged (Karma prefix-vs-postfix pixel delta sits at or below the sampler-noise floor). Unlike MAX-MAT-004/005 there is no latent-contamination case, because the leaked values *are* the nodedef defaults — even under downstream gate-flips (`coat > 0`, `subsurface > 0`) the resulting BSDF evaluates identically with or without the redundant authoring. The benefits are reduced layer bloat, more legible `usddiff` reports, and a clean "input is authored ⇔ artist had an opinion" invariant for downstream tools, variant sets, and round-trip importers. See `doc/translation-mapping.md`.
- [MAX-MAT-004] MaterialX export: strip the spurious coat-block preset (`coat_IOR = 1.52`, `coat_affect_color = 0.5`, `coat_affect_roughness = 0.5`, `coat_roughness = 0.0`) that 3ds Max's `MtlxIOUtil` bridge writes on every `ND_standard_surface_surfaceshader`, but only when `coat` is provably zero (absent or static 0.0) so the four values are visually inert under the current BSDF gate. Each input is tested and stripped independently, so a user-authored override on one of the four is preserved while the other three (still at their leak values) are removed. After normalization the stripped inputs fall back to the MaterialX `standard_surface` nodedef defaults (`coat_IOR = 1.5`, `coat_affect_color = 0.0`, `coat_affect_roughness = 0.0`, `coat_roughness = 0.1`), the correct "no coat was authored" state. Visually inert while `coat == 0` (Karma prefix-vs-postfix pixel delta sits at or below the sampler-noise floor); eliminates a latent regression that surfaces the moment any downstream context flips `coat > 0` (USD overlays, variants, round-trip importers) and that previously inherited Max's opinionated coat profile rather than the cleaner nodedef defaults. See `doc/translation-mapping.md`.
- [MAX-MAT-003] Mesh export: stop leaking the 3ds Max viewport wireframe color into `primvars:displayColor` on meshes that have a material bound. The wireframe color is a scene-graph organizational tag; using it as displayColor misled USD consumers that fall back to displayColor when the bound material can't be evaluated (minimal Hydra delegates, ARKit Quick Look paths without MaterialX, the usdview displayColor overlay, thumbnailers). `MeshConverter::ConvertToUSDMesh` now derives displayColor from the bound material's `GetDiffuse()` when one is bound, and falls back to the wireframe color only when no material is bound. See `doc/translation-mapping.md`.
- [MAX-MAT-003] Mesh export — surgical-preservation reinforcement: lock in the existing fix's surgical guarantee with new regression tests. Adds `test_display_color_preserves_authored_when_material_bound` to `io_color_n_visibility_test.ms`, exercising the combined `(IsAuthored() && node->GetMtl() != nullptr)` case — a box with wireframe red, an artist-authored blue vertex color mapped to `primvars:displayColor` via `SetChannelPrimvarMapping 0 "displayColor"`, AND a Standard material with green diffuse bound — and asserts the exported `primvars:displayColor` is the artist's blue, not the material's green or the wireframe red. This is the surgical bound the existing happy-path coverage missed: prior tests verified `IsAuthored()` preservation only when no material was bound. Also adds a surgical-bounds comment block to `MeshConverter::ConvertToUSDMesh` enumerating each negative case the gate must reject (preauthored, no-mtl, MultiMtl, black/white diffuse) with a back-pointer to the named regression for each. No C++ logic change; all surgical bounds were already enforced by the existing `!IsAuthored()` gate and the `node->GetMtl() != nullptr` branch, but the combined case had no negative-case test coverage. See `doc/translation-mapping.md`.
- [MAX-MAT-003] Mesh export — value-coincidence band reinforcement (second layer, stacked on the 2026-06-23 reinforcement): pin the **value-coincidence edges** of the `MeshConverter::ConvertToUSDMesh` MAX-MAT-003 IsAuthored() gate that the first reinforcement's high-distinctness fixtures (`BLUE`, `YELLOW`, `MAGENTA`) do not exercise. Adds `test_display_color_preserves_default_looking_authored_value_when_material_bound` to `io_color_n_visibility_test.ms`, loading a box with wireframe red, an artist-authored **black** vertex color `(0, 0, 0)` (the canonical "black default" sentinel) mapped to `primvars:displayColor` via `SetChannelPrimvarMapping 0 "displayColor"`, AND a Standard material with vivid red diffuse `(255, 38, 26)` bound, and asserts the exported `primvars:displayColor` is the artist's black, not the material's red. The surgical bound: the C++ gate is `if (!usdMesh.GetDisplayColorAttr().IsAuthored())` — a pure boolean check with no value filter. A future refactor that swapped that for "if not authored OR authored value looks like a default sentinel" (`(0, 0, 0)` / `(1, 1, 1)` / `(0.5, 0.5, 0.5)`) would silently start overwriting legitimate artist authoring at those values, and the first reinforcement's BLUE-vs-GREEN pair would still pass. Python validator `validate_display_color_tolerance_bounds.py` covers the same bound at the USD layer alongside `AuthoredWhiteWithMtl` (`(1, 1, 1)` over a blue mtl), `AuthoredMidGreyWithMtl` (`(0.5, 0.5, 0.5)` over a vivid red mtl), `AuthoredZeroNoMtl` (sentinel + no mtl), `MtlDiffuseHDR` (`(2.0, 0.5, 0.5)` — pins "no clamp to LDR on diffuse"), and `MtlDiffuseTinyChannel` (`(1e-3, 0, 0)` — same per-component-vs-magnitude lesson as MAX-MAT-002's second reinforcement, applied to the diffuse-write branch). All 6 cases + idempotence pass on the 2026-06-26 baseline. Extends the C++ surgical-bounds comment block in `MeshConverter::ConvertToUSDMesh` to distinguish "State-shape bounds (first reinforcement, 0353d63)" from "Value-coincidence bounds (second reinforcement, this commit)" and points at both Python validators by name. Visual auditor pair (`render_karma_postfix.png` solid mid-grey sphere where the canonical sentinel `(0.5, 0.5, 0.5)` is preserved by the IsAuthored() gate even with a vivid red material bound; `render_unreal_reference.png` solid red sphere where the hypothetical sentinel-filter refactor over-wrote) makes the value-coincidence bound visible at the pixel level. No C++ logic change; purely additive test infrastructure stacked on top of the 2026-06-23 reinforcement. See `doc/translation-mapping.md`.
- [MAX-GEO-001] Mesh export: add a new `NormalsMode::Both` and make it the new default. The historical default `NormalsMode::AsPrimvar` authored only `primvars:normals` and left the `UsdGeomMesh.normals` schema attribute unset, which silently broke consumers that read the schema attribute first (ARKit Quick Look historically, minimal Hydra delegates in some configurations, USDZ thumbnailers without the full primvar resolver). `Both` populates both locations from the same source normals (primvar stays indexed, schema attribute is flattened) so any USD consumer sees the authored normals regardless of which carrier it queries. The MAXScript enum gains `#both`; the existing `#asPrimvar` and `#asAttribute` selectors are unchanged. See `doc/translation-mapping.md`.
- [MAX-GEO-001] Mesh export — dual-carrier-parity reinforcement: lock in the existing `NormalsMode::Both` fix's surgical guarantee with new regression tests. Adds `test_normals_both_mode_dual_carrier_parity` to `export_options_test.ms`, exporting a Sphere (vertex interpolation — indexed primvar / flattened schema attribute) and a Box (faceVarying interpolation — alternate parity-invariant path) under `#both` and asserting (a) the primvar's interpolation token equals the schema attribute's interpolation token — the parity invariant the original `1ac2d1b` commit promised ("Both carry the same interpolation token… any consumer reading either location sees the same logical normals") but no existing test exercised, (b) the schema attribute's array length is the FLATTENED form (`points` count for vertex, sum of `faceVertexCounts` for faceVarying, 1 for constant) — not the indexed-unique length the primvar carries, pinning the C++'s explicit `schemaLayout(interp, /* indexed */ false)` override, and (c) the schema attribute has no companion `normals:indices` sibling (the schema attribute is not a primvar). The previously-uncovered surgical bound: the existing `test_normals_option` asserts each carrier is PRESENT/ABSENT per mode but never checks INTERPOLATION PARITY or SHAPE PARITY between them; a regression that recomputed the schema layout independently — e.g. hardcoded `faceVarying` "for safety" on the schema branch, or fed the primvar's `indexed=true` layout to the schema `PopulateAttribute` call — would silently produce two carriers that disagree and the existing categorical test would still pass. Also adds a surgical-bounds comment block to `MeshConverter::ApplyMaxNormals` distinguishing state-shape bounds (original fix, `1ac2d1b`) from dual-carrier parity invariants (this commit), pointing at both the `.ms` regression and the Python validator by name. Python validator (`validate_dual_normals_surgical.py`, 8 cases + idempotence) covers the same bounds at the USD layer enumerating every `NormalsMode` × interpolation × early-return case. Visual auditor pair (`render_karma_postfix.png` smoothly-shaded icosphere where the schema attribute carries the smooth per-vertex normals; `render_unreal_reference.png` heavily faceted icosphere where the schema attribute was never authored — both stages OMIT `primvars:normals` to simulate schema-attribute-only consumers like ARKit Quick Look) lives in the run's arch-build directory and makes the surgical bound visible at the pixel level. No C++ logic change; purely additive test infrastructure. See `doc/translation-mapping.md`.
- [MAX-GEO-004] Mesh export: backfill `primvars:st` (or the channel-1-configured primvar name) with a fallback planar projection of the vertex positions whenever `MeshConverter::ApplyMaxMapChannels` did not author it. 3ds Max parametric primitives (`Box` / `Sphere` / `Cylinder` / `Torus` / `Teapot`) default `Generate Mapping Coords.` to false when constructed via MAXScript without an explicit `mapCoords:true` argument, leaving the converted MNMesh's channel 1 empty and the exported USD with no UV stream — lethal for any texture-bearing material bound to the mesh. The fallback is a top-down XY planar projection normalized to the mesh's bounding box, written with `vertex` interpolation. Conservatively does nothing when the user explicitly opted out of channel-1 export, when real UVs already exist, or when the mesh is degenerate. Emits a `MaxUsd::Log::Warn` so the artist knows the projection is a fallback and how to fix it properly (enable `Generate Mapping Coords.` on the source primitive, or apply a UVW Map modifier). See `doc/translation-mapping.md`.
- [MAX-GEO-002] Mesh export: stop emitting ghost `GeomSubset` prims in the `materialBind` family when the bound material is not a Multi/Sub-Object material. 3ds Max parametric primitives (`Box`, `Cylinder`, `Cone`, `ChamferBox`, ...) default each face to a distinct sub-material id, so `MeshConverter::ApplyMaxMaterialIDs` previously emitted one `GeomSubset` per face (Box → 6 subsets, Cylinder → 3) even when the artist bound a single PhysicalMaterial at the node level. Those subsets carry `familyName = "materialBind"` and a `customData.3dsmax.matId` value but **no** `material:binding` of their own — they are pure layer bloat and mislead consumers (USDZ packagers, scene-graph viewers, Hydra delegate configurations) that interpret `materialBind` partitioning as authored intent for per-face material variation. The new gate generalises the existing `materialIdToFacesMap.size() == 1` early-out: when the bound material is null or non-MultiMtl, collapse to the single-`customData.3dsmax.matId` path so the round-trip importer's `GetMaterialIdFromCustomData` still sees a sensible value. Existing subsets on a re-exported prim are preserved (we never destroy prior authoring). Emits a `MaxUsd::Log::Warn` explaining the collapse and how to preserve per-face partitioning (bind a Multi/Sub-Object material). See `doc/translation-mapping.md`.
- [MAX-GEO-003] Mesh export: rename the `GeomSubset` fallback naming pattern from the legacy underscore-wrapped `_{N}_` (e.g. `_1_`, `_2_`) to the readable `mat_{N}` form (e.g. `mat_1`, `mat_2`). `MaterialUtils::CreateSubsetName` applied the legacy pattern in two branches — (a) when the bound material is null or non-Multi, and (b) when a `MultiMtl` has an empty slot name — producing subset prim names like `/root/Box/_1_` that are ugly in `usdview`'s Prim Tree, awkward to grep for, and diverge from the `mat_*` / `submat_*` convention used by Maya USD, Houdini Solaris, and the broader USD ecosystem. Cosmetic only: subset names are opaque metadata to renderers (Karma SHA-256-identical prefix vs postfix) and the round-trip importer (`TranslatorMaterial.cpp`) treats them as opaque strings, so older USD files with `_N_` subsets still read back unchanged. Unit and ShellMtl integration tests updated to assert the new pattern; the back-compat read test (`import_material_id_test.ms`) still constructs synthetic `_N_` inputs and is intentionally unchanged. See `doc/translation-mapping.md`.

### v0.15.0

#### What's New:
- New public release - version 0.15.0

### v0.14.5

#### Fixes:
- Prevent accidental prim reparenting during selection from the prim picker dialog.

### v0.14.4

#### What's New:
- USD Explorer and USD Layer Editor now retain their docked or floating position and size when reopening a scene, with automatic reset to a visible default location if previously placed off-screen.

#### Fixes:
- Fixed a situation in which the USD Explorer could open over the file picker when creating a stage from a file.
- Removed the "Both" animation export option to prevent conflicting spline and time sample authoring, aligning with USD behaviour where time samples take priority and splines are ignored.
- Fixed a bug that allowed the Prim Picker to edit prims name.
- Improved rename performance and reliability.

### v0.14.3

#### What's New:
- USD Explorer and USD Layer Editor now retain their docked or floating position and size when reopening a scene, with automatic reset to a visible default location if previously placed off-screen.

#### Fixes:
- Removed the "Both" animation export option to prevent conflicting spline and time sample authoring, aligning with USD behaviour where time samples take priority and splines are ignored.
- Improved rename performance and reliability.

### v0.14.2

#### What's New:
- Compatibility release for 3ds Max Beta A519-71.2 release.
- Resolved conversion issues between OpenPBR and USD Preview Surface on export.

#### Fixes:
- Exporting light now properly respects the On/Off state.
- Fixed a potential Gimbal Lock issue when exporting data to USD with Split transform feature enabled.
- Fixed an issue where camera with animated targets wouldn't get animated when exporting with Curves.
- Fixed an issue where 3ds Max would crash when exporting biped with Animation curves.

### v0.14.1

#### Fixes:
- Fixed an issue where animated transforms didn't match the original when exporting with Y-up and animation curves.

### v0.14.0

#### What's New:
- New public release - version 0.14.0

#### Fixes:
- Fixed an issue where lights could lose non-animated attributes when exported with Animation Curves option.
- Fixed environment search path enable/disable state not properly taken into account in asset resolver preferences.
- Fixed saving of USD preferences window position and scale.
- Fixed an issue when exporting split transform would cause the transform stack to have more transforms than intended.
- Prevented empty paths from being added to the user search paths list in the USD preferences window.
- Fixed the DrawModes_Reserved layer not preserving its lock state when saving USD edits into the Max file.
- Fixed stages not re-loading in the USD Explorer after re-opening a scene if the USD Stage has an anonymous root.
- Fixed file-backed sublayers going missing when a USD Stage with an anonymous root layer is saved to disk.

### v0.13.6

#### What's New:
- Added support for USD Animation curves of lights when exporting to USD. Note that USD animation curve support is not yet available in USD for colors, so light colors still export as timesamples.

#### Fixes:
- Fixed the USD Layer Editor to not show the session layer as being dirty when reloading a scene with a USD Stage Object.
- Fixed unexpected prompt to save a USD Layer when saving a Max scene when a USD Prim had been promoted to a USDGeometryObject.
- Fixed the USD Asset Resolver icons becoming unresponsive after adding long paths to the Search Paths.
- Fixed the USD Explorer to not open when 3ds Max is running in headless mode.
- Fixed a crash in 3ds Max USD when rendering with Deadline if there is a USDGeomObject in the scene.
- Fixed the Browse and Delete buttons not appearing on new search paths in the USD Asset Resolver Search Paths.
- Fixed the path field not being in focus automatically when adding a new Search Path to the USD Asset Resolver.
- Updated the Add User Path button in the USD Asset Resolver UI.

### v0.13.5

#### What's New:
- Added support for exporting animation curves on transforms for USD 25.11+.

### v0.13.4

#### What's New:
- Updated the USD Asset Resolver to refresh and re-resolve when changes are made to the Asset Resolver Settings. Previously, a user had to reset 3ds Max.

### v0.13.3

#### Fixes:
- Fixed EXCEPTION_ACCESS_VIOLATION error when trying to export with contentSource set to #nodeAndMaterialList and no nodes where passed in the function.
- Fixed USD View rendering for version 2025.
- Fixed crash due to reserved layer being treated as anonymous when serializing to 3ds Max file.

### v0.13.2

#### What's New:
- Added support for importing materials in a USD file that are not bound to imported geometry. Previously, you could only import materials bound to geometry.

#### Fixes:
- Fixed a crash related to deleting stage with a promoted object with USD edits.

### v0.13.1

#### What's New:
- Compatibility release for 3ds Max Beta N2347-69.19 release.

### v0.13.0

#### What's New:
- New public release - version 0.13.0

#### Fixes:
- After updating to OpenUSD v0.25.08, 3ds Max is crashing when mouse hovering a USD stage.
- Fix edit box cropping letters below line in Asset Resolver preference dialog

### v0.12.5

#### What's New:
- 3ds Max Beta version now uses OpenUSD v0.25.08. No changes to 3ds Max 2026 and earlier.
- Added a new option in the animation section of the USD Exporter to include animation bones that are not being referenced by a skin modifier.
- Added an option in the animation section of the USD Exporter to preserve or not the original 3ds Max bones when exporting Usd Animations.
- Added a new option in the animation section of the USD Exporter to simplify bone paths when exporting Usd Animations.
- Added UX for managing search paths with the USD Asset Resolver.

#### Fixes:
- Fixed the Asset Resolver resolving the when the ADSK_AR_MAPPING_FILE environment variable was set.
- Fixed defect where the Duplicate as USD Data menu in the quad menus could not be customized.
- Fixed the USD Menus not having the top handle bar.
- Fixed EXCEPTION_ACCESS_VIOLATION error when trying to export with contentSource set to #nodeAndMaterialList and no nodes where passed in the function.
- Fixed a defect where a USD stage would not be found after renaming paths and opening a stage file in certain orders.
- Fixed defect where USD edits saved into a Max 2023 scene would not open correctly in 3ds Max 2026.
- Fixed an issue where the Skin options in the USD Exporter could be available when they ought to be disabled in the UI.
- Fixed an issue where exporting Materials without Geometry, where the nodegraph used Multi-materials would not export the materials correctly.
- Fixed an unexpected error after saving a 3ds Max file with anonymous stage
- Fixed an issue with .AvailableChasers() returning unexpected values.

### v0.12.4

#### What's New:
- Optimized the elementSize of joinIndices and jointWeights in USD Skel to remove unnecessary data.
- Added support for bulk deleting of prims in the USD Explorer.
- Added new USD menu to the main toolbar.
- Automatically opens the USD Explorer when creating a new USD Stage that has an anonymous root layer.
- Added a new option in the animation section of the USD Exporter to export Animation Curves. When enabled, camera properties will exported as animation curves in USD 24.11+.
- Updated the USD Exporter with a new Material option to handle Shell Materials. The options are to Use Baked, Us Original and Use Baked and Original. When using baked and original, sets the appropriate material purposes to "full" and "preview" and binds both materials to prims.

#### Fixes:
- Fixed an issue with MaterialX referencing on Import.

### v0.12.3

#### What's New:
- Updated the way anonymous layers are named. Instead of using the layer identifier, anonymous layers are now named with the convention of "anonymousLayerX", where X is the nth anonymous layer at the current level.
- Updated the cloning of USD prims via SHIFT+DRAG to preserve the original prim's reference instead of making a chain of references if there is a single reference entry on the dragged prim. This improves the cleanliness of the USD data.
- Automatically opens the USD Explorer when creating a new USD Stage that has an anonymous root layer.
- Added support to remember usd layer edit targets even for anonymous layers.
- Added Project-based tokens to the USD Asset Resolver. This will enable tokens that match those folders made by a standard Max project settings.
- Added new option in USD Exporter to export materials without requiring geometry in the export.
- Added a new method to the USDStageObject called SetStageFromCache(ID) that allows you to set change the stage used in a USDStageObject from a stage in the USD cache by passing the stage's cacheid.
- Added the ability to rename USD Prims in the USD Explorer and in the command panel.
- Added a Shader Reader for referenced MaterialX documents. When importing USD that has a MaterialX reference, the MaterialX will now be imported.
- Added new Transform Format to the USD Exporter. The options are "Single Matrix" (one transform operator) or "Separate Translate, Rotate and Sale" (one operator for each). The new separation creates data that can remain consistent between 3ds Max and other DCCs such as Maya.

#### Fixes:
- Fixed the USD Layer Editor failing to show a layer stack below any layers brought in using Stage Variable Expressions.
- Improved poor performance when duplicating many objects into a USD Stage.
- Updated the Stage Reload button in the command panel of a USDStageObject to only reload file-backed layers allowing anonymous root layers and anonymous layers belonging to all anonymous root layers to remain when reloading a stage.

### v0.12.2

#### What's New:
- Added first iteration of being able to copy prims in a USD Stage using Shift+Drag in the viewport. At this time, the feature is limited to making an Internal Reference to the "copied" prim, meaning it is prone to break if the source prim moves in the USD hierarchy.
- Added a menu to set/unset a prim in a USD Stage as the stage's default prim. The default prim is needed to properly reference a USD scene into other USD files without having to specify a prim path.
- Added new option in the viewport quad menus to duplicate selected Max objects into a new stage instead of requiring a stage from file or existing stage node.
- Added default values for new stages to set the up-axis, units and FPS based on the current Max scene settings.
- Added support for saving anonymous layer lock and mute states.
- Added the ability to reparent USD prims to change hierarchies in the USD Explorer.

#### Fixes:
- Fixed some UI issues in the USD Explorer showing the wrong selection information after deleting a stage with an anonymous root layer.

### v0.12.1

#### What's New:
- USDStageObject now creates an anonymous root layer by default.
- Added support to create and save anonymous layers in the USD Layer Editor.
- Added support to save a anonymous layers into a 3ds Max scene without saving any files to disk.
- Added the ability to reparent USD prims to change hierarchies in the USD Explorer.

### v0.12.0

#### What's New:
- New public release - version 0.12.0

### v0.11.4

#### What's New:
- Compatibility release for 3ds Max Beta N1548-69.11 release.

### v0.11.3

#### Fixes:
- Fixed an issue where 3ds Max could crash during USD Export of animated content with Splines present.
- Fixed missing root in the USD Explorer after some search operations.
- Fixed crash when toggling between prim subobject mode and object mode after deactivating prims in the USD Explorer.

### v0.11.2

#### What's New:
- Added new right-click quad menus for Duplicate as USD as well as options for the duplication.

### v0.11.1

#### What's New:
- Exposed functions to the new Export To USD api to send Max data directly to a stage object.
  
### v0.11.0

#### Fixes:
- Fixed incorrect transform of subsplines when exporting to USD shapes.
- Fixed crashes related to SHIFT+Clicking prims in the USD Explorer.
- Fixed freezing when deactivating parent and child USD prims then hitting undo.
- Fixed a crash when adding a Prim from the USD Explorer with an active search filter.

### v0.10.6

#### Fixes:
- Fixed an issue where Undoing 'Remove All' function from the Collection menu would not restore all list items that were removed.
- Fixed some issues with the refresh function of the USD Geometry Object not always working.
- Prevent crash when loading a 3ds Max file containing a USD stage object with an invalid stage file (missing/renamed file).
- Improved the UI of the USD Collection Widget type-ahead to be more readable.
- Fixed an issue where the USD Explorer hierarchy could collapse at the root level after some undo operations.
- Fixed an issue where changing the Root Layer while in Prim Sub-object mode would prevent the expected rollouts from being displayed.
- Fixed defect related to deactivating then reactivating a prim in USD.
- Fixed an unexpected doubling of entries in the Undo Stack when a USD Camera is generated from a USD Stage Object.
- Fixed the USD Layer Editor pin setting not being respected in some cases of add/removing USD stages in the scene.

### v0.10.5

#### What's New:
- Updated the label for Push to 3ds Max in the USD Explorer to Promote to 3ds Max.
- Updated the Promote to 3ds Max function to apply the XForm Controller to a Transform List Controller with another PRS Controller so that USD Geom Objects can be moved by the user.
- Added MAXScript exposure to the Stage and Prim Path properties of a USD Geometry Object.
- Updated and cleaned up the USD Geom Object UI in the modify tab.
- Added support for the OpenPBR Material in the USD Exporter.
- Added a help link in the USD Collection widget.
- Added new optional boolean parameter hideClassPrims to maxUsd.PickItems(). This option is true by default. When turned off, the USD Picker will not filter out class prims.
- Refreshed the USD Stage UI with some improved organization and addition of a button to launch the USD Layer Editor.

#### Fixes:
- Fixed a crash when undoing actions related to the new USD Geometry Object.
- Fixed the USD Selection getting lost when using the Promote to 3ds Max function.
- Fixed an issue where Collection rollouts could modify the Command Panel layout.
- Fixed error messages related to the USD Collection widget to appear red in the MAXScript Listener.
- Adding and removing items to collections is now properly respecting edit restrictions and prints information when some attempt to edit is not possible.
- Fixed an issue where Collections for some Prims were not being populated in the Rollouts.
- Fixed an issue where Light Linking Rollout wouldn't load in older versions of 3ds Max.
- Fixed a problem where the USD Collection widget could sometimes become unresponsive when the rollout had been resized.

### v0.10.4

#### What's New:
- Added redo support in the USD Geom Object purpose radio options.
- Added a refresh button to update the current mesh data on a USD Geom Object that isn't set to receive live updates.
- Added a warning to the USD Geom Object when there are settings that have possible conflicts with other USD Geom Objects derived from the same prim.
- Added new method to get/set selected layers in the USD Layer Editor. Python example:
```
python import UsdLayerEditor
layerIdList = UsdLayerEditor.getSelectedLayers()
UsdLayerEditor.setSelectLayers([layerIdList[0]])
```

#### Fixes:
- Fixed a defect that caused an error when passing a lambda function to usdUfe.registerUICallback().
- Fixed the Auto-Expand function not working in the USD Explorer.

### v0.10.3

#### What's New:
- Added new option to include/exclude prims by visibility in the Push to Max workflow in USD.
- Added option to choose what purposes are used with the Push to Max workflow from USD.
- Added lazy loading to the USD Explorer to increase performance of loading a stage into the explorer.
- Added option to serialize USD changes into the Max file for layer edits that haven't been saved to disk.

### v0.10.2

#### What's New:
- Added menu to the USD Collection Widget to copy the path to the collection.
- Added new menu in USD Explorer to Push a prim mesh to a geometry object in Max.
- Added undo support to the USD Collection Widget used in Light Linking.
- Added the ability to select prims in a scene from the USD collection widget (like light linking).

#### Fixes:
- Fixed an issue where some rollups for Prim Sub-object mode were not being loaded during selection.
- Fixed the Include All not getting toggled off when adding prims to an include list for a USD Collection.
- Fixed an error when selecting the Root prim when appending to Include/Exclude lists using the Picker.
- Fixed USD Export of camera lens and filmback properties to be measured in tenths of a scene unit rather than in millimeter units to comply to UsdGeomCamera units.
- Fixed problem where deactivating both parent and child prims could lead to a state where not all the selected prims were deactivated.
- Cleaned up some USD Attribute tooltips.
- Fixed an issue where deactivating then re-activating prims could change the USD Explorer to show unexpected prototype prims.

### v0.10.0

#### What's New:
- Added support for displaying USD Curves in the viewport. Note that the display only supports linear interpolation at this time.
- Added a menu in the USD Collection Widget to remove all prims from the Include/Exclude lists at once.

#### Fixes:
- Fixed an issue where Collection rollouts could modify the Command Panel layout.
- Fixed error where prims in the USD Collection Widget could get deleted unexpectedly.
- Fixed an issue where Collections for some Prims were not being populated in the Rollouts.
- Fixed an issue where Light Linking Rollout wouldn't load in older versions of 3ds Max.
- Fixed label in the Undo Stack of the Mute USD Layer action.
- Fixed an issue where the Title for the modal window for Picking Prims for the Light Linking should reflect where it's being added from.
- Fixed a problem where the USD Collection widget could sometimes become unresponsive when the rollout had been resized.
- Fixed the USD Exporter not adding the extent property to USDBasisCurves when exporting a shape from 3ds Max.
- Fixed error where locking a layer on a USD Stage would clear the current prim selection.
- Fixed a crash in the USD Stage Object when switching between kinds and prim object/sub-object mode.
- Fixed undo getting lost when a USD Layer is muted.
- Fixed the bulk remove of sublayers in the USD Layer Editor not always respecting the entire selection.
- Fixed USD Light gizmos displaying in the viewport even when the prim is hidden.
- Fixed an issue where the Viewport Selection Rollout was missing from the Rollouts while in Prim Sub-object Mode.
- Updated the USDStageObject to accept loading a USD stage that has no prims.
- Fixed error when redoing an action after doing an undo after prim creation in the USD Stage.

### v0.9.8

#### What's New:
- Updated the USDStageObject to preserve the current EditTarget when reloading a scene.
- Added option in USD Stage Object to control the gizmo sized of light shapes.
- Added prompt to Save or Discard unsaved edits in USD Layers when reloading a stage that has edits that are not yet saved.
- Updated menus for creating a USD Stage from file to automatically select the USDStageObject upon creation.
- Updated the USD Layer Editor to set layers to System Lock when the layer is a read-only file on disk.
- Updated lights gizmos from a USD stage to appear yellow to match Max behaviors. Selected lights are still the color of the USD Stage Object selection color.
- Added a new USD Prim Pick mode that can be initiated from the new UsdSharedComponents extension. To load the extension use `maxExtension = Python.Import("UsdSharedComponents.maxExtension");` to call the pick mode, use `maxExtension.PickPrim(stage)` where stage is a python object storing the stage. This function acts similarly to the MAXScript `PickObject()` method but is for returning UFE paths that allow you to process the picked prims for custom purposes.
- Update Max USD to default to the Root Layer as the Edit Target when creating a new stage object. Previously we targeted the session layer by default, but now that the Layer Editor is available we moved it to the Root Layer.
- Added "Class Prims" entry into the USD Explorer Display menu to Show/Hide USD prims in the USD Explorer.

#### Fixes:
- Fixed a crash in the USD Stage Object when switching between kinds and prim object/sub-object mode.
- Fixed the display of prim hierarchy in the USD Explorer when adding a prim to a class prim.
- Fixed defect where reordering layers in the USD Layer Editor could remove the layers entirely.
- Fixed incorrect warning of unsaved edits in a stage when exporting a scene with a stage that generated session data for automatic pointInstance draw modes.
- Properly expose the 3ds Max USD python method `maxUsd.JobContextRegistry.GetJobContextInfo()`.
- Fixed the USD Layer Editor to hide the Remove Layer menu for the root layer and session layer.
- Fixed warning icon in the USD Layer Editor missing from layers that cannot be resolved.
- Fixed USD Layer Editor DPI scaling issues.

### v0.9.7

#### What's New:
- Added option in USD Stage Object to control the gizmo size of light shapes.
- Updated light gizmos from a USD stage to appear yellow to match 3dsMax behaviors. Selected lights are still the color of the USD Stage Object selection color.
- Updated the Display rollout in a USD Stage Object to remain accessible when in Prim Sub-Object mode.
- Updated OpenEXR to OpenEXR 3.3.1 in Max USD.
- Updated the Display rollout in a USD Stage Object to remain accessible when in Prim Sub-Object mode.
- Added an option to display class prims in the USD Explorer.
- Added a confirmation dialog when reloading USD stages.

#### Fixes:
- Fixed error loading some USD Layers in the Layer Editor.
- Fixed bug causing reconsolidation on light gizmo selection.

### v0.9.6

#### What's New:
- Exposed the USD Layer Editor commands to Python.
- Added Edit Restrictions to Max USD to block edits when there is a stronger opinion for that property.
- Added ability to load a sublayer to a layer in the USD Layer Editor.
- Added a bulk save dialog for the USD Layer Editor.
- Updated the MaterialX Exporter in Max USD to use nodegraphs.
- Added support to export a materialX reference using OpenPBR materials in the MaterialX shader writer for USD.

#### Fixes:
- MaterialX Scripted material fails more gracefully when the import fails.

### v0.9.5

#### What's New:
- Updated the USD Layer Editor to correctly respect 3ds Max color themes.
- Added ability to create a new stage from file from the USD Layer Editor.
- Added options for saving dirty layers in a USD Stage when saving a Max scene. Note that in the current iteration, this will create a pop-up even during Autosave to determine what to do with dirty layers. We intend to address this in a coming update.
- The bulk save function in the USD Layer Editor displays the number of layers needing saved.
- Added a right-click menu to print a layer in the USD Layer Editor to the MAXScript Listener. If the layer is more than 400 lines or 50K characters, the function will ask to confirm the print before printing since very large datasets can take a long time to print.
- The USD Layer Editor can now reload a selected layer.
- The Mute and Lock states of a USD Layer is now preserved between Max sessions.
- Users can now launch the USD Layer Editor from the USD Explorer as well as the `Tools > USD > USD Layer Editor...` menu.

### v0.9.4

#### What's New:
- Added support for displaying USD Light shapes in the viewport. Note they do not cast lights into the viewport but can be selected and edited. The results will render in renderers with USD support.
- Update some labels and tooltips around the Draw Mode functions in the USD Stage Object.
- Updated the MaterialX Plugin to use MaterialX version 1.38.8 for 3ds Max 2025.

#### Fixes:
- Fixed some of the shipped tools such as USDView not working because of Powershell security policy settings.
- Fixed an issue where Selecting the USD Stage from a recently edited prim wouldn't always work.
- Animated USD attributes now refresh when scrubbing the timeline.
- Prevent situation where deactivating parent and child leave selection in state that can cause crash.
- Fixed incorrect progress reporting in the USD Importer.
- Fixed an issue where Display Purpose may not sync correctly when changing the file reference in an existing USD Stage or when changing display purposes.

### v0.9.3

#### What's New:
- Updated the USD Exporter to better convert Max Shapes into USD instead of always exporting to linear interpolations.
- Added USD Layer Editor to target layers for USD edits.
- Updated the MaterialX Material to save the current material by name instead of index. This guarantees that sub-materials in a USD remain intact in Max even if the MaterialX document changes the ordering of materials.
- Exposed more of the MaterialX Exporter options to MAXScript.
- Updated the MaterialX plugin to support importing MaterialX documents using OpenPBR shaders.
- Introducing the concept of a devkit archive delivered inside the 3dsMax USD plugin installation. The devkit includes all the 3ds Max USD SDK files (includes and libs), the samples and the minimal dependencies (includes and libs) required to compile the samples (or any third-party add-on plugin for the 3ds Max USD plugin). Additionally, the devkit contains the other dependencies required to compile the open-sourced 3ds Max USD plugin (still in preparation). The previous 3ds Max USD SDK archive is not produced anymore. The devkit is its replacement.

#### Fixes:
- Fixed an issue where the Material Export toggle in the USD Exporter was incorrectly reporting as enabled, when in fact it was disabled.
- Deactivating parent and child leave selection in state that can cause crash.

### v0.9.2

#### What's New:
- Made some USD rollouts more human readable instead of always coming directly from USD Schemas.

#### Fixes:
- Fixed and issue where USD Stage roll-ups would not retain their user set state when switching to sub-object mode.
- The MaterialX Exporter now writes out "sRGB" colorspace instead of "gamma22".

### v0.9.1

#### What's New:
- Compatibility release for 3ds Max Beta D2634-67.16 release.

### v0.9.0

#### What's New:
- Bump component version to 0.9 - new public release.

#### Fixes:
- Changed MaterialX plugin utility to export using colorspace "sRGB" by default instead of "gamma22".
- Fixed an issue where setting Textures for Draw Modes in the Prim Attributes could cause 3ds Max to become unresponsive.
- Cleaned up USD Attribute Tooltips.
- Fix expected behavior on PrimReader plugin not being loaded when 'providesTranslator' type is an ancestor type.
- Fixed spinner for USD Prims to allow negative values.
- Fixed options for loading a stage not persisting between multiple stage-loading actions.
- Fixed unnecessary memory usage from textures that should not be loaded from deactivated prims in a USD Stage Object.
- Update some labels and tooltips around the Draw Mode functions in the USD Stage Object.
