"""Where open water meets built land: the lines along which a quay wall stands (the `wall` kind) and behind which
the ground holds its level up to the edge (the old generator's `coarse/banks.py kade_crest`)."""

from __future__ import annotations

import numpy as np
import shapely

from ...street import osm, registers

BUILT_REACH = 8.0   # m: built land this near the water outline makes the edge a quay (a planted strip may lie between)
QUAY_REACH = 3.0    # m from the outline within which the lidar smears the drop; the crest is measured beyond it


def quay_lines(box):
    """(mapped open water, its outline where a polygon of the street register lies within BUILT_REACH), survey frame;
    (None, None) without water. Green land alone beside water is a natural bank."""
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
    return wet, wet.boundary.intersection(built.buffer(BUILT_REACH))


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
