"""Exercise frozen standby source on OCI before switching traffic; reuse stage caches."""

import argparse
import json
import os
import selectors
import signal
import statistics
import subprocess
import sys
import time
from pathlib import Path


def budget(history):
    recent = [r['full_seconds'] for r in history[-15:]]
    return min(40., 2 * statistics.median(recent)) if recent else 40.


def run(root):
    started = time.monotonic()
    history_path = root / '.canary-history.json'
    history = json.loads(history_path.read_text()) if history_path.exists() else []
    limit = budget(history)
    # Same parent as normal outputs shares .inputs/.stages and shared textures;
    # separate output prevents a first pass replacing the live full manifest.
    out = root / '.deploy-canary'
    out.mkdir(parents=True, exist_ok=True)
    result = dict(tile='t0_0_0', full_budget_seconds=round(limit, 3))
    with (out / 'worker.log').open('w') as log:
        proc = subprocess.Popen([sys.executable, '-m', 'turku3d.coarse', 'worker'],
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log,
                                text=True, start_new_session=True)
        try:
            with selectors.DefaultSelector() as ready:
                ready.register(proc.stdout, selectors.EVENT_READ)
                for kind in ('first', 'full'):
                    t = time.monotonic()
                    proc.stdin.write(json.dumps(dict(tile='t0_0_0', out=str(out), first_pass=kind == 'first')) + '\n')
                    proc.stdin.flush()
                    if not ready.select(40 if kind == 'first' else limit):
                        raise RuntimeError(f'{kind} canary exceeded {40 if kind == "first" else limit:.3f}s budget')
                    line = proc.stdout.readline()
                    if not line:
                        raise RuntimeError(f'{kind} canary worker exited; see {out}/worker.log')
                    row = json.loads(line)
                    if row['code'] != 0:
                        raise RuntimeError(f'{kind} canary failed: {row}')
                    manifest = json.loads((out / ('collision.json' if kind == 'first' else 'manifest.json')).read_text())
                    if manifest.get('pass_kind') != kind:
                        raise RuntimeError(f'{kind} canary produced no {kind} visual manifest')
                    entries = [*manifest['visual'].values(), *manifest['collision'].values()]
                    if not entries or any(not (out / entry['url']).is_file() for entry in entries):
                        raise RuntimeError(f'{kind} canary missing visual/collision output')
                    result[kind + '_seconds'] = round(time.monotonic() - t, 3)
        finally:
            # Own only this canary's process group, including packager children.
            if proc.poll() is None:
                os.killpg(proc.pid, signal.SIGTERM)
            try:
                proc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()
    result['seconds'] = round(time.monotonic() - started, 3)
    temporary = history_path.with_suffix('.tmp')
    temporary.write_text(json.dumps([*history[-14:], result]) + '\n')
    temporary.replace(history_path)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.root)))
