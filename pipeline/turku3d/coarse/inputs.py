"""Coarse tile inputs: cached shared raw data first, the read-through fetchers otherwise.

A tile inside an already prepared area (for example ``work-3x3``) reads that area's raw files and
never touches the network. Elsewhere the existing fetchers download only what one 250 m tile needs
into ``$TURKU3D_DATA/raw`` under the area name ``coarse-<tile>``: the ground key-point LAZ tiles
(``maanpinta``, about 0.5 MB each), the CityGML LOD2 buildings, street areas and water via WFS, and
one 1024 px true-ortho WMS request for the whole tile.
"""

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from turku3d import config as C

ORTHO_PX = 1024  # 24 cm/px over 250 m: one WMS request, fast KTX2 encode
POINT_MARGIN = 20.0
WFS_LAYERS = ("bldg:Building_LOD2", "GIS:Katualueet", "kanta:Vesikohde")
ORTHO_SLUG = "ortho2022to"


class NoCoverage(RuntimeError):
    """The tile lies outside every keyless source this producer can use."""


@dataclass
class Inputs:
    points: np.ndarray  # (N,3) engine x, y, z ground key points (tile + POINT_MARGIN)
    ortho: np.ndarray  # (ORTHO_PX, ORTHO_PX, 3) uint8, row 0 = north edge of the tile
    buildings: Path  # bldg_Building_LOD2.parquet
    streets: Path | None  # GIS_Katualueet.parquet
    water: Path | None  # kanta_Vesikohde.parquet
    sources: dict  # provenance: source name -> path or area
    seconds: dict


def coarse_area(tile: C.VisualTile) -> C.Area:
    return C.Area(f"coarse-{tile.id}", f"coarse tile {tile.id}", tile.bbox, margin=0.0)


def _covers(bbox_en, tile: C.VisualTile, margin=0.0) -> bool:
    t = tile.bbox.buffered(margin).to_en()
    return (
        bbox_en[0] <= t.emin
        and bbox_en[1] <= t.nmin
        and bbox_en[2] >= t.emax
        and bbox_en[3] >= t.nmax
    )


def _cached_wfs(slug: str, tile: C.VisualTile) -> Path | None:
    for summary in sorted(C.raw_dir("wfs").glob(f"*/{slug}.summary.json")):
        s = json.loads(summary.read_text())
        parquet = summary.with_name(f"{slug}.parquet")
        if s.get("status") in ("ok", "empty") and parquet.exists() and _covers(s["bbox_en"], tile):
            return parquet
    return None


def wfs_layer(name: str, tile: C.VisualTile, log=print) -> Path | None:
    from turku3d.fetch import wfs
    from turku3d.fetch.sources import WFS_LAYERS as ALL

    layer = next(layer for layer in ALL if layer.name == name)
    cached = _cached_wfs(layer.slug, tile)
    if cached:
        return cached
    s = wfs.fetch_layer(layer, coarse_area(tile), log=log)
    if s.get("status") == "empty":
        return None
    if s.get("status") != "ok":
        raise RuntimeError(f"WFS {name}: {s}")
    return C.raw_dir("wfs") / coarse_area(tile).name / f"{layer.slug}.parquet"


def ground_points(tile: C.VisualTile, log=print) -> tuple[np.ndarray, list[Path]]:
    import laspy

    from turku3d.fetch import laz

    bbox = tile.bbox.buffered(POINT_MARGIN)
    tiles = C.laz_tiles_for_bbox(bbox)
    listed = laz.known_tiles()
    tiles = [t for t in tiles if not listed or ("maanpinta", t.name) in listed]
    if not tiles:
        raise NoCoverage(
            f"{tile.id}: outside the city lidar index (laz-tiles.csv); the MML 2 m DEM needs an API key"
        )
    root = C.raw_dir("laz") / "maanpinta"
    missing = [t for t in tiles if not (root / f"{t.name}.laz").exists()]
    if missing:
        laz.fetch_tiles(missing, ("maanpinta",), log=log)
    paths = [root / f"{t.name}.laz" for t in tiles]
    chunks = []
    e0, n0, e1, n1 = bbox.to_en().as_tuple()
    for path in paths:
        las = laspy.read(path)
        e, n, h = np.asarray(las.x), np.asarray(las.y), np.asarray(las.z)
        keep = (e >= e0) & (e <= e1) & (n >= n0) & (n <= n1)
        x, y, z = C.en_to_local(e[keep], n[keep], h[keep])
        chunks.append(np.column_stack([x, y, z]))
    points = np.concatenate(chunks) if chunks else np.empty((0, 3))
    if len(points) < 16:
        raise NoCoverage(f"{tile.id}: only {len(points)} ground key points")
    return points, paths


def _read_rgb(path: Path, tile: C.VisualTile) -> np.ndarray | None:
    import rasterio
    from rasterio.enums import Resampling
    from rasterio.windows import from_bounds

    b = tile.bbox.to_en()
    with rasterio.open(path) as ds:
        if not _covers((ds.bounds.left, ds.bounds.bottom, ds.bounds.right, ds.bounds.top), tile):
            return None
        window = from_bounds(b.emin, b.nmin, b.emax, b.nmax, ds.transform)
        rgb = ds.read(
            [1, 2, 3],
            window=window,
            out_shape=(3, ORTHO_PX, ORTHO_PX),
            resampling=Resampling.average,
        )
    return np.ascontiguousarray(np.moveaxis(rgb, 0, 2))


def ortho(tile: C.VisualTile, log=print) -> tuple[np.ndarray, str]:
    from turku3d.fetch import wms
    from turku3d.fetch.sources import WMS_LAYERS

    root = C.raw_dir("ortho") / ORTHO_SLUG
    for vrt in sorted(root.glob("*.vrt")):
        rgb = _read_rgb(vrt, tile)
        if rgb is not None:
            return rgb, str(vrt)
    # One WMS GetMap: a single ORTHO_PX chunk aligned to O covers exactly this 250 m tile.
    layer = next(w for w in WMS_LAYERS if w.slug == ORTHO_SLUG)
    layer = dataclasses.replace(layer, px_m=tile.size / ORTHO_PX)
    area = coarse_area(tile)
    s = wms.fetch_layer(layer, area, workers=1, log=log, chunk_px=ORTHO_PX)
    if s["errors"] or not s["vrt"]:
        raise NoCoverage(f"{tile.id}: no true ortho ({s['errors'] or 'empty'})")
    rgb = _read_rgb(Path(s["vrt"]), tile)
    if rgb is None:
        raise NoCoverage(f"{tile.id}: true ortho does not cover the tile")
    return rgb, s["vrt"]


def gather(tile: C.VisualTile, log=print) -> Inputs:
    import time

    seconds = {}
    t = time.perf_counter()
    points, laz_paths = ground_points(tile, log)
    seconds["points"] = time.perf_counter() - t
    t = time.perf_counter()
    rgb, ortho_source = ortho(tile, log)
    seconds["ortho"] = time.perf_counter() - t
    t = time.perf_counter()
    buildings, streets, water = (wfs_layer(name, tile, log) for name in WFS_LAYERS)
    seconds["wfs"] = time.perf_counter() - t
    if buildings is None:
        raise NoCoverage(f"{tile.id}: no CityGML LOD2 coverage")
    sources = {
        "maanpinta": [str(p) for p in laz_paths],
        "ortho": ortho_source,
        "buildings": str(buildings),
        "streets": str(streets) if streets else None,
        "water": str(water) if water else None,
    }
    return Inputs(points, rgb, buildings, streets, water, sources, seconds)
