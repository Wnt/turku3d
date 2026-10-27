"""Localhost companion service: builds coarse tiles on first request and serves them afterwards.

    GET /coarse/status                      queue, current build, built tiles and their timings,
                                            tiles waiting for data, the prefetcher's state
    GET /coarse/want?x=&z=&vx=&vz=          the player's engine position and velocity (m, m/s), once
                                            a second: drives the raw-data prefetcher (prefetch.py)
    GET /coarse/<tile>/manifest.json?d=<m>  200 when built; otherwise 202 and the tile is queued,
                                            nearest first by the player distance d (latest wins), or
                                            202 {"state": "pending-data", "missing": [units]} while
                                            its inputs are being fetched
    GET /coarse/<tile>/vis.glb | roads.json | col/<c>.col and /coarse/shared/<sha>.ktx2   built files

Tiles not re-requested for STALE_SECONDS leave the queue (the player moved on). The game asks for
the cells ahead of a moving player as well (prefetch), so up to WORKERS builds run at once, each in
a child process, so a slow or failing build never blocks requests or the other builds.
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

TILE = re.compile(r"^t0_-?\d+_-?\d+$")
FILE = re.compile(r"^(vis\.glb|manifest\.json|roads\.json|col/c_-?\d+_-?\d+\.col)$")
SHARED = re.compile(r"^[0-9a-f]{64}\.ktx2$")
STALE_SECONDS = 10.0  # the game re-polls wanted tiles every 2 s; others leave the queue
PENDING_EXIT = 3  # python -m turku3d.coarse build: inputs missing, {"pending": [units]} on stdout
RETRY_SECONDS = 120.0  # a failed tile (no coverage, network error) is retried after this
WORKERS = 4  # parallel builds: a cold tile spends minutes downloading, warm ones take 5-10 s
MIME = {
    ".json": "application/json",
    ".glb": "model/gltf-binary",
    ".col": "application/octet-stream",
    ".ktx2": "image/ktx2",
}


class Builder:
    """Priority queue of tile ids (lowest distance first) drained by `workers` threads."""

    def __init__(
        self,
        root: Path,
        command=None,
        code: str | None = None,
        workers: int = WORKERS,
        inputs=None,
        on_missing=None,
    ):
        """``inputs(tile)`` lists the unit keys a tile still needs (raises NoCoverage outside the
        sources); ``on_missing(units)`` hands them to the prefetcher. Without ``inputs`` every
        tile is assumed to have its data."""
        self.root = root
        self.inputs = inputs or (lambda tile: [])
        self.on_missing = on_missing or (lambda units: None)
        # Re-hashed per check, so a code edit while the service runs rebuilds each tile once.
        self.code = (lambda: code) if code else code_hash
        self.command = command or (
            lambda tile, out: [
                sys.executable,
                "-m",
                "turku3d.coarse",
                "build",
                tile,
                "--out",
                str(out),
            ]
        )
        self.lock = threading.Condition()
        self.priority: dict[str, float] = {}
        self.requested: dict[str, float] = {}
        self.heap: list[tuple[float, str]] = []
        self.building: dict[str, float] = {}
        self.failed: dict[str, tuple[str, float]] = {}
        self.pending: dict[str, tuple[list[str], float]] = {}  # tile -> (missing units, distance)
        self.timings: dict[str, dict] = {}
        self.stopped = False
        self.threads = [
            threading.Thread(target=self._run, name=f"coarse-builder-{k}", daemon=True)
            for k in range(workers)
        ]
        for thread in self.threads:
            thread.start()

    def manifest(self, tile: str) -> Path:
        return self.root / tile / "manifest.json"

    def built(self, tile: str) -> bool:
        path = self.manifest(tile)
        if not path.exists():
            return False
        try:
            return json.loads(path.read_text()).get("code") == self.code()
        except (OSError, ValueError):
            return False

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
                return {"state": "building", "since": self.building[tile]}
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
            self.priority[tile] = distance
            heapq.heappush(self.heap, (distance, tile))
            self.lock.notify()
            order = sorted(self.priority.items(), key=lambda kv: kv[1])
            return {"state": "queued", "position": [t for t, _ in order].index(tile)}

    def _next(self) -> tuple[str, float] | None:
        while self.heap:
            distance, tile = heapq.heappop(self.heap)
            if self.priority.get(tile) != distance:
                continue
            del self.priority[tile]
            if time.time() - self.requested.get(tile, 0.0) <= STALE_SECONDS:
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
            proc = subprocess.run(self.command(tile, out), capture_output=True, text=True)
            seconds = time.perf_counter() - started
            with self.lock:
                del self.building[tile]
                if proc.returncode == PENDING_EXIT:
                    units = json.loads(proc.stdout.strip().splitlines()[-1])["pending"]
                    self.pending[tile] = (units, distance)
                elif proc.returncode == 0 and self.built(tile):
                    m = json.loads(self.manifest(tile).read_text())
                    self.timings[tile] = {"service": round(seconds, 3), **m.get("seconds", {})}
                else:
                    tail = (proc.stderr or proc.stdout).strip().splitlines()[-1:] or [
                        "build failed"
                    ]
                    self.failed[tile] = (tail[0][:500], time.time())
                if tile not in self.pending:
                    self.requested.pop(tile, None)
                outcome = (
                    "built"
                    if tile in self.timings
                    else "pending"
                    if tile in self.pending
                    else "FAILED"
                )
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
        with self.lock:
            return {
                "state": "building" if self.building else "idle",
                "building": sorted(self.building),
                "queue": [t for t, _ in sorted(self.priority.items(), key=lambda kv: kv[1])],
                "built": sorted(
                    p.parent.name
                    for p in self.root.glob("t0_*/manifest.json")
                    if self.built(p.parent.name)
                ),
                "failed": {tile: error for tile, (error, _) in self.failed.items()},
                "pending": {tile: units for tile, (units, _) in self.pending.items()},
                "timings": dict(self.timings),
                "code": self.code(),
            }

    def stop(self):
        with self.lock:
            self.stopped = True
            self.lock.notify_all()


def handler(builder: Builder, prefetcher: Prefetcher | None = None):
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

        def do_GET(self):
            url = urlparse(self.path)
            parts = url.path.removeprefix("/coarse/").split("/", 1)
            if url.path == "/coarse/status":
                status = builder.status()
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
                return self._json(200, prefetcher.want(x, z, vx, vz) if prefetcher else {})
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
        prefetcher = Prefetcher(sources)

        def inputs(tile: str) -> list[str]:
            return [u.key for u in sources.missing(C.VisualTile.parse(tile))]

        builder = Builder(
            root,
            inputs=inputs,
            on_missing=lambda units: prefetcher.need([Unit.parse(u) for u in units]),
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
