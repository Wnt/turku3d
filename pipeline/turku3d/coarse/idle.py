"""Route-ring cache warming in the ordinary pool, strictly below player work."""

from __future__ import annotations

import json
import math
import os
import shutil
import threading
import time
from urllib.request import urlopen

from turku3d import config as C

IDLE_SECONDS = 120
MIN_FREE = 15 * 1024**3
MIN_BOOT_FREE = 5 * 1024**3
RECENT_SECONDS = 24 * 3600
RECENT_LIMIT = 256


def spawn_tiles(points=None, radius=200):
    """Match startupDraftTiles: default office/nearby car search and multiplayer starts.

    Saved player/car locations belong to the client's startup want-list; the server
    never receives or stores save games. No replacement-parent or background ring.
    """
    if points is None:
        x, _, z = C.en_to_local(C.OFFICE_E, C.OFFICE_N, 0)
        points = [(x, z)]
        spawns = C.REPO_ROOT / "server/spawns.json"
        points.extend((p["E"], -p["N"]) for p in json.loads(spawns.read_text())["points"])
    tiles = set()
    size = C.VISUAL_TILE_SIZE
    for x, z in points:
        for i in range(math.floor((x - radius) / size), math.floor((x + radius) / size) + 1):
            for j in range(math.floor((z - radius) / size), math.floor((z + radius) / size) + 1):
                if (
                    math.hypot(
                        max(i * size - x, 0, x - (i + 1) * size),
                        max(j * size - z, 0, z - (j + 1) * size),
                    )
                    <= radius
                ):
                    tiles.add(f"t0_{i}_{j}")
    return sorted(tiles)


