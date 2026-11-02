#!/usr/bin/env python3
"""Fetch the entire dependency halo before building, using per-unit checksum registries.

Network operations never take a shared registry lock. The pipeline's fetchers still perform
checksums, resumable HTTP, paging and atomic writes; their small registry shards are merged
only after all workers finish. Run after OCI rsync has completed, never alongside a build.
"""
import argparse
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import time

from turku3d import config as C
from turku3d.coarse.sources import Sources, Unit, NoCoverage, block_area, block_tiles, osm_area, WFS_LAYERS, OPTIONAL_WFS_LAYERS
from turku3d.fetch import common, laz, wfs, wms, osm
from turku3d.fetch.sources import WFS_LAYERS as ALL_WFS

OriginalRegistry = common.Registry
BASE = {}
CONTEXT = None
STATE = None

class ShardRegistry(OriginalRegistry):
    def __init__(self, root):
        super().__init__(root)
        key = str(self.dir.resolve())
        if key not in BASE:
            BASE[key] = OriginalRegistry(root).records()
        self.base = BASE[key]
        shard = STATE/'registries'/CONTEXT/self.dir.name
        shard.mkdir(parents=True, exist_ok=True)
        self.manifest = shard/'MANIFEST.jsonl'
        self.sums = shard/'SHA256SUMS'
        self.lockfile = shard/'.lock'
    def get(self, path):
        key = self.rel(path)
        return self.records().get(key) or self.base.get(key)

def fetch_job(job):
    global CONTEXT, STATE
    state, key = job
    STATE = Path(state); CONTEXT = key.replace('/','-')
    for module in (common,laz,wfs,wms,osm):
        module.Registry = ShardRegistry
    receipt = STATE/'receipts'/(CONTEXT+'.json')
    if receipt.exists():
        # Content receipts describe every generated/downloaded file for this unit.
        row = json.loads(receipt.read_text())
        if all((C.data_root()/p).is_file() and (C.data_root()/p).stat().st_size == n for p,n in row['files'].items()):
            return dict(unit=key, status='cached', seconds=0)
    if shutil.disk_usage(C.data_root()).free < 35*2**30:
        return dict(unit=key,status='failed',error='35 GiB prefetch disk floor')
    started=time.monotonic(); messages=[]
    try:
        kind,block=key.split('/')
        tile=C.LazTile.parse(block)
        if kind in ('pistepilvi','maanpinta'):
            laz.fetch_tiles([tile],(kind,),log=messages.append)
        elif kind=='wfs':
            names=set(WFS_LAYERS)|set(OPTIONAL_WFS_LAYERS)|{'kanta:Rakennus','GIS:Rakennukset','kanta:Liikennevayla','kanta:Kevyenliikenteenvayla','kanta:Rakenne'}
            for layer in ALL_WFS:
                if layer.name in names:
                    report=wfs.fetch_layer(layer,block_area(tile),log=messages.append)
                    if report.get('status') not in ('ok','empty'):
                        raise RuntimeError(str(report))
        elif kind=='osm':
            osm.fetch_area(osm_area(tile),log=messages.append)
        else:
            # Same ortho footprint and no-coverage handling as the service prefetcher.
            from turku3d.coarse.imagery import fetch as fetch_imagery
            fetch_imagery(tile, log=messages.append)
        files={}
        for registry in (STATE/'registries'/CONTEXT).glob('*/MANIFEST.jsonl'):
            for line in registry.read_text().splitlines():
                r=json.loads(line); files['raw/'+registry.parent.name+'/'+r['path']]=r['bytes']
        # Cached units may have no shard: record relevant existing source paths too.
        if kind in ('pistepilvi','maanpinta'):
            p=C.raw_dir('laz')/kind/(block+'.laz'); files[str(p.relative_to(C.data_root()))]=p.stat().st_size
        elif kind=='wfs':
            for p in (C.raw_dir('wfs')/block_area(tile).name).rglob('*'):
                if p.is_file(): files[str(p.relative_to(C.data_root()))]=p.stat().st_size
        elif kind=='osm':
            for p in (C.raw_dir('osm')/osm_area(tile).name).rglob('*'):
                if p.is_file(): files[str(p.relative_to(C.data_root()))]=p.stat().st_size
        else:
            from turku3d.coarse.imagery import paths as imagery_paths
            for p in imagery_paths(tile):
                for f in ([p] if p.is_file() else p.rglob('*')):
                    if f.is_file(): files[str(f.relative_to(C.data_root()))]=f.stat().st_size
            from turku3d.coarse.sources import content_paths
            sources = Sources()
            for visual in block_tiles(tile):
                try: selected = sources.ortho_source(visual)
                except NoCoverage: selected = None
                if selected:
                    for f in content_paths([selected]):
                        if f.is_file(): files[str(f.relative_to(C.data_root()))]=f.stat().st_size
        if not files:
            raise RuntimeError('fetch returned without any recorded files')
        row=dict(unit=key,status='complete',seconds=round(time.monotonic()-started,2),files=files)
        temp=receipt.with_suffix('.tmp');temp.write_text(json.dumps(row)+'\n');temp.replace(receipt)
        return {k:v for k,v in row.items() if k!='files'}
    except Exception as error:
        return dict(unit=key,status='failed',seconds=round(time.monotonic()-started,2),error=str(error)[-800:])
    finally:
        (STATE/'logs'/(CONTEXT+'.log')).write_text('\n'.join(messages)+'\n')


