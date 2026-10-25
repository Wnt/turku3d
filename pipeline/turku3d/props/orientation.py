"""Furniture headings in engine x/z coordinates; every model's front is +Z."""

import math

from shapely.geometry import Point
from shapely.ops import nearest_points


def _nearest_line(x, z, roads):
    if roads is None or not len(roads.geometries):
        return None
    return roads.geometries[roads.nearest(Point(x, z))]


def heading(kind, x, z, roads, walkway=None):
    """Aim signs against approaching right-hand traffic and furniture at its use side."""
    road = _nearest_line(x, z, roads)
    if road is None:
        return 0.0, "no_road_default"
    point = Point(x, z)
    distance = road.project(point)
    target = road.interpolate(distance)
    dx, dz = target.x - x, target.y - z
    if kind == "bench":
        if 0.05 < math.hypot(dx, dz) <= 12:
            return math.atan2(-dx, -dz), "back_to_kerb"
        if walkway is not None and not walkway.is_empty:
            boundary = walkway.boundary if walkway.covers(point) else walkway
            near = nearest_points(point, boundary)[1]
            wx, wz = near.x - x, near.y - z
            if walkway.covers(point):
                wx, wz = -wx, -wz
            if math.hypot(wx, wz) > 0.05:
                return math.atan2(wx, wz), "walkway_interior"
    if kind in ("sign", "bus_stop"):
        span = min(0.5, max(0.05, road.length / 100))
        a = road.interpolate(max(0, distance - span))
        b = road.interpolate(min(road.length, distance + span))
        tx, tz = b.x - a.x, b.y - a.y
        if math.hypot(tx, tz) > 1e-6 and math.hypot(dx, dz) > 0.05:
            # Engine z increases southward. (-tz, tx) is the right side of travel.
            if (x - target.x) * -tz + (z - target.y) * tx < 0:
                tx, tz = -tx, -tz
            return math.atan2(-tx, -tz), "right_hand_traffic"
    if kind in ("bin", "bollard", "post", "bike_rack", "bike_station"):
        span = min(0.5, max(0.05, road.length / 100))
        a = road.interpolate(max(0, distance - span))
        b = road.interpolate(min(road.length, distance + span))
        if a.distance(b) > 1e-6:
            return math.atan2(b.x - a.x, b.y - a.y), "kerb_parallel"
    if math.hypot(dx, dz) > 0.05:
        return math.atan2(dx, dz), "nearest_road"
    return 0.0, "on_centerline_default"
