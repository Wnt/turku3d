"""Raw-data prefetcher: fetch the source datasets ahead of the player so draft builds find them on disk.

The game reports the player's position and velocity (``GET /coarse/want``) once a second. The
prefetch area is a 500 m ring around the player plus a cone ahead along the direction of travel,
``FOOT_DEPTH`` deep on foot and growing linearly to ``CAR_DEPTH`` at the car's top speed. Every
missing 500 m block unit (``sources.Unit``) that intersects it is fetched into the shared raw cache:
all lidar first (it gates ground and collision), then ortho, then WFS, nearest block first within a
dataset. A tile whose build is waiting for data (``need``) jumps the queue.

Fetches run in child processes under ``nice``, at most ``DOWNLOADS`` at once, so they never hold up
a build; a unit already on disk is never fetched again. Every fetched unit is recorded in the
prefetch ledger next to the raw cache, which keeps the newest ``CACHE_CAP`` bytes of prefetched
inputs and prunes the least recently used ones (``Ledger.prune``). Files the prefetcher did not
fetch, and units under a listed playable tile's input area, are never pruned.
"""

from __future__ import annotations

import collections
import fcntl
import json
import math
import os
import shutil
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import shapely

from turku3d import config as C

from .sources import DATASETS, Sources, Unit, block_bbox

CAR_SPEED = 27.0  # m/s: game/src/physics/constants.ts topSpeed of the player car
FOOT_SPEED = 6.0  # m/s: sprint (game/src/actors/player.ts)
FOOT_DEPTH = 500.0  # cone depth on foot (m)
CAR_DEPTH = 1500.0  # cone depth at the car's top speed (m)
RING = 500.0  # radius around the player (m)
HALF_ANGLE = math.radians(30.0)  # cone half-angle around the direction of travel
MIN_SPEED = 1.0  # below this the direction is noise: ring only
DOWNLOADS = 2
NICE = 10
RETRY_SECONDS = 120.0
REPLAN_METRES = 25.0  # re-plan after this much movement (or every REPLAN_SECONDS)
REPLAN_SECONDS = 5.0
CACHE_CAP = 20 * 1024**3
TOUCH_FLUSH_SECONDS = 60.0


def depth(speed: float) -> float:
    k = min(1.0, max(0.0, (speed - FOOT_SPEED) / (CAR_SPEED - FOOT_SPEED)))
    return FOOT_DEPTH + k * (CAR_DEPTH - FOOT_DEPTH)


def area(x: float, z: float, vx: float = 0.0, vz: float = 0.0):
    """The prefetch area in engine x/z: the ring, plus the cone along (vx, vz) when moving."""
    ring = shapely.Point(x, z).buffer(RING, quad_segs=16)
    speed = math.hypot(vx, vz)
    if speed < MIN_SPEED:
        return ring
    heading, reach = math.atan2(vz, vx), depth(speed)
    arc = [
        (x + reach * math.cos(a), z + reach * math.sin(a))
        for a in (heading - HALF_ANGLE + k * HALF_ANGLE / 8 for k in range(17))
    ]
    return ring.union(shapely.Polygon([(x, z), *arc]))


def plan(x: float, z: float, vx: float, vz: float, covered=lambda block: True) -> list[Unit]:
    """Units over the prefetch area in fetch order: dataset rank, then distance to the block."""
    geom = area(x, z, vx, vz)
    bounds = C.BBoxLocal(*geom.bounds)
    here = shapely.Point(x, z)
    blocks = []
    for block in C.laz_tiles_for_bbox(bounds):
        b = block_bbox(block)
        box = shapely.box(b.xmin, b.zmin, b.xmax, b.zmax)
        if covered(block) and box.intersects(geom):
            blocks.append((box.distance(here), block))
    blocks.sort(key=lambda db: (db[0], db[1]))
    return [Unit(dataset, block) for dataset in DATASETS for _, block in blocks]


def playable_blocks() -> set[str]:
    """Blocks under the listed playable tiles' input areas (both the sandbox's and the data root's)."""
    ids = set()
    for root in {C.work_root(), C.data_root()}:
        path = root / C.PLAYABLE_TILES_FILE
        if path.is_file():
            ids.update(C.read_playable_tiles(path))
    return {
        b.name for tile in ids for b in C.laz_tiles_for_bbox(C.tile_input_area(tile).with_margin)
    }


