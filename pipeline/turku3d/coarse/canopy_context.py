"""Exclusion surfaces around a crown-processing tile, independent of render ownership."""
import json

import shapely

from .canopy_trees import CONTEXT_HALO


def prepare(tile, streets, water, osm):
    from turku3d.roads import CLASS_SHIFT, way_flags
    from turku3d.spatial import rows

    bounds = tile.bbox.buffered(CONTEXT_HALO)
    street_shapes, carriageways, waters, lanes = [], [], [], []
    for _, row in rows(streets, bounds):
        geometry = shapely.make_valid(shapely.force_2d(row["geometry_local"]))
        street_shapes.append(geometry)
        if row.get("Katuosalaji") == "Ajorata":
            carriageways.append(geometry)
    for _, row in rows(water, bounds):
        waters.append(shapely.make_valid(shapely.force_2d(row["geometry_local"])))
    for _, row in rows(osm, bounds.buffered(2)):
        tags = json.loads(row["tags"] or "{}")
        flags = way_flags(tags.get("highway"), tags)
        if not flags or flags >> CLASS_SHIFT == 0:
            continue
        if (tags.get("bridge", "no") != "no" or tags.get("tunnel", "no") != "no"
                or tags.get("layer", "0") not in ("0", "")):
            continue
        geometry = shapely.force_2d(row["geometry_local"])
        if geometry.geom_type in ("LineString", "MultiLineString"):
            lanes.append(geometry.buffer(2))
    return {"water": waters, "streets": street_shapes, "lanes": [*carriageways, *lanes]}
