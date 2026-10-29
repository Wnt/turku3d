"""CPU base-colour sampler for the game facade-kit v4 node program.

Google reference pixels stay in the private scorer. This module evaluates only
our public shader's atlas coordinates, pigments and bands at matched wall samples.
"""

from pathlib import Path

import numpy as np
from PIL import Image


SIZES = {
    '_KIT_LAYOUT': 3, '_KIT_WALL': 3, '_KIT_TRIM': 3, '_KIT_BASE': 3,
    '_KIT_EXTENT': 4, '_KIT_FLOORS': 3, '_KIT_OPENING': 4,
    '_KIT_BANDS': 4, '_KIT_DETAIL': 4,
}


def linear(rgb):
    rgb = np.asarray(rgb, dtype=float)
    return np.where(rgb <= .04045, rgb / 12.92, ((rgb + .055) / 1.055)**2.4)


def srgb(rgb):
    return np.clip(np.where(rgb <= .0031308, rgb * 12.92,
                            1.055 * np.maximum(rgb, 0)**(1/2.4) - .055), 0, 1)


def bilinear_linear_rgba(pixels, uv):
    x = np.clip(uv[:, 0] * pixels.shape[1] - .5, 0, pixels.shape[1] - 1)
    y = np.clip(uv[:, 1] * pixels.shape[0] - .5, 0, pixels.shape[0] - 1)
    x0, y0 = np.floor(x).astype(int), np.floor(y).astype(int)
    x1, y1 = np.minimum(x0 + 1, pixels.shape[1] - 1), np.minimum(y0 + 1, pixels.shape[0] - 1)
    a, b, c, d = (pixels[yy, xx].astype(float) / 255
                  for xx, yy in ((x0, y0), (x1, y0), (x0, y1), (x1, y1)))
    for value in (a, b, c, d):
        value[:, :3] = linear(value[:, :3])
    wx, wy = (x - x0)[:, None], (y - y0)[:, None]
    return (a * (1 - wx) + b * wx) * (1 - wy) + (c * (1 - wx) + d * wx) * wy


def warp(value, lo, hi, start, end):
    return np.where(value < lo, value / np.maximum(lo, .001) * start,
        np.where(value > hi,
            end + (value - hi) / np.maximum(1 - hi, .001) * (1 - end),
            start + (value - lo) / np.maximum(hi - lo, .001) * (end - start)))


