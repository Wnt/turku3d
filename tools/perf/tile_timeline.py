#!/usr/bin/env python3
"""Tile/LOD streaming timeline of a performance capture (tiles.jsonl, slices.jsonl from game/src/telemetry/streamTrace.ts).

tile_timeline.py CAPTURE_DIR [--html OUT.html] [--hitch 50] [--top 10]

Prints a text summary: slowest tiles end to end and by stage, holes/double coverage/pops, HD coverage of the view
(the share of the 3x3 window plus in-view tiles within 400 m drawing the full model, per second), hitches by cause and
the recorder's own cost. --html writes one self-contained page (inline SVG, no network): frame-time strip with
hitches attributed to the nearest streaming work, per-frame main-thread slices, band events, per-tile and
per-cell Gantt rows, coverage over time and the camera path. Times are seconds from the first frame.
"""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
import html
import json
from pathlib import Path

SLICES = ['parse', 'install', 'compile', 'upload', 'scenery', 'lod']
TILE_STAGES = ['tile.manifest', 'tile.collision', 'tile.glb', 'tile.buildings', 'tile.style', 'tile.facades',
               'tile.vegetation', 'tile.prepare']
CELL_STAGES = ['cell.manifest', 'cell.glb', 'cell.trees', 'cell.prepare']
COLOURS = {'tile.manifest': '#8da0cb', 'tile.collision': '#a6d854', 'tile.glb': '#66c2a5', 'tile.buildings': '#e5c494',
           'tile.style': '#fc8d62', 'tile.facades': '#ffd92f', 'tile.vegetation': '#b3de69', 'tile.prepare': '#e78ac3',
           'cell.manifest': '#8da0cb', 'cell.glb': '#66c2a5', 'cell.trees': '#b3de69', 'cell.prepare': '#e78ac3',
           'gfx.prepare': '#bc80bd', 'parse': '#66c2a5', 'install': '#fc8d62', 'compile': '#e78ac3', 'upload': '#8da0cb',
           'scenery': '#a6d854', 'lod': '#ffd92f'}
MARKS = {'tile.preview': '#999999', 'tile.full': '#1b9e77', 'tile.cache': '#7570b3', 'tile.restore': '#7570b3',
         'tile.drop': '#d95f02', 'tile.hold': '#e7298a', 'tile.release': '#e7298a', 'tile.fail': '#e41a1c',
         'tile.refresh': '#66a61e', 'cell.install': '#1b9e77', 'cell.error': '#e41a1c', 'band.tile': '#e6ab02',
         'band.block': '#a6761d', 'band.props': '#666666', 'band.fade': '#1f78b4', 'band.fadeEnd': '#a6cee3',
         'cut.commit': '#b15928', 'tile.new': '#cccccc'}


def rows(directory: Path, name: str):
    file = directory / f'{name}.jsonl'
    if not file.exists():
        return []
    return [json.loads(line) for line in file.read_text().splitlines() if line.strip()]


# JS sample classes, innermost-first stack frames by (function, file). Shader building is r186's node-program build.
SHADER_FUNCTIONS = {'build', 'setHashNode', 'addNode', 'getDataFromNode', 'ShaderNodeObjects', 'nodeObjects', 'generate',
                    'setupOutput', 'getOutputNode', 'analyze', 'buildCode', 'getForRender', '_createNodeBuilder', 'buildStage',
                    'setup', 'flowNode', 'getNodeType', 'getCacheKey'}
STREAMING_FILES = {'coarseTiles.ts', 'screenshotScenery.ts', 'sceneryBatches.ts', 'mergeSceneryGeometry.ts', 'buildingPlacements.ts',
                   'vegetationLod.ts', 'collisionStreaming.ts', 'loadGlb.ts', 'glbRecords.ts', 'graphicsPreparation.ts', 'compileModel.ts',
                   'textureUploads.ts', 'sceneryMaterial.ts', 'staticBatch.ts', 'shadowBatch.ts', 'lodFade.ts', 'streamTrace.ts',
                   'sceneryView.ts', 'propLod.ts', 'tiles.ts', 'materials.ts', 'facadeModules.ts', 'facadeKit.ts'}
POPULATION_FILES = {'scenePopulation.ts', 'flow.ts', 'signals.ts', 'pedestrians.ts', 'population.ts', 'network.ts', 'index.ts',
                    'traffic.ts', 'parkedVehicles.ts', 'walkers.ts'}


