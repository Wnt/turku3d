"""Fit aggregate surface Lab offsets with a tile-disjoint 20% quarter holdout.

All RGB measurements and per-quarter evidence stay private. The production candidate
contains aggregate surface corrections only, and optional supported source-mosaic
residuals selected by training-only cross-validation.
"""

import argparse
import hashlib
import json
import math
from pathlib import Path

import numpy as np

from turku3d.coarse.ground_colour import lab, rgb

CLASSES = {
    'asphalt_fresh': 'asphalt', 'asphalt_worn': 'asphalt',
    'concrete_slabs': 'paving', 'cobblestone': 'paving', 'quay_stone': 'paving',
    'gravel': 'gravel', 'grass': 'grass',
}


def split(rows, quarter_tiles=None, pinned=(), holdout_pool=None):
    """Reserve whole tiles to prevent neighboring quarters leaking into the fit."""
    quarter_tiles = quarter_tiles or {r['quarter']: r['tile'] for r in rows}
    quarters = set(quarter_tiles)
    target = math.ceil(len(quarters) * .2)
    by_tile = {}
    for quarter, tile in quarter_tiles.items():
        by_tile.setdefault(tile, set()).add(quarter)
    pinned = set(pinned)
    if not pinned <= quarters:
        raise ValueError('Pinned holdout includes unknown quarters')
    if holdout_pool is not None and not pinned <= set(holdout_pool):
        raise ValueError('Pinned holdout is outside the leakage-free pool')
    permitted = set(quarters if holdout_pool is None else holdout_pool)
    ordered = sorted(by_tile, key=lambda key: hashlib.sha256(('ground-holdout-v1:' + key).encode()).digest())
    held = set().union(*(by_tile[quarter_tiles[q]] for q in pinned)) if pinned else set()
    if not held <= permitted:
        raise ValueError('A pinned holdout tile contains a previously trained quarter')
    for tile in ordered:
        if len(held) >= target:
            break
        if by_tile[tile] <= permitted:
            held.update(by_tile[tile])
    if len(held) < target:
        raise ValueError('Leakage-free holdout pool has insufficient quarters')
    return [r for r in rows if r['quarter'] not in held], [r for r in rows if r['quarter'] in held], sorted(held)


def fit(rows, alpha=1, per_mosaic=False):
    groups = {}
    for surface in sorted(set(CLASSES.values())):
        group = [r for r in rows if r['surface'] == surface]
        if len({r['tile'] for r in group}) < 5:
            continue
        delta = np.median([lab(r['reference_rgb']) - lab(r['own_rgb']) for r in group], axis=0)
        # Bounded correction avoids turning baked shadows into extreme albedo.
        delta = np.clip(delta, [-15, -10, -10], [15, 10, 10]) * alpha
        groups[surface] = dict(delta_lab=delta.tolist(), training_quarters=len({r['quarter'] for r in group}))
    mosaics = {}
    if per_mosaic:
        for mosaic in sorted({r.get('mosaic') for r in rows if r.get('mosaic')}):
            for surface, global_group in groups.items():
                group = [r for r in rows if r.get('mosaic') == mosaic and r['surface'] == surface]
                support = len({r['tile'] for r in group})
                if support < 3:
                    continue
                residual = np.median([lab(r['reference_rgb']) - lab(r['own_rgb']) for r in group], axis=0)
                residual -= global_group['delta_lab']
                residual = np.clip(residual, -5, 5) * support / (support + 8)
                mosaics.setdefault(mosaic, {})[surface] = dict(delta_lab=residual.tolist(), training_tiles=support)
    return dict(version=1, method='bounded additive D65 Lab surface correction', groups=groups, mosaics=mosaics)


def prediction(row, model):
    delta = model['groups'].get(row['surface'], {}).get('delta_lab', [0, 0, 0])
    residual = model['mosaics'].get(row.get('mosaic'), {}).get(row['surface'], {}).get('delta_lab', [0, 0, 0])
    return rgb(lab(row['own_rgb']) + delta + np.asarray(residual))


