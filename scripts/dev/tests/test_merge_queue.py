"""Synthetic merge-queue daemon rounds and shared-cache key tests."""

import json
import os
import subprocess
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
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.tasks = self.root / "tasks"
        for patcher in (
            patch.object(merge_queue.s, "root", return_value=self.root),
            patch.object(merge_queue.s, "tasks_root", return_value=self.tasks),
            patch.dict(os.environ, {"TURKU3D_COORD_ROOT": str(self.root / "coord")}),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def task(self, name, finished, first=None):
        path = self.tasks / name
        path.mkdir(parents=True, exist_ok=True)
        meta = {"state": "done", "exit_code": 0, "finished": finished}
        if first is not None:
            meta["first_finished"] = first
        (path / "meta.json").write_text(json.dumps(meta))
        (path / "last.md").write_text("## Result: DONE\n")

    def test_finished_tasks_land_once_in_first_finished_order(self):
        # "late" finished again after "early", but its first finish came first.
        self.task("early", finished=20)
        self.task("late", finished=30, first=10)
        record = {"tasks": {}, "events": []}
        calls = []

        def land(name, force=False):
            calls.append(name)
            path = self.tasks / name / "meta.json"
            meta = json.loads(path.read_text())
            meta["landed"] = {"commit": name}
            path.write_text(json.dumps(meta))
            return {"commit": name, "merge_wall_seconds": 0.5, "fast_gate_seconds": 2.0}

        with (
            patch("landing.land", side_effect=land),
            patch.object(merge_queue, "integrate") as integrate,
        ):
            merge_queue.process(record, published=self.root / "data")
            merge_queue.process(record, published=self.root / "data")
        self.assertEqual(calls, ["late", "early"])
        self.assertEqual(integrate.call_count, 2)
        landed = [row for row in record["events"] if row["kind"] == "landed"]
        self.assertEqual([row["commit"] for row in landed], ["late", "early"])
        self.assertEqual(landed[0]["fast_gate_seconds"], 2.0)

    def test_task_landed_by_hand_leaves_the_ready_list(self):
        self.task("manual", finished=20)
        record = {"tasks": {}, "events": []}
        merge_queue.discover(record)
        self.assertEqual(merge_queue.ready(record), ["manual"])
        path = self.tasks / "manual" / "meta.json"
        meta = json.loads(path.read_text())
        meta["landed"] = {"commit": "abc123"}
        path.write_text(json.dumps(meta))
        self.assertEqual(merge_queue.ready(record), [])
        self.assertEqual(record["tasks"]["manual"]["landed"], "abc123")

    def test_integration_starts_only_when_main_moved_past_last_integration(self):
        record = {"tasks": {}, "events": []}
        published = self.root / "data"
        child = type("Child", (), {"pid": 7, "poll": lambda self: None})()
        cases = (
            ("live", {}, False),  # live already names main
            ("other", {"to": "main-head"}, False),  # the last integration covered main
            ("other", {"to": "older"}, True),
        )
        for live, last, started in cases:
            with self.subTest(live=live, last=last):
                merge_queue._child = None
                with (
                    patch.object(merge_queue.s, "git", return_value="main-head"),
                    patch.object(merge_queue.integrator, "running", return_value=None),
                    patch.object(
                        merge_queue.integrator,
                        "live_commit",
                        return_value="main-head" if live == "live" else "older",
                    ),
                    patch.object(merge_queue.integrator, "last", return_value=last),
                    patch.object(merge_queue.integrator, "start", return_value=child) as start,
                ):
                    self.assertEqual(merge_queue.integrate(record, published), started)
                    self.assertEqual(start.called, started)
        # A running child is never doubled.
        with (
            patch.object(merge_queue.integrator, "start") as start,
            patch.object(merge_queue.integrator, "running", return_value=None),
        ):
            self.assertFalse(merge_queue.integrate(record, published))
            start.assert_not_called()
        merge_queue._child = None
        with patch.object(merge_queue.integrator, "running", return_value={"to": "x"}):
            self.assertFalse(merge_queue.integrate(record, published))

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
            merge_queue.s.rebase("main", origin)
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


if __name__ == "__main__":
    unittest.main()
