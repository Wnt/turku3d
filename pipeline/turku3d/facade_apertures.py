"""Reviewed through-openings cut every shell face inside their measured volume.

Roof decomposition can leave inner vertical walls behind an exposed frontage.
Opening only the two outer planes leaves those walls visible and collidable.
"""

import numpy as np
import shapely

from turku3d import config as C


def volumes(observation):
    if not observation or observation.get("needs_review", True):
        return []
    result = []
    for wall in (observation or {}).get("walls", []):
        if wall.get("through_parent") or wall.get("observation", {}).get("needs_review", True):
            continue
        g = wall.get("geometry", {})
        if not all(k in g for k in ("p0", "p1", "normal", "base_h")):
            continue
        normal = np.asarray(g["normal"], float)
        if normal.shape != (2,) or not np.isfinite(normal).all() or np.linalg.norm(normal) < 0.9:
            continue
        normal = normal * [1, -1] / np.linalg.norm(normal)
        a, b = [np.array([e - C.ORIGIN_E, C.ORIGIN_N - n]) for e, n in (g["p0"], g["p1"])]
        width = np.linalg.norm(b - a)
        if width < 0.1:
            continue
        direction = (b - a) / width
        for component in wall.get("geometry_components", []):
            depth = component.get("depth_m", 0)
            if not (
                component.get("kind") == "arcade"
                and component.get("through")
                and component.get("support", 0) >= 0.6
                and 0 < depth <= 12
            ):
                continue
            x, y, w, h = component["bounds"]
            left, right = (
                a + direction * (x * width + 0.04),
                a + direction * ((x + w) * width - 0.04),
            )
            # Two centimetres admit the surveyed outer planes despite roundoff;
            # the aperture's ends retain the same four-centimetre return inset.
            polygon = shapely.Polygon(
                [
                    left + 0.02 * normal,
                    right + 0.02 * normal,
                    right - (depth + 0.02) * normal,
                    left - (depth + 0.02) * normal,
                ]
            )
            bottom = g["base_h"] + y * wall["height_m"] + 0.04
            top = g["base_h"] + (y + h) * wall["height_m"] - 0.04
            result.append((polygon, bottom, top))
    return result


def cuts(vertices, apertures):
    if not apertures:
        return []
    tangent = vertices[1] - vertices[0]
    tangent[1] = 0
    width = np.linalg.norm(tangent)
    if width < 0.01:
        return []
    tangent /= width
    normal = np.array([tangent[2], 0, -tangent[0]])
    line = shapely.LineString(vertices[:2, [0, 2]])
    boundary = shapely.Polygon(np.c_[[0, width, width, 0], vertices[:, 1]])
    result = []
    for polygon, bottom, top in apertures:
        if top <= vertices[:, 1].min() or bottom >= vertices[:, 1].max():
            continue
        intersection = line.intersection(polygon)
        for segment in shapely.get_parts(intersection):
            if segment.geom_type != "LineString" or segment.length < 0.01:
                continue
            distances = (np.asarray(segment.coords) - vertices[0, [0, 2]]) @ tangent[[0, 2]]
            left, right = distances.min(), distances.max()
            rect = shapely.box(left, bottom, right, top)
            if rect.intersection(boundary).area < 0.001:
                continue
            result.append(
                dict(
                    kind="through_cut",
                    through=True,
                    rect=rect,
                    metric=[left, bottom, right, top],
                    depth_m=0,
                    tangent=tangent,
                    normal=normal,
                )
            )
    return result
