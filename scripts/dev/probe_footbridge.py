"""Selective deployed Martinkatu geometry audit and real first/full build evidence."""

import argparse
import json
import shlex
import subprocess
import sys

from deploy_drafts import HOME
from facade_publish import remote

AUDIT = """
import json
from pathlib import Path
import numpy as np
import shapely
from turku3d import config as C
from turku3d.coarse import bridges, stairs, retaining
from turku3d.coarse.bridge_details import roadside_supports, style
from turku3d.coarse.bridge_join import terrain_sampler
from turku3d.coarse.inputs import gather
from turku3d.coarse.sources import Sources
tile = C.VisualTile.parse('t0_-2_3')
inputs = gather(tile, Path('/srv/turku3d/work/coarse'), Sources())
sample = terrain_sampler(inputs.points)
decks = bridges.produce(tile, np.empty((0, 3)), inputs)
deck = next(d for d in decks if d.way['tags'].get('name') == 'Betanianpolku')
w = deck.way
assert w['stair_access']
assert w['survey']['profile_source'] == 'city lidar dominant deck returns'
assert w['survey']['p90_error_m'] < .10, w['survey']
assert 26 < w['line'].length < 29, w['line'].length
columns = roadside_supports(w, deck.height_at, lambda p: np.array([sample(*q) for q in p]),
                           w.get('outline', w['line'].buffer(w['width']/2)),
                           w.get('obstacles', shapely.GeometryCollection()))
assert len(columns) == 2, len(columns)
gaps = []
for under in w['under']:
    if under['tags'].get('highway') in bridges.PATHS:
        continue
    overlap = w['line'].intersection(under['line'].buffer(under['width']/2))
    for part in shapely.get_parts(overlap):
        if part.geom_type != 'LineString':
            continue
        _, xz = bridges.samples(part, 1)
        gaps.extend((deck.height_at(xz) - style(w)['thickness'] -
                     np.array([sample(*q) for q in xz])).tolist())
assert min(gaps) >= 4.5, gaps
meshes, openings = stairs.produce(tile, inputs.stairs, sample, decks)
flights = {}
for key in ('51850389', '51850391', '152098490'):
    tags = next(t for k, _, t in inputs.stairs[0] if k == key)
    profile = tags['_profile']
    walk = next(m for m in meshes if m.object_id.startswith('stairs:' + key + ':') and not m.visual)
    tris = walk.positions[walk.indices]
    normal = np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0])
    grade = np.degrees(np.arctan2(np.linalg.norm(normal[:, [0, 2]], axis=1), abs(normal[:, 1])))
    assert grade.max() <= 45, (key, grade.max())
    flats = sum(abs(b-a) < .06 and hi-lo >= 1 for lo, hi, a, b in
                zip(profile['stations'][:-1], profile['stations'][1:],
                    profile['heights'][:-1], profile['heights'][1:]))
    if key != '152098490':
        assert flats >= 2, (key, profile)
    flights[key] = dict(coverage=profile['coverage'], landings=flats,
                        max_grade_degrees=float(grade.max()))
walls, cuts = retaining.produce(tile, inputs.barriers, sample, openings)
assert walls and cuts
print(json.dumps(dict(span_m=w['line'].length, deck_height_range_m=[min(deck.heights), max(deck.heights)],
                     clearance_m=min(gaps), columns=len(columns), stairs=flights,
                     retaining_walls=len(walls), component_triangles=sum(len(m.indices) for m in [*deck.meshes, *meshes, *walls]))))
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", required=True)
    args = parser.parse_args()
    result = dict(commit=args.commit, builds=[])
    # Both actual changed bridge and a second concrete footbridge, not the office block.
    for tile in ("t0_-2_3", "t0_0_1"):
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
        print(json.dumps(row), flush=True)
    source = f"{HOME}/sources/{args.commit}"
    command = shlex.join([f"{source}/tools/remote-drafts/run.sh", "python", "-c", AUDIT])
    result["geometry"] = json.loads(
        remote(f"TURKU3D_DRAFT_SOURCE={shlex.quote(source)} " + command)
    )
    print(json.dumps(result), flush=True)


if __name__ == "__main__":
    main()
