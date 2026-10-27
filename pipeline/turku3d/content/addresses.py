"""Delivery address pool: every addressed building in the published tiles with a street-side door.

Inputs are the buildings layer (``metrics.json`` semantics: city/OSM address and OSM entrances;
``facades.geojson``: street-facing wall baselines; ``footprints.geojson``: every footprint) and the
road graph layer (``roadgraph.json``). For each building with an address the door is the best OSM
entrance on a street-facing ground-floor wall, or the middle of its widest street-facing wall. The
drop-off is the sidewalk spot ``SIDEWALK_M`` in front of the door, outside every footprint and within
``MAX_STREET_M`` of the walkable street network. Route distances are shortest graph paths from the
pizzeria on foot and by car (oneways honoured), measured like the game's router: from the point
where the pizzeria snaps onto the graph to the point where the drop-off does.

The assembler publishes the pool next to the road graph as ``dist/tiles/addresses/<sha[:16]>.json``
(``manifest.addresses``); the game draws its delivery orders from it.
"""

from __future__ import annotations

import hashlib
import heapq
import json
import math
import re
import unicodedata
from pathlib import Path

from shapely import STRtree
from shapely.geometry import Point, shape

from turku3d import config as C
from turku3d.roads import CAR, FOOT, ONEWAY

FORMAT = "turku3d-addresses"
VERSION = 1
# Pizzeria Linnanportti, Linnankatu 17 (game/src/gameplay/content/locations.ts PIZZERIA.point). The game
# rejects a pool whose origin differs, so the two cannot drift apart silently.
PIZZERIA = (77.1, 187.6)
SIDEWALK_M = 2.5
FALLBACK_OFFSETS_M = (SIDEWALK_M, 4.0, 1.5)
MAX_STREET_M = 40.0
# The game's router snaps at most this far (router.ts MAX_SNAP_M); farther car snaps get no car route.
MAX_SNAP_M = 80.0
ENTRANCE_ON_WALL_M = 1.5
# A wall not flagged street-facing still counts when the walkable network is this close in front of it.
FRONT_STREET_M = 12.0
# An address point picks entrances within this distance and walls within this distance of it.
HINT_ENTRANCE_M = 10.0
HINT_WALL_M = 8.0
# An address point belongs to the footprint it lies in, or the nearest one within this distance.
HINT_BUILDING_M = 30.0
MIN_WALL_M = 3.0
GROUND_WALL_M = 1.0
# A drop-off this close to the pizzeria (its own building, Linnankatu 17) is not an order.
MIN_ROUTE_M = 60.0
ENTRANCE_RANK = {"main": 0, "shop": 1, "yes": 2, "home": 2, "secondary": 3, "staircase": 4}


def slug(text: str) -> str:
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")


def _local(e: float, n: float) -> tuple[float, float]:
    x, _, z = C.en_to_local(e, n)
    return float(x), float(z)


def _project(p, a, b):
    """Closest point on segment a-b to p, and its distance."""
    ax, az = a
    dx, dz = b[0] - ax, b[1] - az
    length2 = dx * dx + dz * dz
    t = 0.0 if length2 == 0 else max(0.0, min(1.0, ((p[0] - ax) * dx + (p[1] - az) * dz) / length2))
    q = (ax + t * dx, az + t * dz)
    return q, math.hypot(p[0] - q[0], p[1] - q[1])


def _ground_walls(facades: list[dict]) -> list[dict]:
    """Ground-floor walls of one building, as local baselines with outward normals."""
    if not facades:
        return []
    ground = min(f["properties"]["base_height"] for f in facades)
    walls = []
    for f in facades:
        p = f["properties"]
        if p["width_m"] < MIN_WALL_M or p["base_height"] > ground + GROUND_WALL_M:
            continue
        (e0, n0), (e1, n1) = f["geometry"]["coordinates"][:2]
        nx, nz = p["normal"][0], p["normal"][2]
        norm = math.hypot(nx, nz)
        if norm < 0.5:
            continue
        walls.append(
            {
                "a": _local(e0, n0),
                "b": _local(e1, n1),
                "normal": (nx / norm, nz / norm),
                "width": p["width_m"],
                "street": bool(p["street_facing"]),
            }
        )
    return walls


