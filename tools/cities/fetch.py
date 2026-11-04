"""Fetch every raw unit the coarse generator needs for a city area (resumable, idempotent).

  tools/cities/run.sh espoo python tools/cities/fetch.py --bbox 25489000 6673000 25490000 6674000
  tools/cities/run.sh espoo python tools/cities/fetch.py --centre 25489700 6674800 --radius 5000 --jobs 4

Per 500 m block: ground (city lidar or the MML 2 m DEM), the draft ortho, the WFS layers (with the
city's absent/derived layers, turku3d.cities) and the OSM clip of its 2 km square, plus the building
registers (kanta:Rakennus, GIS:Rakennukset) that give facade semantics. With --mml-laz the MML 0.5 p
LAZ sheets under the area are downloaded to raw/mml/laz as reference (the generator does not read
them). A JSON line per unit goes to raw/fetch-log.jsonl; ``--dry-run`` only counts the blocks.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from turku3d import config as C
from turku3d.coarse import sources as S

REGISTERS = ("kanta:Rakennus", "GIS:Rakennukset")


def blocks_for(args) -> list[C.LazTile]:
    if args.bbox:
        e0, n0, e1, n1 = args.bbox
        return C.laz_tiles_for_bbox(C.BBoxEN(e0, n0, e1, n1))
    e, n, r = *args.centre, args.radius
    out = []
    for b in C.laz_tiles_for_bbox(C.BBoxEN(e - r, n - r, e + r, n + r)):
        bb = b.bbox_en
        dx = max(bb.emin - e, 0, e - bb.emax)
        dy = max(bb.nmin - n, 0, n - bb.nmax)
        if math.hypot(dx, dy) <= r:
            out.append(b)
    return out


def fetch_registers(block, log):
    from turku3d.fetch import wfs
    from turku3d.fetch.sources import WFS_LAYERS

    for name in REGISTERS:
        layer = next(layer for layer in WFS_LAYERS if layer.name == name)
        s = wfs.fetch_layer(layer, S.block_area(block), log=log)
        if s.get("status") not in ("ok", "empty"):
            raise RuntimeError(f"{name} {block.name}: {s}")


def regional_osm(blocks):
    """Clip the snapshot once to the area's squares; later square clips read that extract."""
    from turku3d.fetch import osm

    areas = [S.osm_area(b).core for b in blocks]
    core = C.BBoxLocal(min(a.xmin for a in areas), min(a.zmin for a in areas),
                       max(a.xmax for a in areas), max(a.zmax for a in areas))
    region = C.Area(f"{C.CITY.name}region", "regional OSM extract", core, 500.0)
    src = osm.clip(region, osm.fetch_finland(), log=print)
    osm.fetch_finland = lambda log=print: src


def mml_laz(blocks, log):
    """MML 0.5 p LAZ sheets (latest scan per sheet) under the blocks -> raw/mml/laz/<year>/<sheet>.laz."""
    import geopandas as gpd
    import shapely
    from pyproj import Transformer

    from turku3d.cities import MML_FUNET
    from turku3d.fetch.common import Registry, curl_download, curl_head

    root = C.raw_dir("mml")
    index = root / "laz-index" / "2008_latest.shp"
    sheets = gpd.read_file(index)
    t = Transformer.from_crs(C.CRS_HORIZONTAL, "EPSG:3067", always_xy=True)
    want = set()
    for b in blocks:
        bb = b.bbox_en
        x, y = t.transform([bb.emin, bb.emax, bb.emin, bb.emax], [bb.nmin, bb.nmin, bb.nmax, bb.nmax])
        hit = sheets[sheets.intersects(shapely.box(min(x), min(y), max(x), max(y)))]
        want.update(hit.path)
    reg = Registry(root)
    for rel in sorted(want):
        url = f"{MML_FUNET}/{rel.removeprefix('mml/')}"
        dest = root / "laz" / rel.split("2008_latest/", 1)[1]
        head = curl_head(url)
        rec, now = curl_download(url, dest, reg, expected_bytes=head["content_length"],
                                 last_modified=head["last_modified"], etag=head["etag"])
        log(f"mml  {rel}: {'downloaded' if now else 'cached'} {rec['bytes'] / 1e6:.0f} MB")


def main() -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--bbox", nargs=4, type=float, metavar=("EMIN", "NMIN", "EMAX", "NMAX"))
    g.add_argument("--centre", nargs=2, type=float, metavar=("E", "N"))
    ap.add_argument("--radius", type=float, default=5000.0)
    ap.add_argument("--jobs", type=int, default=2, help="blocks fetched at once (each is sequential)")
    ap.add_argument("--mml-laz", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    blocks = blocks_for(args)
    squares = {S.osm_area(b).name for b in blocks}
    print(f"{C.CITY.name}: {len(blocks)} blocks, {len(squares)} OSM squares", flush=True)
    # (the ground of the ring of blocks around them is fetched too, see below)
    if args.dry_run:
        return 0
    journal = (C.raw_dir() / "fetch-log.jsonl").open("a")
    failures = 0

    def one(block):
        lines = []
        for dataset in S.DATASETS:
            unit = S.Unit(dataset, block)
            started = time.time()
            try:
                S.fetch(unit, log=lines.append)
                outcome = "ok"
            except Exception as error:  # keep going; the journal lists failures
                outcome = f"error: {error}"[:400]
            lines.append(json.dumps({"unit": unit.key, "outcome": outcome,
                                     "seconds": round(time.time() - started, 1), "at": time.time()}))
        try:
            fetch_registers(block, lines.append)
            lines.append(json.dumps({"unit": f"registers/{block.name}", "outcome": "ok", "at": time.time()}))
        except Exception as error:
            lines.append(json.dumps({"unit": f"registers/{block.name}", "outcome": f"error: {error}"[:400]}))
        return block, lines

    # OSM squares are shared by blocks: clip each square once before the parallel block fetches, from one
    # regional extract (a clip of the whole Finland snapshot costs ~2 min; of the region, seconds).
    if len(squares) > 1:
        regional_osm(blocks)
    seen = set()
    for b in blocks:
        if S.osm_area(b).name not in seen:
            seen.add(S.osm_area(b).name)
            S.fetch(S.Unit("osm", b), log=print)
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        for future in as_completed([pool.submit(one, b) for b in blocks]):
            block, lines = future.result()
            for line in lines:
                if line.startswith("{"):
                    journal.write(line + "\n")
                    failures += '"outcome": "ok"' not in line
                print(f"{block.name} {line}", flush=True)
            journal.flush()
    # Ground key points are read for a tile plus S.POINT_MARGIN: the edge tiles need the next blocks' ground.
    ring = sorted({b for t in (t for blk in blocks for t in S.block_tiles(blk)) for b in S.tile_blocks(t)} - set(blocks))
    for b in ring:
        S.fetch(S.Unit("lidar", b), log=print)
    if args.mml_laz:
        mml_laz(blocks, print)
    print(f"done: {len(blocks)} blocks, {failures} failed units", flush=True)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
