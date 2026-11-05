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
  that was running is logged failed by the pass and the lane moves on. Idle lanes beyond the target release their
  worker after 2 min (memory back), and get a new one when dispatched again.
* Priority queue: $QUEUE_FILE (default /home/wnt/turku3d-bulk-gen3/queue-turku.txt), one tile per line in priority
  order, replaces the pass's Chebyshev rings by priority bands of $QUEUE_BAND tiles (default 16): a lane takes, within
  the first band left, the tile nearest its last survey block (the pass's own pick), so neighbours share decoded
  blocks and stage caches. Tiles not in the file follow it, by ring. When the file changes (write a new one and mv
  it in place), the not-yet-started tiles are re-sorted at the next dispatch; running tiles are never touched.
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

QUEUE = Path(os.environ.get('QUEUE_FILE', '/home/wnt/turku3d-bulk-gen3/queue-turku.txt'))
BAND = int(os.environ.get('QUEUE_BAND', '16'))
chebyshev, original_pick = P.ring, P.pick
queue = dict(stamp=None, rank={})


def reload_queue():
    try:
        stamp = QUEUE.stat().st_mtime
    except OSError:
        stamp = None
    if stamp == queue['stamp']:
        return False
    queue['stamp'] = stamp
    tiles = QUEUE.read_text().split() if stamp else []
    queue['rank'] = {t: k for k, t in enumerate(dict.fromkeys(tiles))}
    print(f'pass-run: priority queue {len(queue["rank"])} tiles from {QUEUE}', file=sys.stderr, flush=True)
    return True


def band(tile):
    k = queue['rank'].get(tile)
    return k // BAND if k is not None else 10**6 + chebyshev(tile)


def pick(pending, last):
    if reload_queue():
        pending.sort(key=lambda t: (band(t), t))  # in place: the pass's own list, not-yet-started tiles only
    return original_pick(pending, last)


reload_queue()
P.ring, P.pick = band, pick

_init = P.Lane.__init__


LANES = []


def lane_init(self, blocks):
    self._blocks = blocks
    _init(self, blocks)
    LANES.append(self)


def release_idle(idle_s=120, every=20):
    """Idle lanes beyond the target give back their worker (and its decoded blocks): RSS does not creep up after the
    lane count is lowered. The lane gets a fresh worker on its next dispatch (get_pool)."""
    import threading
    import time
    seen = {}

    def loop():
        while True:
            time.sleep(every)
            now, want = time.monotonic(), target()
            live = [lane for lane in LANES if getattr(lane.__dict__.get('_pool'), '_processes', None)]
            for lane in LANES:
                if lane.future is not None:
                    seen[id(lane)] = now
            idle = [lane for lane in live if lane.future is None and now - seen.get(id(lane), now) > idle_s]
            spare = max(0, len(live) - (want if want is not None else len(live)))
            for lane in idle[:spare]:
                lane.__dict__['_pool'].shutdown(wait=False)
                print(f'pass-run: idle lane worker released (last tile {lane.tile})', file=sys.stderr, flush=True)
    threading.Thread(target=loop, daemon=True).start()


def get_pool(self):
    pool = self.__dict__['_pool']
    if getattr(pool, '_broken', False) or getattr(pool, '_shutdown_thread', False):
        pool = ProcessPoolExecutor(max_workers=1, initializer=P._init, initargs=(self._blocks,))
        self.__dict__['_pool'] = pool
        print(f'pass-run: lane worker (re)started (last tile {self.tile})', file=sys.stderr, flush=True)
    return pool


def set_pool(self, pool):
    self.__dict__['_pool'] = pool


P.Lane.__init__ = lane_init
P.Lane.pool = property(get_pool, set_pool)
release_idle()
sys.argv = [sys.argv[1]] + argv
raise SystemExit(P.main())
