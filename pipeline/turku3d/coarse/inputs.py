"""Coarse tile inputs: read from the shared raw cache only, prepared once per tile and dataset.

A build never downloads: when lidar, ortho or WFS data for the tile is not on disk yet,
``gather`` raises ``MissingInputs`` with the units the prefetcher has to fetch first
(``python -m turku3d.coarse build --fetch`` fetches them in the foreground for a hand-run build).

Each dataset is reduced to what the build needs and cached under ``<coarse root>/.inputs/<tile>/``
as ``<dataset>.<key>.pkl``: ``ground`` (key points of the tile + POINT_MARGIN and the CELLS grid
heights interpolated over their TIN), ``ortho`` (ORTHO_PX RGB), ``wfs`` (building shells,
street-area polygons with their collision material, water polygons), ``roads`` (the tile's
encoded OSM street graph, ``roads.json``) and ``trees`` (the OSM ``natural=tree`` points in it). The key hashes this module's source and the identities
(path, size, mtime) of the raw files read, so new raw data or changed code prepares it again.
``prepare`` fills the cache for whatever is on disk; the prefetcher runs it in the background for
the tiles near the player, so the build itself starts from the small cached files.
"""

from __future__ import annotations

import hashlib
import json
import os
import pickle
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import shapely

from turku3d import config as C
from turku3d.col.materials import mat_id
from turku3d.spatial import frame as spatial_frame

from . import roofs
from .register import district as register_district
from .sources import ORTHO_PX, POINT_MARGIN, WFS_LAYERS, MissingInputs, NoCoverage, Sources, covers

CELLS = 128  # grid cells per 250 m tile; 32 per collision tile
# Each prepared dataset depends only on its own producers. A surface fix must
# not decode classified roof/bridge lidar again for every surrounding tile.
DEPENDENCIES = {
    "wfs": (
        "roofs.py",
        "../buildings/source_surfaces.py",
        "../ground/materials.py",
        "../spatial.py",
    ),
    "roads": (
        "bridges.py",
        "bridge_survey.py",
        "tunnels.py",
        "bridge_details.py",
        "underpasses.py",
        "area.py",
        "../spatial.py",
        "../roads/__init__.py",
    ),
    "parking": ("parked.py", "../parked/placement.py", "../spatial.py"),
    "trees": ("../spatial.py",),
    "barriers": ("fences.py", "../spatial.py"),
    "forests": ("forests.py", "../spatial.py"),
    "tunnels": ("tunnels.py", "bridge_details.py", "underpasses.py", "area.py", "../spatial.py"),
    "stairs": ("stairs.py", "../spatial.py"),
    "surfaces": ("surfaces.py", "../spatial.py"),
    "markings": ("surfaces.py", "../spatial.py"),
    "kerbs": ("kerbs.py", "../spatial.py"),
    "appearance": ("register.py", "../spatial.py"),
    "register": ("register.py",),
    "poles": (
        "../props/__init__.py",
        "../props/models.py",
        "../props/orientation.py",
        "../spatial.py",
    ),
}
CODE = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()[:16]


__all__ = ["CELLS", "Building", "Inputs", "MissingInputs", "NoCoverage", "gather", "prepare"]


class Building:
    def __init__(self, key, footprint, base, top, material_key):
        self.key, self.footprint, self.base, self.top = key, footprint, base, top
        self.material_key = material_key
        self.facade_key = str(key)
        self.attributes = {}
        self.roof_parts = []
        self.height_evidence = []


