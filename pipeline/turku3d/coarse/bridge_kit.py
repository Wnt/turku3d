"""Parametric bridge components fitted in the crossing's measured local frame.

Dimensions are estimates unless supplied by a source tag. Geometry is shared by
the visual and collision producers; no independent invisible obstruction meshes.
"""

import numpy as np
import shapely

from turku3d.structures.geometry import Faces


def tapered_post(key, centre, direction, levels, surface="granite"):
    """Closed square shaft with (height, along width, across width) sections."""
    from .bridge_details import appearance

    centre = np.asarray(centre, float)
    direction = np.asarray(direction, float)
    direction /= np.linalg.norm(direction)
    normal = np.array([-direction[1], direction[0]])
    rings = []
    for height, length, width in levels:
        xz = centre[[0, 2]] + np.array([
            -direction * length / 2 - normal * width / 2,
            direction * length / 2 - normal * width / 2,
            direction * length / 2 + normal * width / 2,
            -direction * length / 2 + normal * width / 2,
        ])
        rings.append(np.c_[xz[:, 0], np.full(4, centre[1] + height), xz[:, 1]])
    faces = Faces()
    # XZ counterclockwise is downward in engine XYZ.
    faces.face(rings[0])
    faces.face(rings[-1][::-1])
    for bottom, top in zip(rings[:-1], rings[1:], strict=True):
        for i in range(4):
            j = (i + 1) % 4
            faces.face([bottom[j], bottom[i], top[i], top[j]])
    return appearance(faces.mesh(key, "wall"), surface)


def entrance_points(way, inset, setback=0):
    """Inset each longitudinal edge from its own abutment corner.

    A cross-section near a skew end can intersect the end face inside a lane.
    Only long side chains are eligible; ambiguous fragmented outlines abstain.
    """
    from .bridge_details import deck_outline

    axis, outline = way["line"], deck_outline(way)
    sides = []
    for part in shapely.get_parts(outline):
        if part.geom_type != "Polygon":
            continue
        coords = np.asarray(part.exterior.coords)
        for a, b in zip(coords[:-1], coords[1:], strict=True):
            middle = shapely.Point((a + b) / 2)
            station = axis.project(middle)
            tangent = np.asarray(axis.interpolate(min(axis.length, station + .5)).coords[0])
            tangent -= np.asarray(axis.interpolate(max(0, station - .5)).coords[0])
            edge = b - a
            if abs(edge @ tangent) >= .8 * np.linalg.norm(edge) * np.linalg.norm(tangent):
                sides.append(shapely.LineString([a, b]))
    chains = [p for p in shapely.get_parts(shapely.line_merge(shapely.union_all(sides)))
              if p.geom_type == "LineString" and p.length > axis.length * .6]
    if len(chains) != 2:
        return []
    points = []
    for chain in chains:
        for start in (True, False):
            # Slide along the edge until the entire plinth clears a skew end.
            for extra in np.arange(0, max(.01, 4 * setback) + .01, .1):
                distance = inset + extra
                station = distance if start else chain.length - distance
                if not 0 < station < chain.length or distance > chain.length / 4:
                    continue
                p = np.asarray(chain.interpolate(station).coords[0])
                d = np.asarray(chain.interpolate(min(chain.length, station + .2)).coords[0])
                d -= np.asarray(chain.interpolate(max(0, station - .2)).coords[0])
                d /= np.linalg.norm(d)
                n = np.array([-d[1], d[0]])
                towards = np.asarray(axis.interpolate(axis.project(shapely.Point(p))).coords[0]) - p
                if n @ towards < 0:
                    n *= -1
                p += n * setback
                if setback:
                    footprint = shapely.Polygon([p + d * u * setback + n * v * setback
                                                 for u, v in ((-1, -1), (1, -1), (1, 1), (-1, 1))])
                    if not outline.buffer(.001).covers(footprint):
                        continue
                points.append((p, d))
                break
    return points


def entrances(way, height_at, blocked, spec):
    """Four tapered masonry end pylons with layered plinths and bronze collars."""
    height = spec.get("entrance_height", 0)
    if height <= 0 or way.get("landing") or way["line"].length < 12:
        return []
    key = f"draft-bridge:{way['id']}:entrance"
    width = spec.get("entrance_width", 1.05)
    result = []
    half = width * 1.22 / 2
    from .bridge_details import deck_outline

    for p, direction in entrance_points(way, spec.get("entrance_inset", 1.2), half + .02):
        # A same-level source obstacle takes priority over estimated ornament.
        normal = np.array([-direction[1], direction[0]])
        footprint = shapely.Polygon([p + direction * u * half + normal * v * half
                                     for u, v in ((-1, -1), (1, -1), (1, 1), (-1, 1))])
        if not deck_outline(way).buffer(.001).covers(footprint) or any(
            footprint.intersects(g) for g in blocked
        ):
            continue
        y = float(height_at(np.asarray([p]))[0])
        levels = [(0, 1.22, 1.22), (.19, 1.22, 1.22),
                  (.22, 1.06, 1.06), (.95, 1.06, 1.06),
                  (1.02, .92, .92), (1.12, .92, .92),
                  (1.14, .77, .77), (height - .22, .49, .49),
                  (height - .09, .49, .49), (height, .31, .31)]
        levels = [(h, a * width, b * width) for h, a, b in levels]
        result.append(tapered_post(key, [p[0], y, p[1]], direction, levels))
        normal = np.array([-direction[1], direction[0]])
        # The same collar module works at any yaw; modest relief avoids decals.
        collar = height - .85
        for lift, size in ((collar, .64), (collar + .08, .72)):
            result.append(tapered_post(key, [p[0], y, p[1]], direction,
                                      [(lift, size * width, size * width),
                                       (lift + .045, size * width, size * width)], "bronze"))
        for sign in (-1, 1):
            for offset in (-.16, 0, .16):
                from .bridge_details import tube

                endpoints = []
                for lift in (collar - 1.05, collar + .14):
                    shaft_width = np.interp(lift, [1.14, height - .22], [.77, .49]) * width
                    c = p + normal * sign * (shaft_width / 2 + .008) + direction * offset * width
                    endpoints.append([c[0], y + lift, c[1]])
                result.append(tube(key, *endpoints, .018, "bronze", "wall", sides=4))
    return result
