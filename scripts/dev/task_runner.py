"""Headless Codex tasks with durable logs and serialized, verified landing."""
from __future__ import annotations

import argparse
from collections import deque
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import sandbox as s


def task(name):
    s.name_ok(name)
    path = s.tasks_root() / name
    if not (path / 'meta.json').exists():
        raise ValueError(f'no task: {name}')
    return path


def identity(pid):
    result = s.run('ps', '-p', str(pid), '-o', 'lstart=', '-o', 'stat=', check=False).stdout.strip()
    return ' '.join(result.split()[:-1]) if result and not result.split()[-1].startswith('Z') else None


def running(meta):
    return bool(meta.get('pid') and identity(meta['pid']) == meta.get('identity'))


def read_events(path):
    if (path / 'events.jsonl').exists():
        with (path / 'events.jsonl').open() as f:
            for line in f:
                try:
                    yield json.loads(line)
                except ValueError:
                    continue


def quota(force=False):
    # Reuse the texture runner's bounded JSON-RPC quota reader; it has no optional dependencies.
    sys.path.insert(0, str(s.REPO / 'textures'))
    from turkutex.codex.usage import used_percent
    pct = used_percent()
    if pct is None:
        raise ValueError('Codex quota unavailable; launch refused')
    if pct >= 100 and not force:
        raise ValueError(f'Codex quota {pct}%; launch refused (override with --force)')
    return pct


def launch(name, brief=None, ref='main', effort='high', force=False, followup=None):
    s.name_ok(name)
    path = s.tasks_root() / name
    path.mkdir(parents=True, exist_ok=True)
    with s.lock(path / 'lock'):
        meta = s.read_json(path / 'meta.json', {})
        if running(meta):
            raise ValueError(f'{name} is already running')
        if followup is None and meta:
            raise ValueError('task already exists; use resume')
        pct = quota(force)
        if followup is None:
            if not brief:
                raise ValueError('--brief is required')
            brief_path = Path(brief).resolve()
            prompt_brief = brief_path.read_text()
            if not (s.root() / name / 'sandbox.env').exists():
                s.new(name, ref)
            home = s.sandbox(name)
            common = (home / 'repo/docs/briefs/COMMON.md').read_text()
            prompt = (f"You are Codex agent '{name}'. Work only in {home / 'repo'}. "
                      'The exported TURKU3D_* variables define your sandbox. Do not land or merge main.\n\n'
                      + common + '\n\n' + prompt_brief)
            (path / 'prompt.md').write_text(prompt)
            meta = {'name': name, 'repo': str(home / 'repo'), 'effort': effort,
                    'brief': str(brief_path), 'created': time.time()}
        else:
            task(name)
            sessions = [e['thread_id'] for e in read_events(path) if e.get('type') == 'thread.started']
            if not sessions:
                raise ValueError('no session ID in task events')
            meta['session_id'] = sessions[-1]
            prompt = followup
            (path / f'resume-{time.time_ns()}.md').write_text(prompt)
        home = s.sandbox(name)
        env = {**os.environ, **s.read_env(home / 'sandbox.env')}
        (path / 'input.md').write_text(prompt)
        (path / 'last.md').unlink(missing_ok=True)
        meta.update(state='starting', started=time.time(), finished=None, exit_code=None, quota=pct)
        s.write_json(path / 'meta.json', meta)
        with (path / 'runner.log').open('a') as log:
            proc = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), '_worker', name],
                                    cwd=home / 'repo', env=env, start_new_session=True,
                                    stdin=subprocess.DEVNULL, stdout=log, stderr=log)
        meta.update(pid=proc.pid, identity=identity(proc.pid), state='running')
        (path / 'pid').write_text(str(proc.pid) + '\n')
        s.write_json(path / 'meta.json', meta)
    print(f'{name}: running pid={proc.pid}; logs: {path}')


