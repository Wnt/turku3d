"""Aggregate ground-albedo correction, fitted offline without per-location reference pixels."""

from functools import lru_cache
import json
from pathlib import Path

import numpy as np
import shapely

from turku3d.col.materials import mat_id
from . import surfaces

TABLE = Path(__file__).with_name('ground_colour.json')
SURFACES = {
    'asphalt': ('road', 'cycleway', 'crossing'),
    'paving': ('sidewalk', 'paving_slab', 'cobblestone', 'quay'),
    'gravel': ('gravel',),
    'grass': ('grass',),
}
XYZ = np.array([[.4124564, .3575761, .1804375],
                [.2126729, .7151522, .0721750],
                [.0193339, .1191920, .9503041]])
WHITE = np.array([.95047, 1., 1.08883])


@lru_cache(maxsize=1)
def table():
    return json.loads(TABLE.read_text())


def lab(value):
    value = np.asarray(value, dtype=float) / 255
    linear = np.where(value <= .04045, value / 12.92, ((value + .055) / 1.055)**2.4)
    xyz = linear @ XYZ.T / WHITE
    f = np.where(xyz > (6 / 29)**3, np.cbrt(xyz), xyz / (3 * (6 / 29)**2) + 4 / 29)
    return np.stack((116*f[..., 1] - 16, 500*(f[..., 0] - f[..., 1]),
                     200*(f[..., 1] - f[..., 2])), axis=-1)


def rgb(value):
    value = np.asarray(value, dtype=float)
    y = (value[..., 0] + 16) / 116
    f = np.stack((y + value[..., 1] / 500, y, y - value[..., 2] / 200), axis=-1)
    xyz = np.where(f > 6/29, f**3, 3*(6/29)**2*(f - 4/29)) * WHITE
    linear = xyz @ np.linalg.inv(XYZ).T
    value = np.where(linear <= .0031308, linear * 12.92,
                     1.055 * np.maximum(linear, 0)**(1/2.4) - .055)
    return np.clip(value * 255, 0, 255)


def apply(tile, cleaned, original, streets, regions, buildings=(), water=(), mosaic=None, model=None):
    from .ortho_cleanup import raster

    model = table() if model is None else model
    ids, x, z = raster(tile, original, streets, regions)
    blocked = shapely.union_all([*buildings, *water])
    allowed = np.ones(ids.shape, dtype=bool)
    if not blocked.is_empty:
        allowed &= ~shapely.intersects_xy(blocked, x, z)
    # Preserve bright aerial paint; mapped markings are bound separately as well.
    allowed &= cleaned.min(-1) < 205
    output = cleaned.copy()
    stats = {}
    for surface, names in SURFACES.items():
        group = model['groups'].get(surface)
        if group is None:
            continue
        selected = allowed & np.isin(ids, [mat_id(name) for name in names])
        delta = np.asarray(group['delta_lab']) + np.asarray(
            model.get('mosaics', {}).get(mosaic, {}).get(surface, {}).get('delta_lab', [0, 0, 0]))
        if selected.any() and np.any(delta):
            output[selected] = np.rint(rgb(lab(cleaned[selected]) + delta)).astype(np.uint8)
        stats[surface] = dict(pixels=int(selected.sum()), delta_lab=delta.tolist())
    return output, stats
