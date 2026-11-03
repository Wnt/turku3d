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
import subprocess
import sys
import threading
import time
from recipe import acquisition_recipe
from runtime import Monitor, admission_cap, admits, memory, memory_stalled, ordered, preserve_products, split_exclusions, target_workers, worker_limit
from workers import WarmWorkers


def read(path,default=None):
    try:return json.loads(path.read_text())
    except FileNotFoundError:return default


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('root',type=Path)
    select=p.add_mutually_exclusive_group(required=True)
    select.add_argument('--tiles',nargs='+')
    select.add_argument('--walk',type=Path)
    p.add_argument('--workers',type=int,default=1)
    p.add_argument('--reserve-gib',type=float,default=.5)
    p.add_argument('--worker-memory-gib',type=float,default=3)
    p.add_argument('--report-dir',type=Path,help='separate proof reports while an earlier generation is being pulled')
    p.add_argument('--control',type=Path,help='atomically replace JSON {workers:N} to adjust live admission')
    p.add_argument('--floor-gib',type=float,default=25)
    p.add_argument('--ttl-file',type=Path,default=Path('/run/bulk-ttl'),help='in-server TTL deadline (bulk-ttl): stop admitting tiles at stop_admission_at')
    p.add_argument('--results-mount',type=Path,default=Path('/srv/results'),help='products on this mount are refused while it is not mounted')
    p.add_argument('--warm-only',action='store_true',help='city runs: compile the one-time shared work (warm.py) and exit, e.g. on the small plan before the resize')
    p.add_argument('--keep-caches',action='store_true',help='city runs: keep per-tile stage/input caches and lidar units (default: delete when done)')
    a=p.parse_args();root=a.root.resolve();state=root/'state'
    reports=a.report_dir or state;reports.mkdir(parents=True,exist_ok=True)
    maximum=worker_limit()
    if not 1<=a.workers<=maximum:raise ValueError('workers exceed this host oversubscription cap')
    if a.reserve_gib<=0 or a.worker_memory_gib<=0:raise ValueError('reservations must be positive')
    if not admission_cap(a.workers,a.worker_memory_gib,*memory()):
        raise RuntimeError('insufficient available memory for one build')
    control=a.control or reports/'workers.json'
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
        products=(root/'farm/products').resolve()
        if products.is_relative_to(a.results_mount) and not os.path.ismount(a.results_mount):
            # nofail mount: never let products fall onto the root disk under an empty mountpoint.
            raise RuntimeError(f'{a.results_mount} is not mounted; products would land on the root disk')
        raw=root/'farm/cache/raw-derived'
        if raw.exists() and os.stat(raw).st_dev!=os.stat(root/'farm').st_dev:
            # Disk-pressure eviction measures the farm root: it would delete these
            # (pre-decoded) maps without freeing any space there.
            raise RuntimeError('farm/cache/raw-derived must be on the farm root filesystem')
        order,timings=ordered([tile for tile in selected if tile not in outside],reports/'bulk-progress.jsonl',
                              read(state/'tile-classes.json',{}).get('tiles',{}),read(state/'class-seconds.json',{}))
        # Measured per-stratum peak RSS (GiB) turns admission memory-aware; without
        # it every build reserves the flat --worker-memory-gib.
        peaks=read(state/'stratum-memory.json',{});strata=read(state/'pilot-strata.json',{}) if peaks else {}
        def need(tile):return peaks.get(strata.get(tile,{}).get('stratum'),a.worker_memory_gib)
        wanted=deque(order if a.walk else [tile for tile in selected if tile not in outside])
        changed=threading.Event();stopping=threading.Event()
        def stop(*_):stopping.set();changed.set()
        signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
        # Dedicated bulk host only: leave the LAN daemon's 12-worker limit untouched.
        shared.MAX_WORKERS=maximum
        transient=None;requested={}
        class BulkFarm(shared.Farm):
            def _make_room(self,target):
                return preserve_products(self,super()._make_room,target)
            def build(self,*args):
                try:return super().build(*args)
                finally:changed.set()  # Wake after Farm removes the completed job.
            def event(self,kind,**row):
                super().event(kind,**row)
                if kind in ('built','failed'):
                    with (reports/'bulk-progress.jsonl').open('a') as log:
                        log.write(json.dumps(dict(event=kind,at=time.time(),**row))+'\n')
                    if transient and row.get('tile'):transient.done(row['tile'])
                changed.set()
        # Reject unrelated recoverable work before the Farm constructor can resume it.
        expected_revision=hashlib.sha256((commit+':'+sources['version']).encode()).hexdigest()
        pending=read(root/'farm/pending.json',[])
        if any(r['revision']!=expected_revision for r in pending):
            raise RuntimeError('another source revision has pending jobs; finish that explicit run first')
        farm=BulkFarm(root/'farm',root/'run.sh',workers=0,queue_limit=maximum,
                      floor_gib=a.floor_gib,reserve_gib=a.reserve_gib,grace_seconds=60)
        # Warm workers: imports, key closures, area indexes and the packager once per worker, not per tile.
        pool=WarmWorkers(farm,root/'run.sh',reserve=need);farm.execute=pool
        registered=farm.register(commit,alias='bulkgen2',source_version=sources['version'])
        revision,generation=registered['revision'],registered['generation']
        descriptor={**registered,'selected':selected,'workers':a.workers}
        shared.atomic_json(reports/'build.json',descriptor)
        shared.atomic_json(reports/'builds'/generation/'build.json',descriptor)
        # An old live-control file must never raise the new proof's worker count.
        shared.atomic_json(control,{'workers':a.workers})
        target=a.workers;monitor=Monitor(reports);last_control_error=None
        # A city run serially warms all ground channels and representative texture
        # encodes before opening the worker floodgate. Proof selections are serial.
        if a.walk:
            warm=root/'repo/tools/bulk-warmup/gen2/warm.py'
            env=dict(os.environ,REPO=str(root/'repo'),TURKU3D_DATA=farm.revision(revision)['data_root'],
                     TURKU3D_WORK=str(root/'farm/products'/generation/'work'),
                     TURKU3D_SHARED_CACHE=str(root/'farm/cache'))
            farm.mount_supplements(Path(env['TURKU3D_DATA']),Path(env['TURKU3D_WORK']))
            if not (root/'farm/products'/generation/'warm.json').exists():
                subprocess.run([str(root/'run.sh'),'python',str(warm),str(root/'farm/products'/generation)],env=env,check=True)
            if a.warm_only:
                print(json.dumps(dict(warm=str(root/'farm/products'/generation/'warm.json'))),flush=True)
                farm.close();pool.close();return 0
            if not a.keep_caches:
                # A fresh city run computes each tile once: finished tiles and lidar units
                # leave no caches behind, so the run fits the staging disk (transient.py).
                from transient import Transient,UnitCaches
                _,build_env=farm.build_environment(revision,root/'farm/products'/generation)
                os.environ.update({k:build_env[k] for k in ('TURKU3D_SHARED_CACHE','TURKU3D_WORK','TURKU3D_DATA') if k in build_env})
                os.environ['TURKU3D_STAGE_CACHE']='transient'  # builds inherit it: no stage entries are written
                caches=UnitCaches(root/'repo',root/'farm/cache')
                transient=Transient(root/'farm/cache',list(wanted),caches.units,caches.paths)

        started=time.monotonic();before=resource.getrusage(resource.RUSAGE_CHILDREN);swept=started
        failed=[];already=[];pressure=False;memory_pressure=False;closed=False
        def ttl_closed():
            # The in-server TTL powers the 64-core plan off; let in-flight tiles finish first.
            deadline=read(a.ttl_file,{}) if a.ttl_file else {}
            return bool(deadline) and time.time()>=deadline.get('stop_admission_at',float('inf'))
        try:
            while wanted or farm.jobs:
                changed.clear()
                if stopping.is_set():break
                target,error=target_workers(control,target,maximum)
                if error and error!=last_control_error:farm.event('control-rejected',error=error)
                last_control_error=error
                total,available=memory()
                if peaks:
                    with farm.lock:reserved=sum(need(tile) for _,tile in farm.jobs);count=len(farm.jobs)
                    fits=lambda:admits(target,count,reserved,need(wanted[0]),total,available)
                    cap=target if not wanted or fits() or count else 0
                else:
                    cap=admission_cap(target,a.worker_memory_gib,total,available)
                    fits=lambda:count<cap
                with farm.lock:
                    farm.workers=cap;farm.queue_limit=maximum;farm.pump();count=len(farm.jobs)
                monitor.record(target_workers=target,admission_cap=cap,active=count,remaining=len(wanted),
                               free_gib=round(farm.free()/2**30,3))
                if transient and time.monotonic()-swept>60:
                    with farm.lock:inflight=[requested.get(tile,time.time()) for _,tile in farm.jobs]
                    transient.sweep(min(inflight,default=time.time()));swept=time.monotonic()
                if not closed and wanted and ttl_closed():
                    closed=True;farm.event('ttl-admission-closed',remaining=len(wanted),active=count)
                while wanted and fits() and not closed:
                    tile=wanted[0]
                    ack=read(root/'farm/exported'/generation/(tile+'.json'))
                    if ack and ack.get('generation')==generation and ack.get('verified'):
                        already.append(tile);wanted.popleft()
                        if transient:transient.done(tile)
                        continue
                    receipt=root/'farm/products'/generation/tile/'ready.json'
                    if receipt.exists():
                        product=receipt.parent
                        farm.validate(product.parent,tile,read(product/'manifest.json'))
                        already.append(tile);wanted.popleft()
                        if transient:transient.done(tile)
                        continue
                    if not count:
                        # Restore headroom for the whole requested wave while raw
                        # caches have no readers. Otherwise a low-space resume would
                        # refill a partial wave forever without reclaiming raw caches.
                        farm.grace_seconds=0
                        if not farm.make_room(farm.floor+farm.reserve*cap):
                            pressure=True;farm.grace_seconds=60;break
                    pressure=False
                    requested[tile]=time.time();status,result=farm.request(revision,tile)
                    farm.grace_seconds=60
                    if status==507:
                        pressure=True;break
                    wanted.popleft()
                    if status>=400:
                        failed.append(dict(tile=tile,result=result))
                        if transient:transient.done(tile)
                    elif peaks:reserved+=need(tile);available-=need(tile)*2**30
                    with farm.lock:count=len(farm.jobs)
                with farm.lock:active=len(farm.jobs)
                if closed and not active:break  # Resumable: finished tiles persist, a rerun skips them.
                if memory_stalled(cap,active,wanted):
                    memory_pressure=True;break
                if pressure and not active:break  # Pull/acknowledge outputs, then resume.
                if active or (wanted and not cap):changed.wait(5)

            farm.close()  # Finish owned active workers; queued work is persisted on signals.
        finally:
            farm.close()
            pool.close()  # Reaped before the CPU totals below.
            if transient:transient.close()
        after=resource.getrusage(resource.RUSAGE_CHILDREN)
        failed.extend(farm.failures.values())
        excluded,failed=split_exclusions(failed,root/'farm/logs')
        report=dict(**registered,workers=a.workers,final_target_workers=target,wall_seconds=round(time.monotonic()-started,3),
                    cpu_seconds=round(after.ru_utime+after.ru_stime-before.ru_utime-before.ru_stime,3),
                    peak_child_rss_kib=after.ru_maxrss,metrics=farm.metrics,already_complete=len(already),
                    transient=transient.metrics if transient else None,
                    remaining=list(wanted),outside_coverage=len(set(selected)&outside),failures=failed,
                    expected_exclusions=[row.get('tile') for row in excluded],
                    disk_pressure=pressure,memory_pressure=memory_pressure,interrupted=stopping.is_set(),ttl_closed=closed)
        shared.atomic_json(reports/'bulk-result.json',report)
        print(json.dumps(report))
        return 75 if pressure or memory_pressure or stopping.is_set() or (closed and wanted) else 1 if failed else 0

if __name__=='__main__':raise SystemExit(main())
