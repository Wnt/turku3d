"""codex-task finish lands: conflict and red-gate stops, the landed broadcast, the Codex turn
end and land.lock serialisation, on tiny synthetic repositories through the real CLI."""

import json
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_tooling import Fixture  # noqa: E402


class FinishTests(Fixture):
    def ledger(self):
        path = self.base / "wt/.registry/landed.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def meta(self, name):
        return json.loads((self.base / "tasks" / name / "meta.json").read_text())

    def inbox(self, name, *args):
        return json.loads(self.cli("codex-task", "inbox", name, "--json", *args).stdout)

    def as_task(self, name):
        return {**self.env, "TURKU3D_SANDBOX": name}

    def test_conflict_stops_with_the_files_and_changes_nothing(self):
        repo = self.adopt()
        self.commit(repo, "shared.md")
        head = self.git("rev-parse", "HEAD", cwd=repo)
        (self.repo / "shared.md").write_text("main version\n")
        self.git("add", "shared.md")
        self.git("commit", "-qm", "Main moved")
        before = self.git("rev-parse", "main")
        failure = self.finish(ok=False).stderr
        self.assertIn("rebase onto main conflicts in shared.md", failure)
        self.assertEqual(self.git("rev-parse", "main"), before)
        self.assertEqual(self.git("rev-parse", "HEAD", cwd=repo), head)
        self.assertFalse(self.git("status", "--porcelain", cwd=repo))
        self.assertEqual((self.ledger(), self.inbox("demo")), ([], []))
        self.assertNotIn("landed", self.meta("demo"))
        self.assertFalse((self.base / "tasks/demo/gate.log").exists())
        # The worker resolves the conflict on its branch and finishes again.
        rebase = self.cli_git(repo, "rebase", "main")
        self.assertNotEqual(rebase.returncode, 0)
        (repo / "shared.md").write_text("both versions\n")
        self.git("add", "shared.md", cwd=repo)
        self.cli_git(repo, "-c", "core.editor=true", "rebase", "--continue", check=True)
        self.finish()
        self.assertEqual((self.repo / "shared.md").read_text(), "both versions\n")
        self.assertEqual(self.ledger()[0]["before"], before)

    def cli_git(self, repo, *args, check=False):
        return subprocess.run(
            ["git", *args], cwd=repo, env=self.env, text=True, capture_output=True, check=check
        )

    def test_landed_message_reaches_active_tasks_with_interfaces_and_note(self):
        # A finished task and the lander itself get nothing; a running Codex task does.
        self.adopt("gone")
        self.finish("gone")
        lander = self.adopt("lander")
        peer = self.adopt("peer")
        self.cli(
            "codex-task", "run", "busy", "--brief", "brief.md", env={**self.env, "FAKE_SLEEP": "1"}
        )
        self.addCleanup(self.cli, "codex-task", "stop", "busy")
        self.commit(lander, "one.md")
        self.cli(
            "codex-task",
            "msg",
            "all",
            "--kind",
            "interface",
            "--subject",
            "Old API",
            "x",
            env=self.as_task("lander"),
        )
        self.finish("lander", "--keep-sandbox")
        self.commit(lander, "two.md")
        interface = self.cli(
            "codex-task",
            "msg",
            "all",
            "--kind",
            "interface",
            "--subject",
            "Layer API v2",
            "--ref",
            "docs/specs/layers.md",
            "Layers gain a version field.",
            env=self.as_task("lander"),
        ).stdout.strip()
        output = self.finish("lander", "--note", "Rebase before touching layers.").stdout
        self.assertIn("landed message sent to busy, peer", output)
        commit = self.git("rev-parse", "main")
        rows = self.inbox("peer", "--unread")
        self.assertEqual([row["kind"] for row in rows], ["landed", "landed"])
        message = rows[-1]
        self.assertEqual(message["from"], "lander")
        self.assertEqual(message["refs"], [commit, interface])
        self.assertEqual(message["subject"], f"landed lander {commit[:12]}: Test change")
        body = message["body"]
        for text in (
            f"lander landed {commit} on main: Test change",
            "Class inert; changed paths (1): two.md",
            f"Interface {interface}: Layer API v2 (refs: docs/specs/layers.md)",
            "Note: Rebase before touching layers.",
            "git rebase main",
        ):
            self.assertIn(text, body)
        self.assertNotIn("Old API", body)
        self.assertIn("Old API", rows[0]["body"])
        self.assertEqual(len(self.inbox("busy")), 2)
        self.assertEqual((self.inbox("gone"), self.inbox("lander")), ([], []))
        # No ack needed: status counts the landings, a rebase reads them.
        status = self.cli("codex-task", "status", "peer").stdout
        self.assertIn("unread=0,", status)
        self.assertIn("behind main by 2 landings since your last rebase", status)
        self.git("rebase", "-q", "main", cwd=peer)
        self.assertEqual(self.inbox("peer", "--unread"), [])
        self.assertNotIn("behind main", self.cli("codex-task", "status", "peer").stdout)
        self.assertIn("[landed] landed lander", self.cli("codex-task", "inbox", "peer").stdout)

    def test_codex_turn_end_lands_a_done_result(self):
        env = {**self.env, "FAKE_RESULT": "DONE"}
        self.cli("codex-task", "run", "turn", "--brief", "brief.md", env=env)
        deadline = time.monotonic() + 120
        while "landed" not in self.meta("turn") and time.monotonic() < deadline:
            time.sleep(0.1)
        meta = self.meta("turn")
        self.assertEqual(meta["landed"]["commit"], self.git("rev-parse", "main"))
        self.assertEqual(meta["state"], "done")
        # Landed metadata is published only after the success line has been flushed.
        self.assertIn("turn: landed", (self.base / "tasks/turn/land.log").read_text())
        while (self.base / "wt/turn").exists() and time.monotonic() < deadline:
            time.sleep(0.1)
        self.assertFalse((self.base / "wt/turn").exists())

    def test_concurrent_finishes_serialise_on_the_land_lock(self):
        for name in ("left", "right"):
            self.commit(self.adopt(name), f"{name}.md")
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(self.finish, ("left", "right")))
        first, second = self.ledger()
        self.assertEqual(second["before"], first["commit"])
        self.assertEqual(self.git("rev-parse", "main"), second["commit"])
        self.assertTrue((self.repo / "left.md").exists() and (self.repo / "right.md").exists())
        self.assertGreater(second["lock_wait_seconds"], 0)
