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

import numpy as np
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
        base / "night_lighting.py",
        base / "night_lighting.json",
        base / "props/orientation.py",
        base / "props/seating.py",
        base / "package/gltf.py",
        base / "roads/__init__.py",
    ]


def stage(cache, tile, inputs, grid_pos, carried, common):
    """Cached placement, visual, lights and collision of the tile's furniture."""
    from turku3d.stage_cache import source_key

    from .build import grid_height

    from turku3d.ground.inputs import laz_paths
    from turku3d.props.seating import WINDOW
    from turku3d.tile_layer import identities

    footprints = [b.footprint for b in (*inputs.buildings, *inputs.wall_context)]
    water = list(inputs.water)
    roads = inputs.roads["json"] if inputs.roads else None
    barriers = list(inputs.barriers or ())
    laz = laz_paths(tile.bbox.buffered(WINDOW))
    overhead = deck_cover(inputs.roads)
    spans = deck_spans(inputs.roads)
    return cache.run(
        "furniture",
        source_key(*sources(), grid_height, *common),
        [tile, list(inputs.poles), grid_pos, footprints, water, roads, carried, barriers,
         identities({str(p): p for p in laz}), overhead, spans],
        lambda: produce(
            tile,
            inputs.poles,
            lambda x, z: grid_height(tile, grid_pos, x, z),
            footprints,
            water,
            roads,
            carried,
            barriers,
            laz,
            overhead,
            spans,
        ),
    )


LOWER_ROAD_MARGIN = 2.0  # m beyond a lower way's half width: its kerbside where its lamps stand


def deck_cover(roads):
    """Plan area where a deck spans a lower road: each deck's footprint (not on-grade landings) over
    the corridors of the ways that pass under it, or None. A lamp there stands on the lower road and
    would reach up through the deck; a register lamp elsewhere on a deck stays, so a deck with no
    mapped lamps of its own does not go dark."""
    parts = []
    for way in (roads or {}).get("bridge_ways") or []:
        if way.get("landing"):
            continue
        lower = [w["line"].buffer(w["width"] / 2 + LOWER_ROAD_MARGIN)
                 for w in [*way.get("under", []), *way.get("below", [])]
                 if w.get("tags", {}).get("highway") not in (None, "steps")]
        if lower:
            deck = way.get("outline", way["line"].buffer(way["width"] / 2))
            parts.append(deck.intersection(shapely.union_all(lower)))
    parts = [p for p in parts if not p.is_empty]
    return shapely.union_all(parts) if parts else None


POST_REACH = 9.0  # m above its base that a street light post (head and arm) reaches
DECK_GAP = 0.5  # m between a post's top and the deck's surface it may stand under


def deck_spans(roads):
    """Plan area of every deck that is not an on-grade landing, or None."""
    decks = [way.get("outline", way["line"].buffer(way["width"] / 2))
             for way in (roads or {}).get("bridge_ways") or [] if not way.get("landing")]
    return shapely.union_all(decks) if decks else None


def deck_heights(graph_json):
    """(lines, per-line heights) of the street graph's bridge edges: the deck surfaces above the ground."""
    import json as _json

    if not graph_json:
        return []
    graph = _json.loads(graph_json) if isinstance(graph_json, (str, bytes)) else graph_json
    bit = graph.get("bits", {}).get("edge_flags", {}).get("BRIDGE")
    if not bit:
        return []
    flags = graph["edgeAttrColumns"].index("flags")
    nodes = np.asarray(graph["nodes"], dtype=float).reshape(-1, 2)
    out = []
    for edge, attrs, heights in zip(graph["edges"], graph["edgeAttrs"], graph.get("edgeHeights") or [], strict=False):
        if not attrs[flags] & bit or not heights:
            continue
        coords = np.asarray([nodes[edge[0]], *np.asarray(edge[4:], dtype=float).reshape(-1, 2), nodes[edge[1]]])
        if len(coords) == len(heights):
            out.append((shapely.LineString(coords), np.asarray(heights, dtype=float)))
    return out


