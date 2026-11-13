"""Hedges and shrub masses as closed volumes with a leafy shell.

A hedge is its measured line swept by a cross-section of its measured width and height: boxy with a level top
when clipped, rounder and uneven when loose. The body is a closed solid (sides, top, underside, both ends) in the
material family `hedge`, a dark tone of the leaf: the depth between the leaves. Leaf sprays from the nature
library's foliage atlas cover it like shingles: on the sides they lie along the surface, lifted a little at their
upper edge, several deep; on the top they stand, so its edge against the sky is leaves, not a ruled line. A shrub
mass is the same for an outline instead of a line.

Coordinates in: engine metres relative to the entity origin, y up. TEXCOORD_0 of the body is metres (along, around).
"""

from __future__ import annotations

import numpy as np

from ..api import Material
from ..geometry import earcut
from . import treegen

UP = np.array([0.0, 1.0, 0.0])
STEP = 0.5  # metres between cross-sections
PROFILE = 9  # points on the visible arc of a cross-section
SINK = 0.12  # the body goes this far into the ground, so no slope opens a gap under it


def outward(positions: np.ndarray, triangles: np.ndarray) -> np.ndarray:
    """The triangles of a closed mesh wound so that its faces look out (positive volume)."""
    a, b, c = positions[triangles[:, 0]], positions[triangles[:, 1]], positions[triangles[:, 2]]
    volume = float(np.einsum("ij,ij->i", a, np.cross(b, c)).sum())
    return triangles if volume >= 0 else triangles[:, ::-1]


def resample(line: np.ndarray, values: list[np.ndarray], step: float = STEP):
    seg = np.diff(line, axis=0)
    length = np.linalg.norm(seg, axis=1)
    cumulative = np.concatenate([[0.0], np.cumsum(length)])
    total = cumulative[-1]
    count = max(2, int(round(total / step)) + 1)
    s = np.linspace(0.0, total, count)
    points = np.stack([np.interp(s, cumulative, line[:, k]) for k in range(line.shape[1])], axis=1)
    return points, [np.interp(s, cumulative, v) for v in values], s


def profile(form: str) -> tuple[np.ndarray, np.ndarray]:
    """Unit cross-section (across in -0.5..0.5, up in 0..1), closed: the arc from left to right, then the underside."""
    power = 7.0 if form == "clipped" else 2.6
    theta = np.linspace(np.pi, 0.0, PROFILE)
    across = 0.5 * np.sign(np.cos(theta)) * np.abs(np.cos(theta)) ** (2 / power)
    up = 0.3 + 0.7 * np.abs(np.sin(theta)) ** (2 / power)
    foot = 0.46 if form == "clipped" else 0.4
    return np.concatenate([[-foot], across, [foot]]), np.concatenate([[0.0], up, [0.0]])


