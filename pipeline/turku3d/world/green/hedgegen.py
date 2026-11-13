"""Hedges and shrub masses as closed volumes with a leafy shell.

A hedge is its measured line swept by a cross-section of its measured width and height: boxy with a level top
when clipped, rounder and uneven when loose. The body is a closed solid (sides, top, underside, both ends) in the
material family `hedge`; small leaf cards from the nature library's foliage atlas stand out of its surface, so its
edge against the sky is leaves, not a ruled line. A shrub mass is the same for an outline instead of a line.

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
        "surface": {"p": positions[visible], "n": normals[visible], "shade": shade[visible],
                    "area": float(along[-1] * around[-1] * np.mean(width + height) / 2 + 1e-6)},
    }


def mass(ring: np.ndarray, base_y: float, height: float, rng) -> dict:
    """A shrub mass over an outline (engine x, z ring, counter-clockwise seen from above): a closed dome."""
    turn = float(np.sum(ring[:, 0] * np.roll(ring[:, 1], -1) - np.roll(ring[:, 0], -1) * ring[:, 1]))
    ring = ring if turn > 0 else ring[::-1]
    centre = ring.mean(axis=0)
    levels = ((1.0, -SINK), (1.0, 0.55 * height), (0.86, 0.85 * height), (0.6, height))
    n = len(ring)
    rings = []
    for scale, y in levels:
        wobble = 1.0 + (0.06 * rng.normal(size=(n, 1)) if y > 0 else 0.0)
        xz = centre + (ring - centre) * scale * wobble
        rings.append(np.stack([xz[:, 0], np.full(n, base_y + y), xz[:, 1]], axis=1))
    positions = np.concatenate(rings)
    faces = []
    for level in range(len(levels) - 1):
        a = level * n + np.arange(n)
        b = level * n + (np.arange(n) + 1) % n
        faces.append(np.stack([a, a + n, b, b, a + n, b + n], axis=-1).reshape(-1, 3))
    top = earcut([rings[-1][:, [0, 2]]]) + (len(levels) - 1) * n
    bottom = earcut([rings[0][:, [0, 2]]])[:, ::-1]
    middle = np.array([centre[0], base_y + 0.4 * height, centre[1]])
    normals = treegen.unit((positions - middle) * np.array([1.0, 1.6, 1.0]))
    shade = np.concatenate([np.full(n, 0.5), np.full(n, 0.75), np.full(n, 0.92), np.full(n, 1.0)])
    uv = positions[:, [0, 2]]
    visible = np.arange(len(positions)) >= n
    perimeter = float(np.linalg.norm(np.diff(np.vstack([ring, ring[:1]]), axis=0), axis=1).sum())
    area = 0.5 * abs(float(np.sum(ring[:, 0] * np.roll(ring[:, 1], -1) - np.roll(ring[:, 0], -1) * ring[:, 1])))
    triangles = np.concatenate([*faces, top[:, ::-1], bottom[:, ::-1]])
    return {"positions": positions, "normals": normals, "uvs": uv, "colors": shade, "triangles": triangles,
            "surface": {"p": positions[visible], "n": normals[visible], "shade": shade[visible], "area": perimeter * height + area}}


def shell(surface: dict, form: str, evergreen: bool, rng, far: bool = False) -> dict | None:
    """Leaf cards standing out of a body's surface; `far` is the distant level: a fifth of them, larger."""
    density = (15.0 if form == "clipped" else 9.0) * (0.2 if far else 1.0)
    count = int(np.clip(surface["area"] * density, 6, 6000))
    source = len(surface["p"])
    if source < 3:
        return None
    # Samples between neighbouring surface vertices, so the cards do not line up on the sweep's rings.
    a = rng.integers(0, source, count)
    b = np.clip(a + rng.integers(-1, 2, count), 0, source - 1)
    w = rng.random(count)[:, None]
    p = surface["p"][a] * (1 - w) + surface["p"][b] * w
    normal = treegen.unit(surface["n"][a] * (1 - w) + surface["n"][b] * w + rng.normal(0, 0.15, (count, 3)))
    shade = surface["shade"][a] * (1 - w[:, 0]) + surface["shade"][b] * w[:, 0]
    size = (rng.uniform(0.28, 0.4, count) if form == "clipped" else rng.uniform(0.42, 0.6, count)) * (1.7 if far else 1.0)
    lift = 0.18 if form == "clipped" else 0.55
    up = treegen.unit(normal * lift + UP * rng.uniform(0.3, 1.0, count)[:, None] + rng.normal(0, 0.45, (count, 3)))
    facing = normal - up * np.sum(normal * up, axis=1, keepdims=True)
    facing = treegen.unit(np.where(np.linalg.norm(facing, axis=1, keepdims=True) < 0.1, treegen.any_perp(up), facing))
    side = treegen.unit(np.cross(up, facing))
    start = p - normal * (0.1 * size[:, None]) - up * (0.35 * size[:, None])
    grid = np.array([[-0.5, 0.0], [0.5, 0.0], [-0.5, 1.0], [0.5, 1.0]])
    positions = start[:, None] + side[:, None] * (grid[:, 0][None, :, None] * size[:, None, None]) + up[:, None] * (grid[:, 1][None, :, None] * size[:, None, None])
    cell = "juniper" if evergreen else "hedge"
    u0, u1, v0, v1 = treegen.cell_uv(cell)
    uv = np.tile(np.stack([u0 + (u1 - u0) * (grid[:, 0] + 0.5), v1 + (v0 - v1) * grid[:, 1]], axis=1), (count, 1))
    # COLOR_0 of the foliage family: ambient occlusion, leaf brightness, leaf warmth (treegen's convention).
    vary = rng.uniform(-1.0, 1.0, (count, 2))
    tone = (0.85, -0.03) if evergreen else (0.95, 0.0)
    colors = np.repeat(np.stack([0.35 + 0.65 * shade, tone[0] * (1 + treegen.CARD_TONE[0] * vary[:, 0]),
                                 1 + tone[1] + treegen.CARD_TONE[1] * vary[:, 1]], axis=1), 4, axis=0)
    wind = np.stack([np.full(count * 4, 0.04), np.tile(grid[:, 1] * 0.5, count), np.repeat(rng.random(count), 4), np.ones(count * 4)], axis=1)
    faces = (np.array([[0, 1, 2], [1, 3, 2]])[None] + (np.arange(count) * 4)[:, None, None]).reshape(-1, 3)
    return {"positions": positions.reshape(-1, 3), "normals": np.repeat(treegen.unit(normal * 0.8 + UP * 0.2), 4, axis=0), "uvs": uv,
            "colors": colors, "wind": wind, "triangles": faces}


def body_material(evergreen: bool) -> Material:
    colour = (0.028, 0.06, 0.03) if evergreen else (0.045, 0.085, 0.028)
    return Material("hedge", {}, colour, 0.95)


def leaf_material(evergreen: bool) -> Material:
    return Material("foliage", {"atlas": "nature", "cell": "juniper" if evergreen else "hedge", "uv": "atlas"},
                    (0.04, 0.09, 0.04) if evergreen else (0.07, 0.13, 0.035), 0.92, double_sided=True, surface="masked")
