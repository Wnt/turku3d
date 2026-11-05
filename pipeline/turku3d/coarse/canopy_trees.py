"""City-wide crown inventory from cached classified lidar.

Surveyed trunks keep their positions and species. Crown maxima fill the unmapped yards,
verges and parks; crown dimensions come from nearby returns, never planting-year guesses.
"""
from dataclasses import replace

import numpy as np
import shapely
from scipy.ndimage import gaussian_filter, label, maximum_filter
from scipy.spatial import cKDTree

from turku3d.vegetation.trees import Tree, variant

CELL = 0.5
PEAK_DISTANCE = 3.5
MIN_CROWN_RELIEF = 0.35  # m: a flat elevated patch is not evidence of a tree crown


def connected_crown(points, x, z):
    """Use only occupied half-metre cells connected to the return nearest the trunk.

    Nearest-trunk ownership alone joins unrelated facade/awning returns several
    metres apart, making a tiny false peak into a wide, low tree.
    """
    if not len(points):
        return points
    cells = np.floor(points[:, [0, 2]] / CELL).astype(int)
    cells -= cells.min(axis=0)
    occupied = np.zeros(cells.max(axis=0) + 1, dtype=bool)
    occupied[tuple(cells.T)] = True
    components, _ = label(occupied, np.ones((3, 3)))
    nearest = np.argmin(np.linalg.norm(points[:, [0, 2]] - [x, z], axis=1))
    owners = components[tuple(cells.T)]
    return points[owners == owners[nearest]]


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


def inventory(tile, canopy, mapped, height_at, blocked, paths, walked=None):
    """Mapped trees plus separated crown maxima. ``walked``: what a real trunk (a register tree or a lidar
    crown) clears instead of ``paths``: carriageways and the walked lines of mapped paths (``forests.produce``);
    the final trunk clearance (``vegetation.clearance``) places it against the drawn paving."""
    from .forests import clears_path
    from .tree_inputs import REGISTER_TREE

    def clear(tree):
        real = walked is not None and (tree.object_id.startswith(REGISTER_TREE) or tree.source == "lidar")
        return clears_path(tree, walked if real else paths)

    if len(canopy) == 0:
        return mapped
    points = np.asarray(canopy)
    inside = ((points[:, 0] >= tile.bbox.xmin) & (points[:, 0] < tile.bbox.xmax)
              & (points[:, 2] >= tile.bbox.zmin) & (points[:, 2] < tile.bbox.zmax))
    points = points[inside]
    if not len(points):
        return mapped
    many = getattr(height_at, "many", None)  # the same sampler for all points at once
    base = many(points[:, 0], points[:, 2]) if many else np.array([height_at(x, z) for x, _, z in points])
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
        if clear(tree):
            trees.append(tree)
    if not trees:
        return trees
    # Assign each crown cell to its closest trunk, preventing neighbouring crowns from
    # inflating one another. The 98th percentile excludes a stray high return.
    distance, owner = cKDTree([[t.x, t.z] for t in trees]).query(points[:, [0, 2]])
    result = []
    for i, tree in enumerate(trees):
        crown = connected_crown(points[(owner == i) & (distance < 9)], tree.x, tree.z)
        cells = len(np.unique(np.floor(crown[:, [0, 2]] / CELL), axis=0))
        supported = cells >= 12 and np.ptp(crown[:, 1]) >= MIN_CROWN_RELIEF
        if tree.source == "lidar" and not supported:
            continue
        # Surveyed trunks remain valid without a measurable crown. Never replace
        # their dimensions with a flat or disconnected artificial patch either.
        if supported:
            tree.y = height_at(tree.x, tree.z)
            tree.height = float(np.clip(np.quantile(crown[:, 1] - tree.y, 0.98), 3, 35))
            tree.crown_radius = float(np.clip(np.quantile(np.linalg.norm(crown[:, [0, 2]] - [tree.x, tree.z], axis=1), 0.9), 1, 9))
            tree.support = len(crown)
            tree.measured = True
            tree.trunk_radius = float(np.clip(tree.height * 0.018, 0.09, 0.55))
            tree.variant = variant(tree.species, tree.height)
        # Measurements may enlarge a registered tree after its initial clearance
        # check. Every final visible trunk must still clear the path footprint.
        if (tree.source != "lidar" or tree.measured) and clear(tree):
            result.append(tree)
    return result