@dataclass
class Inputs:
    points: np.ndarray  # (N,3) engine x, y, z ground key points (tile + POINT_MARGIN)
    heights: np.ndarray  # (CELLS+1, CELLS+1) grid heights, row = z, column = x
    ortho: np.ndarray  # (ORTHO_PX, ORTHO_PX, 3) uint8, row 0 = north edge of the tile
    buildings: list[Building]  # LOD2 shells whose footprint centroid lies in the tile
    streets: list  # (polygon, collision material id) of street areas over the tile
    water: list  # water polygons over the tile
    roads: (
        dict | None
    )  # prepare_roads: roads.json of the tile's street graph; None before OSM lands
    sources: dict  # provenance: prepared dataset -> raw paths
    seconds: dict
    key: str = ""
    trees: list = ()  # prepare_trees: (id, x, z, height, conifer) in the tile; () before OSM lands  # Sources.input_key: the raw files' identities, recorded in the manifest
    barriers: list = ()  # OSM barrier geometries, tags and deliberate road membership
    forests: list = ()
    canopy: np.ndarray = field(default_factory=lambda: np.empty((0, 3)))
    kerbs: object = None
    markings: list = ()  # explicit OSM crosswalk/lane/stop line polygons
    surfaces: list = ()  # mapped OSM land cover, paving and ground-level corridors
    register: dict = field(default_factory=dict)
    tunnels: list = ()  # OSM full tunnel ways, with tags
    stairs: tuple = ((), ())
    parking: tuple = ((), ())
    poles: list = ()  # sign and bus-stop props with full-tile source identities


# -- ground ---------------------------------------------------------------------------------------


def grid_axes(tile: C.VisualTile) -> tuple[np.ndarray, np.ndarray]:
    step = tile.size / CELLS
    return (
        tile.origin[0] + np.arange(CELLS + 1) * step,
        tile.origin[2] + np.arange(CELLS + 1) * step,
    )


def grid_heights(points: np.ndarray, xs: np.ndarray, zs: np.ndarray) -> np.ndarray:
    """Linear interpolation over the key-point TIN; nearest key point outside its hull."""
    from scipy.interpolate import LinearNDInterpolator
    from scipy.spatial import cKDTree

    gx, gz = np.meshgrid(xs, zs)
    query = np.column_stack([gx.ravel(), gz.ravel()])
    h = LinearNDInterpolator(points[:, [0, 2]], points[:, 1])(query)
    missing = ~np.isfinite(h)
    if missing.any():
        _, nearest = cKDTree(points[:, [0, 2]]).query(query[missing])
        h[missing] = points[nearest, 1]
    return h.reshape(gz.shape)


def ground_points(tile: C.VisualTile, paths: list[Path], canopy=None) -> np.ndarray:
    import laspy

    e0, n0, e1, n1 = tile.bbox.buffered(POINT_MARGIN).to_en().as_tuple()
    chunks = []
    for path in paths:
        las = laspy.read(path)
        e, n, h = np.asarray(las.x), np.asarray(las.y), np.asarray(las.z)
        keep = (e >= e0) & (e <= e1) & (n >= n0) & (n <= n1)
        x, y, z = C.en_to_local(e[keep], n[keep], h[keep])
        xyz = np.column_stack([x, y, z])
        classes = np.asarray(las.classification)[keep]
        vegetation = np.isin(classes, [3, 4, 5])
        chunks.append(xyz[~vegetation])
        if canopy is not None:
            from turku3d.vegetation.trees import highest_per_cell

            canopy.append(highest_per_cell(xyz[vegetation]))
    points = np.concatenate(chunks) if chunks else np.empty((0, 3))
    if len(points) < 16:
        raise NoCoverage(f"{tile.id}: only {len(points)} ground key points")
    return points


def prepare_ground(tile: C.VisualTile, points: np.ndarray) -> dict:
    return {"points": points, "heights": grid_heights(points, *grid_axes(tile))}


# -- ortho ----------------------------------------------------------------------------------------


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


# -- wfs ------------------------------------------------------------------------------------------


def _frame(path: Path | None, bbox: C.BBoxLocal, kinds=("Polygon", "MultiPolygon")):
    if path is None:
        return []
    frame = spatial_frame(path, bbox)
    frame = frame[frame.geom_type.isin(kinds)]
    window = shapely.box(bbox.xmin, bbox.zmin, bbox.xmax, bbox.zmax)
    local = frame.geometry_local.apply(shapely.force_2d)
    keep = local.intersects(window)
    return list(zip(local[keep], frame[keep].itertuples(), strict=True))


