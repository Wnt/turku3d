"""Execute share publication without permitting a real network or service operation."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from test_scene_assets import fixture


SHARE = Path(__file__).resolve().parent

# The remote shell still executes: mocks cover transport, system services and the
# expensive build, while archive extraction, release installation and switching
# use real files. Old OCI credentials deliberately exist to exercise that branch.
MOCK = r'''
import json, os, pathlib, subprocess, sys
name, args = pathlib.Path(sys.argv[0]).name, sys.argv[1:]
with open(os.environ['DEPLOY_TEST_LOG'], 'a') as f:
    f.write(json.dumps({'command': name, 'args': args,
                        'share_hosts': os.environ.get('VITE_SHARE_HOSTS')}) + '\n')
if name == 'ssh':
    if args[0] != 'osgallery-dev':
        sys.exit('test forbids unexpected SSH destination')
    env = dict(os.environ, HOME=os.environ['DEPLOY_TEST_REMOTE'])
    sys.exit(subprocess.call(['bash', '-c', args[1]], env=env))
elif name == 'curl':
    url = args[-1]
    if url.endswith('/version.json'):
        print(os.environ.get('DEPLOY_TEST_VERSION') or json.dumps({'sha': os.environ['DEPLOY_TEST_SHA']}))
    else:
        print('{"state":"ready"}')
elif name == 'rsync':
    if ':' in args[-1]:
        if args[-1] != 'osgallery-dev:turku3d-local/city/scene-assets/shared/':
            sys.exit('test forbids unexpected remote rsync')
        args[-1] = str(pathlib.Path(os.environ['DEPLOY_TEST_REMOTE']) / args[-1].split(':', 1)[1])
    sys.exit(subprocess.call([os.environ['DEPLOY_TEST_RSYNC'], *args]))
elif name == 'flock':
    sys.exit(subprocess.call(args[3:]))  # -w SECONDS LOCKFILE COMMAND...
elif name == 'mv':
    paths = [a for a in args if a != '-T']  # GNU remote flag on macOS test host
    os.replace(*paths)
elif name == 'npm':
    root = pathlib.Path(args[args.index('--prefix') + 1]) if '--prefix' in args else pathlib.Path('.')
    (root / 'node_modules').mkdir(parents=True, exist_ok=True)
elif name == 'nice':
    command = args[2:]  # -n PRIORITY COMMAND...
    if command[0].endswith('/vite'):
        out = pathlib.Path(command[command.index('--outDir') + 1])
        if pathlib.Path('public').is_dir():  # vite copies public/ (including the prepared scenes) into the dist
            import shutil
            shutil.copytree('public', out)
        (out / 'assets').mkdir(parents=True, exist_ok=True)
        (out / 'index.html').write_text('<!doctype html><title>deployment fixture</title>')
        (out / 'assets' / 'fixture.js').write_text('fixture reads turku-scene/1')
    else:
        sys.exit(subprocess.call(command))
elif name not in ('node', 'systemctl'):
    sys.exit('unexpected mock invocation')
'''


class ShareDeploymentTests(unittest.TestCase):
    def deployment(self, temp):
        """A committed fixture repository, asset store, fake remote home and mocked transport."""
        if True:
            work = Path(temp)
            repo, remote, bin_dir = (work / part for part in ('repo', 'remote', 'bin'))
            for directory in (repo, remote, bin_dir, remote / '.config/systemd/user', remote / '.ssh'):
                directory.mkdir(parents=True, exist_ok=True)
            for key in ('citylab_oci_rsync', 'citylab_oci_tunnel'):
                (remote / '.ssh' / key).write_text('test-only fixture; not a key')
            for relative, content in {
                'game/package.json': '{}',
                'game/package-lock.json': '{}',
                'server/package.json': '{}',
                'server/package-lock.json': '{}',
                'tools/dev/serve-dist.mjs': '// fixture',
                'tools/dev/http.mjs': '// fixture',
                'scripts/dev/shared-tile-proxy.mjs': '// fixture',
                'pipeline/turku3d/night_lighting.json': '{}',
            }.items():
                target = repo / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content)
            shutil.copytree(SHARE / 'units', repo / 'tools/share/units')
            shutil.copy2(SHARE / 'deploy.sh', repo / 'tools/share/deploy.sh')
            shutil.copy2(SHARE / 'scene_assets.py', repo / 'tools/share/scene_assets.py')
            store = work / 'asset-store'
            store.mkdir()
            scene, image_name = fixture(store)
            (repo / 'game/public/scenes').mkdir(parents=True)
            (repo / 'game/public/scenes/office.scene.json').write_text(json.dumps(scene))
            (repo / 'tools/share/precompress.mjs').write_text('// build fixture')
            # An old deployment can copy this directory; preserve it in the
            # fixture so the regression fails on the attempted publication.
            (repo / 'tools/share/oci').mkdir()
            (repo / 'tools/share/oci/source').write_text('fixture')
            env = dict(os.environ, GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull)

            def git(*args):
                return subprocess.check_output(['git', '-C', str(repo), *args], env=env, stderr=subprocess.PIPE, text=True).strip()

            git('init', '-q')
            git('add', '.')
            git('-c', 'user.name=Test', '-c', 'user.email=test@example.invalid',
                '-c', 'commit.gpgsign=false', 'commit', '-qm', 'deployment fixture')
            sha = git('rev-parse', 'HEAD')
            pin = work / 'pin.json'
            pin.write_text(json.dumps({'service': 'http://farm.invalid', 'version': 'pilot'}))
            log = work / 'commands.jsonl'
            for command in ('ssh', 'curl', 'rsync', 'flock', 'mv', 'npm', 'nice', 'node', 'systemctl'):
                executable = bin_dir / command
                executable.write_text(f'#!{sys.executable}\n' + MOCK)
                executable.chmod(0o755)
            env.update(
                PATH=str(bin_dir) + os.pathsep + os.environ['PATH'],
                TURKU3D_SHARE_REPO=str(repo), TURKU3D_SHARE_PIN=str(pin),
                TURKU3D_SHARE_SSH='osgallery-dev',
                TURKU3D_PREPARED_ASSETS=str(store),
                DEPLOY_TEST_LOG=str(log), DEPLOY_TEST_REMOTE=str(remote),
                DEPLOY_TEST_SHA=sha, DEPLOY_TEST_RSYNC=shutil.which('rsync'),
            )
            return work, repo, remote, store, image_name, env, git, sha, log

    def test_deploy_publishes_only_lab_even_when_oci_credentials_exist(self):
        with tempfile.TemporaryDirectory(prefix='share-deploy-') as temp:
            work, repo, remote, store, image_name, env, git, sha, log = self.deployment(temp)
            run = subprocess.run(['bash', str(repo / 'tools/share/deploy.sh'), sha],
                                 env=env, capture_output=True, text=True, timeout=15)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            commands = [json.loads(line) for line in log.read_text().splitlines()]
            remote_copies = [row['args'] for row in commands
                            if row['command'] == 'rsync' and ':' in row['args'][-1]]
            self.assertEqual(len(remote_copies), 1)
            self.assertEqual(remote_copies[0][-1], 'osgallery-dev:turku3d-local/city/scene-assets/shared/')
            self.assertIn('--ignore-existing', remote_copies[0])
            services = [arg for row in commands if row['command'] == 'systemctl' for arg in row['args']]
            self.assertIn('city-forwarder', services)
            self.assertFalse(any(arg.removesuffix('.service') == 'citylab-oci-tunnel' for arg in services))
            requests = [row['args'][-1] for row in commands if row['command'] == 'curl']
            self.assertEqual(requests, [
                'https://city.lab.madekivi.fi/version.json',
                'https://city.lab.madekivi.fi/coarse/health',
            ])
            self.assertTrue(all(row['args'][0] == 'osgallery-dev'
                                for row in commands if row['command'] == 'ssh'))
            builds = [row for row in commands if row['command'] == 'nice'
                      and any(arg.endswith('/vite') for arg in row['args'])]
            self.assertEqual([row['share_hosts'] for row in builds], ['city.lab.madekivi.fi'])
            published = remote / 'turku3d-local/city/current/dist'
            self.assertTrue((published / 'index.html').is_file())
            self.assertEqual(json.loads((published / 'version.json').read_text())['sha'], sha)
            self.assertEqual(json.loads((published / 'version.json').read_text())['scenes'], sha)
            self.assertEqual((remote / 'turku3d-local/city/scene-assets/shared' / image_name).read_bytes(), b'fixture image')
            self.assertEqual(len(json.loads((published.parent / 'scene-assets.json').read_text())['files']), 2)
            verify = next(i for i, row in enumerate(commands) if row['command'] == 'ssh' and 'sha256sum --status' in row['args'][1])
            publish = next(i for i, row in enumerate(commands) if row['command'] == 'flock')
            self.assertLess(verify, publish, 'immutable asset closure must be healthy before release switch')
            # A changed local product fails during packaging, before any network or release operation.
            log.write_text('')
            (store / image_name).write_bytes(b'corrupt')
            broken = subprocess.run(['bash', str(repo / 'tools/share/deploy.sh'), sha], env=env,
                                    capture_output=True, text=True, timeout=5)
            self.assertNotEqual(broken.returncode, 0)
            self.assertIn('hash mismatch', broken.stderr)
            self.assertEqual(log.read_text(), '')
            (store / image_name).write_bytes(b'fixture image')
            (remote / 'turku3d-local/city/scene-assets/shared' / image_name).write_bytes(b'corrupt remote')
            broken = subprocess.run(['bash', str(repo / 'tools/share/deploy.sh'), sha], env=env,
                                    capture_output=True, text=True, timeout=5)
            self.assertNotEqual(broken.returncode, 0)
            commands = [json.loads(line) for line in log.read_text().splitlines()]
            self.assertFalse(any(row['command'] == 'flock' for row in commands),
                             'corrupt persistent assets must stop before release publication')


    def test_scenes_only_reuses_the_running_renderer_and_switches_atomically(self):
        with tempfile.TemporaryDirectory(prefix='share-scenes-') as temp:
            work, repo, remote, store, image_name, env, git, renderer, log = self.deployment(temp)
            deploy = repo / 'tools/share/deploy.sh'
            run = subprocess.run(['bash', str(deploy), renderer], env=env, capture_output=True, text=True, timeout=15)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            city = remote / 'turku3d-local/city'
            first = city / os.readlink(city / 'current')
            scene_path = repo / 'game/public/scenes/office.scene.json'
            scene = json.loads(scene_path.read_text())
            scene['release'] = 'appearance-change'
            scene_path.write_text(json.dumps(scene))
            git('add', '.')
            git('-c', 'user.name=Test', '-c', 'user.email=test@example.invalid',
                '-c', 'commit.gpgsign=false', 'commit', '-qm', 'appearance-only scene change')
            scenes = git('rev-parse', 'HEAD')
            log.write_text('')
            env = dict(env, DEPLOY_TEST_VERSION=json.dumps({'sha': renderer, 'scenes': scenes}))
            run = subprocess.run(['bash', str(deploy), '--scenes-only', scenes], env=env, capture_output=True, text=True, timeout=15)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            self.assertIn('published scenes:', run.stdout)
            commands = [json.loads(line) for line in log.read_text().splitlines()]
            self.assertFalse([row for row in commands if row['command'] in ('npm', 'systemctl')
                              or (row['command'] == 'nice' and any(a.endswith('/vite') for a in row['args']))],
                             'scenes-only publication must not build or restart anything')
            current = city / os.readlink(city / 'current')
            self.assertEqual(current.name, f'{renderer[:8]}-scenes-{scenes[:8]}')
            version = json.loads((current / 'dist/version.json').read_text())
            self.assertEqual((version['sha'], version['scenes']), (renderer, scenes))
            self.assertEqual((current / 'dist/index.html').stat().st_ino, (first / 'dist/index.html').stat().st_ino)
            self.assertEqual(json.loads((current / 'dist/scenes/office.scene.json').read_text())['release'], 'appearance-change')
            self.assertNotEqual(json.loads((first / 'dist/scenes/office.scene.json').read_text())['release'], 'appearance-change')
            self.assertEqual(json.loads((first / 'dist/version.json').read_text())['scenes'], renderer)
            self.assertTrue((current / 'src').is_dir())
            # A renderer that cannot read the scene schema is never handed new scenes.
            (current / 'dist/assets/fixture.js').unlink()
            (current / 'dist/assets/fixture.js').write_text('legacy renderer')
            scene['release'] = 'second-change'
            scene_path.write_text(json.dumps(scene))
            git('add', '.')
            git('-c', 'user.name=Test', '-c', 'user.email=test@example.invalid',
                '-c', 'commit.gpgsign=false', 'commit', '-qm', 'second scene change')
            refused = subprocess.run(['bash', str(deploy), '--scenes-only', git('rev-parse', 'HEAD')], env=env,
                                     capture_output=True, text=True, timeout=15)
            self.assertNotEqual(refused.returncode, 0)
            self.assertIn('does not read turku-scene/1', refused.stderr)
            self.assertEqual(city / os.readlink(city / 'current'), current)


if __name__ == '__main__':
    unittest.main()