def worker(name):
    path = task(name)
    # Parent writes pid and identity under this lock before we start.
    with s.lock(path / 'lock'):
        meta = s.read_json(path / 'meta.json')
    signal.signal(signal.SIGTERM, lambda *_: None)  # reap Codex and record the result after group TERM
    cmd = ['codex', 'exec', '-m', 'gpt-6-astra', '--sandbox', 'danger-full-access', '-C', meta['repo'],
           '-c', f'model_reasoning_effort="{meta["effort"]}"', '-c', 'approval_policy="never"']
    if meta.get('session_id'):
        cmd += ['resume', meta['session_id']]
    cmd += ['--json', '-o', str(path / 'last.md'), '-']
    try:
        with (path / 'input.md').open() as prompt, (path / 'events.jsonl').open('a') as events:
            rc = subprocess.call(cmd, stdin=prompt, stdout=events, cwd=meta['repo'])
    except OSError as e:
        print(e, file=sys.stderr)
        rc = 127
    with s.lock(path / 'lock'):
        meta = s.read_json(path / 'meta.json')
        meta.update(exit_code=rc, finished=time.time())
        if meta['state'] != 'stopped':
            meta['state'] = 'done' if rc == 0 else 'failed'
        meta['last_commit'] = s.git('log', '-1', '--format=%h %s', cwd=meta['repo'])
        s.write_json(path / 'meta.json', meta)


def status(name):
    path = task(name)
    meta = s.read_json(path / 'meta.json')
    state = meta['state']
    if state in ('starting', 'running') and not running(meta):
        state = 'interrupted'
    last = deque(read_events(path), maxlen=1)
    elapsed = (meta.get('finished') or time.time()) - meta['started']
    commit = s.git('log', '-1', '--format=%h %s', cwd=meta['repo']) if Path(meta['repo']).exists() else meta.get('last_commit', '?')
    print(f'{name}: {state}, {elapsed:.1f}s, last event={last[-1].get("type") if last else "none"}, commit={commit}')


