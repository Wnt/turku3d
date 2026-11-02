"""Mapped foot/cycle footprints shared by vegetation clearance and forest ground visuals."""

import json

import numpy as np
import shapely


def path_lines(roads):
    """Foot/cycle-class graph lines; car-road clearance remains the existing two metres."""
    from turku3d.roads import CLASS_SHIFT
    from turku3d.roads.layer import graph_ways

    if not roads or not roads.get("json"):
        return []
    return [
        shapely.LineString(coords)
        for coords, flags in graph_ways(json.loads(roads["json"]))
        if flags >> CLASS_SHIFT == 0
    ]


def read_paths(tile, path):
    """Ground-level foot/cycle footprints, retaining mapped widths and area paths."""
    from turku3d.roads import CLASS_SHIFT, way_flags
    from turku3d.spatial import rows

    result = []
    from .canopy_trees import CONTEXT_HALO
    window = shapely.box(*tile.bbox.buffered(CONTEXT_HALO).as_tuple())
    for _, row in rows(path, tile.bbox.buffered(30)):
        tags = json.loads(row["tags"] or "{}")
        flags = way_flags(tags.get("highway"), {**tags, "area": "no"})
        if not flags or flags >> CLASS_SHIFT != 0:
            continue
        if (
            tags.get("bridge", "no") != "no"
            or tags.get("tunnel", "no") not in ("no", "building_passage")
            or tags.get("layer", "0") not in ("0", "")
        ):
            continue
        geom = shapely.force_2d(shapely.make_valid(row["geometry_local"]))
        if tags.get("area") == "yes" and geom.geom_type == "LineString" and geom.is_ring:
            geom = shapely.make_valid(shapely.Polygon(geom))
        if geom.geom_type in ("LineString", "MultiLineString"):
            default = (
                2.5
                if tags.get("highway") in ("path", "footway", "steps", "cycleway")
                else 4
                if tags.get("highway") == "track"
                else 6
            )
            try:
                width = float(str(tags.get("width", default)).removesuffix(" m"))
            except ValueError:
                width = default
            if not np.isfinite(width) or not 0.3 <= width <= 30:
                width = default
            geom = geom.buffer(width / 2)
        if geom.geom_type in ("Polygon", "MultiPolygon") and geom.intersects(window):
            result.append(geom.intersection(window))
    return result


def path_corridors(inputs):
    paths = getattr(inputs, "vegetation_paths", None)
    return (
        shapely.union_all(paths)
        if paths is not None
        else shapely.union_all(path_lines(inputs.roads)).buffer(1.25)
    )


def path_surfaces(inputs):
    """Expose forest paths in visual ground only, preserving terrain/structural caches."""
    from turku3d.col.materials import mat_id

    from .forests import DENSITY

    forest = shapely.union_all([geom for _, geom, kind, _ in inputs.forests if kind in DENSITY])
    if forest.is_empty:
        return []
    corridors = polygonal(path_corridors(inputs).intersection(forest))
    if corridors.is_empty:
        return []
    # Gravel fallback for forest tracks; retain mapped asphalt/paving/soil where available.
    mapped = [
        (polygonal(geom.intersection(corridors)), material)
        for geom, material in inputs.surfaces
        if material != mat_id("grass") and geom.intersects(corridors)
    ]
    return [(corridors, mat_id("gravel")), *[(g, m) for g, m in mapped if not g.is_empty]]


def polygonal(geom):
    # Snapped, valid, area-only regions: raw buffer/intersection slivers made the surface overlay
    # in split_boundaries fail with GEOS "Unable to determine overlay result geometry dimension".
    geom = shapely.make_valid(shapely.set_precision(geom, 0.01))
    parts = [
        g
        for g in shapely.get_parts(geom)
        if g.geom_type in ("Polygon", "MultiPolygon") and g.area > 1e-4
    ]
    return shapely.union_all(parts) if parts else shapely.Polygon()
