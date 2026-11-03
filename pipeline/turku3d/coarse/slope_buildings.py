"""Buildings set into a slope: the exposed facade stands on the lower grade.

A LoD2 GroundSurface is one flat level. For a building dug into a hillside, with its
roof a terrace at the upper grade (a funicular or metro entrance, a hillside garage,
an earth-sheltered pavilion), the survey can put that level well above the open
ground in front of the exposed facade. The extruded walls then float over the street
or stand on a plinth as tall as the building, and the entrance facade is missing.

The open ground is read from the ground key points just outside every footprint edge.
A building is set into the slope when its roof meets the measured grade along part of
its outline (the buried side) while the ground in front of other edges lies clearly
below the LoD2 base (the exposed side): more than ``max(MIN_DROP, PLINTH x wall
height)``, so an ordinary plinth stays a footing (``footings.py``). Its base becomes
the exposed grade, so the exposed walls reach the ground and the buried walls stay
under the hill. The roof keeps its surveyed height as a terrace at the upper grade.
"""

from __future__ import annotations

import numpy as np
import shapely
from scipy.spatial import cKDTree

STEP = 0.5  # station spacing along each footprint edge (m)
NEAR, FAR = 1.2, 3.0  # open ground is sampled this far outside the wall (m)
RADIUS = 1.0  # key points within this radius of a probe describe its ground (m)
ROOF_GRADE = 0.6  # ground this close below the roof (or above it) buries the wall
MIN_DROP = 1.0  # an exposed side lies at least this far below the LoD2 base (m)
PLINTH = 0.5  # ... and at least this fraction of the LoD2 wall height
MIN_RUN = 3.0  # each side needs this much outline length (m)
DEEPEST = 6.0  # a lower grade is a quay, cutting or light well, not this building's
LOW = 10  # percentile of the exposed ground that becomes the base


def stations(footprint):
    """Points along every ring at STEP spacing and their outward unit normals."""
    from .footings import rings
    from .footings import stations as ring_stations

    points, normals = [], []
    for ring in rings(footprint):
        xz, normal = ring_stations(ring)
        points.append(xz)
        normals.append(normal)
    if not points:
        return np.empty((0, 2)), np.empty((0, 2))
    return np.concatenate(points), np.concatenate(normals)


def outside_ground(tree, heights, footprint, xz, normal):
    """Median key-point height at NEAR and FAR outside each station (NaN without points)."""
    out = np.full((len(xz), 2), np.nan)
    for column, distance in enumerate((NEAR, FAR)):
        probe = xz + normal * distance
        for i, hits in enumerate(tree.query_ball_point(probe, RADIUS)):
            if not hits:
                continue
            hits = np.asarray(hits, int)
            # Returns on the roof itself (eave band) are not open ground.
            free = ~shapely.contains_xy(footprint, *tree.data[hits].T)
            if free.any():
                out[i, column] = float(np.median(heights[hits[free]]))
    return out


def classify(building, tree, heights):
    """The set-into-slope evidence of one building, or None for an ordinary building."""
    footprint = building.footprint
    if footprint is None or footprint.is_empty:
        return None
    xz, normal = stations(footprint)
    if len(xz) < 4:
        return None
    shapely.prepare(footprint)
    ground = outside_ground(tree, heights, footprint, xz, normal)
    wall = building.top - building.base
    drop = max(MIN_DROP, PLINTH * wall)
    # Buried: the hill reaches the roof at both distances. Exposed: the nearer ground
    # (and the farther one where measured) lies a full storey fraction below the base.
    buried = np.all(ground >= building.top - ROOF_GRADE, axis=1)
    near = ground[:, 0]
    exposed = (near <= building.base - drop) & ~(ground[:, 1] > building.base - drop)
    if buried.sum() * STEP < MIN_RUN or exposed.sum() * STEP < MIN_RUN:
        return None
    grade = float(np.percentile(near[exposed], LOW))
    if building.base - grade > DEEPEST:
        return None
    return dict(
        exposed_grade=round(grade, 3),
        lod2_base=round(float(building.base), 3),
        buried_m=round(float(buried.sum() * STEP), 1),
        exposed_m=round(float(exposed.sum() * STEP), 1),
    )


def apply(buildings, points):
    """Lower the base of buildings set into a slope to their exposed grade, in place.

    Returns the number of changed buildings. Neighbouring tiles read the same key
    points within their margin, so a seam building gets the same base on both sides.
    """
    if not len(buildings) or points is None or len(points) < 16:
        return 0
    points = np.asarray(points, float)
    tree = cKDTree(points[:, [0, 2]])
    changed = 0
    for b in sorted(buildings, key=lambda b: b.key):
        if b.attributes.get("set_into_slope"):
            continue
        evidence = classify(b, tree, points[:, 1])
        if evidence is None:
            continue
        b.base = evidence["exposed_grade"]
        b.attributes["set_into_slope"] = evidence
        changed += 1
    return changed


RAIL = 1.1  # railing height above a roof terrace (m)
EDGE_DROP = 1.0  # a roof edge needs a railing where the open ground lies this far below
POSTS = 1.6  # railing post spacing (m)


def railings(buildings, points):
    """Steel railings along the exposed edges of roof terraces on set-into-slope buildings."""
    from .bridge_details import wall

    owned = [b for b in buildings if b.attributes.get("set_into_slope")]
    if not owned or points is None or len(points) < 16:
        return []
    points = np.asarray(points, float)
    tree = cKDTree(points[:, [0, 2]])
    meshes = []
    for b in sorted(owned, key=lambda b: b.key):
        xz, normal = stations(b.footprint)
        if len(xz) < 2:
            continue
        shapely.prepare(b.footprint)
        near = outside_ground(tree, points[:, 1], b.footprint, xz, normal)[:, 0]
        edge = np.isfinite(near) & (near < b.top - EDGE_DROP)
        same = np.all(np.abs(normal[1:] - normal[:-1]) < 1e-9, axis=1)
        inset = xz - normal * 0.12
        key = f"terrace_railing:{b.key}"
        run = []
        for i in range(len(xz)):
            if i < len(xz) - 1 and edge[i] and edge[i + 1] and same[i]:
                run.append(i)
                continue
            if run:
                line = inset[[*run, run[-1] + 1]]
                if np.linalg.norm(line[-1] - line[0]) >= 1.0:
                    n = len(line)
                    meshes.append(wall(key, line, np.full(n, b.top + RAIL - 0.06),
                                       np.full(n, b.top + RAIL), 0.06, "steel", "railing"))
                    meshes.append(wall(key, line, np.full(n, b.top + 0.45),
                                       np.full(n, b.top + 0.5), 0.03, "steel", "railing"))
                    length = np.r_[0, np.cumsum(np.linalg.norm(np.diff(line, axis=0), axis=1))]
                    for d in np.linspace(0, length[-1], max(2, int(length[-1] / POSTS) + 1)):
                        p = np.array([np.interp(d, length, line[:, 0]), np.interp(d, length, line[:, 1])])
                        t = line[-1] - line[0]
                        t = t / np.linalg.norm(t) * 0.03
                        meshes.append(wall(key, np.array([p - t, p + t]), np.full(2, b.top),
                                           np.full(2, b.top + RAIL), 0.06, "steel", "railing"))
                run = []
    for mesh in meshes:
        mesh.metadata.update(kind="terrace_railing")
    return meshes
