"""Landing classes from tiny synthetic repositories and data roots."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import landing_class as lc  # noqa: E402

CORE = list(lc.C.CORE_TILES)


class LandingClassTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "class@example.test")
        self.git("config", "user.name", "Class Test")
        self.base = self.commit({"README.md": "base"})
        self.published = self.root / "data"
        self.release = self.published / "live-releases/g"
        (self.release / "textures/assembly").mkdir(parents=True)
        (self.release / "layers/ground").mkdir(parents=True)
        (self.release / "live-build.json").write_text(json.dumps({"commit": "g", "tiles": CORE}))
        (self.published / "live").symlink_to(self.release)
        (self.published / "build").mkdir()
        (self.published / "build/layers").symlink_to(self.release / "layers")
        self.approve(["a_1"])
        (self.release / "textures/assembly/MANIFEST.json").write_bytes(
            (self.published / "facade-approvals/assembly/MANIFEST.json").read_bytes()
        )

    def git(self, *args):
        return subprocess.run(
            ["git", *args], cwd=self.repo, check=True, text=True, capture_output=True
        ).stdout.strip()

    def commit(self, files):
        for name, text in files.items():
            (self.repo / name).parent.mkdir(parents=True, exist_ok=True)
            (self.repo / name).write_text(text)
        self.git("add", ".")
        self.git("commit", "-q", "-m", "change")
        return self.git("rev-parse", "HEAD")

    def approve(self, bound):
        path = self.published / "facade-approvals/assembly/MANIFEST.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"bound": bound}))

    def delta(self):
        return lc.data_delta(lc.data_parts(self.published), lc.release_parts(self.release))

    def classify(self, files):
        head = self.commit(files)
        return lc.classify(self.repo, self.base, head, self.delta(), self.published)

    def test_paths_fall_into_the_conservative_allowlists(self):
        self.assertEqual(
            [
                lc.path_class(path)
                for path in (
                    "docs/reports/x.md",
                    "docs/published-images.toml",
                    "game/README.md",
                    "LICENSE",
                    "game/public/assets/voice/fi/hello.ogg",
                    "game/public/assets/content/addresses.json",
                    "game/public/assets/audio/engine.ogg",
                    "game/src/main.ts",
                    "pipeline/turku3d/refresh.py",
                    "scripts/dev/watch.py",
                    "data-manifests/areas/office-block.yaml",
                    "textures/prompts/facade.txt",
                    "newdir/file.bin",
                )
            ],
            ["inert"] * 4 + ["data"] * 2 + ["code"] * 7,
        )

    def test_docs_only_landing_is_inert_with_no_tiles(self):
        landing = self.classify({"docs/reports/r.md": "r", "docs/published-images.toml": "x"})
        self.assertEqual(
            landing,
            {
                "class": "inert",
                "paths": ["docs/published-images.toml", "docs/reports/r.md"],
                "changed_paths": 2,
                "data_delta": [],
                "tiles": [],
            },
        )

    def test_voice_clip_is_data_without_tiles_and_code_dominates(self):
        voice = {"game/public/assets/voice/fi/a.ogg": "clip", "docs/a.md": "note"}
        landing = self.classify(voice)
        self.assertEqual((landing["class"], landing["tiles"]), ("data", []))
        landing = self.classify({**voice, "game/src/main.ts": "code"})
        self.assertEqual((landing["class"], landing["tiles"]), ("code", CORE))

    def test_data_root_delta_makes_an_inert_landing_data(self):
        self.approve(["a_1", "a_2"])
        landing = self.classify({"docs/reports/facades.md": "report"})
        self.assertEqual((landing["class"], landing["data_delta"]), ("data", ["facade-approvals"]))
        # Facade textures pack into every tile.
        self.assertEqual(landing["tiles"], CORE)

    def test_new_listed_tile_is_the_only_affected_tile(self):
        lc.C.write_playable_tiles(self.published / "playable-tiles.json", [*CORE, "t0_1_-1"])
        landing = self.classify({"docs/reports/tile.md": "report"})
        self.assertEqual((landing["class"], landing["data_delta"]), ("data", ["playable-tiles"]))
        self.assertEqual(landing["tiles"], ["t0_1_-1"])

    def test_layer_manifest_change_is_a_layers_delta(self):
        (self.published / "build/layers").unlink()
        (self.published / "build/layers/ground").mkdir(parents=True)
        (self.published / "build/layers/ground/MANIFEST.json").write_text("{}")
        self.assertEqual(self.delta(), ["layers"])

    def test_parts_match_the_live_release_and_need_no_tile_list(self):
        self.assertEqual(self.delta(), [])
        self.assertFalse((self.published / "playable-tiles.json").exists())
        # A baseline without parts (no earlier ledger row) counts every part as changed.
        self.assertEqual(lc.data_delta(lc.data_parts(self.published), None), list(lc.PARTS))

    def test_paths_are_capped_and_counted(self):
        files = {f"docs/n{index:02}.md": "n" for index in range(lc.PATH_CAP + 5)}
        landing = self.classify(files)
        self.assertEqual((len(landing["paths"]), landing["changed_paths"]), (lc.PATH_CAP, 45))


if __name__ == "__main__":
    unittest.main()
