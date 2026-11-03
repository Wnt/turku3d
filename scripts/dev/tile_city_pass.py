#!/usr/bin/env python3
"""City pass of a migrated alias: centre first, nice 19, a concurrency cap that follows the host's load, resumable,
with a progress log of per-tile and per-step timings. Runs on the farm host from the alias's code snapshot, in the
pipeline environment (``shared-tiles-run.sh``), like ``tile_migrate.py``; the Mac front end is
``scripts/dev/tile-migrate pass``.

  tile_city_pass.py --farm-root ROOT --revision DERIVED [--max-jobs 6] [--high 3.5] [--low 2.5]
                    [--interval 30] [--radius RINGS] [--estimate] [tiles ...]

* Order: rings of tiles round t0_0_0, nearest first (the order players see them), or the given tiles.
* Load: every ``--interval`` seconds the cap moves by one: down while the one-minute load per core is above
  ``--high`` (0: no new tile starts), up while it is below ``--low``, between 0 and ``--max-jobs``. Running tiles
  always finish. Workers run at nice 19, under the farm's serving and builds.
* Resumable: a tile finished earlier is kept (``migrate_tile``'s ready check), so a stopped pass resumes.
* Logs in ``farm/migrations/<generation>/``: ``pass.jsonl`` (one line per tile: state, wall and CPU seconds, each
  step's ``[wall, cpu]``, whether the tile has survey lidar, the cap and the load) and ``pass.json`` (progress, the
  mean CPU per step and tile, CPU hours left and an ETA at the current rate). ``--estimate`` prints the estimate
  from the logged tiles without migrating.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import deque
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import tile_migrate as M  # noqa: E402
from shared_tiles import atomic_json  # noqa: E402

STEPS: dict[str, list[float]] = {}


def _timed(cls):
    original = cls.__dict__['apply']

    def apply(self, product):
        wall, cpu = time.monotonic(), time.process_time()
        try:
            return original(self, product)
        finally:
            STEPS[self.key] = [round(time.monotonic() - wall, 3), round(time.process_time() - cpu, 3)]
    cls.apply = apply


def _init():
    """Worker: nice 19, and every step's apply timed (the transforms and their code hashes are untouched)."""
    os.nice(max(0, 19 - os.nice(0)))
    from turku3d.migrations import core

    _timed(core.Migration)
    _timed(core.Rebuild)


def surveyed(tile: str) -> bool:
    """The tile has the survey lidar the lidar steps read (their cost depends on it)."""
    from turku3d import config as C
    from turku3d.coarse import wires

    return wires.survey_paths(C.VisualTile.parse(tile)) is not None


def _work(args):
    root, meta, tile = args
    STEPS.clear()
    wall, cpu = time.monotonic(), time.process_time()
    result = M._worker((root, meta, tile))
    return dict(tile=tile, state=result.get('state'), error=result.get('error'), applied=result.get('applied'),
                wall=round(time.monotonic() - wall, 3), cpu=round(time.process_time() - cpu, 3),
                steps=dict(STEPS), survey=surveyed(tile))


def ring(tile: str) -> int:
    return max(abs(int(v)) for v in tile.split('_')[1:])


