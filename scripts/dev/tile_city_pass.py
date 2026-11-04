#!/usr/bin/env python3
"""City pass of a migrated alias: centre first, nice 19, a concurrency cap that follows the host's load, resumable,
with a progress log of per-tile and per-step timings. Runs on the farm host from the alias's code snapshot, in the
pipeline environment (``shared-tiles-run.sh``), like ``tile_migrate.py``; the Mac front end is
``scripts/dev/tile-migrate pass``.

  tile_city_pass.py --farm-root ROOT --revision DERIVED [--max-jobs 6] [--high 3.5] [--low 2.5]
                    [--interval 30] [--radius RINGS] [--checkpoint STEP] [--supply DIR] [--estimate] [tiles ...]

* Order: rings of tiles round t0_0_0, nearest first (the order players see them), or the given tiles. Each worker
  is a lane that keeps ``--blocks`` decoded LAZ blocks and walks round the ring from block to neighbouring block,
  so the wires step decodes each block about once per lane instead of once per tile (same output).
* Load: every ``--interval`` seconds the cap moves by one: down while the one-minute load per core is above
  ``--high`` (0: no new tile starts), up while it is below ``--low``, between 0 and ``--max-jobs``. Running tiles
  always finish. Workers run at nice 19, under the farm's serving and builds.
* Disk: no new tile starts while the farm's disk has less than ``--min-free-gb`` free (the steps' caches grow there).
* Memory: the cap also steps down while the host has less than ``--min-free-mem-gb`` available (a lane holds its
  decoded blocks, several GB; the farm's services share the container's memory).
* Resumable: a tile finished earlier is kept (``migrate_tile``'s ready check), so a stopped pass resumes.
* Survey: a tile whose catalog-listed survey blocks are not all on disk waits (re-checked every
  ``--survey-interval`` s, for at most ``--wait-hours``): migrated without them it would stay without wires and
  crowns. ``--supply DIR`` hard-links missing blocks from DIR when size and SHA-256 match the base's catalog, or copies
  them when DIR is on another filesystem (lidarpc's downloads sit on the bulk HDD).
* Spot rebuilds: a tile a Rebuild step needs a fresh build of (``missing-rebuild``, e.g. landmark-windows@1) asks the
  farm for that build (``--farm-url``) and is retried every ``--rebuild-interval`` s until the build is there.
* ``--checkpoint STEP`` stores each tile's product after STEP too (``tile_migrate.checkpoint``): a later chain that
  changes only the steps after STEP resumes from it instead of redoing the costly steps before.
* Logs in ``farm/migrations/<generation>/``: ``pass.jsonl`` (one line per tile: state, wall and CPU seconds, each
  step's ``[wall, cpu]``, whether the tile has survey lidar, the cap and the load) and ``pass.json`` (progress, the
  mean CPU per step and tile, CPU hours left and an ETA at the current rate). ``--estimate`` prints the estimate
  from the logged tiles without migrating.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import threading
import time
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


def _init(blocks=0):
    """Worker: nice 19, every step's apply timed (the transforms and their code hashes are untouched), and the wires
    step keeping ``blocks`` decoded LAZ blocks for the next tiles of its lane (same output, fewer decodes)."""
    os.nice(max(0, 19 - os.nice(0)))
    from turku3d.coarse import wires
    from turku3d.migrations import core

    wires.BLOCKS = blocks
    _timed(core.Migration)
    _timed(core.Rebuild)


def surveyed(tile: str) -> bool:
    """The tile has the survey lidar the lidar steps read (their cost depends on it)."""
    from turku3d import config as C
    from turku3d.coarse import wires

    return wires.survey_paths(C.VisualTile.parse(tile)) is not None


class Survey:
    """The survey blocks a tile's lidar steps read, against the base source version's catalog.

    A block the catalog lists but the data root lacks makes the tile wait: migrated without it, the tile would be
    kept without wires or crowns for good (the ready check never looks again). With ``supply`` directories, such a
    block is hard-linked (across filesystems: copied) in from there once its size and SHA-256 match the catalog (the
    same bytes the version names, as the bulk generation had them)."""

    def __init__(self, supply=(), log: Path | None = None):
        from turku3d import config as C

        self.C = C
        self.root = C.raw_dir("laz") / "pistepilvi"
        catalog = json.loads((C.raw_dir("laz").parents[1] / "source-version.json").read_text()).get("files", {})
        self.catalog = {Path(k).stem: v for k, v in catalog.items() if k.startswith("raw/laz/pistepilvi/")}
        self.supply = [Path(d) for d in supply]
        self.linked = 0
        self.log = log

    def missing(self, tile: str) -> list[str]:
        from turku3d.coarse import wires

        bbox = self.C.VisualTile.parse(tile).bbox.buffered(wires.SEARCH)
        names = [b.name for b in self.C.laz_tiles_for_bbox(bbox)]
        return [n for n in names if n in self.catalog and not (self.root / f"{n}.laz").is_file() and not self._link(n)]

    def _link(self, name: str) -> bool:
        want = self.catalog[name]
        target = self.root / f"{name}.laz"
        for directory in self.supply:
            path = directory / f"{name}.laz"
            if not path.is_file() or path.stat().st_size != want["bytes"]:
                continue
            method = supply_block(path, target, want["sha256"])
            if method is None:
                continue
            self.linked += 1
            if self.log is not None:
                with self.log.open("a") as out:
                    out.write(json.dumps(dict(block=name, bytes=want["bytes"], sha256=want["sha256"], source=str(path),
                                              target=str(target), method=method,
                                              at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))) + "\n")
            return True
        return False


def supply_block(path: Path, target: Path, sha256: str, link: bool | None = None) -> str | None:
    """Put ``path`` at ``target`` when its SHA-256 is ``sha256``: a hard link on the same filesystem, else a copy
    hashed as it is written (one read; the supply sits on the bulk HDD, the farm on the SSD). The copy appears under
    its final name only once complete and verified. Returns ``'link'``, ``'copy'`` or None (digest mismatch)."""
    import hashlib

    digest = hashlib.sha256()
    if link is None:
        link = path.stat().st_dev == target.parent.stat().st_dev
    if link:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1 << 22), b""):
                digest.update(chunk)
        if digest.hexdigest() != sha256:
            return None
        try:
            os.link(path, target)
        except FileExistsError:
            pass
        return "link"
    partial = target.with_name(f".{target.name}.{os.getpid()}.part")
    try:
        with path.open("rb") as stream, partial.open("wb") as out:
            for chunk in iter(lambda: stream.read(1 << 22), b""):
                digest.update(chunk)
                out.write(chunk)
        if digest.hexdigest() != sha256:
            return None
        shutil.copystat(path, partial)
        os.replace(partial, target)
        return "copy"
    finally:
        partial.unlink(missing_ok=True)


def available_memory_gb() -> float | None:
    """MemAvailable of this host or container (lxcfs), None where /proc/meminfo is absent."""
    try:
        for line in Path('/proc/meminfo').read_text().splitlines():
            if line.startswith('MemAvailable:'):
                return int(line.split()[1]) / 1e6
    except OSError:
        pass
    return None


def _work(args):
    root, meta, tile, cut = args
    STEPS.clear()
    wall, cpu = time.monotonic(), time.process_time()
    result = M._worker((root, M.checkpoint(meta, cut), tile)) if cut else {}
    if result.get('state') in (None, 'migrated', 'kept'):
        result = M._worker((root, meta, tile))
    return dict(tile=tile, state=result.get('state'), error=result.get('error'), applied=result.get('applied'),
                wall=round(time.monotonic() - wall, 3), cpu=round(time.process_time() - cpu, 3),
                steps=dict(STEPS), survey=surveyed(tile), rebuild_revision=result.get('rebuild_revision'))


def ring(tile: str) -> int:
    return max(abs(int(v)) for v in tile.split('_')[1:])


def home(tile: str) -> tuple[int, int]:
    """The 500 m LAZ block holding the tile (two 250 m tiles a side)."""
    i, j = (int(v) for v in tile.split('_')[1:])
    return i // 2, j // 2


def pick(pending: list[str], last: tuple[int, int] | None) -> int:
    """Index of the next tile for a lane: in the innermost ring left, the one nearest the lane's last block
    (its own block first), so a lane walks round the ring and reuses the blocks it has decoded."""
    r0 = ring(pending[0])
    best, score = 0, None
    for k, tile in enumerate(pending):
        if ring(tile) != r0:
            break
        if last is None:
            return k
        b = home(tile)
        d = max(abs(b[0] - last[0]), abs(b[1] - last[1]))
        if score is None or d < score:
            best, score = k, d
            if d == 0:
                break
    return best


class Lane:
    """One worker process that keeps its decoded blocks between tiles."""

    def __init__(self, blocks: int):
        self.pool = ProcessPoolExecutor(max_workers=1, initializer=_init, initargs=(blocks,))
        self.future = None
        self.tile = None
        self.last = None
        self.at = (0, 0.0)


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
    parser.add_argument('--supply', action='append', default=[],
                        help='directory of pistepilvi LAZ blocks to link (or copy) in when they match the catalog')
    parser.add_argument('--no-survey-wait', action='store_true', help='migrate tiles whose survey blocks are missing')
    parser.add_argument('--survey-interval', type=float, default=600, help='seconds between looks for new blocks')
    parser.add_argument('--wait-hours', type=float, default=24, help='give up waiting for blocks after this long')
    parser.add_argument('--min-free-gb', type=float, default=12, help='no new tile while the farm disk has less free')
    parser.add_argument('--farm-url', default='http://127.0.0.1:7950', help='farm asked for missing spot rebuilds')
    parser.add_argument('--rebuild-interval', type=float, default=120, help='seconds between retries of a tile '
                        'waiting for its spot rebuild')
    parser.add_argument('--min-free-mem-gb', type=float, default=8,
                        help='the cap steps down while less memory than this is available')
    parser.add_argument('--blocks', type=int, default=9,
                        help='decoded LAZ blocks each lane keeps (0.3-0.6 GB each; a lane with 9 reached 6-8 GB RSS)')
    parser.add_argument('--checkpoint', help='also store the product after this step (e.g. wires@2), so a later '
                        'chain that changes only the steps after it resumes from there')
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
    pending = [t for t in tiles if t not in finished]
    pending.sort(key=lambda t: (ring(t), t))
    cores = os.cpu_count() or 1
    cap, started_at, next_adjust = 1, time.monotonic(), 0.0
    survey = None if args.no_survey_wait else Survey(args.supply, log_dir / 'supply.jsonl')
    waiting: list[str] = []
    rebuilding: dict[str, float] = {}  # tile -> when to try again (its spot rebuild is building on the farm)
    asked: set[str] = set()
    next_survey = time.monotonic() + args.survey_interval
    done_now = 0
    lanes = [Lane(args.blocks) for _ in range(max(1, args.max_jobs))]
    try:
        with journal.open('a') as log:
            while pending or waiting or rebuilding or any(lane.future for lane in lanes):
                now = time.monotonic()
                load = os.getloadavg()[0] / cores
                busy = [lane for lane in lanes if lane.future]
                if now >= next_adjust:
                    next_adjust = now + args.interval
                    free = shutil.disk_usage(root).free / 1e9
                    memory = available_memory_gb()
                    if free < args.min_free_gb:
                        cap = 0  # The steps' caches grow on the farm's disk: hold new tiles until space returns.
                    elif memory is not None and memory < args.min_free_mem_gb:
                        cap = max(0, min(cap, len(busy)) - 1)  # Lanes grow as they work: keep room for the services.
                    elif load > args.high:
                        cap = max(0, cap - 1)
                    elif load < args.low:
                        cap = min(len(lanes), cap + 1)
                    rate = done_now / max(1.0, now - started_at)
                    atomic_json(progress, dict(
                        revision=meta['revision'], generation=meta['generation'], pid=os.getpid(), cap=cap,
                        running=sorted(lane.tile for lane in busy), load_per_core=round(load, 2),
                        disk_free_gb=round(free, 1), memory_available_gb=memory and round(memory, 1),
                        waiting_for_survey=len(waiting), waiting_for_rebuild=sorted(rebuilding), survey_linked=survey.linked if survey else 0,
                        updated=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                        rate_tiles_per_hour=round(rate * 3600, 1),
                        eta_hours=round(len(pending) / rate / 3600, 1) if rate else None, **estimate(rows, tiles)))
                if waiting and now >= next_survey:  # Blocks may have arrived (lidarpc's downloads): look again.
                    next_survey = now + args.survey_interval
                    pending.extend(waiting)
                    pending.sort(key=lambda t: (ring(t), t))
                    waiting.clear()
                due = [t for t, at in rebuilding.items() if now >= at]
                if due:
                    for tile in due:
                        del rebuilding[tile]
                    pending.extend(due)
                    pending.sort(key=lambda t: (ring(t), t))
                for lane in lanes:
                    if lane.future or not pending or len([x for x in lanes if x.future]) >= cap:
                        continue
                    while pending:
                        tile = pending.pop(pick(pending, lane.last))
                        if survey is not None and survey.missing(tile):
                            waiting.append(tile)
                            continue
                        lane.future = lane.pool.submit(_work, (str(root), meta, tile, args.checkpoint))
                        lane.tile, lane.last, lane.at = tile, home(tile), (cap, round(load, 2))
                        break
                busy = [lane for lane in lanes if lane.future]
                if not busy:
                    if not pending and (waiting or rebuilding) and now - started_at > args.wait_hours * 3600:
                        break  # The rest stays waiting for survey blocks or rebuilds; a later pass picks them up.
                    time.sleep(min(args.interval, 5) if args.interval else 0.01)
                    continue
                done = wait([lane.future for lane in busy], timeout=5, return_when=FIRST_COMPLETED).done
                for lane in busy:
                    if lane.future not in done:
                        continue
                    try:
                        row = lane.future.result()
                    except Exception as error:  # A worker crash is reported; the pass continues.
                        row = dict(tile=lane.tile, state='failed', error=f'{type(error).__name__}: {error}'[-1000:])
                    row.update(cap=lane.at[0], load_per_core=lane.at[1],
                               at=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()))
                    if row['state'] == 'missing-rebuild':
                        # Ask the farm for the build off the scheduler thread (it may take up to its timeout).
                        threading.Thread(target=M.request_rebuild, args=(args.farm_url, row), daemon=True).start()
                        first = lane.tile not in asked
                        asked.add(lane.tile)
                        rebuilding[lane.tile] = now + args.rebuild_interval
                        lane.future = lane.tile = None
                        if not first:
                            continue  # Logged once; the retries stay out of the journal.
                    log.write(json.dumps(row) + '\n')
                    log.flush()
                    rows.append(row)
                    done_now += row['state'] == 'migrated'
                    lane.future = lane.tile = None
    finally:
        for lane in lanes:
            lane.pool.shutdown(wait=True, cancel_futures=True)
    atomic_json(progress, dict(revision=meta['revision'], generation=meta['generation'], finished=not waiting and not rebuilding,
                               waiting_for_survey=sorted(waiting), waiting_for_rebuild=sorted(rebuilding),
                               updated=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), **estimate(rows, tiles)))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
