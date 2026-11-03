#!/usr/bin/env python3
"""Build the LiDAR point-cloud overlay product: one COPC file per 500 m city LAZ tile, plus index.json.

COPC (Cloud-Optimized Point Cloud, LAZ 1.4 with an octree hierarchy) is read by the game with HTTP range requests
(game/src/render/pointCloud). The source is the city's `pistepilvi` survey (LAS 1.2, point format 3: RGB, intensity,
classification), listed in data-manifests/laz-tiles.csv. Conversion is PDAL `writers.copc`; only tiles whose source
digest or recipe changed are rebuilt. Raw LAZ is found in --source dirs (read-only) or downloaded into --raw.

  build_copc.py --out /bulk/turku3d/lidarpc/copc --raw /bulk/turku3d/lidarpc/raw \
      --source ~/turku3d-bulk-warmup/data/raw/laz --pdal /bulk/turku3d/lidarpc/tools/pdal/bin/pdal \
      --bbox 23459000,6703500,23460500,6705000 -j 4
"""
import argparse
import csv
import hashlib
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
TILES_CSV = REPO / 'data-manifests/laz-tiles.csv'
# Bump when the output changes for the same source; every tile then rebuilds.
RECIPE_VERSION = 'copc@1'
SRS = 'EPSG:3877+3900'  # ETRS-GK23 + N2000, the engine frame (docs/briefs/COMMON.md)
SCALE = 0.01  # 1 cm: the survey's ~11 cm point spacing needs no millimetres; ~10 % smaller on the wire


def pipeline(source: Path, target: Path, threads: int) -> dict:
    return {'pipeline': [
        {'type': 'readers.las', 'filename': str(source), 'override_srs': SRS},
        {'type': 'writers.copc', 'filename': str(target), 'threads': threads,
         'scale_x': SCALE, 'scale_y': SCALE, 'scale_z': SCALE,
         'offset_x': 'auto', 'offset_y': 'auto', 'offset_z': 'auto'},
    ]}


def recipe_key(pdal_version: str) -> str:
    template = pipeline(Path('SOURCE'), Path('TARGET'), 0)
    return hashlib.sha256(json.dumps([RECIPE_VERSION, pdal_version, template], sort_keys=True).encode()).hexdigest()[:16]


def city_tiles() -> dict[str, dict]:
    """name -> {xmin, ymin, xmax, ymax, url} of every pistepilvi tile."""
    out = {}
    with open(TILES_CSV, newline='') as f:
        for row in csv.DictReader(f):
            if row['dataset'].strip() != 'pistepilvi':
                continue
            out[row['tile'].strip()] = {k: float(row[k]) for k in ('xmin', 'ymin', 'xmax', 'ymax')} | {'url': row['url'].strip()}
    return out


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1 << 22), b''):
            h.update(block)
    return h.hexdigest()


def copc_summary(path: Path) -> dict:
    """Header facts the index carries: point count, bounds and the COPC octree cube (LAS 1.4 + COPC info VLR)."""
    with open(path, 'rb') as f:
        head = f.read(375 + 54 + 160)
    if head[:4] != b'LASF' or head[24:26] != b'\x01\x04':
        raise ValueError(f'{path}: not LAS 1.4')
    if head[375 + 2:375 + 18].rstrip(b'\0') != b'copc' or struct.unpack_from('<H', head, 375 + 18)[0] != 1:
        raise ValueError(f'{path}: first VLR is not COPC info')
    maxx, minx, maxy, miny, maxz, minz = struct.unpack_from('<6d', head, 179)
    points = struct.unpack_from('<Q', head, 247)[0]
    cx, cy, cz, half, spacing = struct.unpack_from('<5d', head, 375 + 54)
    return {'points': points, 'min': [minx, miny, minz], 'max': [maxx, maxy, maxz],
            'cube': [cx, cy, cz, half], 'spacing': spacing}


