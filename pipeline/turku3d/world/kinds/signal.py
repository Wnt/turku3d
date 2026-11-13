"""Traffic lights: one entity per signal post, with its vehicle and pedestrian heads as instance rows.

No open register lists Turku's signal posts. OpenStreetMap maps every signal as a node on the way's centreline: a
stop position (`highway=traffic_signals`, with the travel direction it serves) or a signalled crossing
(`highway=crossing` + `crossing=traffic_signals`). A post stands where the old game stood it
(`roads/signal_posts.py`, moved in): to the right of the approach, just past the surveyed edge of the carriageway
(GIS:Katualueet), so across the street the position is measured and along the street it is the mapper's. Every post
is therefore marked `guessed: ["position"]` until a panorama reading stores it.

- a stop position: one post right of each direction it serves, a three-lens vehicle head facing the approach
- a signalled crossing: a post at each kerb with a two-lens pedestrian head facing across the carriageway, and a
  vehicle head for the approach on its right where no stop position is mapped near

Schema: docs/specs/street-objects.md.
"""

from __future__ import annotations

import json
import math
from functools import lru_cache
from pathlib import Path

import numpy as np
import shapely

from ..api import Area, Instances, Material, Output, Part, Stub
from ..frame import to_engine
from ..objects import registers
from ..objects.common import BLACK_STEEL, GALVANISED, box_of, describe, interaction, unique, yaw_of_bearing
from ..objects.mesh import Mesh, srgb

VERSION = 1
CAR_WAYS = ("motorway", "trunk", "primary", "secondary", "tertiary", "unclassified", "residential", "living_street",
            "service", "motorway_link", "trunk_link", "primary_link", "secondary_link", "tertiary_link", "busway")
ISLAND = "Välikaista"  # medians: the register draws the carriageway across them, their posts stand on them
POST = 3.45  # m: height of a signal post (the old game's post)
KERB_CLEARANCE = 0.35  # m past the carriageway edge
REACH = 14.0  # m from the centreline within which a carriageway edge is looked for
NOMINAL = 3.85  # m from the centreline where no surveyed carriageway is under the node
JOIN = 2.0  # m: posts nearer than this are one post carrying the heads of both
SNAP, SNAP_TOPS = 2.5, (2.4, 5.5)  # m: reach to a lidar pole from the guessed kerb position; tops a signal or sign post has
OWN_STOP = 30.0  # m: a crossing with a stop position this near leaves the vehicle heads to it
VEHICLE_Y, PEDESTRIAN_Y = 2.95, 2.2  # m: centre of a head on the post
FORWARD = 0.2  # m: a head's body stands this far in front of the post axis

# A still shows a signal at red: the red lens emits, the other two are dark glass.
RED = Material("lens", {"kind": "signal", "colour": "#d8261c"}, srgb(216, 38, 28), 0.3, 0.0, emissive=(0.9, 0.05, 0.03))
AMBER = Material("lens", {"kind": "signal", "colour": "#f2a516"}, srgb(70, 50, 16), 0.3, 0.0)
GREEN = Material("lens", {"kind": "signal", "colour": "#2fd67a"}, srgb(18, 60, 38), 0.3, 0.0)


@lru_cache(maxsize=1)
def post_asset() -> tuple[Part, ...]:
    mesh = Mesh()
    mesh.tube("post", GALVANISED, [[0, -0.3, 0], [0, 0, 0], [0, POST, 0]], [0.055, 0.055, 0.045], sides=8)
    return tuple(mesh.parts())


@lru_cache(maxsize=2)
def head_asset(kind: str) -> tuple[Part, ...]:
    """A signal head whose origin is the post axis at ground level and whose lenses look along +Z."""
    mesh = Mesh()
    lenses = (RED, AMBER, GREEN) if kind == "vehicle" else (RED, GREEN)
    radius, pitch = (0.1, 0.3) if kind == "vehicle" else (0.1, 0.28)
    centre = VEHICLE_Y if kind == "vehicle" else PEDESTRIAN_Y
    tall = pitch * len(lenses) + 0.08
    mesh.box("body", BLACK_STEEL, [0, centre, FORWARD], [0.34 if kind == "vehicle" else 0.3, tall, 0.28])
    mesh.box("bracket", BLACK_STEEL, [0, centre, FORWARD / 2 - 0.03], [0.08, 0.12, FORWARD])
    front = FORWARD + 0.14
    for k, lens in enumerate(lenses):
        y = centre + pitch * ((len(lenses) - 1) / 2 - k)
        # The lens is a shallow dome and its visor a hood above it, both closed solids standing on the body's face.
        mesh.tube(f"lens{k}", lens, [[0, y, front - 0.01], [0, y, front + 0.015], [0, y, front + 0.03]], [radius, radius, radius * 0.6], sides=12)
        mesh.box(f"visor{k}", BLACK_STEEL, [0, y + radius + 0.015, front + 0.07], [2 * radius + 0.04, 0.02, 0.16])
    return tuple(mesh.parts())


