"""Roof pigments of distant survey shells from the orthophoto the HD roofs are cut from.

The scenery tier's survey shells (250 m survey cells, ``horizon.shells``; 1 km nodes,
``horizon_nodes.survey_shells``) painted every roof with one grey-green pigment (``blocks.ROOF``), so light,
red and brown roofs changed colour where a flier's view refines from HD tiles to scenery and between scenery
levels. HD roofs are crops of the true orthophoto (``coarse.stages``, ``roof_ortho``); this samples the same
mosaic at about 2 m per pixel and gives each building's roof its area-weighted mean, in linear RGB, as the HD
roof's colour seen from afar. Black or white nodata pixels never count; a roof without imagery keeps ROOF.
"""

from __future__ import annotations

import math
from functools import lru_cache

import numpy as np

from turku3d import config as C

PIXELS = 128  # per 250 m tile: about 2 m per pixel, far below the scenery tiers' detail
ROOF = (0.20, 0.23, 0.22)  # coarse.blocks.ROOF: kept where no imagery covers a roof


@lru_cache(maxsize=64)
def _leaf(path: str, tile_id: str) -> np.ndarray | None:
    """The tile's orthophoto as (PIXELS, PIXELS, 3) uint8, north row first, or None without coverage."""
    import rasterio
    from rasterio.enums import Resampling
    from rasterio.windows import from_bounds

    tile = C.VisualTile.parse(tile_id)
    b = tile.bbox.to_en()
    try:
        with rasterio.open(path) as ds:
            window = from_bounds(b.emin, b.nmin, b.emax, b.nmax, ds.transform)
            rgb = ds.read([1, 2, 3], window=window, out_shape=(3, PIXELS, PIXELS), resampling=Resampling.average,
                          boundless=True, fill_value=0)
    except Exception:  # noqa: BLE001 - an unreadable mosaic is no imagery
        return None
    return np.ascontiguousarray(np.moveaxis(rgb, 0, 2))


def _linear(srgb: np.ndarray) -> np.ndarray:
    c = srgb / 255.0
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


class RoofPigments:
    """Roof means over the orthophoto mosaics of level-0 tiles: ``ortho`` maps tile id -> mosaic path."""

    def __init__(self, ortho: dict[str, str] | None):
        self.ortho = dict(sorted((ortho or {}).items()))

    def pixel(self, x: float, z: float) -> np.ndarray | None:
        size = C.visual_tile_size(0)
        i, j = math.floor(x / size), math.floor(z / size)
        tile_id = f"t0_{i}_{j}"
        path = self.ortho.get(tile_id)
        if path is None:
            return None
        image = _leaf(path, tile_id)
        if image is None:
            return None
        # Local x runs east, z south; the raster's row 0 is the tile's north edge (z = j * size).
        u = min(PIXELS - 1, max(0, int((x - i * size) / size * PIXELS)))
        v = min(PIXELS - 1, max(0, int((z - j * size) / size * PIXELS)))
        rgb = image[v, u].astype(float)
        if rgb.max() <= 3 or rgb.min() >= 252:
            return None
        return rgb

    def mean(self, points: np.ndarray, faces: np.ndarray) -> tuple[float, float, float] | None:
        """Area-weighted mean linear colour under triangles (``points`` local x, y, z), sampled at centroids."""
        if not self.ortho or not len(faces):
            return None
        p = np.asarray(points, float)
        tri = p[np.asarray(faces, np.int64)]
        area = np.linalg.norm(np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0]), axis=1) / 2
        total, weight = np.zeros(3), 0.0
        for centre, a in zip(tri.mean(axis=1), area):
            if a <= 0:
                continue
            rgb = self.pixel(centre[0], centre[2])
            if rgb is None:
                continue
            total += _linear(rgb) * a
            weight += a
        return tuple(float(round(v, 5)) for v in total / weight) if weight > 0 else None


def ortho_inputs(sources, tiles) -> dict[str, str]:
    """Mosaic path per level-0 tile with imagery (``Sources.ortho_source``), for a scenery row's inputs."""
    out = {}
    for tile in tiles:
        try:
            path = sources.ortho_source(tile)
        except Exception:  # noqa: BLE001 - NoCoverage or no fetched imagery
            path = None
        if path:
            out[tile.id] = str(path)
    return dict(sorted(out.items()))
