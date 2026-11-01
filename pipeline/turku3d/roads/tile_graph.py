"""roads.json v2 of one draft tile: stable seam-independent ids, lanes, junctions, signals, crossings.

Spec: docs/specs/roads-json-v2.md. The tile reads its OSM clip around a CONTEXT-buffered box,
builds the logical (unclipped) street graph there, decides per logical edge whether it can stay
whole across tile seams (it lies inside the CONTEXT box of every tile it touches, so every such
tile sees all of it) and then clips the edges to the tile. Heights are added later by
``coarse.build`` (they need the built ground and decks).
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from dataclasses import dataclass, field

from . import CAR, CLASS_SHIFT, CLASSES, FOOT, FORMAT, ONEWAY, PRECISION, VERSION, way_flags
from . import validate as validate_v1
from .attributes import EDGE_FLAGS, HIGHWAYS, attributes

REVISION = 2
CONTEXT = 90.0  # m around the tile whose junctions every tile can see (OSM clips carry >= 100 m)
POINT_CONTEXT = 100.0  # m: signal clustering reads every signal node in the guaranteed clip
ID_QUANT = 0.5  # m: id coordinates
SIGNAL_LINK = 35.0  # m: single-linkage distance of one signal controller
APPROACH = 40.0  # m: an undirected signal applies toward a car junction this close
EXPLICIT_CROSSING = 1.0  # m: an OSM crossing way this close carries its own paint

NODE_FLAGS = {
    "JUNCTION": 1,
    "SIGNALS": 2,
    "CROSSING": 4,
    "GIVE_WAY": 8,
    "STOP": 16,
    "SEAM": 32,
    "DEAD_END": 64,
}
EVENT_KINDS = {"SIGNAL": 1, "CROSSING": 2, "GIVE_WAY": 3, "STOP": 4}
EVENT_BITS = {
    "FORWARD": 16,
    "BACKWARD": 32,
    "MARKED": 64,
    "SIGNALISED": 128,
    "EXPLICIT": 256,
    "CYCLE": 512,
}
FORWARD, BACKWARD = EVENT_BITS["FORWARD"], EVENT_BITS["BACKWARD"]
BOTH = FORWARD | BACKWARD
ATTR_COLUMNS = (
    "way",
    "s0",
    "s1",
    "length",
    "lanes_fwd",
    "lanes_bwd",
    "width",
    "kmh",
    "car_density",
    "foot_density",
    "flags",
    "highway",
    "setback0",
    "setback1",
)
EF = EDGE_FLAGS


# -- geometry -------------------------------------------------------------------------------------


@dataclass
class Run:
    points: list  # (x, z) floats, both ends included
    s0: float  # arc length of points[0] along the input polyline
    s1: float
    cut0: bool  # points[0] is a box crossing, not the polyline's first point
    cut1: bool


def _segment(p, q, box):
    """Liang-Barsky: (t0, t1) of segment p→q inside the closed box, None when outside or a point."""
    t0, t1 = 0.0, 1.0
    dx, dz = q[0] - p[0], q[1] - p[1]
    for d, lo, hi, v in ((dx, box[0], box[2], p[0]), (dz, box[1], box[3], p[1])):
        if d == 0:
            if v < lo or v > hi:
                return None
            continue
        a, b = (lo - v) / d, (hi - v) / d
        if a > b:
            a, b = b, a
        t0, t1 = max(t0, a), min(t1, b)
        if t0 >= t1:
            return None
    return t0, t1


def clip_polyline(points, box) -> list[Run]:
    """Runs of the polyline inside ``box`` with their arc range along the whole polyline.

    Both sides of a box edge compute the same crossing (same segment, same boundary value)."""
    runs, current, s = [], None, 0.0
    last = len(points) - 2
    for i in range(len(points) - 1):
        p, q = points[i], points[i + 1]
        seg = math.dist(p, q)
        span = _segment(p, q, box) if seg > 0 else None
        if span is None:
            if current is not None and seg > 0:
                runs.append(current)
                current = None
            s += seg
            continue
        t0, t1 = span
        a = (p[0] + t0 * (q[0] - p[0]), p[1] + t0 * (q[1] - p[1])) if t0 > 0 else p
        b = (p[0] + t1 * (q[0] - p[0]), p[1] + t1 * (q[1] - p[1])) if t1 < 1 else q
        if current is not None and t0 > 0:
            runs.append(current)
            current = None
        if current is None:
            current = Run([a], s + t0 * seg, s + t0 * seg, not (i == 0 and t0 == 0), False)
        current.points.append(b)
        current.s1 = s + t1 * seg
        current.cut1 = not (i == last and t1 == 1)
        if t1 < 1:
            runs.append(current)
            current = None
        s += seg
    if current is not None:
        runs.append(current)
    return runs


def _key(x: float, z: float) -> tuple[int, int]:
    return round(x / PRECISION), round(z / PRECISION)


def _xz(k) -> tuple[float, float]:
    return k[0] * PRECISION, k[1] * PRECISION


def _coord(v: float) -> float:
    return round(v, 1)


def _cumulative(points) -> list[float]:
    out = [0.0]
    for i in range(1, len(points)):
        out.append(out[-1] + math.dist(points[i - 1], points[i]))
    return out


def _at(points, cumulative, s):
    for i in range(1, len(points)):
        if cumulative[i] >= s or i == len(points) - 1:
            span = cumulative[i] - cumulative[i - 1]
            t = 0.0 if span <= 0 else min(max((s - cumulative[i - 1]) / span, 0.0), 1.0)
            p, q = points[i - 1], points[i]
            return p[0] + t * (q[0] - p[0]), p[1] + t * (q[1] - p[1])
    return points[-1]


def _q(v: float) -> int:
    return math.floor(v / ID_QUANT + 0.5)


def stable_id(prefix: str, source, points) -> str:
    """Spec §3.2: sha256 of source id and quantised start, end and arc midpoint, 16 hex digits."""
    cumulative = _cumulative(points)
    mid = _at(points, cumulative, cumulative[-1] / 2)
    parts = [f"{_q(p[0])},{_q(p[1])}" for p in (points[0], points[-1], mid)]
    text = f"{prefix}{source}|" + "|".join(parts)
    return hashlib.sha256(text.encode()).hexdigest()[:16]


# -- context --------------------------------------------------------------------------------------


@dataclass
class Way:
    coords: list
    flags: int
    way: int
    highway: str | None
    tags: dict


@dataclass
class Logical:
    keys: list
    way: Way
    touches: bool  # an end is the context box boundary: the edge continues beyond what we see
    points: list = field(default_factory=list)
    cumulative: list = field(default_factory=list)
    attrs: dict = field(default_factory=dict)
    whole: bool = False
    id: str = ""
    events: list = field(default_factory=list)  # (s, code, controller id or None)


def read_osm(path, bbox):
    """(ways, points, crossing lines) of the OSM clip at ``path`` around ``bbox`` (BBoxLocal)."""
    from turku3d.spatial import frame as spatial_frame

    ways, points, crossings = [], [], []
    rows = spatial_frame(path, bbox.buffered(POINT_CONTEXT))
    for row in rows.itertuples():
        tags = json.loads(row.tags) if row.tags else {}
        geom = row.geometry_local
        if geom is None or geom.is_empty:
            continue
        if geom.geom_type == "Point":
            points.append((geom.x, geom.y, tags))
            continue
        if geom.geom_type != "LineString" or row.highway is None:
            continue
        if "crossing" in (tags.get("footway"), tags.get("cycleway"), tags.get("path")):
            crossings.append((geom, tags))
        flags = way_flags(row.highway, tags)
        if not flags:
            continue
        coords = [(x, z) for x, z, *_ in geom.coords]
        if tags.get("oneway") == "-1":
            coords = coords[::-1]
        # Clips without osm_type/osm_id (synthetic fixtures) still build; ids then use way 0.
        osm_id = getattr(row, "osm_id", None)
        way = int(osm_id) if getattr(row, "osm_type", "way") == "way" and osm_id is not None else 0
        ways.append(Way(coords, flags, way, row.highway, tags))
    return ways, points, crossings


def logical_edges(ways, box) -> list[Logical]:
    """Junction-to-junction edges of the ways clipped to ``box`` (the context box)."""
    parts = []
    for way in ways:
        for run in clip_polyline(way.coords, box):
            keys = []
            for x, z in run.points:
                k = _key(x, z)
                if not keys or keys[-1] != k:
                    keys.append(k)
            if len(keys) >= 2:
                parts.append((keys, way, run.cut0, run.cut1))
    uses = defaultdict(int)
    for keys, *_ in parts:
        for k in keys[1:-1]:
            uses[k] += 1
        uses[keys[0]] += 2
        uses[keys[-1]] += 2
    out = []
    for keys, way, cut0, cut1 in parts:
        start = 0
        for i in range(1, len(keys)):
            if uses[keys[i]] >= 2:
                touches = (start == 0 and cut0) or (i == len(keys) - 1 and cut1)
                out.append(Logical(keys[start : i + 1], way, touches))
                start = i
    for edge in out:
        edge.points = [_xz(k) for k in edge.keys]
        edge.cumulative = _cumulative(edge.points)
    return out


def stays_whole(edge: Logical, size: float) -> bool:
    """Spec §3.2 seam rule: inside the (CONTEXT - 0.5 m) box of every tile its bbox overlaps."""
    if edge.touches:
        return False
    xs = [p[0] for p in edge.points]
    zs = [p[1] for p in edge.points]
    reach = CONTEXT - 0.5
    for lo, hi in ((min(xs), max(xs)), (min(zs), max(zs))):
        first, last = math.floor(lo / size), math.floor(hi / size)
        if hi - (first + 1) * size > reach or last * size - lo > reach:
            return False
    return True


# -- points ---------------------------------------------------------------------------------------


def _union(parent, k):
    while parent[k] != k:
        parent[k] = parent[parent[k]]
        k = parent[k]
    return k


def controllers(points) -> dict:
    """Signal node key -> (controller id, x, z): single-linkage SIGNAL_LINK clusters."""
    signals = [
        (x, z)
        for x, z, tags in points
        if tags.get("highway") == "traffic_signals" or tags.get("crossing") == "traffic_signals"
    ]
    signals = sorted(set(signals))
    parent = list(range(len(signals)))
    cell = defaultdict(list)
    for i, (x, z) in enumerate(signals):
        cx, cz = math.floor(x / SIGNAL_LINK), math.floor(z / SIGNAL_LINK)
        for dx in (-1, 0, 1):
            for dz in (-1, 0, 1):
                for j in cell[(cx + dx, cz + dz)]:
                    if math.dist(signals[i], signals[j]) <= SIGNAL_LINK:
                        parent[_union(parent, i)] = _union(parent, j)
        cell[(cx, cz)].append(i)
    groups = defaultdict(list)
    for i in range(len(signals)):
        groups[_union(parent, i)].append(signals[i])
    out = {}
    for members in groups.values():
        text = "s" + ";".join(sorted(f"{_q(x)},{_q(z)}" for x, z in members))
        cid = hashlib.sha256(text.encode()).hexdigest()[:16]
        cx = round(sum(x for x, _ in members) / len(members), 1)
        cz = round(sum(z for _, z in members) / len(members), 1)
        for x, z in members:
            out[_key(x, z)] = (cid, cx, cz)
    return out


def _crossing_code(tags: dict, explicit: bool) -> int:
    code = EVENT_KINDS["CROSSING"] | BOTH
    markings = str(tags.get("crossing:markings", ""))
    kind = tags.get("crossing")
    # Zebra paint only where OSM says so; an untagged crossing (often a driveway mouth) stays bare.
    zebra = markings == "yes" or markings.startswith(("zebra", "ladder"))
    zebra = zebra or (
        not markings and kind in ("marked", "zebra", "uncontrolled", "traffic_signals")
    )
    if zebra and kind not in ("unmarked", "no", "informal"):
        code |= EVENT_BITS["MARKED"]
    if kind == "traffic_signals" or tags.get("crossing:signals") == "yes":
        code |= EVENT_BITS["SIGNALISED"]
    if explicit:
        code |= EVENT_BITS["EXPLICIT"]
    if tags.get("bicycle") in ("yes", "designated") and tags.get("foot") == "no":
        code |= EVENT_BITS["CYCLE"]
    if tags.get("cycleway") == "crossing" and tags.get("foot") != "yes":
        code |= EVENT_BITS["CYCLE"]
    return code


def _point_kinds(tags: dict) -> list[str]:
    highway = tags.get("highway")
    kinds = []
    if highway == "traffic_signals":
        kinds.append("SIGNAL")
    if highway == "crossing" or (highway == "traffic_signals" and "crossing" in tags):
        kinds.append("CROSSING")
    if highway == "give_way":
        kinds.append("GIVE_WAY")
    if highway == "stop":
        kinds.append("STOP")
    return kinds


def _direction(edge: Logical, s: float, tags: dict, kind: str, car_degree) -> int:
    value = tags.get("traffic_signals:direction") if kind == "SIGNAL" else None
    value = value or tags.get("direction")
    if value in ("forward", "backward"):
        if edge.way.tags.get("oneway") == "-1":
            value = "forward" if value == "backward" else "backward"
        return FORWARD if value == "forward" else BACKWARD
    if edge.way.flags & ONEWAY:
        return FORWARD
    length = edge.cumulative[-1]
    to_end = length - s if car_degree[edge.keys[-1]] >= 3 else math.inf
    to_start = s if car_degree[edge.keys[0]] >= 3 else math.inf
    if min(to_end, to_start) > APPROACH:
        return BOTH
    return FORWARD if to_end <= to_start else BACKWARD


def attach_points(edges, points, crossing_lines, car_degree):
    """Events on car logical edges and node features keyed by node key (spec §3.6–3.7)."""
    import shapely

    signal_groups = controllers(points)
    vertices = defaultdict(list)
    node_keys = set()
    for index, edge in enumerate(edges):
        node_keys.update((edge.keys[0], edge.keys[-1]))
        if edge.way.flags & CAR:
            for i, k in enumerate(edge.keys):
                vertices[k].append((index, i))
    lines = [g for g, _ in crossing_lines]
    tree = shapely.STRtree(lines) if lines else None
    features = defaultdict(set)
    for x, z, tags in points:
        kinds = _point_kinds(tags)
        if not kinds:
            continue
        k = _key(x, z)
        hits = vertices.get(k)
        if not hits:
            near = [
                (k[0] + dx, k[1] + dz)
                for dx in (-1, 0, 1)
                for dz in (-1, 0, 1)
                if (k[0] + dx, k[1] + dz) in vertices
            ]
            if not near:
                continue
            k = min(near)
            hits = vertices[k]
        group = signal_groups.get(_key(x, z))
        explicit = False
        if "CROSSING" in kinds and tree is not None:
            explicit = (
                len(
                    tree.query(
                        shapely.Point(x, z).buffer(EXPLICIT_CROSSING), predicate="intersects"
                    )
                )
                > 0
            )
        for kind in kinds:
            features[k].add(kind)
        at_node = k in node_keys
        for index, i in hits:
            edge = edges[index]
            s = edge.cumulative[i]
            for kind in kinds:
                if kind == "CROSSING":
                    code = _crossing_code(tags, explicit)
                    ref = group[0] if code & EVENT_BITS["SIGNALISED"] and group else None
                elif at_node and car_degree[k] >= 3:
                    # A signal or sign on a junction node stops traffic arriving there.
                    incoming = FORWARD if i == len(edge.keys) - 1 else BACKWARD
                    if incoming == BACKWARD and edge.way.flags & ONEWAY:
                        continue
                    code = EVENT_KINDS[kind] | incoming
                    ref = group[0] if kind == "SIGNAL" and group else None
                else:
                    code = EVENT_KINDS[kind] | _direction(edge, s, tags, kind, car_degree)
                    ref = group[0] if kind == "SIGNAL" and group else None
                edge.events.append((s, code, ref))
    return features, {cid: (cx, cz) for cid, cx, cz in signal_groups.values()}


# -- tile graph -----------------------------------------------------------------------------------


def build_tile_graph(ways, points, crossing_lines, bbox, area: str, size: float) -> dict:
    """roads.json v2 (without heights) of the tile ``bbox`` from its OSM context."""
    context = bbox.buffered(CONTEXT).as_tuple()
    edges = logical_edges(ways, context)
    car_degree, degree = defaultdict(int), defaultdict(int)
    car_ends = defaultdict(list)
    for index, edge in enumerate(edges):
        for k in (edge.keys[0], edge.keys[-1]):
            degree[k] += 1
            if edge.way.flags & CAR:
                car_degree[k] += 1
                car_ends[k].append(index)
    for edge in edges:
        edge.attrs = attributes(edge.way.highway, edge.way.flags, edge.way.tags)
    for index, edge in enumerate(edges):
        flags, setbacks = edge.attrs["flags"], []
        for end, bit in ((edge.keys[0], "START_JUNCTION"), (edge.keys[-1], "END_JUNCTION")):
            if edge.way.flags & CAR and car_degree[end] >= 3:
                flags |= EF[bit]
                others = [edges[j].attrs["width"] for j in car_ends[end] if j != index]
                if edge.keys[0] == edge.keys[-1]:
                    others.append(edge.attrs["width"])
                setbacks.append(round(max(others, default=0.0) / 2 + 1.0, 1))
            else:
                setbacks.append(0.0)
        edge.attrs.update(flags=flags, setback0=setbacks[0], setback1=setbacks[1])
        edge.whole = stays_whole(edge, size)
        edge.id = stable_id("o", edge.way.way, edge.points) if edge.whole else ""
    features, groups = attach_points(edges, points, crossing_lines, car_degree)
    tile_box = bbox.as_tuple()
    rows = []
    for edge in edges:
        for run in clip_polyline(edge.points, tile_box):
            keys = []
            for x, z in run.points:
                k = _key(x, z)
                if not keys or keys[-1] != k:
                    keys.append(k)
            if len(keys) < 2:
                continue
            piece = [_xz(k) for k in keys]
            length = _cumulative(piece)[-1]
            if edge.whole:
                eid, s0, s1, total = edge.id, run.s0, run.s1, edge.cumulative[-1]
                offset = run.s0
            else:
                eid = stable_id("o", edge.way.way, piece)
                s0, s1, total, offset = 0.0, length, length, run.s0
            events = []
            for s, code, ref in edge.events:
                if run.s0 - 0.05 <= s <= run.s1 + 0.05:
                    events.append((round(min(max(s - offset, 0.0), length), 2), code, ref))
            rows.append(
                dict(
                    keys=keys,
                    flags=edge.way.flags,
                    id=eid,
                    span=(round(s0, 2), round(s1, 2), round(total, 2)),
                    attrs=edge.attrs,
                    way=edge.way.way,
                    events=sorted(events, key=lambda e: (e[0], e[1], e[2] or "")),
                    seams=(run.cut0, run.cut1),
                )
            )
    rows.sort(key=lambda r: (r["id"], r["span"][0], r["keys"][0], r["keys"][-1]))
    index: dict = {}
    nodes, out_edges, ids, attrs, events, node_flags = [], [], [], [], [], []
    seam_nodes = set()
    refs: dict[str, int] = {}
    for row in rows:
        ends = []
        for k, seam in ((row["keys"][0], row["seams"][0]), (row["keys"][-1], row["seams"][1])):
            if k not in index:
                index[k] = len(index)
                nodes.extend(_xz(k))
            if seam:
                seam_nodes.add(k)
            ends.append(index[k])
        length = _cumulative([_xz(k) for k in row["keys"]])[-1]
        interior = [_coord(v) for k in row["keys"][1:-1] for v in _xz(k)]
        out_edges.append([ends[0], ends[1], row["flags"], round(length, 1), *interior])
        ids.append(row["id"])
        a = row["attrs"]
        attrs.append(
            [
                row["way"],
                *row["span"],
                a["lanes_fwd"],
                a["lanes_bwd"],
                a["width"],
                a["kmh"],
                a["car_density"],
                a["foot_density"],
                a["flags"],
                a["highway"],
                a["setback0"],
                a["setback1"],
            ]
        )
        flat = []
        for s, code, ref in row["events"]:
            if ref is not None and ref not in refs:
                refs[ref] = -1
            flat.extend((s, code, ref))
        events.append(flat or None)
    order = sorted(refs)
    refs = {cid: i for i, cid in enumerate(order)}
    for flat in events:
        if flat:
            for i in range(2, len(flat), 3):
                flat[i] = -1 if flat[i] is None else refs[flat[i]]
    for k in index:
        f = 0
        if car_degree[k] >= 3:
            f |= NODE_FLAGS["JUNCTION"]
        for kind, bit in (
            ("SIGNAL", "SIGNALS"),
            ("CROSSING", "CROSSING"),
            ("GIVE_WAY", "GIVE_WAY"),
            ("STOP", "STOP"),
        ):
            if kind in features.get(k, ()):
                f |= NODE_FLAGS[bit]
        if k in seam_nodes and degree.get(k, 0) == 0:
            f |= NODE_FLAGS["SEAM"]
        if degree.get(k, 0) == 1 and k not in seam_nodes:
            f |= NODE_FLAGS["DEAD_END"]
        node_flags.append(f)
    graph = {
        "format": FORMAT,
        "version": VERSION,
        "revision": REVISION,
        "frame": "engine-xz",
        "area": area,
        "bbox": [round(float(v), 1) for v in tile_box],
        "bits": {
            "foot": FOOT,
            "car": CAR,
            "oneway": ONEWAY,
            "class_shift": CLASS_SHIFT,
            "classes": list(CLASSES),
            "edge_flags": dict(EDGE_FLAGS),
            "node_flags": dict(NODE_FLAGS),
            "event_kinds": dict(EVENT_KINDS),
            "event_bits": dict(EVENT_BITS),
            "highways": list(HIGHWAYS),
        },
        "nodes": [_coord(v) for v in nodes],
        "edges": out_edges,
        "edgeIds": ids,
        "edgeAttrColumns": list(ATTR_COLUMNS),
        "edgeAttrs": attrs,
        "edgeEvents": events,
        "nodeFlags": node_flags,
        "controllers": [[cid, *groups[cid]] for cid in order],
    }
    validate(graph)
    return graph


def validate(graph: dict) -> None:
    """v1 checks plus the v2 parallel arrays (spec §3)."""
    validate_v1(graph)
    if graph.get("revision", 1) < 2:
        return
    count = len(graph["edges"])
    for name in ("edgeIds", "edgeAttrs", "edgeEvents"):
        if len(graph[name]) != count:
            raise ValueError(f"{name}: {len(graph[name])} rows for {count} edges")
    if "edgeHeights" in graph and len(graph["edgeHeights"]) != count:
        raise ValueError("edgeHeights must be parallel to edges")
    if len(graph["nodeFlags"]) != len(graph["nodes"]) // 2:
        raise ValueError("nodeFlags must be parallel to nodes")
    width = len(graph["edgeAttrColumns"])
    columns = {name: i for i, name in enumerate(graph["edgeAttrColumns"])}
    for i, row in enumerate(graph["edgeAttrs"]):
        if len(row) != width:
            raise ValueError(f"edgeAttrs[{i}] has {len(row)} columns")
        s0, s1, total = row[columns["s0"]], row[columns["s1"]], row[columns["length"]]
        if not (0 <= s0 < s1 <= total + 0.01):
            raise ValueError(f"edgeAttrs[{i}]: bad span {s0}..{s1} of {total}")
    controllers_count = len(graph["controllers"])
    for i, flat in enumerate(graph["edgeEvents"]):
        if flat is None:
            continue
        if len(flat) % 3 or not all(
            -1 <= flat[j] < controllers_count for j in range(2, len(flat), 3)
        ):
            raise ValueError(f"edgeEvents[{i}] malformed")


def prepare(path, bbox, area: str, size: float) -> dict:
    ways, points, crossings = read_osm(path, bbox)
    return build_tile_graph(ways, points, crossings, bbox, area, size)


HEIGHT_STEP = 8.0  # m: longest polyline segment once heights are sampled (spec §3.1)


def with_heights(roads: dict, sample) -> dict:
    """Give every edge one Y per vertex (spec §3.1).

    Bridge-deck and tunnel-floor arrays written earlier stay; every other edge is densified to
    HEIGHT_STEP and sampled on the tile's walkable ground ``sample(x, z)``."""
    from . import encode

    graph = json.loads(roads["json"])
    if graph.get("revision", 1) < 2:
        return roads
    nodes, edges = graph["nodes"], graph["edges"]
    heights = graph.get("edgeHeights") or [None] * len(edges)
    for k, edge in enumerate(edges):
        if heights[k] is not None:
            continue
        a, b = edge[0], edge[1]
        points = [(nodes[2 * a], nodes[2 * a + 1])]
        points += list(zip(edge[4::2], edge[5::2], strict=True))
        points.append((nodes[2 * b], nodes[2 * b + 1]))
        dense = [points[0]]
        for p, q in zip(points, points[1:], strict=False):
            steps = max(1, math.ceil(math.dist(p, q) / HEIGHT_STEP))
            for i in range(1, steps):
                t = i / steps
                dense.append(
                    (round(p[0] + t * (q[0] - p[0]), 2), round(p[1] + t * (q[1] - p[1]), 2))
                )
            dense.append(q)
        ys = [float(sample(x, z)) for x, z in dense]
        finite = [y for y in ys if math.isfinite(y)]
        if not finite:
            continue
        last = finite[0]
        for i, y in enumerate(ys):
            ys[i] = last = y if math.isfinite(y) else last
        edge[4:] = [v for p in dense[1:-1] for v in p]
        heights[k] = [round(y, 3) for y in ys]
    graph["edgeHeights"] = heights
    validate(graph)
    return {**roads, "json": encode(graph)}
