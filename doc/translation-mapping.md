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

## Status

| Source (3ds Max) | MaterialX target | Kind | Affects MaterialX nodedef | Workaround triggers when | PR | Date |
| --- | --- | --- | --- | --- | --- | --- |
| PhysicalMaterial.anisotropy_angle | `standard_surface.specular_rotation` | bug normalization | `ND_standard_surface_surfaceshader` | Bridge emits `0.25` AND `specular_anisotropy` is static-zero or absent | MAX-MAT-001 | 2026-06-20 |
| PhysicalMaterial.emission (none authored) | `standard_surface.emission` + `standard_surface.emission_color` | bug normalization | `ND_standard_surface_surfaceshader` | Bridge emits `emission = 1.0` AND `emission_color = (0, 0, 0)`, both static | MAX-MAT-002 | 2026-06-20 |
| Node.wireColor / Node.material.diffuse | `UsdGeomMesh.primvars:displayColor` | bug normalization | (mesh primvar; not a shader nodedef) | `MeshConverter` is about to author wireColor into `primvars:displayColor` AND `node->GetMtl() != nullptr` | MAX-MAT-003 | 2026-06-20 |
| Mesh normals (every interpolation) | `primvars:normals` AND `UsdGeomMesh.normals` (the schema attribute) | bug normalization | (mesh attribute; not a shader nodedef) | `NormalsMode` default is now `Both` -- both locations are authored unless the user explicitly picks `AsPrimvar` or `AsAttribute` | MAX-GEO-001 | 2026-06-20 |
| Map channel 1 missing on the converted MNMesh | `primvars:st` (channel 1's configured primvar) | approximating workaround | (mesh primvar; not a shader nodedef) | `ApplyMaxMapChannels` did not author the channel-1 primvar AND channel 1 is not explicitly opted out (`GetChannelPrimvarConfig(1).GetPrimvarName().IsEmpty()`) AND VertexCount() > 0 AND FaceCount() > 0 | MAX-GEO-004 | 2026-06-20 |
| Per-face matIds on a mesh whose bound material is non-MultiMtl | (no GeomSubsets; first matId stored as `customData.3dsmax.matId` on the Mesh prim) | bug normalization | (Mesh prim; not a shader nodedef) | `materialIdToFacesMap.size() > 1` AND `node->GetMtl()` is null or a non-MultiMtl AND the prim does not already have existing `materialBind` subsets | MAX-GEO-002 | 2026-06-20 |
| GeomSubset name for the null / non-Multi / unnamed-slot fallback path | `mat_{maxScriptId}` (single material) / `mat_{maxScriptId}_{subMtlName}` (multi w/o slot name) | cosmetic normalization | (Mesh / GeomSubset prim name; not a shader nodedef) | `MaterialUtils::CreateSubsetName` is invoked AND (the bound material is null/non-Multi OR the Multi/Sub-Object slot name is empty) | MAX-GEO-003 | 2026-06-20 |
| 3ds Max camera Near Clip / Far Clip values (every camera type, regardless of "Clip Manually") | `UsdGeomCamera.clippingRange` | bug normalization | (camera schema attribute; not a shader nodedef) | `CameraWriter::Write` is invoked AND the camera object resolves as a `GenCamera` -- the writer now authors `clippingRange` unconditionally from `GetClipDist(...)` rather than gating on `GetManualClip() != 0`, with degenerate values (≤ 0, NaN, far ≤ near) sanity-clamped to `(1.0, 1000.0)` in scene units | (this PR) | 2026-06-20 |

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

**Retirement condition.** Same as MAX-MAT-001: when Autodesk fixes
`MtlxIOUtil` to stop emitting the spurious emission pair, the pass
becomes inert. Safe to keep as a guard for older 3ds Max installs.

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

## Expressions with no MaterialX equivalent

| Source (3ds Max) | Why no equivalent | Behavior in current fork |
| --- | --- | --- |
| (none catalogued yet) | | |

## Change log

* 2026-06-20 — Initial doc. MAX-MAT-001 specular_rotation default
  normalization landed.
* 2026-06-20 — MAX-MAT-002 emission/emission_color default-pair
  normalization landed (strip `emission = 1.0` paired with
  `emission_color = (0, 0, 0)`).
* 2026-06-20 — MAX-MAT-003 mesh displayColor leak fixed (derive
  `primvars:displayColor` from the bound material's `GetDiffuse()`
  instead of the node's viewport wireframe color, falling back to the
  wireframe color only when no material is bound).
* 2026-06-20 — MAX-GEO-001 mesh normals dual-author: add
  `NormalsMode::Both` and make it the new default so the writer
  populates both `primvars:normals` AND `UsdGeomMesh.normals` (the
  schema attribute). Existing `AsPrimvar` and `AsAttribute` selectors
  unchanged.
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
