"""Where a coarse tile's raw inputs live, whether they are on disk, and how to fetch them.

Inputs are fetched in units of one dataset over one 500 m block (the city lidar grid, which holds
four L0 tiles):

* ``lidar``  the block's ``maanpinta`` ground key-point LAZ (``raw/laz/maanpinta/<block>.laz``)
* ``ortho``  2022 true ortho at the draft resolution, one O-aligned chunk per L0 tile
  (``raw/ortho/ortho2022to/coarse-<block>{/,.vrt,.chunks.json,.summary.json}``)
* ``wfs``    CityGML LOD2 buildings, street areas and water (``raw/wfs/coarse-<block>/``)
* ``osm``    an osmium clip of the cached Finland snapshot for the street graph, one per 2 km
  square holding the block (``raw/osm/coarse-osm-<E>_<N>/``; local: a clip mostly reads the
  snapshot, so a large one costs about as much as a small one). Optional: a draft without it has no
  streets and is rebuilt with them once the clip lands.

The checks here read only the disk (file stats and small JSON summaries, re-read when they change),
so the service can run them on every request. Any prepared area that covers a tile serves it
(``work-3x3``, ``tile-<id>``, the older per-tile ``coarse-t0_i_j`` areas). Only ``fetch`` touches
the network; the service runs it in the background prefetcher, never inside a build.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import math
import re
import threading
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from turku3d import config as C

DATASETS = ("lidar", "ortho", "wfs", "osm")  # fetch order: lidar gates ground and collision
REQUIRED = ("lidar", "ortho", "wfs")  # a build waits for these; without osm it has no streets
OSM_SQUARE = 2000.0  # m: one OSM clip covers this O-aligned square
ORTHO_PX = 512  # draft ortho per 250 m L0 tile (49 cm/px): one WMS chunk, one KTX2 encode
ORTHO_SLUG = "ortho2022to"
POINT_MARGIN = 20.0  # ground key points are read for the tile plus this margin
WFS_LAYERS = (
    "bldg:Building_LOD2",
    "GIS:Katualueet",
    "kanta:Vesikohde",
    "GIS:Liikennemerkit",
    "kanta:Pysakki",
    "GIS:Keskilinjat",
    # Street furniture, street lights and register trees (FURNITURE_WFS_LAYERS), appended so the
    # indices above stay stable. Each is a few requests per block (quadtree paging at the 1000 cap).
    "GIS:Varusteet",
    "frn:CityFurniture_LOD2",
    "GIS:Polkupyoraparkki",
    "GIS:Pysakointiautomaatit",
    "GIS:Follariasemat",
    "GIS:Puut",
)
PERMIT_PARKING = "GIS:Lupapysakointipaikat"
FURNITURE_WFS_LAYERS = WFS_LAYERS[6:]
OPTIONAL_WFS_LAYERS = ("kanta:Korkeuspiste", PERMIT_PARKING)
BLOCK = C.LAZ_TILE_SIZE
AREA_PREFIX = "coarse-"


@lru_cache(maxsize=256)
def _vrt_sources(path, size, modified):
    """Parse shared mosaic dependencies once per file identity."""
    import xml.etree.ElementTree as ET

    try:
        nodes = ET.fromstring(path.read_bytes()).iter("SourceFilename")
        return tuple(
            path.parent / node.text if node.get("relativeToVRT") == "1" else Path(node.text)
            for node in nodes
        )
    except ET.ParseError:
        return ()  # Unreadable mosaics fail in the producer, not during HTTP admission.


def content_paths(paths):
    """Include raster bytes referenced by VRTs, not just their unchanged XML wrapper."""
    result = {}

    def add(path):
        if not isinstance(path, Path):
            path = Path(path)
        if path in result:
            return
        result[path] = path
        if path.suffix == ".vrt":
            try:
                stat = path.stat()
            except FileNotFoundError:
                return
            for source in _vrt_sources(path, stat.st_size, stat.st_mtime_ns):
                add(source)

    for path in paths:
        add(path)
    return list(result)


class NoCoverage(RuntimeError):
    """The tile lies outside every keyless source this producer can use."""


class MissingInputs(RuntimeError):
    """Some inputs are not on disk yet; the build waits for the prefetcher instead of downloading."""

    def __init__(self, tile: str, units: list[str]):
        super().__init__(f"{tile}: waiting for {', '.join(units)}")
        self.tile, self.units = tile, units


@dataclass(frozen=True, order=True)
class Unit:
    """One dataset over one 500 m block: the prefetcher's unit of work."""

    dataset: str
    block: C.LazTile

    @property
    def key(self) -> str:
        return f"{self.dataset}/{self.block.name}"

    @staticmethod
    def parse(key: str) -> Unit:
        dataset, name = key.split("/", 1)
        if dataset not in DATASETS:
            raise ValueError(f"unknown dataset {dataset!r}")
        return Unit(dataset, C.LazTile.parse(name))


