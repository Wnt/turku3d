"""Car edges onto the middle of a surveyed carriageway (roads.json v2, before heights).

OSM centrelines in the Dutch city centres run up to 3 m off the carriageway the BGT surveys: along a canal street
the line often follows the street's axis between the facades, over the parking strip or the pavement. Lanes, the
autopilot, traffic and the markings all follow the graph, so cars rode kerbs and stopped against lamp posts on the
pavement (Amsterdam Damstraat). Each car edge's interior is resampled and every vertex moves across the edge to the
middle of the carriageway piece under it (or the nearest piece within REACH); pieces wider than WIDE (squares, merged
two-way roads with islands) and edges on bridges or in tunnels stay, and so do the end nodes (junctions, tile seams),
with the shift tapered in over TAPER from each end. A vertex off every surveyed street part (a garage ramp, a yard, a
quay) is not placed: a short gap between placed vertices is bridged, a longer run stays where OSM has it and the shift
tapers out towards it (the Rokin garage ramp's parking aisle had been pulled 3 m onto the ramp wall).

An edge without an OSM width tag also takes the surveyed carriageway's width (the median of its placed stations) when
that is narrower than the lane-count default: the lanes are laid out across the edge's width, and a two-way canal
street with the 6.5 m default put its lanes 1.6 m off the middle of a 3.5 m carriageway, over the quay edge and the
lamp posts on it (Amsterdam Grimburgwal).

A Finnish register carriageway includes its kerbside bays, so there only cross-sections over bays OSM maps place
vertices, and only where the lanes reach the bays (``kerbside_bays``, ``centre(bays=...)``); the shift drifts in at
BAY_SLOPE instead of the TAPER ramp.
"""

from __future__ import annotations

import numpy as np
import shapely

STEP = 5.0  # m between resampled interior vertices
HALF = 8.0  # m: half length of the cross-section cut through the carriageway
REACH = 3.0  # m: a carriageway piece farther than this from the line is another street
WIDE = 11.0  # m: a wider piece is a square or a divided road; its middle is no lane
MAX_SHIFT = 3.0  # m
TAPER = 8.0  # m from an end node over which the shift grows from 0 to full
MIN_SHIFT = 0.2  # m: smaller corrections are survey noise
GAP = 2 * STEP  # m: unplaced vertices between placed ones this close follow them; a longer run stays put
OFF_STREET = 0.5  # m: a vertex farther than this from every surveyed street part is not on the street
STRUCTURE = ("BRIDGE", "TUNNEL")
MIN_WIDTH = 2.5  # m: a narrower surveyed carriageway still carries one car lane of this width


def _cut(carriageway, x, z, nx, nz, bays=None, half_width=0.0):
    """(signed offset along the normal of the middle of the carriageway piece under (x, z), the piece's width), or
    None. With ``bays`` only a piece whose lanes (``half_width`` to each side of the line) run into kerbside bays is
    placed, on the middle of its part beside them."""
    section = shapely.LineString([(x - nx * HALF, z - nz * HALF), (x + nx * HALF, z + nz * HALF)])
    cut = section.intersection(carriageway)
    pieces = [p for p in getattr(cut, "geoms", [cut]) if p.geom_type == "LineString" and p.length > 0.5]
    if not pieces:
        return None
    point = shapely.Point(x, z)
    piece = min(pieces, key=lambda p: p.distance(point))
    if piece.distance(point) > REACH:
        return None
    if bays is not None:
        piece = _beside(piece, section, bays, point, half_width + BAY_CLEARANCE)
        if piece is None:
            return None
    if piece.length > WIDE:
        return None
    middle = piece.interpolate(0.5, normalized=True)
    return float(np.clip((middle.x - x) * nx + (middle.y - z) * nz, -MAX_SHIFT, MAX_SHIFT)), float(piece.length)


def _beside(piece, section, bays, point, reach):
    """The driving part of the carriageway cross-section ``piece``, its longest stretch off the kerbside ``bays``, when
    the lanes reach the bays: a bay-side end of the part is within ``reach`` of ``point`` on the line. None when the
    piece crosses less than BAY_DEPTH of bays, when the lanes clear them (a bay pocket beside a street whose line runs
    off-centre would put a kink in it), or when the piece runs to an end of the cut ``section`` (the far kerb is beyond
    the cut: a wide street, whose middle is no lane either)."""
    if piece.intersection(bays).length < BAY_DEPTH:
        return None
    if min(piece.distance(shapely.Point(c)) for c in section.coords) < 0.05:
        return None
    rest = piece.difference(bays)
    parts = [p for p in getattr(rest, "geoms", [rest]) if p.geom_type == "LineString" and p.length > 0.5]
    if not parts:
        return None
    part = max(parts, key=lambda p: p.length)
    ends = [shapely.Point(c) for c in (part.coords[0], part.coords[-1])]
    if not any(e.distance(bays) < 0.05 and e.distance(point) < reach for e in ends):
        return None
    return part


