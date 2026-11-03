#!/usr/bin/env python3
"""Derive the overhead-wire layer of the point-cloud product: one small COPC per city tile under <out>/wires/.

The survey has no wire class (classes 1-9 and 12 only), so wires are found geometrically in the raw pistepilvi LAZ:
  1. candidates: unclassified (1) and overlap (12) points more than 3 m above a 2 m ground grid (class 2 minima);
  2. free air: at most one classified point (anything but 1, 7, 12) in the 1.5 m cube around (0.5 m voxels);
  3. line-like: >= 4 candidates within 1.2 m whose covariance has linearity (l1 - l2) / l1 > 0.85 and a
     near-horizontal principal axis (|z| < 0.5);
  4. long: connected (1.5 m) segments at least 8 m across with at least 10 points (drops branch-like specks).
Wire points keep their survey attributes, become ASPRS class 14 (wire conductor) with a dark RGB, and carry
extra bytes WireSegment (uint32, per tile) and HeightAboveGround (float32) for geometry builders (lidarwires).
Only tiles whose raw LAZ is present are processed (build_copc.py downloads them); rerun or --loop picks up more.

  build_wires.py --out /bulk/turku3d/lidarpc/copc --raw /bulk/turku3d/lidarpc/raw \\
      --source ~/turku3d-bulk-warmup/data/raw/laz --pdal /bulk/turku3d/lidarpc/tools/pdal/bin/pdal -j 2 --loop 1800
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import laspy
import numpy as np
from scipy import ndimage
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components
from scipy.spatial import cKDTree

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_copc import SRS, city_tiles, copc_summary, sha256  # noqa: E402

RECIPE = {'version': 'wires@1', 'min_hag': 3.0, 'voxel': 0.5, 'crowd': 1, 'radius': 1.2, 'min_neighbours': 4,
          'linearity': 0.85, 'max_dz': 0.5, 'link': 1.5, 'min_extent': 8.0, 'min_points': 10, 'class': 14}
RECIPE_KEY = hashlib.sha256(json.dumps(RECIPE, sort_keys=True).encode()).hexdigest()[:16]
WIRE_RGB = (40 << 8, 40 << 8, 46 << 8)


def height_above_ground(x, y, z, cls):
    g = cls == 2
    x0, y0 = np.floor(x.min()), np.floor(y.min())
    gx, gy = ((x - x0) // 2).astype(np.int64), ((y - y0) // 2).astype(np.int64)
    ground = np.full((gx.max() + 1, gy.max() + 1), np.inf)
    if g.any():
        np.minimum.at(ground, (gx[g], gy[g]), z[g])
    holes = ~np.isfinite(ground)
    if holes.all():
        return z - z.min()
    if holes.any():
        ground = ground[tuple(ndimage.distance_transform_edt(holes, return_distances=False, return_indices=True))]
    return z - ground[gx, gy]


def voxel_keys(ix, iy, iz):
    return (ix << 42) | ((iy & 0x1fffff) << 21) | (iz & 0x1fffff)


def wire_points(x, y, z, cls, hag, r=RECIPE):
    """Indices of wire points and their segment ids."""
    cand = np.flatnonzero(((cls == 1) | (cls == 12)) & (hag > r['min_hag']))
    others = np.flatnonzero((hag > 1.0) & (cls != 1) & (cls != 7) & (cls != 12))
    if not len(cand):
        return cand, cand
    v, x0, y0 = r['voxel'], np.floor(x.min()), np.floor(y.min())
    vox = lambda idx: (np.floor((x[idx] - x0) / v).astype(np.int64), np.floor((y[idx] - y0) / v).astype(np.int64),
                       np.floor((z[idx] + 100) / v).astype(np.int64))
    uk, uc = np.unique(voxel_keys(*vox(others)), return_counts=True) if len(others) else (np.zeros(1, np.int64) - 1, np.zeros(1, np.int64))
    cx, cy, cz = vox(cand)
    crowd = np.zeros(len(cand), np.int64)
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            for dz in (-1, 0, 1):
                k = voxel_keys(cx + dx, cy + dy, cz + dz)
                i = np.minimum(np.searchsorted(uk, k), len(uk) - 1)
                crowd += np.where(uk[i] == k, uc[i], 0)
    iso = cand[crowd <= r['crowd']]
    if len(iso) < r['min_points']:
        return iso[:0], iso[:0]
    P = np.c_[x[iso], y[iso], z[iso]]
    nbrs = cKDTree(P).query_ball_point(P, r['radius'], workers=2)
    keep = np.zeros(len(iso), bool)
    for i, n in enumerate(nbrs):
        if len(n) < r['min_neighbours']:
            continue
        Q = P[n] - P[n].mean(0)
        w, vec = np.linalg.eigh(Q.T @ Q)
        keep[i] = (w[2] - w[1]) / max(w[2], 1e-9) > r['linearity'] and abs(vec[2, 2]) < r['max_dz']
    W, idx = P[keep], iso[keep]
    if len(W) < r['min_points']:
        return idx[:0], idx[:0]
    pairs = cKDTree(W).query_pairs(r['link'], output_type='ndarray')
    n = len(W)
    comp = connected_components(coo_matrix((np.ones(len(pairs)), (pairs[:, 0], pairs[:, 1])), shape=(n, n)), directed=False)[1]
    order = np.argsort(comp, kind='stable')
    starts = np.flatnonzero(np.r_[True, np.diff(comp[order]) != 0])
    good = np.zeros(comp.max() + 1, bool)
    for s, e in zip(starts, np.r_[starts[1:], n]):
        q = W[order[s:e], :2]
        good[comp[order[s]]] = e - s >= r['min_points'] and np.hypot(*(q.max(0) - q.min(0))) >= r['min_extent']
    mask = good[comp]
    # Dense segment ids in this tile.
    _, segment = np.unique(comp[mask], return_inverse=True)
    return idx[mask], segment.astype(np.uint32)


def build(name, source, tiles_dir, pdal):
    sidecar = tiles_dir / f'{name}.json'
    digest = sha256(source)
    if sidecar.exists():
        record = json.loads(sidecar.read_text())
        if record.get('source_sha256') == digest and record.get('recipe') == RECIPE_KEY:
            return record
    started = time.time()
    las = laspy.read(source)
    x, y, z, cls = np.asarray(las.x), np.asarray(las.y), np.asarray(las.z), np.asarray(las.classification)
    hag = height_above_ground(x, y, z, cls)
    idx, segment = wire_points(x, y, z, cls, hag)
    record = {'name': name, 'source_sha256': digest, 'recipe': RECIPE_KEY, 'points': int(len(idx)),
              'segments': int(segment.max() + 1) if len(idx) else 0, 'file': f'tiles/{name}.copc.laz'}
    product = tiles_dir / f'{name}.copc.laz'
    if len(idx):
        header = laspy.LasHeader(point_format=7, version='1.4')
        header.scales, header.offsets = np.array([0.01] * 3), np.array([np.floor(x.min()), np.floor(y.min()), 0.0])
        header.add_extra_dims([laspy.ExtraBytesParams('WireSegment', np.uint32), laspy.ExtraBytesParams('HeightAboveGround', np.float32)])
        out = laspy.LasData(header)
        out.x, out.y, out.z = x[idx], y[idx], z[idx]
        for dim in ('intensity', 'return_number', 'number_of_returns', 'gps_time', 'point_source_id'):
            try:
                out[dim] = np.asarray(las[dim])[idx]
            except Exception:
                pass
        out.classification = np.full(len(idx), RECIPE['class'], np.uint8)
        out.red, out.green, out.blue = (np.full(len(idx), c, np.uint16) for c in WIRE_RGB)
        out.WireSegment, out.HeightAboveGround = segment, hag[idx].astype(np.float32)
        with tempfile.TemporaryDirectory(dir=tiles_dir, prefix=f'.{name}.') as tmp:
            las_path, target = Path(tmp) / 'wires.las', Path(tmp) / 'out.copc.laz'
            out.write(las_path)
            spec = {'pipeline': [{'type': 'readers.las', 'filename': str(las_path), 'override_srs': SRS},
                                 {'type': 'writers.copc', 'filename': str(target), 'extra_dims': 'all',
                                  'scale_x': 0.01, 'scale_y': 0.01, 'scale_z': 0.01,
                                  'offset_x': 'auto', 'offset_y': 'auto', 'offset_z': 'auto'}]}
            r = subprocess.run([pdal, 'pipeline', '--stdin'], input=json.dumps(spec), text=True, capture_output=True)
            if r.returncode:
                raise RuntimeError(f'pdal: {r.stderr.strip()[-300:]}')
            record.update(copc_summary(target))
            os.replace(target, product)
        record['bytes'] = product.stat().st_size
    else:
        product.unlink(missing_ok=True)
    record['seconds'] = round(time.time() - started, 1)
    tmp_sidecar = sidecar.with_suffix('.json.tmp')
    tmp_sidecar.write_text(json.dumps(record))
    os.replace(tmp_sidecar, sidecar)
    return record


def write_index(out: Path):
    tiles = []
    for sidecar in sorted((out / 'tiles').glob('*.json')):
        r = json.loads(sidecar.read_text())
        if r.get('recipe') == RECIPE_KEY and r.get('points') and (out / r['file']).exists():
            tiles.append({k: r[k] for k in ('name', 'file', 'bytes', 'points', 'segments', 'min', 'max', 'cube', 'spacing')})
    index = {'format': 'turku3d-wires@1', 'crs': SRS, 'recipe': RECIPE_KEY, 'rules': RECIPE, 'class': RECIPE['class'],
             'extra_bytes': ['WireSegment', 'HeightAboveGround'],
             'updated': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'tiles': tiles}
    tmp = out / 'index.json.tmp'
    tmp.write_text(json.dumps(index, separators=(',', ':')))
    os.replace(tmp, out / 'index.json')
    return len(tiles)


def run(args):
    out = Path(args.out) / 'wires'
    tiles_dir = out / 'tiles'
    tiles_dir.mkdir(parents=True, exist_ok=True)
    city = city_tiles()
    dirs = [Path(s).expanduser() / 'pistepilvi' for s in args.source] + [Path(args.raw) / 'pistepilvi']
    names = sorted(city, key=lambda n: (city[n]['xmin'] + 250 - 23459500) ** 2 + (city[n]['ymin'] + 250 - 6704500) ** 2)
    if args.tiles:
        names = [n for n in names if n in args.tiles.split(',')]
    todo = []
    for n in names:
        source = next((d / f'{n}.laz' for d in dirs if (d / f'{n}.laz').exists()), None)
        if source is not None:
            todo.append((n, source))
    log = lambda *a: print(time.strftime('%H:%M:%S'), *a, flush=True)
    log(f'{len(todo)} of {len(names)} tiles have raw LAZ; recipe {RECIPE_KEY}')
    failed = 0
    with ProcessPoolExecutor(args.jobs, initializer=os.nice, initargs=(19,)) as pool:
        futures = {pool.submit(build, n, s, tiles_dir, args.pdal): n for n, s in todo}
        for i, f in enumerate(as_completed(futures), 1):
            try:
                r = f.result()
            except Exception as error:
                failed += 1
                log('FAIL', futures[f], str(error)[-300:])
                continue
            count = write_index(out)
            if r.get('seconds') is not None:
                log(f"ok {r['name']} {r['points']} wire points, {r['segments']} segments, {r.get('seconds')} s [{i}/{len(todo)}, index {count}]")
    return len(todo), failed


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--out', required=True, help='the point-cloud product directory (build_copc.py --out)')
    ap.add_argument('--raw', required=True)
    ap.add_argument('--source', action='append', default=[])
    ap.add_argument('--pdal', default='pdal')
    ap.add_argument('--tiles')
    ap.add_argument('-j', '--jobs', type=int, default=2)
    ap.add_argument('--loop', type=int, default=0, help='rescan for new raw tiles every N seconds until all are done')
    args = ap.parse_args()
    while True:
        found, failed = run(args)
        if not args.loop or found >= len(city_tiles()):
            return 1 if failed else 0
        time.sleep(args.loop)


if __name__ == '__main__':
    sys.exit(main())
