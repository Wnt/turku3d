"""What the orthophoto says about land nobody mapped: lawns and gravel yards, as outlines.

The colour rules and their cleaning are the old generator's (coarse/surfaces.py `cover_classes`, `stone_dust`,
coarse/yard_cover.py `lawn_mask`), which were set against sampled register blocks: grass is greener than red and
clearly greener than blue (in building shade, greener than red and dark); Turku's gravel is warm grey stone dust, as
bright and nearly as neutral as asphalt. Everything else stays what the ground kind takes unmapped land for.
"""
from __future__ import annotations

import numpy as np
import shapely

MIN_AREA = 8.0        # m2: smaller patches and holes are noise and take the surrounding class
SMOOTH = 0.75         # m: gaussian sigma of each class mask
GRAVEL_SMOOTH = 1.5   # m: gaussian sigma of the sunlit chromaticity
GRAVEL_WARM = 1.03    # smoothed green over blue
GRAVEL_LIT = 0.3      # smallest smoothed share of sunlit neutral pixels that decides


def _without_small(mask: np.ndarray, cells: float) -> np.ndarray:
    """`mask` without its components of fewer than `cells` pixels, and with such holes filled."""
    from scipy.ndimage import label

    for value in (True, False):
        labels, count = label(mask == value)
        if count:
            small = np.bincount(labels.ravel()) < cells
            small[0] = False
            mask = np.where(small[labels], not value, mask)
    return mask


def lawn(rgb: np.ndarray) -> np.ndarray:
    r, g, b = (rgb[..., k].astype(np.float32) for k in range(3))
    sun = (g > 0.95 * r) & (g > 1.15 * b) & (g > 25)
    # Blue skylight in building shade hides the usual green over blue; asphalt stays neutral in red and green.
    shade = (g > 1.15 * r) & (g > 0.8 * b) & (g > 10) & (rgb.max(axis=-1) < 110)
    return sun | shade


def stone_dust(rgb: np.ndarray, step: float) -> np.ndarray:
    """Pixels of warm grey, gravel-like ground (shade and colour never qualify)."""
    from scipy.ndimage import gaussian_filter

    rgb = rgb.astype(np.float32)
    high, low = rgb.max(axis=-1), rgb.min(axis=-1)
    lit = ((high > 110) & (high - low < 0.15 * high)).astype(np.float32)
    sigma = GRAVEL_SMOOTH / step
    share = gaussian_filter(lit, sigma)
    r, g, b = (np.maximum(gaussian_filter(rgb[..., k] * lit, sigma), 1e-3) for k in range(3))
    return (share > GRAVEL_LIT) & (g >= GRAVEL_WARM * b) & (r > 0.95 * g) & (r < 1.06 * g)


def classes(rgb: np.ndarray, valid: np.ndarray, step: float) -> dict[str, np.ndarray]:
    """Cleaned masks by cover name: `grass` and `gravel`."""
    from scipy.ndimage import gaussian_filter

    r, g, b = (rgb[..., k].astype(np.float32) for k in range(3))
    grass = lawn(rgb) & valid
    sand = (r > 1.05 * g) & (g > 1.1 * b) & (r < 1.6 * np.maximum(b, 1))
    gravel = (sand | stone_dust(rgb, step)) & ~grass & valid
    sigma, cells = SMOOTH / step, MIN_AREA / step ** 2
    grass = _without_small(gaussian_filter(grass.astype(np.float32), sigma) > 0.5, cells)
    gravel = _without_small(gaussian_filter(gravel.astype(np.float32), sigma) > 0.5, cells) & ~grass
    return {'grass': grass, 'gravel': gravel}


def outlines(raster, without=None) -> list[tuple[shapely.Polygon, str]]:
    """(polygon in the survey frame, cover name) of the lawns and gravel the orthophoto `raster` shows, outside the
    shape `without`."""
    from affine import Affine
    from rasterio.features import shapes

    step = raster.px_m
    transform = Affine(step, 0, raster.e0, 0, -step, raster.n1)
    found = []
    for name, mask in classes(raster.rgb, raster.valid, step).items():
        mask = mask.astype(np.uint8)
        for geometry, value in shapes(mask, mask=mask.astype(bool), transform=transform):
            if not value:
                continue
            area = shapely.make_valid(shapely.geometry.shape(geometry).simplify(step))
            if without is not None:
                area = area.difference(without)
            for part in shapely.get_parts(area):
                if part.geom_type == 'Polygon' and part.area >= MIN_AREA:
                    found.append((part, name))
    return found