def deck_above(decks, point, reach=20.0):
    """Height of the nearest bridge edge's surface over ``point`` (within ``reach``), else None."""
    best, height = reach, None
    for line, heights in decks:
        d = line.distance(point)
        if d < best:
            coords = shapely.get_coordinates(line)
            stations = np.r_[0, np.cumsum(np.hypot(*np.diff(coords, axis=0).T))]
            best, height = d, float(np.interp(line.project(point), stations, heights))
    return height


LANE_SHOULDER = 0.3  # m of a carriageway's mapped width beside its outer lanes
POST_SHIFT = 3.0  # m a standing item may move to clear the traffic lanes


def lane_area(graph_json):
    """Plan area of the street graph's car lanes: each car edge buffered by its width less its
    shoulders, or None. A post inside it stands in the lane a car drives."""
    import json as _json

    if not graph_json:
        return None
    graph = _json.loads(graph_json) if isinstance(graph_json, (str, bytes)) else graph_json
    columns = graph.get("edgeAttrColumns")
    attrs = graph.get("edgeAttrs")
    if not columns or not attrs or "width" not in columns or "flags" not in columns:
        return None
    from turku3d.roads import CAR

    nodes = np.asarray(graph["nodes"], dtype=float).reshape(-1, 2)
    width, flags = columns.index("width"), columns.index("flags")
    parts = []
    for edge, a in zip(graph["edges"], attrs, strict=False):
        if not int(edge[2]) & CAR or not a[width]:
            continue
        coords = [nodes[edge[0]], *np.asarray(edge[4:], dtype=float).reshape(-1, 2), nodes[edge[1]]]
        parts.append(shapely.LineString(coords).buffer(max(1.5, float(a[width]) / 2 - LANE_SHOULDER), cap_style="flat"))
    return shapely.union_all(parts) if parts else None


EMPTY = {"glb": None, "props": [], "poles": [], "lights": None, "collision": {}, "stats": {}}


def _outside(polygon, point, clearance=CLEARANCE):
    """(depth inside, x, z just outside) of a point covered by a polygon, via its nearest edge."""
    edge = shapely.ops.nearest_points(polygon.boundary, point)[0]
    dx, dz = edge.x - point.x, edge.y - point.y
    length = math.hypot(dx, dz) or 1.0
    return edge.distance(point), edge.x + dx / length * clearance, edge.y + dz / length * clearance


def _out_of_lanes(p, lanes, stats, why=""):
    """Whether ``p`` stands clear of the traffic lanes, moving it to the nearest kerb within POST_SHIFT
    first; False when it is not built."""
    point = shapely.Point(p.x, p.z)
    if lanes is None or not lanes.covers(point):
        return True
    depth, x, z = _outside(lanes, point)
    label = f"{p.kind}_{why}" if why else p.kind
    if depth > POST_SHIFT or lanes.covers(shapely.Point(x, z)):
        stats[f"dropped_in_lane:{label}"] += 1
        return False
    p.x, p.z = x, z
    stats[f"moved_out_of_lane:{label}"] += 1
    return True


