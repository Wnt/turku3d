"""Selective first/full cold, repeat and texture-reuse measurements on real OCI tiles."""
import argparse
import importlib
import importlib.util
import json
import os
import shutil
import struct
import time
from pathlib import Path

from turku3d import config as C
from turku3d.coarse.inputs import gather
from turku3d.coarse.sources import Sources


def facade_atlases(file):
    data = file.read_bytes()
    size = struct.unpack_from('<I', data, 12)[0]
    document = json.loads(data[20:20+size])
    binary = data[28+size:]
    result = {}
    for image in document.get('images', []):
        if image.get('name', '').startswith('facade_atlas_'):
            view = document['bufferViews'][image['bufferView']]
            offset = view.get('byteOffset', 0)
            result[image['name']] = binary[offset:offset+view['byteLength']]
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--assets', type=Path, required=True)
    parser.add_argument('--tiles', nargs='+', required=True)
    parser.add_argument("--assembly-baseline", type=Path)
    parser.add_argument("--atlas-baseline", type=Path)
    parser.add_argument('--reuse-caches', type=Path)
    parser.add_argument('--visual-baseline', type=Path)
    parser.add_argument('--audit', action='store_true')
    parser.add_argument('--passes', nargs='+', choices=['first', 'full-cold', 'full-repeat', 'repackage'],
                        default=['first', 'full-cold', 'full-repeat', 'repackage'])
    args = parser.parse_args()
    args.root.mkdir(parents=True, exist_ok=False)
    if args.reuse_caches:
        for name in [*(f'.stages/{tile}' for tile in args.tiles), '.compiled-textures']:
            source = args.reuse_caches / name
            if not source.exists():
                source = args.assets / name
            if source.exists():
                shutil.copytree(source, args.root / name)
    # Default measures fresh compilation. A forward audit-only check may retain
    # the preceding evidence and rebuild just stages whose content hashes changed.
    assets = args.reuse_caches or args.assets
    for tile in args.tiles:
        source = assets / '.inputs' / tile
        if not source.exists():
            source = args.assets / '.inputs' / tile
        if source.exists():
            shutil.copytree(source, args.root / '.inputs' / tile)
    for pattern in ('shared', '.surface-library*'):
        for source in assets.glob(pattern):
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
    assembly_comparisons = []
    if args.assembly_baseline:
        import numpy as np
        from turku3d import wall_assembly

        spec = importlib.util.spec_from_file_location('turku3d._assembly_before', args.assembly_baseline)
        previous = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(previous)
        assemble = wall_assembly.assemble_structures

        def compare(meshes, protected=()):
            started = time.monotonic()
            expected = previous.assemble_structures(meshes, protected)
            before = time.monotonic() - started
            started = time.monotonic()
            actual = assemble(meshes, protected)
            after = time.monotonic() - started
            assert len(expected) == len(actual)
            for a, b in zip(expected, actual, strict=True):
                assert a.name == b.name
                np.testing.assert_allclose(a.positions, b.positions, atol=1e-8, rtol=0)
                np.testing.assert_array_equal(a.indices, b.indices)
                for field in ('uvs', 'normals'):
                    left, right = getattr(a, field), getattr(b, field)
                    if left is None:
                        assert right is None
                    else:
                        np.testing.assert_allclose(left, right, atol=1e-8, rtol=0)
                assert a.extras == b.extras
            assembly_comparisons.append(dict(before_seconds=before, after_seconds=after,
                                             meshes=len(actual), triangles=sum(len(m.indices) for m in actual)))
            return actual

        wall_assembly.assemble_structures = compare
    sources = Sources()
    report = dict(cores=os.cpu_count(), load_start=os.getloadavg(), root=str(args.root),
                  cache_seed=str(args.reuse_caches) if args.reuse_caches else None, runs=[])
    for tile in args.tiles:
        out = args.root / tile
        for label in args.passes:
            first = label == 'first'
            if args.reuse_caches and label == 'full-cold':
                label = 'full-changed-stages'
            if label == 'repackage':
                # Only this diagnostic's package entry is removed: ordinary/atlas
                # compilation must survive an unrelated geometry-package change.
                shutil.rmtree(args.root / '.stages' / tile / 'package')
            inputs = gather(C.VisualTile.parse(tile), args.root, sources, first_pass=first)
            expected = dict(buildings=len(inputs.buildings),
                            collision=int(C.VISUAL_TILE_SIZE / C.COLLISION_TILE_SIZE) ** 2)
            started = time.monotonic()
            package_stats.clear()
            manifest = module.build(tile, out, sources=sources, inputs=inputs, first_pass=first)
            elapsed = time.monotonic() - started
            actual = dict(buildings=manifest['buildings'], collision=len(manifest['collision']))
            assert actual == expected, (actual, expected)
            assert manifest['pass_kind'] == ('first' if first else 'full')
            for entry in [*manifest['visual'].values(), *manifest['collision'].values()]:
                assert (out / entry['url']).is_file(), entry
            if not first:
                assert manifest['visual'][tile]['brotli_bytes'] <= 8_000_000
                data = (out / manifest['visual'][tile]['url']).read_bytes()
                size = struct.unpack_from('<I', data, 12)[0]
                doc = json.loads(data[20:20+size])
                for item in [*doc.get('images', []), *doc.get('buffers', [])]:
                    uri = item.get('uri')
                    if uri:
                        asset = (out / uri).resolve()
                        assert asset.is_file(), uri
                        assert asset.stat().st_size <= 8_000_000, uri
                        assert asset.stem == __import__('hashlib').sha256(asset.read_bytes()).hexdigest(), uri
            stages = json.loads((out / 'stage-times.json').read_text())
            row = dict(tile=tile, pass_kind=label, seconds=round(elapsed, 3), counts=actual,
                       source_counts=expected, stages=stages, manifest_seconds=manifest['seconds'],
                       package=package_stats.copy(), visual=manifest['visual'],
                       assembly_comparisons=assembly_comparisons.copy())
            if args.atlas_baseline and not first:
                before = facade_atlases(args.atlas_baseline / tile / 'vis.glb')
                after = facade_atlases(out / 'vis.glb')
                assert before == after, 'Facade atlas encoded bytes changed'
                row['identical_facade_atlases'] = sorted(after)
            if args.visual_baseline and not first:
                assert (args.visual_baseline / tile / 'vis.glb').read_bytes() == (out / 'vis.glb').read_bytes(), 'Shipped GLB changed'
                row['identical_visual'] = True
            report['runs'].append(row)
            (args.root / 'measurement.json').write_text(json.dumps(report, indent=2) + '\n')
            print(json.dumps(row), flush=True)
    if args.audit:
        from turku3d.coarse.audit import health
        # Drain is serialized with any already-running auditor. Poll immutable
        # results, including failures, without altering the published payloads.
        until = time.monotonic() + 180
        while health(args.root)['pending'] and time.monotonic() < until:
            time.sleep(.25)
        report['audits'] = health(args.root)
        (args.root / 'measurement.json').write_text(json.dumps(report, indent=2) + '\n')
        print(json.dumps(dict(audits=report['audits'])), flush=True)
        assert report['audits']['pending'] == 0, report['audits']
        assert report['audits']['failed'] == 0, report['audits']
        expected_passes = int('first' in args.passes) + int(any(p != 'first' for p in args.passes))
        assert report['audits']['passed'] >= len(args.tiles) * expected_passes, report['audits']
    report['load_end'] = os.getloadavg()
    (args.root / 'measurement.json').write_text(json.dumps(report, indent=2) + '\n')


if __name__ == '__main__':
    main()
