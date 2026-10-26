"""Synthetic queue scheduling, failures and shared-cache key tests."""

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import live_cache  # noqa: E402
import merge_queue  # noqa: E402
import verification  # noqa: E402
import warm_queue  # noqa: E402
from gate_cache import cache_root, stage_key  # noqa: E402


class QueueTests(unittest.TestCase):
    def test_check_deadline_stops_slow_command(self):
        with tempfile.TemporaryDirectory() as temp:
            start = time.monotonic()
            result = merge_queue.s.run_group(
                [sys.executable, "-c", "import time; time.sleep(5)"],
                cwd=Path(temp),
                env={
                    **os.environ,
                    "TURKU3D_WORK": temp,
                    "TURKU3D_CHECK_DEADLINE": str(start + 0.05),
                },
            )
            self.assertEqual(result.returncode, 124)
            self.assertIn("SLOW", result.stdout)
            self.assertLess(time.monotonic() - start, 3)

    def test_receipt_checks_tree_inputs_and_release_bytes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo, published = root / "repo", root / "published"
            repo.mkdir()
            release = published / "live-releases" / "key"
            release.mkdir(parents=True)
            (release / "live-build.json").write_text('{"commit":"head"}')
            (release / "asset.bin").write_bytes(b"original")
            record = {
                "version": 1,
                "head": "head",
                "tree": "tree",
                "main": "base",
                "checks": {"gate": {"python": "hash"}, "live": {"tile-build": "hash"}},
            }
            record["release_id"] = verification.digest(
                {"tree": record["tree"], "checks": record["checks"]}
            )
            release.rename(release.parent / record["release_id"])
            release = release.parent / record["release_id"]
            record["artifact_hash"] = verification.artifact_hash(release)

            def git(*args, cwd):
                return {
                    ("rev-parse", "HEAD"): "head",
                    ("rev-parse", "HEAD^{tree}"): "tree",
                    ("rev-parse", "main"): "base",
                }[args]

            with patch.object(verification.s, "git", side_effect=git):
                self.assertEqual(verification.validate(record, repo, published), release)
                (release / "asset.bin").write_bytes(b"changed")
                with self.assertRaisesRegex(ValueError, "changed"):
                    verification.validate(record, repo, published)
                (release / "asset.bin").write_bytes(b"original")
                record["checks"]["gate"]["python"] = "other"
                with self.assertRaisesRegex(ValueError, "input hash"):
                    verification.validate(record, repo, published)

    def test_queue_runs_without_optional_warm_browser(self):
        result = type("Result", (), {"returncode": 1})()
        with (
            patch.object(warm_queue.s, "run", return_value=result),
            patch.object(warm_queue.s, "new") as create,
        ):
            with warm_queue.service() as state:
                self.assertIsNone(state)
            create.assert_not_called()

    def test_conflicting_branch_is_aborted_and_routed_back(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            origin = root / "origin"
            origin.mkdir()

            def git(*args, cwd=origin):
                return subprocess.run(
                    ["git", *args], cwd=cwd, check=True, text=True, capture_output=True
                ).stdout.strip()

            git("init", "-b", "main")
            git("config", "user.email", "queue@example.test")
            git("config", "user.name", "Queue Test")
            (origin / "line.txt").write_text("base\n")
            git("add", ".")
            git("commit", "-m", "base")
            home = root / "worker"
            repo = home / "repo"
            home.mkdir()
            git("worktree", "add", "-b", "wt/worker", str(repo))
            (repo / "line.txt").write_text("worker\n")
            git("add", ".", cwd=repo)
            git("commit", "-m", "worker", cwd=repo)
            (origin / "line.txt").write_text("main\n")
            git("add", ".")
            git("commit", "-m", "main")
            with (
                patch.object(merge_queue.s, "sandbox", return_value=home),
                patch.object(merge_queue, "resume") as resume,
            ):
                self.assertFalse(merge_queue.rebase("worker", {"tasks": {}, "events": []}))
                self.assertIn("rebase conflict", resume.call_args.args[1])
                self.assertEqual(git("status", "--porcelain", cwd=repo), "")
                self.assertNotEqual(git("rev-parse", "HEAD", cwd=repo), git("rev-parse", "main"))

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
                patch.object(merge_queue, "reverify"),
                patch.object(merge_queue.s, "sandbox", return_value=root),
                patch.object(merge_queue.s, "git", return_value="main"),
                patch("landing.fast") as fast,
            ):

                def landed(name):
                    path = tasks / name / "meta.json"
                    meta = json.loads(path.read_text())
                    meta["landed"] = {"commit": name, "live_build": {}}
                    path.write_text(json.dumps(meta))
                    return {"commit": name, "merge_wall_seconds": 0.01, "verification": name}

                fast.side_effect = landed
                self.assertTrue(merge_queue.process(record))
                self.assertEqual(merge_queue.ready(record), [])
                self.assertFalse(merge_queue.process(record))
                self.assertEqual(fast.call_count, 2)

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
                patch.object(merge_queue, "reverify"),
                patch.object(merge_queue.s, "sandbox", return_value=root),
                patch.object(merge_queue.s, "git", return_value="main"),
                patch.object(merge_queue, "resume") as resume,
                patch(
                    "landing.fast",
                    side_effect=[
                        {"commit": "one", "merge_wall_seconds": 0.01, "verification": "one"},
                        ValueError("two: verification failed"),
                    ],
                ),
            ):
                self.assertTrue(merge_queue.process(record))
                resume.assert_called_once()
                self.assertEqual(resume.call_args.args[0], "two")
                self.assertIn("published", record["tasks"]["one"])

    def test_verification_failure_does_not_block_next_task(self):
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
            calls = []

            def fast(name):
                calls.append(name)
                if name == "one":
                    raise ValueError("stale precheck")
                return {"commit": name, "merge_wall_seconds": 0.01, "verification": name}

            with (
                patch.object(merge_queue.s, "root", return_value=root),
                patch.object(merge_queue.s, "tasks_root", return_value=tasks),
                patch.object(merge_queue, "rebase", return_value=True),
                patch.object(merge_queue, "reverify"),
                patch.object(merge_queue.s, "sandbox", return_value=root),
                patch.object(merge_queue.s, "git", return_value="main"),
                patch.object(merge_queue, "resume") as resume,
                patch("landing.fast", side_effect=fast),
            ):
                self.assertTrue(merge_queue.process(record))
                self.assertEqual(calls, ["one", "two"])
                self.assertEqual(resume.call_args.args[0], "one")
                self.assertIn("published", record["tasks"]["two"])

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