def _node_shift(graph, n, through, plans, carriageway, bays):
    """Offset of node ``n`` where the street of its two ``through`` car edges (edge, end) runs beside bays: the
    cross-section at the node itself, else the mean of the edges' placed stations within GAP of it. Limited to what
    the shorter edge can drift in at BAY_SLOPE."""
    columns = graph["edgeAttrColumns"]
    point = _node(graph, n)
    direction = np.zeros(2)
    for i, (k, end) in enumerate(through):
        normal = plans[k][2][0 if end == 0 else -1]
        tangent = np.array([normal[1], -normal[0]])  # along the edge, a -> b
        direction += tangent if (end == 1) == (i == 0) else -tangent  # into the node on the first, out on the second
    direction /= max(np.linalg.norm(direction), 1e-9)
    normal = np.array([-direction[1], direction[0]])
    half_width = np.mean([graph["edgeAttrs"][k][columns.index("width")] for k, _ in through]) / 2
    cut = _cut(carriageway, *point, *normal, bays, half_width)
    if cut is not None:
        move = normal * cut[0]
    else:
        near = []
        for k, end in through:
            stations, _, normals, shift, known, length = plans[k]
            gap = stations if end == 0 else length - stations
            i = int(np.argmin(np.where(known, gap, np.inf)))
            if not known[i] or gap[i] > GAP:
                return None
            near.append(normals[i] * shift[i])
        move = np.mean(near, axis=0)
    reach = BAY_SLOPE * min(plans[k][5] for k, _ in through)
    return move * min(1.0, reach / max(np.linalg.norm(move), 1e-9))


def _slope_limited(t, v, fixed, slope):
    """Shifts ``v`` at the sorted positions ``t`` changing by at most ``slope`` per metre, the ``fixed`` ones kept: each
    free one is clamped into reach of every fixed one, then of its neighbours in a forward and a backward pass. Bays
    come and go along a street and short edges sit between driveways; a TAPER ramp put 1.5 m kinks into 10 m stubs and
    jogs where the bays switch sides."""
    w = np.asarray(v, float).copy()
    pinned = np.flatnonzero(fixed)
    reach = slope * np.abs(t[:, None] - t[pinned][None, :])
    free = ~np.asarray(fixed, bool)
    w[free] = np.clip(w[free], (w[pinned][None, :] - reach).max(axis=1)[free], (w[pinned][None, :] + reach).min(axis=1)[free])
    for order, step in ((range(1, len(w)), -1), (range(len(w) - 2, -1, -1), 1)):
        for i in order:
            if free[i]:
                gap = slope * abs(t[i] - t[i + step])
                w[i] = min(max(w[i], w[i + step] - gap), w[i + step] + gap)
    return w


def _narrow(graph, k, widths, stats):
    """An untagged edge of one or two lanes wider than its surveyed carriageway (median of ``widths``) takes that
    width. A wider way can be one OSM line over two surveyed carriageways (a tram reservation between them): the piece
    under it is then only one of them."""
    columns = graph["edgeAttrColumns"]
    row = graph["edgeAttrs"][k]
    if "width" not in columns or row[columns.index("flags")] & graph["bits"]["edge_flags"]["WIDTH_TAGGED"]:
        return
    if row[columns.index("lanes_fwd")] + row[columns.index("lanes_bwd")] > 2:
        return
    surveyed = round(max(float(np.median(widths)), MIN_WIDTH), 1)
    if surveyed < row[columns.index("width")]:
        row[columns.index("width")] = surveyed
        if stats is not None:
            stats["narrowed_edges"] += 1


