"""Traffic-signal posts at the kerb, never on the carriageway.

The street graph (``roads.json``) carries each signal as a stop event at its OSM node, which is
mapped on the way's centreline. The client used to stand each head ``width / 2 + SIDE`` right of
that point (``game/src/traffic/signals.ts``). The OSM width is an estimate, narrower than most
surveyed Turku streets, so the heads stood 2-4 m inside the carriageway (Jonni's edit session
20261103-224901-jozz9f moved all four heads of Aurakatu x Läntinen Rantakatu to the kerb).

``place`` keeps that nominal head and moves its post to the nearest surveyed edge of the
carriageway (GIS:Katualueet ``CARRIAGEWAY`` parts), ``KERB_CLEARANCE`` past it: onto the
sidewalk, verge or median the edge bounds, the way posts stand at a stop line. The head keeps
its facing (against the approach's travel) and its key, so phases and the editor still match it.

* A nominal head already off the carriageway stays (``off_carriageway``).
* A head deeper than ``REACH`` inside stays and is counted (``deep``: a wide square or
  junction box with no surveyed edge near).
* Without surveyed street parts near the tile, heads stay (``no_survey``).

The rows go into ``roads.json`` as ``signalPosts``: ``[edge, event, dir, x, z, y]`` per head
(``event`` indexes the edge's events, ``dir`` is +1 forward / -1 backward, ``y`` the ground under
the post or None). ``migrations/signals.py`` (``signals@1``) writes them; the chain runs at the
end of every generator build, so a regeneration gives the same rows.
"""

from __future__ import annotations

import math
from collections import Counter

import shapely
import shapely.ops

CARRIAGEWAY = ("Ajorata", "Pyöräkaista")  # Katuosalaji parts no post stands on
# Medians and traffic islands: the register often draws the carriageway across them, but their posts
# (keep-right signs, crossing signs, signal posts) stand on them, never in the lanes beside them.
ISLAND = "Välikaista"
SIDE = 0.35  # m beyond the half width: the client's nominal head offset (signals.ts)
KERB_CLEARANCE = 0.35  # m past the carriageway edge (props.KERB_CLEARANCE)
REACH = 8.0  # m: a head deeper inside the carriageway has no kerb of its own near
SIGNAL = 1  # roads.json event kind
FORWARD, BACKWARD = 16, 32  # roads.json event bits


def _polygons(geometries) -> list:
    parts = [shapely.make_valid(shapely.force_2d(g)) for g in geometries if g is not None and not g.is_empty]
    return [p for g in parts for p in getattr(g, "geoms", [g]) if p.geom_type in ("Polygon", "MultiPolygon")]


def carriageway(rows) -> shapely.Geometry:
    """Union of the CARRIAGEWAY parts of ``(geometry, Katuosalaji)`` rows (repaired first), less the medians and
    islands (``ISLAND``) the register overlaps with them."""
    rows = list(rows)
    polygons = _polygons(g for g, kind in rows if kind in CARRIAGEWAY)
    if not polygons:
        return shapely.Polygon()
    road = shapely.union_all(polygons)
    islands = _polygons(g for g, kind in rows if kind == ISLAND)
    return road.difference(shapely.union_all(islands)) if islands else road


def load_carriageway(path, bbox) -> shapely.Geometry:
    """The carriageway of a GIS:Katualueet parquet near ``bbox`` (whole polygons, never clipped)."""
    from turku3d.spatial import frame

    if path is None:
        return shapely.Polygon()
    f = frame(path, bbox)
    if "Katuosalaji" not in f or "geometry_local" not in f:
        return shapely.Polygon()
    return carriageway(zip(f.geometry_local, f.Katuosalaji, strict=True))


def _point_at(points, cumulative, s):
    """``signals.ts`` pointAt: position and unit heading at arc length ``s``."""
    lo, hi = 1, len(points) - 1
    while lo < hi:
        mid = (lo + hi) >> 1
        if cumulative[mid] < s:
            lo = mid + 1
        else:
            hi = mid
    i = max(1, lo)
    span = cumulative[i] - cumulative[i - 1]
    f = min(1.0, max(0.0, (s - cumulative[i - 1]) / span)) if span > 1e-9 else 0.0
    (x0, z0), (x1, z1) = points[i - 1], points[i]
    d = math.hypot(x1 - x0, z1 - z0) or 1.0
    return x0 + (x1 - x0) * f, z0 + (z1 - z0) * f, (x1 - x0) / d, (z1 - z0) / d


