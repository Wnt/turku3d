"""Exact point-to-geometry distances, computed only where they can fall within a reach."""

import numpy as np
import shapely

# Larger than any coordinate rounding; only selects candidates, never decides.
MARGIN = 0.01


def near_distance(xy, geometry, reach):
    """``shapely.distance`` of each (x, z) point to ``geometry`` where it is at most ``reach``.

    Other points get ``inf``. The candidates' values are the identical GEOS distances:
    a point beyond the geometry's bounds by more than ``reach`` cannot be within it,
    and prepared ``dwithin`` only narrows polygon candidates with the same margin.
    """
    xy = np.asarray(xy, np.float64)
    result = np.full(len(xy), np.inf)
    if geometry.is_empty or not len(xy):
        return result
    x0, z0, x1, z1 = geometry.bounds
    m = reach + MARGIN
    ids = np.flatnonzero((xy[:, 0] >= x0 - m) & (xy[:, 0] <= x1 + m)
                         & (xy[:, 1] >= z0 - m) & (xy[:, 1] <= z1 + m))
    if not len(ids):
        return result
    points = shapely.points(xy[ids])
    if shapely.get_dimensions(geometry) == 2:
        shapely.prepare(geometry)
        keep = shapely.dwithin(points, geometry, m)
        ids, points = ids[keep], points[keep]
    result[ids] = shapely.distance(points, geometry)
    return result
