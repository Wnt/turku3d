"""Original low-poly parametric playground equipment (CC0), one prototype per kind and size class.

Local frame as for street furniture (``turku3d.props.models``): x along the item's long axis, +Z
its front, y up from the ground at the footprint centre. ``code`` carries the quantised
parameters (``l4.5w2.0h2.4n2``) so equal sizes share one glTF mesh. Colours come from the
furniture palette (left half of the atlas) plus play colours in its right half; every item of a
tile shares one atlas, so the packager batches them with the furniture into one draw.

``boxes(kind, params)`` gives the static collision boxes ((centre), (half extents)) in the same
frame: posts, frames and solid bodies, never the open space between swing legs or under roofs.
"""

from __future__ import annotations

import io
import math
import re
from functools import cache

import numpy as np
from PIL import Image

from turku3d.package.gltf import GlbMesh
from turku3d.props import models as furniture
from turku3d.props.models import BLACK, GREEN, GREY, ORANGE, STEEL, WOOD, YELLOW, Shape

# Play colours in the right half of the shared atlas (palette columns 8-15).
PLAY_COLORS = ["#c63b2f", "#2f64b4", "#d9c48f", "#5b4a43", "#2e8b85", "#c9a46c", "#4e7d52", "#ecebe6"]
RED, BLUE, SAND, RUBBER, TEAL, LIGHT_WOOD, RUBBER_GREEN, WHITE = range(8, 16)
BRIGHT = (RED, BLUE, YELLOW, GREEN, ORANGE, TEAL)
PAD_COLOURS = {"sand": SAND, "rubber": RUBBER, "gravel": GREY, "grass": RUBBER_GREEN, "mixed": SAND}
PARAM = re.compile(r"([a-z])(-?\d+(?:\.\d+)?)")


@cache
def atlas() -> bytes:
    """The furniture palette with play colours in place of the sign half."""
    im = Image.open(io.BytesIO(furniture.atlas(""))).convert("RGB")
    for k, colour in enumerate(PLAY_COLORS):
        im.paste(colour, (128 + k * 16, 0, 128 + k * 16 + 16, 128))
    out = io.BytesIO()
    im.save(out, format="PNG")
    return out.getvalue()


def code(**params) -> str:
    return "".join(f"{k}{v:g}" if isinstance(v, float) else f"{k}{v}" for k, v in sorted(params.items()))


def params(text: str) -> dict:
    return {k: float(v) for k, v in PARAM.findall(text)}


def bar(s: Shape, a, b, w, h=None, color=STEEL):
    """A square beam from point a to point b (w wide, h deep), outward-wound like Shape.box."""
    a, b = np.asarray(a, float), np.asarray(b, float)
    u = b - a
    length = float(np.linalg.norm(u))
    if length < 1e-6:
        return
    u /= length
    ref = np.array([0.0, 1.0, 0.0]) if abs(u[1]) < 0.9 else np.array([1.0, 0.0, 0.0])
    v = np.cross(ref, u)
    v /= np.linalg.norm(v)
    w_axis = np.cross(u, v)
    c = (a + b) / 2
    half = (length / 2, w / 2, (h or w) / 2)
    corners = []
    for sx, sy, sz in ((-1, -1, -1), (1, -1, -1), (1, 1, -1), (-1, 1, -1),
                       (-1, -1, 1), (1, -1, 1), (1, 1, 1), (-1, 1, 1)):
        corners.append(tuple(c + u * sx * half[0] + v * sy * half[1] + w_axis * sz * half[2]))
    for face in ((0, 3, 2, 1), (4, 5, 6, 7), (0, 4, 7, 3), (1, 2, 6, 5), (3, 7, 6, 2), (0, 1, 5, 4)):
        s.face([corners[i] for i in face], color)


