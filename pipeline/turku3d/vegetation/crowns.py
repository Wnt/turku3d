"""Per-tree crowns of the drawn trees from the classified lidar's canopy cells.

Input: the trees of one tile (trunk position, base height, the drawn height and crown radius) and the class-5 canopy,
reduced to its highest return per 0.5 m cell (``trees.reduced_points``, the generator's own canopy input). Each
canopy cell above 2.5 m goes to the tree whose crown it is nearest to relative to that tree's radius, within reach
of it, and a crown keeps the cells connected to its trunk (``canopy_trees.connected_crown``). Supported crowns
(12 cells, 0.35 m relief: the generator's rule) give:

- height: 98th-percentile cell height above the trunk base, 3-35 m;
- crown radius: 90th-percentile cell distance from the trunk, 1-9 m;
- lean: the crown centroid's offset from the trunk beyond a dead zone (0.25 m + 0.1 of the radius: segmentation noise
  and trunks not quite under the crown's middle), within 0.3 of the radius and 2.5 m, as a shear per metre of height
  taken at 0.6 of the height (where the centroid of a broadleaf crown sits), at most 0.08 (4.6 degrees).

The generator measured surveyed and lidar-found trees the same way, but never OSM-area scatter (random heights) or
unsupported register trees (planting-year heights); here every drawn tree with a supported crown takes its measured
size. Trees without support keep theirs.
"""

from __future__ import annotations

import numpy as np
from scipy.spatial import cKDTree

from turku3d.coarse.canopy_trees import MIN_CROWN_RELIEF, connected_crown

CELL = 0.5
MIN_CELLS = 12
REACH = (1.5, 2.0)  # a crown cell belongs within max(1.5 r, r + 2 m) of a trunk
LEAN_HEIGHT, LEAN_RADIUS, LEAN_MAX, SHEAR_MAX = 0.6, 0.3, 2.5, 0.08
LEAN_DEAD = (0.25, 0.1)  # m + fraction of the radius


def measure(trees, canopy):
    """``trees``: (n, 5) x, z, base y, height, crown radius. ``canopy``: (m, 3) x, y, z cells.

    Returns per tree ``[height, radius, shear_x, shear_z]`` (rounded to cm and 1e-3), or None without support."""
    trees = np.asarray(trees, dtype=float).reshape(-1, 5)
    canopy = np.asarray(canopy, dtype=float).reshape(-1, 3)
    result = [None] * len(trees)
    if not len(trees) or not len(canopy):
        return result
    xz = trees[:, [0, 1]]
    radius = np.clip(trees[:, 4], 1.0, 9.0)
    reach = np.maximum(REACH[0] * radius, radius + REACH[1])
    k = min(8, len(trees))
    distance, nearest = cKDTree(xz).query(canopy[:, [0, 2]], k=k)
    distance, nearest = distance.reshape(len(canopy), k), nearest.reshape(len(canopy), k)
    above = canopy[:, 1][:, None] - trees[nearest, 2]
    # Relative distance chooses the owner: a broad crown claims the cells between it and a slender neighbour.
    relative = np.where((distance <= reach[nearest]) & (above > 2.5), distance / radius[nearest], np.inf)
    pick = np.argmin(relative, axis=1)
    owner = np.where(np.isfinite(relative[np.arange(len(canopy)), pick]), nearest[np.arange(len(canopy)), pick], -1)
    order = np.argsort(owner, kind="stable")
    edges = np.searchsorted(owner[order], np.arange(len(trees) + 1))
    for i in range(len(trees)):
        cells = canopy[order[edges[i]:edges[i + 1]]]
        if len(cells) < MIN_CELLS:
            continue
        x, z, base = trees[i, :3]
        crown = connected_crown(cells, x, z)
        if len(crown) < MIN_CELLS or np.ptp(crown[:, 1]) < MIN_CROWN_RELIEF:
            continue
        height = float(np.clip(np.quantile(crown[:, 1] - base, 0.98), 3, 35))
        offsets = crown[:, [0, 2]] - [x, z]
        r = float(np.clip(np.quantile(np.linalg.norm(offsets, axis=1), 0.9), 1, 9))
        lean = offsets.mean(axis=0)
        size = float(np.hypot(*lean))
        effective = min(max(0.0, size - LEAN_DEAD[0] - LEAN_DEAD[1] * r), LEAN_RADIUS * r, LEAN_MAX)
        shear = lean * (effective / size / (LEAN_HEIGHT * height)) if size > 0 else lean * 0
        slope = float(np.hypot(*shear))
        if slope > SHEAR_MAX:
            shear *= SHEAR_MAX / slope
        result[i] = [round(height, 2), round(r, 2), round(float(shear[0]), 3), round(float(shear[1]), 3)]
    return result
