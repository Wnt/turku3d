"""A closed landmark shell from full-density lidar: footprint-bounded heightfield, budgeted TIN.

Airborne lidar sees landmarks from above: roofs, towers and spires at full density, walls
barely. The shell is therefore a 2.5D surface over the surveyed footprint, closed by vertical
walls on the footprint rings and a flat base below ground:

1. `heightfield`: the highest building return per cell (cells without one take their nearest
   neighbour, isolated spikes above every neighbour are clipped to them).
2. `shell`: greedy insertion (Garland and Heckbert, "Fast polygonal approximation of terrains
   and height fields", 1995) of the worst-approximated heightfield samples into a constrained
   Delaunay triangulation of the footprint, boundary samples included, until the closed shell
   reaches its triangle budget or its tolerance. The budget counts every triangle of the closed
   shell (roof surface, walls and base) and is never exceeded.

Coordinates are engine-local metres: x east, y up, z south.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import shapely
import triangle
from scipy import ndimage
from shapely.geometry import Polygon
from shapely.geometry.polygon import orient

SPIKE_M = 3.0  # A cell this far above all eight neighbours is noise (birds, wires).


@dataclass(frozen=True)
class Heightfield:
    """Cell-centred heights over an axis-aligned grid; row k is z = z0 + (k + 0.5) * cell."""

    x0: float
    z0: float
    cell: float
    heights: np.ndarray  # (rows, cols) float64, filled
    measured: np.ndarray  # (rows, cols) bool, True where a return fell in the cell

    def sample(self, x, z):
        col = np.clip(
            ((np.asarray(x) - self.x0) / self.cell).astype(int), 0, self.heights.shape[1] - 1
        )
        row = np.clip(
            ((np.asarray(z) - self.z0) / self.cell).astype(int), 0, self.heights.shape[0] - 1
        )
        return self.heights[row, col]


@dataclass
class Shell:
    positions: np.ndarray  # (N, 3) float64
    indices: np.ndarray  # (M, 3) uint32, outward winding
    part: np.ndarray  # (M,) uint8: TOP, WALL or BASE
    max_error_m: float  # largest vertical deviation from the heightfield at the samples


TOP, WALL, BASE = 0, 1, 2


def heightfield(
    points: np.ndarray, footprint: Polygon, cell: float, reach: float = 1.0
) -> Heightfield:
    """Max height per cell of `points` (x, y, z) within `reach` of the footprint."""
    if cell <= 0:
        raise ValueError("cell must be positive")
    area = footprint.buffer(reach)
    xmin, zmin, xmax, zmax = area.bounds
    cols = max(1, int(np.ceil((xmax - xmin) / cell)))
    rows = max(1, int(np.ceil((zmax - zmin) / cell)))
    pts = np.asarray(points, np.float64).reshape(-1, 3)
    pts = pts[shapely.contains_xy(area, pts[:, 0], pts[:, 2])]
    if not len(pts):
        raise ValueError("no lidar returns inside the footprint")
    col = np.clip(((pts[:, 0] - xmin) / cell).astype(int), 0, cols - 1)
    row = np.clip(((pts[:, 2] - zmin) / cell).astype(int), 0, rows - 1)
    heights = np.full((rows, cols), -np.inf)
    np.maximum.at(heights, (row, col), pts[:, 1])
    measured = np.isfinite(heights)
    neighbours = ndimage.maximum_filter(
        np.where(measured, heights, -np.inf), footprint=np.array([[1, 1, 1], [1, 0, 1], [1, 1, 1]])
    )
    spikes = measured & np.isfinite(neighbours) & (heights > neighbours + SPIKE_M)
    heights[spikes] = neighbours[spikes]
    _, (near_row, near_col) = ndimage.distance_transform_edt(~measured, return_indices=True)
    heights = heights[near_row, near_col]
    return Heightfield(xmin, zmin, cell, heights, measured)


def _rings(footprint: Polygon) -> list[np.ndarray]:
    """Exterior counter-clockwise and holes clockwise in (x, z), closing vertex dropped."""
    poly = orient(footprint, 1.0)
    return [np.asarray(r.coords)[:-1, :2] for r in (poly.exterior, *poly.interiors)]


def _boundary_samples(rings, spacing):
    """Candidate points along every ring edge; corners are always kept (`fixed`)."""
    xy, fixed, ring_id, order = [], [], [], []
    for r, ring in enumerate(rings):
        k = 0
        for a, b in zip(ring, np.roll(ring, -1, axis=0), strict=True):
            n = max(1, int(np.ceil(np.linalg.norm(b - a) / spacing)))
            for t in range(n):
                xy.append(a + (b - a) * (t / n))
                fixed.append(t == 0)
                ring_id.append(r)
                order.append(k)
                k += 1
    return np.array(xy), np.array(fixed), np.array(ring_id), np.array(order)


def _inward(footprint, xy, depth):
    """Boundary samples moved `depth` towards the inside, so heights come from the building."""
    centre = np.asarray(footprint.representative_point().coords[0])
    out = []
    boundary = footprint.boundary
    for p in xy:
        q = boundary.interpolate(boundary.project(shapely.Point(p)))
        # Probe both normal directions; keep the one inside the footprint.
        tangent = np.asarray(
            boundary.interpolate(boundary.project(q) + 0.01).coords[0]
        ) - np.asarray(q.coords[0])
        norm = np.linalg.norm(tangent)
        normal = np.array([-tangent[1], tangent[0]]) / norm if norm > 0 else centre - p
        for candidate in (p + normal * depth, p - normal * depth):
            if footprint.contains(shapely.Point(candidate)):
                out.append(candidate)
                break
        else:
            out.append(p)
    return np.array(out)


def _locate(tris_xy, points, cell, origin, grid_shape, index):
    """Triangle containing each grid sample (or -1), by rasterising triangle bounding boxes."""
    rows, cols = grid_shape
    owner = np.full(len(points), -1, np.int64)
    lo = np.floor((tris_xy.min(axis=1) - origin) / cell - 0.5).astype(int)
    hi = np.ceil((tris_xy.max(axis=1) - origin) / cell - 0.5).astype(int)
    lo = np.clip(lo, 0, [cols - 1, rows - 1])
    hi = np.clip(hi, 0, [cols - 1, rows - 1])
    span = hi - lo + 1
    counts = span[:, 0] * span[:, 1]
    tri = np.repeat(np.arange(len(tris_xy)), counts)
    offset = np.arange(counts.sum()) - np.repeat(np.cumsum(counts) - counts, counts)
    gc = lo[tri, 0] + offset % span[tri, 0]
    gr = lo[tri, 1] + offset // span[tri, 0]
    sample = index[gr, gc]
    keep = sample >= 0
    tri, sample = tri[keep], sample[keep]
    a, b, c = (tris_xy[tri, k] for k in range(3))
    p = points[sample]
    v0, v1, v2 = b - a, c - a, p - a
    d00 = (v0 * v0).sum(1)
    d01 = (v0 * v1).sum(1)
    d11 = (v1 * v1).sum(1)
    d20 = (v2 * v0).sum(1)
    d21 = (v2 * v1).sum(1)
    den = d00 * d11 - d01 * d01
    ok = np.abs(den) > 1e-12
    v = np.where(ok, (d11 * d20 - d01 * d21) / np.where(ok, den, 1), -1)
    w = np.where(ok, (d00 * d21 - d01 * d20) / np.where(ok, den, 1), -1)
    inside = ok & (v >= -1e-9) & (w >= -1e-9) & (v + w <= 1 + 1e-9)
    owner[sample[inside]] = tri[inside]
    bary = np.zeros((len(points), 2))
    bary[sample[inside]] = np.column_stack((v[inside], w[inside]))
    return owner, bary


def _triangulate(xy, segments, holes):
    data = {"vertices": xy, "segments": segments}
    if len(holes):
        data["holes"] = holes
    out = triangle.triangulate(data, "pQ")
    if len(out["vertices"]) != len(xy) or not np.allclose(out["vertices"], xy):
        raise ValueError("triangulation added Steiner points")
    return out["triangles"].astype(np.int64)


def shell(
    field: Heightfield,
    footprint: Polygon,
    base: float,
    budget: int,
    tolerance: float = 0.05,
) -> Shell:
    """The closed shell over `footprint`, at most `budget` triangles in total."""
    if not footprint.is_valid or footprint.is_empty or footprint.geom_type != "Polygon":
        raise ValueError("footprint must be one valid polygon")
    rings = _rings(footprint)
    corners = sum(len(r) for r in rings)
    holes = np.array(
        [np.asarray(Polygon(r).representative_point().coords[0]) for r in rings[1:]]
    ).reshape(-1, 2)
    # Base triangulation of the corner rings alone: the smallest closed shell.
    spacing = field.cell
    bxy, bfixed, bring, border = _boundary_samples(rings, spacing)
    bheight = field.sample(*_inward(footprint, bxy, field.cell).T)
    # Interior samples: cell centres at least half a cell inside the footprint.
    rows, cols = field.heights.shape
    gz, gx = np.mgrid[0:rows, 0:cols]
    cx = field.x0 + (gx + 0.5) * field.cell
    cz = field.z0 + (gz + 0.5) * field.cell
    inner = footprint.buffer(-0.5 * field.cell)
    inside = shapely.contains_xy(inner, cx, cz) if not inner.is_empty else np.zeros_like(cx, bool)
    index = np.full((rows, cols), -1, np.int64)
    index[inside] = np.arange(inside.sum())
    ixy = np.column_stack((cx[inside], cz[inside]))
    iheight = field.heights[inside]

    def closed_triangles(top_tris, n_boundary, n_base_tris):
        return top_tris + 2 * n_boundary + n_base_tris

    base_tris = len(_triangulate(*_pslg(bxy[bfixed], bring[bfixed], holes)[:2], holes))
    minimum = closed_triangles(base_tris, corners, base_tris)
    if budget < minimum:
        raise ValueError(f"budget {budget} is below the {minimum} triangles of the footprint box")

    chosen_b = bfixed.copy()
    chosen_i = np.zeros(len(ixy), bool)
    best = None
    while True:
        sel_b = np.flatnonzero(chosen_b)
        sel_i = np.flatnonzero(chosen_i)
        xy_b = bxy[sel_b]
        xy, segments, _ = _pslg(xy_b, bring[sel_b], holes, extra=ixy[sel_i])
        tris = _triangulate(xy, segments, holes)
        heights = np.concatenate((bheight[sel_b], iheight[sel_i]))
        base_count = len(_triangulate(*_pslg(xy_b, bring[sel_b], holes)[:2], holes))
        total = closed_triangles(len(tris), len(sel_b), base_count)
        if total > budget:
            break
        # Errors: interior samples by their triangle, boundary samples along their ring.
        owner, bary = _locate(
            xy[tris], ixy, field.cell, np.array([field.x0, field.z0]), (rows, cols), index
        )
        h3 = heights[tris]
        approx = np.where(
            owner >= 0,
            h3[owner, 0]
            + bary[:, 0] * (h3[owner, 1] - h3[owner, 0])
            + bary[:, 1] * (h3[owner, 2] - h3[owner, 0]),
            iheight,
        )
        ierr = np.abs(approx - iheight)
        ierr[chosen_i] = 0
        berr = _ring_error(bxy, bheight, bring, border, chosen_b)
        best = (
            xy,
            tris,
            heights,
            len(sel_b),
            max(float(ierr.max(initial=0)), float(berr.max(initial=0))),
        )
        worst = best[4]
        if worst <= tolerance:
            break
        # Insert the worst samples in a batch sized to the remaining budget.
        room = max(1, (budget - total) // 6)
        errors = np.concatenate((berr, ierr))
        order = np.argsort(-errors, kind="stable")[:room]
        order = order[errors[order] > tolerance]
        if not len(order):
            break
        chosen_b[order[order < len(bxy)]] = True
        chosen_i[order[order >= len(bxy)] - len(bxy)] = True
    if best is None:
        raise ValueError("budget too small for the footprint")
    xy, tris, heights, n_boundary, error = best
    return _close(xy, tris, heights, n_boundary, holes, base, error)


def _pslg(xy_b, ring_b, holes, extra=None):
    """Vertices and ring segments: boundary samples first (in ring order), then interior."""
    segments = []
    start = 0
    for r in np.unique(ring_b):
        n = int((ring_b == r).sum())
        idx = np.arange(start, start + n)
        segments.append(np.column_stack((idx, np.roll(idx, -1))))
        start += n
    xy = xy_b if extra is None or not len(extra) else np.vstack((xy_b, extra))
    return xy, np.vstack(segments), holes


def _ring_error(bxy, bheight, bring, border, chosen):
    """Height error of each boundary sample against the chosen samples of its ring."""
    err = np.zeros(len(bxy))
    for r in np.unique(bring):
        members = np.flatnonzero(bring == r)
        members = members[np.argsort(border[members])]
        picked = members[chosen[members]]
        # Arc length along the ring, wrapping past the last sample.
        seg = np.linalg.norm(np.diff(np.vstack((bxy[members], bxy[members[:1]])), axis=0), axis=1)
        s = np.concatenate(([0.0], np.cumsum(seg)))
        length = s[-1]
        pos = {m: s[k] for k, m in enumerate(members)}
        ps = np.array([pos[m] for m in picked])
        ph = bheight[picked]
        ps_wrap = np.concatenate((ps, [ps[0] + length]))
        ph_wrap = np.concatenate((ph, [ph[0]]))
        q = s[:-1]
        err[members] = np.abs(np.interp(q, ps_wrap, ph_wrap) - bheight[members])
    err[chosen] = 0
    return err


def _close(xy, tris, heights, n_boundary, holes, base, error):
    """Roof surface + one wall quad per boundary edge + base; outward winding, shared edges."""
    top = np.column_stack((xy[:, 0], heights, xy[:, 1]))
    bottom = np.column_stack((xy[:n_boundary, 0], np.full(n_boundary, base), xy[:n_boundary, 1]))
    positions = np.vstack((top, bottom))
    nt = len(top)
    faces, parts = [], []
    top_tris = _upward(top, tris)
    faces.append(top_tris)
    parts.append(np.full(len(top_tris), TOP))
    # Segments of the boundary (indices < n_boundary) as triangle produced them.
    segments = _boundary_edges(top_tris, n_boundary)
    walls = []
    for a, b in segments:
        # The roof triangle on edge a->b runs a->b, so the outward wall runs b->a on top.
        walls.append([b, a, nt + a])
        walls.append([b, nt + a, nt + b])
    walls = np.array(walls, np.int64)
    faces.append(walls)
    parts.append(np.full(len(walls), WALL))
    base_tris = triangle.triangulate(
        {
            "vertices": xy[:n_boundary],
            "segments": segments,
            **({"holes": holes} if len(holes) else {}),
        },
        "pQ",
    )["triangles"].astype(np.int64)
    base_tris = _upward(bottom, base_tris)[:, ::-1] + nt
    faces.append(base_tris)
    parts.append(np.full(len(base_tris), BASE))
    indices = np.vstack(faces)
    return Shell(
        positions, indices.astype(np.uint32), np.concatenate(parts).astype(np.uint8), error
    )


def _upward(points, tris):
    """Wind triangles counter-clockwise seen from above (+y normal)."""
    p = points[tris]
    ny = (p[:, 1, 2] - p[:, 0, 2]) * (p[:, 2, 0] - p[:, 0, 0]) - (p[:, 1, 0] - p[:, 0, 0]) * (
        p[:, 2, 2] - p[:, 0, 2]
    )
    out = tris.copy()
    flip = ny < 0
    out[flip] = out[flip][:, [0, 2, 1]]
    return out


def _boundary_edges(tris, n_boundary):
    """Boundary edges in the direction the upward roof triangles traverse them."""
    edges = {}
    for t in tris:
        for k in range(3):
            a, b = int(t[k]), int(t[(k + 1) % 3])
            edges[(a, b)] = edges.get((a, b), 0) + 1
    boundary = [(a, b) for (a, b) in edges if (b, a) not in edges]
    if any(a >= n_boundary or b >= n_boundary for a, b in boundary):
        raise ValueError("an interior sample lies on the footprint boundary")
    return np.array(boundary, np.int64)
