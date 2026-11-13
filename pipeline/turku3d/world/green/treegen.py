"""Procedural trees and shrubs grown to a measured size: a skeleton, closed bark solids and leaf cards.

A tree is generated from its **generating parameters** alone (`Shape`: family, height, crown radius, crown base,
age class, seed), so the same tree can be regenerated in another representation (leaf cards today, leaf geometry or
a cluster hierarchy later). The skeleton method follows the game's nature library (game/scripts/nature/trees.mjs):
decurrent broadleaves with scaffold branches, branchlets and twigs; excurrent conifers with whorls; multi-stem
shrubs. Here the crown envelope, the number of scaffolds and the number of leaf cards come from the measured crown
instead of a fixed species model, and everything is vectorised: a tree takes tens of milliseconds.

Output conventions (units metres, +Y up, trunk base at the origin):
- bark: one closed tube per branch (capped at both ends), material family `bark`, TEXCOORD_0 in the nature
  library's bark column atlas (u around the branch inside the species' slot, v in metres along it);
- leaves: cards on the nature library's foliage atlas, material family `foliage`, double sided, normals bent to
  the crown's radial direction so the canopy shades as a volume, COLOR_0 = (crown-depth ambient occlusion, leaf
  brightness, leaf warmth): the last two multiply the leaf albedo (1 = the atlas colour; warmth above 1 is
  yellower, below 1 bluer) and carry the family's tone and a variation from card to card;
- `_WIND` (sway, flutter, phase, is-leaf) per vertex, as the library's materials expect.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np

from ..api import Material, Part

FOLIAGE_CELLS = ("birch", "linden", "maple", "oak", "rowan", "willow", "spruce", "pine",
                 "lilac", "rose", "hedge", "juniper", "grass", "lawn", "meadow", "rosette")
BARK_SLOTS = ("birch", "pine", "spruce", "oak", "linden", "maple", "smooth", "willow")
BARK_SLOT, BARK_GUTTER = 512, 32
UP = np.array([0.0, 1.0, 0.0])
GOLDEN = 2.39996


def cell_uv(name: str) -> tuple[float, float, float, float]:
    """Foliage atlas cell (u0, u1, v0, v1) in glTF UV space (v down)."""
    i = FOLIAGE_CELLS.index(name)
    col, row, inset = i % 4, i // 4, 1 / 2048
    return col / 4 + inset, (col + 1) / 4 - inset, row / 4 + inset, (row + 1) / 4 - inset


def bark_u(slot: str) -> tuple[float, float]:
    i, width = BARK_SLOTS.index(slot), BARK_SLOT * len(BARK_SLOTS)
    return (i * BARK_SLOT + BARK_GUTTER) / width, ((i + 1) * BARK_SLOT - BARK_GUTTER) / width


# Families. `ref` is the crown (height above the crown base, radius) the scaffold and card counts were tuned at;
# both scale with the measured crown. Angles in degrees. `tone` = (brightness, warmth) of the family's summer leaf
# against its atlas cell: limes, elms, oaks and spruces are deep greens, birches and willows light ones.
# `shell` is the share of a broadleaf crown's cards that stand on its outline instead of along its branchlets: a
# lime or a maple in leaf is a closed mass, a birch or a willow shows its boughs.
def _broadleaf(**over) -> dict:
    base = dict(kind="broadleaf", centre=0.45, shape=0.55, lean=0.012, n1=30, elev=(28, 58), ratio=0.5, d2=3.2, a2=40,
                l2ratio=0.5, l2max=2.6, d3=2.0, up1=0.05, droop1=0.05, up2=0.08, droop2=0.12, droop3=0.2, noise=0.12,
                card=1.2, spacing=0.26, cards=1500, ref=(12.5, 4.6), leader=0.9, trunk=1.0, card_up=0.2, card_along=0.6,
                card_out=0.6, card_roll=0.9, card_sky=1.2, aspect=1.0, tone=(1.0, 0.0), shell=0.3)
    base.update(over)
    return base


FAMILIES: dict[str, dict] = {
    "lime": _broadleaf(tone=(0.9, -0.02), cell="linden", bark="linden", centre=0.38, shape=0.6, taper=0.95, shell=0.42, leader=0.94),
    "maple": _broadleaf(tone=(1.0, 0.02), shell=0.4, cell="maple", bark="maple", shape=0.45, centre=0.46, lean=0.02, n1=26, elev=(30, 55), ratio=0.55,
                        d2=3.0, a2=42, l2ratio=0.55, droop2=0.1, card=1.25, cards=1400, ref=(11.0, 4.8), leader=0.82),
    "elm": _broadleaf(tone=(0.88, 0.0), shell=0.35, cell="linden", bark="oak", shape=0.5, centre=0.55, n1=24, elev=(40, 68), ratio=0.58, droop2=0.25,
                      card=1.1, ref=(12.0, 5.0), leader=0.78),
    "oak": _broadleaf(tone=(0.88, 0.02), shell=0.35, cell="oak", bark="oak", shape=0.4, centre=0.42, lean=0.03, n1=22, elev=(12, 42), ratio=0.62, d2=3.0,
                      a2=50, l2ratio=0.55, l2max=2.8, d3=2.2, noise=0.35, droop1=0.06, droop2=0.08, cards=1500,
                      ref=(10.5, 5.6), leader=0.72, trunk=1.25),
    "ash": _broadleaf(tone=(0.97, 0.0), cell="linden", bark="maple", shape=0.5, centre=0.5, n1=22, elev=(35, 62), ratio=0.5, d2=2.8,
                      card=1.25, cards=1150, ref=(11.0, 4.6), leader=0.88),
    "birch": _broadleaf(tone=(1.06, 0.04), shell=0.12, cell="birch", bark="birch", shape=0.6, centre=0.5, lean=0.015, n1=30, elev=(38, 62), ratio=0.42,
                        d2=3.6, a2=42, l2ratio=0.55, l2max=2.4, d3=2.2, droop2=0.55, droop3=0.9, card=1.0, cards=1300,
                        ref=(10.8, 3.2), leader=0.95, trunk=0.8, card_up=-0.35, card_along=0.9, card_out=0.35),
    "poplar": _broadleaf(tone=(0.98, -0.02), shell=0.2, cell="birch", bark="willow", shape=0.7, centre=0.5, n1=34, elev=(55, 75), ratio=0.4, d2=3.4,
                         l2ratio=0.5, l2max=1.8, card=1.0, cards=1300, ref=(14.0, 2.6), leader=0.97),
    "rowan": _broadleaf(tone=(1.0, 0.03), shell=0.2, cell="rowan", bark="smooth", shape=0.55, centre=0.5, lean=0.03, n1=18, elev=(40, 68), d2=3.2,
                        l2max=1.6, d3=1.5, droop2=0.15, card=1.0, cards=800, ref=(5.6, 2.6), leader=0.88, trunk=0.85),
    "fruit": _broadleaf(tone=(0.97, 0.03), cell="lilac", bark="smooth", shape=0.4, centre=0.42, lean=0.05, n1=14, elev=(15, 50), ratio=0.65,
                        d2=3.4, a2=48, l2max=1.5, d3=2.2, noise=0.3, droop2=0.2, card=0.8, spacing=0.2, cards=900,
                        ref=(3.6, 2.6), leader=0.7, trunk=1.1),
    "willow": _broadleaf(tone=(1.05, -0.04), shell=0.08, cell="willow", bark="willow", shape=0.5, centre=0.5, lean=0.04, n1=22, elev=(45, 70), ratio=0.55,
                         d2=2.6, a2=25, l2ratio=0.85, l2max=4.5, d3=1.2, up1=0.04, droop1=0.22, droop2=1.6, droop3=1.4,
                         card=1.3, spacing=0.24, cards=1250, ref=(9.4, 5.0), leader=0.7, trunk=1.3, card_up=-1.4,
                         card_along=1.0, card_out=0.15, aspect=0.95, card_roll=1.3),
    "pine": dict(tone=(0.92, -0.04), kind="pine", cell="pine", bark="pine", whorl=0.75, per_whorl=(3, 5), elev=(8, 38), ratio=0.4, up1=0.12,
                 droop1=0.04, noise=0.18, d2=2.6, l2ratio=0.45, hang=0.0, droop2=0.05, card=1.35, spacing=0.3, cards=750,
                 ref=(7.1, 3.6), trunk=0.85, card_up=0.2, card_along=0.6, card_out=0.6, card_roll=0.9, card_sky=1.2,
                 aspect=1.0, flat=0.0),
    "spruce": dict(tone=(0.85, -0.03), kind="spruce", cell="spruce", bark="spruce", whorl=0.55, per_whorl=(4, 5), elev=(-22, 32), ratio=0.35,
                   up1=0.18, droop1=0.12, noise=0.06, d2=3.2, l2ratio=0.45, hang=0.55, droop2=0.6, card=1.3, spacing=0.3,
                   cards=1250, ref=(16.5, 3.0), trunk=0.9, card_up=-0.15, card_along=1.0, card_out=0.35, card_roll=0.5,
                   card_sky=1.2, aspect=1.0, flat=0.55),
    "thuja": dict(tone=(0.9, 0.03), kind="spruce", cell="juniper", bark="spruce", whorl=0.35, per_whorl=(4, 6), elev=(35, 70), ratio=0.3,
                  up1=0.3, droop1=0.0, noise=0.05, d2=4.0, l2ratio=0.5, hang=-0.3, droop2=0.0, card=0.7, spacing=0.2,
                  cards=1100, ref=(5.0, 0.9), trunk=0.7, card_up=0.9, card_along=0.3, card_out=0.5, card_roll=0.6,
                  card_sky=0.4, aspect=1.0, flat=0.0),
    "lilac": dict(tone=(0.95, 0.0), kind="shrub", cell="lilac", bark="smooth", stems=8, spread=0.09, elev=(55, 82), stem=0.035, up=0.0,
                  droop=0.12, d2=3.6, l2=1.0, start=0.3, card=0.6, spacing=0.17, cards=700, ref=(3.4, 1.8),
                  card_up=0.2, card_along=0.6, card_out=0.6, card_roll=0.9, card_sky=1.2, aspect=1.0),
    "rose": dict(tone=(0.95, 0.02), kind="shrub", cell="rose", bark="smooth", stems=13, spread=0.15, elev=(45, 80), stem=0.012, up=0.0,
                 droop=0.55, d2=2.0, l2=0.6, start=0.2, card=0.42, spacing=0.16, cards=400, ref=(1.3, 0.9),
                 card_up=0.2, card_along=0.6, card_out=0.6, card_roll=0.9, card_sky=1.2, aspect=1.0),
    "juniper": dict(tone=(0.85, -0.04), kind="shrub", cell="juniper", bark="spruce", stems=5, spread=0.03, elev=(80, 88), stem=0.05, up=0.02,
                    droop=0.0, d2=7.0, l2=0.9, start=0.05, card=0.5, spacing=0.18, cards=560, ref=(3.0, 0.6),
                    card_up=0.9, card_along=0.3, card_out=0.5, card_roll=0.9, card_sky=0.6, aspect=1.0),
}
FALLBACK_LEAF = {"birch": (0.09, 0.16, 0.04), "pine": (0.05, 0.1, 0.04), "spruce": (0.04, 0.08, 0.04)}


@dataclass(frozen=True)
class Shape:
    """The generating parameters of one tree model."""

    family: str
    height: float  # metres
    radius: float  # crown radius, metres
    base: float  # crown base above ground, metres
    age: str = "mature"  # young | mature | old
    seed: int = 0

    def json(self) -> dict:
        return asdict(self)


@dataclass
class Branches:
    level: int
    points: np.ndarray  # (n, k, 3)
    radius: np.ndarray  # (n, k)
    sway: np.ndarray  # (n, k)
    length: np.ndarray  # (n,)
    phase: np.ndarray  # (n,)


def unit(v: np.ndarray) -> np.ndarray:
    return v / np.maximum(np.linalg.norm(v, axis=-1, keepdims=True), 1e-9)


def any_perp(d: np.ndarray) -> np.ndarray:
    return unit(np.where(np.abs(d[:, 1:2]) < 0.9, np.cross(d, UP), np.cross(d, np.array([1.0, 0.0, 0.0]))))


def rotate(v: np.ndarray, axis: np.ndarray, angle: np.ndarray) -> np.ndarray:
    """Rodrigues rotation of rows of v about unit rows of axis."""
    c, s = np.cos(angle)[:, None], np.sin(angle)[:, None]
    return v * c + np.cross(axis, v) * s + axis * np.sum(axis * v, axis=1, keepdims=True) * (1 - c)


def grow(rng, level, start, direction, length, r0, r1, segs, up, droop, noise, sway0, sway_gain, phase) -> Branches:
    n = len(start)
    points = np.empty((n, segs + 1, 3))
    points[:, 0] = start
    d = unit(direction)
    step = (length / segs)[:, None]
    for i in range(1, segs + 1):
        t = i / segs
        d = unit(d + UP * (up * step) - UP * (droop * step * t) + (rng.random((n, 3)) - 0.5) * noise)
        points[:, i] = points[:, i - 1] + d * step
    t = np.linspace(0.0, 1.0, segs + 1)
    radius = r0[:, None] + (r1 - r0)[:, None] * t ** 0.8
    return Branches(level, points, radius, sway0[:, None] + sway_gain * t * length[:, None], length, phase)


def at(b: Branches, index: np.ndarray, t: np.ndarray):
    """Point, radius, sway and direction at arc fraction t of branches `index`."""
    k = b.points.shape[1] - 1
    f = np.clip(t, 0.0, 1.0) * k
    i = np.minimum(k - 1, np.floor(f).astype(int))
    w = (f - i)[:, None]
    a, c = b.points[index, i], b.points[index, i + 1]
    lerp = lambda grid: grid[index, i] + (grid[index, i + 1] - grid[index, i]) * w[:, 0]  # noqa: E731
    return a + (c - a) * w, lerp(b.radius), lerp(b.sway), unit(c - a)


def child_dir(parent: np.ndarray, angle: np.ndarray, roll: np.ndarray, outward: np.ndarray | None) -> np.ndarray:
    side = rotate(any_perp(parent), parent, roll)
    d = rotate(parent, unit(np.cross(parent, side)), angle)
    return unit(d + outward * 0.35) if outward is not None else d


# The crown's cost, as quality parameters: leaf cards per crown relative to the old library's count, and their size
# relative to its cards. The old game widened every card by about 1.3 to fill the lidar crown (nature/crowns.ts).
CARD_DENSITY = 1.4
CARD_GROWTH = 1.3
CARD_SPAN = (0.34, 0.2)  # a card is at most this share of the crown radius plus this many metres: a small crown has small sprays
CARD_TONE = (0.1, 0.035)  # brightness and warmth vary this much from card to card
FAR_SHARE = 0.06  # the far level keeps this share of the cards (a prefix of the shuffled list), grown to cover


def card_size(sp: dict, radius: float) -> float:
    """The side of a leaf card in metres for a crown of this radius."""
    return min(sp["card"] * CARD_GROWTH, CARD_SPAN[0] * radius + CARD_SPAN[1])


def card_budget(sp: dict, shape: Shape, density: float, low: int, high: int) -> int:
    """The number of leaf cards of a crown: the family's count at its reference crown, scaled by the crown's surface,
    and by more where the cards are smaller than the family's own (not by their whole area: a small crown is thin)."""
    smaller = (sp["card"] * CARD_GROWTH / card_size(sp, shape.radius)) ** 1.2
    return int(np.clip(sp["cards"] * crown_scale(shape, sp) * density * min(smaller, 2.5), low, high))