class IdlePrebuild:
    def __init__(self, builder, plan, presence, free=None, clock=time.monotonic):
        self.builder, self.plan, self.presence = builder, plan, presence
        self.free = free or (
            lambda: {
                "data": shutil.disk_usage("/srv/turku3d").free,
                "boot": shutil.disk_usage("/").free,
            }
        )
        self.clock = clock
        self.last_player = clock()
        self.online = True
        self.presence_known = False  # Fail closed until multiplayer health is known.
        self.recent_path = builder.root / ".warm-recent.json"
        try:
            rows = json.loads(self.recent_path.read_text())
            self.recent = {
                t: at
                for t, at in rows.items()
                if isinstance(at, (int, float)) and math.isfinite(at) and t.startswith("t0_")
            }
        except (OSError, ValueError, AttributeError):
            self.recent = {}
        # Bootstrap the first deployment of this scheduler from recent real builds.
        for manifest in builder.root.glob("t0_*_*/manifest.json"):
            self.recent.setdefault(manifest.parent.name, manifest.stat().st_mtime)
        self.last_saved = 0.0
        self.code = None
        self.tiles = set()
        self.walk = []
        self.done = set()
        self.failed = set()
        self.unavailable = set()
        self.state = "waiting"
        self.ring = 0
        self.active_seconds = 0.0
        self.completed = 0
        self.last_tick = clock()
        self.summary = {}
        self.disk_stopped = False
        builder.idle = self

    def allowed(self):
        return (
            self.presence_known
            and not self.disk_stopped
            and not self.builder.draining
            and not self.builder.stopped
        )

    def fully_idle(self):
        return not self.online and self.clock() - self.last_player >= IDLE_SECONDS

    def capacity(self):
        b = self.builder
        if self.fully_idle():
            return b.workers
        # One low-priority worker at most while online or just activated. Leave
        # a crossing worker free even when the other workers are committed full passes.
        foreground = len(set(b.building) - self.tiles)
        # Raw downloads occupy no build worker; keep warming prepared tiles meanwhile.
        waiting = (set(b.priority) | b.upgrade_queue) - self.tiles
        return 0 if waiting else min(1, max(0, b.workers - foreground - 1))

    def activity(self):
        """A heartbeat ends idle expansion; useful spare work survives repeated polls."""
        self.last_player = self.clock()
        self.preempt()

    def release(self, tiles):
        b = self.builder
        for tile in set(tiles):
            b.priority.pop(tile, None)
            b.pending.pop(tile, None)
            b.upgrade_queue.discard(tile)
            b.upgrade_requested.pop(tile, None)
            b.requested.pop(tile, None)
            b.journal.discard(tile)
            if tile in b.building and tile not in b.cancelled:
                b.cancelled.add(tile)  # Covers cancellation before PID registration.
                if hasattr(b.run, "cancel"):
                    b.run.cancel(tile)
            self.tiles.discard(tile)
        b.lock.notify_all()

    def preempt(self):
        """Called under the scheduler lock after demand updates, before job selection."""
        b = self.builder
        # Imminent/visible work becomes foreground and keeps its completion commitment.
        self.tiles.difference_update(
            t for t in list(self.tiles) if (r := b.demand.rank(t)) is not None and r[0] <= 1
        )
        running = self.tiles & b.building.keys()
        waiting = (set(b.priority) | b.upgrade_queue) - self.tiles
        urgent = [t for t in waiting if (r := b.demand.rank(t)) is not None and r[0] == 0]
        free = max(0, b.workers - len(b.building))
        limit = b.workers if self.fully_idle() else max(len(running), self.capacity())
        if not self.fully_idle():
            limit = min(1, limit)
        if urgent and len(urgent) > free:
            limit = min(limit, max(0, len(running) - (len(urgent) - free)))
        # Keep running warm work before queued work; release only excess capacity.
        ordered = sorted(self.tiles, key=lambda t: (t not in b.building, t))
        self.release(ordered[limit:])

    def pause(self, state):
        self.state = state
        self.release(self.tiles)

    def candidates(self):
        b = self.builder
        # Interleave each anonymous client's already predicted route so one long
        # want-list cannot monopolise warming when ten players drive independently.
        with b.lock:
            routes = [list(row[1]) for row in b.demand.clients.values()]
        ahead = [
            route[i]
            for i in range(max(map(len, routes), default=0))
            for route in routes
            if i < len(route)
        ]
        recent = sorted(self.recent, key=self.recent.get, reverse=True)
        ring_limit = self.summary.get("route_last_ring", 0)
        walk = self.walk if self.fully_idle() else [(r, t) for r, t in self.walk if r <= ring_limit]
        seen = set()
        for ring, tile in [*((-2, t) for t in ahead), *((-1, t) for t in recent), *walk]:
            if tile not in seen:
                seen.add(tile)
                yield ring, tile

    def remember(self):
        now = time.time()
        for _, _tiles, urgent, visible in self.builder.demand.clients.values():
            for tile in urgent | visible:
                self.recent[tile] = now
        self.recent = dict(
            sorted(
                ((t, at) for t, at in self.recent.items() if now - at < RECENT_SECONDS),
                key=lambda row: row[1],
                reverse=True,
            )[:RECENT_LIMIT]
        )
        if now - self.last_saved >= 30 and self.recent:
            self.recent_path.parent.mkdir(parents=True, exist_ok=True)
            temp = self.recent_path.with_name(f".warm-recent.{os.getpid()}.tmp")
            temp.write_text(json.dumps(self.recent) + "\n")
            temp.replace(self.recent_path)
            self.last_saved = now

    def free_bytes(self):
        available = self.free()
        return available if isinstance(available, dict) else {"data": available, "boot": available}

    def reclaim_outer_stages(self):
        """Drop only outermost rebuildable stage entries, never served payloads."""
        from .retention import tile_lock

        root = self.builder.root / ".stages"
        candidates = []
        with self.builder.lock:
            active = set(self.builder.building)
        for stage in root.glob("t0_*_*"):
            if not stage.is_dir() or stage.name in active:
                continue
            try:
                tile = C.VisualTile.parse(stage.name)
            except ValueError:
                continue
            candidates.append((max(abs(tile.i), abs(tile.j)), stage))
        for _, stage in sorted(candidates, reverse=True):
            if self.free_bytes()["boot"] >= MIN_BOOT_FREE:
                break
            with tile_lock(self.builder.root / stage.name, blocking=False) as acquired:
                if not acquired:
                    continue
                for file in stage.rglob("*.pkl"):
                    if file.is_file() and not file.is_symlink():
                        file.unlink()

    def disk_ok(self):
        available = self.free_bytes()
        if available["boot"] < MIN_BOOT_FREE:
            self.reclaim_outer_stages()
            available = self.free_bytes()
        if available["data"] < MIN_FREE or available["boot"] < MIN_BOOT_FREE:
            with self.builder.lock:
                self.disk_stopped = True
                self.summary["stopped_free_bytes"] = available
                self.pause("low-disk")
            return False
        self.disk_stopped = False
        self.summary.pop("stopped_free_bytes", None)
        return True

    def status(self):
        with self.builder.lock:
            return dict(
                state=self.state,
                cached=len(self.done),
                total=len(self.walk),
                ring=self.ring,
                mode="idle" if self.fully_idle() else "spare",
                recent_tiles=len(self.recent),
                generator=self.code,
                capacity=self.capacity(),
                route_cached=sum(
                    t in self.done
                    for r, t in self.walk
                    if r <= self.summary.get("route_last_ring", 0)
                ),
                building=sorted(self.tiles & self.builder.building.keys()),
                failed=sorted(self.failed),
                unavailable=sorted(self.unavailable),
                workers=self.builder.workers,
                completed=self.completed,
                active_seconds=round(self.active_seconds, 2),
                tiles_per_minute=round(60 * self.completed / self.active_seconds, 3)
                if self.active_seconds
                else 0,
                **self.summary,
            )

    def tick(self):
        b = self.builder
        if b.draining:
            # Deployment retirement preserves in-flight idle work as well.
            return
        try:
            online = self.presence()
            self.presence_known = True
        except (OSError, ValueError):
            self.presence_known = False
            online = True
        with b.lock:
            b.demand.expire()
            self.online = online = online or bool(b.demand.clients)
            if online:
                self.activity()
            now = self.clock()
            if self.state == "running":
                self.active_seconds += now - self.last_tick
            self.last_tick = now
            if not self.presence_known:
                self.pause("presence-unavailable")
                return
            self.preempt()
            self.remember()
            self.state = "running" if self.tiles else "idle" if self.fully_idle() else "spare"
        if not self.disk_ok():
            return
        if not self.walk:
            self.walk, self.summary = self.plan()
        # Check identities outside the scheduler lock; cached tiles never consume a worker.
        code = b.code()
        if self.code != code:
            self.done.clear()
            self.failed.clear()
            self.unavailable.clear()
            self.code = code
        for tile in list(self.tiles):
            if tile in b.building:
                continue
            if (
                b.built(tile, code)
                and json.loads(b.manifest(tile).read_text()).get("pass_kind") == "full"
            ):
                self.done.add(tile)
                self.tiles.discard(tile)
                self.completed += 1
        for ring, tile in self.candidates():
            if tile in self.done or tile in self.failed or tile in self.unavailable:
                continue
            with b.lock:
                if not self.allowed():
                    return
                if tile in b.building:
                    continue
                if tile in b.cancelled:
                    continue
            full = (
                b.built(tile, code)
                and json.loads(b.manifest(tile).read_text()).get("pass_kind") == "full"
            )
            if not self.disk_ok():
                return
            with b.lock:
                if not self.allowed():
                    return
                if full:
                    self.done.add(tile)
                    if tile in self.tiles:
                        self.completed += 1
                    self.tiles.discard(tile)
                    continue
                if tile in b.failed:
                    from .build_health import error_kind

                    if error_kind(b.failed[tile][0]) == "NoCoverage":
                        self.unavailable.add(tile)
                    else:
                        self.failed.add(tile)
                    self.tiles.discard(tile)
                    continue
                # Finish a ring before advancing in any direction.
                if self.fully_idle() and self.tiles and ring > self.ring:
                    break
                if len(self.tiles) >= self.capacity():
                    break
                self.ring = ring
                self.tiles.add(tile)
                b._request(tile, 1e9 + ring)
                self.state = "running"
        with b.lock:
            if len(self.done) + len(self.failed) + len(self.unavailable) == len(self.walk):
                self.state = "failed" if self.failed else "done"

    def start(self):
        def loop():
            previous = None
            while not self.builder.stopped:
                try:
                    self.tick()
                    snapshot = self.status()
                    encoded = json.dumps(snapshot, sort_keys=True)
                    # Rate telemetry changes continuously; log progress/state, not polling.
                    key = (
                        self.state,
                        len(self.done),
                        self.ring,
                        tuple(sorted(self.failed)),
                        len(self.unavailable),
                    )
                    if key != previous:
                        print(json.dumps({"idle_prebuild": snapshot}), flush=True)
                        path = self.builder.root.parent / "prebuild.json"
                        temp = path.with_suffix(".tmp")
                        temp.write_text(encoded + "\n")
                        temp.replace(path)
                        previous = key
                except Exception as error:
                    print(f"idle prebuild: {type(error).__name__}: {error}", flush=True)
                time.sleep(1)

        threading.Thread(target=loop, name="coarse-idle", daemon=True).start()


