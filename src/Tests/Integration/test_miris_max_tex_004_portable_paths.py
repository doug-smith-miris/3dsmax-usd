# Copyright 2026 Miris Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
"""
MAX-TEX-004 -- every authored texture path must survive leaving the export machine.

This is a CONTRACT test, not a mirror of the C++ logic. It asserts the property the export must
have, so it holds whatever the implementation does and catches a regression from any direction:
a new writer that forgets the helper, a helper that silently bails, or a downstream tool that
re-absolutises a path.

The defect it locks down: MAX-TEX-003 routes every bitmap through
`FileResolutionManager.getFullFilePath`, which returns where the file actually IS on the exporting
machine. On an arch-viz workstation that is a mapped drive, so the MaterialX `file` inputs came out
as `Y:\\denver\\Visualization\\...\\foo.tx`. Resolution had SUCCEEDED -- the path was simply valid on
exactly one computer. A measured arena carried 135 such references.

Why it went unnoticed for so long: a downstream repair pass authored corrected paths as OVERRIDES
in the small ROOT layer, so the COMPOSED stage resolved every texture and every composed-stage
audit passed. The unportable strings stayed in the crate underneath, invisible unless you read
that layer alone. So this test checks BOTH:

  * per-layer  -- what each layer authors on its own, which is what a consumer reading a sublayer
                  or an extracted crate actually gets
  * composed   -- what a renderer opening the package sees

    usage: python test_miris_max_tex_004_portable_paths.py <root-layer.usd[a|c]>
"""
import os
import re
import sys

from pxr import Sdf, Usd, UsdShade

# A drive letter, a UNC share, or a POSIX absolute path: all machine-specific.
_ABSOLUTE = re.compile(r'^(?:[A-Za-z]:[\\/]|\\\\|/)')


def _authored_asset_paths(layer):
    """Every asset path a single layer authors, with the prim path that holds it."""
    out = []

    def walk(path):
        spec = layer.GetPrimAtPath(path)
        if spec is None:
            return
        for name, attr in spec.attributes.items():
            value = attr.default
            if isinstance(value, Sdf.AssetPath) and value.path:
                out.append((path.pathString, name, value.path))
        for child in spec.nameChildren:
            walk(path.AppendChild(child.name))

    for child in layer.pseudoRoot.nameChildren:
        walk(Sdf.Path.absoluteRootPath.AppendChild(child.name))
    return out


def _layers_of(stage):
    seen, ordered = set(), []
    for layer in stage.GetUsedLayers():
        if layer.identifier in seen or layer.anonymous:
            continue
        seen.add(layer.identifier)
        ordered.append(layer)
    return ordered


def check(root_layer_path):
    stage = Usd.Stage.Open(root_layer_path)
    assert stage, f'could not open {root_layer_path}'

    absolute, unresolved_per_layer = [], []
    for layer in _layers_of(stage):
        layer_dir = os.path.dirname(layer.realPath or layer.identifier)
        for prim_path, attr, raw in _authored_asset_paths(layer):
            if _ABSOLUTE.match(raw):
                absolute.append((os.path.basename(layer.identifier), prim_path, attr, raw))
                continue
            # Relative: it must resolve against the layer that authored it. This is the trap that
            # three separate tools here have hit -- `./textures/x.png` means something different
            # in `stage/` than it does in the package root.
            candidate = os.path.normpath(os.path.join(layer_dir, raw))
            if not os.path.exists(candidate):
                unresolved_per_layer.append(
                    (os.path.basename(layer.identifier), prim_path, attr, raw))

    # And the composed view a renderer gets.
    unresolved_composed = []
    for prim in stage.Traverse():
        shader = UsdShade.Shader(prim)
        if not shader:
            continue
        for shader_input in shader.GetInputs():
            value = shader_input.Get()
            if not isinstance(value, Sdf.AssetPath) or not value.path:
                continue
            resolved = value.resolvedPath
            if not resolved or not os.path.exists(resolved):
                unresolved_composed.append(
                    (prim.GetPath().pathString, shader_input.GetBaseName(), value.path))

    print(f'{root_layer_path}')
    print(f'  layers inspected            {len(_layers_of(stage))}')
    print(f'  ABSOLUTE authored paths     {len(absolute)}')
    print(f'  unresolved per-layer        {len(unresolved_per_layer)}')
    print(f'  unresolved composed         {len(unresolved_composed)}')
    for label, rows in (('ABSOLUTE', absolute),
                        ('UNRESOLVED PER-LAYER', unresolved_per_layer),
                        ('UNRESOLVED COMPOSED', unresolved_composed)):
        for row in rows[:8]:
            print(f'    [{label}] {row}')
        if len(rows) > 8:
            print(f'    [{label}] ... and {len(rows) - 8} more')

    assert not absolute, (
        f'{len(absolute)} authored texture path(s) are machine-specific; MAX-TEX-004 requires '
        f'layer-relative paths so the asset travels')
    assert not unresolved_per_layer, (
        f'{len(unresolved_per_layer)} relative path(s) do not resolve from the layer that authors '
        f'them -- an asset path resolves per LAYER, not per package')
    assert not unresolved_composed, (
        f'{len(unresolved_composed)} texture(s) do not resolve in the composed stage')
    print('  PASS')


if __name__ == '__main__':
    if len(sys.argv) != 2:
        print(__doc__)
        raise SystemExit(2)
    check(sys.argv[1])
