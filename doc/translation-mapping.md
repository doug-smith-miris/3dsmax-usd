# 3ds Max → USD translation mapping

This document tracks how 3ds Max source objects (lights, materials,
geometry, cameras) translate into USD prims and shaders as they pass
through the Miris fork's per-prim writers.

Generated and maintained by the Improvement Agent. Each entry
corresponds to a PR that:

1. Updates the relevant row of the **Status** table below.
2. Lands a C++ change in the corresponding writer (one of
   `src/translators/*Writer.cpp`) OR an audit that pins the existing
   scope with negative-test coverage without widening C++ (see the
   `feedback-scope-coverage-audit` shape).
3. Lands a validator script (Python, runnable under `hython`) that
   proves the logic against a synthetic fixture without requiring a
   Windows build of the plugin.
4. Where the bite is a *workaround for an upstream 3ds Max bug*, it
   documents which 3ds Max version(s) the workaround targets and what
   condition retires it.

Status legend:

* **direct pairing** — a 1:1 mapping between a 3ds Max source class /
  property and a USD prim / attribute the writer authors.
* **approximating workaround** — the writer composes a USD result
  that approximates a 3ds Max feature the schema does not natively
  represent.
* **bug normalization** — the writer strips or rewrites a value that
  the upstream bridge or installed plugin emits incorrectly.
* **audit (lock-in)** — no C++ logic change; the entry pins the
  existing scope with negative-test coverage so a future wildcard
  refactor fails by named case.
* **no equivalent** — the source has no USD representation; the
  writer emits a warning so the divergence is observable.

## Status

| Source (3ds Max) | USD target | Kind | Trigger / bound | PR | Date |
| --- | --- | --- | --- | --- | --- |
| `VRayLight` / `VRayIES` / `VRaySun` / `VRayAmbientLight` (LIGHT\_CLASS\_ID + class-name whitelist) | `UsdLuxSphereLight` / `UsdLuxRectLight` / `UsdLuxDiskLight` / `UsdLuxDistantLight` / `UsdLuxDomeLight` (per-subtype dispatch on className + `type` param) | direct pairing | Every V-Ray light in the source scene lands on the correct UsdLux subtype with the correct shape attrs (Rect: width/height; Sphere/Disk: radius; Distant: angle 0.53°; Dome: nothing). IES profile authored ONLY for VRayIES. | MAX-LIT-002 | 2026-08-11 |
| Per-subtype scope of the MAX-LIT-002 dispatch | Same as above | audit (lock-in of existing surgical-coverage bound, no C++ logic change) | Locks in that each classifier branch authors its own contract and NO other. A wildcard refactor that paints ies:file on a plain Disc (VRayLight type=4), or width/height on a Dome, or radius on a Rect, or unit-converts intensity by default, must fail the audit's 14 named-case suite BEFORE landing. Arena-density census (185 lights → 1 Distant + 68 Disk + 80 Rect + 20 Sphere + 16 Dome) is the anchor case. | MAX-LIT-003 | 2026-08-11 |
| Scope of the MAX-MTLX-007 wrapper walk (`unwrapBlendMaterialSubMtls`) | `outputs:mtlx:surface` NodeGraph texture inputs on wrapper-authored materials | audit (lock-in of existing surgical-coverage bound, no C++ logic change) | Locks in that `unwrapBlendMaterialSubMtls` descends EXACTLY the two wrapper classes `VRayBlendMtl` (10 slots: baseMtl + coatMtl\_1..9) and `VRayOverrideMtl` (5 slots: baseMtl / giMtl / reflectMtl / refractMtl / shadowMtl), with baseMtl-first ordering, and REJECTS every other wrapper-shaped material class (adjacent V-Ray: VRay2SidedMtl, VRayMtlWrapper, VRayBumpMtl, VRayFastSSS2, VRayLightMtl, VRayHairMtl; stock: DoubleSided, Shell Material, Blend, Composite Mtl, Top/Bottom, Matte/Shadow, Multi/Sub-Object). Collision-shape anchor: `.baseMtl` exists on VRayOverrideMtl (walked) AND on VRayMtlWrapper / VRayBumpMtl (NOT walked). A refactor to "check for `.baseMtl` attribute" fails the 48-case suite by named case. | MAX-MTLX-008 | 2026-08-12 |
| PhysicalMaterial `.bump_map_amt` / OpenPBR `.bumpMapAmount` / VRayMtl `.bump_multiplier` / StdMaterial `.bumpAmount` (scalar bump-strength multiplier) | `ND_normalmap_float.scale` (or `ND_normalmap_vector2.scale`) input on every scaffolded normalmap node in the shader's NodeGraphs | direct pairing | The scalar is dropped alongside `ND_normalmap.in` by `MtlxIOUtil.ExportMtlxString`. `_ApplyBumpStrengthToNormalmaps` runs AFTER `_WireDanglingNormalmapInputs` (MAX-MTLX-003) and BEFORE `_AddDependentNodes`, walks every normalmap-class node in the shader's NodeGraphs, and authors `scale` from the Max material's discovered strength property. Discovery reuses the MAX-MTLX-007 base-first `unwrapBlendMaterialSubMtls` wrapper walk (baseMtl's strength wins over any coat's). No-op contract: (a) shader has no normalmap nodes → skip MAXScript round-trip; (b) no discoverable strength property → return 0; (c) discovered value equals port default 1.0 within 1e-6 → return 0 (doc stays clean); (d) non-numeric property value (e.g. a Texmap in the same-name slot on an unfamiliar wrapper) → skip that entry; (e) negative value → clamped to 0. | MAX-MTLX-012 | 2026-08-12 |