def sample_classes(js_rows):
    """(timestamp, class) per JS sample: shader-build, streaming, population, render or other (innermost match wins)."""
    out = []
    for row in js_rows:
        trace = row.get('trace') or {}
        frames, stacks, resources = trace.get('frames', []), trace.get('stacks', []), trace.get('resources', [])
        cache = {}
        def classify(stack_id):
            if stack_id in cache:
                return cache[stack_id]
            result, sid = 'other', stack_id
            while sid is not None:
                frame = frames[stacks[sid]['frameId']]
                name = frame.get('name') or ''
                file = resources[frame['resourceId']].split('/')[-1].split('?')[0] if frame.get('resourceId') is not None else ''
                if file.startswith('three_webgpu') and name in SHADER_FUNCTIONS: result = 'shader-build'; break
                if file in STREAMING_FILES: result = 'streaming'; break
                if file in POPULATION_FILES: result = 'population'; break
                if file.startswith('three'): result = 'render'
                sid = stacks[sid].get('parentId')
            cache[stack_id] = result
            return result
        for sample in trace.get('samples', []):
            if sample.get('stackId') is not None:
                out.append((sample['timestamp'], classify(sample['stackId'])))
    out.sort()
    return out


def load(directory: str | Path):
    directory = Path(directory)
    frames = sorted((f for batch in rows(directory, 'frames') for f in batch), key=lambda f: f[0])
    events = sorted(rows(directory, 'tiles'), key=lambda e: e['t'])
    slices = sorted(rows(directory, 'slices'), key=lambda s: s[0])
    poses = sorted(rows(directory, 'poses'), key=lambda p: p['t'])
    stats = sorted(rows(directory, 'stats'), key=lambda s: s['t'])
    meta = json.loads((directory / 'meta.json').read_text()) if (directory / 'meta.json').exists() else {}
    samples = sample_classes(rows(directory, 'js'))
    interval = (meta.get('jsProfiler') or {}).get('sampleInterval') or 10
    return dict(frames=frames, events=events, slices=slices, poses=poses, stats=stats, meta=meta, name=directory.name,
                samples=samples, sample_ms=interval if samples else None)


def stage_start(event):
    """Stage events are logged when they end; their start is t - ms."""
    return event['t'] - event.get('ms', 0)


def tiles_of(events, prefix='tile.', stages=TILE_STAGES):
    tiles = defaultdict(lambda: {'events': [], 'stages': defaultdict(float), 'spans': []})
    for event in events:
        kind, id = event['k'], event.get('id')
        if not id or not (kind.startswith(prefix) or (prefix == 'tile.' and kind in ('gfx.prepare', 'band.tile', 'band.block', 'band.props'))):
            continue
        tile = tiles[id]
        tile['events'].append(event)
        if kind in stages or kind == 'gfx.prepare':
            tile['stages'][kind] += event.get('ms', 0)
            tile['spans'].append((stage_start(event), event['t'], kind, event))
    for id, tile in tiles.items():
        first = tile['events'][0]['t']
        times = {e['k']: e['t'] for e in reversed(tile['events'])}  # first occurrence
        tile['first'] = first
        tile['preview'] = times.get('tile.preview') if prefix == 'tile.' else None
        tile['full'] = times.get('tile.full') if prefix == 'tile.' else times.get('cell.install')
        requests = [stage_start(e) for e in tile['events'] if e['k'] in ('tile.manifest', 'cell.manifest')]
        tile['request'] = min(requests) if requests else first
        tile['dmin'] = min((e['d'] for e in tile['events'] if 'd' in e), default=None)
        tile['dfull'] = next((e.get('d') for e in tile['events'] if e['k'] in ('tile.full', 'cell.install')), None)
    return tiles


def frame_intervals(frames):
    out = []
    for a, b in zip(frames, frames[1:]):
        out.append((b[0], b[0] - a[0], b))
    return out


def attribute(hitch_t, ms, events, slices_by_frame, frame_id, window=60, samples=None, sample_ms=None):
    """The cause of one long frame, from what actually ran in it: its measured streaming slices and its JS samples by
    class (shader-build, streaming, population, render, other). Time with no JS sample is native work (GPU process
    waits, synchronous pipeline or buffer calls). Events near the frame are context only, never the cause."""
    shares = {}
    row = slices_by_frame.get(frame_id)
    if row:
        for i, kind in enumerate(SLICES):
            if row[2 + i] > 0:
                shares[f'slice:{kind}'] = row[2 + i]
    if samples is not None and sample_ms:
        import bisect
        lo = bisect.bisect_left(samples, (hitch_t - ms, ''))
        hi = bisect.bisect_right(samples, (hitch_t, '~'))
        counts = Counter(c for _, c in samples[lo:hi])
        for c, n in counts.items():
            shares[f'js:{c}'] = shares.get(f'js:{c}', 0) + n * sample_ms
        sampled = sum(counts.values()) * sample_ms
        shares['native'] = max(0.0, ms - sampled - sum(v for k, v in shares.items() if k.startswith('slice:')))
    # Programs built inside this frame (game/src/render/census.ts emits gfx.build for every in-frame build).
    built = sorted({build_family(e) for e in events if e['k'] == 'gfx.build' and hitch_t - ms - 2 <= e['t'] <= hitch_t + 2})
    near = [e for e in events if hitch_t - ms - window <= e['t'] <= hitch_t + 10 and e['k'] not in ('coverage', 'tile.new', 'gfx.build')]
    context = ', '.join(sorted({e['k'] + (f" {e['id']}" if e.get('id') else '') for e in near}))[:160]
    if not shares:
        best = max(near, key=lambda e: e.get('ms', 0)) if near else None
        return ('unattributed', f'no samples; near: {context}' if best else 'no streaming work within the frame')
    cause = max(shares, key=shares.get)
    parts = ', '.join(f'{k} {v:.0f}' for k, v in sorted(shares.items(), key=lambda kv: -kv[1]) if v >= 1)
    return cause, f'{cause} ({parts} ms of {ms:.0f})' + (f'; built: {"; ".join(built)[:240]}' if built else '') \
        + (f'; near: {context}' if context else '')


