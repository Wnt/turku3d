"""Localhost companion service: builds coarse tiles on first request and serves them afterwards.

    GET /coarse/status                      from memory, in milliseconds: the builds running, the
                                            queue length and its first STATUS_ITEMS tiles with their
                                            ETA (s), how many tiles this process built and the timings
                                            of the latest, recent failures and tiles waiting for data,
                                            the build counter `version`, the prefetcher's state;
                                            the service's start time and the repository files it loaded
                                            (`started`, `modules`: the localhost game restarts it when
                                            one of them lands, tools/dev/localhost/coarse.mjs)
    POST /coarse/drain                      start no further builds (running ones finish); the
                                            supervisor restarts the service once `building` is empty
    GET /coarse/want?x=&z=&vx=&vz=          the player's engine position and velocity (m, m/s), once
                                            a second: re-ranks the queue (``rank``), drops the tiles
                                            the player no longer needs, drives the raw-data prefetcher
                                            (prefetch.py), answers {queued, fetching, version}
    GET /coarse/<tile>/manifest.json?d=<m>  200 when built; otherwise 202 {"state": "queued",
                                            "position", "eta"} or "building" with "eta", or 202
                                            {"state": "deferred"} while the tile is outside the ring
                                            and the cone (``rank``), or 202 {"state": "pending-data",
                                            "missing": [units]} while its inputs are being fetched
    GET /coarse/<tile>/vis.glb | roads.json | col/<c>.col and /coarse/shared/<sha>.ktx2   built files

Tiles not re-requested for STALE_SECONDS leave the queue, and so do the tiles ``rank`` drops at each
position report (the player turned or moved on); a running build always finishes. The game asks for
the cells ahead of a moving player as well (prefetch), so up to WORKERS builds run at once, each on
its own persistent worker process (``Workers``: the interpreter, imports and the node packager are
started once, not per tile), so a slow or failing build never blocks requests or the other builds.
A build never downloads: a tile whose inputs are not on disk waits as pending-data, its units jump
the raw-data prefetch queue, and it is queued again as soon as they land.
A failed tile (outside every keyless source, or a network error) answers 404 with the reason and is
retried after RETRY_SECONDS.
Built tiles live under $TURKU3D_WORK/coarse/<tile>/ and are rebuilt when the producer code changes.
"""

from __future__ import annotations

import argparse
import heapq
import json
import math
import os
import re
import signal
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from turku3d import config as C
from turku3d.facade_kit import parameter_hash

from .packaging import current_code
from .collision_key import current_code as current_collision_code
from .collision import fragment as collision_fragment
from .collision import current as collision_current
from .demand import Demand, write_snapshot
from .prefetch import Prefetcher
from .request_journal import RequestJournal, complete_tile, request_tile
from .sources import NoCoverage, Sources, Unit
from .upgrade_queue import next_job, next_upgrade
from .worker_pool import Workers

REPO = Path(__file__).resolve().parents[3]
TILE = re.compile(r"^t0_-?\d+_-?\d+$")
FILE = re.compile(
    r"^(vis(?:\.[0-9a-f]{64})?\.glb|(?:manifest|collision)\.json|roads(?:\.[0-9a-f]{64})?\.json|col/c_-?\d+_-?\d+(?:\.[0-9a-f]{64})?\.col)$"
)
SHARED = re.compile(r"^[0-9a-f]{64}\.(?:ktx2|glb)$")
STALE_SECONDS = 10.0  # the game re-polls wanted tiles every 2 s; others leave the queue
PENDING_EXIT = 3  # python -m turku3d.coarse build: inputs missing, {"pending": [units]} on stdout
RETRY_SECONDS = 120.0  # a failed tile (no coverage, network error) is retried after this
MIN_SPEED = 1.0  # m/s: slower than this the heading is noise and there is no cone
CONE_SECONDS = 30.0  # cone depth in seconds of travel (the game's PREFETCH_SECONDS), plus a tile
QUEUE_MAX = 12  # queued tiles at most with a known player position; the ring always stays
STATUS_ITEMS = 12  # queue entries, failures, waiting tiles and timings /coarse/status lists
ETA_BUILDS = 8  # recent builds averaged for the queue's time estimates
STALE_REBUILDS = 3  # a build stale on arrival is redone at most this often in a row
WORKERS = 1  # Finish the imminent crossing before spending CPU on neighbours or upgrades.
MIME = {
    ".json": "application/json",
    ".glb": "model/gltf-binary",
    ".col": "application/octet-stream",
    ".ktx2": "image/ktx2",
}


