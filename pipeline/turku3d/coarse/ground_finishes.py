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


def maps(key, size=None):
    """Albedo, tangent normal and ORM. Fine grain is mipmapped by the packager."""
    from scipy.ndimage import gaussian_filter, distance_transform_edt

    size = size or (1024 if key == "fieldstone" else 512)
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

        # Toroidal dart throwing gives mixed stone sizes without the diagonal
        # rows of a jittered grid. Minimum spacing excludes tiny sliver stones.
        sites = []
        for _ in range(10000):
            point = rng.random(2)
            delta = abs(np.asarray(sites).reshape(-1, 2) - point)
            delta = np.minimum(delta, 1 - delta)
            if len(delta) and np.min(np.linalg.norm(delta, axis=1)) < .016:
                continue
            sites.append(point)
            if len(sites) == 1100:
                break
        sites = np.asarray(sites)
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
        # Open each cell by 12 mm, then restore 9 mm: round polygon corners and
        # leave sandy joints. Tiling the mask gives true periodic distances.
        core = edge >= .012
        outside = distance_transform_edt(~np.tile(core, (3, 3)))[size:2*size, size:2*size] * (2 / size)
        footprint = outside <= .009
        inner = distance_transform_edt(np.tile(footprint, (3, 3)))[size:2*size, size:2*size] * (2 / size)
        crown = np.tanh(inner / .009)
        coverage = np.clip((.011 - outside) / .004, 0, 1)
        # Finnish fieldstone mixes grey rock, pale granite, feldspar pink and
        # occasional dark stones; sandy joints stay lighter than deep cavities.
        palette = np.array([[85, 89, 86], [155, 154, 146], [121, 126, 122],
                            [139, 122, 114], [139, 135, 119]])
        colours = palette[rng.choice(len(palette), len(sites), p=[.08, .17, .60, .07, .08])]
        colours = colours + rng.normal(0, 5, (len(sites), 1))
        rock = colours[nearest].reshape(size, size, 3)
        dirt = np.array([104, 104, 96])
        albedo = dirt + (rock - dirt) * coverage[..., None] + grain[..., None] * 5 + macro[..., None] * 2
        relief = rng.uniform(.006, .011, len(sites))[nearest].reshape(size, size)
        height = crown * relief + gaussian_filter(grain, .8, mode="wrap") * .0008
        ao = .9 + .1 * crown
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
