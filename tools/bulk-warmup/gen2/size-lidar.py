#!/usr/bin/env python3
"""Resumable public HEAD inventory before committing the 300 GB disk to raw lidar."""
import concurrent.futures
import json
from pathlib import Path
import sys,time
from turku3d import config as C
from turku3d.fetch.laz import known_tiles
from turku3d.fetch.common import curl_head
state=Path(sys.argv[1]); log=state/'lidar-head.jsonl'
done={r['unit']:r for line in log.read_text().splitlines() if (r:=json.loads(line)).get('status')==200 and r.get('content_length')} if log.exists() else {}
blocks={b.name for t in json.loads((state/'input-tiles.json').read_text()) for b in C.laz_tiles_for_bbox(C.VisualTile.parse(t).bbox)}
units=[(ds,b) for ds,b in sorted(known_tiles()) if b in blocks]
def head(unit):
 ds,b=unit; key=ds+'/'+b
 try: return dict(unit=key,**curl_head(C.LazTile.parse(b).url(ds)))
 except Exception as e: return dict(unit=key,error=str(e))
with log.open('a') as out, concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
 for row in pool.map(head,[(ds,b) for ds,b in units if ds+'/'+b not in done]):
  out.write(json.dumps(row)+'\n');out.flush();done[row['unit']]=row
summary={ds:dict(units=sum(d==ds for d,b in units),bytes=sum(int(r.get('content_length') or 0) for key,r in done.items() if key.startswith(ds+'/'))) for ds in C.LAZ_DATASETS}
summary['failures']=[r for r in done.values() if r.get('status')!=200 or not r.get('content_length')]
(state/'lidar-size.json').write_text(json.dumps(summary,indent=2)+'\n');print(json.dumps(summary),flush=True)
