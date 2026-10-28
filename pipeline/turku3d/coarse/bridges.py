"""Fast OSM bridge slabs, unobstructed underpasses and lidar-ground approaches."""

from __future__ import annotations

import json
from dataclasses import dataclass

import numpy as np
import shapely
from scipy.spatial import cKDTree

from turku3d.col.materials import mat_id
from turku3d.layers import write_visual
from turku3d.package.gltf import GlbMesh
from turku3d.structures.geometry import Faces
from turku3d.structures.layer import tile_cells

from . import bridge_details as details
from .geometry import clip_mesh

PATHS = {"footway", "cycleway", "path", "steps", "pedestrian", "bridleway"}
RAILS = {"rail", "tram", "light_rail", "narrow_gauge"}
THICKNESS = 0.4


def number(value, default):
    try:
        return float(str(value).split()[0])
    except (ValueError, IndexError):
        return default


def way_width(tags):
    if tags.get("railway") in RAILS:
        default = 4.5
    elif tags.get("highway") in PATHS:
        default = 3.0
    else:
        default = number(tags.get("lanes"), 2) * 3.25 + 1.0
    return float(np.clip(number(tags.get("width"), default), 1.0, 40.0))


def clearance(tags):
    """Clear space below the slab, metres; railway loading gauge wins over road/path."""
    if tags.get("railway") in RAILS:
        return 6.0
    return 2.5 if tags.get("highway") in PATHS else 4.5


def footprint(geom):
    """OSM emits closed ways as lines as well as multipolygon area records."""
    if geom.geom_type == "LineString" and geom.is_ring:
        geom = shapely.Polygon(geom)
    if geom.geom_type in ("Polygon", "MultiPolygon"):
        return shapely.make_valid(geom)
    return shapely.GeometryCollection()


def parallel(a, b):
    """Require substantial longitudinal overlap, not merely nearby bridge endpoints."""
    pa, pb = np.asarray(a["line"].coords), np.asarray(b["line"].coords)
    da, db = pa[-1] - pa[0], pb[-1] - pb[0]
    if abs(da @ db) < 0.97 * np.linalg.norm(da) * np.linalg.norm(db):
        return False
    if a["layer"] != b["layer"]:
        return False
    corridor = a["line"].buffer((a["width"] + b["width"]) / 2 + 2, cap_style="flat")
    return b["line"].intersection(corridor).length > 0.7 * b["line"].length


def merge_crossings(ways, outlines):
    """One physical crossing: authoritative outline first, parallel ways otherwise."""
    remaining = {w["id"]: w for w in ways}
    result = []
    for key, poly, tags in sorted(outlines, key=lambda item: item[0]):
        members = [
            w
            for w in remaining.values()
            if w["layer"] == number(tags.get("layer"), 1)
            and poly.buffer(0.5).intersection(w["line"]).length > 0.8 * w["line"].length
        ]
        if not members:
            continue
        representative = max(members, key=lambda w: w["width"])
        coords = np.asarray(representative["line"].coords)
        direction = coords[-1] - coords[0]
        direction /= np.linalg.norm(direction)
        normal = np.array([-direction[1], direction[0]])
        sections = []
        for fraction in (0.25, 0.5, 0.75):
            point = representative["line"].interpolate(fraction, normalized=True)
            c = np.asarray(point.coords[0])
            across = poly.intersection(shapely.LineString([c - normal * 1000, c + normal * 1000]))
            sections.extend(
                part
                for part in shapely.get_parts(across)
                if part.geom_type == "LineString" and part.distance(point) < 0.5
            )
        section = min(sections, key=lambda part: part.length)
        centre = np.asarray(section.interpolate(0.5, normalized=True).coords[0])
        along = poly.intersection(
            shapely.LineString([centre - direction * 1000, centre + direction * 1000])
        )
        axis = max(
            (part for part in shapely.get_parts(along) if part.geom_type == "LineString"),
            key=lambda part: part.length,
        )
        result.append(
            {
                **representative,
                "id": key,
                "line": axis,
                "width": section.length,
                "outline": poly,
                "tags": {**representative["tags"], **tags},
                "members": [w["id"] for w in members],
                "member_ways": members,
            }
        )
        for w in members:
            del remaining[w["id"]]
    while remaining:
        key = min(remaining)
        first = remaining.pop(key)
        members = [first, *[w for w in remaining.values() if parallel(first, w)]]
        for w in members[1:]:
            del remaining[w["id"]]
        if len(members) == 1:
            result.append({**first, "members": [key]})
            continue
        # Derive a common envelope from all carriageways, never one stacked slab per way.
        poly = shapely.union_all(
            [w["line"].buffer(w["width"] / 2, cap_style="flat") for w in members]
        ).convex_hull
        merged = merge_crossings(
            members, [(key, poly, {**first["tags"], "layer": str(first["layer"])})]
        )
        result.extend(merged)
    return result


