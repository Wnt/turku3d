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

SERIAL = True


class DeploymentTests(unittest.TestCase):
    def test_standby_health_then_proxy_switch_then_stop_and_activate(self):
        with tempfile.TemporaryDirectory() as temp:
            commit = 'a' * 40
            (Path(temp) / 'build/draft-deploy' / commit).mkdir(parents=True)
            calls = []

            def remote(command):
                calls.append(command)
                if 'hashlib.sha256()' in command:
                    return 'old' if "sources/previous" in command else 'new'
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
                  patch.object(deploy_drafts, 'canary', return_value={'full_seconds': 8.}),
                  patch.object(deploy_drafts, 'remote', side_effect=remote),
                  patch.object(deploy_drafts.subprocess, 'check_output', return_value=commit),
                  patch.object(deploy_drafts.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, '', ''))):
                result = deploy_drafts.deploy(Path(temp), commit)
            start = next(i for i, c in enumerate(calls) if 'start turku-drafts-slot@7766' in c)
            health = next(i for i, c in enumerate(calls) if '7766/coarse/health' in c)
            switch = next(i for i, c in enumerate(calls) if '--signal=USR1 turku-drafts-proxy' in c)
            confirm = next(i for i, c in enumerate(calls) if 'proxy did not switch' in c)
            stop = next(i for i, c in enumerate(calls) if 'systemctl --user stop turku-drafts &&' in c)
            self.assertLess(start, health)
            self.assertLess(health, switch)
            self.assertLess(switch, confirm)
            self.assertLess(confirm, stop)
            self.assertIn("json.load(r).get('instance') == 'standby-instance'", shlex.split(calls[confirm])[2])
            self.assertIn('TURKU3D_DRAFT_STANDBY=1', calls[start])
            storage = next(i for i, c in enumerate(calls) if 'cache-storage.py' in c)
            self.assertLess(stop, storage)
            self.assertIn('TURKU3D_DRAFT_STANDBY=0', calls[storage + 1])
            self.assertIn('--signal=USR1 turku-drafts-slot@7766', calls[storage + 1])
            self.assertFalse(result['proxy_restarted'])
            self.assertEqual(result['active_port'], 7766)
            self.assertIn('update-multiplayer.sh', calls[-1])

    def test_failed_canary_never_switches_and_client_only_skips_slot(self):
        for changed in (True, False):
            with self.subTest(changed=changed), tempfile.TemporaryDirectory() as temp:
                commit = 'c' * 40
                (Path(temp) / 'build/draft-deploy' / commit).mkdir(parents=True)
                calls = []

                def remote(command):
                    calls.append(command)
                    if 'readlink -f' in command:
                        return '/previous'
                    if 'hashlib.sha256()' in command:
                        return 'old' if changed and "'/previous'" in command else 'new'
                    if command.startswith('cat ') and 'ACTIVE_PORT' in command:
                        return '7765'
                    if command.startswith('cat ') and 'ACTIVE_UNIT' in command:
                        return 'old-unit'
                    return ''

                with (patch.dict(os.environ, TURKU3D_WORK=temp),
                      patch.object(deploy_drafts, 'remote', side_effect=remote),
                      patch.object(deploy_drafts, 'canary', side_effect=ValueError('full timeout')) as build,
                      patch.object(deploy_drafts.subprocess, 'check_output', return_value=commit),
                      patch.object(deploy_drafts.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, '', ''))):
                    if changed:
                        with self.assertRaisesRegex(ValueError, 'full timeout'):
                            deploy_drafts.deploy(Path(temp), commit)
                        self.assertIn('systemctl --user stop turku-drafts-slot@7766', calls)
                    else:
                        self.assertTrue(deploy_drafts.deploy(Path(temp), commit)['generator_unchanged'])
                        build.assert_not_called()
                        self.assertFalse(any('systemctl' in c for c in calls))
                self.assertFalse(any('--signal=USR1 turku-drafts-proxy' in c for c in calls))
                self.assertFalse(any('stop old-unit' in c for c in calls))

    def test_reused_snapshot_still_updates_multiplayer(self):
        with tempfile.TemporaryDirectory() as temp:
            commit = 'b' * 40
            (Path(temp) / 'build/draft-deploy' / commit).mkdir(parents=True)
            destination = f'{deploy_drafts.HOME}/sources/{commit}'
            with (patch.dict(os.environ, TURKU3D_WORK=temp),
                  patch.object(deploy_drafts, 'remote', side_effect=['', destination, 'presence ok']) as remote,
                  patch.object(deploy_drafts.subprocess, 'check_output', return_value=commit)):
                result = deploy_drafts.deploy(Path(temp), commit)
            self.assertTrue(result['reused'])
            self.assertEqual(result['multiplayer'], 'presence ok')
            self.assertIn(f'update-multiplayer.sh {destination}', remote.call_args.args[0])

    def test_multiplayer_sync_preserves_data_and_restarts_only_on_content_change(self):
        script = Path(__file__).resolve().parents[3] / 'tools/remote-drafts/update-multiplayer.sh'
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / 'source/server'
            server = root / 'turku3d-mp/repo/server'
            source.mkdir(parents=True)
            (server / 'data').mkdir(parents=True)
            (server / 'node_modules/ws').mkdir(parents=True)
            (server / 'data/scores.json').write_text('saved scores')
            (server / 'obsolete.js').write_text('old code')
            for name in ('package.json', 'package-lock.json', 'economy.js'):
                (source / name).write_text(name)
                (server / name).write_text(name)
            (source / 'data').mkdir()
            (source / 'data/scores.json').write_text('must not replace scores')
            log = root / 'calls'
            bin_dir = root / 'bin'
            bin_dir.mkdir()
            for command in ('systemctl', 'npm', 'node', 'curl'):
                stub = bin_dir / command
                stub.write_text('#!/bin/sh\nprintf "%s\\n" "' + command + ' $*" >> "$CALL_LOG"\n')
                stub.chmod(0o755)
            env = {**os.environ, 'HOME': temp, 'CALL_LOG': str(log),
                   'PATH': str(bin_dir) + os.pathsep + os.environ['PATH']}

            def sync():
                log.write_text('')
                subprocess.run(['bash', str(script), str(source.parent)], env=env,
                               check=True, capture_output=True, text=True)
                return log.read_text()

            self.assertIn('systemctl --user restart turku-mp-server', sync())
            self.assertFalse((server / 'obsolete.js').exists())
            self.assertEqual((server / 'data/scores.json').read_text(), 'saved scores')
            self.assertTrue((server / 'node_modules/ws').is_dir())
            os.utime(source / 'economy.js', (100, 100))
            unchanged = sync()
            self.assertNotIn('systemctl', unchanged)
            self.assertNotIn('npm', unchanged)
            self.assertIn('probe-presence.mjs ws://127.0.0.1:7764/', unchanged)
            (source / 'economy.js').write_text('new payout validator')
            self.assertIn('systemctl --user restart turku-mp-server', sync())
            self.assertEqual((server / 'economy.js').read_text(), 'new payout validator')
            (source / 'package-lock.json').write_text('new dependencies')
            changed = sync()
            self.assertLess(changed.index('npm ci'), changed.index('systemctl'))
            self.assertLess(changed.index('systemctl'), changed.index('probe-presence'))