def trunk_radius(shape: Shape, sp: dict) -> float:
    """Trunk radius at the ground from height and crown, an allometry for open-grown urban trees."""
    radius = (0.008 * shape.height + 0.024 * shape.radius) * sp.get("trunk", 1.0)
    radius *= {"young": 0.7, "old": 1.25}.get(shape.age, 1.0)
    return float(np.clip(radius, 0.03, 0.75))


def crown_scale(shape: Shape, sp: dict) -> float:
    """The measured crown's surface relative to the family's reference crown."""
    depth = max(shape.height - shape.base, 0.5)
    return float(depth * shape.radius / (sp["ref"][0] * sp["ref"][1]))


def broadleaf(shape: Shape, sp: dict, rng) -> dict:
    H, R = shape.height, shape.radius
    # The lowest boughs leave the trunk under the measured crown base and their leaves hang to it: the base is where
    # the crown's foliage starts, as the old library's crowns were fitted (game/src/world/nature/crowns.ts).
    y0 = max(min(shape.base, 1.6), shape.base - 0.2 * (H - shape.base))
    young, old = shape.age == "young", shape.age == "old"
    top = H * (0.96 if young else sp["leader"])
    centre = sp["centre"] if not old else sp["centre"] - 0.05
    yc = y0 + (H - y0) * centre
    hc = max(yc - y0, H - yc) * 1.02
    power = sp["shape"] * (0.85 if old else 1.0)
    # `taper` is the same power above the widest point: near 1 the crown narrows to its top as a lime's does.
    env = lambda y: R * np.maximum(0.0, 1 - ((y - yc) / hc) ** 2) ** np.where(y > yc, sp.get("taper", power), power)  # noqa: E731
    t = np.linspace(0.0, 1.0, 11)
    lean = (rng.random(2) * 2 - 1) * sp["lean"] * H * (0.3 if young else 1.0)
    wobble = 0.012 * H * t * (0.3 if young else 1.0)
    r_ground = trunk_radius(shape, sp)
    points = np.stack([lean[0] * t * t + np.sin(t * 5 + shape.seed) * wobble, t * top,
                       lean[1] * t * t + np.cos(t * 4 + shape.seed) * wobble], axis=1)
    radius = r_ground * (1 - 0.82 * t ** 1.15) * (1 + 0.45 * np.exp(-t * top / 0.35))
    trunk = Branches(0, points[None], radius[None], (0.25 * t * t)[None], np.array([top]), rng.random(1))
    small = R < 2.2  # a small crown needs more branchlets per metre to close
    dense = 1.6 if small else 1.0
    n1 = int(np.clip(round(sp["n1"] * (H - y0) / sp["ref"][0]), 9, 44))
    f = (np.arange(n1) + rng.random(n1) * 0.8) / n1
    y = y0 + f * (top * 0.96 - y0)
    az = np.arange(n1) * GOLDEN + rng.uniform(-0.3, 0.3, n1)
    low, high = sp["elev"]
    if young:
        low, high = low + 14, high + 8
    elev = np.radians(low + (high - low) * f + rng.uniform(-8, 8, n1))
    direction = np.stack([np.cos(elev) * np.cos(az), np.sin(elev), np.cos(elev) * np.sin(az)], axis=1)
    reach = env(y + np.sin(elev) * env(y) * 0.6)
    length = np.maximum(0.4, reach / np.maximum(0.35, np.cos(elev)) * rng.uniform(0.82, 1.05, n1))
    p, r, sway, _ = at(trunk, np.zeros(n1, int), y / top)
    r0 = np.maximum(0.015, r * sp["ratio"] * np.minimum(1.0, np.sqrt(length / max(R, 0.5))))
    one = grow(rng, 1, p, direction, length, r0, r0 * 0.22, 7, sp["up1"], sp["droop1"], sp["noise"], sway, 0.09, rng.random(n1))

    n2 = np.maximum(2, np.rint(one.length * sp["d2"] * dense)).astype(int)
    parent = np.repeat(np.arange(n1), n2)
    k = np.concatenate([np.arange(c) for c in n2])
    t2 = 0.22 + 0.74 * (k + rng.random(len(k)) * 0.7) / n2[parent]
    p, r, sway, d = at(one, parent, t2)
    outward = unit(np.stack([p[:, 0], np.full(len(p), 0.25), p[:, 2]], axis=1))
    direction = child_dir(d, np.radians(sp["a2"] + rng.uniform(-12, 12, len(k))), np.where(k % 2, 1, -1) * rng.uniform(1.0, 2.0, len(k)), outward)
    length = np.minimum(sp["l2max"], one.length[parent] * (1 - t2) * sp["l2ratio"] + 0.35) * rng.uniform(0.7, 1.1, len(k))
    two = grow(rng, 2, p, direction, length, np.maximum(0.008, r * 0.62), np.full(len(k), 0.006), 4, sp["up2"], sp["droop2"], 0.18, sway, 0.13, one.phase[parent])

    n3 = np.rint(two.length * sp["d3"] * dense).astype(int)
    parent = np.repeat(np.arange(len(n3)), n3)
    k = np.concatenate([np.arange(c) for c in n3]) if n3.sum() else np.zeros(0, int)
    t3 = 0.3 + 0.65 * (k + rng.random(len(k))) / np.maximum(1, n3[parent])
    p, r, sway, d = at(two, parent, t3)
    direction = child_dir(d, np.radians(rng.uniform(30, 55, len(k))), rng.uniform(0, 2 * np.pi, len(k)), None)
    three = grow(rng, 3, p, direction, rng.uniform(0.25, 0.55, len(k)), np.full(len(k), 0.006), np.full(len(k), 0.003), 2,
                 0.05, sp["droop3"], 0.2, sway, 0.2, two.phase[parent])

    anchors = [along(two, 0.3, sp["spacing"] / dense), along(three, 0.35, sp["spacing"] * 1.2 / dense)]
    budget = card_budget(sp, shape, CARD_DENSITY, 200, 3200)
    outline = int(budget * sp["shell"] * (0.6 if young else 1.0))
    inner = thin(merge(anchors), budget - outline, rng)
    return {"branches": [trunk, one, two, three], "anchors": merge([inner, shell(env, max(y0, shape.base + 0.4 * card_size(sp, R)), top, outline, rng)]),
            "crown": (y0, yc, hc, R), "conifer": False, "trunk": r_ground}


