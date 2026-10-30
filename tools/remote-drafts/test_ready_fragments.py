"""Cached delivery cannot wait for freshness, and cannot mix published generations."""
import json
import tempfile
import threading
import unittest
from pathlib import Path

from ready_fragments import ReadyFragments


class ReadyFragmentsTest(unittest.TestCase):
    def test_delivers_while_revalidation_is_blocked_and_observes_republication(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tile = "t0_0_0"
            folder = root / tile
            (folder / "col").mkdir(parents=True)
            cells = {}
            for i in range(4):
                for j in range(4):
                    cid = f"c_{i}_{j}"
                    name = f"col/{cid}.col"
                    (folder / name).write_bytes(b"x")
                    cells[cid] = {"url": name, "bytes": 1, "sha256": "cell"}
            (folder / "vis.glb").write_bytes(b"old")
            value = {"coarse": True, "tile": tile, "pass_kind": "full", "code": "unchanged",
                     "visual": {tile: {"url": "vis.glb", "bytes": 3, "sha256": "old"}}, "collision": cells}
            manifest = folder / "manifest.json"
            manifest.write_text(json.dumps(value))
            started, release = threading.Event(), threading.Event()
            calls = []

            def refresh():
                calls.append(1)
                started.set()
                release.wait(2)
                return 200, value

            cache = ReadyFragments(root, lambda _: manifest.read_bytes())
            try:
                self.assertEqual(cache.get(tile, refresh)["visual"][tile]["sha256"], "old")
                self.assertTrue(started.wait(1))
                # Repeated clients get the complete pair while the source check is still blocked.
                for _ in range(20):
                    self.assertTrue(cache.get(tile, refresh)["stale"])
                self.assertEqual(len(calls), 1)
                updated = {**value, "visual": {tile: {"url": "vis.glb", "bytes": 3, "sha256": "new"}}}
                manifest.write_text(json.dumps(updated))
                self.assertEqual(cache.get(tile, refresh)["visual"][tile]["sha256"], "new")
                # Missing collision prevents publishing an otherwise complete-looking visual.
                (folder / "col/c_0_0.col").unlink()
                manifest.write_text(json.dumps({**updated, "built": "replacement"}))
                self.assertIsNone(cache.get(tile, refresh))
            finally:
                release.set()
                cache.close()


if __name__ == "__main__":
    unittest.main()
