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


def ordered(tiles,progress):
    timings={}
    if progress.exists():
        for line in progress.read_text().splitlines():
            try:row=json.loads(line)
            except ValueError:continue
            if row.get('event')=='built':timings[row['tile']]=row['seconds']
    def estimate(tile):
        _,x,z=tile.split('_');radius=math.hypot(int(x)+.5,int(z)+.5)
        # Measured durations supersede the centre-density prior. Edge/water cells
        # naturally follow. This is an ordering hint, not a runtime estimate.
        return timings.get(tile,210 if radius<8 else 130 if radius<24 else 60)
    return sorted(tiles,key=lambda tile:(-estimate(tile),tile)),timings


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
