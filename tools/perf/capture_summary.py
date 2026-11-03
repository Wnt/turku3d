#!/usr/bin/env python3
"""Summarise in-game/coordinator captures; segments are numbered from 1.

capture_summary.py DIR [--segment N]
capture_summary.py --compare ORIGINAL REPLAY [--segment N]
Replay summaries automatically compare the sibling capture named in meta.replayOf.
"""
from __future__ import annotations
import argparse
import bisect
from collections import defaultdict
import json
import math
from pathlib import Path


def rows(directory, name):
    file = Path(directory) / f'{name}.jsonl'
    if not file.exists():
        return []
    return [json.loads(line) for line in file.read_text().splitlines() if line.strip()]


def segments(poses, marks):
    poses = sorted(poses, key=lambda p: p['t'])
    result = []
    edges = sorted((m for m in marks if m.get('k') in ('pause', 'resume')), key=lambda m: m['t'])
    if edges:
        start = poses[0]['t'] if poses and not poses[0]['paused'] else None
        for edge in edges:
            if edge['k'] == 'resume' and start is None:
                start = edge['t']
            elif edge['k'] == 'pause' and start is not None:
                selected = [p for p in poses if start <= p['t'] <= edge['t'] and not p['paused']]
                if len(selected) > 1:
                    result.append((selected[0]['t'], selected[-1]['t']))
                start = None
        if start is not None:
            selected = [p for p in poses if p['t'] >= start and not p['paused']]
            if len(selected) > 1:
                result.append((selected[0]['t'], selected[-1]['t']))
    else:
        selected = []
        for p in [*poses, {'paused': True}]:
            if p['paused']:
                if len(selected) > 1:
                    result.append((selected[0]['t'], selected[-1]['t']))
                selected = []
            else:
                selected.append(p)
    return result


def percentile(values, q):
    values = sorted(v for v in values if v is not None and math.isfinite(v))
    if not values:
        return None
    index = (len(values) - 1) * q
    lo = int(index)
    return values[lo] + (values[min(lo + 1, len(values) - 1)] - values[lo]) * (index - lo)


def js_functions(traces, start, end):
    own, inclusive = defaultdict(float), defaultdict(float)
    total_samples = 0
    for row in traces:
        trace = row.get('trace', row)
        frames, stacks, resources = trace.get('frames', []), trace.get('stacks', []), trace.get('resources', [])
        samples = trace.get('samples', [])
        for i, sample in enumerate(samples):
            timestamp = sample['timestamp']
            if not start <= timestamp <= end or sample.get('stackId') is None:
                continue
            duration = min(100, max(0, samples[i + 1]['timestamp'] - timestamp)) if i + 1 < len(samples) else 10
            stack, seen, names = sample['stackId'], set(), []
            while stack is not None and stack not in seen and 0 <= stack < len(stacks):
                seen.add(stack)
                entry = stacks[stack]
                frame = frames[entry['frameId']]
                resource = frame.get('resourceId')
                url = resources[resource] if resource is not None and resource < len(resources) else '(browser/native)'
                name = f"{frame.get('name') or '(anonymous)'} @ {url}:{frame.get('line', '?')}:{frame.get('column', '?')}"
                names.append(name)
                stack = entry.get('parentId')
            if names:
                own[names[0]] += duration
                for name in set(names):
                    inclusive[name] += duration
                total_samples += 1
    return own, inclusive, total_samples


def gpu_pass_summary(stats, start, end):
    values, seen = defaultdict(list), set()
    for row in sorted(stats, key=lambda row: row['t']):
        if not start <= row['t'] <= end:
            continue
        frame, passes = row.get('gpuPassFrame'), row.get('gpuPassMs')
        if not isinstance(frame, int) or isinstance(frame, bool) or frame < 0 or frame in seen or not isinstance(passes, dict):
            continue
        valid = {name: value for name, value in passes.items() if isinstance(value, (int, float))
                 and not isinstance(value, bool) and math.isfinite(value) and value >= 0}
        if not valid:
            continue
        seen.add(frame)
        for name, value in valid.items():
            values[name].append(value)
    return {name: dict(samples=len(samples), p50=percentile(samples, .5), p95=percentile(samples, .95))
            for name, samples in values.items()}, len(seen)


