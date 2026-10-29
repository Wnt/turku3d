"""Durable, serial, low-priority verification after immutable tile publication."""

import fcntl
import json
import os
import subprocess
import sys
import time
from pathlib import Path


def atomic(path, value):
    temporary = path.with_suffix(f'.{os.getpid()}.tmp')
    temporary.write_text(json.dumps(value))
    temporary.replace(path)


def submit(out, visual, *, launch=True, require_snapshot=False):
    """Called after manifest publication; failure can never unpublish a tile."""
    root = out.parent / '.visual-audits'
    try:
        root.mkdir(exist_ok=True)
        with (root / 'enqueue.lock').open('a+b') as queue_lock:
            fcntl.flock(queue_lock, fcntl.LOCK_EX)
            identity = visual['sha256']
            result = root / f'{identity}.result.json'
            if result.exists():
                return
            # Pin this exact payload: retention may prune the former tile version.
            target = root / f'{identity}.glb'
            if not target.exists():
                os.link(out / visual['url'], target)
            expected = root / f'{identity}.snapshot'
            atomic(root / f'{identity}.pending.json', dict(
                tile=out.name, sha256=identity, file=str(target),
                expected=str(expected) if expected.exists() or require_snapshot else None,
                queued=time.time(),
            ))
            if launch:
                # The cross-process lock admits one auditor across all build workers.
                subprocess.Popen([sys.executable, '-m', __name__, str(root)],
                                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError as error:
        print(f'Post-publication audit enqueue failed: {error}', file=sys.stderr)


def drain(root):
    root = Path(root)
    os.nice(10)
    with (root / 'worker.lock').open('a+b') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        while True:
            with (root / 'enqueue.lock').open('a+b') as queue_lock:
                fcntl.flock(queue_lock, fcntl.LOCK_EX)
                pending = sorted(root.glob('*.pending.json'), key=lambda p: p.stat().st_mtime)
                if not pending:
                    # Release under the enqueue lock: a just-published job either
                    # reaches this loop or starts a new worker, never neither.
                    fcntl.flock(lock, fcntl.LOCK_UN)
                    return
            for path in pending:
                job = json.loads(path.read_text())
                started = time.monotonic()
                command = ['node', str(Path(__file__).parents[1] / 'package/visual_verify.mjs'), job['file']]
                if job['expected']:
                    command.append(job['expected'])
                try:
                    run = subprocess.run(command, capture_output=True, text=True, timeout=180)
                    if run.returncode:
                        raise RuntimeError(run.stderr[-4000:])
                    evidence = json.loads(run.stdout)
                    ok = evidence['wall_surfaces']['violations'] == 0
                    result = dict(state='passed' if ok else 'failed', **evidence)
                except (subprocess.TimeoutExpired, RuntimeError, ValueError) as error:
                    result = dict(state='failed', error=str(error))
                atomic(root / f"{job['sha256']}.result.json", dict(
                    **job, **result, completed=time.time(), seconds=time.monotonic() - started))
                path.unlink(missing_ok=True)
                Path(job['file']).unlink(missing_ok=True)
                if job['expected']:
                    Path(job['expected']).unlink(missing_ok=True)


def health(root):
    directory = root / '.visual-audits'
    results = []
    for path in directory.glob('*.result.json'):
        try:
            value = json.loads(path.read_text())
            if value['completed'] >= time.time() - 900:
                results.append(value)
        except (OSError, ValueError, KeyError):
            continue
    return dict(pending=sum(1 for _ in directory.glob('*.pending.json')),
                passed=sum(r['state'] == 'passed' for r in results),
                failed=sum(r['state'] == 'failed' for r in results),
                failed_tiles=sorted({r['tile'] for r in results if r['state'] == 'failed'}))


if __name__ == '__main__':
    drain(Path(sys.argv[1]))