def shell(env, y0: float, top: float, count: int, rng) -> dict:
    """Card anchors on a crown's outline: sprays at the ends of twigs too fine to model, which close the crown where
    no bough happens to reach. The outline bulges and dents by a fifth of its radius so that it does not read as a
    turned solid."""
    y = np.sort(rng.uniform(y0, top, count * 4))
    # Evenly over the outline's area: more where the crown is wide.
    weight = np.maximum(env(y), 0.0) + 1e-6
    y = rng.choice(y, count, replace=False, p=weight / weight.sum()) if count else y[:0]
    az = np.arange(count) * GOLDEN + rng.uniform(-0.4, 0.4, count)
    waves = rng.uniform(0, 2 * np.pi, 4)
    lump = 0.5 + 0.25 * np.sin(2 * az + waves[0] + 1.7 * np.sin(y * 0.9 + waves[1])) + 0.25 * np.sin(3 * az + waves[2] + y * 1.3 + waves[3])
    r = env(y) * (0.76 + 0.24 * lump) * rng.uniform(0.9, 1.0, count)
    p = np.stack([r * np.cos(az), y, r * np.sin(az)], axis=1)
    out = unit(np.stack([np.cos(az), np.full(count, 0.35), np.sin(az)], axis=1))
    height = np.clip(y / max(top, 1e-6), 0.0, 1.0)
    return {"p": p, "dir": out, "sway": 0.25 * height * height + 0.1, "phase": rng.random(count), "tuft": np.zeros(count, bool)}


