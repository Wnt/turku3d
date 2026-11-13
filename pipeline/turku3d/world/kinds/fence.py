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
from functools import lru_cache
from pathlib import Path

import numpy as np
import shapely

from ..api import Area, Instances, Output, Stub
from ..frame import to_engine
from ..objects import registers
from ..objects.common import GALVANISED, GRANITE, describe, interaction, painted_metal, painted_wood, yaw_of_run, unique
from ..objects.mesh import Mesh

VERSION = 1
PANEL = {"board": 2.4, "picket": 2.4, "iron": 2.0, "mesh": 2.0, "stone-wall": 2.0, "railing": 2.0}
LIMITS = {"wood-fence": (0.7, 2.6), "stone-fence": (0.3, 3.0), "railing": (0.8, 1.3)}
DEFAULT_HEIGHT = {"wood-fence": 1.6, "stone-fence": 0.9, "railing": 1.0}
PRESENT = {"wood-fence": 0.3, "stone-fence": 0.2, "railing": 0.1}  # share of the line the lidar must see standing
OVERGROWN = 0.15  # m under the upper limit: a wood fence measured this high is overgrown, its height not measured
SCAN_DATE = "2021-04-17"
BURY = 0.2  # m a panel reaches below its base, so stepped panels leave no gap on a slope
OPEN = 4.0  # lidar returns per metre of fence under which it is open bars, not boards (boards: 20 to 35, bars: about 1)
IRON = "#1b1c1e"
# Fence paints of the wooden-house blocks. The scan's colours come from above (shadow, ground beside the fence) and
# only tell a warm paint from the rest, so a board fence is cream, the commonest paint, unless they are clearly warm.
PAINTS = {"cream": "#d8d0b6", "ochre": "#c9a35a", "red": "#8c3b2c", "brown": "#6b4a36"}
DEFAULT_WOOD = PAINTS["cream"]
OPEN_TYPES = ("bars", "metal", "railing", "chain_link", "wire")  # OSM fence_type values of a fence one sees through
MAPPED = 1.5  # m: a register line this near a mapped fence along its length is that fence
GATE_REACH = 2.0  # m from a mapped gate to the fence line it stands in
GATE_WIDTH = {"drive": 3.0, "walk": 1.0}  # m: a double gate across a driveway, a single leaf across a path
DRIVES = ("service", "residential", "unclassified", "track", "living_street")


def _paint(rgb: np.ndarray | None) -> tuple[str, bool]:
    """A paint of the palette from the scan's own colours of the fence returns, when they are clearly warm."""
    if rgb is None or len(rgb) < 12:
        return DEFAULT_WOOD, False
    r, g, b = (float(v) / 255 for v in np.median(rgb, axis=0))
    h, s, v = colorsys.rgb_to_hsv(r, g, b)
    if s < 0.28 or v < 0.25 or not (h <= 0.14 or h >= 0.95):
        return DEFAULT_WOOD, False
    if h >= 0.08:
        return PAINTS["ochre"], True
    return (PAINTS["red"] if v >= 0.45 else PAINTS["brown"]), True


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
    mesh.box("cap", wood, [0, height + 0.014, -0.012], [length, 0.028, 0.12])
    return mesh


def iron_panel(height: float) -> Mesh:
    """An open steel fence, 2 m long: square bars between a top and a bottom rail, painted black."""
    steel = painted_metal(IRON)
    mesh = Mesh()
    length, pitch, bar = PANEL["iron"], 0.11, 0.014
    count = round(length / pitch)
    for k in range(count):
        x = -length / 2 + pitch * (k + 0.5)
        mesh.box("bars", steel, [x, (0.12 + height) / 2, 0], [bar, height - 0.12, bar])
    for y in (0.14, height - 0.06):
        mesh.box("rails", steel, [0, y, 0], [length, 0.03, 0.03])
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


def mesh_panel(height: float, colour: str) -> Mesh:
    """A wire-mesh or chain-link fence, 2 m long: a top rail and a grid of wires one sees through, galvanised or
    coated in the colour read for it. (Read from a panorama only: the scan and the register do not tell it.)"""
    wire = painted_metal(colour)
    mesh = Mesh()
    length, pitch, thick = PANEL["mesh"], 0.2, 0.006
    mesh.tube("rails", wire, [[-length / 2, height, 0], [length / 2, height, 0]], 0.02, sides=6)
    for k in range(round(length / pitch)):
        mesh.box("wires", wire, [-length / 2 + pitch * (k + 0.5), (0.05 + height) / 2, 0], [thick, height - 0.05, thick])
    for k in range(max(int((height - 0.1) / pitch), 1)):
        mesh.box("wires", wire, [0, 0.1 + pitch * k, 0], [length, thick, thick])
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
    elif family == "mesh":
        mesh.tube("post", painted_metal(colour), [[0, -BURY, 0], [0, height + 0.02, 0]], 0.025, sides=8)
    elif family == "iron":
        mesh.box("post", painted_metal(IRON), [0, (height + 0.04 - BURY) / 2, 0], [0.05, height + 0.04 + BURY, 0.05])
    else:
        mesh.box("post", painted_wood(colour), [0, (height + 0.08 - BURY) / 2, -0.06], [0.1, height + 0.08 + BURY, 0.1])
    return mesh


