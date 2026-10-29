"""Agent-selected real first/full builds on OCI, independent of package/upload."""

import argparse
import json
import os
import selectors
import signal
import subprocess
import sys
import time
from pathlib import Path


def source_counts(root, tile_id, first_pass):
    from turku3d import config as C
    from turku3d.coarse.inputs import gather
    from turku3d.coarse.sources import Sources
    inputs = gather(C.VisualTile.parse(tile_id), root, Sources(), first_pass=first_pass)
    return dict(buildings=len(inputs.buildings), collision=int(C.VISUAL_TILE_SIZE / C.COLLISION_TILE_SIZE) ** 2)


def run(root, *, tile_id, first_budget, full_budget):
    from turku3d.coarse.build_health import BuildHealth
    started = time.monotonic()
    out = root / '.build-probe'
    out.mkdir(parents=True, exist_ok=True)
    telemetry = BuildHealth(root)
    result = dict(tile=tile_id, passes={})
    with (out / 'worker.log').open('w') as log:
        proc = subprocess.Popen([sys.executable, '-m', 'turku3d.coarse', 'worker'],
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log,
                                text=True, start_new_session=True)
        try:
            with selectors.DefaultSelector() as ready:
                ready.register(proc.stdout, selectors.EVENT_READ)
                for kind, limit in (('first', first_budget), ('full', full_budget)):
                    source_started = time.monotonic()
                    counts = source_counts(root, tile_id, kind == 'first')
                    source_seconds = time.monotonic() - source_started
                    t = time.monotonic()
                    try:
                        proc.stdin.write(json.dumps(dict(tile=tile_id, out=str(out), first_pass=kind == 'first')) + '\n')
                        proc.stdin.flush()
                        if not ready.select(limit):
                            raise TimeoutError(f'{kind} probe exceeded {limit:.3f}s budget')
                        line = proc.stdout.readline()
                        if not line:
                            raise RuntimeError(f'{kind} worker exited; see {out}/worker.log')
                        row = json.loads(line)
                        if row['code'] != 0:
                            raise RuntimeError(f'{kind} probe failed: {row}')
                        manifest = json.loads((out / ('collision.json' if kind == 'first' else 'manifest.json')).read_text())
                        if manifest.get('pass_kind') != kind:
                            raise RuntimeError(f'{kind} probe produced no {kind} visual manifest')
                        entries = [*manifest['visual'].values(), *manifest['collision'].values()]
                        if not entries or any(not (out / entry['url']).is_file() for entry in entries):
                            raise RuntimeError(f'{kind} probe missing visual/collision output')
                        actual = dict(buildings=manifest['buildings'], collision=len(manifest['collision']))
                        if actual != counts:
                            raise RuntimeError(f'{kind} counts {actual} differ from source {counts}')
                    except Exception as error:
                        telemetry.record(kind, 'FAILED', time.monotonic() - t, type(error).__name__)
                        raise
                    seconds = round(time.monotonic() - t, 3)
                    telemetry.record(kind, 'built', seconds)
                    result['passes'][kind] = dict(seconds=seconds, budget_seconds=limit,
                                                  counts=actual, source_counts=counts,
                                                  source_seconds=round(source_seconds, 3))
        finally:
            if proc.poll() is None:
                os.killpg(proc.pid, signal.SIGTERM)
            try:
                proc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()
            proc.stdin.close()
            proc.stdout.close()
    result['seconds'] = round(time.monotonic() - started, 3)
    (out / 'result.json').write_text(json.dumps(result) + '\n')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--tile', required=True)
    parser.add_argument('--first-budget', type=float, required=True)
    parser.add_argument('--full-budget', type=float, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.root, tile_id=args.tile, first_budget=args.first_budget,
                         full_budget=args.full_budget)))
