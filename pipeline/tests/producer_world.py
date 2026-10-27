"""A tiny synthetic city for per-tile producer tests: raw WFS/OSM inputs and a flat ground layer.

Inputs go to `$TURKU3D_DATA/raw/{wfs,osm}/<area>` exactly as the fetchers write them
(GeoParquet with `geometry_local` in engine x/z); the ground layer is flat collision at
GROUND_Y under every collision tile of the given L0 tiles."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from turku3d import config as C
from turku3d.col.materials import mat_id
from turku3d.layers import CollisionLayer, layer_root, write_collision, write_manifest

GROUND_Y = 3.0


OSM_COLUMNS = (
    "highway",
    "building",
    "barrier",
    "kerb",
    "amenity",
    "natural",
    "landuse",
    "crossing",
    "entrance",
    "step_count",
    "layer",
    "name",
)


def frame(rows, geometries, columns=()):
    """Fetch layout: `geometry` in EPSG:3877, `geometry_local` in engine x/z."""
    import geopandas as gpd
    import pandas as pd
    import shapely

    def to_en(xz):
        return np.c_[xz[:, 0] + C.ORIGIN_E, C.ORIGIN_N - xz[:, 1], xz[:, 2:]]

    en = [shapely.transform(g, to_en, include_z=g.has_z) for g in geometries]
    rows = pd.DataFrame(rows, columns=list(columns) or None)
    data = gpd.GeoDataFrame(rows, geometry=gpd.GeoSeries(en), crs=C.CRS_HORIZONTAL)
    data["geometry_local"] = gpd.GeoSeries(geometries)
    return data


def write_wfs(
    name: str, rows: list[dict], geometries: list, area: str = "work-3x3", columns=()
) -> Path:
    path = C.raw_dir("wfs") / area / f"{name}.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    frame(rows, geometries, columns).to_parquet(path)
    return path


def write_osm(features: list[tuple[dict, object]], area: str = "work-3x3") -> Path:
    """OSM rows: (tags, engine x/z geometry). Columns follow the OSM fetch GeoParquet."""
    rows = [
        {
            "osm_type": "way" if g.geom_type != "Point" else "node",
            "osm_id": i + 1,
            "tags": json.dumps(tags),
            **{k: tags.get(k) for k in OSM_COLUMNS},
        }
        for i, (tags, g) in enumerate(features)
    ]
    path = C.raw_dir("osm") / area / f"{area}-261024.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    frame(rows, [g for _, g in features]).to_parquet(path)
    return path


def city(area: str = "work-3x3", streets=(), centrelines=()) -> None:
    """The city layers placement reads; streets are (Katuosalaji, polygon) pairs."""
    columns = ("Katuosalaji", "Kadunnimi", "feature_key")
    write_wfs(
        "GIS_Katualueet",
        [{"Katuosalaji": kind, "feature_key": f"street{i}"} for i, (kind, _) in enumerate(streets)],
        [g for _, g in streets],
        area,
        columns,
    )
    write_wfs(
        "GIS_Keskilinjat",
        [{"Katuosalaji": "Ajorata", "feature_key": f"line{i}"} for i in range(len(centrelines))],
        list(centrelines),
        area,
        columns,
    )
    write_wfs("GIS_Rakennukset", [], [], area, ("feature_key",))


def ground(tiles, material: str = "sidewalk") -> Path:
    """Flat ground collision under the tiles and their margin ring, as the ground layer ships."""
    directory = layer_root() / "ground"
    names = []
    cells = {
        cell
        for tile in map(C.VisualTile.parse, tiles)
        for cell in C.collision_tiles_for_bbox(tile.bbox.buffered(C.MESH_MARGIN))
    }
    for cell in sorted(cells):
        x, _, z = cell.origin
        s = C.COLLISION_TILE_SIZE
        corners = [(x, z), (x + s, z), (x, z + s), (x + s, z + s)]
        positions = np.array([[cx, GROUND_Y, cz] for cx, cz in corners])
        indices = np.array([[0, 2, 1], [1, 2, 3]], np.uint32)
        name = f"{cell.id}.col.npz"
        write_collision(
            directory / name,
            CollisionLayer(positions, indices, np.full(2, mat_id(material), np.uint8)),
        )
        names.append(name)
    write_manifest(directory, inputs={}, params={"seams": "continuous"}, code="flat", tiles=names)
    return directory


def ortho(area: str = "work-3x3") -> Path:
    """A plain grey 2 m/px image over work-3x3 where the ortho VRT lives (GDAL sniffs GeoTIFF)."""
    import rasterio
    from rasterio.transform import Affine

    path = C.raw_dir("ortho") / "ortho2022to" / f"{area}.vrt"
    path.parent.mkdir(parents=True, exist_ok=True)
    bounds = C.area(area).core.to_en()
    width = int((bounds.emax - bounds.emin) / 2)
    height = int((bounds.nmax - bounds.nmin) / 2)
    profile = dict(driver="GTiff", width=width, height=height, count=3, dtype="uint8")
    transform = Affine(2, 0, bounds.emin, 0, -2, bounds.nmax)
    with rasterio.open(path, "w", transform=transform, crs=C.CRS_HORIZONTAL, **profile) as ds:
        ds.write(np.full((3, height, width), 128, np.uint8))
    return path


def lidar(points: np.ndarray, classes: np.ndarray, bbox: C.BBoxLocal) -> list[Path]:
    """Engine x/y/z points as city pistepilvi LAZ files, one per 500 m tile under bbox."""
    import laspy

    paths = []
    e, n, h = C.local_to_en(points[:, 0], points[:, 1], points[:, 2])
    for tile in C.laz_tiles_for_bbox(bbox):
        box_en = tile.bbox_en
        inside = (e >= box_en.emin) & (e < box_en.emax) & (n > box_en.nmin) & (n <= box_en.nmax)
        header = laspy.LasHeader(point_format=3, version="1.2")
        header.offsets, header.scales = [box_en.emin, box_en.nmin, 0], [0.01, 0.01, 0.01]
        las = laspy.LasData(header)
        las.x, las.y, las.z = e[inside], n[inside], h[inside]
        las.classification = classes[inside].astype(np.uint8)
        path = C.raw_dir("laz") / "pistepilvi" / f"{tile.name}.laz"
        path.parent.mkdir(parents=True, exist_ok=True)
        las.write(path)
        paths.append(path)
    return paths


def lod2(buildings: list[tuple[str, object, float, float]], area: str = "work-3x3") -> Path:
    """CityGML LOD2 rows (ground and roof surface per building) and the fetch summary."""
    import shapely

    rows, geometries = [], []
    for key, footprint, base, top in buildings:
        for kind, height in (("GroundSurface", base), ("RoofSurface", top)):
            ring = [(x, z, height) for x, z in footprint.exterior.coords]
            rows.append(
                {"feature_key": key, "surface_type": kind, "measuredHeight": str(top - base)}
            )
            geometries.append(shapely.Polygon(ring))
    path = write_wfs("bldg_Building_LOD2", rows, geometries, area)
    bounds = C.area(area).core.to_en().as_tuple()
    summary = {"layer": "bldg:Building_LOD2", "status": "ok", "bbox_en": list(bounds)}
    path.with_name("bldg_Building_LOD2.summary.json").write_text(json.dumps(summary))
    return path
