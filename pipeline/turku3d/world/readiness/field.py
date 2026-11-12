"""Does a signed distance field of this mesh have an inside? (F1, F2, F5 of docs/design/render-readiness.md)

A distance field is signed only where the surface separates an inside from the outside at the field's resolution.
The mesh's opaque triangles are voxelised, the free space is flooded from the grid's corner, and what the flood
cannot reach is the inside. A shell with a gap wider than a voxel floods completely: light would pass through it.
"""

from __future__ import annotations

import numpy as np
from scipy import ndimage

MAX_CELLS = 5_000_000
MAX_SAMPLES = 30_000_000


def voxel_size(extent: np.ndarray) -> float:
    """The resolution a per-object field would get: about a hundred voxels along the longest side, 2 to 25 cm."""
    return float(np.clip(extent.max() / 96, 0.02, 0.25))


def samples(corners: np.ndarray, spacing: float, centres: bool = False):
    """Points on triangles no further apart than `spacing`: batches of (points (t, k, 3), triangle indices, weights
    (k,)). Rows run parallel to each triangle's longest edge, so a sliver costs a few points, not a square of them.
    The default lattice includes the edges (it finds every voxel a triangle passes through); `centres` gives cell
    centres whose weights are each point's share of the triangle's area."""
    sides = np.stack(
        [np.linalg.norm(corners[:, (k + 1) % 3] - corners[:, k], axis=1) for k in range(3)], axis=1
    )
    turn = np.argmax(sides, axis=1)
    index = np.arange(len(corners))
    a, b, c = corners[index, turn], corners[index, (turn + 1) % 3], corners[index, (turn + 2) % 3]
    base = sides[index, turn]
    height = np.linalg.norm(np.cross(b - a, c - a), axis=1) / np.where(base > 0, base, 1.0)
    along = 2 ** np.ceil(np.log2(np.maximum(1, np.ceil(base / spacing)))).astype(int)
    across = 2 ** np.ceil(np.log2(np.maximum(1, np.ceil(height / spacing)))).astype(int)
    for n1, n2 in {(int(i), int(j)) for i, j in zip(along, across, strict=True)}:
        if centres:
            t, s = np.meshgrid(
                (np.arange(n1) + 0.5) / n1, (np.arange(n2) + 0.5) / n2, indexing="ij"
            )
        else:
            t, s = np.meshgrid(np.arange(n1 + 1) / n1, np.arange(n2 + 1) / n2, indexing="ij")
        t, s = t.reshape(-1), s.reshape(-1)
        weights = (1 - s) / (1 - s).sum() if centres else np.full(len(s), 1.0 / len(s))
        members = np.flatnonzero((along == n1) & (across == n2))
        batch = max(1, int(2_000_000 // len(t)))
        for first in range(0, len(members), batch):
            chosen = members[first : first + batch]
            # p = a + t (1 - s) (b - a) + s (c - a): rows shrink towards the apex c.
            points = (
                a[chosen, None]
                + (t * (1 - s))[None, :, None] * (b - a)[chosen, None]
                + s[None, :, None] * (c - a)[chosen, None]
            )
            yield points, chosen, weights


def sample_count(corners: np.ndarray, spacing: float) -> float:
    sides = np.stack(
        [np.linalg.norm(corners[:, (k + 1) % 3] - corners[:, k], axis=1) for k in range(3)], axis=1
    )
    base = sides.max(axis=1)
    height = np.linalg.norm(
        np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0]), axis=1
    ) / np.where(base > 0, base, 1.0)
    return float(
        ((2 * np.maximum(1, base / spacing) + 1) * (2 * np.maximum(1, height / spacing) + 1)).sum()
    )


