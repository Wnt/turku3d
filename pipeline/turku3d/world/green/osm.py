"""OSM trees and hedges of a survey box, from the raw extracts when they cover it (none are fetched here)."""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

from ... import config

ROUGH_NATURAL = ("wood", "scrub", "grassland", "heath")
REACH = 1500.0  # an extract is named by a corner of its 500 m block and clipped with a margin


@lru_cache(maxsize=1)
def extracts() -> list[tuple[float, float, Path]]:
    root = config.raw_dir("osm")
    rows = []
    for folder in sorted(root.iterdir()) if root.is_dir() else []:
        match = re.fullmatch(r"coarse-(\d+)_(\d+)", folder.name)
        files = sorted(folder.glob("*.parquet")) if match else []
        if files:
            rows.append((float(match[1]), float(match[2]), files[-1]))
    return rows


@lru_cache(maxsize=32)
def frame(path: Path):
    import geopandas as gpd

    data = gpd.read_parquet(path, columns=["osm_type", "osm_id", "tags", "natural", "barrier", "geometry"])
    rough = data["natural"].isin(ROUGH_NATURAL) | data["tags"].astype(str).str.contains(r'"landuse":\s*"(?:meadow|forest)"', regex=True)
    return data[(data["natural"] == "tree") | (data["barrier"] == "hedge") | (rough & data.geometry.geom_type.isin(("Polygon", "MultiPolygon")))]


def features(box: tuple[float, float, float, float]) -> dict[str, list[dict]]:
    """{"trees": [{e, n, id, leaf_type?, genus?, species?}], "hedges": [{id, line: [[e, n], ...]}], "rough":
    [{id, shape, tags: {landuse?, natural?}}] (meadows, grassland, scrub, woods as polygons)} in the box."""
    import shapely

    query = shapely.box(*box)
    trees: dict[int, dict] = {}
    hedges: dict[int, dict] = {}
    rough: dict[int, dict] = {}
    for e0, n0, path in extracts():
        if not (e0 - REACH <= box[0] and box[2] <= e0 + REACH and n0 - REACH <= box[1] and box[3] <= n0 + REACH):
            continue
        data = frame(path)
        hit = data[data.geometry.intersects(query)]
        for osm_id, tags, natural, barrier, shape in zip(hit["osm_id"], hit["tags"], hit["natural"], hit["barrier"], hit.geometry):
            tags = json.loads(tags) if isinstance(tags, str) else {}
            if barrier != "hedge" and natural != "tree":
                rough[int(osm_id)] = {"id": int(osm_id), "shape": shapely.make_valid(shapely.force_2d(shape)),
                                      "tags": {key: tags[key] for key in ("landuse", "natural") if key in tags}}
            elif natural == "tree" and shape.geom_type == "Point":
                row = {"e": round(shape.x, 2), "n": round(shape.y, 2), "id": int(osm_id)}
                row.update({key.replace(":", "_"): tags[key] for key in ("leaf_type", "leaf_cycle", "genus", "species", "species:fi", "taxon") if key in tags})
                trees[int(osm_id)] = row
            elif natural != "tree" and shape.geom_type in ("LineString", "Polygon"):
                line = shape.exterior if shape.geom_type == "Polygon" else shape
                hedges[int(osm_id)] = {"id": int(osm_id), "line": [[round(x, 2), round(y, 2)] for x, y in line.coords]}
    return {"trees": sorted(trees.values(), key=lambda t: t["id"]), "hedges": sorted(hedges.values(), key=lambda h: h["id"]),
            "rough": sorted(rough.values(), key=lambda r: r["id"])}
