"""Vegetation returns of the city's lidar, reduced once per 500 m LAZ tile.

The scan (April 2021, leaf-off, about 70 returns per square metre) is the only measurement of every tree and hedge
in the city, private gardens included. A reduction keeps what planting needs and nothing else:

- every low, medium and high vegetation return (classes 3, 4, 5) at least `MIN_HEIGHT` above the ground, as
  centimetre offsets in the tile, with its height above ground, class, colour and whether it is a first return;
- `ground`: N2000 ground height on a 0.5 m grid (median of the ground returns, gaps filled from their rim);
- `roof`: height above ground of the highest building return per 0.5 m cell (0 where none);
- `hits`: the number of ground returns per 0.5 m cell (how much of the pulse reached the ground under a crown).

The returns are sorted into 31.25 m blocks so that a window read touches only its own blocks. Files live in the
shared source cache, `$TURKU3D_WORLD_DATA/sources/green/lidar-<RECIPE>/<tile>.npz`. A tile is named by its
north-west corner, as the city names it.

The survey ends at the city's edge: a tile the city's server does not have (HTTP 404) is unsurveyed, not an error.
It is remembered as `<tile>.unsurveyed` beside the fetched tiles, holds no returns, and a window that reaches into
it takes its ground from the nearest measured cells.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np

from ... import config

TILE = config.LAZ_TILE_SIZE
GRID = 0.5  # ground, roof and hits rasters
SIDE = int(TILE / GRID)
BLOCK = 31.25
BLOCKS = int(TILE / BLOCK)
MIN_HEIGHT = 0.2
MAX_HEIGHT = 45.0
GROUND, LOW, MEDIUM, HIGH, BUILDING = 2, 3, 4, 5, 6
# Names the reduction's content. Change it when `reduce` keeps something else; an edit that leaves the reduced
# layers as they are must not re-read a city's worth of tiles.
RECIPE = "fb10002f9940"


class Unsurveyed(FileNotFoundError):
    """The city's survey has no such tile."""


def sources_root() -> Path:
    root = os.environ.get("TURKU3D_WORLD_DATA")
    return (Path(root).expanduser() if root else config.data_root() / "world") / "sources"


def tile_name(e: float, n: float) -> str:
    """The LAZ tile that holds a survey point."""
    e0 = int(np.floor(e / TILE) * TILE)
    n0 = int(np.floor(n / TILE) * TILE)
    return f"{e0}_{n0 + int(TILE)}"


def tile_corner(name: str) -> tuple[float, float]:
    """South-west corner of a tile."""
    east, north = name.split("_")
    return float(east), float(north) - TILE


def laz_path(name: str) -> Path:
    """The LAZ tile: the read-only raw mirror first, else fetched once into the shared source cache."""
    fetched = sources_root() / "laz" / "pistepilvi" / f"{name}.laz"
    for path in (config.raw_dir("laz") / "pistepilvi" / f"{name}.laz", config.raw_dir("laz-3x3") / f"{name}.laz", fetched):
        if path.exists():
            return path
    missing = fetched.with_suffix(".unsurveyed")
    if missing.exists():
        raise Unsurveyed(name)
    fetched.parent.mkdir(parents=True, exist_ok=True)
    part = fetched.with_name(f"{fetched.name}.{os.getpid()}.part")
    url = config.LAZ_URL.format(dataset="pistepilvi", name=name)
    done = subprocess.run(["curl", "-L", "--fail", "--silent", "--show-error", "--max-time", "900", "-o", str(part), url],
                          capture_output=True, text=True, timeout=930)
    if done.returncode:
        part.unlink(missing_ok=True)
        if "returned error: 404" in done.stderr:
            missing.write_text(f"{url}\n{done.stderr.strip()}\n")
            raise Unsurveyed(name)
        raise FileNotFoundError(f"No lidar tile {name}: {done.stderr.strip()}")
    part.replace(fetched)
    return fetched


