"""Synthetic integration batches: publish, skip, bisect and revert, flake guard, facades."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import coordination  # noqa: E402
import integrator  # noqa: E402
import live_build  # noqa: E402

GATE = "#!/bin/sh\necho PASS fast-gate 0.01s\n"


class IntegratorTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.wt = self.root / "wt"
        self.tasks = self.root / "tasks"
        self.published = self.root / "data"
        env = {
            "TURKU3D_WT_ROOT": str(self.wt),
            "TURKU3D_TASKS_ROOT": str(self.tasks),
            "TURKU3D_COORD_ROOT": str(self.root / "coord"),
            "TURKU3D_SLOT_DIR": str(self.root / "slots"),
            "TURKU3D_SANDBOX": "main",
        }
        patcher = patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.main = self.root / "origin"
        self.main.mkdir()
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "integrator@example.test")
        self.git("config", "user.name", "Integrator Test")
        (self.main / "scripts/dev").mkdir(parents=True)
        (self.main / "scripts/dev/gate").write_text(GATE)
        (self.main / "scripts/dev/gate").chmod(0o755)
        (self.main / "game").mkdir()
        (self.main / "game/facades.txt").write_text("2\n")
        self.git("add", ".")
        self.git("commit", "-q", "-m", "base")
        self.base = self.git("rev-parse", "HEAD")
        home = self.wt / integrator.NAME
        home.mkdir(parents=True)
        self.git("worktree", "add", "-q", "--detach", str(home / "repo"), "main")
        self.work = home / "work"
        (home / "sandbox.env").write_text(f"export TURKU3D_WORK={self.work}\n")
        self.release(self.base)
        self.builds = []
        self.chrome_flakes = 0

    def git(self, *args, cwd=None):
        return subprocess.run(
            ["git", *args], cwd=cwd or self.main, check=True, text=True, capture_output=True
        ).stdout.strip()

    def outputs(self, work, facades):
        for name in ("build/game", "dist/tiles", "build/layers", "textures"):
            (work / name).mkdir(parents=True, exist_ok=True)
            (work / name / "asset.bin").write_text(str(facades))
        visual = {
            tile: {"tris": 1000, "ground_texture_bytes": 1, "wire_bytes": 1}
            for tile in live_build.TILES
        }
        manifest = work / "dist/tiles/manifest.json"
        manifest.write_text(json.dumps({"visual": visual, "spawn": [0, 0, 0]}))
        return manifest

    def release(self, commit):
        work = self.root / "seed"
        self.outputs(work, 2)
        report = {"facades_bound": 2, "seconds": {}, "cache_hits": []}
        for parent in ("build", "dist"):
            (self.published / parent).mkdir(parents=True, exist_ok=True)
        live_build.prepare(work, self.published, commit, report)
        live_build.publish(self.published, commit)

    def fake_build(self, repo, work, published, env, log, reason=None, previous=None):
        """Stands in for the real stages: red on game/bad.ts, real facade inspection."""
        self.builds.append(self.git("rev-parse", "HEAD", cwd=repo))
        self.assertEqual(previous, (published / "live").resolve())
        log.write_text("== tile-build: make tile-build ==\n")
        if (repo / "game/bad.ts").exists():
            raise live_build.StageFailed("tile-build", f"tile-build failed (2); {log}")
        if self.chrome_flakes:
            self.chrome_flakes -= 1
            raise live_build.StageFailed("chrome", f"chrome failed (1); {log}")
        facades = int((repo / "game/facades.txt").read_text())
        manifest = self.outputs(work, facades)
        report = live_build.inspect(manifest, facades, live_build.baseline(published), reason)
        return {**report, "seconds": {"tile-build": 0.01}, "cache_hits": ["refresh"]}

    def land(self, name, path, text, reason=None):
        (self.main / path).parent.mkdir(parents=True, exist_ok=True)
        (self.main / path).write_text(text)
        self.git("add", ".")
        self.git("commit", "-q", "-m", name)
        commit = self.git("rev-parse", "HEAD")
        (self.tasks / name).mkdir(parents=True, exist_ok=True)
        (self.tasks / name / "meta.json").write_text(json.dumps({"state": "done"}))
        row = {"name": name, "commit": commit, "facade_regression_reason": reason}
        ledger = self.wt / ".registry/landed.jsonl"
        ledger.parent.mkdir(parents=True, exist_ok=True)
        with ledger.open("a") as file:
            file.write(json.dumps(row) + "\n")
        return commit

    def integrate(self):
        with patch.object(live_build, "build", side_effect=self.fake_build):
            return integrator.run_once(self.published, self.main)

    def test_green_batch_publishes_main_head(self):
        self.land("one", "game/a.ts", "a")
        head = self.land("two", "game/b.ts", "b")
        row = self.integrate()
        self.assertEqual((row["result"], row["from"], row["to"]), ("green", self.base, head))
        self.assertEqual(integrator.live_commit(self.published), head)
        self.assertEqual(
            (row["builds"], row["cache_hits"], row["facades_bound"]), (1, ["refresh"], 2)
        )
        self.assertEqual(integrator.last(self.published), row)
        self.assertIsNone(self.integrate())

    def test_docs_only_batch_publishes_nothing(self):
        self.land("docs", "docs/notes.md", "prose")
        head = self.land("tooling", "scripts/dev/watch.py", "print()")
        row = self.integrate()
        self.assertEqual(row["result"], "skipped")
        self.assertEqual(self.builds, [])
        self.assertEqual(integrator.live_commit(self.published), self.base)
        self.assertEqual(row["to"], head)

    def test_bad_commit_in_batch_of_five_is_bisected_and_reverted(self):
        commits = [
            self.land(f"task-{n}", f"game/{'bad' if n == 3 else f'f{n}'}.ts", str(n))
            for n in range(1, 6)
        ]
        row = self.integrate()
        self.assertEqual(row["result"], "red")
        self.assertEqual((row["culprit"], row["culprit_task"]), (commits[2], "task-3"))
        self.assertLessEqual(row["builds"], 3)
        self.assertEqual(row["failure"]["stage"], "tile-build")
        self.assertEqual(integrator.live_commit(self.published), self.base)
        # The revert landed on main through the fast gate and removed only the culprit.
        main = self.git("rev-parse", "main")
        self.assertEqual(row["revert"]["commit"], main)
        self.assertEqual(self.git("rev-parse", "main~1"), commits[-1])
        self.assertFalse((self.main / "game/bad.ts").exists())
        self.assertTrue((self.main / "game/f5.ts").exists())
        landed = (self.wt / ".registry/landed.jsonl").read_text().splitlines()
        self.assertEqual(json.loads(landed[-1])["reverts"], f"{commits[1]}..{commits[2]}")
        [request] = coordination.inbox("task-3", unread=True)
        self.assertEqual(request["kind"], "request")
        self.assertIn("tile-build failed", request["body"])
        self.assertIn(commits[2][:12], request["subject"])
        # The next run integrates the rest of the batch plus the revert and goes green.
        self.builds.clear()
        self.assertEqual(self.integrate()["result"], "green")
        self.assertEqual(integrator.live_commit(self.published), main)

    def test_flaky_chrome_stage_that_passes_on_rerun_is_green(self):
        head = self.land("game", "game/a.ts", "a")
        self.chrome_flakes = 1
        row = self.integrate()
        self.assertEqual((row["result"], row["builds"]), ("green", 2))
        self.assertEqual(integrator.live_commit(self.published), head)

    def test_chrome_red_twice_is_red(self):
        self.land("game", "game/a.ts", "a")
        self.chrome_flakes = 2
        row = self.integrate()
        self.assertEqual((row["result"], row["failure"]["stage"]), ("red", "chrome"))

    def test_facade_drop_without_reason_is_red(self):
        culprit = self.land("drop", "game/facades.txt", "1\n")
        row = self.integrate()
        self.assertEqual((row["result"], row["culprit"]), ("red", culprit))
        self.assertIn("facades_bound regressed", row["failure"]["detail"])
        self.assertEqual(integrator.live_commit(self.published), self.base)

    def test_facade_drop_with_ledger_reason_is_green(self):
        self.land("drop", "game/facades.txt", "1\n", reason="old facade map was wrong")
        head = self.land("later", "game/a.ts", "a")
        row = self.integrate()
        self.assertEqual((row["result"], row["facades_bound"]), ("green", 1))
        self.assertEqual(integrator.live_commit(self.published), head)

    def test_failed_publish_leaves_live_on_verified_release(self):
        self.land("game", "game/a.ts", "a")
        # The version file write after the link swap fails; publish swaps the link back.
        (self.root / "logs/live-version.txt").unlink()
        (self.root / "logs").rmdir()
        (self.root / "logs").write_text("not a directory")
        row = self.integrate()
        self.assertEqual(row["result"], "failed")
        self.assertEqual(integrator.live_commit(self.published), self.base)
        self.assertEqual(
            (self.published / "build/game").resolve(),
            (self.published / "live-releases" / self.base / "game").resolve(),
        )

    def test_status_reports_running_stage(self):
        self.assertIsNone(integrator.running())
        log = self.root / "build.log"
        log.write_text("== refresh: make refresh ==\n== tile-build: make tile-build ==\n")
        integrator.status_path().parent.mkdir(parents=True, exist_ok=True)
        integrator.status_path().write_text(
            json.dumps({"from": "a", "to": "b", "log": str(log), "started": 1})
        )
        integrator.lock_path().touch()
        # A held lock (a running integrator child) makes the non-blocking probe fail.
        with patch.object(integrator.fcntl, "flock", side_effect=BlockingIOError):
            current = integrator.running()
        self.assertEqual(
            (current["from"], current["to"], current["stage"]), ("a", "b", "tile-build")
        )

    def test_classification_follows_declared_stage_inputs(self):
        self.assertEqual(integrator.domains(["docs/a.md", "scripts/dev/watch.py"]), [])
        self.assertEqual(
            integrator.domains(["scripts/dev/live_build.py", "pipeline/turku3d/a.py"]),
            ["pipeline/turku3d", "scripts/dev"],
        )
        self.assertEqual(integrator.domains(["newdata/a.bin"]), ["newdata/a.bin"])


if __name__ == "__main__":
    unittest.main()