def conifer(shape: Shape, sp: dict, rng) -> dict:
    H, R, y0 = shape.height, shape.radius, shape.base
    t = np.linspace(0.0, 1.0, 13)
    r_ground = trunk_radius(shape, sp)
    points = np.stack([np.sin(t * 3 + shape.seed) * 0.01 * H * t, t * H * 0.98, np.cos(t * 2.5 + shape.seed) * 0.01 * H * t], axis=1)
    radius = r_ground * (1 - 0.92 * t) * (1 + 0.4 * np.exp(-t * H * 0.98 / 0.4))
    trunk = Branches(0, points[None], radius[None], (0.25 * t * t)[None], np.array([H]), rng.random(1))
    if sp["kind"] == "pine":
        env = lambda y: R * np.maximum(0.0, np.sin(np.pi * np.minimum(1.0, (y - y0) / (H - y0)) * 0.92 + 0.12)) ** 0.7  # noqa: E731
    else:
        env = lambda y: R * np.maximum(0.0, 1 - (y - y0) / (H * 1.02 - y0)) ** 0.95 + 0.12  # noqa: E731
    ys, y = [], y0
    while y < H - 0.3:
        ys.append(y)
        y += sp["whorl"] * max(1.0, (H - y0) / 22.0) * rng.uniform(0.8, 1.25)
    counts = rng.integers(sp["per_whorl"][0], sp["per_whorl"][1] + 1, len(ys))
    y = np.repeat(np.array(ys), counts)
    k = np.concatenate([np.arange(c) for c in counts])
    az = np.repeat(rng.random(len(ys)) * 2 * np.pi, counts) + k / np.repeat(counts, counts) * 2 * np.pi + rng.uniform(-0.3, 0.3, len(k))
    f = (y - y0) / (H - y0)
    elev = np.radians(sp["elev"][0] + (sp["elev"][1] - sp["elev"][0]) * f + rng.uniform(-6, 6, len(k)))
    length = np.maximum(0.25, env(y) * rng.uniform(0.85, 1.08, len(k)) / np.maximum(0.4, np.cos(elev)))
    p, r, sway, _ = at(trunk, np.zeros(len(k), int), y / (H * 0.98))
    r0 = np.maximum(0.012, r * sp["ratio"] * np.minimum(1.0, np.sqrt(length / max(R, 0.4))) + 0.01)
    direction = np.stack([np.cos(elev) * np.cos(az), np.sin(elev), np.cos(elev) * np.sin(az)], axis=1)
    one = grow(rng, 1, p, direction, length, r0, r0 * 0.3, 6, sp["up1"], sp["droop1"], sp["noise"], sway, 0.1, rng.random(len(k)))

    n2 = np.rint(one.length * sp["d2"]).astype(int)
    parent = np.repeat(np.arange(len(n2)), n2)
    k = np.concatenate([np.arange(c) for c in n2]) if n2.sum() else np.zeros(0, int)
    t2 = 0.15 + 0.8 * (k + rng.random(len(k))) / np.maximum(1, n2[parent])
    p, r, sway, d = at(one, parent, t2)
    side = unit(np.cross(d, UP))
    direction = unit(d * 0.75 + side * (np.where(k % 2, 1, -1) * 0.75)[:, None] + np.array([0.0, -sp["hang"], 0.0]))
    length = np.maximum(0.2, one.length[parent] * (1 - t2) * sp["l2ratio"] + 0.25) * rng.uniform(0.75, 1.1, len(k))
    two = grow(rng, 2, p, direction, length, np.maximum(0.006, r * 0.55), np.full(len(k), 0.004), 3, 0.02, sp["droop2"], 0.15, sway, 0.15, one.phase[parent])
    if sp["kind"] == "pine":
        anchors = [tips(one, (0.55, 0.7, 0.85, 1.0), rng), tips(two, (0.35, 0.55, 0.8, 1.0), rng)]
    else:
        anchors = [along(one, 0.25, sp["spacing"]), along(two, 0.1, sp["spacing"])]
    budget = card_budget(sp, shape, CARD_DENSITY, 160, 2400)
    return {"branches": [trunk, one, two], "anchors": thin(merge(anchors), budget, rng),
            "crown": (y0, (y0 + H) / 2, (H - y0) / 2, R), "conifer": True, "trunk": r_ground}