def centre(graph: dict, carriageway, stats=None, streets=None, bays=None) -> dict:
    """Move the interior vertices of every car edge (not bridge/tunnel) onto the carriageway's middle, in place.
    ``streets``: every surveyed street part (carriageway, pavements, verges, parking...); vertices off it stay.
    ``bays``: kerbside bays inside the carriageway (``kerbside_bays``); when given, only cross-sections over them place
    vertices, on the middle of the part beside them, and only edges of up to BESIDE_BAYS_LANES lanes move."""
    if carriageway is None or carriageway.is_empty or graph.get("revision", 1) < 2:
        return graph
    if bays is not None and bays.is_empty:
        return graph
    shapely.prepare(carriageway)
    if bays is not None:
        shapely.prepare(bays)
    if streets is not None and not streets.is_empty:
        shapely.prepare(streets)
    else:
        streets = None
    car = graph["bits"]["car"]
    flags_col = graph["edgeAttrColumns"].index("flags")
    structure = sum(graph["bits"]["edge_flags"][name] for name in STRUCTURE)
    nodes = graph["nodes"]
    columns = graph["edgeAttrColumns"]
    lanes = [columns.index("lanes_fwd"), columns.index("lanes_bwd")] if bays is not None and "lanes_fwd" in columns else None
    width = columns.index("width") if "width" in columns else None
    plans = {}
    for k, edge in enumerate(graph["edges"]):
        if not edge[2] & car or graph["edgeAttrs"][k][flags_col] & structure:
            continue
        if lanes and sum(graph["edgeAttrs"][k][i] for i in lanes) > BESIDE_BAYS_LANES:
            continue
        points = np.array([(nodes[2 * edge[0]], nodes[2 * edge[0] + 1]), *zip(edge[4::2], edge[5::2], strict=True),
                           (nodes[2 * edge[1]], nodes[2 * edge[1] + 1])], float)
        line = shapely.LineString(points)
        length = line.length
        if length < (STEP if bays is not None else 2 * STEP):  # beside bays a 9 m piece between driveways moves too
            continue
        stations = np.unique(np.r_[np.arange(STEP, length - STEP / 2, STEP),
                                   shapely.line_locate_point(line, shapely.points(points[1:-1]))])
        stations = stations[(stations > 0.5) & (stations < length - 0.5)]
        if not len(stations):
            continue
        xz = shapely.get_coordinates(shapely.line_interpolate_point(line, stations))
        ahead = shapely.get_coordinates(shapely.line_interpolate_point(line, np.minimum(stations + 0.5, length)))
        behind = shapely.get_coordinates(shapely.line_interpolate_point(line, np.maximum(stations - 0.5, 0)))
        tangent = ahead - behind
        tangent /= np.maximum(np.linalg.norm(tangent, axis=1), 1e-9)[:, None]
        normal = np.c_[-tangent[:, 1], tangent[:, 0]]
        half_width = graph["edgeAttrs"][k][width] / 2 if width is not None else 0.0
        cuts = [_cut(carriageway, x, z, nx, nz, bays, half_width) for (x, z), (nx, nz) in zip(xz, normal, strict=True)]
        known = np.array([c is not None for c in cuts])
        if streets is not None:
            known &= shapely.dwithin(streets, shapely.points(xz), OFF_STREET)
        if not known.any() and bays is None:
            continue
        if known.any():
            _narrow(graph, k, [cuts[i][1] for i in np.flatnonzero(known)], stats)
        shift = np.where(known, [c[0] if c is not None else 0.0 for c in cuts], 0.0)
        # An unplaced vertex between placed ones within GAP follows them; a longer unplaced run stays and is an
        # anchor like the end nodes. A lone outlier is smoothed away.
        placed = np.flatnonzero(known)
        for a, b in zip(placed[:-1], placed[1:], strict=True):
            if b > a + 1 and stations[b] - stations[a] <= GAP:
                inside = slice(a + 1, b)
                shift[inside] = np.interp(stations[inside], stations[[a, b]], shift[[a, b]])
                known[inside] = True
        if len(shift) >= 3:
            padded = np.r_[shift[0], shift, shift[-1]]
            shift = np.median(np.c_[padded[:-2], padded[1:-1], padded[2:]], axis=1)
        if bays is not None:  # also unplaced edges: a driveway node at their end may move
            plans[k] = (stations, xz, normal, np.where(known, shift, 0.0), known, length)
            continue
        anchors = np.r_[0.0, length, stations[~known]]
        shift *= np.clip(np.abs(stations[:, None] - anchors[None, :]).min(axis=1) / TAPER, 0, 1)
        _shift_edge(graph, k, points[0], points[-1], xz, normal, shift, stats)
    if bays is not None:
        _move_beside_bays(graph, plans, stats, carriageway, bays)
    return graph