def render_settings(meta, stats, start, end):
    stats = sorted(stats, key=lambda row: row['t'])
    preceding = [row for row in stats if row['t'] <= start]
    anchor = preceding[-1] if preceding else stats[0] if stats else {}
    settings = {key: meta.get(key, meta.get('params', {}).get(key)) for key in ['quality', 'backend', 'commit']}
    for key in ['pixelRatio', 'viewport']:
        settings[key] = anchor.get(key, meta.get(key))
    samples = [anchor, *[row for row in stats if start < row['t'] <= end]]
    resolutions = {json.dumps([row.get('pixelRatio', meta.get('pixelRatio')), row.get('viewport', meta.get('viewport'))]) for row in samples}
    return settings, len(resolutions) > 1


def analyse(directory, segment=None):
    directory = Path(directory)
    meta = json.loads((directory / 'meta.json').read_text())
    poses = sorted(rows(directory, 'poses'), key=lambda p: p['t'])
    batches = rows(directory, 'frames')
    frames = sorted([frame for batch in batches for frame in (batch if batch and isinstance(batch[0], list) else [batch])], key=lambda f: f[0])
    if not frames:
        raise ValueError(f'{directory}: no frames')
    spans = segments(poses, rows(directory, 'marks'))
    start, end = frames[0][0], frames[-1][0]
    if segment is not None:
        if not 1 <= segment <= len(spans):
            raise ValueError(f'{directory}: segment {segment} missing; {len(spans)} available')
        start, end = spans[segment - 1]
    stats = rows(directory, 'stats')
    settings, resolution_changed = render_settings(meta, stats, start, end)
    gpu_passes, gpu_pass_frames = gpu_pass_summary(stats, start, end)
    frames = [f for f in frames if start <= f[0] <= end]
    intervals = [(b[0], b[0] - a[0]) for a, b in zip(frames, frames[1:]) if b[0] > a[0]]
    gpu, seen = [], set()
    for frame in frames:
        if frame[4] is not None and frame[4] >= 0 and frame[4] not in seen and frame[3] is not None and frame[3] > 0:
            gpu.append(frame[3]); seen.add(frame[4])
    metrics = {'fps': len(intervals) * 1000 / sum(dt for _, dt in intervals) if intervals else None}
    for label, values, quantiles in [('interval', [dt for _, dt in intervals], [50, 95, 99]), ('cpu', [f[2] for f in frames], [50, 95]),
                                      ('gpu', gpu, [50, 95]), ('draws', [f[5] for f in frames], [50, 95]), ('tris', [f[6] for f in frames], [50, 95])]:
        for q in quantiles:
            metrics[f'{label}_p{q}'] = percentile(values, q / 100)
    # Sliding windows start at each rendered frame and cover exactly one second.
    times = [f[0] for f in frames]
    candidates = []
    for i, t in enumerate(times):
        if t + 1000 > end:
            break
        j = bisect.bisect_left(times, t + 1000, i)
        dts = [times[k + 1] - times[k] for k in range(i, min(j, len(times) - 1))]
        candidates.append({'t': t, 'fps': max(0, j - i), 'max_ms': max(dts, default=0), 'p95_ms': percentile(dts, .95)})
    worst = []
    pose_times = [p['t'] for p in poses]
    for window in sorted(candidates, key=lambda w: (w['fps'], -w['max_ms'])):
        if any(abs(window['t'] - w['t']) < 1000 for w in worst):
            continue
        if poses:
            index = min(len(poses) - 1, bisect.bisect_left(pose_times, window['t']))
            if index and abs(pose_times[index - 1] - window['t']) < abs(pose_times[index] - window['t']):
                index -= 1
            p = poses[index]
            window['position'] = [p['E'], p['N'], p['H']]
        worst.append(window)
        if len(worst) == 5:
            break
    loaf = [r for r in rows(directory, 'loaf') if r['t'] < end and r['t'] + r['d'] > start]
    scripts = defaultdict(lambda: [0, 0, 0])
    for entry in loaf:
        for script in entry.get('scripts', []):
            key = f"{script.get('fn') or '(anonymous)'} @ {script.get('url')}:{script.get('pos')}"
            scripts[key][0] += script.get('d', 0)
            scripts[key][1] += 1
            scripts[key][2] += script.get('forced', 0)
    network = [r for r in rows(directory, 'network') if start <= r['t'] <= end]
    own, inclusive, sample_count = js_functions(rows(directory, 'js'), start, end)
    return dict(directory=str(directory), meta=meta, settings=settings, resolution_changed=resolution_changed, start=start, end=end, segments=spans, frames=len(frames), gpu_samples=len(gpu), gpu_passes=gpu_passes, gpu_pass_frames=gpu_pass_frames, metrics=metrics,
                worst=worst, loaf=loaf, scripts=scripts, network=network, own=own, inclusive=inclusive, sample_count=sample_count)


