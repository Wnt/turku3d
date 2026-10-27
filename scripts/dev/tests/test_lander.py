"""The lander: rebase, fast gate and fast-forward merge in tiny synthetic repositories."""

import json
import shlex
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import landing  # noqa: E402
from test_tooling import Fixture  # noqa: E402


class LanderTests(Fixture):
    def ledger(self):
        path = self.base / "wt/.registry/landed.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def meta(self, name):
        return json.loads((self.base / "tasks" / name / "meta.json").read_text())

    def advance_main(self, name="other.md", text="main moved\n"):
        (self.repo / name).write_text(text)
        self.git("add", name)
        self.git("commit", "-qm", "Main moved")
        return self.git("rev-parse", "main")

    def test_lands_after_main_moved_without_live_build_quickly(self):
        repo = self.adopt()
        self.commit(repo, "change.md")
        moved = self.advance_main()
        self.finish()
        self.assertFalse(self.live_marker.exists(), "the lander imported live_build")
        (row,) = self.ledger()
        self.assertEqual(row["before"], moved)
        self.assertEqual(row["commit"], self.git("rev-parse", "main"))
        self.assertEqual(self.git("rev-parse", "main^"), moved)
        self.assertTrue((self.repo / "change.md").exists())
        for key in ("merge_wall_seconds", "fast_gate_seconds", "fast_gate_stages", "source"):
            self.assertIn(key, row)
        self.assertGreaterEqual(row["lock_wait_seconds"], 0)
        self.assertLess(row["merge_wall_seconds"], 5, row)
        self.assertFalse(row["merge_slow"])
        self.assertNotIn("live_build", row)
        self.assertEqual((row["class"], row["paths"], row["tiles"]), ("inert", ["change.md"], []))
        self.assertEqual((row["changed_paths"], row["data_delta"]), (1, []))
        self.assertIsNone(row["facade_regression_reason"])
        self.assertIn("browser_minutes", row)
        self.assertEqual(row["first_done_time"], self.meta("demo")["first_finished"])
        self.assertIn("SKIP   game-e2e", (self.base / "tasks/demo/gate.log").read_text())

    def public_origin(self):
        """A bare origin holding one squashed import of main's tree as its main branch."""
        origin = self.base / "origin.git"
        self.git("init", "-q", "--bare", str(origin))
        self.git("remote", "add", "origin", str(origin))
        root = self.git("commit-tree", "main^{tree}", "-m", "Import")
        self.git("update-ref", landing.PUBLIC_REF, root)
        self.git("push", "-q", "origin", f"{landing.PUBLIC_REF}:refs/heads/main")
        return origin, root

    def test_landing_pushes_main_tree_on_the_public_line_only(self):
        origin, root = self.public_origin()
        private = self.git("rev-parse", "main")
        repo = self.adopt()
        self.commit(repo, "change.md")
        self.finish()
        (row,) = self.ledger()
        public = self.git("rev-parse", landing.PUBLIC_REF)
        self.assertEqual(row["push"]["commit"], public)
        self.assertEqual(self.git("rev-parse", "main", cwd=origin), public)
        self.assertEqual(self.git("rev-parse", f"{public}^"), root)
        self.assertEqual(
            self.git("rev-parse", f"{public}^{{tree}}"), self.git("rev-parse", "main^{tree}")
        )
        self.assertEqual(self.git("log", "-1", "--format=%s", public), "Test change")
        missing = subprocess.run(
            ["git", "cat-file", "-e", private], cwd=origin, capture_output=True
        )
        self.assertNotEqual(missing.returncode, 0, "local main history reached the public origin")

    def test_push_failure_is_recorded_and_the_landing_stands(self):
        self.public_origin()
        self.git("remote", "set-url", "origin", str(self.base / "missing.git"))
        repo = self.adopt()
        self.commit(repo, "change.md")
        self.finish()
        (row,) = self.ledger()
        self.assertEqual(row["commit"], self.git("rev-parse", "main"))
        self.assertIn("error", row["push"])
        self.assertFalse(row["merge_slow"])

    def test_slices_land_with_a_kept_sandbox_and_keep_the_first_done_time(self):
        repo = self.adopt("ext")
        self.commit(repo, "one.md")
        self.finish("ext", "--keep-sandbox")
        first = self.meta("ext")
        self.assertEqual(first["state"], "external")
        self.assertTrue(repo.is_dir())
        # The worker is active again: there is no new result to land.
        self.assertIn(
            "completed successfully", self.cli("codex-task", "land", "ext", ok=False).stderr
        )
        time.sleep(0.01)
        self.commit(repo, "two.md")
        self.finish("ext", text="## Result: DONE\n\nFacade regression reason: fewer, larger\n")
        rows = self.ledger()
        self.assertEqual([row["paths"] for row in rows], [["one.md"], ["two.md"]])
        self.assertEqual(rows[1]["before"], rows[0]["commit"])
        self.assertEqual(rows[1]["first_done_time"], first["first_finished"])
        self.assertGreater(rows[1]["done_time"], rows[0]["done_time"])
        self.assertEqual(rows[1]["facade_regression_reason"], "fewer, larger")
        self.assertIsNone(rows[0]["facade_regression_reason"])
        self.assertFalse(repo.exists())

    def test_open_requests_are_reported_and_do_not_hold(self):
        repo = self.adopt()
        self.commit(repo)
        expected = self.git("rev-parse", "HEAD", cwd=repo)
        message_id = self.cli(
            "codex-task", "msg", "demo", "--kind", "request", "--subject", "Review", "Check API"
        ).stdout.strip()
        output = self.finish().stdout
        self.assertIn(f"open request {message_id} from orchestrator: Review (not holding", output)
        self.assertIn("integrator not started", output)
        self.assertIn("sandbox removed; freed", output)
        self.assertFalse((self.base / "wt/demo").exists())
        self.assertEqual(self.git("rev-parse", "main"), expected)
        self.assertFalse(self.git("status", "--porcelain"))
        self.assertIn("unread=1 UNACKNOWLEDGED", self.cli("codex-task", "ls").stdout)
        # A repeated land of the same finish is a no-op, not a second ledger row.
        self.assertIn("already landed", self.cli("codex-task", "land", "demo").stdout)
        self.assertEqual(len(self.ledger()), 1)
        log = (self.base / "tasks/demo/gate.log").read_text()
        for line in ("PASS   policy", "PASS   generated", "TOTAL  GREEN"):
            self.assertIn(line, log)
        meta = self.meta("demo")
        self.assertEqual(meta["landed"]["commit"], expected)
        self.assertGreater(meta["landed"]["fast_gate_seconds"], 0)
        self.assertIn("policy", meta["landed"]["fast_gate_stages"])
        # A warning to the finished task is addressed by the orchestrator.
        self.cli("codex-task", "msg", "demo", "--kind", "warning", "--subject", "Late", "Late")
        notice = json.loads(self.cli("codex-task", "inbox", "orchestrator", "--json").stdout)[-1]
        self.assertIn("recipient finished; addressed by orchestrator", notice["body"])

    def test_refusals_leave_main_unchanged_and_land_retries_the_recorded_result(self):
        repo = self.adopt()
        self.commit(repo)
        before = self.git("rev-parse", "main")
        self.assertIn("DONE", self.finish(text="## Result: BLOCKED\n", ok=False).stderr)
        (repo / "dirty.md").write_text("dirty")
        self.assertIn("dirty", self.finish(ok=False).stderr)
        (repo / "dirty.md").unlink()
        (self.repo / "dirty-main.md").write_text("dirty")
        self.assertIn("main worktree is dirty", self.finish(ok=False).stderr)
        (self.repo / "dirty-main.md").unlink()
        (repo / "oversized.png").write_bytes(b"\x89PNG\r\n\x1a\n" + bytes(2 * 1024 * 1024))
        self.git("add", ".", cwd=repo)
        self.git("commit", "-qm", "Red gate", cwd=repo)
        failure = self.finish(ok=False).stderr
        for text in ("fast gate failed", "host load", "FAIL"):
            self.assertIn(text, failure)
        self.assertEqual(self.git("rev-parse", "main"), before)
        self.assertEqual(self.ledger(), [])
        self.assertIn("oversized.png", (self.base / "tasks/demo/gate.log").read_text())
        self.assertIn("TOTAL  RED", (self.base / "tasks/demo/gate.log").read_text())
        # The orchestrator's land retries the recorded DONE result once the branch is fixed.
        self.git("reset", "-q", "--hard", "HEAD~1", cwd=repo)
        self.cli("codex-task", "land", "demo")
        self.assertEqual(self.ledger()[0]["commit"], self.git("rev-parse", "main"))

    def test_land_refuses_missing_gate(self):
        repo = self.adopt()
        before = self.git("rev-parse", "main")
        self.git("rm", "-q", "scripts/dev/gate", cwd=repo)
        self.git("commit", "-qm", "Missing gate", cwd=repo)
        self.assertIn("missing scripts/dev/gate", self.finish(ok=False).stderr)
        self.assertEqual(self.git("rev-parse", "main"), before)

    def gate_fixture(self, repo, script):
        gate = repo / "scripts/dev/gate"
        gate.write_text(script)
        gate.chmod(0o755)
        self.git("add", ".", cwd=repo)
        self.git("commit", "-qm", "Gate fixture", cwd=repo)

    def test_land_refuses_main_change_during_gate(self):
        repo = self.adopt()
        self.gate_fixture(
            repo,
            "#!/bin/sh\ngit -C "
            + shlex.quote(str(self.repo))
            + " -c user.name=test -c user.email=test@example.invalid commit --allow-empty -qm concurrent\n",
        )
        self.assertIn("main changed", self.finish(ok=False).stderr)
        self.assertNotEqual(self.git("rev-parse", "main"), self.git("rev-parse", "HEAD", cwd=repo))

    def test_land_retries_one_main_advance(self):
        repo = self.adopt()
        marker = self.base / "advanced"
        self.gate_fixture(
            repo,
            "#!/bin/sh\nif [ ! -f "
            + shlex.quote(str(marker))
            + " ]; then\ntouch "
            + shlex.quote(str(marker))
            + "\ngit -C "
            + shlex.quote(str(self.repo))
            + " commit --allow-empty -qm concurrent\nfi\n",
        )
        self.finish()
        self.assertFalse(repo.exists())

    def test_data_root_change_is_attributed_to_the_first_landing_after_it(self):
        repos = {name: self.adopt(name) for name in ("first", "second")}
        for name, repo in repos.items():
            self.commit(repo, f"{name}.md")
        # Approvals changed in the data root since the last ledger row (here: no live release).
        approvals = self.base / "data/facade-approvals/assembly/MANIFEST.json"
        approvals.parent.mkdir(parents=True)
        approvals.write_text('{"bound": []}')
        for name in repos:
            self.finish(name)
        rows = self.ledger()
        self.assertEqual(
            [(row["name"], row["class"], row["paths"], row["data_delta"]) for row in rows],
            [
                ("first", "data", ["first.md"], ["facade-approvals"]),
                ("second", "inert", ["second.md"], []),
            ],
        )
        self.assertEqual(rows[0]["tiles"], list(landing.landing_class.C.CORE_TILES))
        self.assertEqual(rows[0]["data_parts"], rows[1]["data_parts"])


class BudgetTests(unittest.TestCase):
    def test_over_budget_records_merge_slow_and_raises(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)
            (path / "meta.json").write_text("{}")
            landing.over_budget([path], time.monotonic(), 180, "fast gate")
            with self.assertRaisesRegex(ValueError, "merge_slow.*over the 180s budget"):
                landing.over_budget([path], time.monotonic() - 181, 180, "fast gate")
            slow = json.loads((path / "meta.json").read_text())["merge_slow"]
            self.assertEqual(slow["where"], "fast gate")
            self.assertGreater(slow["seconds"], 180)


if __name__ == "__main__":
    unittest.main()