def build_family(event):
    """One in-frame program build: pass, material (digits folded) and object."""
    import re
    fold = lambda value: re.sub(r'\d+', '#', str(value))
    return f"{event.get('pass', '?')} {fold(event.get('material', '?'))} [{fold(event.get('object', '?'))}]"


def coverage_incidents(events):
    holes, doubles = [], []
    for event in events:
        if event['k'] != 'coverage':
            continue
        n, i0, j0, cells = event['n'], event['i0'], event['j0'], event['cells']
        for k, c in enumerate(cells):
            id = f't0_{i0 + k % n}_{j0 + k // n}'
            if c == '.':
                holes.append((event['t'], id))
            elif c == 'D':
                doubles.append((event['t'], id))
    return holes, doubles


TILE = 250.0
VIEW_RADIUS = 400.0
HD_CODES = set('FDh')
BAND_NAMES = {'P': 'collision preview', 'C': 'scenery cell', 'c': 'scenery cell (fading)', 'U': 'coarser scenery cell',
              '.': 'nothing', '?': 'no full model (cell or nothing)'}


def _forward(cam):
    """Horizontal view direction (x, z) of a pose camera [x, y, z, qx, qy, qz, qw, fov]: three.js looks down -Z."""
    qx, qy, qz, qw = cam[3:7]
    fx, fz = -2 * (qx * qz + qw * qy), -(1 - 2 * (qx * qx + qy * qy))
    n = (fx * fx + fz * fz) ** .5
    return (fx / n, fz / n) if n > 1e-3 else None


def view_tiles(cam, half_angle, radius=VIEW_RADIUS):
    """{tile id: 'window' | 'view'}: the 3x3 window around the camera's 250 m tile, plus tiles whose square comes within
    `radius` of the camera horizontally and enters the horizontal view wedge (half_angle radians)."""
    import math
    x, z = cam[0], cam[2]
    ci, cj = math.floor(x / TILE), math.floor(z / TILE)
    out = {f't0_{ci + di}_{cj + dj}': 'window' for di in (-1, 0, 1) for dj in (-1, 0, 1)}
    forward, reach = _forward(cam), math.ceil(radius / TILE) + 1
    cos_half = math.cos(half_angle)
    for i in range(ci - reach, ci + reach + 1):
        for j in range(cj - reach, cj + reach + 1):
            id = f't0_{i}_{j}'
            if id in out:
                continue
            x0, z0, x1, z1 = i * TILE, j * TILE, (i + 1) * TILE, (j + 1) * TILE
            if math.hypot(max(x0 - x, 0, x - x1), max(z0 - z, 0, z - z1)) > radius:
                continue
            if forward is None:
                out[id] = 'view'
                continue
            # Sample the square's edges: a 250 m square can cross a narrow wedge between its corners.
            steps = [k / 4 for k in range(5)]
            points = [(x0 + (x1 - x0) * a, z0) for a in steps] + [(x0 + (x1 - x0) * a, z1) for a in steps] + \
                     [(x0, z0 + (z1 - z0) * a) for a in steps] + [(x1, z0 + (z1 - z0) * a) for a in steps]
            for px, pz in points:
                dx, dz = px - x, pz - z
                d = math.hypot(dx, dz)
                if d <= radius and (dx * forward[0] + dz * forward[1]) / max(d, 1e-6) >= cos_half:
                    out[id] = 'view'
                    break
    return out


