#!/usr/bin/env python3
"""OSM helpers for the patrol (shared read-only extract ~/turku3d/data/raw/osm, pipeline venv: pyarrow, shapely).

  $PY tools/patrol/osm.py drive SPOTS.json ID "Street name" E0,N0 E1,N1 [--max 400]
      adds/replaces a drive: the named street's line between the points nearest E0,N0 and E1,N1, resampled every
      8 m in engine x/z, cut to --max m.
Library: masks(E, N, half) -> shapely geometry of water and rail areas around a point (pit detector exclusion).
"""
import glob, json, sys
from functools import lru_cache

import pyarrow.parquet as pq
import shapely
from shapely.geometry import Point, box
from shapely.ops import linemerge, substring, unary_union

E0, N0 = 23459500, 6704500
ROOT = '/Users/wnt/turku3d/data/raw/osm'


@lru_cache(maxsize=None)
def chunk(path):
    return pq.read_table(path, columns=['name', 'highway', 'railway', 'natural', 'landuse', 'tags', 'geometry']).to_pylist()


def chunks_near(E, N, half):
    out = []
    for f in glob.glob(f'{ROOT}/coarse-osm-*/*.parquet'):
        e, n = map(int, f.split('coarse-osm-')[1].split('/')[0].split('_'))
        if abs(e + 1000 - E) < 1000 + half and abs(n + 1000 - N) < 1000 + half: out.append(f)
    return out


def masks(E, N, half=60):
    """Water (natural=water, waterway areas, riverbank) buffered 6 m for quays, and rail (railway lines buffered 8 m,
    landuse=railway): ground there legitimately lies below the street."""
    area = box(E - half, N - half, E + half, N + half); geoms = []
    for f in chunks_near(E, N, half):
        for r in chunk(f):
            t = r['tags'] or ''
            water = r['natural'] == 'water' or '"waterway"' in t and ('riverbank' in t or '"water"' in t) or '"water": "river"' in t
            rail = r['railway'] in ('rail', 'light_rail', 'tram', 'subway', 'narrow_gauge') or r['landuse'] == 'railway'
            if not (water or rail): continue
            g = shapely.from_wkb(r['geometry'])
            if not g.intersects(area.buffer(20)): continue
            if water and g.geom_type in ('Polygon', 'MultiPolygon'): geoms.append(g.buffer(6))
            elif water: geoms.append(g.buffer(12))  # river centrelines without an area
            elif rail: geoms.append(g.buffer(8) if g.geom_type.endswith('LineString') else g)
    return unary_union(geoms) if geoms else None


def drive(name, a, b, max_len=400):
    ws = []
    for f in chunks_near((a[0] + b[0]) / 2, (a[1] + b[1]) / 2, abs(a[0] - b[0]) + abs(a[1] - b[1]) + 200):
        for r in chunk(f):
            if r['name'] == name and r['highway']:
                g = shapely.from_wkb(r['geometry'])
                if g.geom_type == 'LineString': ws.append(g)
    m = linemerge(ws); parts = list(m.geoms) if m.geom_type == 'MultiLineString' else [m]
    pa, pb = Point(a), Point(b)
    # the longest merged part passing within 150 m of both points (else the nearest part)
    near = [g for g in parts if g.distance(pa) < 150 and g.distance(pb) < 150]
    g = max(near, key=lambda g: g.length) if near else min(parts, key=lambda g: g.distance(pa) + g.distance(pb))
    s0, s1 = g.project(pa), g.project(pb)
    if abs(s1 - s0) < 50: s0, s1 = (0, g.length) if s0 <= s1 else (g.length, 0)
    seg = substring(g, min(s0, s1), max(s0, s1)); c = list(seg.coords)
    if s0 > s1: c = c[::-1]
    L = shapely.LineString(c)
    if L.length > max_len: L = substring(L, 0, max_len)
    k = max(1, int(L.length // 8))
    return [[round(L.interpolate(i * L.length / k).x - E0, 1), round(-(L.interpolate(i * L.length / k).y - N0), 1)] for i in range(k + 1)], round(L.length)


if __name__ == '__main__':
    a = sys.argv[1:]
    if a[:1] == ['drive']:
        path, did, name = a[1], a[2], a[3]
        p0 = tuple(map(float, a[4].split(','))); p1 = tuple(map(float, a[5].split(',')))
        mx = float(a[a.index('--max') + 1]) if '--max' in a else 400
        pts, length = drive(name, p0, p1, mx)
        cfg = json.load(open(path)); cfg['drives'] = [d for d in cfg.get('drives', []) if d['id'] != did]
        cfg['drives'].append({'id': did, 'street': name, 'why': f'{name} from {a[4]} towards {a[5]}', 'length_m': length, 'path_xz': pts})
        json.dump(cfg, open(path, 'w'), indent=1, ensure_ascii=False); print(did, name, length, 'm', len(pts), 'points')
    else:
        print(__doc__); sys.exit(2)
