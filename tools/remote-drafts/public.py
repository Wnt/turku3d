"""Public HTTP boundary around the pinned draft producer; management stays local to systemd."""

from __future__ import annotations

import hashlib
import json
import os
import re
import signal
import threading
from collections import OrderedDict
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from turku3d import config as C
from turku3d.coarse.prefetch import Prefetcher
from turku3d.coarse.demand import MAX_TILES
from turku3d.coarse.collision import fragment as collision_fragment
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
from turku3d.coarse.sources import Sources, Unit


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
                self.entries.move_to_end(key)
                return self.entries[key]
            bodies = [(self.root / tile / e["url"]).read_bytes() for _, e in rows]
            for body, (_, entry) in zip(bodies, rows):
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
        or bool(re.fullmatch(r"http://localhost(?::[1-9][0-9]{0,4})?", origin))
    )


def public_route(path: str, bounds: list[int]) -> tuple[str, str | None]:
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


def public_handler(builder, prefetcher, settings):
    base = handler(builder, prefetcher)
    instance = os.urandom(8).hex()
    bundles = CollisionBundles(builder.root)

    class PublicHandler(base):
        server_version = "TurkuDrafts"
        sys_version = ""

        def _send(self, status, body, kind="application/json", cache="no-cache"):
            origin = self.headers.get("Origin")
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
            if status in (429, 503):
                self.send_header("Retry-After", "5")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)
            # No IP, query, headers, player position or user-agent in request logs.
            route, _ = public_route(urlparse(self.path).path, settings["tile_bounds"])
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
            idle = getattr(builder, 'idle', None)
            # Only game clients carry a client id; probes that merely send a Pages Origin must not hold off prebuilding.
            if (idle is not None and route in ('demand', 'manifest', 'collision')
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
                        return self._json(429, {"state": "queue-full"})
                    builder.demand.prune(builder)
                    builder.lock.notify_all()
                    return self._json(200, {"version": builder.version, "queued": len(builder.priority),
                                            "building": sorted(builder.building)})
            if route == "navigation":
                return super().do_GET()
            if route == "health":
                return self._json(200, {"state": "online", "instance": instance,
                                        "failure_count": builder.failure_count, "failed_tiles": len(builder.failed),
                                        "active_builds": len(builder.building), "draining": builder.draining,
                                        "prebuild": idle.status() if idle else None,
                                        "build_health": builder.build_health.snapshot(
                                            bool(builder.demand.clients) or bool(idle and idle.online))})
            if route == "invalid":
                return self._json(404, {"error": "route"})
            name = urlparse(self.path).path.removeprefix("/coarse/").split("/", 1)[1]
            if route in ("manifest", "collision"):
                # Condition uses an RLock; hold admission through Builder.request so racing
                # requests cannot exceed the cap. Cached/running requests never consume a slot.
                with builder.lock:
                    q = parse_qs(urlparse(self.path).query)
                    client = q.get("client", [None])[0]
                    if client is not None and not builder.demand.wants(client, tile):
                        return self._json(202, {"state": "deferred"})
                    if route == "collision":
                        fragment = collision_fragment(builder, tile)
                        if fragment is not None:
                            return self._json(200, fragment)
                    active = set(builder.priority) | set(builder.building) | set(builder.pending)
                    if (
                        tile not in active
                        and not builder.built(tile)
                        and not ((builder.root / tile / "collision.json").exists()
                                 and collision_fragment(builder, tile) is not None)
                        and len(active) >= max(settings["queue_max"], min(256, 12 * len(builder.demand.clients)))
                        and not builder.demand.admit(builder, tile)
                    ):
                        return self._json(429, {"state": "queue-full"})
                    lease = builder.demand.rank(tile) if hasattr(builder, "demand") else None
                    # Legacy clients/prewarming have background priority, without player motion.
                    t = C.VisualTile.parse(tile)
                    spawn_x, _, spawn_z = C.en_to_local(C.OFFICE_E, C.OFFICE_N, 0)
                    distance = ((t.origin[0] + t.size / 2 - spawn_x) ** 2
                                + (t.origin[2] + t.size / 2 - spawn_z) ** 2) ** 0.5
                    distance = lease[1] if lease is not None else distance + 1e6
                    state = builder.request(tile, distance)
                if route == "collision":
                    fragment = collision_fragment(builder, tile)
                    if fragment is not None:
                        return self._json(200, fragment)
                if state["state"] == "failed":
                    return self._json(404, {"state": "failed"})
                if state["state"] != "built":
                    return self._json(202, {k: state[k] for k in ("state", "eta") if k in state})
                fragment = json.loads(builder.manifest(tile).read_text())
                return self._json(
                    200,
                    {
                        **{
                            k: fragment[k]
                            for k in ("coarse", "tile", "visual", "collision", "roads", "pass_kind",
                                      "vehicles", "vehicle_models")
                            if k in fragment
                        },
                        **(
                            {
                                "stale": True,
                                "failure": {"state": "failed"},
                            }
                            if state.get("stale")
                            else {}
                        ),
                    },
                )
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

    return PublicHandler


def main():
    settings = json.loads(Path(__file__).with_name("config.json").read_text())
    root = C.work_root() / "coarse"
    root.mkdir(parents=True, exist_ok=True)
    sources = Sources()
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
    idle = IdlePrebuild(builder, lambda: route_walk(json.loads(navigation(root)), settings['tile_bounds']),
                        multiplayer_present)
    idle.start()
    prefetcher.on_landed = lambda unit: builder.landed()
    server = Server(("127.0.0.1", int(os.environ.get("TURKU3D_DRAFT_PORT", settings["port"]))), public_handler(builder, prefetcher, settings))

    def stop(*_):
        builder.drain()
        raise KeyboardInterrupt

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
        builder.wait_drained()
        builder.stop()
        prefetcher.stop()
        server.server_close()


if __name__ == "__main__":
    main()
