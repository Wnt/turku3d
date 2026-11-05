"""Native Dutch source downloads for one city area (RD New EPSG:28992 + NAP heights).

    python -m turku3d.nl.fetch amsterdam --bbox 120500,487000,121500,488000 [--plan]
    python -m turku3d.nl.fetch amsterdam --radius 5000 [--plan] [--only ahn,3dbag]

Run through ``tools/cities/run.sh <city>`` (TURKU3D_DATA = the city's data root). Writes under
``<root>/<dataset>/`` (default ``$TURKU3D_DATA/raw/nl``, on the bulk pool) and records every
file with its URL, licence, size, sha256 and fetch date in ``<root>/SOURCES.json``:

* ``ahn``    AHN point cloud, GeoTiles 1 x 1.25 km sub-tiles of the AHN sheets (AHN5, else AHN4), CC0
* ``3dbag``  3DBAG CityJSON tiles (LoD 1.2/1.3/2.2 with semantic surfaces), CC BY 4.0
* ``bgt``    BGT OGC API GeoJSON, every collection, one file per 500 m block of the city grid, CC0
* ``osm``    Geofabrik province extract (ODbL), through the generator's own OSM fetcher into raw/osm

The orthophoto (PDOK WMS, CC BY 4.0) is fetched per tile by the generator's own WMS fetcher, which
writes the raw layout the build reads (``turku3d.fetch.wms``); ``--plan`` estimates it.
``--plan`` only sends HEAD/count requests and prints the expected bytes per dataset.
Downloads resume: a file already recorded with the same size is skipped.
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import datetime as dt
import gzip
import hashlib
import json
import math
import os
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

from turku3d.cities import CITIES

AHN_INDEX = "https://service.pdok.nl/rws/actueel-hoogtebestand-nederland/atom/downloads/dtm_05m/kaartbladindex.json"
GEOTILES = "https://geotiles.citg.tudelft.nl/{version}_T/{name}.LAZ"
AHN_VERSIONS = ("AHN5", "AHN4")
BAG_VERSION = "v20250903"
BAG_INDEX = f"https://data.3dbag.nl/{BAG_VERSION}/tile_index.fgb"
BGT_API = "https://api.pdok.nl/lv/bgt/ogc/v1"
RD = "http://www.opengis.net/def/crs/EPSG/0/28992"
BLOCK = 500.0
LICENCES = {
    "ahn": "CC0 1.0, Rijkswaterstaat / Het Waterschapshuis (AHN); tiling: GeoTiles, TU Delft (CC BY 4.0)",
    "3dbag": "CC BY 4.0, 3DBAG by tudelft3d and 3DGI",
    "bgt": "CC0 1.0, Basisregistratie Grootschalige Topografie (Kadaster / PDOK)",
    "osm": "ODbL 1.0, (c) OpenStreetMap contributors (Geofabrik extract)",
    "ortho": "CC BY 4.0, Beeldmateriaal Nederland (PDOK Luchtfoto RGB)",
}
_lock = threading.Lock()


def cities_root() -> Path:
    return Path(os.environ.get("TURKU3D_CITIES", "/bulk/turku3d/cities"))


# -- area -------------------------------------------------------------------------------------------


class Area:
    """An RD bbox, optionally limited to a circle (centre, radius)."""

    def __init__(self, bbox, centre=None, radius=None):
        self.bbox = tuple(float(v) for v in bbox)
        self.centre, self.radius = centre, radius

    def hits(self, x0, y0, x1, y1) -> bool:
        b = self.bbox
        if x1 <= b[0] or x0 >= b[2] or y1 <= b[1] or y0 >= b[3]:
            return False
        if self.centre is None:
            return True
        cx, cy = self.centre
        dx = max(x0 - cx, 0, cx - x1)
        dy = max(y0 - cy, 0, cy - y1)
        return dx * dx + dy * dy < self.radius**2

    def blocks(self, origin):
        """500 m blocks of the city grid (aligned to the city origin) touching the area."""
        oe, on = origin
        b = self.bbox
        i0, i1 = math.floor((b[0] - oe) / BLOCK), math.ceil((b[2] - oe) / BLOCK)
        j0, j1 = math.floor((b[1] - on) / BLOCK), math.ceil((b[3] - on) / BLOCK)
        for i in range(i0, i1):
            for j in range(j0, j1):
                x0, y0 = oe + i * BLOCK, on + j * BLOCK
                if self.hits(x0, y0, x0 + BLOCK, y0 + BLOCK):
                    yield (x0, y0, x0 + BLOCK, y0 + BLOCK)


# -- http ---------------------------------------------------------------------------------------------


def _get(url, *, method="GET", tries=8, timeout=120):
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, method=method, headers={"User-Agent": "turku3d-fetch"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, r.headers, (r.read() if method == "GET" else b"")
        except urllib.error.HTTPError as e:
            if e.code in (404, 403):
                return e.code, e.headers or {}, b""
            if attempt == tries - 1:
                raise
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            if attempt == tries - 1:
                raise
        time.sleep(min(60, 2 + 5 * attempt))  # rides out short DNS or proxy outages
    raise RuntimeError(url)


def head_size(url) -> int | None:
    status, headers, _ = _get(url, method="HEAD")
    return int(headers.get("Content-Length", 0)) if status == 200 else None


def sha256(path: Path) -> str:
    with path.open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


class Ledger:
    """``SOURCES.json``: one record per downloaded file (url, licence, bytes, sha256, date)."""

    def __init__(self, root: Path):
        self.path = root / "SOURCES.json"
        self.data = json.loads(self.path.read_text()) if self.path.exists() else {"files": {}}

    def done(self, path: Path) -> bool:
        if self.path.exists() and self.path.stat().st_mtime_ns != getattr(self, "_seen", None):
            self._seen = self.path.stat().st_mtime_ns
            self.data = json.loads(self.path.read_text())
        rec = self.data["files"].get(str(path.relative_to(self.path.parent)))
        return bool(rec) and path.exists() and path.stat().st_size == rec["bytes"]

    def record(self, path: Path, dataset: str, url: str, **extra):
        rec = {
            "dataset": dataset,
            "url": url,
            "licence": LICENCES[dataset],
            "bytes": path.stat().st_size,
            "sha256": sha256(path),
            "fetched": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
            **extra,
        }
        import fcntl

        # Several processes (a 5 km fetch, on-demand builds) share one ledger: merge under a lock.
        with _lock, open(self.path.with_suffix(".lock"), "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            if self.path.exists():
                self.data = json.loads(self.path.read_text())
            self.data["files"][str(path.relative_to(self.path.parent))] = rec
            tmp = self.path.with_name(f"{self.path.name}.{os.getpid()}.tmp")
            tmp.write_text(json.dumps(self.data, indent=1, sort_keys=True))
            tmp.replace(self.path)


def curl(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    subprocess.run(
        ["curl", "-fsSL", "--retry", "5", "--retry-delay", "5", "-C", "-", "-o", str(part), url],
        check=True,
    )
    part.replace(dest)


# -- AHN ----------------------------------------------------------------------------------------------


def ahn_subtiles(area: Area, index_path: Path):
    """(name, bbox) of the GeoTiles sub-tiles touching the area: each AHN sheet (5 x 6.25 km) is cut
    into 5 x 5 tiles of 1 x 1.25 km numbered 01..25 row by row from the north-west corner."""
    index = json.loads(index_path.read_text())
    for feature in index["features"]:
        ring = feature["geometry"]["coordinates"][0]
        xs, ys = [p[0] for p in ring], [p[1] for p in ring]
        x0, y0, x1, y1 = min(xs), min(ys), max(xs), max(ys)
        if not area.hits(x0, y0, x1, y1):
            continue
        sheet = feature["properties"]["kaartbladNr"].removeprefix("M_")
        w, h = (x1 - x0) / 5, (y1 - y0) / 5
        for k in range(25):
            c, r = k % 5, k // 5
            sx0, sy1 = x0 + c * w, y1 - r * h
            if area.hits(sx0, sy1 - h, sx0 + w, sy1):
                yield f"{sheet}_{k + 1:02d}", (sx0, sy1 - h, sx0 + w, sy1)


def fetch_ahn(area, root, ledger, plan, log):
    index = cities_root() / "nl-index" / "kaartbladindex.json"
    if not index.exists():
        curl(AHN_INDEX, index)
    total, rows = 0, []
    tiles = list(ahn_subtiles(area, index))

    def one(item):
        name, bbox = item
        for version in AHN_VERSIONS:
            url = GEOTILES.format(version=version, name=name)
            dest = root / "ahn" / version / f"{name}.LAZ"
            if ledger.done(dest):
                return version, dest.stat().st_size
            size = head_size(url)
            if size is None:
                continue
            if not plan:
                curl(url, dest)
                ledger.record(dest, "ahn", url, version=version, bbox_rd=list(bbox))
                log(f"ahn  {version}/{name}: {size / 1e6:.0f} MB")
            return version, size
        return None, 0

    with cf.ThreadPoolExecutor(4) as pool:
        for (name, _), (version, size) in zip(tiles, pool.map(one, tiles)):
            rows.append((name, version, size))
            total += size
    return {"files": len(rows), "bytes": total, "versions": sorted({v for _, v, _ in rows if v})}


# -- 3DBAG --------------------------------------------------------------------------------------------


def bag_tiles(area: Area):
    import geopandas as gpd

    index = cities_root() / "nl-index" / f"3dbag-{BAG_VERSION}-tile_index.fgb"
    if not index.exists():
        curl(BAG_INDEX, index)
    frame = gpd.read_file(index)
    for row in frame.itertuples():
        if area.hits(*row.geometry.bounds):
            yield row.tile_id, row.cj_download, row.cj_sha256, row.geometry.bounds


def fetch_3dbag(area, root, ledger, plan, log):
    tiles = list(bag_tiles(area))

    def one(item):
        tile_id, url, digest, bounds = item
        dest = root / "3dbag" / BAG_VERSION / url.rsplit("/", 1)[1]
        if ledger.done(dest):
            return dest.stat().st_size
        if plan:
            return head_size(url) or 0
        curl(url, dest)
        got = hashlib.sha256(gzip.open(dest).read()).hexdigest()
        if got != digest and sha256(dest) != digest:
            raise RuntimeError(f"3dbag {tile_id}: sha256 mismatch")
        ledger.record(dest, "3dbag", url, tile=tile_id, bbox_rd=list(bounds), version=BAG_VERSION)
        return dest.stat().st_size

    with cf.ThreadPoolExecutor(6) as pool:
        sizes = list(pool.map(one, tiles))
    return {"files": len(tiles), "bytes": sum(sizes), "version": BAG_VERSION}


# -- BGT ----------------------------------------------------------------------------------------------


def bgt_collections() -> list[str]:
    _, _, body = _get(f"{BGT_API}/collections?f=json")
    return [c["id"] for c in json.loads(body)["collections"]]


def bgt_items(collection: str, bbox) -> tuple[list, int]:
    params = {
        "f": "json",
        "limit": "1000",
        "bbox": ",".join(f"{v:.0f}" for v in bbox),
        "bbox-crs": RD,
        "crs": RD,
    }
    url = f"{BGT_API}/collections/{collection}/items?{urllib.parse.urlencode(params)}"
    features, pages = [], 0
    while url:
        status, _, body = _get(url)
        if status != 200:
            raise RuntimeError(f"BGT {collection} {bbox}: HTTP {status}")
        page = json.loads(body)
        features.extend(page.get("features", []))
        pages += 1
        url = next((l["href"] for l in page.get("links", []) if l.get("rel") == "next"), None)
    return features, pages


def fetch_bgt(area, root, ledger, plan, log, origin):
    collections = bgt_collections()
    blocks = list(area.blocks(origin))
    if plan:
        # Count one central block fully and scale by the number of blocks.
        sample = sorted(blocks, key=lambda b: abs(b[0] - origin[0]) + abs(b[1] - origin[1]))[0]
        per_block = served = 0
        for name in collections:
            feats, _ = bgt_items(name, sample)
            served += len(json.dumps({"type": "FeatureCollection", "features": feats}))
            feats = [f for f in feats if not (f.get("properties") or {}).get("eind_registratie")]
            per_block += len(gzip.compress(json.dumps({"type": "FeatureCollection", "features": feats}).encode(), 6))
        return {"blocks": len(blocks), "collections": len(collections), "bytes": per_block * len(blocks),
                "transfer_bytes": served * len(blocks),
                "note": "scaled from the densest (central) block; stored gzipped, current objects only"}
    jobs = [(name, b) for b in blocks for name in collections]

    def one(job):
        name, b = job
        dest = root / "bgt" / name / f"{int(b[0])}_{int(b[3])}.geojson.gz"
        if ledger.done(dest):
            return dest.stat().st_size
        feats, pages = bgt_items(name, b)
        served = len(feats)
        # The API also serves ended object versions (eind_registratie): keep only current objects.
        feats = [f for f in feats if not (f.get("properties") or {}).get("eind_registratie")]
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".tmp")
        tmp.write_bytes(gzip.compress(json.dumps({"type": "FeatureCollection", "features": feats}).encode(), 6))
        tmp.replace(dest)
        url = f"{BGT_API}/collections/{name}/items?bbox={','.join(f'{v:.0f}' for v in b)}"
        ledger.record(dest, "bgt", url, collection=name, bbox_rd=list(b), features=len(feats),
                      served=served, pages=pages)
        return dest.stat().st_size

    with cf.ThreadPoolExecutor(4) as pool:
        sizes = list(pool.map(one, jobs))
    log(f"bgt  {len(jobs)} block layers, {sum(sizes) / 1e6:.0f} MB")
    return {"blocks": len(blocks), "collections": len(collections), "bytes": sum(sizes)}


# -- OSM ----------------------------------------------------------------------------------------------


def fetch_osm(profile, root, ledger, plan, log):
    status, headers, _ = _get(profile.osm_url, method="HEAD")
    size = int(headers.get("Content-Length", 0))
    if not plan:
        from turku3d.fetch import osm

        path = osm.fetch_finland(log=log)  # the city's Geofabrik snapshot (fetch.sources)
        return {"files": 1, "bytes": path.stat().st_size, "path": str(path)}
    return {"files": 1, "bytes": size}


def ortho_estimate(area, origin):
    """PDOK 8 cm orthophoto: draft chunks the generator fetches (512 px per 250 m tile, PNG->DEFLATE
    GeoTIFF ~0.6 MB) and, for reference, the full 8 cm resolution as JPEG GeoTIFF (~4 MB per 250 m)."""
    tiles = sum(4 for _ in area.blocks(origin))
    return {"tiles_250m": tiles, "bytes": tiles * 600_000, "bytes_full_8cm": tiles * 4_000_000}


# -- driver -------------------------------------------------------------------------------------------


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("city", choices=sorted(k for k, c in CITIES.items() if c.ground == "nl-ahn"))
    ap.add_argument("--bbox", help="xmin,ymin,xmax,ymax in RD New metres")
    ap.add_argument("--radius", type=float, help="metres around the city centre")
    ap.add_argument("--root", type=Path)
    ap.add_argument("--only", default="ahn,3dbag,bgt,osm")
    ap.add_argument("--plan", action="store_true", help="estimate sizes only")
    args = ap.parse_args(argv)
    profile = CITIES[args.city]
    if args.radius:
        cx, cy = profile.spawn_e, profile.spawn_n
        area = Area((cx - args.radius, cy - args.radius, cx + args.radius, cy + args.radius),
                    (cx, cy), args.radius)
    elif args.bbox:
        area = Area([float(v) for v in args.bbox.split(",")])
    else:
        area = Area(profile.sample_bbox)
    if not args.plan and os.environ.get("TURKU3D_CITY") != args.city:
        raise SystemExit(f"run through tools/cities/run.sh {args.city} (TURKU3D_CITY/TURKU3D_DATA)")
    data = Path(os.environ.get("TURKU3D_DATA", cities_root() / args.city))
    root = args.root or data / "raw" / "nl"
    root.mkdir(parents=True, exist_ok=True)
    ledger = Ledger(root)
    origin = (profile.origin_e, profile.origin_n)

    def log(*a):
        print(*a, file=sys.stderr, flush=True)

    report = {"city": args.city, "bbox_rd": list(area.bbox), "radius": args.radius, "plan": args.plan}
    steps = {
        "ahn": lambda: fetch_ahn(area, root, ledger, args.plan, log),
        "3dbag": lambda: fetch_3dbag(area, root, ledger, args.plan, log),
        "bgt": lambda: fetch_bgt(area, root, ledger, args.plan, log, origin),
        "osm": lambda: fetch_osm(profile, root, ledger, args.plan, log),
    }
    for name in args.only.split(","):
        started = time.monotonic()
        report[name] = steps[name]()
        report[name]["seconds"] = round(time.monotonic() - started, 1)
        log(f"{name}: {json.dumps(report[name])}")
    if args.plan:
        report["ortho"] = ortho_estimate(area, origin)
        report["total_bytes"] = sum(v["bytes"] for k, v in report.items() if isinstance(v, dict))
    print(json.dumps(report, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
