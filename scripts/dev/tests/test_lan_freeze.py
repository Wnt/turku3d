"""An active LAN freeze blocks producer switches while preserving client publication."""

import json
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import lan_freeze


class FreezeTests(unittest.TestCase):
    def test_missing_or_future_marker_allows_deploy_active_marker_refuses(self):
        with tempfile.TemporaryDirectory() as temp:
            marker = Path(temp) / "freeze.json"
            with patch.object(lan_freeze, "MARKER", str(marker)):
                command = shlex.split(lan_freeze.guard_command())
                self.assertEqual(subprocess.run(command, capture_output=True).returncode, 0)
                marker.write_text(json.dumps(dict(freeze_at="2099-10-30T04:00:00+02:00")))
                self.assertEqual(subprocess.run(command, capture_output=True).returncode, 0)
                marker.write_text(json.dumps(dict(freeze_at="2020-10-30T04:00:00+02:00")))
                run = subprocess.run(command, capture_output=True, text=True)
                self.assertNotEqual(run.returncode, 0)
                self.assertIn("LAN generator freeze active", run.stderr)

    def test_cutoff_requires_offset(self):
        with self.assertRaisesRegex(ValueError, "UTC offset"):
            lan_freeze.arm("2026-10-30T04:00:00")
