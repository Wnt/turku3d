"""Register-first tree inventory and conservative class-5 crown measurements."""

from __future__ import annotations

import fcntl
import hashlib
from dataclasses import asdict, dataclass

import numpy as np
from scipy.ndimage import maximum_filter
from scipy.spatial import cKDTree

from turku3d import config as C, integer_rows, profiling

SOURCES = ("GIS_Puut", "kanta_Lehtipuu", "kanta_Havupuu", "kanta_Lehtipuurivi")
VARIANTS = ("tree_default_fall", "tree_detailed_fall", "tree_oak_fall", "tree_pineDefaultA")


@dataclass
class Tree:
    object_id: str
    x: float
    z: float
    source: str
    species: str = ""
    y: float = 0.0
    height: float = 10.0
    crown_radius: float = 3.0
    trunk_radius: float = 0.2
    support: int = 0
    measured: bool = False
    variant: str = VARIANTS[0]

    def record(self):
        return asdict(self)


def variant(species, height):
    name = species.lower()
    if any(s in name for s in ("pinus", "picea", "havupuu", "mänty", "kuusi")):
        return VARIANTS[3]
    if any(s in name for s in ("quercus", "tammi", "ulmus", "jalava")):
        return VARIANTS[2]
    return VARIANTS[1] if height >= 13 else VARIANTS[0]


def deduplicate(candidates, distance=2.0):
    """Priority order is register, mapped points, then sampled rows; never move a point."""
    kept, cells = [], {}
    for t in candidates:
        cell = (int(np.floor(t.x / distance)), int(np.floor(t.z / distance)))
        neighbours = [
            q
            for i in range(cell[0] - 1, cell[0] + 2)
            for j in range(cell[1] - 1, cell[1] + 2)
            for q in cells.get((i, j), [])
        ]
        if any(np.hypot(t.x - q.x, t.z - q.z) < distance for q in neighbours):
            continue
        kept.append(t)
        cells.setdefault(cell, []).append(t)
    return kept


def register(bounds, source_area="work-3x3"):
    """Register, mapped and row trees within bounds, from the city layers of source_area."""
    import geopandas as gpd
    from shapely import from_wkb
    from shapely.geometry import box

    candidates = []
    for source in SOURCES:
        path = C.raw_dir("wfs") / source_area / f"{source}.parquet"
        frame = gpd.read_parquet(path)
        for _, row in frame.iterrows():
            geom = row["geometry_local"]
            if isinstance(geom, bytes):
                geom = from_wkb(geom)
            if geom is None or geom.is_empty or not geom.intersects(box(*bounds.as_tuple())):
                continue
            species = str(row.get("Kasvilaji", row.get("suku", source)))
            if source == "kanta_Havupuu":
                species += " havupuu"
            identity = str(row.get("Id", row["feature_key"]))
            if geom.geom_type == "Point":
                points = [geom]
            elif geom.geom_type == "LineString":
                # A row is not an inventory of individual trunks. Require lidar below.
                points = [geom.interpolate(float(s)) for s in np.arange(0, geom.length, 8.0)]
            else:
                continue
            for i, p in enumerate(points):
                if bounds.contains(p.x, p.y):
                    candidates.append(Tree(f"{source}:{identity}:{i}", p.x, p.y, source, species))
    return deduplicate(candidates)


CELL = 0.5  # metres; origin-aligned, so any bounds on the half-metre grid crop whole cells


def laz_paths(bounds):
    return [C.raw_dir("laz") / "pistepilvi" / f"{t.name}.laz" for t in C.laz_tiles_for_bbox(bounds)]


