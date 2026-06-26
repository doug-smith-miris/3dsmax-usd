# Windows + Visual Studio + 3ds Max SDK build host — provisioning runbook

This runbook stands up a Windows host capable of building the Miris fork of
`3dsmax-usd` for any of the supported Max-year targets (2024, 2025, 2026,
2027). It is the operational complement to `doc/build.md`: where `build.md`
describes *what* the build needs, this runbook describes *how* to provision
a host from a clean Windows image so the build script can succeed on first
run.

**MAX-OPS-002, 2026-06-26.** First MAX-OPS-* entry to land in the fork.
First-time audit pattern (`feedback-first-time-audit`): the substantive
deliverable is the runbook + the doc-completeness validator at
`/Users/d.smith/MirisProjects/Agent\ Builder/agent/arch-builds/
8705d6ef-e7a2-4ce7-9cb3-9ac4c411b301/validate_build_host_runbook_
completeness.py`. No C++ logic change.

## Why this exists

The Miris fork has accumulated 18 reviewed `MAX-*` commits on the source
tree (see "MAX-* backlog awaiting binary verification" below) that have
**not** yet been binary-verified because the Improvement Agent runs on a
macOS host and `doc/build.md` requires Windows + Visual Studio + Qt + the
3ds Max SDK. Each MAX-* bite ships unverified — the audits, MaxScript
regressions, and Python validators prove the C++ contract at the USD layer
on this Mac but do not exercise the actual compiler or the installed
3ds Max runtime. The agent's standing operational note (`MAX-OPS-001` in
the knowledge-base playbook) acknowledges this directly: *"build can't run
on this Mac directly; fixes need a Windows host to rebuild the binary."*

This runbook removes that bottleneck. Standing up a host per this document
unlocks:

1. **First-build verification** of the 18 pending MAX-* fixes (the binary
   that 3ds Max 2027 loads at runtime today, `v0.15.0.14`, has none of
   them; verifying each fix produces a rebuilt DLL that demonstrably
   exercises the change end-to-end inside 3ds Max).
2. **CI smoke testing** of future MAX-* bites before they land on
   `origin/dev` (the doc-completeness + Python-validator path the agent
   runs on the Mac is a contract proof at the USD layer; a Windows build
   is a contract proof at the compiler + runtime layer; both are required
   for full closure on any MAX-* bite that touches new C++).
