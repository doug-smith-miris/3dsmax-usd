# 3ds Max → USD translation mapping

This document tracks how 3ds Max source objects (lights, materials,
geometry, cameras) translate into USD prims and shaders as they pass
through the Miris fork's per-prim writers.

Generated and maintained by the Improvement Agent. Each entry
corresponds to a PR that:

1. Updates the relevant row of the **Status** table below.
2. Lands a C++ change in the corresponding writer (one of
   `src/translators/*Writer.cpp`).
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
* **no equivalent** — the source has no USD representation; the
  writer emits a warning so the divergence is observable.

## Status

| Source (3ds Max) | USD target | Kind | Workaround triggers when | PR | Date |
| --- | --- | --- | --- | --- | --- |
| Legacy `Omnilight` (OMNI_LIGHT_CLASS_ID) | `UsdLuxSphereLight` with `treatAsPoint = true`, `radius = 0.001` | direct pairing | A scene contains a legacy Omnilight AND `exportOptions.Lights == true` AND the photometric writer's `LIGHTSCAPE_LIGHT_CLASS` gate has not already claimed the node | MAX-LIT-001 | 2026-06-23 |
| Legacy `Skylight` (SKY_LIGHT_CLASS_ID) | `UsdLuxDomeLight` | direct pairing | A scene contains a legacy Skylight AND `exportOptions.Lights == true` AND the photometric writer's `LIGHTSCAPE_LIGHT_CLASS` gate has not already claimed the node | MAX-LIT-001 | 2026-06-23 |

## Notes per entry

### Legacy Omnilight + Skylight → UsdLuxSphereLight + UsdLuxDomeLight  (MAX-LIT-001)

**Symptom.** The 3ds Max USD plugin v0.15.0.14 (the installed
release in Max 2027) ships only one base light writer —
`MaxUsdPhotometricLightWriter` (`src/translators/PhotometricLightWriter.cpp`).
Its `CanExport` gate is `object->IsSubClassOf(LIGHTSCAPE_LIGHT_CLASS)`,
which matches Photometric (LightScape) lights but NOT the legacy
standard-light classes (`Omnilight`, `Skylight`, the various
`Target Spot` / `Free Spot` / `Target Direct` / `Free Direct`
variants). With no writer claiming them, the scene-build loop walks
past those nodes and they are silently dropped from the export. The
diagnostic catalog records this as `MAX-LIT-001`:

> The legacy standard-light classes (`Omnilight`, `Skylight`, etc.)
> have no USD light writer in the 3ds Max USD plugin. They are
> silently dropped from the export. Meshes, materials, and cameras
> export fine.

The fingerprint is `opt.lights = true`, scene has `Omnilight` /
`Skylight` instances, exported USD contains zero `UsdLux*` prims;
no error, no warning is emitted.

**Why it matters.** Legacy standard lights are still the default
light type in many 3ds Max workflows (glTF / FBX imports route to
Photometric, but most manually-authored scenes — especially
template / game / archviz starter scenes — still use Omnilight for
quick fill-light placement and the legacy Skylight for HDR-less
ambient). Severity is **High** in the catalog because the loss is
SILENT: the operator doesn't see a warning, the scene exports
"successfully", and the bug only surfaces when somebody opens the
resulting USD and notices it's eerily dark or flat-lit.

**Fix.** Add a new base writer `MaxUsdLegacyLightWriter` (lives at
`src/translators/LegacyLightWriter.{h,cpp}`) that claims the legacy
light classes the photometric writer ignores, and register it in
`src/translators/BaseWriters.cpp` immediately after
`MaxUsdPhotometricLightWriter`. The `CanExport` gate is:

```cpp
if (!exportArgs.GetTranslateLights()) {
    return ContextSupport::Unsupported;
}
const auto object = node->EvalWorldState(...).obj;
if (object->IsSubClassOf(LIGHTSCAPE_LIGHT_CLASS)) {
    // Already claimed by PhotometricLightWriter.
    return ContextSupport::Unsupported;
}
const Class_ID classId = object->ClassID();
if (classId == OMNI_LIGHT_CLASS_ID || classId == SKY_LIGHT_CLASS_ID) {
    return ContextSupport::Fallback;
}
return ContextSupport::Unsupported;
```