def multiplayer_present():
    with urlopen("http://127.0.0.1:7700/healthz", timeout=0.5) as response:
        return json.load(response)["connections"] > 0


def route_walk(nav, bounds=(-24, -24, 24, 24)):
    """Same 150 m network rings as facade characterization; include eight neighbours."""
    import numpy as np

    from turku3d.routes_rings import graph_routes

    tertiary = C.data_root() / "facade-characterization/inputs/tertiary.json"
    lines, edges, distances, corridor, records = graph_routes(
        nav, json.loads(tertiary.read_text()) if tertiary.exists() else ()
    )
    rings = {tile: 0 for tile in spawn_tiles()}
    size = C.VISUAL_TILE_SIZE
    for index in corridor:
        line, edge = lines[index], edges[index]
        # Sample much more finely than a tile; segment/grid intersections also
        # catch tiny corner crossings that equidistant samples could miss.
        cuts = {0.0, line.length}
        coords = list(line.coords)
        along = 0.0
        for a, b in zip(coords, coords[1:], strict=False):
            length = math.dist(a, b)
            for axis in (0, 1):
                lo, hi = sorted((a[axis], b[axis]))
                for grid in range(math.floor(lo / size) + 1, math.floor(hi / size) + 1):
                    t = (grid * size - a[axis]) / (b[axis] - a[axis])
                    cuts.add(along + t * length)
            along += length
        ordered = sorted(cuts)
        for start, end in zip(ordered, ordered[1:], strict=False):
            point = line.interpolate((start + end) / 2)
            distance = min(distances[edge[0]] + start, distances[edge[1]] + line.length - end)
            if not np.isfinite(distance):
                continue
            ring = max(0, math.floor(distance / 150))
            i, j = math.floor(point.x / size), math.floor(point.y / size)
            for di in (-1, 0, 1):
                for dj in (-1, 0, 1):
                    ti, tj = i + di, j + dj
                    if bounds[0] <= ti <= bounds[2] and bounds[1] <= tj <= bounds[3]:
                        tile = f"t0_{ti}_{tj}"
                        rings[tile] = min(ring, rings.get(tile, ring))
    route_count = len(rings)
    last_ring = max(rings.values())
    # The public register extent is the producer's bounded tile area. Once all
    # route corridors have been covered, expand square rings in every direction.
    origin_i, origin_j = math.floor(77.1 / size), math.floor(187.6 / size)
    for i in range(bounds[0], bounds[2] + 1):
        for j in range(bounds[1], bounds[3] + 1):
            tile = f"t0_{i}_{j}"
            if tile not in rings:
                rings[tile] = last_ring + 1 + max(abs(i - origin_i), abs(j - origin_j))
    walk = sorted((ring, tile) for tile, ring in rings.items())
    return walk, dict(
        route_tiles=route_count,
        route_last_ring=last_ring,
        register_tiles=len(walk),
        destinations=len(nav["addresses"]["addresses"]),
        landmarks=sum(bool(a.get("landmark")) for a in nav["addresses"]["addresses"]),
        unreachable_routes=sum(bool(r.get("unreachable")) for r in records),
        ring_metres=150,
    )