def roof(s: Shape, lx, lz, y, peak, color=RED, overhang=0.2):
    """A four-sided pyramid roof over an lx x lz rectangle, eaves at y."""
    a, b = lx / 2 + overhang, lz / 2 + overhang
    corners = [(-a, y, -b), (a, y, -b), (a, y, b), (-a, y, b)]
    top = (0, y + peak, 0)
    for i in range(4):
        s.face([corners[i], corners[(i + 1) % 4], top], color)
    s.face(corners, color)  # underside


def swing(s, p):
    n = int(p.get("n", 2))
    style = int(p.get("s", 0))  # 0 flat seats, 1 baby seats, 2 basket
    length, h = p["l"], 2.3 if style != 2 else 2.5
    spread = 0.9 if style != 2 else 1.1
    for x in (-length / 2, length / 2):
        for z in (-spread, spread):
            bar(s, (x, 0, z), (x, h, 0), 0.09, color=STEEL)
    bar(s, (-length / 2 - 0.1, h, 0), (length / 2 + 0.1, h, 0), 0.11, color=RED if style != 2 else STEEL)
    if style == 2:
        for k in range(n):
            x = (k - (n - 1) / 2) * length / n
            s.cylinder(x, 0, 0.55, 0.08, ORANGE, 0.5)
            for dx, dz in ((-0.3, -0.3), (0.3, -0.3), (0.3, 0.3), (-0.3, 0.3)):
                bar(s, (x + dx, 0.56, dz), (x, h, 0), 0.02, color=BLACK)
        return
    for k in range(n):
        x = (k - (n - 1) / 2) * length / n
        for side in (-0.2, 0.2):
            bar(s, (x + side, 0.48, 0), (x + side, h, 0), 0.02, color=STEEL)
        if style == 1:
            s.box((x, 0.5, 0), (0.42, 0.24, 0.32), RED)
        else:
            s.box((x, 0.46, 0), (0.46, 0.04, 0.18), BLACK)


def slide(s, p):
    length, h = p["l"], p.get("h", 1.5)
    deck = -length / 2 + 0.5
    for x in (deck - 0.4, deck + 0.4):
        for z in (-0.35, 0.35):
            bar(s, (x, 0, z), (x, h + 0.9, z), 0.08, color=STEEL)
    s.box((deck, h, 0), (0.8, 0.06, 0.8), WOOD)
    for k in range(int(h / 0.3)):
        bar(s, (deck - 0.4, 0.3 * (k + 1), -0.35), (deck - 0.4, 0.3 * (k + 1), 0.35), 0.04, color=STEEL)
    end = length / 2
    bar(s, (deck + 0.4, h + 0.03, 0), (end - 0.4, 0.45, 0), 0.6, 0.05, color=YELLOW)
    bar(s, (end - 0.4, 0.45, 0), (end, 0.3, 0), 0.6, 0.05, color=YELLOW)
    for z in (-0.3, 0.3):
        bar(s, (deck + 0.4, h + 0.2, z), (end - 0.4, 0.6, z), 0.05, 0.2, color=YELLOW)