def carriageway(sources, box) -> shapely.Geometry:
    """The surveyed carriageway in a box, less the medians and islands the register overlaps with it."""
    from ..street import registers as street

    frame = street.layer("GIS:Katualueet", box)
    if frame is None or not len(frame) or "Katuosalaji" not in frame:
        return shapely.Polygon()

    def union(kinds) -> shapely.Geometry:
        shapes = [shapely.make_valid(shapely.force_2d(g)) for g, kind in zip(frame.geometry, frame.Katuosalaji) if kind in kinds and g is not None]
        shapes = [g for g in shapes if g.geom_type in ("Polygon", "MultiPolygon")]
        return shapely.union_all(shapes) if shapes else shapely.Polygon()

    return union(registers.CAR_PARTS).difference(union((ISLAND,)))


def kerb(road: shapely.Geometry, e: float, n: float, ux: float, uy: float) -> tuple[float, float, str]:
    """Where a post stands on the side (ux, uy) of a centreline point: just past the carriageway edge."""
    if road.is_empty or not road.covers(shapely.Point(e, n)):
        return e + ux * NOMINAL, n + uy * NOMINAL, "nominal width"
    ray = shapely.LineString([(e, n), (e + ux * REACH, n + uy * REACH)])
    inside = ray.intersection(road)
    first = next((g for g in getattr(inside, "geoms", [inside]) if g.geom_type == "LineString" and g.distance(shapely.Point(e, n)) < 0.05), None)
    if first is None or first.length >= REACH - 0.05:
        return e + ux * NOMINAL, n + uy * NOMINAL, "nominal width"
    reach = first.length + KERB_CLEARANCE
    return e + ux * reach, n + uy * reach, "carriageway edge"


def _bearing(dx: float, dy: float) -> float:
    return round(math.degrees(math.atan2(dx, dy)) % 360, 1)


def posts_of(nodes: list[dict], ways: list[dict], road: shapely.Geometry) -> list[dict]:
    """Signal posts of mapped signal nodes: {id, e, n, heads [{type, bearing}], basis, source}. `nodes` are
    {id, e, n, kind ("stop" | "crossing"), direction}, `ways` {line, oneway}."""
    tree = shapely.STRtree([way["line"] for way in ways]) if ways else None
    stops = np.array([(node["e"], node["n"]) for node in nodes if node["kind"] == "stop"] or np.zeros((0, 2)))
    posts: list[dict] = []

    def stand(node: dict, suffix: str, side, heads: list[dict]) -> None:
        e, n, basis = kerb(road, node["e"], node["n"], *side)
        for post in posts:
            if math.hypot(post["e"] - e, post["n"] - n) <= JOIN:
                for head in heads:
                    if not any(h["type"] == head["type"] and abs((h["bearing"] - head["bearing"] + 180) % 360 - 180) < 30 for h in post["heads"]):
                        post["heads"].append(head)
                return
        posts.append({"id": f"n{node['id']}{suffix}", "e": round(e, 2), "n": round(n, 2), "heads": heads, "basis": basis, "source": node["source"]})

    for node in sorted(nodes, key=lambda node: (node["kind"] != "stop", node["id"])):
        point = shapely.Point(node["e"], node["n"])
        near = [ways[int(k)] for k in tree.query(point.buffer(0.5))] if tree is not None else []
        near = [way for way in near if way["line"].distance(point) <= 0.5]
        if not near:
            continue
        way = min(near, key=lambda way: way["line"].distance(point))
        s = way["line"].project(point)
        a, b = way["line"].interpolate(max(s - 1.0, 0.0)), way["line"].interpolate(min(s + 1.0, way["line"].length))
        length = math.hypot(b.x - a.x, b.y - a.y)
        if length < 1e-6:
            continue
        tx, ty = (b.x - a.x) / length, (b.y - a.y) / length
        travel = [1] if way["oneway"] else [1, -1]
        if node["kind"] == "stop":
            if node["direction"] in ("forward", "backward"):
                travel = [1 if node["direction"] == "forward" else -1]
            for d in travel:
                dx, dy = tx * d, ty * d
                stand(node, "f" if d > 0 else "b", (dy, -dx), [{"type": "vehicle", "bearing": _bearing(-dx, -dy)}])
        else:
            own_stop = len(stops) and float(np.hypot(stops[:, 0] - node["e"], stops[:, 1] - node["n"]).min()) <= OWN_STOP
            for d in (1, -1):
                dx, dy = tx * d, ty * d
                side = (dy, -dx)  # the kerb right of the travel direction d
                heads = [{"type": "pedestrian", "bearing": _bearing(-side[0], -side[1])}]
                if d in travel and not own_stop:
                    heads.insert(0, {"type": "vehicle", "bearing": _bearing(-dx, -dy)})
                stand(node, "r" if d > 0 else "l", side, heads)
    return posts