def passes_under(bridge, way):
    """Only actual transverse interior crossings need headroom; approaches do not."""
    if way["id"] in bridge.get("members", [bridge["id"]]):
        return False
    if way["layer"] > bridge["layer"] or (way["bridge"] and way["layer"] >= bridge["layer"]):
        return False
    a, b = bridge["line"], way["line"]
    cross = a.intersection(b)
    if cross.is_empty or cross.geom_type not in ("Point", "MultiPoint"):
        return False
    for p in shapely.get_parts(cross):
        if a.boundary.distance(p) < 0.5 or b.boundary.distance(p) < 0.5:
            continue

        def tangent(line, p=p):
            t = line.project(p)
            return np.asarray(line.interpolate(min(line.length, t + 1)).coords[0]) - np.asarray(
                line.interpolate(max(0, t - 1)).coords[0]
            )

        da, db = tangent(a), tangent(b)
        if abs(da @ db) < 0.85 * np.linalg.norm(da) * np.linalg.norm(db):
            return True
    return False


def prepare(tile, osm, survey_paths=(), spot_paths=()):
    """Resolve physical crossings with full source ways before selecting this tile."""
    import pyarrow.parquet as pq

    names = pq.read_schema(osm).names
    table = pq.read_table(
        osm, columns=[c for c in ("osm_id", "tags", "geometry_local") if c in names]
    )
    window = shapely.box(*tile.bbox.buffered(500).as_tuple())
    ways, outlines, obstacles, piers, lamps = [], {}, [], [], []
    for row_index, row in enumerate(table.to_pylist()):
        tags = json.loads(row["tags"] or "{}")
        if not (
            tags.get("highway")
            or tags.get("railway") in RAILS
            or tags.get("building")
            or tags.get("man_made") in ("bridge", "pier")
        ):
            continue
        line = shapely.force_2d(shapely.from_wkb(row["geometry_local"]))
        if not line.intersects(window):
            continue
        key = str(row.get("osm_id", row_index))
        if tags.get("highway") == "street_lamp" and line.geom_type == "Point":
            lamps.append((list(line.coords[0]), tags))
        poly = footprint(line)
        if tags.get("building") not in (None, "no") and not poly.is_empty:
            obstacles.append(poly)
        if tags.get("man_made") == "pier":
            piers.append(poly if not poly.is_empty else line)
        if tags.get("man_made") == "bridge" and not poly.is_empty:
            outlines[key] = (key, poly, tags)
        if not (tags.get("highway") or tags.get("railway") in RAILS):
            continue
        if line.geom_type != "LineString" or line.length < 0.5 or line.is_ring:
            continue
        bridge = tags.get("bridge", "no") not in ("no", "false", "0", "")
        ways.append(
            dict(
                id=key,
                tags=tags,
                line=line,
                bridge=bridge,
                layer=number(tags.get("layer"), 1 if bridge else 0),
                width=way_width(tags),
            )
        )
    from .bridge_survey import height_anchors, spot_heights

    spots = spot_heights(spot_paths)
    bridges = merge_crossings([w for w in ways if w["bridge"]], list(outlines.values()))
    road_tree = shapely.STRtree([w["line"] for w in ways])
    obstacle = shapely.union_all(obstacles)
    pier = shapely.union_all(piers)
    result = []
    for b in bridges:
        if not b["line"].buffer(b["width"] / 2 + 5).intersects(shapely.box(*tile.bbox.as_tuple())):
            continue
        b["landing"] = (b["line"].length < 25 and b["line"].distance(pier) < 2) or (
            b["line"].length < 8 and b["tags"].get("highway") in PATHS
        )
        b["obstacles"] = obstacle
        b["under"] = (
            []
            if b["landing"]
            else [
                ways[i]
                for i in sorted(road_tree.query(b["line"], predicate="intersects"))
                if passes_under(b, ways[i])
            ]
        )
        members = b.get("member_ways", [b])
        endpoints = shapely.union_all([member["line"].boundary for member in members])
        rail = b["tags"].get("railway") in RAILS
        foot = b["tags"].get("highway") in PATHS
        b["approaches"] = [
            w
            for i in sorted(road_tree.query(endpoints.buffer(0.05), predicate="intersects"))
            for w in [ways[i]]
            if not w["bridge"]
            and (w["tags"].get("railway") in RAILS) == rail
            and (w["tags"].get("highway") in PATHS) == foot
            and w["line"].boundary.distance(endpoints) < 0.05
        ]
        extended_way = {**b, "line": extended(b["line"], 30)}
        under_ids = {w["id"] for w in b["under"]}
        b["approach_under"] = [
            ways[i]
            for i in sorted(road_tree.query(extended_way["line"], predicate="intersects"))
            if ways[i]["id"] not in under_ids and passes_under(extended_way, ways[i])
        ]
        from .bridge_survey import survey

        b["lamps"] = [
            (p, tags)
            for p, tags in lamps
            if b.get("outline", b["line"].buffer(b["width"] / 2))
            .buffer(0.5)
            .covers(shapely.Point(p))
        ]
        b["survey"] = survey(b, survey_paths)
        if "heights" not in b["survey"]:
            b["survey"]["height_anchors"] = height_anchors(b, spots)
        result.append(b)
    return result


