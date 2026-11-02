"""Rule road paint from roads.json v2 (phase G3): centre, lane and edge lines, crosswalks, stop lines.

OSM rarely maps lines (Turku has no ``road_marking`` ways), so lines follow the lanes and class
of each car edge, crosswalks come from OSM crossing points, stop lines from signal stop
positions. White lines separate opposing traffic and same-direction lanes, and mark carriageway
edges, crossings and stop lines, matching the current Turku street references. Dash phases run along the
logical edge (``s0`` + local arc), so a whole edge's pattern continues across tile seams.

Output items are ``(polygon, rgb, deck, kind)``: rgb multiplies the existing ``road_paint`` base colour
(COLOR_0), ``deck`` says whether the paint belongs on a bridge deck or on the ground. Polygons are
draped by ``coarse.surfaces.paint_visual`` onto the tile's road triangles only, so paint never
lands on buildings, kerbs, sidewalks or water.
"""

from __future__ import annotations

import math

from . import CAR
from .attributes import EDGE_FLAGS as F
from .attributes import HIGHWAYS, MAJOR

WHITE = (1.0, 1.0, 1.0)
YELLOW = (1.0, 0.66, 0.09)  # Explicit yellow paint remains supported by the colour-preserving mesh path.
LINE_WIDTH = 0.12  # m: centre and lane lines (urban)
RURAL_WIDTH = 0.15  # m: lines on 60+ km/h roads, edge lines
DASH = 3.0  # m
URBAN_GAP, RURAL_GAP = 5.0, 9.0  # m
CROSSWALK_LENGTH = 3.0  # m along the road (OSM crossing width default)
STRIPE, PERIOD = 0.5, 1.0  # m: zebra bars across the walking direction
STOP_WIDTH = 0.5  # m
CROSSING_CLEAR = 2.5  # m: lines stop this far either side of a zebra centre
EDGE_INSET = 0.25  # m: edge line centre inside the carriageway edge
# m past the lane-rule carriageway edge: zebras and stop lines reach the real kerb, where the drape onto
# road triangles cuts them (OSM centrelines and default widths rarely match the surveyed kerbs).
KERB_REACH = 1.5
CENTRE_CLASSES = MAJOR | {
    "tertiary",
    "motorway_link",
    "trunk_link",
    "primary_link",
    "secondary_link",
    "tertiary_link",
}
MINOR_CLASSES = {"residential", "unclassified"}


def _edges(graph: dict):
    nodes = graph["nodes"]
    columns = {name: i for i, name in enumerate(graph["edgeAttrColumns"])}
    events = graph.get("edgeEvents") or [None] * len(graph["edges"])
    for row, attrs, flat in zip(graph["edges"], graph["edgeAttrs"], events, strict=True):
        a, b = row[0], row[1]
        points = [(nodes[2 * a], nodes[2 * a + 1])]
        points += list(zip(row[4::2], row[5::2], strict=True))
        points.append((nodes[2 * b], nodes[2 * b + 1]))
        yield row[2], points, {name: attrs[i] for name, i in columns.items()}, flat or []


def _cumulative(points):
    out = [0.0]
    for i in range(1, len(points)):
        out.append(out[-1] + math.dist(points[i - 1], points[i]))
    return out


def _frame(points, cumulative, s):
    """Point and unit tangent at arc length s."""
    for i in range(1, len(points)):
        if cumulative[i] >= s or i == len(points) - 1:
            span = max(cumulative[i] - cumulative[i - 1], 1e-9)
            t = min(max((s - cumulative[i - 1]) / span, 0.0), 1.0)
            p, q = points[i - 1], points[i]
            tx, tz = (q[0] - p[0]) / span, (q[1] - p[1]) / span
            return (p[0] + t * (q[0] - p[0]), p[1] + t * (q[1] - p[1])), (tx, tz)
    return points[-1], (1.0, 0.0)


def _rect(centre, tangent, along, across):
    """Quad spanning ``along`` (tangent) x ``across`` (right normal) around ``centre``."""
    import shapely

    (cx, cz), (tx, tz) = centre, tangent
    rx, rz = -tz, tx  # right-hand normal, x east / z south
    corners = [
        (cx + tx * a + rx * o, cz + tz * a + rz * o)
        for a, o in (
            (along[0], across[0]),
            (along[1], across[0]),
            (along[1], across[1]),
            (along[0], across[1]),
        )
    ]
    return shapely.Polygon(corners)


