"""Explicitly degraded footprint shells when surveyed/reconstructed shells fail.

The residual limits are unchanged. Nearby lidar is height evidence only: it never
counts as an in-footprint residual sample or as proof of reconstruction accuracy.
"""

import numpy as np
import shapely

from .height import supported_plane
from .shells import Surface


def extrusion(footprint, base, height):
    """Closed walls, roof and ground, including concavities and courtyard rings."""
    if not np.isfinite([base, height]).all() or height <= base:
        raise ValueError("extrusion needs finite roof height above its base")
    rings = [np.asarray(r.coords)[:-1] for r in [footprint.exterior, *footprint.interiors]]

    def lift(ring, y):
        return np.column_stack((ring[:, 0], np.full(len(ring), y), ring[:, 1]))

    surfaces = [
        Surface([lift(r, base) for r in rings], "GroundSurface"),
        Surface([lift(r, height) for r in rings], "RoofSurface"),
    ]
    for ring in rings:
        for a, b in zip(ring, np.roll(ring, -1, axis=0), strict=True):
            surfaces.append(
                Surface(
                    [
                        np.array(
                            [
                                [a[0], base, a[1]],
                                [b[0], base, b[1]],
                                [b[0], height, b[1]],
                                [a[0], height, a[1]],
                            ]
                        )
                    ],
                    "WallSurface",
                )
            )
    return surfaces


def lidar_extrusion(building, points, metadata, overlap=None):
    """Use local class-6 median height, or the nearest supported neighbourhood.

    Roofer's ground estimate is derived from the same lidar input. Missing roof
    evidence is a hard error; no assumed storey height is presented as measured.
    """
    base = metadata.get("rf_h_ground")
    if base is None or not np.isfinite(base):
        raise ValueError("no lidar ground height for fallback")
    inside = shapely.intersects_xy(building.footprint, points[:, 0], points[:, 2])
    elevated = points[:, 1] > base + 1
    support = points[inside & elevated]
    source, distance, details = "class6_inside_footprint", 0.0, {}
    if not len(support) and overlap is not None:
        plane = supported_plane(building.footprint, overlap, base)
        if plane is not None:
            support, details = plane
            source = "local_overlap_class12_plane"
    if not len(support):
        distances = shapely.distance(building.footprint, shapely.points(points[:, 0], points[:, 2]))
        candidates = np.flatnonzero(elevated & (distances <= 100))
        if not len(candidates):
            raise ValueError("no elevated class-6 height evidence within 100 m")
        distance = float(distances[candidates].min())
        support = points[candidates[distances[candidates] <= distance + 0.5]]
        source = "nearest_class6_outside_footprint"
    height = float(np.median(support[:, 1]))
    evidence = {
        "method": "footprint_extrusion",
        "height_source": source,
        "ground_source": "roofer_lidar_ground",
        "base_m": float(base),
        "roof_m": height,
        "height_points": len(support),
        "nearest_support_distance_m": distance,
        "height_range_m": [float(support[:, 1].min()), float(support[:, 1].max())],
        "accuracy_certified": False,
        **details,
    }
    roof, shape = _roof_from_class6(building.footprint, float(base), support)
    if roof is not None and source == "class6_inside_footprint":
        evidence.update(shape)
        return roof, evidence
    evidence["roof_shape"] = "flat"
    return extrusion(building.footprint, float(base), height), evidence


