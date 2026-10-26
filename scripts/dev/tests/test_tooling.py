"""Integration tests use tiny temporary git repos, fake Codex and no shared data."""

import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[3]


class ToolingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="turku tooling-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.repo = self.base / "main"
        self.repo.mkdir()
        shutil.copytree(
            SOURCE / "scripts/dev",
            self.repo / "scripts/dev",
            ignore=shutil.ignore_patterns("__pycache__"),
        )
        # The CLI fixture has no real AOI. Exercise orchestration with a synthetic
        # live builder; live_build itself has separate artifact tests.
        (self.repo / "scripts/dev/live_build.py").write_text(
            "def build(repo, work, published, env, log_path, reason=None):\n"
            "    log_path.write_text('synthetic live build passed\\n')\n"
            "    return {'facades_bound': 1, 'seconds': {'refresh': 0.001}}\n"
            "def prepare(work, published, commit, report):\n"
            "    pass\n"
            "def publish(published, commit):\n"
            "    published.mkdir(parents=True, exist_ok=True)\n"
            "    (published / 'published-commit').write_text(commit)\n"
        )
        shutil.copytree(SOURCE / "textures/turkutex/codex", self.repo / "textures/turkutex/codex")
        # Use the production routing table and always-on checks for landing, without models
        # or runtime assets. These are the real material generator inputs and policy exception.
        shutil.copytree(
            SOURCE / "pipeline/turku3d",
            self.repo / "pipeline/turku3d",
            ignore=shutil.ignore_patterns("__pycache__"),
        )
        for name in (
            "textures/codex-routing.toml",
            "docs/flags.toml",
            "docs/specs/materials.md",
            "game/src/shared/col/materials.ts",
            "game/public/basis/basis_transcoder.js",
        ):
            dest = self.repo / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(SOURCE / name, dest)
        (self.repo / "docs/briefs").mkdir(parents=True)
        (self.repo / "docs/briefs/COMMON.md").write_text("Common test policy.\n")
        (self.repo / "brief.md").write_text("Tiny fake test brief.\n")
        (self.repo / ".gitignore").write_text("__pycache__/\n.cache/\n")
        self.bin = self.base / "bin"
        self.bin.mkdir()
        shutil.copy(SOURCE / "scripts/dev/tests/fake_codex.py", self.bin / "codex")
        (self.bin / "codex").chmod(0o755)
        self.env = {
            **os.environ,
            "TURKU3D_WT_ROOT": str(self.base / "wt"),
            "TURKU3D_TASKS_ROOT": str(self.base / "tasks"),
            "TURKU3D_DATA": str(self.base / "data"),
            "TURKU3D_CODEX_LEDGER": str(self.base / "ledger.jsonl"),
            "PATH": str(self.bin) + os.pathsep + os.environ["PATH"],
            "GATE_PYTHON": sys.executable,
            "TURKU3D_SANDBOX": "orchestrator",
            "TURKU3D_COORD_ROOT": str(self.base / "coord"),
        }
        self.env.pop("TURKUTEX_USAGE_CMD", None)
        # Synthetic registries must still avoid the caller's real, concurrently running stacks.
        registry = (
            Path(os.environ.get("TURKU3D_WT_ROOT", Path.home() / "turku3d/wt"))
            / ".registry/sandboxes.json"
        )
        reservations = json.loads(registry.read_text()) if registry.exists() else {}
        reservations["reserved-current"] = {
            "port": int(os.environ.get("TURKU3D_PORT_BASE", "5170"))
        }
        # Deduplicate manual reservations before importing them into the test registry.
        reservations = {
            f"reserved-{port}": {"port": port}
            for port in {r["port"] for r in reservations.values()}
        }
        (self.base / "wt/.registry").mkdir(parents=True)
        (self.base / "wt/.registry/sandboxes.json").write_text(json.dumps(reservations))
        self.git("init", "-b", "main")
        self.git("config", "user.email", "test@example.invalid")
        self.git("config", "user.name", "Tooling test")
        self.git("add", ".")
        self.git("commit", "-qm", "Initial fixture")

    def git(self, *args, cwd=None):
        return subprocess.run(
            ["git", *args],
            cwd=cwd or self.repo,
            env=self.env,
            text=True,
            capture_output=True,
            check=True,
        ).stdout.strip()

    def cli(self, tool, *args, ok=True, env=None, cwd=None):
        result = subprocess.run(
            [str(self.repo / "scripts/dev" / tool), *args],
            cwd=cwd or self.repo,
            env=env or self.env,
            capture_output=True,
            text=True,
            # Functional CLI deadline, not a throughput assertion: overloaded hosts
            # can deschedule Git/lsof children for seconds. The here budget stays explicit.
            timeout=180,
        )
        if ok:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout)
        return result

    def wait_done(self, name):
        meta = self.base / "tasks" / name / "meta.json"
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            row = json.loads(meta.read_text())
            if row["state"] not in ("starting", "running"):
                self.assertEqual(row["state"], "done", row)
                return row
            time.sleep(0.02)
        self.fail("fake task did not complete")

    def start(self, name="demo"):
        self.cli("codex-task", "run", name, "--brief", "brief.md")
        self.wait_done(name)
        return self.base / "wt" / name / "repo"

    def commit(self, repo, name="change.md"):
        (repo / name).write_text("A trivial change.\n")
        self.git("add", name, cwd=repo)
        self.git("commit", "-qm", "Test change", cwd=repo)

    def test_concurrent_port_claims_and_env(self):
        with ThreadPoolExecutor(max_workers=6) as pool:
            list(pool.map(lambda i: self.cli("wt", "new", f"w{i}"), range(6)))
        records = json.loads((self.base / "wt/.registry/sandboxes.json").read_text())
        ports = [v["port"] for k, v in records.items() if k.startswith("w")]
        self.assertEqual(len(set(ports)), 6)
        for p in ports:
            self.assertTrue(all(p == q or abs(p - q) >= 10 for q in ports))
        output = self.cli("wt", "env", "w0").stdout
        loaded = subprocess.run(
            ["bash", "-c", output + '\nprintf "%s" "$TURKU3D_WORK"'],
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertEqual(loaded.stdout, str(self.base / "wt/w0/work"))
        self.assertTrue((self.base / "wt/w0/work/qa").is_dir())
        self.assertEqual(self.cli("wt", "path", "w0").stdout.strip(), str(self.base / "wt/w0/repo"))

    def test_adopts_manual_reservations(self):
        self.cli("wt", "new", "manual")
        old = json.loads((self.base / "wt/.registry/sandboxes.json").read_text())["manual"]["port"]
        (self.base / "wt/.registry/sandboxes.json").unlink()
        self.cli("wt", "new", "next")
        rows = json.loads((self.base / "wt/.registry/sandboxes.json").read_text())
        self.assertEqual(rows["manual"]["port"], old)
        self.assertNotEqual(rows["next"]["port"], old)

    def test_run_resume_and_logs(self):
        self.start()
        prompt = (self.base / "tasks/demo/prompt.md").read_text()
        self.assertIn("## Coordination", prompt)
        self.assertIn("scripts/dev/codex-task inbox --unread", prompt)
        self.assertIn("scripts/dev/codex-task board", prompt)
        self.assertIn("Tiny fake test brief.", prompt)
        self.assertIn("## Result: DONE", self.cli("codex-task", "result", "demo").stdout)
        self.assertIn("done", self.cli("codex-task", "status", "demo").stdout)
        self.cli("codex-task", "resume", "demo", "Follow-up with $literal and `text`.")
        self.wait_done("demo")
        resume = next((self.base / "tasks/demo").glob("resume-*.md")).read_text()
        self.assertIn("## Coordination", resume)
        self.assertIn("scripts/dev/codex-task ack <id>", resume)
        self.assertTrue(resume.endswith("Follow-up with $literal and `text`."))
        self.assertIn("Follow-up", self.cli("codex-task", "result", "demo").stdout)
        self.assertIn("turn.completed", self.cli("codex-task", "log", "demo", "1").stdout)
        self.cli("codex-task", "run", "demo", "--brief", "brief.md", ok=False)
        ledger = [
            json.loads(line) for line in (self.base / "ledger.jsonl").read_text().splitlines()
        ]
        self.assertEqual(len(ledger), 2)
        self.assertEqual(len({row["call_id"] for row in ledger}), 2)
        self.assertEqual(sum(row["tokens"]["input_tokens"] for row in ledger), 20)
        self.assertTrue(all(row["task"] == "demo" and row["effort"] == "medium" for row in ledger))

    def test_task_route_and_explicit_overrides(self):
        self.start()
        self.cli(
            "codex-task",
            "resume",
            "demo",
            "Verify only",
            "--route",
            "task_rebase",
            env={**self.env, "FAKE_EXPECT_MODEL": "gpt-6-luna"},
        )
        self.wait_done("demo")
        meta = json.loads((self.base / "tasks/demo/meta.json").read_text())
        self.assertEqual(
            (meta["model"], meta["effort"], meta["route"]), ("gpt-6-luna", "low", "task_rebase")
        )
        self.cli(
            "codex-task",
            "resume",
            "demo",
            "Explicit override",
            "--route",
            "light",
            "--model",
            "gpt-6-sol",
            "--effort",
            "high",
            env={**self.env, "TURKU3D_CODEX_MODEL": "gpt-6-astra", "TURKU3D_CODEX_EFFORT": "low"},
        )
        self.wait_done("demo")
        meta = json.loads((self.base / "tasks/demo/meta.json").read_text())
        self.assertEqual((meta["model"], meta["effort"]), ("gpt-6-sol", "high"))

    def test_quota_refusal_and_override(self):
        env = {**self.env, "FAKE_QUOTA": "100"}
        self.assertIn(
            "quota 100%",
            self.cli("codex-task", "run", "full", "--brief", "brief.md", env=env, ok=False).stderr,
        )
        self.assertFalse((self.base / "wt/full").exists())
        self.assertIn(
            "unavailable",
            self.cli(
                "codex-task",
                "run",
                "unknown",
                "--brief",
                "brief.md",
                "--force",
                env={**self.env, "FAKE_QUOTA": "unknown"},
                ok=False,
            ).stderr,
        )
        self.cli("codex-task", "run", "full", "--brief", "brief.md", "--force", env=env)
        self.wait_done("full")

    def test_low_disk_refuses_before_creating_sandbox(self):
        code = (
            "import sys; from unittest.mock import patch; "
            "sys.path.insert(0, 'scripts/dev'); import task_runner; "
            "from collections import namedtuple; "
            "Usage = namedtuple('Usage', 'total used free'); "
            "\nwith patch.object(task_runner, 'quota', return_value=0), "
            "patch.object(task_runner.shutil, 'disk_usage', return_value=Usage(100, 75, 25)): "
            "\n try: task_runner.launch('low', brief='brief.md')"
            "\n except ValueError as error: print(error)"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=self.repo,
            env=self.env,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("below 30 GiB", result.stdout)
        self.assertFalse((self.base / "wt/low").exists())

    def test_du_and_prune_landed_only_remove_safe_sandboxes(self):
        self.cli("wt", "new", "landed")
        self.cli("wt", "new", "unmerged")
        self.cli("wt", "new", "dirty")
        self.commit(self.base / "wt/unmerged/repo")
        (self.base / "wt/dirty/repo/dirty.txt").write_text("dirty")
        (self.base / "wt/landed/work/cache/filler").write_bytes(b"x" * 4096)
        rows = self.cli("wt", "du").stdout
        self.assertIn("landed", rows)
        self.assertIn("unmerged", rows)
        self.assertIn("dirty", rows)
        self.assertLess(rows.index("landed"), rows.index("unmerged"))
        result = self.cli("wt", "prune-landed").stdout
        self.assertIn("landed: removed; freed", result)
        self.assertFalse((self.base / "wt/landed").exists())
        self.assertTrue((self.base / "wt/unmerged").exists())
        self.assertTrue((self.base / "wt/dirty").exists())

    def test_stop_and_duplicate_run(self):
        self.cli(
            "codex-task", "run", "slow", "--brief", "brief.md", env={**self.env, "FAKE_SLEEP": "1"}
        )
        self.addCleanup(lambda: self.cli("codex-task", "stop", "slow"))
        self.assertIn(
            "already running",
            self.cli("codex-task", "run", "slow", "--brief", "brief.md", ok=False).stderr,
        )
        deadline = time.monotonic() + 60
        child = None
        while time.monotonic() < deadline:
            events = self.base / "tasks/slow/events.jsonl"
            if events.exists():
                rows = [json.loads(line) for line in events.read_text().splitlines() if line]
                child = next((row["pid"] for row in rows if row["type"] == "fake.child"), None)
                if child:
                    break
            time.sleep(0.02)
        self.assertIsNotNone(child, "fake Codex never launched its descendant")
        self.cli("codex-task", "stop", "slow")
        self.assertIn("stopped", self.cli("codex-task", "status", "slow").stdout)
        state = subprocess.run(
            ["ps", "-p", str(child), "-o", "stat="], capture_output=True, text=True
        ).stdout.strip()
        self.assertTrue(not state or state.startswith("Z"), f"child still running: {state}")

    def test_land_success(self):
        repo = self.start()
        self.commit(repo)
        expected = self.git("rev-parse", "HEAD", cwd=repo)
        message_id = self.cli(
            "codex-task", "msg", "demo", "--kind", "request", "--subject", "Review", "Check API"
        ).stdout.strip()
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
        self.assertGreater(meta["landed"]["gate_seconds"], 0)

    def test_coordination_exchange_between_two_sandboxes(self):
        for name in ("alpha", "beta"):
            self.cli("wt", "new", name)

        def sandbox_env(name):
            lines = (self.base / "wt" / name / "sandbox.env").read_text().splitlines()
            return {**self.env, **dict(shlex.split(line)[1].split("=", 1) for line in lines)}

        alpha, beta = sandbox_env("alpha"), sandbox_env("beta")
        message_id = self.cli(
            "codex-task",
            "msg",
            "beta",
            "--kind",
            "request",
            "--subject",
            "Layer API",
            "--ref",
            "abc123",
            "--ref",
            "docs/specs/tiles.md",
            "Please review.\nThanks!",
            env=alpha,
            cwd=self.base / "wt/alpha/repo",
        ).stdout.strip()
        path = self.base / "tasks/beta/inbox.jsonl"
        original = path.read_bytes()
        row = json.loads(original)
        self.assertEqual(row["from"], "alpha")
        self.assertEqual(row["to"], "beta")
        self.assertEqual(row["refs"], ["abc123", "docs/specs/tiles.md"])
        self.assertIsNone(row["ack"])
        self.assertEqual(
            json.loads(self.cli("codex-task", "inbox", "--unread", "--json", env=beta).stdout),
            [row],
        )
        self.assertIn("beta: inbox only, unread=1", self.cli("codex-task", "ls").stdout)
        self.cli("codex-task", "ack", message_id, env=alpha, ok=False)
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(
                pool.map(
                    lambda _: self.cli(
                        "codex-task", "ack", message_id, "--note", "Reviewed.", env=beta
                    ),
                    range(4),
                )
            )
        self.assertTrue(path.read_bytes().startswith(original))
        records = [json.loads(line) for line in path.read_text().splitlines()]
        self.assertEqual(len(records), 2)
        self.assertEqual(records[1]["kind"], "ack")
        self.assertEqual(records[1]["ack"], message_id)
        self.assertEqual(records[1]["from"], "beta")
        self.assertEqual(records[1]["body"], "Reviewed.")
        self.assertEqual(
            json.loads(self.cli("codex-task", "inbox", "--unread", "--json", env=beta).stdout), []
        )
        resolved = json.loads(self.cli("codex-task", "inbox", "beta", "--json").stdout)
        self.assertEqual(resolved[0]["ack"], records[1])
        reply = self.cli(
            "codex-task",
            "msg",
            "alpha",
            "--kind",
            "note",
            "--subject",
            "Reviewed",
            "Ready to use.",
            env=beta,
        ).stdout.strip()
        self.cli("codex-task", "ack", reply, env=alpha)
        board_id = self.cli(
            "codex-task",
            "msg",
            "all",
            "--kind",
            "interface",
            "--subject",
            "API ready",
            "--ref",
            "abc123",
            "Layer API is ready.",
            env=beta,
        ).stdout.strip()
        board = json.loads(self.cli("codex-task", "board", "--json", env=alpha).stdout)
        self.assertEqual(board[0]["id"], board_id)
        self.assertEqual(board[0]["to"], "all")
        self.cli("codex-task", "ack", board_id, env=alpha)
        self.assertIsNotNone(
            json.loads(self.cli("codex-task", "board", "--json", env=alpha).stdout)[0]["ack"]
        )
        self.assertIsNone(
            json.loads(self.cli("codex-task", "board", "--json", env=beta).stdout)[0]["ack"]
        )
        self.assertEqual(
            json.loads(
                self.cli("codex-task", "board", "--since", str(board[0]["time"]), "--json").stdout
            ),
            [],
        )
        self.cli("codex-task", "ack", board_id, env=beta)
        self.assertEqual(len((self.base / "coord/board.jsonl").read_text().splitlines()), 3)

    def test_coordination_concurrent_writers_and_readers(self):
        body = "ä Unicode and literal $text `data`\n" * 512

        def write(index):
            recipient = "all" if index % 2 else "orchestrator"
            result = self.cli(
                "codex-task", "msg", recipient, "--kind", "note", "--subject", str(index), body
            )
            command = ["board"] if recipient == "all" else ["inbox", recipient]
            rows = json.loads(self.cli("codex-task", *command, "--json").stdout)
            self.assertTrue(all(row["body"] == body for row in rows))
            return result.stdout.strip()

        with ThreadPoolExecutor(max_workers=6) as pool:
            ids = set(pool.map(write, range(24)))
        rows = []
        for path in (self.base / "tasks/orchestrator/inbox.jsonl", self.base / "coord/board.jsonl"):
            log = [json.loads(line) for line in path.read_text().splitlines()]
            self.assertEqual(len(log), 12)
            rows.extend(log)
        self.assertEqual({row["id"] for row in rows}, ids)
        self.assertEqual(len(ids), 24)
        self.assertEqual({row["subject"] for row in rows}, {str(i) for i in range(24)})
        self.assertTrue(all(row["body"] == body for row in rows))

    def test_coordination_defaults_validation_and_corruption(self):
        env = dict(self.env)
        env.pop("TURKU3D_SANDBOX")
        env.pop("TURKU3D_COORD_ROOT")
        message_id = self.cli(
            "codex-task",
            "msg",
            "orchestrator",
            "--kind",
            "note",
            "--subject",
            "Default",
            "Defaults work.",
            env=env,
        ).stdout.strip()
        rows = json.loads(self.cli("codex-task", "inbox", "--json", env=env).stdout)
        self.assertEqual(rows[0]["from"], "orchestrator")
        self.cli("codex-task", "ack", message_id, env=env)
        self.cli("codex-task", "msg", "all", "--kind", "note", "--subject", "Board", "OK", env=env)
        self.assertTrue((self.base / "coord/board.jsonl").exists())
        self.cli("codex-task", "inbox", "../../escape", ok=False)
        self.cli("codex-task", "ack", "unknown", ok=False)
        self.cli("codex-task", "board", "--since", "nan", ok=False)
        self.cli("codex-task", "msg", "typo", "--kind", "note", "--subject", "No", "No", ok=False)
        self.cli("codex-task", "msg", "all", "--kind", "ack", "--subject", "No", "No", ok=False)
        log = self.base / "tasks/orchestrator/inbox.jsonl"
        with log.open("a") as stream:
            stream.write('{"kind":')
        self.assertIn("invalid coordination log", self.cli("codex-task", "inbox", ok=False).stderr)
        before = log.read_bytes()
        self.cli(
            "codex-task", "msg", "orchestrator", "--kind", "note", "--subject", "No", "No", ok=False
        )
        self.assertEqual(log.read_bytes(), before)

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

    def test_land_refuses_request_arriving_during_gate(self):
        repo = self.start()
        gate = repo / "scripts/dev/gate"
        gate.write_text(
            "#!/bin/sh\nexec scripts/dev/codex-task msg demo --kind request "
            "--subject 'Arrived during gate' 'Review before landing'\n"
        )
        self.git("add", ".", cwd=repo)
        self.git("commit", "-qm", "Gate message fixture", cwd=repo)
        before = self.git("rev-parse", "main")
        self.assertIn("unacknowledged", self.cli("codex-task", "land", "demo", ok=False).stderr)
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
        self.assertIn("gate failed", self.cli("codex-task", "land", "demo", ok=False).stderr)
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

    def test_batch_red_bisects_without_updating_main(self):
        a = self.start("batch-a")
        b = self.start("batch-b")
        (a / "a.md").write_text("good")
        self.git("add", ".", cwd=a)
        self.git("commit", "-qm", "good", cwd=a)
        (b / "bad.png").write_bytes(b"x" * (2 * 1024 * 1024))
        self.git("add", ".", cwd=b)
        self.git("commit", "-qm", "bad", cwd=b)
        before = self.git("rev-parse", "main")
        result = self.cli("codex-task", "land", "--batch", "batch-a", "batch-b", ok=False)
        self.assertIn("bisected failing prefix ends at batch-b", result.stderr)
        self.assertEqual(self.git("rev-parse", "main"), before)

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

    def test_rm_refuses_unmerged_and_live_then_releases(self):
        self.cli("wt", "new", "a")
        repo = self.base / "wt/a/repo"
        self.commit(repo)
        self.assertIn("unmerged", self.cli("wt", "rm", "a", ok=False).stderr)
        proc = subprocess.Popen(
            [sys.executable, "-c", "import threading;threading.Event().wait()"], cwd=repo
        )
        try:
            self.assertIn("live processes", self.cli("wt", "rm", "a", "--force", ok=False).stderr)
        finally:
            proc.terminate()
            proc.wait()
        self.cli("wt", "rm", "a", "--force")
        self.assertNotIn("a", json.loads((self.base / "wt/.registry/sandboxes.json").read_text()))

    def test_here_from_main_and_worktree(self):
        self.assertIn("main | main", self.cli("here").stdout)
        self.cli("wt", "new", "here")
        self.assertIn("here | wt/here", self.cli("here", cwd=self.base / "wt/here/repo").stdout)


if __name__ == "__main__":
    unittest.main()
