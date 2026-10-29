"""Build the affected Aura boat tiles first/full and compare boat geometry with source."""

import argparse
import json
import shlex
import subprocess
import sys

from deploy_drafts import HOME
from facade_publish import remote

AUDIT = r"""
import json,pickle
from pathlib import Path
import numpy as np
import shapely
from turku3d import config as C
from turku3d.coarse.inputs import gather
from turku3d.coarse.sources import Sources
from turku3d.coarse.boats import stats,visuals
from turku3d.stage_cache import decompress_payload
root=Path('/srv/turku3d/work/coarse')
sources=Sources();result={};owners={}
for name,output in OUTPUTS.items():
    tile=C.VisualTile.parse(name)
    inputs=gather(tile,root,sources)
    manifest=json.loads((Path(output)/'manifest.json').read_text())
    first=json.loads((Path(output)/'collision.json').read_text())
    times=json.loads((Path(output)/'stage-times.json').read_text())
    path=root/'.stages'/name/'boats'/(times['boats']['key']+'.pkl')
    meshes=pickle.loads(decompress_payload(path.read_bytes()[32:]))
    expected=stats(tile,inputs.boats,meshes)
    # Passage carving may reduce only triangles, not source boat identities.
    assert manifest['boats']['boats']==first['boats']['boats']==expected['boats']
    assert len(meshes)>0
    removed={key for boat in inputs.boats for key in boat['building_keys']}
    assert not removed.intersection(b.key for b in inputs.buildings)
    facade_ids={key for boat in inputs.boats for key in boat['source_facades']}
    assert not facade_ids.intersection(manifest['building_keys'])
    for boat in expected['boats']:
        owners[boat['id']]=boat
        assert .9 <= boat['deck_m']-boat['water_m'] <= 1.90001
        assert boat['cabin_parts']>0,boat
    look=visuals(tile,meshes)
    keys=sorted({m.extras['material_key']for m in look})
    assert 'boat_hull' in keys and 'boat_deck' in keys,(name,keys)
    window=shapely.box(*tile.bbox.as_tuple())
    has_cabin=any(g.intersection(window).area>1e-4 for b in inputs.boats for g,_ in b['cabins'])
    assert ('boat_glass' in keys)==has_cabin,(name,has_cabin,keys)
    assert not any('facade' in k or 'plaster' in k for k in keys)
    solid=sum(len(m.indices)for m in meshes if m.metadata.get('collision',True))
    assert solid<14000,(name,solid)
    result[name]=dict(source_boats=expected['boats'],fragments=len(meshes),solid_triangles=solid,
                      visual_triangles=sum(len(m.indices)for m in meshes if m.visual),materials=keys,
                      ordinary_buildings=manifest['buildings'])
assert len(owners)>=7,owners
assert 'osm:642149874' in owners and owners['osm:642149874']['name']=='Donna'
assert owners['osm:642149874']['masts']==2
print(json.dumps(dict(boats_changed=len(owners),boats=owners,tiles=result)))
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", required=True)
    args = parser.parse_args()
    result, outputs = dict(commit=args.commit, builds=[]), {}
    # All seven central mapped ships, including Donna's cross-tile bow.
    for tile in ("t0_0_1", "t0_1_1", "t0_-1_1", "t0_-2_2"):
        output = subprocess.check_output(
            [
                sys.executable,
                "scripts/dev/probe_drafts.py",
                "--commit",
                args.commit,
                "--tile",
                tile,
                "--first-budget",
                "35",
                "--full-budget",
                "100",
            ],
            text=True,
        )
        row = json.loads(output)
        result["builds"].append(row)
        outputs[tile] = row["output"]
        print(json.dumps(row), flush=True)
    source = f"{HOME}/sources/{args.commit}"
    command = shlex.join(
        [
            f"{source}/tools/remote-drafts/run.sh",
            "python",
            "-c",
            "OUTPUTS=" + repr(outputs) + "\n" + AUDIT,
        ]
    )
    try:
        result["geometry"] = json.loads(
            remote(f"TURKU3D_DRAFT_SOURCE={shlex.quote(source)} " + command)
        )
    except subprocess.CalledProcessError as error:
        raise SystemExit(error.stderr[-2000:]) from error
    print(json.dumps(result), flush=True)


if __name__ == "__main__":
    main()