def sweep(points: np.ndarray, width: np.ndarray, height: np.ndarray, form: str, rng) -> dict:
    """The closed body of a hedge along engine points (n, 3): positions, normals, uvs, colors, triangles and the
    surface samples for the leaf shell."""
    n = len(points)
    tangent = np.gradient(points, axis=0)
    tangent[:, 1] = 0.0
    tangent = treegen.unit(tangent)
    side = np.stack([-tangent[:, 2], np.zeros(n), tangent[:, 0]], axis=1)
    across, up = profile(form)
    k = len(across)
    if form != "clipped":
        # An uneven outline: slow noise along the hedge, different per profile point.
        from scipy import ndimage

        wobble = ndimage.gaussian_filter1d(rng.normal(0.0, 1.0, (n, k)), 1.2, axis=0, mode="nearest")
        swell = 1.0 + 0.3 * wobble
    else:
        swell = np.ones((n, k))
    s = across[None, :] * width[:, None] * swell
    t = up[None, :] * height[:, None] * np.where(up[None, :] > 0, swell, 1.0)
    positions = points[:, None, :] + side[:, None, :] * s[..., None] + UP * (t[..., None] - np.where(up[None, :, None] > 0, 0.0, SINK))
    # Outward normals from the section's own shape.
    centre = points[:, None, :] + UP * (0.45 * height[:, None, None])
    normals = treegen.unit((positions - centre) * np.array([1.0, 0.6, 1.0]))
    around = np.concatenate([[0.0], np.cumsum(np.hypot(np.diff(across), np.diff(up)))])
    along = np.concatenate([[0.0], np.cumsum(np.linalg.norm(np.diff(points, axis=0), axis=1))])
    uv = np.stack([np.broadcast_to(along[:, None], (n, k)), np.broadcast_to(around[None, :] * float(np.mean(width + height)), (n, k))], axis=-1)
    shade = np.broadcast_to(0.5 + 0.5 * np.clip(up, 0, 1)[None, :] ** 0.7, (n, k))
    ring = np.arange(n - 1)[:, None] * k + np.arange(k)[None, :]
    nxt = np.arange(n - 1)[:, None] * k + (np.arange(k)[None, :] + 1) % k
    faces = np.stack([ring, ring + k, nxt, nxt, ring + k, nxt + k], axis=-1).reshape(-1, 3)
    flat = positions.reshape(-1, 3)
    # End caps: fans round each end's centre.
    ends = np.array([centre[0, 0], centre[-1, 0]])
    first, last = np.arange(k), (n - 1) * k + np.arange(k)
    base = n * k
    caps = np.concatenate([np.stack([np.full(k, base), first, np.roll(first, -1)], axis=1),
                           np.stack([np.full(k, base + 1), np.roll(last, -1), last], axis=1)])
    cap_normals = np.array([-tangent[0], tangent[-1]])
    visible = (up > 0)[None, :] & np.ones((n, 1), bool)
    every = np.concatenate([flat, ends])
    return {
        "positions": every, "normals": np.concatenate([normals.reshape(-1, 3), cap_normals]),
        "uvs": np.concatenate([uv.reshape(-1, 2), np.zeros((2, 2))]), "colors": np.concatenate([shade.reshape(-1), [0.7, 0.7]]),
        "triangles": outward(every - every.mean(axis=0), np.concatenate([faces, caps])),
        "surface": {"p": positions[visible], "n": normals[visible], "shade": shade[visible], "grid": (n, int((up > 0).sum())), "closed": False,
                    "area": float(along[-1] * around[-1] * np.mean(width + height) / 2 + 1e-6)},
    }


def densified(ring: np.ndarray, values: np.ndarray, step: float = 0.8) -> tuple[np.ndarray, np.ndarray]:
    """The closed ring with a vertex at least every `step` metres, and `values` per vertex carried along."""
    points, carried = [], []
    for k in range(len(ring)):
        a, b = ring[k], ring[(k + 1) % len(ring)]
        parts = max(1, int(np.ceil(np.linalg.norm(b - a) / step)))
        t = np.arange(parts)[:, None] / parts
        points.append(a + (b - a) * t)
        carried.append(values[k] + (values[(k + 1) % len(ring)] - values[k]) * t[:, 0])
    return np.concatenate(points), np.concatenate(carried)


def inset(ring: np.ndarray, distance: np.ndarray) -> np.ndarray:
    """A counter-clockwise ring moved inward by `distance` per vertex, along the bisector of its two edges."""
    back, ahead = treegen.unit(ring - np.roll(ring, 1, axis=0)), treegen.unit(np.roll(ring, -1, axis=0) - ring)
    inward = treegen.unit(np.stack([-back[:, 1], back[:, 0]], axis=1) + np.stack([-ahead[:, 1], ahead[:, 0]], axis=1))
    sharp = np.clip(-back[:, 1] * inward[:, 0] + back[:, 0] * inward[:, 1], 0.5, 1.0)
    return ring + inward * (distance / sharp)[:, None]