def block_bbox(block: C.LazTile) -> C.BBoxLocal:
    return block.bbox_en.to_local()


def permit_sources(tile: C.VisualTile, sources) -> list[Path]:
    """Optional permit-parking kerb lines of every area over the tile and its 60 m margin."""
    slug = PERMIT_PARKING.replace(":", "_")
    window = tile.bbox.buffered(60).to_en().as_tuple()
    paths = []
    for summary, state in sources.wfs.within(f"{slug}.summary.json", window, intersect=True):
        path = summary.with_name(f"{slug}.parquet")
        if state.get("status") == "ok" and path.exists():
            paths.append(path)
    return sorted(set(paths))


def block_area(block: C.LazTile) -> C.Area:
    return C.Area(
        AREA_PREFIX + block.name, f"coarse inputs of block {block.name}", block_bbox(block), 0.0
    )


def block_of(tile: C.VisualTile) -> C.LazTile:
    """The 500 m block holding an L0 tile (the L0 grid nests in the lidar grid)."""
    x = tile.origin[0] + tile.size / 2
    z = tile.origin[2] + tile.size / 2
    e, n, _ = C.local_to_en(x, 0.0, z)
    return C.LazTile.at_en(e, n)


def block_tiles(block: C.LazTile) -> list[C.VisualTile]:
    b = block_bbox(block)
    return [C.VisualTile(0, i, j) for i, j in C.grid_range(b, C.VISUAL_TILE_SIZE)]


def tile_blocks(tile: C.VisualTile) -> list[C.LazTile]:
    """Lidar blocks under a tile's key-point window (tile + POINT_MARGIN)."""
    return C.laz_tiles_for_bbox(tile.bbox.buffered(POINT_MARGIN))


def unit_paths(unit: Unit) -> list[Path]:
    """Files and directories a unit's fetch writes (what the prefetch ledger may prune)."""
    if unit.dataset == "lidar":
        return [C.raw_dir("laz") / "maanpinta" / f"{unit.block.name}.laz"]
    if unit.dataset == "osm":
        return [C.raw_dir("osm") / osm_area(unit.block).name]
    name = AREA_PREFIX + unit.block.name
    if unit.dataset == "ortho":
        root = C.raw_dir("ortho") / ORTHO_SLUG
        return [
            root / name,
            *(root / f"{name}{s}" for s in (".vrt", ".chunks.json", ".summary.json")),
        ]
    return [C.raw_dir(unit.dataset) / name]


def osm_area(block: C.LazTile) -> C.Area:
    """The OSM_SQUARE holding a block (O-aligned, named by its west and north edges)."""
    b = block_bbox(block)
    i, j = C.grid_index(b.xmin, b.zmin, OSM_SQUARE)
    square = C.grid_bbox(i, j, OSM_SQUARE)
    en = square.to_en()
    return C.Area(
        f"{AREA_PREFIX}osm-{int(en.emin)}_{int(en.nmax)}", "coarse OSM square", square, 0.0
    )


def covers(bbox_en, tile: C.VisualTile) -> bool:
    t = tile.bbox.to_en()
    return (
        bbox_en[0] <= t.emin
        and bbox_en[1] <= t.nmin
        and bbox_en[2] >= t.emax
        and bbox_en[3] >= t.nmax
    )


def vrt_bounds(text: str) -> tuple[float, float, float, float]:
    """(emin, nmin, emax, nmax) of a north-up GDAL VRT from its size and geotransform."""
    w, h = (int(re.search(rf'raster{a}Size="(\d+)"', text)[1]) for a in "XY")
    e0, dx, _, n0, _, dy = map(float, re.search(r"<GeoTransform>([^<]+)<", text)[1].split(","))
    return e0, n0 + h * dy, e0 + w * dx, n0