def under_height(bridge, under, point, sample):
    """Interpolate the lower way from outside the deck, which may be in the ground dataset.

    Sampling at the crossing can return the upper deck itself (Hämeensilta's railway),
    falsely asking for another loading gauge above the existing bridge.
    """
    line = under["line"]
    station = line.project(point)
    deck = bridge.get("outline", bridge["line"].buffer(bridge["width"] / 2)).buffer(4)
    anchors = []
    for sign in (-1, 1):
        for distance in range(2, 102, 2):
            t = float(np.clip(station + sign * distance, 0, line.length))
            p = line.interpolate(t)
            if not deck.covers(p):
                anchors.append((t, sample(p.x, p.y)))
                break
            if t in (0, line.length):
                break
    if not anchors:
        return sample(point.x, point.y)
    anchors.sort()
    return float(np.interp(station, [a[0] for a in anchors], [a[1] for a in anchors]))


def deck_profile(stations, ground, ends, requirements=(), thickness=THICKNESS):
    """Abutment line raised uniformly to clear terrain and (station, ground, clearance) crossings.

    A uniform offset keeps the deck straight even when the lower way is above either abutment.
    ``clearance`` is measured to the underside, not to the driving surface.
    """
    stations, ground = np.asarray(stations), np.asarray(ground)
    line = np.interp(stations, [stations[0], stations[-1]], ends)
    lift = max(0.0, float(np.max(ground - line)))
    for station, height, gap in requirements:
        base = float(np.interp(station, [stations[0], stations[-1]], ends))
        lift = max(lift, height + gap + thickness - base)
    return line + lift


def samples(line, step=4.0):
    lengths = np.linalg.norm(np.diff(np.asarray(line.coords), axis=0), axis=1)
    s = np.unique(
        np.r_[
            0,
            np.cumsum(lengths),
            np.linspace(0, line.length, max(2, int(np.ceil(line.length / step)) + 1)),
        ]
    )
    return s, np.array([line.interpolate(float(t)).coords[0] for t in s])


def extended(line, distance):
    coords = np.asarray(line.coords)
    a = (coords[1] - coords[0]) / np.linalg.norm(coords[1] - coords[0])
    b = (coords[-1] - coords[-2]) / np.linalg.norm(coords[-1] - coords[-2])
    start, end = np.broadcast_to(distance, (2,))
    return shapely.LineString(
        [
            *([coords[0] - a * start] if start else []),
            *coords,
            *([coords[-1] + b * end] if end else []),
        ]
    )