def shrub(shape: Shape, sp: dict, rng) -> dict:
    H, R = shape.height, shape.radius
    scale = np.sqrt(max(crown_scale(shape, sp), 0.2))
    stems = int(np.clip(round(sp["stems"] * scale), 3, 24))
    az = np.arange(stems) * GOLDEN + rng.uniform(-0.4, 0.4, stems)
    a = rng.random(stems) * sp["spread"] * R / 0.5
    start = np.stack([np.cos(az) * a, np.zeros(stems), np.sin(az) * a], axis=1)
    # Stems lean out as far as the measured crown needs.
    lean = np.degrees(np.arctan2(H, max(R * 0.85, 0.05)))
    elev = np.radians(np.clip(rng.uniform(lean - 8, min(lean + 22, 88), stems), 35, 88))
    length = H / np.sin(elev) * rng.uniform(0.8, 1.05, stems)
    stem = sp["stem"] * max(H / sp["ref"][0], 0.6)
    direction = np.stack([np.cos(elev) * np.cos(az), np.sin(elev), np.cos(elev) * np.sin(az)], axis=1)
    one = grow(rng, 1, start, direction, length, np.full(stems, stem), np.full(stems, stem * 0.3), 6, sp["up"], sp["droop"], 0.15,
               np.zeros(stems), 0.25, rng.random(stems))
    n2 = np.rint(one.length * sp["d2"]).astype(int)
    parent = np.repeat(np.arange(stems), n2)
    k = np.concatenate([np.arange(c) for c in n2]) if n2.sum() else np.zeros(0, int)
    t2 = 0.35 + 0.6 * (k + rng.random(len(k))) / np.maximum(1, n2[parent])
    p, r, sway, d = at(one, parent, t2)
    outward = unit(np.stack([p[:, 0], np.full(len(p), 0.4), p[:, 2]], axis=1))
    direction = child_dir(d, np.radians(rng.uniform(30, 60, len(k))), rng.uniform(0, 2 * np.pi, len(k)), outward)
    two = grow(rng, 2, p, direction, rng.uniform(0.25, 0.7, len(k)) * sp["l2"], np.maximum(0.005, r * 0.6), np.full(len(k), 0.003), 3,
               0.05, 0.2, 0.2, sway, 0.3, one.phase[parent])
    anchors = [along(one, sp["start"], sp["spacing"]), along(two, sp["start"], sp["spacing"])]
    budget = card_budget(sp, shape, 1.2, 60, 2400)
    return {"branches": [None, one, two], "anchors": thin(merge(anchors), budget, rng),
            "crown": (0.0, H * 0.55, H * 0.55, R), "conifer": False, "trunk": stem * 2.5}


def along(b: Branches, start: float, spacing: float) -> dict:
    """Card anchors along branches from `start` of their length, every `spacing` metres, and at the tip."""
    if not len(b.length):
        return merge([])
    count = np.floor(b.length * (1 - start) / spacing).astype(int) + 1
    index = np.repeat(np.arange(len(count)), count + 1)
    k = np.concatenate([np.arange(c + 1) for c in count])
    t = np.minimum(1.0, start + k * spacing / np.maximum(b.length[index], 1e-6))
    t = np.where(k == count[index], 1.0, t)
    p, _, sway, d = at(b, index, t)
    return {"p": p, "dir": d, "sway": sway, "phase": b.phase[index], "tuft": np.zeros(len(p), bool)}


def tips(b: Branches, fractions: tuple, rng) -> dict:
    index = np.repeat(np.arange(len(b.length)), sum(4 if f == 1.0 else 2 for f in fractions))
    t = np.tile(np.concatenate([np.full(4 if f == 1.0 else 2, f) for f in fractions]), len(b.length))
    p, _, sway, d = at(b, index, t)
    return {"p": p, "dir": d, "sway": sway, "phase": b.phase[index], "tuft": np.ones(len(p), bool)}


def merge(groups: list[dict]) -> dict:
    if not groups:
        return {"p": np.zeros((0, 3)), "dir": np.zeros((0, 3)), "sway": np.zeros(0), "phase": np.zeros(0), "tuft": np.zeros(0, bool)}
    return {key: np.concatenate([g[key] for g in groups]) for key in groups[0]}


