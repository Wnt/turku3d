"""Eight-channel material weights. Ground triangle semantics always override RGB heuristics."""

from __future__ import annotations

import numpy as np
from turku3d.col.materials import by_name, table

MATERIAL_KEYS = (
    "asphalt_fresh",
    "asphalt_worn",
    "granite_curb",
    "cobblestone",
    "concrete_slabs",
    "grass",
    "gravel",
    "quay_stone",
)
# Names come from the shared collision table; these are visual bindings, not new IDs.
BINDINGS = {
    "road": 1,
    "sidewalk": 4,
    "curb": 2,
    "curb_dropped": 2,
    "crossing": 1,
    "cycleway": 1,
    "cobblestone": 3,
    "paving_slab": 4,
    "gravel": 6,
    "grass": 5,
    "soil": 6,
    "sand": 6,
    "rock": 2,
    "quay": 7,
    "stair_slope": 4,
    "stair_tread_helper": 4,
    "ramp": 4,
    "bridge_deck": 1,
}


def weights(material_ids: np.ndarray, rgb: np.ndarray) -> np.ndarray:
    """Return float32 H×W×8 weights summing to one. ID 0 uses colour segmentation.

    Water/buildings/unsupported semantics are rejected, rather than silently made into lawn.
    Worn/fresh asphalt mix uses brightness; green segmentation is only a no-vector fallback.
    """
    ids, rgb = np.asarray(material_ids), np.asarray(rgb)
    if ids.ndim != 2 or rgb.shape != (*ids.shape, 3):
        raise ValueError("expected material HxW and ortho HxWx3")
    if not np.issubdtype(ids.dtype, np.integer):
        raise ValueError("material IDs must be integers")
    names = by_name()
    lut = np.full(256, -1, np.int16)
    for name, channel in BINDINGS.items():
        lut[names[name].id] = channel
    if np.any((ids < 0) | (ids > 255)):
        raise ValueError("material ID outside uint8 range")
    invalid = np.unique(ids[(ids != 0) & (lut[ids] < 0)])
    if len(invalid):
        raise ValueError(f"unsupported ground material IDs: {invalid.tolist()}")
    if not np.isfinite(rgb).all() or np.any((rgb < 0) | (rgb > 255)):
        raise ValueError("ortho must be finite RGB in [0,255]")
    colour = rgb.astype(np.float32) / 255
    luminance = colour @ np.array([0.2126, 0.7152, 0.0722], np.float32)
    channels = lut[ids]
    unknown = ids == 0
    # Conservative excess-green test: neutral paving never becomes grass from brightness alone.
    green = (colour[..., 1] > colour[..., 0] * 1.12) & (colour[..., 1] > colour[..., 2] * 1.08)
    channels = np.where(unknown, np.where(green, 5, np.where(luminance > 0.52, 4, 1)), channels)
    out = np.eye(8, dtype=np.float32)[channels]
    asphalt = channels == 1
    worn = np.clip((luminance - 0.12) / 0.30, 0.15, 1)
    out[..., 0] = np.where(asphalt, 1 - worn, 0)
    out[..., 1] = np.where(asphalt, worn, 0)
    return out


def pack_weights(values: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Two RGBA8 maps, using largest-remainder rounding so every texel sums to 255."""
    a = np.asarray(values)
    if a.shape[-1] != 8 or not np.isfinite(a).all() or np.any(a < 0):
        raise ValueError("expected finite nonnegative eight-channel weights")
    total = a.sum(-1, keepdims=True)
    if np.any(total <= 0):
        raise ValueError("zero-sum weights")
    scaled = a / total * 255
    q = np.floor(scaled).astype(np.uint8)
    order = np.argsort(-(scaled - q), axis=-1, kind="stable")
    remaining = 255 - q.astype(np.int32).sum(-1)
    for rank in range(8):
        channel = order[..., rank : rank + 1]
        selected = np.take_along_axis(q, channel, axis=-1)
        np.put_along_axis(q, channel, selected + (remaining > rank)[..., None], axis=-1)
    return q[..., :4], q[..., 4:]


def rasterize_triangles(positions, indices, material, *, shape, x0, z0, pixel_m):
    """World-local layer .col.npz -> north-first semantic raster; vertical faces have no area.

    Call with a halo and all neighbouring collision tiles, not only the target tile's triangles.
    """
    from affine import Affine
    from rasterio.features import rasterize

    p, tri, ids = np.asarray(positions), np.asarray(indices), np.asarray(material)
    if (
        p.ndim != 2
        or p.shape[1] != 3
        or tri.ndim != 2
        or tri.shape[1] != 3
        or ids.shape != (len(tri),)
    ):
        raise ValueError("invalid ground layer array shapes")
    if (
        not np.isfinite(p).all()
        or not np.issubdtype(tri.dtype, np.integer)
        or np.any(tri < 0)
        or np.any(tri >= len(p))
    ):
        raise ValueError("invalid ground layer positions/indices")
    valid_ids = {m.id for m in table() if m.name in BINDINGS}
    if not set(np.unique(ids)).issubset(valid_ids):
        raise ValueError("ground triangles contain unknown/unsupported materials")
    shapes = []
    for vertices, mid in zip(p[tri][:, :, [0, 2]], ids, strict=True):
        a, b, c = vertices
        area = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
        if abs(area) > 1e-12:
            shapes.append(
                ({"type": "Polygon", "coordinates": [[*vertices.tolist(), a.tolist()]]}, int(mid))
            )
    if not shapes:
        return np.zeros(shape, np.uint8)
    return rasterize(
        shapes,
        out_shape=shape,
        transform=Affine(pixel_m, 0, x0, 0, pixel_m, z0),
        fill=0,
        dtype="uint8",
    )
