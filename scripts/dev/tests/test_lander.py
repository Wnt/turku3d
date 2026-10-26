"""The lander: rebase, fast gate and fast-forward merge in tiny synthetic repositories."""

import json
import shlex
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
        repo = self.start()
        self.commit(repo, "change.md")
        moved = self.advance_main()
        self.cli("codex-task", "land", "demo")
        self.assertFalse(self.live_marker.exists(), "the lander imported live_build")
        (row,) = self.ledger()
        self.assertEqual(row["before"], moved)
        self.assertEqual(row["commit"], self.git("rev-parse", "main"))
        self.assertEqual(self.git("rev-parse", "main^"), moved)
        self.assertTrue((self.repo / "change.md").exists())
        for key in ("merge_wall_seconds", "fast_gate_seconds", "fast_gate_stages", "source"):
            self.assertIn(key, row)
        self.assertLess(row["merge_wall_seconds"], 5, row)
        self.assertFalse(row["merge_slow"])
        self.assertNotIn("live_build", row)
        self.assertIsNone(row["facade_regression_reason"])
        self.assertEqual(row["first_done_time"], self.meta("demo")["first_finished"])
        self.assertIn("SKIP   game-e2e", (self.base / "tasks/demo/gate.log").read_text())

    def test_rebase_conflict_returns_task_and_keeps_main(self):
        repo = self.start()
        self.commit(repo, "shared.md")
        before = self.advance_main("shared.md", "main version\n")
        failure = self.cli("codex-task", "land", "demo", ok=False).stderr
        self.assertIn("returned to worker: rebase conflict", failure)
        self.assertEqual(self.git("rev-parse", "main"), before)
        self.assertEqual(self.ledger(), [])
        meta = self.meta("demo")
        self.assertEqual(meta["state"], "returned")
        self.assertIn("returned: rebase conflict", self.cli("codex-task", "status", "demo").stdout)
        (request,) = json.loads(
            self.cli("codex-task", "inbox", "demo", "--unread", "--json").stdout
        )
        self.assertEqual(request["kind"], "request")
        self.assertIn("shared.md", request["body"])
        self.assertFalse(self.git("status", "--porcelain", cwd=repo))
        # The open request blocks a relanding until the worker acks it and finishes again.
        result = self.base / "result.md"
        result.write_text("## Result: DONE\n")
        self.cli("codex-task", "finish", "demo", "--result", str(result))
        self.assertIn("unacknowledged", self.cli("codex-task", "land", "demo", ok=False).stderr)

    def test_batch_with_red_member_lands_green_prefix(self):
        repos = {name: self.start(name) for name in ("batch-a", "batch-b", "batch-c")}
        self.commit(repos["batch-a"], "a.md")
        (repos["batch-b"] / "bad.png").write_bytes(b"x" * (2 * 1024 * 1024))
        self.git("add", ".", cwd=repos["batch-b"])
        self.git("commit", "-qm", "bad", cwd=repos["batch-b"])
        self.commit(repos["batch-c"], "c.md")
        result = self.cli("codex-task", "land", "--batch", *repos, ok=False)
        self.assertIn("returned to worker: fast gate red in batch", result.stderr)
        self.assertEqual([row["name"] for row in self.ledger()], ["batch-a"])
        self.assertEqual(self.git("rev-parse", "main"), self.ledger()[0]["commit"])
        self.assertTrue((self.repo / "a.md").exists())
        self.assertFalse((self.repo / "bad.png").exists())
        self.assertFalse((self.repo / "c.md").exists())
        self.assertEqual(self.meta("batch-b")["state"], "returned")
        self.assertEqual(self.meta("batch-c")["state"], "done")
        (request,) = json.loads(self.cli("codex-task", "inbox", "batch-b", "--json").stdout)
        self.assertIn("Bisected failing prefix ends at batch-b", request["body"])
        self.assertIn("not attempted after it: batch-c", request["body"])

    def test_ledger_keeps_first_done_time_across_second_finish(self):
        self.cli("wt", "new", "ext")
        self.cli("codex-task", "adopt", "ext", "--brief", "brief.md")
        self.commit(self.base / "wt/ext/repo", "ext.md")
        result = self.base / "result.md"
        result.write_text("## Result: DONE\n\nFacade regression reason: fewer, larger facades\n")
        self.cli("codex-task", "finish", "ext", "--result", str(result))
        first = self.meta("ext")["first_finished"]
        time.sleep(0.01)
        self.cli("codex-task", "finish", "ext", "--result", str(result))
        meta = self.meta("ext")
        self.assertEqual(meta["first_finished"], first)
        self.assertGreater(meta["finished"], first)
        self.cli("codex-task", "land", "ext")
        (row,) = self.ledger()
        self.assertEqual(row["first_done_time"], first)
        self.assertEqual(row["done_time"], meta["finished"])
        self.assertEqual(row["facade_regression_reason"], "fewer, larger facades")

    def test_land_success(self):
        repo = self.start()
        self.commit(repo)
        expected = self.git("rev-parse", "HEAD", cwd=repo)
        message_id = self.cli(
            "codex-task", "msg", "demo", "--kind", "request", "--subject", "Review", "Check API"
        ).stdout.strip()
        inbox = self.base / "tasks/demo/inbox.jsonl"
        rows = [json.loads(line) for line in inbox.read_text().splitlines()]
        rows[0]["time"] = (
            json.loads((self.base / "tasks/demo/meta.json").read_text())["started"] - 1
        )
        inbox.write_text("".join(json.dumps(row) + "\n" for row in rows))
        before = self.git("rev-parse", "main")
        self.assertIn(message_id, self.cli("codex-task", "land", "demo", ok=False).stderr)
        self.assertEqual(self.git("rev-parse", "main"), before)
        self.assertFalse((self.base / "tasks/demo/gate.log").exists())
        self.assertIn("unread=1 UNACKNOWLEDGED", self.cli("codex-task", "ls").stdout)
        self.assertIn("unread=1", self.cli("codex-task", "status", "demo").stdout)
        self.cli("codex-task", "ack", message_id, env={**self.env, "TURKU3D_SANDBOX": "demo"})
        self.assertIn("unread=0", self.cli("codex-task", "status", "demo").stdout)
        land_output = self.cli("codex-task", "land", "demo").stdout
        self.assertIn("sandbox removed; freed", land_output)
        self.assertFalse((self.base / "wt/demo").exists())
        self.assertEqual(self.git("rev-parse", "main"), expected)
        self.assertTrue((self.repo / "change.md").exists())
        self.assertFalse(self.git("status", "--porcelain"))
        log = (self.base / "tasks/demo/gate.log").read_text()
        self.assertIn("PASS   policy", log)
        self.assertIn("PASS   generated", log)
        self.assertIn("materials", (self.repo / "scripts/dev/gate.toml").read_text())
        self.assertIn("TOTAL  GREEN", log)
        meta = json.loads((self.base / "tasks/demo/meta.json").read_text())
        self.assertEqual(meta["landed"]["commit"], expected)
        self.assertGreater(meta["landed"]["fast_gate_seconds"], 0)
        self.assertIn("policy", meta["landed"]["fast_gate_stages"])

    def test_land_force_only_overrides_open_messages(self):
        repo = self.start()
        self.commit(repo)
        for kind in ("note", "interface"):
            self.cli("codex-task", "msg", "demo", "--kind", kind, "--subject", "FYI", "FYI")
        self.cli("codex-task", "msg", "all", "--kind", "request", "--subject", "Board", "FYI")
        self.cli("codex-task", "land", "demo")  # Informational and board messages do not block.
        self.assertFalse(repo.exists())
        repo = self.start("demo2")
        self.commit(repo, "second.md")
        self.cli("codex-task", "msg", "demo2", "--kind", "warning", "--subject", "Check", "Check")
        notice = json.loads(self.cli("codex-task", "inbox", "orchestrator", "--json").stdout)[-1]
        self.assertIn("recipient finished; addressed by orchestrator", notice["body"])
        inbox = self.base / "tasks/demo2/inbox.jsonl"
        rows = [json.loads(line) for line in inbox.read_text().splitlines()]
        rows[0]["time"] = (
            json.loads((self.base / "tasks/demo2/meta.json").read_text())["started"] - 1
        )
        inbox.write_text("".join(json.dumps(row) + "\n" for row in rows))
        self.assertIn("unacknowledged", self.cli("codex-task", "land", "demo2", ok=False).stderr)
        (repo / "dirty.md").write_text("dirty")
        self.assertIn("dirty", self.cli("codex-task", "land", "demo2", "--force", ok=False).stderr)
        (repo / "dirty.md").unlink()
        self.cli("codex-task", "land", "demo2", "--force")
        self.assertFalse(repo.exists())
        self.assertEqual(
            len(json.loads(self.cli("codex-task", "inbox", "demo2", "--unread", "--json").stdout)),
            1,
        )

    def test_land_blocks_request_arriving_during_gate(self):
        repo = self.start()
        gate = repo / "scripts/dev/gate"
        gate.write_text(
            "#!/bin/sh\nexec scripts/dev/codex-task msg demo --kind request "
            "--subject 'Arrived during gate' 'Review before landing'\n"
        )
        self.git("add", ".", cwd=repo)
        self.git("commit", "-qm", "Gate message fixture", cwd=repo)
        before = self.git("rev-parse", "main")
        self.assertIn(
            "unacknowledged request/warning",
            self.cli("codex-task", "land", "demo", ok=False).stderr,
        )
        self.assertEqual(self.git("rev-parse", "main"), before)
        self.assertTrue((self.base / "tasks/demo/gate.log").exists())

    def test_land_refuses_result_dirty_and_red_gate(self):
        repo = self.start()
        self.commit(repo)
        before = self.git("rev-parse", "main")
        result = self.base / "tasks/demo/last.md"
        result.write_text("## Result: BLOCKED\n")
        self.assertIn("DONE", self.cli("codex-task", "land", "demo", ok=False).stderr)
        result.write_text("## Result: DONE\n")
        (repo / "dirty.md").write_text("dirty")
        self.assertIn("dirty", self.cli("codex-task", "land", "demo", ok=False).stderr)
        (repo / "dirty.md").unlink()
        (self.repo / "dirty-main.md").write_text("dirty")
        self.assertIn(
            "main worktree is dirty", self.cli("codex-task", "land", "demo", ok=False).stderr
        )
        (self.repo / "dirty-main.md").unlink()
        (repo / "oversized.png").write_bytes(b"\x89PNG\r\n\x1a\n" + bytes(2 * 1024 * 1024))
        self.git("add", ".", cwd=repo)
        self.git("commit", "-qm", "Red gate", cwd=repo)
        failure = self.cli("codex-task", "land", "demo", ok=False).stderr
        self.assertIn("fast gate failed", failure)
        self.assertIn("host load", failure)
        self.assertIn("FAIL", failure)
        self.assertEqual(self.git("rev-parse", "main"), before)
        self.assertIn("oversized.png", (self.base / "tasks/demo/gate.log").read_text())
        self.assertIn("TOTAL  RED", (self.base / "tasks/demo/gate.log").read_text())

    def test_land_refuses_missing_gate(self):
        repo = self.start()
        before = self.git("rev-parse", "main")
        self.git("rm", "scripts/dev/gate", cwd=repo)
        self.git("commit", "-qm", "Missing gate", cwd=repo)
        self.assertIn(
            "missing scripts/dev/gate", self.cli("codex-task", "land", "demo", ok=False).stderr
        )
        self.assertEqual(self.git("rev-parse", "main"), before)

    def test_land_refuses_main_change_during_gate(self):
        repo = self.start()
        gate = repo / "scripts/dev/gate"
        gate.write_text(
            "#!/bin/sh\ngit -C "
            + shlex.quote(str(self.repo))
            + " -c user.name=test -c user.email=test@example.invalid commit --allow-empty -qm concurrent\n"
        )
        gate.chmod(0o755)
        self.git("add", ".", cwd=repo)
        self.git("commit", "-qm", "Gate fixture", cwd=repo)
        self.assertIn("main changed", self.cli("codex-task", "land", "demo", ok=False).stderr)
        self.assertNotEqual(self.git("rev-parse", "main"), self.git("rev-parse", "HEAD", cwd=repo))

    def test_batch_lands_once_and_records_every_member(self):
        a = self.start("batch-a")
        b = self.start("batch-b")
        for repo, filename in ((a, "a.md"), (b, "b.md")):
            (repo / filename).write_text(filename)
            self.git("add", ".", cwd=repo)
            self.git("commit", "-qm", filename, cwd=repo)
        self.cli("codex-task", "land", "--batch", "batch-a", "batch-b")
        self.assertTrue((self.repo / "a.md").exists())
        self.assertTrue((self.repo / "b.md").exists())
        records = [
            json.loads(line)
            for line in (self.base / "wt/.registry/landed.jsonl").read_text().splitlines()
        ]
        self.assertEqual([row["name"] for row in records], ["batch-a", "batch-b"])
        self.assertFalse((self.base / "wt/batch-a").exists())
        self.assertFalse((self.base / "wt/batch-b").exists())

    def test_land_retries_one_main_advance(self):
        repo = self.start()
        gate = repo / "scripts/dev/gate"
        marker = self.base / "advanced"
        gate.write_text(
            "#!/bin/sh\nif [ ! -f "
            + shlex.quote(str(marker))
            + "]; then\ntouch "
            + shlex.quote(str(marker))
            + "\ngit -C "
            + shlex.quote(str(self.repo))
            + " commit --allow-empty -qm concurrent\nfi\n"
        )
        gate.chmod(0o755)
        self.git("add", ".", cwd=repo)
        self.git("commit", "-qm", "Gate fixture", cwd=repo)
        self.cli("codex-task", "land", "demo")
        self.assertFalse(repo.exists())


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