def logged(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def estimate(rows: list[dict], total: list[str], survey_share: float | None = None) -> dict:
    """Mean CPU per step and tile (surveyed tiles apart), and CPU hours for the tiles not yet done."""
    done = {r['tile'] for r in rows if r['state'] in ('migrated', 'kept')}
    measured = [r for r in rows if r['state'] == 'migrated']
    out = {'tiles': len(total), 'done': len(done), 'left': len(set(total) - done), 'steps': {}}
    for survey in (True, False):
        group = [r for r in measured if r.get('survey') == survey]
        if not group:
            continue
        keys = sorted({k for r in group for k in r['steps']})
        out['steps']['surveyed' if survey else 'unsurveyed'] = {
            'tiles': len(group),
            **{k: round(sum(r['steps'].get(k, [0, 0])[1] for r in group) / len(group), 1) for k in keys},
            'cpu_per_tile': round(sum(r['cpu'] for r in group) / len(group), 1),
            'wall_per_tile': round(sum(r['wall'] for r in group) / len(group), 1)}
    groups = out['steps']
    if groups:
        share = survey_share if survey_share is not None else (
            sum(r.get('survey', False) for r in measured) / max(1, len(measured)))
        per = {k: v['cpu_per_tile'] for k, v in groups.items()}
        mean = share * per.get('surveyed', per.get('unsurveyed', 0)) + (1 - share) * per.get('unsurveyed', per.get('surveyed', 0))
        out['survey_share'] = round(share, 3)
        out['cpu_hours_left'] = round(out['left'] * mean / 3600, 1)
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('tiles', nargs='*')
    parser.add_argument('--farm-root', type=Path, required=True)
    parser.add_argument('--revision', required=True)
    parser.add_argument('--max-jobs', type=int, default=M.MAX_JOBS)
    parser.add_argument('--high', type=float, default=3.5, help='one-minute load per core that lowers the cap')
    parser.add_argument('--low', type=float, default=2.5, help='one-minute load per core that raises the cap')
    parser.add_argument('--interval', type=float, default=30)
    parser.add_argument('--radius', type=int, help='only tiles within this many rings of t0_0_0')
    parser.add_argument('--estimate', action='store_true')
    args = parser.parse_args()
    root = args.farm_root
    meta = M.revision_meta(root, args.revision)
    if meta.get('kind') != 'migrated':
        raise SystemExit(f'{args.revision} is not a migrated revision')
    M.use_base_data(root, meta)
    log_dir = root / 'migrations' / meta['generation']
    log_dir.mkdir(parents=True, exist_ok=True)
    journal, progress = log_dir / 'pass.jsonl', log_dir / 'pass.json'
    tiles = args.tiles or M.finished_base_tiles(root, meta)
    if args.radius is not None:
        tiles = [t for t in tiles if ring(t) <= args.radius]
    rows = logged(journal)
    if args.estimate:
        share = sum(map(surveyed, tiles)) / max(1, len(tiles))
        print(json.dumps(estimate(rows, tiles, share), indent=1))
        return 0
    finished = {r['tile'] for r in rows if r['state'] in ('migrated', 'kept')}
    pending = deque(t for t in tiles if t not in finished)
    cores = os.cpu_count() or 1
    cap, running, started_at, next_adjust = 1, {}, time.monotonic(), 0.0
    done_now = 0
    with ProcessPoolExecutor(max_workers=max(1, args.max_jobs), initializer=_init) as pool, journal.open('a') as log:
        while pending or running:
            now = time.monotonic()
            load = os.getloadavg()[0] / cores
            if now >= next_adjust:
                next_adjust = now + args.interval
                if load > args.high:
                    cap = max(0, cap - 1)
                elif load < args.low:
                    cap = min(args.max_jobs, cap + 1)
                rate = done_now / max(1.0, now - started_at)
                state = dict(revision=meta['revision'], generation=meta['generation'], pid=os.getpid(),
                             cap=cap, running=sorted(t for t, *_ in running.values()), load_per_core=round(load, 2),
                             updated=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                             rate_tiles_per_hour=round(rate * 3600, 1),
                             eta_hours=round(len(pending) / rate / 3600, 1) if rate else None,
                             **estimate(rows, tiles))
                atomic_json(progress, state)
            while pending and len(running) < cap:
                tile = pending.popleft()
                future = pool.submit(_work, (str(root), meta, tile))
                running[future] = (tile, cap, round(load, 2))
            if not running:
                time.sleep(min(args.interval, 5))
                continue
            for future in wait(running, timeout=5, return_when=FIRST_COMPLETED).done:
                tile, at_cap, at_load = running.pop(future)
                try:
                    row = future.result()
                except Exception as error:  # A worker crash is reported; the pass continues.
                    row = dict(tile=tile, state='failed', error=f'{type(error).__name__}: {error}'[-1000:])
                row.update(cap=at_cap, load_per_core=at_load, at=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()))
                log.write(json.dumps(row) + '\n')
                log.flush()
                rows.append(row)
                done_now += row['state'] == 'migrated'
        atomic_json(progress, dict(revision=meta['revision'], generation=meta['generation'], finished=True,
                                   updated=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), **estimate(rows, tiles)))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