def reduced_points(path):
    """One LAZ file's class-2 ground (first return per 0.5 m cell) and class-5 canopy (highest
    return per 0.5 m cell), cached by the file's content; immutable raw is read once."""
    from turku3d.lidar.decoded import chunks as decoded_chunks

    from turku3d.tile_layer import identities

    identity = identities({"laz": path})["laz"]
    key = hashlib.sha256(repr((identity, C.ORIGIN, CELL, "reduced-v1")).encode()).hexdigest()
    dest = C.cache_dir("vegetation") / (key + ".npz")
    dest.parent.mkdir(parents=True, exist_ok=True)
    # Tiles built in parallel share LAZ files: one process reduces a file, the others wait.
    with open(dest.with_suffix(".lock"), "a") as lock:
        profiling.flock(lock, fcntl.LOCK_EX, "vegetation")
        if not dest.exists():
            ground, canopy = [], []
            print(f"Reading {path.name}", flush=True)
            for p in decoded_chunks(path):
                cls = np.asarray(p.classification)
                x, y, z = C.en_to_local(np.asarray(p.x), np.asarray(p.y), np.asarray(p.z))
                xyz = np.column_stack((x, y, z))
                ground.append(first_per_cell(xyz[cls == 2]))
                canopy.append(highest_per_cell(xyz[cls == 5]))
            ground = first_per_cell(np.concatenate(ground)) if ground else np.empty((0, 3))
            canopy = highest_per_cell(np.concatenate(canopy)) if canopy else np.empty((0, 3))
            temporary = dest.with_suffix(".tmp.npz")
            np.savez_compressed(temporary, ground=ground, canopy=canopy)
            temporary.replace(dest)
    with np.load(dest) as a:
        return a["ground"], a["canopy"]


LEAF_CELL = 2.0  # m


LEAF_BIN = 0.5  # m of height per profile bin


def _summed(keys, values):
    """Unique rows of ``keys`` with the matching rows of ``values`` summed."""
    unique, inverse = integer_rows.unique(keys, return_inverse=True)
    return unique, np.column_stack([np.bincount(inverse, v, len(unique)) for v in values.T])


def leaf_cells(path):
    """One LAZ file's high vegetation per LEAF_CELL m cell, cached by the file's content: rows of cell centre x, z,
    first-return pulses of class 5, their mean intensity, and the 10th and 98th percentile height (engine y, to
    LEAF_BIN) of all class 3-5 returns (nan under three returns). Needles and leaves return the pulse differently,
    and a spruce's crown reaches the ground where a pine's stands high (canopy_trees.leaf_type_at)."""
    from turku3d.lidar.decoded import chunks as decoded_chunks
    from turku3d.tile_layer import identities

    identity = identities({"laz": path})["laz"]
    key = hashlib.sha256(repr((identity, C.ORIGIN, LEAF_CELL, LEAF_BIN, "leaf-v2")).encode()).hexdigest()
    dest = C.cache_dir("vegetation") / (key + ".npz")
    dest.parent.mkdir(parents=True, exist_ok=True)
    with open(dest.with_suffix(".lock"), "a") as lock:
        profiling.flock(lock, fcntl.LOCK_EX, "vegetation")
        if not dest.exists():
            # Reduced per chunk: (cell, height bin) return counts and per-cell first-return sums, never the points.
            bins, leaves = [], []
            for p in decoded_chunks(path):
                cls = np.asarray(p.classification)
                keep = (cls >= 3) & (cls <= 5)
                if not keep.any():
                    continue
                x, y, z = C.en_to_local(np.asarray(p.x)[keep], np.asarray(p.y)[keep], np.asarray(p.z)[keep])
                cell = np.floor(np.column_stack((x / LEAF_CELL, z / LEAF_CELL))).astype(np.int64)
                level = np.floor(y / LEAF_BIN).astype(np.int64)[:, None]
                bins.append(_summed(np.hstack((cell, level)), np.ones((len(x), 1))))
                leaf = (cls[keep] == 5) & (np.asarray(p.return_number)[keep] == 1)
                if leaf.any():
                    intensity = np.asarray(p.intensity)[keep][leaf].astype(float)
                    leaves.append(_summed(cell[leaf], np.column_stack((np.ones(len(intensity)), intensity))))
            rows = np.empty((0, 6))
            if bins:
                keys, counts = _summed(np.concatenate([k for k, _ in bins]), np.concatenate([c for _, c in bins]))
                # np.unique sorts by cell then height bin: cumulative counts give each cell's percentiles.
                start = np.r_[0, np.flatnonzero(np.any(keys[1:, :2] != keys[:-1, :2], axis=1)) + 1]
                cell = np.repeat(np.arange(len(start)), np.diff(np.r_[start, len(keys)]))
                counts = counts[:, 0]
                total = np.bincount(cell, counts, len(start))
                before = np.cumsum(counts) - counts - (np.cumsum(total) - total)[cell]  # returns below each bin

                def percentile(q):
                    reached = before + counts >= q * total[cell]  # bins holding or above the q-th return
                    index = np.full(len(start), len(keys) - 1)
                    np.minimum.at(index, cell[reached], np.flatnonzero(reached))
                    return (keys[index, 2] + .5) * LEAF_BIN

                profile = total >= 3
                low = np.where(profile, percentile(.1), np.nan)
                top = np.where(profile, percentile(.98), np.nan)
                pulses = np.zeros(len(start))
                intensity = np.zeros(len(start))
                if leaves:
                    leaf_keys, sums = _summed(np.concatenate([k for k, _ in leaves]),
                                              np.concatenate([c for _, c in leaves]))
                    # Every leaf cell holds class-5 returns, so it is one of the profile cells (both sorted).
                    at = np.searchsorted(keys[start, 0] * 2**32 + keys[start, 1], leaf_keys[:, 0] * 2**32 + leaf_keys[:, 1])
                    pulses[at] = sums[:, 0]
                    intensity[at] = sums[:, 1] / sums[:, 0]
                rows = np.column_stack(((keys[start, :2] + .5) * LEAF_CELL, pulses, intensity, low, top))
            temporary = dest.with_suffix(".tmp.npz")
            np.savez_compressed(temporary, cells=rows)
            temporary.replace(dest)
    with np.load(dest) as a:
        return a["cells"]


