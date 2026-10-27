"""Coarse tile inputs, read from the shared raw cache only (``sources.py`` says where).

A build never downloads: when lidar, ortho or WFS data for the tile is not on disk yet,
``gather`` raises ``MissingInputs`` with the units the prefetcher has to fetch first.
``python -m turku3d.coarse build --fetch`` fetches them in the foreground for a hand-run build.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from turku3d import config as C

from .sources import (
    ORTHO_PX,
    POINT_MARGIN,
    WFS_LAYERS,
    MissingInputs,
    NoCoverage,
    Sources,
    covers,
)

__all__ = ["Inputs", "MissingInputs", "NoCoverage", "ORTHO_PX", "gather"]


@dataclass
class Inputs:
    points: np.ndarray  # (N,3) engine x, y, z ground key points (tile + POINT_MARGIN)
    ortho: np.ndarray  # (ORTHO_PX, ORTHO_PX, 3) uint8, row 0 = north edge of the tile
    buildings: Path  # bldg_Building_LOD2.parquet
    streets: Path | None  # GIS_Katualueet.parquet
    water: Path | None  # kanta_Vesikohde.parquet
    sources: dict  # provenance: source name -> path or area
    seconds: dict
    osm: Path | None = None  # OSM GeoParquet holding the tile (street graph)


def ground_points(tile: C.VisualTile, paths: list[Path]) -> np.ndarray:
    import laspy

    e0, n0, e1, n1 = tile.bbox.buffered(POINT_MARGIN).to_en().as_tuple()
    chunks = []
    for path in paths:
        las = laspy.read(path)
        e, n, h = np.asarray(las.x), np.asarray(las.y), np.asarray(las.z)
        keep = (e >= e0) & (e <= e1) & (n >= n0) & (n <= n1)
        x, y, z = C.en_to_local(e[keep], n[keep], h[keep])
        chunks.append(np.column_stack([x, y, z]))
    points = np.concatenate(chunks) if chunks else np.empty((0, 3))
    if len(points) < 16:
        raise NoCoverage(f"{tile.id}: only {len(points)} ground key points")
    return points


def read_ortho(path: Path, tile: C.VisualTile) -> np.ndarray:
    import rasterio
    from rasterio.enums import Resampling
    from rasterio.windows import from_bounds

    b = tile.bbox.to_en()
    with rasterio.open(path) as ds:
        bounds = (ds.bounds.left, ds.bounds.bottom, ds.bounds.right, ds.bounds.top)
        if not covers(bounds, tile):
            raise NoCoverage(f"{tile.id}: {path.name} does not cover the tile")
        window = from_bounds(b.emin, b.nmin, b.emax, b.nmax, ds.transform)
        rgb = ds.read(
            [1, 2, 3],
            window=window,
            out_shape=(3, ORTHO_PX, ORTHO_PX),
            resampling=Resampling.average,
        )
    return np.ascontiguousarray(np.moveaxis(rgb, 0, 2))


def gather(tile: C.VisualTile, sources: Sources | None = None) -> Inputs:
    sources = sources or Sources()
    missing = sources.missing(tile)
    if missing:
        raise MissingInputs(tile.id, [u.key for u in missing])
    seconds = {}
    t = time.perf_counter()
    laz_paths, _ = sources.lidar_paths(tile)
    points = ground_points(tile, laz_paths)
    seconds["points"] = time.perf_counter() - t
    t = time.perf_counter()
    ortho_source = sources.ortho_source(tile)
    rgb = read_ortho(ortho_source, tile)
    seconds["ortho"] = time.perf_counter() - t
    t = time.perf_counter()
    buildings, streets, water = (sources.wfs_source(name, tile)[1] for name in WFS_LAYERS)
    seconds["wfs"] = time.perf_counter() - t
    osm = sources.osm_source(tile)
    if buildings is None:
        raise NoCoverage(f"{tile.id}: no CityGML LOD2 coverage")
    provenance = {
        "maanpinta": [str(p) for p in laz_paths],
        "ortho": str(ortho_source),
        "buildings": str(buildings),
        "streets": str(streets) if streets else None,
        "water": str(water) if water else None,
        "osm": str(osm),
    }
    return Inputs(points, rgb, buildings, streets, water, provenance, seconds, osm)
