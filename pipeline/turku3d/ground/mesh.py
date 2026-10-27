"""Canonical constrained triangulation, adaptive ground samples and exact tile seams."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import shapely
import triangle
from shapely.geometry import LineString, box

from turku3d import config as C
from turku3d.col.materials import mat_id

from .points import base_points


@dataclass
class Mesh:
    positions: np.ndarray
    indices: np.ndarray
    material: np.ndarray

    def compact(self):
        used, inverse = np.unique(self.indices, return_inverse=True)
        return Mesh(
            self.positions[used].astype(np.float64),
            inverse.reshape(-1, 3).astype(np.uint32),
            self.material.astype(np.uint8),
        )

    def canonical(self):
        """Compact with vertices in coordinate order and triangles in index order.

        Equal geometry then gives equal bytes, whichever envelope a tile was cut from."""
        mesh = self.compact()
        order = np.lexsort(mesh.positions.T[::-1])
        rank = np.empty(len(order), np.uint32)
        rank[order] = np.arange(len(order), dtype=np.uint32)
        faces = rank[mesh.indices]
        # Rotate each triangle to start at its lowest index; the winding is kept.
        faces = np.take_along_axis(faces, (faces.argmin(axis=1)[:, None] + np.arange(3)) % 3, 1)
        rows = np.lexsort((faces[:, 2], faces[:, 1], faces[:, 0], mesh.material))
        return Mesh(mesh.positions[order], faces[rows], mesh.material[rows])

    def save(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        with temporary.open("wb") as stream:
            np.savez_compressed(
                stream, positions=self.positions, indices=self.indices, material=self.material
            )
        if path.exists() and path.read_bytes() == temporary.read_bytes():
            temporary.unlink()
        else:
            temporary.replace(path)

    @classmethod
    def load(cls, path):
        with np.load(path, allow_pickle=False) as a:
            return cls(a["positions"], a["indices"], a["material"])


def parts(geometry, kinds=("LineString", "LinearRing")):
    if geometry.is_empty:
        return
    if geometry.geom_type in kinds:
        yield geometry
    elif hasattr(geometry, "geoms"):
        for part in geometry.geoms:
            yield from parts(part, kinds)


def triangulate(vertices, segments):
    data = {
        "vertices": vertices[:, :2],
        "vertex_attributes": vertices[:, 2:3],
        "segments": segments,
    }
    out = triangle.triangulate(data, "pc")
    p = np.column_stack(
        (out["vertices"][:, 0], out["vertex_attributes"][:, 0], out["vertices"][:, 1])
    )
    f = out["triangles"][:, [0, 2, 1]].astype(np.uint32)  # x/z is clockwise when viewed from +y
    return p, f


class HeightQuery:
    """Linear interpolation on the mesh's own triangles (never re-Delaunay a constrained mesh).

    Triangles are bucketed by the grid cells their bounding boxes overlap, so a query tests
    only the triangles of its own cell. Each triangle interpolates from its vertices in
    coordinate order, so the same triangle cut from another envelope gives the same bits.
    Points outside every triangle are NaN."""

    CHUNK = 262_144

    def __init__(self, mesh):
        p = np.asarray(mesh.positions, np.float64)
        f = np.asarray(mesh.indices, np.int64).reshape(-1, 3)
        xz = p[:, [0, 2]]
        # Canonical vertex order per triangle: lexicographic by x, then z.
        corners = xz[f]
        order = np.lexsort((corners[:, :, 1], corners[:, :, 0]), axis=-1)
        f = np.take_along_axis(f, order, axis=1)
        corners = xz[f]
        a, e1, e2 = corners[:, 0], corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0]
        det = e1[:, 0] * e2[:, 1] - e1[:, 1] * e2[:, 0]
        # One row per triangle: a, e1, e2, det, then the corner heights.
        self.rows = np.column_stack((a, e1, e2, det, p[f, 1]))
        self.key = corners.reshape(-1, 6)
        if not len(f):
            self.cells = np.empty(0, np.int64)
            self.triangles = np.empty(0, np.int64)
            self.origin, self.size, self.shape = np.zeros(2), 1.0, (0, 0)
            return
        lo, hi = corners.min(axis=1), corners.max(axis=1)
        self.origin = lo.min(axis=0)
        self.size = max(float(np.median(hi - lo)) / 2, 1e-3)
        first = np.floor((lo - self.origin) / self.size).astype(np.int64)
        last = np.floor((hi - self.origin) / self.size).astype(np.int64)
        self.shape = tuple(int(n) for n in last.max(axis=0) + 1)
        width = last[:, 0] - first[:, 0] + 1
        counts = width * (last[:, 1] - first[:, 1] + 1)
        owner = np.repeat(np.arange(len(f)), counts)
        local = np.arange(counts.sum()) - np.repeat(np.cumsum(counts) - counts, counts)
        i = first[owner, 0] + local % width[owner]
        j = first[owner, 1] + local // width[owner]
        cells = i * self.shape[1] + j
        order = np.argsort(cells, kind="stable")
        self.cells, self.triangles = cells[order], owner[order]

    def __call__(self, xz):
        xz = np.asarray(xz, np.float64).reshape(-1, 2)
        out = np.full(len(xz), np.nan)
        for start in range(0, len(xz), self.CHUNK):
            out[start : start + self.CHUNK] = self._query(xz[start : start + self.CHUNK])
        return out

    def _query(self, q):
        out = np.full(len(q), np.nan)
        if not len(self.cells):
            return out
        c = np.floor((q - self.origin) / self.size).astype(np.int64)
        valid = (c >= 0).all(axis=1) & (c[:, 0] < self.shape[0]) & (c[:, 1] < self.shape[1])
        ids = np.where(valid, c[:, 0] * self.shape[1] + c[:, 1], -1)
        left = np.searchsorted(self.cells, ids, "left")
        n = np.where(valid, np.searchsorted(self.cells, ids, "right") - left, 0)
        point = np.repeat(np.arange(len(q)), n)
        slot = np.repeat(left, n) + np.arange(n.sum()) - np.repeat(np.cumsum(n) - n, n)
        tri = self.triangles[slot]
        r = self.rows[tri]
        dx, dz = q[point, 0] - r[:, 0], q[point, 1] - r[:, 1]
        with np.errstate(divide="ignore", invalid="ignore"):
            u = (dx * r[:, 5] - dz * r[:, 4]) / r[:, 6]
            v = (r[:, 2] * dz - r[:, 3] * dx) / r[:, 6]
        eps = 1e-12
        inside = (r[:, 6] != 0) & (u >= -eps) & (v >= -eps) & (u + v <= 1 + eps)
        point, tri, u, v = point[inside], tri[inside], u[inside], v[inside]
        if not len(point):
            return out
        # Hits are grouped by point. A point on a shared edge picks the triangle with the
        # lowest corner coordinates.
        start = np.r_[True, point[1:] != point[:-1]]
        run = np.cumsum(start) - 1
        shared = np.flatnonzero(np.bincount(run)[run] > 1)
        keep = np.flatnonzero(np.bincount(run)[run] == 1)
        if len(shared):
            rank = shared[np.lexsort((*self.key[tri[shared]].T[::-1], point[shared]))]
            keep = np.r_[keep, rank[np.r_[True, point[rank][1:] != point[rank][:-1]]]]
        point, tri, u, v = point[keep], tri[keep], u[keep], v[keep]
        h = self.rows[tri, 7:]
        out[point] = h[:, 0] + u * (h[:, 1] - h[:, 0]) + v * (h[:, 2] - h[:, 0])
        return out


def build_mesh(
    surface, bounds, tiles, curbs, water, levels, materials, tolerance=0.01, bridges=None
):
    """Build with 50 m support, then extract tiles by constrained triangle ownership.

    `water` is the union of the water bodies and `levels` their (polygon, N2000 level) pairs;
    each wet triangle's sensor lies at the level of the body holding it.

    All neighbouring tiles use the SAME vertex array before compaction; no rounding or
    independent interpolation can change a seam. Triangle `pc` retains the convex hull.
    """
    envelope = box(*bounds.as_tuple())
    bridges = shapely.Polygon() if bridges is None else bridges.intersection(envelope)
    deck_cut = bridges.intersection(water.buffer(2.0))
    constraints = [envelope.boundary]
    for tile in tiles:
        constraints.append(box(*tile.bbox.as_tuple()).boundary)
    curb_lines, curb_heights = [], []
    for curb in curbs:
        if curb.kind == "quay":
            continue
        for line in curb.lines():
            curb_lines.append(LineString(line[:, :2]))
            curb_heights.append(line)
            constraints.append(curb_lines[-1])
        # Close the strips so face materials and bevels have well-defined ends.
        sides = curb.lines()
        constraints.extend([LineString([sides[0][i, :2], sides[1][i, :2]]) for i in (0, -1)])
    if not water.is_empty:
        constraints.extend(parts(water.intersection(envelope).boundary))
    if not deck_cut.is_empty:
        constraints.extend(parts(deck_cut.boundary))
    # Node intersections once, before Triangle, including curb/tile crossings.
    clipped = [g.intersection(envelope) for g in constraints]
    # City vectors can meet a lidar-snapped endpoint a few millimetres apart.
    # Node them on a 5 mm grid so Triangle cannot emit sub-mm sliver faces.
    network = shapely.union_all([g for g in clipped if not g.is_empty], grid_size=0.005)
    lines = list(parts(network))
    vertices, segments, lookup = [], [], {}
    for line in lines:
        chain = []
        # Densify constraints to avoid very long unconstrained shore/tile height spans.
        coords = shapely.get_coordinates(shapely.segmentize(line, 1.0))
        for xy in coords:
            key = tuple(np.round(xy, 9))
            if key not in lookup:
                lookup[key] = len(vertices)
                vertices.append(key)
            chain.append(lookup[key])
        segments.extend((a, b) for a, b in zip(chain, chain[1:], strict=False) if a != b)
    xy = np.array(vertices)
    h = surface.sample(xy)[0]
    # A shoreline constraint may be metres from the nearest ground return. Its
    # unconstrained local plane can then project a river-bank point above the deck.
    support, nearest = surface.tree.query(xy)
    unsupported = support > 2.0
    h[unsupported] = np.clip(
        h[unsupported],
        surface.points[nearest[unsupported], 1] - 0.3,
        surface.points[nearest[unsupported], 1] + 0.3,
    )
    if curb_lines:
        tree = shapely.STRtree(curb_lines)
        ids, distances = tree.query_nearest(
            shapely.points(xy), return_distance=True, all_matches=False
        )
        for qi, li, distance in zip(*ids, distances, strict=True):
            if distance < 0.004:
                original = curb_heights[li]
                chainage = np.r_[
                    0, np.cumsum(np.linalg.norm(np.diff(original[:, :2], axis=0), axis=1))
                ]
                along = curb_lines[li].project(shapely.Point(xy[qi]))
                h[qi] = np.interp(along, chainage, original[:, 2])
    fixed = np.c_[xy, h]
    # Start coarse, add observed half-metre cell samples only where error exceeds 1 cm.
    candidates = base_points(surface.points, 0.5)
    cxz = candidates[:, [0, 2]]
    inside = shapely.contains_xy(envelope, *cxz.T)
    if curb_lines:
        exclusion = shapely.union_all(curb_lines).buffer(0.25)
        inside &= ~shapely.contains_xy(exclusion, *cxz.T)
    inside &= ~shapely.contains_xy(water, *cxz.T)
    if not deck_cut.is_empty:
        inside &= ~shapely.contains_xy(deck_cut, *cxz.T)
    # Do not add vertices on a constraint (Triangle would split them again).
    constraint_tree = shapely.STRtree(lines)
    nearest, distances = constraint_tree.query_nearest(
        shapely.points(cxz), return_distance=True, all_matches=False
    )
    distance = np.empty(len(cxz))
    distance[nearest[0]] = distances
    candidates = candidates[inside & (distance > 0.005)]
    coarse = base_points(candidates, 2.0)
    nearest, distances = constraint_tree.query_nearest(
        shapely.points(coarse[:, [0, 2]]), return_distance=True, all_matches=False
    )
    distance = np.empty(len(coarse))
    distance[nearest[0]] = distances
    coarse = coarse[distance > 0.01]
    points = np.r_[fixed, coarse[:, [0, 2, 1]]]
    seg = np.array(segments, dtype=np.int32)
    p, f = triangulate(points, seg)
    refinement_counts = []
    remaining = candidates
    for _ in range(3):
        query = HeightQuery(Mesh(p, f, np.zeros(len(f), np.uint8)))
        error = np.abs(query(remaining[:, [0, 2]]) - remaining[:, 1])
        add = error > tolerance
        refinement_counts.append(int(add.sum()))
        if not add.any():
            break
        points = np.r_[points, remaining[add][:, [0, 2, 1]]]
        remaining = remaining[~add]
        p, f = triangulate(points, seg)
    centers = p[f].mean(axis=1)[:, [0, 2]]
    mids = materials.lookup(centers)
    # Only the strip between the double lines is curb collision material.
    for curb in curbs:
        if curb.kind == "quay":
            continue
        sides = curb.lines()
        polygon = shapely.make_valid(shapely.Polygon(np.r_[sides[0][:, :2], sides[1][::-1, :2]]))
        mids[shapely.contains_xy(polygon, *centers.T)] = mat_id(curb.kind)
    # The material on either side follows the measured rise, even where the
    # source street polygons stop short of the lidar edge.
    upper, lower = [], []
    for curb in curbs:
        if curb.kind != "curb":
            continue
        low, high = curb.lines()
        for i in range(len(low) - 1):
            low_side, high_side = (
                (low, high) if low[i : i + 2, 2].mean() < high[i : i + 2, 2].mean() else (high, low)
            )
            direction = high_side[i : i + 2, :2] - low_side[i : i + 2, :2]
            direction /= np.maximum(np.linalg.norm(direction, axis=1)[:, None], 1e-12)
            for line, offset, target in (
                (low_side, -direction, lower),
                (high_side, direction, upper),
            ):
                edge = line[i : i + 2, :2]
                polygon = shapely.make_valid(
                    shapely.Polygon(np.r_[edge, (edge + 1.0 * offset)[::-1]])
                )
                if not polygon.is_empty:
                    target.append(polygon)
    if lower:
        mids[
            shapely.contains_xy(shapely.union_all(lower, grid_size=1e-5), *centers.T)
            & (mids != mat_id("curb"))
        ] = mat_id("road")
        mids[
            shapely.contains_xy(shapely.union_all(upper, grid_size=1e-5), *centers.T)
            & (mids != mat_id("curb"))
        ] = mat_id("sidewalk")
    # Raw lidar terrain can contain an actual promenade retaining face away from
    # mapped curb lines. Its collision and visual mesh share vertices, so give any
    # too-tall walkable face an explicitly visible blocking material.
    triangles = p[f]
    normals = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
    up = np.abs(normals[:, 1]) / np.maximum(np.linalg.norm(normals, axis=1), 1e-12)
    tall = (np.ptp(triangles[:, :, 1], axis=1) > 0.35) & (up < np.cos(np.pi / 4))
    mids[
        tall
        & np.isin(
            mids,
            [
                mat_id(k)
                for k in ("road", "sidewalk", "crossing", "cycleway", "cobblestone", "paving_slab")
            ],
        )
    ] = mat_id("wall")
    wet = shapely.contains_xy(water, *centers.T)
    bridged_land = ~wet & shapely.contains_xy(deck_cut, *centers.T)
    # Duplicate shoreline vertices for the sensor: ground-bank height is not water height.
    level = np.full(len(f), np.nan)
    for polygon, value in levels:
        level[np.isnan(level) & wet & shapely.contains_xy(polygon, *centers.T)] = value
    if np.isnan(level[wet]).any():
        raise ValueError("a water triangle lies in no water body")
    water_ids, inverse = np.unique(f[wet], return_inverse=True)
    wp = p[water_ids].copy()
    wp[inverse.reshape(-1, 3), 1] = level[wet][:, None]
    wf = inverse.reshape(-1, 3) + len(p)
    positions = np.r_[p, wp]
    indices = np.r_[f[~wet & ~bridged_land], wf].astype(np.uint32)
    material = np.r_[mids[~wet & ~bridged_land], np.full(wet.sum(), mat_id("water"), np.uint8)]
    return Mesh(positions, indices, material), {
        "adaptive_added": refinement_counts,
        "triangles": len(indices),
    }


def tile_mesh(mesh, tile):
    centers = mesh.positions[mesh.indices].mean(axis=1)
    b = tile.bbox
    inside = (
        (centers[:, 0] >= b.xmin)
        & (centers[:, 0] < b.xmax)
        & (centers[:, 2] >= b.zmin)
        & (centers[:, 2] < b.zmax)
    )
    return Mesh(mesh.positions, mesh.indices[inside], mesh.material[inside]).canonical()


def seam_errors(meshes):
    """Compare actual boundary edge coordinates and multiplicities, not just vertex sets."""
    errors = []

    def boundary(mesh, axis, value):
        f = mesh.indices
        edges = np.concatenate([f[:, [0, 1]], f[:, [1, 2]], f[:, [2, 0]]])
        xyz = mesh.positions[edges]
        selected = np.all(xyz[:, :, axis] == value, axis=1)
        # Direction-independent byte equality, including water sensor edges.
        return sorted(tuple(sorted((a.tobytes(), b.tobytes()))) for a, b in xyz[selected])

    for tile, mesh in meshes.items():
        for axis, neighbor, value in [
            (0, C.CollisionTile(tile.i + 1, tile.j), tile.bbox.xmax),
            (2, C.CollisionTile(tile.i, tile.j + 1), tile.bbox.zmax),
        ]:
            if neighbor in meshes and boundary(mesh, axis, value) != boundary(
                meshes[neighbor], axis, value
            ):
                errors.append([tile.id, neighbor.id])
    return errors
