"""Bridges over water, as far as the ground carries them: the deck's level across the span.

The lidar's ground under a bridge is the water, so a street on a bridge fell from one bank to the water's edge and
climbed out on the other. A deck is found from OpenStreetMap (a way with a `bridge` tag that crosses mapped water)
and takes the street register's `Silta` polygons around the way as its outline, or a strip as wide as its class
where the register has none. Relief nodes on the deck take the level of the straight line between the ground at the
two ends of the span, measured just beyond them where the scan no longer mixes bank and water. The answer depends
on the place alone, so neighbouring cells agree.

UNFINISHED (2026-11-13): the deck is part of the single-valued skin, so its sides fall to the river bed as a bank
and nothing passes under it; an open span with piers, girders and railings is a kind of its own, still to write.
Bridges over land stay as the scan has them (the street dips to the level below, both stay passable).
"""

from __future__ import annotations

import json

import numpy as np
import shapely

from ...street import osm, registers

MIN_SPAN = 8.0      # m: shorter bridge ways are gangways and culverts
AROUND = 12.0       # m either side of a bridge way within which a register deck polygon belongs to it
LANE = 3.5          # m of deck per lane where the register maps no deck
PATH = 3.5          # m of deck for a foot or cycle bridge where the register maps none
BEYOND = np.arange(0.0, 8.5, 1.0)   # m beyond an end of the span at which the bank's level is looked for
EDGE = 0.75         # m around the deck's outline that takes the deck's level too (the relief's node spacing)
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


def spans(box, window) -> list[dict]:
    """Decks over water that touch the box: `key`, `area` (outline), `axis` (the way along the span), `ends`
    (the level at the axis's start and end), `mapped` (the register draws the deck's surface)."""
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
    for row, tags in ways:
        line = row.geometry
        own = shapely.unary_union([part for part in shapely.get_parts(silta.intersection(line.buffer(AROUND, cap_style='flat')))
                                   if part.geom_type == 'Polygon' and part.intersects(line)] or [shapely.Polygon()])
        half = (_lanes(row) * LANE + 1.0) / 2 if row.highway in MAJOR else PATH / 2
        strip = line.buffer(half, cap_style='flat')
        found.append({'key': f'osm:way/{row.osm_id}', 'line': line, 'major': row.highway in MAJOR,
                      'area': own if not own.is_empty else strip, 'mapped': not own.is_empty})
    # Ways side by side on one bridge (a carriageway between two footways) are one deck with one level: the widest
    # street's way is its axis.
    decks = []
    whole = shapely.unary_union([deck['area'] for deck in found])
    for part in shapely.get_parts(whole):
        if part.geom_type != 'Polygon':
            continue
        on = [deck for deck in found if deck['area'].intersects(part)]
        axis = max(on, key=lambda deck: (deck['major'], deck['line'].length))
        line = axis['line']
        ends = []
        for at, toward in ((0.0, min(1.0, line.length)), (line.length, max(line.length - 1.0, 0.0))):
            here, inner = np.asarray(line.interpolate(at).coords[0]), np.asarray(line.interpolate(toward).coords[0])
            out = here - inner
            out /= max(np.linalg.norm(out), 1e-9)
            points = here[None, :] + out[None, :] * BEYOND[:, None]
            ends.append(float(np.nanmax(window.sample('ground', points[:, 0], points[:, 1]))))
        decks.append({'key': axis['key'], 'area': part, 'axis': line, 'ends': tuple(ends),
                      'mapped': all(deck['mapped'] for deck in on)})
    return decks


def held(heights: np.ndarray, east: np.ndarray, north: np.ndarray, decks: list[dict]) -> np.ndarray:
    """Relief heights with the nodes on a deck (and EDGE around it) at the deck's level."""
    if not decks:
        return heights
    out = np.array(heights, dtype=float)
    points = shapely.points(east, north)
    for deck in decks:
        e0, n0, e1, n1 = deck['area'].bounds
        near = np.flatnonzero((east > e0 - EDGE) & (east < e1 + EDGE) & (north > n0 - EDGE) & (north < n1 + EDGE))
        if not len(near):
            continue
        near = near[shapely.distance(points[near], deck['area']) <= EDGE]
        if not len(near):
            continue
        share = shapely.line_locate_point(deck['axis'], points[near]) / max(deck['axis'].length, 1e-9)
        level = deck['ends'][0] + (deck['ends'][1] - deck['ends'][0]) * share
        # A deck never lies under the ground the scan found (the abutments, a pier that stands above it).
        out[near] = np.maximum(level, np.where(np.isfinite(out[near]), out[near], level))
    return out
