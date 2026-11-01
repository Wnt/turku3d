"""Per-tile light list: street-light posts, lights hung over a carriageway and facade lights.

The client (``game/src/render/lights/``) draws a corona for every light record and a lamp post for
every post record; nothing about street lights is decided on the client any more.

Wire format (manifest field ``lights``; little-endian records, base64 in ``data``)::

    light record, 16 B:  float32 x, y, z   light point, engine metres
                         uint8 r, g, b     linear HDR colour x intensity / LIGHT_SCALE * 255
                         uint8 info        bits 0-3 category (CATEGORIES), bit 7 street-light flag
    post record, 16 B:   float32 x, y, z   post base on the ground
                         uint16 yaw        arm direction, yaw / 2pi * 65536 (three.js rotation.y)
                         uint8 scale       post size, scale * 100
                         uint8 style       0 = arm post (POST_SHAPE)

``count`` light records come first, then ``posts`` post records; post i carries light i. Street
lights (flag set) are sorted first and ``street`` counts them, so they switch on as one group.
"""

from __future__ import annotations

import base64
import hashlib
import json
import math
import struct
from dataclasses import dataclass

import numpy as np
import shapely
from shapely.strtree import STRtree

FORMAT = "turku3d-lights"
VERSION = 1
LIGHT = struct.Struct("<fffBBBB")
POST = struct.Struct("<fffHBB")
LIGHT_SCALE = 8.0  # HDR colour x intensity range of the uint8 RGB channels
STREET_FLAG = 0x80
CATEGORIES = {"street": 0, "path": 1, "hung": 2, "wall": 3}
# Unit post of the client's LampPosts (game/src/render/lights/lampPosts.ts POST_SHAPE): base at
# the origin, arm along local +X; the light sits at (ARM, LIGHT) times the post scale.
POST_SHAPE = {"height": 8.0, "arm": 1.6, "light": 7.58}
POST_RADIUS = 0.11  # base radius of the client post (collision)
# Post scale by the class of the street it lights (roads.json class 1..3); paths and parks get
# the small post.
STREET_SCALE = (0.6, 0.75, 1.0, 1.15)
PATH_SCALE = 0.6
STREET_REACH = 15.0  # m: a lamp this close to a car street lights it
PATH_REACH = 10.0
HUNG_HEIGHT = 7.0  # m above the street for a lamp hung over the carriageway
# Linear colour and intensity per light type.
TYPES = {
    "led": ((1.0, 0.86, 0.68), 5.0),
    "sodium": ((1.0, 0.62, 0.28), 4.6),
    "path": ((1.0, 0.88, 0.74), 3.6),
    "wall": ((1.0, 0.74, 0.46), 1.8),
}
SODIUM_SHARE = 0.2  # share of street lamps drawn as older sodium heads, by position hash
# Facade lights: a search outward from the kerb at stations along every car street finds the
# first building; most such facades carry a light just in front of it.
WALL = {
    "spacing": 16.0,
    "step": 0.5,
    "reach": 14.0,
    "share": 0.7,
    "height": (3.2, 4.4),
    "separation": 4.0,
    "inset": 0.75,
}
KERB_OFFSET = (0.0, 3.2, 4.6, 7.0)  # centreline to kerb by class, two-way streets
ONEWAY_OFFSET = (0.0, 2.4, 3.4, 4.6)


@dataclass
class Light:
    x: float
    y: float
    z: float
    category: str
    kind: str  # TYPES key
    street: bool
    post: tuple | None = None  # (x, y, z, yaw, scale) of its post


def position_hash(x: float, z: float) -> float:
    """Deterministic [0, 1) value of a position at 0.5 m."""
    key = f"{round(x * 2)},{round(z * 2)}".encode()
    return int.from_bytes(hashlib.blake2b(key, digest_size=4).digest(), "little") / 2**32


