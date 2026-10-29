"""Resumable current-generator scoring of a durable quarter inventory, without a browser.

Run with host_slots.py build. Reference media and measurements remain private.
The original coverage and reports are never overwritten by this batch index.
"""

import argparse
import ast
from concurrent.futures import ProcessPoolExecutor, as_completed
import hashlib
import json
import os
from pathlib import Path
import subprocess
import shutil
import struct

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


def materialize_own(own, tile):
    source = Path(own['path'])
    destination = Path(os.environ['TURKU3D_WORK']) / 'gmaps-own' / own['sha256'] / tile
    destination.mkdir(parents=True, exist_ok=True)
    payload = source.read_bytes()
    if hashlib.sha256(payload).hexdigest() != own['sha256']:
        raise ValueError('Frozen OCI source changed before materialization')
    length = struct.unpack_from('<I', payload, 12)[0]
    gltf = json.loads(payload[20:20 + length])
    paths = [source.name, 'manifest.json']
    paths.extend(image['uri'] for image in gltf.get('images', [])
                 if image.get('uri') and not image['uri'].startswith('data:'))
    for relative in paths:
        original = (source.parent / relative).resolve()
        target = (destination / relative).resolve()
        if not original.is_relative_to(source.parent.parent.resolve()) or not target.is_relative_to(destination.parent.resolve()):
            raise ValueError('OCI resource escapes its frozen tree')
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(original, target)
    return destination / source.name


def score(row, root, own):
    import gmaps_round3 as base
    import scores
    from gmaps_wall_adapter import install as install_wall_sampler

    scores.ground_materials = export_ground
    install_wall_sampler()
    # The scorer prohibits reference-cache paths as own inputs. Materialize only
    # hash-verified OCI bytes in the sandbox; durable originals stay in the inventory.
    destination = materialize_own(own, row['tile'])
    original = own
    own = dict(own, path=str(destination))
    try:
        return base.score_one(row, root, frozen_own=own)
    finally:
        base.atomic_json(Path(root) / 'captures' / row['coverage_id'] / 'oci-snapshot.json', original)


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


def measurement_source(source):
    """CLI/reporting edits may resume; measurement algebra and input handling may not."""
    selected = {'export_ground', 'materialize_own', 'score', 'summary'}
    return ast.dump(ast.Module(body=[node for node in ast.parse(source).body
                                    if isinstance(node, ast.FunctionDef) and node.name in selected],
                               type_ignores=[]), include_attributes=False)


def compatible_request(previous, current):
    if previous['plan_sha256'] != current['plan_sha256']:
        return False
    if previous.get('oci_source') != current.get('oci_source'):
        return False
    if previous.get('wall_adapter_sha256') != current.get('wall_adapter_sha256'):
        return False
    repo = Path(__file__).resolve().parents[2]
    before = previous['commit']
    old = subprocess.check_output(['git', 'show', f'{before}:tools/refs/gmaps_ground_score.py'], cwd=repo)
    if hashlib.sha256(old).hexdigest() != previous['export_sha256']:
        return False
    if measurement_source(old) != measurement_source(Path(__file__).read_bytes()):
        return False
    from turku3d.coarse.packaging import producer_paths
    from turku3d.facade_kit import palette_sources
    for path in {*producer_paths(), *palette_sources()}:
        relative = path.resolve().relative_to(repo.resolve()).as_posix()
        original = subprocess.check_output(['git', 'show', f'{before}:{relative}'], cwd=repo)
        if original != path.read_bytes():
            return False
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--label', required=True, help='New immutable generator measurement label')
    parser.add_argument('--workers', type=int, default=2, choices=(1, 2))
    parser.add_argument('--oci-from',
                        help='Reuse immutable own-scene snapshots from an earlier scoring label')
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
                   export_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                   wall_adapter_sha256=hashlib.sha256(
                       Path(__file__).with_name('gmaps_wall_adapter.py').read_bytes() +
                       Path(__file__).with_name('gmaps_facade_v4.py').read_bytes()).hexdigest(),
                   oci_source=args.oci_from)
    request_path = out / 'request.json'
    if request_path.exists():
        previous = json.loads(request_path.read_text())
        if previous != request and not compatible_request(previous, request):
            raise ValueError('Measurement label already belongs to different generator/measurement source/plan')
        atomic_json(out / 'latest-invocation.json', request)
    else:
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
                    if frozen.exists():
                        own = json.loads(frozen.read_text())
                    elif args.oci_from:
                        prior = base.ROOT / 'calibration' / args.oci_from / 'oci' / tile / 'snapshot.json'
                        own = json.loads(prior.read_text())
                        if own['status'] != 'frozen' or own['tile'] != tile:
                            raise ValueError('Reused OCI snapshot is not a frozen matching tile')
                    else:
                        own = freeze_current(tile, out / 'oci')
                    if hashlib.sha256(Path(own['path']).read_bytes()).hexdigest() != own['sha256']:
                        raise ValueError('Frozen own source has changed')
                    frozen.parent.mkdir(parents=True, exist_ok=True)
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