@lru_cache(maxsize=256)
def gate(leaf: str, height: float, width: float, colour: str) -> tuple:
    """A gate in a fence line, its length along +X and centred: two posts that stand above the fence and one leaf
    (two when it is wider than 2 m). A board leaf is thicker than the fence's boards and rails and an iron leaf's
    frame thicker than its rails, so the fence the register draws through the opening is inside the gate."""
    mesh = Mesh()
    material = painted_metal(IRON) if leaf == "iron" else painted_wood(colour)
    thick = 0.08 if leaf == "iron" else 0.14
    for side in (-1, 1):
        mesh.box("posts", material, [side * (width / 2 + thick / 2), (height + 0.25 - BURY) / 2, -0.015], [thick, height + 0.25 + BURY, thick])
    leaves = 2 if width > 2.0 else 1
    span = width / leaves
    for k in range(leaves):
        x = -width / 2 + span * (k + 0.5)
        if leaf == "iron":
            for y in (0.12, height - 0.04):
                mesh.box("frame", material, [x, y, 0], [span - 0.02, 0.045, 0.045])
            for edge in (-1, 1):
                mesh.box("frame", material, [x + edge * (span / 2 - 0.033), height / 2 + 0.04, 0], [0.045, height - 0.2, 0.045])
            count = max(round((span - 0.14) / 0.11), 1)
            for j in range(count):
                mesh.box("bars", material, [x - (span - 0.14) / 2 + (span - 0.14) * (j + 0.5) / count, height / 2 + 0.04, 0], [0.02, height - 0.2, 0.02])
        else:
            mesh.box("leaf", material, [x, height / 2 + 0.05, -0.015], [span - 0.02, height - 0.06, 0.11])
            for y in (0.3, height - 0.25):  # the ledges a board gate is nailed on, proud of its face
                mesh.box("ledges", material, [x, y, 0.05], [span - 0.06, 0.12, 0.03])
    return tuple(mesh.parts())


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
        gates, mapped = registers.mapped_barriers(sources, area.bbox(GATE_REACH + 2.0))
        stubs = []
        built = []  # (line, facts) of the fences a gate can stand in
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
                if kind == "wood-fence" and height > high - OVERGROWN:
                    # What stands this high on a garden fence's line is the planting on it, not the fence (four of
                    # four such lines read from panoramas: 1.0 to 1.65 m, or no fence).
                    height = DEFAULT_HEIGHT[kind]
                    guessed.append("height")
            else:
                height = DEFAULT_HEIGHT[kind]
                guessed.append("height")
            family = {"stone-fence": "stone-wall", "railing": "railing"}.get(kind) or ("board" if height >= 1.25 else "picket")
            guessed.append("family")
            near = np.zeros((0, 7))
            path = shapely.LineString(line)
            samples = [path.interpolate(t, normalized=True) for t in (0.25, 0.5, 0.75)]
            tags = next((row["tags"] for row in mapped if all(row["line"].distance(point) <= MAPPED for point in samples)), {})
            if kind == "wood-fence":
                points = lidar.box(box[0] - 0.4, box[1] - 0.4, box[2] + 0.4, box[3] + 0.4, rgb=True)
                points = points[(points[:, 2] > 0.3) & (points[:, 2] < 3.0) & (points[:, 3] != 5)]
                near = points[shapely.distance(path, shapely.points(points[:, :2])) <= 0.3] if len(points) else near
                density = len(near) / max(path.length, 0.5)
                # A mapper's fence type decides between bars and boards; without one, how much the scan sees does.
                see_through = tags["fence_type"] in OPEN_TYPES if "fence_type" in tags else density < OPEN and path.length >= 4.0
                if "fence_type" in tags:
                    guessed.remove("family")
                    if not see_through and tags["fence_type"] == "wood":
                        guessed.append("family")  # boards or pickets is still read from the height
                if see_through:
                    # Boards return the scan all along; a fence it barely sees is open bars. What stands higher over
                    # such a line is the planting behind it, so its height is read from the low returns only.
                    family = "iron"
                    low_returns = near[near[:, 2] < 1.6, 2]
                    height = float(np.clip(np.percentile(low_returns, 70), 0.8, 1.3)) if len(low_returns) >= 5 else 1.0
                    if len(low_returns) < 5:
                        guessed.append("height")
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
            if tags:
                facts["osm"] = tags
            if kind == "wood-fence":
                facts["measured"]["lidar"]["returns_per_m"] = round(float(density), 1)
                if family == "iron":
                    facts["colour"] = IRON
                else:
                    body = near[(near[:, 2] > 0.5) & (near[:, 2] < height + 0.2)]
                    facts["colour"], _ = _paint(body[:, 4:7] if len(body) else None)
                    facts["measured"]["lidar"]["rgb"] = [int(v) for v in np.median(body[:, 4:7], axis=0)] if len(body) else None
                guessed.append("colour")
            facts["guessed"] = guessed
            facts["break"] = "panel"
            behaviour = "fixed" if family == "stone-wall" else "breakable"
            facts["interaction"] = interaction(behaviour, "box", [PANEL[family], height, 0.4 if family == "stone-wall" else 0.1],
                                               None if behaviour == "fixed" else 18.0 * height, per="panel")
            stubs.append(Stub(f"fence/{row['id']}", tuple(float(v) for v in box), [row["source"]], facts))
            if family in ("board", "picket", "iron"):
                built.append((shapely.LineString(np.array(facts["line"])[:, :2]), facts))
        for row in gates:
            # A mapped gate stands in the fence line it is on, as tall and of the same paint; one that is on no
            # fence (a gateway through a building, a boom) is not drawn.
            point = shapely.Point(row["e"], row["n"])
            if not built or not area.touches((row["e"] - 0.5, row["n"] - 0.5, row["e"] + 0.5, row["n"] + 0.5)):
                continue
            path, fence = min(built, key=lambda item: item[0].distance(point))
            if path.distance(point) > GATE_REACH:
                continue
            along = path.project(point)
            a, b = path.interpolate(max(along - 0.5, 0.0)), path.interpolate(min(along + 0.5, path.length))
            foot = path.interpolate(along)
            ground = registers.ground_height(sources, foot.x, foot.y)
            if not math.isfinite(ground) or a.distance(b) < 0.1:
                continue
            width = GATE_WIDTH["drive" if row["way"] in DRIVES else "walk"]
            height = fence["height"]
            facts = {"at": [round(foot.x, 2), round(foot.y, 2), round(ground, 2)], "type": "gate", "family": "gate",
                     "leaf": "iron" if fence["family"] == "iron" else "board", "height": height, "width": width,
                     "bearing": round(math.degrees(math.atan2(b.x - a.x, b.y - a.y)) % 360, 1), "colour": fence.get("colour", DEFAULT_WOOD),
                     "osm": {"way": row["way"], **{k: v for k, v in row["tags"].items() if k in ("material", "access", "height")}},
                     "basis": "OSM gate node moved onto the register fence line it stands in; as tall as that fence",
                     "guessed": ["width", "leaf"],
                     "interaction": interaction("breakable", "box", [width, height, 0.14], 25.0 * height * width)}
            stubs.append(Stub(f"fence/{row['id']}", (foot.x - width, foot.y - width, foot.x + width, foot.y + width), [row["source"]], facts))
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
        return unique(stubs)

    def describe(self, stub: Stub, stored: dict | None) -> dict:
        return describe(self.name, VERSION, stub, stored)

    def compile(self, description: dict) -> Output:
        family = description.get("family", "board")
        height = round(float(description.get("height", 1.6)) * 10) / 10
        colour = description.get("colour", DEFAULT_WOOD)
        block = description.get("interaction") or {}
        entity = description["id"]
        if family == "none":  # a reader saw no fence on the register's line
            return Output(tuple(float(v) for v in to_engine(*description["at"])), [], [])
        if family == "gate-post":
            e, n, h = description["at"]
            item = Instances(gate_post(height).parts(), np.array([[0.0, 0.0, 0.0, 0.0, 1.0, 1.0, 1.0]]), ids=[entity],
                             behaviour=block.get("behaviour"), collision=block.get("collision"))
            return Output(tuple(float(v) for v in to_engine(e, n, h)), [], [item])
        if family == "gate":
            e, n, h = description["at"]
            width = round(float(description.get("width", 1.0)) * 4) / 4
            item = Instances(list(gate(description.get("leaf", "board"), height, width, colour)),
                             np.array([[0.0, 0.0, 0.0, round(yaw_of_run(float(description.get("bearing", 0.0))), 4), 1.0, 1.0, 1.0]]),
                             ids=[entity], behaviour=block.get("behaviour"), collision=block.get("collision"), mass=block.get("mass"))
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
                 "iron": lambda: iron_panel(height), "mesh": lambda: mesh_panel(height, colour), "stone-wall": lambda: wall_panel(height), "railing": lambda: railing_panel(height)}.get(family, lambda: board_panel(height, colour))()
        items = [Instances(asset.parts(), np.round(np.array(panels), 4), ids=[f"{entity}#p{k}" for k in range(len(panels))],
                           behaviour=block.get("behaviour"), collision=block.get("collision"))]
        if family != "stone-wall":
            items.append(Instances(post(family, height, colour).parts(), np.round(np.array(posts), 4),
                                   ids=[f"{entity}#q{k}" for k in range(len(posts))], behaviour=block.get("behaviour"),
                                   collision={"shape": "box", "size": [0.1, height, 0.1]}))
        return Output(tuple(origin), [], items)