def _subtract(intervals, cuts):
    for lo, hi in cuts:
        out = []
        for a, b in intervals:
            if hi <= a or lo >= b:
                out.append((a, b))
                continue
            if lo > a:
                out.append((a, lo))
            if hi < b:
                out.append((hi, b))
        intervals = out
    return [(a, b) for a, b in intervals if b - a > 0.05]


def _line(line, intervals, offset, width):
    import shapely
    from shapely.ops import substring

    out = []
    for a, b in intervals:
        sub = substring(line, a, b)
        if sub.is_empty or sub.length < 0.05:
            continue
        if offset:
            sub = shapely.offset_curve(sub, offset, join_style="mitre", mitre_limit=2.0)
            if sub.is_empty:
                continue
        poly = sub.buffer(width / 2, cap_style="flat", join_style="mitre", mitre_limit=2.0)
        if not poly.is_empty and poly.area > 0.001:
            out.append(poly)
    return out


def _dashes(s0, length, dash, gap):
    """Local [a, b] dash intervals of a pattern anchored at logical arc 0."""
    period = dash + gap
    out = []
    k = math.floor((s0 - dash) / period)
    while k * period - s0 < length:
        a, b = k * period - s0, k * period + dash - s0
        if b > 0:
            out.append((max(a, 0.0), min(b, length)))
        k += 1
    return [(a, b) for a, b in out if b - a > 0.05]


def _layout(attrs):
    """(divider offset, lane width, half width) of the carriageway (spec §3.4)."""
    fwd, bwd, width = attrs["lanes_fwd"], attrs["lanes_bwd"], attrs["width"]
    lanes = max(fwd + bwd, 1)
    w = width / lanes
    return -width / 2 + bwd * w, w, width / 2


EXPLICIT_CLEAR = 0.75  # m: rule lines stop this far from explicit OSM marking polygons