def produce(tile, poles, height_at, footprints, water, roads, carried, barriers=(), laz=(), overhead=None,
            spans=None):
    from turku3d.props import seating, visual_bytes
    from turku3d.props.lights import Light, encode, post_light, street_edges, wall_lights
    from turku3d.night_lighting import lamp_kind, lamp_height

    stats = Counter()
    decks = deck_heights(roads) if spans is not None else []
    lanes = lane_area(roads)
    buildings = STRtree(footprints) if footprints else None
    wet = STRtree(water) if water else None
    blocked_deck = carried if carried is not None and not carried.is_empty else None
    placed, lights, benches = [], [], []
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
        if p.kind == "lamp" and overhead is not None and overhead.covers(point):
            # A street light on the lower road under a deck pokes its head up through the deck
            # into its traffic lanes (Kupittaansilta). The deck carries its own mapped lamps.
            stats["lamp_under_deck"] += 1
            continue
        if p.kind == "lamp" and spans is not None and spans.covers(point):
            # Anywhere else under a deck, a post that would reach up into it (a verge or footway
            # under the deck, t0_0_-4) stands in the deck's lanes as a pole collider. Without a
            # surveyed deck height (the street graph's deck heights come later) it is dropped too:
            # the deck builds its own mapped lamps on its surface.
            above = deck_above(decks, point) if decks else None
            if above is None or height_at(p.x, p.z) + POST_REACH > above - DECK_GAP:
                stats["lamp_through_deck"] += 1
                continue
        if p.kind in ("lamp", "sign", "bus_stop", *COLLIDES):
            # A post, sign or bollard in a traffic lane stops every car that drives it (Kalevan's south
            # joint, the t0_0_-3 underpass): it moves to the nearest kerb, or is not built.
            if not _out_of_lanes(p, lanes, stats):
                continue
            point = shapely.Point(p.x, p.z)
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
        if p.kind == "bench":
            benches.append(p)  # placed against the survey and barriers below (props.seating)
            continue
        if not tile.bbox.contains(p.x, p.z):
            stats[f"left_tile:{p.kind}"] += 1
            continue
        p.y = height_at(p.x, p.z)
        if p.kind == "lamp":
            if p.variant == "hung":
                lights.append(Light(p.x, p.y + lamp_height(p.light_tags, 7.0), p.z, "hung", lamp_kind(p.light_tags, "led"), True))
                stats["lamp_hung"] += 1
                continue
            placed.append(p)
            continue
        placed.append(p)
    if benches:
        cloud = seating.survey_cloud(tile.bbox, benches, list(laz))
        for p in seating.place(benches, cloud, barriers, footprints, water, stats, roads):
            # The survey snap can put a bench on a blob in the carriageway (a parked van or a
            # parklet beside the lane): the lanes are cleared again (Rotterdam Meent, a wooden bench
            # mid-street at the Goudsesingel corner).
            if not _out_of_lanes(p, lanes, stats, "snap"):
                continue
            if not tile.bbox.contains(p.x, p.z):
                stats["left_tile:bench"] += 1
                continue
            p.y = height_at(p.x, p.z)
            placed.append(p)
    edges, trees = street_edges(roads)
    lamps = [p for p in placed if p.kind == "lamp"]
    furniture = [p for p in placed if p.kind != "lamp"]
    posts = [post_light(p.x, p.y, p.z, edges, trees["car"], trees["path"], p.light_tags) for p in lamps]
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
        # Everything standing on the ground masks parked-car slots (coarse/parked.py), each by its
        # own footprint (ground_shape): lot bays give way to lot lamps, bins and meters, while kerb
        # bays keep the small kerb clearance, so a lamp beside the kerb never costs the bay.
        "poles": placed,
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


def ground_shape(p):
    """The horizontal footprint of a standing item: its collision boxes or cylinder, else its
    base point (posts, bins, meters, racks; anything without a kind, such as a tree trunk)."""
    kind = getattr(p, "kind", None)
    if kind in CYLINDERS:
        return shapely.Point(p.x, p.z).buffer(CYLINDERS[kind][0])
    if kind not in BOXES:
        return shapely.Point(p.x, p.z)
    c, s = math.cos(p.yaw), math.sin(p.yaw)
    boxes = []
    for (cx, _cy, cz), (hx, _hy, hz) in BOXES[kind]:
        corners = [(cx + dx, cz + dz) for dx, dz in ((-hx, -hz), (hx, -hz), (hx, hz), (-hx, hz))]
        boxes.append(shapely.Polygon([(p.x + c * x + s * z, p.z - s * x + c * z) for x, z in corners]))
    return shapely.union_all(boxes)


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