def graph_edges(graph_json):
    """(LineString, flags) of every edge of an encoded roads.json graph."""
    if not graph_json:
        return []
    graph = json.loads(graph_json) if isinstance(graph_json, (str, bytes)) else graph_json
    nodes = np.asarray(graph["nodes"], dtype=float).reshape(-1, 2)
    out = []
    for edge in graph["edges"]:
        coords = [nodes[edge[0]], *np.asarray(edge[4:], dtype=float).reshape(-1, 2), nodes[edge[1]]]
        line = shapely.LineString(coords)
        if line.length > 0.05:
            out.append((line, int(edge[2])))
    return out


def _flags():
    from turku3d.roads import CAR, CLASS_SHIFT, FOOT, ONEWAY

    return CAR, CLASS_SHIFT, FOOT, ONEWAY


def post_light(x, y, z, edges, car_tree, path_tree):
    """A street-light post at (x, y, z): its arm reaches toward the street (or path) it lights."""
    car, shift, _, _ = _flags()
    point = shapely.Point(x, z)
    target, scale, category = None, PATH_SCALE, "path"
    if car_tree is not None:
        i = car_tree.nearest(point)
        line, flags = edges["car"][i]
        if line.distance(point) <= STREET_REACH:
            target = line.interpolate(line.project(point))
            scale, category = STREET_SCALE[min(3, flags >> shift)], "street"
    if target is None and path_tree is not None:
        line = edges["path"][path_tree.nearest(point)][0]
        if line.distance(point) <= PATH_REACH:
            target = line.interpolate(line.project(point))
    h = position_hash(x, z)
    if target is not None and math.hypot(target.x - x, target.y - z) > 0.05:
        ax, az = target.x - x, target.y - z
    else:
        ax, az = math.cos(h * math.tau), math.sin(h * math.tau)
    length = math.hypot(ax, az)
    ax, az = ax / length, az / length
    # three.js rotation.y maps local +X to (cos yaw, -sin yaw).
    yaw = math.atan2(-az, ax)
    arm, height = POST_SHAPE["arm"] * scale, POST_SHAPE["light"] * scale
    kind = "path" if category == "path" else ("sodium" if h < SODIUM_SHARE else "led")
    return Light(
        x + ax * arm,
        y + height,
        z + az * arm,
        category,
        kind,
        True,
        post=(x, y, z, yaw, scale),
    )


def street_edges(graph_json):
    """Car edges (class >= 1) and foot-only edges of a tile's street graph, with STRtrees."""
    car, shift, foot, _ = _flags()
    edges = {"car": [], "path": []}
    for line, flags in graph_edges(graph_json):
        if flags & car and flags >> shift >= 1:
            edges["car"].append((line, flags))
        elif flags & foot:
            edges["path"].append((line, flags))
    trees = {k: STRtree([line for line, _ in v]) if v else None for k, v in edges.items()}
    return edges, trees


