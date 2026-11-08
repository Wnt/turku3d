"""Mast lighting of open city squares that no surveyed lamp lights (no per-square data).

Turku's large squares are lit by tall masts standing on the square, which neither street-light
register maps: on Kauppatori (t0_0_-1) the tile has posts round the north-west edge only, while
the 2026 panoramas from the surrounding streets show slender masts across the square. The lamp lists
leave such a square dark.

A class rule for every mapped square:

* **Squares** are OSM ``place=square`` areas and pedestrian areas (``highway=pedestrian`` +
  ``area=yes``, ``area:highway=pedestrian``) of ``MIN_AREA`` to ``MAX_AREA``, at ground level and not
  ``lit=no``, that fill at least ``COMPACT`` of their frame. An area mostly inside a larger one is
  part of it (a pedestrian area mapped inside ``place=square``).
* **Dark** when the part farther than ``REACH`` from every mapped lamp (OSM street lamps and lighting
  masts: the street-light register is not among the fetched layers) is at least ``DARK_SHARE`` of the
  square and ``DARK_AREA``.
* **Masts** stand in the middle of the cells of a grid of about ``SPACING`` along the square's long
  axis and across it, at least ``EDGE`` inside the square and ``CLEAR`` from any mapped lamp,
  ``HEIGHT`` tall. A dark square with no such cell centre gets one mast in its dark part.

A dark square becomes a ``square`` venue of the night floodlights (``coarse.floodlights``): its frame
is lit by the client's area light at ``LEVEL`` and its masts carry lamp heads. Each tile lists the
squares whose frame centre it owns and the masts standing in it. Every tile reads a square whole and
the lamps round it from the square's own bounds, so neighbours agree.
"""

from __future__ import annotations

import json

import numpy as np
import shapely

from turku3d.coarse import floodlights as F

KIND = "square"
MIN_AREA = 2500.0  # m2: smaller squares and plazas are lit by the lamps along their edges
MAX_AREA = 40000.0  # m2, as a sports venue
REACH = 20.0  # m a mapped lamp lights round itself (props LAMP_REACH)
DARK_SHARE = 0.4  # share of a square beyond every lamp's reach that makes it a dark square
DARK_AREA = 1500.0  # m2 beyond every lamp's reach that makes it a dark square
SPACING = 40.0  # m between masts
EDGE = 3.0  # m inside the square's edge
CLEAR = 12.0  # m from a mapped lamp
HEIGHT = 14.0  # m
LEVEL = 0.25  # area-light level (stadium 1): about the light under a street lamp, over the whole square
SEARCH = F.SEARCH  # m round the tile for squares reaching it
INSIDE = 0.5  # share of an area inside a larger square that makes it part of that square
# Least share of its frame (minimum rotated rectangle) a square fills: the client lights the frame
# evenly, so a long or bent pedestrian area would light the buildings round it.
COMPACT = 0.6


def _tags(row):
    return json.loads(row["tags"]) if isinstance(row["tags"], str) else dict(row["tags"] or {})


def _is_square(tags):
    if tags.get("lit") == "no" or tags.get("indoor") == "yes" or tags.get("building"):
        return False
    if tags.get("layer", "0") not in ("0", "") or tags.get("covered", "no") != "no":
        return False
    return tags.get("place") == "square" or tags.get("area:highway") == "pedestrian" or (
        tags.get("highway") == "pedestrian" and tags.get("area") == "yes")


def _members(osm, box):
    """(polygon, id) of every square area in ``box`` (BBoxLocal), each read whole."""
    from turku3d.spatial import rows

    out, seen = [], set()
    for key, row in rows(osm, box):
        tags = _tags(row)
        if not _is_square(tags):
            continue
        identity = f"osm:{row.get('osm_type', 'way')}:{row.get('osm_id', key)}"
        if identity in seen:
            continue
        seen.add(identity)
        polygons = F._polygons(shapely.force_2d(row["geometry_local"]))
        if polygons:
            out.append((shapely.union_all(polygons), identity))
    return out


def _box(geometry, margin):
    from turku3d import config as C

    xmin, zmin, xmax, zmax = geometry.bounds
    return C.BBoxLocal(xmin - margin, zmin - margin, xmax + margin, zmax + margin)


