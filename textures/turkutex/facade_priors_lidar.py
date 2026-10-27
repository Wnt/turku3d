"""Diagnostic wall density/depth grids. Empty airborne-lidar cells are unknown, not windows."""

from __future__ import annotations

import numpy as np
from scipy.ndimage import gaussian_filter

from .facade_cv import grid_axis


def opening_grid(points, width, height, resolution=0.5):
    shape = (max(1, int(np.ceil(height / resolution))), max(1, int(np.ceil(width / resolution))))
    counts = np.zeros(shape)
    depth = np.zeros(shape)
    keep = (
        ~np.isin(points["classification"], [2, 3, 4, 5, 7, 8, 18])
        & (points["h"] >= 0.3)
        & (np.abs(points["d"]) <= 0.6)
        & (points["u"] >= 0)
        & (points["u"] <= width)
        & (points["h"] <= height)
    )
    p = points[keep]
    if len(p):
        y = np.minimum((p["h"] / resolution).astype(int), shape[0] - 1)
        x = np.minimum((p["u"] / resolution).astype(int), shape[1] - 1)
        np.add.at(counts, (y, x), 1)
        np.add.at(depth, (y, x), p["d"])
    mean_depth = np.divide(depth, counts, out=np.zeros(shape), where=counts > 0)
    # Density depressions are compared to local sampling, not treated as guaranteed glass.
    smooth = gaussian_filter(counts, 2)
    density_holes = (counts < smooth * 0.3) & (smooth >= 1)
    plane = float(np.median(p["d"])) if len(p) else 0.0
    recessed = (counts >= 2) & (mean_depth < plane - 0.12)
    outputs = {}
    for name, mask in (("density", density_holes), ("depth", recessed)):
        rows, _, row_conf = grid_axis(mask, 1, 30)
        columns, _, col_conf = (
            grid_axis(mask[max(1, round(shape[0] * 0.25)) :], 0, 60) if shape[0] > 1 else (0, 0, 0)
        )
        outputs[name] = dict(
            storeys=rows, window_columns=columns if rows > 1 else 0, regularity=[row_conf, col_conf]
        )
    coverage = float((counts >= 1).mean())
    supported = float((counts >= 3).mean())
    stats = dict(
        points=len(p),
        points_per_m2=len(p) / max(width * height, 0.01),
        coverage=coverage,
        supported=supported,
        usable=coverage >= 0.8 and supported >= 0.5,
        candidates=outputs,
    )
    return stats, dict(
        counts=counts, mean_depth=mean_depth, density_holes=density_holes, recessed=recessed
    )
