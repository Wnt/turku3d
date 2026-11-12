"""Fences, stone fences, railings and gate posts: one entity per surveyed run, built from panels and posts.

The base map (kanta:Rakenne) draws fences and railings as lines with a material (wood, stone) and gate posts as
points; it knows neither height, type nor colour. The lidar gives the height of what stands on the line and whether
anything stands there at all; type and colour are defaults until a panorama reading stores them (`family`,
`height`, `colour`, `plinth`, `gates`). Every panel and post is its own instance row, so a run breaks per panel.

Families: `board` (closed vertical boards on rails), `picket` (spaced pointed pickets), `stone-wall` (a stone or
rendered fence wall), `railing` (steel tube rails on posts), `gate-post` (a pillar beside a gateway).
Schema: docs/specs/street-objects.md.
"""

from __future__ import annotations

import colorsys
import math
from pathlib import Path

import numpy as np

from ..api import Area, Instances, Output, Stub
from ..frame import to_engine
from ..objects import registers
from ..objects.common import GALVANISED, GRANITE, describe, interaction, painted_wood, yaw_of_run
from ..objects.mesh import Mesh

VERSION = 1
PANEL = {"board": 2.4, "picket": 2.4, "stone-wall": 2.0, "railing": 2.0}
LIMITS = {"wood-fence": (0.7, 2.6), "stone-fence": (0.3, 3.0), "railing": (0.8, 1.3)}
DEFAULT_HEIGHT = {"wood-fence": 1.6, "stone-fence": 0.9, "railing": 1.0}
PRESENT = {"wood-fence": 0.3, "stone-fence": 0.2, "railing": 0.1}  # share of the line the lidar must see standing
DEFAULT_WOOD = "#8a8074"
SCAN_DATE = "2021-04-17"
BURY = 0.2  # m a panel reaches below its base, so stepped panels leave no gap on a slope


def _paint(rgb: np.ndarray | None) -> tuple[str, bool]:
    """A paint colour from the scan's own colours of the fence returns, when they have a hue at all."""
    if rgb is None or len(rgb) < 12:
        return DEFAULT_WOOD, False
    r, g, b = (float(v) / 255 for v in np.median(rgb, axis=0))
    h, s, v = colorsys.rgb_to_hsv(r, g, b)
    if s < 0.18 or v < 0.15:
        return DEFAULT_WOOD, False
    r, g, b = colorsys.hsv_to_rgb(h, min(s * 1.15, 0.8), min(max(v * 1.25, 0.35), 0.8))
    return f"#{round(r * 255):02x}{round(g * 255):02x}{round(b * 255):02x}", True


def _joints(line: np.ndarray, panel: float) -> np.ndarray:
    """The line's vertices with each straight piece divided into equal panels of about the nominal length."""
    out = [line[0]]
    for a, b in zip(line[:-1], line[1:]):
        length = float(np.hypot(*(b - a)))
        if length < 0.05:
            continue
        count = max(1, round(length / panel))
        out += [a + (b - a) * k / count for k in range(1, count + 1)]
    return np.array(out)


def board_panel(height: float, colour: str) -> Mesh:
    """Closed vertical boards on two rails, 2.4 m long, centred, the boards' face towards +Z."""
    wood = painted_wood(colour)
    mesh = Mesh()
    length, pitch, board = PANEL["board"], 0.1, 0.095
    count = round(length / pitch)
    for k in range(count):
        x = -length / 2 + pitch * (k + 0.5)
        mesh.box("boards", wood, [x, (height - BURY) / 2, 0.011], [board, height + BURY, 0.022])
    for y in (0.3, height - 0.25):
        mesh.box("rails", wood, [0, y, -0.035], [length, 0.095, 0.045])
    return mesh


def picket_panel(height: float, colour: str) -> Mesh:
    wood = painted_wood(colour)
    mesh = Mesh()
    length, pitch, width = PANEL["picket"], 0.12, 0.07
    count = round(length / pitch)
    for k in range(count):
        x = -length / 2 + pitch * (k + 0.5)
        outline = [(x - width / 2, 0.06), (x + width / 2, 0.06), (x + width / 2, height - 0.05), (x, height), (x - width / 2, height - 0.05)]
        mesh.prism("pickets", wood, outline, 0.0, 0.022)
    for y in (0.25, height - 0.25):
        mesh.box("rails", wood, [0, y, -0.035], [length, 0.07, 0.045])
    return mesh