class _Files:
    """Files matching a glob, listed at most every MAX_AGE seconds (or after ``invalidate``), each
    parsed again only when its size or mtime changes (JSON by default)."""

    MAX_AGE = 1.0

    def __init__(self, root: Path, pattern: str, parse=json.loads):
        self.root, self.pattern, self.parse = root, pattern, parse
        self.cache: dict[Path, tuple[tuple[int, int], dict]] = {}
        self.listing: list[tuple[Path, dict]] = []
        self.by_name: dict[str, list[tuple[Path, dict]]] = {}
        self.queries = {}
        self.listed_at = -math.inf
        self.lock = threading.Lock()

    def invalidate(self) -> None:
        with self.lock:
            self.listed_at = -math.inf

    def items(self) -> list[tuple[Path, dict]]:
        with self.lock:
            if time.monotonic() - self.listed_at < self.MAX_AGE:
                return self.listing
            out = []
            seen = set()
            for path in sorted(self.root.glob(self.pattern)):
                try:
                    st = path.stat()
                except OSError:
                    continue
                sig = (st.st_size, st.st_mtime_ns)
                seen.add(path)
                cached = self.cache.get(path)
                if not cached or cached[0] != sig:
                    try:
                        cached = (sig, self.parse(path.read_text()))
                    except (OSError, ValueError, TypeError):
                        continue
                    self.cache[path] = cached
                out.append((path, cached[1]))
            for gone in set(self.cache) - seen:
                del self.cache[gone]
            if out != self.listing:
                grouped = {}
                for path, value in out:
                    grouped.setdefault(path.name, []).append((path, value))
                self.by_name, self.listing = grouped, out
                self.queries.clear()
            self.listed_at = time.monotonic()
            return self.listing

    def named(self, name):
        self.items()
        return self.by_name.get(name, ())

    def within(self, name, bounds, *, intersect=False):
        """Reuse per-tile coverage queries until the shared area summaries change."""
        rows = self.named(name)
        key = (name, bounds, intersect)
        cached = self.queries.get(key)
        if cached is not None and cached[0] is rows:
            return cached[1]
        x0, y0, x1, y1 = bounds
        out = []
        for path, state in rows:
            b = state.get("bbox_en")
            if b is not None and (
                b[0] <= x1 and b[1] <= y1 and b[2] >= x0 and b[3] >= y0
                if intersect
                else b[0] <= x0 and b[1] <= y0 and b[2] >= x1 and b[3] >= y1
            ):
                out.append((path, state))
        if len(self.queries) >= 4096:
            self.queries.clear()
        self.queries[key] = rows, out
        return out


