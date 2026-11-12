"""Orthophoto pixels over any box, at a chosen ground resolution.

A read is assembled from square chunks on the origin-aligned grid, CHUNK pixels a side at one of LEVELS (metres per
pixel), cached as RGBA PNG (alpha 0: no imagery) under sources/ortho/<layer>/<cm>cm/<i>_<j>.png. A chunk is resampled
from a mosaic of the read-only raw mirror when one covers it, and requested from the city's WMS otherwise.
"""

from __future__ import annotations

import io
import math
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

import numpy as np

from ... import config
from ...fetch import wms
from ...fetch.common import http_get
from ...fetch.sources import WMS_LAYERS, WMS_URL

LEVELS = (0.05, 0.1, 0.2, 0.4, 0.8)
CHUNK = 1024
LAYERS = {layer.slug: layer for layer in WMS_LAYERS}
DEFAULT = WMS_LAYERS[0].slug


@dataclass
class Raster:
    rgb: np.ndarray  # (rows, columns, 3) uint8; row 0 is the north edge
    valid: np.ndarray  # (rows, columns) bool: imagery exists
    e0: float  # west edge
    n1: float  # north edge
    px_m: float

    def at(self, e, n) -> tuple[np.ndarray, np.ndarray]:
        """Colours and validity at survey points (nearest pixel); points outside the raster are invalid."""
        column = np.floor((np.asarray(e) - self.e0) / self.px_m).astype(int)
        row = np.floor((self.n1 - np.asarray(n)) / self.px_m).astype(int)
        inside = (row >= 0) & (row < self.rgb.shape[0]) & (column >= 0) & (column < self.rgb.shape[1])
        row, column = np.clip(row, 0, self.rgb.shape[0] - 1), np.clip(column, 0, self.rgb.shape[1] - 1)
        return self.rgb[row, column], self.valid[row, column] & inside


class Ortho:
    def __init__(self, cache: Path, sources):
        self.cache = cache
        self.sources = sources
        self.fetched = 0

    @cached_property
    def mosaics(self) -> dict[str, list[tuple[tuple[float, float, float, float], Path]]]:
        """Raw mosaics by layer: (survey box, VRT)."""
        import rasterio

        found: dict[str, list] = {}
        for slug in LAYERS:
            folder = config.raw_dir("ortho") / slug
            for path in sorted(folder.glob("*.vrt")) if folder.is_dir() else []:
                try:
                    with rasterio.open(path) as mosaic:
                        found.setdefault(slug, []).append((tuple(mosaic.bounds), path))
                except rasterio.errors.RasterioIOError:
                    continue
        return found

    def chunk(self, slug: str, px_m: float, i: int, j: int) -> np.ndarray:
        """One chunk as (CHUNK, CHUNK, 4) uint8."""
        from PIL import Image

        path = self.cache / slug / f"{round(px_m * 100)}cm" / f"{i}_{j}.png"
        if path.exists():
            return np.asarray(Image.open(path).convert("RGBA"))
        side = CHUNK * px_m
        box = (config.ORIGIN_E + i * side, config.ORIGIN_N - (j + 1) * side, config.ORIGIN_E + (i + 1) * side, config.ORIGIN_N - j * side)
        pixels = self.from_raw(slug, box) if px_m >= LAYERS[slug].px_m else None
        if pixels is None:
            body, _ = http_get(WMS_URL, wms.getmap_params(LAYERS[slug].name, box, CHUNK, CHUNK), headers=wms.REFERER)
            rgb, valid = wms.decode_png(body)
            pixels = np.dstack([rgb, np.where(valid, 255, 0).astype(np.uint8)])
            self.fetched += 1
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.tmp")
        buffer = io.BytesIO()
        Image.fromarray(pixels, "RGBA").save(buffer, "PNG")
        tmp.write_bytes(buffer.getvalue())
        tmp.replace(path)
        return pixels

    def from_raw(self, slug: str, box) -> np.ndarray | None:
        import rasterio
        from rasterio.enums import Resampling
        from rasterio.windows import from_bounds

        for cover, path in self.mosaics.get(slug, []):
            if cover[0] <= box[0] and cover[1] <= box[1] and cover[2] >= box[2] and cover[3] >= box[3]:
                with rasterio.open(path) as mosaic:
                    window = from_bounds(*box, transform=mosaic.transform)
                    data = mosaic.read(indexes=[1, 2, 3], window=window, out_shape=(3, CHUNK, CHUNK), resampling=Resampling.average)
                    mask = mosaic.read_masks(1, window=window, out_shape=(CHUNK, CHUNK), resampling=Resampling.nearest)
                return np.dstack([np.moveaxis(data, 0, 2), mask]).astype(np.uint8)
        return None

    def image(self, box: tuple[float, float, float, float], px_m: float = 0.2, layer: str = DEFAULT) -> Raster:
        """The orthophoto over the survey box (e0, n0, e1, n1) at the level nearest to `px_m`."""
        with self.sources.timed(f"ortho:{layer}"):
            px_m = min(LEVELS, key=lambda level: abs(math.log(level / px_m)))
            side = CHUNK * px_m
            i0, i1 = math.floor(round((box[0] - config.ORIGIN_E) / side, 9)), math.ceil(round((box[2] - config.ORIGIN_E) / side, 9))
            j0, j1 = math.floor(round((config.ORIGIN_N - box[3]) / side, 9)), math.ceil(round((config.ORIGIN_N - box[1]) / side, 9))
            cells = [(i, j) for j in range(j0, j1) for i in range(i0, i1)]
            with ThreadPoolExecutor(max_workers=6) as pool:  # the requests wait on the server, not on this machine
                chunks = dict(zip(cells, pool.map(lambda cell: self.chunk(layer, px_m, *cell), cells)))
            pixels = np.vstack([np.hstack([chunks[i, j] for i in range(i0, i1)]) for j in range(j0, j1)])
            e0, n1 = config.ORIGIN_E + i0 * side, config.ORIGIN_N - j0 * side
            left, top = math.floor(round((box[0] - e0) / px_m, 6)), math.floor(round((n1 - box[3]) / px_m, 6))
            right, bottom = math.ceil(round((box[2] - e0) / px_m, 6)), math.ceil(round((n1 - box[1]) / px_m, 6))
            pixels = pixels[top:bottom, left:right]
            return Raster(np.ascontiguousarray(pixels[..., :3]), pixels[..., 3] > 0, e0 + left * px_m, n1 - top * px_m, px_m)
