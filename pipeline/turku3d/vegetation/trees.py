"""Register-first tree inventory and conservative class-5 crown measurements."""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
from scipy.ndimage import maximum_filter
from scipy.spatial import cKDTree

from turku3d import config as C

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


def register(bounds):
    import geopandas as gpd
    from shapely import from_wkb
    from shapely.geometry import box

    candidates, inputs = [], {}
    for source in SOURCES:
        path = C.raw_dir("wfs") / "work-3x3" / f"{source}.parquet"
        inputs[str(path)] = digest(path)
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
    return deduplicate(candidates), inputs


def digest(path: Path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(4 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def load_points(bounds):
    """Stream immutable LAZ once; cache only cropped class 2/5 xyz, keyed by content."""
    import laspy

    paths = [
        C.raw_dir("laz") / "pistepilvi" / (t.name + ".laz") for t in C.laz_tiles_for_bbox(bounds)
    ]
    inputs = {str(p): digest(p) for p in paths}
    key = hashlib.sha256(repr((inputs, bounds, C.ORIGIN, "xyz25-v1")).encode()).hexdigest()
    dest = C.cache_dir("vegetation") / (key + ".npz")
    if dest.exists():
        with np.load(dest) as a:
            return a["ground"], a["canopy"], inputs
    parts = {2: [], 5: []}
    for path in paths:
        print(f"Reading {path.name}", flush=True)
        with laspy.open(path) as f:
            for p in f.chunk_iterator(1_000_000):
                x, y, z = C.en_to_local(np.asarray(p.x), np.asarray(p.y), np.asarray(p.z))
                inside = (
                    (x >= bounds.xmin) & (x < bounds.xmax) & (z >= bounds.zmin) & (z < bounds.zmax)
                )
                for cls in parts:
                    keep = inside & (np.asarray(p.classification) == cls)
                    if keep.any():
                        parts[cls].append(np.column_stack((x[keep], y[keep], z[keep])))
    ground, canopy = [np.concatenate(parts[c]) if parts[c] else np.empty((0, 3)) for c in (2, 5)]
    if not len(ground):
        raise ValueError("No class-2 ground observations in vegetation AOI")
    # One observed ground point per 0.5 m cell bounds memory and query cost.
    _, unique = np.unique(
        np.floor(ground[:, [0, 2]] / 0.5).astype(np.int32), axis=0, return_index=True
    )
    ground = ground[unique]
    dest.parent.mkdir(parents=True, exist_ok=True)
    temporary = dest.with_suffix(".tmp.npz")
    np.savez_compressed(temporary, ground=ground, canopy=canopy)
    temporary.replace(dest)
    return ground, canopy, inputs


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
