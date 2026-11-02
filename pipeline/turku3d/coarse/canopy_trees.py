"""City-wide crown inventory from cached classified lidar.

Surveyed trunks keep their positions and species. Crown maxima fill the unmapped yards,
verges and parks; crown dimensions come from nearby returns, never planting-year guesses.
"""
from dataclasses import replace

import numpy as np
import shapely
from scipy.ndimage import gaussian_filter, maximum_filter
from scipy.spatial import cKDTree

from turku3d.vegetation.trees import Tree, variant

CELL = 0.5
PEAK_DISTANCE = 3.5


def canopy_points(tile, paths):
    """Read classified crowns once per source hash, then crop per tile.

    This input belongs to vegetation even when terrain no longer uses aerial imagery.
    """
    from turku3d.vegetation.trees import reduced_points

    parts = []
    bounds = tile.bbox.buffered(2)
    for path in paths:
        _, canopy = reduced_points(path)
        keep = ((canopy[:, 0] >= bounds.xmin) & (canopy[:, 0] < bounds.xmax)
                & (canopy[:, 2] >= bounds.zmin) & (canopy[:, 2] < bounds.zmax))
        parts.append(canopy[keep])
    return np.concatenate(parts) if parts else np.empty((0, 3))


def inventory(tile, canopy, mapped, height_at, blocked, paths):
    from .forests import clears_path

    if len(canopy) == 0:
        return mapped
    points = np.asarray(canopy)
    inside = ((points[:, 0] >= tile.bbox.xmin) & (points[:, 0] < tile.bbox.xmax)
              & (points[:, 2] >= tile.bbox.zmin) & (points[:, 2] < tile.bbox.zmax))
    points = points[inside]
    if not len(points):
        return mapped
    base = np.array([height_at(x, z) for x, _, z in points])
    heights = points[:, 1] - base
    valid = (heights > 3) & (heights < 38)
    points, heights = points[valid], heights[valid]
    if not len(points):
        return mapped
    origin = np.array([tile.bbox.xmin, tile.bbox.zmin])
    ij = np.floor((points[:, [0, 2]] - origin) / CELL).astype(int)
    size = int(np.ceil(tile.size / CELL))
    chm = np.zeros((size, size))
    np.maximum.at(chm, (ij[:, 0], ij[:, 1]), heights)
    smooth = gaussian_filter(chm, 1)
    maxima = np.argwhere((smooth == maximum_filter(smooth, size=7)) & (smooth > 3))
    index = cKDTree(points[:, [0, 2]])
    trees = [replace(t) for t in mapped]
    for i, j in sorted(maxima.tolist(), key=lambda p: (-smooth[tuple(p)], p)):
        x, z = origin + (np.array([i, j]) + 0.5) * CELL
        if any(np.hypot(t.x - x, t.z - z) < PEAK_DISTANCE for t in trees):
            continue
        if blocked.covers(shapely.Point(x, z)) or len(index.query_ball_point([x, z], 2)) < 12:
            continue
        tree = Tree(f"lidar:{x:.2f}:{z:.2f}", float(x), float(z), "lidar",
                    y=height_at(x, z), height=float(chm[i, j]), crown_radius=2)
        tree.variant = variant("", tree.height)
        if clears_path(tree, paths):
            trees.append(tree)
    if not trees:
        return trees
    # Assign each crown cell to its closest trunk, preventing neighbouring crowns from
    # inflating one another. The 98th percentile excludes a stray high return.
    distance, owner = cKDTree([[t.x, t.z] for t in trees]).query(points[:, [0, 2]])
    result = []
    for i, tree in enumerate(trees):
        crown = points[(owner == i) & (distance < 9)]
        if len(crown) >= 12:
            tree.y = height_at(tree.x, tree.z)
            tree.height = float(np.clip(np.quantile(crown[:, 1] - tree.y, 0.98), 3, 35))
            tree.crown_radius = float(np.clip(np.quantile(np.linalg.norm(crown[:, [0, 2]] - [tree.x, tree.z], axis=1), 0.9), 1, 9))
            tree.support = len(crown)
            tree.measured = True
            tree.trunk_radius = float(np.clip(tree.height * 0.018, 0.09, 0.55))
            tree.variant = variant(tree.species, tree.height)
        # Measurements may enlarge a registered tree after its initial clearance
        # check. Every final visible trunk must still clear the path footprint.
        if (tree.source != "lidar" or tree.measured) and clears_path(tree, paths):
            result.append(tree)
    return result
