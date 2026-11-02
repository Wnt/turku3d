#!/usr/bin/env python3
"""Classify city input density from existing WFS metadata, without reading/copying geometry."""
import argparse
from collections import Counter
from functools import lru_cache
import json
import math
from pathlib import Path
import pyarrow.parquet as pq
from turku3d import config as C
from turku3d.coarse.sources import Sources

p=argparse.ArgumentParser(description=__doc__);p.add_argument('state',type=Path);a=p.parse_args()
source=Sources();excluded=json.loads((a.state/'PREFETCH_COMPLETE.json').read_text())['unavailable_tiles']
@lru_cache(maxsize=None)
def count(path):return pq.ParquetFile(path).metadata.num_rows if path else 0
rows={}
for tile in json.loads((a.state/'walk.json').read_text()):
 if tile in excluded:continue
 t=C.VisualTile.parse(tile);found,path=source.wfs_source('bldg:Building_LOD2',t)
 if not found:raise ValueError('prefetched WFS missing: '+tile)
 n=count(path);kind='dense' if n>=100 else 'sparse' if n else 'no_buildings'
 rows[tile]=dict(source_rows=n,kind=kind,source=str(path) if path else None)
# Source rows count a buffered WFS block, not buildings uniquely inside a tile.
summary=dict(counts=dict(Counter(r['kind'] for r in rows.values())),tiles=rows,
             caveat='WFS block-density classes; no_buildings does not imply water')
(a.state/'tile-classes.json').write_text(json.dumps(summary,sort_keys=True)+'\n')
probes={}
for kind in ('sparse','no_buildings'):
 candidates=[t for t,r in rows.items() if r['kind']==kind and math.hypot(*map(int,t.split('_')[1:]))>30]
 if candidates:probes[kind]=min(candidates,key=lambda t:sum(int(v)**2 for v in t.split('_')[1:]))
print(json.dumps(dict(counts=summary['counts'],probes=probes)))
