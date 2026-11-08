"""Original deterministic procedural street maps, periodic over metric surface tiles."""

import numpy as np

from .street_materials import FINISH_KEYS

# Metres covered by one texture repeat; the library records uv_scale = 1 / period.
PERIODS = {"fieldstone": 4, "granite_rock": 4, "forest_floor_broadleaf": 4, "forest_floor_conifer": 4,
           "forest_floor_moss": 4, "grass_meadow": 4, "sand": 4, "field_hay": 4}
PROCEDURAL = (*FINISH_KEYS.values(), "granite_rock")
FOREST_FLOORS = ("forest_floor_broadleaf", "forest_floor_conifer", "forest_floor_moss")


def _outline(s, t, lobes):
    """Squared radius of a leaf at local (s along, t across), both in -1..1: egg-shaped, lobed (oak) where lobes > 0."""
    width = (1 + .2 * s) * np.where(lobes > 0, .78 + .22 * np.cos(np.pi * lobes * s), 1)
    return s * s + (t / width) ** 2


def _scatter(rng, size, period, count, length, aspect, palette, weights, layers, lift, where=None, rib=0.0, lobed=0.0):
    """Lay ``count`` leaves, needles or twigs (``length`` m range, width/length ``aspect`` range) on the periodic
    ``layers`` (albedo, height), each over the last. ``where`` (size x size, 0-1) thins them by place."""
    albedo, height = (a.reshape(-1, *a.shape[2:]) for a in layers)
    palette = np.asarray(palette, float)
    cx, cy = rng.random(count) * size, rng.random(count) * size
    half = rng.uniform(*length, count) * (size / period) / 2
    width = half * rng.uniform(*aspect, count)
    across = np.maximum(width, .6)
    # Below 1024 px a needle drawn at the minimum width covers more ground than it does; thin them to keep the cover.
    full = width * 1024 / size
    thin = np.minimum(1, (width / across) / (full / np.maximum(full, .6)))
    angle = rng.uniform(0, np.pi, count)
    cos, sin = np.cos(angle), np.sin(angle)
    tone = (palette[rng.choice(len(palette), count, p=weights)] * rng.uniform(.88, 1.12, (count, 1))
            + rng.normal(0, 3, (count, 3)))
    lobes = np.where(rng.random(count) < lobed, rng.integers(3, 6, count), 0)
    keep = np.flatnonzero(rng.random(count) <= thin * (1 if where is None else where[cy.astype(int), cx.astype(int)]))
    # Coverage: the topmost object per pixel, from local samples closer than a pixel apart.
    ns, nt = int(np.ceil(2.9 * half.max())) + 1, int(np.ceil(2.9 * across.max())) + 1
    s, t = (g.ravel()[None] for g in np.meshgrid(np.linspace(-1, 1, ns), np.linspace(-1, 1, nt), indexing="ij"))
    top = np.full(size * size, -1)
    for chunk in np.array_split(keep, max(1, len(keep) * s.size // 2_000_000)):
        k = chunk[:, None]
        u, v = s * half[k], t * across[k]
        x = np.floor(cx[k] + u * cos[k] - v * sin[k]).astype(int) % size
        y = np.floor(cy[k] + u * sin[k] + v * cos[k]).astype(int) % size
        inside = _outline(s, t, lobes[k]) < 1
        np.maximum.at(top, (y * size + x)[inside], np.broadcast_to(k, inside.shape)[inside])
    hit = np.flatnonzero(top >= 0)
    k = top[hit]
    dx = (hit % size + .5 - cx[k] + size / 2) % size - size / 2
    dy = (hit // size + .5 - cy[k] + size / 2) % size - size / 2
    u, v = dx * cos[k] + dy * sin[k], -dx * sin[k] + dy * cos[k]
    d = np.clip(_outline(u / half[k], v / across[k], lobes[k]), 0, 1)
    # Edges curl down and darken; a broad leaf shows its midrib.
    shade = 1 - .16 * d - rib * np.exp(-(v / .7) ** 2) * (np.abs(u) < .9 * half[k])
    albedo[hit] = tone[k] * shade[:, None]
    height[hit] = lift * (1 - .4 * d) + .002 * k / count


def _forest_floor(key, rng, size, period, grain, macro, gaussian_filter):
    """Summer forest floors under closed canopy (Ruissalo oak and lime litter with herbs, Nunnavuori needle duff and
    feather moss, wet hollows of moss): sRGB albedo, height (m), AO and roughness."""
    def noise(sigma):
        n = gaussian_filter(rng.normal(0, 1, (size, size)), sigma, mode="wrap")
        return n / max(n.std(), 1e-6)

    height = np.zeros((size, size))
    if key == "forest_floor_broadleaf":
        albedo = np.array([56, 49, 43]) + grain[..., None] * 5
        # Last autumn's litter, closed over the soil: oak (lobed), lime and maple leaves of 5-10 cm, weathered over
        # the winter to grey-brown (Ruissalo summer panoramas: floor saturation 0.02-0.06, lightness 0.25-0.33 in the
        # shade; the fresh tan of autumn read orange, 0.17 against 0.05, in the game's sun).
        _scatter(rng, size, period, 9000, (.05, .1), (.5, .75),
                 [[104, 90, 74], [116, 100, 80], [90, 80, 68], [126, 110, 88], [76, 68, 60], [98, 88, 72]],
                 [.22, .18, .2, .1, .16, .14], (albedo, height), .004, rib=.12, lobed=.4)
        _scatter(rng, size, period, 90, (.12, .4), (.02, .05), [[70, 62, 54], [92, 82, 70]], [.6, .4],
                 (albedo, height), .008)
        # Summer herbs (anemone, sorrel, seedlings) in broad patches over about two fifths of the floor.
        herbs = np.clip((noise(size / 14) + .55 * noise(size / 40) - .3) * 1.2, 0, 1)
        _scatter(rng, size, period, 12000, (.02, .05), (.55, .9),
                 [[70, 94, 46], [82, 104, 52], [60, 82, 40], [96, 112, 60]], [.3, .3, .25, .15],
                 (albedo, height), .012, where=herbs, rib=.08)
        roughness = np.full((size, size), .9)
    elif key == "forest_floor_conifer":
        albedo = np.array([74, 57, 42]) + grain[..., None] * 6
        needles = ([[132, 94, 60], [112, 82, 54], [94, 72, 52], [146, 118, 88], [82, 64, 48]], [.22, .26, .22, .1, .2])
        # Matted needle duff of pine and spruce (3-6 cm), rust to grey, and a few cones.
        _scatter(rng, size, period, 70000, (.03, .06), (.02, .05), *needles, (albedo, height), .003)
        _scatter(rng, size, period, 24, (.04, .07), (.5, .65), [[96, 70, 46], [82, 60, 42]], [.5, .5],
                 (albedo, height), .02, rib=.2)
        # Feather moss and bilberry-green patches of 10-40 cm over about a third of the floor, bumpy and lit from above.
        bumps = noise(1.2)
        clumps = noise(size / 200)
        moss = np.clip((noise(size / 40) + .6 * noise(size / 90) + .25 * clumps - .25) * 1.4, 0, 1)
        green = (np.array([70, 86, 40]) + bumps[..., None] * [8, 10, 4] + clumps[..., None] * [6, 8, 3]
                 + np.clip(bumps - 1.2, 0, None)[..., None] * [28, 24, 10])
        albedo = albedo * (1 - moss[..., None]) + green * moss[..., None]
        height = height * (1 - moss) + moss * (.012 + bumps * .0015 + clumps * .003)
        # Fresh needles lie on the moss too.
        _scatter(rng, size, period, 6000, (.03, .06), (.02, .05), *needles, (albedo, height), .016)
        roughness = np.clip(.9 + .05 * moss, 0, 1)
    else:
        # Wet hollows: a carpet of feather and peat moss clumps with yellow-green tips, a few needles on it.
        bumps, clumps, tufts = noise(.9), noise(size / 220), noise(size / 60)
        albedo = (np.array([64, 84, 36]) + bumps[..., None] * [7, 10, 4] + clumps[..., None] * [9, 11, 5]
                  + tufts[..., None] * [6, 7, 3] + np.clip(bumps + clumps - 1.8, 0, None)[..., None] * [24, 22, 10]
                  - np.clip(-clumps - 1.3, 0, None)[..., None] * [10, 13, 6] + macro[..., None] * [3, 4, 2])
        height = bumps * .0015 + clumps * .004 + tufts * .006
        _scatter(rng, size, period, 3000, (.03, .06), (.02, .05), [[124, 92, 58], [98, 76, 52]], [.5, .5],
                 (albedo, height), height.max() + .002)
        roughness = np.full((size, size), .95)
    relief = (height - height.min()) / max(np.ptp(height), 1e-6)
    ao = .72 + .28 * relief
    return albedo, height, ao, roughness


def _sand(rng, size, period, grain, macro, gaussian_filter):
    """Beach and sandpit sand: fine beige-grey grains, trampled into footprint dimples, compacted darker patches, a
    few pebbles and dry stalks. Kansanpuisto beach in summer panoramas: hue 31-34 deg, saturation 0.09-0.12, sunlit
    lightness 0.44-0.5 (the albedo sits a little under it)."""
    def noise(sigma):
        n = gaussian_filter(rng.normal(0, 1, (size, size)), sigma, mode="wrap")
        return n / max(n.std(), 1e-6)

    # Compacted, damp-ish patches and loose drifts at metre scale.
    patches = np.clip(noise(size / 10) * .6 + noise(size / 30) * .4, -2, 2)
    albedo = (np.array([140, 128, 112]) + patches[..., None] * [6, 6, 5] + grain[..., None] * [7, 7, 6]
              + gaussian_filter(grain, 1.2, mode="wrap")[..., None] * [9, 8, 7])
    height = gaussian_filter(grain, .8, mode="wrap") * .0004 + noise(size / 40) * .004
    # Footprints: soft oval dimples, 10-14 cm, in their thousands on a used beach; their walls catch the light.
    count = 1600
    y, x = rng.random(count) * size, rng.random(count) * size
    r = rng.uniform(.05, .07, count) * size / period
    dip = np.zeros((size, size))
    yy, xx = np.indices((int(2 * r.max()) + 3,) * 2) - (int(r.max()) + 1)
    for cy, cx, rr, angle in zip(y.astype(int), x.astype(int), r, rng.uniform(0, np.pi, count), strict=True):
        u = (xx * np.cos(angle) + yy * np.sin(angle)) / (rr * 1.5)
        v = (-xx * np.sin(angle) + yy * np.cos(angle)) / rr
        np.add.at(dip, ((cy + yy) % size, (cx + xx) % size), np.exp(-2.5 * (u * u + v * v)))
    dip = np.minimum(dip, 1.4)
    height -= dip * .012
    albedo -= dip[..., None] * [7, 7, 6]
    # Pebbles and shell grit, and a few dry stalks blown onto the sand.
    _scatter(rng, size, period, 2600, (.006, .018), (.6, .9),
             [[120, 116, 110], [150, 144, 134], [96, 92, 88], [170, 160, 146]], [.35, .3, .2, .15],
             (albedo, height), .003)
    _scatter(rng, size, period, 160, (.04, .12), (.05, .1), [[150, 132, 96], [122, 108, 80]], [.6, .4],
             (albedo, height), .002)
    ao = .9 + .1 * np.clip(1 - dip, 0, 1)
    roughness = np.clip(.94 + grain * .02, .85, 1)
    return albedo, height, ao, roughness


def _meadow(rng, size, period, grain, macro, gaussian_filter):
    """Unmown summer grass seen from above (Turku's meadows and open areas, July): long blades in green and straw
    tones lying in every direction over a darker thatch, drier patches, and flowers (white, yellow, violet) in
    drifts. sRGB albedo, height (m), AO and roughness."""
    def noise(sigma):
        n = gaussian_filter(rng.normal(0, 1, (size, size)), sigma, mode="wrap")
        return n / max(n.std(), 1e-6)

    dry = np.clip((noise(size / 18) + .5 * noise(size / 50)) * .6, -1, 1)
    albedo = np.array([58, 66, 34]) + grain[..., None] * 5 + dry[..., None] * [8, 5, 2]
    height = np.zeros((size, size))
    green = ([[84, 112, 46], [72, 98, 40], [98, 122, 54], [64, 86, 36], [110, 128, 62]], [.25, .25, .2, .2, .1])
    straw = ([[138, 128, 74], [120, 112, 66], [152, 140, 86], [104, 98, 58]], [.3, .3, .2, .2])
    # Thatch, then two layers of blades (10-30 cm long, 3-6 mm wide), straw-coloured where the patch is dry.
    _scatter(rng, size, period, 30000, (.08, .2), (.015, .03), *straw, (albedo, height), .004)
    wet = np.clip(.55 - .6 * dry, .1, 1)
    _scatter(rng, size, period, 70000, (.1, .3), (.012, .025), *green, (albedo, height), .01, where=wet)
    _scatter(rng, size, period, 45000, (.1, .3), (.012, .025), *straw, (albedo, height), .012, where=1 - wet)
    _scatter(rng, size, period, 15000, (.1, .25), (.012, .025), *green, (albedo, height), .016)
    # Seed heads of timothy, fescue and bent (1-3 cm, pale tan), thicker where the grass has gone to seed.
    _scatter(rng, size, period, 3000, (.015, .035), (.2, .3), [[150, 138, 92], [134, 126, 84], [164, 150, 104]],
             [.4, .35, .25], (albedo, height), .014, where=np.clip(.3 + .7 * dry, 0, 1))
    # Flowers in drifts: ox-eye daisy, buttercup, meadow cranesbill and clover heads of 1-3 cm.
    drifts = np.clip((noise(size / 24) + .5 * noise(size / 70) - .7) * 1.5, 0, 1)
    _scatter(rng, size, period, 2600, (.012, .03), (.8, 1), [[236, 232, 214], [226, 198, 60], [150, 120, 190],
             [204, 140, 170]], [.35, .3, .2, .15], (albedo, height), .03, where=drifts)
    relief = (height - height.min()) / max(np.ptp(height), 1e-6)
    ao = .7 + .3 * relief
    roughness = np.full((size, size), .95)
    return albedo, height, ao, roughness


def _field(rng, size, period, grain, macro, gaussian_filter):
    """A mown hay field in July (Kurala panoramas: hue 45-52, saturation 0.2-0.35, lightness 0.35-0.45): pale straw
    stubble over green regrowth in mower lanes, lying hay wisps, greener damp patches and a few weeds. sRGB albedo,
    height (m), AO and roughness."""
    def noise(sigma):
        n = gaussian_filter(rng.normal(0, 1, (size, size)), sigma, mode="wrap")
        return n / max(n.std(), 1e-6)

    damp = np.clip((noise(size / 14) + .5 * noise(size / 40)) * .5, -1, 1)
    # Mower lanes: four 1 m lanes per repeat, their stubble laid one way, then the other.
    lanes = np.sin(np.arange(size) / size * 2 * np.pi * 4)[:, None] * np.ones((1, size))
    albedo = (np.array([120, 110, 64]) + grain[..., None] * 6 + lanes[..., None] * [7, 6, 3]
              - damp[..., None] * [10, 4, 6])
    height = np.zeros((size, size))
    straw = ([[150, 136, 84], [134, 122, 74], [164, 150, 98], [118, 108, 62]], [.3, .3, .2, .2])
    regrowth = ([[96, 104, 50], [84, 94, 44], [108, 112, 58]], [.4, .35, .25])
    # Green regrowth between the stubble, then cut stalks (3-8 cm, upright, seen end-on as short strokes).
    _scatter(rng, size, period, 30000, (.03, .08), (.08, .15), *regrowth, (albedo, height), .006,
             where=np.clip(.45 + .5 * damp, .1, 1))
    _scatter(rng, size, period, 90000, (.02, .06), (.12, .22), *straw, (albedo, height), .01)
    # Hay wisps the rake left (15-40 cm, lying), and a few weeds: clover, plantain, thistle rosettes.
    _scatter(rng, size, period, 9000, (.15, .4), (.01, .02), [[176, 160, 108], [158, 144, 94]], [.6, .4],
             (albedo, height), .014)
    _scatter(rng, size, period, 900, (.04, .1), (.6, 1), [[70, 92, 40], [84, 104, 46], [214, 206, 190]],
             [.45, .4, .15], (albedo, height), .012, where=np.clip(.3 + .7 * damp, 0, 1))
    relief = (height - height.min()) / max(np.ptp(height), 1e-6)
    ao = .75 + .25 * relief
    roughness = np.full((size, size), .96)
    return albedo, height, ao, roughness


def maps(key, size=None):
    """Albedo, tangent normal and ORM. Fine grain is mipmapped by the packager."""
    from scipy.ndimage import gaussian_filter, distance_transform_edt

    size = size or (2048 if key == "fieldstone" else 1024 if key in ("granite_rock", "grass_meadow", "sand", "field_hay",
                                                                   *FOREST_FLOORS) else 512)
    period = PERIODS.get(key, 2)
    rng = np.random.default_rng(4817)
    grain = rng.normal(0, 1, (size, size))
    macro = gaussian_filter(grain, size / 32, mode="wrap")
    macro /= max(macro.std(), 1e-6)
    if key == "asphalt_red":
        albedo = np.array([122, 65, 52]) + grain[..., None] * 7 + macro[..., None] * 2
        height = gaussian_filter(grain, .6, mode="wrap") * .0005
        ao = np.ones((size, size))
        roughness = np.clip(.9 + grain * .02, .75, 1)
    elif key == "track_rubber":
        # Poured polyurethane athletics surface: terracotta with fine embedded granules.
        speck = rng.random((size, size)) < .04
        albedo = np.array([164, 74, 58]) + grain[..., None] * 6 + macro[..., None] * 3 - speck[..., None] * 22
        height = gaussian_filter(grain, .5, mode="wrap") * .0004
        ao = np.ones((size, size))
        roughness = np.clip(.86 + grain * .03, .7, 1)
    elif key in ("pitch_grass_light", "pitch_grass_dark"):
        # Short mown turf; the two keys alternate as stripes, lit and shaded by the mower's lay.
        blades = gaussian_filter(grain, .7, mode="wrap")
        blades /= max(blades.std(), 1e-6)
        tone = np.array([84, 122, 50]) if key == "pitch_grass_light" else np.array([66, 101, 41])
        albedo = tone + blades[..., None] * [6, 9, 4] + macro[..., None] * [3, 4, 2]
        height = blades * .0008
        ao = .92 + .08 * np.clip(blades, -1, 1)
        roughness = np.full((size, size), .93)
    elif key in ("brick_paving", "concrete_pavers"):
        # Running bond with narrow sand-filled joints: 200 x 100 mm red-brown pavers
        # (Turku register Betonikivi, Maatiili), or 200 x 125 mm warm grey concrete pavers,
        # what the register's Betonilaatta sidewalks are in street panoramas (aged concrete
        # reads red/blue 1.12-1.14 beside neutral asphalt at Vähäheikkiläntie and Kokkakatu).
        depth, tone, joint, spread, patch = ((.1, [160, 134, 122], [145, 132, 114], 7, 0) if key == "brick_paving"
                                             else (.125, [172, 162, 152], [136, 128, 118], 6, 3))
        y, x = (np.indices((size, size)) + .5) * (period / size)
        row = np.floor(y / depth).astype(int)
        bx = (x + (row % 2) * .1) % .2
        by = y % depth
        edge = np.minimum(np.minimum(bx, .2 - bx), np.minimum(by, depth - by))
        crown = np.clip((edge - .002) / .005, 0, 1)
        cell = (np.floor((x + (row % 2) * .1) / .2).astype(int) % 10)
        tones = rng.normal(0, spread, (20, 10))[row % 20, cell]
        brick = np.array(tone) + tones[..., None] + grain[..., None] * 3
        albedo = np.array(joint) + (brick - joint) * crown[..., None] + macro[..., None] * patch
        height = crown * .003 + gaussian_filter(grain, .6, mode="wrap") * .0002
        ao = .86 + .14 * crown
        roughness = np.full((size, size), .88)
    elif key == "fieldstone":
        from scipy.spatial import cKDTree

        # Toroidal dart throwing gives mixed stone sizes without the diagonal
        # rows of a jittered grid. Minimum spacing excludes tiny sliver stones.
        sites = []
        for _ in range(50000):
            point = rng.random(2)
            # Broad periodic density patches leave mixed small and large stones,
            # rather than a uniformly spaced scatter across every metre.
            patch = .5 + .5 * np.sin(2 * np.pi * (point[0] + point[1])) * np.sin(4 * np.pi * point[1])
            if rng.random() > .28 + .72 * patch:
                continue
            delta = abs(np.asarray(sites).reshape(-1, 2) - point)
            delta = np.minimum(delta, 1 - delta)
            if len(delta) and np.min(np.linalg.norm(delta, axis=1)) < .008:
                continue
            sites.append(point)
            if len(sites) == 4400:
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
                / np.maximum(separation, 1e-6)).reshape(size, size) * (period / 2)
        # Open each cell by 20 mm, then restore 17 mm: round worn stone shoulders and
        # leave sandy joints. Tiling the mask gives true periodic distances.
        core = edge >= .020
        outside = distance_transform_edt(~np.tile(core, (3, 3)))[size:2*size, size:2*size] * (period / size)
        footprint = outside <= .017
        inner = distance_transform_edt(np.tile(footprint, (3, 3)))[size:2*size, size:2*size] * (period / size)
        crown = np.tanh(inner / .014)
        coverage = np.clip((.019 - outside) / .004, 0, 1)
        # Finnish fieldstone mixes grey rock, pale granite, feldspar pink and
        # occasional dark stones; sandy joints stay lighter than deep cavities.
        palette = np.array([[85, 89, 86], [155, 154, 146], [121, 126, 122],
                            [139, 122, 114], [139, 135, 119]])
        colours = palette[rng.choice(len(palette), len(sites), p=[.08, .17, .60, .07, .08])]
        colours = colours + rng.normal(0, 5, (len(sites), 1))
        rock = colours[nearest].reshape(size, size, 3)
        dirt = np.array([126, 124, 111])
        albedo = dirt + (rock - dirt) * coverage[..., None] + grain[..., None] * 5 + macro[..., None] * 2
        relief = rng.uniform(.004, .007, len(sites))[nearest].reshape(size, size)
        height = crown * relief + gaussian_filter(grain, .8, mode="wrap") * .0008
        ao = .9 + .1 * crown
        roughness = .94 - .13 * crown
    elif key == "granite_rock":
        # Glacially polished Finnish granite: pink feldspar, grey quartz and dark
        # biotite in a 2-6 mm grain, broad weathering tones, a few long joints,
        # grey-green lichen rosettes and moss darkening the joints.
        y, x = np.indices((size, size))
        fine = gaussian_filter(grain, .8, mode="wrap")
        crystals = gaussian_filter(rng.normal(0, 1, (size, size)), 1.2, mode="wrap")
        crystals /= max(crystals.std(), 1e-6)
        broad = gaussian_filter(rng.normal(0, 1, (size, size)), size / 10, mode="wrap")
        broad /= max(broad.std(), 1e-6)
        feldspar, quartz, biotite = np.array([168, 140, 128]), np.array([158, 156, 150]), np.array([64, 63, 62])
        pink = np.clip(.5 + .6 * crystals, 0, 1)[..., None]
        albedo = quartz + (feldspar - quartz) * pink
        dark = (gaussian_filter(rng.normal(0, 1, (size, size)), .9, mode="wrap") > .55)[..., None]
        albedo = np.where(dark, .6 * albedo + .4 * biotite, albedo) + fine[..., None] * 10
        albedo = albedo * (1 + .07 * broad[..., None]) - 6 * np.clip(macro, 0, None)[..., None]
        # Joints: a few long, irregular cracks. Periodic noise warps each straight
        # line at two scales so it wanders and kinks like a real joint.
        warp = [gaussian_filter(rng.normal(0, 1, (size, size)), sigma, mode="wrap") for sigma in (size / 12, size / 60)]
        warp = [w / max(w.std(), 1e-6) for w in warp]
        joints = np.zeros((size, size))
        for k in range(4):
            angle = rng.uniform(0, np.pi)
            u = (np.cos(angle) * x + np.sin(angle) * y) / size
            u = u + .035 * warp[0] * np.cos(k) + .006 * warp[1] * np.sin(k) + rng.random()
            width = rng.uniform(.0025, .006)
            line = np.abs((u % 1) - .5) * period
            joints = np.maximum(joints, np.clip(1 - line / width, 0, 1) ** .7)
        moss = gaussian_filter(joints, 6, mode="wrap")
        moss = np.clip(moss / max(moss.max(), 1e-6) * 1.4 - .15, 0, 1)
        spots = [gaussian_filter(rng.normal(0, 1, (size, size)), sigma, mode="wrap") for sigma in (size / 70, size / 220)]
        lichen = sum(w / max(w.std(), 1e-6) * f for w, f in zip(spots, (1, .6)))
        lichen = np.clip((lichen - 1.5) * 1.2, 0, 1) * np.clip(.6 + .4 * broad, 0, 1)
        albedo = albedo * (1 - lichen[..., None]) + np.array([150, 156, 136]) * lichen[..., None]
        albedo = albedo * (1 - .8 * moss[..., None]) + np.array([66, 78, 42]) * .8 * moss[..., None]
        albedo = albedo * (1 - .7 * joints[..., None]) + np.array([46, 44, 42]) * .7 * joints[..., None]
        # Rounded glacial swells, sunk joints and crystal-scale grain (metres).
        height = broad * .004 - joints * .006 + crystals * .0003 + fine * .0002
        ao = 1 - .45 * joints - .1 * moss
        roughness = np.clip(.72 + .12 * lichen + .15 * moss + crystals * .02, .6, 1)
    elif key in FOREST_FLOORS:
        albedo, height, ao, roughness = _forest_floor(key, rng, size, period, grain, macro, gaussian_filter)
    elif key == "grass_meadow":
        albedo, height, ao, roughness = _meadow(rng, size, period, grain, macro, gaussian_filter)
    elif key == "sand":
        albedo, height, ao, roughness = _sand(rng, size, period, grain, macro, gaussian_filter)
    elif key == "field_hay":
        albedo, height, ao, roughness = _field(rng, size, period, grain, macro, gaussian_filter)
    else:
        raise ValueError(f"Unknown ground finish: {key}")

    # Periodic central differences in physical metres; tangent UV v points north.
    dx = (np.roll(height, -1, 1) - np.roll(height, 1, 1)) / (2 * period / size)
    dy = (np.roll(height, -1, 0) - np.roll(height, 1, 0)) / (2 * period / size)
    normal = np.stack([-dx, dy, np.ones_like(height)], axis=-1)
    normal /= np.linalg.norm(normal, axis=-1, keepdims=True)
    orm = np.stack([ao, roughness, np.zeros_like(height)], axis=-1)
    byte = lambda values: np.rint(np.clip(values, 0, 255)).astype(np.uint8)
    return {"albedo": byte(albedo), "normal": byte((normal + 1) * 127.5),
            "orm": byte(orm * 255)}
