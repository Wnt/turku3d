"""Resumable current-generator scoring of a durable quarter inventory, without a browser.

Run with host_slots.py build. Reference media and measurements remain private.
The original coverage and reports are never overwritten by this batch index.
"""

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import hashlib
import json
import os
from pathlib import Path
import subprocess

import numpy as np


def export_ground(reference, ours, masks, color_valid):
    """Export matched open-ground cell medians, including support below mismatch thresholds."""
    from compare import delta_e

    selected = masks['valid'] & color_valid & masks['calibration']
    selected &= (np.abs(reference.h - masks['ground']) < 0.5)
    selected &= (np.abs(ours.h - reference.h) < 0.5)
    result = {}
    for key in sorted(set(ours.material_keys)):
        ids = [i for i, name in enumerate(ours.material_keys) if name == key]
        target = selected & np.isin(ours.material_index, ids)
        if target.sum() < 30:
            continue
        own = np.median(ours.rgb[target], axis=0)
        truth = np.median(reference.rgb[target], axis=0)
        result[key] = dict(value=float(delta_e(own, truth)), cells=int(target.sum()),
                           own_rgb=own.tolist(), reference_rgb=truth.tolist(),
                           unit='CIE76 appearance')
    return result


def score(row, root, own):
    import gmaps_round3 as base
    import scores

    scores.ground_materials = export_ground
    return base.score_one(row, root, frozen_own=own)


def summary(states):
    observations = {}
    for state in states.values():
        if state['status'] != 'scored':
            continue
        report = json.loads(Path(state['report']).read_text())
        for name, metric in report['scores'].items():
            if metric.get('value') is not None:
                observations.setdefault(name, []).append(metric['value'])
    return dict(scored=sum(s['status'] == 'scored' for s in states.values()),
                abstained=sum(s['status'] == 'abstained' for s in states.values()),
                metrics={k: dict(quarter_median=float(np.median(v)), quarters=len(v))
                         for k, v in observations.items()})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--label', required=True, help='New immutable generator measurement label')
    parser.add_argument('--workers', type=int, default=2, choices=(1, 2))
    args = parser.parse_args()
    import gmaps_round3 as base
    from gmaps_oci import freeze_current
    from storage import atomic_json

    if Path(args.label).name != args.label or args.label in ('.', '..'):
        parser.error('label must be a single directory name')
    root = base.ROOT / 'synthetic'
    out = base.ROOT / 'calibration' / args.label
    out.mkdir(parents=True, exist_ok=True)
    request = dict(commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
                   plan_sha256=hashlib.sha256(json.dumps(base.PLAN, sort_keys=True).encode()).hexdigest(),
                   export_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    request_path = out / 'request.json'
    if request_path.exists() and json.loads(request_path.read_text()) != request:
        raise ValueError('Measurement label already belongs to different source/plan')
    atomic_json(request_path, request)
    coverage = out / 'coverage.json'
    states = json.loads(coverage.read_text())['quarters'] if coverage.exists() else {}
    snapshots = {}
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        # Keep at most one frozen tile per worker in flight, so requests remain selective.
        pending = {}
        rows = iter(base.PLAN['quarters'])
        exhausted = False
        while pending or not exhausted:
            while len(pending) < args.workers and not exhausted:
                row = next(rows, None)
                if row is None:
                    exhausted = True
                    break
                key = row['coverage_id']
                if states.get(key, {}).get('status') in ('scored', 'abstained'):
                    continue
                record = json.loads((root / 'captures' / key / 'capture.json').read_text())
                if record.get('inventory_state') != 'eligible':
                    states[key] = dict(row, status='incomplete')
                    continue
                tile = row['tile']
                if tile not in snapshots:
                    frozen = out / 'oci' / tile / 'snapshot.json'
                    own = json.loads(frozen.read_text()) if frozen.exists() else freeze_current(tile, out / 'oci')
                    if hashlib.sha256(Path(own['path']).read_bytes()).hexdigest() != own['sha256']:
                        raise ValueError('Frozen own source has changed')
                    atomic_json(frozen, own)
                    snapshots[tile] = own
                pending[pool.submit(score, row, root, snapshots[tile])] = row
            if not pending:
                continue
            done = next(as_completed(pending))
            row = pending.pop(done)
            try:
                state = dict(row, **done.result())
            except Exception as error:
                state = dict(row, status='error', error=str(error))
            states[row['coverage_id']] = state
            atomic_json(coverage, dict(quarters=states))
            atomic_json(out / 'summary.json', summary(states))
            print(json.dumps(state), flush=True)
    atomic_json(coverage, dict(quarters=states))
    result = summary(states)
    atomic_json(out / 'summary.json', result)
    print(json.dumps(result, indent=2), flush=True)
    if result['scored'] != 57 or result['abstained'] != 3:
        raise SystemExit('Full 57 scored + 3 abstained inventory not restored; inspect coverage')


if __name__ == '__main__':
    main()
