"""Robust local surfaces and deterministic lidar splits, in engine x/y/z metres."""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor

import numpy as np
from scipy.spatial import cKDTree


class Surface:
    def __init__(self, points):
        self.points = np.asarray(points, dtype=np.float64)
        if len(self.points) < 6:
            raise ValueError("At least six ground points are required")
        self.tree = cKDTree(self.points[:, [0, 2]])

    def sample(self, xz, k=16):
        """Iteratively trimmed local plane; return height and residual MAD.

        Centre the fit at the query to avoid large-coordinate conditioning errors.
        Distance weights limit influence across gaps. No held-out points enter this fit.
        Each query is fitted on its own, so chunks run on TURKU3D_JOBS threads with
        results independent of chunking and thread count.
        """
        xz = np.asarray(xz).reshape(-1, 2)
        chunks = [xz[start : start + 8192] for start in range(0, len(xz), 8192)]
        workers = max(1, min(len(chunks), int(os.environ.get("TURKU3D_JOBS", "1"))))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            results = list(pool.map(lambda q: self._fit(q, k), chunks))
        if not results:
            return np.empty(0), np.empty(0)
        return np.concatenate([r[0] for r in results]), np.concatenate([r[1] for r in results])

    def _fit(self, q, k):
        d, ii = self.tree.query(q, k=min(k, len(self.points)))
        p = self.points[ii]
        a = np.concatenate((p[:, :, [0, 2]] - q[:, None], np.ones((*ii.shape, 1))), axis=2)
        h = p[:, :, 1]
        weights = 1 / np.maximum(d, 0.1) ** 2
        for _ in range(3):
            ata = np.einsum("nki,nk,nkj->nij", a, weights, a)
            ata += np.eye(3)[None] * 1e-9
            ah = np.einsum("nki,nk,nk->ni", a, weights, h)
            coeff = np.linalg.solve(ata, ah[..., None])[..., 0]
            residual = h - np.einsum("nki,ni->nk", a, coeff)
            mad = np.median(np.abs(residual - np.median(residual, axis=1)[:, None]), axis=1)
            weights = (np.abs(residual) <= np.maximum(0.025, 3 * mad)[:, None]) / np.maximum(
                d, 0.1
            ) ** 2
        return coeff[:, 2], mad


def split_points(points, classes):
    """Coordinate hash: stable across LAZ chunk order and duplicate overlap returns."""
    millimetres = np.rint(points * 1000).astype(np.int64)
    hashed = (
        (millimetres[:, 0] * 73856093)
        ^ (millimetres[:, 1] * 19349663)
        ^ (millimetres[:, 2] * 83492791)
    )
    held = hashed % 5 == 0
    return (points[~held], classes[~held]), (points[held], classes[held])


def select_ground(points, classes):
    """Class 12 must lie within 15 cm of robust class-2 ground; reject isolated noise."""
    reference = Surface(points[classes == 2])
    h, mad = reference.sample(points[:, [0, 2]])
    residual = np.abs(points[:, 1] - h)
    keep = ((classes == 2) & (residual <= np.maximum(0.06, 4 * mad))) | (
        (classes == 12) & (residual <= 0.15)
    )
    accepted = points[keep]
    # Denoise the merged returns independently of the class-2 eligibility surface.
    merged = Surface(accepted)
    h, mad = merged.sample(accepted[:, [0, 2]])
    good = np.abs(accepted[:, 1] - h) <= np.maximum(0.04, 4 * mad)
    return accepted[good], {
        "input": len(points),
        "class2": int((classes == 2).sum()),
        "class12_accepted": int(((classes == 12) & keep).sum()),
        "accepted": int(good.sum()),
    }


def base_points(points, cell=0.5):
    """One observed representative per occupied cell, with robust height median.

    Per column, the median of each cell's values: the middle value, or the mean of the two
    middle values for an even count (the same numbers np.median gives, without a cell loop)."""
    keys = np.floor(points[:, [0, 2]] / cell).astype(np.int64)
    _, inverse, counts = np.unique(keys, axis=0, return_inverse=True, return_counts=True)
    inverse = inverse.ravel()
    starts = np.r_[0, np.cumsum(counts)[:-1]]
    low = starts + (counts - 1) // 2
    high = starts + counts // 2
    result = np.empty((len(counts), points.shape[1]), dtype=np.float64)
    for column in range(points.shape[1]):
        values = points[:, column]
        ordered = values[np.lexsort((values, inverse))]
        result[:, column] = (ordered[low] + ordered[high]) / 2
    return result