def heads(graph: dict):
    """Every drawn signal head of a v2 graph as the client places it nominally:
    ``(edge, event, dir, x, z, hx, hz)`` with (hx, hz) the approach's travel direction."""
    car = graph["bits"]["car"]
    nodes, columns = graph["nodes"], {n: i for i, n in enumerate(graph.get("edgeAttrColumns", ()))}
    heights = graph.get("edgeHeights") or [None] * len(graph["edges"])
    for k, edge in enumerate(graph["edges"]):
        flat = (graph.get("edgeEvents") or [None] * len(graph["edges"]))[k]
        if not flat or not edge[2] & car or heights[k] is None:
            continue
        a, b = edge[0], edge[1]
        points = [(nodes[2 * a], nodes[2 * a + 1]), *zip(edge[4::2], edge[5::2], strict=True),
                  (nodes[2 * b], nodes[2 * b + 1])]
        cumulative = [0.0]
        for p, q in zip(points, points[1:], strict=False):
            cumulative.append(cumulative[-1] + math.hypot(q[0] - p[0], q[1] - p[1]))
        attrs = graph["edgeAttrs"][k]
        width = attrs[columns["width"]] if "width" in columns else 3
        lanes = {1: attrs[columns["lanes_fwd"]], -1: attrs[columns["lanes_bwd"]]}
        for n in range(0, len(flat) - 2, 3):
            s, code = flat[n], flat[n + 1]
            if code & 15 != SIGNAL:
                continue
            x, z, hx, hz = _point_at(points, cumulative, s)
            for direction in (1, -1):
                if not code & (FORWARD if direction > 0 else BACKWARD) or not lanes[direction]:
                    continue
                dx, dz, side = hx * direction, hz * direction, width / 2 + SIDE
                yield k, n // 3, direction, x - dz * side, z + dx * side, dx, dz


def kerb_post(x, z, road: shapely.Geometry, boundary=None, reach=REACH) -> tuple[float, float, str]:
    """Where a post at (x, z) stands: just past the nearest carriageway edge (at most ``reach``
    inside), or where it is. Traffic-sign posts use the same rule (``props.signposts``)."""
    if road.is_empty:
        return x, z, "no_survey"
    point = shapely.Point(x, z)
    if not road.covers(point):
        return x, z, "off_carriageway"
    boundary = road.boundary if boundary is None else boundary
    edge = shapely.ops.nearest_points(boundary, point)[0]
    depth = edge.distance(point)
    if depth > reach:
        return x, z, "deep"
    ux, uz = edge.x - x, edge.y - z
    length = math.hypot(ux, uz) or 1.0
    ux, uz = ux / length, uz / length
    # A median narrower than twice the clearance: the post stands in its middle.
    for clearance in (KERB_CLEARANCE, KERB_CLEARANCE / 2, KERB_CLEARANCE / 4, 0.02):
        px, pz = edge.x + ux * clearance, edge.y + uz * clearance
        if not road.covers(shapely.Point(px, pz)):
            return px, pz, "kerb"
    return edge.x, edge.y, "kerb"


def place(graph: dict, road: shapely.Geometry, ground=None) -> tuple[list, Counter]:
    """``signalPosts`` rows of a graph and per-rule counts. ``ground(x, z)`` gives the post's y."""
    rows, stats = [], Counter()
    boundary = None if road.is_empty else road.boundary
    for edge, event, direction, x, z, _, _ in heads(graph):
        px, pz, rule = kerb_post(x, z, road, boundary)
        stats[f"signal_{rule}"] += 1
        y = ground(px, pz) if ground is not None else None
        rows.append([edge, event, direction, round(px, 2), round(pz, 2), None if y is None else round(y, 3)])
    return rows, stats
