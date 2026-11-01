"""Street furniture, street lights and their static collision for a coarse tile (G1).

Input is the tile's prepared ``poles`` list (``turku3d.props.read_sources``: register first, OSM
fallback, the lit-street rule only where neither has a lamp, nothing left on a carriageway). This
stage places it against the tile's own ground, buildings, water and bridge decks:

* furniture and signs at most ``BUILDING_MOVE`` (lamp poles ``LAMP_MOVE``) inside a building
  footprint move just outside it; deeper ones are dropped;
* anything on a bridge/tunnel deck is dropped (decks carry their own furniture); an item at most
  ``WATER_MOVE`` inside a water polygon (quay edges) moves onto the land, deeper ones are dropped;
* traffic signs whose code has no face (kind ``post``) are not drawn, as before G1;
* lamps become light records with a client-drawn post (``turku3d.props.lights``), lamps hung over
  a carriageway become posts-free street lights, and every car street gets facade lights;
* benches, tables, large waste containers, planters, register bollards and lamp posts get static
  collision primitives, merged into the tile's collision cells (never breakable: only sign and
  bus-stop poles are session-owned bodies).

The props GLB holds every non-lamp prop; the packager merges it into the one ``props_palette``
batch (signs and stops keep their own atlas batch).
"""

from __future__ import annotations

import copy
import math
from collections import Counter, defaultdict
from pathlib import Path

import shapely
import shapely.ops
from shapely.strtree import STRtree

from turku3d import config as C

BUILDING_MOVE = 1.0  # m inside a footprint that a register point may be off (survey offset)
LAMP_MOVE = 1.5  # m inside a footprint that a register lighting pole may be off
CLEARANCE = 0.3  # m outside the footprint for a moved item
WATER_MOVE = 1.5  # m inside a water polygon that a quay-edge register point may be off
WALL_LIGHT_NEAR_LAMP = 3.0  # m: a facade light this close to a street light is that light
COLLIDES = ("bench", "table", "waste_container", "planter", "bollard")


def sources():
    """Code this stage depends on (its stage-cache key)."""
    base = Path(__file__).parents[1]
    return [
        Path(__file__),
        base / "props/__init__.py",
        base / "props/models.py",
        base / "props/lights.py",
        base / "props/orientation.py",
        base / "package/gltf.py",
        base / "roads/__init__.py",
    ]


def stage(cache, tile, inputs, grid_pos, carried, common):
    """Cached placement, visual, lights and collision of the tile's furniture."""
    from turku3d.stage_cache import source_key

    from .build import grid_height

    footprints = [b.footprint for b in (*inputs.buildings, *inputs.wall_context)]
    water = list(inputs.water)
    roads = inputs.roads["json"] if inputs.roads else None
    return cache.run(
        "furniture",
        source_key(*sources(), grid_height, *common),
        [tile, list(inputs.poles), grid_pos, footprints, water, roads, carried],
        lambda: produce(
            tile,
            inputs.poles,
            lambda x, z: grid_height(tile, grid_pos, x, z),
            footprints,
            water,
            roads,
            carried,
        ),
    )


EMPTY = {"glb": None, "props": [], "poles": [], "lights": None, "collision": {}, "stats": {}}


def _outside(polygon, point, clearance=CLEARANCE):
    """(depth inside, x, z just outside) of a point covered by a polygon, via its nearest edge."""
    edge = shapely.ops.nearest_points(polygon.boundary, point)[0]
    dx, dz = edge.x - point.x, edge.y - point.y
    length = math.hypot(dx, dz) or 1.0
    return edge.distance(point), edge.x + dx / length * clearance, edge.y + dz / length * clearance


