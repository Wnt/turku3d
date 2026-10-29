"""Verify the reported duck ponds and first/full publication on deployed OCI source."""

import argparse
import json
import shlex
import subprocess
import sys

from deploy_drafts import HOME
from facade_publish import remote

SURVEY = r'''
import json
import numpy as np
import shapely
from pathlib import Path
from turku3d import config as C
from turku3d.coarse.inputs import gather
from turku3d.coarse.sources import Sources
from turku3d.coarse.build import grid_mesh, water_level
from turku3d.coarse.banks import small_bodies, pond_levels, produce
from turku3d.col.materials import mat_id

tile = C.VisualTile.parse('t0_-1_2')
inputs = gather(tile, Path('/srv/turku3d/work/coarse'), Sources())
ponds = small_bodies(inputs.water)
paths = sorted({C.raw_dir('laz') / 'maanpinta' / f'{b.name}.laz'
                for p in ponds for b in C.laz_tiles_for_bbox(C.BBoxLocal(*p.buffer(3).bounds))})
levels = pond_levels(inputs.water, paths)
assert len(levels) == 3, f'Expected three surveyed ponds, got {len(levels)}'
p, ix = grid_mesh(tile, inputs.heights)
level = water_level(inputs.points, inputs.water)
args = (p, ix, np.full(len(ix), mat_id('grass'), np.uint8), np.ones(len(ix), bool),
        inputs.water, level, inputs.surfaces, inputs.barriers)
before = produce(*args)[0]
after, wet, bank, solid = produce(*args, ponds=levels)
rows = []
for pond, surface in levels:
    band = pond.buffer(3).difference(pond)
    def height(mesh, water):
        mask = shapely.intersects_xy(band, mesh.positions[:, 0], mesh.positions[:, 2])
        return float(mesh.positions[mask, 1].max() - water)
    faces = bank.positions[bank.indices]
    normals = np.cross(faces[:, 1] - faces[:, 0], faces[:, 2] - faces[:, 0])
    natural = (bank.material != mat_id('quay')) & (np.abs(normals[:, 1]) < 1e-10)
    centres = bank.positions[bank.indices].mean(axis=1)
    assert not (natural & shapely.contains_xy(pond.buffer(0.01), centres[:, 0], centres[:, 2])).any()
    mask = shapely.contains_xy(pond.buffer(1e-7), wet.positions[:, 0], wet.positions[:, 2])
    assert np.all(wet.positions[mask, 1] == surface)
    triangles = after.positions[after.indices]
    centre = triangles.mean(axis=1)
    lip = shapely.contains_xy(pond.buffer(2), centre[:, 0], centre[:, 2])
    normal = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
    slope = np.rad2deg(np.arctan2(np.linalg.norm(normal[lip][:, [0, 2]], axis=1),
                                np.abs(normal[lip, 1])))
    assert slope.max() <= 30, f'Pond lip exceeds 30 degrees: {slope.max()}'
    rows.append(dict(max_lip_slope_degrees=round(float(slope.max()), 2), area_m2=round(pond.area, 3), water_before=round(level, 3),
                     water_after=round(surface, 3),
                     max_bank_height_before=round(height(before, level), 3),
                     max_bank_height_after=round(height(after, surface), 3)))
print(json.dumps(dict(changed_water_bodies=len(levels), bank_band_metres=3, ponds=rows)))
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--commit', required=True)
    args = parser.parse_args()
    source = f'{HOME}/sources/{args.commit}'
    remote('test -d ' + shlex.quote(source))
    cmd = shlex.join([f'{source}/tools/remote-drafts/run.sh', 'python', '-c', SURVEY])
    survey = json.loads(remote(f'TURKU3D_DRAFT_SOURCE={shlex.quote(source)} ' + cmd))
    print(json.dumps(dict(commit=args.commit, survey=survey)), flush=True)
    # Pond tile plus an Aura river control; both compare source building/collision counts.
    for tile in ('t0_-1_2', 't0_0_0'):
        subprocess.run([sys.executable, 'scripts/dev/probe_drafts.py', '--commit', args.commit,
                        '--tile', tile, '--first-budget', '45', '--full-budget', '150'], check=True)


if __name__ == '__main__':
    main()
