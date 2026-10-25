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
    return extrusion(building.footprint, float(base), height), evidence
