"""Continuous open water, with surveyed outer banks and a shared N2000 surface."""

import json

import shapely

from turku3d.ground.mesh import parts
from turku3d.spatial import frame

# The tidal river and sea share one datum. Tile-local lidar percentiles describe
# different banks, boats and bridges, not independent water surfaces.
LEVEL = 0.0
INTERIOR_MARGIN = 3.0


def flowing_areas(path):
    """Complete mapped water areas; centre lines cannot define a shoreline."""
    if not path:
        return []
    result = []
    for row in frame(path).itertuples():
        tags = json.loads(row.tags or "{}")
        if (tags.get("water") not in ("river", "stream", "canal", "sea")
                and tags.get("waterway") != "riverbank"):
            continue
        geometry = shapely.force_2d(row.geometry_local)
        if geometry.geom_type == "LineString" and geometry.is_ring:
            geometry = shapely.Polygon(geometry)
        result.extend(parts(shapely.make_valid(geometry), ("Polygon",)))
    return result


def prepare(tile, survey, osm):
    """Dissolve before tile selection and fill survey breaks in mapped open water.

    Survey water stops at bridge cross sections and can omit small slivers. Its
    internal ends are not banks or islands. The inset mapped interior closes
    these breaks while surveyed coordinates still determine the outer shore.
    Mapped islands remain holes in the inset; isolated ponds are unchanged.
    Keep complete components so the tile window never becomes a shoreline.
    """
    surveyed = []
    if survey:
        for geometry in frame(survey).geometry_local:
            surveyed.extend(parts(shapely.make_valid(shapely.force_2d(geometry)), ("Polygon",)))
    interior = shapely.union_all(flowing_areas(osm)).buffer(-INTERIOR_MARGIN)
    body = shapely.union_all([*surveyed, interior])
    window = shapely.box(*tile.bbox.as_tuple()).buffer(INTERIOR_MARGIN)
    return [g for g in parts(body, ("Polygon",)) if g.intersects(window)]
