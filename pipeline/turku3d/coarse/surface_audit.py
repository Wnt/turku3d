"""Advisory 0.5 m source-ground audit of emitted visual and collision triangles.

Counts are samples (holes/parity), faces (slivers/walls), and obstructing faces
(openings), not unique physical defects. Coordinates are engine metres and EPSG:3877.
Unsupported walls are candidates: a missing source tag is not proof of a defect.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import time

import numpy as np
import shapely

from turku3d import config as C
from turku3d.col.format import read_col
from turku3d.col.materials import mat_id


def triangles(mesh):
    return np.asarray(mesh.positions[mesh.indices], float).reshape(-1, 3, 3)


def normals(t):
    return np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0])


class Rays:
    """Vertical triangle intersections, batched with a planar spatial index."""
    def __init__(self, t):
        t = np.asarray(t, float).reshape(-1, 3, 3)
        self.t = t[np.abs(normals(t)[:, 1]) > 1e-10]
        self.tree = shapely.STRtree(shapely.polygons(self.t[:, :, [0, 2]]))

    def cast(self, query, expected, above=1.5):
        out = np.full(len(query), np.nan)
        # Nearest source floor handles a lower passage below a bridge. Ignore
        # overburden more than `above` metres over the requested walkable sheet.
        for start in range(0, len(query), 16384):
            q = query[start:start + 16384]
            target = expected[start:start + len(q)]
            qi, ti = self.tree.query(shapely.points(q), predicate='dwithin', distance=1e-5)
            a = self.t[ti, 0]
            b, c = self.t[ti, 1] - a, self.t[ti, 2] - a
            d = q[qi] - a[:, [0, 2]]
            det = b[:, 0] * c[:, 2] - b[:, 2] * c[:, 0]
            u = (d[:, 0] * c[:, 2] - d[:, 1] * c[:, 0]) / det
            v = (b[:, 0] * d[:, 1] - b[:, 2] * d[:, 0]) / det
            y = a[:, 1] + u * b[:, 1] + v * c[:, 1]
            valid = y <= target[qi] + above
            qi, y = qi[valid], y[valid]
            order = np.argsort(abs(y - target[qi]), kind='stable')
            _, first = np.unique(qi[order], return_index=True)
            pick = order[first]
            out[start + qi[pick]] = y[pick]
        return out


def records(points, **values):
    result = []
    for i, p in enumerate(points):
        row = dict(x=float(p[0]), y=float(p[1]), z=float(p[2]),
                   E=float(C.ORIGIN_E + p[0]), N=float(C.ORIGIN_N - p[2]))
        for key, data in values.items():
            value = data[i]
            row[key] = float(value) if np.isfinite(value) else None
        result.append(row)
    return result


def sample_domain(bounds, excluded, reference, spacing=0.5):
    x0, z0, x1, z1 = bounds
    x, z = np.meshgrid(np.arange(x0 + spacing / 2, x1, spacing),
                       np.arange(z0 + spacing / 2, z1, spacing))
    q = np.c_[x.ravel(), z.ravel()]
    q = q[~shapely.intersects_xy(excluded, q[:, 0], q[:, 1])]
    # The reference is an independently prepared source-ground sheet.
    from .street_profiles import HeightQuery
    y = HeightQuery(reference)(q)
    valid = np.isfinite(y)
    return q[valid], y[valid]


def audit(visual, collision, query, expected, *, support=None, openings=None):
    started = time.perf_counter()
    visual, collision = (np.asarray(t, float).reshape(-1, 3, 3) for t in (visual, collision))
    vy, cy = Rays(visual).cast(query, expected), Rays(collision).cast(query, expected)
    vh = ~np.isfinite(vy) | (expected - vy > 1.5)
    ch = ~np.isfinite(cy) | (expected - cy > 1.5)
    mismatch = np.isfinite(vy) & np.isfinite(cy) & (abs(vy - cy) > .15)
    mismatch |= np.isfinite(vy) != np.isfinite(cy)
    points = np.c_[query[:, 0], expected, query[:, 1]]
    defects = {}
    for name, mask in [('visual_holes', vh), ('collision_holes', ch), ('mismatch', mismatch)]:
        defects[name] = records(points[mask], visual_y=vy[mask], collision_y=cy[mask])
    defects['open_seams'] = open_seams(visual, query, vy)
    normal = normals(visual)
    twice_area = np.linalg.norm(normal, axis=1)
    edges = visual - np.roll(visual, 1, axis=1)
    # Longest side / its altitude; dimensionless and stable in 3D.
    aspect = np.max(np.sum(edges * edges, axis=2), axis=1) / np.maximum(twice_area, 1e-20)
    sliver = aspect > 20
    defects['slivers'] = records(visual[sliver].mean(axis=1), aspect=aspect[sliver])
    defects['degenerate'] = records(visual[twice_area < 2e-6].mean(axis=1))
    centre = visual.mean(axis=1)
    tall = (np.ptp(visual[:, :, 1], axis=1) > .5) & (abs(normal[:, 1]) < twice_area * .1)
    if support is not None:
        tall &= ~shapely.intersects_xy(support, centre[:, 0], centre[:, 2])
    else:
        tall[:] = False  # unknown provenance must not imply unsupported
    defects['unsupported_walls'] = records(centre[tall])
    # A nearly vertical face crossing a supported floor above its foot is a
    # thin-wall candidate, rather than a buried foundation or shoreline face.
    ids = np.flatnonzero(tall)
    floor = Rays(collision).cast(centre[ids][:, [0, 2]], centre[ids, 1])
    free = np.isfinite(floor) & (floor >= visual[ids, :, 1].min(axis=1) - .15)
    free &= floor < visual[ids, :, 1].max(axis=1) - .35
    defects['thin_walls'] = records(centre[ids[free]])
    defects['blocked_openings'] = []
    if openings is not None and openings.floors:
        # Count actual area removed by the authoritative 3D clearance prisms.
        p = collision.reshape(-1, 3)
        ix = np.arange(len(p)).reshape(-1, 3)
        cp, ci, _, owners = openings.carve(p, ix)
        original = np.linalg.norm(normals(collision), axis=1) / 2
        remaining = np.zeros(len(collision))
        np.add.at(remaining, owners, np.linalg.norm(normals(cp[ci]), axis=1) / 2)
        blocked = original - remaining > .002
        defects['blocked_openings'] = records(collision[blocked].mean(axis=1),
                                               area_m2=(original - remaining)[blocked])
    return dict(schema=1, spacing_m=.5, samples=len(query),
                counts={k: len(v) for k, v in defects.items()}, defects=defects,
                seconds=round(time.perf_counter() - started, 4), advisory=True)


def open_seams(visual, query, height, step=.5, spacing=.5):
    """Adjacent samples over `step` apart in height with no face crossing between them.

    From the side such a seam shows the void under the higher sheet's edge.
    """
    from .surface_closure import crossed

    if len(query) < 2:
        return []
    key = np.floor(query / spacing).astype(np.int64)
    key -= key.min(axis=0)
    width = int(key[:, 1].max()) + 2
    code = key[:, 0] * width + key[:, 1]
    order = np.argsort(code)
    pairs = []
    for step_code in (width, 1):  # +x and +z neighbours on the sample grid
        at = np.clip(np.searchsorted(code[order], code + step_code), 0, len(code) - 1)
        found = code[order][at] == code + step_code
        pairs.append(np.c_[np.flatnonzero(found), order[at[found]]])
    i, j = np.concatenate(pairs).T
    ok = np.isfinite(height[i]) & np.isfinite(height[j]) & (abs(height[i] - height[j]) > step)
    i, j = i[ok], j[ok]
    low = np.where(height[i] < height[j], i, j)
    high = np.where(height[i] < height[j], j, i)
    # Stacked sheets are not seams: the lower sheet continues under the higher
    # one (a deck edge over a walkway), or the higher one continues over it.
    rays = Rays(visual)
    under = rays.cast(query[high], height[low], above=.3)
    over = rays.cast(query[low], height[high], above=.3)
    stacked = (np.isfinite(under) & (abs(under - height[low]) < .3)) | \
              (np.isfinite(over) & (abs(over - height[high]) < .3))
    i, j = i[~stacked], j[~stacked]
    mid = (height[i] + height[j]) / 2
    shut = crossed(visual, query[i], query[j], mid)
    i, j, mid = i[~shut], j[~shut], mid[~shut]
    centre = (query[i] + query[j]) / 2
    return records(np.c_[centre[:, 0], mid, centre[:, 1]], step_m=abs(height[i] - height[j]))


def decode_visual(path):
    script = Path(__file__).parents[1] / 'package/surface_snapshot.mjs'
    run = subprocess.run(['node', str(script), str(path)], check=True,
                         capture_output=True, text=True, timeout=120)
    draws = json.loads(run.stdout)
    parts = []
    for d in draws:
        p = np.asarray(d['v']).reshape(-1, 3)
        ix = np.asarray(d['ix'], int).reshape(-1, 3)
        parts.append(p[ix])
    return np.concatenate(parts) if parts else np.empty((0, 3, 3))


def built(output, context):
    manifest = json.loads((output / 'manifest.json').read_text())
    visual = decode_visual(output / manifest['visual'][manifest['tile']]['url'])
    cols = [read_col((output / e['url']).read_bytes()) for e in manifest['collision'].values()]
    collision = np.concatenate([c.world_positions()[c.indices[c.materials != mat_id('water')]] for c in cols])
    with np.load(context, allow_pickle=False) as data:
        from .openings import Openings
        opening = Openings(list(data['floors']), list(data['clearance']))
        result = audit(visual, collision, data['query'], data['expected'],
                       support=shapely.from_wkb(bytes(data['support'])), openings=opening)
    result.update(tile=manifest['tile'], revision=manifest.get('revision'), source='decoded GLB + COL')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    parser.add_argument('--context', type=Path)
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    result = built(args.output, args.context or args.output / 'surface-context.npz')
    dest = args.report or args.output / 'surface-audit.json'
    dest.write_text(json.dumps(result, allow_nan=False) + '\n')
    print(json.dumps({k: v for k, v in result.items() if k != 'defects'}))


def build_context(tile, inputs, reference, openings, structures=()):
    """Persist source domain independently of the final mesh for later GLB audits."""
    buildings = [b.footprint for b in [*inputs.buildings, *inputs.wall_context]]
    excluded = shapely.union_all([*inputs.water, *buildings])
    q, expected = sample_domain(tile.bbox.as_tuple(), excluded, reference)
    # Preserve the lower mapped passage sheet. The source terrain can be the
    # overburden above it; the generated clearance floors carry its surveyed grade.
    if openings.floors:
        floors = np.asarray(openings.floors)
        floor_y = Rays(floors).cast(q, np.full(len(q), 10000.))
        hit = np.isfinite(floor_y)
        expected[hit] = floor_y[hit]
    supported = [*buildings, *[g.buffer(.3) for g, *_ in inputs.retaining],
                 *[g.buffer(.3) for g, *_ in inputs.barriers]]
    supported += [line.buffer(float(tags.get('width', 2.5)) / 2 + .3)
                  for _, line, tags in inputs.stairs[0]
                  if str(tags.get('width', 2.5)).replace('.', '', 1).isdigit()]
    # Water-boundary retaining faces have source support even without barrier tags.
    supported.extend(g.boundary.buffer(.3) for g in inputs.water)
    return dict(query=q, expected=expected, support=np.frombuffer(shapely.union_all(supported).wkb, np.uint8),
                floors=np.asarray(openings.floors).reshape(-1,3,3), clearance=np.asarray(openings.heights))


def gate(out, tile, inputs, reference, openings, visual_meshes, cols):
    """Cheap advisory farm check; export counts and coordinates without rejecting a tile."""
    try:
        context = build_context(tile, inputs, reference, openings)
        np.savez_compressed(out / 'surface-context.npz', **context)
        visible = [triangles(m) for m in visual_meshes if getattr(m, 'visual', True)]
        collision = [c.world_positions()[c.indices[c.materials != mat_id('water')]] for c in cols]
        result = audit(np.concatenate(visible), np.concatenate(collision), context['query'], context['expected'],
                       support=shapely.from_wkb(bytes(context['support'])), openings=openings)
        result.update(tile=tile.id, source='export meshes + float32 COL', status='complete')
        (out / 'surface-audit.json').write_text(json.dumps(result, allow_nan=False) + '\n')
        return {k: v for k, v in result.items() if k != 'defects'}
    except Exception as error:
        # Advisory diagnostics must not take the tile service down. An error is
        # explicit, never presented as zero defects or a successful audit.
        return dict(status='error', error=f'{type(error).__name__}: {error}', advisory=True)
