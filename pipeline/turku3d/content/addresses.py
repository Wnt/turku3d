"""Delivery address pool: every addressed building in the published tiles with a street-side door.

Inputs are the buildings layer (``metrics.json`` semantics: city/OSM address and OSM entrances;
``facades.geojson``: street-facing wall baselines; ``footprints.geojson``: every footprint) and the
road graph layer (``roadgraph.json``, named streets from its OSM snapshot). For each building with an
address the door is the best OSM entrance on a street-side ground-floor wall, or the middle of its
widest street-side wall, preferring walls that face the address's own street. A wall is street-side
when the walkable network crosses its front strip (a sidewalk traced along the facade counts). The
drop-off is the sidewalk spot ``SIDEWALK_M`` in front of the door, outside every footprint (a wall
whose spot is not, such as one shared with a neighbour, gets no door) and within ``MAX_STREET_M`` of
the walkable street network. Route distances are shortest graph paths from the
pizzeria on foot and by car (oneways honoured), measured like the game's router: from the point
where the pizzeria snaps onto the graph to the point where the drop-off does.

The assembler publishes the pool next to the road graph as ``dist/tiles/addresses/<sha[:16]>.json``
(``manifest.addresses``); the game draws its delivery orders from it.
"""

from __future__ import annotations

import hashlib
import heapq
import itertools
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
# The network may be drawn this far behind a wall's line and still be in front (OSM sidewalks are
# traced along the facades).
ALONG_WALL_M = 2.0
# A wall carries a street's name when the street's centre line runs this close in front of it.
NAMED_STREET_M = 25.0
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


def street_key(name: str) -> str:
    return " ".join(name.casefold().split())


def street_of(address: str) -> str:
    """The street part of an address as a `street_key` ("Läntinen Rantakatu 13a" -> "läntinen rantakatu")."""
    match = re.match(r"(.+?)\s+\d", address.strip())
    return street_key(match.group(1) if match else address)


def _in_front(wall: dict, lines, far: float) -> float | None:
    """Distance from the wall to the nearest part of `lines` in front of it, or None when none is.

    A line is in front where it crosses the strip the wall sweeps along its outward normal, at a
    signed distance from the wall's line between -ALONG_WALL_M (a sidewalk traced along the facade)
    and `far`.
    """
    (ax, az), (bx, bz) = wall["a"], wall["b"]
    nx, nz = wall["normal"]
    length = math.hypot(bx - ax, bz - az)
    tx, tz = (bx - ax) / length, (bz - az) / length
    best = None
    for pts in lines:
        for p, q in itertools.pairwise(pts):
            u0 = (p[0] - ax) * tx + (p[1] - az) * tz
            u1 = (q[0] - ax) * tx + (q[1] - az) * tz
            s0 = (p[0] - ax) * nx + (p[1] - az) * nz
            s1 = (q[0] - ax) * nx + (q[1] - az) * nz
            lo, hi = 0.0, 1.0
            if u1 != u0:
                ta, tb = -u0 / (u1 - u0), (length - u0) / (u1 - u0)
                lo, hi = max(lo, min(ta, tb)), min(hi, max(ta, tb))
            elif not 0.0 <= u0 <= length:
                continue
            if lo > hi:
                continue
            sa, sb = s0 + (s1 - s0) * lo, s0 + (s1 - s0) * hi
            first, last = max(min(sa, sb), -ALONG_WALL_M), min(max(sa, sb), far)
            if first <= last and (best is None or max(first, 0.0) < best):
                best = max(first, 0.0)
    return best


def _street_side(wall: dict, roads: Graph) -> tuple[float, bool] | None:
    """(distance to the walkable network, whether it is in front of the wall), or None without a street side.

    The street is in front when the network crosses the wall's front strip within FRONT_STREET_M;
    a wall the layer flags `street_facing` also counts with the network elsewhere, at its distance
    from the sidewalk spot.
    """
    front = _in_front(wall, roads.walkable, FRONT_STREET_M)
    if front is not None:
        return front, True
    if not wall["street"]:
        return None
    mid = ((wall["a"][0] + wall["b"][0]) / 2, (wall["a"][1] + wall["b"][1]) / 2)
    spot = (mid[0] + wall["normal"][0] * SIDEWALK_M, mid[1] + wall["normal"][1] * SIDEWALK_M)
    snapped = roads.snap(spot, FOOT)
    return None if snapped is None else (snapped[3], False)