class Signal:
    name = "signal"
    code = (Path(__file__).parent.parent / "objects",)

    def discover(self, area: Area, sources) -> list[Stub]:
        box = area.bbox(REACH + 2.0)
        frame = sources.osm.features(box)
        if not len(frame):
            return []
        nodes, ways = [], []
        for kind, ident, tags, highway, crossing, oneway, geometry in zip(frame.osm_type, frame.osm_id, frame.tags, frame.highway, frame.crossing,
                                                                           frame.oneway, frame.geometry):
            if geometry.geom_type == "LineString" and highway in CAR_WAYS:
                ways.append({"line": shapely.force_2d(geometry), "oneway": oneway in ("yes", "1", "true")})
            elif geometry.geom_type == "Point" and (highway == "traffic_signals" or (highway == "crossing" and crossing == "traffic_signals")):
                tags = json.loads(tags) if isinstance(tags, str) else dict(tags or {})
                nodes.append({"id": int(ident), "e": float(geometry.x), "n": float(geometry.y), "kind": "stop" if highway == "traffic_signals" else "crossing",
                              "direction": tags.get("traffic_signals:direction", ""), "source": {"source": "osm", "key": f"{kind}/{ident}"}})
        if not nodes:
            return []
        stubs = []
        lidar = registers.lidar_of(sources)
        for post in posts_of(nodes, ways, carriageway(sources, box)):
            e, n = post["e"], post["n"]
            if not area.touches(box_of(e, n, 0.5)):
                continue
            # The kerb position is a guess to a couple of metres; a post the scan shows there (a signal post, or the
            # sign post the heads share: dev_12's photo has head, prohibition and diamond on one pole) is the place.
            measured = lidar.pole_top(e, n, search=SNAP)
            snapped = bool(measured) and SNAP_TOPS[0] <= measured["top"] <= SNAP_TOPS[1]
            if snapped:
                e, n = round(e + measured["offset"][0], 2), round(n + measured["offset"][1], 2)
            ground = registers.ground_height(sources, e, n)
            if not math.isfinite(ground):
                continue
            facts = {
                "at": [e, n, round(ground, 2)], "height": POST, "heads": post["heads"],
                "basis": f"OSM signal node; post right of the approach at the {post['basis']}" + (", on the pole the lidar shows there" if snapped else ""),
                "guessed": [] if snapped else ["position"],
                "interaction": interaction("knock-over", "cylinder", [0.06, POST], 55.0 + 14.0 * len(post["heads"])),
            }
            stubs.append(Stub(f"signal/{post['id']}", box_of(e, n, 1.0), [post["source"]], facts))
        return unique(stubs)

    def describe(self, stub: Stub, stored: dict | None) -> dict:
        return describe(self.name, VERSION, stub, stored)

    def compile(self, description: dict) -> Output:
        e, n, h = description["at"]
        entity = description["id"]
        block = description.get("interaction") or {}
        behaviour = block.get("behaviour")
        items = [Instances(list(post_asset()), np.array([[0.0, 0.0, 0.0, 0.0, 1.0, 1.0, 1.0]]), ids=[f"{entity}#post"], behaviour=behaviour,
                           collision=block.get("collision"), mass=block.get("mass"))]
        heads = description.get("heads") or []
        low = any(head.get("type") == "vehicle" for head in heads)  # a pedestrian head hangs under a vehicle head
        for kind in ("vehicle", "pedestrian"):
            rows, ids = [], []
            for k, head in enumerate(heads):
                if head.get("type") == kind:
                    lift = 0.0 if kind == "vehicle" or low else 0.45
                    rows.append([0.0, lift, 0.0, round(yaw_of_bearing(float(head.get("bearing", 0.0))), 4), 1.0, 1.0, 1.0])
                    ids.append(f"{entity}#head{k}")
            if rows:
                size = [0.34, 0.98, 0.28] if kind == "vehicle" else [0.3, 0.64, 0.28]
                items.append(Instances(list(head_asset(kind)), np.array(rows), ids=ids, behaviour=behaviour, collision={"shape": "box", "size": size}, mass=9.0))
        return Output(tuple(float(v) for v in to_engine(e, n, h)), [], items)
