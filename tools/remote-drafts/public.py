"""Public HTTP boundary around the pinned draft producer; management stays local to systemd."""

from __future__ import annotations

import hashlib
import json
import os
import re
import signal
import threading
import time
from collections import OrderedDict
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from turku3d import config as C
from turku3d.coarse.demand import MAX_TILES
from turku3d.coarse.prefetch import Prefetcher
from turku3d.coarse.service import (
    FILE,
    MIME,
    SHARED,
    TILE,
    Builder,
    Server,
    handler,
    immutable_payload,
)
from turku3d.coarse.serving import public_fragment
from turku3d.coarse.serving import retained_fragment as collision_fragment
from turku3d.coarse.sources import Unit

# Serving derivative only: no generator invocation or format changes to individual .col cells.
BUNDLE = re.compile(r"^bundle\.[0-9a-f]{64}\.col$")
IMMUTABLE_CACHE = "public, max-age=31536000, immutable"


class CollisionBundles:
    """Concatenate immutable cells in sorted-id order once, retaining a bounded descriptor cache."""
    def __init__(self, root):
        self.root = root
        self.lock = threading.Lock()
        self.entries = OrderedDict()

    def describe(self, fragment):
        cells = fragment.get("collision", {})
        if len(cells) != 16 or not fragment.get("visual"):
            return None
        rows = sorted(cells.items())
        if any(not re.fullmatch(r"col/c_-?\d+_-?\d+\.[0-9a-f]{64}\.col", e.get("url", "")) for _, e in rows):
            return None
        tile = fragment["tile"]
        key = (tile, tuple((cid, e["url"], e["sha256"], e["bytes"]) for cid, e in rows))
        with self.lock:
            if key in self.entries:
                descriptor = self.entries[key]
                if (self.root / tile / descriptor["url"]).is_file():
                    self.entries.move_to_end(key)
                    return descriptor
                # Disk retention or a cold-output reset can remove the derivative
                # while this producer still remembers it. Rebuild from its cells.
                del self.entries[key]
            bodies = [(self.root / tile / e["url"]).read_bytes() for _, e in rows]
            for body, (_, entry) in zip(bodies, rows, strict=False):
                if len(body) != entry["bytes"] or hashlib.sha256(body).hexdigest() != entry["sha256"]:
                    raise ValueError("collision bundle source mismatch")
            body = b"".join(bodies)
            digest = hashlib.sha256(body).hexdigest()
            name = f"bundle.{digest}.col"
            target = self.root / tile / name
            if not target.exists():
                temporary = target.with_suffix(f".tmp-{os.getpid()}-{threading.get_ident()}")
                temporary.write_bytes(body)
                temporary.replace(target)
            descriptor = {"url": name, "sha256": digest, "bytes": len(body), "format": "sorted-col-v1"}
            self.entries[key] = descriptor
            while len(self.entries) > 128:
                self.entries.popitem(last=False)
            return descriptor


def allowed_origin(origin: str | None) -> bool:
    return (
        origin is None
        or origin == "https://wnt.github.io"
        or bool(re.fullmatch(r"http://(?:localhost|127\.0\.0\.1|\[::1\])(?::[1-9][0-9]{0,4})?", origin))
    )


def public_route(path: str, bounds: list[int]) -> tuple[str, str | None]:
    if path == "/coarse/scenery":
        return "scenery-catalogue", None
    if re.fullmatch(r"/coarse/scenery/(?:[a-f0-9]{64}\.json|lod\.[a-f0-9]{64}\.glb)", path):
        return "scenery-asset", None
    if path == "/coarse/navigation.json":
        return "navigation", None
    if path == "/coarse/demand":
        return "demand", None
    if path == "/coarse/health":
        return "health", None
    parts = path.removeprefix("/coarse/").split("/", 1)
    if not path.startswith("/coarse/") or len(parts) != 2:
        return "invalid", None
    tile, name = parts
    if tile == "shared" and SHARED.fullmatch(name):
        return "asset", None
    if not TILE.fullmatch(tile) or not (FILE.fullmatch(name) or BUNDLE.fullmatch(name)):
        return "invalid", None
    _, i, j = tile.split("_")
    if not (bounds[0] <= int(i) <= bounds[2] and bounds[1] <= int(j) <= bounds[3]):
        return "invalid", None
    return ("manifest" if name == "manifest.json" else "collision" if name == "collision.json" else "asset"), tile


class PublicServer(Server):
    request_queue_size = 1024  # Admit a 10 x 32 burst while the accept thread is descheduled.
    # Join admitted HTTP handlers before process exit, including slow payload writes.
    daemon_threads = False