def wall_panel(height: float) -> Mesh:
    mesh = Mesh()
    length = PANEL["stone-wall"]
    mesh.box("wall", GRANITE, [0, (height - 0.06 - BURY) / 2, 0], [length, height - 0.06 + BURY, 0.4])
    mesh.box("cap", GRANITE, [0, height - 0.03, 0], [length, 0.06, 0.46])
    return mesh


def railing_panel(height: float) -> Mesh:
    mesh = Mesh()
    length = PANEL["railing"]
    for y in (height, height * 0.5):
        mesh.tube("rails", GALVANISED, [[-length / 2, y, 0], [length / 2, y, 0]], 0.024, sides=8)
    return mesh


def post(family: str, height: float, colour: str) -> Mesh:
    mesh = Mesh()
    if family == "railing":
        mesh.tube("post", GALVANISED, [[0, -BURY, 0], [0, height + 0.02, 0]], 0.024, sides=8)
    else:
        mesh.box("post", painted_wood(colour), [0, (height + 0.08 - BURY) / 2, -0.06], [0.1, height + 0.08 + BURY, 0.1])
    return mesh


def gate_post(height: float) -> Mesh:
    mesh = Mesh()
    mesh.box("pillar", GRANITE, [0, (height - BURY) / 2, 0], [0.36, height + BURY, 0.36])
    mesh.box("cap", GRANITE, [0, height + 0.04, 0], [0.44, 0.08, 0.44])
    return mesh