def flood(
    positions: np.ndarray, triangles: np.ndarray, low: np.ndarray, extent: np.ndarray, voxel: float
) -> float:
    """The volume a flood from outside cannot reach when the surface is voxelised at `voxel` metres."""
    dims = np.ceil(extent / voxel).astype(int) + 4
    origin = low - 2 * voxel
    surface = np.zeros(int(np.prod(dims)), bool)
    for points, _, _ in samples(positions[triangles], 0.4 * voxel):
        index = np.clip(
            np.floor((points.reshape(-1, 3) - origin) / voxel).astype(np.int64), 0, dims - 1
        )
        surface[np.unique(np.ravel_multi_index(index.T, dims))] = True
    surface = surface.reshape(dims)
    labels, _ = ndimage.label(~surface)
    inside = (~surface) & (labels != labels[0, 0, 0])
    return float(inside.sum() * voxel**3)


def enclosure(positions: np.ndarray, triangles: np.ndarray, voxel: float | None = None) -> dict:
    """Floods the voxelised surface at the object's field resolution and at two and four times coarser.

    `sealed_voxel_m` is the finest of the three at which the surface keeps the flood out of most of what the others
    enclose; `state` is `sealed` (at the field's own resolution), `leaks` (only a coarser field has an inside: gaps
    wider than a voxel) or `open` (no inside worth 2 % of the bounding box at any of them: a sheet, or a shell
    missing a side)."""
    if len(triangles) == 0:
        return {"state": "empty"}
    low, high = positions.min(axis=0), positions.max(axis=0)
    extent = high - low
    voxel = voxel or voxel_size(extent)
    corners = positions[triangles]
    while (
        np.prod(np.ceil(extent / voxel) + 4) > MAX_CELLS
        or sample_count(corners, 0.4 * voxel) > MAX_SAMPLES
    ):
        voxel *= 1.26
    sizes = [voxel, 2 * voxel, 4 * voxel]
    inside = [flood(positions, triangles, low, extent, size) for size in sizes]
    most = max(inside)
    if most < 0.02 * float(np.prod(np.maximum(extent, voxel))):
        most = 0.0  # pockets between pieces are not an inside
    sealed = next(
        (
            size
            for size, volume in zip(sizes, inside, strict=True)
            if most > 0 and volume >= 0.5 * most
        ),
        None,
    )
    state = "open" if sealed is None else "sealed" if sealed == voxel else "leaks"
    return {
        "voxel_m": round(voxel, 4),
        "inside_m3": [round(v, 3) for v in inside],
        "sealed_voxel_m": None if sealed is None else round(sealed, 4),
        "state": state,
    }


def plan_layers(positions: np.ndarray, triangles: np.ndarray, cell: float = 0.25) -> dict:
    """For a ground cell (F3, F4): how much of the surface faces sideways or down, and how much of the plan is
    covered by more than one upward-facing layer."""
    corners = positions[triangles]
    cross = np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0])
    area = np.linalg.norm(cross, axis=1) / 2
    up = cross[:, 1] / np.where(area > 0, 2 * area, 1.0)
    plan_area = np.abs(cross[:, 1]) / 2
    low, high = positions.min(axis=0), positions.max(axis=0)
    dims = np.maximum(1, np.ceil((high[[0, 2]] - low[[0, 2]]) / cell).astype(int) + 1)
    cover = np.zeros(int(np.prod(dims)))
    facing_up = np.flatnonzero(up > 0.05)
    for points, chosen, weights in samples(corners[facing_up], cell / 3, centres=True):
        share = (plan_area[facing_up][chosen][:, None] * weights[None, :]).reshape(-1)
        index = np.clip(
            np.floor((points.reshape(-1, 3)[:, [0, 2]] - low[[0, 2]]) / cell).astype(np.int64),
            0,
            dims - 1,
        )
        cover += np.bincount(
            np.ravel_multi_index(index.T, dims), weights=share, minlength=len(cover)
        )
    covered = cover > 0.25 * cell * cell
    total = float(area.sum()) or 1.0
    return {
        "stacked_share": float((cover[covered] > 1.6 * cell * cell).mean())
        if covered.any()
        else 0.0,
        "steep_share": float(area[np.abs(up) <= 0.05].sum() / total),
        "underside_share": float(area[up < -0.05].sum() / total),
        "plan_m2": float(covered.sum() * cell * cell),
    }
