"""Closed draft building shells on complete surveyed footprint parts."""

import mapbox_earcut
import numpy as np
import shapely

from turku3d import config as C

from .inputs import Building


def extrude(b: Building):
    """Compose closed roof-part solids while retaining the building ownership footprint."""
    from .roofs import above, plane_height, polygons

    centroid = b.footprint.centroid
    collision_origin = np.asarray(C.CollisionTile.at(centroid.x, centroid.y).origin)[[0, 2]]
    parts = []
    for polygon, plane in b.roof_parts:
        for piece in polygons(
            above(polygon.intersection(b.footprint), plane, b.base + 0.01).simplify(
                0.005, preserve_topology=True
            )
        ):
            part = Building(
                b.key, shapely.geometry.polygon.orient(piece, 1), b.base, b.top, b.material_key
            )
            parts.append(_extrude(part, lambda xz, p=plane: plane_height(p, xz), collision_origin))
    if not parts and any(
        plane_height(plane, np.asarray(polygon.exterior.coords)[:, :2]).max() > b.base + 0.01
        for polygon, plane in b.roof_parts
    ):
        empty_pos = np.empty((0, 3), float)
        empty_idx = np.empty((0, 3), np.uint32)
        return (
            empty_pos,
            empty_idx,
            empty_pos.copy(),
            empty_idx.copy(),
            empty_pos.copy(),
            np.empty((0, 2), float),
            empty_idx.copy(),
        )
    if not parts:
        return _extrude(b, lambda xz: np.full(len(xz), b.top), collision_origin)
    result = []
    for index in range(7):
        if index in (1, 3, 6):
            vertex = {1: 0, 3: 2, 6: 4}[index]
            offsets = np.cumsum([0] + [len(p[vertex]) for p in parts[:-1]])
            result.append(
                np.concatenate(
                    [p[index] + offset for p, offset in zip(parts, offsets, strict=True)]
                )
            )
        else:
            result.append(np.concatenate([p[index] for p in parts]))
    return tuple(result)


def _extrude(b: Building, height, collision_origin):
    """Closed shell: walls (with metre UVs), roof and floor. Returns parts in world-local metres."""
    # Roof-plane intersections can retain consecutive micrometre edges even after
    # topology-preserving simplification. Weld them before making any faces, so
    # walls, roof and floor share a ring that survives float32 tile export.
    rings = []
    for ring in [b.footprint.exterior, *b.footprint.interiors]:
        points = np.asarray(ring.coords)[:-1, :2]
        kept = [points[0]]
        for point in points[1:]:
            if np.linalg.norm(point - kept[-1]) >= 1e-4:
                kept.append(point)
        if len(kept) > 1 and np.linalg.norm(kept[-1] - kept[0]) < 1e-4:
            kept.pop()
        if len(kept) >= 3:
            rings.append(np.asarray(kept))
    flat = np.concatenate(rings)
    ends = np.cumsum([len(r) for r in rings]).astype(np.uint32)
    tri = mapbox_earcut.triangulate_float64(flat, ends).reshape(-1, 3).astype(np.uint32)
    roof = np.column_stack([flat[:, 0], height(flat), flat[:, 1]])
    a, bb, cc = roof[tri[:, 0]], roof[tri[:, 1]], roof[tri[:, 2]]
    up = np.cross(bb - a, cc - a)[:, 1] > 0
    roof_idx = np.where(up[:, None], tri, tri[:, [0, 2, 1]])
    # Polygon differences can leave collinear earcut ears. Test in export precision
    # before using the triangulation for both roof and floor. These have no usable area.
    for coords in (flat, flat.astype(np.float32), (flat - collision_origin).astype(np.float32)):
        projected = coords[roof_idx]
        ab, ac = projected[:, 1] - projected[:, 0], projected[:, 2] - projected[:, 0]
        area = np.abs(ab[:, 0] * ac[:, 1] - ab[:, 1] * ac[:, 0]) / 2
        roof_idx = roof_idx[area >= 2e-6]
    floor = roof.copy()
    floor[:, 1] = b.base
    wall_pos, wall_uv, wall_idx = [], [], []
    for ring in rings:
        nxt = np.roll(ring, -1, axis=0)
        for p, q in zip(ring, nxt, strict=True):
            length = float(np.hypot(*(q - p)))
            if length < 1e-6:
                continue
            k = len(wall_pos) * 4
            hp, hq = height(np.array([p, q]))
            wall_pos.append(
                [
                    [p[0], b.base, p[1]],
                    [q[0], b.base, q[1]],
                    [q[0], hq, q[1]],
                    [p[0], hp, p[1]],
                ]
            )
            wall_uv.append([[0, b.base], [length, b.base], [length, hq], [0, hp]])
            # The footprint is oriented counter-clockwise in (x, z), so these face outwards.
            wall_idx += [[k, k + 3, k + 2], [k, k + 2, k + 1]]
    wall_pos = np.asarray(wall_pos, float).reshape(-1, 3)
    wall_uv = np.asarray(wall_uv, float).reshape(-1, 2)
    wall_idx = np.asarray(wall_idx, np.uint32).reshape(-1, 3)
    return roof, roof_idx, floor, roof_idx[:, [0, 2, 1]], wall_pos, wall_uv, wall_idx
