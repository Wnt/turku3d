"""Deterministic process-group exit, zombie and PID-reuse regressions."""

import signal
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import task_runner as runner  # noqa: E402


class StopTests(unittest.TestCase):
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
