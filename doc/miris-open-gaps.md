# Miris open exporter gaps

Fork-local record. One section per gap that is real, diagnosed, and **not yet
fixed**, with the specific evidence that would let the next session act
without re-deriving it. Fixed gaps live in commit messages and
`src/Tests/Integration/test_miris_*`, not here.

---

## MAX-MTLX-HEIGHTNORMAL-031 — a height map is fed into `ND_normalmap`

**Status:** diagnosed, root cause located in our source, fix shape decided.
Blocked on one fact that needs 3ds Max to settle. **Do not implement the
V-Ray branch by guessing the property name.**

### Symptom

The MaterialX half — the half NVIDIA Omniverse and our own backend actually
consume — renders garbage normals. Reported by
`3dsmax-usd-suts/generic-arena/tools/bake_mtlx_channel_conversions.py`:

> The MaterialX half [...] still feeds the raw gloss and bump maps into
> `specular_roughness` and, through `ND_normalmap`, into `normal`. So every
> package we have queued for upload renders inverted roughness and garbage
> normals on the consumer that matters most.

The gloss→roughness half of that sentence is closed by
`MAX-MTLX-GLOSSINESS-GATE-029` and `MAX-MTLX-GLOSSINESS-SCALAR-030`. The
height→normal half is this gap.

### Root cause, verified in our source

1. `MtlxShaderWriter.cpp` slotMap, lines 902–905, maps Max's bump-family
   slots onto the MaterialX `normal` input **as `vector3`**:

       #("bump_map",   "normal", "vector3"),
       #("bumpMap",    "normal", "vector3"),
       #("norm_map",   "normal", "vector3"),
       #("normalMap",  "normal", "vector3"),

   A greyscale height map and an encoded tangent normal are given the same
   treatment: sampled as `vector3` and handed to a decoder.

2. `resolveMaxTexmapFilename`, lines 332–346, unwraps `Normal_Bump` to its
   `.normal_map` and **falls back to `.bump_map`**, returning the same kind
   of value either way. The wrapper slot the map came out of is the
   authoritative height-vs-normal discriminator, and this discards it. The
   exporter destroys its own evidence.

3. Verified empirically against MaterialX 1.39.3 (`hython`, `getNodeDef`):

   | nodedef | `in` type | `in` default |
   |---|---|---|
   | `ND_normalmap_float` | `vector3` | `0.5, 0.5, 1.0` |
   | `ND_heighttonormal_vector3` | `float` | `0.0` |

   `ND_normalmap_float` wants an encoded normal. `ND_heighttonormal_vector3`
   wants a height scalar and outputs `vector3` — so it **replaces**
   `ND_normalmap`, it does not feed it. (There is no `ND_normalmap`; the
   float and vector2 variants are the real nodedefs.)

### Fix shape

A pass running **after** `_ApplyBumpStrengthToNormalmaps` (MAX-MTLX-012, call
site line ~3249), so the `scale` MAX-MTLX-012 authored on the `normalmap`
node can be carried across rather than lost. No change to MAX-MTLX-012
itself.

For each shader input the probe classifies as HEIGHT, where the input's
NodeGraph output is driven by a `normalmap` node — that driver is the defect
signature; if it is anything else, skip:

    [img_<input>_height  ND_tiledimage_float]
                 |(in)
                 v
    [heighttonormal_<input>  ND_heighttonormal_vector3]   scale <- normalmap.scale
                 |
                 v
    [NG.<input>_output] -> standard_surface.<input>

Then `NodeGraph::removeNode` the orphaned `normalmap` node, and the old
`ND_tiledimage_vector3` too if nothing else in the graph references it.
`removeNode` and `setCategory` were both confirmed present in the bindings;
`removeOutput` is already used in this file at line 1914.

Classification, decided by Max data and never by pixels or filenames:

| Max-side fact | verdict |
|---|---|
| wrapped in `Normal_Bump` with `.normal_map` set | NORMAL — skip |
| class name contains `NormalMap` (`VRayNormalMap`) | NORMAL — skip |
| `Normal_Bump` with only `.bump_map` set | HEIGHT |
| plain bitmap in a `bump_map` / `bumpMap` slot (PhysicalMaterial, OpenPBR — an encoded normal there is wrapped in `Normal_Bump`, caught above) | HEIGHT |
| VRayMtl `texmap_bump`, bump-mode reads "bump" | HEIGHT |
| VRayMtl `texmap_bump`, bump-mode reads any normal flavour | NORMAL — skip |
| anything else | **skip** |