def rank(tile: str, distance: float, motion) -> tuple[int, float] | None:
    """Build priority of a queued tile for the player's latest position and velocity (``motion``,
    engine x, z, vx, vz): (class, metric), lower first, or None when the player no longer needs it.

    The current tile comes first, then tiles intersected by the swept velocity ray,
    ordered by entry time. Ring and cone neighbours follow by travel distance and
    lateral distance. Stationary players retain nearest-first ring ordering.
    Without a position report, use the distance reported by the game.
    """
    if motion is None:
        return 0, distance
    x, z, vx, vz = motion
    t = C.VisualTile.parse(tile)
    size = t.size
    x0, _, z0 = t.origin
    near = math.hypot(max(x0 - x, 0.0, x - x0 - size), max(z0 - z, 0.0, z - z0 - size))
    ci, cj = math.floor(x / size), math.floor(z / size)
    ring = abs(t.i - ci) <= 1 and abs(t.j - cj) <= 1
    if t.i == ci and t.j == cj:
        return 0, -1.0
    speed = math.hypot(vx, vz)
    if speed >= MIN_SPEED:
        # Ray/slab intersection uses the footprint, so a diagonal corner and a
        # tile behind the car cannot outrank the next actual crossing.
        enter, leave = 0.0, CONE_SECONDS + size / speed
        for position, velocity, lo in ((x, vx, x0), (z, vz, z0)):
            if abs(velocity) < 1e-9:
                if not lo <= position < lo + size:
                    leave = -1.0
                    break
            else:
                aa, bb = sorted(((lo - position) / velocity, (lo + size - position) / velocity))
                enter, leave = max(enter, aa), min(leave, bb)
        if leave > enter:
            return 0, enter
        dx, dz = x0 + size / 2 - x, z0 + size / 2 - z
        along = (dx * vx + dz * vz) / speed
        lateral = abs(dx * vz - dz * vx) / speed
        if ring or (-size / 2 <= along <= speed * CONE_SECONDS + size and lateral <= 1.5 * size):
            return 1, max(along, 0.0) + 2 * lateral + (size if along < 0 else 0)
    elif ring:
        return 0, near
    return None


def immutable_payload(name: str) -> bool:
    """An old fragment may finish loading during a rebuild; content-addressed bytes stay valid."""
    return bool(FILE.fullmatch(name) and re.search(r"\.[0-9a-f]{64}\.(?:glb|json|col)$", name))


