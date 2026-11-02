"""Visual street finishes; collision retains the existing physical surface classes.

The city distinguishes irregular fieldstone from sawn granite setts and explicitly
records red asphalt. These ids are reserved for visual terrain only, never .col.
Maps are original deterministic procedural surfaces, periodic over two metres.
"""

import numpy as np

FIELDSTONE = 128
RED_ASPHALT = 129
FINISH_KEYS = {FIELDSTONE: "fieldstone", RED_ASPHALT: "asphalt_red"}


def street_finish(surface):
    value = surface.casefold() if isinstance(surface, str) else ""
    if "kenttäkivi" in value:
        return FIELDSTONE
    if "asfalt" in value and "punai" in value:
        return RED_ASPHALT
    return None


def maps(key, size=512):
    """Albedo, tangent normal and ORM. Fine grain is mipmapped by the packager."""
    from scipy.ndimage import gaussian_filter

    rng = np.random.default_rng(4817)
    grain = rng.normal(0, 1, (size, size))
    macro = gaussian_filter(grain, size / 32, mode="wrap")
    macro /= max(macro.std(), 1e-6)
    if key == "asphalt_red":
        albedo = np.array([122, 65, 52]) + grain[..., None] * 7 + macro[..., None] * 2
        height = gaussian_filter(grain, .6, mode="wrap") * .0005
        ao = np.ones((size, size))
        roughness = np.clip(.9 + grain * .02, .75, 1)
    elif key == "fieldstone":
        from scipy.spatial import cKDTree

        # Jittered, staggered sites keep plausible 8–15 cm rounded stones,
        # without the straight joints of concrete slabs or regular granite setts.
        rows, cols = 19, 18
        yy, xx = np.indices((rows, cols))
        sites = np.c_[((xx + .5 + (yy % 2) * .5).ravel() / cols) % 1,
                      (yy.ravel() + .5) / rows]
        sites += rng.uniform(-.28, .28, sites.shape) / [cols, rows]
        offsets = np.array([(x, y) for x in (-1, 0, 1) for y in (-1, 0, 1)])
        repeated = (sites[None] + offsets[:, None]).reshape(-1, 2)
        y, x = np.indices((size, size))
        points = np.c_[(x.ravel() + .5) / size, (y.ravel() + .5) / size]
        distance, ids = cKDTree(repeated).query(points, k=2)
        nearest = ids[:, 0] % len(sites)
        # Distance to the bisector between neighbouring sites, in metres.
        separation = np.linalg.norm(repeated[ids[:, 1]] - repeated[ids[:, 0]], axis=1)
        edge = ((distance[:, 1] ** 2 - distance[:, 0] ** 2)
                / np.maximum(separation, 1e-6)).reshape(size, size)
        edge = gaussian_filter(edge, .8, mode="wrap")
        crown = np.tanh(np.maximum(edge - .0015, 0) / .015)
        stone = rng.uniform(105, 160, len(sites))[nearest].reshape(size, size)
        warmth = rng.uniform(-12, 12, len(sites))[nearest].reshape(size, size)
        rock = np.stack([stone + warmth, stone, stone - warmth * .6], axis=-1)
        dirt = np.array([101, 98, 86])
        albedo = dirt + (rock - dirt) * crown[..., None] + grain[..., None] * 6 + macro[..., None] * 3
        height = crown * .022 + gaussian_filter(grain, .8, mode="wrap") * .0008
        ao = .82 + .18 * crown
        roughness = .94 - .13 * crown
    else:
        raise ValueError(f"Unknown ground finish: {key}")

    # Periodic central differences in physical metres; tangent UV v points north.
    dx = (np.roll(height, -1, 1) - np.roll(height, 1, 1)) / (4 / size)
    dy = (np.roll(height, -1, 0) - np.roll(height, 1, 0)) / (4 / size)
    normal = np.stack([-dx, dy, np.ones_like(height)], axis=-1)
    normal /= np.linalg.norm(normal, axis=-1, keepdims=True)
    orm = np.stack([ao, roughness, np.zeros_like(height)], axis=-1)
    byte = lambda values: np.rint(np.clip(values, 0, 255)).astype(np.uint8)
    return {"albedo": byte(albedo), "normal": byte((normal + 1) * 127.5),
            "orm": byte(orm * 255)}