def number(value):
    return 'n/a' if value is None else f'{value:,.2f}'


def show(result):
    m = result['metrics']
    print(f"{result['directory']} | {result['frames']} frames / {(result['end'] - result['start']) / 1000:.2f} s | {len(result['segments'])} unpaused segments")
    print(f"Render at start: quality={result['settings']['quality']} viewport={result['settings']['viewport']} pixelRatio={result['settings']['pixelRatio']}; resolution changed={result['resolution_changed']}")
    print(f"FPS {number(m['fps'])}; interval ms p50/p95/p99 {number(m['interval_p50'])} / {number(m['interval_p95'])} / {number(m['interval_p99'])}")
    print(f"CPU ms p50/p95 {number(m['cpu_p50'])} / {number(m['cpu_p95'])}; GPU ms p50/p95 {number(m['gpu_p50'])} / {number(m['gpu_p95'])} ({result['gpu_samples']} unique readbacks)")
    print(f"Draws p50/p95 {number(m['draws_p50'])} / {number(m['draws_p95'])}; tris p50/p95 {number(m['tris_p50'])} / {number(m['tris_p95'])}")
    if result['gpu_pass_frames']:
        print(f"GPU render passes (1 Hz stats, {result['gpu_pass_frames']} unique owning frames; sampled p50/p95 ms):")
        for name, timing in sorted(result['gpu_passes'].items(), key=lambda item: -item[1]['p95']):
            print(f"  {name:<14} {timing['samples']:5} samples  {number(timing['p50']):>9} / {number(timing['p95']):>9}")
    else:
        print('GPU render passes: unavailable (no complete frame-owned pass results in stats)')

    print('Worst 1-second windows (non-overlapping, ranked by FPS):')
    for w in result['worst']:
        print(f"  t={w['t']:.1f} fps={w['fps']} max={w['max_ms']:.1f} ms p95={number(w['p95_ms'])} position={w.get('position')}")
    loaf = result['loaf']
    print(f"Long animation frames: {len(loaf)}, >100 ms: {sum(e['d'] > 100 for e in loaf)}; script total / calls / forced-layout ms:")
    for name, (duration, count, forced) in sorted(result['scripts'].items(), key=lambda item: -item[1][0])[:12]:
        print(f'  {duration:9.1f} / {count:5} / {forced:7.1f}  {name}')
    network = result['network']
    print(f"Resource responses: {len(network)}, transferred {sum(r.get('transfer', 0) for r in network):,} bytes; slowest URLs by total duration:")
    urls = defaultdict(lambda: [0, 0, 0])
    for row in network:
        url = row['url'].split('?')[0]
        urls[url][0] += 1; urls[url][1] += row.get('transfer', 0); urls[url][2] += row['d']
    for url, (count, transferred, duration) in sorted(urls.items(), key=lambda item: -item[1][2])[:10]:
        print(f'  {count:4} responses {transferred:10,} bytes {duration:9.1f} ms  {url}')
    print(f"JS Self-Profiling: {result['sample_count']} attributed samples (sampled time estimates)")
    for label, values in [('self', result['own']), ('inclusive', result['inclusive'])]:
        print(f'  Top {label} JS functions (ms):')
        for name, duration in sorted(values.items(), key=lambda item: -item[1])[:10]:
            print(f'    {duration:9.1f}  {name}')
    if not result['sample_count']:
        print('  No self-profiler traces for this interval; check meta.jsProfiler for support/errors.')