def markings(graph: dict | None, explicit=()) -> list:
    """Rule paint items ``(polygon, rgb, deck, kind)`` for one tile's v2 graph (empty for v1).

    ``explicit`` holds the OSM marking polygons painted anyway (crossing ways): rule lines and stop
    lines keep clear of them, also where a crossing way shares no node with the road."""
    import shapely

    if not graph or graph.get("revision", 1) < 2:
        return []
    kinds, bits = graph["bits"]["event_kinds"], graph["bits"]["event_bits"]
    items, crossings, stops = [], {}, {}
    for flags, points, attrs, flat in _edges(graph):
        if not flags & CAR:
            continue
        eflags = attrs["flags"]
        if not eflags & F["PAVED"] or eflags & (F["SHARED_SPACE"] | F["TUNNEL"]):
            continue
        deck = bool(eflags & F["BRIDGE"])
        line = shapely.LineString(points)
        cumulative = _cumulative(points)
        length = cumulative[-1]
        if length < 0.2:
            continue
        s0, total = attrs["s0"], attrs["length"]
        highway = HIGHWAYS[int(attrs["highway"])]
        events = [(flat[i], int(flat[i + 1])) for i in range(0, len(flat), 3)]
        o_div, w, half = _layout(attrs)
        fwd, bwd = attrs["lanes_fwd"], attrs["lanes_bwd"]

        # Crosswalks: one per crossing point, centred on the centreline across the carriageway.
        zebra_at = []
        for s, code in events:
            if code & 15 != kinds["CROSSING"] or not code & bits["MARKED"]:
                continue
            zebra_at.append(s)
            if code & (bits["EXPLICIT"] | bits["CYCLE"]):
                continue
            centre, tangent = _frame(points, cumulative, s)
            key = (round(centre[0] * 2), round(centre[1] * 2))
            reach = half + KERB_REACH
            n = math.ceil(reach / PERIOD)
            bars = [
                _rect(
                    centre,
                    tangent,
                    (-CROSSWALK_LENGTH / 2, CROSSWALK_LENGTH / 2),
                    (k * PERIOD - STRIPE / 2, k * PERIOD + STRIPE / 2),
                )
                for k in range(-n, n + 1)
            ]
            # A crossing on a node is on every car edge there: the widest carriageway is the one crossed.
            if key not in crossings or crossings[key][2] < attrs["width"]:
                crossings[key] = (bars, deck, attrs["width"])

        # Stop lines at signal and stop-sign positions, ahead of any zebra.
        for s, code in events:
            if code & 15 not in (kinds["SIGNAL"], kinds["STOP"]):
                continue
            direction = code & (bits["FORWARD"] | bits["BACKWARD"])
            for sense in (bits["FORWARD"], bits["BACKWARD"]):
                if not direction & sense:
                    continue
                forward = sense == bits["FORWARD"]
                # A stop position on a node is on both pieces meeting there: the approach piece
                # (the one traffic arrives along) draws it, clamped just inside its end.
                if (forward and s <= 0.3) or (not forward and s >= length - 0.3):
                    continue
                at = min(s, length - 0.3) if forward else max(s, 0.3)
                if (
                    forward
                    and s >= length - 0.05
                    and eflags & F["END_JUNCTION"]
                    and attrs["s1"] >= total - 0.05
                ):
                    at = length - attrs["setback1"] - 0.5
                if not forward and s <= 0.05 and eflags & F["START_JUNCTION"] and s0 <= 0.05:
                    at = attrs["setback0"] + 0.5
                for z in zebra_at:
                    if forward and z - 4.0 < at < z + CROSSING_CLEAR:
                        at = z - CROSSING_CLEAR
                    if not forward and z - CROSSING_CLEAR < at < z + 4.0:
                        at = z + CROSSING_CLEAR
                if not 0.3 <= at <= length - 0.3:
                    continue
                centre, tangent = _frame(points, cumulative, at)
                edge = half + KERB_REACH
                across = (
                    (o_div, edge) if forward and bwd else (-edge, o_div) if bwd else (-edge, edge)
                )
                along = (-STOP_WIDTH, 0.0) if forward else (0.0, STOP_WIDTH)
                key = (round(centre[0] * 2), round(centre[1] * 2), forward)
                stops.setdefault(key, (_rect(centre, tangent, along, across), deck))

        # Lines need a smooth paved carriageway of a marked class.
        if not eflags & F["SMOOTH"] or eflags & F["NO_MARKINGS"]:
            continue
        rural = attrs["kmh"] >= 60
        gap = RURAL_GAP if rural else URBAN_GAP
        width = RURAL_WIDTH if rural else LINE_WIDTH
        cuts = [(z - CROSSING_CLEAR, z + CROSSING_CLEAR) for z in zebra_at]
        if eflags & F["START_JUNCTION"]:
            cuts.append((-1.0, attrs["setback0"] - s0))
        if eflags & F["END_JUNCTION"]:
            cuts.append((total - attrs["setback1"] - s0, length + 1.0))
        marked = highway in CENTRE_CLASSES or (
            highway in MINOR_CLASSES and eflags & F["LANES_TAGGED"] and fwd + bwd >= 2
        )
        if marked and bwd and not eflags & F["SINGLE_TRACK"] and attrs["width"] >= 5.5:
            dashes = _subtract(_dashes(s0, length, DASH, gap), cuts)
            items.extend((p, WHITE, deck, "centre") for p in _line(line, dashes, o_div, width))
        if marked:
            lanes = [o_div + i * w for i in range(1, fwd)] + [o_div - j * w for j in range(1, bwd)]
            for offset in lanes:
                dashes = _subtract(_dashes(s0, length, DASH, RURAL_GAP), cuts)
                items.extend((p, WHITE, deck, "lane") for p in _line(line, dashes, offset, width))
        if rural or highway in ("motorway", "trunk"):
            solid = _subtract([(0.0, length)], cuts)
            for offset in (-half + EDGE_INSET, half - EDGE_INSET):
                items.extend(
                    (p, WHITE, deck, "edge") for p in _line(line, solid, offset, RURAL_WIDTH)
                )
    for bars, deck, _ in crossings.values():
        items.extend((bar, WHITE, deck, "crosswalk") for bar in bars)
    for poly, deck in stops.values():
        items.append((poly, WHITE, deck, "stop"))
    if len(explicit):
        clear = shapely.union_all(list(explicit)).buffer(EXPLICIT_CLEAR)
        kept = []
        for poly, rgb, deck, kind in items:
            if kind != "crosswalk" and poly.intersects(clear):
                poly = poly.difference(clear)
                if poly.is_empty or poly.area < 0.01:
                    continue
            kept.append((poly, rgb, deck, kind))
        items = kept
    return items


def summary(items) -> dict:
    """Per-kind counts and painted area (m2) for the tile manifest and the sanity report."""
    out = {}
    for poly, _, _, kind in items:
        row = out.setdefault(kind, {"polygons": 0, "area_m2": 0.0})
        row["polygons"] += 1
        row["area_m2"] = round(row["area_m2"] + poly.area, 2)
    return out
