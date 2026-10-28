"""The served tile set, its provenance and the publish-pages composition (served.py)."""

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import served  # noqa: E402
import tile_store  # noqa: E402

CORE = ["t0_0_0", "t0_0_1", "t0_1_0", "t0_1_1"]


def git(repo, *args):
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


class ServedTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.repo, self.data = root / "repo", root / "data"
        self.repo.mkdir()
        git(self.repo, "init", "-q", "-b", "main")
        self.base = self.commit("pipeline/a.py", "a = 1\n")
        release = self.data / "live-releases" / self.base
        (release / "tiles").mkdir(parents=True)
        self.manifest(release / "tiles")
        (release / "layers").mkdir(parents=True)
        (release / "textures/assembly").mkdir(parents=True)
        (release / "textures/assembly/MANIFEST.json").write_text('{"bound": ["b_1"]}')
        (release / "live-build.json").write_text(
            json.dumps({"commit": self.base, "tiles": CORE, "facades_bound": 1})
        )
        (self.data / "live").symlink_to(release)

    def commit(self, name, text):
        (self.repo / name).parent.mkdir(parents=True, exist_ok=True)
        (self.repo / name).write_text(text)
        git(self.repo, "add", "-A")
        git(self.repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", name)
        return git(self.repo, "rev-parse", "HEAD")

    def manifest(self, tiles, extra=()):
        visual = {tile: {"tris": 1000} for tile in [*CORE, *extra, "t1_0_0"]}
        (tiles / "manifest.json").write_text(json.dumps({"visual": visual}))

    def test_live_tiles_are_served_without_a_store_entry_for_the_key(self):
        head = self.commit("game/main.ts", "x\n")
        shipped = served.tiles(self.repo, head, self.data)
        self.assertEqual((shipped["source"], shipped["release"]), ("live", self.base))
        self.assertEqual(shipped["commit"], self.base)
        self.assertEqual(shipped["tile_commits"], {tile: self.base for tile in CORE})
        self.assertEqual(shipped["facades_bound"], 1)
        # Game code is not tile code: the live tiles are current.
        self.assertEqual(served.provenance(self.repo, head, shipped)["stale_by_code"], [])

    def test_the_store_entry_for_the_key_is_served_with_its_facade_count(self):
        head = self.commit("pipeline/a.py", "a = 2\n")
        dist = self.data.parent / "dist"
        dist.mkdir()
        self.manifest(dist, extra=["t0_2_0"])
        key = tile_store.key(self.repo, head, self.data)
        # An entry put before the facade count was recorded is not shipped.
        tile_store.put(self.data, dist, key, head, "integrator", ["t0_2_0"])
        self.assertEqual(served.tiles(self.repo, head, self.data)["source"], "live")
        shutil.rmtree(tile_store.root(self.data) / key)
        tile_store.put(self.data, dist, key, head, "integrator", ["t0_2_0"], {"facades_bound": 3})
        shipped = served.tiles(self.repo, head, self.data)
        self.assertEqual((shipped["source"], shipped["key"]), ("store", key))
        self.assertEqual(shipped["facades_bound"], 3)
        # The tiles the entry built are its commit's; the seeded rest stay the live release's.
        self.assertEqual(
            shipped["tile_commits"], {**{tile: self.base for tile in CORE}, "t0_2_0": head}
        )
        self.assertEqual(served.provenance(self.repo, head, shipped)["stale_by_code"], sorted(CORE))

    def test_built_keeps_the_previous_builder_of_tiles_it_did_not_rebuild(self):
        live = (self.data / "live").resolve()
        shipped = served.built("t" * 40, live, [*CORE, "t0_2_0"], ["t0_0_0"])
        self.assertEqual(shipped["tile_commits"]["t0_0_0"], "t" * 40)
        self.assertEqual(shipped["tile_commits"]["t0_1_1"], self.base)
        # A tile the previous release did not carry is always the build's own.
        self.assertEqual(shipped["tile_commits"]["t0_2_0"], "t" * 40)
        record = {"commit": "c", "verified_commit": "v", "tiles_from": {"commit": "b"}}
        self.assertEqual(served.builder(record), "b")
        self.assertEqual(served.builder({"commit": "c", "verified_commit": "v"}), "v")


if __name__ == "__main__":
    unittest.main()