class Builder:
    """Queue of tile ids drained by `workers` threads, in ``rank`` order (the ring first)."""

    def __init__(
        self,
        root: Path,
        run=None,
        code: str | None = None,
        workers: int = WORKERS,
        inputs=None,
        on_missing=None,
        input_key=None,
        structural_input_key=None,
        enrichment_inputs=None,
    ):
        """``inputs(tile)`` lists the unit keys a tile still needs (raises NoCoverage outside the
        sources); ``on_missing(units)`` hands them to the prefetcher. Without ``inputs`` every
        tile is assumed to have its data. ``input_key(tile)`` identifies the raw files a build
        would read now (None: unknown); a draft built from other files is stale and rebuilt.
        ``run(tile, out)`` builds one tile and returns a CompletedProcess-like result (exit code 0,
        PENDING_EXIT or another failure, stdout, stderr); default: persistent ``Workers``."""
        self.input_key = input_key or (lambda tile: None)
        self.structural_input_key = structural_input_key
        self.root = root
        self.inputs = inputs or (lambda tile: [])
        self.enrichment_inputs = enrichment_inputs or self.inputs
        self.on_missing = on_missing or (lambda units: None)
        # Re-hashed per check, so a code edit while the service runs rebuilds each tile once.
        self.code = (lambda: code) if code else current_code
        self.collision_code = current_collision_code
        self.two_pass = run is None
        self.run = run or Workers()
        self.demand = Demand()
        self.cancelled = set()
        self.upgrade_queue: set[str] = set()
        self.upgrading: set[str] = set()
        self.upgrade_requested: dict[str, float] = {}
        self.lock = threading.Condition()
        self.priority: dict[str, float] = {}  # queued tile -> distance the game reported
        self.requested: dict[str, float] = {}
        self.motion: tuple[float, float, float, float] | None = None  # player x, z, vx, vz
        self.version = 0  # counts finished builds; the game re-polls its tiles when it changes
        self.built_count = 0  # tiles this process built
        self.servable: frozenset[str] = frozenset()  # immutable snapshot, read without the lock
        self.recent: list[float] = []  # first-pass service seconds for deadline estimates
        self.full_recent: list[float] = []
        self.stale: dict[str, int] = {}  # tile -> builds in a row that were stale on arrival
        self.building: dict[str, float] = {}
        self.failed: dict[str, tuple[str, float]] = {}
        self.failure_codes: dict[str, tuple] = {}
        self.failure_count = 0
        self.pending: dict[str, tuple[list[str], float]] = {}  # tile -> (missing units, distance)
        self.timings: dict[str, dict] = {}  # the latest STATUS_ITEMS builds, oldest first
        self.code_seen = self.code()  # producer hash of the latest check, for /coarse/status
        self.stopped = False
        self.draining = os.environ.get("TURKU3D_DRAFT_STANDBY") == "1"
        self.workers = workers
        self.journal = RequestJournal(root)
        replay = [(t, r) for t, r in self.journal.rows.items() if time.time() - r["at"] < 30]
        self.replay_until = {tile: time.time() + STALE_SECONDS for tile, _ in replay}
        for tile, row in sorted(replay, key=lambda item: (item[1]["distance"], item[0])):
            self.request(tile, row["distance"])
        self.threads = [
            threading.Thread(target=self._run, name=f"coarse-builder-{k}", daemon=True)
            for k in range(workers)
        ]
        if workers:
            self.threads.append(threading.Thread(target=self._maintain, name="coarse-demand", daemon=True))
        for thread in self.threads:
            thread.start()

    def _maintain(self):
        while True:
            with self.lock:
                if self.stopped:
                    return
                self.demand.prune(self)
                snapshot = self.status()
                snapshot["at"] = time.time()
                try:
                    write_snapshot(self.root, snapshot)
                except OSError as error:
                    # Telemetry must not stop demand expiry/cancellation when storage is unavailable.
                    print(f"coarse scheduler snapshot: {error}", file=sys.stderr, flush=True)
                self.lock.notify_all()
            time.sleep(1)

    def job_rank(self, tile, distance=0):
        lease = self.demand.rank(tile)
        if lease is not None:
            return lease
        if self.demand.clients:
            return 3, distance
        return rank(tile, distance, self.motion)

    def manifest(self, tile: str) -> Path:
        return self.root / tile / "manifest.json"

    def built(self, tile: str, code: str | None = None) -> bool:
        """``code``: the producer hash when the caller checks many tiles (hashed once, not per tile)."""
        path = self.manifest(tile)
        if not path.exists():
            return False
        try:
            m = json.loads(path.read_text())
        except (OSError, ValueError):
            return False
        self.code_seen = code = code or self.code()
        if m.get("code") != code:
            return False
        if "building_keys" in m and m.get("facade_params") != parameter_hash(m["building_keys"]):
            return False
        try:
            key = (self.structural_input_key or self.input_key) if m.get("pass_kind") == "first" else self.input_key
            now = key(tile)
        except NoCoverage:
            return True
        return now is None or m.get("inputs") == now

    def failure_key(self, tile):
        try:
            return self.code(), self.input_key(tile)
        except NoCoverage:
            return self.code(), None

    def cached(self, tile: str) -> bool:
        return complete_tile(self.root, tile, FILE)

    def request(self, tile: str, distance: float) -> dict:
        return request_tile(self, tile, distance)

    def _request(self, tile: str, distance: float) -> dict:
        """Queue or re-prioritise a tile; returns its state."""
        with self.lock:
            self.requested[tile] = time.time()
            # A worker may have written its manifest but not committed its completion yet.
            # Publish only after counters, timings and servability are updated under this lock.
            if tile in self.building:
                return (
                    {"state": "built"}
                    if tile in self.servable and self.built(tile)
                    else self._building(tile)
                )
            if tile in self.priority:
                # Already admitted from complete sources. The worker checks fresh
                # inputs when it starts; polling must not repeat every disk lookup.
                self.priority[tile] = distance
                order = self._prune()
                self.lock.notify_all()
                return ({"state": "queued", "position": order.index(tile),
                         "eta": self.eta()[tile]} if tile in self.priority
                        else {"state": "deferred"})
            if self.built(tile):
                self.requested[tile] = time.time()
                if self.two_pass and tile not in self.upgrading and self.manifest(tile).exists():
                    m = json.loads(self.manifest(tile).read_text())
                    if (
                        m.get("pass_kind") == "first"
                        and time.time() - self.failed.get(tile, ("", 0))[1] >= RETRY_SECONDS
                    ):
                        self.upgrade_queue.add(tile)
                        self.upgrade_requested[tile] = time.time()
                        self.lock.notify_all()
                self.pending.pop(tile, None)
                self.servable = self.servable | {tile}
                return {"state": "built"}
            # A structural job releases its worker before any visual packaging.
            # Visual polling must enqueue enrichment, never repeat the first pass.
            if self.two_pass and collision_current(self, tile) is not None:
                if tile in self.pending and (missing := self.enrichment_inputs(tile)):
                    self.pending[tile] = (missing, distance)
                    return {"state": "pending-data", "missing": missing}
                self.upgrade_queue.add(tile)
                self.upgrade_requested[tile] = time.time()
                self.pending.pop(tile, None)
                self.priority.pop(tile, None)
                self.lock.notify_all()
                return {"state": "queued"}
            self.servable = self.servable - {tile}
            error, at = self.failed.get(tile, (None, 0.0))
            current_code = self.failure_key(tile)
            if (
                error
                and self.failure_codes.get(tile, current_code) == current_code
                and time.time() - at < RETRY_SECONDS
            ):
                return {"state": "failed", "error": error}
            self.failed.pop(tile, None)
            self.failure_codes.pop(tile, None)
        try:
            missing = self.inputs(tile)
        except NoCoverage as e:
            with self.lock:
                self.failed[tile] = (str(e), time.time())
                self.failure_codes[tile] = self.failure_key(tile)
            return {"state": "failed", "error": str(e)}
        with self.lock:
            self.requested[tile] = time.time()
            if missing:
                self.pending[tile] = (missing, distance)
            else:
                self.pending.pop(tile, None)
        if missing:
            self.on_missing(missing)
            return {"state": "pending-data", "missing": missing}
        with self.lock:
            if tile in self.building:  # a worker took it while the inputs were checked
                return self._building(tile)
            self.priority[tile] = distance
            order = self._prune()
            if tile not in self.priority:
                return {"state": "deferred"}
            self.lock.notify_all()
            return {"state": "queued", "position": order.index(tile), "eta": self.eta()[tile]}

    def steer(self, x: float, z: float, vx: float, vz: float) -> None:
        """The player's position and velocity (from /coarse/want, once a second) re-rank the queue
        and drop the tiles the player no longer needs; running builds are not interrupted."""
        with self.lock:
            self.motion = (x, z, vx, vz)
            self._prune()

    def _prune(self) -> list[str]:
        """Drops the queued tiles ``rank`` rejects and, with a known player position, those beyond
        QUEUE_MAX in build order (the ring always stays); returns the build order (lock held)."""
        ranked = sorted(
            (r, t) for t, d in self.priority.items() if (r := self.job_rank(t, d)) is not None
        )
        keep = [
            t
            for k, ((cls, _), t) in enumerate(ranked)
            if self.motion is None or k < QUEUE_MAX or self._in_ring(t)
        ]
        for tile in set(self.priority) - set(keep):
            del self.priority[tile]
        return keep

    def _in_ring(self, tile: str) -> bool:
        if self.motion is None:
            return False
        t = C.VisualTile.parse(tile)
        return (
            abs(t.i - math.floor(self.motion[0] / t.size)) <= 1
            and abs(t.j - math.floor(self.motion[1] / t.size)) <= 1
        )

    def order(self) -> list[str]:
        """Queued tiles in build order (call with the lock held)."""
        ranked = ((self.job_rank(t, d), t) for t, d in self.priority.items())
        return [t for r, t in sorted((r, t) for r, t in ranked if r is not None)]

    def eta(self) -> dict[str, float]:
        """Seconds until each queued or building tile is built, from the mean of the recent builds
        and the free workers (call with the lock held)."""
        mean = sum(self.recent) / len(self.recent) if self.recent else 8.0
        now = time.time()
        out = {
            t: max(0.5, mean - (now - since))
            for t, since in self.building.items()
            if t not in self.upgrading
        }
        full_mean = sum(self.full_recent) / len(self.full_recent) if self.full_recent else 30.0
        occupied = [max(0.5, full_mean - (now - self.building[t])) for t in self.upgrading]
        free = sorted([*out.values(), *occupied, *[0.0] * max(0, self.workers - len(self.building))])
        for tile in self.order():
            start = heapq.heappop(free) if free else 0.0
            out[tile] = start + mean
            heapq.heappush(free, out[tile])
        full_mean = sum(self.full_recent) / len(self.full_recent) if self.full_recent else 30.0
        out.update({t: max(0.5, full_mean - (now - self.building[t])) for t in self.upgrading})
        return {t: round(v, 1) for t, v in out.items()}

    def _building(self, tile: str) -> dict:
        return {"state": "building", "since": self.building[tile], "eta": self.eta()[tile]}

    def _next(self) -> tuple[str, float] | None:
        if self.draining:
            return None
        now = time.time()
        for tile in self.order():
            distance = self.priority.pop(tile)
            if tile in self.building:
                continue
            if now - self.requested.get(tile, 0.0) <= STALE_SECONDS or now < self.replay_until.get(
                tile, 0.0
            ):
                return tile, distance
            self.requested.pop(tile, None)
        return None

    def _next_upgrade(self):
        return next_upgrade(self)

    def _run(self, upgrade_only=False):
        while True:
            with self.lock:
                self.demand.prune(self)
                selected = next_job(self, upgrade_only)
                while selected is None and not self.stopped:
                    self.lock.wait(timeout=1)
                    selected = next_job(self, upgrade_only)
                if self.stopped:
                    return
                (tile, distance), upgrade = selected
                self.building[tile] = time.time()
            started = time.perf_counter()
            out = self.root / tile
            proc = (
                self.run(tile, out, first_pass=not upgrade)
                if self.two_pass
                else self.run(tile, out)
            )
            seconds = time.perf_counter() - started
            with self.lock:
                del self.building[tile]
                self.upgrading.discard(tile)
                self.version += 1
                outcome = "FAILED"
                if tile in self.cancelled:
                    self.cancelled.discard(tile)
                    outcome = "cancelled"
                    self.failed.pop(tile, None)
                elif proc.returncode == PENDING_EXIT:
                    units = json.loads(proc.stdout.strip().splitlines()[-1])["pending"]
                    self.pending[tile] = (units, distance)
                    outcome = "pending"
                elif proc.returncode == 0 and (self.built(tile) or
                        (self.two_pass and collision_current(self, tile) is not None)):
                    self.pending.pop(tile, None)
                    self.failed.pop(tile, None)
                    self.failure_codes.pop(tile, None)
                    visual_ready = self.built(tile)
                    m = (json.loads(self.manifest(tile).read_text()) if visual_ready
                         else collision_current(self, tile))
                    previous = self.timings.pop(tile, {})
                    self.timings[tile] = {"service": round(seconds, 3), **m.get("seconds", {})}
                    first = (
                        previous.get("first_pass")
                        if upgrade
                        else {
                            "service": round(seconds, 3),
                            "total": m.get("seconds", {}).get("total"),
                        }
                    )
                    if first:
                        self.timings[tile]["first_pass"] = first
                    for old in list(self.timings)[:-STATUS_ITEMS]:
                        del self.timings[old]
                    if upgrade:
                        self.full_recent = [*self.full_recent[-(ETA_BUILDS - 1) :], seconds]
                    else:
                        self.recent = [*self.recent[-(ETA_BUILDS - 1) :], seconds]
                    self.stale.pop(tile, None)
                    self.built_count += 1
                    if not self.two_pass or m.get("pass_kind") != "first":
                        self.journal.discard(tile)
                        self.replay_until.pop(tile, None)
                        self.upgrade_requested.pop(tile, None)
                    if visual_ready:
                        self.servable = self.servable | {tile}
                    outcome = "built"
                    if self.two_pass and m.get("pass_kind") == "first":
                        self.upgrade_queue.add(tile)
                        self.requested[tile] = time.time()
                        self.lock.notify_all()
                elif proc.returncode == 0 and self.stale.get(tile, 0) < STALE_REBUILDS:
                    # Raw data landed during the build, so the draft is stale already: again.
                    self.stale[tile] = self.stale.get(tile, 0) + 1
                    self.priority[tile] = distance
                    self._prune()
                    self.lock.notify_all()
                    outcome = "stale"
                else:
                    tail = (proc.stderr or proc.stdout).strip().splitlines()[-1:] or [
                        "build failed"
                    ]
                    self.failure_count += 1
                    self.failed[tile] = (tail[0][:500], time.time())
                    self.failure_codes[tile] = self.failure_key(tile)
                    if upgrade:
                        self.upgrade_queue.add(tile)
                if (
                    tile not in self.pending
                    and outcome != "stale"
                    and tile not in self.upgrade_queue
                ):
                    self.requested.pop(tile, None)
                self.lock.notify_all()
                print(json.dumps({"job": tile, "at": time.time(), "seconds": round(seconds, 3),
                                  "pass": "full" if upgrade else "first", "outcome": outcome}), flush=True)
                print(
                    f"coarse {tile}: {outcome} ({'upgrade' if upgrade else 'first' if self.two_pass else 'full'}) in {seconds:.1f} s",
                    flush=True,
                )
            if outcome == "FAILED":
                detail = (proc.stderr or proc.stdout).strip()
                print(
                    f"coarse {tile} failure detail: {detail[-4000:]}", file=sys.stderr, flush=True
                )
            if tile in self.pending:
                self.on_missing(self.pending[tile][0])

    def landed(self) -> None:
        """Data landed: re-request the waiting tiles the game still asks for."""
        with self.lock:
            waiting = [
                (tile, distance)
                for tile, (_, distance) in self.pending.items()
                if (
                    time.time() - self.requested.get(tile, 0.0) <= STALE_SECONDS
                    or time.time() < self.replay_until.get(tile, 0.0)
                )
            ]
        for tile, distance in waiting:
            self.request(tile, distance)

    def status(self) -> dict:
        """The builders' in-memory state, small: no manifest, input or source file is read, so it
        answers in milliseconds however loaded the host and however many drafts are on disk."""
        with self.lock:
            order = self.order()
            eta = self.eta()
            shown = [*sorted(self.building), *order[:STATUS_ITEMS]]
            return {
                "state": "building" if self.building else "idle",
                "building": sorted(self.building),
                "upgrading": sorted(self.upgrading),
                "foreground": sum(self.demand.rank(t) is not None
                                  for t in set(order) | set(self.building) | set(self.pending)),
                "queued": len(order),
                "queue": order[:STATUS_ITEMS],
                "eta": {tile: eta[tile] for tile in shown},
                "version": self.version,
                "built": self.built_count,
                "failure_count": self.failure_count,
                "failed": {
                    tile: error for tile, (error, _) in list(self.failed.items())[-STATUS_ITEMS:]
                },
                "pending": {
                    tile: units for tile, (units, _) in list(self.pending.items())[-STATUS_ITEMS:]
                },
                "timings": dict(self.timings),
                "code": self.code_seen,
                "draining": self.draining,
            }

    def drain(self) -> None:
        """No further builds start; the running ones finish (the service is about to restart)."""
        with self.lock:
            self.draining = True

    def stop(self):
        with self.lock:
            self.stopped = True
            self.lock.notify_all()
        if isinstance(self.run, Workers):
            self.run.close()


