"""Ground-TIN support for bridge approach joins."""

import numpy as np
from scipy.spatial import cKDTree


def terrain_sampler(points):
    """Sample the rendered ground triangles, also beyond the current tile boundary.

    A nearest-keypoint height can sit metres above the interpolated bank TIN.
    Bridge joins must use the latter, on the same global two-metre lattice.
    """
    from functools import lru_cache

    from scipy.interpolate import LinearNDInterpolator

    from turku3d import config as C

    from .inputs import CELLS

    linear = LinearNDInterpolator(points[:, [0, 2]], points[:, 1])
    tree = cKDTree(points[:, [0, 2]])
    step = C.VISUAL_TILE_SIZE / CELLS

    @lru_cache(maxsize=8192)
    def vertex(i, j):
        xz = [i * step, j * step]
        value = float(linear([xz])[0])
        return value if np.isfinite(value) else float(points[tree.query(xz)[1], 1])

    def sample(x, z):
        u, v = x / step, z / step
        i, j = int(np.floor(u)), int(np.floor(v))
        fu, fv = u - i, v - j
        if fu + fv <= 1:
            return vertex(i, j) * (1 - fu - fv) + vertex(i + 1, j) * fu + vertex(i, j + 1) * fv
        return (
            vertex(i + 1, j + 1) * (fu + fv - 1)
            + vertex(i, j + 1) * (1 - fu)
            + vertex(i + 1, j) * (1 - fv)
        )

    return sample