def buildings(path: Path, tile: C.VisualTile) -> list[Building]:
    """Complete surveyed solid footprints owned by centroid, retaining the roof envelope."""
    from turku3d.buildings.source_surfaces import classify_surfaces

    frame = classify_surfaces(spatial_frame(path, tile.bbox.buffered(100), whole_features=True))
    frame = frame[frame.surface_type.isin(["RoofSurface", "GroundSurface"])]
    if frame.empty:
        return []
    bbox = tile.bbox.buffered(100.0)
    window = shapely.box(bbox.xmin, bbox.zmin, bbox.xmax, bbox.zmax)
    local = frame.geometry_local.apply(shapely.force_2d)
    keys = frame.loc[local.intersects(window), "feature_key"].unique()
    frame = frame[frame.feature_key.isin(keys)]
    local = local.loc[frame.index]
    out = []
    for key, group in frame.groupby("feature_key", sort=True):
        ground = group.surface_type == "GroundSurface"
        footprint = shapely.make_valid(shapely.union_all(local[group.index[ground]].values))
        polys = [g for g in getattr(footprint, "geoms", [footprint]) if g.geom_type == "Polygon"]
        if not polys:
            continue
        for part_index, footprint in enumerate(sorted(polys, key=lambda p: (-p.area, p.wkb))):
            if footprint.area < 0.01:
                continue
            c = footprint.centroid
            if not tile.bbox.contains(c.x, c.y):
                continue
            grounds = group[ground & local.loc[group.index].intersects(footprint)]
            heights = np.concatenate(
                [np.asarray(g.exterior.coords)[:, 2] for g in grounds.geometry]
            )
            base = float(heights.min())
            roof_surfaces = group[~ground & local.loc[group.index].intersects(footprint)]
            if len(roof_surfaces):
                areas = roof_surfaces.geometry.area.to_numpy()
                means = np.array(
                    [np.asarray(g.exterior.coords)[:, 2].mean() for g in roof_surfaces.geometry]
                )
                top = float((means * areas).sum() / max(areas.sum(), 1e-9))
            else:
                top = base + float(group.measuredHeight.astype(float).iloc[0])
            if not np.isfinite(top) or top < base + 2.0:
                top = base + 3.0
            building = Building(
                key if part_index == 0 else f"{key}-part{part_index}",
                shapely.geometry.polygon.orient(footprint, 1.0),
                base,
                top,
                "facade_plaster",
            )

            building.roof_parts = roofs.envelope(footprint, roof_surfaces.geometry_local, top)

            def value(*columns, group=group):
                for column in columns:
                    if column in group.columns:
                        v = str(group[column].iloc[0])
                        if v not in ("", "0", "None", "nan", "<NA>"):
                            return v
                return ""

            building.facade_key = value("pysyvarakennustunnus") or str(key)
            levels = value("kerrosluku", "storeysAboveGround")
            year = value("yearOfConstruction", "valmistunut")[:4]
            year = int(year) if year.isdigit() else None
            building.attributes = dict(
                construction_year=year,
                use=value("käyttötarkoitus", "usage", "function") or "unknown",
                footprint_m2=float(footprint.area),
                district=register_district(value("kiinteistotunnus")),
                levels=int(levels) if levels.isdigit() and int(levels) > 0 else None,
                era="unknown"
                if year is None
                else "pre-1850"
                if year < 1850
                else "1850-1919"
                if year < 1920
                else "1920-1959"
                if year < 1960
                else "1960+",
            )
            out.append(building)
    return out


def prepare_wfs(
    tile: C.VisualTile, bldg: Path | None, streets: Path | None, water: Path | None, roof_paths=()
) -> dict:
    """An empty LOD2 layer (open water, fields at the city edge) is a tile without buildings."""
    from turku3d.ground.materials import surface_material

    shells = buildings(bldg, tile) if bldg else []
    if shells and roof_paths:
        roofs.refine(shells, roof_paths)
    return {
        "buildings": shells,
        "streets": [
            (shapely.make_valid(g), mat_id(surface_material(r.Katuosalaji, r.Pintamateriaali)))
            for g, r in _frame(streets, tile.bbox)
        ],
        "water": [shapely.make_valid(g) for g, _ in _frame(water, tile.bbox)],
    }


# -- roads ----------------------------------------------------------------------------------------