_LOADED: tuple[int, list[str]] = (-1, [])


def loaded_files() -> list[str]:
    """Repository files of the modules this process has imported (repo-relative, sorted); resolved
    again only when the process imported another module."""
    global _LOADED
    if _LOADED[0] == len(sys.modules):
        return _LOADED[1]
    files = set()
    for module in list(sys.modules.values()):
        path = getattr(module, "__file__", None)
        if not path:
            continue
        resolved = Path(path).resolve()
        if resolved.is_relative_to(REPO):
            files.add(resolved.relative_to(REPO).as_posix())
    _LOADED = (len(sys.modules), sorted(files))
    return _LOADED[1]


def handler(builder: Builder, prefetcher: Prefetcher | None = None):
    started = time.time()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _send(
            self, status: int, body: bytes, kind: str = "application/json", cache: str = "no-cache"
        ):
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", cache)
            self.send_header("Cross-Origin-Resource-Policy", "cross-origin")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, status: int, value: dict):
            self._send(status, (json.dumps(value) + "\n").encode())

        def do_HEAD(self):
            self.do_GET()

        def do_POST(self):
            if urlparse(self.path).path != "/coarse/drain":
                return self._json(404, {"error": "not found"})
            builder.drain()
            return self._json(200, {"draining": True, "building": builder.status()["building"]})

        def do_GET(self):
            url = urlparse(self.path)
            parts = url.path.removeprefix("/coarse/").split("/", 1)
            if url.path == "/coarse/navigation.json":
                from .navigation import navigation
                try:
                    return self._send(200, navigation(builder.root), cache="public, max-age=300")
                except Exception as error:
                    return self._json(503, {"error": f"navigation: {error}"})
            if url.path == "/coarse/status":
                status = builder.status() | {"started": started, "modules": loaded_files()}
                if prefetcher:
                    status["prefetch"] = prefetcher.status()
                return self._json(200, status)
            if url.path == "/coarse/want":
                q = parse_qs(url.query)
                try:
                    x, z, vx, vz = (float(q.get(k, ["0"])[0]) for k in ("x", "z", "vx", "vz"))
                except ValueError:
                    return self._json(400, {"error": "x, z, vx, vz are numbers"})
                if not all(map(math.isfinite, (x, z, vx, vz))):
                    return self._json(400, {"error": "x, z, vx, vz are finite"})
                builder.steer(x, z, vx, vz)
                reply = prefetcher.want(x, z, vx, vz) if prefetcher else {}
                with builder.lock:
                    work = {"queued": len(builder.priority), "building": sorted(builder.building)}
                return self._json(200, {**reply, **work, "version": builder.version})
            if len(parts) == 2 and parts[0] == "shared" and SHARED.match(parts[1]):
                path = builder.root / "shared" / parts[1]
                if path.exists():
                    return self._send(
                        200, path.read_bytes(), MIME[path.suffix], "public, max-age=3600"
                    )
                return self._json(404, {"error": "not found"})
            if len(parts) != 2 or not TILE.match(parts[0]) or not FILE.match(parts[1]):
                return self._json(404, {"error": "not found"})
            tile, name = parts
            if name in ("manifest.json", "collision.json"):
                d = parse_qs(url.query).get("d", ["inf"])[0]
                try:
                    distance = float(d)
                except ValueError:
                    distance = float("inf")
                state = builder.request(tile, distance)
                if name == "collision.json":
                    fragment = collision_fragment(builder, tile)
                    if fragment is not None:
                        return self._json(200, fragment)
                if state["state"] == "failed":
                    return self._json(404, state)
                if state["state"] != "built":
                    return self._json(202, state)
                if state.get("stale"):
                    return self._json(
                        200,
                        {
                            **json.loads(builder.manifest(tile).read_text()),
                            "stale": True,
                            "failure": state["failure"],
                        },
                    )
            elif tile not in builder.servable and not immutable_payload(name):
                return self._json(404, {"error": f"{tile} is not built"})
            path = builder.root / tile / name
            if not path.exists():
                return self._json(404, {"error": "not found"})
            return self._send(200, path.read_bytes(), MIME[path.suffix], "no-cache")

    return Handler


