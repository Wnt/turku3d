"""Cache invalidation and dependency synchronization contracts."""

import fcntl
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from env_sync import sync  # noqa: E402
from gate_cache import cached_execute, stage_key  # noqa: E402
from gate_lib import run_gate  # noqa: E402
from resources import jobs  # noqa: E402


class GateStateTests(unittest.TestCase):
    def test_jobs_default_override_and_invalid(self):
        self.assertEqual(jobs({}, cores=10), 3)
        self.assertEqual(jobs({"TURKU3D_JOBS": "1"}), 1)
        for value in ("auto", "0", "-1", "2.5"):
            with self.assertRaises(ValueError):
                jobs({"TURKU3D_JOBS": value})

    def test_cache_inputs_scope_tool_version_and_red(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source.py"
            source.write_text("one")
            stage = {"name": "test", "triggers": ["*.py"]}

            def key(scope=None, tools=None):
                return stage_key(root, stage, ["source.py"], scope, {}, tools or {})

            first = key()
            self.assertNotEqual(first, stage_key(root, stage, [], None, {}, {}))
            other = root / "notes.md"
            other.write_text("unrelated")
            self.assertEqual(first, stage_key(root, stage, ["source.py", "notes.md"], None, {}, {}))
            result = {"status": "PASS", "seconds": 1, "name": "test"}
            with patch("builtins.print"):
                self.assertEqual(cached_execute(root, stage, first, lambda: result), result)
                self.assertTrue(
                    cached_execute(root, stage, first, lambda: self.fail("cache miss"))["cached"]
                )
            self.assertNotEqual(first, key(["partial"]))
            self.assertNotEqual(first, key(tools={"node": "new"}))
            source.write_text("two")
            self.assertNotEqual(first, key())
            red = {"status": "FAIL"}
            self.assertEqual(cached_execute(root, stage, key(), lambda: red), red)
            self.assertEqual(cached_execute(root, stage, key(), lambda: result), result)

    def test_sync_changed_dependencies_and_failed_install_retries(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "package.json").write_text("{}")
            lock = root / "package-lock.json"
            lock.write_text("one")
            (root / "node_modules").mkdir()
            (root / "node_modules/.package-lock.json").write_text("{}")
            good = subprocess.CompletedProcess([], 0, stdout="installed")
            bad = subprocess.CompletedProcess([], 1, stdout="failure")
            with patch("env_sync.invoke", return_value=good) as run:
                self.assertEqual(sync(root, {})["status"], "PASS")
                self.assertEqual(run.call_count, 1)
                sync(root, {})
                self.assertEqual(run.call_count, 1)
                lock.write_text("two")
                run.return_value = bad
                self.assertEqual(sync(root, {})["status"], "FAIL")
                run.return_value = good
                self.assertEqual(sync(root, {})["status"], "PASS")
                self.assertEqual(run.call_count, 3)

    def test_python_manifest_drift_reinstalls_extras(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "pipeline").mkdir()
            manifest = root / "pipeline/pyproject.toml"
            manifest.write_text("version = 'one'")
            python = root / ".venv/bin/python"
            python.parent.mkdir(parents=True)
            python.touch()
            good = subprocess.CompletedProcess([], 0, stdout="installed")
            with (
                patch("env_sync.invoke", return_value=good) as run,
            ):
                self.assertEqual(sync(root, {})["status"], "PASS")
                self.assertIn(
                    f"{root}/pipeline[dev,fetch,lidar,buildings,region]",
                    run.call_args_list[0].args[0],
                )
                self.assertEqual(run.call_count, 2)
                sync(root, {})
                self.assertEqual(run.call_count, 2)
                manifest.write_text("version = 'two'")
                sync(root, {})
                self.assertEqual(run.call_count, 4)

    def test_gate_holds_checkout_lock_through_execution(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)

            def inspect(*args, **kwargs):
                with (root / ".cache/gate.lock").open("a") as file:
                    with self.assertRaises(BlockingIOError):
                        fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return 0

            with patch("gate_lib._run_gate", side_effect=inspect):
                self.assertEqual(run_gate(root, {}, []), 0)