def prepare_roads(tile: C.VisualTile, osm: Path, survey_paths=(), spot_paths=()) -> dict:
    """``roads.json`` bytes of the tile's street graph with its node and edge counts."""
    from turku3d.roads import build_graph, encode, osm_ways

    graph = build_graph(
        osm_ways(osm, tile.bbox), tile.bbox.as_tuple(), f"coarse-{tile.id}", connected=False
    )
    from .bridges import prepare as prepare_bridges

    return {
        "json": encode(graph),
        "nodes": len(graph["nodes"]) // 2,
        "edges": len(graph["edges"]),
        "bridge_ways": prepare_bridges(tile, osm, survey_paths, spot_paths),
    }


def prepare_trees(tile: C.VisualTile, osm: Path) -> list:
    """The OSM ``natural=tree`` points in the tile: (id, x, z, height, conifer)."""
    import json

    from turku3d.spatial import rows

    trees = []
    for k, row in rows(osm, tile.bbox):
        tags = json.loads(row["tags"]) if row["tags"] else {}
        if tags.get("natural") != "tree":
            continue
        point = row["geometry_local"]
        if point.geom_type != "Point" or not tile.bbox.contains(point.x, point.y):
            continue
        try:
            height = float(str(tags.get("height", "10")).removesuffix(" m"))
        except ValueError:
            height = 10.0
        conifer = tags.get("leaf_type") == "needleleaved"
        trees.append(
            (f"osm:{row.get('osm_id', k)}", point.x, point.y, min(max(height, 3.0), 35.0), conifer)
        )
    return trees


# -- cache ----------------------------------------------------------------------------------------


def _key(paths: list[Path], dataset: str = "") -> str:
    h = hashlib.sha256(CODE.encode())
    for name in DEPENDENCIES.get(dataset.removesuffix("-first"), ()):
        h.update(
            Path(__file__).with_name(name).read_bytes()
            if "/" not in name
            else (Path(__file__).parent / name).read_bytes()
        )
    from turku3d.tile_layer import identities

    from .sources import content_paths

    paths = content_paths(paths)
    # Size/mtime memoize the expensive read; only bytes identify a prepared input.
    h.update(
        json.dumps(identities({str(i): p for i, p in enumerate(paths)}), sort_keys=True).encode()
    )
    return h.hexdigest()[:24]


_ABSENT = object()


def _load(path: Path, key: str):
    """The cached value, or _ABSENT when there is none for this key."""
    try:
        with open(path, "rb") as f:
            entry = pickle.load(f)
    except (OSError, EOFError, pickle.UnpicklingError, AttributeError, ImportError):
        return _ABSENT
    return entry["value"] if entry.get("key") == key else _ABSENT


