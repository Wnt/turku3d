"""Public deployment preserves the proxy unless its configuration actually changes."""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import deploy_drafts  # noqa: E402


class DeploymentTests(unittest.TestCase):
    def exercise(self, changed):
        with tempfile.TemporaryDirectory() as temp:
            commit = "a" * 40
            (Path(temp) / "build/draft-deploy" / commit).mkdir(parents=True)
            calls = []

            def remote(command):
                calls.append(command)
                if "readlink -f" in command:
                    return "/home/ubuntu/turku3d-drafts/sources/previous"
                if command.startswith("cmp -s"):
                    return "changed" if changed else "same"
                if "date -u" in command:
                    return "2026-10-28T19:31:08Z"
                return ""

            with (
                patch.dict(os.environ, TURKU3D_WORK=temp),
                patch.object(deploy_drafts, "remote", side_effect=remote),
                patch.object(deploy_drafts.subprocess, "check_output", return_value=commit),
                patch.object(
                    deploy_drafts.subprocess,
                    "run",
                    return_value=subprocess.CompletedProcess([], 0, "changed source only", ""),
                ),
            ):
                result = deploy_drafts.deploy(Path(temp), commit)
            activation = next(c for c in calls if "systemctl --user stop" in c)
            self.assertIn("restart --no-block turku-drafts-prebuild", activation)
            self.assertEqual("restart turku-drafts-proxy" in activation, changed)
            self.assertEqual(result["proxy_restarted"], changed)
            self.assertIn("coarse/health", calls[-1])
            self.assertIn("time.monotonic()+5", calls[-1])
            self.assertEqual(result["restarted_utc"], "2026-10-28T19:31:08Z")

    def test_unchanged_proxy_keeps_its_connections_and_waits_for_producer(self):
        self.exercise(False)

    def test_changed_proxy_is_restarted_before_health_verification(self):
        self.exercise(True)


if __name__ == "__main__":
    unittest.main()