class Responses:
    """Coalesce public polls; publication invalidates the one-second validated snapshot.

    The lock only protects cache bookkeeping, never scheduler work, disk I/O or
    socket writes. Bound both cached entries and simultaneous distinct lookups.
    """
    def __init__(self):
        self.lock = threading.Lock()
        self.entries = OrderedDict()
        self.pending = {}

    def get(self, key, compute):
        with self.lock:
            entry = self.entries.get(key)
            if entry and time.monotonic() < entry[0]:
                self.entries.move_to_end(key)
                return entry[1]
            self.entries.pop(key, None)
            event = self.pending.get(key)
            if event is None:
                if len(self.pending) >= 256:
                    return 202, {"state": "queued"}
                event = self.pending[key] = threading.Event()
                owner = True
            else:
                owner = False
        if not owner:
            # A cold source check must not tie up every HTTP connection.
            if not event.wait(1):
                return 202, {"state": "queued"}
            with self.lock:
                entry = self.entries.get(key)
                return entry[1] if entry else (202, {"state": "queued"})
        try:
            result = compute()
            if result[1].get("state") != "deferred":
                with self.lock:
                    self.entries[key] = (time.monotonic() + 1, result)
                    self.entries.move_to_end(key)
                    while len(self.entries) > 256:
                        self.entries.popitem(last=False)
            return result
        finally:
            with self.lock:
                self.pending.pop(key, None)
                event.set()


def publication_key(builder, tile):
    files = []
    for name in ("manifest.json", "collision.json", "first.json", "last-full.json"):
        try:
            stat = (builder.root / tile / name).stat()
            files.append((stat.st_ino, stat.st_mtime_ns, stat.st_size))
        except FileNotFoundError:
            files.append(None)
    return tile, getattr(builder, "version", 0), tuple(files)