def squares(osm, bbox):
    """(polygon, id) of the squares reaching ``bbox`` (BBoxLocal) by ``SEARCH``, largest first: an area
    mostly inside a larger one (a pedestrian area mapped inside ``place=square``) is part of it.
    Features are read whole and a containing area reaches every box its parts reach, so every tile
    finds the same squares."""
    out = []
    for polygon, identity in sorted(_members(osm, bbox.buffered(SEARCH)), key=lambda m: (-m[0].area, m[1])):
        if any(polygon.intersection(kept).area >= INSIDE * polygon.area for kept, _ in out):
            continue
        out.append((polygon, identity))
    return [(p, i) for p, i in out if MIN_AREA <= p.area <= MAX_AREA and p.area >= COMPACT * _frame_area(p)]


def _frame_area(polygon):
    _, _, length, width = F._frame(polygon)
    return length * width


def lamp_points(osm, polygon):
    """Mapped lamps that can light ``polygon``: OSM street lamps and lighting masts, as points."""
    from turku3d.spatial import rows

    points = []
    for _, row in rows(osm, _box(polygon, REACH + CLEAR)):
        tags = _tags(row)
        if tags.get("highway") == "street_lamp" or tags.get("man_made") == "street_lamp" or (
                tags.get("man_made") in ("mast", "tower") and tags.get("tower:type") == "lighting"):
            points.append(shapely.force_2d(row["geometry_local"]).centroid)
    return points


def _stations(extent):
    """Offsets from the middle of the centres of ``round(extent / SPACING)`` equal cells (at least one)."""
    n = max(1, round(extent / SPACING))
    return ((np.arange(n) + 0.5) / n - 0.5) * extent


def dark_square(polygon, identity, near):
    """The square as a ``floodlights.venues`` dict with its mast points, or None when it is lit.
    ``near``: the mapped lamps round it (points)."""
    lit = shapely.union_all([p.buffer(REACH, quad_segs=4) for p in near]) if near else shapely.Polygon()
    dark = polygon.difference(lit)
    if dark.area < max(DARK_AREA, DARK_SHARE * polygon.area):
        return None
    centre, u, length, width = F._frame(polygon)
    v = np.array([-u[1], u[0]])
    room = polygon.buffer(-EDGE)
    free = lambda p: room.contains(p) and all(p.distance(q) >= CLEAR for q in near)  # noqa: E731
    points = []
    for a in _stations(length):
        for b in _stations(width):
            p = shapely.Point(*(centre + u * a + v * b))
            if free(p):
                points.append(p)
    if not points:
        p = dark.point_on_surface()
        if free(p):
            points.append(p)
    if not points:
        return None
    masts = [dict(id=f"{KIND}:{identity}:mast:{i}", x=float(p.x), z=float(p.y), height=HEIGHT, mesh=0)
             for i, p in enumerate(points)]
    return dict(id=f"{KIND}:{identity}", kind=KIND, union=polygon, centre=centre, u=u, length=length,
                width=width, masts=masts, dark=dark.area)


def dark_squares(osm, bbox):
    """The dark squares reaching ``bbox``."""
    found = (dark_square(polygon, identity, lamp_points(osm, polygon)) for polygon, identity in squares(osm, bbox))
    return [v for v in found if v is not None]


def tile_square_lights(tile, osm, ground_at):
    """(venues, masts) floodlight records of the dark squares: the venues whose frame centre the
    tile owns and the masts standing in it."""
    if osm is None:
        return [], []
    bbox = tile.bbox
    owns = lambda x, z: bbox.xmin <= x < bbox.xmax and bbox.zmin <= z < bbox.zmax  # noqa: E731
    out_venues, out_masts = [], []
    for venue in dark_squares(osm, bbox):
        c = venue["centre"]
        if owns(*c):
            samples = [ground_at(*(c + venue["u"] * s * venue["length"])) for s in (-0.25, 0.0, 0.25)]
            samples = [y for y in samples if y is not None]
            if samples:
                out_venues.append(dict(
                    id=venue["id"], kind=KIND, sport=[], x=F._r(c[0]),
                    y=F._r(np.median(samples)), z=F._r(c[1]), ux=round(float(venue["u"][0]), 4),
                    uz=round(float(venue["u"][1]), 4), length=F._r(venue["length"]),
                    width=F._r(venue["width"]), level=LEVEL))
        for mast in venue["masts"]:
            if not owns(mast["x"], mast["z"]):
                continue
            base = ground_at(mast["x"], mast["z"])
            if base is None:
                continue
            out_masts.append(dict(
                id=mast["id"], venue=venue["id"], x=F._r(mast["x"]), y=F._r(base), z=F._r(mast["z"]),
                top=F._r(base + mast["height"]), aim=[F._r(c[0]), F._r(c[1])], lamps=F.lamps(mast["height"]),
                mesh=0))
    return out_venues, out_masts
