"""Per-tree crowns of the drawn trees from the classified lidar's canopy cells.

Input: the trees of one tile (trunk position, base height, the drawn height and crown radius) and the class-5 canopy,
reduced to its highest return per 0.5 m cell (as the generator's own canopy input, ``trees.reduced_points``, but
streamed from each LAZ block once into a 3 MB per-block cell cache, with no decoded copy: ``canopy_cells``). Class 12 (the second flight line, unclassified)
adds only about 1 % to the class-5 crown top surface, so it is left out. Each
canopy cell above 2.5 m goes to the tree whose crown it is nearest to relative to that tree's radius, within reach
of it, and a crown keeps the cells connected to its trunk (``canopy_trees.connected_crown``). Supported crowns
(12 cells, 0.35 m relief: the generator's rule) give:

- height: 98th-percentile cell height above the trunk base, 3-35 m;
- crown radius: 90th-percentile cell distance from the trunk, 1-9 m;
- lean: the crown centroid's offset from the trunk beyond a dead zone (0.25 m + 0.1 of the radius: segmentation noise
  and trunks not quite under the crown's middle), within 0.3 of the radius and 2.5 m, as a shear per metre of height
  taken at 0.6 of the height (where the centroid of a broadleaf crown sits), at most 0.08 (4.6 degrees);
- footprint: an area-preserving ellipse from the principal axes of the crown cells about their centroid (crowns
  pruned along a facade, squeezed in a row): the major axis's stretch (the minor axis takes its inverse) and the
  major axis's angle from +x towards +z. Crowns of 40+ cells with an axis ratio under 0.85; the ratio is at least 0.75.

- crown base (with the leaf cells, ``trees.leaf_cells``: per 2 m cell the 10th-percentile height of all class 3-5
  returns): for a crown with open ground around it (BASE_OPEN of a ring BASE_RING beyond its radius has no canopy over
  3 m), the median of those cell heights above the trunk base within BASE_REACH of the radius, on the open side, as a
  fraction of the height. On solitary park trees it is 0.29-0.31, the library models' foliage base (0.27-0.35); on
  stand edges and roadside rows 0.16-0.26 (Kurala, Puolanpuisto, a central park): their crowns reach lower on the
  light side.
  Inside a closed stand, shrubs and saplings under the crowns are measured with them, so no base is given there.

The generator measured surveyed and lidar-found trees the same way, but never OSM-area scatter (random heights) or
unsupported register trees (planting-year heights); here every drawn tree with a supported crown takes its measured
size. Trees without support keep theirs.
"""

from __future__ import annotations

import numpy as np
from scipy.spatial import cKDTree

from turku3d.coarse.canopy_trees import MIN_CROWN_RELIEF, connected_crown

CELL = 0.5
MARGIN = 12.0  # m of canopy beyond the tile: crowns of edge trees overhang it
MIN_CELLS = 12
REACH = (1.5, 2.0)  # a crown cell belongs within max(1.5 r, r + 2 m) of a trunk
LEAN_HEIGHT, LEAN_RADIUS, LEAN_MAX, SHEAR_MAX = 0.6, 0.3, 2.5, 0.08
LEAN_DEAD = (0.25, 0.1)  # m + fraction of the radius
ELLIPSE_CELLS, ELLIPSE_ROUND, ELLIPSE_MIN = 40, 0.85, 0.75  # cells; axis ratios: round enough, flattest (neighbours sharing canopy also squeeze the cells)
BASE_RING, BASE_OPEN, BASE_REACH, BASE_CANOPY = 2.0, 0.2, 0.9, 3.0  # m beyond the radius; ring share; of the radius; m
BASE_RAYS = 16