def _street_side(wall: dict, roads: Graph) -> float | None:
    """Distance from the wall's sidewalk spot to the walkable network, or None when the street is not in front.

    The layer's `street_facing` flag counts; otherwise the network must be close and ahead of the wall.
    """
    mid = ((wall["a"][0] + wall["b"][0]) / 2, (wall["a"][1] + wall["b"][1]) / 2)
    spot = (mid[0] + wall["normal"][0] * SIDEWALK_M, mid[1] + wall["normal"][1] * SIDEWALK_M)
    snapped = roads.snap(spot, FOOT)
    if snapped is None:
        return None
    _, _, q, d = snapped
    if wall["street"]:
        return d
    ahead = ((q[0] - mid[0]) * wall["normal"][0] + (q[1] - mid[1]) * wall["normal"][1]) / max(
        1e-6, math.hypot(q[0] - mid[0], q[1] - mid[1])
    )
    return d if d <= FRONT_STREET_M and ahead > 0.3 else None


def door(semantics: dict, walls: list[dict], roads: Graph, hint=None):
    """(door point, outward normal, source) for one address of a building, or None without a street side.

    `hint` is the city register's address point: with several addresses on one building (a corner
    block) the door is on the wall nearest to it.
    """
    street = [(w, d) for w in walls if (d := _street_side(w, roads)) is not None]
    if not street:
        return None
    near = (lambda w: _project(hint, w["a"], w["b"])[1]) if hint else (lambda w: 0.0)
    best = None
    for entrance in semantics.get("entrances", []):
        kind = entrance.get("tags", {}).get("entrance")
        if kind not in ENTRANCE_RANK or entrance["geometry"]["type"] != "Point":
            continue
        p = _local(*entrance["geometry"]["coordinates"][:2])
        if hint and math.dist(p, hint) > HINT_ENTRANCE_M:
            continue
        for wall, _ in street:
            q, d = _project(p, wall["a"], wall["b"])
            if d <= ENTRANCE_ON_WALL_M:
                key = (near(wall) > HINT_WALL_M, ENTRANCE_RANK[kind], d)
                if best is None or key < best[0]:
                    best = (key, q, wall["normal"], "entrance")
    if best:
        return best[1], best[2], best[3]
    wall = min(
        street, key=lambda wd: (round(near(wd[0]), 1), not wd[0]["street"], -wd[0]["width"], wd[1])
    )[0]
    mid = ((wall["a"][0] + wall["b"][0]) / 2, (wall["a"][1] + wall["b"][1]) / 2)
    if hint:
        mid = _project(hint, wall["a"], wall["b"])[0]
    return mid, wall["normal"], "wall"