## Notes per entry

### V-Ray light writer per-subtype scope (MAX-LIT-003)

**Symptom / risk this audit prevents.** After MAX-LIT-002 landed the
`MaxUsdVRayLightWriter` and taught it to dispatch every V-Ray light
class onto the closest UsdLux prim type, the auto-planner emitted a
follow-on bite (`max-lit-003-vraylights-to-usdlux`, this entry) whose
literal reading was "specialize the mapping so each of the 185 arena
lights round-trips to the correct UsdLux subtype with correct intensity
units, color temperature, cone shaping (IES → UsdLuxShapingAPI.ies:file),
and directional-ness (VRaySun → UsdLuxDistantLight)". Reading MAX-LIT-002
against the current fork HEAD showed that the C++ writer already
implements the entire specialization the planner asked for: class-name
dispatch to Distant/Disk/Dome, `type`-param dispatch to Rect/Sphere/Disk,
IES asset-path authoring via `UsdLuxShapingAPI`, temperature clamping,
disabled-light zero-out, and per-branch shape-attr authoring
(width/height on Rect, radius on Sphere/Disk, angle 0.53° on Distant,
none on Dome).

The remaining risk is that a future refactor — say, "unify the five
branches into one generic light-authoring path" or "silently convert
V-Ray intensity units to USD nits" — silently mutates the per-subtype
contract. Because the writer's tests (as of MAX-LIT-002) exercise
each branch in isolation, a wildcard widening would slip through
positive-case coverage. MAX-LIT-003 adds the SCOPE assertions: the
inverse of every branch's contract, so a wildcard failure surfaces by
named case instead of silently mutating artist-authored data.

**Why it matters.** The Spectrum Center arena carries 185 V-Ray lights
across five distinct subtypes; the auto-planner rationale explicitly
called out that "coverage delta must show every VRayLight instance in
the source scene has a corresponding UsdLux prim of the RIGHT type —
not just any UsdLux prim". Losing per-subtype fidelity would break:

* Karma / Storm / RenderMan light shape sampling (Rect ≠ Sphere ≠ Disk
  sampling in every hydra delegate).
* IES cone shaping on VRayIES fixtures — the arena's 60 downlights
  each carry a specific IES profile that drives the visible pool
  pattern on the concourse floor; a fallback to plain DiskLight would
  wash the pool into a flat disc.
* Sun shadow geometry — VRaySun as a DistantLight with angle=0.53°
  gives soft-edged sun shadows on the arena bowl; regressing to
  SphereLight or DomeLight makes the sun a hard shadow or no shadow.

