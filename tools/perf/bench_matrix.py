#!/usr/bin/env python3
"""Run bench lab variants (game/src/qa/lab) through tools/perf/run-bench.mjs and print one comparison table.

  python3 tools/perf/bench_matrix.py 'bench=static' --off shadows,post,bloom,vegetation [--repeat 3]
  python3 tools/perf/bench_matrix.py 'bench=install&tiles=t0_0_0' --variants 'quality=high' 'off=facades'
  python3 tools/perf/bench_matrix.py --results ~/turku3d/captures/bench-lab/*.json      # tabulate saved results

The first row is the base query; `--off a,b` adds one variant per ablation (`&off=a`), `--variants` adds query
suffixes as given. With --repeat each metric is the median over the repeats. Deltas are against the first row.
A capture folder (meta.json with bench.result) can stand in for a result JSON.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import re
import statistics
import subprocess
import sys
import time

RUNNER = Path(__file__).with_name('run-bench.mjs')

# (column, path in the result, digits)
COLUMNS = [
    ('fps', ('summary', 'fps'), 1),
    ('cpu50', ('summary', 'cpuMs', 'p50'), 1), ('cpu95', ('summary', 'cpuMs', 'p95'), 1), ('cpu99', ('summary', 'cpuMs', 'p99'), 1),
    ('gpu50', ('summary', 'gpuMs', 'p50'), 1), ('gpu95', ('summary', 'gpuMs', 'p95'), 1), ('gpu99', ('summary', 'gpuMs', 'p99'), 1),
    ('int50', ('summary', 'intervalMs', 'p50'), 1), ('int99', ('summary', 'intervalMs', 'p99'), 1),
    ('max', ('summary', 'intervalMs', 'max'), 0), ('>50ms', ('summary', 'longFrames', 'over50'), 0),
    ('draws', ('summary', 'draws', 'p50'), 0), ('Mtris', ('summary', 'tris', 'p50'), 2),
    ('gcMin', ('gc', 'minor'), 0), ('gcMaj', ('gc', 'major'), 0), ('gcMs', ('gc', 'ms'), 0), ('mainMs', ('gc', 'busyMs'), 0),
]
DELTAS = ['fps', 'cpu95', 'gpu50', 'int99']


def variants(base: str, off: list[str], extra: list[str]) -> list[str]:
    base = base.lstrip('?')
    out = [base]
    out += [f'{base}&off={flag}' for flag in off]
    out += [f'{base}&{suffix.lstrip("&?")}' for suffix in extra]
    return list(dict.fromkeys(out))


def load(path: Path) -> dict:
    if path.is_dir():
        meta = json.loads((path / 'meta.json').read_text())
        result = (meta.get('bench') or {}).get('result')
        if not result:
            raise ValueError(f'{path}: capture without bench.result')
        return result
    return json.loads(path.read_text())


def trace_window(result: dict) -> dict | None:
    windows = (result.get('trace') or {}).get('windows') or {}
    return windows.get(result.get('mode')) or windows.get('install')


def metrics(result: dict) -> dict[str, float | None]:
    window = trace_window(result)
    flat = dict(result)
    flat['gc'] = {'minor': window['gc']['minor'], 'major': window['gc']['major'], 'ms': window['gc']['ms'],
                  'busyMs': window['mainThread']['busyMs']} if window else {}
    out = {}
    for name, keys, _ in COLUMNS:
        value = flat
        for key in keys:
            value = value.get(key) if isinstance(value, dict) else None
        if name == 'Mtris' and isinstance(value, (int, float)):
            value = value / 1e6
        out[name] = value if isinstance(value, (int, float)) else None
    return out


def median_row(results: list[dict]) -> dict[str, float | None]:
    rows = [metrics(r) for r in results]
    out = {}
    for name, _, _ in COLUMNS:
        values = [row[name] for row in rows if row[name] is not None]
        out[name] = statistics.median(values) if values else None
    return out


def label(query: str, results: list[dict]) -> str:
    if results and results[0].get('label'):
        return results[0]['label']
    return query


def table(rows: list[tuple[str, int, dict]], markdown: bool = False) -> str:
    base = rows[0][2] if rows else {}
    head = ['variant', 'n'] + [name for name, _, _ in COLUMNS] + [f'd{name}' for name in DELTAS]
    digits = {name: d for name, _, d in COLUMNS}

    def cell(name, value):
        return '-' if value is None else f'{value:.{digits[name]}f}'

    lines = []
    for name, n, row in rows:
        cells = [name, str(n)] + [cell(c, row[c]) for c, _, _ in COLUMNS]
        for c in DELTAS:
            a, b = base.get(c), row.get(c)
            cells.append('-' if a in (None, 0) or b is None or row is base else f'{100 * (b - a) / a:+.0f}%')
        lines.append(cells)
    if markdown:
        return '\n'.join(['| ' + ' | '.join(head) + ' |', '|' + '---|' * len(head)] + ['| ' + ' | '.join(r) + ' |' for r in lines])
    widths = [max(len(h), *(len(r[i]) for r in lines)) for i, h in enumerate(head)]
    fmt = lambda r: '  '.join(c.ljust(widths[i]) if i == 0 else c.rjust(widths[i]) for i, c in enumerate(r))
    return '\n'.join([fmt(head)] + [fmt(r) for r in lines])


def run_variant(query: str, out: Path, args) -> list[dict]:
    out.mkdir(parents=True, exist_ok=True)
    cmd = ['node', str(RUNNER), query, '--repeat', str(args.repeat), '--out', str(out), '--cdp', args.cdp, '--base', args.base,
           '--timeout', str(args.timeout)] + (['--no-trace'] if args.no_trace else [])
    print(f'== {query}', flush=True)
    code = subprocess.run(cmd).returncode
    results = [load(p) for p in sorted(out.glob('*.json'))]
    if code != 0:
        print(f'   run-bench exited {code}; {len(results)} result(s) kept', file=sys.stderr)
    return results


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('query', nargs='?', help="base query, e.g. 'bench=static&quality=medium'")
    ap.add_argument('--off', default='', help='comma-separated ablations: one variant each')
    ap.add_argument('--variants', nargs='*', default=[], help="extra query suffixes, e.g. 'quality=high'")
    ap.add_argument('--repeat', type=int, default=1)
    ap.add_argument('--results', nargs='*', type=Path, help='tabulate saved result JSONs or capture folders instead of running')
    ap.add_argument('--out', type=Path, default=Path.home() / 'turku3d/captures/bench-lab/matrix')
    ap.add_argument('--cdp', default='http://127.0.0.1:5223')
    ap.add_argument('--base', default='http://localhost:5220/')
    ap.add_argument('--timeout', type=int, default=900)
    ap.add_argument('--no-trace', action='store_true')
    ap.add_argument('--markdown', action='store_true')
    ap.add_argument('--json', type=Path, help='also write the rows as JSON')
    args = ap.parse_args(argv)
    rows: list[tuple[str, int, dict]] = []
    if args.results:
        for path in args.results:
            result = load(path)
            rows.append((label(str(path), [result]), 1, median_row([result])))
    else:
        if not args.query:
            ap.error('give a base query or --results')
        stamp = time.strftime('%Y%m%d-%H%M%S')
        for i, query in enumerate(variants(args.query, [f for f in args.off.split(',') if f], args.variants)):
            slug = re.sub(r'[^\w.=,-]+', '_', query)[:80]
            results = run_variant(query, args.out / stamp / f'{i:02d}-{slug}', args)
            rows.append((label(query, results), len(results), median_row(results) if results else {c: None for c, _, _ in COLUMNS}))
    print(table(rows, args.markdown))
    if args.json:
        args.json.write_text(json.dumps([{'variant': name, 'runs': n, **row} for name, n, row in rows], indent=1))
    return 0


if __name__ == '__main__':
    sys.exit(main())
