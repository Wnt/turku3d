"""Synthetic cancellation, timeout and stale-listener ownership regressions."""

import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import advisory_chrome as chrome  # noqa: E402

SERIAL = True


class AdvisoryChromeTests(unittest.TestCase):
    def test_cancel_and_timeout_reap_server_and_grandchild(self):
        for cancel in (True, False):
            with self.subTest(cancel=cancel), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / "game").mkdir()
                (root / "tools/dev").mkdir(parents=True)
                # A fake dist server, in the wrapper's group, with the real CLI's port.
                (root / "tools/dev/serve-dist.mjs").write_text(
                    "import net from 'node:net';\n"
                    "net.createServer().listen(Number(process.argv[process.argv.indexOf('--port')+1]), '127.0.0.1');\n"
                )
                ready = root / "ready"
                child = (
                    "import subprocess,sys,time; from pathlib import Path; "
                    "p=subprocess.Popen([sys.executable,'-c',"
                    "'import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)']); "
                    f"Path({str(ready)!r}).write_text(str(p.pid)); time.sleep(60)"
                )
                port = int(os.environ["TURKU3D_PORT_BASE"]) + 1
                env = {**os.environ, "TURKU3D_WORK": str(root / "work"), "E2E_TILE_PORT": str(port)}
                command = [sys.executable, "-c", child]
                if cancel:
                    with self.assertRaises(InterruptedError):
                        chrome.run(command, cwd=root / "game", env=env, cancelled=ready.exists)
                else:
                    result = chrome.run(command, cwd=root / "game", env=env, timeout=3)
                    self.assertEqual(result.returncode, 124)
                self.assertTrue(ready.exists(), "fake Playwright never spawned its grandchild")
                pid = int(ready.read_text())
                until = time.monotonic() + 2
                while chrome.identity(pid) is not None and time.monotonic() < until:
                    time.sleep(0.05)
                self.assertIsNone(chrome.identity(pid), "grandchild survived group cleanup")
                self.assertFalse(chrome.listening(port))
                self.assertFalse((root / "work/cache/advisory-chrome-server.json").exists())

    def test_stale_server_is_reaped_only_with_matching_identity(self):
        port = int(os.environ["TURKU3D_PORT_BASE"]) + 1
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "server.json"
            proc = subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    f"import socket,time; s=socket.socket(); s.bind(('127.0.0.1',{port})); s.listen(); time.sleep(60)",
                ],
                start_new_session=True,
            )
            try:
                until = time.monotonic() + 5
                while not chrome.listening(port) and time.monotonic() < until:
                    time.sleep(0.05)
                self.assertTrue(chrome.listening(port))
                record = {"pid": proc.pid, "group": proc.pid, "port": port, "identity": "reused"}
                path.write_text(json.dumps(record))
                with self.assertRaisesRegex(RuntimeError, "unowned listener"):
                    chrome.clear_stale(path, port)
                self.assertIsNone(proc.poll())
                record["identity"] = chrome.identity(proc.pid)
                path.write_text(json.dumps(record))
                chrome.clear_stale(path, port)
                proc.wait(timeout=2)
                self.assertFalse(chrome.listening(port))
                self.assertFalse(path.exists())
            finally:
                if proc.poll() is None:
                    os.killpg(proc.pid, signal.SIGKILL)
                    proc.wait()