def _save(path: Path, key: str, value) -> None:
    """Write the entry and drop the dataset's entries for older keys."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with open(tmp, "wb") as f:
        pickle.dump({"key": key, "value": value}, f, protocol=pickle.HIGHEST_PROTOCOL)
    tmp.replace(path)
    for old in path.parent.glob(path.name.split(".", 1)[0] + ".*.pkl"):
        if old != path:
            old.unlink(missing_ok=True)


def _sources(tile: C.VisualTile, sources: Sources, *, first_pass=False) -> dict[str, list | None]:
    """Raw files per prepared dataset (None for an empty WFS layer); None while not on disk."""
    from . import bridge_survey

    laz, missing = sources.lidar_paths(tile)
    ortho = None if first_pass else sources.ortho_source(tile)
    wfs = [sources.wfs_source(name, tile) for name in WFS_LAYERS]
    osm = sources.osm_source(tile)
    return {
        "ground": None if missing else laz,
        "ortho": [ortho] if ortho else None,
        "wfs": [p for _, p in wfs[:3]] + roofs.lidar_paths(tile)
        if all(found for found, _ in wfs[:3])
        else None,
        "roads": [
            osm,
            *bridge_survey.source_paths(tile),
            *bridge_survey.spot_sources(tile, sources),
        ]
        if osm
        else None,
        "parking": [osm, wfs[1][1], wfs[0][1]] if osm else None,
        "trees": [osm] if osm else None,
        "barriers": [osm] if osm else None,
        "forests": [osm] if osm else None,
        "tunnels": [osm] if osm else None,
        "stairs": [osm] if osm else None,
        "poles": [wfs[3][1], wfs[4][1], wfs[5][1], wfs[1][1], osm]
        if all(found for found, _ in wfs)
        else None,
        "surfaces": [osm] if osm else None,
        "markings": [osm] if osm else None,
        "kerbs": [osm] if osm else None,
        "appearance": [osm] if osm else None,
        "register": [
            p
            for name in ("kanta:Rakennus", "GIS:Rakennukset")
            if (p := sources.wfs_source(name, tile)[1])
        ],
    }


def _prepare_one(tile: C.VisualTile, dataset: str, paths: list):
    import pyarrow

    pyarrow.set_cpu_count(1)
    pyarrow.set_io_thread_count(1)
    if dataset == "parking":
        from .parked import prepare as prepare_parking

        return prepare_parking(tile, *paths)
    if dataset == "appearance":
        from .register import read_osm

        return read_osm(tile, paths[0])
    if dataset == "register":
        from .register import read_register

        return read_register(paths)
    if dataset == "kerbs":
        from .kerbs import prepare as prepare_kerbs

        return prepare_kerbs(tile, paths[0])
    if dataset == "markings":
        from .surfaces import mapped_paint

        return mapped_paint(tile, paths[0])
    if dataset == "surfaces":
        from .surfaces import osm_surfaces

        return osm_surfaces(tile, paths[0])
    if dataset == "stairs":
        from .stairs import prepare as prepare_stairs

        return prepare_stairs(tile, paths[0])
    if dataset == "tunnels":
        from .tunnels import prepare as prepare_tunnels

        return prepare_tunnels(tile, paths[0])
    if dataset == "ground":
        canopy = []
        ground = prepare_ground(tile, ground_points(tile, paths, canopy))
        ground["canopy"] = np.concatenate(canopy) if canopy else np.empty((0, 3))
        return ground
    if dataset == "ortho":
        return read_ortho(paths[0], tile)
    if dataset == "roads":
        return prepare_roads(
            tile,
            paths[0],
            [p for p in paths[1:] if p.suffix == ".laz"],
            [p for p in paths[1:] if p.suffix == ".parquet"],
        )
    if dataset == "barriers":
        from .fences import prepare as prepare_barriers

        return prepare_barriers(tile, paths[0])
    if dataset == "forests":
        from .forests import read_areas

        return read_areas(tile, paths[0])
    if dataset == "trees":
        return prepare_trees(tile, paths[0])
    if dataset == "poles":
        from turku3d.props import read_sources

        signs, stops, roads, streets, osm = paths
        found, _ = read_sources(
            tile.bbox,
            source_paths={
                "GIS_Liikennemerkit": signs,
                "kanta_Pysakki": stops,
                "GIS_Keskilinjat": roads,
                "GIS_Katualueet": streets,
            },
            osm_path=osm,
        )
        return [p for p in found if p.kind in ("sign", "bus_stop")]
    bldg, streets, water, *roof_paths = paths
    return prepare_wfs(tile, bldg, streets, water, roof_paths)


def _entry(root: Path, tile: C.VisualTile, dataset: str, paths: list) -> tuple[Path, str]:
    """The cache file names its key, so checking for a current entry is one stat."""
    key = _key([p for p in paths if p], dataset)
    return root / ".inputs" / tile.id / f"{dataset}.{key}.pkl", key


def prepare(tile: C.VisualTile, root: Path, sources: Sources | None = None) -> dict:
    """Prepare every dataset whose raw files are on disk: {dataset: seconds or 'cached'}, and the
    datasets not on disk yet under 'missing'."""
    sources = sources or Sources()
    out: dict = {"missing": []}
    for dataset, paths in _sources(tile, sources).items():
        if paths is None:
            out["missing"].append(dataset)
            continue
        path, key = _entry(root, tile, dataset, paths)
        if path.exists():
            out[dataset] = "cached"
            continue
        t = time.perf_counter()
        _save(path, key, _prepare_one(tile, dataset, paths))
        out[dataset] = round(time.perf_counter() - t, 3)
    return out


def unprepared(tile: C.VisualTile, root: Path, sources: Sources) -> bool:
    """A dataset on disk has no current prepared entry (file stats only)."""
    for dataset, paths in _sources(tile, sources).items():
        if paths is not None and not _entry(root, tile, dataset, paths)[0].exists():
            return True
    return False


def gather(
    tile: C.VisualTile, root: Path, sources: Sources | None = None, *, first_pass: bool = False
) -> Inputs:
    """The tile's prepared inputs, preparing what is not cached yet. Raises MissingInputs (after
    preparing the datasets that are on disk) while units are missing."""
    sources = sources or Sources()
    missing = sources.missing(tile, first_pass=first_pass)
    if missing:
        if not first_pass:
            prepare(tile, root, sources)
        raise MissingInputs(tile.id, [u.key for u in missing])
    seconds, values, provenance = {}, {}, {}
    for dataset, paths in _sources(tile, sources, first_pass=first_pass).items():
        if first_pass and dataset in {
            "parking",
            "trees",
            "forests",
            "barriers",
            "poles",
            "surfaces",
            "markings",
            "kerbs",
            "appearance",
            "register",
            "ortho",
        }:
            # Collision-safe structural coverage precedes decorative inputs. These
            # are all restored by the full pass, using their ordinary caches.
            values[dataset] = (
                np.zeros((ORTHO_PX, ORTHO_PX, 3), np.uint8)
                if dataset == "ortho"
                else None
                if dataset == "kerbs"
                else {}
                if dataset in ("appearance", "register")
                else ()
            )
            seconds[dataset] = 0.0
            continue
        full_paths = paths
        if first_pass and paths and dataset == "wfs":
            paths = paths[:3]
        # Bridge geometry is structural: neighboring initial/full drafts must use
        # the same optional survey inputs and the same prepared road cache.
        if paths is None:  # only the optional roads and trees: no OSM clip yet
            values[dataset] = None
            continue
        t = time.perf_counter()
        cache_dataset = dataset + "-first" if first_pass and dataset == "wfs" else dataset
        path, key = _entry(root, tile, cache_dataset, paths)
        value = _ABSENT
        if first_pass and dataset == "wfs":
            full_path, full_key = _entry(root, tile, dataset, full_paths)
            value = _load(full_path, full_key)
        if value is _ABSENT:
            value = _load(path, key)
        if value is _ABSENT:
            if dataset == "wfs" and not first_pass:
                base_path, base_key = _entry(root, tile, "wfs-first", paths[:3])
                value = _load(base_path, base_key)
                if value is _ABSENT:
                    value = _prepare_one(tile, dataset, paths[:3])
                    _save(base_path, base_key, value)
                if value["buildings"] and paths[3:]:
                    roofs.refine(value["buildings"], paths[3:])
            else:
                value = _prepare_one(tile, dataset, paths)
            _save(path, key, value)
        values[dataset] = value
        provenance[dataset] = [str(p) if p else None for p in paths]
        seconds[dataset] = time.perf_counter() - t
    ground, wfs = values["ground"], values["wfs"]
    from .register import enrich

    for building in wfs["buildings"]:
        enrich(building, values["register"], values["appearance"] or {})
    return Inputs(
        ground["points"],
        ground["heights"],
        values["ortho"],
        wfs["buildings"],
        wfs["streets"],
        wfs["water"],
        values["roads"],
        provenance,
        seconds,
        sources.input_key(tile, first_pass=first_pass) or "",
        kerbs=values["kerbs"],
        markings=values["markings"] or (),
        surfaces=values["surfaces"] or (),
        register=values["register"],
        barriers=values["barriers"] or (),
        forests=values["forests"] or (),
        canopy=ground.get("canopy", np.empty((0, 3))),
        trees=values["trees"] or (),
        tunnels=values["tunnels"] or (),
        stairs=values["stairs"] or ((), ()),
        poles=values["poles"] or (),
        parking=values["parking"] or ((), ()),
    )
