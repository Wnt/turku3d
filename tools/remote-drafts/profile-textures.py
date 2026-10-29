"""Selective first/full cold, repeat and texture-reuse measurements on real OCI tiles."""
import argparse
import importlib
import json
import os
import shutil
import time
from pathlib import Path

from turku3d import config as C
from turku3d.coarse.inputs import gather
from turku3d.coarse.sources import Sources


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--assets', type=Path, required=True)
    parser.add_argument('--tiles', nargs='+', required=True)
    args = parser.parse_args()
    args.root.mkdir(parents=True, exist_ok=False)
    # Copy prepared inputs and authored/shared assets, never generated stage/codec caches.
    for tile in args.tiles:
        source = args.assets / '.inputs' / tile
        if source.exists():
            shutil.copytree(source, args.root / '.inputs' / tile)
    for pattern in ('shared', '.surface-library*'):
        for source in args.assets.glob(pattern):
            if source.is_dir():
                shutil.copytree(source, args.root / source.name)
    module = importlib.import_module('turku3d.coarse.build')
    original = module.package_visual
    package_stats = {}

    def capture(*a, **kw):
        result = original(*a, **kw)
        package_stats.clear()
        package_stats.update(result[1])
        return result

    module.package_visual = capture
    sources = Sources()
    report = dict(cores=os.cpu_count(), load_start=os.getloadavg(), root=str(args.root), runs=[])
    for tile in args.tiles:
        out = args.root / tile
        for label, first in [('first', True), ('full-cold', False), ('full-repeat', False), ('repackage', False)]:
            if label == 'repackage':
                # Only this diagnostic's package entry is removed: ordinary/atlas
                # compilation must survive an unrelated geometry-package change.
                shutil.rmtree(args.root / '.stages' / tile / 'package')
            inputs = gather(C.VisualTile.parse(tile), args.root, sources, first_pass=first)
            expected = dict(buildings=len(inputs.buildings), collision=16)
            started = time.monotonic()
            package_stats.clear()
            manifest = module.build(tile, out, sources=sources, inputs=inputs, first_pass=first)
            elapsed = time.monotonic() - started
            actual = dict(buildings=manifest['buildings'], collision=len(manifest['collision']))
            assert actual == expected, (actual, expected)
            assert manifest['pass_kind'] == ('first' if first else 'full')
            for entry in [*manifest['visual'].values(), *manifest['collision'].values()]:
                assert (out / entry['url']).is_file(), entry
            stages = json.loads((out / 'stage-times.json').read_text())
            row = dict(tile=tile, pass_kind=label, seconds=round(elapsed, 3), counts=actual,
                       source_counts=expected, stages=stages, manifest_seconds=manifest['seconds'],
                       package=package_stats.copy(), visual=manifest['visual'])
            report['runs'].append(row)
            (args.root / 'measurement.json').write_text(json.dumps(report, indent=2) + '\n')
            print(json.dumps(row), flush=True)
    report['load_end'] = os.getloadavg()
    (args.root / 'measurement.json').write_text(json.dumps(report, indent=2) + '\n')


if __name__ == '__main__':
    main()