**Fix.** No C++ logic change (the Mac cannot build the maxUsd plugin;
see the fix playbook's MAX-OPS-001 entry). The audit adds:

* `src/Tests/Integration/test_miris_max_lit_003.py` — 14-case
  scope-audit suite that:
  - Locks in the 185-light arena distribution census
    (1 Distant + 68 Disk + 80 Rect + 20 Sphere + 16 Dome).
  - Pins each subtype's authoring contract (radius / width / height /
    angle / ies:file) AND the inverse — every attr each subtype
    MUST NOT author.
  - Pins the intensity pass-through invariant across all subtypes
    (no silent unit conversion).
  - Pins the IES asset-path VERBATIM contract (no relative rewriting,
    no case flip, no slash flip).
  - Extends the MAX-LIT-002 class-name gate negatives with 12
    additional non-light V-Ray class names that a wildcard match
    would misclaim (VRayProxy, VRayMtl, VRayBitmap, VRayFastSSS2,
    VRayCamera, VRayPhysicalCamera, VRayDisplacementMod,
    VRayEnvironmentFog, VRayInfiniteVolume, plus foreign-SDK PointLight
    and empty-string guard).
  - Idempotence + arena delta invariant re-checks.
  - Fallback-type-preservation check (future VRayLight type=5/42/99
    must land on RectLight, not silently drop).
  - `CrossWriter_divergence` anchor case — six subtypes side-by-side
    on the same fixture; asserts each preserves its own convention
    with zero bleed.
* `src/translators/VRayLightWriter.cpp` — comment-only surgical-bounds
  block at `_ClassifyVRayLight` enumerating the exclusive contract per
  branch. No logic change; the comment is the safety anchor that
  points a future maintainer at the audit tests before widening.

**Bounds — WHERE THE V-RAY LIGHT WRITER STOPS.**

* `VRayLight`, `VRayIES`, `VRaySun`, `VRayAmbientLight` are the ONLY
  claimed class names. Anything else with "vray" in the name (proxy,
  material, bitmap, camera, atmospheric, modifier) is REJECTED.
* Every branch authors ONLY the shape attrs listed in its row of the
  bounds table (see the C++ comment block or the audit tests). Painting
  attrs across branches would violate UsdLux schema and break Karma's
  shape sampling.
* Intensity is pass-through. Any unit conversion must be an explicit
  opt-in flag, not a silent default. Audit case
  `test_intensity_passes_through_verbatim_across_all_subtypes` catches
  regressions on all seven dispatch branches.
* IES asset path is pass-through. Audit case
  `test_ies_asset_path_preserved_verbatim` exercises five path shapes
  (forward slashes, backslashes, UNC, relative, uppercase) that a
  "helpful" refactor might touch.

**Validator.** `src/Tests/Integration/test_miris_max_lit_003.py` runs
under `hython` from any dev machine (no Max SDK required):

```
$ hython src/Tests/Integration/test_miris_max_lit_003.py -v
Ran 14 tests in 0.21s — OK
```

**MaxScript regression.** Deferred to the Windows build host — the
MAXScript-side regression that exercises the arena export is a
follow-on bite (would live at `src/Tests/Integration/
export_vray_light_scope_test.ms`). The Python validator locks in the
USD-layer contract; the on-box regression locks in the Max-side
`discoverMaxVrayLight` probe manifest that feeds it.

**Visual demonstration.** Structural-only — this audit does not change
what Karma renders (LIT-002's per-subtype dispatch already produced the
correct pixels for the arena). See `structural_only.md` in the arch-
build artifact directory for the auditor's rubric.

**Retirement condition.** If a future MAX-* bite deliberately widens
the writer to add a new dispatch branch (e.g. VRayLight type=5 gains a
first-class UsdLux subtype), that bite MUST (a) extend the audit's
subtype table and (b) add its own per-branch positive + negative test
cases before landing. The audit's docstring records the current branch
table so the diff surface for such an extension is small.

### MTLX wrapper-walk scope (MAX-MTLX-008)