class Ledger:
    """Prefetched units under the raw root: paths, bytes, fetch and last-use times (flock'ed JSON)."""

    def __init__(self, path: Path, cap: int = CACHE_CAP, protected=playable_blocks):
        self.path, self.cap, self.protected = Path(path), cap, protected
        self.raw = self.path.parent.parent
        self.touched: dict[str, float] = {}
        self.flushed = time.time()
        self.lock = threading.Lock()

    @contextmanager
    def _locked(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path.with_suffix(".lock"), "a+") as lf:
            fcntl.flock(lf, fcntl.LOCK_EX)
            try:
                yield self._read()
            finally:
                fcntl.flock(lf, fcntl.LOCK_UN)

    def _read(self) -> dict:
        try:
            return json.loads(self.path.read_text())
        except (OSError, ValueError):
            return {}

    def _write(self, entries: dict) -> None:
        for key, at in self.touched.items():
            if key in entries:
                entries[key]["used"] = max(entries[key]["used"], at)
        self.touched.clear()
        self.flushed = time.time()
        tmp = self.path.with_name(f".{self.path.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(entries, indent=0, sort_keys=True))
        tmp.replace(self.path)

    def entries(self) -> dict:
        return self._read()

    def add(self, unit: Unit, paths: list[Path], size: int, now: float | None = None) -> None:
        now = now or time.time()
        with self.lock, self._locked() as entries:
            entries[unit.key] = {
                "paths": [str(Path(p).relative_to(self.raw)) for p in paths],
                "bytes": int(size),
                "fetched": now,
                "used": now,
            }
            self._write(entries)

    def touch(self, units, now: float | None = None) -> None:
        """Mark units as used (planned again or read by a build); flushed at most once a minute."""
        now = now or time.time()
        with self.lock:
            for unit in units:
                self.touched[unit.key] = now
            if now - self.flushed < TOUCH_FLUSH_SECONDS:
                return
            with self._locked() as entries:
                self._write(entries)

    def total(self) -> int:
        return sum(e["bytes"] for e in self._read().values())

    def prune(self, keep=()) -> list[str]:
        """Delete least-recently-used prefetched units until the ledger fits ``cap``.

        ``keep`` (unit keys being fetched or planned) and protected blocks are never pruned."""
        from turku3d.fetch.common import Registry

        pruned = []
        with self.lock, self._locked() as entries:
            for key, at in self.touched.items():
                if key in entries:
                    entries[key]["used"] = max(entries[key]["used"], at)
            total = sum(e["bytes"] for e in entries.values())
            if total <= self.cap:
                return []
            protected = self.protected()
            for key, entry in sorted(entries.items(), key=lambda kv: (kv[1]["used"], kv[0])):
                if total <= self.cap:
                    break
                if key in keep or key.split("/", 1)[1] in protected:
                    continue
                paths = [self.raw / p for p in entry["paths"]]
                for path in paths:
                    if path.is_dir():
                        shutil.rmtree(path)
                    else:
                        path.unlink(missing_ok=True)
                kind = entry["paths"][0].split("/", 1)[0]
                Registry(self.raw / kind).forget(paths)
                del entries[key]
                total -= entry["bytes"]
                pruned.append(key)
            self._write(entries)
        return pruned


def default_ledger() -> Ledger:
    return Ledger(C.raw_dir("prefetch") / "ledger.json")


def fetch_child(unit: Unit) -> dict:
    """Fetch one unit in a niced child process; returns its {bytes, seconds, paths} report."""
    command = ["nice", "-n", str(NICE), sys.executable, "-m", "turku3d.coarse", "fetch", unit.key]
    proc = subprocess.run(command, capture_output=True, text=True)
    result = _last_json(proc.stdout) if proc.returncode == 0 else None
    if result is None:
        tail = (proc.stderr or proc.stdout).strip().splitlines()[-1:] or ["fetch failed"]
        raise RuntimeError(tail[0][:300])
    return result


class Prefetcher:
    """Plans units from the player's motion and fetches the missing ones on DOWNLOADS threads."""

    def __init__(
        self,
        sources: Sources | None = None,
        fetcher=fetch_child,
        ledger: Ledger | None = None,
        downloads: int = DOWNLOADS,
        on_landed=None,
    ):
        self.sources = sources or Sources()
        self.fetcher, self.on_landed = fetcher, on_landed
        self.ledger = ledger or default_ledger()
        self.lock = threading.Condition()
        self.queue: list[Unit] = []  # planned, missing, in fetch order
        self.urgent: list[Unit] = []  # needed by a build that is waiting
        self.fetching: dict[Unit, float] = {}
        self.failed: dict[Unit, tuple[str, float]] = {}
        self.stats = {d: {"units": 0, "bytes": 0, "seconds": 0.0} for d in DATASETS}
        self.recent = collections.deque(maxlen=40)
        self.pruned: list[str] = []
        self.motion: tuple[float, float, float, float] | None = None
        self.planned_at: tuple[float, float, float] | None = None  # x, z, time of the last plan
        self.planned: list[Unit] = []
        self.stopped = False
        self.threads = [
            threading.Thread(target=self._work, name=f"coarse-prefetch-{k}", daemon=True)
            for k in range(downloads)
        ]
        for t in self.threads:
            t.start()

    def _wanted(self, unit: Unit, now: float) -> bool:
        if unit in self.fetching:
            return False
        error = self.failed.get(unit)
        return not (error and now - error[1] < RETRY_SECONDS)

    def want(self, x: float, z: float, vx: float = 0.0, vz: float = 0.0) -> dict:
        """The player's position and velocity (engine frame); re-plans when it moved or aged."""
        now = time.time()
        with self.lock:
            self.motion = (x, z, vx, vz)
            last = self.planned_at
            fresh = last and math.hypot(x - last[0], z - last[1]) < REPLAN_METRES
            if fresh and now - last[2] < REPLAN_SECONDS:
                return self.brief()
            self.planned_at = (x, z, now)
        units = plan(x, z, vx, vz, self.sources.covered)
        missing = [u for u in units if not self.sources.unit_present(u)]
        self.ledger.touch([u for u in units if u not in missing])
        with self.lock:
            self.planned = units
            self.queue = [u for u in missing if self._wanted(u, now)]
            self.lock.notify_all()
            return self.brief()

    def need(self, units: list[Unit]) -> None:
        """Units a waiting build needs: fetched before the planned ones."""
        now = time.time()
        with self.lock:
            for unit in units:
                if unit not in self.urgent and self._wanted(unit, now):
                    self.urgent.append(unit)
            self.urgent.sort(key=lambda u: DATASETS.index(u.dataset))
            self.lock.notify_all()

    def _next(self) -> Unit | None:
        now = time.time()
        for source in (self.urgent, self.queue):
            while source:
                unit = source.pop(0)
                if self._wanted(unit, now):
                    return unit
        return None

    def _work(self):
        while True:
            with self.lock:
                unit = self._next()
                while unit is None and not self.stopped:
                    self.lock.wait()
                    unit = self._next()
                if self.stopped:
                    return
                self.fetching[unit] = time.time()
            if self.sources.unit_present(unit):  # landed meanwhile (another sandbox, a build list)
                with self.lock:
                    del self.fetching[unit]
                continue
            started = time.perf_counter()
            try:
                result, error = self.fetcher(unit), None
            except Exception as e:  # a network or server error: retried after RETRY_SECONDS
                result, error = None, str(e)[:300] or type(e).__name__
            seconds = time.perf_counter() - started
            if result is None:
                with self.lock:
                    del self.fetching[unit]
                    self.failed[unit] = (error, time.time())
                continue
            self.sources.invalidate()
            self.ledger.add(unit, [Path(p) for p in result["paths"]], result["bytes"])
            with self.lock:
                del self.fetching[unit]
                self.failed.pop(unit, None)
                s = self.stats[unit.dataset]
                s["units"] += 1
                s["bytes"] += result["bytes"]
                s["seconds"] = round(s["seconds"] + seconds, 3)
                self.recent.append(
                    dict(
                        unit=unit.key,
                        bytes=result["bytes"],
                        seconds=round(seconds, 3),
                        at=time.time(),
                    )
                )
                keep = {u.key for u in (*self.fetching, *self.planned, *self.urgent)}
            pruned = self.ledger.prune(keep)
            if pruned:
                with self.lock:
                    self.pruned.extend(pruned)
            if self.on_landed:
                try:
                    self.on_landed(unit)
                except Exception as e:  # never lose a download thread to a callback
                    print(f"coarse prefetch {unit.key}: landing callback failed: {e}", flush=True)

    def brief(self) -> dict:
        return {
            "queued": len(self.urgent) + len(self.queue),
            "fetching": sorted(u.key for u in self.fetching),
        }

    def status(self) -> dict:
        with self.lock:
            return {
                "motion": self.motion,
                "planned": len(self.planned),
                "urgent": [u.key for u in self.urgent],
                "queue": [u.key for u in self.queue[:20]],
                "queued": len(self.urgent) + len(self.queue),
                "fetching": {u.key: round(time.time() - t, 1) for u, t in self.fetching.items()},
                "failed": {u.key: e for u, (e, _) in self.failed.items()},
                "datasets": {d: dict(s) for d, s in self.stats.items()},
                "recent": list(self.recent),
                "cache": {
                    "bytes": self.ledger.total(),
                    "cap": self.ledger.cap,
                    "pruned": list(self.pruned[-20:]),
                },
            }

    def stop(self, timeout: float = 10.0):
        """Stop taking units and wait for the downloads in flight (up to ``timeout`` each)."""
        with self.lock:
            self.stopped = True
            self.lock.notify_all()
        for thread in self.threads:
            thread.join(timeout)


def _last_json(text: str) -> dict | None:
    for line in reversed(text.strip().splitlines()):
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict):
            return value
    return None
