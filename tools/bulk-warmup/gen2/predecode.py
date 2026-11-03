#!/usr/bin/env python3
"""Staging host, before the 64-core window: build every per-unit lidar cache the city run reads.

Runs the pinned revision's own producers, so builds find the same content-addressed entries:
the source-order decode, TileStore point shards, the vegetation reduction and the class-6 roof
points of every classified 500 m unit, plus every ground unit's decode. Resumable per unit.

  predecode.py ROOT [--workers 2] [--keep-raw] [--sample N]

With --keep-raw (17 B per point twice: about 850 GB for the 24.9 G classified points, plus
reductions) the city run decodes nothing; the farm root must then live on that volume, because
disk-pressure eviction measures the farm root's filesystem. Without it only the reductions
(vegetation, class-6 roofs) and ground decodes are kept, and the window still decodes and shards
each classified unit. --sample N builds N evenly spaced classified units (throughput probes).
"""
import argparse
from concurrent.futures import ProcessPoolExecutor
import json
import os
from pathlib import Path
import shutil
import sys
import threading
import time


def setup(root):
    """The same cache mounts and environment as a farm build of the pinned revision."""
    sys.path.insert(0, str(root / 'repo/scripts/dev'))
    from shared_tiles import Farm
    meta = json.loads((root / 'state/revision.json').read_text())
    sources = json.loads((root / 'state/sources.json').read_text())
    farm = object.__new__(Farm)
    farm.root, farm.lock = root / 'farm', threading.RLock()
    data = root / 'farm/sources/versions' / sources['version']
    work = root / 'state/predecode-work'
    farm.mount_intermediates(root / 'repo', work)
    farm.mount_supplements(data, work)
    os.environ.update(TURKU3D_SHARED_CACHE=str(root / 'farm/cache'), TURKU3D_WORK=str(work),
                      TURKU3D_DATA=str(data))
    return meta, data


def unit(args):
    path, kind, keep_raw = args
    from turku3d import config as C
    from turku3d.lidar import decoded
    started = time.process_time()
    path = Path(path)
    data = decoded.source(path)
    row = dict(unit=f'{kind}/{path.stem}', points=len(data))
    if kind == 'pistepilvi':
        from turku3d.buildings.reconstruct import class6
        from turku3d.lidar.cache import TileStore
        from turku3d.vegetation.trees import reduced_points
        store = TileStore(cache_dir=C.cache_dir('lidar'), source_paths={path.stem: path})
        shards = store._ensure_source(C.LazTile.parse(path.stem))
        reduced_points(path)
        class6([path], C.cache_dir('coarse-roofs'))
        if not keep_raw:
            # Unlinking keeps any mapped reader valid; builds re-create a missing entry.
            target = Path(data.data.filename)
            del data
            for p in (target, target.with_name(target.stem + '.scale.json')):
                p.unlink(missing_ok=True)
            shutil.rmtree(shards, ignore_errors=True)
    row['cpu_seconds'] = round(time.process_time() - started, 3)
    return row


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('root', type=Path)
    p.add_argument('--workers', type=int, default=2)
    p.add_argument('--keep-raw', action='store_true')
    p.add_argument('--sample', type=int)
    a = p.parse_args()
    root = a.root.resolve()
    meta, data = setup(root)
    progress = root / 'state/predecode-progress.jsonl'
    done = set()
    if progress.exists():
        for line in progress.read_text().splitlines():
            row = json.loads(line)
            if row.get('keep_raw') == a.keep_raw and row.get('commit') == meta['commit']:
                done.add(row['unit'])
    jobs = [(str(f), kind, a.keep_raw) for kind in ('pistepilvi', 'maanpinta')
            for f in sorted((data / 'raw/laz' / kind).glob('*.laz'))
            if f'{kind}/{f.stem}' not in done]
    if a.sample:
        classified = [j for j in jobs if j[1] == 'pistepilvi']
        jobs = classified[::max(1, len(classified) // a.sample)][:a.sample]
    started = time.monotonic()
    with ProcessPoolExecutor(max_workers=a.workers) as pool, progress.open('a') as log:
        for row in pool.map(unit, jobs):
            log.write(json.dumps(dict(**row, keep_raw=a.keep_raw, commit=meta['commit'])) + '\n')
            log.flush()
    finished = set(done) | {f"{kind}/{Path(f).stem}" for f, kind, _ in jobs}
    remaining = [f for kind in ('pistepilvi', 'maanpinta')
                 for f in (data / 'raw/laz' / kind).glob('*.laz') if f'{kind}/{f.stem}' not in finished]
    report = dict(commit=meta['commit'], keep_raw=a.keep_raw, units=len(finished),
                  remaining=len(remaining), wall_seconds=round(time.monotonic() - started, 1))
    if not remaining:
        (root / 'state/PREDECODE_COMPLETE.json').write_text(json.dumps(report) + '\n')
    print(json.dumps(report), flush=True)


if __name__ == '__main__':
    main()
