"""Single source of truth: origin, CRS, tile grids, areas of interest, transforms, paths.

Conventions (docs/ARCHITECTURE.md D3/D4, docs/specs/tiles.md):

* Source CRS: EPSG:3877 (ETRS89 / GK23FIN) horizontal + N2000 (EPSG:3900) heights.
  The LAZ files carry no CRS VLR; this is assumed and consistent with WFS/WMS.
* World origin O = (E 23459500, N 6704500, H 0), the NW corner of LAZ tile 23459500_6704500.
* Engine ("local") axes, glTF Y-up, metres:
      x = E - E0        (east)
      y = H             (N2000 height, not offset)
      z = -(N - N0)     (south is +z)
* Tile grids are aligned to O. Index i grows east, j grows south:
      i = floor(x / size), j = floor(z / size)
  A tile covers x in [i*s, (i+1)*s), z in [j*s, (j+1)*s); its local origin is the corner
  (i*s, 0, j*s), which is the NW corner in map terms (E0 + i*s, N0 - j*s).
* Visual tiles: level L has size 250 * 2**L m, id ``t{L}_{i}_{j}`` (L0 = 250 m).
* Collision tiles: 62.5 m, id ``c_{i}_{j}`` (16 per L0 visual tile).
* LAZ tiles: 500 m city grid (== visual level 1), name ``<xmin>_<ymax>`` in EPSG:3877 integers.

All functions accept Python floats or numpy arrays where it makes sense (plain arithmetic).
"""

from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

# --------------------------------------------------------------------------------------
# CRS and origin
# --------------------------------------------------------------------------------------

CRS_HORIZONTAL = "EPSG:3877"  # ETRS89 / GK23FIN
CRS_VERTICAL = "EPSG:3900"  # N2000 height
CRS_COMPOUND = "EPSG:3877+3900"
CRS_WGS84 = "EPSG:4326"

ORIGIN_E = 23459500.0
ORIGIN_N = 6704500.0
ORIGIN_H = 0.0
ORIGIN = (ORIGIN_E, ORIGIN_N, ORIGIN_H)

# Office: Läntinen Rantakatu 15 (ARCHITECTURE.md D3, research/ai-textures.md).
OFFICE_E = 23459668.0
OFFICE_N = 6704254.0
OFFICE_LATLON = (60.44839, 22.26726)

# --------------------------------------------------------------------------------------
# Tile sizes
# --------------------------------------------------------------------------------------

VISUAL_TILE_SIZE = 250.0  # level 0
VISUAL_MAX_LEVEL = 3  # 250 m, 500 m, 1 km, 2 km
COLLISION_TILE_SIZE = 62.5
LAZ_TILE_SIZE = 500.0
COLLISION_PER_VISUAL = int(VISUAL_TILE_SIZE / COLLISION_TILE_SIZE)  # 4 -> 4x4 = 16
MESH_MARGIN = 50.0  # CDT buffer around a tile before clipping (D4)

assert ORIGIN_E % LAZ_TILE_SIZE == 0 and ORIGIN_N % LAZ_TILE_SIZE == 0


def visual_tile_size(level: int) -> float:
    if not 0 <= level <= VISUAL_MAX_LEVEL:
        raise ValueError(f"visual level {level} out of range 0..{VISUAL_MAX_LEVEL}")
    return VISUAL_TILE_SIZE * (2**level)


# --------------------------------------------------------------------------------------
# local <-> EPSG:3877
# --------------------------------------------------------------------------------------


def en_to_local(e, n, h=0.0):
    """EPSG:3877 (E, N, H N2000) -> engine (x, y, z)."""
    return e - ORIGIN_E, h - ORIGIN_H, -(n - ORIGIN_N)


def local_to_en(x, y, z):
    """Engine (x, y, z) -> EPSG:3877 (E, N, H N2000)."""
    return x + ORIGIN_E, -z + ORIGIN_N, y + ORIGIN_H


# --------------------------------------------------------------------------------------
# EPSG:3877 <-> WGS84 (horizontal only; H stays N2000 orthometric)
# --------------------------------------------------------------------------------------