**Symptom / risk this audit prevents.** MAX-MTLX-007 (branch
`miris/max-mtlx-007-walk-blend-mtl-sub-materials`) added
`unwrapBlendMaterialSubMtls` in `src/translators/MtlxShaderWriter.cpp`
— the MAXScript recursion that expands `VRayBlendMtl` and
`VRayOverrideMtl` into their sub-materials so the standard
texture-map discovery loop can see the maps that were previously
dropped. The auto-planner then emitted a follow-on bite
(`max-mtlx-008-vray-override-basemtl-audit`, this entry) whose
literal reading was "VRayOverrideMtl (GImtl wrapper) baseMtl not
unwrapped on standard map-discovery path". Reading the fork HEAD
after MAX-MTLX-007 shows that VRayOverrideMtl.baseMtl IS unwrapped —
the planner's rationale was captured against a baseline that
predates MAX-MTLX-007. So the literal task is done. What remains
uncovered is the SCOPE of the wrapper walk: which classes it fires
on, which it doesn't, and why the boundary sits exactly where it
does.

The risk the audit closes is a future "unify wrapper handling"
refactor — say, replacing the class-name gate with an attribute-
name heuristic (`if isProperty m #baseMtl then descend`), or
allowing prefix / substring matches against V-Ray class names, or
adding a slot to `unwrapBlendMaterialSubMtls`'s per-class slot list
without a corresponding bite. Each of those would silently widen
scope on the 179-material arena baseline: VRayMtlWrapper /
VRayBumpMtl carry `.baseMtl` too and would start being descended,
double-counting the substrate against the wrapper's own
render-pass / bump-only role.

**Why it matters.** The Spectrum Center arena carries wrappers
across MAX-MTLX-007's target classes (VRayBlendMtl on layered
paint, VRayOverrideMtl on GI-corrected glazing) AND across the
adjacent classes MAX-MTLX-007 is intentionally NOT walking
(VRay2SidedMtl on translucent panels, VRayMtlWrapper on
render-pass-tagged assets, VRayBumpMtl on bump-only wrappers).
A refactor that silently descended into the adjacent classes
would double-emit `ND_tiledimage` nodes into the mtlx surface
network and produce non-authoritative textures for those
materials — the wrapper's own visual role would be
misrepresented.