def jobs(state):
    tiles=json.loads((state/'input-tiles.json').read_text())
    blocks={b.name:b for name in tiles for b in C.laz_tiles_for_bbox(C.VisualTile.parse(name).bbox)}
    known=laz.known_tiles()
    result=[ds+'/'+b for ds,b in sorted(known) if b in blocks]
    result += ['wfs/'+b for b in sorted(blocks)]
    # Gen 2 ortho fetches imagery for a block, including its stable neighbouring chunks.
    result += ['ortho/'+b for b in sorted(blocks)]
    osm_blocks={osm_area(b).name:b.name for b in blocks.values()}
    result += ['osm/'+b for b in sorted(osm_blocks.values())]
    return result


def merge_registries(state):
    for kind in ('laz','wfs','ortho','osm'):
        root=C.raw_dir(kind); registry=OriginalRegistry(root)
        rows=registry.records()
        for p in sorted((state/'registries').glob('*/'+kind+'/MANIFEST.jsonl')):
            for line in p.read_text().splitlines():
                r=json.loads(line);rows[r['path']]=r
        with registry._locked():
            common.atomic_write_text(registry.manifest,''.join(json.dumps(row,sort_keys=True)+'\n' for _,row in sorted(rows.items())))
            registry._rewrite_sums()


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('state',type=Path)
    p.add_argument('--workers',type=int,default=3)
    p.add_argument('--plan-only',action='store_true')
    p.add_argument('--unit',action='append',help='probe selected fetch units; never publishes complete')
    p.add_argument('--limit',type=int,help='prove a bounded number of fetches; never publishes complete')
    a=p.parse_args(); state=a.state.resolve()
    for name in ('receipts','registries','logs'): (state/name).mkdir(exist_ok=True)
    work=jobs(state)
    (state/'prefetch-jobs.json').write_text(json.dumps(work)+'\n')
    print(json.dumps(dict(jobs=len(work),by_kind={k:sum(x.startswith(k+'/') for x in work) for k in ('pistepilvi','maanpinta','wfs','ortho','osm')})),flush=True)
    if a.plan_only: return
    with (state/'prefetch.lock').open('a') as lock:
        import fcntl
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        (state/'PREFETCH_COMPLETE.json').unlink(missing_ok=True)
        failures=[]
        selected=a.unit or work[:a.limit]
        if any(key not in work for key in selected): raise ValueError('unit outside planned area')
        with (state/'prefetch-progress.jsonl').open('a') as log, concurrent.futures.ProcessPoolExecutor(max_workers=a.workers) as pool:
            for row in pool.map(fetch_job,[(str(state),key) for key in selected]):
                log.write(json.dumps(row)+'\n');log.flush()
                if row['status']=='failed': failures.append(row)
        merge_registries(state)
        summary=dict(jobs=len(work),completed=len(selected)-len(failures),failures=failures,at=time.time())
        if not a.limit and not a.unit and not failures:
            sources=Sources(); uncovered=[];missing={}
            for name in json.loads((state/'walk.json').read_text()):
                try:
                    units=sources.missing_for_service(C.VisualTile.parse(name))
                    if units: missing[name]=[u.key for u in units]
                except NoCoverage: uncovered.append(name)
            summary.update(outside_lidar_coverage=uncovered,missing_tiles=missing)
            if not missing:
                summary['jobs_sha256']=hashlib.sha256((state/'prefetch-jobs.json').read_bytes()).hexdigest()
                (state/'PREFETCH_COMPLETE.json').write_text(json.dumps(summary)+'\n')
        (state/'prefetch-coverage.json').write_text(json.dumps(summary,indent=2)+'\n')
        print(json.dumps(summary),flush=True)
        if failures or summary.get('missing_tiles'): raise SystemExit(1)

if __name__=='__main__': main()
