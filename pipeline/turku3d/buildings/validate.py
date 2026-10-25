"""Validate actual layer NPZ triangles in Rapier, without creating shipped artifacts."""
import json
from pathlib import Path
import subprocess
import numpy as np
from turku3d import config as C
from .build import sha256, write_json


def main():
    layer = C.work_root()/'build/layers/buildings'
    manifest_hash = sha256(layer/'MANIFEST.json')
    report = json.loads((layer/'metrics.json').read_text())
    footprints = json.loads((layer/'footprints.geojson').read_text())
    facets = json.loads((layer/'facades.geojson').read_text())
    bases = {}
    for f in facets['features']:
        p = f['properties']
        bases[p['building_id']] = min(bases.get(p['building_id'], float('inf')), p['base_height'])
    data = {'tiles': [], 'footprints': footprints, 'base_heights': bases}
    for path in sorted(layer.glob('*.col.npz')):
        with np.load(path) as tile:
            data['tiles'].append({'positions': tile['positions'].ravel().tolist(),
                                  'indices': tile['indices'].ravel().tolist()})
    cache = C.work_root()/'cache/buildings'
    cache.mkdir(parents=True, exist_ok=True)
    path = cache/'walkers-input.json'
    write_json(path, data)
    result_path = cache/'walkers-result.json'
    result_path.unlink(missing_ok=True)
    result = subprocess.run(['node', str(C.REPO_ROOT/'tools/validator/src/buildings.ts'), str(path), str(result_path)])
    if sha256(layer/'MANIFEST.json') != manifest_hash:
        print('Layer changed during validation; rerun buildings-validate against the completed build')
        return 2
    if result_path.exists():
        walkers = json.loads(result_path.read_text())
        write_json(layer/'walkers.json', walkers)
        report['walker_validation'] = walkers
        write_json(layer/'metrics.json', report)
        with (layer/'metrics.md').open('a') as f:
            f.write(f'\n## Rapier walker validation\n\n{walkers["walkers"]} walkers, '
                    f'{walkers["entered"]} entered, {walkers["endedInside"]} ended inside. '
                    f'Elapsed: {walkers["elapsed_ms"]} ms.\n\n{walkers["terrain"]}.\n')
        manifest = json.loads((layer/'MANIFEST.json').read_text())
        manifest['walker_validation'] = 'pass' if walkers['pass'] else 'fail'
        if 'walkers.json' not in manifest['artifacts']:
            manifest['artifacts'].append('walkers.json')
        write_json(layer/'MANIFEST.json', manifest)
    return result.returncode


if __name__ == '__main__':
    raise SystemExit(main())