def _reconstruct(events, first_cam):
    """Per-tile drawn band over time from lifecycle events, for captures without coverage snapshots. The window around
    the first pose starts full (drive benches start once the 3x3 is detailed). Yields (t, state dict) after each event."""
    import math
    state = {}
    if first_cam:
        ci, cj = math.floor(first_cam[0] / TILE), math.floor(first_cam[2] / TILE)
        for di in (-1, 0, 1):
            for dj in (-1, 0, 1):
                state[f't0_{ci + di}_{cj + dj}'] = 'F'
    for e in events:
        id, k = e.get('id'), e['k']
        if not id or not id.startswith('t0_'):
            continue
        current = state.get(id, '?')
        if k == 'tile.preview' and current not in HD_CODES:
            state[id] = 'P'
        elif k == 'tile.full':
            state[id] = 'C' if e.get('distant') else 'F'
        elif k == 'band.tile':
            state[id] = 'F' if e.get('to') == 'full' else 'C'
        elif k in ('tile.cache', 'tile.hold') and current in HD_CODES:
            state[id] = 'h'
        elif k == 'tile.release':
            state[id] = 'C' if e.get('covered') else '.'
        elif k == 'tile.restore':
            state[id] = 'F'
        elif k == 'tile.drop' and current != 'h':
            state[id] = '?'
        elif k == 'tile.fail' and current not in HD_CODES:
            state[id] = '?'
        elif k == 'cell.install' and current in ('?', '.'):
            state[id] = 'C'
        else:
            continue
        yield e['t'], state


def hd_coverage(capture, radius=VIEW_RADIUS):
    """Per second: the share of the 3x3 window plus the in-view tiles within `radius` that draw the full HD model ('F',
    'D' or a held 'h'), as opposed to a collision preview, scenery cell or nothing. Uses the 1 Hz coverage snapshots;
    without them, the drawn band is reconstructed from tile lifecycle events (source says which)."""
    import bisect
    import math
    poses = [p for p in capture['poses'] if p.get('cam')]
    if not poses:
        return None
    viewport = next((s['viewport'] for s in capture['stats'] if s.get('viewport')), [16, 9])
    aspect = viewport[0] / viewport[1]
    snaps = [e for e in capture['events'] if e['k'] == 'coverage']
    source = 'coverage snapshots' if snaps else 'reconstructed from tile lifecycle events'
    first = poses[0]['cam']
    ci, cj = math.floor(first[0] / TILE), math.floor(first[2] / TILE)
    seed = {f't0_{ci + di}_{cj + dj}': 'F' for di in (-1, 0, 1) for dj in (-1, 0, 1)}
    timeline = [] if snaps else [(t, dict(state)) for t, state in _reconstruct(capture['events'], first)]
    times = [t for t, _ in timeline]
    snap_times = [e['t'] for e in snaps]
    pose_times = [p['t'] for p in poses]
    seconds = []
    t = poses[0]['t']
    while t <= poses[-1]['t']:
        sample, pose = t, poses[bisect.bisect_right(pose_times, t) - 1]
        t += 1000
        if pose.get('paused'):
            continue
        cam = pose['cam']
        half = math.atan(math.tan(math.radians(cam[7] if len(cam) > 7 else 60) / 2) * aspect) + math.radians(5)
        wanted = view_tiles(cam, half, radius)
        if snaps:
            k = bisect.bisect_right(snap_times, sample) - 1
            if k < 0:
                continue
            snap = snaps[k]
            def code(id, snap=snap):
                _, i, j = id.split('_')
                di, dj = int(i) - snap['i0'], int(j) - snap['j0']
                return snap['cells'][dj * snap['n'] + di] if 0 <= di < snap['n'] and 0 <= dj < snap['n'] else '?'
        else:
            k = bisect.bisect_right(times, sample) - 1
            state = timeline[k][1] if k >= 0 else seed
            code = lambda id, state=state: state.get(id, '?')
        codes = {id: code(id) for id in wanted}
        hd = [id for id, c in codes.items() if c in HD_CODES]
        window = [id for id, role in wanted.items() if role == 'window']
        seconds.append(dict(t=sample, share=len(hd) / len(codes), tiles=len(codes),
                            window=sum(codes[id] in HD_CODES for id in window) / len(window),
                            missing=sorted((id, c, wanted[id]) for id, c in codes.items() if c not in HD_CODES)))
    return dict(source=source, radius=radius, seconds=seconds)


def in_view(capture, id, t, radius=2000.0):
    """Whether tile `id` was inside the horizontal view wedge at time t (None without poses)."""
    import bisect
    import math
    poses = [p for p in capture['poses'] if p.get('cam')]
    if not poses or not id:
        return None
    pose = poses[max(0, bisect.bisect_right([p['t'] for p in poses], t) - 1)]
    viewport = next((x['viewport'] for x in capture['stats'] if x.get('viewport')), [16, 9])
    cam = pose['cam']
    half = math.atan(math.tan(math.radians(cam[7] if len(cam) > 7 else 60) / 2) * viewport[0] / viewport[1])
    return view_tiles(cam, half, radius).get(id) == 'view' or id in view_tiles(cam, half, 0)


