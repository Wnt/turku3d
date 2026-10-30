"""Canonical tunnel ground sampling and exact terrain-cut classification."""

from functools import lru_cache

import numpy as np
import shapely
from turku3d import config as C


@lru_cache(maxsize=8)
def _keypoints(path, size, mtime):
    """A bounded cache of complete raw blocks makes cross-tile tunnel profiles identical."""
    import laspy
    from scipy.spatial import cKDTree

    las = laspy.read(path)
    x, h, z = C.en_to_local(np.asarray(las.x), np.asarray(las.y), np.asarray(las.z))
    return cKDTree(np.c_[x, z]), np.asarray(h)


def ground_sampler(fallback):
    """Sample full raw blocks at portals too, rather than clamping to this tile's edge."""

    def sample(x, z):
        e, n = C.ORIGIN_E + x, C.ORIGIN_N - z
        block = C.LazTile.at_en(e, n)
        path = C.raw_dir("laz") / "maanpinta" / f"{block.name}.laz"
        if not path.exists():
            return fallback(x, z)
        st = path.stat()
        tree, h = _keypoints(path, st.st_size, st.st_mtime_ns)
        distances, ids = tree.query([x, z], k=min(4, len(h)))
        weights = 1 / np.maximum(distances, 0.05) ** 2
        return float(np.sum(h[ids] * weights) / np.sum(weights))

    return sample


def ground_cuts(tree, positions, indices, corridor, mouths, floor_height, clearance):
    """Intersect and classify candidate terrain triangles in native batches."""
    candidates = tree.query(corridor, predicate="intersects")
    triangles = positions[indices[candidates]]
    polygons = shapely.polygons(triangles[:, :, [0, 2]])
    overlap = shapely.intersection(polygons, corridor)
    valid = shapely.area(overlap) >= 1e-10
    candidates, triangles, polygons, overlap = (
        a[valid] for a in (candidates, triangles, polygons, overlap)
    )
    if not len(candidates):
        return []
    query, owner = shapely.get_coordinates(overlap, return_index=True)
    matrix = np.concatenate([triangles[:, :, [0, 2]], np.ones((*triangles.shape[:2], 1))], axis=2)
    coeff = np.linalg.solve(matrix, triangles[:, :, 1, None])[:, :, 0]
    delta = np.sum(query * coeff[owner, :2], axis=1) + coeff[owner, 2] - floor_height(query)
    low, high = np.full(len(candidates), np.inf), np.full(len(candidates), -np.inf)
    np.minimum.at(low, owner, delta)
    np.maximum.at(high, owner, delta)
    mouth_only = (high <= -0.25) | (low >= clearance + 0.35)
    accepted = np.ones(len(candidates), bool)
    accepted[mouth_only] = shapely.area(shapely.intersection(polygons[mouth_only], mouths)) >= 1e-10
    return [
        (int(tid), mouths if only else corridor)
        for tid, only, keep in zip(candidates, mouth_only, accepted, strict=True)
        if keep
    ]
