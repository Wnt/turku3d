"""Ground and vegetation rasters from the city's lidar, one small file per 500 m LAZ tile.

The point cloud is the only measured relief. Every kind that needs a height (ground, trees, wall bases) reads it
here, so that things meet. A raster file is derived data: it is rebuilt from the LAZ tile when `RECIPE` changes.

Layers, all on one grid of `CELL` metres whose cell (row, col) of the tile with south-west corner (E0, N0) is centred at
E0 + (col + 0.5) * CELL, N0 + (row + 0.5) * CELL:

- `ground`   N2000 height of the ground (median of ground returns; gaps under buildings and cars interpolated)
- `measured` whether the cell had a ground return
- `canopy`   height above ground of the highest high-vegetation return, 0 where none
- `shrub`    height above ground of the highest low or medium vegetation return, 0 where none
- `rgb`      mean colour of the ground returns (the scan's own imagery, April 2021, leaf-off)
- `intensity` median return intensity of the ground returns

A tile the survey does not have (the city's edge, open sea) is not an error: its ground continues level from the
nearest measured cell on the edge of a neighbouring tile, sea level where there is none, and nothing grows on it.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import numpy as np

from turku3d import config as C
from turku3d.world.sources import lidar as shared

CELL = 0.5
TILE = C.LAZ_TILE_SIZE
SIDE = int(TILE / CELL)
GROUND, LOW_VEG, MEDIUM_VEG, HIGH_VEG = 2, 3, 4, 5


def sources_root() -> Path:
    root = os.environ.get('TURKU3D_WORLD_DATA')
    return (Path(root).expanduser() if root else C.data_root() / 'world') / 'sources'


def tile_name(e0: float, n0: float) -> str:
    """The city names a LAZ tile by its north-west corner; (e0, n0) is its south-west corner."""
    return f'{int(e0)}_{int(n0 + TILE)}'


def tile_corner(name: str) -> tuple[float, float]:
    east, north = name.split('_')
    return float(east), float(north) - TILE


class NotSurveyed(FileNotFoundError):
    """The city's survey has no tile there."""


def laz_path(name: str) -> Path:
    """The LAZ tile (`sources.lidar.laz_path`); raises `NotSurveyed` where the survey has none."""
    path = shared.laz_path(name)
    if path is None:
        raise NotSurveyed(f'The survey has no lidar tile {name}')
    return path


def _cell_groups(index, order_key):
    """Sort returns by cell then by key; yield the sorted order, each cell id, its start and its count."""
    order = np.lexsort((order_key, index))
    cells, starts, counts = np.unique(index[order], return_index=True, return_counts=True)
    return order, cells, starts, counts


def _fill(grid, known):
    """Linear interpolation across gaps from the cells on their rim; nearest value beyond the hull."""
    from scipy.interpolate import LinearNDInterpolator
    from scipy.ndimage import binary_erosion, distance_transform_edt

    if known.all():
        return grid
    rim = known & ~binary_erosion(known, border_value=1)
    rows, cols = np.nonzero(rim)
    gap_rows, gap_cols = np.nonzero(~known)
    filled = grid.copy()
    if len(rows) >= 3:
        linear = LinearNDInterpolator(np.c_[rows, cols], grid[rows, cols])
        filled[gap_rows, gap_cols] = linear(np.c_[gap_rows, gap_cols])
    else:
        filled[gap_rows, gap_cols] = np.nan
    missing = np.isnan(filled)
    if missing.any():
        if not known.any():
            return np.zeros_like(grid)
        nearest = distance_transform_edt(~known, return_distances=False, return_indices=True)
        filled[missing] = grid[nearest[0][missing], nearest[1][missing]]
    return filled