def produce(tile, poles, height_at, footprints, water, roads, carried):
    from turku3d.props import visual_bytes
    from turku3d.props.lights import Light, encode, post_light, street_edges, wall_lights

    stats = Counter()
    buildings = STRtree(footprints) if footprints else None
    wet = STRtree(water) if water else None
    blocked_deck = carried if carried is not None and not carried.is_empty else None
    placed, lights = [], []
    for source in poles:
        p = copy.copy(source)
        if p.kind == "post":
            # A traffic sign whose code has no face: drawn as a bare post it reads as a bollard.
            stats["skipped_unsupported_sign"] += 1
            continue
        point = shapely.Point(p.x, p.z)
        if blocked_deck is not None and blocked_deck.distance(point) <= 0.5:
            stats[f"dropped_on_deck:{p.kind}"] += 1
            continue
        flooded = [water[i] for i in wet.query(point)] if wet is not None else []
        flooded = [w for w in flooded if w.covers(point)]
        if flooded:
            # Quay-edge benches and lamps: water polygons overlap the quay by survey offsets.
            depth, x, z = _outside(flooded[0], point)
            if depth > WATER_MOVE or any(w.covers(shapely.Point(x, z)) for w in flooded):
                stats[f"dropped_on_water:{p.kind}"] += 1
                continue
            p.x, p.z = x, z
            point = shapely.Point(x, z)
            stats[f"moved_out_of_water:{p.kind}"] += 1
        inside = [footprints[i] for i in buildings.query(point)] if buildings is not None else []
        inside = [f for f in inside if f.covers(point)]
        if inside:
            depth, x, z = _outside(inside[0], point)
            # The register lists lighting poles only: a pole just inside a footprint stands by
            # that wall (survey offset or a recess), never on the roof.
            moved = shapely.Point(x, z)
            # Terraced buildings share walls: stepping out of one must not step into the next.
            neighbour = any(footprints[i].covers(moved) for i in buildings.query(moved))
            if depth <= (LAMP_MOVE if p.kind == "lamp" else BUILDING_MOVE) and not neighbour:
                p.x, p.z = x, z
                stats[f"moved_out_of_building:{p.kind}"] += 1
            else:
                stats[f"dropped_in_building:{p.kind}"] += 1
                continue
        if not tile.bbox.contains(p.x, p.z):
            stats[f"left_tile:{p.kind}"] += 1
            continue
        p.y = height_at(p.x, p.z)
        if p.kind == "lamp":
            if p.variant == "hung":
                lights.append(Light(p.x, p.y + 7.0, p.z, "hung", "led", True))
                stats["lamp_hung"] += 1
                continue
            placed.append(p)
            continue
        placed.append(p)
    edges, trees = street_edges(roads)
    lamps = [p for p in placed if p.kind == "lamp"]
    furniture = [p for p in placed if p.kind != "lamp"]
    posts = [post_light(p.x, p.y, p.z, edges, trees["car"], trees["path"]) for p in lamps]
    lights = posts + lights
    blocked = shapely.union_all([*water, *([carried] if blocked_deck is not None else [])])
    heads = STRtree([shapely.Point(light.x, light.z) for light in lights]) if lights else None
    for light in wall_lights(tile, edges, footprints, height_at, blocked):
        if heads is not None and len(
            heads.query(
                shapely.Point(light.x, light.z), predicate="dwithin", distance=WALL_LIGHT_NEAR_LAMP
            )
        ):
            stats["wall_light_near_lamp"] += 1
            continue
        lights.append(light)
    collision = defaultdict(list)
    for light in posts:
        add_post(collision, light.post)
    for p in furniture:
        if p.kind in COLLIDES:
            add_furniture(collision, p)
    for p in placed:
        stats[f"kind:{p.kind}"] += 1
        stats[f"source:{p.source}"] += 1
    for light in lights:
        stats[f"light:{light.category}"] += 1
    return {
        "glb": visual_bytes(furniture, tile) if furniture else None,
        "props": furniture,
        # Parked-car bays give way to sign and stop poles only, as before G1: furniture and lamps
        # stand off the carriageway, and a lamp at the kerb would otherwise cost the bay beside it.
        "poles": [p for p in placed if p.kind in ("sign", "bus_stop")],
        "lights": encode(lights) if lights else None,
        "collision": dict(collision),
        "stats": dict(sorted(stats.items())),
    }


def _yaw_quaternion(yaw):
    return (0.0, math.sin(yaw / 2), 0.0, math.cos(yaw / 2))


def add_post(collision, post):
    """A lamp post's static cylinder, matching the client post's height and base radius."""
    from turku3d.col.format import Primitive
    from turku3d.col.materials import mat_id
    from turku3d.props.lights import POST_RADIUS, POST_SHAPE

    x, y, z, _, scale = post
    ct = C.CollisionTile.at(x, z)
    half = POST_SHAPE["height"] * scale / 2
    collision[ct.id].append(
        Primitive.cylinder(
            mat_id("pole"),
            (x - ct.origin[0], y + half, z - ct.origin[2]),
            half,
            max(0.08, POST_RADIUS * scale),
        )
    )


# Local (x, y, z) centre and half extents of each colliding kind's boxes; cylinders as
# (radius, height). The model front is +Z (turku3d.props.models).
BOXES = {
    "bench": [((0, 0.25, 0), (0.9, 0.25, 0.28)), ((0, 0.73, -0.24), (0.9, 0.17, 0.04))],
    "table": [((0, 0.38, 0), (0.92, 0.38, 0.76))],
    "planter": [((0, 0.3, 0), (0.6, 0.3, 0.6))],
}
CYLINDERS = {"waste_container": (0.8, 1.22, "street_furniture"), "bollard": (0.09, 0.95, "bollard")}


def add_furniture(collision, p):
    from turku3d.col.format import Primitive
    from turku3d.col.materials import mat_id

    ct = C.CollisionTile.at(p.x, p.z)
    c, s = math.cos(p.yaw), math.sin(p.yaw)
    for (cx, cy, cz), half in BOXES.get(p.kind, ()):
        centre = (
            p.x + c * cx + s * cz - ct.origin[0],
            p.y + cy,
            p.z - s * cx + c * cz - ct.origin[2],
        )
        collision[ct.id].append(
            Primitive.box(mat_id("street_furniture"), centre, half, _yaw_quaternion(p.yaw))
        )
    if p.kind in CYLINDERS:
        radius, height, material = CYLINDERS[p.kind]
        collision[ct.id].append(
            Primitive.cylinder(
                mat_id(material),
                (p.x - ct.origin[0], p.y + height / 2, p.z - ct.origin[2]),
                height / 2,
                radius,
            )
        )