def stop(name):
    path = task(name)
    with s.lock(path / 'lock'):
        meta = s.read_json(path / 'meta.json')
        if not running(meta):
            print(f'{name}: not running')
            return
        pid = meta['pid']
        if os.getpgid(pid) != pid:
            raise ValueError('task no longer owns its process group; refusing signal')
        meta.update(state='stopped', finished=time.time())
        s.write_json(path / 'meta.json', meta)
        os.killpg(pid, signal.SIGTERM)
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        try:
            os.killpg(pid, 0)
        except ProcessLookupError:
            break
        time.sleep(.05)
    else:
        # The group was verified above; descendants can outlive the supervisor.
        try:
            os.killpg(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    print(f'{name}: stopped')


def fallback_targets(paths):
    targets = set()
    for path in paths:
        if path.startswith(('scripts/dev/', 'tools/dev/')):
            targets.update(('tooling-test', 'game-dev-tools-test'))
        elif path.startswith('tools/engine-probe/'):
            targets.add('engine-probe-test')
        elif path.startswith('game/'):
            targets.update(('game-test', 'game-e2e'))
        elif path.startswith('pipeline/'):
            targets.add('pipeline-test')
        elif path.startswith('qa/'):
            targets.add('qa-test')
        elif path.startswith('textures/'):
            targets.add('textures-test')
        elif path.startswith('tools/validator/'):
            targets.add('col-test')
        elif path == 'Makefile' or path.startswith('mk/'):
            targets.update(('tooling-test', 'foundation-test', 'game-test', 'game-dev-tools-test', 'qa-test', 'textures-test'))
        elif not (path.endswith('.md') or path.startswith('docs/')):
            raise ValueError(f'no fallback gate for {path}; install scripts/dev/gate')
    return sorted(targets)


def main_checkout(repo):
    for block in s.git('worktree', 'list', '--porcelain', cwd=repo).split('\n\n'):
        if 'branch refs/heads/main' in block.splitlines():
            return Path(block.splitlines()[0].removeprefix('worktree '))
    return None


def land(name):
    path = task(name)
    with s.lock(s.root() / '.registry/land.lock'), s.lock(path / 'lock'):
        meta = s.read_json(path / 'meta.json')
        if running(meta) or meta.get('state') != 'done' or meta.get('exit_code') != 0:
            raise ValueError('task must have completed successfully before landing')
        if not (path / 'last.md').exists() or [line for line in (path / 'last.md').read_text().splitlines()
                if line.startswith('## Result:')] != ['## Result: DONE']:
            raise ValueError('result must contain an exact ## Result: DONE heading')
        repo = s.sandbox(name) / 'repo'
        if s.git('branch', '--show-current', cwd=repo) != f'wt/{name}':
            raise ValueError('sandbox is not on its expected branch')
        if s.git('status', '--porcelain', cwd=repo):
            raise ValueError('sandbox is dirty')
        main = main_checkout(repo)
        if main and s.git('status', '--porcelain', cwd=main):
            raise ValueError('main worktree is dirty')
        before = s.git('rev-parse', 'main', cwd=repo)
        try:
            s.git('rebase', 'main', cwd=repo)
        except subprocess.CalledProcessError:
            s.git('rebase', '--abort', cwd=repo, check=False)
            raise
        head = s.git('rev-parse', 'HEAD', cwd=repo)
        paths = s.git('diff', '--name-only', before, head, cwd=repo).splitlines()
        env = {**os.environ, **s.read_env(repo.parent / 'sandbox.env')}
        gate = repo / 'scripts/dev/gate'
        commands = [[str(gate), '--base', before]] if gate.exists() else [
            ['make', '-j', '4', *fallback_targets(paths)]] if fallback_targets(paths) else []
        start = time.monotonic()
        with (path / 'gate.log').open('w') as log:
            for command in commands:
                rc = subprocess.call(command, cwd=repo, env=env, stdout=log, stderr=subprocess.STDOUT)
                if rc:
                    raise ValueError(f'gate failed ({rc}): {path / "gate.log"}; main unchanged')
        if s.git('status', '--porcelain', cwd=repo) or s.git('rev-parse', 'HEAD', cwd=repo) != head:
            raise ValueError('gate changed sandbox files or HEAD; main unchanged')
        if s.git('rev-parse', 'main', cwd=repo) != before:
            raise ValueError('main changed during gate; retry landing')
        if main:
            if s.git('status', '--porcelain', cwd=main):
                raise ValueError('main became dirty during gate; main unchanged')
            s.git('merge', '--ff-only', head, cwd=main)
        else:
            s.git('update-ref', 'refs/heads/main', head, before, cwd=repo)
        record = {'name': name, 'before': before, 'commit': head, 'time': time.time(),
                  'gate_seconds': round(time.monotonic() - start, 3)}
        meta['landed'] = record
        s.write_json(path / 'meta.json', meta)
        with (s.root() / '.registry/landed.jsonl').open('a') as f:
            f.write(json.dumps(record) + '\n')
        print(f'{name}: landed {head[:12]} (gate {record["gate_seconds"]}s)')


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    subs = ap.add_subparsers(dest='cmd', required=True)
    p = subs.add_parser('run')
    p.add_argument('name')
    p.add_argument('--brief', required=True)
    p.add_argument('--from', dest='ref', default='main')
    p.add_argument('--effort', default='high', choices=('low', 'medium', 'high', 'xhigh', 'max', 'ultra'))
    p.add_argument('--force', action='store_true')
    subs.add_parser('ls')
    for cmd in ('status', 'log', 'result', 'resume', 'stop', 'land', '_worker'):
        p = subs.add_parser(cmd)
        p.add_argument('name')
        if cmd == 'log':
            p.add_argument('n', type=int, default=20, nargs='?')
        if cmd == 'resume':
            p.add_argument('text')
            p.add_argument('--force', action='store_true')
    a = ap.parse_args()
    if a.cmd == 'run':
        launch(a.name, a.brief, a.ref, a.effort, a.force)
    elif a.cmd == 'resume':
        launch(a.name, force=a.force, followup=a.text)
    elif a.cmd == 'ls':
        for f in sorted(s.tasks_root().glob('*/meta.json')):
            status(f.parent.name)
    elif a.cmd == 'status':
        status(a.name)
    elif a.cmd == 'stop':
        stop(a.name)
    elif a.cmd == 'land':
        land(a.name)
    elif a.cmd == '_worker':
        worker(a.name)
    elif a.cmd == 'result':
        print((task(a.name) / 'last.md').read_text(), end='')
    else:
        for event in deque(read_events(task(a.name)), maxlen=max(0, a.n)):
            print(json.dumps(event))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, subprocess.CalledProcessError) as e:
        print(f'codex-task: {e}' + (f'\n{e.stderr}' if isinstance(e, subprocess.CalledProcessError) else ''), file=sys.stderr)
        sys.exit(1)