def public_handler(builder, prefetcher, settings):
    from ready_fragments import ReadyFragments
    from scenery import Scenery, serve as serve_scenery

    base = handler(builder, prefetcher)
    instance = os.urandom(8).hex()
    bundles = CollisionBundles(builder.root)
    responses = Responses()
    ready = ReadyFragments(builder.root, lambda tile: publication_key(builder, tile)[2])
    admitting = {}
    scenery = Scenery(builder.root)

    class PublicHandler(base):
        server_version = "TurkuDrafts"
        sys_version = ""
        protocol_version = "HTTP/1.1"
        disable_nagle_algorithm = True
        timeout = 35  # Bound admitted slow clients while a retired slot drains.

        def handle_one_request(self):
            self._response_started = False
            try:
                return super().handle_one_request()
            except (BrokenPipeError, ConnectionResetError):
                self.close_connection = True
            except Exception as error:
                # Never leak exception text/paths, or write a second HTTP response.
                print(json.dumps({"http_error": type(error).__name__}), flush=True)
                self.close_connection = True
                if not self._response_started:
                    self._json(503, {"state": "failed"})

        def send_error(self, code, message=None, explain=None):
            # BaseHTTPRequestHandler parser errors and unsupported methods use this.
            self._json(code, {"error": "request unavailable"})

        def _send(self, status, body, kind="application/json", cache="no-cache"):
            self._response_started = True
            origin = getattr(self, "headers", {}).get("Origin")
            etag = '"' + hashlib.sha256(body).hexdigest() + '"' if status == 200 else None
            if etag and self.headers.get("If-None-Match") == etag:
                status, body = 304, b""
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", cache)
            self.send_header("Cross-Origin-Resource-Policy", "cross-origin")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Vary", "Origin")
            if origin and allowed_origin(origin):
                self.send_header("Timing-Allow-Origin", origin)
            if origin and allowed_origin(origin):
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Access-Control-Allow-Methods", "GET, HEAD, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "If-None-Match")
                self.send_header("Access-Control-Expose-Headers", "ETag, Retry-After")
            if etag:
                self.send_header("ETag", etag)
            if status in (202, 408, 429) or status >= 500:
                self.send_header("Retry-After", "5")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)
            # No IP, query, headers, player position or user-agent in request logs.
            route, _ = public_route(urlparse(getattr(self, "path", "")).path, settings["tile_bounds"])
            print(json.dumps({"http": status, "route": route}), flush=True)

        def _json(self, status, value):
            # Pipeline exceptions can contain filesystem paths; expose only retryable states.
            if value.get("state") == "failed":
                value = {"state": "failed", "error": "draft unavailable"}
            elif "error" in value:
                value = {"error": "not available"}
            if status == 200 and value.get("coarse") and value.get("collision"):
                try:
                    bundle = bundles.describe(value)
                    if bundle:
                        value = {**value, "collision_bundle": bundle}
                except (OSError, ValueError, KeyError):
                    # Preserve normal per-cell validation if a producer is repairing a missing payload.
                    pass
            return super()._json(status, value)

        def do_POST(self):
            # This path is outside /coarse and therefore never proxied to a producer.
            if (self.path == '/internal/player-connected'
                    and self.client_address[0] == '127.0.0.1' and builder.idle is not None):
                with builder.lock:
                    builder.idle.online = True
                    builder.idle.activity()
                return self._json(200, {'ok': True})
            self._json(405, {"error": "method"})

        def do_OPTIONS(self):
            if not allowed_origin(self.headers.get("Origin")):
                return self._json(403, {"error": "origin"})
            return self._send(204, b"")

        def do_GET(self):
            if not allowed_origin(self.headers.get("Origin")):
                return self._json(403, {"error": "origin"})
            route, tile = public_route(urlparse(self.path).path, settings["tile_bounds"])
            if route in ('scenery-catalogue', 'scenery-asset'):
                serve_scenery(self, scenery, urlparse(self.path), settings['tile_bounds'])
                return
            idle = getattr(builder, 'idle', None)
            # Only game clients carry a client id; probes that merely send a Pages Origin must not hold off prebuilding.
            if (idle is not None and route == 'demand'
                    and 'client' in parse_qs(urlparse(self.path).query)):
                with builder.lock:
                    idle.activity()
            if route == "demand":
                q = parse_qs(urlparse(self.path).query, keep_blank_values=True)
                client = q.get("client", [""])[0]
                tiles = q.get("tiles", [""])[0].split(",") if q.get("tiles", [""])[0] else []
                urgent = q.get("urgent", [""])[0].split(",") if q.get("urgent", [""])[0] else []
                visible = q.get("visible", [""])[0].split(",") if q.get("visible", [""])[0] else []
                if (len(self.path) > 16384 or not re.fullmatch(r"[a-zA-Z0-9-]{8,64}", client)
                        or len(tiles) > MAX_TILES or len(set(tiles)) != len(tiles)
                        or not set(urgent + visible) <= set(tiles)
                        or any(public_route(f"/coarse/{t}/manifest.json", settings["tile_bounds"])[0] != "manifest" for t in tiles)):
                    return self._json(400, {"error": "invalid demand"})
                with builder.lock:
                    if not builder.demand.update(client, tiles, urgent, visible):
                        status, reply = 429, {"state": "queue-full"}
                    else:
                        builder.demand.prune(builder)
                        builder.lock.notify_all()
                        status, reply = 200, {"version": builder.version, "queued": len(builder.priority),
                                              "building": sorted(builder.building)}
                return self._json(status, reply)
            if route == "navigation":
                return super().do_GET()
            if route == "health":
                from turku3d.coarse.audit import health as audit_health

                def health():
                    return 200, {"state": "online", "instance": instance,
                                        "visual_audits": audit_health(builder.root),
                                        "failure_count": builder.failure_count, "failed_tiles": len(builder.failed),
                                        "active_builds": len(builder.building), "draining": builder.draining,
                                        "prebuild": idle.status() if idle else None,
                                        "build_health": builder.build_health.snapshot(
                                            bool(builder.demand.clients) or bool(idle and idle.online))}
                return self._json(*responses.get("health", health))
            if route == "invalid":
                return self._json(404, {"error": "route"})
            name = urlparse(self.path).path.removeprefix("/coarse/").split("/", 1)[1]
            if route in ("manifest", "collision"):
                q = parse_qs(urlparse(self.path).query)
                client = q.get("client", [None])[0]
                # Published assets are public. Lease/admission work must not delay cached delivery;
                # the background refresh still checks the lease before requesting generation.
                fragment = ready.get(tile, lambda: self._manifest(route, tile, client))
                if fragment is not None:
                    return self._json(200, fragment)
                if client is not None:
                    with builder.lock:
                        wanted = builder.demand.wants(client, tile)
                    if not wanted:
                        return self._json(202, {"state": "deferred"})
                return self._json(*responses.get((route, publication_key(builder, tile)),
                                                lambda: self._manifest(route, tile, client)))
            if tile is not None and tile not in builder.servable and not (immutable_payload(name) or BUNDLE.fullmatch(name)):
                return self._json(404, {"error": "unbuilt"})
            path = builder.root / (tile or "shared") / name
            if not path.is_file():
                return self._json(404, {"error": "missing"})
            return self._send(
                200, path.read_bytes(), MIME[path.suffix],
                IMMUTABLE_CACHE if immutable_payload(name) or BUNDLE.fullmatch(name) or (tile is None and SHARED.fullmatch(name))
                else "public, max-age=0, must-revalidate"
            )

        def _manifest(self, route, tile, client):
            built = builder.built(tile)
            with builder.lock:
                if client is not None and not builder.demand.wants(client, tile):
                    return 202, {"state": "deferred"}
                active = set(builder.priority) | set(builder.building) | set(builder.pending) | set(admitting)
                full = (tile not in active and not built
                        and len(active) >= max(settings["queue_max"], min(256, 12 * len(builder.demand.clients)))
                        and not builder.demand.admit(builder, tile))
                if not full:
                    admitting[tile] = admitting.get(tile, 0) + 1
                lease = builder.demand.rank(tile)
            if full:
                fragment = collision_fragment(builder, tile)
                if fragment is None:
                    return 429, {"state": "queue-full"}
                # Retained output bypasses admission but still requests its repair.
                with builder.lock:
                    admitting[tile] = admitting.get(tile, 0) + 1
            # Legacy clients/prewarming have background priority, without player motion.
            t = C.VisualTile.parse(tile)
            spawn_x, _, spawn_z = C.en_to_local(C.OFFICE_E, C.OFFICE_N, 0)
            distance = ((t.origin[0] + t.size / 2 - spawn_x) ** 2
                        + (t.origin[2] + t.size / 2 - spawn_z) ** 2) ** 0.5
            distance = lease[1] if lease is not None else distance + 1e6
            try:
                state = builder.request(tile, distance)
            finally:
                with builder.lock:
                    admitting[tile] -= 1
                    if not admitting[tile]:
                        del admitting[tile]
            if route == "collision":
                fragment = state.get("fragment") or collision_fragment(builder, tile)
                if fragment is not None:
                    return 200, public_fragment(fragment)
            if state["state"] == "failed":
                return 404, {"state": "failed"}
            if state["state"] != "built":
                return 202, {k: state[k] for k in ("state", "eta") if k in state}
            fragment = state.get("fragment") or json.loads(builder.manifest(tile).read_text())
            return 200, public_fragment(fragment)

    PublicHandler.close_cached = staticmethod(ready.close)
    return PublicHandler


