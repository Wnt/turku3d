"""Health must catch failed/slowed live passes while preserving privacy across slot switches."""
import importlib.util
import json
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(REPO / 'pipeline'), str(REPO / 'scripts/dev')]
from turku3d.coarse.build_health import BuildHealth
from build_health import alerts
from deploy_drafts import generator_hash_command

spec = importlib.util.spec_from_file_location('canary', REPO / 'tools/remote-drafts/canary.py')
canary = importlib.util.module_from_spec(spec)
spec.loader.exec_module(canary)


class HealthTests(unittest.TestCase):
    def test_rolling_counters_survive_slot_switch_and_redact_errors(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            health = BuildHealth(root, clock=lambda: 1000)
            health.record('full', 'built', 31)
            health.record('full', 'FAILED', 10, 'RuntimeError: /private/path')
            health.record('first', 'FAILED', 2, 'RuntimeError: /other/path')
            health.record('full', 'cancelled', 20)
            fresh = BuildHealth(root, clock=lambda: 1001).snapshot(True)
            full = fresh['passes']['full']
            self.assertEqual((full['built'], full['failed'], full['cancelled']), (1, 1, 1))
            self.assertEqual(full['p50_seconds'], 31)
            self.assertEqual(len(alerts({'build_health': fresh})), 3)
            self.assertNotIn('/private', json.dumps(fresh))
            self.assertEqual(BuildHealth(root, clock=lambda: 1901).snapshot()['passes']['full']['built'], 0)
            self.assertEqual(len(alerts({'build_health': {**fresh, 'players_online': False}})), 1)

    def test_content_hash_ignores_client_change_but_tracks_generator_bytes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'pipeline').mkdir()
            (root / 'pipeline/a.py').write_text('one')
            command = shlex.split(generator_hash_command(temp))
            initial = subprocess.check_output(command)
            (root / 'game').mkdir()
            (root / 'game/client.ts').write_text('client')
            self.assertEqual(subprocess.check_output(command), initial)
            (root / 'pipeline/a.py').write_text('two')
            self.assertNotEqual(subprocess.check_output(command), initial)

    def test_canary_budget_uses_median_and_hard_cap(self):
        self.assertEqual(canary.budget([]), 40)
        self.assertEqual(canary.budget([{'full_seconds': v} for v in (8, 10, 30)]), 20)
        self.assertEqual(canary.budget([{'full_seconds': 35}]), 40)