def hd_lines(capture, s, top=10):
    hd = hd_coverage(capture)
    if not hd or not hd['seconds']:
        return []
    secs = hd['seconds']
    mean = sum(x['share'] for x in secs) / len(secs)
    window = sum(x['window'] for x in secs) / len(secs)
    below = [x for x in secs if x['share'] < 1]
    out = [f"\nHD coverage (3x3 window + view wedge <= {hd['radius']:.0f} m; {hd['source']}): mean {100 * mean:.1f} %, "
           f"window {100 * window:.1f} %, {len(below)} of {len(secs)} s below 100 %"]
    bands = Counter(BAND_NAMES.get(c, c) + (' in window' if role == 'window' else ' in view') for x in secs for _, c, role in x['missing'])
    if bands:
        out.append('  non-HD tile-seconds: ' + ', '.join(f'{k} {n}' for k, n in bands.most_common()))
    for x in sorted(below, key=lambda x: (x['share'], x['t']))[:top]:
        missing = ', '.join(f"{id} {c}{'*' if role == 'window' else ''}" for id, c, role in x['missing'])
        out.append(f"    {s(x['t']):7.1f} s  {100 * x['share']:5.1f} % of {x['tiles']}  ({missing})")
    if below:
        out.append('    (* = in the 3x3 window; F/D/h full, P preview, C/c cell, U coarser cell, . nothing, ? no full model)')
    return out


def pops(events):
    """Full installs that replaced a collision preview or nothing with no dissolve within 300 ms: neither the square's
    cell dissolving out over the model nor the model dissolving in (band.fade with band 'full')."""
    fades = defaultdict(list)
    for e in events:
        if e['k'] == 'band.fade' and e.get('id'):
            fades[e['id']].append(e['t'])
    result = []
    for e in events:
        if e['k'] == 'tile.full' and e.get('replaced') != 'model' and not any(abs(t - e['t']) < 300 for t in fades[e.get('id')]):
            result.append(e)
    return result


