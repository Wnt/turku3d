"""Per-tree crowns of the drawn trees from the classified lidar's canopy cells.

Input: the trees of one tile (trunk position, base height, the drawn height and crown radius) and the class-5 canopy,
reduced to its highest return per 0.5 m cell (as the generator's own canopy input, ``trees.reduced_points``, but
reduced once from the shared decoded block into a 3 MB per-block cell cache: ``canopy_cells``). Class 12 (the second flight line, unclassified)
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
- openness (with the gap cells, ``gap_cells``: per 2 m cell the count of ground and of vegetation returns): the ground
  share of all class 2-5 returns within GAP_REACH of the radius, the sky the crown lets through from above. A roadside
  row at Kurala 0.13, the Puolanpuisto grove 0.25 (the stand gap share its sunlit floor was calibrated to against the
  panorama), solitary park trees 0.62.

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
GAP_CELL, GAP_REACH, GAP_MIN_RETURNS = 2.0, 0.6, 20  # m; of the radius; returns under a crown to measure it


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


def openness(x, z, radius, gaps, gap_index):
    """The ground share of the returns under one crown (module docstring), or None under GAP_MIN_RETURNS."""
    cells = gaps[gap_index.query_ball_point([x, z], max(GAP_REACH * radius, GAP_CELL * 0.75))]
    total = cells[:, 2].sum() + cells[:, 3].sum() if len(cells) else 0
    return round(float(cells[:, 2].sum() / total), 3) if total >= GAP_MIN_RETURNS else None


def measure(trees, canopy, leaves=None, gaps=None):
    """``trees``: (n, 5) x, z, base y, height, crown radius. ``canopy``: (m, 3) x, y, z cells. ``leaves``: the leaf
    cells (``trees.leaf_cells`` rows: x, z, pulses, intensity, 10th and 98th percentile height) and ``gaps`` the gap
    cells (``gap_cells`` rows: x, z, ground returns, vegetation returns), in the same frame.

    Returns per tree ``[height, radius, shear_x, shear_z, stretch, angle]`` (rounded to cm and 1e-3), with ``leaves``
    or ``gaps`` and two more values, the crown base fraction (``crown_base``) and the openness (``openness``), each
    None where not measured; or None without support. A round crown has stretch 1 and angle 0."""
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
    extended = leaves is not None or gaps is not None
    leaf_index = gap_index = None
    if leaves is not None:
        canopy_index = cKDTree(canopy[:, [0, 2]])
        leaves = np.asarray(leaves, dtype=float).reshape(-1, 6)
        leaves = leaves[np.isfinite(leaves[:, 4])]
        leaf_index = cKDTree(leaves[:, :2]) if len(leaves) else None
    if gaps is not None:
        gaps = np.asarray(gaps, dtype=float).reshape(-1, 4)
        gap_index = cKDTree(gaps[:, :2]) if len(gaps) else None
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
        if extended:
            result[i].append(crown_base(x, z, base, height, r, canopy, canopy_index, leaves, leaf_index)
                             if leaf_index is not None else None)
            result[i].append(openness(x, z, r, gaps, gap_index) if gap_index is not None else None)
    return result


def block_canopy(path):
    """One LAZ block's class-5 returns, highest per 0.5 m cell, as exact LAZ integers (X, Y, Z) and the header's
    scales and offsets. Reduced from the shared source-order decode and cached by the block's content:
    about 3 MB. Tiles share blocks (4-8 tiles read each with the margin)."""
    import fcntl
    import hashlib
    import json
    import os
    from pathlib import Path

    from turku3d import config as C, profiling
    from turku3d.lidar.decoded import source
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
            header = source(path)
            parts = []
            for start in range(0, len(header), 2_000_000):
                chunk = header.data[start:start + 2_000_000]
                keep = chunk["classification"] == 5
                if keep.any():
                    parts.append(_highest(np.c_[chunk["X"][keep], chunk["Y"][keep],
                                                chunk["Z"][keep]].astype(np.int64), header))
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
    from turku3d import config as C, integer_rows

    (sx, sy, _), (ox, oy, _) = header.scales, header.offsets
    i = np.floor((xyz[:, 0] * sx + ox - C.ORIGIN_E) / CELL).astype(np.int64)
    j = np.floor((C.ORIGIN_N - (xyz[:, 1] * sy + oy)) / CELL).astype(np.int64)
    top = integer_rows.highest(np.c_[i, j], xyz[:, 2])
    return xyz[np.sort(top)]


def block_gaps(path):
    """One LAZ block's ground (class 2) and vegetation (classes 3-5) returns counted per GAP_CELL engine cell: rows of
    cell centre x, z and the two counts. Read from the decoded block (``lidar.decoded``, as the leaf cells are) and
    cached by the block's content; tiles share blocks."""
    import fcntl
    import hashlib

    from turku3d import config as C, profiling
    from turku3d.lidar.decoded import chunks as decoded_chunks
    from turku3d.tile_layer import identities

    identity = identities({"laz": path})["laz"]
    if identity is None:
        raise FileNotFoundError(path)
    key = hashlib.sha256(repr((identity, C.ORIGIN, GAP_CELL, "crown-gaps-v1")).encode()).hexdigest()
    dest = C.cache_dir("vegetation") / (key + ".npz")
    dest.parent.mkdir(parents=True, exist_ok=True)
    with open(dest.with_suffix(".lock"), "a") as lock:
        profiling.flock(lock, fcntl.LOCK_EX, "vegetation")
        if not dest.exists():
            # Counted per chunk by a 1-D cell key (i in the high, j in the low 32 bits), never the points.
            parts = []
            for p in decoded_chunks(path):
                cls = np.asarray(p.classification)
                keep = (cls >= 2) & (cls <= 5)
                if not keep.any():
                    continue
                x, _, z = C.en_to_local(np.asarray(p.x)[keep], np.asarray(p.y)[keep], np.asarray(p.z)[keep])
                i, j = np.floor(x / GAP_CELL).astype(np.int64), np.floor(z / GAP_CELL).astype(np.int64)
                unique, inverse = np.unique((i << 32) + (j & 0xFFFFFFFF), return_inverse=True)
                ground = (cls[keep] == 2).astype(float)
                parts.append((unique, np.bincount(inverse, ground, len(unique)), np.bincount(inverse, None, len(unique))))
            rows = np.empty((0, 4))
            if parts:
                unique, inverse = np.unique(np.concatenate([k for k, _, _ in parts]), return_inverse=True)
                ground = np.bincount(inverse, np.concatenate([g for _, g, _ in parts]), len(unique))
                total = np.bincount(inverse, np.concatenate([n for _, _, n in parts]), len(unique))
                j = unique & 0xFFFFFFFF
                j = np.where(j >= 2**31, j - 2**32, j)
                rows = np.column_stack(((unique >> 32) + 0.5, j + 0.5, ground, total - ground))
                rows[:, :2] *= GAP_CELL
            temporary = dest.with_suffix(".tmp.npz")
            np.savez_compressed(temporary, cells=rows)
            temporary.replace(dest)
        with np.load(dest) as data:
            return data["cells"]


def gap_cells(tile, margin=MARGIN):
    """Ground and vegetation return counts per GAP_CELL cell around ``tile`` (``block_gaps``): rows of cell centre x, z
    (engine frame) and the two counts; None unless every block is on disk."""
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
        cells = block_gaps(path)
        x, z = cells[:, 0], cells[:, 1]
        parts.append(cells[(x >= bbox.xmin) & (x < bbox.xmax) & (z >= bbox.zmin) & (z < bbox.zmax)])
    if not parts:
        return np.empty((0, 4))
    # A cell on a block edge is counted in each block it has returns in: sum them.
    cells = np.concatenate(parts)
    unique, inverse = np.unique(cells[:, :2], axis=0, return_inverse=True)
    inverse = inverse.ravel()
    return np.c_[unique, np.bincount(inverse, cells[:, 2], len(unique)), np.bincount(inverse, cells[:, 3], len(unique))]


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
