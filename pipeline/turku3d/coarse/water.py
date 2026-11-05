"""Continuous open water, with surveyed outer banks and a shared N2000 surface."""

from turku3d import cities
import json
from functools import lru_cache
from pathlib import Path

import shapely

from turku3d import config as C
from turku3d.ground.mesh import parts
from turku3d.spatial import frame

# The tidal river and sea share one datum. Tile-local lidar percentiles describe
# different banks, boats and bridges, not independent water surfaces.
LEVEL = cities.current().water_level  # Turku and Espoo: N2000 0 m; Amsterdam: canal level NAP -0.40
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


@lru_cache(maxsize=4)
def _bodies(identities):
    """Dissolved survey water and inset mapped interior of whole source files.

    ``identities`` are (path, size, mtime) of the survey and the OSM source (or None), so a
    persistent worker dissolves an area once for all of its tiles.
    """
    (survey, osm) = (path and Path(path[0]) for path in identities)
    surveyed = []
    if survey:
        for geometry in frame(survey).geometry_local:
            surveyed.extend(parts(shapely.make_valid(shapely.force_2d(geometry)), ("Polygon",)))
    interior = shapely.union_all(flowing_areas(osm)).buffer(-INTERIOR_MARGIN)
    return tuple(parts(shapely.union_all([*surveyed, interior]), ("Polygon",)))


def _identity(path):
    if not path:
        return None
    stat = Path(path).stat()
    return str(path), stat.st_size, stat.st_mtime_ns


def prepare(tile, survey, osm):
    """Dissolve before tile selection and fill survey breaks in mapped open water.

    Survey water stops at bridge cross sections and can omit small slivers. Its
    internal ends are not banks or islands. The inset mapped interior closes
    these breaks while surveyed coordinates still determine the outer shore.
    Mapped islands remain holes in the inset; isolated ponds are unchanged.
    Keep complete components so the tile window never becomes a shoreline.
    """
    window = shapely.box(*tile.bbox.as_tuple()).buffer(INTERIOR_MARGIN)
    return [g for g in _bodies((_identity(survey), _identity(osm))) if g.intersects(window)]