def build(name: str) -> dict:
    """Rasterise one LAZ tile. About two seconds for 17 million returns."""
    e0, n0 = tile_corner(name)
    las = shared.read(laz_path(name))
    cls = np.asarray(las.classification)
    use = np.isin(cls, (GROUND, LOW_VEG, MEDIUM_VEG, HIGH_VEG))
    cls = cls[use]
    col = np.floor((np.asarray(las.x)[use] - e0) / CELL).astype(np.int64)
    row = np.floor((np.asarray(las.y)[use] - n0) / CELL).astype(np.int64)
    z = np.asarray(las.z)[use].astype(np.float32)
    inside = (col >= 0) & (col < SIDE) & (row >= 0) & (row < SIDE)
    index = row * SIDE + col

    ground_at = inside & (cls == GROUND)
    gi, gz = index[ground_at], z[ground_at]
    order, cells, starts, counts = _cell_groups(gi, gz)
    ground = np.full(SIDE * SIDE, np.nan, np.float32)
    ground[cells] = gz[order][starts + counts // 2]
    measured = ~np.isnan(ground)
    ground = _fill(ground.reshape(SIDE, SIDE), measured.reshape(SIDE, SIDE))

    intensity = np.zeros(SIDE * SIDE, np.uint16)
    raw_intensity = np.asarray(las.intensity)[use][ground_at]
    order_i, cells_i, starts_i, counts_i = _cell_groups(gi, raw_intensity)
    intensity[cells_i] = raw_intensity[order_i][starts_i + counts_i // 2]
    rgb = np.zeros((SIDE * SIDE, 3), np.uint8)
    if 'red' in las.point_format.dimension_names:
        hits = np.bincount(gi, minlength=SIDE * SIDE).astype(np.float64)
        for channel, colour in enumerate(('red', 'green', 'blue')):
            value = np.asarray(getattr(las, colour))[use][ground_at].astype(np.float64)
            total = np.bincount(gi, weights=value, minlength=SIDE * SIDE)
            rgb[:, channel] = np.clip(total / np.maximum(hits, 1) / 256.0, 0, 255).astype(np.uint8)

    def top(classes):
        at = inside & np.isin(cls, classes)
        height = np.zeros(SIDE * SIDE, np.float32)
        np.maximum.at(height, index[at], z[at] - ground.ravel()[index[at]])
        return np.clip(height * 100.0, 0, 65535).astype(np.uint16).reshape(SIDE, SIDE)

    return dict(ground=ground, measured=measured.reshape(SIDE, SIDE), canopy=top((HIGH_VEG,)),
                shrub=top((LOW_VEG, MEDIUM_VEG)), rgb=rgb.reshape(SIDE, SIDE, 3),
                intensity=intensity.reshape(SIDE, SIDE))


def unsurveyed(name: str) -> dict:
    """Rasters of a tile the survey lacks, decided per tile so that every window over it reads the same heights."""
    from scipy.ndimage import distance_transform_edt

    e0, n0 = tile_corner(name)
    ground = np.zeros((SIDE + 2, SIDE + 2), np.float32)
    known = np.zeros(ground.shape, bool)
    inner = slice(1, -1)
    # (east step, north step): where the neighbour's facing edge lies in the padded grid, and that edge.
    edges = {(-1, 0): ((inner, 0), lambda g: g[:, -1]), (1, 0): ((inner, -1), lambda g: g[:, 0]),
             (0, -1): ((0, inner), lambda g: g[-1, :]), (0, 1): ((-1, inner), lambda g: g[0, :])}
    for (east, north), (place, edge) in edges.items():
        near = tile_name(e0 + east * TILE, n0 + north * TILE)
        try:
            laz_path(near)
        except NotSurveyed:
            continue
        ground[place] = edge(tile(near)['ground'])
        known[place] = True
    if known.any():
        row, column = distance_transform_edt(~known, return_distances=False, return_indices=True)
        ground = ground[row, column]
    blank = np.zeros((SIDE, SIDE), np.uint16)
    return dict(ground=np.ascontiguousarray(ground[1:-1, 1:-1]), measured=np.zeros((SIDE, SIDE), bool), canopy=blank,
                shrub=blank, rgb=np.zeros((SIDE, SIDE, 3), np.uint8), intensity=blank)


# Names the rasters' content. Change it when `build` or `unsurveyed` would give other layers; an edit that leaves
# them as they are must not rasterise a city's worth of tiles again.
RECIPE = '53a8ebc55ca041e8'


def tile_path(name: str) -> Path:
    return sources_root() / 'lidar-rasters' / RECIPE / f'{name}.npz'


def tile_done(name: str) -> bool:
    return tile_path(name).exists()


@lru_cache(maxsize=12)
def tile(name: str) -> dict:
    """The rasters of one LAZ tile, built on first use and kept in the shared source cache."""
    path = tile_path(name)
    if path.exists():
        with np.load(path) as data:
            return {key: data[key] for key in data.files}
    try:
        layers = build(name)
    except NotSurveyed:
        layers = unsurveyed(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(f'{path.stem}.{os.getpid()}.part.npz')
    np.savez_compressed(part, **layers)
    part.replace(path)
    return layers


class Window:
    """Rasters over a survey-frame box, mosaicked from the tiles it touches, with bilinear sampling."""

    def __init__(self, e_min: float, n_min: float, e_max: float, n_max: float, layers=('ground',)):
        self.col0 = int(np.floor(e_min / CELL)) - 1
        self.row0 = int(np.floor(n_min / CELL)) - 1
        cols = int(np.ceil(e_max / CELL)) + 2 - self.col0
        rows = int(np.ceil(n_max / CELL)) + 2 - self.row0
        self.layers = {}
        e_tiles = range(int(np.floor(self.col0 * CELL / TILE)), int(np.floor((self.col0 + cols - 1) * CELL / TILE)) + 1)
        n_tiles = range(int(np.floor(self.row0 * CELL / TILE)), int(np.floor((self.row0 + rows - 1) * CELL / TILE)) + 1)
        for te in e_tiles:
            for tn in n_tiles:
                data = tile(tile_name(te * TILE, tn * TILE))
                c_from, r_from = te * SIDE - self.col0, tn * SIDE - self.row0
                c0, c1 = max(c_from, 0), min(c_from + SIDE, cols)
                r0, r1 = max(r_from, 0), min(r_from + SIDE, rows)
                for key in layers:
                    source = data[key]
                    target = self.layers.setdefault(key, np.zeros((rows, cols) + source.shape[2:], source.dtype))
                    target[r0:r1, c0:c1] = source[r0 - r_from:r1 - r_from, c0 - c_from:c1 - c_from]

    def position(self, e, n):
        """Fractional (row, col) of survey-frame points on the cell-centre grid."""
        return np.asarray(n, float) / CELL - 0.5 - self.row0, np.asarray(e, float) / CELL - 0.5 - self.col0

    def sample(self, key: str, e, n) -> np.ndarray:
        """Bilinear value of a layer at survey-frame points."""
        grid = self.layers[key]
        row, col = self.position(e, n)
        r0 = np.clip(np.floor(row).astype(int), 0, grid.shape[0] - 2)
        c0 = np.clip(np.floor(col).astype(int), 0, grid.shape[1] - 2)
        fr = np.clip(row - r0, 0.0, 1.0)
        fc = np.clip(col - c0, 0.0, 1.0)
        if grid.ndim == 3:
            fr, fc = fr[..., None], fc[..., None]
        return (grid[r0, c0] * (1 - fr) * (1 - fc) + grid[r0, c0 + 1] * (1 - fr) * fc
                + grid[r0 + 1, c0] * fr * (1 - fc) + grid[r0 + 1, c0 + 1] * fr * fc)

    def nearest(self, key: str, e, n) -> np.ndarray:
        grid = self.layers[key]
        row, col = self.position(e, n)
        return grid[np.clip(np.rint(row).astype(int), 0, grid.shape[0] - 1),
                    np.clip(np.rint(col).astype(int), 0, grid.shape[1] - 1)]


def ground_height(e, n) -> np.ndarray:
    """N2000 ground height at survey-frame points (any shape). The one terrain sampler of the world pipeline."""
    e, n = np.atleast_1d(np.asarray(e, float)), np.atleast_1d(np.asarray(n, float))
    window = Window(e.min(), n.min(), e.max(), n.max())
    return window.sample('ground', e, n)