def _shift_edge(graph, k, start, end, xz, normal, shift, stats):
    """Edge ``k``'s interior onto ``xz + normal * shift`` between its (possibly moved) end points; its length and event
    positions follow."""
    if np.abs(shift).max() < MIN_SHIFT and stats is not None:
        stats["centred_edges"] += 1
    if np.abs(shift).max() < MIN_SHIFT and np.allclose(start, _node(graph, graph["edges"][k][0])) and np.allclose(
        end, _node(graph, graph["edges"][k][1])
    ):
        return
    edge = graph["edges"][k]
    moved = xz + normal * shift[:, None]
    old = _length(graph, edge)
    new = shapely.LineString([start, *moved, end]).length
    edge[3] = round(new, 1)
    edge[4:] = [round(float(v), 2) for p in moved for v in p]
    _rescale_events(graph, k, old, new)
    if stats is not None and np.abs(shift).max() >= MIN_SHIFT:
        stats["moved_edges"] += 1
        stats["moved_vertices"] += int((np.abs(shift) >= MIN_SHIFT).sum())
        stats["max_shift_cm"] = max(stats.get("max_shift_cm", 0), int(round(np.abs(shift).max() * 100)))


def _node(graph, n):
    return np.array([graph["nodes"][2 * n], graph["nodes"][2 * n + 1]], float)


def _length(graph, edge, moves=None):
    """Polyline length of ``edge`` (its nodes moved by ``moves``, node -> offset, when given)."""
    moves = moves or {}
    ends = [_node(graph, n) + moves.get(n, 0.0) for n in edge[:2]]
    return shapely.LineString([ends[0], *zip(edge[4::2], edge[5::2], strict=True), ends[1]]).length


def _rescale_events(graph, k, old, new):
    events = graph["edgeEvents"][k]
    if events and old > 0:
        for i in range(0, len(events), 3):
            events[i] = round(min(events[i] * new / old, new), 2)


def _move_beside_bays(graph, plans, stats, carriageway, bays):
    """Apply the bay-side shifts of ``plans`` (edge -> stations, xz, normal, shift, placed, length). A node where a
    street runs on through driveways (two car edges that are no service ways, every other car edge one, not on a tile
    seam) moves with the lane by the mean of its two nearest placed stations: Turku Kristiinankatu's line is cut into
    9-13 m pieces by driveways, and pinned there it could not leave the parked cars' noses. Junctions with other
    streets and seam nodes (shared with the next tile) stay. Each edge's shift then drifts at BAY_SLOPE between its end
    nodes' shifts and its unplaced stations (0)."""
    edges = graph["edges"]
    columns = graph["edgeAttrColumns"]
    car = graph["bits"]["car"]
    highways = graph["bits"].get("highways", [])
    seam = graph["bits"].get("node_flags", {}).get("SEAM")
    node_flags = graph.get("nodeFlags")
    movable = "service" in highways and "highway" in columns and seam and node_flags is not None
    at: dict[int, list] = {}
    for k, edge in enumerate(edges if movable else ()):
        if edge[2] & car:
            at.setdefault(edge[0], []).append((k, 0))
            at.setdefault(edge[1], []).append((k, 1))
    moves = {}
    for n, ends in at.items():
        if node_flags[n] & seam:
            continue
        through = [(k, end) for k, end in ends if graph["edgeAttrs"][k][columns.index("highway")] != highways.index("service")]
        if len(through) != 2 or through[0][0] == through[1][0] or any(k not in plans for k, _ in through):
            continue
        move = _node_shift(graph, n, through, plans, carriageway, bays)
        if move is not None and np.linalg.norm(move) >= MIN_SHIFT:
            moves[n] = move
    for k, (stations, xz, normal, shift, known, length) in plans.items():
        a, b = edges[k][0], edges[k][1]
        if not known.any() and a not in moves and b not in moves:
            continue
        ends = [float(moves[a] @ normal[0]) if a in moves else 0.0, float(moves[b] @ normal[-1]) if b in moves else 0.0]
        # Unplaced stations stay on the OSM line, except within drift reach of a moved end: the lane returns there.
        fixed = ~known & (stations >= abs(ends[0]) / BAY_SLOPE) & (length - stations >= abs(ends[1]) / BAY_SLOPE)
        t = np.r_[0.0, stations, length]
        limited = _slope_limited(t, np.r_[ends[0], shift, ends[1]], np.r_[True, fixed, True], BAY_SLOPE)[1:-1]
        _shift_edge(graph, k, _node(graph, a) + moves.get(a, 0.0), _node(graph, b) + moves.get(b, 0.0), xz, normal,
                    limited, stats)
    for k, edge in enumerate(edges):
        if k not in plans and (edge[0] in moves or edge[1] in moves):
            old, new = _length(graph, edge), _length(graph, edge, moves)
            edge[3] = round(new, 1)
            _rescale_events(graph, k, old, new)
    for n, move in moves.items():
        graph["nodes"][2 * n] = round(graph["nodes"][2 * n] + float(move[0]), 2)
        graph["nodes"][2 * n + 1] = round(graph["nodes"][2 * n + 1] + float(move[1]), 2)
    if stats is not None and moves:
        stats["moved_nodes"] += len(moves)