def crown_base(x, z, base, height, radius, canopy, canopy_index, leaves, leaf_index):
    """The measured crown base of one crown as a fraction of its height (module docstring), or None inside a closed
    stand or without three leaf cells under the crown's open side."""
    angles = np.arange(BASE_RAYS) * (2 * np.pi / BASE_RAYS)
    rays = np.c_[np.cos(angles), np.sin(angles)]
    ring = rays * (radius + BASE_RING) + [x, z]
    covered = np.array([bool(len(near)) and bool((canopy[near, 1] - base > BASE_CANOPY).any())
                        for near in canopy_index.query_ball_point(ring, 0.75)])
    if (~covered).mean() < BASE_OPEN:
        return None
    cells = leaves[leaf_index.query_ball_point([x, z], BASE_REACH * radius)]
    if not len(cells):
        return None
    side = rays[~covered].mean(axis=0)
    if np.hypot(*side) >= 0.3:  # open on one side: the half facing it (a solitary crown is open all round)
        cells = cells[(cells[:, :2] - [x, z]) @ (side / np.hypot(*side)) > -0.5]
    if len(cells) < 3:
        return None
    return round(float(np.clip(np.median(cells[:, 4] - base) / height, 0, 1)), 3)


def measure(trees, canopy, leaves=None):
    """``trees``: (n, 5) x, z, base y, height, crown radius. ``canopy``: (m, 3) x, y, z cells. ``leaves``: the leaf
    cells (``trees.leaf_cells`` rows: x, z, pulses, intensity, 10th and 98th percentile height) in the same frame.

    Returns per tree ``[height, radius, shear_x, shear_z, stretch, angle]`` (rounded to cm and 1e-3), with ``leaves``
    and a seventh value, the crown base fraction (``crown_base``, or None), or None without support. A round crown
    has stretch 1 and angle 0."""
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
    if leaves is not None:
        canopy_index = cKDTree(canopy[:, [0, 2]])
        leaves = np.asarray(leaves, dtype=float).reshape(-1, 6)
        leaves = leaves[np.isfinite(leaves[:, 4])]
        leaf_index = cKDTree(leaves[:, :2]) if len(leaves) else None
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
        stretch, angle = 1.0, 0.0
        if len(crown) >= ELLIPSE_CELLS:
            values, vectors = np.linalg.eigh(np.cov((offsets - offsets.mean(axis=0)).T))
            ratio = float(np.sqrt(max(values[0], 0.0) / values[1])) if values[1] > 0 else 1.0
            if ratio < ELLIPSE_ROUND:
                stretch = 1 / np.sqrt(max(ratio, ELLIPSE_MIN))
                angle = float(np.arctan2(vectors[1, 1], vectors[0, 1]) % np.pi)
        result[i] = [round(height, 2), round(r, 2), round(float(shear[0]), 3), round(float(shear[1]), 3),
                     round(float(stretch), 3), round(angle, 3)]
        if leaves is not None:
            result[i].append(crown_base(x, z, base, height, r, canopy, canopy_index, leaves, leaf_index)
                             if leaf_index is not None else None)
    return result


def block_canopy(path):
    """One LAZ block's class-5 returns, highest per 0.5 m cell, as exact LAZ integers (X, Y, Z) and the header's
    scales and offsets. Streamed once (xyz and classification only) and cached by the block's content: about 3 MB,
    where a decoded copy of the block is 270-410 MB. Tiles share blocks (4-8 tiles read each with the margin)."""
    import fcntl
    import hashlib
    import json
    import os
    from pathlib import Path

    import laspy

    from turku3d import config as C, profiling
    from turku3d.tile_layer import identities

    identity = identities({"laz": Path(path)})["laz"]
    if identity is None:
        raise FileNotFoundError(path)
    recipe = hashlib.sha256(repr((identity, C.ORIGIN, CELL, "crown-canopy-v1")).encode()).hexdigest()
    shared = os.environ.get("TURKU3D_SHARED_CACHE")
    root = Path(shared) / "raw-derived" / "crown-canopy" if shared else C.cache_dir("crown-canopy")
    root.mkdir(parents=True, exist_ok=True)
    dest = root / f"{recipe}.npz"
    with dest.with_suffix(".lock").open("a") as lock:
        profiling.flock(lock, fcntl.LOCK_EX, "crown-canopy")
        if not dest.exists():
            selection = laspy.DecompressionSelection.base().decompress_z().decompress_classification()
            parts = []
            with laspy.open(path, decompression_selection=selection) as reader:
                header = reader.header
                for chunk in reader.chunk_iterator(2_000_000):
                    keep = np.asarray(chunk.classification) == 5
                    if keep.any():
                        parts.append(_highest(np.c_[np.asarray(chunk.X)[keep], np.asarray(chunk.Y)[keep],
                                                    np.asarray(chunk.Z)[keep]].astype(np.int64), header))
            cells = _highest(np.concatenate(parts), header) if parts else np.empty((0, 3), np.int64)
            temporary = dest.with_suffix(".tmp.npz")
            np.savez_compressed(temporary, xyz=cells.astype(np.int32), meta=np.frombuffer(json.dumps(
                dict(scales=[float(v) for v in header.scales], offsets=[float(v) for v in header.offsets])).encode(), np.uint8))
            temporary.replace(dest)
        with np.load(dest) as data:
            xyz, meta = data["xyz"].astype(np.int64), json.loads(data["meta"].tobytes())
    (sx, sy, sz), (ox, oy, oz) = meta["scales"], meta["offsets"]
    x = xyz[:, 0] * sx + ox - C.ORIGIN_E
    z = C.ORIGIN_N - (xyz[:, 1] * sy + oy)
    y = xyz[:, 2] * sz + oz - C.ORIGIN_H
    return np.c_[x, y, z]