def approach_lengths(way, ends, sample, obstacles):
    """Find a gentle ground join within a mapped continuation road."""
    axis = way["line"]
    if way.get("landing"):
        return 0.0, 0.0
    minimum = min(3.0, axis.length * 0.1)
    approaches = way.get("approaches", [])
    corridor = shapely.union_all([w["line"].buffer(w["width"] / 2 + 2) for w in approaches])
    # A divided carriageway's outline axis lies between its mapped continuations.
    # Fill that narrow median, rather than treating it as an unmapped bank.
    if len(approaches) > 1:
        corridor = corridor.buffer(way["width"] / 4).buffer(-way["width"] / 4)
    coords = np.asarray(axis.coords)
    result = []
    for endpoint, other, height in (
        (coords[0], coords[1], ends[0]),
        (coords[-1], coords[-2], ends[-1]),
    ):
        direction = (endpoint - other) / np.linalg.norm(endpoint - other)
        length = minimum
        for distance in np.r_[minimum, np.arange(4, 32, 2)]:
            point = endpoint + direction * distance
            p = shapely.Point(point)
            if not corridor.covers(p) or obstacles.covers(p):
                break
            ahead = point + direction * 2
            if (
                abs(height - sample(*point)) <= distance * 0.08
                and abs(sample(*ahead) - sample(*point)) <= 0.2
            ):
                length = float(distance)
                break
        result.append(length)
    return tuple(result)


def slab(key, polygon, line, stations, heights, material, height_at=None, thickness=THICKNESS):
    """Closed deck over the source outline, with holes for ships and building footprints."""
    import mapbox_earcut

    faces, body = Faces(), Faces()
    from shapely.ops import split

    pieces = list(shapely.get_parts(polygon))
    # Station cuts triangulate the crown; they are not physical slab edges.
    # Allow the ring simplification below without retaining paired internal walls.
    exterior = shapely.union_all(
        [part.boundary for part in pieces if part.geom_type == "Polygon"]
    ).buffer(0.0006)
    for station in stations[1:-1]:
        a = np.asarray(line.interpolate(max(0, station - 0.01)).coords[0])
        b = np.asarray(line.interpolate(min(line.length, station + 0.01)).coords[0])
        normal = np.array([-(b - a)[1], (b - a)[0]])
        normal /= np.linalg.norm(normal)
        c = np.asarray(line.interpolate(station).coords[0])
        cut = shapely.LineString([c - normal * 1000, c + normal * 1000])
        pieces = [part for poly in pieces for part in shapely.get_parts(split(poly, cut))]
    for poly in pieces:
        if poly.geom_type != "Polygon" or poly.area < 0.01:
            continue
        # Intersected sidewalk strips can leave submillimetre ears that collapse
        # after collision-cell float32 export. Keep top, soffit and walls on one
        # simplified ring, rather than dropping mismatched triangles afterwards.
        poly = shapely.geometry.polygon.orient(poly.simplify(0.0005, preserve_topology=True), 1)
        rings = [np.asarray(r.coords)[:-1] for r in [poly.exterior, *poly.interiors]]
        xz = np.concatenate(rings)
        yy = (
            height_at(xz)
            if height_at
            else np.interp(shapely.line_locate_point(line, shapely.points(xz)), stations, heights)
        )
        p = np.c_[xz[:, 0], yy, xz[:, 1]]
        triangles = mapbox_earcut.triangulate_float64(
            xz, np.cumsum([len(r) for r in rings]).astype(np.uint32)
        ).reshape(-1, 3)
        for tri in triangles:
            top = p[tri]
            if np.cross(top[1] - top[0], top[2] - top[0])[1] < 0:
                top = top[::-1]
            faces.face(top, top[:, [0, 2]])
            body.face(top[::-1] - [0, thickness, 0], top[::-1][:, [0, 2]])
        for ring in rings:
            for a, b in zip(ring, np.roll(ring, -1, axis=0), strict=True):
                if not exterior.covers(shapely.LineString([a, b])):
                    continue
                h = (
                    height_at(np.asarray([a, b]))
                    if height_at
                    else np.interp(
                        [line.project(shapely.Point(a)), line.project(shapely.Point(b))],
                        stations,
                        heights,
                    )
                )
                body.face(
                    [
                        [a[0], h[0], a[1]],
                        [b[0], h[1], b[1]],
                        [b[0], h[1] - thickness, b[1]],
                        [a[0], h[0] - thickness, a[1]],
                    ]
                )
    return (
        [
            faces.mesh(key, material),
            details.appearance(body.mesh(key + ":body", "wall"), "concrete"),
        ]
        if faces.indices
        else []
    )


@dataclass
class Deck:
    way: dict
    line: object  # includes the approaches
    stations: np.ndarray
    heights: np.ndarray
    meshes: list
    height_at: object = None


