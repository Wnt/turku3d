"""Measurements on one mesh (an entity's lod 0 or an instanced asset): topology, solids, thickness, density, UVs.

Rule ids refer to docs/design/render-readiness.md.
"""

from __future__ import annotations

import numpy as np
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components
from scipy.spatial import cKDTree

from .glbfile import Primitive

SNAP = 1e-4  # the packer's position grid (G3): topology is judged after this weld
LOOSE = 2e-3  # borders that close under this weld are near misses, not openings
ON_EDGE = 3e-4  # a vertex this close to another border edge makes a T-junction
ZERO_AREA = 1e-8  # m2
SMALL_AREA = 0.25e-4  # m2: 0.25 cm2 (G4)
THIN = 0.10  # m (F5)
STRETCH = 0.05  # metric UV tolerance (M4)


def weld(positions: np.ndarray, step: float) -> tuple[np.ndarray, np.ndarray]:
    """(index per vertex, welded points) on a grid of `step` metres."""
    keys = np.round(positions / step).astype(np.int64)
    unique, inverse = np.unique(keys, axis=0, return_inverse=True)
    return inverse.reshape(-1), unique * step


def assemble(primitives: list[Primitive], surfaces: tuple[str, ...] | None = None):
    """(positions, triangles, primitive index per triangle) of the chosen surface types, concatenated."""
    positions, triangles, owner, base = [], [], [], 0
    for index, primitive in enumerate(primitives):
        if surfaces is not None and primitive.material.surface not in surfaces:
            continue
        positions.append(primitive.positions)
        triangles.append(primitive.triangles + base)
        owner.append(np.full(len(primitive.triangles), index))
        base += len(primitive.positions)
    if not positions:
        return np.zeros((0, 3)), np.zeros((0, 3), np.int64), np.zeros(0, np.int64)
    return np.vstack(positions), np.vstack(triangles), np.concatenate(owner)