def shrub_cells(path):
    """One LAZ file's medium vegetation (class 4: shrubs, saplings, the low mantle of a forest edge), the highest
    return per CELL m cell, cached by the file's content (canopy_trees.shrubs)."""
    from turku3d.lidar.decoded import chunks as decoded_chunks
    from turku3d.tile_layer import identities

    identity = identities({"laz": path})["laz"]
    key = hashlib.sha256(repr((identity, C.ORIGIN, CELL, "shrub-v1")).encode()).hexdigest()
    dest = C.cache_dir("vegetation") / (key + ".npz")
    dest.parent.mkdir(parents=True, exist_ok=True)
    with open(dest.with_suffix(".lock"), "a") as lock:
        profiling.flock(lock, fcntl.LOCK_EX, "vegetation")
        if not dest.exists():
            parts = []
            for p in decoded_chunks(path):
                keep = np.asarray(p.classification) == 4
                if keep.any():
                    x, y, z = C.en_to_local(np.asarray(p.x)[keep], np.asarray(p.y)[keep], np.asarray(p.z)[keep])
                    parts.append(highest_per_cell(np.column_stack((x, y, z))))
            points = highest_per_cell(np.concatenate(parts)) if parts else np.empty((0, 3))
            temporary = dest.with_suffix(".tmp.npz")
            np.savez_compressed(temporary, points=points)
            temporary.replace(dest)
    with np.load(dest) as a:
        return a["points"]


def _cells(points):
    return np.floor(points[:, [0, 2]] / CELL).astype(np.int64)


def first_per_cell(points):
    if not len(points):
        return points.reshape(0, 3)
    _, first = integer_rows.unique(_cells(points), return_index=True)
    return points[np.sort(first)]


def highest_per_cell(points):
    if not len(points):
        return points.reshape(0, 3)
    return points[integer_rows.highest(_cells(points), points[:, 1])]


def load_points(bounds):
    """Class-2 ground and class-5 canopy cells within bounds (on the half-metre grid)."""
    grounds, canopies = [], []
    for path in laz_paths(bounds):
        ground, canopy = reduced_points(path)
        for points, target in ((ground, grounds), (canopy, canopies)):
            inside = (
                (points[:, 0] >= bounds.xmin)
                & (points[:, 0] < bounds.xmax)
                & (points[:, 2] >= bounds.zmin)
                & (points[:, 2] < bounds.zmax)
            )
            target.append(points[inside])
    ground = np.concatenate(grounds) if grounds else np.empty((0, 3))
    canopy = np.concatenate(canopies) if canopies else np.empty((0, 3))
    if not len(ground):
        raise ValueError("No class-2 ground observations in vegetation AOI")
    return ground, canopy


