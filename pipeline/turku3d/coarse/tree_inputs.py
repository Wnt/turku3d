"""Register trees (GIS:Puut) for the prepared ``trees`` input (``inputs.prepare_trees``).

This file is a dependency of that input only (``inputs.DEPENDENCIES``), so a change here
re-prepares trees and nothing else.
"""

from __future__ import annotations

from pathlib import Path

import shapely

from turku3d import config as C
from turku3d.spatial import frame as spatial_frame

REGISTER_TREE = "puut:"  # object-id prefix of register trees (forests.produce trusts them)
TREE_DUPLICATE = 3.0  # m: an OSM tree this close to a register tree is that tree
CONIFERS = ("pinus", "picea", "abies", "taxus", "thuja", "juniperus", "pseudotsuga", "tsuga")
SURVEY_YEAR = 2026
NURSERY_HEIGHT, GROWTH, MAX_HEIGHT, UNKNOWN_HEIGHT = 4.0, 0.35, 22.0, 10.0


def register_trees(tile: C.VisualTile, path: Path, streets: Path | None = None) -> list:
    """GIS:Puut street and park trees in the tile: (id, x, z, height, conifer). Height grows with
    the planting year (nursery stock about 4 m, 0.35 m a year, at most 22 m); unknown years get
    the OSM default of 10 m. A row on a carriageway (GIS:Katualueet Ajorata) is not drawn."""
    carriageway = shapely.Polygon()
    if streets is not None:
        frame = spatial_frame(streets, tile.bbox)
        if len(frame):
            carriageway = shapely.union_all(
                [
                    shapely.make_valid(shapely.force_2d(g))
                    for g, kind in zip(frame.geometry_local, frame["Katuosalaji"], strict=True)
                    if kind == "Ajorata" and g is not None
                ]
            )
    trees = []
    for row in spatial_frame(path, tile.bbox).to_dict("records"):
        point = row["geometry_local"]
        if point is None or point.geom_type != "Point" or not tile.bbox.contains(point.x, point.y):
            continue
        if not carriageway.is_empty and carriageway.covers(shapely.Point(point.x, point.y)):
            continue
        try:
            year = int(float(row.get("Istutusvuosi") or 0))
        except ValueError:
            year = 0
        height = (
            min(MAX_HEIGHT, NURSERY_HEIGHT + GROWTH * (SURVEY_YEAR - year))
            if 1900 < year <= SURVEY_YEAR
            else UNKNOWN_HEIGHT
        )
        species = str(row.get("Kasvilaji") or "").casefold()
        conifer = str(row.get("Kasviryhma") or "").casefold().startswith("havu") or any(
            species.startswith(genus) for genus in CONIFERS
        )
        trees.append((f"{REGISTER_TREE}{row['feature_key']}", point.x, point.y, height, conifer))
    return trees