def _highest(xyz, header):
    """Rows of LAZ integers, the highest per 0.5 m engine cell (ties: the first in file order)."""
    from turku3d import config as C

    (sx, sy, _), (ox, oy, _) = header.scales, header.offsets
    i = np.floor((xyz[:, 0] * sx + ox - C.ORIGIN_E) / CELL).astype(np.int64)
    j = np.floor((C.ORIGIN_N - (xyz[:, 1] * sy + oy)) / CELL).astype(np.int64)
    order = np.lexsort((np.arange(len(xyz)), -xyz[:, 2], j, i))
    first = np.ones(len(order), bool)
    first[1:] = (i[order][1:] != i[order][:-1]) | (j[order][1:] != j[order][:-1])
    return xyz[np.sort(order[first])]


def leaf_cells(tile, margin=MARGIN):
    """The leaf cells (``trees.leaf_cells``) around ``tile`` (engine frame) from its ``pistepilvi`` LAZ blocks; None
    unless every block is on disk."""
    from turku3d import config as C
    from turku3d.vegetation.trees import leaf_cells as block_leaves

    bbox = tile.bbox.buffered(margin)
    paths = []
    for block in C.laz_tiles_for_bbox(bbox):
        path = C.raw_dir("laz") / "pistepilvi" / f"{block.name}.laz"
        if not path.is_file():
            return None
        paths.append(path)
    parts = []
    for path in sorted(paths):
        cells = block_leaves(path)
        keep = ((cells[:, 0] >= bbox.xmin) & (cells[:, 0] < bbox.xmax)
                & (cells[:, 1] >= bbox.zmin) & (cells[:, 1] < bbox.zmax))
        parts.append(cells[keep])
    return np.concatenate(parts) if parts else np.empty((0, 6))


def canopy_cells(tile, margin=MARGIN):
    """Class-5 returns around ``tile`` (engine frame), highest per 0.5 m cell, from the tile's ``pistepilvi`` LAZ blocks
    (``block_canopy``); None unless every block is on disk."""
    from turku3d import config as C

    bbox = tile.bbox.buffered(margin)
    paths = []
    for block in C.laz_tiles_for_bbox(bbox):
        path = C.raw_dir("laz") / "pistepilvi" / f"{block.name}.laz"
        if not path.is_file():
            return None
        paths.append(path)
    parts = []
    for path in sorted(paths):
        cells = block_canopy(path)
        keep = ((cells[:, 0] >= bbox.xmin) & (cells[:, 0] < bbox.xmax)
                & (cells[:, 2] >= bbox.zmin) & (cells[:, 2] < bbox.zmax))
        parts.append(cells[keep])
    return np.concatenate(parts) if parts else np.empty((0, 3))
