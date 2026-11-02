"""City-wide crown inventory from cached classified lidar.

Surveyed trunks keep their positions and species. Crown maxima fill the unmapped yards,
verges and parks; crown dimensions come from nearby returns, never planting-year guesses.
"""
from dataclasses import replace

import numpy as np
import shapely
from scipy.ndimage import gaussian_filter, maximum_filter
from scipy.spatial import cKDTree

from turku3d.vegetation.trees import VARIANTS, Tree, variant

CELL = 0.5
PEAK_DISTANCE = 3.5
ASSIGN_RADIUS = 9.0
CONTEXT_HALO = 20.0


def canopy_points(tile, paths):
    """Read classified crowns once per source hash, then crop per tile.

    This input belongs to vegetation even when terrain no longer uses aerial imagery.
    """
    from turku3d.vegetation.trees import reduced_points

    crowns, terrain = [], []
    bounds = tile.bbox.buffered(CONTEXT_HALO)
    for path in paths:
        ground, canopy = reduced_points(path)
        for source, parts in [(ground, terrain), (canopy, crowns)]:
            keep = ((source[:, 0] >= bounds.xmin) & (source[:, 0] < bounds.xmax)
                    & (source[:, 2] >= bounds.zmin) & (source[:, 2] < bounds.zmax))
            parts.append(source[keep])
    combine = lambda parts: np.concatenate(parts) if parts else np.empty((0, 3))
    return {"canopy": combine(crowns), "ground": combine(terrain)}


def terrain_sampler(ground, fallback):
    """Use classified terrain over the halo instead of clamping to the current tile edge."""
    points = np.asarray(ground)
    index = cKDTree(points[:, [0, 2]]) if len(points) else None
    count = min(4, len(points))

    def sample(x, z):
        xs, zs = np.broadcast_arrays(x, z)
        locations = np.column_stack((xs.ravel(), zs.ravel()))
        output = np.empty(len(locations))
        for start in range(0, len(locations), 32768):
            query = locations[start:start + 32768]
            if index is None:
                output[start:start + len(query)] = [fallback(px, pz) for px, pz in query]
                continue
            distance, nearest = index.query(query, k=count)
            if count == 1:
                distance, nearest = distance[:, None], nearest[:, None]
            weight = 1 / np.maximum(distance, .05) ** 2
            values = (points[nearest, 1] * weight).sum(axis=1) / weight.sum(axis=1)
            for i in np.flatnonzero(distance[:, 0] > CONTEXT_HALO):
                values[i] = fallback(*query[i])
            output[start:start + len(query)] = values
        return float(output[0]) if xs.ndim == 0 else output.reshape(xs.shape)

    return sample


def inventory(tile, canopy, mapped, height_at, blocked, paths, ground=(), context=(), *, eligible=lambda tree: True):
    from .forests import clears_path

    if len(canopy) == 0:
        return mapped
    points = np.asarray(canopy)
    bounds = tile.bbox.buffered(CONTEXT_HALO)
    inside = ((points[:, 0] >= bounds.xmin) & (points[:, 0] < bounds.xmax)
              & (points[:, 2] >= bounds.zmin) & (points[:, 2] < bounds.zmax))
    points = points[inside]
    if not len(points):
        return mapped
    terrain_at = terrain_sampler(ground, height_at)
    base = terrain_at(points[:, 0], points[:, 2])
    heights = points[:, 1] - base
    valid = (heights > 3) & (heights < 38)
    points, heights = points[valid], heights[valid]
    if not len(points):
        return mapped
    origin = np.floor(np.array([bounds.xmin, bounds.zmin]) / CELL) * CELL
    ij = np.floor((points[:, [0, 2]] - origin) / CELL).astype(int)
    size = int(np.ceil((tile.size + 2 * CONTEXT_HALO) / CELL))
    chm = np.zeros((size, size))
    np.maximum.at(chm, (ij[:, 0], ij[:, 1]), heights)
    smooth = gaussian_filter(chm, 1)
    maxima = np.argwhere((smooth == maximum_filter(smooth, size=7)) & (smooth > 3))
    index = cKDTree(points[:, [0, 2]])
    trees = [replace(t) for t in mapped if eligible(t)]
    # Neighbouring surveyed trunks compete for returns before ownership is cropped.
    # Otherwise the edge tree inherits its neighbour's half-crown or a duplicate peak.
    for identity, x, z, height, conifer, *species in context:
        if tile.bbox.contains(x, z) or not bounds.contains(x, z):
            continue
        name = species[0] if species else "havupuu" if conifer else ""
        tree = Tree(identity, x, z, "register" if identity.startswith("puut:") else "osm",
                    species=name, y=terrain_at(x, z), height=height,
                    crown_radius=float(np.clip(height * .3, 1.5, 7.0)))
        tree.trunk_radius = float(np.clip(height * .018, .09, .55))
        tree.variant = VARIANTS[3] if conifer else variant(name, height)
        if eligible(tree):
            trees.append(tree)
    trees.sort(key=lambda tree: tree.object_id)
    for i, j in sorted(maxima.tolist(), key=lambda p: (-smooth[tuple(p)], p)):
        x, z = origin + (np.array([i, j]) + 0.5) * CELL
        if any(np.hypot(t.x - x, t.z - z) < PEAK_DISTANCE for t in trees):
            continue
        if blocked.covers(shapely.Point(x, z)) or len(index.query_ball_point([x, z], 2)) < 12:
            continue
        tree = Tree(f"lidar:{x:.2f}:{z:.2f}", float(x), float(z), "lidar",
                    y=terrain_at(x, z), height=float(chm[i, j]), crown_radius=2)
        tree.variant = variant("", tree.height)
        if clears_path(tree, paths):
            trees.append(tree)
    if not trees:
        return trees
    # Assign each crown cell to its closest trunk, preventing neighbouring crowns from
    # inflating one another. The 98th percentile excludes a stray high return.
    distance, owner = cKDTree([[t.x, t.z] for t in trees]).query(points[:, [0, 2]])
    order = np.argsort(owner, kind="stable")
    offsets = np.r_[0, np.cumsum(np.bincount(owner, minlength=len(trees)))]
    result = []
    for i, tree in enumerate(trees):
        if not tile.bbox.contains(tree.x, tree.z):
            continue
        owned = order[offsets[i]:offsets[i + 1]]
        crown = points[owned[distance[owned] < ASSIGN_RADIUS]]
        if len(crown) >= 12:
            tree.y = height_at(tree.x, tree.z)
            tree.height = float(np.clip(np.quantile(crown[:, 1] - tree.y, 0.98), 3, 35))
            tree.crown_radius = float(np.clip(np.quantile(np.linalg.norm(crown[:, [0, 2]] - [tree.x, tree.z], axis=1), 0.9), 1, ASSIGN_RADIUS))
            tree.support = len(crown)
            tree.measured = True
            tree.trunk_radius = float(np.clip(tree.height * 0.018, 0.09, 0.55))
            tree.variant = variant(tree.species, tree.height)
        # Measurements may enlarge a registered tree after its initial clearance
        # check. Every final visible trunk must still clear the path footprint.
        if (tree.source != "lidar" or tree.measured) and clears_path(tree, paths):
            result.append(tree)
    return result
