"""Tile store keys and entries (tile_store.py) on a throwaway git repository and data root."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tile_store  # noqa: E402

CORE = ["t0_0_0", "t0_0_1", "t0_1_0", "t0_1_1"]


def listed(tiles):
    return json.dumps(
        {"level": 0, "tiles": tiles, "core_bbox_en": [23459500.0, 6704000.0, 23460000.0, 6704500.0]}
    )


def git(repo, *args):
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


class TileStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.repo, self.data = root / "repo", root / "data"
        for path in (self.repo / "pipeline", self.repo / "game", self.data):
            path.mkdir(parents=True)
        git(self.repo, "init", "-q", "-b", "main")
        (self.data / "playable-tiles.json").write_text(listed(CORE))

    def tearDown(self):
        self.temp.cleanup()

    def commit(self, name, text):
        (self.repo / name).write_text(text)
        git(self.repo, "add", "-A")
        git(self.repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", name)
        return git(self.repo, "rev-parse", "HEAD")

    def test_key_follows_tile_inputs_and_data_but_not_game_code(self):
        first = self.commit("pipeline/a.py", "a = 1\n")
        game = self.commit("game/main.ts", "x\n")
        self.assertEqual(
            tile_store.key(self.repo, first, self.data), tile_store.key(self.repo, game, self.data)
        )
        pipeline = self.commit("pipeline/a.py", "a = 2\n")
        self.assertNotEqual(
            tile_store.key(self.repo, game, self.data),
            tile_store.key(self.repo, pipeline, self.data),
        )
        before = tile_store.key(self.repo, pipeline, self.data)
        (self.data / "playable-tiles.json").write_text(listed([*CORE, "t0_2_0"]))
        self.assertNotEqual(before, tile_store.key(self.repo, pipeline, self.data))
        self.assertNotEqual(tile_store.quick_key(before), before)

    def test_put_resolve_keep_and_prune(self):
        commit = self.commit("pipeline/a.py", "a = 1\n")
        tiles = Path(self.temp.name) / "dist/tiles"
        (tiles / "visual").mkdir(parents=True)
        (tiles / "manifest.json").write_text("{}")
        (tiles / "visual/t0_0_0.glb").write_bytes(b"glb")
        self.assertEqual(tile_store.resolve(self.repo, commit, self.data)["integrator"], None)
        full = tile_store.key(self.repo, commit, self.data)
        entry = tile_store.put(self.data, tiles, full, commit, "integrator", ["t0_0_0"])
        self.assertEqual((entry / "visual/t0_0_0.glb").read_bytes(), b"glb")
        found = tile_store.resolve(self.repo, commit, self.data)
        self.assertEqual(found["integrator"], str(entry))
        self.assertIsNone(found["localhost"])
        record = tile_store.entry(self.data, full)
        self.assertEqual(
            (record["commit"], record["source"], record["tiles"]),
            (commit, "integrator", ["t0_0_0"]),
        )
        # An existing entry is immutable: a second put keeps the first bytes.
        (tiles / "visual/t0_0_0.glb").write_bytes(b"changed")
        tile_store.put(self.data, tiles, full, commit, "integrator")
        self.assertEqual((entry / "visual/t0_0_0.glb").read_bytes(), b"glb")
        index = (self.data / "tile-store/index.jsonl").read_text().splitlines()
        self.assertEqual(len(index), 1)
        for n in range(3):
            tile_store.put(self.data, tiles, f"k{n}", commit, "localhost")
        tile_store.prune(self.data, keep=2)
        kept = sorted(p.name for p in (self.data / "tile-store").iterdir() if p.is_dir())
        self.assertEqual(kept, ["k1", "k2"])

    def test_put_without_a_manifest_stores_nothing(self):
        commit = self.commit("pipeline/a.py", "a = 1\n")
        empty = Path(self.temp.name) / "empty"
        empty.mkdir()
        self.assertIsNone(tile_store.put(self.data, empty, "k", commit, "integrator"))


if __name__ == "__main__":
    unittest.main()