def thin(anchors: dict, n: int, rng) -> dict:
    """At most n anchors: the same share of every tenth of the crown's height, the outer ones first."""
    total = len(anchors["p"])
    if total <= n:
        return anchors
    y = anchors["p"][:, 1]
    band = np.minimum(9, ((y - y.min()) / max(np.ptp(y), 1e-6) * 10).astype(int))
    out = np.hypot(anchors["p"][:, 0], anchors["p"][:, 2])
    reach = np.maximum([out[band == b].max() if (band == b).any() else 1.0 for b in range(10)], 1e-6)
    score = rng.random(total) + 0.45 * out / reach[band]
    order = np.lexsort((-score, band))
    counts = np.bincount(band, minlength=10)
    rank = np.arange(total) - np.repeat(np.concatenate([[0], np.cumsum(counts)[:-1]]), counts)
    quota = np.rint(n * counts / total).astype(int)
    keep = order[rank < np.repeat(quota, counts)]
    return {key: value[keep] for key, value in anchors.items()}


def fit(sk: dict, shape: Shape, sp: dict) -> dict:
    """Stretches the grown skeleton onto the measured crown: its leaves end at the height and at the radius."""
    p = sk["anchors"]["p"]
    if not len(p):
        return sk
    card = card_size(sp, shape.radius)
    y0 = sk["crown"][0]
    top = float(p[:, 1].max()) + 0.5 * card
    reach = float(np.percentile(np.hypot(p[:, 0], p[:, 2]), 97)) + 0.25 * card
    sy = (shape.height - y0) / max(top - y0, 0.3)
    sr = shape.radius / max(reach, 0.2)

    def move(q):
        q = q.copy()
        q[..., 0] *= sr
        q[..., 2] *= sr
        q[..., 1] = np.where(q[..., 1] > y0, y0 + (q[..., 1] - y0) * sy, q[..., 1])
        return q

    for b in sk["branches"]:
        if b is not None:
            b.points = move(b.points)
    sk["anchors"]["p"] = move(p)
    return sk


def skeleton(shape: Shape) -> dict:
    sp = FAMILIES[shape.family]
    rng = np.random.default_rng([shape.seed, 7919])
    if sp["kind"] == "broadleaf":
        return fit(broadleaf(shape, sp, rng), shape, sp)
    if sp["kind"] == "shrub":
        return fit(shrub(shape, sp, rng), shape, sp)
    return fit(conifer(shape, sp, rng), shape, sp)


def tubes(b: Branches, segs: int, rings: int, slot: str, tint, ao, v0: float, capped: bool = True) -> dict:
    """Tubes for a set of branches: `rings` rings of `segs` + 1 vertices each; with a cap at both ends (trunk and
    scaffold branches) each is a closed solid."""
    n, count = b.points.shape[0], b.points.shape[1]
    rings = min(rings, count)
    pick = np.rint(np.arange(rings) * (count - 1) / (rings - 1)).astype(int)
    points, radius, sway = b.points[:, pick], b.radius[:, pick], b.sway[:, pick]
    u0, u1 = bark_u(slot)
    span = np.clip(2 * np.pi * radius[:, 0], 0.3, 1.0)
    frame = any_perp(unit(points[:, 1] - points[:, 0]))
    angle = np.arange(segs + 1) / segs * 2 * np.pi
    cos, sin = np.cos(angle)[None, :, None], np.sin(angle)[None, :, None]
    positions = np.empty((n, rings, segs + 1, 3))
    normals = np.empty_like(positions)
    v = np.full(n, v0) + np.arange(n) * 0.37
    vs = np.empty((n, rings))
    for i in range(rings):
        d = unit(points[:, min(i + 1, rings - 1)] - points[:, max(i - 1, 0)])
        frame = unit(frame - d * np.sum(frame * d, axis=1, keepdims=True))
        binormal = np.cross(d, frame)
        if i:
            v = v + np.linalg.norm(points[:, i] - points[:, i - 1], axis=1)
        vs[:, i] = v
        normals[:, i] = frame[:, None] * cos + binormal[:, None] * sin
        positions[:, i] = points[:, i, None] + normals[:, i] * radius[:, i, None, None]
    u = u0 + (u1 - u0) * (np.arange(segs + 1) / segs)[None, None, :] * span[:, None, None]
    uv = np.stack([np.broadcast_to(u, (n, rings, segs + 1)), np.broadcast_to(vs[:, :, None], (n, rings, segs + 1))], axis=-1)
    flat = positions.reshape(-1, 3)
    shade = ao(flat)[:, None] * np.asarray(tint(flat) if callable(tint) else tint)
    wind = np.zeros((n, rings, segs + 1, 4))
    wind[..., 0] = np.minimum(1.0, sway)[:, :, None]
    wind[..., 2] = b.phase[:, None, None]
    ring = np.arange(rings - 1)[:, None] * (segs + 1) + np.arange(segs)[None, :]
    quad = np.stack([ring, ring + 1, ring + segs + 1, ring + 1, ring + segs + 2, ring + segs + 1], axis=-1).reshape(-1, 3)
    per = rings * (segs + 1)
    faces = (quad[None] + (np.arange(n) * per)[:, None, None]).reshape(-1, 3)
    # Caps: a fan at each end closes the solid.
    ends = np.stack([points[:, 0], points[:, -1]], axis=1).reshape(-1, 3)
    end_normal = np.stack([-unit(points[:, 1] - points[:, 0]), unit(points[:, -1] - points[:, -2])], axis=1).reshape(-1, 3)
    cap_uv = np.stack([np.full(2 * n, (u0 + u1) / 2), np.stack([vs[:, 0], vs[:, -1]], axis=1).reshape(-1)], axis=1)
    cap_wind = np.zeros((2 * n, 4))
    cap_wind[:, 0] = np.minimum(1.0, np.stack([sway[:, 0], sway[:, -1]], axis=1).reshape(-1))
    cap_wind[:, 2] = np.repeat(b.phase, 2)
    base = n * per + np.arange(n) * 2
    j = np.arange(segs)
    first = (np.arange(n) * per)[:, None] + j[None, :]
    last = (np.arange(n) * per + (rings - 1) * (segs + 1))[:, None] + j[None, :]
    caps = np.concatenate([np.stack([np.broadcast_to(base[:, None], first.shape), first + 1, first], axis=-1).reshape(-1, 3),
                           np.stack([np.broadcast_to(base[:, None] + 1, last.shape), last, last + 1], axis=-1).reshape(-1, 3)])
    if not capped:
        caps = caps[:0]
    return {"positions": np.concatenate([flat, ends]), "normals": np.concatenate([normals.reshape(-1, 3), end_normal]),
            "uvs": np.concatenate([uv.reshape(-1, 2), cap_uv]), "colors": np.concatenate([shade, ao(ends)[:, None] * np.ones(3)]),
            "wind": np.concatenate([wind.reshape(-1, 4), cap_wind]), "triangles": np.concatenate([faces, caps])}