def areas(positions: np.ndarray, triangles: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(area, unit normal) per triangle."""
    a, b, c = positions[triangles[:, 0]], positions[triangles[:, 1]], positions[triangles[:, 2]]
    cross = np.cross(b - a, c - a)
    length = np.linalg.norm(cross, axis=1)
    return length / 2, cross / np.where(length > 0, length, 1.0)[:, None]


def edges_of(tri: np.ndarray, count: int):
    """Undirected edges of welded triangles: (lo, hi, uses, forward uses, edge index per triangle corner)."""
    a, b = tri.reshape(-1), tri[:, [1, 2, 0]].reshape(-1)
    lo, hi = np.minimum(a, b), np.maximum(a, b)
    keys, inverse, uses = np.unique(lo * count + hi, return_inverse=True, return_counts=True)
    forward = np.bincount(inverse, weights=(a < b), minlength=len(keys))
    return keys // count, keys % count, uses, forward, inverse.reshape(-1, 3)


def border_length(positions: np.ndarray, triangles: np.ndarray, step: float) -> float:
    index, points = weld(positions, step)
    tri = index[triangles]
    tri = tri[(tri[:, 0] != tri[:, 1]) & (tri[:, 1] != tri[:, 2]) & (tri[:, 0] != tri[:, 2])]
    if not len(tri):
        return 0.0
    lo, hi, uses, _, _ = edges_of(tri, len(points))
    border = uses == 1
    return float(np.linalg.norm(points[lo[border]] - points[hi[border]], axis=1).sum())


def t_junctions(a: np.ndarray, b: np.ndarray, limit: int = 60000) -> tuple[np.ndarray, np.ndarray]:
    """Border edges (a[i] -> b[i]) that take part in a T-junction: (long edges that carry another border edge's
    midpoint, the shorter edges lying on them). The seam's length is that of the long edges."""
    count = len(a)
    carrying, lying = np.zeros(count, bool), np.zeros(count, bool)
    if count < 2:
        return carrying, lying
    middle, vector = (a + b) / 2, b - a
    length = np.linalg.norm(vector, axis=1)
    tree = cKDTree(middle)
    order = np.argsort(-length)[:limit]
    near = tree.query_ball_point(middle[order], length[order] / 2 + ON_EDGE)
    for edge, candidates in zip(order, near, strict=True):
        if len(candidates) < 2 or length[edge] < 2 * ON_EDGE:
            continue
        others = np.array([c for c in candidates if c != edge])
        others = others[length[others] < length[edge] - ON_EDGE]
        if not len(others):
            continue
        along = (middle[others] - a[edge]) @ vector[edge] / length[edge] ** 2
        foot = a[edge] + np.clip(along, 0.0, 1.0)[:, None] * vector[edge]
        on = (np.linalg.norm(middle[others] - foot, axis=1) < ON_EDGE) & (along > 0) & (along < 1)
        if on.any():
            carrying[edge] = True
            lying[others[on]] = True
    return carrying, lying & ~carrying


def topology(positions: np.ndarray, triangles: np.ndarray) -> dict:
    """Welds, then reports borders, T-junctions, bad triangles and the solids (connected pieces) of the mesh."""
    area, _ = areas(positions, triangles)
    index, points = weld(positions, SNAP)
    exact = (
        len(np.unique(positions.astype(np.float32).view(np.uint32).reshape(-1, 3), axis=0))
        if len(positions)
        else 0
    )
    tri = index[triangles]
    collapsed = (tri[:, 0] == tri[:, 1]) | (tri[:, 1] == tri[:, 2]) | (tri[:, 0] == tri[:, 2])
    zero = collapsed | (area < ZERO_AREA)
    tri, kept_area, kept = tri[~zero], area[~zero], np.flatnonzero(~zero)
    out = {
        "triangles": int(len(triangles)),
        "vertices": int(len(points)),
        "area_m2": float(area.sum()),
        "zero_area": int(zero.sum()),
        "small_share": float((area < SMALL_AREA).mean()) if len(area) else 0.0,
        "unsnapped_vertices": int(exact - len(points)),
    }
    if not len(tri):
        return {
            **out,
            "duplicates": 0,
            "border_m": 0.0,
            "tjunction_m": 0.0,
            "nearmiss_m": 0.0,
            "open_m": 0.0,
            "nonmanifold_m": 0.0,
            "flipped_edges": 0,
            "solids": [],
            "welded": (points, tri),
            "kept": kept,
            "triangle_border_m": np.zeros((0, 2)),
        }
    _, duplicate_counts = np.unique(np.sort(tri, axis=1), axis=0, return_counts=True)
    lo, hi, uses, forward, corner_edge = edges_of(tri, len(points))
    length = np.linalg.norm(points[lo] - points[hi], axis=1)
    border, nonmanifold = uses == 1, uses > 2
    border_index = np.flatnonzero(border)
    carrying, lying = t_junctions(points[lo[border_index]], points[hi[border_index]])
    border_m = float(length[border].sum())
    tjunction_m = float(length[border_index[carrying]].sum())
    in_t = float(length[border_index[carrying | lying]].sum())
    loose_m = border_length(positions, triangles, LOOSE) if border_m > 0 else 0.0
    nearmiss_m = min(max(0.0, border_m - loose_m), max(0.0, border_m - in_t))
    out.update(
        duplicates=int((duplicate_counts - 1).sum()),
        border_m=border_m,
        tjunction_m=tjunction_m,
        nearmiss_m=nearmiss_m,
        open_m=max(0.0, border_m - in_t - nearmiss_m),
        nonmanifold_m=float(length[nonmanifold].sum()),
        flipped_edges=int(((uses == 2) & (forward != 1)).sum()),
    )
    # Solids: triangles joined through shared edges.
    flat = corner_edge.reshape(-1)
    order = np.argsort(flat, kind="stable")
    owner = np.repeat(np.arange(len(tri)), 3)[order]
    same = flat[order][1:] == flat[order][:-1]
    graph = coo_matrix(
        (np.ones(int(same.sum()), bool), (owner[:-1][same], owner[1:][same])),
        shape=(len(tri), len(tri)),
    )
    solid_count, labels = connected_components(graph, directed=False)
    corners = points[tri]
    centre = points.mean(axis=0)
    signed = (
        np.einsum(
            "ij,ij->i",
            corners[:, 0] - centre,
            np.cross(corners[:, 1] - centre, corners[:, 2] - centre),
        )
        / 6
    )
    solid_area = np.bincount(labels, weights=kept_area, minlength=solid_count)
    solid_volume = np.bincount(labels, weights=signed, minlength=solid_count)
    solid_triangles = np.bincount(labels, minlength=solid_count)
    edge_solid = np.empty(len(uses), int)
    edge_solid[flat] = np.repeat(labels, 3)
    solid_border = np.bincount(
        edge_solid, weights=(border | nonmanifold) * length, minlength=solid_count
    )
    by_solid = np.argsort(labels, kind="stable")
    starts = np.concatenate([[0], np.cumsum(solid_triangles)])
    solids = []
    for solid in range(solid_count):
        members = corners[by_solid[starts[solid] : starts[solid + 1]]].reshape(-1, 3)
        size = members.max(axis=0) - members.min(axis=0)
        closed = solid_border[solid] < 1e-6
        volume = float(solid_volume[solid])
        solids.append(
            {
                "triangles": int(solid_triangles[solid]),
                "area_m2": float(solid_area[solid]),
                "closed": bool(closed),
                "volume_m3": volume if closed else None,
                "border_m": float(solid_border[solid]),
                "size_m": [float(v) for v in size],
                # Hydraulic thickness of a closed solid: a slab gives its thickness, a rod half its diameter.
                "thickness_m": float(2 * abs(volume) / solid_area[solid])
                if closed and solid_area[solid] > 0
                else None,
                "inside_out": bool(closed and volume < 0),
            }
        )
    # Per kept triangle: the length of its border edges, and of those that are T-junction seams.
    in_seam = np.zeros(len(uses), bool)
    in_seam[border_index[carrying | lying]] = True
    triangle_border = np.column_stack(
        [
            (border[corner_edge] * length[corner_edge]).sum(axis=1),
            (in_seam[corner_edge] * length[corner_edge]).sum(axis=1),
        ]
    )
    return {
        **out,
        "solids": solids,
        "welded": (points, tri),
        "kept": kept,
        "triangle_border_m": triangle_border,
    }


def first_hits(
    start: np.ndarray,
    direction: np.ndarray,
    corners: np.ndarray,
    reach: float,
    skip: np.ndarray | None = None,
):
    """(distance, triangle index) of the first triangle each ray meets within `reach`; inf and -1 when none.
    `skip[i]` is a triangle ray i ignores (the one it starts on)."""
    count = len(start)
    best, which = np.full(count, np.inf), np.full(count, -1)
    low, high = corners.min(axis=1), corners.max(axis=1)
    edge1, edge2 = corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0]
    chunk = max(1, min(count, int(4e6 // max(1, len(corners)))))
    for first in range(0, count, chunk):
        rays = slice(first, first + chunk)
        o, d = start[rays], direction[rays]
        end = o + d * reach
        seg_low, seg_high = np.minimum(o, end) - 1e-6, np.maximum(o, end) + 1e-6
        mask = np.all(high[None] >= seg_low[:, None], axis=2) & np.all(
            low[None] <= seg_high[:, None], axis=2
        )
        if skip is not None:
            mask[np.arange(len(o)), skip[rays]] = False
        ray, tri = np.nonzero(mask)
        if not len(ray):
            continue
        p = np.cross(d[ray], edge2[tri])
        det = np.einsum("ij,ij->i", edge1[tri], p)
        ok = np.abs(det) > 1e-12
        inv = np.where(ok, 1.0 / np.where(ok, det, 1.0), 0.0)
        offset = o[ray] - corners[tri, 0]
        u = np.einsum("ij,ij->i", offset, p) * inv
        q = np.cross(offset, edge1[tri])
        v = np.einsum("ij,ij->i", d[ray], q) * inv
        t = np.einsum("ij,ij->i", edge2[tri], q) * inv
        hit = ok & (u >= -1e-9) & (v >= -1e-9) & (u + v <= 1 + 1e-9) & (t > 1e-5) & (t <= reach)
        ray, tri, t = first + ray[hit], tri[hit], t[hit]
        order = np.lexsort((t, ray))
        ray, tri, t = ray[order], tri[order], t[order]
        lead = np.concatenate([[True], ray[1:] != ray[:-1]]) if len(ray) else np.zeros(0, bool)
        best[ray[lead]], which[ray[lead]] = t[lead], tri[lead]
    return best, which


def surface_points(positions: np.ndarray, triangles: np.ndarray, samples: int, seed: int = 7):
    """(points, unit normals, triangle indices) spread evenly by area over the surface."""
    area, normal = areas(positions, triangles)
    good = area > ZERO_AREA
    rng = np.random.default_rng(seed)
    pick = rng.choice(np.flatnonzero(good), size=samples, p=area[good] / area[good].sum())
    weights = rng.random((samples, 2))
    flip = weights.sum(axis=1) > 1
    weights[flip] = 1 - weights[flip]
    corners = positions[triangles]
    points = (
        corners[pick, 0]
        + weights[:, :1] * (corners[pick, 1] - corners[pick, 0])
        + weights[:, 1:] * (corners[pick, 2] - corners[pick, 0])
    )
    return points, normal[pick], pick


AXES = np.array([[1, 0, 0], [-1, 0, 0], [0, 1, 0], [0, -1, 0], [0, 0, 1], [0, 0, -1]], dtype=float)


def ray_thickness(
    positions: np.ndarray, triangles: np.ndarray, samples: int = 256, reach: float = 2.0
) -> dict:
    """Two ray tests on the opaque surface.

    Thickness: the distance from a surface point, along the inward normal, to the next surface. A sheet has nothing
    behind it; a hollow shell shows its wall thickness.
    Escape: from a point just inside the surface, the share of the six axis directions that leave the mesh without
    meeting it. A closed solid lets none out; a building without a bottom lets a sixth out; a sheet half or more.
    This is the share of directions through which a distance field of the object leaks light (F1, F2)."""
    area, _ = areas(positions, triangles)
    if (area > ZERO_AREA).sum() < 2 or samples < 1:
        return {"samples": 0}
    corners = positions[triangles]
    points, normal, pick = surface_points(positions, triangles, samples)
    depth, _ = first_hits(points, -normal, corners, reach, pick)
    found = np.isfinite(depth)
    inset = np.minimum(0.15, np.where(found, depth / 2, 0.15))
    probes = np.repeat(points - normal * inset[:, None], len(AXES), axis=0)
    size = float(np.linalg.norm(positions.max(axis=0) - positions.min(axis=0))) + 1.0
    far, _ = first_hits(probes, np.tile(AXES, (samples, 1)), corners, size)
    return {
        "samples": samples,
        "thin_share": float((depth < THIN).mean()),
        "backed_share": float(found.mean()),
        "min_m": float(depth[found].min()) if found.any() else None,
        "p05_m": float(np.percentile(depth[found], 5)) if found.any() else None,
        "escape_share": float((~np.isfinite(far)).mean()),
    }


def repeated_share(positions: np.ndarray, triangles: np.ndarray) -> float:
    """The share of triangles that have at least two congruent, parallel copies in the same mesh: an upper bound
    on what instancing its repeated assemblies would save (I1)."""
    if len(triangles) < 3:
        return 0.0
    corners = positions[triangles]
    sides = np.sort(
        np.stack(
            [
                np.linalg.norm(corners[:, i] - corners[:, j], axis=1)
                for i, j in ((0, 1), (1, 2), (2, 0))
            ],
            axis=1,
        ),
        axis=1,
    )
    _, normal = areas(positions, triangles)
    keys = np.round(np.hstack([sides * 1000, normal * 50])).astype(np.int64)
    _, inverse, counts = np.unique(keys, axis=0, return_inverse=True, return_counts=True)
    return float((counts[inverse.reshape(-1)] >= 3).mean())


def uv_stretch(primitive: Primitive) -> tuple[float, float]:
    """(area with metric UVs, area whose UV edges differ from the surface by more than the tolerance)."""
    if primitive.uvs is None or not len(primitive.triangles):
        return 0.0, 0.0
    area, _ = areas(primitive.positions, primitive.triangles)
    worst = np.zeros(len(primitive.triangles))
    for i, j in ((0, 1), (1, 2), (2, 0)):
        world = np.linalg.norm(
            primitive.positions[primitive.triangles[:, i]]
            - primitive.positions[primitive.triangles[:, j]],
            axis=1,
        )
        chart = np.linalg.norm(
            primitive.uvs[primitive.triangles[:, i]] - primitive.uvs[primitive.triangles[:, j]],
            axis=1,
        )
        ratio = np.abs(chart / np.where(world > 1e-3, world, 1.0) - 1.0)
        worst = np.maximum(worst, np.where(world > 1e-3, ratio, 0.0))
    return float(area.sum()), float(area[worst > STRETCH].sum())


def repeats(solids: list[dict]) -> dict:
    """Solids of the same shape that occur more than twice in one mesh: geometry that could be an instance (I1)."""
    shapes: dict[tuple, list[int]] = {}
    for solid in solids:
        if solid["triangles"] < 4:
            continue
        key = (
            solid["triangles"],
            round(solid["area_m2"], 3),
            round(sorted(solid["size_m"])[2], 2),
            round(solid["size_m"][1], 2),
        )
        shapes.setdefault(key, []).append(solid["triangles"])
    repeated = {key: rows for key, rows in shapes.items() if len(rows) > 2}
    return {
        "shapes": len(repeated),
        "copies": sum(len(rows) for rows in repeated.values()),
        "triangles": sum(sum(rows) for rows in repeated.values()),
        "saved_triangles": sum(sum(rows[1:]) for rows in repeated.values()),
    }