**Fix.** No C++ logic change (the Mac cannot build the maxUsd
plugin; see the fix playbook's MAX-OPS-001 entry). The audit adds:

* `src/Tests/Integration/test_miris_max_mtlx_008.py` — 48-case
  scope-audit suite that:
  - Pins the class-name gate as EXACT (superstring / substring /
    different-suffix rejections; empty-string guard).
  - Locks in the case-insensitive MAXScript `==` semantics for the
    gate (all-lower / all-caps class-name variants would fire —
    documented as intentional and bounded).
  - Rejects six adjacent V-Ray wrapper classes explicitly
    (VRay2SidedMtl, VRayMtlWrapper, VRayFastSSS2, VRayLightMtl,
    VRayBumpMtl, VRayHairMtl).
  - Rejects seven 3ds Max stock wrapper classes explicitly
    (Multi/Sub-Object, DoubleSided, Shell Material, Blend
    (stock), Composite Mtl, Top/Bottom, Matte/Shadow).
  - Pins the `.baseMtl` COLLISION SHAPE — the same attribute
    name exists on VRayOverrideMtl (walked) AND on
    VRayMtlWrapper / VRayBumpMtl (NOT walked); the class-name
    gate is the sole boundary.
  - Pins slot-table completeness: exactly 10 slots for
    VRayBlendMtl (baseMtl + coatMtl\_1..coatMtl\_9), exactly 5
    for VRayOverrideMtl (baseMtl, giMtl, reflectMtl, refractMtl,
    shadowMtl). A novel slot (e.g. `.envMtl`) must NOT be
    walked without an explicit slot-table update.
  - Pins the baseMtl-first ordering as load-bearing (a
    hypothetical reversal changes user-visible texture
    precedence — surfaces the differential explicitly).
  - Idempotence + depth-cap + cycle safety at scope-audit scale.
  - Arena census delta anchor: the 89-material resolved-diffuse
    delta MAX-MTLX-007 introduced must survive any future
    refactor, on a 189-material mix that includes 10 adjacent-
    wrapper negatives whose textures MUST remain invisible.
  - Cross-wrapper divergence anchor: VRayOverrideMtl and
    VRayMtlWrapper side by side, same-looking `.baseMtl`
    substrates; walked resolves ONE, unwalked resolves ZERO.
* `src/translators/MtlxShaderWriter.cpp` — surgical-bounds
  comment block inside `discoverMaxMtlxTexmapsFn`, immediately
  above the `unwrapBlendMaterialSubMtls` call. Enumerates the
  collision shape (`.baseMtl` on walked vs. non-walked classes),
  the case-insensitive `==` semantics, the six adjacent V-Ray
  classes deliberately excluded, and the seven stock 3ds Max
  classes deliberately excluded. No logic change; the comment
  is the anchor that points a future maintainer at the audit
  before widening.

**Bounds — WHERE THE WRAPPER WALK STOPS.**

* Walked classes: `VRayBlendMtl` (10 slots: baseMtl +
  coatMtl\_1..coatMtl\_9), `VRayOverrideMtl` (5 slots: baseMtl,
  giMtl, reflectMtl, refractMtl, shadowMtl). Anything else is
  identity (returns `#(m)`).
* Class-name gate uses MAXScript `==` which is case-insensitive
  on strings. Canonical V-Ray class names are PascalCase, so the
  case-insensitive tolerance is a boundary condition rather than
  an accidental widening.
* Slot ordering is baseMtl-first. The first-hit-wins dedupe in
  `discoverMaxMtlxTexmaps` combined with this ordering is what
  makes baseMtl's textures beat any per-ray override or coat.
  A future reordering (say "for symmetry with the per-ray table
  layout") would silently change texture precedence.
* Attribute-name descent (any material with a `.baseMtl`) is
  DISALLOWED. The class-name gate is the sole boundary against
  VRayMtlWrapper / VRayBumpMtl.
* Extending scope to a new wrapper class is a SEPARATE future
  bite (its own captured leak corpus + own tests + own doc
  entry). Do NOT add classes to `_WRAPPER_TABLE` without one.

**Collision-shape table.**

| Attribute | Walked classes | Non-walked classes | Guard |
| --- | --- | --- | --- |
| `.baseMtl` | VRayOverrideMtl, VRayBlendMtl | VRayMtlWrapper, VRayBumpMtl | class-name gate |
| `.giMtl` / `.reflectMtl` / `.refractMtl` / `.shadowMtl` | VRayOverrideMtl | (none; unique to VRayOverrideMtl) | slot-table membership |
| `.coatMtl_1..coatMtl_9` | VRayBlendMtl | (none; unique to VRayBlendMtl) | slot-table membership |
| `.frontMtl` / `.backMtl` | (none) | VRay2SidedMtl | class-name gate |
| `.mtl1` / `.mtl2` | (none) | DoubleSided, stock Blend | class-name gate |
| `.originalMtl` / `.bakedMtl` | (none) | Shell Material | class-name gate |
| `.mtlList` | (none) | Composite Mtl | class-name gate |
| `.materialList` | (none) | Multi/Sub-Object | class-name gate |
| `.topMtl` / `.bottomMtl` | (none) | Top/Bottom | class-name gate |

**Validator.**
`src/Tests/Integration/test_miris_max_mtlx_008.py` runs under
`hython` from any dev machine (no Max SDK required):

```
$ hython src/Tests/Integration/test_miris_max_mtlx_008.py -v
Ran 48 tests in 0.001s — OK
```

**MaxScript regression.** Deferred to the Windows build host — the
on-box test that exercises the arena export with side-by-side
VRayOverrideMtl and VRayMtlWrapper instances is a follow-on bite
(would live at `src/Tests/Integration/
export_mtlx_wrapper_scope_test.ms`). The Python validator locks in
the walker-level contract; the on-box regression would lock in
the Max-side `classOf` return values that feed it.

**Visual demonstration.** Structural-only — this audit does not
change what Karma renders (MAX-MTLX-007 already produced the
correct pixels for VRayOverrideMtl.baseMtl-walked materials; the
audit locks in the boundary without a code path change). See
`structural_only.md` in the arch-build artifact directory for the
auditor's rubric.

**Retirement condition.** If a future MAX-* bite deliberately
extends the wrapper walk (e.g. adds `VRay2SidedMtl` to the walk
under a `subGeomSubset`-aware handling contract), that bite MUST
(a) update `_WRAPPER_TABLE` in the MAXScript block, (b) update
the collision-shape table above with the new attribute, (c) add
its own per-class positive AND negative tests to
`test_miris_max_mtlx_008.py` before landing. The audit's
docstring records the current wrapper set so the diff surface
for such an extension is small.

### Bump / normal-map strength on ND_normalmap (MAX-MTLX-012)

**Symptom this fix removes.** Every scaffolded `ND_normalmap_float`
in a MaterialX NodeGraph ran with `scale` at its port default
`1.0`, regardless of the artist-authored bump-strength scalar on
the source material (PhysicalMaterial `.bump_map_amt`, OpenPBR
`.bumpMapAmount`, VRayMtl `.bump_multiplier`, StdMaterial
`.bumpAmount`). The scalar was dropped alongside `ND_normalmap.in`
by `MtlxIOUtil.ExportMtlxString`. Even AFTER MAX-MTLX-003 wired
the tiledimage into `normalmap.in`, Karma / Hydra evaluated the
tangent-space delta at 100% because `scale` fell back to the
port default. Materials the artist dialed down (0.2-0.4 for
weathered stone, carpet weave) or up (2.0+ for hero brick,
masonry) all rendered at 100% bump intensity — the map data was
present, the strength scalar was dropped. See
`agent/pipeline-runs/e218e7b9-.../evidence-slotmap-and-wrappers.md`
for the enumeration that surfaced this alongside MAX-MTLX-006.

**Fix.** `_ApplyBumpStrengthToNormalmaps(mtlxDoc, shaderNode,
animHandle)` in `src/translators/MtlxShaderWriter.cpp:1390`.
Called after `_WireDanglingNormalmapInputs` (MAX-MTLX-003) and
before `_AddDependentNodes`. Walks every normalmap-class node in
the shader's NodeGraphs (same category / name gate MAX-MTLX-003
uses so the two functions agree on which nodes count), asks Max
for the material's bump-strength scalar via
`discoverMaxMtlxBumpStrengthFn`, and authors `scale` on each
normalmap. Discovery reuses the MAX-MTLX-007
`unwrapBlendMaterialSubMtls` wrapper walk — base-first traversal
so `VRayBlendMtl.baseMtl`'s strength wins over any coat's, and
`VRayOverrideMtl.baseMtl`'s strength wins over per-ray
overrides.

**Property table** (checked in order per sub-material, first
numeric hit wins for that sub-material):

  1. `bump_map_amt`    — PhysicalMaterial (Autodesk stock, snake_case)
  2. `bumpMapAmount`   — PhysicalMaterial / OpenPBR (camelCase)
  3. `bump_multiplier` — VRayMtl (pre-Scene-Converter)
  4. `bumpAmount`      — StdMaterial (legacy)

**No-op contract** (each case has a named test in
`test_miris_max_mtlx_012.py`):

  * Shader has no normalmap nodes → skip the MAXScript round-trip
    entirely (matches MAX-MTLX-003's conservative no-op).
  * No discoverable strength property on any walked sub-material
    → `_ApplyBumpStrengthToNormalmaps` returns 0, doc unchanged.
  * Discovered value equals the ND_normalmap port default `1.0`
    within 1e-6 → return 0. This keeps the exported doc clean of
    synthetic `scale=1` inputs on every material whose artist
    accepted Max's UI default.
  * Non-numeric property value (e.g. a Texmap in the same-name
    slot on an unfamiliar wrapper class) → walker skips that
    entry and tries the next property / sub-material rather than
    mis-authoring a garbage `scale`.
  * Negative artist-authored value → clamped to 0 in
    `_ParseBumpStrengthString`, matching Max's own UI
    non-negative clamp on bump.

**Validator.** `src/Tests/Integration/test_miris_max_mtlx_012.py`
runs 33 cases under Houdini's `hython` covering pre-fix defect
visibility, per-property dispatch (all four supported spellings),
wrapper walk (blend base wins, override base wins, nested
blend-of-blend), port-default skip (both exact 1.0 and near-1.0
epsilon), non-numeric skip (fallthrough to next property /
sub-material), negative clamp, multi-normalmap (two normalmaps
in one graph both receive `scale`), no-normalmap shader
(early-out), surgical scope (only `scale` authored — `in`,
`normal`, `tangent`, `bitangent`, `file` all untouched, no
duplicate `scale` inputs on idempotent re-apply), walker-reuses-
MTLX-007 (VRayMtlWrapper / MultiSub not descended), and an
arena-scale census (179 materials mixing plain / VRayMtl / blend
/ override / port-default / no-scalar) whose invariant is
"authored count equals expected non-default count under the
per-material contract".

```
$ hython src/Tests/Integration/test_miris_max_mtlx_012.py -v
Ran 33 tests in 0.033s — OK
```

**MaxScript regression.** Deferred to the Windows build host —
the on-box test that exercises `USDExporter.ExportFile` against
a scene with a PhysicalMaterial (`.bump_map_amt = 0.35`), a
VRayMtl (`.bump_multiplier = 2.0`), and a VRayBlendMtl (base
= 0.5, coat = 5.0) is a follow-on operational bite (would live
at `src/Tests/Integration/export_mtlx_bump_strength_test.ms`).
The Python validator locks in the writer-level contract at the
MaterialX-doc layer.

**Visual demonstration.** Structural-only — this fix does not
change what Karma renders on the current Mac test environment
because reproducing the visual delta requires either (a) the
Windows-box rebuild + real Max scene with a bump map & authored
strength, or (b) hand-authoring a MaterialX-doc-pair with the
exact `scale` values and rendering with husk, which is a test
of husk's ND_normalmap evaluator not of the export path. See
`structural_only.md` in the arch-build artifact directory for
the auditor's rubric.

**Retirement condition.** If a future MaterialX version renames
`scale` on `ND_normalmap_float` (e.g. reverts to the 1.36
`strength` spelling), the fix's input-name string must change
to match AND `TestNormalmapMaterialXPortDefault` fails
immediately to force the update.

---

## Changelog

* 2026-06-20 — MAX-MAT-003 landed (PR Autodesk#38). Fixed
  displayColor leaking the 3ds Max viewport wireframe color when
  a material is bound; derives displayColor from
  `boundMtl->GetDiffuse()`.
* 2026-06-21 — MAX-GEO-001 landed (PR fork#2). Added
  `NormalsMode::Both = 3` so normals are authored as BOTH the
  UsdGeomMesh schema attribute AND the `primvars:normals`
  primvar.
* 2026-06-21 — MAX-GEO-004 landed (PR fork#3). Added
  `EnsureFallbackStPrimvar` so parametric primitives without a
  channel-1 UV get a planar-projection `primvars:st` fallback.
* 2026-06-21 — MAX-GEO-002 landed (PR fork#4). Generalized the
  single-matId early-out in `ApplyMaxMaterialIDs` to also
  short-circuit when the bound material is null or non-MultiMtl.
* 2026-06-21 — MAX-CAM-001 landed (PR fork#6). Removed the
  `GetManualClip() != 0` gate around
  `CameraWriter::Write`'s clippingRange author.
* 2026-08-11 — MAX-MTLX-001 landed (Miris fork PR, draft).
  Enrich MtlxDoc with the Bitmap map slots
  `MtlxIOUtil.ExportMtlxString` dropped.
* 2026-08-11 — MAX-LIT-002 landed (Miris fork PR, draft). Added
  `MaxUsdVRayLightWriter` for V-Ray light classes.
* 2026-08-11 — MAX-MAT-003 re-landed on v0.16.2 base (Miris
  fork branch `miris/max-mat-003-port-to-v0162`, PR opened
  manually).
* 2026-08-11 — MAX-GEO-006 landed (Miris fork branch
  `miris/max-geo-006-meshes-with-no-material-binding`, PR
  opened manually).
* 2026-08-11 — MAX-TEX-003 landed (Miris fork branch
  `miris/max-tex-003-mixed-case-texture-paths`, PR opened
  manually).
* 2026-08-12 — MAX-MTLX-007 landed (Miris fork branch
  `miris/max-mtlx-007-walk-blend-mtl-sub-materials`, PR
  opened manually).
* 2026-08-12 — MAX-MTLX-008 audit landed (Miris fork branch
  `miris/max-mtlx-008-vray-override-basemtl-audit`, PR
  opened manually).
* 2026-08-12 — MAX-MTLX-012 landed (Miris fork branch
  `miris/max-mtlx-012-bump-strength-on-normalmap`, PR opened
  manually).
