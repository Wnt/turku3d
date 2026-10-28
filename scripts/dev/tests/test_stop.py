"""Deterministic process-group exit, zombie, PID-reuse and blocking-command regressions."""

import signal
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import task_runner as runner  # noqa: E402

# os.kill/killpg and sandbox.run are shared with the real process tests. Keep
# their mocks in a separate process so they cannot intercept sibling cleanup.
SERIAL = True


class StopTests(unittest.TestCase):
    def test_run_group_cleans_descendant_after_parent_exit(self):
        command = [
            sys.executable,
            "-c",
            "import subprocess,sys; p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)']); print(p.pid, flush=True)",
        ]
        result = runner.s.run_group(command, cwd=Path.cwd(), env=runner.os.environ.copy())
        self.assertEqual(result.returncode, 0)
        child = int(result.stdout.strip())
        # The child would sleep 30 s; waiting up to 20 s for it to go tolerates a loaded host.
        deadline = runner.time.monotonic() + 20
        while True:
            status = subprocess.run(
                ["ps", "-p", str(child), "-o", "stat="], capture_output=True, text=True
            ).stdout.strip()
            if not status or status.startswith("Z") or runner.time.monotonic() > deadline:
                break
            runner.time.sleep(0.05)
        self.assertTrue(not status or status.startswith("Z"), f"child still running: {status}")

    def test_reap_only_profiles_inside_named_sandbox(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / "owned"
            (home / "repo/.git").mkdir(parents=True)
            (home / "browser/tmp").mkdir(parents=True)
            (home / "sandbox.env").write_text("export TURKU3D_PORT_BASE=5360\n")
            commands = (
                f"41 /browsers/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing --user-data-dir={home}/browser/tmp/a\n"
                f"42 /browsers/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing --user-data-dir={home}/browser2/a\n"
            )
            with patch.object(runner.s, "root", return_value=Path(directory)):
                with patch.object(
                    runner.s,
                    "run",
                    side_effect=[
                        SimpleNamespace(stdout=commands),
                        SimpleNamespace(stdout=commands.splitlines()[0].split(" ", 1)[1] + "\n"),
                        SimpleNamespace(stdout=""),
                    ],
                ):
                    with patch.object(runner.s.os, "kill") as kill:
                        self.assertEqual(runner.s.reap_browsers("owned"), [41])
                        kill.assert_called_once_with(41, signal.SIGTERM)

    def test_signal_races(self):
        meta = {"pid": 123, "identity": "birth"}
        with patch.object(runner, "group_members", side_effect=[{123: "birth"}, {}]):
            with patch.object(runner.os, "killpg", side_effect=PermissionError) as kill:
                self.assertEqual(runner.signal_group(meta), {123: "birth"})
                kill.assert_called_once_with(123, signal.SIGTERM)
        with patch.object(runner, "group_members", return_value={123: "birth"}):
            with patch.object(runner.os, "killpg", side_effect=PermissionError):
                with self.assertRaises(PermissionError):
                    runner.signal_group(meta)
        with patch.object(runner, "group_members", return_value={123: "reused"}):
            with patch.object(runner.os, "killpg") as kill:
                self.assertEqual(runner.signal_group(meta), {})
                kill.assert_not_called()
        output = "123 123 Z Sun Oct 25 22:00:00 2026\n124 123 S Sun Oct 25 22:00:01 2026\n"
        with patch.object(runner.s, "run", return_value=SimpleNamespace(stdout=output)):
            self.assertEqual(runner.group_members(123), {124: "Sun Oct 25 22:00:01 2026"})

        with patch.object(runner, "identity", side_effect=["reused", "birth"]):
            with patch.object(runner.os, "kill") as kill:
                runner.kill_members({123: "birth", 124: "birth"})
                kill.assert_called_once_with(124, signal.SIGKILL)

        # A listener outside the sandbox cwd still prevents removal; empty port
        # ranges avoid the expensive descriptor-wide socket scan entirely.
        for occupied in (False, True):
            outputs = [SimpleNamespace(stdout="p8\nfcwd\nn/tmp/owned/repo\n")]
            if occupied:
                outputs.append(SimpleNamespace(stdout="p7\nfIPv4\nn*:5360\n"))
            with patch.object(runner.s, "read_env", return_value={"TURKU3D_PORT_BASE": "5360"}):
                with patch.object(runner.s, "ports_free", return_value=not occupied):
                    with patch.object(runner.s.socket, "socket"):
                        with patch.object(runner.s, "run", side_effect=outputs) as scan:
                            self.assertEqual(
                                runner.s.live_processes(Path("/tmp/owned")),
                                [7, 8] if occupied else [8],
                            )
                            self.assertEqual(scan.call_count, 2 if occupied else 1)

    def test_wait_cmd_logs_everything_and_prints_tail_and_exit_code(self):
        cli = [sys.executable, str(Path(runner.__file__)), "wait-cmd"]
        with tempfile.TemporaryDirectory() as directory:
            tasks = Path(directory) / "tasks"
            (tasks / "demo").mkdir(parents=True)
            (tasks / "demo/meta.json").write_text("{}")
            env = {
                **runner.os.environ,
                "TURKU3D_SANDBOX": "demo",
                "TURKU3D_TASKS_ROOT": str(tasks),
                "TURKU3D_WT_ROOT": str(Path(directory) / "wt"),
            }
            script = "import sys\nfor i in range(5): print('line', i)\nsys.exit(3)"
            done = subprocess.run(
                [*cli, "--tail", "2", "--", sys.executable, "-c", script],
                env=env,
                capture_output=True,
                text=True,
            )
            self.assertEqual(done.returncode, 3)
            printed = done.stdout.splitlines()
            self.assertEqual(printed[:2], ["line 3", "line 4"])
            self.assertRegex(printed[2], r"^wait-cmd: exit 3 after [0-9.]+s; full log: ")
            log = (tasks / "demo/wait-cmd.log").read_text()
            self.assertEqual(log.splitlines(), [f"line {i}" for i in range(5)])
            # Without a task the log goes to the sandbox work dir; a timeout ends the group.
            (tasks / "demo/meta.json").unlink()
            env["TURKU3D_WORK"] = str(Path(directory) / "work")
            start = runner.time.monotonic()
            sleeper = [sys.executable, "-c", "import time; time.sleep(30)"]
            done = subprocess.run(
                [*cli, "--timeout", "0.2", *sleeper], env=env, capture_output=True, text=True
            )
            self.assertEqual(done.returncode, 124)
            # The sleeper would take 30 s: ending well before that proves the timeout ended it.
            self.assertLess(runner.time.monotonic() - start, 25)
            self.assertIn("SLOW", (Path(directory) / "work/wait-cmd.log").read_text())
            done = subprocess.run([*cli, "--"], env=env, capture_output=True, text=True)
            self.assertEqual(done.returncode, 1)
            self.assertIn("needs a command", done.stderr)