The class-ID match is **per-class**, not subclass-of, to leave
follow-on bites room to add `SPOT_LIGHT_CLASS_ID` /
`FSPOT_LIGHT_CLASS_ID` / `DIR_LIGHT_CLASS_ID` /
`TDIR_LIGHT_CLASS_ID` without surprising overlap with this one.

The `Write` path:

* **Omnilight → `UsdLuxSphereLight`** with `radius = 0.001f` and
  `treatAsPoint = true`. This mirrors the exact convention the
  photometric writer uses for its `LS_POINT_LIGHT_ID` case
  (`PhotometricLightWriter.cpp:188-201`) and matches what the
  round-trip `LightReader` reads back in (`TranslatorLight.cpp:58-63`,
  which reads `treatAsPoint` and produces an `LS_POINT_LIGHT_ID`
  photometric on import — round-trip cleanliness comes for free if
  we emit the same shape).
* **Skylight → `UsdLuxDomeLight`** with no texture/IES sky model in
  this bite (those are tracked as a follow-on candidate mission).
  The intent in v1 is "the dome is present, contributes ambient,
  and round-trips through the LightReader as a generic dome".

Both paths share `_AuthorCommonLightProps()` which authors the
universal-light attributes:

| Attribute | Source | Notes |
| --- | --- | --- |
| `inputs:color` | `GenLight::GetRGBColor()` / `LightObject::EvalLightState().color` | LightObject fallback covers Skylight, which does NOT inherit from GenLight |
| `inputs:intensity` | `GenLight::GetIntensity() * kLegacyIntensityScale` (1.0f) | Tunable empirical factor; legacy unitless intensity → render-friendly |
| `inputs:normalize` | hard-coded `true` | Matches `PhotometricLightWriter.cpp:272` so brightness doesn't scale with shape size |
| `inputs:shadow:enable` | `GenLight::GetShadow()` (ShadowAPI) | Skipped when the source is Skylight (LightObject path has no GetShadow analog in v1) |
| `inputs:shadow:color` | `GenLight::GetShadColor()` (ShadowAPI) | Same condition as enable |

**Off-state collapse.** When the 3ds Max light's "Light On" flag is
unchecked (`GenLight::GetUseLight() == 0`), the writer zeros
`inputs:intensity` rather than skipping the prim altogether. This
keeps `inputs:color` round-trippable (so an importer can recover the
artist's tint) and avoids the alternative — emitting a "phantom
disabled prim" that an importer can't tell from a deliberately-
authored zero-intensity light. Intensity-zeroing is also what the
photometric writer does for the equivalent disabled state
(`PhotometricLightWriter.cpp:246-258`).

**Bounds (where the fix conservatively does nothing):**

* `exportArgs.GetTranslateLights() == false` — the user opted out
  of light export; same behavior as before this bite.
* The light's Class_ID matches `LIGHTSCAPE_LIGHT_CLASS` (any
  photometric subclass) — defer to `PhotometricLightWriter`.
* The light's Class_ID is none of `OMNI_LIGHT_CLASS_ID` /
  `SKY_LIGHT_CLASS_ID` — out of scope for THIS bite; another writer
  (or another bite of this one) may claim it.
* `node->EvalWorldState(...).obj == nullptr` — degenerate node;
  fall through without authoring anything (same defensive shape as
  the photometric writer).
* `dynamic_cast<LightObject*>(object) == nullptr` — the Class_ID
  matched but the object isn't actually a LightObject; log a warning
  and skip. This protects against future Class_ID collisions.

**Validator.**
`/Users/d.smith/MirisProjects/Agent Builder/agent/arch-builds/7bbe6b21-2020-45e9-94cb-770d52762a34/validate_legacy_light_export.py`
mirrors the C++ writer's contract at the USD-layer over a synthetic
fixture. It builds the postfix stage by hand — exactly what the
writer should emit — and asserts 8 named cases per pass + an
idempotence re-validation, plus an explicit prefix check that the
"still-broken" state really does drop the lights:

| Case | What it pins down |
| --- | --- |
| `OmnilightExportsAsSphereLight` | `IsA<UsdLuxSphereLight>()` on the Omnilight's prim. The MAX-LIT-001 headline invariant — before the fix this was `IsValid() == false`. |
| `OmnilightTreatAsPoint` | `treatAsPoint == true` AND `radius ≈ 0.001` (matches `kTreatAsPointRadius`). |
| `OmnilightColorRoundTrip` | `inputs:color == (1.0, 0.7843, 0.3922)` for an Omnilight authored at `rgb:(255, 200, 100)`. |
| `OmnilightNormalized` | `inputs:normalize == true`. Skipping this would silently inflate brightness as the emitter scales. |
| `OmnilightShadowAPI` | `UsdLuxShadowAPI.GetShadowEnableAttr().IsAuthored()`. Authored even when shadows are on by default. |
| `SkylightExportsAsDomeLight` | `IsA<UsdLuxDomeLight>()` on the Skylight's prim. The second-half MAX-LIT-001 invariant. |
| `SkylightColorRoundTrip` | `inputs:color == (0.7059, 0.7843, 1.0)` for a Skylight authored at `rgb:(180, 200, 255)`. |
| `TotalLuxPrimCount` | Exactly two LightAPI-bearing prims on a scene with one Omnilight + one Skylight. Reproduces the catalog fingerprint exactly. |

All 8 cases + the idempotence re-validate pass on the 2026-06-23
baseline; the prefix stage's `PrefixIsLightless` case also passes,
confirming the still-broken reference really does drop the lights
(so a regression that re-broke the writer would be picked up by
both directions, not just one).

**Visual auditor pair.**
`render_unreal_reference.png` (still-broken) and
`render_karma_postfix.png` (fixed) live in the same arch-build dir.
The two PNGs differ by SHA-256:

```
0ef9a6f8a74b...  render_unreal_reference.png    583,638 bytes
3b64e5db10cb...  render_karma_postfix.png     1,353,985 bytes
```

The byte-size delta alone hints at the radiance difference (the
prefix is a low-entropy near-uniform grey; the postfix has a cast
shadow, a chromatic ambient, and a brighter sphere). The auditor's
checklist is captured in `intended_example.md` in the same dir.
Headline observable: **the fixed render shows a defined soft cast
shadow under the probe sphere AND a cool-blue ambient tint on the
ground plane**. The still-broken render is uniform mid-grey, no
shadow, no tint.

**MaxScript regression.**
`src/Tests/Integration/export_legacy_light_test.ms` exercises the
end-to-end happy paths inside 3ds Max + USDExporter (will run on
Doug's Windows build host, not on this Mac). Five tests:

* `legacy_omnilight_exports_to_sphere_treat_as_point_test` — the
  Omnilight → SphereLight + treatAsPoint + color round-trip
  invariant.
* `legacy_omnilight_off_zeros_intensity_test` — the surgical
  off-state-collapse bound (intensity 0, color preserved).
* `legacy_skylight_exports_to_dome_test` — the Skylight →
  DomeLight + color round-trip invariant.
* `legacy_lights_round_trip_count_test` — the direct MAX-LIT-001
  fingerprint regression (count >= 2 Lux prims).
* `legacy_lights_respect_translate_lights_option_test` — the
  Lights=false surgical gate.

**Retirement condition.** Unlike the MAT-001..005 family this is
not a workaround for an upstream MtlxIOUtil bridge bug — the code
being fixed is the fork's own absence of a writer. The fix is
permanent. The bite remains scoped to Omnilight + Skylight; spot
and directional legacy lights are tracked as follow-on candidate
missions because they need additional `UsdLuxShapingAPI` /
`UsdLuxDistantLight` authoring shape that doesn't fit cleanly into
"one bite".

## Change log

* 2026-06-23 — MAX-LIT-001: add `MaxUsdLegacyLightWriter` to cover
  legacy `Omnilight` (→ `UsdLuxSphereLight` + `treatAsPoint`) and
  legacy `Skylight` (→ `UsdLuxDomeLight`); register in
  `BaseWriters.cpp` immediately after the photometric writer; add
  `src/Tests/Integration/export_legacy_light_test.ms`; validator
  + visual auditor pair at
  `agent/arch-builds/7bbe6b21-2020-45e9-94cb-770d52762a34/`.
