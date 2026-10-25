"""Small macOS/Linux worktree registry; no third-party Python dependencies."""
from __future__ import annotations

import contextlib
import fcntl
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import socket
import subprocess
import time

REPO = Path(__file__).resolve().parents[2]


def run(*args, cwd=REPO, check=True, **kwargs):
    return subprocess.run(args, cwd=cwd, check=check, text=True, capture_output=True, **kwargs)


def git(*args, cwd=REPO, check=True):
    return run('git', *args, cwd=cwd, check=check).stdout.strip()


def root():
    return Path(os.environ.get('TURKU3D_WT_ROOT', Path.home() / 'turku3d/wt')).expanduser().resolve()


def tasks_root():
    return Path(os.environ.get('TURKU3D_TASKS_ROOT', Path.home() / 'turku3d/tasks')).expanduser().resolve()


def name_ok(name):
    if not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,63}', name) or name == 'main':
        raise ValueError('name must be 1–64 lowercase letters/digits/hyphens/underscores, except main')
    return name


@contextlib.contextmanager
def lock(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a') as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        yield


def read_json(path, default=None):
    return json.loads(path.read_text()) if path.exists() else default


def write_json(path, value):
    temp = path.with_name(path.name + f'.{os.getpid()}.tmp')
    temp.write_text(json.dumps(value, indent=2) + '\n')
    temp.replace(path)


def exports(env):
    return ''.join(f'export {key}={shlex.quote(str(value))}\n' for key, value in env.items())


def read_env(path):
    env = {}
    for line in path.read_text().splitlines():
        parts = shlex.split(line)
        if len(parts) == 2 and parts[0] == 'export':
            key, value = parts[1].split('=', 1)
            env[key] = value
    return env


def environment(name, port):
    home = root() / name
    return {'TURKU3D_DATA': os.environ.get('TURKU3D_DATA', str(Path.home() / 'turku3d/data')),
            'TURKU3D_WORK': str(home / 'work'), 'TURKU3D_SANDBOX': name,
            'TURKU3D_PORT_BASE': str(port), 'TURKU3D_BROWSER_DIR': str(home / 'browser')}


def registry():
    records = read_json(root() / '.registry/sandboxes.json', {})
    # Adopt the orchestrator's manually created worktrees without changing their env or ports.
    for env_file in root().glob('*/sandbox.env'):
        name = env_file.parent.name
        if name not in records and (env_file.parent / 'repo/.git').exists():
            env = read_env(env_file)
            records[name] = {'port': int(env['TURKU3D_PORT_BASE']), 'created': env_file.stat().st_mtime}
    ports = [r['port'] for r in records.values()]
    if len(ports) != len(set(ports)):
        raise ValueError('duplicate port blocks in existing sandbox.env files')
    return records


def save_registry(records):
    write_json(root() / '.registry/sandboxes.json', records)


def ports_free(base):
    with contextlib.ExitStack() as stack:
        try:
            for port in range(base, base + 10):
                s = stack.enter_context(socket.socket())
                s.bind(('127.0.0.1', port))
        except OSError:
            return False
    return True


def new(name, ref='main'):
    name_ok(name)
    with lock(root() / '.registry/lock'):
        records = registry()
        home = root() / name
        if name in records or home.exists():
            raise ValueError(f'sandbox already exists: {name}')
        base = next((p for p in range(5180, 65000, 10)
                     if all(abs(p - r['port']) >= 10 for r in records.values()) and ports_free(p)), None)
        if base is None:
            raise ValueError('no free port block')
        # Git validates REF before any registry entry is committed.
        git('rev-parse', '--verify', ref + '^{commit}')
        home.mkdir(parents=True)
        try:
            git('worktree', 'add', '-b', f'wt/{name}', str(home / 'repo'), ref)
            for sub in ('build', 'cache', 'dist', 'reports', 'qa'):
                (home / 'work' / sub).mkdir(parents=True)
            (home / 'browser').mkdir()
            (home / 'sandbox.env').write_text(exports(environment(name, base)))
            records[name] = {'port': base, 'created': time.time()}
            save_registry(records)
        except BaseException:
            if (home / 'repo/.git').exists():
                git('worktree', 'remove', '--force', str(home / 'repo'), check=False)
                git('branch', '-D', f'wt/{name}', check=False)
            shutil.rmtree(home)
            raise
    return home / 'repo'


def sandbox(name):
    name_ok(name)
    home = root() / name
    if not (home / 'sandbox.env').is_file() or not (home / 'repo/.git').exists():
        raise ValueError(f'no sandbox: {name}')
    return home


def live_processes(home):
    """Processes whose cwd is inside the sandbox, or which listen on its ports."""
    env = read_env(home / 'sandbox.env')
    base = int(env['TURKU3D_PORT_BASE'])
    pids = set()
    # One scan, with OR selectors: cwd entries plus listeners on the reserved block.
    out = run('lsof', '-nP', '-Fpnf', '-d', 'cwd',
              f'-iTCP:{base}-{base + 9}', '-sTCP:LISTEN', check=False).stdout
    pid, descriptor = None, None
    for line in out.splitlines():
        if line.startswith('p'):
            pid = int(line[1:])
        elif line.startswith('f'):
            descriptor = line[1:]
        elif line.startswith('n'):
            if descriptor != 'cwd' or line[1:] == str(home) or line[1:].startswith(str(home) + '/'):
                pids.add(pid)
    return sorted(pids - {os.getpid()})


def remove(name, force=False):
    with lock(root() / '.registry/lock'):
        home = sandbox(name)
        repo = home / 'repo'
        live = live_processes(home)
        # --force may discard work, but never kills somebody else's processes.
        if live:
            raise ValueError(f'live processes in {name}: {live}; stop them before removal')
        if not force:
            if git('status', '--porcelain', cwd=repo):
                raise ValueError('worktree is dirty; use --force to discard it')
            if run('git', 'merge-base', '--is-ancestor', 'HEAD', 'main', cwd=repo, check=False).returncode:
                raise ValueError('unmerged commits; land first or use --force')
        records = registry()
        git('worktree', 'remove', *(['--force'] if force else []), str(repo))
        git('branch', '-D', f'wt/{name}')
        shutil.rmtree(home)
        records.pop(name, None)
        save_registry(records)


def gc():
    with lock(root() / '.registry/lock'):
        git('worktree', 'prune')
        records = registry()
        # Never delete directories or release a block for a damaged but extant sandbox.
        for name in list(records):
            if not (root() / name).exists():
                del records[name]
        save_registry(records)


def list_sandboxes():
    with lock(root() / '.registry/lock'):
        records = registry()
        save_registry(records)
    for name, row in sorted(records.items()):
        home = root() / name
        repo = home / 'repo'
        if not repo.exists():
            print(f'{name}: missing (run wt gc)')
            continue
        behind, ahead = git('rev-list', '--left-right', '--count', 'main...HEAD', cwd=repo).split()
        size = run('du', '-sh', str(home)).stdout.split('\t')[0]
        print(f'{name:20} {git("branch", "--show-current", cwd=repo):24} +{ahead}/-{behind} '
              f'ports={row["port"]}..{row["port"] + 9} pids={live_processes(home)} size={size}')
