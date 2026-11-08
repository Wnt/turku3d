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
MIN_SPREAD = 0.1  # crown radius per metre of height of a lidar-found tree (a 16 m column 1 m across is a mast)


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

    This input belongs to vegetation even when terrain no longer uses aerial imagery. The crop reaches the sky bake's
    horizon beyond the tile (visibility.FAR), so ground on both sides of a tile border sees the same crowns; tree
    placement uses only the tile's own returns.
    """
    from turku3d.vegetation.trees import reduced_points

    from .visibility import FAR

    parts = []
    bounds = tile.bbox.buffered(FAR + 2)
    for path in paths:
        _, canopy = reduced_points(path)
        keep = ((canopy[:, 0] >= bounds.xmin) & (canopy[:, 0] < bounds.xmax)
                & (canopy[:, 2] >= bounds.zmin) & (canopy[:, 2] < bounds.zmax))
        parts.append(canopy[keep])
    return np.concatenate(parts) if parts else np.empty((0, 3))


LEAF_RADIUS = 2.5  # m around a trunk whose returns tell needles from leaves and a spruce from a pine
# A spruce's crown reaches the ground, a pine's stands high: within LEAF_RADIUS the median of the cells' lowest tenth
# of class 3-5 returns lies below this share of the crown's height for a spruce (652 register conifers in six tiles,
# 82.5 % right against 63 % for calling every one a pine; 72 % on the three tiles not used to pick it).
SPRUCE_LOW = 0.3
# The species names (register vocabulary) a measured conifer gets: trees.variant and the client map them to pine or
# spruce prototypes.
CONIFERS = {"pine": "mänty", "spruce": "kuusi"}


def leaf_points(tile, paths):
    """The leaf cells (``trees.leaf_cells``: x, z, pulses, mean intensity, low and top height) of the tile and
    LEAF_RADIUS around it."""
    from turku3d.vegetation.trees import leaf_cells

    bounds = tile.bbox.buffered(LEAF_RADIUS + 2)
    parts = []
    for path in paths:
        cells = leaf_cells(path)
        keep = ((cells[:, 0] >= bounds.xmin) & (cells[:, 0] < bounds.xmax)
                & (cells[:, 1] >= bounds.zmin) & (cells[:, 1] < bounds.zmax))
        parts.append(cells[keep])
    return np.concatenate(parts) if parts else np.empty((0, 6))


def leaf_type_at(leaves, height_at, threshold=None):
    """``f(x, z)``: "broadleaf", "pine", "spruce", or None without measured foliage within LEAF_RADIUS. A leaf-on scan
    returns far more of the pulse from needles than from broadleaf foliage: over the survey's ``conifer_intensity``
    (city profile) the mean first-return intensity of high vegetation (class 5) around the trunk is a conifer's.
    Without a calibrated survey nothing is classified. A conifer whose crown reaches the ground is a spruce
    (SPRUCE_LOW), measured from the ground ``height_at`` the trunk."""
    if threshold is None:
        from turku3d.cities import current

        threshold = current().conifer_intensity
    leaves = np.asarray(leaves, float).reshape(-1, 6)
    leaves = leaves[leaves[:, 2] > 0]
    if threshold is None or not len(leaves):
        return lambda x, z: None
    index = cKDTree(leaves[:, :2])

    def classify(x, z):
        near = index.query_ball_point([x, z], LEAF_RADIUS)
        if not near:
            return None
        cells = leaves[near]
        if (cells[:, 3] * cells[:, 2]).sum() / cells[:, 2].sum() <= threshold:
            return "broadleaf"
        profile = cells[np.isfinite(cells[:, 4])]
        ground = height_at(x, z)
        height = profile[:, 5].max() - ground if len(profile) else 0.0
        if len(profile) < 2 or height < 4:
            return "pine"
        return "spruce" if (np.median(profile[:, 4]) - ground) / height < SPRUCE_LOW else "pine"

    return classify


def cover(tile, canopy, height_at, cell=2.0, min_height=3.0):
    """Where the lidar measured canopy: class-5 returns above ``min_height`` per ``cell`` metres over the tile, grown by
    one cell. A vectorised ``keep(x, z) -> bool array`` for fills that must stay under measured trees (clearings, rock
    and lawns between crowns stay open); None without measured canopy in the tile."""
    from scipy.ndimage import binary_dilation

    points = np.asarray(canopy)
    if not len(points):
        return None
    inside = ((points[:, 0] >= tile.bbox.xmin) & (points[:, 0] < tile.bbox.xmax)
              & (points[:, 2] >= tile.bbox.zmin) & (points[:, 2] < tile.bbox.zmax))
    points = points[inside]
    if not len(points):
        return None
    many = getattr(height_at, "many", None)
    base = many(points[:, 0], points[:, 2]) if many else np.array([height_at(x, z) for x, _, z in points])
    heights = points[:, 1] - base
    points = points[(heights > min_height) & (heights < 38)]
    size = int(np.ceil(tile.size / cell))
    grid = np.zeros((size, size), dtype=bool)
    ij = np.clip(np.floor((points[:, [0, 2]] - [tile.bbox.xmin, tile.bbox.zmin]) / cell).astype(int), 0, size - 1)
    grid[ij[:, 0], ij[:, 1]] = True
    grid = binary_dilation(grid)
    x0, z0 = tile.bbox.xmin, tile.bbox.zmin

    def keep(x, z):
        i = np.floor((np.asarray(x) - x0) / cell).astype(int)
        j = np.floor((np.asarray(z) - z0) / cell).astype(int)
        ok = (i >= 0) & (i < size) & (j >= 0) & (j < size)
        result = np.zeros(len(i), dtype=bool)
        result[ok] = grid[i[ok], j[ok]]
        return result

    return keep


def inventory(tile, canopy, mapped, height_at, blocked, paths, walked=None, leaf_type=None):
    """Mapped trees plus separated crown maxima. ``walked``: what a real trunk (a register tree or a lidar
    crown) clears instead of ``paths``: carriageways and the walked lines of mapped paths (``forests.produce``);
    the final trunk clearance (``vegetation.clearance``) places it against the drawn paving. ``leaf_type``
    (``leaf_type_at``) gives lidar crowns their measured conifer species."""
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
            if tree.source == "lidar" and leaf_type is not None:
                tree.species = CONIFERS.get(leaf_type(tree.x, tree.z), tree.species)
            tree.variant = variant(tree.species, tree.height)
            if tree.source == "lidar" and tree.crown_radius < MIN_SPREAD * tree.height:
                continue  # a mast, lamp column or monument: thin pulses split on it like on twigs
        # Measurements may enlarge a registered tree after its initial clearance
        # check. Every final visible trunk must still clear the path footprint.
        if (tree.source != "lidar" or tree.measured) and clear(tree):
            result.append(tree)
    return result