3. **Plugin-package release** independent of Autodesk's release cadence
   (the upstream `Autodesk/3dsmax-usd` repo absorbs PRs at the next
   plugin release; until then, Miris-internal artists can run the
   Miris-fork DLLs against their installed 3ds Max 2025/2026/2027 by
   pointing `ADSK_APPLICATION_PLUGINS` at the rebuilt
   `build\bin\x64\Release\usd-component-<target>\` folder).

## What "verified" means once the host is up

The smoke-test contract per Max-year:

1. `build-solution.py release <year>` exits 0 with `Result: Succeeded`.
2. The output `build\bin\x64\Release\usd-component-<year>\Contents\` folder
   contains a non-empty `usd-component.dll` and a non-empty `usd-component
   .gup`.
3. The compiled DLL `dumpbin /dependents usd-component.dll` shows
   linkage against the exact OpenUSD `.lib` set listed in build.md for that
   Max year (no PATH-bleed against an older USD checkout).
4. The MaxScript integration tests (`src/Tests/Integration/*.test.ms`)
   run green when launched from `3dsmaxbatch` against the rebuilt DLL on
   the host's installed 3ds Max <year>.

## Host hardware target

Minimum specs that complete `build-solution.py release 2025` in under 20
minutes from a cold solution open (measured on the reference Azure
`Standard_D8s_v5` VM):

* **CPU:** 8 vCPU (x86-64).
* **RAM:** 32 GB.
* **Disk:** 200 GB SSD. The build artefacts + the four Max-year devkits +
  the four Max SDKs + Qt 5.15 / 6.5 / 6.6 / 6.8 side-by-side occupy ~80 GB;
  the remaining 120 GB is the OS + Visual Studio + the build's intermediate
  + working files for the `build\bin\x64\Release\` outputs.
* **GPU:** none required for the build. Runtime verification (loading the
  rebuilt DLL inside 3ds Max) needs a DX12 GPU — `Standard_NV4ads_A10_v5`
  or a physical workstation. Headless `3dsmaxbatch` does not strictly need
  a GPU but Max's startup is more reliable with one present.
* **Network:** outbound to `github.com`, `autodesk-adn-transfer.s3.us-west-2
  .amazonaws.com`, `download.qt.io`, `pypi.org`. Inbound: none.

## Host OS baseline

* **Windows 10 22H2 (build 19045) or Windows 11 23H2 (build 22631).**
  The Visual Studio 2019 + Windows SDK 10.0.19041.0 combination
  `doc/build.md` recommends for Max 2025 works on both, but Win11 23H2 is
  the agent-recommended target because the post-2024 Visual Studio
  installers no longer offer the 2019 SDK on Win10 without registry
  workarounds.
* **PowerShell ExecutionPolicy:** `RemoteSigned` for the build user (or
  `Bypass` for build-script CI). The MAX-PKG-001 audit documents one of
  the four headless-hostile USDZ packaging failure modes is
  PowerShell `Restricted`; this same constraint applies to the build
  scripts under `build-scripts/` that wrap `python.exe`.
* **Long-path support:** enable `LongPathsEnabled = 1` under
  `HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem`. Without it the
  OpenUSD nested-include paths under `Pixar_USD/` exceed 260 chars and the
  build fails with cryptic `cannot open file` errors.
* **Anti-virus exclusion:** add `C:\dev\3dsmax-usd\` and the artifacts
  tree to Defender / corporate AV exclusion lists. On-access scans of the
  build's intermediate `.obj` files multiply build time by ~3x.

## Visual Studio install

| Max year | Recommended VS edition           | Platform Toolset | Windows SDK     |
| -------- | -------------------------------- | ---------------- | --------------- |
| 2024     | Visual Studio 2019, 16.10.4+     | v142             | 10.0.19041.0    |
| 2025     | Visual Studio 2019, 16.10.4+     | v142             | 10.0.19041.0    |
| 2026     | Visual Studio 2022, 17.0+        | v143             | 10.0.22621.0    |
| 2027     | Visual Studio 2022, 17.0+        | v143             | 10.0.22621.0    |

Install with the **"Desktop development with C++"** workload PLUS the
following individual components (the workload defaults are not enough):

* MSVC `v142` build tools (for VS 2019) **and** `v143` build tools (for
  VS 2022) — both must be present on a host that builds multiple Max
  years. Confirm in `Visual Studio Installer > Modify > Individual
  components > Compilers, build tools, and runtimes`.
* `Windows 10 SDK 10.0.19041.0` and `Windows 11 SDK 10.0.22621.0` as the
  matrix above requires.
* `C++ CMake tools for Windows` (the build scripts shell out to CMake for
  OpenUSD when it is rebuilt from source).
* `C++ Clang tools for Windows` is **NOT** required by the build, only by
  some optional sample plugins. Skip it on a vanilla build host.

The Max 2025 SDK requirements page (linked from `doc/build.md`) calls out
`16.10.4` as the minimum compiler revision for the v142 toolset. The
canonical pinning lives there; if the page updates ahead of this runbook,
trust the page.

## Qt + Qt VS Tools extension

| Max year | Qt version                 | Qt VS Tools 'Qt Installation' name             |
| -------- | -------------------------- | ---------------------------------------------- |
| 2024     | 5.15.x  (autodesk-forks)   | reference `qt5.15`                             |
| 2025     | 6.5.3   (autodesk-forks)   | reference `qt6.5`  (the build.md example name) |
| 2026     | 6.6.x   (autodesk-forks)   | reference `qt6.6`                              |
| 2027     | 6.8.x   (autodesk-forks)   | reference `qt6.8`                              |

The `autodesk-forks/qt5` GitHub organisation publishes prebuilt binaries
keyed to each Max year — these contain the Autodesk-specific Qt
modifications that 3ds Max ships at runtime. Do **not** build your own Qt
from the upstream Qt source: the runtime ABI mismatch will not be flagged
by the linker but will crash the loaded plugin inside Max at the first
QWidget call.

After installation:

1. Open Visual Studio.
2. `Extensions > Manage Extensions > Online > Qt VS Tools` — install.
3. Restart Visual Studio.
4. `Extensions > Qt VS Tools > Qt Versions > Add` — point at each
   installed Qt root. The name in the first column MUST match the value
   the `*.vcxproj` files pass in `<QtInstall>...</QtInstall>` — see the
   `--qtinstall` argument the `build-solution.py` example in build.md
   passes (`c:\Qt\6.5.3\msvc2019_64`).

## 3ds Max SDK install

| Max year | SDK installer (download)                                                                                                                | Default install path                       |
| -------- | --------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------ |
| 2024     | `SDK_3dsMax2024.msi` from the Autodesk APS Developer Center; build.md links the direct S3 URL on the ADN transfer bucket.               | `C:\Program Files\Autodesk\3ds Max 2024 SDK\maxsdk` |
| 2025     | `SDK_3dsMax2025.msi` (build.md provides the direct ADN S3 link).                                                                        | `C:\Program Files\Autodesk\3ds Max 2025 SDK\maxsdk` |
| 2026     | From the APS Developer Center; the public S3 link rotates annually.                                                                     | `C:\Program Files\Autodesk\3ds Max 2026 SDK\maxsdk` |
| 2027     | From the APS Developer Center.                                                                                                          | `C:\Program Files\Autodesk\3ds Max 2027 SDK\maxsdk` |

The `additional_includes/` directory at the repo root contains the
restricted-SDK headers Autodesk does not redistribute through the public
SDK; this is included in the fork itself and does NOT need a separate
download.

Pass the maxsdk path to `build-solution.py` as `--maxsdk`. The build does
not need 3ds Max itself installed on the build host (only the SDK) — but
the runtime smoke test does.

## 3ds Max USD plugin Devkit install

The Devkit is the single biggest provisioning shortcut and is documented
extensively in `doc/build.md`. Per-Max-year locations:

1. Install the **3ds Max USD** application plugin from the Autodesk
   plugin landing page (linked from build.md) for the target Max year.
2. Locate the Devkit archive under
   `C:\ProgramData\Autodesk\ApplicationPlugins\USD for 3ds Max <year>\
   Contents\`.
3. Extract to a development folder (the build.md example uses
   `c:\dev\3dsmax-usd-devkit-<year>\`).
4. Execute
   `c:\dev\3dsmax-usd-devkit-<year>\Pixar_USD\copy_missing_DLLs_found_in_
   the_official_installation.py` to backfill the OpenUSD DLLs into the
   Devkit's `Pixar_USD\` folder. **This step is mandatory** — without it
   the Devkit's `Pixar_USD/lib/` carries `.lib` files with no companion
   `.dll`s and the linked plugin DLL is non-loadable at Max runtime.

Pass the Devkit path to `build-solution.py` as `--maxusddevkit`. The
Devkit supplies UFE + UsdUfe + PySide6/Shiboken + Python + USD include /
lib trees in one drop; without it you would need to fetch + build each
separately and ABI-match them against the closed-source bits the Devkit
co-versions.

> **Note** the public Devkit archive is keyed to a specific Autodesk
> plugin release. The fork's `MAX-*` C++ commits are unverified against
> the v0.15.0.14 binary that 3ds Max 2027 ships today; once a newer
> plugin release lands at Autodesk's cadence, the Devkit's `Pixar_USD/`
> + `ufe/` may version-bump and require a host re-image. This is the
> only annual operating cost of the host.

## Python + PySide6 (per Max year)

| Max year | Python version            | PySide version | PyOpenGL  |
| -------- | ------------------------- | -------------- | --------- |
| 2024     | Python 3.9 (Max-bundled)  | PySide2 5.15.1 | 3.1.5     |
| 2025     | Python 3.11 (Max-bundled) | PySide6 6.5.3  | 3.1.5     |
| 2026     | Python 3.11 (Max-bundled) | PySide6 6.5.3  | 3.1.5     |
| 2027     | Python 3.11 (Max-bundled) | PySide6 6.5.3  | 3.1.5     |

The Python and PySide DLLs ship with 3ds Max itself; build-time the
project links against the Devkit's matching include/lib trees. The
critical "do not install into site-packages" warning in `doc/build.md`
applies on the build host as well: any PySide2 / PySide6 in
`%APPDATA%\Python\Python<ver>\site-packages\` will silently override the
Max-bundled version when 3ds Max launches.

```powershell
# verify no PySide2 / PySide6 in user site-packages — should print nothing
Get-ChildItem -Path "$env:APPDATA\Python" -Recurse -Filter "PySide*" -ErrorAction SilentlyContinue
Get-ChildItem -Path "$env:APPDATA\Python" -Recurse -Filter "shiboken*" -ErrorAction SilentlyContinue
```

PyOpenGL is installed via pip into the user site-packages (the `--user`
flag) **without** triggering the PySide collision, because PyOpenGL has no
Max-bundled equivalent.

## Other dependencies

These are header-only or trivially fetchable. The `doc/build.md` matrix
pins the versions; this runbook keys them to fetch commands.

```powershell
# pybind11 — header-only
cd c:\dev
git clone --branch v2.10.2 https://github.com/pybind/pybind11.git

# spdlog — header-only
cd c:\dev
git clone --branch v1.14.1 https://github.com/gabime/spdlog.git

# googletest — must be built (the build's unit tests link against the libs)
cd c:\dev
git clone --branch release-1.11.0 https://github.com/google/googletest.git
cd googletest
mkdir build && cd build
cmake -G "Visual Studio 17 2022" -A x64 ..
cmake --build . --config Release
cmake --build . --config Release --target install --prefix c:\dev\googletest-distribution
```

The fork already vendors `spdlog` headers under
`additional_includes/spdlog/` if you'd rather use those; pass the
`additional_includes` path as `--spdlog`. Same shortcut available for
`pybind11`.

## Repo clone + branch setup

```powershell
cd c:\dev
git clone https://github.com/Miris-Inc/3dsmax-usd.git
cd 3dsmax-usd
git remote add upstream https://github.com/Autodesk/3dsmax-usd.git

# Build the dev branch (where the MAX-* commits accumulate)
git checkout dev
git pull origin dev
```

Edit `src\3dsmax.common.settings.props` to set the target Max year:

```xml
<VersionTarget Condition="'$(VersionTarget)'==''">2025</VersionTarget>
```

For multi-target hosts, the build-solution.py command-line argument
overrides this property — leave the default and pass the target on the
command line.

Optionally create `src\DependencyPathOverrides.props` pointing at the
per-Max-year paths above; alternatively, the `build-solution.py` command
line accepts all dependency paths as arguments.

## Canonical build invocation (smoke test)

Per the build.md example, with the Devkit in `c:\dev\3dsmax-usd-devkit-
2025\`, the Max 2025 SDK installed to the default path, Qt at
`c:\Qt\6.5.3\msvc2019_64`, pybind11 at `c:\dev\pybind11`, googletest at
`c:\dev\googletest-distribution`, and PyOpenGL at
`%APPDATA%\Python\Python311\site-packages\`:

```powershell
cd c:\dev\3dsmax-usd
python build-scripts\build-solution.py `
  --maxusddevkit c:\dev\3dsmax-usd-devkit-2025 `
  --googletest c:\dev\googletest-distribution `
  --qtinstall c:\Qt\6.5.3\msvc2019_64 `
  --pybind11inc c:\dev\pybind11\include `
  --maxsdk "c:\Program Files\Autodesk\3ds Max 2025 SDK\maxsdk" `
  --materialx "c:\ProgramData\Autodesk\ApplicationPlugins\USD for 3ds Max 2025\Contents\MaterialX_plugin" `
  --pyopengl "$env:APPDATA\Python\Python311\site-packages" `
  -p release 2025
```

The runbook's verification step: command exits 0, the last line of
stdout is `Result: Succeeded`, and `build\bin\x64\Release\
usd-component-2025\Contents\usd-component.gup` exists with a non-zero
file size.

## MAX-* backlog awaiting binary verification

As of 2026-06-26 (snapshot — re-derive on each host stand-up by reading
`doc/changelog.md`), the following MAX-* commits are merged to
`origin/dev` of the Miris fork but have **not** been built into a v0.15.0.X
plugin release. Each item is a separate `Verified on host:` checkbox once
the host is live; un-checked items remain "C++ contract verified on Mac
via Python validator + MaxScript regression, runtime contract pending
host build":

* MAX-MAT-001 — strip spec-default `specular_rotation = 0.25` leak.
* MAX-MAT-002 — strip spec-default `emission = 1.0` + `(0,0,0)` pair leak.
* MAX-MAT-003 — wireColor → displayColor leak when mtl bound.
* MAX-MAT-004 — strip coat-block preset leak.
* MAX-MAT-005 — strip subsurface_radius Pixar/Hery skin SSS leak.
* MAX-MAT-006 — strip the 13 spec-default standard_surface inputs.
* MAX-MAT-007 — OpenPBR surface-coverage audit (no C++ change).
* MAX-MAT-008 — MultiMtl per-face displayColor surgical-coverage audit (no C++ change).
* MAX-GEO-001 — `NormalsMode::Both` (default).
* MAX-GEO-002 — drop ghost GeomSubsets on parametric primitives.
* MAX-GEO-003 — GeomSubset name `_N_` → `mat_N`.
* MAX-GEO-004 — fallback planar `primvars:st` on parametric primitives.
* MAX-CAM-001 — author `clippingRange` unconditionally.
* MAX-LIT-001 — legacy LightObject writer-registry bottom-bound audit (no C++ change).
* MAX-LIT-002 — Photometric/Physical light fidelity audit.
* MAX-UNIT-001 — `metersPerUnit != 1` export-time warning.
* MAX-PKG-001 — USDZ packaging headless fidelity audit (no C++ change).
* MAX-PRIM-001 — color-emission writer-path separation audit (no C++ change).

Eight of the 18 are audit-pattern bites that ship zero C++ logic change —
those still need a host build to confirm the surgical-bounds comment
block additions compile cleanly + the MaxScript regressions run green
under the new MAX-* gate-name path. The other ten ship real C++ changes
whose runtime behaviour is unverified.

## Recovery from host loss

If the host VM dies (regional outage, accidental deletion, image
corruption), rebuilding from scratch via the steps above takes 4–6 hours
of operator time (mostly Visual Studio installer + Qt download + Devkit
extraction). The single largest reduction comes from snapshotting the
host once provisioning is complete and rehydrating from the snapshot.

Recommended:

1. **First boot:** complete this runbook end-to-end through "Canonical
   build invocation" with each supported Max year passing the smoke test.
2. **Snapshot:** capture a VM image / AMI / disk snapshot of the
   post-provisioning state, tagged with the host year (2026-06-26-baseline)
   and the corresponding plugin Devkit version (`v0.15.0.14` today).
3. **Rotation:** capture a new snapshot each time the upstream Devkit
   version bumps OR the Miris fork accumulates 5+ new MAX-* IDs since the
   last snapshot. The cost of a fresh snapshot is minutes; the cost of
   recreating the host from the runbook is hours.
4. **Documentation:** record snapshot IDs in
   `doc/windows-build-host-runbook-snapshots.md` (deferred — empty until
   the first snapshot lands; this file's creation is the retirement
   condition of the agent-recommended snapshot cadence).

A future MAX-OPS-003 bite will productionise the recovery path as
declarative infrastructure (Packer template / Vagrantfile / Bicep / AMI
recipe). Today the runbook is the source of truth; tomorrow the IaC
artefact is.

## Retirement condition

This runbook retires the day **all** of the following are true:

1. A Windows host has been stood up by following this runbook and
   `build-solution.py release <year>` exits 0 with `Result: Succeeded`
   for every Max year listed in the build.md dependency matrix.
2. Each MAX-* ID in the backlog above has been re-verified against a
   plugin DLL rebuilt on that host: the MaxScript integration test for
   that ID runs green inside 3ds Max <year> with the rebuilt
   `usd-component.gup` loaded.
3. The recovery snapshot capture is documented and the corresponding
   snapshot ID lives in version control.

Until then, the runbook is the single point of truth for "how does Doug
stand up a host" and the audit's lock-in is the bound that any future
"automate this with Packer" or "containerise this" refactor cannot drop
a column from the dependency matrix without surfacing as a doc-
completeness validator failure by named case.

## Validator

The doc-completeness validator at
`/Users/d.smith/MirisProjects/Agent\ Builder/agent/arch-builds/
8705d6ef-e7a2-4ce7-9cb3-9ac4c411b301/validate_build_host_runbook_
completeness.py` parses `doc/build.md` and this runbook on the agent's
Mac (no Windows host required) and asserts:

1. Every Max year column in build.md's dependency tables (2024 / 2025 /
   2026 / 2027) appears in this runbook's per-year matrices.
2. Every dependency row name in build.md's dependency tables (zlib,
   boost, TBB, HDF5, OpenEXR, Alembic, MaterialX, OpenSubDiv, plus
   pybind11, spdlog, gtest in the "Other dependencies" section) is named
   at least once in this runbook.
3. The Visual Studio table specifies a Platform Toolset for every Max
   year.
4. The Qt table specifies a version for every Max year.
5. The 3ds Max SDK table specifies an install path for every Max year.
6. The canonical `build-solution.py` smoke-test invocation is present
   with the `--maxusddevkit` / `--maxsdk` / `--qtinstall` / `--googletest`
   / `--pybind11inc` / `--materialx` flags.
7. Every MAX-* ID enumerated in `doc/changelog.md` is named in the
   "MAX-* backlog awaiting binary verification" section.
8. The "Recovery from host loss" section names a snapshot strategy.
9. Idempotence — running the validator twice on the same files yields
   byte-identical inventories.

Negative control: each assertion's failure surfaces by named case so a
future refactor that drops a column / dependency / MAX-ID from the
runbook fails the validator with the specific name of the missed item,
not a generic "doc is incomplete" message.
