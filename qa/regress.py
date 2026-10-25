#!/usr/bin/env python3
"""Compare complete QA runs with identical views, photos, judge and renderer settings."""
import argparse
import json
import math
from pathlib import Path
from qalib.paths import data_dir


def compare(baseline, current, margin=0.5):
    if not math.isfinite(margin) or margin < 0:
        raise ValueError('margin must be finite and nonnegative')
    for row in (baseline, current):
        if row.get('kind') != 'render' or not row.get('complete') or not row.get('scores'):
            raise ValueError('regression requires two complete judged render runs')
        if not row.get('commit') or not row.get('comparison_key'):
            raise ValueError('missing commit or comparison identity')
        if any(not isinstance(s, (int, float)) or not math.isfinite(s) or not 0 <= s <= 10 for s in row['scores'].values()):
            raise ValueError('invalid score')
    if baseline['comparison_key'] != current['comparison_key'] or baseline['scores'].keys() != current['scores'].keys():
        raise ValueError('incomparable runs: view/photo/pose, judge prompt/model, backend or ID context changed')
    delta = sum(current['scores'][v] - baseline['scores'][v] for v in baseline['scores']) / len(baseline['scores'])
    return {'ok': delta >= -margin, 'mean_delta': delta, 'margin': margin,
            'baseline': baseline['run_id'], 'current': current['run_id'], 'views': len(current['scores'])}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--history', type=Path, default=data_dir() / 'history.jsonl')
    ap.add_argument('--baseline', required=True, help='baseline run id (explicit, never silently reset)')
    ap.add_argument('--current', required=True, help='current run id')
    ap.add_argument('--margin', type=float, default=0.5)
    a = ap.parse_args(argv)
    try:
        rows = {r['run_id']: r for r in map(json.loads, a.history.read_text().splitlines())}
        result = compare(rows[a.baseline], rows[a.current], a.margin)
    except (ValueError, KeyError, OSError) as e:
        print(f'BLOCKED: {e}'); return 2
    print(json.dumps(result, indent=2))
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
