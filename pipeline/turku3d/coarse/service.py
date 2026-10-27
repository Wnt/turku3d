"""Localhost companion service: builds coarse tiles on first request and serves them afterwards.

    GET /coarse/status                      queue in build order with each tile's ETA (s), builds
                                            running, built tiles and their timings, tiles waiting for
                                            data, the build counter `version`, the prefetcher's state;
                                            the service's start time and the repository files it loaded
                                            (`started`, `modules`: the localhost game restarts it when
                                            one of them lands, tools/dev/localhost/coarse.mjs)
    POST /coarse/drain                      start no further builds (running ones finish); the
                                            supervisor restarts the service once `building` is empty
    GET /coarse/want?x=&z=&vx=&vz=          the player's engine position and velocity (m, m/s), once
                                            a second: orders the queue, drives the raw-data prefetcher
                                            (prefetch.py), answers {queued, fetching, version}
    GET /coarse/<tile>/manifest.json?d=<m>  200 when built; otherwise 202 {"state": "queued",
                                            "position", "eta"} (the path ahead first, then by the
                                            player distance d, latest wins) or "building" with "eta", or
                                            202 {"state": "pending-data", "missing": [units]} while
                                            its inputs are being fetched
    GET /coarse/<tile>/vis.glb | roads.json | col/<c>.col and /coarse/shared/<sha>.ktx2   built files

Tiles not re-requested for STALE_SECONDS leave the queue (the player moved on). The game asks for
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
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from turku3d import config as C

from .build import code_hash
from .prefetch import Prefetcher
from .sources import NoCoverage, Sources, Unit

REPO = Path(__file__).resolve().parents[3]
TILE = re.compile(r"^t0_-?\d+_-?\d+$")
FILE = re.compile(r"^(vis\.glb|manifest\.json|roads\.json|col/c_-?\d+_-?\d+\.col)$")
SHARED = re.compile(r"^[0-9a-f]{64}\.ktx2$")
STALE_SECONDS = 10.0  # the game re-polls wanted tiles every 2 s; others leave the queue
PENDING_EXIT = 3  # python -m turku3d.coarse build: inputs missing, {"pending": [units]} on stdout
RETRY_SECONDS = 120.0  # a failed tile (no coverage, network error) is retried after this
MIN_SPEED = 1.0  # m/s: slower than this the heading is noise and the queue is nearest first
AFTER_PATH = 100_000.0  # score offset of tiles off the path ahead
ETA_BUILDS = 8  # recent builds averaged for the queue's time estimates
STALE_REBUILDS = 3  # a build stale on arrival is redone at most this often in a row
WORKERS = 4  # parallel builds: a cold tile spends minutes downloading, warm ones take 5-10 s
MIME = {
    ".json": "application/json",
    ".glb": "model/gltf-binary",
    ".col": "application/octet-stream",
    ".ktx2": "image/ktx2",
}


def score(tile: str, distance: float, motion) -> float:
    """Build order: with a moving player, tiles on the path ahead first (by distance along the
    heading, then off it), everything else (the ring, behind) after them by distance. Otherwise
    nearest first by the distance the game reported."""
    if motion is None:
        return distance
    x, z, vx, vz = motion
    speed = math.hypot(vx, vz)
    if speed < MIN_SPEED:
        return distance
    t = C.VisualTile.parse(tile)
    size = t.size
    dx, dz = t.origin[0] + size / 2 - x, t.origin[2] + size / 2 - z
    along = (dx * vx + dz * vz) / speed
    lateral = abs(dx * vz - dz * vx) / speed
    if along >= -size / 2 and lateral <= 1.5 * size:
        return max(along, 0.0) + lateral
    return AFTER_PATH + distance


class Workers:
    """One persistent ``python -m turku3d.coarse worker`` per builder thread: the interpreter, the
    producer's imports and the node packager are paid once, not per tile (seconds of wall time each
    on a loaded host). A worker is started again when it died, after MAX_BUILDS tiles (bounded
    memory), and when a pipeline source file changed since it started, so code edits take effect."""

    MAX_BUILDS = 50
    SOURCES = ("pipeline/turku3d/**/*.py", "pipeline/turku3d/package/*.mjs")

    def __init__(self):
        self.local = threading.local()
        self.procs: list[subprocess.Popen] = []
        self.lock = threading.Lock()

    def _changed_since(self, started: float) -> bool:
        return any(
            p.stat().st_mtime > started
            for pattern in self.SOURCES
            for p in C.REPO_ROOT.glob(pattern)
        )

    def _worker(self) -> subprocess.Popen:
        w = getattr(self.local, "worker", None)
        if w and w["proc"].poll() is None and w["builds"] < self.MAX_BUILDS:
            if not self._changed_since(w["started"]):
                return w["proc"]
        if w:
            self._end(w["proc"])
        proc = subprocess.Popen(
            [sys.executable, "-m", "turku3d.coarse", "worker"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
        )
        self.local.worker = {"proc": proc, "builds": 0, "started": time.time()}
        with self.lock:
            self.procs.append(proc)
        return proc

    def __call__(self, tile: str, out: Path) -> subprocess.CompletedProcess:
        proc = self._worker()
        self.local.worker["builds"] += 1
        try:
            proc.stdin.write(json.dumps({"tile": tile, "out": str(out)}) + "\n")
            proc.stdin.flush()
            line = proc.stdout.readline()
        except OSError:
            line = ""
        if not line:
            self._end(proc)
            self.local.worker = None
            return subprocess.CompletedProcess([], 1, "", "the build worker exited")
        r = json.loads(line)
        return subprocess.CompletedProcess([], r["code"], r.get("stdout", ""), r.get("stderr", ""))

    def _end(self, proc: subprocess.Popen) -> None:
        if proc.poll() is None:
            proc.stdin.close()
            try:
                proc.wait(10)
            except subprocess.TimeoutExpired:
                proc.kill()
        with self.lock:
            if proc in self.procs:
                self.procs.remove(proc)

    def close(self) -> None:
        with self.lock:
            procs = list(self.procs)
        for proc in procs:
            self._end(proc)


class Builder:
    """Queue of tile ids drained by `workers` threads, in ``score`` order (the path ahead first)."""

    def __init__(
        self,
        root: Path,
        run=None,
        code: str | None = None,
        workers: int = WORKERS,
        inputs=None,
        on_missing=None,
        input_key=None,
    ):
        """``inputs(tile)`` lists the unit keys a tile still needs (raises NoCoverage outside the
        sources); ``on_missing(units)`` hands them to the prefetcher. Without ``inputs`` every
        tile is assumed to have its data. ``input_key(tile)`` identifies the raw files a build
        would read now (None: unknown); a draft built from other files is stale and rebuilt.
        ``run(tile, out)`` builds one tile and returns a CompletedProcess-like result (exit code 0,
        PENDING_EXIT or another failure, stdout, stderr); default: persistent ``Workers``."""
        self.input_key = input_key or (lambda tile: None)
        self.root = root
        self.inputs = inputs or (lambda tile: [])
        self.on_missing = on_missing or (lambda units: None)
        # Re-hashed per check, so a code edit while the service runs rebuilds each tile once.
        self.code = (lambda: code) if code else code_hash
        self.run = run or Workers()
        self.lock = threading.Condition()
        self.priority: dict[str, float] = {}  # queued tile -> distance the game reported
        self.requested: dict[str, float] = {}
        self.motion: tuple[float, float, float, float] | None = None  # player x, z, vx, vz
        self.version = 0  # counts finished builds; the game re-polls its tiles when it changes
        self.recent: list[float] = []  # service seconds of the latest builds
        self.stale: dict[str, int] = {}  # tile -> builds in a row that were stale on arrival
        self.building: dict[str, float] = {}
        self.failed: dict[str, tuple[str, float]] = {}
        self.pending: dict[str, tuple[list[str], float]] = {}  # tile -> (missing units, distance)
        self.timings: dict[str, dict] = {}
        self.stopped = False
        self.draining = False
        self.workers = workers
        self.threads = [
            threading.Thread(target=self._run, name=f"coarse-builder-{k}", daemon=True)
            for k in range(workers)
        ]
        for thread in self.threads:
            thread.start()

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
        if m.get("code") != (code or self.code()):
            return False
        try:
            now = self.input_key(tile)
        except NoCoverage:
            return True
        return now is None or m.get("inputs") == now

    def request(self, tile: str, distance: float) -> dict:
        """Queue or re-prioritise a tile; returns its state."""
        with self.lock:
            if self.built(tile):
                return {"state": "built"}
            error, at = self.failed.get(tile, (None, 0.0))
            if error and time.time() - at < RETRY_SECONDS:
                return {"state": "failed", "error": error}
            self.failed.pop(tile, None)
            if tile in self.building:
                return self._building(tile)
        try:
            missing = self.inputs(tile)
        except NoCoverage as e:
            with self.lock:
                self.failed[tile] = (str(e), time.time())
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
            self.lock.notify()
            order = self.order()
            return {"state": "queued", "position": order.index(tile), "eta": self.eta()[tile]}

    def steer(self, x: float, z: float, vx: float, vz: float) -> None:
        """The player's position and velocity (from /coarse/want) order the queue."""
        with self.lock:
            self.motion = (x, z, vx, vz)

    def order(self) -> list[str]:
        """Queued tiles in build order (call with the lock held)."""
        return sorted(self.priority, key=lambda t: (score(t, self.priority[t], self.motion), t))

    def eta(self) -> dict[str, float]:
        """Seconds until each queued or building tile is built, from the mean of the recent builds
        and the free workers (call with the lock held)."""
        mean = sum(self.recent) / len(self.recent) if self.recent else 8.0
        now = time.time()
        out = {t: max(0.5, mean - (now - since)) for t, since in self.building.items()}
        free = sorted([*out.values(), *[0.0] * max(0, self.workers - len(self.building))])
        for tile in self.order():
            start = heapq.heappop(free) if free else 0.0
            out[tile] = start + mean
            heapq.heappush(free, out[tile])
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
            if now - self.requested.get(tile, 0.0) <= STALE_SECONDS:
                return tile, distance
            self.requested.pop(tile, None)
        return None

    def _run(self):
        while True:
            with self.lock:
                job = self._next()
                while job is None and not self.stopped:
                    self.lock.wait()
                    job = self._next()
                if self.stopped:
                    return
                tile, distance = job
                self.building[tile] = time.time()
            started = time.perf_counter()
            out = self.root / tile
            proc = self.run(tile, out)
            seconds = time.perf_counter() - started
            with self.lock:
                del self.building[tile]
                self.version += 1
                outcome = "FAILED"
                if proc.returncode == PENDING_EXIT:
                    units = json.loads(proc.stdout.strip().splitlines()[-1])["pending"]
                    self.pending[tile] = (units, distance)
                    outcome = "pending"
                elif proc.returncode == 0 and self.built(tile):
                    m = json.loads(self.manifest(tile).read_text())
                    self.timings[tile] = {"service": round(seconds, 3), **m.get("seconds", {})}
                    self.recent = [*self.recent[-(ETA_BUILDS - 1) :], seconds]
                    self.stale.pop(tile, None)
                    outcome = "built"
                elif proc.returncode == 0 and self.stale.get(tile, 0) < STALE_REBUILDS:
                    # Raw data landed during the build, so the draft is stale already: again.
                    self.stale[tile] = self.stale.get(tile, 0) + 1
                    self.priority[tile] = distance
                    self.lock.notify()
                    outcome = "stale"
                else:
                    tail = (proc.stderr or proc.stdout).strip().splitlines()[-1:] or [
                        "build failed"
                    ]
                    self.failed[tile] = (tail[0][:500], time.time())
                if tile not in self.pending and outcome != "stale":
                    self.requested.pop(tile, None)
                print(f"coarse {tile}: {outcome} in {seconds:.1f} s", flush=True)
            if tile in self.pending:
                self.on_missing(self.pending[tile][0])

    def landed(self) -> None:
        """Data landed: re-request the waiting tiles the game still asks for."""
        with self.lock:
            waiting = [
                (tile, distance)
                for tile, (_, distance) in self.pending.items()
                if time.time() - self.requested.get(tile, 0.0) <= STALE_SECONDS
            ]
        for tile, distance in waiting:
            self.request(tile, distance)

    def status(self) -> dict:
        code = self.code()
        # Reading every draft's manifest and input files takes seconds on a loaded host: not under
        # the lock, so tile requests are not held up behind a status call.
        built = sorted(
            p.parent.name
            for p in self.root.glob("t0_*/manifest.json")
            if self.built(p.parent.name, code)
        )
        with self.lock:
            return {
                "state": "building" if self.building else "idle",
                "building": sorted(self.building),
                "queue": self.order(),
                "eta": self.eta(),
                "version": self.version,
                "built": built,
                "failed": {tile: error for tile, (error, _) in self.failed.items()},
                "pending": {tile: units for tile, (units, _) in self.pending.items()},
                "timings": dict(self.timings),
                "code": code,
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


def loaded_files() -> list[str]:
    """Repository files of the modules this process has imported (repo-relative, sorted)."""
    files = set()
    for module in list(sys.modules.values()):
        path = getattr(module, "__file__", None)
        if not path:
            continue
        resolved = Path(path).resolve()
        if resolved.is_relative_to(REPO):
            files.add(resolved.relative_to(REPO).as_posix())
    return sorted(files)


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
                return self._json(200, {**reply, "version": builder.version})
            if len(parts) == 2 and parts[0] == "shared" and SHARED.match(parts[1]):
                path = builder.root / "shared" / parts[1]
                if path.exists():
                    return self._send(200, path.read_bytes(), MIME[".ktx2"], "public, max-age=3600")
                return self._json(404, {"error": "not found"})
            if len(parts) != 2 or not TILE.match(parts[0]) or not FILE.match(parts[1]):
                return self._json(404, {"error": "not found"})
            tile, name = parts
            if name == "manifest.json":
                d = parse_qs(url.query).get("d", ["inf"])[0]
                try:
                    distance = float(d)
                except ValueError:
                    distance = float("inf")
                state = builder.request(tile, distance)
                if state["state"] == "failed":
                    return self._json(404, state)
                if state["state"] != "built":
                    return self._json(202, state)
            elif not builder.built(tile):
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
            return [u.key for u in sources.missing(C.VisualTile.parse(tile))]

        builder = Builder(
            root,
            inputs=inputs,
            on_missing=lambda units: prefetcher.need([Unit.parse(u) for u in units]),
            input_key=lambda tile: sources.input_key(C.VisualTile.parse(tile)),
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
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        builder.stop()
        prefetcher.stop()
        server.server_close()
    return 0