def approach_branches(way):
    """Only mapped paths leaving the main axis are ramps; widened bays remain level."""
    axis = way["line"]
    branches = []
    for member in way.get("member_ways", []):
        line = member["line"]
        along = shapely.line_locate_point(axis, shapely.points(np.asarray(line.coords)))
        if np.abs(np.diff(along)).sum() < line.length * 0.85:
            branches.append(line.buffer(member["width"] / 2 + 1, cap_style="square"))
    return shapely.union_all(branches)


def produce(tile, grid_pos, inputs):
    """Build separate decks; only the landward ramps alter the ground grid."""
    ways = inputs.roads.get("bridge_ways", []) if inputs.roads else []
    if not ways:
        return []
    points = inputs.points
    tree = cKDTree(points[:, [0, 2]])

    def ground(xz):
        _, near = tree.query(np.asarray(xz), k=min(4, len(points)))
        return np.median(points[near, 1], axis=-1)

    from .tunnels import ground_sampler

    full_ground = ground_sampler(lambda x, z: float(ground([[x, z]])[0]))
    from .bridge_join import terrain_sampler

    join_ground = terrain_sampler(points)

    def sample_ground(xz):
        return np.asarray([full_ground(x, z) for x, z in xz])

    decks = []
    for way in sorted(ways, key=lambda w: (w["layer"], w["id"])):
        axis, width = way["line"], way["width"]
        spec = details.style(way)
        s, xz = samples(axis)
        survey = way.get("survey", {})
        anchors = survey.get("height_anchors", [])
        if anchors:
            from .bridge_survey import above_ground_anchors

            anchors = above_ground_anchors(axis, anchors, full_ground)
            survey["height_anchors"] = anchors
        delta = np.diff(xz, axis=0)
        normals = np.c_[-delta[:, 1], delta[:, 0]] / np.linalg.norm(delta, axis=1)[:, None]
        normals = np.vstack([normals, normals[-1]])
        terrain = np.max(
            [sample_ground(xz + normals * offset) for offset in (-width / 2, 0, width / 2)], axis=0
        )
        # Sample just beyond the actual abutments, not a remote hill or building.
        ends = sample_ground(np.asarray([axis.coords[0], axis.coords[-1]])).tolist()
        landing = way.get("landing", False)
        obstacles = shapely.union_all(
            [
                way.get("obstacles", shapely.GeometryCollection()),
                *[b.footprint for b in inputs.buildings],
            ]
        )
        requirements, blocked = [], []
        for under in way["under"]:
            intersection = axis.intersection(under["line"])
            if not intersection.is_empty and axis.boundary.buffer(0.2).covers(intersection):
                continue  # A road continuing from an abutment is not a crossed lower way.
            corridor = under["line"].buffer(under["width"] / 2 + 1.5)
            overlap = axis.intersection(corridor)
            if overlap.is_empty:
                continue
            blocked.append(corridor)
            # Sample the whole crossed carriageway, so clearance holds at both edges.
            for part in shapely.get_parts(overlap):
                if part.geom_type != "LineString":
                    continue
                _, cross = samples(part, 2)
                requirements.extend(
                    (
                        axis.project(shapely.Point(p)),
                        under_height(way, under, shapely.Point(p), full_ground),
                        clearance(under["tags"]),
                    )
                    for p in cross
                )
        for lower in decks:
            if lower.way["layer"] >= way["layer"] or not passes_under(way, lower.way):
                continue
            overlap = axis.intersection(lower.way["line"].buffer(lower.way["width"] / 2 + 1.5))
            for part in shapely.get_parts(overlap):
                if part.geom_type != "LineString":
                    continue
                _, cross = samples(part, 2)
                for p in cross:
                    point = shapely.Point(p)
                    height = float(
                        np.interp(lower.line.project(point), lower.stations, lower.heights)
                    )
                    requirements.append((axis.project(point), height, clearance(lower.way["tags"])))
        blocked.extend(
            w["line"].buffer(w["width"] / 2 + 1.5) for w in way.get("approach_under", [])
        )
        heights = (
            np.interp(s, [0, axis.length], ends)
            if landing
            else deck_profile(s, terrain, ends, requirements, spec["thickness"])
        )
        if "heights" in survey:
            heights = np.interp(s, survey["stations"], survey["heights"])
        elif anchors:
            from .bridge_survey import anchored_profile

            measured_s = np.unique(np.r_[s, [a[0] for a in anchors]])
            measured = anchored_profile(measured_s, np.interp(measured_s, s, heights), anchors)
            if measured is not None:
                s, heights = measured_s, measured
                survey["profile_source"] = "city spot-height anchors with ground approaches"
            else:
                anchors = []
        # Preserve measured crown stations in the slab's piecewise-linear surface.
        start_ramp, end_ramp = approach_lengths(
            way, (heights[0], heights[-1]), join_ground, obstacles
        )
        has_ramp = bool(start_ramp or end_ramp)
        line = extended(axis, (start_ramp, end_ramp)) if has_ramp else axis
        ss, xx = samples(line, 2)
        if anchors:
            ss = np.unique(np.r_[ss, start_ramp + s])
            xx = np.asarray([line.interpolate(float(t)).coords[0] for t in ss])
        hh = np.interp(
            ss,
            np.r_[0, start_ramp + s, line.length],
            np.r_[join_ground(*xx[0]), heights, join_ground(*xx[-1])],
        )
        hh += 0.02
        # Preserve a straight span, including the highest ground across the deck width.
        inside = (ss >= start_ramp) & (ss <= start_ramp + axis.length)
        hh[inside] = np.interp(ss[inside] - start_ramp, s, heights) + 0.02
        key = f"draft-bridge:{way['id']}"
        rail = way["tags"].get("railway") in RAILS
        material = (
            "gravel" if rail else ("sidewalk" if way["tags"].get("highway") in PATHS else "road")
        )
        # The full pipeline's closed, mitered wall prism is also a slab at deck width.
        footprint_poly = way.get(
            "outline", axis.buffer(width / 2, cap_style="flat", join_style="mitre")
        )
        if has_ramp:
            corridor = line.buffer(width / 2, cap_style="flat", join_style="mitre")
            # Skew abutments end each carriageway at a different axis station.
            # The rectangular extension alone leaves wedges between those road
            # ends and the outline. Fill only the mapped continuation corridors.
            continuations = shapely.union_all(
                [
                    w["line"].buffer(w["width"] / 2, cap_style="flat", join_style="mitre")
                    for w in way.get("approaches", [])
                ]
            )
            footprint_poly = footprint_poly.union(
                corridor.difference(axis.buffer(width / 2, cap_style="flat", join_style="mitre"))
            ).union(continuations.intersection(corridor))
        footprint_poly = footprint_poly.difference(obstacles.buffer(0.15))

        ramps = approach_branches(way)

        def height_at(xz, line=line, ss=ss, hh=hh, axis=axis, width=width, ramps=ramps):
            points = shapely.points(xz)
            base = np.interp(shapely.line_locate_point(line, points), ss, hh)
            # Outline branches are mapped approach ramps. Join the deck to measured ground
            # across the branch instead of extending the main span's elevation to its tip.
            blend = np.clip((shapely.distance(axis, points) - width / 2) / 10, 0, 1)
            blend *= shapely.intersects_xy(ramps, xz[:, 0], xz[:, 1])
            return base * (1 - blend) + (sample_ground(xz) + 0.02) * blend

        meshes = slab(
            key + ":deck", footprint_poly, line, ss, hh, material, height_at, spec["thickness"]
        )
        if meshes:
            details.appearance(meshes[0], spec["surface"])
            details.appearance(meshes[1], spec["body"])
        meshes.extend(details.surfaces(way, footprint_poly, height_at))
        outline = way.get("outline", axis.buffer(width / 2, cap_style="flat", join_style="mitre"))
        member_ways = way.get("member_ways", [way])
        openings = shapely.union_all(
            [
                shapely.Point(p).buffer(w["width"] / 2 + 0.5)
                for w in member_ways
                for p in (w["line"].coords[0], w["line"].coords[-1])
            ]
        )
        # Abutments are open to traffic. Rails follow the actual outline, including branches.
        rail_lines = outline.boundary.difference(openings).difference(obstacles.buffer(0.25))
        meshes.extend(
            details.furniture(
                way,
                rail_lines,
                height_at,
                sample_ground,
                [*blocked, obstacles.buffer(0.6)],
                footprint_poly,
            )
        )
        # Blend only the approaches into the ground. The entire span below stays traversable.
        corridor = line.buffer(width / 2 + 2, cap_style="flat")
        candidates = np.flatnonzero(shapely.contains_xy(corridor, grid_pos[:, 0], grid_pos[:, 2]))
        blocked_approaches = obstacles.buffer(width / 2)
        for i in candidates:
            p = shapely.Point(grid_pos[i, [0, 2]])
            t = line.project(p)
            if (
                start_ramp <= t <= start_ramp + axis.length
                or blocked_approaches.covers(p)
                or any(g.covers(p) for g in blocked)
            ):
                continue
            lateral = max(0.0, line.distance(p) - width / 2)
            target = float(np.interp(t, ss, hh)) - 0.02
            grid_pos[i, 1] = max(
                grid_pos[i, 1], target * (1 - lateral / 2) + grid_pos[i, 1] * lateral / 2
            )
        meshes = [clipped for mesh in meshes if (clipped := clip_mesh(mesh, tile.bbox)) is not None]
        if meshes:
            decks.append(Deck(way, line, ss, hh, meshes, height_at))
    return decks