def summary(capture, hitch=50.0, top=10):
    frames, events, slices = capture['frames'], capture['events'], capture['slices']
    t0 = frames[0][0] if frames else (events[0]['t'] if events else 0)
    s = lambda t: (t - t0) / 1000
    lines = [f"capture {capture['name']}: {len(frames)} frames, {len(events)} trace events, {len(slices)} frames with streaming slices"]
    rec = [x['recorder'] for x in capture['stats'] if x.get('recorder') and x['recorder'].get('msPerFrame') is not None]
    if rec:
        costs = sorted(r['msPerFrame'] for r in rec)
        lines.append(f"recorder cost: mean {sum(costs) / len(costs):.3f} ms/frame, p95 {costs[int(.95 * (len(costs) - 1))]:.3f}, "
                     f"max {costs[-1]:.3f}; dropped events {max(r.get('dropped', 0) for r in rec)}")
    tiles = tiles_of(events)
    done = [(id, t) for id, t in tiles.items() if t['full'] is not None]
    lines.append(f"\ntiles: {len(tiles)} traced, {len(done)} reached a full model")
    lines.append(f"  slowest request -> full model (top {top}):")
    for id, t in sorted(done, key=lambda kv: kv[1]['request'] - kv[1]['full'])[:top]:
        stages = ', '.join(f"{k.split('.')[1]} {v:.0f}" for k, v in sorted(t['stages'].items(), key=lambda kv: -kv[1]) if v >= 1)
        lines.append(f"    {id:<12} {(t['full'] - t['request']) / 1000:6.2f} s at {s(t['full']):7.1f} s, d {t['dfull']} m  [{stages}]")
    lines.append('  slowest by stage (ms):')
    for stage in TILE_STAGES + ['gfx.prepare']:
        ranked = sorted(((t['stages'][stage], id) for id, t in tiles.items() if t['stages'].get(stage)), reverse=True)[:3]
        if ranked:
            lines.append(f"    {stage:<16} " + ', '.join(f'{id} {ms:.0f}' for ms, id in ranked))
    cells = tiles_of(events, 'cell.', CELL_STAGES)
    installed = [(id, c) for id, c in cells.items() if c['full'] is not None]
    lines.append(f"\nscenery cells: {len(cells)} traced, {len(installed)} installed")
    for id, c in sorted(installed, key=lambda kv: kv[1]['request'] - kv[1]['full'])[:5]:
        lines.append(f"    {id:<12} {(c['full'] - c['request']) / 1000:6.2f} s  " + ', '.join(f"{k.split('.')[1]} {v:.0f}" for k, v in c['stages'].items() if v >= 1))
    holes, doubles = coverage_incidents(events)
    popped = pops(events)
    lines.append(f"\nincidents: {len(holes)} hole cell-samples ({len({h[1] for h in holes})} squares), "
                 f"{len(doubles)} double-coverage samples, {len(popped)} full installs without a dissolve")
    for t, id in holes[:top]:
        lines.append(f"    hole {id} at {s(t):.1f} s")
    swaps = defaultdict(list)
    for x in events:
        if x['k'] == 'band.swap' and x.get('id'):
            swaps[x['id']].append(x)
    for e in popped[:top]:
        seen = in_view(capture, e.get('id'), e['t'])
        where = '' if seen is None else ', in view' if seen else ', outside the view'
        # The code says why it swapped at once: a band.swap event carries its reason (e.g. twin not compiled).
        why = sorted({f"{x.get('dir')} {x.get('reason')}" for x in swaps[e.get('id')] if abs(x['t'] - e['t']) < 300})
        lines.append(f"    pop {e.get('id')} at {s(e['t']):.1f} s (replaced {e.get('replaced')}, d {e.get('d')} m{where}"
                     f"{'; swap: ' + ', '.join(why) if why else '; no dissolve or swap event'})")
    lines.extend(hd_lines(capture, s, top))
    bands = Counter(e['k'] + ('>' + str(e.get('to')) if e.get('to') else '') for e in events if e['k'].startswith('band.') or e['k'] == 'cut.commit')
    if bands:
        lines.append('band events: ' + ', '.join(f'{k} {n}' for k, n in sorted(bands.items())))
    by_frame = {row[1]: row for row in slices}
    intervals = frame_intervals(frames)
    long = [(t, ms, f) for t, ms, f in intervals if ms > hitch]
    causes = Counter()
    lines.append(f"\nhitches (> {hitch:.0f} ms): {len(long)} of {len(intervals)} frames")
    for t, ms, f in long:
        causes[attribute(t, ms, events, by_frame, f[1], samples=capture.get('samples'), sample_ms=capture.get('sample_ms'))[0]] += 1
    for cause, n in causes.most_common():
        lines.append(f"    {cause:<24} {n}")
    builds = Counter(build_family(e) for e in events if e['k'] == 'gfx.build')
    if builds:
        lines.append(f'    in-frame program builds: {sum(builds.values())}, by family:')
        for family, n in builds.most_common(12):
            lines.append(f'      {n:4d}  {family}')
    # Does any streaming event predict hitches? Its rate in the second before each hitch against before every frame.
    if long:
        import bisect
        frame_ends = [t for t, _, _ in intervals]
        kinds = sorted({e['k'] for e in events if e['k'] not in ('coverage', 'tile.new')})
        lifts = []
        for kind in kinds:
            ts = sorted(e['t'] for e in events if e['k'] == kind)
            def before(t, ms):
                i = bisect.bisect_left(ts, t - ms - 1000)
                return i < len(ts) and ts[i] <= t
            hit = sum(before(t, ms) for t, ms, _ in long) / len(long)
            base = sum(before(t, ms) for t, ms, _ in intervals) / len(intervals)
            if len(ts) >= 5 and base > 0:
                lifts.append((hit / base, kind, hit, base, len(ts)))
        lifts.sort(reverse=True)
        lines.append('    streaming events in the second before a hitch vs before any frame (lift > 1.5 suggests a cause):')
        for lift, kind, hit, base, n in lifts[:6]:
            lines.append(f'      {kind:<16} {hit * 100:5.1f}% vs {base * 100:5.1f}%  lift {lift:.1f}x  (n={n})')
    for t, ms, f in sorted(long, key=lambda x: -x[1])[:top]:
        lines.append(f"    {s(t):7.2f} s {ms:5.0f} ms  {attribute(t, ms, events, by_frame, f[1], samples=capture.get('samples'), sample_ms=capture.get('sample_ms'))[1]}")
    return '\n'.join(lines)


