#!/usr/bin/env python3
"""Derive the overhead-wire layer of the point-cloud product: one small COPC per city tile under <out>/wires/.

The survey has no wire class (classes 1-9 and 12 only), so wires are found geometrically in the raw pistepilvi LAZ
by pipeline/turku3d/wire_points.py, the stage the generator's overhead wires (coarse/wires.py) fit their spans on.
Wire points keep their survey attributes, become ASPRS class 14 (wire conductor) with a dark RGB, and carry
extra bytes WireSegment (uint32, per tile) and HeightAboveGround (float32) for geometry builders (lidarwires).
Only tiles whose raw LAZ is present are processed (build_copc.py downloads them); rerun or --loop picks up more.

  build_wires.py --out /bulk/turku3d/lidarpc/copc --raw /bulk/turku3d/lidarpc/raw \\
      --source ~/turku3d-bulk-warmup/data/raw/laz --pdal /bulk/turku3d/lidarpc/tools/pdal/bin/pdal -j 2 --loop 1800
"""
import argparse
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

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'pipeline'))
from build_copc import SRS, city_tiles, copc_summary, sha256  # noqa: E402
# The detection stage is shared with the generator's overhead wires (pipeline/turku3d/coarse/wires.py).
from turku3d.wire_points import RECIPE, RECIPE_KEY, WIRE_RGB, height_above_ground, wire_points  # noqa: E402

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
