"""Native roofer execution and CityJSONSeq 2.2 surface extraction."""
from __future__ import annotations
import json
from pathlib import Path
import subprocess
import numpy as np
from turku3d import config as C
from .shells import Surface


def run(pointclouds, footprints, output, jobs=4):
    output.mkdir(parents=True, exist_ok=True)
    # One directory is one pointcloud source in roofer. Passing adjacent LAZ
    # files as separate sources can make its epoch selector discard neighbours.
    pointcloud_dir = output/'pointclouds'
    pointcloud_dir.mkdir(exist_ok=True)
    wanted = {Path(p).name: Path(p).resolve() for p in pointclouds}
    for p in pointcloud_dir.iterdir():
        if p.name not in wanted:
            if not p.is_symlink():
                raise ValueError(f'unexpected file in roofer input staging: {p}')
            p.unlink()
    for name, target in wanted.items():
        link = pointcloud_dir/name
        if link.is_symlink() and link.resolve() != target:
            link.unlink()
        if not link.exists():
            link.symlink_to(target)
    command = ['roofer', '-j', str(jobs), '--srs', C.CRS_HORIZONTAL,
               '--id-attribute', 'building_id', '--lod22', '--no-clip-terrain',
               str(pointcloud_dir.resolve()), str(footprints.resolve()), str(output.resolve())]
    with (output / 'run.log').open('w') as log:
        subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=True, cwd=output)
    version = subprocess.check_output(['roofer', '--version'], text=True, cwd=output).strip()
    (output/'execution.json').write_text(json.dumps({'command': command, 'version': version}, indent=2)+'\n')
    return command


def read(paths):
    results = {}
    for path in sorted(paths):
        transform = {'scale': [1, 1, 1], 'translate': [0, 0, 0]}
        for line in Path(path).read_text().splitlines():
            data = json.loads(line)
            transform = data.get('transform', transform)
            if not data.get('vertices'):
                continue
            v = np.array(data['vertices'], dtype=float) * transform['scale'] + transform['translate']
            v = np.column_stack(C.en_to_local(v[:, 0], v[:, 1], v[:, 2]))
            objects = data['CityObjects']
            for key, obj in objects.items():
                bid = str(obj.get('parents', [key])[0])
                for geom in obj.get('geometry', []):
                    if str(geom.get('lod')) != '2.2':
                        continue
                    semantics = geom.get('semantics', {})
                    values = semantics.get('values', [])
                    boundaries = geom['boundaries']
                    if geom['type'] == 'Solid':
                        surfaces = [(s, values[k][i] if values else None)
                                    for k, shell in enumerate(boundaries) for i, s in enumerate(shell)]
                    elif geom['type'] == 'MultiSurface':
                        surfaces = [(s, values[i] if values else None) for i, s in enumerate(boundaries)]
                    else:
                        raise ValueError(f'unsupported roofer geometry {geom["type"]}')
                    for rings, semantic in surfaces:
                        kind = semantics['surfaces'][semantic]['type'] if semantic is not None else 'WallSurface'
                        results.setdefault(bid, []).append(Surface([v[r] for r in rings], kind))
    return results


def read_metadata(paths):
    metadata = {}
    for path in sorted(paths):
        for line in Path(path).read_text().splitlines():
            data = json.loads(line)
            for key, obj in data.get('CityObjects', {}).items():
                if obj.get('type') == 'Building':
                    metadata[key] = obj.get('attributes', {})
    return metadata