Unknown must mean skip. The risk is asymmetric: converting a real normal map
to height destroys good data, while leaving a height map in `ND_normalmap` is
the status quo. Fire only on positive proof.

### The one blocking fact

**What MAXScript property carries VRayMtl's bump/normal mapping mode, and
what value means plain bump.** Candidate spellings: `bump_type`,
`bump_map_type`, `bumpMapType`, `option_bump_type`. Searched this repo, the
SUT tree and every local `.ms`/`.py`/`.md` — no occurrence of any of them.
It cannot be settled without Max or V-Ray installed.

It matters because the Spectrum Center arena is **all VRayMtl**. The two
wrapper-provable HEIGHT rows above are safe and correct but would not fire on
this asset, so a fix shipped without this fact would be inert on the scene
that motivated it — the same way `MAX-MTLX-GLOSSINESS-INVERT-018` shipped
inert for two weeks.

Settle it in one line on any arena material:

```maxscript
m = sceneMaterials[1]
for pn in (getPropNames m) do
    if (findString (pn as string) "bump") != undefined do
        format "% = %\n" pn (getProperty m pn)
```

Then implement the table above with the confirmed spelling, and add a probe
line for it alongside the wrapper rules.

---

## Closed as NOT exporter gaps

Recorded so they are not re-litigated.

### `fix_nonspecular_ior_mtlx.py` — the IOR repair

**Not an exporter gap.** Evidence, measured on the two exterior exports:

| file | authored `specular_IOR` | authored `ior` |
|---|---|---|
| `diagnostics/generic-arena-exterior.usda.bak-preIor-1012` | 0 | 0 |
| `ext-package/generic-arena-exterior.usda` (post-repair) | 9 at `1` | 9 at `1` |

Material-name sets are identical between the two (0-line diff), so this is
the same export before and after the repair. Every IOR spelling was searched,
not just these two. The exporter authored **no IOR at all**; the repair
*added* opinions where there were none.

Grepped our source: the only IOR handling anywhere is six texmap slots →
`specular_IOR` (MAX-MTLX-009, lines 999–1004). We author no scalar IOR under
any spelling. Where the repair tool reported `mtlx.specular_IOR = 50.0` on
the interior, that value came from Autodesk's native
`MtlxIOUtil.ExportMtlxString` faithfully carrying a real V-Ray parameter —
and it survived a Maya round trip, which is what a real source value does.

The tool's own docstring concedes the repair changes nothing that renders
correctly: a `standard_surface` with `specular_color = (0,0,0)` has no
specular response, so its IOR is already inert. Teaching the exporter to
author `ior = 1.0` would mean inventing an opinion we cannot source from the
Max material, on data that changes no correct render. Leave it to the
consumer-robustness repair where it belongs.

### `fix_fan_diffuse.py` — the crowd texture rename

**Asset-specific; the generalisable half is fixed.** The exporter authored
the path the scene asked for, which is correct. Guessing that
`rp_alice_rigged_003_dif.jpg` means `RP-FAN-ALICE_003_TC01.jpg` is a guess
about someone else's naming scheme and cannot be generalised. What was ours —
authoring an unresolvable path in silence — is now `MAX-TEX-005`.

### `invert_gloss_to_roughness_mtlx.py` — the glossiness repair

**Exporter gap, fixed.** `MAX-MTLX-GLOSSINESS-GATE-029` (the texmap path was
gated off) and `MAX-MTLX-GLOSSINESS-SCALAR-030` (the scalar was never
handled). Note the exporter fix wires an `ND_invert_float` at graph time,
where the repair baked `*__as_roughness.png` files — no derived textures, no
resampling, and the package stays portable.

---

## Latent, low priority

`std::stof` at `MtlxShaderWriter.cpp:1235` and `:2146` honours `LC_NUMERIC`.
3ds Max initialises the C locale from the OS on some releases, so on a
comma-decimal workstation these would misparse a MAXScript-formatted number.
Predates this work and has not been observed. `MAX-MTLX-GLOSSINESS-SCALAR-030`
avoided adding a third instance by formatting from integers
(`_FormatUnitFloat`); the same treatment would close these two.
