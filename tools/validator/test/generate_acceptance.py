"""Regenerate tiny acceptance fixtures; run with the native pipeline Python from repo root."""
from pathlib import Path
import json
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'pipeline'))
from turku3d.col.fixtures import Mesh, MAT, Y0, S
from turku3d.col.format import write_col

root = Path(__file__).resolve().parents[1] / 'fixtures' / 'acceptance'
root.mkdir(parents=True, exist_ok=True)
for i in range(2):
    for j in range(2):
        mesh = Mesh()
        mesh.ground(cell=S)
        target = root / 'flat' / f'c_{i}_{j}.col'
        target.parent.mkdir(exist_ok=True)
        target.write_bytes(write_col(mesh.tile(i, j), encoding='raw'))
mesh = Mesh()
mesh.ground(cell=S)
mesh.box(20, 40, Y0, Y0 + .30, 20, 40, MAT['curb'], MAT['sidewalk'])
(root / 'curb30.col').write_bytes(write_col(mesh.tile(0, 0), encoding='raw'))
(root / 'curbs.geojson').write_text(json.dumps({
    'type': 'FeatureCollection', 'turku3d_coords': 'engine-xz',
    'features': [{'type': 'Feature', 'properties': {'id': 'planted-30cm', 'step': .25},
                  'geometry': {'type': 'LineString', 'coordinates': [[22, 20], [38, 20]]}}]
}, indent=2) + '\n')

mesh = Mesh()
mesh.ground(cell=S / 5, holes=[(S * .4, S * .6, S * .4, S * .6)])
(root / 'hole').mkdir(exist_ok=True)
(root / 'hole' / 'c_0_0.col').write_bytes(write_col(mesh.tile(0, 0), encoding='raw'))
