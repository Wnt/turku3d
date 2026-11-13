"""Bridges over water, as far as the ground carries them: where a deck lies and how high.

The lidar's ground under a bridge is the water and the banks under its ends, so a street on a bridge fell from one
bank to the water's edge and climbed out on the other. A deck is found from OpenStreetMap (a way with a `bridge` tag
that crosses mapped water) and takes the street register's `Silta` polygons around the way as its outline, or a
strip as wide as its class where the register has none. The mapped bridge usually ends over the bank, where the
scan's ground still climbs from the water to the street: the deck goes on to the abutment, the first place beyond
the way's end where that climb is over, and its top is the straight line between the two abutments' levels.

The ground kind draws the deck as a second sheet over its skin (`mesh.py`) with the slab's faces, and the `bridge`
kind the structure, parapets and pylons around it (`kinds/bridge`); the height raster carries the deck's level, so the
deck is what is walked and driven on. The answer depends on the place alone (every end reads its own piece of the
scan), so neighbouring cells and locations agree.

Bridges over land stay as the scan has them (the street dips to the level below, both stay passable).
"""

from __future__ import annotations

import json

import numpy as np
import shapely

from ...street import lidar, osm, registers

MIN_SPAN = 8.0      # m: shorter bridge ways are gangways and culverts
AROUND = 12.0       # m either side of a bridge way within which a register deck polygon belongs to it
LANE = 3.5          # m of deck per lane where the register maps no deck
PATH = 3.5          # m of deck for a foot or cycle bridge where the register maps none
GAP = 0.75          # m: register polygons of one bridge this near each other are one deck, the slit between them closed
REACH = 24          # m beyond an end of the mapped bridge within which the abutment is looked for
CLIMB = 0.25        # m of rise over 2 m: the scan's ground still climbs from under the deck
START = 6           # m: a climb that begins farther than this beyond the end is a hill, not the bank under the deck
KERB = 0.12         # m: a footway on a deck above its carriageway
SLAB = 0.9          # m from the deck's top to its underside
APRON = 1.5         # m around the outline that the height raster holds at the deck's level (collision samples it coarsely)
MAJOR = ('motorway', 'trunk', 'primary', 'secondary', 'tertiary', 'unclassified', 'residential', 'living_street', 'service',
         'motorway_link', 'trunk_link', 'primary_link', 'secondary_link', 'tertiary_link', 'pedestrian')


def _tags(row) -> dict:
    try:
        return json.loads(row.tags) if isinstance(row.tags, str) else {}
    except ValueError:
        return {}


def _lanes(row) -> int:
    try:
        return max(int(float(row.lanes)), 1)
    except (TypeError, ValueError):
        return 2


def abutment(ground: np.ndarray) -> int:
    """How many metres beyond the mapped end the deck goes on, from the scan's ground at every metre outwards: to the
    end of the climb that begins under the deck's end, 0 where the ground does not climb there."""
    rise = ground[2:] - ground[:-2]
    climbing = np.flatnonzero(rise > CLIMB)
    if not len(climbing) or climbing[0] > START:
        return 0
    k = int(climbing[0])
    while k + 1 < len(rise) and rise[k + 1] > CLIMB:
        k += 1
    return k + 2


def _beyond(line) -> tuple[list, list] | None:
    """The points where the deck ends beyond the two ends of the mapped way, and the ground's level there."""
    points, levels = [], []
    for at, toward in ((0.0, min(1.0, line.length)), (line.length, max(line.length - 1.0, 0.0))):
        here, inner = np.asarray(line.interpolate(at).coords[0]), np.asarray(line.interpolate(toward).coords[0])
        out = here - inner
        out /= max(np.linalg.norm(out), 1e-9)
        outward = here[None, :] + out[None, :] * np.arange(0.0, REACH + 1.0)[:, None]
        low, high = outward.min(axis=0), outward.max(axis=0)
        ground = lidar.Window(low[0] - 1, low[1] - 1, high[0] + 1, high[1] + 1).sample('ground', outward[:, 0], outward[:, 1])
        if not np.isfinite(ground).all():
            return None
        k = abutment(ground)
        points.append(outward[k])
        levels.append(float(ground[k]))
    return points, levels