def measure(trees, ground, canopy, bounds):
    """Assign canopy returns to nearest trunk; add separated, supported CHM maxima."""
    # CHM cells represent crown area, avoiding millions of repeated vertical returns.
    if len(canopy):
        shape = (
            int(np.ceil((bounds.xmax - bounds.xmin) * 2)),
            int(np.ceil((bounds.zmax - bounds.zmin) * 2)),
        )
        ij = np.floor((canopy[:, [0, 2]] - [bounds.xmin, bounds.zmin]) * 2).astype(int)
        top = np.full(shape, -np.inf)
        np.maximum.at(top, (ij[:, 0], ij[:, 1]), canopy[:, 1])
        x, z = np.nonzero(np.isfinite(top))
        canopy = np.column_stack(
            (bounds.xmin + (x + 0.5) / 2, top[x, z], bounds.zmin + (z + 0.5) / 2)
        )
    ground_index = cKDTree(ground[:, [0, 2]])

    def ground_height(xz):
        d, ix = ground_index.query(xz, k=min(6, len(ground)))
        if np.ndim(ix) == 1:
            return ground[ix, 1], d
        return np.median(ground[ix, 1], axis=1), d[:, 0]

    if len(canopy):
        base, distance = ground_height(canopy[:, [0, 2]])
        canopy = canopy[(canopy[:, 1] - base > 2.5) & (distance < 12)]
    if len(canopy):
        base, _ = ground_height(canopy[:, [0, 2]])
        height = canopy[:, 1] - base
        resolution = 1.0
        shape = (int(np.ceil(bounds.xmax - bounds.xmin)), int(np.ceil(bounds.zmax - bounds.zmin)))
        ij = np.floor((canopy[:, [0, 2]] - [bounds.xmin, bounds.zmin]) / resolution).astype(int)
        chm = np.zeros(shape)
        np.maximum.at(chm, (ij[:, 0], ij[:, 1]), height)
        peaks = np.argwhere((chm == maximum_filter(chm, size=9)) & (chm >= 6))
        canopy_index = cKDTree(canopy[:, [0, 2]])
        for i, j in sorted(peaks.tolist(), key=lambda p: (-chm[tuple(p)], p)):
            x, z = bounds.xmin + i + 0.5, bounds.zmin + j + 0.5
            if any(np.hypot(t.x - x, t.z - z) < max(5, min(9, chm[i, j] * 0.35)) for t in trees):
                continue
            if len(canopy_index.query_ball_point([x, z], 2.5)) >= 20:
                trees.append(Tree(f"lidar:{x:.1f}:{z:.1f}", x, z, "lidar"))
    if not trees:
        return []
    bases, distances = ground_height(np.array([[t.x, t.z] for t in trees]))
    assigned = [[] for _ in trees]
    if len(canopy):
        dist, owner = cKDTree([[t.x, t.z] for t in trees]).query(canopy[:, [0, 2]])
        valid = np.flatnonzero(dist <= 10)
        order = valid[np.argsort(owner[valid], kind="stable")]
        edges = np.searchsorted(owner[order], np.arange(len(trees) + 1))
        for i in range(len(trees)):
            assigned[i] = canopy[order[edges[i] : edges[i + 1]]]
    result = []
    for i, t in enumerate(trees):
        if distances[i] > 12:
            continue  # Do not invent a ground elevation across an unobserved area.
        t.y = float(bases[i])
        points = np.asarray(assigned[i])
        t.support = len(points)
        if len(points) >= 12:
            t.height = float(np.clip(np.quantile(points[:, 1] - t.y, 0.98), 3, 35))
            t.crown_radius = float(
                np.clip(
                    np.quantile(np.linalg.norm(points[:, [0, 2]] - [t.x, t.z], axis=1), 0.9),
                    0.8,
                    10,
                )
            )
            t.measured = True
        elif t.source in ("lidar", "kanta_Lehtipuurivi"):
            continue
        t.trunk_radius = float(np.clip(t.height * 0.018, 0.09, 0.55))
        t.variant = variant(t.species, t.height)
        result.append(t)
    return result
