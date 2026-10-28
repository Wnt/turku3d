"""Shared caches and the up-to-date rebase rule, on tiny synthetic inputs."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import game_build  # noqa: E402
import live_cache  # noqa: E402
import reuse_tiles  # noqa: E402
import sandbox  # noqa: E402
from gate_cache import cache_root, stage_key  # noqa: E402


class CacheTests(unittest.TestCase):
    def test_rebase_keeps_merge_history_already_on_main(self):
        # An integration branch merges two branches that each touch line.txt; replaying
        # its commits linearly would conflict, so an up-to-date branch must not move.
        with tempfile.TemporaryDirectory() as temp:
            origin = Path(temp)

            def git(*args):
                return subprocess.run(
                    ["git", *args], cwd=origin, check=True, text=True, capture_output=True
                ).stdout.strip()

            git("init", "-b", "main")
            git("config", "user.email", "queue@example.test")
            git("config", "user.name", "Queue Test")
            (origin / "line.txt").write_text("base\n")
            git("add", ".")
            git("commit", "-m", "base")
            git("checkout", "-b", "left")
            (origin / "line.txt").write_text("left\n")
            git("commit", "-am", "left")
            git("checkout", "-b", "integration", "main")
            (origin / "line.txt").write_text("right\n")
            git("commit", "-am", "right")
            subprocess.run(["git", "merge", "left"], cwd=origin, capture_output=True)
            (origin / "line.txt").write_text("both\n")
            git("commit", "-am", "merge left")
            head = git("rev-parse", "HEAD")
            sandbox.rebase("main", origin)
            self.assertEqual(git("rev-parse", "HEAD"), head)
            self.assertEqual(git("status", "--porcelain"), "")

    def test_gate_cache_key_is_sandbox_independent(self):
        with tempfile.TemporaryDirectory() as temp:
            roots = [Path(temp) / name for name in ("a", "b")]
            for root in roots:
                root.mkdir()
                (root / "source.py").write_text("print(1)\n")
            stage = {"name": "python", "triggers": ["*.py"]}
            envs = [
                {
                    "TURKU3D_DATA": str(Path(temp) / "data"),
                    "TURKU3D_WORK": str(root / "work"),
                    "TURKU3D_PORT_BASE": str(5180 + n * 10),
                }
                for n, root in enumerate(roots)
            ]
            keys = [
                stage_key(root, stage, ["source.py"], None, env, {})
                for root, env in zip(roots, envs, strict=True)
            ]
            self.assertEqual(keys[0], keys[1])
            self.assertEqual(cache_root(roots[0], envs[0]), cache_root(roots[1], envs[1]))
            (roots[1] / "source.py").write_text("print(2)\n")
            self.assertNotEqual(
                keys[0], stage_key(roots[1], stage, ["source.py"], None, envs[1], {})
            )

    def test_live_stage_cache_restores_and_invalidates_manifest_change(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo, work, published = root / "repo", root / "work", root / "data"
            repo.mkdir()
            layer = work / "build/layers/ground"
            layer.mkdir(parents=True)
            (layer / "MANIFEST.json").write_text('{"source":"one"}')
            textures = work / "textures"
            textures.mkdir()
            (textures / "asset.ktx2").write_bytes(b"verified")
            approvals = textures / "assembly"
            approvals.mkdir(parents=True)
            (approvals / "MANIFEST.json").write_text('{"source":"one"}')
            with patch.object(live_cache.s, "git", return_value="tree"):
                key = live_cache.digest(repo, work, published, "refresh", "")
                live_cache.store(published, work, "refresh", key, {})
                (textures / "asset.ktx2").unlink()
                self.assertEqual(live_cache.restore(published, work, "refresh", key), {})
                self.assertEqual((textures / "asset.ktx2").read_bytes(), b"verified")
                (approvals / "MANIFEST.json").write_text('{"source":"two"}')
                changed = live_cache.digest(repo, work, published, "refresh", "")
                self.assertNotEqual(key, changed)
                self.assertIsNone(live_cache.restore(published, work, "refresh", changed))

    def test_live_key_uses_declared_code_paths(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo, work, published = root / "repo", root / "work", root / "data"
            (repo / "game").mkdir(parents=True)
            (repo / "pipeline").mkdir()
            (repo / "game/main.ts").write_text("one")
            (repo / "pipeline/main.py").write_text("one")
            with patch.object(live_cache.s, "git", return_value="game/main.ts\npipeline/main.py"):
                first = live_cache.digest(repo, work, published, "game-build", "")
                (repo / "pipeline/main.py").write_text("two")
                self.assertEqual(first, live_cache.digest(repo, work, published, "game-build", ""))
                (repo / "game/main.ts").write_text("two")
                self.assertNotEqual(
                    first, live_cache.digest(repo, work, published, "game-build", "")
                )

    def test_game_key_ignores_data_and_previous_stage_but_tracks_build_environment(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "game").mkdir()
            (root / "game/main.ts").write_text("source")
            (root / "work/build/layers").mkdir(parents=True)
            layer = root / "work/build/layers/MANIFEST.json"
            layer.write_text("one")
            with patch.object(live_cache.s, "git", return_value="game/main.ts"):
                first = live_cache.digest(root, root / "work", root, "game-build", "old-tiles")
                layer.write_text("two")
                self.assertEqual(
                    first, live_cache.digest(root, root / "work", root, "game-build", "new-tiles")
                )
                self.assertNotEqual(
                    live_cache.game_key(root, {"VITE_PUBLIC_RELEASE": "1"}),
                    live_cache.game_key(root, {"VITE_PUBLIC_RELEASE": ""}),
                )
                self.assertNotEqual(
                    live_cache.game_key(root, {"VITE_DRAFT_URL": "one"}),
                    live_cache.game_key(root, {"VITE_DRAFT_URL": "two"}),
                )

    def test_game_output_stamp_rejects_missing_corrupt_and_added_files(self):
        with tempfile.TemporaryDirectory() as temp:
            work = Path(temp)
            output = work / "build/game"
            output.mkdir(parents=True)
            index = output / "index.html"
            index.write_text("good")
            first = game_build.outputs(work, False)
            self.assertIsNone(game_build.outputs(work, True))
            index.write_text("bad")
            self.assertNotEqual(first, game_build.outputs(work, False))
            index.write_text("good")
            (output / "extra.js").write_text("stale")
            self.assertNotEqual(first, game_build.outputs(work, False))
            index.unlink()
            self.assertIsNone(game_build.outputs(work, False))

    def test_tile_reuse_preserves_local_work_and_freezes_the_cloned_list(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            work, data = root / "work", root / "data"
            release = data / "releases/one"
            for name in ("layers", "textures", "tiles"):
                (release / name).mkdir(parents=True)
                (release / name / "asset").write_text("published")
            (data / "live").symlink_to(release)
            (release / "live-build.json").write_text(
                json.dumps({"tiles": list(reuse_tiles.C.CORE_TILES)})
            )
            local = work / "build/layers/asset"
            local.parent.mkdir(parents=True)
            local.write_text("local")
            with patch.dict(os.environ, {"TURKU3D_WORK": str(work)}):
                result = reuse_tiles.reuse(work, data)
                self.assertEqual(result["copied"], ["textures", "dist/tiles"])
                self.assertEqual(local.read_text(), "local")
                self.assertEqual(
                    reuse_tiles.C.read_playable_tiles(work / reuse_tiles.C.PLAYABLE_TILES_FILE),
                    list(reuse_tiles.C.CORE_TILES),
                )
                self.assertEqual(reuse_tiles.reuse(work, data)["copied"], [])


if __name__ == "__main__":
    unittest.main()