def doors(semantics: dict, walls: list[dict], roads: Graph, hint=None, named=()):
    """Door candidates (point on a wall, outward normal, source) for one address, best first.

    Only street-side walls carry doors. `named` are the centre lines of the address's street: the
    walls with the walkable network and one of those lines in front of them come first. Within each
    group the OSM entrances come first, then the walls: the one nearest to `hint`, the city
    register's address point (with several addresses on one building, a corner block, the door is
    on the wall nearest to it), else the widest.
    """
    on_street, others = [], []
    for wall in walls:
        side = _street_side(wall, roads)
        if side is None:
            continue
        distance, ahead = side
        faces_name = ahead and named and _in_front(wall, named, NAMED_STREET_M) is not None
        (on_street if faces_name else others).append((wall, distance))
    near = (lambda w: _project(hint, w["a"], w["b"])[1]) if hint else (lambda w: 0.0)
    for group in (on_street, others):
        entrances = []
        for entrance in semantics.get("entrances", []):
            kind = entrance.get("tags", {}).get("entrance")
            if kind not in ENTRANCE_RANK or entrance["geometry"]["type"] != "Point":
                continue
            p = _local(*entrance["geometry"]["coordinates"][:2])
            if hint and math.dist(p, hint) > HINT_ENTRANCE_M:
                continue
            for wall, _ in group:
                q, d = _project(p, wall["a"], wall["b"])
                if d <= ENTRANCE_ON_WALL_M:
                    entrances.append(((near(wall) > HINT_WALL_M, ENTRANCE_RANK[kind], d), q, wall))
        for _, q, wall in sorted(entrances, key=lambda c: c[0]):
            yield q, wall["normal"], "entrance"
        for wall, _ in sorted(
            group,
            key=lambda wd: (round(near(wd[0]), 1), not wd[0]["street"], -wd[0]["width"], wd[1]),
        ):
            if hint:
                point = _project(hint, wall["a"], wall["b"])[0]
            else:
                point = ((wall["a"][0] + wall["b"][0]) / 2, (wall["a"][1] + wall["b"][1]) / 2)
            yield point, wall["normal"], "wall"


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
        self.walkable = [e["points"] for e in self.edges if e["flags"] & FOOT]

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


def read_streets(osm: Path) -> dict[str, list[list[tuple[float, float]]]]:
    """Named OSM highway centre lines (engine x/z) by `street_key` from one OSM clip."""
    import geopandas as gpd

    frame = gpd.read_parquet(osm, columns=["highway", "tags", "geometry_local"])
    streets: dict[str, list[list[tuple[float, float]]]] = {}
    for row in frame[frame["highway"].notna()].itertuples():
        name = json.loads(row.tags).get("name")
        if name and row.geometry_local.geom_type == "LineString":
            streets.setdefault(street_key(name), []).append(
                [(x, z) for x, z, *_ in row.geometry_local.coords]
            )
    return streets


def build_pool(
    buildings_dir: Path,
    graph: dict,
    address_points=(),
    origin=PIZZERIA,
    tiles: set[str] | None = None,
    streets: dict | None = None,
) -> dict:
    """Address pool from a buildings layer, a road graph and register address points (engine x/z).

    Every address comes from a building's own address or from a register point on or next to a
    footprint; `tiles` limits the pool to drop-offs in those L0 tiles. `streets` (`read_streets`)
    puts each door on a wall facing the address's own street.
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

    def outside_footprints(spot) -> bool:
        p = Point(spot)
        return not any(footprints[i].buffer(0.3).contains(p) for i in blockers.query(p.buffer(0.3)))

    def drop_off(candidates):
        """(spot, source) of the first door with a sidewalk spot outside every footprint."""
        for (px, pz), (nx, nz), source in candidates:
            spots = ((px + nx * offset, pz + nz * offset) for offset in FALLBACK_OFFSETS_M)
            if spot := next((s for s in spots if outside_footprints(s)), None):
                return spot, source
        return None

    chosen: dict[str, tuple] = {}
    for address, building, hint in wanted:
        semantics = records[building].get("semantics", {})
        walls = _ground_walls(facades.get(building, []))
        named = (streets or {}).get(street_of(address), ())
        found = drop_off(doors(semantics, walls, roads, hint, named))
        if not found:
            continue
        spot, source = found
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
    manifest = json.loads(graph_manifest.read_text())
    graph = json.loads((root / "roads" / manifest["params"]["graph"]["file"]).read_text())
    # Named streets from the OSM clip of each tile's own input area (the roads layer's sources).
    from turku3d.tile_layer import osm

    streets: dict[str, list] = {}
    for path in sorted({osm(tile) for tile in tiles}):
        for key, lines in read_streets(path).items():
            streets.setdefault(key, []).extend(lines)
    pool = build_pool(buildings, graph, read_address_points(), PIZZERIA, tiles, streets)
    data = encode(pool)
    entry = tree._put(f"addresses/{hashlib.sha256(data).hexdigest()[:16]}.json", data)
    entry.update(count=len(pool["addresses"]))
    tree.addresses = entry
    return previous != entry
