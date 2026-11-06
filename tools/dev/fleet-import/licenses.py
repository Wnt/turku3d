#!/usr/bin/env python3
"""Regenerate the converted-car section of game/public/assets/LICENSES.md from models.json and the registry entries.
Run from the repository root after converting: python3 tools/dev/fleet-import/licenses.py"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
LIC = ROOT / 'game/public/assets/LICENSES.md'
MARK = '\n\n## Converted CC BY 4.0 cars (`cars/<id>/`)'
models = json.loads((ROOT / 'tools/dev/fleet-import/models.json').read_text())['models']


def row(m):
    e = json.loads((ROOT / f"game/src/actors/fleet/models/{m['id']}.json").read_text())
    src = e['source']
    title = src['title'].replace('|', '\\|')
    author = src['author'] + (' (model by Spaehling)' if m['id'] == 'sprinter-ambulance' else '')
    carla = m['id'].startswith('carla-')
    credit = (f"Attribution: {src['title'].split(' (')[0]} from the CARLA Simulator (https://carla.org), CARLA Team / Computer "
              f"Vision Center, CC BY 4.0, modified for Turku3D." if carla else
              f"Attribution: \"{title}\" by {author}, CC BY 4.0, modified for Turku3D.")
    changes = ['reoriented' + (', mirrored to left-hand drive' if m.get('mirror') else '') + ('' if carla else f" and rescaled to {e['fleet']['length']} m"),
               'wheels split at hubs' + (' (skinned wheel bones)' if carla else ''),
               'badges, logos and plate text removed (blank generic plates)' + (', "CARLA" plates blanked' if carla else ''),
               'textures sampled into vertex colours (no images shipped)',
               'livery colours kept' if m.get('livery') else 'paint made tintable']
    if m.get('drop') or m.get('blank') or (m.get('emblems') or {}).get('extra'):
        changes.append('grille emblems removed by hand-placed boxes')
    changes.append('decimated to ' + '/'.join(str(t) for t in e['triangles']) + ' triangles')
    return (f"| `cars/{m['id']}/lod{{0,1,2}}.glb` | \"{title}\" ({src['url']}) by {author} | **CC BY 4.0** "
            f"([licence]({src['licenceUrl']})) | {credit} Changes: {'; '.join(changes)}. Runtime name \"{m['name']}\" (fictional). |")


text = LIC.read_text()
head = text[:text.index(MARK)] if MARK in text else text.rstrip('\n')
sketch = [m for m in models if not m['id'].startswith('carla-')]
carla = [m for m in models if m['id'].startswith('carla-')]
out = head + MARK + """

Converted on 2026-11-05 with `game/scripts/import-fleet-gltf.mjs` and `tools/dev/fleet-import/models.json`
(rebuild in `game/`: `node scripts/import-fleet-gltf.mjs ../tools/dev/fleet-import/models.json <id>`; this table:
`python3 tools/dev/fleet-import/licenses.py`). Sources stay outside git: Sketchfab downloads with each model page's licence text
as read at download time (`LICENSE.txt`, `sketchfab-record.json`) in `~/turku3d/loops/carmodels/<id>/`; CARLA 0.9.16
exports with their `LICENSE.txt` and `info.json` in `~/turku3d/loops/carmodels/carla/<vehicle>/` (original export on
osgallery-dev `/bulk/turku3d/assets/carla/`). Brand names appear only to credit the source models; the game uses fictional names.

| File | Source | Licence | Notes |
|---|---|---|---|
""" + '\n'.join(row(m) for m in sketch) + """

### CARLA Simulator vehicles

CARLA specific assets are distributed under CC BY 4.0 (carla-content `LICENSE`; CARLA `README`). Credit: CARLA Team,
Computer Vision Center (CVC), Barcelona, https://carla.org.

| File | Source | Licence | Notes |
|---|---|---|---|
""" + '\n'.join(row(m) for m in carla) + '\n'
LIC.write_text(out)
print(len(sketch), 'Sketchfab rows,', len(carla), 'CARLA rows')