def evaluate(uv, attributes, pixels):
    """Return sRGB 0..255, matching facadeKitMaterial/programLayout/Pigment."""
    layout = attributes['_KIT_LAYOUT']
    wall = attributes['_KIT_WALL']
    trim = attributes['_KIT_TRIM']
    base = attributes['_KIT_BASE']
    extent = attributes['_KIT_EXTENT']
    floors = attributes['_KIT_FLOORS']
    opening = attributes['_KIT_OPENING']
    bands = attributes['_KIT_BANDS']
    detail = attributes['_KIT_DETAIL']
    if np.any(layout[:, 1] <= 0) or np.any(floors[:, :2] <= 0) or np.any(extent[:, 2:] <= 0):
        raise ValueError('Facade-kit v4 has nonpositive dimensions')
    metres = uv - extent[:, :2]
    upper = (metres[:, 1] >= floors[:, 0]).astype(float)
    bays = np.column_stack((metres[:, 0] / layout[:, 1],
        np.where(upper > 0, (metres[:, 1] - floors[:, 0]) / floors[:, 1] + 1,
                 metres[:, 1] / floors[:, 0])))
    entrance = (layout[:, 0] < 42) & (layout[:, 0] % 3 == 1)
    selected = np.where(entrance & (np.floor(bays[:, 0]) != floors[:, 2]) & (upper < 1),
                        layout[:, 0] - 1, layout[:, 0])
    blank = np.where(layout[:, 0] >= 42, layout[:, 0], np.floor(layout[:, 0] / 6) + 42)
    above = metres[:, 1] >= extent[:, 3]
    plain = (detail[:, 2] % 2 > .5) & (upper < 1)
    selected_module = np.where((layout[:, 0] < 42) & (above | plain), blank, selected)
    phase = ((np.floor(bays[:, 0]) % 2) * 2 - 1) * detail[:, 0]
    x = (bays[:, 0] + phase) % 1
    y = bays[:, 1] % 1
    glass = np.floor(layout[:, 0] / 6) == 5
    source_width = np.where(glass, .92, np.where(layout[:, 0] % 6 >= 3, .65, .45))
    source_height = np.where(glass, .72, .426 * (1 - upper) + .5 * upper)
    source_sill = np.where(glass, .15, .292 * (1 - upper) + .21 * upper)
    patch_kind = detail[:, 3] % 4
    is_patch = detail[:, 3] > 0
    shape = np.where(is_patch, np.floor(detail[:, 3] / 4),
        np.where(upper < 1, np.floor(detail[:, 2] / 2), -detail[:, 3])) * (layout[:, 0] < 42)
    special = (upper < 1) & (selected_module % 3 != 0) & (selected_module < 42)
    unit_height = np.where(is_patch, extent[:, 3], np.where(upper > 0, floors[:, 1], floors[:, 0]))
    window_width = np.where(is_patch, 1, np.where(special, .49, opening[:, 0]))
    window_height = np.where(is_patch, 1, np.where(special, .76, opening[:, 1]))
    bottom = np.where(is_patch, 0, np.where(special, 0, opening[:, 2]))
    half = window_width * np.where(is_patch, extent[:, 2], layout[:, 1]) / 2
    rise = np.minimum(half * np.where(shape == 3, np.sqrt(3), np.where(shape == 2, 1, .36)),
                      window_height * unit_height * .5)
    radius = (half**2 + rise**2) / np.maximum(rise * 2, .001)
    across = abs(x - .5) * np.where(is_patch, extent[:, 2], layout[:, 1])
    round_drop = radius - np.sqrt(np.maximum(radius**2 - np.minimum(across, half)**2, 0))
    pointed_cap = np.sqrt(np.maximum(4 - (np.clip(across / np.maximum(half, .001), 0, 1) + 1)**2, 0)) / np.sqrt(3)
    drop = np.where(shape == 3, rise * (1 - pointed_cap), round_drop) / np.maximum(unit_height, .001)
    top = bottom + window_height - np.where(shape > 0, drop, 0)
    py = np.clip(metres[:, 1] / extent[:, 3], 0, 1)
    opening_y = np.where(is_patch, py, y)
    cutout = (shape > 0) & (across <= half) & (opening_y > top)
    module = np.where(cutout, blank, selected_module)
    horizontal = warp(x, .5 - opening[:, 0] / 2, .5 + opening[:, 0] / 2,
                      .5 - source_width / 2, .5 + source_width / 2)
    vertical = warp(y, opening[:, 2], top, source_sill, source_sill + source_height)
    ordinary_x = np.where(special | (module >= 42), bays[:, 0] % 1, horizontal)
    ordinary_y = np.where(special | (module >= 42),
        np.where((shape > 0) & special, y / np.maximum(top, .001) * .76, y), vertical)
    patch_y = np.clip(py / np.maximum(top, .001), 0, 1)
    patch_x = np.where(patch_kind == 2, (x - .5) * .49 + .5, (x - .5) * .45 + .5)
    patch_v = np.where(patch_kind == 2, patch_y * .76, patch_y * .5 + .21)
    local_x = np.where(cutout, .05, np.where(is_patch, patch_x, ordinary_x))
    local_y = np.where(cutout, .95, np.where(is_patch, patch_v, ordinary_y))
    cell = np.column_stack((module % 7, np.floor(module / 7) * 2 + 1 - upper))
    atlas = (cell * 128 + np.column_stack((local_x, 1 - local_y)) * 120 + 4) / [1024, 2048]
    sample = bilinear_linear_rgba(pixels, atlas)

    plinth = (metres[:, 1] <= bands[:, 0]).astype(float)
    cornice = ((metres[:, 1] >= extent[:, 3] - bands[:, 1]) &
               (metres[:, 1] <= extent[:, 3])).astype(float)
    corner = (np.minimum(metres[:, 0], extent[:, 2] - metres[:, 0]) <= bands[:, 3]).astype(float)
    floor_distance = np.minimum(bays[:, 1] % 1, 1 - bays[:, 1] % 1) * layout[:, 2]
    course = (floor_distance <= bands[:, 2] / 2).astype(float) * upper
    dx = abs(x - .5) * layout[:, 1]
    sill, head = opening[:, 2], opening[:, 2] + opening[:, 1]
    dy = np.maximum(sill - y, y - head) * layout[:, 2]
    surround = ((dx <= opening[:, 0] * layout[:, 1] / 2 + opening[:, 3]) &
                (dy <= opening[:, 3])).astype(float) * upper * (
                (dx >= opening[:, 0] * layout[:, 1] / 2) |
                (dy >= 0)).astype(float) * (layout[:, 0] < 42)
    balcony = ((dx <= layout[:, 1] * .36) & (y * layout[:, 2] <= 1.05) &
               (y * layout[:, 2] >= .15)).astype(float) * upper * detail[:, 1] * (
               1 - np.floor(bays[:, 0]) % 2) * (layout[:, 0] < 42)
    railing = balcony * np.maximum((metres[:, 0] * 5 % 1 <= .12), (y * layout[:, 2] >= .93))
    band = np.maximum.reduce((cornice, corner, course, surround)) * (metres[:, 1] <= extent[:, 3])
    pigment = (base * (1 - upper[:, None]) + wall * upper[:, None]) * (1 - .33 * plinth[:, None])
    painted_trim = trim * (1 - .35 * railing[:, None])
    balcony_shade = np.clip(balcony * .15 + railing * .65, 0, 1)
    alpha = sample[:, 3, None]
    tint = 1 + (pigment - 1) * np.clip(1 - 2 * alpha, 0, 1) + (trim - 1) * np.clip(2 * alpha - 1, 0, 1)
    color = sample[:, :3] * tint * (1 - balcony_shade[:, None])
    color = color * (1 - band[:, None]) + painted_trim * band[:, None]
    return srgb(color) * 255


def sampler(scene, record, triangles):
    """Return a v4 evaluator, or None for unsupported and incomplete materials."""
    kit = record.get('kit') or {}
    if (kit.get('version') != 4 or not record.get('kit_maps_complete') or
            record.get('unsupported_vertex_color')):
        return None
    names = record.get('attributes', {})
    if not all(name in names for name in SIZES):
        return None
    from compare import texture_file

    path = texture_file(scene, record)
    if path is None:
        return None
    attrs = {name: np.fromfile(Path(scene) / names[name], '<f4').reshape(-1, size)
             for name, size in SIZES.items()}
    pixels = np.asarray(Image.open(path).convert('RGBA'))

    def sample(uv, ids, weights):
        interpolated = {name: np.einsum('ij,ijk->ik', weights, values[triangles[ids]])
                        for name, values in attrs.items()}
        return evaluate(uv, interpolated, pixels)

    return sample