class Sources:
    """Disk-only lookups of a tile's inputs under one raw root."""

    def __init__(self):
        self.wfs = _Files(C.raw_dir("wfs"), "*/*.summary.json")
        self.ortho = _Files(C.raw_dir("ortho") / ORTHO_SLUG, "*.chunks.json")
        self.mosaics = _Files(C.raw_dir("ortho") / ORTHO_SLUG, "*.vrt", vrt_bounds)
        self._listed: set[str] | None = None
        self._laz = None

    def invalidate(self) -> None:
        """A fetch landed: list the summaries again on the next lookup."""
        self.wfs.invalidate()
        self.ortho.invalidate()
        self.mosaics.invalidate()

    def listed(self) -> set[str]:
        """maanpinta tiles in the city lidar index (laz-tiles.csv); empty = no index, assume all."""
        if self._listed is None:
            from turku3d.fetch import laz

            self._listed = {name for ds, name in laz.known_tiles() if ds == "maanpinta"}
        return self._listed

    def covered(self, block: C.LazTile) -> bool:
        listed = self.listed()
        return not listed or block.name in listed

    # -- lidar -----------------------------------------------------------------------------------
    def lidar_paths(self, tile: C.VisualTile) -> tuple[list[Path], list[C.LazTile]]:
        """(present LAZ files, missing listed blocks) under the tile's key-point window."""
        blocks = [b for b in tile_blocks(tile) if self.covered(b)]
        if not blocks:
            raise NoCoverage(
                f"{tile.id}: outside the city lidar index (laz-tiles.csv); "
                "the MML 2 m DEM needs an API key"
            )
        root = C.raw_dir("laz") / "maanpinta"
        present = {b: self.lidar_present(b) for b in blocks}
        return [root / f"{b.name}.laz" for b in blocks if present[b]], [
            b for b in blocks if not present[b]
        ]

    def lidar_present(self, block: C.LazTile) -> bool:
        path = C.raw_dir("laz") / "maanpinta" / f"{block.name}.laz"
        try:
            size = path.stat().st_size
        except FileNotFoundError:
            return False
        if self._laz is None:
            from turku3d.fetch.common import Registry

            self._laz = Registry(C.raw_dir("laz"))
        # This path is already relative to the registry root; avoid resolving
        # both absolute paths (including every parent symlink) for each request.
        record = self._laz.records().get(f"maanpinta/{block.name}.laz")
        return record is not None and record.get("bytes") == size

    # -- ortho -----------------------------------------------------------------------------------
    def ortho_source(self, tile: C.VisualTile) -> Path | None:
        """The first ortho mosaic (by name: the 512 px coarse ones before the 5 cm areas) whose
        extent covers the tile. NoCoverage when the block's coarse fetch found no imagery there."""
        t = tile.bbox.to_en()
        for vrt, bounds in self.mosaics.items():
            if (
                bounds[0] <= t.emin
                and bounds[1] <= t.nmin
                and bounds[2] >= t.emax
                and bounds[3] >= t.nmax
            ):
                return vrt
        own = f"{AREA_PREFIX}{block_of(tile).name}.chunks.json"
        for state_path, state in self.ortho.items():
            if (
                state_path.name == own
                and state.get(f"{tile.i}_{tile.j}", {}).get("status") == "empty"
            ):
                raise NoCoverage(f"{tile.id}: no true ortho")
        return None

    # -- wfs -------------------------------------------------------------------------------------
    def wfs_source(self, name: str, tile: C.VisualTile) -> tuple[bool, Path | None]:
        """(found, parquet or None for an empty layer) for one WFS layer over the tile."""
        slug = name.replace(":", "_")
        for summary, s in self.wfs.within(f"{slug}.summary.json", tile.bbox.to_en().as_tuple()):
            if s.get("status") not in ("ok", "empty"):
                continue
            if s["status"] == "empty":
                return True, None
            parquet = summary.with_name(f"{slug}.parquet")
            if parquet.exists():
                return True, parquet
        return False, None

    # -- osm -------------------------------------------------------------------------------------
    def osm_source(self, tile: C.VisualTile) -> Path | None:
        """The newest OSM GeoParquet of an area holding the tile: work-3x3, the tile's input area,
        the older per-tile clip, or the tile's block."""
        w, b = C.WORK_3X3.core, tile.bbox
        inside = w.xmin <= b.xmin and w.zmin <= b.zmin and b.xmax <= w.xmax and b.zmax <= w.zmax
        areas = ([C.WORK_3X3.name] if inside else []) + [
            C.tile_input_area(tile).name,
            f"{AREA_PREFIX}{tile.id}",
            osm_area(block_of(tile)).name,
        ]
        for area in areas:
            found = sorted((C.raw_dir("osm") / area).glob("*.parquet"))
            if found:
                return found[-1]
        return None

    # -- whole tile ------------------------------------------------------------------------------
    def missing(self, tile: C.VisualTile, *, first_pass=False) -> list[Unit]:
        """Units the tile still needs, in fetch order. Raises NoCoverage outside the sources."""
        _, lidar = self.lidar_paths(tile)
        out = [Unit("lidar", b) for b in lidar]
        if not first_pass and self.ortho_source(tile) is None:
            out.append(Unit("ortho", block_of(tile)))
        layers = WFS_LAYERS[:3] if first_pass else WFS_LAYERS
        if not all(self.wfs_source(name, tile)[0] for name in layers):
            out.append(Unit("wfs", block_of(tile)))
        return out

    def missing_for_service(self, tile: C.VisualTile, *, first_pass=False) -> list[Unit]:
        """Public clients have no motion-prefetch route: request OSM with the required datasets."""
        missing = self.missing(tile, first_pass=first_pass)
        if self.osm_source(tile) is None:
            missing.append(Unit("osm", block_of(tile)))
        return missing

    def input_key(self, tile: C.VisualTile, *, first_pass=False) -> str | None:
        """Content identity of every raw file the tile's build reads, and whether OSM is
        there; None while a required unit is missing. A draft built from other files is stale."""
        laz, missing = self.lidar_paths(tile)
        ortho, osm = (None if first_pass else self.ortho_source(tile)), self.osm_source(tile)
        wfs = [
            self.wfs_source(name, tile) for name in (WFS_LAYERS[:3] if first_pass else WFS_LAYERS)
        ]
        registers = (
            [self.wfs_source(name, tile)[1] for name in ("kanta:Rakennus", "GIS:Rakennukset")]
            if not first_pass
            else []
        )
        if missing or (not first_pass and ortho is None) or not all(found for found, _ in wfs):
            return None
        from .bridge_survey import source_paths, spot_sources

        laz = [*laz, *source_paths(tile)]
        imagery = (
            [ortho, ortho.with_name(ortho.name.removesuffix(".vrt") + ".chunks.json")]
            if ortho
            else []
        )
        h = hashlib.sha256(b"osm" if osm else b"no-osm")
        from .roofs import lidar_paths
        from .street_survey import source_paths as street_survey_paths

        paths = [
            *laz,
            *lidar_paths(tile),
            *street_survey_paths(tile),
            *imagery,
            *(p for _, p in wfs if p),
            *(p for p in registers if p),
            *spot_sources(tile, self),
            *([osm] if osm else []),
        ]
        from turku3d.tile_layer import identities

        paths = content_paths(paths)
        if not all(p.is_file() for p in paths):
            return None
        h.update(
            json.dumps(
                identities({str(i): p for i, p in enumerate(paths)}), sort_keys=True
            ).encode()
        )
        return h.hexdigest()[:24]

    def unit_present(self, unit: Unit) -> bool:
        if unit.dataset == "lidar":
            return not self.covered(unit.block) or self.lidar_present(unit.block)
        tiles = block_tiles(unit.block)
        try:
            if unit.dataset == "ortho":
                return all(self.ortho_source(t) is not None for t in tiles)
        except NoCoverage:
            return True  # fetched: the city has no imagery there
        if unit.dataset == "osm":
            return all(self.osm_source(t) is not None for t in tiles)
        return all(
            self.wfs_source(name, t)[0]
            for t in tiles
            for name in (*WFS_LAYERS, *OPTIONAL_WFS_LAYERS)
        )


