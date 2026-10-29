"""Compare two complete rendered scores on identical reference and ground support."""

import argparse
import json
from collections import defaultdict
from pathlib import Path

import numpy as np

from fit_ground_colour import CLASSES


def median(values):
    return float(np.median(values)) if values else None


def paired(before, after, held=()):
    a = json.loads(Path(before).read_text())['quarters']
    b = json.loads(Path(after).read_text())['quarters']
    if set(a) != set(b) or sum(s['status'] == 'scored' for s in a.values()) != 57 or \
            sum(s['status'] == 'scored' for s in b.values()) != 57 or \
            sum(s['status'] == 'abstained' for s in a.values()) != 3 or \
            sum(s['status'] == 'abstained' for s in b.values()) != 3:
        raise ValueError('Paired comparison requires identical complete 57 + 3 inventories')
    held = set(held)
    pairs = defaultdict(list)
    surfaces = defaultdict(list)
    changed_other_support = defaultdict(int)
    for key in sorted(a):
        left, right = a[key], b[key]
        if left['status'] != right['status']:
            raise ValueError(f'{key}: changed calibration eligibility')
        if left['status'] != 'scored':
            continue
        old = json.loads(Path(left['report']).read_text())
        new = json.loads(Path(right['report']).read_text())
        if old['reference_sha256'] != new['reference_sha256']:
            raise ValueError(f'{key}: reference changed')
        if old['scorer_sha256'] != new['scorer_sha256']:
            raise ValueError(f'{key}: scorer changed')
        for name, metric in old['scores'].items():
            updated = new['scores'].get(name, {})
            if metric.get('value') is None or updated.get('value') is None:
                continue
            if metric['samples'] != updated.get('samples'):
                if name == 'ground_colour_dE76':
                    raise ValueError(f'{key}: {name} sample support changed')
                changed_other_support[name] += 1
                continue
            pairs[name].append((key, metric['value'], updated['value']))
        material_a = old['ground_colour_by_material']
        material_b = new['ground_colour_by_material']
        if set(material_a) != set(material_b):
            raise ValueError(f'{key}: ground material support changed')
        grouped = defaultdict(lambda: ([], []))
        for material, first in material_a.items():
            second = material_b[material]
            if first['cells'] != second['cells']:
                raise ValueError(f'{key}: {material} ground cell support changed')
            surface = CLASSES.get(material.removeprefix('draft_'))
            if surface:
                grouped[surface][0].append(first['value'])
                grouped[surface][1].append(second['value'])
        for surface, (first, second) in grouped.items():
            surfaces[surface].append((key, median(first), median(second)))

    def summarize(rows):
        return dict(quarters=len(rows), before_median=median([a for _, a, _ in rows]),
                    after_median=median([b for _, _, b in rows]),
                    median_paired_change=median([b - a for _, a, b in rows]),
                    improved=sum(b < a for _, a, b in rows))

    ground = pairs['ground_colour_dE76']
    if len(ground) != 57:
        raise ValueError(f'Ground colour support is {len(ground)}, expected 57')
    holdout = [row for row in ground if row[0] in held]
    if held and len(holdout) != len(held):
        raise ValueError('A held-out quarter lacks paired ground colour')
    return dict(scored=57, abstained=3, ground=summarize(ground),
                holdout=summarize(holdout) if held else None,
                surfaces={surface: summarize(rows) for surface, rows in sorted(surfaces.items())},
                categories={name: summarize(rows) for name, rows in sorted(pairs.items())},
                changed_other_support=dict(changed_other_support))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--before', required=True, type=Path)
    parser.add_argument('--after', required=True, type=Path)
    parser.add_argument('--holdout-policy', type=Path)
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args()
    held = json.loads(args.holdout_policy.read_text())['final_held'] if args.holdout_policy else ()
    result = paired(args.before, args.after, held)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