def climbing(s, p):
    lx, lz, h = p["l"], p["w"], p.get("h", 2.0)
    xs = np.linspace(-lx / 2, lx / 2, max(2, int(lx / 1.2) + 1))
    for x in (xs[0], xs[-1]):
        for z in (-lz / 2, lz / 2):
            bar(s, (x, 0, z), (x, h, z), 0.1, color=WOOD)
    for k in range(1, int(h / 0.6) + 1):
        y = min(h, k * 0.6)
        for z in (-lz / 2, lz / 2):
            bar(s, (xs[0], y, z), (xs[-1], y, z), 0.05, color=BLUE)
        for x in (xs[0], xs[len(xs) // 2], xs[-1]) if k % 2 else xs:
            bar(s, (x, y, -lz / 2), (x, y, lz / 2), 0.05, color=BLUE if k % 2 else RED)
    s.box((0, h - 0.3, 0), (min(lx, 1.4), 0.06, min(lz, 1.4)), WOOD)


def net(s, p):
    radius, h = p["r"], p.get("h", 4.0)
    bar(s, (0, 0, 0), (0, h, 0), 0.14, color=STEEL)
    ring = [(radius * math.cos(a), radius * math.sin(a)) for a in np.linspace(0, math.tau, 8, endpoint=False)]
    for x, z in ring:
        bar(s, (x, 0.05, z), (0, h - 0.1, 0), 0.04, color=RED)
    for f in (0.3, 0.55, 0.8):
        pts = [(x * (1 - f), h * f, z * (1 - f)) for x, z in ring]
        for i in range(len(pts)):
            bar(s, pts[i], pts[(i + 1) % len(pts)], 0.035, color=RED)


def tower(s, p):
    lx, lz, h = p["l"], p["w"], p.get("h", 3.4)
    deck = min(1.5, h * 0.42)
    a, b = lx / 2 - 0.08, lz / 2 - 0.08
    for x in (-a, a):
        for z in (-b, b):
            bar(s, (x, 0, z), (x, h - 0.9, z), 0.12, color=WOOD)
    s.box((0, deck, 0), (lx, 0.08, lz), WOOD)
    for z in (-b, b):
        s.box((0, deck + 0.45, z), (lx - 0.3, 0.6, 0.04), RED if z < 0 else BLUE)
    s.box((-a, deck + 0.45, 0), (0.04, 0.6, lz - 0.3), GREEN)
    roof(s, lx, lz, h - 0.9, 0.9, RED)
    bar(s, (a, deck, 0), (a + deck * 1.8, 0.3, 0), 0.6, 0.05, color=YELLOW)
    for k in range(int(deck / 0.3)):
        bar(s, (-a - 0.05, 0.3 * (k + 1), -0.3), (-a - 0.05, 0.3 * (k + 1), 0.3), 0.04, color=STEEL)


def sandbox(s, p):
    lx, lz = p["l"], p["w"]
    t = 0.12
    for z in (-lz / 2 + t / 2, lz / 2 - t / 2):
        s.box((0, 0.15, z), (lx, 0.3, t), WOOD)
    for x in (-lx / 2 + t / 2, lx / 2 - t / 2):
        s.box((x, 0.15, 0), (t, 0.3, lz - 2 * t), WOOD)
    s.box((0, 0.1, 0), (lx - 2 * t, 0.16, lz - 2 * t), SAND)


def spring(s, p):
    colour = BRIGHT[int(p.get("c", 0)) % len(BRIGHT)]
    s.cylinder(0, 0, 0.11, 0.42, BLACK)
    s.box((0, 0.62, 0), (0.75, 0.32, 0.24), colour)
    s.box((0.28, 0.9, 0), (0.18, 0.3, 0.2), colour)
    bar(s, (0.18, 0.95, -0.18), (0.18, 0.95, 0.18), 0.04, color=STEEL)


def seesaw(s, p):
    length = p.get("l", 3.0)
    s.box((0, 0.25, 0), (0.25, 0.5, 0.35), STEEL)
    bar(s, (-length / 2, 0.3, 0), (length / 2, 0.75, 0), 0.26, 0.06, color=RED)
    for x, y in ((-length / 2 + 0.35, 0.37), (length / 2 - 0.35, 0.68)):
        bar(s, (x, y, -0.15), (x, y + 0.35, -0.15), 0.04, color=STEEL)
        bar(s, (x, y + 0.35, -0.15), (x, y + 0.35, 0.15), 0.04, color=STEEL)
        bar(s, (x, y, 0.15), (x, y + 0.35, 0.15), 0.04, color=STEEL)


def carousel(s, p):
    radius = p["r"]
    s.cylinder(0, 0, 0.08, 1.0, STEEL)
    s.cylinder(0, 0, radius, 0.08, RED, 0.25)
    ring = [(radius * 0.85 * math.cos(a), radius * 0.85 * math.sin(a))
            for a in np.linspace(0, math.tau, 6, endpoint=False)]
    for x, z in ring:
        bar(s, (x, 0.33, z), (x, 0.9, z), 0.04, color=YELLOW)
        bar(s, (x, 0.9, z), (0, 1.0, 0), 0.035, color=YELLOW)


def balance(s, p):
    length = p["l"]
    if int(p.get("t", 0)):  # stepping stones / rings
        n = max(2, int(length / 0.7))
        for k in range(n):
            x = (k - (n - 1) / 2) * length / n
            s.cylinder(x, 0.15 * (k % 2) - 0.07, 0.24, 0.2 + 0.1 * (k % 3), BRIGHT[k % len(BRIGHT)])
        return
    for x in (-length / 2 + 0.2, length / 2 - 0.2):
        s.box((x, 0.15, 0), (0.12, 0.3, 0.3), STEEL)
    s.box((0, 0.33, 0), (length, 0.08, 0.14), WOOD)


def zipline(s, p):
    length = p["l"]
    for x, h in ((-length / 2, 3.0), (length / 2, 2.4)):
        for z in (-1.0, 1.0):
            bar(s, (x, 0, z), (x, h, 0), 0.12, color=STEEL)
        bar(s, (x, h, -0.2), (x, h, 0.2), 0.12, color=STEEL)
    s.box((-length / 2 + 0.8, 0.9, 0), (1.4, 0.08, 1.2), WOOD)
    for k in range(3):
        s.box((-length / 2 + 0.8 + 0.85, 0.25 + 0.3 * k, 0), (0.3, 0.06, 0.9), WOOD)
    bar(s, (-length / 2, 2.9, 0), (length / 2, 2.3, 0), 0.025, color=BLACK)
    x = -length / 2 + 2.0
    bar(s, (x, 1.0, 0), (x, 2.85, 0), 0.025, color=BLACK)
    s.cylinder(x, 0, 0.18, 0.06, RED, 0.95)


def trampoline(s, p):
    lx, lz = p["l"], p["w"]
    s.box((0, 0.02, 0), (lx, 0.04, lz), RUBBER_GREEN)
    s.box((0, 0.045, 0), (lx - 0.5, 0.01, lz - 0.5), BLACK)


def gym(s, p):
    length = p["l"]
    n = max(1, int(length / 2.0))
    xs = np.linspace(-length / 2 + 0.2, length / 2 - 0.2, n + 1)
    for x in xs:
        bar(s, (x, 0, 0), (x, 2.3, 0), 0.11, color=GREEN)
    for k in range(n):
        y = (2.2, 1.6, 1.0)[k % 3]
        bar(s, (xs[k], y, 0), (xs[k + 1], y, 0), 0.04, color=STEEL)
    s.box((xs[0] - 0.7, 0.25, 0.6), (1.2, 0.08, 0.35), WOOD)


def goal(s, x, width, facing, color=WHITE):
    """A football goal at local x, its open side towards ``facing`` (+1/-1 along x)."""
    depth = 0.8
    for z in (-width / 2, width / 2):
        bar(s, (x, 0, z), (x, 1.5, z), 0.08, color=color)
        bar(s, (x, 1.5, z), (x - facing * depth, 0.02, z), 0.04, color=STEEL)
    bar(s, (x, 1.5, -width / 2), (x, 1.5, width / 2), 0.08, color=color)


def ballcourt(s, p):
    lx, lz = p["l"], p["w"]
    if lx < 6 or lz < 4:  # a single goal or hoop outline
        goal(s, 0, min(max(lx, lz), 3.0), 1)
        return
    gap = min(3.0, lz / 3)
    for z in (-lz / 2, lz / 2):
        s.box((0, 0.45, z), (lx, 0.9, 0.06), GREEN)
    for x in (-lx / 2, lx / 2):
        for sign in (-1, 1):
            side = (lz - gap) / 2
            s.box((x, 0.45, sign * (gap / 2 + side / 2)), (0.06, 0.9, side), GREEN)
    width = min(3.0, lz / 3)
    goal(s, -lx / 2 + 0.6, width, 1)
    goal(s, lx / 2 - 0.6, width, -1)


def skate(s, p):
    lx, lz = p["l"], p["w"]
    h = min(1.2, 0.18 * lx)
    a, b = lx / 2, lz / 2
    # A quarter-pipe wedge rising towards +x.
    s.face([(-a, 0.01, b), (a, h, b), (a, h, -b), (-a, 0.01, -b)], GREY)
    s.face([(a, 0, -b), (a, h, -b), (a, h, b), (a, 0, b)], GREY)
    s.face([(-a, 0, b), (a, 0, b), (a, h, b)], GREY)
    s.face([(-a, 0, -b), (a, h, -b), (a, 0, -b)], GREY)
    bar(s, (a, h, -b), (a, h, b), 0.06, color=STEEL)


def shelter(s, p):
    lx, lz = p["l"], p["w"]
    if int(p.get("t", 0)):  # play house
        h = 1.5
        s.box((0, h / 2, -lz / 2 + 0.03), (lx, h, 0.06), LIGHT_WOOD)
        s.box((-lx / 2 + 0.03, h / 2, 0), (0.06, h, lz), LIGHT_WOOD)
        s.box((lx / 2 - 0.03, h / 2, 0), (0.06, h, lz), LIGHT_WOOD)
        for x in (-lx / 4 - 0.15, lx / 4 + 0.15):
            s.box((x, h / 2, lz / 2 - 0.03), (lx / 2 - 0.3, h, 0.06), LIGHT_WOOD)
        roof(s, lx, lz, h, 0.8, RED, 0.15)
        return
    h = 2.4
    a, b = lx / 2 - 0.1, lz / 2 - 0.1
    for x in (-a, a):
        for z in (-b, b):
            bar(s, (x, 0, z), (x, h, z), 0.12, color=WOOD)
    roof(s, lx, lz, h, 0.7, GREEN, 0.25)


def pool(s, p):
    lx, lz = p["l"], p["w"]
    for z in (-lz / 2, lz / 2):
        s.box((0, 0.1, z), (lx + 0.3, 0.2, 0.3), GREY)
    for x in (-lx / 2, lx / 2):
        s.box((x, 0.1, 0), (0.3, 0.2, lz), GREY)
    s.box((0, 0.06, 0), (lx, 0.03, lz), TEAL)


def pad(s, p):
    t = p.get("t", 0.03)
    s.box((0, t / 2, 0), (p["l"], t, p["w"]), int(p.get("c", SAND)))


BUILDERS = {
    "play_swing": swing, "play_slide": slide, "play_climbing": climbing, "play_net": net,
    "play_tower": tower, "play_sandbox": sandbox, "play_spring": spring, "play_seesaw": seesaw,
    "play_carousel": carousel, "play_balance": balance, "play_zipline": zipline,
    "play_trampoline": trampoline, "play_gym": gym, "play_ballcourt": ballcourt,
    "play_skate": skate, "play_shelter": shelter, "play_pool": pool, "play_pad": pad,
}


@cache
def model(kind: str, variant: str = "") -> GlbMesh:
    """One prototype per kind and quantised parameters; furniture kinds reuse the street models."""
    if kind in BUILDERS:
        s = Shape()
        BUILDERS[kind](s, params(variant))
        positions, indices, uvs = np.array(s.positions), np.array(s.indices), np.array(s.uvs)
    else:
        base = furniture.model(kind, variant)
        positions, indices, uvs = base.positions, base.indices, base.uvs
    return GlbMesh(
        f"{kind}_{variant}", positions, indices, uvs=uvs, image=atlas(),
        extras={"layer": "props", "object_id": f"prototype:{kind}:{variant}",
                "material_key": f"props_{kind}", "texture_uv": "normalized"},
    )


def boxes(kind: str, p: dict) -> list[tuple[tuple, tuple]]:
    """Static collision boxes (local centre, half extents) of one item."""
    from turku3d.coarse.furniture import BOXES

    if kind in BOXES:
        return BOXES[kind]
    lx, lz = p.get("l", 1.0), p.get("w", 1.0)
    if kind == "play_swing":
        return [((x, 1.15, 0), (0.12, 1.15, 0.95)) for x in (-lx / 2, lx / 2)]
    if kind == "play_zipline":
        return [((x, 1.3, 0), (0.15, 1.3, 1.0)) for x in (-lx / 2, lx / 2)] + [
            ((-lx / 2 + 0.8, 0.47, 0), (0.7, 0.47, 0.6))]
    if kind == "play_slide":
        h = p.get("h", 1.5)
        return [((-lx / 2 + 0.5, (h + 0.9) / 2, 0), (0.45, (h + 0.9) / 2, 0.4)),
                ((0.25, 0.45, 0), (lx / 2 - 0.25, 0.45, 0.3))]
    if kind in ("play_climbing", "play_tower", "play_gym"):
        h = p.get("h", 2.3)
        return [((0, h / 2, 0), (lx / 2, h / 2, max(lz, 0.3) / 2))]
    if kind == "play_net":
        r, h = p["r"], p.get("h", 4.0)
        return [((0, h / 2, 0), (r * 0.6, h / 2, r * 0.6))]
    if kind == "play_carousel":
        r = p["r"]
        return [((0, 0.5, 0), (r * 0.75, 0.5, r * 0.75))]
    if kind == "play_sandbox":
        return [((0, 0.15, z), (lx / 2, 0.15, 0.06)) for z in (-lz / 2 + 0.06, lz / 2 - 0.06)] + [
            ((x, 0.15, 0), (0.06, 0.15, lz / 2)) for x in (-lx / 2 + 0.06, lx / 2 - 0.06)]
    if kind == "play_spring":
        return [((0, 0.5, 0), (0.4, 0.5, 0.15))]
    if kind == "play_seesaw":
        return [((0, 0.35, 0), (p.get("l", 3.0) / 2, 0.35, 0.15))]
    if kind == "play_balance":
        return [((0, 0.2, 0), (lx / 2, 0.2, 0.15))]
    if kind == "play_ballcourt":
        if lx < 6 or lz < 4:
            return [((0, 0.75, 0), (0.4, 0.75, 1.5))]
        gap, side = min(3.0, lz / 3), (lz - min(3.0, lz / 3)) / 2
        result = [((0, 0.45, z), (lx / 2, 0.45, 0.05)) for z in (-lz / 2, lz / 2)]
        for x in (-lx / 2, lx / 2):
            for sign in (-1, 1):
                result.append(((x, 0.45, sign * (gap / 2 + side / 2)), (0.05, 0.45, side / 2)))
        return result
    if kind == "play_skate":
        h = min(1.2, 0.18 * lx)
        return [((lx / 4, h / 4, 0), (lx / 4, h / 4, lz / 2))]
    if kind == "play_shelter":
        if int(p.get("t", 0)):
            return [((0, 0.75, -lz / 2 + 0.03), (lx / 2, 0.75, 0.05)),
                    ((-lx / 2 + 0.03, 0.75, 0), (0.05, 0.75, lz / 2)),
                    ((lx / 2 - 0.03, 0.75, 0), (0.05, 0.75, lz / 2))]
        a, b = lx / 2 - 0.1, lz / 2 - 0.1
        return [((x, 1.2, z), (0.08, 1.2, 0.08)) for x in (-a, a) for z in (-b, b)]
    if kind == "play_pool":
        return [((0, 0.1, z), (lx / 2, 0.1, 0.15)) for z in (-lz / 2, lz / 2)]
    return []  # trampolines, pads: flat
