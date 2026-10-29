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
            upstream = Path(temp) / 'build/draft-deploy' / commit / 'tools/remote-drafts/draft-upstream.caddy'
            upstream.parent.mkdir(parents=True)
            upstream.write_text('reverse_proxy 127.0.0.1:7765 {\n transport http {\n response_header_timeout 30s\n }\n}\n')

            def remote(command):
                calls.append(command)
                if 'hashlib.sha256()' in command:
                    return 'old' if "sources/previous" in command else 'new'
                if 'readlink -f' in command:
                    return '/home/ubuntu/turku3d-drafts/sources/previous'
                if 'ACTIVE_PORT' in command and command.startswith('cat '):
                    return '7765'
                if 'for port in range(7765, 7770)' in command:
                    return '7766'
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
            stop = next(i for i, c in enumerate(calls) if 'systemctl --user stop --no-block turku-drafts &&' in c)
            self.assertIn('response_header_timeout 30s', calls[switch])
            self.assertIn('127.0.0.1:7766', calls[switch])
            self.assertLess(start, health)
            self.assertLess(health, switch)
            self.assertLess(switch, confirm)
            self.assertLess(confirm, stop)
            self.assertIn("json.load(r).get('instance') == 'standby-instance'", shlex.split(calls[confirm])[2])
            self.assertIn('TURKU3D_DRAFT_STANDBY=1', calls[start])
            self.assertFalse(any('cache-storage.py' in c for c in calls))
            self.assertIn('TURKU3D_DRAFT_STANDBY=0', calls[stop + 1])
            self.assertIn('--signal=USR1 turku-drafts-slot@7766', calls[stop + 1])
            self.assertEqual(result['retiring_unit'], 'turku-drafts')
            self.assertFalse(result['proxy_restarted'])
            self.assertEqual(result['active_port'], 7766)
            self.assertIn('update-multiplayer.sh', calls[-1])

    def test_ready_standby_is_resumed_before_allocating_another_port(self):
        with tempfile.TemporaryDirectory() as temp:
            (Path(temp) / "slot-7769.env").write_text(
                "TURKU3D_DRAFT_SOURCE=/standby\nTURKU3D_DRAFT_STANDBY=1\n")
            script = shlex.split(deploy_drafts.select_port_command("/standby"))[2]
            script = script.replace(repr(deploy_drafts.HOME), repr(temp))
            # Matching standby avoids binding any new ports, even though earlier ports are free.
            result = subprocess.check_output([sys.executable, "-c", script], text=True)
            self.assertEqual(result.strip(), "7769")

    def test_systemd_only_signals_parent_while_build_workers_drain(self):
        unit = Path(__file__).resolve().parents[3] / 'tools/remote-drafts/turku-drafts-slot@.service'
        text = unit.read_text()
        self.assertIn('KillMode=mixed', text)
        self.assertIn('TimeoutStopSec=600', text)
        self.assertNotIn('KillMode=control-group', text)

    def test_client_only_source_skips_standby_start(self):
        with tempfile.TemporaryDirectory() as temp:
            commit = 'c' * 40
            (Path(temp) / 'build/draft-deploy' / commit).mkdir(parents=True)
            calls = []
            def remote(command):
                calls.append(command)
                return '/previous' if 'readlink -f' in command else 'same'
            with (patch.dict(os.environ, TURKU3D_WORK=temp),
                  patch.object(deploy_drafts, 'remote', side_effect=remote),
                  patch.object(deploy_drafts.subprocess, 'check_output', return_value=commit),
                  patch.object(deploy_drafts.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, '', ''))):
                self.assertTrue(deploy_drafts.deploy(Path(temp), commit)['generator_unchanged'])
            self.assertFalse(any('systemctl' in c for c in calls))
            self.assertFalse(any('canary' in c or 'probe-builds' in c for c in calls))

    def test_reused_snapshot_still_updates_multiplayer(self):
        with tempfile.TemporaryDirectory() as temp:
            commit = 'b' * 40
            (Path(temp) / 'build/draft-deploy' / commit).mkdir(parents=True)
            destination = f'{deploy_drafts.HOME}/sources/{commit}'
            with (patch.dict(os.environ, TURKU3D_WORK=temp),
                  patch.object(deploy_drafts, 'remote', side_effect=['', destination, commit, 'presence ok']) as remote,
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
