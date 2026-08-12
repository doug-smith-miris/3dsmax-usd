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
