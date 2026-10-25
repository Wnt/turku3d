"""Integration tests use tiny temporary git repos, fake Codex and no shared data."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

SOURCE = Path(__file__).resolve().parents[3]


class ToolingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='turku tooling-')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.repo = self.base / 'main'
        self.repo.mkdir()
        shutil.copytree(SOURCE / 'scripts/dev', self.repo / 'scripts/dev', ignore=shutil.ignore_patterns('__pycache__'))
        shutil.copytree(SOURCE / 'textures/turkutex/codex', self.repo / 'textures/turkutex/codex')
        (self.repo / 'docs/briefs').mkdir(parents=True)
        (self.repo / 'docs/briefs/COMMON.md').write_text('Common test policy.\n')
        (self.repo / 'brief.md').write_text('Tiny fake test brief.\n')
        (self.repo / '.gitignore').write_text('__pycache__/\n')
        self.bin = self.base / 'bin'
        self.bin.mkdir()
        shutil.copy(SOURCE / 'scripts/dev/tests/fake_codex.py', self.bin / 'codex')
        (self.bin / 'codex').chmod(0o755)
        self.env = {**os.environ, 'TURKU3D_WT_ROOT': str(self.base / 'wt'),
                    'TURKU3D_TASKS_ROOT': str(self.base / 'tasks'), 'TURKU3D_DATA': str(self.base / 'data'),
                    'PATH': str(self.bin) + os.pathsep + os.environ['PATH']}
        self.env.pop('TURKUTEX_USAGE_CMD', None)
        # Synthetic registries must still avoid the caller's real, concurrently running stacks.
        registry = Path(os.environ.get('TURKU3D_WT_ROOT', Path.home() / 'turku3d/wt')) / '.registry/sandboxes.json'
        reservations = json.loads(registry.read_text()) if registry.exists() else {}
        reservations['reserved-current'] = {'port': int(os.environ.get('TURKU3D_PORT_BASE', '5170'))}
        # Deduplicate manual reservations before importing them into the test registry.
        reservations = {f'reserved-{port}': {'port': port} for port in {r['port'] for r in reservations.values()}}
        (self.base / 'wt/.registry').mkdir(parents=True)
        (self.base / 'wt/.registry/sandboxes.json').write_text(json.dumps(reservations))
        self.git('init', '-b', 'main')
        self.git('config', 'user.email', 'test@example.invalid')
        self.git('config', 'user.name', 'Tooling test')
        self.git('add', '.')
        self.git('commit', '-qm', 'Initial fixture')

    def git(self, *args, cwd=None):
        return subprocess.run(['git', *args], cwd=cwd or self.repo, env=self.env, text=True,
                              capture_output=True, check=True).stdout.strip()

    def cli(self, tool, *args, ok=True, env=None, cwd=None):
        result = subprocess.run([str(self.repo / 'scripts/dev' / tool), *args], cwd=cwd or self.repo,
                                env=env or self.env, capture_output=True, text=True, timeout=20)
        if ok:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout)
        return result

    def wait_done(self, name):
        meta = self.base / 'tasks' / name / 'meta.json'
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            row = json.loads(meta.read_text())
            if row['state'] not in ('starting', 'running'):
                self.assertEqual(row['state'], 'done', row)
                return row
            time.sleep(.02)
        self.fail('fake task did not complete')

    def start(self, name='demo'):
        self.cli('codex-task', 'run', name, '--brief', 'brief.md')
        self.wait_done(name)
        return self.base / 'wt' / name / 'repo'

    def commit(self, repo, name='change.md'):
        (repo / name).write_text('A trivial change.\n')
        self.git('add', name, cwd=repo)
        self.git('commit', '-qm', 'Test change', cwd=repo)

    def test_concurrent_port_claims_and_env(self):
        with ThreadPoolExecutor(max_workers=6) as pool:
            list(pool.map(lambda i: self.cli('wt', 'new', f'w{i}'), range(6)))
        records = json.loads((self.base / 'wt/.registry/sandboxes.json').read_text())
        ports = [v['port'] for k, v in records.items() if k.startswith('w')]
        self.assertEqual(len(set(ports)), 6)
        for p in ports:
            self.assertTrue(all(p == q or abs(p - q) >= 10 for q in ports))
        output = self.cli('wt', 'env', 'w0').stdout
        loaded = subprocess.run(['bash', '-c', output + '\nprintf "%s" "$TURKU3D_WORK"'],
                                capture_output=True, text=True, check=True)
        self.assertEqual(loaded.stdout, str(self.base / 'wt/w0/work'))
        self.assertTrue((self.base / 'wt/w0/work/qa').is_dir())
        self.assertEqual(self.cli('wt', 'path', 'w0').stdout.strip(), str(self.base / 'wt/w0/repo'))

    def test_adopts_manual_reservations(self):
        self.cli('wt', 'new', 'manual')
        old = json.loads((self.base / 'wt/.registry/sandboxes.json').read_text())['manual']['port']
        (self.base / 'wt/.registry/sandboxes.json').unlink()
        self.cli('wt', 'new', 'next')
        rows = json.loads((self.base / 'wt/.registry/sandboxes.json').read_text())
        self.assertEqual(rows['manual']['port'], old)
        self.assertNotEqual(rows['next']['port'], old)

    def test_run_resume_and_logs(self):
        self.start()
        self.assertIn('## Result: DONE', self.cli('codex-task', 'result', 'demo').stdout)
        self.assertIn('done', self.cli('codex-task', 'status', 'demo').stdout)
        self.cli('codex-task', 'resume', 'demo', 'Follow-up with $literal and `text`.')
        self.wait_done('demo')
        self.assertIn('Follow-up', self.cli('codex-task', 'result', 'demo').stdout)
        self.assertIn('turn.completed', self.cli('codex-task', 'log', 'demo', '1').stdout)
        self.cli('codex-task', 'run', 'demo', '--brief', 'brief.md', ok=False)

    def test_quota_refusal_and_override(self):
        env = {**self.env, 'FAKE_QUOTA': '100'}
        self.assertIn('quota 100%', self.cli('codex-task', 'run', 'full', '--brief', 'brief.md', env=env, ok=False).stderr)
        self.assertFalse((self.base / 'wt/full').exists())
        self.assertIn('unavailable', self.cli('codex-task', 'run', 'unknown', '--brief', 'brief.md',
                      '--force', env={**self.env, 'FAKE_QUOTA': 'unknown'}, ok=False).stderr)
        self.cli('codex-task', 'run', 'full', '--brief', 'brief.md', '--force', env=env)
        self.wait_done('full')

    def test_stop_and_duplicate_run(self):
        self.cli('codex-task', 'run', 'slow', '--brief', 'brief.md', env={**self.env, 'FAKE_SLEEP': '1'})
        self.addCleanup(lambda: self.cli('codex-task', 'stop', 'slow'))
        self.assertIn('already running', self.cli('codex-task', 'run', 'slow', '--brief', 'brief.md', ok=False).stderr)
        deadline = time.monotonic() + 5
        child = None
        while time.monotonic() < deadline:
            events = self.base / 'tasks/slow/events.jsonl'
            if events.exists():
                rows = [json.loads(line) for line in events.read_text().splitlines() if line]
                child = next((row['pid'] for row in rows if row['type'] == 'fake.child'), None)
                if child:
                    break
            time.sleep(.02)
        self.assertIsNotNone(child, 'fake Codex never launched its descendant')
        self.cli('codex-task', 'stop', 'slow')
        self.assertIn('stopped', self.cli('codex-task', 'status', 'slow').stdout)
        state = subprocess.run(['ps', '-p', str(child), '-o', 'stat='], capture_output=True, text=True).stdout.strip()
        self.assertTrue(not state or state.startswith('Z'), f'child still running: {state}')

    def test_land_success(self):
        repo = self.start()
        self.commit(repo)
        expected = self.git('rev-parse', 'HEAD', cwd=repo)
        self.cli('codex-task', 'land', 'demo')
        self.assertEqual(self.git('rev-parse', 'main'), expected)
        self.assertTrue((self.repo / 'change.md').exists())
        self.assertFalse(self.git('status', '--porcelain'))

    def test_land_refuses_result_dirty_and_red_gate(self):
        repo = self.start()
        self.commit(repo)
        before = self.git('rev-parse', 'main')
        result = self.base / 'tasks/demo/last.md'
        result.write_text('## Result: BLOCKED\n')
        self.assertIn('DONE', self.cli('codex-task', 'land', 'demo', ok=False).stderr)
        result.write_text('## Result: DONE\n')
        (repo / 'dirty.md').write_text('dirty')
        self.assertIn('dirty', self.cli('codex-task', 'land', 'demo', ok=False).stderr)
        (repo / 'dirty.md').unlink()
        (self.repo / 'dirty-main.md').write_text('dirty')
        self.assertIn('main worktree is dirty', self.cli('codex-task', 'land', 'demo', ok=False).stderr)
        (self.repo / 'dirty-main.md').unlink()
        gate = repo / 'scripts/dev/gate'
        gate.write_text('#!/bin/sh\necho planted failure\nexit 1\n')
        gate.chmod(0o755)
        self.git('add', '.', cwd=repo)
        self.git('commit', '-qm', 'Red gate', cwd=repo)
        self.assertIn('gate failed', self.cli('codex-task', 'land', 'demo', ok=False).stderr)
        self.assertEqual(self.git('rev-parse', 'main'), before)
        self.assertIn('planted failure', (self.base / 'tasks/demo/gate.log').read_text())

    def test_land_refuses_main_change_during_gate(self):
        repo = self.start()
        gate = repo / 'scripts/dev/gate'
        gate.write_text('#!/bin/sh\ngit -C ' + shlex.quote(str(self.repo)) + ' -c user.name=test -c user.email=test@example.invalid commit --allow-empty -qm concurrent\n')
        gate.chmod(0o755)
        self.git('add', '.', cwd=repo)
        self.git('commit', '-qm', 'Gate fixture', cwd=repo)
        self.assertIn('main changed', self.cli('codex-task', 'land', 'demo', ok=False).stderr)
        self.assertNotEqual(self.git('rev-parse', 'main'), self.git('rev-parse', 'HEAD', cwd=repo))

    def test_rm_refuses_unmerged_and_live_then_releases(self):
        self.cli('wt', 'new', 'a')
        repo = self.base / 'wt/a/repo'
        self.commit(repo)
        self.assertIn('unmerged', self.cli('wt', 'rm', 'a', ok=False).stderr)
        proc = subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(30)'], cwd=repo)
        try:
            self.assertIn('live processes', self.cli('wt', 'rm', 'a', '--force', ok=False).stderr)
        finally:
            proc.terminate()
            proc.wait()
        self.cli('wt', 'rm', 'a', '--force')
        self.assertNotIn('a', json.loads((self.base / 'wt/.registry/sandboxes.json').read_text()))

    def test_here_from_main_and_worktree(self):
        start = time.monotonic()
        self.assertIn('main | main', self.cli('here').stdout)
        self.assertLess(time.monotonic() - start, 2)
        self.cli('wt', 'new', 'here')
        self.assertIn('here | wt/here', self.cli('here', cwd=self.base / 'wt/here/repo').stdout)


if __name__ == '__main__':
    unittest.main()
