"""Profiling includes CPU of live persistent packagers and distinguishes lock waits."""
import fcntl
import os
from pathlib import Path
import subprocess
import sys

from turku3d import profiling
from turku3d.stage_cache import StageCache


def test_running_child_cpu_is_counted_before_wait():
    if not Path('/proc/self/stat').exists():return
    before=profiling.cpu_seconds()
    child=subprocess.Popen([sys.executable,'-c',
        'import time,sys; end=time.process_time()+.12\nwhile time.process_time()<end: pass\nprint("ready",flush=True)\nsys.stdin.readline()'],
        stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True)
    try:
        assert child.stdout.readline().strip()=='ready'
        assert child.poll() is None
        assert profiling.cpu_seconds()-before>=.09
    finally:
        child.communicate('\n',timeout=5)


def test_stage_records_blocked_lock_without_changing_cache(tmp_path):
    lockpath=tmp_path/'held.lock'
    child=subprocess.Popen([sys.executable,'-c',
        'import fcntl,sys,time\nf=open(sys.argv[1],"a");fcntl.flock(f,fcntl.LOCK_EX)\nprint("ready",flush=True)\ntime.sleep(.15)',str(lockpath)],
        stdout=subprocess.PIPE,text=True)
    assert child.stdout.readline().strip()=='ready'
    cache=StageCache(tmp_path/'stages')
    def operation():
        with lockpath.open('a') as handle:profiling.flock(handle,fcntl.LOCK_EX,'test')
        return 42
    try:assert cache.run('work','code',[],operation)==42
    finally:child.wait(timeout=5)
    assert cache.metrics['work']['lock_wait_by_kind']['test']>.05
    assert cache.metrics['work']['cpu_seconds']>=0
    assert cache.run('work','code',[],lambda:0)==42
    assert cache.metrics['work']['cached']
    assert cache.metrics['work']['lock_wait_by_kind'].get('test',0)==0