def compare(a, b):
    print(f"\nComparison: {Path(a['directory']).name} -> {Path(b['directory']).name}")
    print(f"{'metric':<19} {'original':>14} {'replay':>14} {'change':>12}")
    for key, value in a['metrics'].items():
        other = b['metrics'][key]
        change = f'{(other / value - 1) * 100:+.1f}%' if value and other is not None else 'n/a'
        print(f'{key:<19} {number(value):>14} {number(other):>14} {change:>12}')
    print(f"{'LoAF >100ms':<19} {sum(e['d'] > 100 for e in a['loaf']):>14} {sum(e['d'] > 100 for e in b['loaf']):>14}")
    print(f"{'resource responses':<19} {len(a['network']):>14} {len(b['network']):>14}")
    names = set(a['gpu_passes']) | set(b['gpu_passes'])
    if names:
        print('GPU render pass p50/p95 ms (1 Hz sampled stats, not every rendered frame):')
        for name in sorted(names):
            left, right = a['gpu_passes'].get(name, {}), b['gpu_passes'].get(name, {})
            print(f"  {name:<14} {number(left.get('p50')):>9}/{number(left.get('p95')):<9} -> {number(right.get('p50')):>9}/{number(right.get('p95')):<9}")

    for key in ['quality', 'pixelRatio', 'viewport', 'backend', 'commit']:
        print(f"  {key}: {a['settings'][key]} -> {b['settings'][key]}")
    changed = [key for key in ['quality', 'pixelRatio', 'viewport', 'backend'] if a['settings'][key] != b['settings'][key]]
    if changed:
        print(f"  Different render settings: {', '.join(changed)}")
    if a['resolution_changed'] or b['resolution_changed']:
        print('  Resolution changed within a measured window; replay holds its starting resolution fixed.')
    print('  Replay uses the QA render path; live driving also includes physics, input and HUD work. Compare replay against replay to isolate build changes.')
    if b['meta'].get('populationFallback'):
        print('  Original has no replay snapshot; ambient population uses the documented fallback seed, not the original actor state.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', nargs='?')
    parser.add_argument('--segment', type=int)
    parser.add_argument('--compare', nargs=2, metavar=('A', 'B'))
    args = parser.parse_args()
    try:
        if args.compare:
            a_dir, b_dir = map(Path, args.compare)
            b_meta = json.loads((b_dir / 'meta.json').read_text())
            source_segment = args.segment or b_meta.get('segment')
            a = analyse(a_dir, source_segment)
            # A replay directory contains only the replayed interval.
            b = analyse(b_dir, None if b_meta.get('replayOf') else args.segment)
            show(a); show(b); compare(a, b)
        elif args.directory:
            result = analyse(args.directory, args.segment); show(result)
            original = Path(args.directory).parent / result['meta'].get('replayOf', '')
            if result['meta'].get('replayOf') and (original / 'meta.json').exists():
                compare(analyse(original, result['meta'].get('segment', 1)), result)
        else:
            parser.error('provide a capture directory or --compare A B')
    except (OSError, ValueError, KeyError) as error:
        parser.exit(1, f'{error}\n')


if __name__ == '__main__':
    main()
