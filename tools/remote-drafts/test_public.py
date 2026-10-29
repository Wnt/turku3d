"""Exercise the public boundary in memory, without a server or pipeline builds."""

import importlib.util
import io
import hashlib
import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace

spec = importlib.util.spec_from_file_location("public", Path(__file__).with_name("public.py"))
public = importlib.util.module_from_spec(spec)
spec.loader.exec_module(public)
from turku3d.coarse.demand import Demand


class PublicTest(unittest.TestCase):
    def test_origins_and_routes(self):
        for origin in (None, "https://wnt.github.io", "http://localhost:7761"):
            self.assertTrue(public.allowed_origin(origin))
        for origin in (
            "null",
            "https://evil.example",
            "https://wnt.github.io.evil",
            "http://wnt.github.io",
        ):
            self.assertFalse(public.allowed_origin(origin))
        bounds = [-2, -2, 2, 2]
        self.assertEqual(public.public_route("/coarse/navigation.json", bounds), ("navigation", None))
        self.assertEqual(
            public.public_route("/coarse/t0_-2_2/manifest.json", bounds), ("manifest", "t0_-2_2")
        )
        self.assertEqual(public.public_route("/coarse/shared/" + "a" * 64 + ".bin", bounds), ("asset", None))
        for path in (
            "/coarse/status",
            "/coarse/want",
            "/coarse/drain",
            "/coarse/t0_3_0/manifest.json",
            "/coarse/t1_0_0/vis.glb",
            "/coarse/t0_0_0/../secret",
            "/coarse/shared/../../secret",
        ):
            self.assertEqual(public.public_route(path, bounds), ("invalid", None))

    def test_admission_cache_privacy_and_cors(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "t0_0_0").mkdir()
            (root / "t0_0_0/manifest.json").write_text(
                json.dumps(
                    {
                        "coarse": True,
                        "tile": "t0_0_0",
                        "visual": {},
                        "collision": {},
                        "vehicles": [{"object_id": "osm:way:slot", "model": "sedan"}],
                        "vehicle_models": {"sedan/blue": {"url": "../shared/" + "a" * 64 + ".glb"}},
                        "code": "private",
                        "seconds": {"build": 12},
                        "inputs": ["/private/path"],
                    }
                )
            )
            requests = []
            builder = SimpleNamespace(
                root=root,
                build_health=__import__("turku3d.coarse.build_health", fromlist=["BuildHealth"]).BuildHealth(root),
                failure_count=0, failed={}, draining=False,
                lock=threading.Condition(),
                priority={"t0_1_1": 0},
                building={},
                pending={},
                demand=Demand(),
                servable={"t0_0_0"},
                cached=lambda tile: False,
                built=lambda tile: tile == "t0_0_0",
                manifest=lambda tile: root / tile / "manifest.json",
                request=lambda tile, distance: requests.append(tile) or {"state": "built"},
            )
            cls = public.public_handler(
                builder, None, {"queue_max": 1, "tile_bounds": [-2, -2, 2, 2]}
            )

            def call(path, method="GET", headers=None):
                h = object.__new__(cls)
                h.path, h.command = path, method
                h.headers = {"Origin": "https://wnt.github.io", **(headers or {})}
                h.wfile = io.BytesIO()
                h.response_headers = {}
                h.send_response = lambda status: setattr(h, "status", status)
                h.send_header = lambda k, v: h.response_headers.update({k: v})
                h.end_headers = lambda: None
                getattr(h, f"do_{method}")()
                return h.status, h.response_headers, h.wfile.getvalue()

            status, headers, body = call("/coarse/t0_2_2/manifest.json")
            self.assertEqual(status, 429)
            self.assertEqual(headers["Retry-After"], "5")
            self.assertEqual(requests, [])
            status, headers, body = call("/coarse/t0_0_0/manifest.json")
            self.assertEqual(status, 200)  # Cached tile bypasses the full build queue.
            self.assertEqual(headers["Access-Control-Allow-Origin"], "https://wnt.github.io")
            self.assertEqual(
                set(json.loads(body)),
                {"coarse", "tile", "visual", "collision", "vehicles", "vehicle_models"},
            )
            self.assertEqual(json.loads(body)["vehicles"][0]["object_id"], "osm:way:slot")
            (root / "shared").mkdir()
            (root / "shared" / ("a" * 64 + ".glb")).write_bytes(b"glTF-fixture")
            asset_status, asset_headers, asset_body = call("/coarse/shared/" + "a" * 64 + ".glb")
            self.assertEqual((asset_status, asset_body), (200, b"glTF-fixture"))
            self.assertEqual(asset_headers["Content-Type"], "model/gltf-binary")
            self.assertEqual(asset_headers["Cache-Control"], public.IMMUTABLE_CACHE)
            self.assertEqual(asset_headers["Timing-Allow-Origin"], "https://wnt.github.io")
            self.assertEqual(requests, ["t0_0_0"])
            status, _, body = call(
                "/coarse/t0_0_0/manifest.json", headers={"If-None-Match": headers["ETag"]}
            )
            self.assertEqual((status, body), (304, b""))
            self.assertEqual(call("/coarse/health", "HEAD")[2], b"")
            self.assertEqual(call("/coarse/drain", "POST")[0], 405)
            for path in ("/coarse/status", "/coarse/want", "/coarse/t0_3_0/manifest.json"):
                self.assertEqual(call(path)[0], 404)
            status, headers, _ = call("/coarse/health", headers={"Origin": "https://evil.example"})
            self.assertEqual(status, 403)
            self.assertNotIn("Access-Control-Allow-Origin", headers)
            self.assertEqual(call("/coarse/health", "OPTIONS")[0], 204)

    def test_retirement_joins_an_admitted_slow_response(self):
        import socket
        from http.server import BaseHTTPRequestHandler

        admitted, release, closed = threading.Event(), threading.Event(), threading.Event()

        class Slow(BaseHTTPRequestHandler):
            def do_GET(self):
                admitted.set()
                release.wait(2)
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"complete")
            def log_message(self, *_):
                pass

        server = public.PublicServer(("127.0.0.1", 0), Slow)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        with socket.create_connection(server.server_address, timeout=2) as sock:
            sock.sendall(b"GET / HTTP/1.0\r\n\r\n")
            self.assertTrue(admitted.wait(1))
            server.shutdown()
            closer = threading.Thread(target=lambda: (server.server_close(), closed.set()))
            closer.start()
            try:
                self.assertFalse(closed.wait(.02))
            finally:
                release.set()
                closer.join(2)
                worker.join(2)
            response = b""
            while part := sock.recv(4096):
                response += part
            self.assertTrue(response.endswith(b"complete"))
            self.assertTrue(closed.is_set())

    def test_parser_unsupported_method_and_exception_responses_have_cors(self):
        import socket
        from http.server import ThreadingHTTPServer

        with tempfile.TemporaryDirectory() as temp:
            builder = SimpleNamespace(root=Path(temp))
            cls = public.public_handler(builder, None, {"queue_max": 1, "tile_bounds": [-2, -2, 2, 2]})
            server = ThreadingHTTPServer(("127.0.0.1", 0), cls)
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            try:
                for method, path, expected in (("PUT", "/coarse/health", 501),
                                                ("GET", "/coarse/health", 503),
                                                ("OPTIONS", "/coarse/health", 204),
                                                ("GET", "/coarse/missing", 404)):
                    with socket.create_connection(server.server_address, timeout=2) as sock:
                        sock.sendall(f"{method} {path} HTTP/1.1\r\nHost: localhost\r\nOrigin: https://wnt.github.io\r\nConnection: close\r\n\r\n".encode())
                        response = b""
                        while part := sock.recv(65536):
                            response += part
                    headers = response.split(b"\r\n\r\n")[0].decode()
                    self.assertIn(f" {expected} ", headers)
                    self.assertIn("Access-Control-Allow-Origin: https://wnt.github.io", headers)
                    if expected == 503:
                        self.assertIn("Retry-After: 5", headers)
                        self.assertNotIn("AttributeError", response.decode())
            finally:
                server.shutdown()
                server.server_close()
                worker.join()

    def test_collision_bundle_is_content_addressed_and_reuses_unchanged_cells(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "t0_0_0/col").mkdir(parents=True)
            cells = {}
            for i in range(16):
                body = bytes([i]) * (i + 1)
                digest = hashlib.sha256(body).hexdigest()
                name = f"col/c_{i}_0.{digest}.col"
                (root / "t0_0_0" / name).write_bytes(body)
                cells[f"c_{i}_0"] = {"url": name, "bytes": len(body), "sha256": digest}
            fragment = {"tile": "t0_0_0", "visual": {"t0_0_0": {}}, "collision": cells}
            bundles = public.CollisionBundles(root)
            entry = bundles.describe(fragment)
            path = root / "t0_0_0" / entry["url"]
            body = path.read_bytes()
            self.assertEqual(hashlib.sha256(body).hexdigest(), entry["sha256"])
            self.assertEqual(body, b"".join((root / "t0_0_0" / cells[c]["url"]).read_bytes() for c in sorted(cells)))
            self.assertEqual(entry["bytes"], sum(e["bytes"] for e in cells.values()))
            self.assertIs(bundles.describe(fragment), entry)
            self.assertEqual(public.public_route(f"/coarse/t0_0_0/{entry['url']}", [-2,-2,2,2]), ("asset", "t0_0_0"))
            # A corrupt source never earns a content-addressed bundle.
            (root / "t0_0_0" / cells["c_0_0"]["url"]).write_bytes(b"bad")
            with self.assertRaises(ValueError):
                public.CollisionBundles(root).describe(fragment)

    def test_want_list_is_bounded_and_old_polls_cannot_resurrect_departed_tiles(self):
        from turku3d.coarse.service import Builder

        with tempfile.TemporaryDirectory() as temp:
            builder = Builder(Path(temp), code="test", workers=0)
            cls = public.public_handler(
                builder, None, {"queue_max": 24, "tile_bounds": [-2, -2, 2, 2]}
            )

            def call(path):
                h = object.__new__(cls)
                h.path, h.command, h.headers = path, "GET", {}
                h.wfile = io.BytesIO()
                h.send_response = lambda value: setattr(h, "status", value)
                h.send_header = lambda *_: None
                h.end_headers = lambda: None
                h.do_GET()
                return h.status, json.loads(h.wfile.getvalue())

            self.assertEqual(
                call("/coarse/demand?client=driver-one&tiles=t0_1_0&urgent=t0_1_0")[0], 200
            )
            self.assertEqual(call("/coarse/t0_1_0/manifest.json?client=driver-one")[0], 202)
            self.assertEqual(builder.order(), ["t0_1_0"])
            self.assertEqual(
                call("/coarse/demand?client=driver-one&tiles=t0_-1_0&urgent=t0_-1_0")[0], 200
            )
            self.assertEqual(
                call("/coarse/t0_1_0/manifest.json?client=driver-one"), (202, {"state": "deferred"})
            )
            self.assertNotIn("t0_1_0", builder.journal.rows)
            self.assertEqual(call("/coarse/demand?client=driver-one&tiles=t0_3_0")[0], 400)
            self.assertEqual(call("/coarse/demand?client=x&tiles=t0_1_0")[0], 400)
            self.assertEqual(
                call("/coarse/demand?client=driver-two&tiles=t0_1_0&visible=t0_0_0")[0], 400
            )
            builder.stop()



if __name__ == "__main__":
    unittest.main()
