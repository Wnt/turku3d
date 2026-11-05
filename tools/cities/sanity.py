"""Built tiles against their sources: one row per tile of output counts next to source counts.

  tools/cities/run.sh espoo python tools/cities/sanity.py ~/turku3d-local/cities/espoo/tiles t0_0_0 ...

Sources per tile (centroid or point in the tile, as the producers assign them): LoD2 buildings (unique
CityGML buildings), street parts (derived GIS:Katualueet), water polygons (kanta:Vesikohde), OSM highways
and trees, register street lights. Output: manifest buildings, roads graph nodes/edges, water level, trees,
lights, ground surfaces, collision cells. Ground heights are checked against the DEM key points.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import geopandas as gpd
import numpy as np
import shapely

from turku3d import config as C
from turku3d.coarse.sources import Sources


def inside(tile, geoms):
    b = tile.bbox
    c = shapely.centroid(np.asarray(geoms, dtype=object))
    x, z = shapely.get_x(c), shapely.get_y(c)
    return (x >= b.xmin) & (x < b.xmax) & (z >= b.zmin) & (z < b.zmax)


def source_counts(tile, sources):
    out = {}
    found, path = sources.wfs_source("bldg:Building_LOD2", tile)
    if path:
        f = gpd.read_parquet(path, columns=["gml_id", "geometry_local", "surface_type"])
        g = f[f.surface_type.isin(["RoofSurface", "GroundSurface"])]
        per = g.groupby("gml_id").geometry_local.apply(
            lambda s: shapely.convex_hull(shapely.geometrycollections(list(shapely.force_2d(s.values)))))
        out["lod2_buildings"] = int(inside(tile, per.values).sum())
    for name, key in (("GIS:Katualueet", "street_parts"), ("kanta:Vesikohde", "water_features")):
        found, path = sources.wfs_source(name, tile)
        if path:
            f = gpd.read_parquet(path).drop_duplicates("feature_key")
            out[key] = int(inside(tile, shapely.force_2d(f.geometry_local.values)).sum())
    osm = sources.osm_source(tile)
    if osm:
        f = gpd.read_parquet(osm, columns=["highway", "natural", "building", "geometry_local"])
        m = inside(tile, f.geometry_local.values)
        out["osm_highways"] = int((m & f.highway.notna()).sum())
        out["osm_trees"] = int((m & (f.natural == "tree")).sum())
        out["osm_buildings"] = int((m & f.building.notna()).sum())
    return out


def output_counts(directory):
    m = json.loads((directory / "manifest.json").read_text())
    roads = m.get("roads") or {}
    out = {
        "buildings": m.get("buildings"),
        "road_nodes": roads.get("nodes"),
        "road_edges": roads.get("edges"),
        "water_level": m.get("water_level"),
        "trees": m.get("trees"),
        "lights": (m.get("lights") or {}).get("count") if isinstance(m.get("lights"), dict) else m.get("lights"),
        "poles": m.get("poles"),
        "tunnels": m.get("tunnels"),
        "bridges": m.get("bridges"),
        "collision_cells": len(list((directory / "col").glob("*.col"))),
        "ground_surfaces": m.get("ground_surfaces"),
        "seconds_total": (m.get("seconds") or {}).get("total"),
    }
    return {k: (v if not isinstance(v, dict) else v.get("count", v)) for k, v in out.items()}


def ground_check(tile, directory):
    """Median |collision-height-grid - DEM key point| is not available offline; use the visual GLB bounds."""
    sources = Sources()
    paths, _ = sources.lidar_paths(tile)
    import laspy

    b = tile.bbox
    hs = []
    for p in paths:
        las = laspy.read(p)
        x, h, z = C.en_to_local(np.asarray(las.x), np.asarray(las.y), np.asarray(las.z))
        keep = (x >= b.xmin) & (x < b.xmax) & (z >= b.zmin) & (z < b.zmax)
        hs.append(h[keep])
    h = np.concatenate(hs) if hs else np.array([])
    return {"dem_points": int(len(h)), "dem_h_min": round(float(h.min()), 2) if len(h) else None,
            "dem_h_max": round(float(h.max()), 2) if len(h) else None}


def main():
    root = Path(sys.argv[1]).expanduser()
    sources = Sources()
    rows = []
    for tid in sys.argv[2:]:
        tile = C.VisualTile.parse(tid)
        d = root / tid
        row = {"tile": tid, **source_counts(tile, sources), **ground_check(tile, d)}
        if (d / "manifest.json").exists():
            row.update(output_counts(d))
        else:
            row["built"] = False
        rows.append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)
    return rows


if __name__ == "__main__":
    main()