def visual(decks, tile, stage, layer_files):
    meshes = [m for d in decks for m in d.meshes]
    if not meshes:
        return
    path = stage / "bridges.vis.glb"
    write_visual(
        path,
        [
            GlbMesh(
                m.object_id,
                m.positions - tile.origin,
                m.indices,
                uvs=m.uvs,
                image=details.texture(m.metadata.get("surface", "concrete")),
                color=(1, 1, 1, 1),
                repeat=True,
                roughness=0.48 if m.metadata.get("surface") == "steel" else 0.92,
                extras=dict(
                    layer="bridges",
                    object_id=m.object_id,
                    material_key="ground_asphalt"
                    if m.metadata.get("surface") == "asphalt"
                    else "draft_bridge_" + m.metadata.get("surface", "concrete"),
                ),
            )
            for m in meshes
        ],
        root_translation=tile.origin,
    )
    layer_files.append(path)


def collision(decks, tile, ct, pos, idx, mat):
    positions, indices, materials = [pos], [idx], [mat]
    count = len(pos)
    for deck in decks:
        for mesh in deck.meshes:
            if not mesh.metadata.get("collision", True) or ct not in tile_cells(mesh, tile):
                continue
            clipped = clip_mesh(mesh, ct.bbox)
            if clipped is None:
                continue
            # Tiny boundary ears can collapse at the final cell-local precision.
            # Use the same area calculation as the COL validator before exporting.
            local = (clipped.positions - ct.origin).astype(np.float32).astype(np.float64)
            triangles = local[clipped.indices]
            area = (
                np.linalg.norm(
                    np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0]),
                    axis=1,
                )
                / 2
            )
            idx = clipped.indices[area >= 1e-6]
            if not len(idx):
                continue
            positions.append(clipped.positions)
            indices.append(idx + count)
            materials.append(np.full(len(idx), mat_id(mesh.material), np.uint8))
            count += len(clipped.positions)
    return np.concatenate(positions), np.concatenate(indices), np.concatenate(materials)