def carriageway_of(rows) -> shapely.Geometry:
    """Union of the drivable carriageway (Katuosalaji Ajorata) of ``(geometry, Katuosalaji)`` rows."""
    parts = [shapely.make_valid(shapely.force_2d(g)) for g, kind in rows if kind == "Ajorata" and g is not None]
    polygons = [p for g in parts for p in getattr(g, "geoms", [g]) if p.geom_type in ("Polygon", "MultiPolygon")]
    return shapely.union_all(polygons) if polygons else shapely.Polygon()


def streets_of(rows) -> shapely.Geometry:
    """Union of every surveyed street part of ``(geometry, Katuosalaji)`` rows."""
    parts = [shapely.make_valid(shapely.force_2d(g)) for g, _ in rows if g is not None]
    polygons = [p for g in parts for p in getattr(g, "geoms", [g]) if p.geom_type in ("Polygon", "MultiPolygon")]
    return shapely.union_all(polygons) if polygons else shapely.Polygon()


BAYS_INSIDE = 0.5  # share of a parking area's surface inside the surveyed carriageway that makes it kerbside bays
BAY_DEPTH = 1.0  # m: a cross-section over less of the bays than this is not beside them
BAY_CLEARANCE = 0.5  # m: lanes whose outer edge stays this far off the bays keep their line
BAY_SLOPE = 0.15  # m of shift per m along the edge beside bays (a gentle lane drift, not a swerve)
BESIDE_BAYS_LANES = 2  # beside bays only streets of one or two lanes move; a wider one may run on either side of them


def kerbside_bays(carriageway, parking) -> shapely.Geometry:
    """The mapped kerbside bays of a Finnish register carriageway, else empty.

    The Finnish street-area registers (GIS:Katualueet, Katuosalaji Ajorata) survey kerbside parking bays as part of
    the carriageway and have no parking kind; OSM maps the bays as parking areas inside it. Where it does, the lanes
    belong between the bays and the far kerb: Turku Kristiinankatu's 11.7 m Ajorata holds 5 m of perpendicular bays,
    the OSM line runs 1.5 m off the middle of the 6.2 m left to drive, and its northbound lane ran the length of the
    parked cars' noses. Pass them to ``centre(bays=...)``: only cross-sections over them move lanes, so every other
    street keeps its OSM line (a register carriageway elsewhere can hold bays OSM does not map), and one whose part
    beside the bays is wider than WIDE or than the cut stays too (a 16 m Turku street with bays on alternate
    sides had its lanes weave 3 m from side to side).
    ``parking``: parking-area polygons; those with at least BAYS_INSIDE of their surface inside the carriageway are
    bays."""
    if carriageway is None or carriageway.is_empty or not parking:
        return shapely.Polygon()
    bays = [p for p in parking if p.area > 0 and p.intersection(carriageway).area >= BAYS_INSIDE * p.area]
    return shapely.union_all(bays) if bays else shapely.Polygon()


def parking_areas(path, bbox) -> list:
    """Parking-area polygons at grade (``amenity=parking``, not underground, multi-storey or rooftop) of an OSM
    parquet around ``bbox``."""
    import json

    from turku3d.spatial import frame

    f = frame(path, bbox)
    if "amenity" not in f or "geometry_local" not in f:
        return []
    out = []
    for g, amenity, tags in zip(f.geometry_local, f.amenity, f.tags, strict=True):
        if amenity != "parking" or g is None:
            continue
        t = json.loads(tags) if isinstance(tags, str) and tags else {}
        if t.get("parking") in ("underground", "multi-storey", "rooftop") or t.get("location") == "underground":
            continue
        g = shapely.make_valid(shapely.force_2d(g))
        out.extend(p for p in getattr(g, "geoms", [g]) if p.geom_type == "Polygon")
    return out


def load(path, bbox) -> tuple[shapely.Geometry, shapely.Geometry]:
    """(carriageway, all street parts) of a GIS:Katualueet parquet around ``bbox`` (whole polygons)."""
    from turku3d.spatial import frame

    if path is None:
        return shapely.Polygon(), shapely.Polygon()
    f = frame(path, bbox)
    if "Katuosalaji" not in f or "geometry_local" not in f:
        return shapely.Polygon(), shapely.Polygon()
    rows = list(zip(f.geometry_local, f.Katuosalaji, strict=True))
    return carriageway_of(rows), streets_of(rows)
