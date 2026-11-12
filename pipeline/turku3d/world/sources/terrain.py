"""Ground heights from the city's lidar ground key points (`maanpinta`, 500 m LAZ tiles).

Each tile becomes a 1 m height grid once (sources/dem/maanpinta-1m/<xmin>_<ymax>.npy): a linear interpolation of
the tile's points and those within 30 m in its neighbours, so that adjoining tiles agree at their shared edge.
Raw tiles are read from $TURKU3D_DATA/raw/laz/maanpinta and fetched into the cache when missing.
"""

from __future__ import annotations

import urllib.error
from pathlib import Path

import numpy as np

from ... import config
from ...fetch.common import http_get

TILE = 500.0
STEP = 1.0
NODES = int(TILE / STEP) + 1
BUFFER = 30.0
URL = "https://turku.asiointi.fi/3d/maanpinta/{name}.laz"


class Terrain:
    def __init__(self, cache: Path, sources):
        self.cache = cache
        self.sources = sources
        self.grids: dict[tuple[int, int], np.ndarray | None] = {}
        self.points: dict[tuple[int, int], np.ndarray | None] = {}

    def laz(self, tile: tuple[int, int]) -> Path | None:
        """The key-point LAZ of the tile whose north-west corner is (xmin, ymax); None where the city has none."""
        name = f"{tile[0]}_{tile[1]}"
        raw = config.raw_dir("laz") / "maanpinta" / f"{name}.laz"
        if raw.exists():
            return raw
        cached = self.cache / "laz" / "maanpinta" / f"{name}.laz"
        missing = cached.with_suffix(".missing")
        if cached.exists():
            return cached
        if missing.exists():
            return None
        cached.parent.mkdir(parents=True, exist_ok=True)
        try:
            body, _ = http_get(URL.format(name=name))
        except urllib.error.HTTPError as error:
            if error.code != 404:
                raise
            missing.write_text("404\n")
            return None
        tmp = cached.with_suffix(".tmp")
        tmp.write_bytes(body)
        tmp.replace(cached)
        self.sources.log(f"  fetched lidar ground tile {name}: {len(body) // 1024} KiB")
        return cached

    def tile_points(self, tile: tuple[int, int]) -> np.ndarray | None:
        if tile not in self.points:
            import laspy

            path = self.laz(tile)
            if path is None:
                self.points[tile] = None
            else:
                las = laspy.read(path)
                self.points[tile] = np.column_stack([np.asarray(las.x), np.asarray(las.y), np.asarray(las.z)])
        return self.points[tile]

    def grid(self, tile: tuple[int, int]) -> np.ndarray | None:
        """Heights at the tile's 1 m nodes, row 0 at its south edge: grid[row, column] = H(xmin + c, ymin + r)."""
        if tile in self.grids:
            return self.grids[tile]
        path = self.cache / "dem" / "maanpinta-1m" / f"{tile[0]}_{tile[1]}.npy"
        if path.exists():
            self.grids[tile] = np.load(path)
            return self.grids[tile]
        own = self.tile_points(tile)
        if own is None:
            self.grids[tile] = None
            return None
        from scipy.interpolate import LinearNDInterpolator, NearestNDInterpolator

        xmin, ymax = tile
        ymin = ymax - TILE
        clouds = [own]
        for dx in (-TILE, 0.0, TILE):
            for dy in (-TILE, 0.0, TILE):
                if dx or dy:
                    near = self.tile_points((int(xmin + dx), int(ymax + dy)))
                    if near is not None:
                        keep = ((near[:, 0] > xmin - BUFFER) & (near[:, 0] < xmin + TILE + BUFFER)
                                & (near[:, 1] > ymin - BUFFER) & (near[:, 1] < ymax + BUFFER))
                        clouds.append(near[keep])
        points = np.concatenate(clouds)
        gx, gy = np.meshgrid(xmin + np.arange(NODES) * STEP, ymin + np.arange(NODES) * STEP)
        heights = LinearNDInterpolator(points[:, :2], points[:, 2])(gx, gy)
        holes = np.isnan(heights)
        if holes.any():
            heights[holes] = NearestNDInterpolator(points[:, :2], points[:, 2])(gx[holes], gy[holes])
        grid = heights.astype(np.float32)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp.npy")
        np.save(tmp, grid)
        tmp.replace(path)
        self.grids[tile] = grid
        return grid

    def heights(self, e, n) -> np.ndarray:
        """N2000 ground heights at survey points (bilinear in the 1 m grid); NaN where the city has no lidar."""
        with self.sources.timed("terrain"):
            e, n = np.atleast_1d(np.asarray(e, dtype=np.float64)), np.atleast_1d(np.asarray(n, dtype=np.float64))
            out = np.full(e.shape, np.nan)
            tx = (np.floor(e / TILE) * TILE).astype(np.int64)
            ty = (np.floor(n / TILE) * TILE + TILE).astype(np.int64)
            for xmin, ymax in {(int(a), int(b)) for a, b in zip(tx.ravel(), ty.ravel())}:
                grid = self.grid((xmin, ymax))
                if grid is None:
                    continue
                pick = (tx == xmin) & (ty == ymax)
                u, v = (e[pick] - xmin) / STEP, (n[pick] - (ymax - TILE)) / STEP
                c, r = np.clip(np.floor(u).astype(int), 0, NODES - 2), np.clip(np.floor(v).astype(int), 0, NODES - 2)
                fu, fv = u - c, v - r
                out[pick] = (grid[r, c] * (1 - fu) * (1 - fv) + grid[r, c + 1] * fu * (1 - fv)
                             + grid[r + 1, c] * (1 - fu) * fv + grid[r + 1, c + 1] * fu * fv)
            return out
