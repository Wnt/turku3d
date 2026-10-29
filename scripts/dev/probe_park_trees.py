"""Verify restored park trees against source, path widths and real OCI first/full builds."""

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
from turku3d.coarse.build import grid_mesh
from turku3d.coarse.forests import produce,draft_trees,clears_path
from turku3d.coarse.forest_paths import path_corridors
from turku3d.coarse.surfaces import grid_height
from turku3d.stage_cache import decompress_payload
from turku3d.col.format import read_col
from turku3d.col.materials import mat_id
root=Path('/srv/turku3d/work/coarse')
result={}
for name,output in OUTPUTS.items():
    tile=C.VisualTile.parse(name)
    inputs=gather(tile,root,Sources())
    out=Path(output)
    manifest=json.loads((out/'manifest.json').read_text())
    times=json.loads((out/'stage-times.json').read_text())
    path=root/'.stages'/name/'vegetation'/(times['vegetation']['key']+'.pkl')
    trees,bushes=pickle.loads(decompress_payload(path.read_bytes()[32:]))
    grid=grid_mesh(tile,inputs.heights)[0]
    expected,_=produce(tile,inputs,lambda x,z:grid_height(tile,inputs.heights,x,z),
                       draft_trees(tile,grid,inputs.trees,inputs.water,
                                   [(b,None) for b in inputs.buildings+list(inputs.wall_context)]))
    assert manifest['trees']==len(trees)==len(expected),(name,manifest['trees'],len(expected))
    assert 250<=len(trees)<=768,(name,len(trees))
    paths=path_corridors(inputs)
    assert all(clears_path(t,paths) for t in trees)
    open_mask=shapely.union_all([g for _,g,k,_ in inputs.forests if k=='open'])
    assert all(not open_mask.covers(shapely.Point(t.x,t.z)) for t in trees if t.source=='osm-area')
    assert all(not g.covers(shapely.Point(t.x,t.z)) for t in trees for g in inputs.water)
    trunks=sum(sum(p.material==mat_id('tree_trunk') for p in read_col((out/e['url']).read_bytes()).primitives)
               for e in manifest['collision'].values())
    assert trunks==len(trees),(name,trunks,len(trees))
    canopy=inputs.ortho_canopy
    cells=len(np.unique(np.floor(canopy[:,[0,2]]/5),axis=0)) if len(canopy) else 0
    result[name]=dict(trees=len(trees),source_trees=len(expected),trunk_colliders=trunks,
                      bushes=len(bushes),lidar_class5_half_metre_samples=len(canopy),
                      lidar_class5_5m_cells=cells,path_trunk_violations=0,
                      open_surface_violations=0,water_violations=0,
                      vegetation_seconds=manifest['seconds']['vegetation_scatter'])
print(json.dumps(result))
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", required=True)
    args = parser.parse_args()
    result, outputs = dict(commit=args.commit, builds=[]), {}
    for tile in ("t0_-1_2", "t0_-1_3", "t0_0_3"):
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
                "100",
            ],
            text=True,
        )
        row = json.loads(output)
        result["builds"].append(row)
        outputs[tile] = row["output"]
        print(json.dumps(dict(build=row)), flush=True)
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
        result["vegetation"] = json.loads(
            remote(f"TURKU3D_DRAFT_SOURCE={shlex.quote(source)} " + command)
        )
    except subprocess.CalledProcessError as error:
        raise SystemExit(error.stderr[-4000:]) from error
    print(json.dumps(result), flush=True)


if __name__ == "__main__":
    main()
