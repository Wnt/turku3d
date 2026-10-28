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
                        "code": "private",
                        "seconds": {"build": 12},
                        "inputs": ["/private/path"],
                    }
                )
            )
            requests = []
            builder = SimpleNamespace(
                root=root,
                lock=threading.Condition(),
                priority={"t0_1_1": 0},
                building={},
                pending={},
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
            self.assertEqual(set(json.loads(body)), {"coarse", "tile", "visual", "collision"})
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


if __name__ == "__main__":
    unittest.main()
