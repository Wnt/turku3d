"""Fast GPU-free audit of actual tile triangles. Never requests or rebuilds tiles.

python -m turku3d.geometry_qa tile/vis.glb --out reports/qa
python -m turku3d.geometry_qa --sweep CACHE --out reports/city

Thresholds are engineering heuristics, not proof of road drivability. Cross-slope,
bridge approach/clearance and enclosed holes require semantic attribution; this
first version reports slope magnitude and boundary suspects without inventing it.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import subprocess
import time

import numpy as np


@dataclass
class Mesh:
    name: str
    vertices: np.ndarray
    indices: np.ndarray
    metadata: dict

    @property
    def label(self):
        return ' '.join(str(self.metadata.get(k, '')) for k in
                        ('name', 'layer', 'material_key', 'material', 'surface', 'object_id')).lower()


def load_glb(path):
    helper = Path(__file__).parent / 'package/geometry_decode.mjs'
    result = subprocess.run(['node', str(helper), str(path)], capture_output=True, check=True)
    meshes = []
    for i, d in enumerate(json.loads(result.stdout)):
        vertices = np.asarray(d['v'], dtype=float).reshape(-1, 3)
        indices = np.asarray(d['ix'], dtype=int)
        meta = d['metadata']
        # Packaged material batches retain original object index ranges.
        # Inspect each range so adjacent coplanar source objects remain detectable.
        for part in meta.get('objects') or [dict(start=0, count=len(indices))]:
            info = {**meta, **part}
            name = str(info.get('object_id') or d.get('id') or info.get('name') or i)
            ix = indices[part['start']:part['start']+part['count']].reshape(-1, 3)
            meshes.append(Mesh(name, vertices, ix, info))
    return meshes


def clipped_overlap(a, b):
    """Convex triangle intersection area; edge contact alone is not overlap."""
    def cross(u, v):
        return u[0] * v[1] - u[1] * v[0]
    sign = 1 if cross(b[1] - b[0], b[2] - b[0]) >= 0 else -1
    poly = list(a)
    for i in range(3):
        if not poly:
            return 0.0
        q, r = b[i], b[(i + 1) % 3]
        old, poly = poly, []
        p = old[-1]
        dp = sign * cross(r - q, p - q)
        for s in old:
            ds = sign * cross(r - q, s - q)
            if (ds >= 0) != (dp >= 0):
                poly.append(p + (s - p) * dp / (dp - ds))
            if ds >= 0:
                poly.append(s)
            p, dp = s, ds
    if len(poly) < 3:
        return 0.0
    p = np.asarray(poly)
    return abs(float(np.sum(p[:, 0] * np.roll(p[:, 1], -1) - p[:, 1] * np.roll(p[:, 0], -1)))) * .5


def audit(meshes, *, epsilon=.003, overlap_area=.01):
    findings = []
    triangles, owners = [], []
    def emit(kind, severity, point, mesh, **detail):
        p = np.asarray(point)
        findings.append(dict(kind=kind, severity=severity, mesh=mesh,
                             position=p.tolist(), world_enh=[23459500 + float(p[0]),
                             6704500 - float(p[2]), float(p[1])], **detail))
    road_count = 0
    for mi, mesh in enumerate(meshes):
        tri = mesh.vertices[mesh.indices]
        finite = np.isfinite(tri).all(axis=(1, 2))
        for j in np.flatnonzero(~finite):
            emit('nonfinite', 'high', np.nan_to_num(tri[j], nan=0, posinf=0, neginf=0).mean(0), mesh.name, triangle=int(j))
        tri = tri[finite]
        if not len(tri):
            continue
        triangles.append(tri); owners.extend([mi] * len(tri))
        normal = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
        area2 = np.linalg.norm(normal, axis=1)
        edges = np.linalg.norm(tri - np.roll(tri, 1, axis=1), axis=2)
        road = bool(re.search(r'road|asphalt|cobblestone|bridge_deck', mesh.label))
        for j in np.flatnonzero((np.abs(tri[:, :, 1]) > 500).any(1)):
            emit('height_spike', 'high', tri[j].mean(0), mesh.name, triangle=int(j), height_range=tri[j, :, 1].tolist())
        if not road:
            continue
        road_count += len(tri)
        horizontal = np.abs(normal[:, 1]) / np.maximum(area2, 1e-20)
        slope = np.linalg.norm(normal[:, [0, 2]], axis=1) / np.maximum(np.abs(normal[:, 1]), 1e-20)
        # Steep curb/rim faces are excluded; a road surface faces roughly upward.
        surface = horizontal > .65
        for kind, severity, mask in [
            ('road_degenerate', 'high', area2 < 1e-8),
            ('road_grade', 'high', surface & (slope > .25)),
            ('road_flipped', 'high', surface & (normal[:, 1] < 0)),
            ('road_sliver', 'warning', surface & (area2 / np.maximum(edges.max(1)**2, 1e-20) < .005) & (edges.max(1) > 2)),
        ]:
            for j in np.flatnonzero(mask):
                emit(kind, severity, tri[j].mean(0), mesh.name, triangle=int(j), slope=float(min(slope[j], 1e6)))
    if not triangles:
        return dict(counts=dict(Counter(f['kind'] for f in findings)), findings=findings, triangles=0, road_triangles=0)
    t = np.concatenate(triangles); owner = np.asarray(owners)
    n = np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0])
    lengths = np.linalg.norm(n, axis=1)
    n /= np.maximum(lengths[:, None], 1e-20)
    dominant = np.argmax(np.abs(n), axis=1)
    # A projected spatial hash bounds work by local density, including vertical facades.
    buckets = defaultdict(list)
    candidates = 0
    for i in range(len(t)):
        if lengths[i] < 1e-8:
            continue
        axis = int(dominant[i]); axes = [k for k in range(3) if k != axis]
        p = t[i][:, axes]
        lo = np.floor(p.min(0) / 4).astype(int); hi = np.floor(p.max(0) / 4).astype(int)
        # Pathological extents are already a defect, not a reason to allocate millions of cells.
        if np.prod(hi - lo + 1) > 16384:
            emit('oversized_triangle', 'high', t[i].mean(0), meshes[owner[i]].name, triangle=i)
            continue
        keys = [(axis, x, z) for x in range(lo[0], hi[0]+1) for z in range(lo[1], hi[1]+1)]
        prior = set(j for key in keys for j in buckets[key])
        if prior:
            js = np.fromiter(prior, dtype=int)
            js = js[(owner[js] != owner[i]) & (np.abs(n[js] @ n[i]) > .99999)]
            if len(js):
                distance = np.max(np.abs((t[js] - t[i, 0]) @ n[i]), axis=1)
                js = js[distance <= epsilon]
                for j in js:
                    candidates += 1
                    other = t[j][:, axes]
                    if (p.max(0) <= other.min(0)).any() or (other.max(0) <= p.min(0)).any():
                        continue
                    area = clipped_overlap(p, other)
                    if area > overlap_area:
                        emit('coplanar_overlap', 'high', (t[i].mean(0) + t[j].mean(0)) / 2,
                             meshes[owner[i]].name, other_mesh=meshes[owner[j]].name,
                             triangles=[i, int(j)], projected_area_m2=area,
                             depth_delta_m=float(np.max(np.abs((t[j] - t[i, 0]) @ n[i]))))
        for key in keys:
            buckets[key].append(i)
    return dict(counts=dict(sorted(Counter(f['kind'] for f in findings).items())),
                severity=dict(Counter(f['severity'] for f in findings)), findings=findings,
                triangles=len(t), road_triangles=road_count, overlap_candidates=candidates)


def heatmap(meshes, report, path):
    from PIL import Image, ImageDraw
    points = np.concatenate([m.vertices[:, [0, 2]] for m in meshes if len(m.vertices)])
    lo, hi = points.min(0), points.max(0)
    span = max(float((hi-lo).max()), 1)
    def xy(p):
        return tuple(((np.asarray(p)[[0, 2]] - lo) / span * 940 + 30).astype(int))
    im = Image.new('RGB', (1000, 1050), '#151d25'); d = ImageDraw.Draw(im)
    for mesh in meshes:
        for tri in mesh.vertices[mesh.indices]:
            if np.isfinite(tri).all():
                d.line([xy(v) for v in [*tri, tri[0]]], fill='#35424c', width=1)
    for f in report['findings']:
        x, y = xy(f['position']); color = '#ff493f' if f['severity']=='high' else '#ffcf40'
        d.ellipse((x-4, y-4, x+4, y+4), fill=color)
    d.text((20, 995), 'North up; red=high, yellow=warning. ' + str(report['counts']), fill='white')
    im.save(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('files', nargs='*', type=Path)
    parser.add_argument('--sweep', type=Path, help='Recursively audit existing vis.glb or *.vis.glb, no tile requests')
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument('--epsilon', type=float, default=.003)
    parser.add_argument('--no-heatmap', action='store_true')
    args = parser.parse_args()
    files = args.files
    if args.sweep:
        files += sorted(set(args.sweep.rglob('vis.glb')) | set(args.sweep.rglob('*.vis.glb')))
    if not files:
        parser.error('no existing tile products found')
    args.out.mkdir(parents=True, exist_ok=True)
    total = Counter(); failed = []; summaries = []
    started = time.perf_counter()
    for file in files:
        tick = time.perf_counter()
        name = re.search(r't0_-?\d+_-?\d+', str(file))
        key = (name[0] if name else file.stem) + '-' + hashlib.sha256(str(file.resolve()).encode()).hexdigest()[:8]
        try:
            meshes = load_glb(file)
            report = audit(meshes, epsilon=args.epsilon)
            report.update(source=str(file), source_sha256=hashlib.sha256(file.read_bytes()).hexdigest(),
                          seconds=round(time.perf_counter()-tick, 3), depth_epsilon_m=args.epsilon,
                          coverage=['spikes', 'road_grade_magnitude', 'road_winding', 'slivers', 'coplanar_overlap'],
                          not_checked=['bridge_clearance', 'approach_continuity', 'road_cross_slope', 'holes', 'height_steps', 'terrain_penetration', 'detached_railings'])
            (args.out / f'{key}.json').write_text(json.dumps(report, indent=1, allow_nan=False)+'\n')
            if not args.no_heatmap and meshes:
                heatmap(meshes, report, args.out / f'{key}.png')
            total.update(report['counts']); summaries.append({k:report[k] for k in ['source','seconds','counts','triangles','road_triangles']})
        except Exception as exc:
            failed.append(dict(source=str(file), error=str(exc)))
    summary = dict(tiles=len(summaries), failed=failed, counts=dict(total), seconds=round(time.perf_counter()-started, 3), products=summaries)
    (args.out/'summary.json').write_text(json.dumps(summary, indent=1)+'\n')
    print(f"geometry-qa tiles={len(summaries)} failed={len(failed)} seconds={summary['seconds']} counts={json.dumps(dict(total), sort_keys=True)}")
    return 2 if failed else 1 if any(total.values()) else 0


if __name__ == '__main__':
    raise SystemExit(main())
