"""Building footings: every exterior wall meets the visible ground.

A LoD2 GroundSurface can follow a higher street behind a building, so its base sits
above the open ground in front of another facade (a pavilion on a lower quay). The
extruded wall then floats over the terrain and leaves a slit into the building. A
plinth face closes each footprint edge from just below the final dry ground up to
the wall bottom, in visuals and collision alike. Mapped passages stay open.
"""

from __future__ import annotations

import numpy as np
import shapely

STEP = 0.5  # sampling along each footprint edge (m)
OUTSIDE = 0.08  # ground is sampled this far in front of the wall
GAP = 0.05  # a smaller gap is within the shared surface tolerance
BURY = 0.15  # the plinth continues below the ground it meets
DEEPEST = 2.5  # deeper drops are quays, ramps or water, not a missing footing
PASSAGE = 1.5  # clearance kept around mapped tunnel and passage ways


def rings(footprint):
    for polygon in shapely.get_parts(footprint):
        if polygon.geom_type != "Polygon":
            continue
        polygon = shapely.geometry.polygon.orient(polygon, 1.0)
        yield np.asarray(polygon.exterior.coords)[:, :2]
        for interior in polygon.interiors:
            yield np.asarray(interior.coords)[:, :2]


def stations(ring):
    """Dense samples along a closed ring and the outward (right-hand) edge normals."""
    points, normals = [], []
    for a, b in zip(ring[:-1], ring[1:], strict=True):
        length = float(np.hypot(*(b - a)))
        if length < 1e-6:
            continue
        d = (b - a) / length
        count = max(1, int(np.ceil(length / STEP)))
        t = np.linspace(0, length, count + 1)
        points.append(a + t[:, None] * d)
        normals.append(np.repeat([[d[1], -d[0]]], count + 1, axis=0))
    if not points:
        return np.empty((0, 2)), np.empty((0, 2))
    return np.concatenate(points), np.concatenate(normals)


def produce(buildings, ground, passages=()):
    """Plinth meshes for walls whose bottom stands above the final dry ground."""
    from turku3d.structures.geometry import Faces

    from .walkable_audit import heights

    triangles = ground.positions[ground.indices] if len(ground.indices) else np.empty((0, 3, 3))
    if not len(triangles):
        return []
    open_ways = [g for g in passages if g is not None and not g.is_empty]
    keep_open = shapely.union_all([g.buffer(PASSAGE) for g in open_ways]) if open_ways else None
    out = []
    for b in sorted(buildings, key=lambda b: b.key):
        faces = Faces()
        for ring in rings(b.footprint):
            xz, normal = stations(ring)
            if len(xz) < 2:
                continue
            probe = xz + normal * OUTSIDE
            ground_y = heights(triangles, probe, np.full(len(probe), b.base))
            gap = b.base - ground_y
            floating = np.isfinite(gap) & (gap > GAP) & (gap <= DEEPEST)
            if keep_open is not None:
                floating &= ~shapely.contains_xy(keep_open, probe[:, 0], probe[:, 1])
            # Consecutive samples on one edge share a normal; a corner restarts.
            same_edge = np.all(np.abs(normal[1:] - normal[:-1]) < 1e-9, axis=1)
            for i in np.flatnonzero(same_edge & floating[:-1] & floating[1:]):
                a, c = xz[i], xz[i + 1]
                if np.hypot(*(c - a)) < 1e-6:
                    continue
                low_a, low_c = ground_y[i] - BURY, ground_y[i + 1] - BURY
                faces.face(
                    [[a[0], b.base, a[1]], [c[0], b.base, c[1]],
                     [c[0], low_c, c[1]], [a[0], low_a, a[1]]]
                )
        if faces.indices:
            out.append(faces.mesh(f"footing:{b.key}", "wall",
                                  metadata=dict(kind="footing", surface="concrete",
                                                building=b.key)))
    return out
