"""Public HTTP boundary around the pinned draft producer; management stays local to systemd."""

from __future__ import annotations

import hashlib
import json
import re
import signal
from pathlib import Path
from urllib.parse import urlparse

from turku3d import config as C
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
from turku3d.coarse.sources import Sources, Unit


def allowed_origin(origin: str | None) -> bool:
    return (
        origin is None
        or origin == "https://wnt.github.io"
        or bool(re.fullmatch(r"http://localhost(?::[1-9][0-9]{0,4})?", origin))
    )


def public_route(path: str, bounds: list[int]) -> tuple[str, str | None]:
    if path == "/coarse/health":
        return "health", None
    parts = path.removeprefix("/coarse/").split("/", 1)
    if not path.startswith("/coarse/") or len(parts) != 2:
        return "invalid", None
    tile, name = parts
    if tile == "shared" and SHARED.fullmatch(name):
        return "asset", None
    if not TILE.fullmatch(tile) or not FILE.fullmatch(name):
        return "invalid", None
    _, i, j = tile.split("_")
    if not (bounds[0] <= int(i) <= bounds[2] and bounds[1] <= int(j) <= bounds[3]):
        return "invalid", None
    return ("manifest" if name == "manifest.json" else "asset"), tile


def public_handler(builder, prefetcher, settings):
    base = handler(builder, prefetcher)

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
            return super()._json(status, value)

        def do_POST(self):
            self._json(405, {"error": "method"})

        def do_OPTIONS(self):
            if not allowed_origin(self.headers.get("Origin")):
                return self._json(403, {"error": "origin"})
            return self._send(204, b"")

        def do_GET(self):
            if not allowed_origin(self.headers.get("Origin")):
                return self._json(403, {"error": "origin"})
            route, tile = public_route(urlparse(self.path).path, settings["tile_bounds"])
            if route == "health":
                return self._json(200, {"state": "online"})
            if route == "invalid":
                return self._json(404, {"error": "route"})
            name = urlparse(self.path).path.removeprefix("/coarse/").split("/", 1)[1]
            if route == "manifest":
                # Condition uses an RLock; hold admission through Builder.request so racing
                # requests cannot exceed the cap. Cached/running requests never consume a slot.
                with builder.lock:
                    active = set(builder.priority) | set(builder.building) | set(builder.pending)
                    if (
                        tile not in active
                        and not builder.built(tile)
                        and not builder.cached(tile)
                        and len(active) >= settings["queue_max"]
                    ):
                        return self._json(429, {"state": "queue-full"})
                    # Ignore client priority: one public player must not steer every player's queue.
                    t = C.VisualTile.parse(tile)
                    spawn_x, _, spawn_z = C.en_to_local(C.OFFICE_E, C.OFFICE_N, 0)
                    distance = (
                        (t.origin[0] + t.size / 2 - spawn_x) ** 2
                        + (t.origin[2] + t.size / 2 - spawn_z) ** 2
                    ) ** 0.5
                    state = builder.request(tile, distance)
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
                            for k in ("coarse", "tile", "visual", "collision", "roads")
                            if k in fragment
                        },
                        **(
                            {
                                "stale": True,
                                "refresh": {
                                    k: state["refresh"][k]
                                    for k in ("state", "eta")
                                    if k in state["refresh"]
                                },
                            }
                            if state.get("stale")
                            else {}
                        ),
                    },
                )
            if tile is not None and tile not in builder.servable and not immutable_payload(name):
                return self._json(404, {"error": "unbuilt"})
            path = builder.root / (tile or "shared") / name
            if not path.is_file():
                return self._json(404, {"error": "missing"})
            return self._send(
                200, path.read_bytes(), MIME[path.suffix], "public, max-age=0, must-revalidate"
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
        inputs=lambda tile: [u.key for u in sources.missing_for_service(C.VisualTile.parse(tile))],
        input_key=lambda tile: sources.input_key(C.VisualTile.parse(tile)),
        on_missing=lambda units: prefetcher.need([Unit.parse(u) for u in units]),
    )
    prefetcher.on_landed = lambda unit: builder.landed()
    server = Server(("127.0.0.1", settings["port"]), public_handler(builder, prefetcher, settings))

    def stop(*_):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        builder.stop()
        prefetcher.stop()
        server.server_close()


if __name__ == "__main__":
    main()
