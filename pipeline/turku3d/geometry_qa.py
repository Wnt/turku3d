"""Fast GPU-free audit of actual tile triangles. Never requests or rebuilds tiles.

python -m turku3d.geometry_qa tile/vis.glb --out reports/qa
python -m turku3d.geometry_qa --sweep CACHE --out reports/city

Thresholds are engineering heuristics, not proof of road drivability. Sibling
roads.json adds directional grade, cross-slope and shared-edge step checks.
Bridge clearance remains outside this audit. Quay-floor edges are checked for
missing supports where a terrain cut meets open water.
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


def road_context(graph, points):
    """Nearest car-corridor tangent/profile, respecting stacked road heights."""
    size = len(points)
    best = np.full(size, np.inf)
    tangents = np.zeros((size, 2)); selected = np.full(size, -1, dtype=int)
    nodes = np.asarray(graph['nodes'], float).reshape(-1, 2)
    columns = graph.get('edgeAttrColumns', [])
    attrs = graph.get('edgeAttrs', [])
    car = graph.get('bits', {}).get('car', 2)
    for edge_id, edge in enumerate(graph['edges']):
        if not edge[2] & car:
            continue
        width = attrs[edge_id][columns.index('width')] if 'width' in columns else 6
        line = np.vstack([nodes[edge[0]], np.asarray(edge[4:]).reshape(-1, 2), nodes[edge[1]]])
        heights = graph.get('edgeHeights', [])[edge_id] if graph.get('edgeHeights') else None
        for i, (a, b) in enumerate(zip(line, line[1:])):
            v = b-a; length2 = v@v
            if length2 < 1e-10:
                continue
            u = np.clip((points[:, [0,2]]-a)@v/length2, 0, 1)
            delta = points[:, [0,2]] - (a+u[:,None]*v)
            distance = np.linalg.norm(delta,axis=1)
            valid = (distance < width*.5) & (distance < best)
            if heights is not None:
                height = heights[i]*(1-u)+heights[i+1]*u
                # Do not attribute the road beneath an overpass to the upper deck.
                valid &= np.abs(points[:,1]-height) < .8
            best[valid] = distance[valid]
            tangents[valid] = v/np.sqrt(length2)
            selected[valid] = edge_id
    return selected >= 0, tangents, selected


def quay_floor_gaps(meshes, epsilon=.015):
    """Locate water-facing passage floors with no surface immediately below them."""
    import shapely

    water, vertical, floors = [], [], []
    for mesh in meshes:
        t = mesh.vertices[mesh.indices]
        if not len(t):
            continue
        normal = np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0])
        length = np.linalg.norm(normal, axis=1)
        vertical.extend(t[(length > 1e-8) & (abs(normal[:, 1]) < .2 * length)])
        if mesh.metadata.get('material_key') == 'water':
            water.extend(t[:, :, [0, 2]])
        if mesh.name.startswith('tunnel:') and mesh.name.endswith(':floor') and mesh.metadata.get('material_key') == 'draft_bridge_stone':
            floors.append((mesh, t))
    if not water or not floors:
        return []
    wet = shapely.STRtree(shapely.polygons(np.asarray(water)))
    walls = np.asarray(vertical).reshape(-1, 3, 3)
    lo, hi = walls.min(axis=1), walls.max(axis=1)
    tree = shapely.STRtree(shapely.box(lo[:, 0]-epsilon, lo[:, 2]-epsilon,
                                    hi[:, 0]+epsilon, hi[:, 2]+epsilon))
    result = []
    for mesh, triangles in floors:
        footprint = shapely.union_all(shapely.polygons(triangles[:, :, [0, 2]]))
        edges = np.concatenate([triangles[:, pair] for pair in ([0, 1], [1, 2], [2, 0])])
        centres = edges.mean(axis=1)
        boundary = shapely.distance(shapely.points(centres[:, [0, 2]]), footprint.boundary) < epsilon
        for edge, centre in zip(edges[boundary], centres[boundary], strict=True):
            if centre[1] < .3 or np.linalg.norm(edge[1]-edge[0]) < .1:
                continue
            # A tile edge is closed by the neighbouring tile, not a local skirt.
            if any(np.all(abs(edge[:, axis] / 250 - round(centre[axis] / 250)) < 1e-6) for axis in (0, 2)):
                continue
            point = shapely.Point(centre[[0, 2]])
            if not len(wet.query(point, predicate='dwithin', distance=.08)):
                continue
            q = centre.copy(); q[1] -= .25
            ids = tree.query(point)
            supported = False
            for tri in walls[ids]:
                a, b = tri[1]-tri[0], tri[2]-tri[0]
                n = np.cross(a, b)
                if abs((q-tri[0]) @ n) > epsilon * np.linalg.norm(n):
                    continue
                uv = np.linalg.lstsq(np.stack([a, b], axis=1), q-tri[0], rcond=None)[0]
                if uv.min() >= -.01 and uv.sum() <= 1.01:
                    supported = True; break
            if not supported:
                result.append((mesh.name, q))
    return result


def audit(meshes, *, epsilon=.003, overlap_area=.01, graph=None):
    findings = []
    triangles, owners = [], []
    def emit(kind, severity, point, mesh, **detail):
        p = np.asarray(point)
        findings.append(dict(kind=kind, severity=severity, mesh=mesh,
                             position=p.tolist(), world_enh=[23459500 + float(p[0]),
                             6704500 - float(p[2]), float(p[1])], **detail))
    for name, point in quay_floor_gaps(meshes):
        emit('quay_floor_unsealed', 'high', point, name)
    road_count = 0; contextual_road_count = 0; road_edges = defaultdict(list)
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
        road = bool(re.search(r'road|asphalt|cobblestone|bridge_deck', mesh.label)) and not bool(re.search(r'paint|marking', mesh.label))
        for j in np.flatnonzero((np.abs(tri[:, :, 1]) > 500).any(1)):
            emit('height_spike', 'high', tri[j].mean(0), mesh.name, triangle=int(j), height_range=tri[j, :, 1].tolist())
        if not road:
            continue
        road_count += len(tri)
        horizontal = np.abs(normal[:, 1]) / np.maximum(area2, 1e-20)
        slope = np.linalg.norm(normal[:, [0, 2]], axis=1) / np.maximum(np.abs(normal[:, 1]), 1e-20)
        # Steep curb/rim faces are excluded; a road surface faces roughly upward.
        surface = horizontal > .65
        meaningful = area2 >= .02  # <1 cm² reversed faces are usually codec-boundary dust.
        context = np.zeros(len(tri), bool)
        if graph:
            context, tangent, edge_id = road_context(graph, tri.mean(1))
            context &= surface & meaningful
            contextual_road_count += int(context.sum())
            gradient = -normal[:, [0,2]] / np.where(np.abs(normal[:,1,None]) > 1e-12, normal[:,1,None], 1e-12)
            grade = np.abs(np.sum(gradient*tangent,axis=1))
            cross_slope = np.abs(gradient[:,0]*tangent[:,1]-gradient[:,1]*tangent[:,0])
            for kind, values, limit in [('road_longitudinal_grade', grade, .18),
                                        ('road_cross_slope', cross_slope, .12)]:
                for j in np.flatnonzero(context & (values > limit)):
                    emit(kind, 'high', tri[j].mean(0), mesh.name, triangle=int(j),
                         slope=float(values[j]), road_edge=int(edge_id[j]))
            for j in np.flatnonzero(context):
                for a, b in zip(tri[j], np.roll(tri[j],-1,axis=0)):
                    key = tuple(sorted((tuple(np.round(a[[0,2]]/.005).astype(int)),
                                        tuple(np.round(b[[0,2]]/.005).astype(int)))))
                    road_edges[key].append((float((a[1]+b[1])*.5), (a+b)*.5, mesh.name))
        for kind, severity, mask in [
            ('road_degenerate', 'warning', area2 < 1e-8),
            ('road_grade', 'warning', surface & (slope > .25)),
            ('road_flipped', 'high', surface & meaningful & (normal[:, 1] < 0)),
            ('road_codec_sliver', 'warning', surface & ~meaningful & (normal[:, 1] < 0)),
            ('road_sliver', 'warning', surface & (area2 / np.maximum(edges.max(1)**2, 1e-20) < .005) & (edges.max(1) > 2)),
        ]:
            for j in np.flatnonzero(mask):
                emit(kind, severity, tri[j].mean(0), mesh.name, triangle=int(j), slope=float(min(slope[j], 1e6)))
    for rows in road_edges.values():
        low, high = min(rows,key=lambda r:r[0]), max(rows,key=lambda r:r[0])
        if high[0]-low[0] > .18:
            emit('road_height_step', 'high', high[1], high[2],
                 other_mesh=low[2], step_m=high[0]-low[0])
    if not triangles:
        return dict(counts=dict(Counter(f['kind'] for f in findings)), findings=findings, triangles=0, road_triangles=0)
    t = np.concatenate(triangles); owner = np.asarray(owners)
    n = np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0])
    lengths = np.linalg.norm(n, axis=1)
    n /= np.maximum(lengths[:, None], 1e-20)
    dominant = np.argmax(np.abs(n), axis=1)
    # A projected spatial hash bounds work by local density, including vertical facades.
    buckets = defaultdict(lambda: defaultdict(list))
    mins, maxs = t.min(1), t.max(1)
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
        prior = set(j for key in keys for other, ids in buckets[key].items()
                    if other != owner[i] for j in ids)
        if prior:
            js = np.fromiter(prior, dtype=int)
            js = js[(maxs[js] >= mins[i]-epsilon).all(1) & (mins[js] <= maxs[i]+epsilon).all(1)]
            js = js[np.abs(n[js] @ n[i]) > .99999]
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
                        program = meshes[owner[i]].metadata.get('vertex_program') or meshes[owner[j]].metadata.get('vertex_program')
                        emit('coplanar_program_bounds' if program else 'coplanar_overlap',
                             'warning' if program else 'high', (t[i].mean(0) + t[j].mean(0)) / 2,
                             meshes[owner[i]].name, other_mesh=meshes[owner[j]].name,
                             material=meshes[owner[i]].metadata.get('material_key'),
                             other_material=meshes[owner[j]].metadata.get('material_key'),
                             unresolved_vertex_program=program,
                             triangles=[i, int(j)], projected_area_m2=area,
                             depth_delta_m=float(np.max(np.abs((t[j] - t[i, 0]) @ n[i]))))
        for key in keys:
            buckets[key][owner[i]].append(i)
    return dict(counts=dict(sorted(Counter(f['kind'] for f in findings).items())),
                severity=dict(Counter(f['severity'] for f in findings)), findings=findings,
                triangles=len(t), road_triangles=road_count, contextual_road_triangles=contextual_road_count,
                overlap_candidates=candidates)


def heatmap(meshes, report, path):
    from PIL import Image, ImageDraw
    points = np.concatenate([m.vertices[:, [0, 2]] for m in meshes if len(m.vertices)])
    points = points[np.isfinite(points).all(1)]
    if not len(points):
        points = np.zeros((1,2))
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


def farm_products(root, generation):
    """Current generation wins, then retained fallbacks in service priority order."""
    selected = {}
    current = root/'products'/generation
    if not current.is_dir():
        raise ValueError(f'unknown generation {generation}')
    fallback_file = root/'fallbacks.json'
    fallbacks = json.loads(fallback_file.read_text()) if fallback_file.exists() else []
    for directory in [current, *map(Path, fallbacks)]:
        for file in sorted(directory.glob('t0_*/vis.glb')):
            selected.setdefault(file.parent.name, file)
        for file in sorted(directory.glob('visual/0/t0_*.glb')):
            selected.setdefault(file.stem, file)
    def order(file):
        match = re.search(r't0_(-?\d+)_(-?\d+)', str(file))
        return sum(int(v)**2 for v in match.groups()) if match else float('inf')
    return sorted(selected.values(), key=order)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('files', nargs='*', type=Path)
    parser.add_argument('--sweep', type=Path, help='Recursively audit existing vis.glb or *.vis.glb, no tile requests')
    parser.add_argument('--farm', type=Path, help='Farm root: current generation plus service-priority retained fallback tiles')
    parser.add_argument('--generation', help='Required with --farm; explicitly pins the product generation')
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument('--epsilon', type=float, default=.003)
    parser.add_argument('--no-heatmap', action='store_true')
    args = parser.parse_args()
    files = args.files
    if args.sweep:
        files += sorted(set(args.sweep.rglob('vis.glb')) | set(args.sweep.rglob('*.vis.glb')))
    if args.farm:
        if not args.generation:
            parser.error('--farm needs --generation')
        files += farm_products(args.farm, args.generation)
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
            source_hash = hashlib.sha256(file.read_bytes()).hexdigest()
            meshes = load_glb(file)
            road_file = file.parent/'roads.json'
            road_data = road_file.read_bytes() if road_file.exists() else None
            graph = json.loads(road_data) if road_data else None
            report = audit(meshes, epsilon=args.epsilon, graph=graph)
            if hashlib.sha256(file.read_bytes()).hexdigest() != source_hash:
                raise ValueError('product changed during audit; retry the immutable hashed GLB')
            report.update(source=str(file), source_sha256=source_hash,
                          seconds=round(time.perf_counter()-tick, 3), depth_epsilon_m=args.epsilon,
                          road_context_source=str(road_file) if graph else None,
                          road_context_sha256=hashlib.sha256(road_data).hexdigest() if graph else None,
                          coverage=['spikes', 'road_grade_magnitude', 'road_winding', 'slivers', 'coplanar_overlap']
                            + (['road_longitudinal_grade', 'road_cross_slope', 'road_height_step'] if graph else []),
                          not_checked=['bridge_clearance', 'approach_continuity', 'holes', 'terrain_penetration', 'detached_railings']
                            + ([] if graph else ['road_longitudinal_grade', 'road_cross_slope', 'road_height_step']))
            (args.out / f'{key}.json').write_text(json.dumps(report, indent=1, allow_nan=False)+'\n')
            if not args.no_heatmap and meshes:
                heatmap(meshes, report, args.out / f'{key}.png')
            total.update(report['counts']); summaries.append({k:report[k] for k in ['source','seconds','counts','triangles','road_triangles']})
        except Exception as exc:
            failed.append(dict(source=str(file), error=str(exc)))
    summary = dict(tiles=len(summaries), failed=failed, counts=dict(total), seconds=round(time.perf_counter()-started, 3), products=summaries,
                   farm_generation=args.generation if args.farm else None)
    (args.out/'summary.json').write_text(json.dumps(summary, indent=1)+'\n')
    print(f"geometry-qa tiles={len(summaries)} failed={len(failed)} seconds={summary['seconds']} counts={json.dumps(dict(total), sort_keys=True)}")
    return 2 if failed else 1 if any(total.values()) else 0


if __name__ == '__main__':
    raise SystemExit(main())