def main():
    settings = json.loads(Path(__file__).with_name("config.json").read_text())
    root = C.work_root() / "coarse"
    root.mkdir(parents=True, exist_ok=True)
    from turku3d.coarse.imagery import ImagerySources

    sources = ImagerySources(root)
    prefetcher = Prefetcher(sources, root=root)
    builder = Builder(
        root,
        workers=settings["workers"],
        inputs=lambda tile: [u.key for u in sources.missing_for_service(C.VisualTile.parse(tile), first_pass=True)],
        input_key=lambda tile: sources.input_key(C.VisualTile.parse(tile)),
        structural_input_key=lambda tile: sources.input_key(C.VisualTile.parse(tile), first_pass=True),
        enrichment_inputs=lambda tile: [u.key for u in sources.missing_for_service(C.VisualTile.parse(tile))],
        on_missing=lambda units: prefetcher.need([Unit.parse(u) for u in units]),
    )
    from turku3d.coarse.idle import IdlePrebuild, multiplayer_present, route_walk
    from turku3d.coarse.navigation import navigation
    idle = IdlePrebuild(builder, lambda: route_walk(json.loads(navigation(root)), settings['idle_tile_bounds']),
                        multiplayer_present)
    idle.start()
    prefetcher.on_landed = lambda unit: builder.landed()
    server = PublicServer(("127.0.0.1", int(os.environ.get("TURKU3D_DRAFT_PORT", settings["port"]))), public_handler(builder, prefetcher, settings))

    retirement = None

    def stop(*_):
        nonlocal retirement
        builder.drain()
        if retirement is None:
            # Caddy may still dispatch requests belonging to its retired config.
            # Keep the old listener available through the client's request timeout;
            # server_close then joins every admitted handler before workers exit.
            retirement = threading.Timer(35, server.shutdown)
            retirement.daemon = True
            retirement.start()

    signal.signal(signal.SIGTERM, stop)
    def activate(*_):
        with builder.lock:
            builder.draining = False
            builder.lock.notify_all()
    signal.signal(signal.SIGUSR1, activate)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if retirement is not None:
            retirement.cancel()
        server.server_close()
        server.RequestHandlerClass.close_cached()
        builder.wait_drained()
        builder.stop()
        prefetcher.stop()


if __name__ == "__main__":
    main()