def fetch(unit: Unit, log=print) -> None:
    """Download one unit into the shared raw cache with the read-through fetchers."""
    if unit.dataset == "lidar":
        from turku3d.fetch import laz

        laz.fetch_tiles([unit.block], ("maanpinta",), log=log)
    elif unit.dataset == "ortho":
        from turku3d.fetch import wms
        from turku3d.fetch.sources import WMS_LAYERS

        layer = next(w for w in WMS_LAYERS if w.slug == ORTHO_SLUG)
        layer = dataclasses.replace(layer, px_m=C.VISUAL_TILE_SIZE / ORTHO_PX)
        s = wms.fetch_layer(layer, block_area(unit.block), workers=1, log=log, chunk_px=ORTHO_PX)
        if s["errors"]:
            raise RuntimeError(f"WMS {unit.key}: {s['errors'][:2]}")
    elif unit.dataset == "osm":
        from turku3d.fetch import osm

        osm.fetch_area(osm_area(unit.block), log=log)
    else:
        from turku3d.fetch import wfs
        from turku3d.fetch.sources import WFS_LAYERS as ALL

        for name in WFS_LAYERS:
            layer = next(layer for layer in ALL if layer.name == name)
            s = wfs.fetch_layer(layer, block_area(unit.block), log=log)
            if s.get("status") not in ("ok", "empty"):
                raise RuntimeError(f"WFS {name} {unit.key}: {s}")
        # Height anchors enrich new bridges without making the structural pass
        # wait for another required dataset or downloading during generation.
        for name in OPTIONAL_WFS_LAYERS:
            layer = next(layer for layer in ALL if layer.name == name)
            try:
                s = wfs.fetch_layer(layer, block_area(unit.block), log=log)
                if s.get("status") not in ("ok", "empty"):
                    log(f"WFS optional {layer.name} {unit.key}: {s}")
            except Exception as error:
                log(f"WFS optional {layer.name} {unit.key}: {error}")


def size(paths: list[Path]) -> int:
    total = 0
    for p in paths:
        if p.is_dir():
            total += sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
        elif p.exists():
            total += p.stat().st_size
    return total