class Graph:
    """The road graph's edges as polylines with per-mode adjacency (oneway honoured for cars)."""

    def __init__(self, graph: dict):
        nodes = graph["nodes"]
        self.edges = []
        for row in graph["edges"]:
            a, b, flags = row[0], row[1], row[2]
            pts = [(nodes[2 * a], nodes[2 * a + 1])]
            pts += [(row[i], row[i + 1]) for i in range(4, len(row), 2)]
            pts.append((nodes[2 * b], nodes[2 * b + 1]))
            cum = [0.0]
            for i in range(1, len(pts)):
                cum.append(
                    cum[-1] + math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1])
                )
            self.edges.append({"a": a, "b": b, "flags": flags, "points": pts, "cum": cum})
        self.count = len(nodes) // 2

    def snap(self, p, mode: int):
        """(edge index, offset along the edge, snapped point, distance) on the nearest usable edge."""
        best = None
        for index, edge in enumerate(self.edges):
            if not edge["flags"] & mode:
                continue
            pts, cum = edge["points"], edge["cum"]
            for i in range(1, len(pts)):
                q, d = _project(p, pts[i - 1], pts[i])
                if best is None or d < best[3]:
                    offset = cum[i - 1] + math.hypot(q[0] - pts[i - 1][0], q[1] - pts[i - 1][1])
                    best = (index, offset, q, d)
        return best

    def _forward_only(self, edge, mode):
        return mode == CAR and bool(edge["flags"] & ONEWAY)

    def distances(self, start, mode: int) -> list[float]:
        """Shortest distance from the snapped start (edge, offset) to every node."""
        index, offset = start
        edge = self.edges[index]
        dist = [math.inf] * self.count
        heap = [(edge["cum"][-1] - offset, edge["b"])]
        if not self._forward_only(edge, mode):
            heap.append((offset, edge["a"]))
        adjacency: list[list[tuple[int, float]]] = [[] for _ in range(self.count)]
        for e in self.edges:
            if not e["flags"] & mode:
                continue
            length = e["cum"][-1]
            adjacency[e["a"]].append((e["b"], length))
            if not self._forward_only(e, mode):
                adjacency[e["b"]].append((e["a"], length))
        while heap:
            d, node = heapq.heappop(heap)
            if d >= dist[node]:
                continue
            dist[node] = d
            for other, length in adjacency[node]:
                if d + length < dist[other]:
                    heapq.heappush(heap, (d + length, other))
        return dist

    def route_length(self, start, dist, goal, mode: int) -> float:
        """Path length from the start (whose node distances are `dist`) to the snapped goal."""
        (si, so), (gi, go) = start, goal
        edge = self.edges[gi]
        forward_only = self._forward_only(edge, mode)
        best = dist[edge["a"]] + go
        if not forward_only:
            best = min(best, dist[edge["b"]] + edge["cum"][-1] - go)
        if si == gi and (go >= so or not forward_only):
            best = min(best, abs(go - so))
        return best


def _metres(value: float) -> float | None:
    return round(value, 1) if math.isfinite(value) else None


def read_address_points() -> list[tuple[str, tuple[float, float]]]:
    """City register address points (GIS:Osoitteet) of every fetched WFS area, in engine x/z."""
    import geopandas as gpd

    points, seen = [], set()
    for path in sorted((C.data_root() / "raw/wfs").glob("*/GIS_Osoitteet.parquet")):
        for row in gpd.read_parquet(path).itertuples():
            if row.feature_key in seen or row.geometry.geom_type != "Point":
                continue
            seen.add(row.feature_key)
            number = str(row.Osoitenumero or "")
            if not row.Osoite_suomeksi or number in ("", "0", "None"):
                continue  # park and area labels carry number 0
            points.append(
                (f"{row.Osoite_suomeksi} {number}", _local(row.geometry.x, row.geometry.y))
            )
    return points


