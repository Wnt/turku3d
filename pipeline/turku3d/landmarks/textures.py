"""Landmark surface images: the true ortho over the roof, a procedural wall material.

Neither uses street imagery. The roof image is the city's 2022 true ortho (no building
lean). The wall material is generated here from a seed and the allowlist's hand-picked colour
(the lidar's RGB on wall returns is grey shade from the nadir camera, so it cannot give one),
so it ships under the project licence.
"""

from __future__ import annotations

import io

import numpy as np
from PIL import Image

from turku3d import config as C

WALL_TILE_M = 4.0  # One wall image covers 4 m x 4 m and repeats.
WALL_PX = 512
MAX_ORTHO_PX = 2048


def jpeg(rgb: np.ndarray, quality: int = 90) -> bytes:
    out = io.BytesIO()
    Image.fromarray(np.ascontiguousarray(rgb, np.uint8)).save(out, "JPEG", quality=quality)
    return out.getvalue()


def ortho_crop(
    vrt, bounds_local, resolution: float
) -> tuple[np.ndarray, tuple[float, float, float, float]]:
    """RGB over local (xmin, zmin, xmax, zmax), row 0 at zmin (north), sides multiples of 4."""
    import rasterio
    from rasterio.enums import Resampling
    from rasterio.windows import from_bounds

    xmin, zmin, xmax, zmax = bounds_local
    width = min(MAX_ORTHO_PX, max(4, int(round((xmax - xmin) / resolution / 4)) * 4))
    height = min(MAX_ORTHO_PX, max(4, int(round((zmax - zmin) / resolution / 4)) * 4))
    e0, n1 = xmin + C.ORIGIN_E, C.ORIGIN_N - zmin
    e1, n0 = xmax + C.ORIGIN_E, C.ORIGIN_N - zmax
    with rasterio.open(vrt) as src:
        window = from_bounds(e0, n0, e1, n1, transform=src.transform)
        rgb = src.read(
            [1, 2, 3],
            window=window,
            out_shape=(3, height, width),
            resampling=Resampling.average,
            boundless=True,
            fill_value=0,
        )
    image = np.moveaxis(rgb, 0, -1)
    if not image.any():
        raise ValueError("the true ortho has no pixels over the landmark")
    return image, (xmin, zmin, xmax, zmax)


def wall_image(kind: str, tint, seed: int) -> np.ndarray:
    """A seamless WALL_TILE_M square of brick courses or rendered plaster in the given tint."""
    rng = np.random.default_rng(seed)
    px_per_m = WALL_PX / WALL_TILE_M
    yy, xx = np.mgrid[0:WALL_PX, 0:WALL_PX].astype(np.float64)
    base = np.asarray(tint, np.float64)
    # Low-frequency weathering that wraps seamlessly: a sum of whole-period waves.
    weather = np.zeros((WALL_PX, WALL_PX))
    for _ in range(6):
        kx, ky = rng.integers(1, 5, size=2)
        phase = rng.uniform(0, 2 * np.pi)
        weather += np.sin(2 * np.pi * (kx * xx + ky * yy) / WALL_PX + phase)
    weather = 1.0 + 0.04 * weather / 6
    grain = 1.0 + rng.normal(0, 0.035, (WALL_PX, WALL_PX))
    if kind == "render":
        rgb = base[None, None, :] * (weather * grain)[..., None]
        return np.clip(rgb, 0, 255).astype(np.uint8)
    if kind != "brick":
        raise ValueError(f"unknown wall material {kind}")
    # Medieval large-format brick: 0.30 x 0.09 m courses with 12 mm joints, running bond.
    course, length, joint = 0.09 * px_per_m, 0.30 * px_per_m, 0.012 * px_per_m
    rows = 2 * max(1, int(round(WALL_PX / course / 2)))  # even, so the running bond wraps
    cols = int(round(WALL_PX / length))
    course, length = WALL_PX / rows, WALL_PX / cols
    row = np.floor(yy / course).astype(int)
    shifted = xx + (row % 2) * length / 2
    col = np.floor(shifted / length).astype(int) % cols
    in_joint = ((yy % course) < joint) | ((shifted % length) < joint)
    brick_tone = 1.0 + rng.normal(0, 0.06, (rows, cols))
    brick_hue = rng.normal(0, 0.02, (rows, cols, 3))
    tone = brick_tone[row % rows, col][..., None] + brick_hue[row % rows, col]
    rgb = base[None, None, :] * tone * (weather * grain)[..., None]
    mortar = np.array([158.0, 150.0, 138.0]) * (weather * grain)[..., None]
    rgb = np.where(in_joint[..., None], mortar, rgb)
    return np.clip(rgb, 0, 255).astype(np.uint8)