def mass(ring: np.ndarray, base_y, height: float, rng, cut=None) -> dict:
    """A shrub mass over an outline (engine x, z ring; `base_y` the ground under it, one height or one per vertex):
    a closed dome whose sides stand on the outline and lean in towards a flat top, by the same distance all round,
    so that a long bed is a bank and not a tent. `cut` marks the vertices where the outline was cut and the mass
    goes on in a neighbour: the side stands upright there. Sprays cover the sides and the top."""
    turn = float(np.sum(ring[:, 0] * np.roll(ring[:, 1], -1) - np.roll(ring[:, 0], -1) * ring[:, 1]))
    base = np.broadcast_to(np.asarray(base_y, float), (len(ring),))
    cut = np.zeros(len(ring)) if cut is None else np.asarray(cut, float)
    ring, base, cut = (ring, base, cut) if turn > 0 else (ring[::-1], base[::-1], cut[::-1])
    area = 0.5 * abs(turn)
    perimeter = float(np.linalg.norm(ring - np.roll(ring, 1, axis=0), axis=1).sum())
    closed = densified(ring, cut)[1] < 0.999  # a vertex between two cut ones lies on the cut
    ring, base = densified(ring, base)
    n = len(ring)
    lean = min(0.6, 0.45 * height, 0.5 * area / max(perimeter, 1e-6))  # the top ring's inset: half the narrowest half-width at most
    levels = ((0.0, -SINK), (0.0, 0.55 * height), (0.4, 0.85 * height), (1.0, height))
    rings = []
    for share, y in levels:
        wobble = rng.normal(0.0, 0.06, n) if y > 0 else 0.0
        xz = inset(ring, (lean * share + wobble) * closed)
        rings.append(np.stack([xz[:, 0], base + y, xz[:, 1]], axis=1))
    positions = np.concatenate(rings)
    faces = []
    for level in range(len(levels) - 1):
        a = level * n + np.arange(n)
        b = level * n + (np.arange(n) + 1) % n
        faces.append(np.stack([a, a + n, b, b, a + n, b + n], axis=-1).reshape(-1, 3))
    top = earcut([rings[-1][:, [0, 2]]]) + (len(levels) - 1) * n
    bottom = earcut([rings[0][:, [0, 2]]])[:, ::-1]
    out = treegen.unit(np.stack([-(np.roll(ring, -1, axis=0) - np.roll(ring, 1, axis=0))[:, 1], (np.roll(ring, -1, axis=0) - np.roll(ring, 1, axis=0))[:, 0]], axis=1)) * -1.0
    rise = (0.0, 0.25, 0.7, 1.6)  # the normal turns up towards the top
    normals = treegen.unit(np.concatenate([np.stack([out[:, 0], np.full(n, up), out[:, 1]], axis=1) for up in rise]))
    shade = np.concatenate([np.full(n, 0.5), np.full(n, 0.75), np.full(n, 0.92), np.full(n, 1.0)])
    uv = positions[:, [0, 2]]
    triangles = np.concatenate([*faces, top[:, ::-1], bottom[:, ::-1]])
    return {"positions": positions, "normals": normals, "uvs": uv, "colors": shade, "triangles": triangles,
            "surface": {"p": positions, "n": normals, "shade": shade, "grid": (len(levels), n), "closed": True, "area": perimeter * height * 1.15,
                        "top": positions[top]}}


DENSITY = {"clipped": 26.0, "loose": 20.0}  # leaf sprays a square metre of surface
SPRAY = {"clipped": (0.26, 0.36), "loose": (0.4, 0.58)}  # metres across a spray
TILT = {"clipped": (6.0, 20.0), "loose": (14.0, 40.0)}  # degrees a spray on a side is lifted off the surface
EVERGREEN = 1.6  # times as many sprays on an evergreen: a frond is narrower than a leafy spray
TOP = 1.4  # times as many on a flat top, which is looked down on
FAR = (0.15, 2.3)  # the distant level: this share of the sprays, this much larger