def wall_lights(tile, edges, footprints, height_at, blocked=None):
    """Facade lights in front of the first building each kerb-outward search meets."""
    car, shift, _, oneway_flag = _flags()
    if not footprints:
        return []
    tree = STRtree(footprints)
    lights = []
    b = tile.bbox
    spacing, step, reach = WALL["spacing"], WALL["step"], WALL["reach"]
    for line, flags in edges["car"]:
        cls = min(3, flags >> shift)
        offset = (ONEWAY_OFFSET if flags & oneway_flag else KERB_OFFSET)[cls]
        for s in np.arange(spacing / 2, line.length, spacing):
            p = line.interpolate(float(s))
            if not (b.xmin <= p.x < b.xmax and b.zmin <= p.y < b.zmax):
                continue
            if blocked is not None and not blocked.is_empty and blocked.covers(p):
                continue
            a = line.interpolate(max(0.0, float(s) - 0.5))
            c = line.interpolate(min(line.length, float(s) + 0.5))
            tx, tz = c.x - a.x, c.y - a.y
            length = math.hypot(tx, tz)
            if length < 1e-6:
                continue
            tx, tz = tx / length, tz / length
            for side in (1, -1):
                nx, nz = -tz * side, tx * side
                start = shapely.Point(p.x + nx * (offset + step), p.y + nz * (offset + step))
                if any(footprints[i].covers(start) for i in tree.query(start)):
                    continue  # the kerb point itself is inside a building: no facade to face
                ray = shapely.LineString([start, (p.x + nx * reach, p.y + nz * reach)])
                hits = [footprints[i].intersection(ray) for i in tree.query(ray)]
                coords = [xy for g in hits if not g.is_empty for xy in _coords(g)]
                if not coords:
                    continue
                first = min(math.hypot(x - start.x, z - start.y) for x, z in coords)
                d = offset + step + first - WALL["inset"]
                if d <= offset:
                    continue
                x, z = p.x + nx * d, p.y + nz * d
                h = position_hash(x, z)
                if h >= WALL["share"]:
                    continue
                if any(math.hypot(q.x - x, q.z - z) < WALL["separation"] for q in lights):
                    continue
                low, high = WALL["height"]
                y = height_at(p.x, p.y) + low + (high - low) * (h / WALL["share"])
                lights.append(Light(x, y, z, "wall", "wall", False))
    return lights


def _coords(geometry):
    for part in shapely.get_parts(geometry):
        if part.geom_type == "Point":
            yield part.x, part.y
        elif hasattr(part, "coords"):
            yield from ((x, z) for x, z, *_ in part.coords)
        elif part.geom_type == "Polygon":
            yield from ((x, z) for x, z, *_ in part.exterior.coords)


def encode(lights):
    """The manifest ``lights`` field: street lights first (posts before hung lights), then the rest."""
    order = {"street": 0, "path": 0, "hung": 1, "wall": 2}
    lights = sorted(
        lights,
        key=lambda light: (
            not light.street,
            light.post is None,
            order[light.category],
            round(light.x, 2),
            round(light.z, 2),
        ),
    )
    data = bytearray()
    for light in lights:
        colour, intensity = TYPES[light.kind]
        rgb = [min(255, max(0, round(c * intensity / LIGHT_SCALE * 255))) for c in colour]
        info = CATEGORIES[light.category] | (STREET_FLAG if light.street else 0)
        data += LIGHT.pack(light.x, light.y, light.z, *rgb, info)
    posts = [light.post for light in lights if light.post is not None]
    if any(light.post is None for light in lights[: len(posts)]):
        raise ValueError("post records must follow the first lights in order")
    for x, y, z, yaw, scale in posts:
        turn = round((yaw % math.tau) / math.tau * 65536) % 65536
        data += POST.pack(x, y, z, turn, min(255, round(scale * 100)), 0)
    return {
        "format": FORMAT,
        "version": VERSION,
        "count": len(lights),
        "posts": len(posts),
        "street": sum(light.street for light in lights),
        "data": base64.b64encode(bytes(data)).decode(),
    }


def decode(field):
    """(lights, posts) of a manifest ``lights`` field, as plain dicts (tests, scorecard)."""
    if not field:
        return [], []
    data = base64.b64decode(field["data"])
    n, p = field["count"], field["posts"]
    names = {v: k for k, v in CATEGORIES.items()}
    lights = []
    for i in range(n):
        x, y, z, r, g, b, info = LIGHT.unpack_from(data, i * LIGHT.size)
        lights.append(
            {
                "x": x,
                "y": y,
                "z": z,
                "rgb": [v / 255 * LIGHT_SCALE for v in (r, g, b)],
                "category": names[info & 0x0F],
                "street": bool(info & STREET_FLAG),
            }
        )
    posts = []
    for i in range(p):
        x, y, z, turn, scale, style = POST.unpack_from(data, n * LIGHT.size + i * POST.size)
        posts.append({"x": x, "y": y, "z": z, "yaw": turn / 65536 * math.tau, "scale": scale / 100})
    return lights, posts