def fill(grid: np.ndarray, known: np.ndarray) -> np.ndarray:
    """Gaps take the value of the nearest measured cell, then a light blur inside the gaps removes the steps."""
    from scipy.ndimage import distance_transform_edt, uniform_filter

    if known.all() or not known.any():
        return np.where(known, grid, 0.0).astype(np.float32)
    nearest = distance_transform_edt(~known, return_distances=False, return_indices=True)
    filled = grid[nearest[0], nearest[1]]
    smooth = uniform_filter(filled, 5)
    return np.where(known, grid, smooth).astype(np.float32)


def reduce(name: str) -> dict[str, np.ndarray]:
    """Reads one LAZ tile (about a second) and reduces it (about two more)."""
    import laspy

    e0, n0 = tile_corner(name)
    las = laspy.read(laz_path(name))
    cls = np.asarray(las.classification)
    x, y, z = np.asarray(las.x), np.asarray(las.y), np.asarray(las.z)
    col = np.clip(np.floor((x - e0) / GRID).astype(np.int64), 0, SIDE - 1)
    row = np.clip(np.floor((y - n0) / GRID).astype(np.int64), 0, SIDE - 1)
    index = row * SIDE + col

    at = cls == GROUND
    gi, gz = index[at], z[at]
    order = np.lexsort((gz, gi))
    cells, starts, counts = np.unique(gi[order], return_index=True, return_counts=True)
    ground = np.full(SIDE * SIDE, np.nan, np.float32)
    ground[cells] = gz[order][starts + counts // 2]
    hits = np.zeros(SIDE * SIDE, np.uint16)
    hits[cells] = np.minimum(counts, 65535)
    known = ~np.isnan(ground)
    ground = fill(ground.reshape(SIDE, SIDE), known.reshape(SIDE, SIDE))

    at = cls == BUILDING
    roof = np.zeros(SIDE * SIDE, np.float32)
    np.maximum.at(roof, index[at], z[at] - ground.ravel()[index[at]])

    at = (cls >= LOW) & (cls <= HIGH)
    height = z[at] - ground.ravel()[index[at]]
    keep = (height >= MIN_HEIGHT) & (height <= MAX_HEIGHT)
    at = np.flatnonzero(at)[keep]
    height = height[keep]
    east = np.clip(np.rint((x[at] - e0) * 100), 0, 49999).astype(np.uint16)
    north = np.clip(np.rint((y[at] - n0) * 100), 0, 49999).astype(np.uint16)
    block = (north // int(BLOCK * 100)).astype(np.int64) * BLOCKS + east // int(BLOCK * 100)
    order = np.argsort(block, kind="stable")
    at = at[order]
    if "red" in las.point_format.dimension_names:
        rgb = np.stack([np.asarray(getattr(las, c))[at] >> 8 for c in ("red", "green", "blue")], axis=1).astype(np.uint8)
    else:
        rgb = np.zeros((len(at), 3), np.uint8)
    return {
        "ground": ground, "roof": roof.reshape(SIDE, SIDE).astype(np.float16), "hits": hits.reshape(SIDE, SIDE),
        "east": east[order], "north": north[order], "height": np.rint(height[order] * 100).astype(np.uint16),
        "cls": cls[at].astype(np.uint8), "rgb": rgb, "first": np.asarray(las.return_number)[at] == 1,
        "starts": np.searchsorted(block[order], np.arange(BLOCKS * BLOCKS + 1)).astype(np.int64),
    }


@lru_cache(maxsize=12)
def tile(name: str) -> dict[str, np.ndarray] | None:
    """The reduction of a tile, or None where the city was not surveyed."""
    path = sources_root() / "green" / f"lidar-{RECIPE}" / f"{name}.npz"
    if path.exists():
        with np.load(path) as data:
            return {key: data[key] for key in data.files}
    try:
        layers = reduce(name)
    except Unsurveyed:
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(f"{path.stem}.{os.getpid()}.part.npz")
    np.savez_compressed(part, **layers)
    part.replace(path)
    return layers


@dataclass
class Returns:
    """Vegetation returns of a survey box."""

    e: np.ndarray  # float64 EPSG:3877
    n: np.ndarray
    h: np.ndarray  # float32 metres above ground
    cls: np.ndarray
    rgb: np.ndarray
    first: np.ndarray


class Window:
    """Everything the reductions hold for one survey box (e0, n0, e1, n1), mosaicked over the tiles it touches."""

    def __init__(self, box: tuple[float, float, float, float]):
        self.box = box
        self.col0 = int(np.floor(box[0] / GRID))
        self.row0 = int(np.floor(box[1] / GRID))
        cols = int(np.ceil(box[2] / GRID)) - self.col0
        rows = int(np.ceil(box[3] / GRID)) - self.row0
        self.ground = np.zeros((rows, cols), np.float32)
        self.roof = np.zeros((rows, cols), np.float32)
        self.hits = np.zeros((rows, cols), np.uint16)
        known = np.zeros((rows, cols), bool)
        parts = [Returns(np.zeros(0), np.zeros(0), np.zeros(0, np.float32), np.zeros(0, np.uint8), np.zeros((0, 3), np.uint8),
                         np.zeros(0, bool))]
        for te in range(int(np.floor(box[0] / TILE)), int(np.floor((box[2] - 1e-6) / TILE)) + 1):
            for tn in range(int(np.floor(box[1] / TILE)), int(np.floor((box[3] - 1e-6) / TILE)) + 1):
                data = tile(f"{int(te * TILE)}_{int((tn + 1) * TILE)}")
                if data is None:
                    continue
                c_from, r_from = te * SIDE - self.col0, tn * SIDE - self.row0
                c0, c1, r0, r1 = max(c_from, 0), min(c_from + SIDE, cols), max(r_from, 0), min(r_from + SIDE, rows)
                for key in ("ground", "roof", "hits"):
                    getattr(self, key)[r0:r1, c0:c1] = data[key][r0 - r_from:r1 - r_from, c0 - c_from:c1 - c_from]
                known[r0:r1, c0:c1] = True
                parts.append(self._returns(data, te * TILE, tn * TILE))
        self.surveyed = bool(known.any())  # False: the whole box lies outside the survey
        if self.surveyed and not known.all():
            self.ground = fill(self.ground, known)
        self.returns = Returns(*(np.concatenate([getattr(p, key) for p in parts])
                                 for key in ("e", "n", "h", "cls", "rgb", "first")))

    def _returns(self, data, e0: float, n0: float) -> Returns:
        box, starts = self.box, data["starts"]
        b0 = int(np.clip(np.floor((box[0] - e0) / BLOCK), 0, BLOCKS - 1))
        b1 = int(np.clip(np.floor((box[2] - e0) / BLOCK), 0, BLOCKS - 1))
        rows = range(int(np.clip(np.floor((box[1] - n0) / BLOCK), 0, BLOCKS - 1)),
                     int(np.clip(np.floor((box[3] - n0) / BLOCK), 0, BLOCKS - 1)) + 1)
        index = np.concatenate([np.arange(starts[r * BLOCKS + b0], starts[r * BLOCKS + b1 + 1]) for r in rows])
        e = e0 + data["east"][index] / 100.0
        n = n0 + data["north"][index] / 100.0
        keep = (e >= box[0]) & (e < box[2]) & (n >= box[1]) & (n < box[3])
        index = index[keep]
        return Returns(e[keep], n[keep], data["height"][index].astype(np.float32) / 100.0, data["cls"][index],
                       data["rgb"][index], data["first"][index])

    def sample(self, grid: np.ndarray, e, n) -> np.ndarray:
        """Bilinear value of one of the 0.5 m rasters at survey points."""
        row = np.asarray(n, float) / GRID - 0.5 - self.row0
        col = np.asarray(e, float) / GRID - 0.5 - self.col0
        r0 = np.clip(np.floor(row).astype(int), 0, grid.shape[0] - 2)
        c0 = np.clip(np.floor(col).astype(int), 0, grid.shape[1] - 2)
        fr, fc = np.clip(row - r0, 0.0, 1.0), np.clip(col - c0, 0.0, 1.0)
        return (grid[r0, c0] * (1 - fr) * (1 - fc) + grid[r0, c0 + 1] * (1 - fr) * fc
                + grid[r0 + 1, c0] * fr * (1 - fc) + grid[r0 + 1, c0 + 1] * fr * fc)

    def ground_height(self, e, n) -> np.ndarray:
        return self.sample(self.ground, e, n)
