"""Earcut triangulation, footprint snapping and strict closed-shell validation."""
from __future__ import annotations
from dataclasses import dataclass

import mapbox_earcut
import numpy as np
import shapely
from shapely.geometry import Point, Polygon
from shapely.ops import nearest_points, unary_union
import trimesh

from turku3d.col.materials import mat_id


@dataclass
class Surface:
    rings: list[np.ndarray]  # engine x/y/z, no repeated closing coordinate required
    kind: str


@dataclass
class Shell:
    positions: np.ndarray
    indices: np.ndarray
    material: np.ndarray
    surface_ids: np.ndarray
    surfaces: list[Surface]
    footprint: Polygon
    hausdorff: float


def clean_ring(ring):
    r = np.asarray(ring, dtype=np.float64)
    if len(r) > 1 and np.allclose(r[0], r[-1], atol=1e-9, rtol=0):
        r = r[:-1]
    keep = np.ones(len(r), dtype=bool)
    if len(r) > 1:
        keep[1:] = np.linalg.norm(np.diff(r, axis=0), axis=1) > 1e-9
    return r[keep]


def triangulate(surface):
    rings = [clean_ring(r) for r in surface.rings]
    if any(len(r) < 3 for r in rings):
        raise ValueError('surface has fewer than three distinct vertices')
    outer = rings[0]
    normal = np.cross(outer, np.roll(outer, -1, axis=0)).sum(axis=0)
    length = np.linalg.norm(normal)
    if length < 1e-9:
        raise ValueError('surface has zero area')
    normal /= length
    p = np.concatenate(rings)
    if surface.kind == 'WallSurface' and np.max(np.abs((p - p[0]) @ normal)) > .05:
        raise ValueError('surface is non-planar by more than 5 cm')
    axis = int(np.argmax(np.abs(normal)))
    xy = np.ascontiguousarray(np.delete(p - p[0], axis, axis=1))
    ends = np.cumsum([len(r) for r in rings]).astype(np.uint32)
    idx = mapbox_earcut.triangulate_float64(xy, ends).reshape(-1, 3)
    if not len(idx):
        raise ValueError('earcut returned no triangles')
    # Earcut chooses its own orientation; restore the input polygon orientation.
    dots = np.cross(p[idx[:, 1]]-p[idx[:, 0]], p[idx[:, 2]]-p[idx[:, 0]]) @ normal
    idx[dots < 0] = idx[dots < 0][:, ::-1]
    polygon = Polygon(xy[:ends[0]], [xy[ends[k-1]:ends[k]] for k in range(1, len(ends))])
    if not polygon.is_valid:
        # Millimetre CityJSON quantisation can make rings touch or cross. Split
        # the exact polygon region, preserving source vertices and edge heights.
        fixed = shapely.make_valid(polygon)
        parts = list(fixed.geoms) if hasattr(fixed, 'geoms') else [fixed]
        positions, faces, offset = [], [], 0
        for part in parts:
            if part.geom_type != 'Polygon' or part.area < 1e-10:
                continue
            lifted = []
            for ring in [part.exterior, *part.interiors]:
                vertices = []
                for q in np.asarray(ring.coords)[:-1]:
                    closest = np.argmin(np.linalg.norm(xy-q, axis=1))
                    if np.linalg.norm(xy[closest]-q) < 1e-8:
                        vertices.append(p[closest])
                        continue
                    candidates = []
                    begin = 0
                    for end in ends:
                        for i in range(begin, end):
                            j = begin if i+1 == end else i+1
                            delta = xy[j]-xy[i]
                            length2 = delta@delta
                            if length2 < 1e-16:
                                continue
                            t = np.clip((q-xy[i])@delta/length2, 0, 1)
                            candidates.append((np.linalg.norm(q-(xy[i]+t*delta)), p[i]+t*(p[j]-p[i])))
                        begin = end
                    distance, vertex = min(candidates, key=lambda item: item[0])
                    if distance > 1e-7:
                        raise ValueError('polygon repair introduced a vertex outside source edges')
                    vertices.append(vertex)
                lifted.append(np.array(vertices))
            pp, tt = triangulate(Surface(lifted, surface.kind))
            positions.extend(pp)
            faces.extend(tt+offset)
            offset += len(pp)
        if not faces:
            raise ValueError('surface has no polygon area after ring repair')
        return np.array(positions), np.array(faces, np.uint32)
    triangles = [Polygon(xy[t]) for t in idx]
    if polygon.symmetric_difference(unary_union(triangles)).area > max(1e-7, polygon.area * 1e-8):
        raise ValueError('earcut triangulation does not cover polygon')
    return p, idx


def conform_edges(p, triangles, ids, tolerance=1e-7):
    """Insert existing vertices on triangle edges; repairs source polygon T junctions.

    This splits an already triangulated triangle along an edge; concave polygons
    and holes are always triangulated by earcut, never a polygon fan.
    """
    result, source = [], []
    stack = list(zip(map(tuple, triangles), ids))
    visited = set()
    while stack:
        tri, sid = stack.pop()
        if np.linalg.norm(np.cross(p[tri[1]]-p[tri[0]], p[tri[2]]-p[tri[0]])) < 2e-8:
            continue
        key = (tuple(tri), int(sid))
        if key in visited:
            raise ValueError('ambiguous near-collinear vertices prevent conforming triangulation')
        visited.add(key)
        for a, b, c in ((tri[0], tri[1], tri[2]), (tri[1], tri[2], tri[0]), (tri[2], tri[0], tri[1])):
            delta = p[b] - p[a]
            length2 = delta @ delta
            if length2 < tolerance**2:
                continue
            t = (p-p[a]) @ delta / length2
            candidates = np.flatnonzero((t > tolerance) & (t < 1-tolerance))
            distance = np.linalg.norm(p[candidates] - (p[a] + t[candidates, None]*delta), axis=1)
            candidates = candidates[(distance < tolerance) & ~np.isin(candidates, tri)]
            if len(candidates):
                v = int(candidates[np.argmin(t[candidates])])
                stack.extend([((a, v, c), sid), ((v, b, c), sid)])
                break
        else:
            result.append(tri)
            source.append(sid)
    return np.asarray(result, np.uint32), np.asarray(source, np.int32)