def build_pool(
    buildings_dir: Path,
    graph: dict,
    address_points=(),
    origin=PIZZERIA,
    tiles: set[str] | None = None,
) -> dict:
    """Address pool from a buildings layer, a road graph and register address points (engine x/z).

    Every address comes from a building's own address or from a register point on or next to a
    footprint; `tiles` limits the pool to drop-offs in those L0 tiles.
    """
    metrics = json.loads((buildings_dir / "metrics.json").read_text())
    facades: dict[str, list[dict]] = {}
    for f in json.loads((buildings_dir / "facades.geojson").read_text())["features"]:
        facades.setdefault(f["properties"]["building_id"], []).append(f)
    features = json.loads((buildings_dir / "footprints.geojson").read_text())["features"]
    footprints = [shape(f["geometry"]) for f in features]
    footprint_ids = [f["properties"]["building_id"] for f in features]
    blockers = STRtree(footprints)
    records = {r["building_id"]: r for r in metrics["buildings"]}
    roads = Graph(graph)
    starts, dists = {}, {}
    for mode in (FOOT, CAR):
        snapped = roads.snap(origin, mode)
        if snapped is None:
            raise ValueError("the road graph has no edge for the pizzeria")
        starts[mode] = snapped[:2]
        dists[mode] = roads.distances(starts[mode], mode)

    wanted: list[tuple[str, str, tuple[float, float] | None]] = []
    for record in metrics["buildings"]:
        address = " ".join(record.get("semantics", {}).get("address", "").split())
        if address:
            wanted.append((address, record["building_id"], None))
    for address, p in address_points:
        point = Point(p)
        near = min(
            blockers.query(point.buffer(HINT_BUILDING_M)),
            key=lambda i: footprints[i].distance(point),
            default=None,
        )
        if (
            near is not None
            and footprints[near].distance(point) <= HINT_BUILDING_M
            and footprint_ids[near] in records
        ):
            wanted.append((" ".join(address.split()), footprint_ids[near], p))

    chosen: dict[str, tuple] = {}
    for address, building, hint in wanted:
        semantics = records[building].get("semantics", {})
        found = door(semantics, _ground_walls(facades.get(building, [])), roads, hint)
        if not found:
            continue
        (dx, dz), (nx, nz), source = found
        spot = None
        for offset in FALLBACK_OFFSETS_M:
            candidate = (dx + nx * offset, dz + nz * offset)
            point = Point(candidate)
            if not any(
                footprints[i].buffer(0.3).contains(point) for i in blockers.query(point.buffer(0.3))
            ):
                spot = candidate
                break
        if spot is None:
            continue
        tile = C.VisualTile.at(*spot).id
        if tiles is not None and tile not in tiles:
            continue
        foot = roads.snap(spot, FOOT)
        if foot is None or foot[3] > MAX_STREET_M:
            continue
        foot_m = roads.route_length(starts[FOOT], dists[FOOT], foot[:2], FOOT)
        if not math.isfinite(foot_m) or foot_m < MIN_ROUTE_M:
            continue
        car = roads.snap(spot, CAR)
        car_m = (
            roads.route_length(starts[CAR], dists[CAR], car[:2], CAR)
            if car is not None and car[3] <= MAX_SNAP_M
            else math.inf
        )
        entry = {
            "id": slug(address),
            "address": address,
            "building": building,
            "tile": tile,
            "point": [round(spot[0], 2), round(spot[1], 2)],
            "door": source,
            "street": [round(foot[2][0], 2), round(foot[2][1], 2)],
            "foot": _metres(foot_m),
            "car": _metres(car_m),
        }
        # The register point places a corner building's second address; an OSM entrance beats a wall.
        rank = (hint is None, source != "entrance", foot[3])
        previous = chosen.get(entry["id"])
        if previous is None or rank < previous[0]:
            chosen[entry["id"]] = (rank, entry)
    addresses = sorted((e for _, e in chosen.values()), key=lambda e: (e["foot"], e["id"]))
    per_tile: dict[str, int] = {}
    for e in addresses:
        per_tile[e["tile"]] = per_tile.get(e["tile"], 0) + 1
    return {
        "format": FORMAT,
        "version": VERSION,
        "frame": "engine-xz",
        "origin": [origin[0], origin[1]],
        "tiles": dict(sorted(per_tile.items())),
        "addresses": addresses,
    }


def encode(pool: dict) -> bytes:
    return (json.dumps(pool, separators=(",", ":"), ensure_ascii=False) + "\n").encode()


def publish(root: Path, tree, tiles: set[str]) -> bool:
    """Publish the pool for the tree's L0 `tiles`; True when manifest.addresses changed.

    Without a buildings layer or a road graph there is nothing to route to and the entry is removed.
    """
    previous = tree.addresses
    buildings, graph_manifest = root / "buildings", root / "roads/MANIFEST.json"
    if not (buildings / "metrics.json").is_file() or not graph_manifest.is_file():
        tree.addresses = None
        return previous is not None
    record = json.loads(graph_manifest.read_text())["params"]["graph"]
    graph = json.loads((root / "roads" / record["file"]).read_text())
    pool = build_pool(buildings, graph, read_address_points(), PIZZERIA, tiles)
    data = encode(pool)
    entry = tree._put(f"addresses/{hashlib.sha256(data).hexdigest()[:16]}.json", data)
    entry.update(count=len(pool["addresses"]))
    tree.addresses = entry
    return previous != entry
