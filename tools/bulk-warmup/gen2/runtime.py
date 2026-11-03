"""Bulk-only scheduling policy: live admission cap, longest-first order and telemetry."""
import json
import math
import os
from pathlib import Path
import time

GIB=2**30


def memory():
    values={k:int(v.split()[0])*1024 for k,v in
            (line.split(':',1) for line in Path('/proc/meminfo').read_text().splitlines())}
    return values['MemTotal'],values['MemAvailable']


def worker_limit(cpus=None):
    return min(96,max(1,math.ceil((cpus or os.cpu_count() or 1)*1.5)))


def target_workers(path,previous,limit):
    try:
        value=json.loads(path.read_text())['workers']
        if type(value) is not int or not 1<=value<=limit:
            raise ValueError(f'workers must be an integer in 1..{limit}')
        return value,None
    except FileNotFoundError:
        return previous,None
    except (ValueError,KeyError,TypeError) as error:
        return previous,str(error)


def admission_cap(target,memory_gib,total,available):
    # Reserve worst observed per-tile RSS before starting a burst, then stop new
    # admission under actual memory pressure. Existing builders always drain.
    floor=min(8*GIB,total*.05)
    capacity=max(1,int((total-floor)/(memory_gib*GIB)))
    return min(target,capacity) if available>=floor+memory_gib*GIB else 0


def memory_stalled(cap,active,remaining):
    """No owned builder can release memory: exit resumably instead of idling forever."""
    return cap==0 and active==0 and bool(remaining)


def ordered(tiles,progress,classes=None,class_seconds=None):
    """Centre-out spiral: the costliest dense centre starts first, cheap outer water ends.

    Neighbouring tiles share their 500 m lidar units, so building them close in time
    decodes each unit about once within the disk-bounded raw cache. Ordering by class
    first built every dense tile before the sparse tiles beside them: a simulated 64-vCPU
    city run with 28 live units re-decoded 4,294 units (0.92 h) versus 2,248 (0.83 h).
    Measured durations are returned for reporting only.
    """
    timings={}
    if progress.exists():
        for line in progress.read_text().splitlines():
            try:row=json.loads(line)
            except ValueError:continue
            if row.get('event')=='built':timings[row['tile']]=row['seconds']
    def radius(tile):
        _,x,z=tile.split('_')
        return math.hypot(int(x)+.5,int(z)+.5)
    return sorted(tiles,key=lambda tile:(radius(tile),tile)),timings


class Monitor:
    def __init__(self,state):
        self.state=state;self.before=self.ticks();self.at=time.monotonic()
    @staticmethod
    def ticks():
        return list(map(int,Path('/proc/stat').read_text().splitlines()[0].split()[1:9]))
    def record(self,**row):
        now=time.monotonic()
        if now-self.at<5:return
        current=self.ticks();delta=[a-b for a,b in zip(current,self.before)];ticks=max(1,sum(delta))
        total,available=memory()
        row.update(at=time.time(),interval_seconds=round(now-self.at,3),
                   idle_fraction=delta[3]/ticks,iowait_fraction=delta[4]/ticks,
                   memory_available_gib=round(available/GIB,3))
        with (self.state/'utilization.jsonl').open('a') as log:log.write(json.dumps(row)+'\n')
        self.before=current;self.at=now


def preserve_products(farm,evict,target):
    """Bulk exports own retirement: cache pressure must never delete an old product."""
    if farm.free()>=target:return True
    previous=farm.aliases
    retained=dict(previous)
    for generation in (farm.root/'products').iterdir():
        ready=next(generation.glob('t0_*/ready.json'),None)
        if ready:
            revision=json.loads(ready.read_text())['revision']
            retained['bulk-retain-'+generation.name]=revision
    # Farm calls this under its reentrant lock; no concurrent alias mutation can
    # observe the temporary pins. Verified transfer --prune owns product removal.
    farm.aliases=retained
    try:return evict(target)
    finally:farm.aliases=previous


def split_exclusions(failures,logs):
    """Tiles whose generator reported NoCoverage (open water, no ground returns) are expected
    exclusions, not failures; every other failure stays a failure."""
    excluded,failed=[],[]
    for row in failures:
        tile,revision=row.get('tile'),row.get('revision')
        log=logs/f'{revision}-{tile}.log' if tile and revision else None
        try:text=log.read_text(errors='replace')[-4000:] if log else ''
        except FileNotFoundError:text=''
        (excluded if 'NoCoverage' in text else failed).append(row)
    return excluded,failed
