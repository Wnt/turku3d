"""A standby serves cached textures before a graceful proxy switch stops the old writer."""
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import deploy_drafts


class DeploymentTests(unittest.TestCase):
    def test_standby_health_then_proxy_switch_then_stop_and_activate(self):
        with tempfile.TemporaryDirectory() as temp:
            commit = 'a' * 40
            (Path(temp) / 'build/draft-deploy' / commit).mkdir(parents=True)
            calls = []

            def remote(command):
                calls.append(command)
                if 'readlink -f' in command:
                    return '/home/ubuntu/turku3d-drafts/sources/previous'
                if 'ACTIVE_PORT' in command and command.startswith('cat '):
                    return '7765'
                if 'ACTIVE_UNIT' in command and command.startswith('cat '):
                    return 'turku-drafts'
                if "standby draft producer did not become ready" in command:
                    return 'standby-instance'
                if 'date -u' in command:
                    return '2026-10-28T19:31:08Z'
                return ''

            with (patch.dict(os.environ, TURKU3D_WORK=temp),
                  patch.object(deploy_drafts, 'remote', side_effect=remote),
                  patch.object(deploy_drafts.subprocess, 'check_output', return_value=commit),
                  patch.object(deploy_drafts.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, '', ''))):
                result = deploy_drafts.deploy(Path(temp), commit)
            start = next(i for i, c in enumerate(calls) if 'start turku-drafts-slot@7766' in c)
            health = next(i for i, c in enumerate(calls) if '7766/coarse/health' in c)
            switch = next(i for i, c in enumerate(calls) if '--signal=USR1 turku-drafts-proxy' in c)
            confirm = next(i for i, c in enumerate(calls) if 'proxy did not switch' in c)
            stop = next(i for i, c in enumerate(calls) if 'stop turku-drafts-prebuild turku-drafts' in c)
            self.assertLess(start, health)
            self.assertLess(health, switch)
            self.assertLess(switch, confirm)
            self.assertLess(confirm, stop)
            self.assertIn("json.load(r).get('instance') == 'standby-instance'", shlex.split(calls[confirm])[2])
            self.assertIn('TURKU3D_DRAFT_STANDBY=1', calls[start])
            self.assertIn('TURKU3D_DRAFT_STANDBY=0', calls[stop])
            self.assertIn('--signal=USR1 turku-drafts-slot@7766', calls[stop])
            self.assertFalse(result['proxy_restarted'])
            self.assertEqual(result['active_port'], 7766)
