"""Audit Aura connectivity and build only the affected spawn neighbours on OCI."""

import argparse
import json
import shlex
import subprocess
import sys

from deploy_drafts import HOME
from facade_publish import remote

SURVEY = r'''
import json
from pathlib import Path
from types import SimpleNamespace
import shapely
from turku3d import config as C
from turku3d.coarse.banks import pond_bodies, small_bodies
from turku3d.coarse.inputs import _frame
from turku3d.coarse.sources import Sources
root = Path('/srv/turku3d/work/coarse')
sources = Sources()
changed = []
controls = {}
spawn = ('t0_0_0', 't0_0_1', 't0_1_0', 't0_1_1')
for name in sorted(set(spawn) | {p.parent.name for p in root.glob('t0_*/manifest.json')}):
    tile = C.VisualTile.parse(name)
    _, path = sources.wfs_source('kanta:Vesikohde', tile)
    if not path:
        continue
    water = [shapely.make_valid(g) for g, _ in _frame(path, tile.bbox)]
    inputs = SimpleNamespace(water=water, sources={
        'wfs': [None, None, str(path)], 'roads': [sources.osm_source(tile)]})
    before, after = small_bodies(water), pond_bodies(inputs)
    if {g.wkb for g in before} != {g.wkb for g in after}:
        # Ring/part ordering alone is not a geometry change.
        if shapely.union_all(before).symmetric_difference(shapely.union_all(after)).area > 1e-7:
            changed.append(name)
    if name in spawn:
        assert not after, (name, [g.area for g in after])
        controls[name] = dict(before=len(before), after=len(after))
assert 't0_1_0' in changed and 't0_1_1' in changed, changed
print(json.dumps(dict(changed_tiles=changed, other_tiles=len(set(changed)-{'t0_0_1'}), spawn=controls)))
'''

GEOMETRY = r'''
import json, pickle
from pathlib import Path
import numpy as np
from turku3d.stage_cache import decompress_payload
root = Path('/srv/turku3d/work/coarse')
result = {}
for name, output in OUTPUTS.items():
    times = json.loads((Path(output)/'stage-times.json').read_text())
    row = times['river_banks']
    path = root/'.stages'/name/'river_banks'/(row['key']+'.pkl')
    dry, wet, bank, solid = pickle.loads(decompress_payload(path.read_bytes()[32:]))
    manifest = json.loads((Path(output)/'manifest.json').read_text())
    level = manifest['water_level']
    assert len(wet.indices) and np.all(wet.positions[:, 1] == level), (name, level)
    result[name] = dict(water_level=level, wet_triangles=len(wet.indices), bank_triangles=len(bank.indices))
print(json.dumps(result))
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--commit', required=True)
    args = parser.parse_args()
    source = f'{HOME}/sources/{args.commit}'

    def audit(script):
        command = shlex.join([f'{source}/tools/remote-drafts/run.sh', 'python', '-c', script])
        return json.loads(remote(f'TURKU3D_DRAFT_SOURCE={shlex.quote(source)} ' + command))

    result = dict(commit=args.commit, survey=audit(SURVEY), builds=[])
    print(json.dumps(result), flush=True)
    outputs = {}
    for tile in ('t0_1_0', 't0_1_1'):
        output = subprocess.check_output([
            sys.executable, 'scripts/dev/probe_drafts.py', '--commit', args.commit,
            '--tile', tile, '--first-budget', '45', '--full-budget', '150'], text=True)
        row = json.loads(output)
        result['builds'].append(row)
        outputs[tile] = row['output']
        print(json.dumps(row), flush=True)
    result['geometry'] = audit('OUTPUTS = ' + repr(outputs) + '\n' + GEOMETRY)
    print(json.dumps(result), flush=True)


if __name__ == '__main__':
    main()
