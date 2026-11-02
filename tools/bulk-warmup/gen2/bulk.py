#!/usr/bin/env python3
"""Host: build an explicit selection using the farm's production/validation/cache code.

No downloads. A completed input acquisition and immutable source profile are mandatory.
Defaults to one worker for small-plan proofs. --walk is the explicit city-run command.
"""
import argparse
from collections import deque
from contextlib import ExitStack
import fcntl
import hashlib
import json
import os
from pathlib import Path
import resource
import signal
import sys
import threading
import time
from recipe import acquisition_recipe


def read(path,default=None):
    try:return json.loads(path.read_text())
    except FileNotFoundError:return default


def cpu_ticks():
    values=list(map(int,Path('/proc/stat').read_text().splitlines()[0].split()[1:]))
    return sum(values[:8]),values[7]


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('root',type=Path)
    select=p.add_mutually_exclusive_group(required=True)
    select.add_argument('--tiles',nargs='+')
    select.add_argument('--walk',type=Path)
    p.add_argument('--workers',type=int,default=1)
    p.add_argument('--reserve-gib',type=float,default=1)
    p.add_argument('--floor-gib',type=float,default=25)
    a=p.parse_args();root=a.root.resolve();state=root/'state'
    if not 1<=a.workers<=min(64,os.cpu_count() or 1):raise ValueError('workers exceed this host CPU plan')
    available=int(next(l.split()[1] for l in Path('/proc/meminfo').read_text().splitlines() if l.startswith('MemAvailable:')))*1024
    if available<a.workers*1.25*2**30:raise RuntimeError('insufficient available memory for requested workers')
    sys.path.insert(0,str(root/'repo/scripts/dev'))
    import shared_tiles as shared
    with ExitStack() as stack:
        for name,mode in [('prefetch',fcntl.LOCK_SH),('bulk',fcntl.LOCK_EX)]:
            handle=stack.enter_context((state/(name+'.lock')).open('a'))
            fcntl.flock(handle,mode|fcntl.LOCK_NB)
        complete=read(state/'PREFETCH_COMPLETE.json')
        sources=read(state/'sources.json')
        if not complete or not sources or complete.get('failures') or complete.get('missing_tiles'):
            raise RuntimeError('prefetch and immutable source freeze must complete first')
        if sources.get('recipe')!=acquisition_recipe(root/'repo'):
            raise RuntimeError('source acquisition recipe changed: rerun prefetch and freeze first')
        if complete['jobs_sha256']!=sources['prefetch_jobs_sha256']:
            raise RuntimeError('frozen sources and prefetch plan differ')
        meta=read(state/'revision.json');commit=meta['commit']
        outside=set(complete.get('unavailable_tiles',[]))
        selected=list(dict.fromkeys(a.tiles or read(a.walk)))
        if any(not shared.TILE.fullmatch(tile) for tile in selected):raise ValueError('invalid L0 tile')
        if a.tiles and outside.intersection(selected):raise ValueError('proof selection lacks source coverage')
        planned=read(state/'walk.json')
        if hashlib.sha256((state/'walk.json').read_bytes()).hexdigest()!=sources['walk_sha256'] or not set(selected)<=set(planned):
            raise RuntimeError('selection is outside the frozen acquisition plan')
        wanted=deque(tile for tile in selected if tile not in outside)
        changed=threading.Event();stopping=threading.Event()
        def stop(*_):stopping.set();changed.set()
        signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
        # Dedicated bulk host only: leave the LAN daemon's 12-worker limit untouched.
        shared.MAX_WORKERS=a.workers
        class BulkFarm(shared.Farm):
            def build(self,*args):
                try:return super().build(*args)
                finally:changed.set()  # Wake after Farm removes the completed job.
            def event(self,kind,**row):
                super().event(kind,**row)
                if kind in ('built','failed'):
                    with (state/'bulk-progress.jsonl').open('a') as log:
                        log.write(json.dumps(dict(event=kind,at=time.time(),**row))+'\n')
                changed.set()
        # Reject unrelated recoverable work before the Farm constructor can resume it.
        expected_revision=hashlib.sha256((commit+':'+sources['version']).encode()).hexdigest()
        pending=read(root/'farm/pending.json',[])
        if any(r['revision']!=expected_revision for r in pending):
            raise RuntimeError('another source revision has pending jobs; finish that explicit run first')
        farm=BulkFarm(root/'farm',root/'run.sh',workers=a.workers,queue_limit=a.workers,
                      floor_gib=a.floor_gib,reserve_gib=a.reserve_gib,grace_seconds=60)
        registered=farm.register(commit,alias='bulkgen2',source_version=sources['version'])
        revision,generation=registered['revision'],registered['generation']
        shared.atomic_json(state/'build.json',{**registered,'selected':selected,'workers':a.workers})
        started=time.monotonic();before=resource.getrusage(resource.RUSAGE_CHILDREN);ticks0=cpu_ticks()
        failed=[];already=[];pressure=False
        try:
            while wanted or farm.jobs:
                changed.clear()
                if stopping.is_set():break
                with farm.lock:count=len(farm.jobs)
                while wanted and count<a.workers:
                    tile=wanted[0]
                    ack=read(root/'farm/exported'/generation/(tile+'.json'))
                    if ack and ack.get('generation')==generation and ack.get('verified'):
                        already.append(tile);wanted.popleft();continue
                    receipt=root/'farm/products'/generation/tile/'ready.json'
                    if receipt.exists():
                        product=receipt.parent
                        farm.validate(product.parent,tile,read(product/'manifest.json'))
                        already.append(tile);wanted.popleft();continue
                    if not count:
                        # No readers remain: raw-derived caches can be evicted immediately.
                        farm.grace_seconds=0
                    pressure=False
                    status,result=farm.request(revision,tile)
                    farm.grace_seconds=60
                    if status==507:
                        pressure=True;break
                    wanted.popleft()
                    if status>=400:failed.append(dict(tile=tile,result=result))
                    with farm.lock:count=len(farm.jobs)
                with farm.lock:active=len(farm.jobs)
                if pressure and not active:break  # Pull/acknowledge outputs, then resume.
                if active:changed.wait(30)
            farm.close()  # Finish owned active workers; queued work is persisted on signals.
        finally:
            farm.close()
        after=resource.getrusage(resource.RUSAGE_CHILDREN);ticks1=cpu_ticks()
        failed.extend(farm.failures.values())
        report=dict(**registered,workers=a.workers,wall_seconds=round(time.monotonic()-started,3),
                    cpu_seconds=round(after.ru_utime+after.ru_stime-before.ru_utime-before.ru_stime,3),
                    cpu_steal_fraction=(ticks1[1]-ticks0[1])/max(1,ticks1[0]-ticks0[0]),
                    peak_child_rss_kib=after.ru_maxrss,metrics=farm.metrics,already_complete=len(already),
                    remaining=list(wanted),outside_coverage=len(set(selected)&outside),failures=failed,
                    disk_pressure=pressure,interrupted=stopping.is_set())
        shared.atomic_json(state/'bulk-result.json',report)
        print(json.dumps(report))
        return 75 if pressure or stopping.is_set() else 1 if failed else 0

if __name__=='__main__':raise SystemExit(main())