def shell(surface: dict, form: str, evergreen: bool, rng, far: bool = False) -> dict | None:
    """Leaf sprays over a body's surface. `surface` is a grid of points (rows along the hedge or up the mass,
    columns round its section) with normals; the sprays are spread evenly between the grid's points."""
    rows, cols = surface["grid"]
    if rows < 2 or cols < 2:
        return None
    count = int(np.clip(surface["area"] * DENSITY[form] * (EVERGREEN if evergreen else 1.0) * (FAR[0] if far else 1.0), 6, 16000))
    grid_p, grid_n, grid_s = surface["p"].reshape(rows, cols, 3), surface["n"].reshape(rows, cols, 3), surface["shade"].reshape(rows, cols)
    u = rng.random(count) * (rows - 1)
    v = rng.random(count) * (cols if surface["closed"] else cols - 1)
    r0, c0 = np.minimum(u.astype(int), rows - 2), v.astype(int) % cols
    c1 = (c0 + 1) % cols if surface["closed"] else np.minimum(c0 + 1, cols - 1)
    fu, fv = (u - r0)[:, None], (v - np.floor(v))[:, None]

    def at(grid):
        g = grid[..., None] if grid.ndim == 2 else grid
        return (g[r0, c0] * (1 - fu) * (1 - fv) + g[r0, c1] * (1 - fu) * fv + g[r0 + 1, c0] * fu * (1 - fv) + g[r0 + 1, c1] * fu * fv)

    p, normal, shade = at(grid_p), treegen.unit(at(grid_n)), at(grid_s)[:, 0]
    if "top" in surface and len(surface["top"]):
        # The flat top of a mass, by area: sprays standing on it.
        a, b, c = surface["top"][:, 0], surface["top"][:, 1], surface["top"][:, 2]
        areas = 0.5 * np.linalg.norm(np.cross(b - a, c - a), axis=1)
        more = int(min(areas.sum() * TOP * DENSITY[form] * (EVERGREEN if evergreen else 1.0) * (FAR[0] if far else 1.0), 24000))
        if more and areas.sum() > 0:
            pick = rng.choice(len(areas), more, p=areas / areas.sum())
            s, t = rng.random(more), rng.random(more)
            s, t = np.where(s + t > 1, 1 - s, s), np.where(s + t > 1, 1 - t, t)
            p = np.concatenate([p, a[pick] + (b[pick] - a[pick]) * s[:, None] + (c[pick] - a[pick]) * t[:, None]])
            normal = np.concatenate([normal, np.broadcast_to(UP, (more, 3))])
            shade = np.concatenate([shade, np.ones(more)])
            count += more
    size = rng.uniform(*SPRAY[form], count) * (FAR[1] if far else 1.0)
    # On a side a spray climbs the surface; on the top it has no way up and stands in any direction.
    climb = UP - normal * normal[:, 1:2]
    top = np.linalg.norm(climb, axis=1) < 0.45
    around = treegen.any_perp(normal)
    turn = rng.uniform(0, 2 * np.pi, count)[:, None]
    climb = treegen.unit(np.where(top[:, None], around * np.cos(turn) + np.cross(normal, around) * np.sin(turn), climb))
    tilt = np.radians(np.where(top, rng.uniform(15.0, 60.0, count), rng.uniform(*TILT[form], count)))[:, None]
    up = treegen.unit(climb * np.cos(tilt) + normal * np.sin(tilt) + rng.normal(0, 0.12, (count, 3)))
    side = treegen.unit(np.cross(up, normal))
    out = rng.uniform(0.0, 0.1 if form == "loose" else 0.03, count)[:, None]
    start = p + normal * (out - 0.03) - up * (0.3 * size[:, None])
    grid = np.array([[-0.5, 0.0], [0.5, 0.0], [-0.5, 1.0], [0.5, 1.0]])
    positions = start[:, None] + side[:, None] * (grid[:, 0][None, :, None] * size[:, None, None]) + up[:, None] * (grid[:, 1][None, :, None] * size[:, None, None])
    cell, tone = leaf(form, evergreen)
    u0, u1, v0, v1 = treegen.cell_uv(cell)
    uv = np.tile(np.stack([u0 + (u1 - u0) * (grid[:, 0] + 0.5), v1 + (v0 - v1) * grid[:, 1]], axis=1), (count, 1))
    # COLOR_0 of the foliage family: ambient occlusion, leaf brightness, leaf warmth (treegen's convention).
    vary = rng.uniform(-1.0, 1.0, (count, 2))
    colors = np.repeat(np.stack([0.45 + 0.55 * shade, tone[0] * (1 + 1.5 * treegen.CARD_TONE[0] * vary[:, 0]),
                                 1 + tone[1] + treegen.CARD_TONE[1] * vary[:, 1]], axis=1), 4, axis=0)
    wind = np.stack([np.full(count * 4, 0.04), np.tile(grid[:, 1] * 0.5, count), np.repeat(rng.random(count), 4), np.ones(count * 4)], axis=1)
    faces = (np.array([[0, 1, 2], [1, 3, 2]])[None] + (np.arange(count) * 4)[:, None, None]).reshape(-1, 3)
    return {"positions": positions.reshape(-1, 3), "normals": np.repeat(treegen.unit(normal * 0.8 + UP * 0.2), 4, axis=0), "uvs": uv,
            "colors": colors, "wind": wind, "triangles": faces}


def leaf(form: str, evergreen: bool) -> tuple[str, tuple[float, float]]:
    """(atlas cell, (brightness, warmth)) of a hedge's sprays. The atlas's own hedge leaf and its juniper fill a
    fifth of their card and leave the body bare. A leafy hedge takes the lilac's spray (half its card is leaf), an
    evergreen one the spruce frond, lightened and warmed to a thuja's green and laid closer."""
    return ("spruce", (1.3, 0.07)) if evergreen else ("lilac", (0.95, 0.0))


def body_material(evergreen: bool) -> Material:
    colour = (0.006, 0.012, 0.006) if evergreen else (0.008, 0.017, 0.005)  # far darker than the leaf: the depth between the sprays
    return Material("hedge", {}, colour, 0.95)


def leaf_material(evergreen: bool) -> Material:
    return Material("foliage", {"atlas": "nature", "uv": "atlas"},
                    (0.04, 0.09, 0.04) if evergreen else (0.07, 0.13, 0.035), 0.92, double_sided=True, surface="masked")