def cards(anchors: dict, sp: dict, crown, conifer_crown: bool, rng, fold: bool, grow_by: float, seed: int = 0) -> dict:
    y0, yc, hc, R = crown
    n = len(anchors["p"])
    p, direction, tuft = anchors["p"], anchors["dir"], anchors["tuft"]

    def radial(q):
        if conifer_crown:
            return unit(np.stack([q[:, 0], (q[:, 1] - yc) * 0.25 + 0.35 * R, q[:, 2]], axis=1))
        return unit(np.stack([q[:, 0], (q[:, 1] - yc) * (R / hc), q[:, 2]], axis=1))

    def ao(q):
        depth = np.minimum(1.4, np.sqrt((q[:, 0] / R) ** 2 + ((q[:, 1] - yc) / hc) ** 2 + (q[:, 2] / R) ** 2))
        return (0.18 + 0.82 * np.minimum(1.0, depth ** 2.2)) * (0.82 + 0.18 * np.clip((q[:, 1] - y0) / (2 * hc), 0, 1))

    out = radial(p)
    jitter = np.stack([rng.uniform(-0.25, 0.25, n), sp["card_up"] + rng.uniform(-0.2, 0.2, n), rng.uniform(-0.25, 0.25, n)], axis=1)
    up = unit(direction * sp["card_along"] + out * sp["card_out"] + jitter)
    tuft_up = unit(direction + out * 0.6 + np.stack([rng.uniform(-0.6, 0.6, n), rng.uniform(0.0, 0.8, n), rng.uniform(-0.6, 0.6, n)], axis=1))
    up = np.where(tuft[:, None], tuft_up, up)
    sky = rng.uniform(0, sp["card_sky"], n) * np.clip((p[:, 1] - y0) / (2 * hc) + 0.2, 0, 1)
    flat = sp.get("flat", 0.0)
    if not flat:
        flatter = unit(up - UP * (up[:, 1] * np.minimum(0.8, sky * 0.6))[:, None])
        up = np.where(tuft[:, None], up, flatter)
    facing = out + UP * sky[:, None]
    facing = facing - up * np.sum(facing * up, axis=1, keepdims=True)
    weak = np.linalg.norm(facing, axis=1) < 0.1
    facing = np.where(weak[:, None], any_perp(up), facing)
    facing = rotate(unit(facing), up, rng.uniform(-1, 1, n) * sp["card_roll"])
    if flat:
        facing = unit(facing + (UP - facing) * flat)
    size = card_size(sp, R) * rng.uniform(0.8, 1.15, n) * grow_by
    side = unit(np.cross(up, facing))
    normal = unit(np.cross(side, up))
    w, h = size * sp["aspect"], size
    grid = np.array([[-0.5, 0], [0, 0], [0.5, 0], [-0.5, 1], [0, 1], [0.5, 1]] if fold else [[-0.5, 0], [0.5, 0], [-0.5, 1], [0.5, 1]])
    k = len(grid)
    bulge = np.where(grid[:, 0] == 0, 0.16, 0.0) - 0.05 if fold else np.zeros(k)
    positions = (p[:, None] + side[:, None] * (grid[:, 0][None, :, None] * w[:, None, None]) + up[:, None] * (grid[:, 1][None, :, None] * h[:, None, None])
                 + normal[:, None] * (bulge[None, :, None] * w[:, None, None]))
    flat_positions = positions.reshape(-1, 3)
    normals = unit(np.repeat(normal, k, axis=0) * 0.2 + radial(flat_positions) * 0.8)
    u0, u1, v0, v1 = cell_uv(sp["cell"])
    uv = np.tile(np.stack([u0 + (u1 - u0) * (grid[:, 0] + 0.5), v1 + (v0 - v1) * grid[:, 1]], axis=1), (n, 1))
    shade = ao(flat_positions)
    wind = np.stack([np.repeat(np.minimum(1.0, anchors["sway"] + 0.12), k), np.tile(grid[:, 1], n),
                     np.repeat(anchors["phase"], k), np.ones(n * k)], axis=1)
    quad = np.array([[0, 1, 3], [1, 4, 3], [1, 2, 4], [2, 5, 4]] if fold else [[0, 1, 2], [1, 3, 2]])
    faces = (quad[None] + (np.arange(n) * k)[:, None, None]).reshape(-1, 3)
    # Leaves of one spray share their light and age: each card is a little lighter or darker, yellower or bluer.
    # Drawn per anchor from the anchors' own order, so a card keeps its colour at every level of detail.
    vary = np.random.default_rng([seed, 2750159]).uniform(-1.0, 1.0, (n, 2))
    brightness, warmth = sp["tone"][0] * (1 + CARD_TONE[0] * vary[:, 0]), 1 + sp["tone"][1] + CARD_TONE[1] * vary[:, 1]
    colors = np.stack([shade, np.repeat(brightness, k), np.repeat(warmth, k)], axis=1)
    return {"positions": flat_positions, "normals": normals, "uvs": uv, "colors": colors, "wind": wind, "triangles": faces}


def join(groups: list[dict]) -> dict | None:
    groups = [g for g in groups if g is not None and len(g["triangles"])]
    if not groups:
        return None
    offset, faces = 0, []
    for g in groups:
        faces.append(g["triangles"] + offset)
        offset += len(g["positions"])
    out = {key: np.concatenate([g[key] for g in groups]) for key in ("positions", "normals", "uvs", "colors", "wind")}
    out["triangles"] = np.concatenate(faces)
    return out


def part(name: str, mesh: dict, material: Material, lod: int) -> Part:
    return Part(name, np.round(mesh["positions"], 4), mesh["triangles"], material, np.round(mesh["normals"], 3), np.round(mesh["uvs"], 5),
                np.round(mesh["colors"], 3), {"wind": np.round(mesh["wind"], 3)}, lod)


