"""Build a contiguous capture neighbourhood using only cached, read-only source data."""
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

work = Path(os.environ['TURKU3D_WORK'])
root = work / 'coarse'
port = int(os.environ['TURKU3D_PORT_BASE']) + 5
published = json.loads((work / 'dist/tiles/tileset.json').read_text())
children = []

def collect(node):
    tile = node.get('extras', {}).get('id', '')
    if tile.startswith('t0_') and node.get('content'):
        child = {k: v for k, v in node.items() if k != 'children'}
        child['content'] = {'uri': '/tiles/' + node['content']['uri']}
        child['geometricError'] = 0
        children.append(child)
    for child in node.get('children', []):
        collect(child)

collect(published['root'])
core = {c['extras']['id'] for c in children}
ids = [f't0_{i}_{j}' for i in range(-1, 4) for j in range(-2, 3)
       if f't0_{i}_{j}' not in core]
ids.append('t0_0_1')  # Separate draft-only bridge demonstration over the same neighbourhood.
pending = set(ids)
failures = {}
records = {}
deadline = time.monotonic() + 900
while pending and time.monotonic() < deadline:
    for tile in sorted(pending):
        try:
            with urllib.request.urlopen(f'http://127.0.0.1:{port}/coarse/{tile}/manifest.json', timeout=20) as res:
                answer = json.load(res)
                if res.status != 200:
                    if answer.get('state') == 'pending-data':
                        failures[tile] = answer
                        pending.remove(tile)
                    continue
            records[tile] = answer
            pending.remove(tile)
            print(tile, answer.get('seconds'), flush=True)
        except urllib.error.HTTPError as error:
            failures[tile] = error.read().decode()
            pending.remove(tile)
    if pending:
        time.sleep(2)
for tile in pending:
    failures[tile] = 'capture build timed out after 900 seconds'
for tile in records:
    if tile in core:
        continue
    _, i, j = tile.split('_')
    i, j = int(i), int(j)
    children.append({'boundingVolume': {'box': [i*250+125, -j*250-125, 40, 155,0,0,0,155,0,0,0,100]},
                     'geometricError': 0, 'content': {'uri': f'/draft/{tile}/vis.glb'},
                     'extras': {'id': tile}, 'refine': 'REPLACE'})
(work / 'dist/tiles/capture.json').write_text(json.dumps({'asset': {'version': '1.0', 'gltfUpAxis': 'y'},
    'geometricError': 1024, 'root': {'boundingVolume': {'box': [375,-125,40,900,0,0,0,900,0,0,0,150]},
    'geometricError': 512, 'refine': 'REPLACE', 'children': children}}, indent=2)+'\n')
if 't0_0_1' in records:
    comparison = json.loads((work / 'dist/tiles/capture.json').read_text())
    for child in comparison['root']['children']:
        if child['extras']['id'] == 't0_0_1':
            child['content']['uri'] = '/draft/t0_0_1/vis.glb'
    (work / 'dist/tiles/capture-draft-bridge.json').write_text(json.dumps(comparison)+'\n')
(work / 'video/drafts.json').write_text(json.dumps({'built': records, 'failed': failures, 'published': sorted(core)}, indent=2)+'\n')
print(f'{len(core)} published + {len(records)} adjacent drafts; failures: {failures}', flush=True)