class Fence:
    name = "fence"
    code = (Path(__file__).parent.parent / "objects",)

    def discover(self, area: Area, sources) -> list[Stub]:
        lines, posts = registers.barriers(sources, area.bbox(2.0))
        lidar = registers.lidar_of(sources)
        stubs = []
        for row in lines:
            line = np.array(row["line"])
            box = (line[:, 0].min(), line[:, 1].min(), line[:, 0].max(), line[:, 1].max())
            if not area.touches(box):
                continue
            kind = row["type"]
            profile = lidar.profile(line, step=1.0, reach=0.35)
            standing = profile[np.isfinite(profile) & (profile > 0.4)]
            coverage = len(standing) / max(len(profile), 1)
            newer = row["surveyed"] > SCAN_DATE
            if coverage < PRESENT[kind] and not newer and line.shape[0] and float(np.hypot(*(line[-1] - line[0]))) > 1.5:
                continue  # the scan saw nothing standing on a line surveyed before it
            low, high = LIMITS[kind]
            guessed = []
            if len(standing) >= 2 and coverage >= PRESENT[kind]:
                height = float(np.clip(np.percentile(standing, 60), low, high))
            else:
                height = DEFAULT_HEIGHT[kind]
                guessed.append("height")
            family = {"stone-fence": "stone-wall", "railing": "railing"}.get(kind) or ("board" if height >= 1.25 else "picket")
            guessed.append("family")
            joints = _joints(line, PANEL[family])
            ground = np.array([registers.ground_height(sources, e, n) for e, n in joints])
            if not np.isfinite(ground).all():
                continue
            facts = {
                "at": [round(float(joints[0, 0]), 2), round(float(joints[0, 1]), 2), round(float(ground[0]), 2)],
                "type": kind, "family": family, "height": round(height, 2),
                "line": [[round(float(e), 2), round(float(n), 2), round(float(h), 2)] for (e, n), h in zip(joints, ground)],
                "measured": {"lidar": {"coverage": round(coverage, 2), "steps": int(len(profile)),
                                       "height_p60": round(float(np.percentile(standing, 60)), 2) if len(standing) else None}},
                "register": {"code": row["code"], "surveyed": row["surveyed"]},
            }
            if kind == "wood-fence":
                points = lidar.box(box[0] - 0.4, box[1] - 0.4, box[2] + 0.4, box[3] + 0.4, rgb=True)
                near = points[(points[:, 2] > 0.5) & (points[:, 2] < height + 0.2)]
                if len(near):
                    import shapely

                    distance = shapely.distance(shapely.LineString(line), shapely.points(near[:, :2]))
                    near = near[distance <= 0.3]
                colour, hued = _paint(near[:, 4:7] if len(near) else None)
                facts["colour"] = colour
                guessed.append("colour")
                facts["measured"]["lidar"]["rgb"] = [int(v) for v in np.median(near[:, 4:7], axis=0)] if len(near) else None
            facts["guessed"] = guessed
            facts["break"] = "panel"
            behaviour = "fixed" if family == "stone-wall" else "breakable"
            facts["interaction"] = interaction(behaviour, "box", [PANEL[family], height, 0.4 if family == "stone-wall" else 0.1],
                                               None if behaviour == "fixed" else 18.0 * height, per="panel")
            stubs.append(Stub(f"fence/{row['id']}", tuple(float(v) for v in box), [row["source"]], facts))
        for row in posts:
            e, n = row["e"], row["n"]
            if not area.touches((e - 0.5, n - 0.5, e + 0.5, n + 0.5)):
                continue
            around = lidar.around(e, n, 0.45)
            around = around[(around[:, 2] < 3.2) & (around[:, 3] != 5)]
            ground = registers.ground_height(sources, e, n)
            if not math.isfinite(ground):
                continue
            height = float(np.clip(np.percentile(around[:, 2], 90), 0.8, 3.0)) if len(around) >= 3 else 1.6
            facts = {"at": [e, n, round(ground, 2)], "type": "gate-post", "family": "gate-post", "height": round(height, 2),
                     "guessed": ["family"] + ([] if len(around) >= 3 else ["height"]),
                     "measured": {"lidar": {"returns": int(len(around))}},
                     "interaction": interaction("fixed", "box", [0.36, height, 0.36])}
            stubs.append(Stub(f"fence/{row['id']}", (e - 0.5, n - 0.5, e + 0.5, n + 0.5), [row["source"]], facts))
        return stubs

    def describe(self, stub: Stub, stored: dict | None) -> dict:
        return describe(self.name, VERSION, stub, stored)

    def compile(self, description: dict) -> Output:
        family = description.get("family", "board")
        height = round(float(description.get("height", 1.6)) * 10) / 10
        colour = description.get("colour", DEFAULT_WOOD)
        block = description.get("interaction") or {}
        entity = description["id"]
        if family == "gate-post":
            e, n, h = description["at"]
            item = Instances(gate_post(height).parts(), np.array([[0.0, 0.0, 0.0, 0.0, 1.0, 1.0, 1.0]]), ids=[entity],
                             behaviour=block.get("behaviour"), collision=block.get("collision"))
            return Output(tuple(float(v) for v in to_engine(e, n, h)), [], [item])
        line = np.array(description["line"], dtype=float)
        origin = np.array(to_engine(*line[0]), dtype=float)
        points = np.column_stack(to_engine(line[:, 0], line[:, 1], line[:, 2])) - origin
        nominal = PANEL.get(family, 2.4)
        panels, posts = [], []
        for k, (a, b) in enumerate(zip(points[:-1], points[1:])):
            run = b - a
            length = float(math.hypot(run[0], run[2]))
            if length < 0.05:
                continue
            bearing = math.degrees(math.atan2(run[0], -run[2]))
            mid = (a + b) / 2
            panels.append([mid[0], min(a[1], b[1]) if family == "stone-wall" else mid[1], mid[2], yaw_of_run(bearing), length / nominal, 1.0, 1.0])
            posts.append([a[0], a[1], a[2], yaw_of_run(bearing), 1.0, 1.0, 1.0])
        if not panels:
            return Output(tuple(origin), [], [])
        last = points[-1]
        posts.append([last[0], last[1], last[2], posts[-1][3], 1.0, 1.0, 1.0])
        asset = {"board": lambda: board_panel(height, colour), "picket": lambda: picket_panel(height, colour),
                 "stone-wall": lambda: wall_panel(height), "railing": lambda: railing_panel(height)}.get(family, lambda: board_panel(height, colour))()
        items = [Instances(asset.parts(), np.round(np.array(panels), 4), ids=[f"{entity}#p{k}" for k in range(len(panels))],
                           behaviour=block.get("behaviour"), collision=block.get("collision"))]
        if family != "stone-wall":
            items.append(Instances(post(family, height, colour).parts(), np.round(np.array(posts), 4),
                                   ids=[f"{entity}#q{k}" for k in range(len(posts))], behaviour=block.get("behaviour"),
                                   collision={"shape": "box", "size": [0.1, height, 0.1]}))
        return Output(tuple(origin), [], items)
