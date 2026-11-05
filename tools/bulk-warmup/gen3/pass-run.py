#!/usr/bin/env python3
"""Bulk host: run the alias's tile_city_pass.py (from its code snapshot, unchanged) with two window controls.

  python pass-run.py tile_city_pass.py ARGS...      (cwd: the code snapshot; window-pass.sh runs it)

* Live lane count: $LANES_FILE (default /home/wnt/turku3d-bulk-gen3/lanes-turku) holds the number of lanes the pass
  may run. The pass sizes its lanes by one-minute load per core (--high/--low); here os.getloadavg reports overload
  while the pass already runs that many tiles (its own pass.json `running`), so the pass's cap stops at the target
  and grows again when the target is raised. Running tiles are never stopped: lowering the target only holds new
  dispatches. Start the pass with --max-jobs at the most lanes the host could take.
* A lane survives a dead worker: a lane is a one-process pool, and a killed or crashed worker breaks it for good
  (every later tile on that lane would fail at once). The pool is recreated on the lane's next dispatch; the tile
  that was running is logged failed by the pass and the lane moves on.
Tile products are untouched: only scheduling is patched, outside the snapshot.
"""
from concurrent.futures import ProcessPoolExecutor
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, 'scripts/dev')
import tile_city_pass as P  # noqa: E402

CTL = Path(os.environ.get('LANES_FILE', '/home/wnt/turku3d-bulk-gen3/lanes-turku'))
argv = sys.argv[2:]


def arg(name):
    return argv[argv.index(name) + 1] if name in argv else None


meta = json.loads((Path(arg('--farm-root')) / 'revisions' / arg('--revision') / 'revision.json').read_text())
PROGRESS = Path(arg('--farm-root')) / 'migrations' / meta['generation'] / 'pass.json'
real_load = os.getloadavg


def target():
    try:
        return int(CTL.read_text().split()[0])
    except (OSError, ValueError, IndexError):
        return None


def running():
    try:
        return len(json.loads(PROGRESS.read_text()).get('running', []))
    except (OSError, ValueError):
        return 0


def load():
    want = target()
    if want is not None and running() >= want:
        return (1e6, 1e6, 1e6)
    return real_load()


os.getloadavg = load

_init = P.Lane.__init__


def lane_init(self, blocks):
    self._blocks = blocks
    _init(self, blocks)


def get_pool(self):
    pool = self.__dict__['_pool']
    if getattr(pool, '_broken', False):
        pool = ProcessPoolExecutor(max_workers=1, initializer=P._init, initargs=(self._blocks,))
        self.__dict__['_pool'] = pool
        print(f'pass-run: lane pool recreated after a dead worker (last tile {self.tile})', file=sys.stderr, flush=True)
    return pool


def set_pool(self, pool):
    self.__dict__['_pool'] = pool


P.Lane.__init__ = lane_init
P.Lane.pool = property(get_pool, set_pool)
sys.argv = [sys.argv[1]] + argv
raise SystemExit(P.main())
