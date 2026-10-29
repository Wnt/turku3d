"""Camera elevation from local N2000 ground key points; raw provider metadata is retained."""

from __future__ import annotations

import hashlib
import math
from functools import lru_cache
from pathlib import Path

import numpy as np
from turku3d import config


def fit_ground(xy: np.ndarray, heights: np.ndarray, query: np.ndarray) -> float | None:
    """Fit a supported local plane, rejecting gaps, steep/discontinuous or noisy samples."""
    if not all(np.isfinite(values).all() for values in (xy, heights, query)):
        return None
    delta = np.asarray(xy) - query
    distances = np.linalg.norm(delta, axis=1)
    if len(distances) < 6 or distances.min() > 5 or distances.max() > 20:
        return None
    design = np.column_stack([delta, np.ones(len(delta))])
    weights = 1 / np.maximum(distances, 0.5)
    coeff, _, rank, _ = np.linalg.lstsq(design * weights[:, None], heights * weights, rcond=None)
    if rank < 3 or np.linalg.norm(coeff[:2]) > 0.5:
        return None
    residual = heights - design @ coeff
    if np.quantile(np.abs(residual), 0.75) > 0.3:
        return None
    return float(coeff[2])


@lru_cache(maxsize=16)
def ground_tile(path: Path, size: int, modified: int):
    import laspy
    from scipy.spatial import cKDTree

    points = laspy.read(path)
    keep = np.isin(np.asarray(points.classification), [2, 8, 12])
    xy = np.column_stack([np.asarray(points.x)[keep], np.asarray(points.y)[keep]])
    heights = np.asarray(points.z)[keep]
    return xy, heights, cKDTree(xy)


def ground_height(e: float, n: float) -> float | None:
    # City maanpinta uses 500 m files named by west and north bounds.
    west, north = math.floor(e / 500) * 500, (math.floor(n / 500) + 1) * 500
    path = config.raw_dir("laz/maanpinta") / f"{west}_{north}.laz"
    try:
        stat = path.stat()
        xy, heights, tree = ground_tile(path, stat.st_size, stat.st_mtime_ns)
    except (OSError, ImportError):
        return None
    if len(xy) < 6:
        return None
    _, indices = tree.query([e, n], k=min(16, len(xy)))
    return fit_ground(xy[indices], heights[indices], np.array([e, n]))


def ground_fingerprint(e: float, n: float, radius: float) -> str:
    """Invalidate reference caches when nearby key points arrive or are replaced."""
    records = []
    for west in range(
        math.floor((e - radius) / 500) * 500, math.floor((e + radius) / 500) * 500 + 1, 500
    ):
        for south in range(
            math.floor((n - radius) / 500) * 500, math.floor((n + radius) / 500) * 500 + 1, 500
        ):
            path = config.raw_dir("laz/maanpinta") / f"{west}_{south + 500}.laz"
            try:
                stat = path.stat()
                records.append((str(path), stat.st_size, stat.st_mtime_ns))
            except FileNotFoundError:
                records.append((str(path), None, None))
    return hashlib.sha256(repr(records).encode()).hexdigest()


def camera_elevation(meta, base_h: float) -> tuple[float, str]:
    if meta.source == "lookaround" or meta.elevation is None:
        ground = ground_height(meta.E, meta.N)
        if ground is not None:
            return (
                ground + meta.cam_height,
                f"N2000 ground key-point plane + assumed {meta.source} camera height",
            )
    # Look Around provider elevation has no reliable N2000 datum. Using it when
    # ground support is missing can place a street camera above the facade roof.
    # The target base is an explicit approximation, preferable to a wrong datum.
    if meta.elevation is not None and meta.source != "lookaround":
        return meta.elevation, "provider elevation"
    return base_h + meta.cam_height, "facade base + assumed camera height"
