"""Connected, coplanar LOD2 wall extents for independent reference inventories."""

from __future__ import annotations

import numpy as np
import shapely
from shapely.geometry import LineString, Polygon, box


def wall_rectangles(polygons):
    """Return supported rectangles without joining offset walls or courtyard gaps.

    Ground footprints and terrace roof vertices do not define the upper facade.
    Each group is a vertical plane supported by actual WallSurface polygons. A
    stepped/gabled top or a nonrectangular component is omitted from this truth
    inventory; the production mesh is not changed.
    """
    groups = []
    for polygon in polygons:
        rings = [np.asarray(r.coords) for r in [polygon.exterior, *polygon.interiors]]
        points = rings[0][:-1]
        if points.shape[1] != 3 or np.ptp(points[:, 2]) < 2:
            continue
        origin = points[:, :2].mean(0)
        _, _, vectors = np.linalg.svd(points[:, :2] - origin, full_matrices=False)
        tangent = vectors[0]
        if tangent[np.argmax(np.abs(tangent))] < 0:
            tangent = -tangent
        normal = np.array([tangent[1], -tangent[0]])
        if np.max(np.abs((points[:, :2] - origin) @ normal)) > 0.15:
            continue
        group = next(
            (
                g
                for g in groups
                if g[1] @ tangent > np.cos(np.radians(2))
                and np.max(np.abs((points[:, :2] - g[0]) @ g[2])) <= 0.6
            ),
            None,
        )
        if group is None:
            group = (origin, tangent, normal, [])
            groups.append(group)
        group[3].append(rings)
    result = []
    for origin, tangent, normal, rings_list in groups:
        xy = np.concatenate([rings[0][:-1, :2] for rings in rings_list])
        origin = xy.mean(0)
        _, _, vectors = np.linalg.svd(xy - origin, full_matrices=False)
        fitted = vectors[0]
        tangent = fitted if fitted @ tangent > 0 else -fitted
        normal = np.array([tangent[1], -tangent[0]])
        surfaces = []
        for rings in rings_list:
            projected = [np.column_stack([(r[:, :2] - origin) @ tangent, r[:, 2]]) for r in rings]
            surfaces.append(shapely.make_valid(Polygon(projected[0], projected[1:])))
        # Close survey seams and shallow ornamental offsets, while preserving
        # courtyard gaps and genuinely separate planes.
        merged = shapely.union_all(surfaces).buffer(0.35, join_style=2).buffer(-0.35, join_style=2)
        for component in getattr(merged, "geoms", [merged]):
            if component.geom_type != "Polygon":
                continue
            left, bottom, right, top = component.bounds
            if right - left < 8 or top - bottom < 3:
                continue
            tops, bottoms = [], []
            for x in np.linspace(left, right, 43)[1:-1]:
                section = component.intersection(LineString([(x, bottom - 1), (x, top + 1)]))
                if not section.is_empty:
                    bottoms.append(section.bounds[1])
                    tops.append(section.bounds[3])
            if len(tops) != 41 or np.ptp(np.quantile(tops, [0.05, 0.95])) > 2:
                continue
            # Small roof survey noise must not turn a complete wall into a strip.
            top, bottom = float(np.quantile(tops, 0.98)), float(np.median(bottoms))
            rectangle = box(left, bottom, right, top)
            if top - bottom < 3 or component.intersection(rectangle).area / rectangle.area < 0.95:
                continue
            result.append((origin + left * tangent, origin + right * tangent, bottom, top, normal))
    return result