@lru_cache(maxsize=None)
def _transformer(src: str, dst: str):
    from pyproj import Transformer  # imported lazily so pure-grid users need no pyproj

    return Transformer.from_crs(src, dst, always_xy=True)


def en_to_wgs84(e, n):
    """EPSG:3877 (E, N) -> (lat, lon) degrees. Heights are not converted (N2000 != ellipsoidal;
    the geoid in Turku is ~18 m; use a FIN2005N00 geoid grid if ellipsoidal heights are needed)."""
    lon, lat = _transformer(CRS_HORIZONTAL, CRS_WGS84).transform(e, n)
    return lat, lon


def wgs84_to_en(lat, lon):
    """(lat, lon) degrees -> EPSG:3877 (E, N)."""
    return _transformer(CRS_WGS84, CRS_HORIZONTAL).transform(lon, lat)


def grid_bearing_of_true_north(e, n) -> float:
    """Grid bearing (deg, clockwise from EPSG:3877 grid north) of TRUE north at (E, N): the meridian
    convergence, +0.640 deg at O (Turku is west of the GK23 central meridian). A true azimuth (sun, compass,
    Street View / Look Around heading) becomes a grid/engine bearing by adding this."""
    from pyproj import Proj

    lat, lon = en_to_wgs84(e, n)
    return -float(Proj(CRS_HORIZONTAL).get_factors(lon, lat).meridian_convergence)


def true_to_grid_bearing(e, n, bearing_true: float) -> float:
    return (bearing_true + grid_bearing_of_true_north(e, n)) % 360.0


def local_to_wgs84(x, z):
    e, n, _ = local_to_en(x, 0.0, z)
    return en_to_wgs84(e, n)


def wgs84_to_local(lat, lon):
    """(lat, lon) -> engine (x, z)."""
    e, n = wgs84_to_en(lat, lon)
    x, _, z = en_to_local(e, n)
    return x, z


# --------------------------------------------------------------------------------------
# Bounding boxes
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class BBoxEN:
    """Axis-aligned box in EPSG:3877 metres."""

    emin: float
    nmin: float
    emax: float
    nmax: float

    def to_local(self) -> "BBoxLocal":
        return BBoxLocal(
            self.emin - ORIGIN_E, ORIGIN_N - self.nmax, self.emax - ORIGIN_E, ORIGIN_N - self.nmin
        )

    def buffered(self, m: float) -> "BBoxEN":
        return BBoxEN(self.emin - m, self.nmin - m, self.emax + m, self.nmax + m)

    def as_tuple(self):
        return (self.emin, self.nmin, self.emax, self.nmax)


@dataclass(frozen=True)
class BBoxLocal:
    """Axis-aligned box in engine x/z metres (xmin <= xmax, zmin <= zmax)."""

    xmin: float
    zmin: float
    xmax: float
    zmax: float

    def to_en(self) -> BBoxEN:
        return BBoxEN(
            ORIGIN_E + self.xmin, ORIGIN_N - self.zmax, ORIGIN_E + self.xmax, ORIGIN_N - self.zmin
        )

    def buffered(self, m: float) -> "BBoxLocal":
        return BBoxLocal(self.xmin - m, self.zmin - m, self.xmax + m, self.zmax + m)

    def contains(self, x: float, z: float) -> bool:
        return self.xmin <= x < self.xmax and self.zmin <= z < self.zmax

    def as_tuple(self):
        return (self.xmin, self.zmin, self.xmax, self.zmax)


# --------------------------------------------------------------------------------------
# Generic grid
# --------------------------------------------------------------------------------------


def grid_index(x: float, z: float, size: float) -> tuple[int, int]:
    return math.floor(x / size), math.floor(z / size)


def grid_bbox(i: int, j: int, size: float) -> BBoxLocal:
    return BBoxLocal(i * size, j * size, (i + 1) * size, (j + 1) * size)


def grid_range(bbox: BBoxLocal, size: float) -> list[tuple[int, int]]:
    """All (i, j) cells of `size` that intersect the open interior of `bbox` (row-major, j then i)."""
    i0, j0 = math.floor(bbox.xmin / size), math.floor(bbox.zmin / size)
    i1, j1 = math.ceil(bbox.xmax / size) - 1, math.ceil(bbox.zmax / size) - 1
    return [(i, j) for j in range(j0, j1 + 1) for i in range(i0, i1 + 1)]