def spans(box) -> list[dict]:
    """Decks over water that touch the box: `key`, `area` (outline), `axis` (the way, drawn on to both abutments),
    `ends` (the level at the axis's start and end), `mapped` (the register draws the deck's surface)."""
    mapped = osm.features(box)
    if mapped is None:
        return []
    water = mapped[mapped.geom_type.isin(('Polygon', 'MultiPolygon')) & (mapped.natural == 'water')]
    if not len(water):
        return []
    wet = shapely.unary_union([shapely.make_valid(g) for g in water.geometry])
    lines = mapped[(mapped.geom_type == 'LineString') & mapped.highway.notna() & (mapped.highway != 'steps')]
    ways = [(row, _tags(row)) for row in lines.itertuples()]
    ways = [(row, tags) for row, tags in ways if tags.get('bridge', 'no') != 'no' and row.geometry.length >= MIN_SPAN
            and row.geometry.intersects(wet)]
    if not ways:
        return []
    streets = registers.layer('GIS:Katualueet', box)
    silta = [shapely.make_valid(shapely.force_2d(row.geometry)) for row in streets.itertuples()
             if row.geometry.geom_type == 'Polygon' and str(getattr(row, 'Katuosalaji', '')).lower() == 'silta']
    silta = shapely.unary_union(silta) if silta else shapely.Polygon()
    found = []
    for row, tags in sorted(ways, key=lambda way: way[0].osm_id):
        beyond = _beyond(row.geometry)
        if beyond is None:
            continue
        (start, end), levels = beyond
        coords = np.asarray(row.geometry.coords)[:, :2]
        line = shapely.LineString(np.concatenate([[start], coords, [end]]))
        line = shapely.LineString(shapely.get_coordinates(shapely.remove_repeated_points(line, 0.01)))
        own = [part for part in shapely.get_parts(silta.intersection(line.buffer(AROUND, cap_style='flat')))
               if part.geom_type == 'Polygon' and part.intersects(row.geometry)]
        own = shapely.unary_union(own) if own else shapely.Polygon()
        half = (_lanes(row) * LANE + 1.0) / 2 if row.highway in MAJOR else PATH / 2
        found.append({'key': f'osm:way/{row.osm_id}', 'line': line, 'ends': levels, 'major': row.highway in MAJOR,
                      'own': own, 'half': half, 'way': row.geometry})
    # Ways side by side on one bridge (a carriageway between two footways) are one deck with one level: the widest
    # street's way is its axis, and the deck is as wide beyond the mapped bridge as on it. Register polygons of one
    # bridge that do not quite touch are one deck too.
    shapes = [deck['own'] if not deck['own'].is_empty else deck['line'].buffer(deck['half'], cap_style='flat') for deck in found]
    decks = []
    for group in shapely.get_parts(shapely.unary_union(shapes or [shapely.Polygon()]).buffer(GAP)):
        on = [(deck, shape) for deck, shape in zip(found, shapes) if shape.intersects(group)]
        if group.geom_type != 'Polygon' or not on:
            continue
        axis = max((deck for deck, _ in on), key=lambda deck: (deck['major'], deck['way'].length))
        body = shapely.unary_union([shape for _, shape in on])
        half = max(body.area / max(axis['way'].length, 1e-9) / 2, axis['half'])
        beyond = axis['line'].buffer(half, cap_style='flat').difference(axis['way'].buffer(half + AROUND, cap_style='flat'))
        area = shapely.unary_union([body, axis['line'].buffer(axis['half'], cap_style='flat'), beyond])
        area = area.buffer(GAP, join_style='mitre').buffer(-GAP, join_style='mitre')
        area = max((g for g in shapely.get_parts(area) if g.geom_type == 'Polygon'), key=lambda g: g.area, default=None)
        if area is None:
            continue
        decks.append({'key': axis['key'], 'area': shapely.Polygon(area.exterior), 'axis': axis['line'], 'ends': tuple(axis['ends']),
                      'mapped': all(not deck['own'].is_empty for deck, _ in on)})
    return sorted(decks, key=lambda deck: deck['key'])


def level(axis, ends, east, north) -> np.ndarray:
    """The deck's top (its carriageway) at points: the straight line between the abutments' levels."""
    share = shapely.line_locate_point(axis, shapely.points(east, north)) / max(axis.length, 1e-9)
    return ends[0] + (ends[1] - ends[0]) * share


def lifted(heights: np.ndarray, east: np.ndarray, north: np.ndarray, decks: list[dict], reach: float = 0.0) -> np.ndarray:
    """Ground heights with the points on a deck (and `reach` around it) at the deck's level: what stands or walks on
    a bridge stands on this. A deck never lies under the ground the scan found (the abutments)."""
    if not decks:
        return heights
    out = np.array(heights, dtype=float)
    points = shapely.points(east, north)
    for deck in decks:
        e0, n0, e1, n1 = deck['area'].bounds
        near = np.flatnonzero((east >= e0 - reach) & (east <= e1 + reach) & (north >= n0 - reach) & (north <= n1 + reach))
        if not len(near):
            continue
        near = near[shapely.distance(points[near], deck['area']) <= reach]
        if not len(near):
            continue
        top = level(deck['axis'], deck['ends'], east[near], north[near])
        out[near] = np.maximum(top, np.where(np.isfinite(out[near]), out[near], top))
    return out