def make_shell(surfaces, footprint, snap_distance=1.0):
    if not surfaces:
        raise ValueError('candidate is missing')
    surfaces = [Surface([clean_ring(r).copy() for r in s.rings], s.kind) for s in surfaces]
    ground = [Polygon(s.rings[0][:, [0, 2]], [r[:, [0, 2]] for r in s.rings[1:]])
              for s in surfaces if s.kind == 'GroundSurface']
    if not ground:
        raise ValueError('candidate has no ground surface')
    original = unary_union(ground)
    if not original.is_valid:
        raise ValueError('candidate ground polygon is invalid')
    # All height levels at a source boundary column receive the SAME displacement.
    # Interior ridges and roof details retain their measured horizontal coordinates.
    mapping = {}
    for s in surfaces:
        for ring in s.rings:
            for v in ring:
                key = tuple(v[[0, 2]])
                if key not in mapping:
                    point = Point(key)
                    if original.boundary.distance(point) < .002 and footprint.boundary.distance(point) <= snap_distance:
                        q = nearest_points(footprint.boundary, point)[0]
                        mapping[key] = np.array(q.coords[0])
                    else:
                        mapping[key] = v[[0, 2]].copy()
                v[[0, 2]] = mapping[key]
    positions, triangles, ids = [], [], []
    count = 0
    for i, s in enumerate(surfaces):
        if len(s.rings[0]) < 3:
            continue
        p, t = triangulate(s)
        positions.extend(p)
        triangles.extend(t + count)
        ids.extend([i] * len(t))
        count += len(p)
    # Preserve projected edge collinearity: rounding each coordinate separately
    # introduces slivers and false T junctions. Shared source columns are exact.
    p, inverse = np.unique(np.asarray(positions, np.float64), axis=0, return_inverse=True)
    t = inverse[np.asarray(triangles)]
    t, ids = conform_edges(p, t, ids)
    area = np.linalg.norm(np.cross(p[t[:, 1]]-p[t[:, 0]], p[t[:, 2]]-p[t[:, 0]]), axis=1) / 2
    # Snapping may collapse a zero-width source sliver. Remove it, then require
    # the remaining shell to pass the same strict manifold tests.
    keep = area >= 1e-8
    t, ids = t[keep], ids[keep]
    groups = {}
    for i, face in enumerate(t):
        groups.setdefault(tuple(sorted(face)), []).append(i)
    keep = np.ones(len(t), dtype=bool)
    for group in groups.values():
        if len(group) < 2:
            continue
        first = t[group[0]]
        n = np.cross(p[first[1]]-p[first[0]], p[first[2]]-p[first[0]])
        orientation = [np.dot(n, np.cross(p[t[i, 1]]-p[t[i, 0]], p[t[i, 2]]-p[t[i, 0]])) for i in group]
        keep[group[1:]] = False
        if any(value < 0 for value in orientation):
            keep[group[0]] = False  # opposite coincident part faces are interior
    t, ids = t[keep], ids[keep]
    mesh = trimesh.Trimesh(p, t, process=False)
    mesh.fix_normals(multibody=True)
    if not mesh.is_watertight or not mesh.is_winding_consistent:
        raise ValueError('shell is not a closed, consistently oriented manifold')
    # Edge incidence alone misses two closed solids touching at one vertex.
    for vertex in range(len(p)):
        incident = t[np.any(t == vertex, axis=1)]
        if not len(incident):
            continue
        links = [set(face) - {vertex} for face in incident]
        reached, pending = set(links[0]), links[1:]
        while pending:
            joined = [link for link in pending if link & reached]
            if not joined:
                raise ValueError('non-manifold vertex link')
            for link in joined:
                reached.update(link)
                pending.remove(link)
    if mesh.volume <= 0:
        raise ValueError('shell has non-positive enclosed volume')
    if len(t) > 2000:
        raise ValueError(f'collision triangle budget exceeded: {len(t)} > 2000')
    ground = [Polygon(s.rings[0][:, [0, 2]], [r[:, [0, 2]] for r in s.rings[1:]])
              for s in surfaces if s.kind == 'GroundSurface']
    actual = unary_union(ground)
    # Densify because vertex-only Hausdorff can miss differences in long segments.
    distance = shapely.hausdorff_distance(actual.boundary, footprint.boundary, densify=.1)
    if distance > .3:
        raise ValueError(f'footprint Hausdorff {distance:.6f} m exceeds 0.3 m')
    material = np.array([mat_id('roof' if surfaces[i].kind == 'RoofSurface' else 'building') for i in ids], np.uint8)
    return Shell(p, np.asarray(mesh.faces, np.uint32), material, ids, surfaces, actual, float(distance))