def road_heights(roads, decks):
    """Optional per-edge vertex Y arrays; XZ v1 readers continue to read the same graph."""
    if roads is None or not decks:
        return roads
    graph = json.loads(roads["json"])
    nodes = np.asarray(graph["nodes"]).reshape(-1, 2)
    heights = []
    for edge_index, edge in enumerate(graph["edges"]):
        coords = np.vstack([nodes[edge[0]], np.asarray(edge[4:]).reshape(-1, 2), nodes[edge[1]]])
        line = shapely.LineString(coords)
        # A crossing lower way must never inherit the bridge height.
        matching = []
        for d in decks:
            corridor = d.way.get("outline", d.line.buffer(d.way["width"] / 2)).buffer(0.3)
            projected = shapely.line_locate_point(d.line, shapely.points(coords))
            if (
                line.intersection(corridor).length > line.length * 0.9
                and np.abs(np.diff(projected)).sum() > line.length * 0.85
            ):
                matching.append(d)
        if not matching:
            previous = graph.get("edgeHeights", [])
            heights.append(previous[edge_index] if edge_index < len(previous) else None)
            continue
        _, coords = samples(line, 2)
        edge[4:] = coords[1:-1].ravel().round(3).tolist()
        ys = []
        for p in coords:
            d = min(matching, key=lambda d: d.line.distance(shapely.Point(p)))
            ys.append(
                round(
                    float(d.height_at(np.asarray([p]))[0])
                    if d.height_at
                    else float(np.interp(d.line.project(shapely.Point(p)), d.stations, d.heights)),
                    3,
                )
            )
        heights.append(ys)
    graph["edgeHeights"] = heights
    return {**roads, "json": (json.dumps(graph, separators=(",", ":")) + "\n").encode()}
