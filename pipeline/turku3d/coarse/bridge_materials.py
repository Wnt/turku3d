"""Deterministic metre-scale bridge PBR, synthesized without reference-image pixels.

One repeat covers two metres. Keep material work separate from structure hashes so
surface refinement never rebuilds the surveyed deck or its collision geometry.
"""
from functools import lru_cache
from io import BytesIO

import numpy as np
from PIL import Image
from scipy.ndimage import gaussian_filter

SIZE = 512
METRES = 2.0
PALETTE = {
    'asphalt': (67, 69, 70), 'concrete': (155, 154, 148), 'stone': (140, 137, 128),
    'steel': (78, 86, 87), 'wood': (109, 91, 69), 'ballast': (107, 103, 95),
    'paint': (194, 196, 184), 'rock': (127, 128, 122),
    'granite': (158, 153, 141), 'bronze': (68, 94, 83),
}


def _noise(rng, scale):
    field = gaussian_filter(rng.normal(size=(SIZE, SIZE)), scale, mode='wrap')
    return field / max(field.std(), 1e-6)


def fields(surface):
    """Return linear heights (metres), sRGB albedo and linear ORM channels."""
    rng = np.random.default_rng(2307)
    y, x = np.mgrid[:SIZE, :SIZE] * METRES / SIZE
    fine, grain, macro = (_noise(rng, s) for s in (.65, 2., 28.))
    tint = np.array(PALETTE[surface], float)
    color = tint + (grain * 2.5 + macro * 2)[..., None]
    height = fine * .0002
    rough = np.full((SIZE, SIZE), .87)
    metal = np.zeros((SIZE, SIZE))
    ao = np.ones((SIZE, SIZE))
    if surface in ('granite', 'stone', 'rock'):
        # Small quartz/feldspar inclusions; no printed stone photographs or text.
        quartz = np.clip(grain - .65, 0, 2)
        mica = np.clip(-fine - 1., 0, 2)
        color += (quartz * 12 - mica * 17)[..., None]
        color += (np.clip(macro, -1.5, 1.5)[..., None] * np.array([3., 1., -1.]))
        height = grain * .0007 + fine * .0005
        rough = np.clip(.82 + fine * .035, .65, .96)
        if surface == 'stone':
            row = np.floor(y / .5)
            edge_y = np.minimum(y % .5, .5 - y % .5)
            edge_x = np.minimum((x + (row % 2) * .5) % 1., 1. - (x + (row % 2) * .5) % 1.)
            joint = np.exp(-np.minimum(edge_x, edge_y) ** 2 / (.009 ** 2))
            height -= joint * .009
            color -= joint[..., None] * 24
            ao -= joint * .35
            rough = np.maximum(rough, joint * .96)
        elif surface == 'rock':
            fissure = np.exp(-np.sin((x + .17 * np.sin(y * np.pi)) * np.pi * 2) ** 2 / .002)
            height -= fissure * .008
            color -= fissure[..., None] * 17
            ao -= fissure * .25
    elif surface == 'concrete':
        # Faint formwork courses and aggregate pores, not dark masonry joints.
        seam = np.exp(-np.sin(y * np.pi * 4) ** 2 / .003)
        pore = np.clip(-grain - 1.5, 0, 1)
        color -= (seam * 4 + pore * 12)[..., None]
        height = fine * .00035 - seam * .0005 - pore * .001
        rough = np.clip(.88 + grain * .025, .75, .97)
    elif surface in ('steel', 'paint', 'bronze'):
        patina = np.clip(.5 + macro * .22, 0, 1)
        height = fine * .000035 + grain * .00008
        if surface == 'bronze':
            copper = np.array([110., 91., 56.])
            color = tint * patina[..., None] + copper * (1 - patina[..., None])
            color += grain[..., None] * 2
            metal = .3 + .6 * (1 - patina)
            rough = .48 + patina * .32
        else:
            rough = .48 + patina * .16
            # Weathered paint is dielectric; tiny exposed flecks remain metallic.
            wear = np.clip((grain - 2.2) * .18, 0, .3)
            metal = wear
            color += (patina * 3 - wear * 22)[..., None]
    elif surface == 'asphalt':
        aggregate = np.clip(grain, -.8, 1.8)
        color += (fine * 5 + aggregate * 8)[..., None]
        height = fine * .00045 + aggregate * .0008
        rough = np.clip(.9 + fine * .02, .8, .98)
    elif surface == 'wood':
        fibre = np.sin(x * np.pi * 140 + macro * 1.5)
        seam = np.exp(-np.sin(x * np.pi * 4) ** 2 / .001)
        color += (fibre * 4 - seam * 24)[..., None]
        height = fibre * .00025 - seam * .004
        ao -= seam * .3
    elif surface == 'ballast':
        color += grain[..., None] * 14
        height = grain * .003
    orm = np.stack([ao, rough, metal], axis=-1)
    return height, np.clip(color, 0, 255).astype(np.uint8), np.uint8(np.clip(orm, 0, 1) * 255)


def _png(pixels):
    out = BytesIO()
    Image.fromarray(pixels).save(out, format='PNG')
    return out.getvalue()


@lru_cache(maxsize=16)
def maps(surface):
    height, color, orm = fields(surface)
    dx = (np.roll(height, -1, axis=1) - np.roll(height, 1, axis=1)) * SIZE / (2 * METRES)
    dy = (np.roll(height, -1, axis=0) - np.roll(height, 1, axis=0)) * SIZE / (2 * METRES)
    # GLTFLoader flips normalScale.y for derivative tangents. Image rows and
    # our metric UVs both increase downward, so encode the compensating green.
    normal = np.stack([-dx, dy, np.ones_like(dx)], axis=-1)
    normal /= np.linalg.norm(normal, axis=-1)[..., None]
    return dict(image=_png(color), normal_image=_png(np.uint8((normal * .5 + .5) * 255)),
                orm_image=_png(orm))
