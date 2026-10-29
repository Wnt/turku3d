"""Build the two Rettig courtyard tiles on deployed OCI and audit exact pool geometry."""

import argparse
import json
import shlex
import subprocess
import sys

from deploy_drafts import HOME
from facade_publish import remote

AUDIT = r"""
import json, pickle
from pathlib import Path
import numpy as np
import shapely
from turku3d import config as C
from turku3d.stage_cache import decompress_payload
from turku3d.coarse.garden_inputs import prepare, features
from turku3d.coarse.sources import Sources
from turku3d.coarse.gardens import stats, grade
from turku3d.col.materials import mat_id
from turku3d.spatial import rows
root = Path('/srv/turku3d/work/coarse')
sources = Sources()
result, seams, scene_seams = {}, [], []
for name, output in OUTPUTS.items():
    tile = C.VisualTile.parse(name)
    scene = prepare(tile, sources.osm_source(tile), sources.lidar_paths(tile)[0],
                    sources.wfs_source('kanta:Vesikohde', tile)[1])
    expected = stats(tile, scene)
    assert len(expected['pools']) == 1, expected
    assert expected['pools'][0]['source'] == 'small_water:1029612222'
    manifest = json.loads((Path(output)/'manifest.json').read_text())
    first = json.loads((Path(output)/'collision.json').read_text())
    assert manifest['gardens'] == first['gardens'] == expected
    times = json.loads((Path(output)/'stage-times.json').read_text())
    path = root/'.stages'/name/'gardens'/(times['gardens']['key']+'.pkl')
    dry, wet, bank, solid = pickle.loads(decompress_payload(path.read_bytes()[32:]))
    body, level, _ = next(row for row in scene['pools'] if row[2].endswith('1029612222'))
    tris = wet.positions[wet.indices]
    near = shapely.contains_xy(body.buffer(1e-6), tris.mean(axis=1)[:,0], tris.mean(axis=1)[:,2])
    pool_faces = tris[near]
    assert len(pool_faces) and np.all(pool_faces[:,:,1] == level-.12)
    water_area = shapely.union_all(shapely.polygons(pool_faces[:,:,[0,2]]))
    expected_area = body.intersection(shapely.box(*tile.bbox.as_tuple()))
    assert water_area.symmetric_difference(expected_area).area < 1e-7
    rim = dry.positions[dry.indices[dry.material == mat_id('quay')]]
    rim = rim[shapely.intersects_xy(body.buffer(.31), rim.mean(axis=1)[:,0], rim.mean(axis=1)[:,2])]
    assert len(rim) and abs(rim[:,:,1].max()-(level+.06)) < 1e-9
    seam = {tuple(v) for v in pool_faces.reshape(-1,3) if v[0] == 500}
    seams.append(seam)
    query = np.c_[np.full(129,500.), np.zeros(129), np.linspace(0,250,129)]
    scene_seams.append(grade(query, scene)[:,1])
    mapped = features(tile, sources.osm_source(tile))
    result[name] = dict(source=expected, water_area_in_tile_m2=water_area.area,
                        rim_triangles=len(rim), mapped_gardens=len(mapped[0]),
                        stairs=manifest['stairs'], barriers=manifest['barriers'])
assert seams[0] == seams[1] and len(seams[0]) == 2, seams
assert np.array_equal(*scene_seams), 'Terrace source levels differ on tile seam'
# Source candidates beyond the two measured tiles. Do not rebuild them.
candidates, seen = set(), set()
for name in OUTPUTS:
    tile = C.VisualTile.parse(name)
    for _, row in rows(sources.osm_source(tile)):
        tags = json.loads(row['tags'] or '{}')
        geom = shapely.force_2d(row['geometry_local'])
        if not (tags.get('leisure') in ('garden', 'swimming_pool')
                or tags.get('water') == 'reflecting_pool' or tags.get('man_made') == 'basin'
                or tags.get('barrier') in ('hedge', 'wall', 'city_wall') or tags.get('wall')):
            continue
        if geom.wkb in seen:
            continue
        seen.add(geom.wkb)
        x0,z0,x1,z1 = geom.bounds
        for i in range(int(np.floor(x0/250)), int(np.floor(x1/250))+1):
            for j in range(int(np.floor(z0/250)), int(np.floor(z1/250))+1):
                if geom.intersects(shapely.box(i*250,j*250,(i+1)*250,(j+1)*250)):
                    candidates.add(C.VisualTile(0,i,j).id)
print(json.dumps(dict(tiles=result, pool_seam_vertices=sorted(seams[0]),
                     other_source_candidate_tiles=sorted(candidates-set(OUTPUTS)))))
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", required=True)
    args = parser.parse_args()
    source = f"{HOME}/sources/{args.commit}"
    result, outputs = dict(commit=args.commit, builds=[]), {}
    for tile in ("t0_1_0", "t0_2_0"):
        output = subprocess.check_output(
            [
                sys.executable,
                "scripts/dev/probe_drafts.py",
                "--commit",
                args.commit,
                "--tile",
                tile,
                "--first-budget",
                "45",
                "--full-budget",
                "150",
            ],
            text=True,
        )
        row = json.loads(output)
        result["builds"].append(row)
        outputs[tile] = row["output"]
        print(json.dumps(row), flush=True)
    command = shlex.join(
        [
            f"{source}/tools/remote-drafts/run.sh",
            "python",
            "-c",
            "OUTPUTS = " + repr(outputs) + "\n" + AUDIT,
        ]
    )
    result["geometry"] = json.loads(
        remote(f"TURKU3D_DRAFT_SOURCE={shlex.quote(source)} " + command)
    )
    print(json.dumps(result), flush=True)


if __name__ == "__main__":
    main()
