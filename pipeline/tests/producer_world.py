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


def frame(rows, geometries):
    """Fetch layout: `geometry` in EPSG:3877, `geometry_local` in engine x/z."""
    import geopandas as gpd
    import shapely

    en = [
        shapely.transform(g, lambda xz: np.c_[xz[:, 0] + C.ORIGIN_E, C.ORIGIN_N - xz[:, 1]])
        for g in geometries
    ]
    data = gpd.GeoDataFrame(rows, geometry=gpd.GeoSeries(en), crs=C.CRS_HORIZONTAL)
    data["geometry_local"] = gpd.GeoSeries(geometries)
    return data


def write_wfs(name: str, rows: list[dict], geometries: list, area: str = "work-3x3") -> Path:
    path = C.raw_dir("wfs") / area / f"{name}.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    frame(rows, geometries).to_parquet(path)
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
    write_wfs(
        "GIS_Katualueet",
        [{"Katuosalaji": kind, "feature_key": f"street{i}"} for i, (kind, _) in enumerate(streets)],
        [g for _, g in streets],
        area,
    )
    write_wfs(
        "GIS_Keskilinjat",
        [{"Katuosalaji": "Ajorata", "feature_key": f"line{i}"} for i in range(len(centrelines))],
        list(centrelines),
        area,
    )
    write_wfs("GIS_Rakennukset", [], [], area)


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
    from rasterio.transform import from_origin

    path = C.raw_dir("ortho") / "ortho2022to" / f"{area}.vrt"
    path.parent.mkdir(parents=True, exist_ok=True)
    bounds = C.area(area).core.to_en()
    width = int((bounds.emax - bounds.emin) / 2)
    height = int((bounds.nmax - bounds.nmin) / 2)
    profile = dict(driver="GTiff", width=width, height=height, count=3, dtype="uint8")
    transform = from_origin(bounds.emin, bounds.nmax, 2, 2)
    with rasterio.open(path, "w", transform=transform, crs=C.CRS_HORIZONTAL, **profile) as ds:
        ds.write(np.full((3, height, width), 128, np.uint8))
    return path
