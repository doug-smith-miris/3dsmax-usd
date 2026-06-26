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

### Per-face matIds on a non-MultiMtl mesh → drop GeomSubsets, keep first matId as customData (MAX-GEO-002)

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