def _roof_from_class6(footprint, base, points):
    """Fit a gable only when both roof slopes have measured class-6 support.

    Other footprints retain the measured flat-height extrusion. The four-corner
    restriction keeps the resulting shell closed without inventing roof topology.
    """
    ring = np.asarray(footprint.exterior.coords[:-1], dtype=float)
    if len(ring) != 4 or footprint.interiors or len(points) < 40:
        return None, {}
    edges = np.roll(ring, -1, axis=0) - ring
    lengths = np.linalg.norm(edges, axis=1)
    i = int(np.argmax(lengths))
    if abs(np.dot(edges[i], edges[(i + 1) % 4])) > 0.15 * lengths[i] * lengths[(i + 1) % 4]:
        return None, {}
    if abs(lengths[i] - lengths[(i + 2) % 4]) > 0.15 * lengths[i]:
        return None, {}
    v = np.roll(ring, -i, axis=0)
    along = (v[1] - v[0]) / np.linalg.norm(v[1] - v[0])
    longitudinal = (points[:, [0, 2]] - v[0]) @ along / lengths[i]
    halves = [
        points[(longitudinal > a) & (longitudinal < b), 1] for a, b in ((0.05, 0.45), (0.55, 0.95))
    ]
    if min(map(len, halves)) >= 15:
        levels = [float(np.median(h)) for h in halves]
        spreads = [float(np.quantile(h, 0.75) - np.quantile(h, 0.25)) for h in halves]
        if abs(levels[0] - levels[1]) >= 1.5 and max(spreads) < 0.5 and min(levels) > base + 2:
            surfaces = _level_roof(v, base, levels)
            return surfaces, {"roof_shape": "multi_level", "level_heights_m": levels}
    width = (lengths[(i + 1) % 4] + lengths[(i + 3) % 4]) / 2
    transverse = (v[2] - v[1]) / np.linalg.norm(v[2] - v[1])
    t = (points[:, [0, 2]] - v[0]) @ transverse / width
    center = points[(t > 0.35) & (t < 0.65), 1]
    edges_y = points[((t > 0.05) & (t < 0.25)) | ((t > 0.75) & (t < 0.95)), 1]
    if min(len(center), len(edges_y)) < 10:
        return None, {}
    ridge = float(np.quantile(center, 0.75))
    eave = float(np.quantile(edges_y, 0.75))
    if not (0.8 <= ridge - eave <= 6 and eave > base + 2):
        return None, {}

    def xyz(q, y):
        return [q[0], y, q[1]]

    center_long = points[(t > 0.35) & (t < 0.65)]
    end_roof = center_long[
        (longitudinal[(t > 0.35) & (t < 0.65)] < 0.15)
        | (longitudinal[(t > 0.35) & (t < 0.65)] > 0.85),
        1,
    ]
    hip = len(end_roof) >= 10 and ridge - float(np.median(end_roof)) > 0.6
    m12, m30 = (v[1] + v[2]) / 2, (v[3] + v[0]) / 2
    surfaces = [Surface([[xyz(q, base) for q in v]], "GroundSurface")]
    for j in range(4):
        a, b = v[j], v[(j + 1) % 4]
        top = [xyz(a, eave)]
        if j == 1 and not hip:
            top.append(xyz(m12, ridge))
        elif j == 3 and not hip:
            top.append(xyz(m30, ridge))
        top.append(xyz(b, eave))
        surfaces.append(Surface([[xyz(a, base), xyz(b, base), *reversed(top)]], "WallSurface"))
    if hip:
        start = m30 + (m12 - m30) * 0.2
        end = m30 + (m12 - m30) * 0.8
        surfaces.extend(
            [
                Surface(
                    [[xyz(v[0], eave), xyz(v[1], eave), xyz(end, ridge), xyz(start, ridge)]],
                    "RoofSurface",
                ),
                Surface(
                    [[xyz(v[2], eave), xyz(v[3], eave), xyz(start, ridge), xyz(end, ridge)]],
                    "RoofSurface",
                ),
                Surface([[xyz(v[1], eave), xyz(v[2], eave), xyz(end, ridge)]], "RoofSurface"),
                Surface([[xyz(v[3], eave), xyz(v[0], eave), xyz(start, ridge)]], "RoofSurface"),
            ]
        )
    else:
        surfaces.extend(
            [
                Surface(
                    [[xyz(v[0], eave), xyz(v[1], eave), xyz(m12, ridge), xyz(m30, ridge)]],
                    "RoofSurface",
                ),
                Surface(
                    [[xyz(v[2], eave), xyz(v[3], eave), xyz(m30, ridge), xyz(m12, ridge)]],
                    "RoofSurface",
                ),
            ]
        )
    return surfaces, {"roof_shape": "hip" if hip else "gable", "eave_m": eave, "ridge_m": ridge}


def _level_roof(v, base, levels):
    def xyz(q, y):
        return [q[0], y, q[1]]

    a = (v[0] + v[1]) / 2
    b = (v[3] + v[2]) / 2
    h0, h1 = levels
    surfaces = [Surface([[xyz(q, base) for q in v]], "GroundSurface")]
    for corners, height in [([v[0], a, b, v[3]], h0), ([a, v[1], v[2], b], h1)]:
        surfaces.append(Surface([[xyz(q, height) for q in corners]], "RoofSurface"))
    for p, q, h in [
        (v[0], a, h0),
        (a, v[1], h1),
        (v[1], v[2], h1),
        (v[2], b, h1),
        (b, v[3], h0),
        (v[3], v[0], h0),
    ]:
        surfaces.append(
            Surface([[xyz(p, base), xyz(q, base), xyz(q, h), xyz(p, h)]], "WallSurface")
        )
    surfaces.append(Surface([[xyz(a, h0), xyz(b, h0), xyz(b, h1), xyz(a, h1)]], "WallSurface"))
    return surfaces


def placeholder_extrusion(building, metadata, ground_points=None):
    """Publish a conspicuous stand-in when measured shell evidence is unavailable."""
    base = metadata.get("rf_h_ground")
    source = "roofer_lidar_ground"
    top = base
    if base is None or not np.isfinite(base):
        ground = np.asarray(ground_points if ground_points is not None else np.empty((0, 3)))
        under = ground[
            shapely.contains_xy(building.footprint.buffer(1.0), ground[:, 0], ground[:, 2])
        ]
        if len(under):
            # A stand-in may straddle a terrace edge: reach the lowest ground so
            # it never floats, and rise above the highest.
            base, top = float(under[:, 1].min()), float(under[:, 1].max())
            source = "footprint_ground_points"
        elif len(ground):
            center = building.footprint.representative_point()
            distance = np.linalg.norm(ground[:, [0, 2]] - [center.x, center.y], axis=1)
            base = top = float(np.median(ground[np.argsort(distance)[: min(20, len(ground))], 1]))
            source = "nearby_ground_points"
        else:
            base = top = 0.0
            source = "default_n2000_zero"
    levels = building.properties.get("osm", {}).get("building:levels") or building.properties.get(
        "levels"
    )
    try:
        height = float(levels) * 3.2
        if not 2.5 <= height <= 200:
            raise ValueError
        height_source = "building_levels"
    except (ValueError, TypeError):
        height, height_source = 10.0, "default_10m"
    return extrusion(building.footprint, float(base), float(top) + height), {
        "method": "work_in_progress_placeholder",
        "ground_source": source,
        "base_m": float(base),
        "roof_m": float(top) + height,
        "height_source": height_source,
        "accuracy_certified": False,
    }
