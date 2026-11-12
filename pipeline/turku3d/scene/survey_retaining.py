"""Freeze a small raw-lidar retaining survey for the semantic source adapter.

Run with an owned TURKU3D_WORK/cache and shared raw inputs mounted read-only.
This command does not build a tile or modify the source data.
"""
import argparse
import hashlib
from pathlib import Path
import pickle
from types import SimpleNamespace

from turku3d import config as C
from turku3d.coarse.bridge_survey import source_paths
from turku3d.coarse.retaining_survey import prepare


def freeze(output, bounds):
    tile = SimpleNamespace(bbox=C.BBoxLocal(*bounds))
    vector = C.raw_dir('wfs') / 'office-block/kanta_Rakenne.parquet'
    paths = source_paths(tile)
    records = prepare(tile, vector, paths)
    provenance = dict(vector=str(vector.relative_to(C.raw_dir())),
                      vectorSha256=hashlib.sha256(vector.read_bytes()).hexdigest(),
                      lidarFiles=[p.name for p in paths], bounds=list(bounds),
                      classes=[2,12], lastReturn=True, heightDatum='N2000')
    data=pickle.dumps(dict(value=records,provenance=provenance))
    Path(output).parent.mkdir(parents=True,exist_ok=True)
    Path(output).write_bytes(data)
    return len(records)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--bounds',nargs=4,type=float,default=[140,240,215,285])
    args=parser.parse_args()
    print(freeze(args.output,args.bounds),'measured retaining runs')