def compose(base, residual):
    """Apply a fitted residual after an already deployed Lab correction."""
    result = dict(version=1, method=residual['method'], groups={}, mosaics={})
    for surface in sorted(set(base['groups']) | set(residual['groups'])):
        old = base['groups'].get(surface, {}).get('delta_lab', [0, 0, 0])
        new = residual['groups'].get(surface, {}).get('delta_lab', [0, 0, 0])
        result['groups'][surface] = dict(delta_lab=(np.asarray(old) + new).tolist(),
                                         training_quarters=residual['groups'].get(surface, {}).get('training_quarters', 0))
    for mosaic in sorted(set(base.get('mosaics', {})) | set(residual.get('mosaics', {}))):
        result['mosaics'][mosaic] = {}
        for surface in sorted(set(base.get('mosaics', {}).get(mosaic, {})) |
                              set(residual.get('mosaics', {}).get(mosaic, {}))):
            old = base.get('mosaics', {}).get(mosaic, {}).get(surface, {}).get('delta_lab', [0, 0, 0])
            new = residual.get('mosaics', {}).get(mosaic, {}).get(surface, {}).get('delta_lab', [0, 0, 0])
            result['mosaics'][mosaic][surface] = dict(delta_lab=(np.asarray(old) + new).tolist(),
                training_tiles=residual.get('mosaics', {}).get(mosaic, {}).get(surface, {}).get('training_tiles', 0))
    return result


def errors(rows, model):
    result = []
    for row in rows:
        truth = lab(row['reference_rgb'])
        result.append(dict(quarter=row['quarter'], surface=row['surface'],
                           before=float(np.linalg.norm(lab(row['own_rgb']) - truth)),
                           after=float(np.linalg.norm(lab(prediction(row, model)) - truth))))
    return result


def choose_alpha(rows):
    # Fit strength is selected without examining any held-out RGB.
    values = {}
    for alpha, per_mosaic in [(a, m) for a in (0., .25, .5, .75, 1.) for m in (False, True)]:
        observations = []
        for tile in sorted({r['tile'] for r in rows}):
            train = [r for r in rows if r['tile'] != tile]
            evaluation = [r for r in rows if r['tile'] == tile]
            observations.extend(errors(evaluation, fit(train, alpha, per_mosaic)))
        values[alpha, per_mosaic] = float(np.median([r['after'] for r in observations]))
    return min(values, key=values.get), values


def load(coverage):
    from turku3d.coarse.sources import Sources
    from turku3d.config import VisualTile

    source = Sources()
    states = json.loads(Path(coverage).read_text())['quarters']
    if sum(s['status'] == 'scored' for s in states.values()) != 57:
        raise ValueError('Fit requires the complete 57-quarter current-generator baseline')
    rows = []
    for quarter, state in states.items():
        if state['status'] != 'scored':
            continue
        report = json.loads(Path(state['report']).read_text())
        path = source.ortho_source(VisualTile.parse(state['tile']))
        for key, measured in report['ground_colour_by_material'].items():
            surface = CLASSES.get(key.removeprefix('draft_'))
            if surface is None:
                continue
            rows.append(dict(quarter=quarter, tile=state['tile'], surface=surface,
                             mosaic=path.name if path else None, **measured))
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--coverage', required=True, type=Path)
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument('--base-model', type=Path,
                        help='Existing deployed correction to which the new residual is added')
    parser.add_argument('--holdout-policy', type=Path,
                        help='JSON {pinned,pool} preserving a holdout blind to the deployed base model')
    args = parser.parse_args()
    rows = load(args.coverage)
    states = json.loads(args.coverage.read_text())['quarters']
    if args.base_model and not args.holdout_policy:
        parser.error('--base-model requires --holdout-policy to prevent pilot training leakage')
    policy = json.loads(args.holdout_policy.read_text()) if args.holdout_policy else {}
    train, held, held_quarters = split(rows,
        {key: s['tile'] for key, s in states.items() if s['status'] == 'scored'},
        pinned=policy.get('pinned', ()), holdout_pool=policy.get('pool'))
    (alpha, per_mosaic), cv = choose_alpha(train)
    residual = fit(train, alpha, per_mosaic)
    evaluations = {label: errors(group, residual) for label, group in [('training', train), ('holdout', held)]}
    model = compose(json.loads(args.base_model.read_text()), residual) if args.base_model else residual
    result = dict(alpha=alpha, per_mosaic=per_mosaic, training_cv={str(k): v for k, v in cv.items()}, held_quarters=held_quarters, splits={})
    for label, observed in evaluations.items():
        result['splits'][label] = {
            surface: dict(samples=len(selected),
                          before_median=float(np.median([r['before'] for r in selected])),
                          after_median=float(np.median([r['after'] for r in selected])))
            for surface in ['all', *sorted(set(CLASSES.values()))]
            if (selected := [r for r in observed if surface == 'all' or r['surface'] == surface])
        }
    args.out.mkdir(parents=True, exist_ok=True)
    for name, value in [('measurements', rows), ('residual', residual), ('model', model), ('summary', result), ('evaluation', evaluations)]:
        (args.out / f'{name}.json').write_text(json.dumps(value, indent=2) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
