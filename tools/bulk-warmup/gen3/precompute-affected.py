#!/usr/bin/env python3
"""Bulk host, during a window: fill the pass's per-tile Rebuild-class cache (e.g. finished-ground zones under
work/migrate/cache) in parallel, in priority order, so the pass's single dispatch thread finds it cached.
The pass evaluates pending_rebuild() for each tile before dispatching it; for finished-ground that computes the tile's
zones (seconds each, written atomically to the cache), and with dozens of lanes the dispatch loop became the limit.
  precompute-affected.py --farm-root F --revision REV --queue queue-turku.txt [--procs 16]   (from the code snapshot)
"""
import argparse
from concurrent.futures import ProcessPoolExecutor
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, 'scripts/dev')
import tile_city_pass as P  # noqa: E402
import tile_migrate as M  # noqa: E402

ROOT = META = None


def init(root, rev):
    global ROOT, META
    ROOT = Path(root)
    META = json.loads((ROOT / 'revisions' / rev / 'revision.json').read_text())
    M.use_base_data(ROOT, META)


def one(tile):
    try:
        P.pending_rebuild(ROOT, META, tile)
        return tile, None
    except Exception as error:  # the pass logs its own errors; this only warms the cache
        return tile, f'{type(error).__name__}: {error}'[:200]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--farm-root', required=True)
    ap.add_argument('--revision', required=True)
    ap.add_argument('--queue', required=True)
    ap.add_argument('--procs', type=int, default=16)
    a = ap.parse_args()
    tiles = list(dict.fromkeys(Path(a.queue).read_text().split()))
    t0, errors = time.monotonic(), 0
    with ProcessPoolExecutor(a.procs, initializer=init, initargs=(a.farm_root, a.revision)) as pool:
        for k, (tile, error) in enumerate(pool.map(one, tiles, chunksize=2), 1):
            errors += bool(error)
            if k % 50 == 0 or k == len(tiles):
                print(json.dumps(dict(t=round(time.monotonic() - t0), done=k, of=len(tiles), errors=errors)), flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
