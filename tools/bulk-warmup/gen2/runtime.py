"""Bulk-only scheduling policy: live admission cap, Hilbert order and telemetry."""
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


def admits(target,count,reserved_gib,need_gib,total,available):
    """Memory-aware admission: the predicted peaks of all owned builds stay within the plan.

    Reservations come from measured per-stratum peaks, so light suburb tiles run more
    builders than the flat worst case allows while dense centre fronts stay bounded.
    """
    floor=min(8*GIB,total*.05)
    return (count<target and (reserved_gib+need_gib)*GIB<=total-floor
            and available>=floor+need_gib*GIB)


def memory_stalled(cap,active,remaining):
    """No owned builder can release memory: exit resumably instead of idling forever."""
    return cap==0 and active==0 and bool(remaining)


def hilbert(x,y,order):
    """Distance of grid cell (x, y) along a Hilbert curve over a 2**order square."""
    d=0;s=1<<(order-1)
    while s:
        rx=1 if x&s else 0;ry=1 if y&s else 0
        d+=s*s*((3*rx)^ry)
        if ry==0:
            if rx==1:x,y=s-1-x,s-1-y
            x,y=y,x
        s>>=1
    return d


def spiral(du,dv):
    """(ring, position) of a 500 m unit on a continuous square spiral around the centre unit.

    Rings grow outwards by Chebyshev distance; each ring is walked once around its perimeter
    (right, top, left, bottom edge), and the last cell of ring r touches the first of r + 1.
    """
    r=max(abs(du),abs(dv))
    if r==0:return 0,0
    if du==r and dv>-r:k=dv+r-1
    elif dv==r and du<r:k=2*r+(r-1-du)
    elif du==-r and dv<r:k=4*r+(r-1-dv)
    else:k=6*r+(du+r-1)
    return r,k


def ordered(tiles,progress,classes=None,class_seconds=None):
    """Centre-out spiral over the 500 m lidar units; the 2x2 tiles of a unit run together.

    The run starts at the unit containing t0_0_0 and grows outwards ring by ring, so the most
    important tiles are built first even if the 55-minute deadline cuts the run. Each ring is
    walked continuously, so neighbouring units stay adjacent in time and a unit is decoded
    about once within the disk-bounded raw cache (simulated with the 21 B/pt index-shard
    cache: see model.md). Measured durations are reported only.
    """
    timings={}
    if progress.exists():
        for line in progress.read_text().splitlines():
            try:row=json.loads(line)
            except ValueError:continue
            if row.get('event')=='built':timings[row['tile']]=row['seconds']
    cells={tile:tuple(int(v) for v in tile.split('_')[1:]) for tile in tiles}
    def key(tile):
        i,j=cells[tile]
        return (*spiral(i//2,j//2),j,i)
    return sorted(tiles,key=key),timings


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
