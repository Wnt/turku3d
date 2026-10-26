"""Synthetic queue scheduling, failures and shared-cache key tests."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import live_cache  # noqa: E402
import merge_queue  # noqa: E402
from gate_cache import cache_root, stage_key  # noqa: E402


class QueueTests(unittest.TestCase):
    def test_done_tasks_enqueue_once_and_publish_batch(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            tasks = root / "tasks"
            for number, name in enumerate(("one", "two"), 1):
                home = tasks / name
                home.mkdir(parents=True)
                (home / "meta.json").write_text(
                    json.dumps({"state": "done", "exit_code": 0, "finished": number})
                )
                (home / "last.md").write_text("## Result: DONE\n")
            record = {"tasks": {}, "events": []}
            with (
                patch.object(merge_queue.s, "root", return_value=root),
                patch.object(merge_queue.s, "tasks_root", return_value=tasks),
                patch.object(merge_queue, "rebase", return_value=True),
                patch("landing.batch") as batch,
            ):

                def landed(names, force):
                    self.assertEqual(names, ["one", "two"])
                    self.assertTrue(force)
                    for name in names:
                        path = tasks / name / "meta.json"
                        meta = json.loads(path.read_text())
                        meta["landed"] = {"commit": name, "live_build": {}}
                        path.write_text(json.dumps(meta))

                batch.side_effect = landed
                self.assertTrue(merge_queue.process(record))
                self.assertEqual(merge_queue.ready(record), [])
                self.assertFalse(merge_queue.process(record))
                batch.assert_called_once()

    def test_failing_member_is_resumed_and_other_member_retries(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            tasks = root / "tasks"
            for name in ("one", "two"):
                path = tasks / name
                path.mkdir(parents=True)
                (path / "meta.json").write_text(
                    json.dumps({"state": "done", "exit_code": 0, "finished": 1})
                )
                (path / "last.md").write_text("## Result: DONE\n")
            record = {"tasks": {}, "events": []}
            with (
                patch.object(merge_queue.s, "root", return_value=root),
                patch.object(merge_queue.s, "tasks_root", return_value=tasks),
                patch.object(merge_queue, "rebase", return_value=True),
                patch.object(merge_queue, "resume") as resume,
                patch(
                    "landing.batch",
                    side_effect=ValueError(
                        "batch bisected failing prefix ends at two; failing test"
                    ),
                ),
            ):
                self.assertTrue(merge_queue.process(record))
                resume.assert_called_once()
                self.assertEqual(resume.call_args.args[0], "two")
                self.assertEqual(
                    [r["task"] for r in record["events"] if r["kind"] == "retry"], ["one"]
                )

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
            approvals = published / "facade-approvals"
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
