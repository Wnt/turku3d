"""Robust ground selection and rasters with explicit observation support."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.ndimage import distance_transform_edt
from scipy.spatial import cKDTree
from scipy.stats import binned_statistic_2d

from turku3d import config as C


class Surface:
    """Local sloping planes, compatible with the ground layer's Surface API."""

    def __init__(self, points):
        self.points = np.asarray(points, dtype=np.float64)
        if self.points.ndim != 2 or self.points.shape[1] != 3 or len(self.points) < 6:
            raise ValueError("at least six xyz ground points required")
        self.tree = cKDTree(self.points[:, [0, 2]])

    def sample(self, xz, k=16, max_distance=np.inf):
        """Trim outliers in a distance-weighted plane; unsupported heights stay NaN."""
        xz = np.asarray(xz).reshape(-1, 2)
        heights, noise = [], []
        for start in range(0, len(xz), 8192):
            q = xz[start : start + 8192]
            d, ii = self.tree.query(q, k=min(k, len(self.points)))
            p = self.points[ii]
            a = np.concatenate((p[:, :, [0, 2]] - q[:, None], np.ones((*ii.shape, 1))), axis=2)
            h = p[:, :, 1]
            support = d <= max_distance
            weights = support / np.maximum(d, 0.1) ** 2
            for _ in range(3):
                ata = np.einsum("nki,nk,nkj->nij", a, weights, a)
                ata += np.eye(3)[None] * 1e-9
                ah = np.einsum("nki,nk,nk->ni", a, weights, h)
                coeff = np.linalg.solve(ata, ah[..., None])[..., 0]
                residual = h - np.einsum("nki,ni->nk", a, coeff)
                mad = np.median(np.abs(residual - np.median(residual, axis=1)[:, None]), axis=1)
                weights = (
                    support & (np.abs(residual) <= np.maximum(0.025, 3 * mad)[:, None])
                ) / np.maximum(d, 0.1) ** 2
            valid = (weights > 0).sum(axis=1) >= 5
            heights.append(np.where(valid, coeff[:, 2], np.nan))
            noise.append(np.where(valid, mad, np.nan))
        if not heights:
            return np.empty(0), np.empty(0)
        return np.concatenate(heights), np.concatenate(noise)


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


def select_ground(cloud, near_ground=0.15):
    """Class 2 plus class 12 within 15 cm of a locally supported robust ground plane."""
    classes = cloud.records["classification"]
    pts = cloud.points
    reference = Surface(pts[classes == 2])
    candidate = np.flatnonzero(np.isin(classes, [2, 12]))
    h, mad = reference.sample(pts[candidate][:, [0, 2]], max_distance=2.0)
    residual = np.abs(pts[candidate, 1] - h)
    keep = np.where(
        classes[candidate] == 2, residual <= np.maximum(0.06, 4 * mad), residual <= near_ground
    )
    accepted = candidate[keep]
    return pts[accepted], {
        "input": len(cloud),
        "class2": int((classes == 2).sum()),
        "class12_accepted": int((classes[accepted] == 12).sum()),
        "accepted": len(accepted),
    }


@dataclass
class Rasters:
    """Rows increase south (+z), columns east (+x), values at cell centres."""

    bbox: C.BBoxLocal
    resolution: float
    ground: np.ndarray
    dsm: np.ndarray
    ndsm: np.ndarray
    chm: np.ndarray
    density: np.ndarray
    confidence: np.ndarray
    observed: np.ndarray

    def save(self, path):
        np.savez_compressed(
            path,
            bbox=self.bbox.as_tuple(),
            resolution=self.resolution,
            **{
                key: getattr(self, key)
                for key in ("ground", "dsm", "ndsm", "chm", "density", "confidence", "observed")
            },
        )


def surfaces(cloud, bbox, resolution=0.25, fill_distance=0.75, *, ground_points=None):
    if resolution <= 0 or not np.isfinite(resolution) or fill_distance < 0:
        raise ValueError("resolution must be positive; fill distance must be nonnegative")
    nx = int(np.ceil((bbox.xmax - bbox.xmin) / resolution))
    nz = int(np.ceil((bbox.zmax - bbox.zmin) / resolution))
    xe = bbox.xmin + np.arange(nx + 1) * resolution
    ze = bbox.zmin + np.arange(nz + 1) * resolution

    def raster(points, statistic):
        if not len(points):
            return np.full((nz, nx), np.nan)
        return binned_statistic_2d(
            points[:, 2], points[:, 0], points[:, 1], statistic=statistic, bins=[ze, xe]
        ).statistic

    if ground_points is None:
        ground_points, _ = select_ground(cloud)
    ground = raster(ground_points, "median")
    count = raster(ground_points, "count")
    observed = np.isfinite(ground)
    confidence = np.minimum(count / 3, 1.0)
    if observed.any():
        distance, nearest = distance_transform_edt(~observed, return_indices=True)
        fill = (~observed) & (distance * resolution <= fill_distance)
        ground[fill] = ground[tuple(nearest[:, fill])]
        confidence[fill] = confidence[tuple(nearest[:, fill])] * np.exp(-distance[fill])
    valid = ~np.isin(cloud.records["classification"], [7, 18])
    dsm = raster(cloud.points[valid], "max")
    vegetation = np.isin(cloud.records["classification"], [3, 4, 5])
    canopy = raster(cloud.points[vegetation], "max")
    return Rasters(
        bbox,
        resolution,
        ground,
        dsm,
        np.maximum(dsm - ground, 0),
        np.maximum(canopy - ground, 0),
        count / resolution**2,
        confidence,
        observed,
    )
