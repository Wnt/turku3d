"""Exercise the public boundary in memory, without a server or pipeline builds."""

import importlib.util
import io
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
                failure_count=0, failed={},
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
