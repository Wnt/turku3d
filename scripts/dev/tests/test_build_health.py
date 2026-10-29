"""Health must catch failed/slowed live passes while preserving privacy across slot switches."""

import json
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(REPO / "pipeline"), str(REPO / "scripts/dev")]
from build_health import alerts, smoke_alerts  # noqa: E402
from deploy_drafts import generator_hash_command  # noqa: E402
from turku3d.coarse.build_health import BuildHealth  # noqa: E402


class HealthTests(unittest.TestCase):
    def test_no_coverage_is_explicitly_unavailable_without_hiding_real_failures(self):
        with tempfile.TemporaryDirectory() as temp:
            health = BuildHealth(Path(temp), clock=lambda: 1000)
            health.record("full", "built", 4)
            health.record(
                "full", "FAILED", 2, "turku3d.coarse.sources.NoCoverage: only 0 ground key points"
            )
            health.record("full", "unavailable", 1)
            full = health.snapshot()["passes"]["full"]
            self.assertEqual((full["built"], full["failed"], full["unavailable"]), (1, 0, 2))
            self.assertEqual(full["success_rate"], 1)
            self.assertEqual(full["error_kinds"], {})
            health.record("full", "FAILED", 3, "ValueError: malformed geometry")
            full = health.snapshot()["passes"]["full"]
            self.assertEqual(full["failed"], 1)
            self.assertEqual(full["success_rate"], 0.5)
            self.assertEqual(full["error_kinds"], {"ValueError": 1})

    def test_rolling_counters_survive_slot_switch_and_redact_errors(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            health = BuildHealth(root, clock=lambda: 1000)
            health.record("full", "built", 31)
            health.record("full", "FAILED", 10, "RuntimeError: /private/path")
            health.record("first", "FAILED", 2, "RuntimeError: /other/path")
            health.record("full", "cancelled", 20)
            fresh = BuildHealth(root, clock=lambda: 1001).snapshot(True)
            full = fresh["passes"]["full"]
            self.assertEqual((full["built"], full["failed"], full["cancelled"]), (1, 1, 1))
            self.assertEqual(full["p50_seconds"], 31)
            self.assertEqual(len(alerts({"build_health": fresh})), 3)
            self.assertNotIn("/private", json.dumps(fresh))
            self.assertTrue(smoke_alerts({"build_health": {**fresh, "players_online": False}}))
            self.assertEqual(
                BuildHealth(root, clock=lambda: 1901).snapshot()["passes"]["full"]["built"], 0
            )
            self.assertEqual(len(alerts({"build_health": {**fresh, "players_online": False}})), 1)

    def test_content_hash_ignores_client_change_but_tracks_generator_bytes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "pipeline").mkdir()
            (root / "pipeline/a.py").write_text("one")
            command = shlex.split(generator_hash_command(temp))
            initial = subprocess.check_output(command)
            (root / "game").mkdir()
            (root / "game/client.ts").write_text("client")
            self.assertEqual(subprocess.check_output(command), initial)
            (root / "pipeline/a.py").write_text("two")
            self.assertNotEqual(subprocess.check_output(command), initial)