class Server(ThreadingHTTPServer):
    request_queue_size = 128  # the game opens many parallel requests; the default 5 drops some
    daemon_threads = True


def serve(
    port: int,
    root: Path,
    host: str = "127.0.0.1",
    builder: Builder | None = None,
    prefetcher: Prefetcher | None = None,
):
    """Without an explicit builder: the real producer, gated on its inputs, with a prefetcher."""
    if builder is None:
        sources = Sources()
        prefetcher = Prefetcher(sources, root=root)

        def inputs(tile: str) -> list[str]:
            return [u.key for u in sources.missing_for_service(C.VisualTile.parse(tile), first_pass=True)]

        builder = Builder(
            root,
            inputs=inputs,
            on_missing=lambda units: prefetcher.need([Unit.parse(u) for u in units]),
            input_key=lambda tile: sources.input_key(C.VisualTile.parse(tile)),
            structural_input_key=lambda tile: sources.input_key(C.VisualTile.parse(tile), first_pass=True),
            enrichment_inputs=lambda tile: [u.key for u in sources.missing_for_service(C.VisualTile.parse(tile))],
        )
        prefetcher.on_landed = lambda unit: builder.landed()
    server = Server((host, port), handler(builder, prefetcher))
    return server, builder, prefetcher


def default_port() -> int:
    return int(os.environ.get("TURKU3D_PORT_BASE", "5170")) + 5


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="tools/dev/coarse-tiles",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--port", type=int, default=default_port())
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--root", type=Path, default=C.work_root() / "coarse")
    a = ap.parse_args(argv)
    if a.host not in ("127.0.0.1", "localhost", "::1"):
        ap.error("the coarse tile service is localhost only")
    a.root.mkdir(parents=True, exist_ok=True)
    server, builder, prefetcher = serve(a.port, a.root, a.host)
    print(f"coarse-tiles: http://{a.host}:{a.port}/coarse/status -> {a.root}", flush=True)

    def terminate(*_):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, terminate)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        builder.stop()
        prefetcher.stop()
        server.server_close()
    return 0
