#!/usr/bin/env python3
"""turku3d-bulk3: keep the migration's decoded-LAZ cache from filling the disk during a city pass.

Below LOW_GB free, delete the least recently modified decoded blocks (.npy + .scale.json, under their
.lock so no decode is in progress) until HIGH_GB is free. Decoded arrays are rebuildable from the LAZ;
an open memory map keeps its inode until unmapped. Exits when the pass pid file's process is gone.

  trim-decoded.py CACHE_DIR      (CACHE_DIR = farm/work/migrate/cache)
"""
import fcntl
import os
import shutil
import sys
import time
from pathlib import Path

LOW_GB, HIGH_GB, EVERY = float(os.environ.get('LOW_GB', 30)), float(os.environ.get('HIGH_GB', 50)), 20
PID = Path('/home/wnt/turku3d-bulk-gen3/pass.pid')


def free_gb(path):
    return shutil.disk_usage(path).free / 1e9


def alive():
    try:
        os.kill(int(PID.read_text()), 0)
        return True
    except (FileNotFoundError, ValueError, ProcessLookupError):
        return False


def trim(cache):
    blocks = sorted(cache.glob('decoded/*/*.npy'), key=lambda p: p.stat().st_mtime)
    freed = 0
    for npy in blocks:
        if free_gb(cache) >= HIGH_GB:
            break
        with npy.with_suffix('.lock').open('a') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                continue
            size = npy.stat().st_size
            npy.with_suffix('.scale.json').unlink(missing_ok=True)
            npy.unlink(missing_ok=True)
            freed += size
    return freed


def main():
    cache = Path(sys.argv[1])
    time.sleep(30)
    while alive():
        if free_gb(cache) < LOW_GB:
            freed = trim(cache)
            print(time.strftime('%FT%TZ', time.gmtime()), f'freed {freed / 1e9:.1f} GB, free {free_gb(cache):.1f} GB',
                  flush=True)
        time.sleep(EVERY)


if __name__ == '__main__':
    main()
