"""Traffic sign posts: one entity per real support, carrying the register's plates as Finnish sign faces.

The sign register (GIS:Liikennemerkit) has one row per plate and no bearing. `discover` groups the rows that share a
support, finds the support in the lidar (its true axis and height, or the lamp pole the plates hang on) and faces
the plates against the traffic that passes on their side of the street. A stored description replaces any of it
(the reading of a panorama: `plates`, `facing`, `support`). Schema: docs/specs/street-objects.md.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np

from functools import lru_cache

from ..api import Area, Instances, Output, Stub
from ..frame import to_engine
from ..objects import registers, signfaces
from ..objects.common import ALUMINIUM, GALVANISED, SIGN_BACK, box_of, describe, interaction, sign_material, yaw_of_bearing, unique
from ..objects.mesh import Mesh

VERSION = 1
SAME_POST = 0.5  # m: register plates this close share a support
PLATE_REACH = 1.2  # m: an additional plate alone joins the nearest main sign this close
TWIN = 0.35  # m: the same code this close is the other face of a two-sided sign, or the row twice
GIVE_WAY = ("B5", "B6")  # give way, stop
TWO_SIDED = ("E1", "E6", "E24", "E25", "E26", "E27", "F24")
HOST_REACH = 0.8  # m: a register lamp pole this close to the plates carries them
POST_RADIUS = 0.03
CLEAR = 2.2  # m: lowest plate edge above the ground when the post was not measured
GAP = 0.03
PLATE_THICKNESS = 0.003
STANDOFF = 0.02  # m between the post and the back of a plate: the clamp rail
CLASS_ORDER = "BCDEAFG"


def _group(rows: list[dict]) -> list[list[dict]]:
    parent = list(range(len(rows)))

    def root(k: int) -> int:
        while parent[k] != k:
            parent[k] = parent[parent[k]]
            k = parent[k]
        return k

    def near(a: dict, b: dict) -> float:
        return math.hypot(a["e"] - b["e"], a["n"] - b["n"])

    for i, a in enumerate(rows):
        for j in range(i):
            if near(a, rows[j]) <= SAME_POST:
                parent[root(i)] = root(j)
    sizes: dict[int, int] = {}
    for k in range(len(rows)):
        sizes[root(k)] = sizes.get(root(k), 0) + 1
    for i, a in enumerate(rows):
        if a["code"][:1] != "H" or sizes[root(i)] > 1:
            continue
        mains = [(near(a, b), j) for j, b in enumerate(rows) if b["code"][:1] != "H" and near(a, b) <= PLATE_REACH]
        if mains:
            parent[root(i)] = root(min(mains)[1])
    groups: dict[int, list[dict]] = {}
    for k, row in enumerate(rows):
        groups.setdefault(root(k), []).append(row)
    return list(groups.values())


def _plates(members: list[dict]) -> list[dict]:
    """The plates of one support, top first: main signs by class, then the additional plates; a second row of a
    two-sided code becomes the back face."""
    members = sorted(members, key=lambda r: (r["code"][:1] == "H", CLASS_ORDER.find(r["code"][:1]) % 99, r["code"], r["id"]))
    plates: list[dict] = []
    for row in members:
        twin = next((p for p in plates if p["code"] == row["code"] and math.hypot(p["e"] - row["e"], p["n"] - row["n"]) <= TWIN), None)
        plate = {"code": row["code"], "register": row["id"], "e": row["e"], "n": row["n"]}
        if row["label"]:
            plate["label"] = row["label"]
        if row["note"]:
            plate["note"] = row["note"]
        if twin is not None:
            if row["code"].split(".")[0] not in TWO_SIDED or twin.get("turn"):
                continue  # the same plate recorded twice
            plate["turn"] = 180
        plates.append(plate)
    # A crossing sign that shares its post with a give-way or stop sign is the far-side sign of the crossing at the
    # mouth of that street: it faces the junction, back to back with the sign for the street's own approach
    # (portsa_03: the photo shows the crossing sign's face and the triangle's back). A rule, so the turn is marked.
    yields = any(plate["code"].split(".")[0] in GIVE_WAY for plate in plates)
    crossings = [plate for plate in plates if plate["code"].split(".")[0] == "E1"]
    if yields and len(crossings) == 1:
        crossings[0]["turn"] = 180
        crossings[0]["turn_basis"] = "rule: on a give-way post the crossing sign faces the junction"
    blue = False
    for plate in plates:
        if plate["code"][:1] != "H":
            blue = signfaces.is_blue(plate["code"])
        elif blue or (plate.get("note") or "").strip().lower() == "sininen":
            plate["blue"] = True
    for plate in plates:
        del plate["e"], plate["n"]
    return plates


class SignPost:
    name = "signpost"
    code = (Path(__file__).parent.parent / "objects",)

    def discover(self, area: Area, sources) -> list[Stub]:
        box = area.bbox(3.0)
        rows = [r for r in registers.signs(sources, box) if signfaces.is_sign(r["code"]) or r["code"] == "999"]
        if not rows:
            return []
        lidar = registers.lidar_of(sources)
        lamps = registers.lamp_posts(sources, area.bbox(6.0))
        streets = registers.Streets(sources, box)
        stubs = []
        for members in _group(rows):
            mains = [r for r in members if r["code"][:1] != "H"] or members
            e, n = float(np.mean([r["e"] for r in mains])), float(np.mean([r["n"] for r in mains]))
            if not area.touches(box_of(e, n, 0.5)):
                continue
            plates = _plates(members)
            known = [p for p in plates if p["code"] != "999"]
            measured = lidar.pole_top(e, n, search=1.0)
            support: dict = {"type": "post", "radius": POST_RADIUS}
            facts: dict = {}
            if measured:
                facts["measured"] = {"lidar": measured}
            host = min(lamps, key=lambda l: math.hypot(l["e"] - e, l["n"] - n), default=None)
            if host and math.hypot(host["e"] - e, host["n"] - n) <= HOST_REACH + (0.7 if measured and measured["top"] > 5.5 else 0.0):
                support = {"type": "host", "host": f"lamp/{host['id']}", "radius": 0.08}
                e, n = host["e"], host["n"]
            elif measured and measured["top"] > 5.5:
                # A tall pole the lamp register does not list (a signal mast, a wire pole): the plates hang on it.
                support = {"type": "host", "host": None, "radius": 0.08}
                e, n = e + measured["offset"][0], n + measured["offset"][1]
            elif measured:
                e, n = e + measured["offset"][0], n + measured["offset"][1]
                support["height"] = measured["top"]
            elif not known:
                continue  # an unknown sign with no post in the lidar: nothing certain to draw
            facing = streets.sign_facing(e, n) or {"bearing": 0.0, "method": "none"}
            facing["conf"] = 0.5 if facing["method"].startswith("approach") else 0.2
            ground = registers.ground_height(sources, e, n)
            if not math.isfinite(ground):
                continue
            mass = 12.0 + 4.0 * len(plates)
            behaviour = "fixed" if support["type"] == "host" else "knock-over"
            facts.update({
                "at": [round(e, 2), round(n, 2), round(ground, 2)],
                "support": support, "facing": facing, "plates": plates,
                "placement": "register position" + (", post axis from lidar" if measured else ", post not found in lidar"),
                "interaction": interaction(behaviour, "cylinder", [0.3, 3.2], mass,
                                           **({"attached_to": support["host"]} if support.get("host") else {})),
            })
            key = min((p["register"] for p in plates), key=lambda v: (len(v), v))
            refs = [{"source": "turku-wfs:GIS:Liikennemerkit", "key": p["register"]} for p in plates]
            stubs.append(Stub(f"signpost/s{key}", box_of(e, n, 0.5), refs, facts))
        return unique(stubs)

    def describe(self, stub: Stub, stored: dict | None) -> dict:
        return describe(self.name, VERSION, stub, stored)

    def compile(self, description: dict) -> Output:
        """The post is one instance row and every plate another (a plate of one code and text is one shared asset
        in the whole city); the rows of one entity move as one body."""
        support = description.get("support") or {"type": "post", "radius": POST_RADIUS}
        radius = float(support.get("radius", POST_RADIUS))
        hosted = support.get("type") == "host"
        entity = description["id"]
        block = description.get("interaction") or {}
        behaviour, mass = block.get("behaviour"), block.get("mass")
        stacks: dict[int, list] = {}
        for index, plate in enumerate(description.get("plates", [])):
            text = (plate.get("text") or plate.get("note") or "").strip()
            key = (plate["code"], plate.get("label", ""), text, bool(plate.get("blue")))
            if plate_face(*key) is not None:
                stacks.setdefault(int(plate.get("turn", 0)) % 360, []).append((index, key))
        # One column down the post: plates that face different ways are clamped above one another, the turned ones on
        # top (the crossing sign over the back of the give-way triangle, as at the junctions photographed); only the
        # two faces of a two-sided sign share a height, back to back.
        slots: list[tuple[float, dict]] = []  # (plate height, {turn: (index, key)})
        for turn in sorted(stacks, key=lambda turn: (turn == 0, turn)):
            for index, key in stacks[turn]:
                twin = next((held for _, held in slots if turn not in held and any(other == key for _, other in held.values())), None)
                if twin is None:
                    slots.append((plate_face(*key).size[1], twin := {}))
                twin[turn] = (index, key)
        tallest = sum(height for height, _ in slots) + GAP * (len(slots) - 1) if slots else 0.6
        top = float(support.get("height") or 0.0)
        if hosted or not 2.0 <= top <= 4.8 or top - 0.04 - tallest < 1.0:
            top = float(description.get("clear", CLEAR)) + tallest + 0.04
        top = round(top * 10) / 10
        bearing = float((description.get("facing") or {}).get("bearing", 0.0))
        e, n, h = description["at"]
        items = []
        if not hosted:
            items.append(Instances(post_asset(top, radius), np.array([[0.0, 0.0, 0.0, 0.0, 1.0, 1.0, 1.0]]), ids=[f"{entity}#post"],
                                   behaviour=behaviour, collision={"shape": "cylinder", "size": [round(radius, 3), top]}, mass=mass))
        rows: dict[tuple, list] = {}
        y = top - 0.04
        for height, held in slots:
            y -= height
            for turn, (index, key) in sorted(held.items()):
                yaw = yaw_of_bearing(bearing + turn)
                out = np.array([math.sin(yaw), 0.0, math.cos(yaw)]) * (radius + STANDOFF)
                rows.setdefault(key, []).append(([out[0], y, out[2], yaw, 1.0, 1.0, 1.0], f"{entity}#plate{index}"))
            y -= GAP
        for key, placed in rows.items():
            width, height = plate_face(*key).size
            items.append(Instances(plate_asset(*key), np.round(np.array([row for row, _ in placed]), 4), ids=[name for _, name in placed],
                                   behaviour=behaviour, collision={"shape": "box", "size": [round(width, 3), round(height, 3), 0.03]},
                                   mass=4.0 if mass is not None else None))
        images = {image.url: image for image in (plate_image(*key) for stack in stacks.values() for _, key in stack)}
        return Output(tuple(float(v) for v in to_engine(e, n, h)), [], items, images=list(images.values()))


@lru_cache(maxsize=4096)
def plate_face(code: str, label: str, text: str, blue: bool):
    return signfaces.face(code, label, text, blue)


@lru_cache(maxsize=256)
def _post_parts(top: float, radius: float) -> tuple:
    mesh = Mesh()
    mesh.tube("post", GALVANISED, [[0, -0.2, 0], [0, top, 0]], radius, sides=10)
    return tuple(mesh.parts())


def post_asset(top: float, radius: float) -> list:
    return list(_post_parts(round(top, 2), round(radius, 3)))


@lru_cache(maxsize=4096)
def _plate(code: str, label: str, text: str, blue: bool) -> tuple:
    """One plate as a closed 3 mm slab and the picture of its face: origin at the bottom centre of its back, face
    towards +Z. The front is the outline triangulated over its border vertices with the picture mapped on it (sign
    family); back and rim are bare aluminium and meet the front at the same vertices; a clamp rail sits behind."""
    drawn = plate_face(code, label, text, blue)
    width, height = drawn.size
    x0, y0, _, y1 = drawn.outline.bounds
    picture = signfaces.face_image(drawn)
    image = signfaces.encoded(picture)
    points, triangles = signfaces.outline_mesh(drawn)
    lift = np.array([0.0, -y0])
    flat = points + lift
    front = np.column_stack([flat, np.full(len(flat), PLATE_THICKNESS)])
    back = np.column_stack([flat, np.zeros(len(flat))])
    mesh = Mesh()
    uvs = np.column_stack([(points[:, 0] - x0) / width, (y1 - points[:, 1]) / height])
    material = sign_material(drawn.code, width, height, image.url, signfaces.mean_colour(picture), text, blue)
    mesh.add("face", material, front, np.tile([0.0, 0.0, 1.0], (len(front), 1)), triangles, uvs=uvs)
    mesh.add("plate", SIGN_BACK, back, np.tile([0.0, 0.0, -1.0], (len(back), 1)), triangles[:, ::-1])
    rim = [t for a, b in signfaces.border_edges(points, triangles) for t in (
        [front[a], back[a], back[b]], [front[a], back[b], front[b]])]
    if rim:
        corners = np.array(rim).reshape(-1, 3)
        mesh.flat("plate", SIGN_BACK, corners, np.arange(len(corners)).reshape(-1, 3))
    for share in ((0.25, 0.75) if height > 0.45 else (0.5,)):
        half = min(width * 0.36, 0.22)
        mesh.box("clamp", ALUMINIUM, [0.0, height * share, -STANDOFF / 2], [half * 2, 0.035, STANDOFF])
    return tuple(mesh.parts()), image


def plate_asset(code: str, label: str, text: str, blue: bool) -> list:
    return list(_plate(code, label, text, blue)[0])


def plate_image(code: str, label: str, text: str, blue: bool):
    return _plate(code, label, text, blue)[1]