class Builder:
    def __init__(self, args):
        self.args = args
        self.out = Path(args.out)
        self.tiles_dir = self.out / 'tiles'
        self.tiles_dir.mkdir(parents=True, exist_ok=True)
        self.raw = Path(args.raw) / 'pistepilvi'
        self.raw.mkdir(parents=True, exist_ok=True)
        self.sources = [Path(s).expanduser() / 'pistepilvi' for s in args.source]
        version = subprocess.run([args.pdal, '--version'], capture_output=True, text=True, check=True).stdout
        self.pdal_version = next((line.split()[1] for line in version.splitlines() if line.startswith('pdal ')), version.strip())
        self.key = recipe_key(self.pdal_version)
        self.lock = threading.Lock()
        self.city = city_tiles()

    def locate(self, name: str, tile: dict) -> Path | None:
        for d in self.sources + [self.raw]:
            if (d / f'{name}.laz').exists():
                return d / f'{name}.laz'
        if self.args.no_download:
            return None
        target = self.raw / f'{name}.laz'
        part = target.with_suffix('.laz.part')
        request = urllib.request.Request(tile['url'], headers={'User-Agent': 'turku3d-pointcloud/1'})
        try:
            with urllib.request.urlopen(request, timeout=120) as reply, open(part, 'wb') as f:
                expected = int(reply.headers.get('Content-Length') or -1)
                shutil.copyfileobj(reply, f, 1 << 20)
        except urllib.error.HTTPError as error:
            part.unlink(missing_ok=True)
            if error.code == 404:
                return None
            raise
        if expected >= 0 and part.stat().st_size != expected:
            part.unlink(missing_ok=True)
            raise IOError(f'{name}: truncated download')
        part.rename(target)
        return target

    def build(self, name: str) -> dict | None:
        tile = self.city[name]
        sidecar = self.tiles_dir / f'{name}.json'
        product = self.tiles_dir / f'{name}.copc.laz'
        source = self.locate(name, tile)
        if source is None:
            return None
        digest = sha256(source)
        if sidecar.exists() and product.exists():
            record = json.loads(sidecar.read_text())
            if record.get('source_sha256') == digest and record.get('recipe') == self.key:
                return record
        started = time.time()
        with tempfile.TemporaryDirectory(dir=self.tiles_dir, prefix=f'.{name}.') as tmp:
            target = Path(tmp) / 'out.copc.laz'
            spec = json.dumps(pipeline(source, target, self.args.threads))
            cmd = ['nice', '-n', '19', self.args.pdal, 'pipeline', '--stdin']
            if shutil.which('ionice'):
                cmd = ['ionice', '-c', '3'] + cmd
            r = subprocess.run(cmd, input=spec, capture_output=True, text=True)
            if r.returncode != 0:
                raise RuntimeError(f'{name}: pdal failed: {r.stderr.strip()[-400:]}')
            summary = copc_summary(target)
            os.replace(target, product)
        record = {'name': name, 'file': f'tiles/{name}.copc.laz', 'bytes': product.stat().st_size,
                  'source_sha256': digest, 'source_bytes': source.stat().st_size, 'recipe': self.key,
                  'seconds': round(time.time() - started, 1), **summary}
        tmp_sidecar = sidecar.with_suffix('.json.tmp')
        tmp_sidecar.write_text(json.dumps(record))
        os.replace(tmp_sidecar, sidecar)
        return record

    def write_index(self):
        """index.json lists every built tile; written atomically after each tile so serving follows the build."""
        tiles = []
        for sidecar in sorted(self.tiles_dir.glob('*.json')):
            record = json.loads(sidecar.read_text())
            if record.get('recipe') != self.key or not (self.tiles_dir / f"{record['name']}.copc.laz").exists():
                continue
            tiles.append({k: record[k] for k in ('name', 'file', 'bytes', 'points', 'min', 'max', 'cube', 'spacing')})
        index = {'format': 'turku3d-pointcloud@1', 'crs': SRS, 'recipe': self.key, 'pdal': self.pdal_version,
                 'tile_size': 500, 'updated': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'tiles': tiles}
        tmp = self.out / 'index.json.tmp'
        tmp.write_text(json.dumps(index, separators=(',', ':')))
        os.replace(tmp, self.out / 'index.json')
        return len(tiles)

    def select(self) -> list[str]:
        names = sorted(self.city)
        if self.args.tiles:
            names = [n for n in self.args.tiles.split(',') if n in self.city]
        if self.args.bbox:
            x0, y0, x1, y1 = map(float, self.args.bbox.split(','))
            names = [n for n in names if self.city[n]['xmax'] > x0 and self.city[n]['xmin'] < x1
                     and self.city[n]['ymax'] > y0 and self.city[n]['ymin'] < y1]
        # Nearest the engine origin first (E 23459500, N 6704500): the pilot block and the river centre lead.
        return sorted(names, key=lambda n: (self.city[n]['xmin'] + 250 - 23459500) ** 2 + (self.city[n]['ymin'] + 250 - 6704500) ** 2)

    def run(self):
        names = self.select()
        log = lambda *a: print(time.strftime('%H:%M:%S'), *a, flush=True)
        log(f'{len(names)} tiles, recipe {self.key}, pdal {self.pdal_version}, {self.args.jobs} jobs')
        done = missing = failed = 0
        with ThreadPoolExecutor(self.args.jobs) as pool:
            futures = {pool.submit(self.build, n): n for n in names}
            for future in as_completed(futures):
                name = futures[future]
                try:
                    record = future.result()
                except Exception as error:  # one bad tile must not stop the city
                    failed += 1
                    log('FAIL', name, error)
                    continue
                if record is None:
                    missing += 1
                    log('missing', name)
                    continue
                done += 1
                with self.lock:
                    count = self.write_index()
                log(f"ok {name} {record['points']} pts {record['bytes'] / 1e6:.1f} MB {record.get('seconds', 0)} s "
                    f'[{done + missing + failed}/{len(names)}, index {count}]')
        log(f'done {done}, missing {missing}, failed {failed}')
        return 1 if failed else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--out', required=True, help='product directory: index.json + tiles/*.copc.laz')
    ap.add_argument('--raw', required=True, help='download cache for raw LAZ (never in git)')
    ap.add_argument('--source', action='append', default=[], help='existing raw/laz roots to read (read-only)')
    ap.add_argument('--pdal', default='pdal')
    ap.add_argument('--tiles', help='comma-separated tile names')
    ap.add_argument('--bbox', help='E0,N0,E1,N1 (EPSG:3877)')
    ap.add_argument('-j', '--jobs', type=int, default=4)
    ap.add_argument('--threads', type=int, default=2, help='writers.copc threads per tile')
    ap.add_argument('--no-download', action='store_true')
    ap.add_argument('--index-only', action='store_true')
    args = ap.parse_args()
    builder = Builder(args)
    if args.index_only:
        print(builder.write_index(), 'tiles indexed')
        return 0
    return builder.run()


if __name__ == '__main__':
    sys.exit(main())
