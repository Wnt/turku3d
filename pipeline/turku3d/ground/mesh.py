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
    """Triangle-aware linear interpolation (never re-Delaunay a constrained mesh)."""

    def __init__(self, mesh):
        import matplotlib.tri as tri

        p, f = mesh.positions, mesh.indices
        # Triangle pc is CCW in x/z before our upward winding reversal.
        self.triangulation = tri.Triangulation(p[:, 0], p[:, 2], f[:, [0, 2, 1]])
        self.interpolator = tri.LinearTriInterpolator(self.triangulation, p[:, 1])

    def __call__(self, xz):
        xz = np.asarray(xz).reshape(-1, 2)
        return np.asarray(self.interpolator(xz[:, 0], xz[:, 1]).filled(np.nan))


def build_mesh(surface, bounds, tiles, curbs, water, water_level, materials, tolerance=0.01):
    """Build with 50 m support, then extract tiles by constrained triangle ownership.

    All neighbouring tiles use the SAME vertex array before compaction; no rounding or
    independent interpolation can change a seam. Triangle `pc` retains the convex hull.
    """
    envelope = box(*bounds.as_tuple())
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
    mids[tall & np.isin(mids, [mat_id(k) for k in (
        "road", "sidewalk", "crossing", "cycleway", "cobblestone", "paving_slab"
    )])] = mat_id("wall")
    wet = shapely.contains_xy(water, *centers.T)
    # Duplicate shoreline vertices for the sensor: ground-bank height is not water height.
    water_ids, inverse = np.unique(f[wet], return_inverse=True)
    wp = p[water_ids].copy()
    wp[:, 1] = water_level
    wf = inverse.reshape(-1, 3) + len(p)
    positions = np.r_[p, wp]
    indices = np.r_[f[~wet], wf].astype(np.uint32)
    material = np.r_[mids[~wet], np.full(wet.sum(), mat_id("water"), np.uint8)]
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
    return Mesh(mesh.positions, mesh.indices[inside], mesh.material[inside]).compact()


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
