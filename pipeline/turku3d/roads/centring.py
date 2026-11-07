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


def _cut(carriageway, x, z, nx, nz):
    """Signed offset (along the normal) of the middle of the carriageway piece under (x, z), or None."""
    section = shapely.LineString([(x - nx * HALF, z - nz * HALF), (x + nx * HALF, z + nz * HALF)])
    cut = section.intersection(carriageway)
    pieces = [p for p in getattr(cut, "geoms", [cut]) if p.geom_type == "LineString" and p.length > 0.5]
    if not pieces:
        return None
    point = shapely.Point(x, z)
    piece = min(pieces, key=lambda p: p.distance(point))
    if piece.distance(point) > REACH or piece.length > WIDE:
        return None
    middle = piece.interpolate(0.5, normalized=True)
    return float(np.clip((middle.x - x) * nx + (middle.y - z) * nz, -MAX_SHIFT, MAX_SHIFT))


def centre(graph: dict, carriageway, stats=None, streets=None) -> dict:
    """Move the interior vertices of every car edge (not bridge/tunnel) onto the carriageway's middle, in place.
    ``streets``: every surveyed street part (carriageway, pavements, verges, parking...); vertices off it stay."""
    if carriageway is None or carriageway.is_empty or graph.get("revision", 1) < 2:
        return graph
    shapely.prepare(carriageway)
    if streets is not None and not streets.is_empty:
        shapely.prepare(streets)
    else:
        streets = None
    car = graph["bits"]["car"]
    flags_col = graph["edgeAttrColumns"].index("flags")
    structure = sum(graph["bits"]["edge_flags"][name] for name in STRUCTURE)
    nodes = graph["nodes"]
    for k, edge in enumerate(graph["edges"]):
        if not edge[2] & car or graph["edgeAttrs"][k][flags_col] & structure:
            continue
        points = np.array([(nodes[2 * edge[0]], nodes[2 * edge[0] + 1]), *zip(edge[4::2], edge[5::2], strict=True),
                           (nodes[2 * edge[1]], nodes[2 * edge[1] + 1])], float)
        line = shapely.LineString(points)
        length = line.length
        if length < 2 * STEP:
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
        offsets = [_cut(carriageway, x, z, nx, nz) for (x, z), (nx, nz) in zip(xz, normal, strict=True)]
        known = np.array([o is not None for o in offsets])
        if streets is not None:
            known &= shapely.dwithin(streets, shapely.points(xz), OFF_STREET)
        if not known.any():
            continue
        shift = np.where(known, [o if o is not None else 0.0 for o in offsets], 0.0)
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
        anchors = np.r_[0.0, length, stations[~known]]
        taper = np.clip(np.abs(stations[:, None] - anchors[None, :]).min(axis=1) / TAPER, 0, 1)
        shift *= taper
        if np.abs(shift).max() < MIN_SHIFT:
            if stats is not None:
                stats["centred_edges"] += 1
            continue
        moved = xz + normal * shift[:, None]
        old = length
        new = shapely.LineString([points[0], *moved, points[-1]]).length
        edge[3] = round(new, 1)
        edge[4:] = [round(float(v), 2) for p in moved for v in p]
        events = graph["edgeEvents"][k]
        if events and old > 0:
            for i in range(0, len(events), 3):
                events[i] = round(min(events[i] * new / old, new), 2)
        if stats is not None:
            stats["moved_edges"] += 1
            stats["moved_vertices"] += int((np.abs(shift) >= MIN_SHIFT).sum())
            stats["max_shift_cm"] = max(stats.get("max_shift_cm", 0), int(round(np.abs(shift).max() * 100)))
    return graph


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
