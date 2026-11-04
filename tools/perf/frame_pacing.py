#!/usr/bin/env python3
"""Frame pacing of captures against the release goal (docs/handover/frame-goal.md).

  python3 tools/perf/frame_pacing.py ~/turku3d/captures/<id> [...] [--tier medium|reduced]

Prints per capture: FPS, median, p99 (and its ratio to the median), p99.9, frames over 33.4/50/100 ms, max, CPU and
GPU p50/p99, and PASS/FAIL against the tier's targets. The tier defaults to the capture's meta.tier."""
import argparse, json, os, sys

GOAL = {  # tier: mean FPS floor, p99 cap (ms), p99/median cap, share over 33.4 ms (%), frames over 50 ms
    'medium': dict(fps=50, p99=33.4, ratio=1.5, over33=0.2, over50=0),
    'reduced': dict(fps=40, p99=40.0, ratio=1.5, over33=1.0, over50=0),
}
GOAL['high'] = GOAL['medium']
GOAL['low'] = GOAL['mobile'] = GOAL['reduced']


def frames(directory):
    rows = []
    for line in open(os.path.join(directory, 'frames.jsonl')):
        rows.extend(json.loads(line))
    rows.sort(key=lambda r: r[0])
    return rows


def pct(values, q):
    return values[min(len(values) - 1, int(len(values) * q))] if values else float('nan')


def tier_of(directory):
    try:
        tier = json.load(open(os.path.join(directory, 'meta.json'))).get('tier')
    except (OSError, ValueError):
        return None
    return tier.get('tier') if isinstance(tier, dict) else tier


def summary(directory):
    rows = frames(directory)
    intervals = [b[0] - a[0] for a, b in zip(rows, rows[1:]) if b[0] > a[0]]
    ordered = sorted(intervals)
    cpu = sorted(r[2] for r in rows if r[2] is not None)
    gpu = sorted(r[3] for r in rows if r[3])
    over = lambda ms: sum(1 for v in intervals if v > ms)
    median, p99 = pct(ordered, .5), pct(ordered, .99)
    return dict(n=len(intervals), fps=len(intervals) * 1000 / sum(intervals), median=median, p99=p99, ratio=p99 / median,
                p999=pct(ordered, .999), over33=over(33.4), share33=100 * over(33.4) / len(intervals), over50=over(50),
                over100=over(100), max=max(intervals), cpu50=pct(cpu, .5), cpu99=pct(cpu, .99), gpu50=pct(gpu, .5),
                gpu99=pct(gpu, .99))


def verdict(s, tier):
    goal = GOAL.get(tier or '')
    if not goal:
        return 'no goal for tier'
    misses = [name for name, bad in (('fps', s['fps'] < goal['fps']), ('p99', s['p99'] > goal['p99']),
                                     ('p99/median', s['ratio'] > goal['ratio']), ('>33.4ms', s['share33'] > goal['over33']),
                                     ('>50ms', s['over50'] > goal['over50'])) if bad]
    return 'PASS' if not misses else 'FAIL ' + ','.join(misses)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('captures', nargs='+')
    parser.add_argument('--tier')
    args = parser.parse_args()
    for directory in args.captures:
        s, tier = summary(directory), args.tier or tier_of(directory)
        print(f"{os.path.basename(directory.rstrip('/'))} [{tier}]: fps {s['fps']:.1f} med {s['median']:.1f} p99 {s['p99']:.1f} "
              f"({s['ratio']:.2f}x) p99.9 {s['p999']:.1f} >33 {s['over33']} ({s['share33']:.2f}%) >50 {s['over50']} "
              f">100 {s['over100']} max {s['max']:.0f} | cpu {s['cpu50']:.1f}/{s['cpu99']:.1f} gpu {s['gpu50']:.1f}/{s['gpu99']:.1f} "
              f"| {verdict(s, tier)}")


if __name__ == '__main__':
    sys.exit(main())