# --------------------------------------------------------------------------------------
# Visual tiles  t{level}_{i}_{j}
# --------------------------------------------------------------------------------------

_VISUAL_RE = re.compile(r"^t(\d+)_(-?\d+)_(-?\d+)$")
_COLLISION_RE = re.compile(r"^c_(-?\d+)_(-?\d+)$")
_LAZ_RE = re.compile(r"^(\d+)_(\d+)$")


@dataclass(frozen=True, order=True)
class VisualTile:
    level: int
    i: int
    j: int

    @property
    def id(self) -> str:
        return f"t{self.level}_{self.i}_{self.j}"

    @property
    def size(self) -> float:
        return visual_tile_size(self.level)

    @property
    def bbox(self) -> BBoxLocal:
        return grid_bbox(self.i, self.j, self.size)

    @property
    def origin(self) -> tuple[float, float, float]:
        """Engine-space translation of the tile-local frame (min-x, min-z corner, y = 0)."""
        return (self.i * self.size, 0.0, self.j * self.size)

    def parent(self) -> "VisualTile | None":
        if self.level >= VISUAL_MAX_LEVEL:
            return None
        return VisualTile(self.level + 1, self.i // 2, self.j // 2)

    def children(self) -> list["VisualTile"]:
        if self.level == 0:
            return []
        lv = self.level - 1
        return [VisualTile(lv, 2 * self.i + di, 2 * self.j + dj) for dj in (0, 1) for di in (0, 1)]

    def collision_tiles(self) -> list["CollisionTile"]:
        n = COLLISION_PER_VISUAL * (2**self.level)
        return [
            CollisionTile(self.i * n + di, self.j * n + dj) for dj in range(n) for di in range(n)
        ]

    @staticmethod
    def parse(tile_id: str) -> "VisualTile":
        m = _VISUAL_RE.match(tile_id)
        if not m:
            raise ValueError(f"not a visual tile id: {tile_id!r}")
        t = VisualTile(int(m[1]), int(m[2]), int(m[3]))
        visual_tile_size(t.level)
        return t

    @staticmethod
    def at(x: float, z: float, level: int = 0) -> "VisualTile":
        return VisualTile(level, *grid_index(x, z, visual_tile_size(level)))


# --------------------------------------------------------------------------------------
# Collision tiles  c_{i}_{j}
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, order=True)
class CollisionTile:
    i: int
    j: int

    @property
    def id(self) -> str:
        return f"c_{self.i}_{self.j}"

    @property
    def bbox(self) -> BBoxLocal:
        return grid_bbox(self.i, self.j, COLLISION_TILE_SIZE)

    @property
    def origin(self) -> tuple[float, float, float]:
        return (self.i * COLLISION_TILE_SIZE, 0.0, self.j * COLLISION_TILE_SIZE)

    def visual_parent(self) -> VisualTile:
        return VisualTile(0, self.i // COLLISION_PER_VISUAL, self.j // COLLISION_PER_VISUAL)

    @staticmethod
    def parse(tile_id: str) -> "CollisionTile":
        m = _COLLISION_RE.match(tile_id)
        if not m:
            raise ValueError(f"not a collision tile id: {tile_id!r}")
        return CollisionTile(int(m[1]), int(m[2]))

    @staticmethod
    def at(x: float, z: float) -> "CollisionTile":
        return CollisionTile(*grid_index(x, z, COLLISION_TILE_SIZE))


# --------------------------------------------------------------------------------------
# LAZ tiles  <xmin>_<ymax>  (500 m, EPSG:3877)
# --------------------------------------------------------------------------------------

LAZ_URL = "https://turku.asiointi.fi/3d/{dataset}/{name}.laz"  # use curl -L (old host redirects)
LAZ_DATASETS = ("pistepilvi", "maanpinta")


@dataclass(frozen=True, order=True)
class LazTile:
    xmin: int
    ymax: int

    @property
    def name(self) -> str:
        return f"{self.xmin}_{self.ymax}"

    @property
    def bbox_en(self) -> BBoxEN:
        s = LAZ_TILE_SIZE
        return BBoxEN(self.xmin, self.ymax - s, self.xmin + s, self.ymax)

    def url(self, dataset: str = "pistepilvi") -> str:
        if dataset not in LAZ_DATASETS:
            raise ValueError(dataset)
        return LAZ_URL.format(dataset=dataset, name=self.name)

    @staticmethod
    def parse(name: str) -> "LazTile":
        m = _LAZ_RE.match(name)
        if not m:
            raise ValueError(f"not a LAZ tile name: {name!r}")
        t = LazTile(int(m[1]), int(m[2]))
        if t.xmin % LAZ_TILE_SIZE or t.ymax % LAZ_TILE_SIZE:
            raise ValueError(f"LAZ tile not on the 500 m grid: {name!r}")
        return t

    @staticmethod
    def at_en(e: float, n: float) -> "LazTile":
        """LAZ tile using the same half-open rule as the engine grid (N = ymax belongs to it)."""
        x, _, z = en_to_local(e, n)
        i, j = grid_index(x, z, LAZ_TILE_SIZE)
        return LazTile(int(ORIGIN_E + i * LAZ_TILE_SIZE), int(ORIGIN_N - j * LAZ_TILE_SIZE))


def laz_tiles_for_bbox(bbox: BBoxLocal | BBoxEN) -> list[LazTile]:
    local = bbox.to_local() if isinstance(bbox, BBoxEN) else bbox
    return [
        LazTile(int(ORIGIN_E + i * LAZ_TILE_SIZE), int(ORIGIN_N - j * LAZ_TILE_SIZE))
        for i, j in grid_range(local, LAZ_TILE_SIZE)
    ]


def collision_tiles_for_bbox(bbox: BBoxLocal) -> list[CollisionTile]:
    return [CollisionTile(i, j) for i, j in grid_range(bbox, COLLISION_TILE_SIZE)]


def visual_tiles_for_bbox(bbox: BBoxLocal, level: int = 0) -> list[VisualTile]:
    return [VisualTile(level, i, j) for i, j in grid_range(bbox, visual_tile_size(level))]


# --------------------------------------------------------------------------------------
# Areas of interest
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Area:
    name: str
    description: str
    core: BBoxLocal  # snapped to the collision grid
    margin: float = MESH_MARGIN
    anchor_en: tuple[float, float] | None = None  # point of interest (spawn hint)
    extra: dict = field(default_factory=dict, compare=False, hash=False)

    @property
    def with_margin(self) -> BBoxLocal:
        return self.core.buffered(self.margin)

    @property
    def collision_tiles(self) -> list[CollisionTile]:
        return collision_tiles_for_bbox(self.core)

    @property
    def visual_tiles(self) -> list[VisualTile]:
        return visual_tiles_for_bbox(self.core, 0)

    @property
    def laz_tiles(self) -> list[LazTile]:
        """LAZ tiles needed for the core + margin."""
        return laz_tiles_for_bbox(self.with_margin)


def area_centred_on(name: str, description: str, e: float, n: float, size: float) -> Area:
    """A size x size area, snapped to whole collision tiles, centred as closely as possible on (e, n)."""
    k = round(size / COLLISION_TILE_SIZE)
    x, _, z = en_to_local(e, n)
    i0 = round(x / COLLISION_TILE_SIZE - k / 2)
    j0 = round(z / COLLISION_TILE_SIZE - k / 2)
    s = COLLISION_TILE_SIZE
    core = BBoxLocal(i0 * s, j0 * s, (i0 + k) * s, (j0 + k) * s)
    return Area(name, description, core, MESH_MARGIN, (e, n))


# Phase 1 AOI: 250 m (4 x 4 collision tiles) centred on the office + 50 m meshing margin.
# Note: the visual L0 grid is aligned to O, so this AOI straddles 4 L0 visual tiles.
OFFICE_BLOCK = area_centred_on(
    "office-block",
    "Phase 1 AOI: 250 m around Läntinen Rantakatu 15, snapped to the 62.5 m collision grid",
    OFFICE_E,
    OFFICE_N,
    VISUAL_TILE_SIZE,
)

WORK_3X3 = Area(
    "work-3x3", "Nine 500 m LAZ tiles around the office; shared input working area",
    BBoxEN(23459000, 6703500, 23460500, 6705000).to_local(), margin=0,
)
AREAS: dict[str, Area] = {a.name: a for a in (OFFICE_BLOCK, WORK_3X3)}


def area_yaml(area: Area) -> str:
    """Deterministic YAML for data-manifests/areas/<name>.yaml (no pyyaml dependency)."""
    c, m = area.core, area.with_margin
    ce, me = c.to_en(), m.to_en()

    def f(v: float) -> str:
        return repr(float(v))

    def bb(b, keys) -> str:
        return "{ " + ", ".join(f"{k}: {f(v)}" for k, v in zip(keys, b.as_tuple())) + " }"

    lines = [
        "# GENERATED by `python -m turku3d.config --area-yaml "
        f"{area.name}` -- do not edit; edit pipeline/turku3d/config.py",
        f"name: {area.name}",
        f'description: "{area.description}"',
        f"crs: {CRS_COMPOUND}",
        f"origin_en: [{f(ORIGIN_E)}, {f(ORIGIN_N)}, {f(ORIGIN_H)}]",
    ]
    if area.anchor_en:
        ax, _, az = en_to_local(*area.anchor_en)
        lines.append(f"anchor_en: [{f(area.anchor_en[0])}, {f(area.anchor_en[1])}]")
        lines.append(f"anchor_local_xz: [{f(ax)}, {f(az)}]")
    lines += [
        f"margin_m: {f(area.margin)}",
        "core_local: " + bb(c, ("xmin", "zmin", "xmax", "zmax")),
        "core_en: " + bb(ce, ("emin", "nmin", "emax", "nmax")),
        "with_margin_local: " + bb(m, ("xmin", "zmin", "xmax", "zmax")),
        "with_margin_en: " + bb(me, ("emin", "nmin", "emax", "nmax")),
        "collision_tiles: [" + ", ".join(t.id for t in area.collision_tiles) + "]",
        "visual_tiles_l0: [" + ", ".join(t.id for t in area.visual_tiles) + "]",
        "laz_tiles: [" + ", ".join(t.name for t in area.laz_tiles) + "]",
    ]
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_MANIFESTS = REPO_ROOT / "data-manifests"
LAZ_TILES_CSV = DATA_MANIFESTS / "laz-tiles.csv"
SPECS_DIR = REPO_ROOT / "docs" / "specs"

def data_root() -> Path:
    """Shared input root on every OS; workers treat raw/ as immutable."""
    return Path(os.environ.get("TURKU3D_DATA", "~/turku3d/data")).expanduser()


def work_root() -> Path:
    """Writable sandbox root (W0-A environment contract)."""
    return Path(os.environ.get("TURKU3D_WORK", str(data_root()))).expanduser()


def build_dir(layer: str = "") -> Path:
    return work_root() / "build" / layer


def raw_dir(kind: str = "") -> Path:
    """kind in laz, wfs, ortho, osm, panos (immutable downloads)."""
    return data_root() / "raw" / kind


def cache_dir(step: str = "") -> Path:
    return work_root() / "cache" / step


def dist_dir() -> Path:
    return work_root() / "dist"


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(prog="python -m turku3d.config")
    ap.add_argument("--area-yaml", metavar="NAME", help="print the area manifest YAML")
    ap.add_argument("--where", nargs=2, type=float, metavar=("E", "N"),
                    help="print local coords and tile ids for an EPSG:3877 point")
    a = ap.parse_args(argv)
    if a.area_yaml:
        print(area_yaml(AREAS[a.area_yaml]), end="")
    elif a.where:
        e, n = a.where
        x, _, z = en_to_local(e, n)
        print(f"local x={x:.3f} z={z:.3f}  visual={VisualTile.at(x, z).id}  "
              f"collision={CollisionTile.at(x, z).id}  laz={LazTile.at_en(e, n).name}")
    else:
        print(f"origin {ORIGIN}  crs {CRS_COMPOUND}  data_root {data_root()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
