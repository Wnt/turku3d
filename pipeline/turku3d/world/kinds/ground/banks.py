"""Where open water meets built land: the lines along which a quay wall stands (the `wall` kind) and behind which
the ground holds its level up to the edge (the old generator's `coarse/banks.py kade_crest`)."""

from __future__ import annotations

import numpy as np
import shapely

from ...street import osm, registers

BUILT_REACH = 8.0   # m: built land this near the water outline makes the edge a quay (a planted strip may lie between)
WALL_REACH = 4.0    # m: a mapped wall or quay this near the water outline is the quay wall of that stretch
QUAY_REACH = 3.0    # m from the outline within which the lidar smears the drop; the crest is measured beyond it
# OpenStreetMap ways that are a wall standing at the water where they follow its outline (the old generator's
# `coarse/banks.py produce` masonry; along the Aura they are mapped as retaining walls on both banks).
WALLS = {'barrier': {'retaining_wall', 'wall', 'city_wall'}, 'man_made': {'quay'}}


def quay_lines(box):
    """(mapped open water, its outline where a quay stands), survey frame; (None, None) without water.

    A quay stands where built land (a polygon of the street register) lies within BUILT_REACH of the outline, or a
    mapped wall or quay (WALLS) within WALL_REACH, even when a grass embankment lies behind it. Green land alone
    beside water is a natural bank; whether a quay is tall enough to stand is the lidar's (the `wall` kind)."""
    mapped = osm.features(box)
    if mapped is None:
        return None, None
    areas = mapped[mapped.geom_type.isin(('Polygon', 'MultiPolygon')) & (mapped.natural == 'water')]
    if not len(areas):
        return None, None
    wet = shapely.unary_union([shapely.make_valid(g) for g in areas.geometry])
    streets = registers.layer('GIS:Katualueet', box)
    built = shapely.unary_union([shapely.make_valid(shapely.force_2d(g)) for g in streets.geometry if g.geom_type == 'Polygon']
                                or [shapely.Polygon()])
    walls = [shapely.make_valid(g) for key, values in WALLS.items() if key in mapped
             for g in mapped.geometry[mapped[key].isin(values).values & ~mapped.geom_type.isin(('Point',)).values]]
    hard = built.buffer(BUILT_REACH)
    if walls:
        lines = shapely.unary_union([g.boundary if g.geom_type in ('Polygon', 'MultiPolygon') else g for g in walls])
        hard = hard.union(lines.buffer(WALL_REACH))
    return wet, wet.boundary.intersection(hard)


RING = np.array([[np.cos(turn), np.sin(turn)] for turn in np.arange(8) * np.pi / 4])


def held(heights: np.ndarray, east: np.ndarray, north: np.ndarray, wet, quays, window) -> np.ndarray:
    """Relief heights with the ground beside a quay held at its crest.

    The scan smears the drop to the water over the last metres. A node within QUAY_REACH of a quay line takes the
    lidar height a little beyond that reach, straight inland of its foot on the line. The answer depends on the
    place alone, so neighbouring cells read the same heights on their shared border.
    """
    points = shapely.points(east, north)
    near = np.flatnonzero(shapely.distance(points, quays) < QUAY_REACH)
    if not len(near):
        return heights
    e, n = east[near], north[near]
    foot = shapely.get_coordinates(shapely.shortest_line(points[near], quays))[1::2]
    away = np.column_stack([e, n]) - foot
    span = np.linalg.norm(away, axis=1)
    away /= np.maximum(span, 1e-9)[:, None]
    away[shapely.contains_xy(wet, e, n)] *= -1
    inland = foot + away * (QUAY_REACH + 0.5)
    crest = window.sample('ground', inland[:, 0], inland[:, 1])
    on_line = span < 0.1   # no direction to speak of: the highest ground around is the land side
    if on_line.any():
        around = foot[on_line][:, None, :] + RING[None] * (QUAY_REACH + 0.5)
        crest[on_line] = window.sample('ground', around[..., 0], around[..., 1]).max(axis=1)
    out = np.array(heights, dtype=float)
    out[near] = crest
    return out