def materials(shape: Shape) -> tuple[Material, Material]:
    sp = FAMILIES[shape.family]
    leaf = FALLBACK_LEAF.get(sp["cell"], (0.07, 0.13, 0.035))
    bark = (0.55, 0.55, 0.52) if sp["bark"] == "birch" else (0.09, 0.075, 0.06)
    return (Material("bark", {"atlas": "nature", "slot": sp["bark"], "uv": "atlas"}, bark, 0.92),
            Material("foliage", {"atlas": "nature", "cell": sp["cell"], "uv": "atlas"}, leaf, 0.92, double_sided=True, surface="masked"))


def build(shape: Shape, lod: int = 0) -> list[Part]:
    """The mesh parts of one tree model at a level of detail: 0 = street level, 1 = the far representation
    (the whole canopy as fewer, larger flat cards and the trunk)."""
    sp = FAMILIES[shape.family]
    sk = skeleton(shape)
    rng = np.random.default_rng([shape.seed, 104729, lod])
    y0, yc, hc, R = sk["crown"]

    def bark_ao(q):
        depth = np.minimum(1.4, np.sqrt((q[:, 0] / R) ** 2 + ((q[:, 1] - yc) / hc) ** 2 + (q[:, 2] / R) ** 2))
        return np.where(q[:, 1] < y0, 1.0, 0.75 + 0.25 * np.minimum(1.0, depth))

    tint = (1.0, 1.0, 1.0)
    if sp["kind"] == "pine":
        tint = lambda q: np.where((q[:, 1] > 0.45 * shape.height)[:, None], [1.25, 0.86, 0.62], [1.0, 1.0, 1.0])  # noqa: E731
    segs = (10, 6, 3) if lod == 0 else (4, 0, 0)
    rings = (11, 4, 3) if lod == 0 else (5, 0, 0)
    minimum = (0.0, 0.02, 0.045) if lod == 0 else (0.0, 0.045, 1.0)  # thinner boughs are hidden by the leaves they carry
    solids, v = [], 0.0
    for level, b in enumerate(sk["branches"][:3]):
        if b is None or not segs[level] or not len(b.length):
            continue
        keep = b.radius[:, 0] >= minimum[level]
        if not keep.any():
            continue
        chosen = Branches(b.level, b.points[keep], b.radius[keep], b.sway[keep], b.length[keep], b.phase[keep])
        solids.append(tubes(chosen, segs[level], rings[level], sp["bark"], tint, bark_ao, v, capped=True))
        v += 0.37 * keep.sum()
    # The cards in a seeded shuffle: any prefix is an even thinning of the crown, and the far level is the prefix
    # of a seventh of them, grown to keep the crown's cover (one source for every level of detail).
    order = np.random.default_rng([shape.seed, 15485863]).permutation(len(sk["anchors"]["p"]))
    anchors = {key: value[order] for key, value in sk["anchors"].items()}
    grow_by = 1.0
    if lod:
        share = FAR_SHARE
        anchors = {key: value[:max(int(np.ceil(share * len(order))), min(len(order), 10))] for key, value in anchors.items()}
        grow_by = 1 / np.sqrt(share) * 0.8
    leaves = cards(anchors, sp, sk["crown"], sk["conifer"], rng, fold=lod == 0, grow_by=grow_by, seed=shape.seed)
    bark_material, leaf_material = materials(shape)
    out = []
    bark = join(solids)
    if bark is not None:
        out.append(part("bark", bark, bark_material, lod))
    if len(leaves["triangles"]):
        out.append(part("leaves", leaves, leaf_material, lod))
    return out


def trunk_collision(shape: Shape) -> dict:
    """The trunk as the game's collision shape: a cylinder from the ground to the crown base, asset-local."""
    sp = FAMILIES[shape.family]
    height = max(shape.base, min(shape.height, 1.5)) if sp["kind"] != "shrub" else min(shape.height, 1.0)
    return {"shape": "cylinder", "size": [round(trunk_radius(shape, sp) if sp["kind"] != "shrub" else sp["stem"] * 2.5, 3), round(height, 2)]}


def shared_material(material: Material) -> Material:
    """The material of meshes that hold several species at once: the atlas cell or bark slot is in the UVs."""
    return Material(material.family, {"atlas": "nature", "uv": "atlas"}, (0.07, 0.13, 0.035) if material.family == "foliage" else (0.09, 0.075, 0.06),
                    0.92, double_sided=material.double_sided, surface=material.surface)


def placed(part: Part, row) -> dict:
    """A library part at an instance row (x, y, z, yaw, sx, sy, sz), as a mesh for `Meshes`."""
    x, y, z, yaw, sx, sy, sz = row
    cos, sin = np.cos(yaw), np.sin(yaw)

    def turn(v):
        return np.stack([v[:, 0] * cos + v[:, 2] * sin, v[:, 1], -v[:, 0] * sin + v[:, 2] * cos], axis=1)

    return {"positions": turn(part.positions * np.array([sx, sy, sz])) + np.array([x, y, z]),
            "normals": turn(unit(part.normals / np.array([sx, sy, sz]))), "uvs": part.uvs, "colors": part.colors,
            "wind": part.attributes["wind"], "triangles": part.triangles}


class Meshes:
    """Collects generated meshes per (name, material) and hands them over as parts."""

    def __init__(self):
        self.groups: dict[tuple[str, int], tuple] = {}

    def add(self, name: str, lod: int, material, mesh: dict | None) -> None:
        if mesh is not None and len(mesh["triangles"]):
            self.groups.setdefault((name, lod), (material, []))[1].append(mesh)

    def parts(self) -> list[Part]:
        out = []
        for (name, lod), (material, meshes) in sorted(self.groups.items()):
            offset, faces = 0, []
            for mesh in meshes:
                faces.append(mesh["triangles"] + offset)
                offset += len(mesh["positions"])
            join = lambda key: np.concatenate([np.asarray(mesh[key], float).reshape(len(mesh["positions"]), -1) for mesh in meshes])  # noqa: E731
            colors = join("colors")
            colors = np.repeat(colors, 3, axis=1) if colors.shape[1] == 1 else colors
            attributes = {"wind": np.round(join("wind"), 3)} if "wind" in meshes[0] else {}
            out.append(Part(name, np.round(join("positions"), 4), np.concatenate(faces), material, np.round(join("normals"), 3),
                            np.round(join("uvs"), 4), np.round(colors, 3), attributes, lod))
        return out