def svg_page(capture, hitch=50.0, width=1500):
    frames, events, slices, poses = capture['frames'], capture['events'], capture['slices'], capture['poses']
    if not frames:
        return '<p>No frames.</p>'
    t0, t1 = frames[0][0], frames[-1][0]
    span = max(1.0, t1 - t0)
    left = 150
    x = lambda t: left + (t - t0) / span * (width - left - 10)
    sec = lambda t: (t - t0) / 1000
    esc = html.escape
    parts = []

    def axis(y, height):
        out = []
        step = 1000 * max(1, round(span / 1000 / 20))
        t = t0 - (t0 % step) + step
        while t < t1:
            out.append(f'<line x1="{x(t):.1f}" x2="{x(t):.1f}" y1="{y}" y2="{y + height}" stroke="#eee"/>'
                       f'<text x="{x(t):.1f}" y="{y + height + 11}" font-size="9" text-anchor="middle" fill="#888">{sec(t):.0f}s</text>')
            t += step
        return ''.join(out)

    # Frame time strip with hitches.
    by_frame = {row[1]: row for row in slices}
    h = 110
    out = [axis(20, h), f'<text x="4" y="34" font-size="11">frame ms</text>']
    for limit in (16.7, 33.3, hitch):
        yy = 20 + h - min(limit, 100) / 100 * h
        out.append(f'<line x1="{left}" x2="{width}" y1="{yy:.1f}" y2="{yy:.1f}" stroke="#ddd" stroke-dasharray="3,3"/>'
                   f'<text x="{left - 4}" y="{yy + 3:.1f}" font-size="9" text-anchor="end" fill="#999">{limit:.0f}</text>')
    pts = ' '.join(f'{x(t):.1f},{20 + h - min(ms, 100) / 100 * h:.1f}' for t, ms, _ in frame_intervals(frames))
    out.append(f'<polyline points="{pts}" fill="none" stroke="#4477aa" stroke-width="0.8"/>')
    for t, ms, f in frame_intervals(frames):
        if ms > hitch:
            label = attribute(t, ms, events, by_frame, f[1], samples=capture.get('samples'), sample_ms=capture.get('sample_ms'))[1]
            out.append(f'<circle cx="{x(t):.1f}" cy="{20 + h - min(ms, 100) / 100 * h:.1f}" r="3" fill="#e41a1c">'
                       f'<title>{sec(t):.2f}s {ms:.0f} ms: {esc(label)}</title></circle>')
    parts.append(('Frame time (hitches red, hover for the attributed cause)', h + 40, ''.join(out)))

    # Main-thread streaming slices, stacked per frame.
    h = 70
    out = [axis(20, h), f'<text x="4" y="34" font-size="11">streaming ms</text>']
    for row in slices:
        base = 20 + h
        for i, kind in enumerate(SLICES):
            v = row[2 + i]
            if v <= 0:
                continue
            hh = min(v, 30) / 30 * h
            base -= hh
            out.append(f'<rect x="{x(row[0]):.1f}" y="{base:.1f}" width="1.5" height="{hh:.1f}" fill="{COLOURS[kind]}">'
                       f'<title>{sec(row[0]):.2f}s {kind} {v:.1f} ms</title></rect>')
    legend = ' '.join(f'<tspan fill="{COLOURS[k]}">■</tspan> {k}' for k in SLICES)
    out.append(f'<text x="{left}" y="14" font-size="10">{legend} (0-30 ms)</text>')
    parts.append(('Main-thread streaming slices per frame', h + 40, ''.join(out)))

    # Band events lane.
    lanes = ['band.tile', 'band.block', 'band.fade', 'cut.commit', 'band.props', 'tile.hold', 'tile.release']
    h = 16 * len(lanes)
    out = [axis(10, h)]
    for k, lane in enumerate(lanes):
        y = 10 + k * 16
        out.append(f'<text x="4" y="{y + 11}" font-size="10">{lane}</text>')
        for e in events:
            if e['k'] == lane or (lane == 'band.fade' and e['k'] == 'band.fadeEnd'):
                tip = esc(f"{sec(e['t']):.2f}s {e['k']} {e.get('id', '')} " + json.dumps({k: v for k, v in e.items() if k not in ('t', 'k', 'id')}))
                out.append(f'<rect x="{x(e["t"]):.1f}" y="{y + 2}" width="2" height="11" fill="{MARKS.get(e["k"], "#444")}"><title>{tip}</title></rect>')
    parts.append(('Band switches and fades', h + 30, ''.join(out)))

    def gantt(title, items, stages):
        items = sorted(items.items(), key=lambda kv: kv[1]['first'])[:400]
        hh = 12 * len(items) + 10
        out = [axis(0, hh)]
        for k, (id, item) in enumerate(items):
            y = k * 12
            label = f"{id} {item['dmin'] if item['dmin'] is not None else ''}m"
            out.append(f'<text x="4" y="{y + 9}" font-size="9">{esc(label)}</text>')
            for start, end, kind, event in item['spans']:
                tip = esc(f"{kind} {event.get('ms', 0):.0f} ms at {sec(end):.2f}s " + json.dumps({k: v for k, v in event.items() if k not in ('t', 'k', 'id', 'ms')}))
                out.append(f'<rect x="{x(start):.1f}" y="{y + 1}" width="{max(1.0, x(end) - x(start)):.1f}" height="9" '
                           f'fill="{COLOURS.get(kind, "#999")}"><title>{tip}</title></rect>')
            for event in item['events']:
                if event['k'] in MARKS and event['k'] not in stages:
                    tip = esc(f"{event['k']} at {sec(event['t']):.2f}s " + json.dumps({k: v for k, v in event.items() if k not in ('t', 'k', 'id')}))
                    out.append(f'<rect x="{x(event["t"]) - 1:.1f}" y="{y}" width="2.5" height="11" fill="{MARKS[event["k"]]}"><title>{tip}</title></rect>')
        legend = ' '.join(f'<tspan fill="{COLOURS[k]}">■</tspan> {k.split(".")[1]}' for k in stages)
        return (f'{title} ({len(items)} rows; stages: {legend})', hh + 20, ''.join(out))

    parts.append(gantt('Full tiles', tiles_of(events), TILE_STAGES + ['gfx.prepare']))
    parts.append(gantt('Scenery cells', tiles_of(events, 'cell.', CELL_STAGES), CELL_STAGES))

    # Coverage over time: holes and double coverage per snapshot.
    snaps = [e for e in events if e['k'] == 'coverage']
    h = 60
    out = [axis(10, h), '<text x="4" y="24" font-size="10">holes</text><text x="4" y="40" font-size="10" fill="#e7298a">double</text>']
    for e in snaps:
        holes, doubles = e['cells'].count('.'), e['cells'].count('D')
        counts = Counter(e['cells'])
        tip = esc(f"{sec(e['t']):.1f}s " + ', '.join(f'{c}:{n}' for c, n in sorted(counts.items())) + '\n' + '\n'.join(
            e['cells'][r * e['n']:(r + 1) * e['n']] for r in reversed(range(e['n']))))
        out.append(f'<rect x="{x(e["t"]):.1f}" y="{10 + h - holes / max(1, e["n"] ** 2) * h:.1f}" width="3" '
                   f'height="{holes / max(1, e["n"] ** 2) * h:.1f}" fill="#444"><title>{tip}</title></rect>')
        if doubles:
            out.append(f'<rect x="{x(e["t"]) + 3:.1f}" y="{10 + h - doubles / e["n"] ** 2 * h:.1f}" width="3" height="{doubles / e["n"] ** 2 * h:.1f}" fill="#e7298a"/>')
    parts.append(('Coverage of the 9x9 squares around the camera (F full, P preview, C cell, c fading, U coarser node, h held, D double, . hole)', h + 30, ''.join(out)))

    # Camera path with hitches.
    cam = [(p['cam'][0], p['cam'][2], p['t'], p.get('H')) for p in poses if p.get('cam')]
    path_svg = ''
    if cam:
        xs, zs = [c[0] for c in cam], [c[1] for c in cam]
        lo_x, hi_x, lo_z, hi_z = min(xs), max(xs), min(zs), max(zs)
        size = max(hi_x - lo_x, hi_z - lo_z, 1)
        px = lambda v: 20 + (v - lo_x) / size * 460
        pz = lambda v: 20 + (v - lo_z) / size * 460
        pts = ' '.join(f'{px(a):.1f},{pz(b):.1f}' for a, b, _, _ in cam)
        out = [f'<polyline points="{pts}" fill="none" stroke="#4477aa" stroke-width="1.2"/>']
        times = [c[2] for c in cam]
        import bisect
        for t, ms, f in frame_intervals(frames):
            if ms > hitch:
                i = min(len(cam) - 1, bisect.bisect_left(times, t))
                out.append(f'<circle cx="{px(cam[i][0]):.1f}" cy="{pz(cam[i][1]):.1f}" r="3" fill="#e41a1c"><title>{sec(t):.2f}s {ms:.0f} ms</title></circle>')
        out.append(f'<text x="20" y="495" font-size="10">engine x right, z down; {size:.0f} m across; start '
                   f'{cam[0][0]:.0f},{cam[0][1]:.0f}; heights {min(c[3] or 0 for c in cam):.0f}-{max(c[3] or 0 for c in cam):.0f} m</text>')
        path_svg = f'<svg width="500" height="505" xmlns="http://www.w3.org/2000/svg">{"".join(out)}</svg>'

    body = []
    for title, height, content in parts:
        body.append(f'<h3>{esc(title) if "<tspan" not in title else title}</h3>'
                    f'<svg width="{width}" height="{height}" xmlns="http://www.w3.org/2000/svg" font-family="sans-serif">{content}</svg>')
    text = esc(summary(capture, hitch))
    return (f'<!doctype html><html><head><meta charset="utf-8"><title>Tile timeline {esc(capture["name"])}</title>'
            '<style>body{font:13px sans-serif;margin:16px;color:#222;background:#fff}h3{margin:18px 0 4px;font-size:13px}'
            'pre{background:#f6f6f6;padding:10px;overflow-x:auto}svg{display:block}</style></head><body>'
            f'<h2>Tile / LOD timeline: {esc(capture["name"])}</h2><pre>{text}</pre>' + ''.join(body)
            + '<h3>Camera path (hitches red)</h3>' + path_svg + '</body></html>')


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('capture')
    parser.add_argument('--html')
    parser.add_argument('--hitch', type=float, default=50.0, help='frame interval (ms) counted as a hitch')
    parser.add_argument('--top', type=int, default=10)
    args = parser.parse_args()
    capture = load(args.capture)
    print(summary(capture, args.hitch, args.top))
    if args.html:
        Path(args.html).write_text(svg_page(capture, args.hitch))
        print(f'\nwrote {args.html}')


if __name__ == '__main__':
    main()
