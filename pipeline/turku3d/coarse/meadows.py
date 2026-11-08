"""Meadows: unmown grass instead of lawn, city-wide from data.

- The green-area register (WFS ``GIS:Viheralueet``) maintains each part to a class (``Kunnossapitoluokka_koodi``):
  R1-R4 built green and U sports turf are mown; A1-A5 (valuable, use and landscape meadows, open areas, landscape
  fields) are cut once or twice a summer, and EH is not maintained. Parts surveyed as meadow (``Viheralueosalaji``
  Niitty) are meadows whatever their class.
- OSM ``landuse=meadow`` and ``natural=grassland`` fill in where the register has no part (it covers the city's own
  green areas only).
- Fields (OSM ``landuse=farmland``) are mown hay or stubble in summer (``street_materials.FIELD``), not lawn; their
  edges keep a FIELD_MARGIN-wide unmown verge (ditch banks, headlands, road verges) of meadow.

Meadows are a visual finish (``street_materials.MEADOW``): the client grows meadow grass on them, and collision keeps
the grass class. Paths, paving, mapped finishes and the terrain stage's natural ground keep their own surfaces.
"""
import json

import shapely

from turku3d.spatial import frame, rows

from .street_materials import FIELD, MEADOW

MEADOW_CLASSES = ("A1", "A2", "A3", "A4", "A5", "EH")  # Kunnossapitoluokka_koodi of unmown parts
MEADOW_PARTS = ("Niitty",)  # Viheralueosalaji
OSM_MEADOWS = {"landuse": ("meadow",), "natural": ("grassland",)}
OSM_FIELDS = {"landuse": ("farmland",)}
FIELD_MARGIN = 2.5  # m: the unmown strip a mower leaves along a field's ditch, road or hedge (Kurala panoramas)
MIN_AREA = 25.0  # m2: smaller register slivers are edge strips, not meadows


def _polygonal(geom):
    parts = [g for g in shapely.get_parts(shapely.make_valid(geom)) if g.geom_type in ("Polygon", "MultiPolygon")]
    return shapely.union_all(parts) if parts else shapely.Polygon()


def _area(geom):
    if geom is None:
        return shapely.Polygon()
    geom = shapely.force_2d(geom)
    if geom.geom_type == "LineString" and geom.is_ring:
        geom = shapely.Polygon(geom)
    return _polygonal(geom)


def prepare(tile, register, osm):
    """The tile's meadows and fields (dataset ``green_areas``): [(MEADOW, polygon)] for the register's unmown parts and
    OSM meadow/grassland, [(FIELD, polygon)] for OSM farmland, in the engine frame."""
    areas = []
    if register is not None:
        for row in frame(register, tile.bbox.buffered(1)).itertuples():
            unmown = str(getattr(row, "Kunnossapitoluokka_koodi", "") or "") in MEADOW_CLASSES
            if unmown or str(getattr(row, "Viheralueosalaji", "") or "") in MEADOW_PARTS:
                areas.append((MEADOW, _area(row.geometry_local)))
    if osm is not None:
        for _, row in rows(osm, tile.bbox.buffered(1), columns=("tags", "geometry_local")):
            tags = json.loads(row["tags"]) if row.get("tags") else {}
            if any(tags.get(key) in values for key, values in OSM_MEADOWS.items()):
                areas.append((MEADOW, _area(row["geometry_local"])))
            elif any(tags.get(key) in values for key, values in OSM_FIELDS.items()):
                areas.append((FIELD, _area(row["geometry_local"])))
    return [(finish, a) for finish, a in areas if a.area >= MIN_AREA]


def regions(tile, areas, excluded=None):
    """[(polygon, MEADOW), (polygon, FIELD)] inside the tile, clear of ``excluded`` (paths, paving, natural ground).
    A field's outer FIELD_MARGIN is meadow; a meadow overlapping a field stays meadow."""
    if not len(areas):
        return []
    box = shapely.box(*tile.bbox.as_tuple())
    meadow = shapely.union_all([a for finish, a in areas if finish == MEADOW])
    # The whole field, beyond the tile too, decides its margin: a tile border is no field edge.
    field = shapely.union_all([a for finish, a in areas if finish == FIELD])
    margin = field.difference(field.buffer(-FIELD_MARGIN))
    result = []
    for finish, area in ((MEADOW, shapely.union_all([meadow, margin])), (FIELD, field.difference(meadow).difference(margin))):
        area = area.intersection(box)
        if excluded is not None and not excluded.is_empty:
            area = area.difference(excluded)
        area = _polygonal(area)
        if not area.is_empty and area.area >= MIN_AREA:
            result.append((area, finish))
    return result
