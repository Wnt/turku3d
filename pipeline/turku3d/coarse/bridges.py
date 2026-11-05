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


SELECT_MARGIN = 5.0  # m around a deck: the tiles that build it (and its end ramps)


def selects(bridge, bbox):
    """Whether the tile with this bbox builds the bridge, its aprons and its approach ramps."""
    return bridge["line"].buffer(bridge["width"] / 2 + SELECT_MARGIN).intersects(shapely.box(*bbox))


def building_region(bridge):
    """Union of the level-0 tiles that build this bridge: a ramp never runs past it, or the next
    tile (which does not build the bridge) would leave a step at the seam."""
    from turku3d import config as C

    size = C.VISUAL_TILE_SIZE
    x0, z0, x1, z1 = bridge["line"].buffer(bridge["width"] / 2 + SELECT_MARGIN).bounds
    boxes = [(i * size, j * size, (i + 1) * size, (j + 1) * size)
             for i in range(int(np.floor(x0 / size)), int(np.floor(x1 / size)) + 1)
             for j in range(int(np.floor(z0 / size)), int(np.floor(z1 / size)) + 1)]
    return shapely.union_all([shapely.box(*b) for b in boxes if selects(bridge, b)])


def is_carriageway(tags):
    return bool(tags.get("highway")) and tags["highway"] not in PATHS and tags["highway"] != "steps"


LANE_CLEARANCE = 0.25  # m between a deck post and the traffic lanes' outer edge
LAMP_SHIFT = 3.0  # m: the farthest a mapped deck lamp moves to clear the lanes


def lane_envelope(ways):
    """Plan area the traffic lanes of these carriageways sweep: the way's width less its 0.5 m shoulders."""
    return shapely.union_all([w["line"].buffer(max(1.0, w["width"] / 2 - 0.5) + LANE_CLEARANCE)
                              for w in ways]) if ways else shapely.Polygon()


def clear_of_lanes(lamps, deck, carriageways):
    """Deck lamps stand clear of the traffic lanes.

    A mapped lamp inside a lane moves to the nearest deck point outside every lane, at most
    LAMP_SHIFT away; a lamp with no such room (a median narrower than the mapped lanes) is not built,
    since a post in a lane stops every car that drives it."""
    if not lamps:
        return []
    envelope = lane_envelope([w for w in carriageways
                              if w["line"].distance(shapely.MultiPoint([p for p, _ in lamps])) < 40])
    if envelope.is_empty:
        return lamps
    from shapely.ops import nearest_points

    room = deck.buffer(-0.3).difference(envelope)
    out = []
    for p, tags in lamps:
        point = shapely.Point(p)
        if not envelope.contains(point):
            out.append((p, tags))
            continue
        if room.is_empty:
            continue
        target = nearest_points(room, point)[0]
        if target.distance(point) <= LAMP_SHIFT:
            out.append(([round(target.x, 3), round(target.y, 3)], tags))
    return out


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


SKEW_LIMIT = float(np.cos(np.radians(10)))  # crossings closer to parallel than this run along the deck


def passes_under(bridge, way):
    """Only actual transverse interior crossings need headroom; approaches do not.

    An indoor way (a station or mall corridor) runs inside the structure that carries
    the deck, such as a driveway over a building set into a slope. It asks no road
    headroom, supports or stair access: lifting the deck over it raises a free span
    metres above the roof and drags the surrounding terrain up with it.
    """
    if way["id"] in bridge.get("members", [bridge["id"]]):
        return False
    tags = way.get("tags", {})
    if tags.get("indoor") not in (None, "no") or tags.get("highway") == "corridor":
        return False
    if way["layer"] > bridge["layer"] or (way["bridge"] and way["layer"] >= bridge["layer"]):
        return False
    a, b = bridge["line"], way["line"]
    cross = a.intersection(b)
    if cross.is_empty or cross.geom_type not in ("Point", "MultiPoint"):
        return False
    for p in shapely.get_parts(cross):
        # A crossing at the bridge's own end is an approach. One at the lower way's end is not:
        # OSM splits a road under a bridge, so a lower way often starts or ends beneath the deck.
        if a.boundary.distance(p) < 0.5:
            continue

        def tangent(line, p=p):
            t = line.project(p)
            return np.asarray(line.interpolate(min(line.length, t + 1)).coords[0]) - np.asarray(
                line.interpolate(max(0, t - 1)).coords[0]
            )

        da, db = tangent(a), tangent(b)
        # Any real crossing, also a skew one (Helsinginkatu runs 18 deg off the railway under
        # Lemminkäisenkatu): only a way along the deck itself is not crossed.
        if abs(da @ db) < SKEW_LIMIT * np.linalg.norm(da) * np.linalg.norm(db):
            return True
    return False


def prepare(tile, osm, survey_paths=(), spot_paths=()):
    """Resolve physical crossings with full source ways before selecting this tile."""
    from turku3d.spatial import rows

    window = shapely.box(*tile.bbox.buffered(500).as_tuple())
    ways, outlines, obstacles, piers, lamps = [], {}, [], [], []
    for row_index, row in rows(
        osm, tile.bbox.buffered(500), columns=("osm_id", "tags", "geometry_local")
    ):
        tags = json.loads(row["tags"] or "{}")
        if not (
            tags.get("highway")
            or tags.get("railway") in RAILS
            or tags.get("building")
            or tags.get("man_made") in ("bridge", "pier")
        ):
            continue
        line = shapely.force_2d(row["geometry_local"])
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
    from .area import passages as area_passages
    from .bridge_survey import height_anchors, spot_heights

    passages = area_passages(osm)
    spots = spot_heights(spot_paths)
    bridges = merge_crossings([w for w in ways if w["bridge"]], list(outlines.values()))
    road_tree = shapely.STRtree([w["line"] for w in ways])
    obstacle = shapely.union_all(obstacles)
    pier = shapely.union_all(piers)
    result = []
    for b in bridges:
        if not selects(b, tile.bbox.as_tuple()):
            continue
        b["landing"] = (b["line"].length < 25 and b["line"].distance(pier) < 2) or (
            b["line"].length < 8 and b["tags"].get("highway") in PATHS
        )
        b["quay_passages"] = [p for p in passages if p[1].distance(b["line"]) < b["width"]]
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
        foot = b["tags"].get("highway") in PATHS
        from .bridge_join import connected_approaches

        b["approaches"] = connected_approaches(
            members,
            [ways[i] for i in sorted(road_tree.query(endpoints.buffer(0.05), predicate="intersects"))],
        )
        from .bridge_access import configure

        configure(b, ways, members, foot)
        extended_way = {**b, "line": extended(b["line"], 30)}
        under_ids = {w["id"] for w in b["under"]}
        b["below"] = below_deck(b, ways, road_tree)
        b["lower_continuations"] = lower_continuations(b, ways, road_tree)
        b["approach_under"] = [
            ways[i]
            for i in sorted(road_tree.query(extended_way["line"], predicate="intersects"))
            if ways[i]["id"] not in under_ids and passes_under(extended_way, ways[i])
        ]
        from .bridge_survey import survey

        deck = b.get("outline", b["line"].buffer(b["width"] / 2))
        b["lamps"] = clear_of_lanes(
            [(p, tags) for p, tags in lamps if deck.buffer(0.5).covers(shapely.Point(p))],
            deck, [w for w in ways if w["bridge"] and is_carriageway(w["tags"])])
        b["survey"] = survey(b, survey_paths)
        if "heights" not in b["survey"]:
            b["survey"]["height_anchors"] = height_anchors(b, spots)
        result.append(b)
    return result


CUT_REACH = 2.0  # m around a deck where the ground over a lower way is cut fully down to it
CUT_ANCHOR = 10.0  # m beyond the deck where the lower way's own ground level is read
# m beyond CUT_REACH over which the cut fades out, so it never ends in a step; the whole cut stays
# inside the tiles that build the deck (SELECT_MARGIN).
CUT_FEATHER = SELECT_MARGIN - CUT_REACH
CUT_MARGIN = 2.0  # m beyond the lower way's half width: its outer lanes, shoulders and footways
SAG_REACH = 40.0  # m beyond a deck that its clearance dip may reach


def under_profile(bridge, line, sample, step=2.0):
    """(stations, heights, inside) of a lower road's surface through a crossing along ``line``: its
    measured ground beyond CUT_ANCHOR from the deck, interpolated straight under it. Ground under a
    deck is a survey gap; interpolating the terrain across it buries the lower road in an
    embankment. ``inside`` marks the stations bracketed by measured ground on both sides: a road
    that ends under the deck, or climbs to it, has no level beyond its last anchor to cut to."""
    deck = bridge.get("outline", bridge["line"].buffer(bridge["width"] / 2))
    far = deck.buffer(CUT_ANCHOR)
    stations = np.unique(np.r_[np.arange(0, line.length, step), line.length])
    points = shapely.line_interpolate_point(line, stations)
    outside = ~shapely.intersects(far, points)
    if not outside.any():
        return None
    heights = np.array([sample(p.x, p.y) for p in points[outside]], dtype=np.float64)
    anchored = stations[outside]
    inside = (stations >= anchored.min()) & (stations <= anchored.max())
    return stations, np.interp(stations, anchored, heights), inside


def lower_chains(lower):
    """Lower ways joined end to end: OSM often splits a street right under a deck, and each half
    alone has measured ground on one side only."""
    lines = [w["line"] for w in lower]
    return list(shapely.get_parts(shapely.line_merge(shapely.union_all(lines)))) if lines else []


def below_deck(bridge, ways, tree):
    """Lower ways under the deck's footprint that the axis test misses: under a wide outline (a
    railway viaduct over a skew street) a road can pass beneath without crossing the deck's axis.
    Members, their land continuations and anything at or above the deck's layer are not below it."""
    deck = bridge.get("outline", bridge["line"].buffer(bridge["width"] / 2))
    members = {m.get("id") for m in bridge.get("member_ways", [bridge])}
    approaches = {w.get("id") for w in bridge.get("approaches", [])}
    ends = shapely.union_all([m["line"].boundary for m in bridge.get("member_ways", [bridge])]).buffer(1.0)
    out = []
    for i in sorted(tree.query(deck, predicate="intersects")):
        way = ways[i]
        if (way["id"] in members or way["id"] in approaches or way["bridge"]
                or way["layer"] >= bridge["layer"] or way["tags"].get("highway") in (None, "steps")
                or way["line"].intersects(ends)):
            continue
        if way["line"].intersection(deck).length < 2.0:
            continue
        out.append(way)
    return out


def lower_continuations(bridge, ways, tree):
    """Open ways that continue a lower way end to end beyond the deck. A street mapped in pieces
    can end right beside a deck (Helsinginkatu under the Lemminkäisensilta viaducts), and its
    measured ground on that side lies on the next piece."""
    lower = [*bridge.get("under", []), *bridge.get("below", [])]
    if not lower:
        return []
    skip = {w.get("id") for w in lower} | {m.get("id") for m in bridge.get("member_ways", [bridge])}
    ends = shapely.union_all([w["line"].boundary for w in lower]).buffer(0.05)
    out = []
    for i in sorted(tree.query(ends, predicate="intersects")):
        way = ways[i]
        tags = way["tags"]
        if (way["id"] in skip or way["bridge"] or way["layer"] >= bridge["layer"]
                or tags.get("highway") in (None, "steps")
                or tags.get("tunnel") not in (None, "no") or tags.get("covered") not in (None, "no")):
            continue
        out.append(way)
    return out


def cut_under(grid_pos, bridge, sample):
    """Lower the terrain over every way that passes under this deck to that way's own level
    (``under_profile``), within the way's width and CUT_REACH of the deck. Only lowers: a lower way
    already in a cutting keeps its ground. Shared by the visual ground and the collision."""
    positions = np.asarray(grid_pos, dtype=np.float64)
    result = positions.copy()
    if bridge.get("landing"):
        return result
    outline = bridge.get("outline", bridge["line"].buffer(bridge["width"] / 2))
    deck = outline.buffer(CUT_REACH + CUT_FEATHER)
    lower = {w.get("id", id(w)): w for w in [*bridge.get("under", []), *bridge.get("below", [])]}
    chains = lower_chains([w for w in [*lower.values(), *bridge.get("lower_continuations", [])]
                           if w["tags"].get("highway") != "steps"])
    for under in lower.values():
        if under["tags"].get("highway") == "steps":
            continue
        corridor = under["line"].buffer(under["width"] / 2 + CUT_MARGIN).intersection(deck)
        if corridor.is_empty:
            continue
        inside = np.flatnonzero(shapely.intersects_xy(corridor, positions[:, 0], positions[:, 2]))
        if not len(inside):
            continue
        chain = max(chains, key=lambda c: c.intersection(under["line"].buffer(0.05)).length)
        profile = under_profile(bridge, chain, sample)
        if profile is None:
            continue
        stations, heights, bracketed = profile
        points = shapely.points(positions[inside][:, [0, 2]])
        located = shapely.line_locate_point(chain, points)
        level = np.interp(located, stations, heights)
        # Full depth within CUT_REACH of the deck, fading to none over CUT_FEATHER beyond it.
        weight = np.clip(1 - (shapely.distance(outline, points) - CUT_REACH) / CUT_FEATHER, 0, 1)
        weight *= np.interp(located, stations, bracketed.astype(np.float64)) >= 1.0
        y = result[inside, 1]
        result[inside, 1] = y - weight * np.maximum(0.0, y - level)
    return result


DRIVE_CLEARANCE = 3.0  # m under a deck's soffit that a road keeps when no maxheight is mapped
WALK_CLEARANCE = 2.3  # m for a path
SAG_GRADE = 0.08  # steepest grade of the dip that gives a lower road its clearance


def mapped_clearance(tags):
    """Mapped maxheight in metres (``3.4``, ``3.4 m``), or the drive/walk default."""
    raw = str(tags.get("maxheight", "")).replace("m", "").strip()
    try:
        return float(raw)
    except ValueError:
        return WALK_CLEARANCE if tags.get("highway") in PATHS else DRIVE_CLEARANCE


def clearance_cut(grid_pos, bridge, height_at, thickness, outline):
    """Lower the ground over each lower road until it keeps its clearance below the deck's
    soffit, dipping in and out at SAG_GRADE. The ground under a survey-fixed deck (a railway
    viaduct) can meet its soffit: Puistokatu climbed to 1.2 m under the station tracks."""
    positions = np.asarray(grid_pos, dtype=np.float64)
    result = positions.copy()
    if bridge.get("landing") or outline is None or outline.is_empty:
        return result
    lower = {w.get("id", id(w)): w for w in [*bridge.get("under", []), *bridge.get("below", [])]}
    region = building_region(bridge)
    for under in lower.values():
        if under["tags"].get("highway") in (None, "steps"):
            continue
        need = mapped_clearance(under["tags"])
        # Only inside the tiles that build this deck: the next tile does not know the dip.
        corridor = under["line"].buffer(under["width"] / 2 + CUT_MARGIN).intersection(
            outline.buffer(SAG_REACH)).intersection(region)
        if corridor.is_empty:
            continue
        inside = np.flatnonzero(shapely.intersects_xy(corridor, positions[:, 0], positions[:, 2]))
        if not len(inside):
            continue
        xz = positions[inside][:, [0, 2]]
        points = shapely.points(xz)
        nearest = shapely.get_coordinates(shapely.shortest_line(outline, points))[0::2]
        distance = shapely.distance(outline, points)
        soffit = np.asarray(height_at(nearest), dtype=np.float64) - thickness
        limit = soffit - need + SAG_GRADE * distance
        y = result[inside, 1]
        result[inside, 1] = np.where(soffit > y, np.minimum(y, limit), y)
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


GRADE = 0.4  # a deck this close to the measured ground under it rests on that ground
DROP = 1.0  # an on-grade deck edge needs a railing only above a drop this deep


def on_grade(heights, terrain, requirements, footprint, water, roofs):
    """A deck on a building's roof terrace: over no mapped way or water, partly on the
    roof, with a profile that follows the measured ground (the terrace) under it."""
    if requirements or not len(heights) or (not water.is_empty and footprint.intersects(water)):
        return False
    if roofs.is_empty or footprint.intersection(roofs).area < 0.1 * footprint.area:
        return False
    return float(np.mean(np.abs(np.asarray(heights) - np.asarray(terrain)) <= GRADE)) >= 0.9


def drop_edges(lines, height_at, sample_ground, step=1.0):
    """Parts of an on-grade deck's edges where the ground beside it falls away."""
    from shapely.ops import substring

    keep = []
    for part in shapely.get_parts(lines):
        if part.geom_type not in ("LineString", "LinearRing") or part.length < 1e-6:
            continue
        count = max(1, int(np.ceil(part.length / step)))
        for k in range(count):
            piece = substring(part, k * part.length / count, (k + 1) * part.length / count)
            ends = np.asarray(piece.coords)[[0, -1]]
            direction = ends[1] - ends[0]
            if np.linalg.norm(direction) < 1e-9:
                continue
            normal = np.array([-direction[1], direction[0]]) / np.linalg.norm(direction)
            middle = ends.mean(axis=0)
            beside = sample_ground(np.array([middle + normal, middle - normal]))
            if float(height_at(middle[None])[0]) - float(np.min(beside)) > DROP:
                keep.append(piece)
    # Merged pieces keep their joints; drop them so railings get no zero-length bays.
    return shapely.line_merge(shapely.union_all(keep)).simplify(0.01) if keep else shapely.LineString()


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
    approaches = [
        w for w in way.get("approaches", [])
        if (w["tags"].get("highway") in PATHS) == (way["tags"].get("highway") in PATHS)
        and (w["tags"].get("railway") in RAILS) == (way["tags"].get("railway") in RAILS)
    ]
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
        if way.get("stair_access"):
            from .bridge_access import landing_length

            result.append(landing_length(way, endpoint, direction))
            continue
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


def slab(key, polygon, line, stations, heights, material, height_at=None, thickness=THICKNESS,
         outline=None):
    """Closed deck over the source outline, with holes for ships and building footprints.

    ``polygon`` may be a list of (polygon, height function) levels (``deck_levels``): each level is
    triangulated with its own heights, so one outline can carry carriageways at different levels.
    Only edges on ``outline`` (default: the pieces' own boundary) get the slab's side faces."""
    import mapbox_earcut

    faces, body = Faces(), Faces()
    from shapely.ops import split

    levels = polygon if isinstance(polygon, list) else [(polygon, height_at)]
    pieces = [(part, fn) for geom, fn in levels for part in shapely.get_parts(geom)]
    # Station cuts triangulate the crown; they are not physical slab edges, and neither are the
    # boundaries between levels. Allow the ring simplification without retaining internal walls.
    edge = [outline] if outline is not None else [part for part, _ in pieces]
    exterior = shapely.union_all(
        [part.boundary for geom in edge for part in shapely.get_parts(geom)
         if part.geom_type == "Polygon"]
    ).buffer(0.0006)
    for station in stations[1:-1]:
        a = np.asarray(line.interpolate(max(0, station - 0.01)).coords[0])
        b = np.asarray(line.interpolate(min(line.length, station + 0.01)).coords[0])
        normal = np.array([-(b - a)[1], (b - a)[0]])
        normal /= np.linalg.norm(normal)
        c = np.asarray(line.interpolate(station).coords[0])
        cut = shapely.LineString([c - normal * 1000, c + normal * 1000])
        pieces = [(part, fn) for poly, fn in pieces for part in shapely.get_parts(split(poly, cut))]
    # Cuts along the deck as well, every SLAB_CELL: a station strip spans the whole deck width,
    # and one triangle from a lowered outline corner to the far edge sloped across the lanes
    # between them (Kalevan: 2.6 m below the deck in the middle of its main carriageway).
    ends = np.asarray(shapely.get_coordinates(line))[[0, -1]]
    along = ends[1] - ends[0]
    if np.linalg.norm(along) > 1e-6 and pieces:
        along /= np.linalg.norm(along)
        across = np.array([-along[1], along[0]])
        coords = np.concatenate([shapely.get_coordinates(poly) for poly, _ in pieces])
        offsets = (coords - ends[0]) @ across
        for offset in np.arange(np.floor(offsets.min() / SLAB_CELL) * SLAB_CELL, offsets.max(), SLAB_CELL)[1:]:
            c = ends[0] + across * offset
            cut = shapely.LineString([c - along * 1000, c + along * 1000])
            pieces = [(part, fn) for poly, fn in pieces for part in shapely.get_parts(split(poly, cut))]
    for poly, height_at in pieces:
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
    aprons: tuple = ()


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


BRANCH_OUTSIDE = 10.0  # m of a deck member outside the main carriageway that make it a branch


def branch_profiles(way, main_height, sample_ground):
    """(line, stations, heights, half width) of each deck member that leaves the main axis (a link):
    the deck's height where it leaves, then a vertical curve to the ground at its far end."""
    from .bridge_join import vertical_curve

    axis, out = way["line"], []
    merged = axis.buffer(way["width"] / 2)
    members = way.get("member_ways", [])
    # A link diverges slowly, so most of its length can run along the axis: what makes it a
    # branch is a stretch outside the main carriageway.
    # Only carriageways: a cycleway or footway leaving the deck keeps the deck whole (Kalevan's
    # north cycleway split the main deck and sloped its triangles across the main lanes).
    branches = [m for m in members if is_carriageway(m["tags"])
                and m["line"].difference(merged).length >= BRANCH_OUTSIDE]
    # Where a link leaves the deck is where it leaves the other members' own carriageways, not the
    # merged deck's width (which also spans the link while it descends alongside).
    core = shapely.union_all([m["line"].buffer(m["width"] / 2) for m in members if m not in branches]) \
        if len(branches) < len(members) else merged
    for member in branches:
        line = member["line"]
        if core.distance(shapely.Point(line.coords[0])) > core.distance(shapely.Point(line.coords[-1])):
            line = shapely.LineString(list(line.coords)[::-1])  # run from the deck outward
        stations = np.unique(np.r_[np.arange(0, line.length, 2.0), line.length])
        points = shapely.line_interpolate_point(line, stations)
        on_deck = shapely.intersects(core, points)
        leave = float(stations[np.flatnonzero(on_deck)[-1]]) if on_deck.any() else 0.0
        top = float(main_height(np.asarray(line.interpolate(leave).coords))[0])
        end = np.asarray(line.coords[-1])[None]
        bottom = float(sample_ground(end)[0]) + 0.02
        heights = np.where(stations <= leave, top, vertical_curve(stations - leave, line.length - leave,
                                                                    top, bottom, 0.0, 0.0))
        out.append((line, stations, heights, member["width"] / 2 + 1))
    return out, core


SLAB_CELL = 4.0  # m between the slab's cuts along the deck (it follows the height function)
LEVEL_STEP = 0.1  # m: a link this far below the main deck beside it is a separate level, behind a parapet
PARAPET = 1.0  # m above the higher level, on the cut between two levels
LINK_SIDE = 3.0  # m of outline beside a descending link that rides on the link's level
LINK_RUNOUT = 12.0  # m of outline past a link's end that rides on the link's end height


def deck_levels(footprint, profiles, main_height, link_height, core=None):
    """(levels, cuts): the deck footprint as (polygon, height function) levels and the cut lines.

    Each link's descent (from where its profile leaves the deck's height) is a level of its own.
    It takes the outline beside the link (LINK_SIDE) and past its end (LINK_RUNOUT) too, at the
    nearest link station's height: an outline drawn wider or longer than the link is not left at
    the main deck's height above the link's lane, standing as a wall where the link meets the
    ground. ``core`` (the main carriageways) always stays on the main level. The rest keeps the
    main deck's heights, except within LEVEL_STEP of the link where the two meet without a step
    (the junction, where lanes cross between them). ``cuts`` are the shared boundaries where the
    levels differ by more than LEVEL_STEP."""
    from shapely.ops import substring

    from shapely.ops import split

    descents, cuts_across = [], []
    for line, stations, heights, half in profiles:
        flat = stations[np.isclose(heights, heights[0])]
        leave = float(flat[-1]) if len(flat) else 0.0
        if line.length - leave > 1.0:
            tail = shapely.get_coordinates(substring(line, leave, line.length))
            direction = tail[-1] - tail[-2]
            direction /= max(float(np.hypot(*direction)), 1e-9)
            runout = shapely.LineString(np.vstack([tail, tail[-1] + direction * LINK_RUNOUT]))
            descents.append(runout.buffer(half + LINK_SIDE, cap_style="flat"))
            # Cuts across the link every 2 m: the level is wider than the lane, and a triangle
            # spanning it between far-apart stations would cut a chord through the descent.
            for station in np.arange(2.0, runout.length, 2.0):
                a = np.asarray(runout.interpolate(station - 0.01).coords[0])
                b = np.asarray(runout.interpolate(station + 0.01).coords[0])
                normal = np.array([a[1] - b[1], b[0] - a[0]]) / max(float(np.hypot(*(b - a))), 1e-9)
                c = np.asarray(runout.interpolate(station).coords[0])
                reach = half + LINK_SIDE + 1.0
                cuts_across.append(shapely.LineString([c - normal * reach, c + normal * reach]))
    if not descents:
        return [(footprint, None)], shapely.MultiLineString()
    descent = shapely.union_all(descents).intersection(footprint)
    if core is not None and not core.is_empty:
        descent = descent.difference(core)
    descent = shapely.segmentize(descent, 2.0)
    strips = [part for part in shapely.get_parts(descent) if part.geom_type == "Polygon"]
    for cut in cuts_across:
        strips = [piece for part in strips for piece in shapely.get_parts(split(part, cut))
                  if piece.geom_type == "Polygon"]
    descent_strips = shapely.MultiPolygon(strips) if strips else descent
    rest = shapely.segmentize(footprint.difference(descent), 2.0)

    def descent_height(xz):
        return branch_height(profiles, xz, link_height(xz), reach=np.inf)

    def rest_height(xz):
        main, link = main_height(xz), link_height(xz)
        return np.where(np.abs(main - link) <= LEVEL_STEP, link, main)

    shared = shapely.intersection(rest.boundary, descent.boundary)
    cuts = []
    for part in shapely.get_parts(shapely.line_merge(shapely.union_all(
            [g for g in shapely.get_parts(shared) if g.geom_type in ("LineString", "MultiLineString")]))):
        coords = shapely.get_coordinates(shapely.segmentize(part, 1.0))
        if len(coords) < 2:
            continue
        step = np.abs(rest_height(coords) - descent_height(coords)) > LEVEL_STEP
        for run in np.split(np.flatnonzero(step), np.flatnonzero(np.diff(np.flatnonzero(step)) > 1) + 1):
            if len(run) >= 2:
                cuts.append(shapely.LineString(coords[run]))
    return [(rest, rest_height), (descent_strips, descent_height)], shapely.MultiLineString(cuts)


def levels_height(levels, fallback):
    """One height function over (polygon, height function) levels; ``fallback`` off all of them."""
    def height(xz):
        xz = np.asarray(xz, dtype=np.float64)
        out = np.asarray(fallback(xz), dtype=np.float64).copy()
        for polygon, fn in levels:
            inside = shapely.intersects_xy(polygon, xz[:, 0], xz[:, 1])
            if inside.any():
                out[inside] = fn(xz[inside])
        return out

    return height


def level_parapets(key, cuts, main_height, link_height):
    """A wall on each cut between levels, from the lower level up to PARAPET above the higher."""
    from . import bridge_details as details

    meshes = []
    for cut in shapely.get_parts(cuts):
        points = shapely.get_coordinates(cut)
        if len(points) < 2:
            continue
        main, link = main_height(points), link_height(points)
        meshes.append(details.wall(key + ":level-cut", points, np.minimum(main, link),
                                   np.maximum(main, link) + PARAPET, 0.25, "concrete"))
    return meshes


def branch_height(profiles, xz, fallback, reach=None):
    """Height of the nearest branch profile within its half width (or ``reach``), else ``fallback``."""
    result = np.asarray(fallback, dtype=np.float64).copy()
    best = np.full(len(result), np.inf)
    points = shapely.points(xz)
    for line, stations, heights, half in profiles:
        distance = shapely.distance(line, points)
        use = (distance <= (half if reach is None else reach)) & (distance < best)
        if use.any():
            result[use] = np.interp(shapely.line_locate_point(line, points[use]), stations, heights)
            best[use] = distance[use]
    return result


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
    from .bridge_join import terrain_sampler, walk_levels

    join_ground = terrain_sampler(points)
    # Separately mapped walks and flights keep their measured lower level.
    levels = walk_levels(getattr(inputs, "walkable", ()), (getattr(inputs, "stairs", None) or ((),))[0])

    def sample_ground(xz):
        return np.asarray([full_ground(x, z) for x, z in xz])

    decks = []
    rails = {w["id"]: r for w, r in zip(ways, details.railing_lines(ways), strict=True)}
    # Ground over the ways under the decks first: every deck's end ramps (aprons) then raise from
    # it, so a lower road that climbs onto the next bridge keeps its ramp.
    for way in ways:
        grid_pos[:] = cut_under(grid_pos, way, full_ground)
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
        requirements = []
        blocked = [
            line.buffer(float(tags["width"]) / 2 + 0.4)
            for _, line, tags in way.get("quay_passages", [])
        ]
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
        # Lower roads under the outline that miss the axis are protected from the end ramps too:
        # an apron raised over their lanes stands as an abutment block in them (Kalevan south joint).
        blocked.extend(
            w["line"].buffer(w["width"] / 2 + CUT_MARGIN) for w in way.get("below", [])
        )
        heights = (
            np.interp(s, [0, axis.length], ends)
            if landing
            else deck_profile(s, terrain, ends, requirements, spec["thickness"])
        )
        wet = shapely.union_all(list(getattr(inputs, "water", ()) or ()))
        # The way's mapped building outlines are the same in both tiles of a split deck.
        if not landing and on_grade(heights, terrain, requirements, axis.buffer(width / 2), wet,
                                    obstacles):
            # A mapped bridge that crosses nothing and rests on measured ground is a
            # terrace driveway (a roof terrace of a building set into a slope): a
            # thin paved slab, no body, abutments, supports or ramps, and a railing
            # only where the ground beside it drops away.
            way = {**way, "landing": True, "on_grade": True}
            spec = details.style(way)
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
        if way.get("stair_access"):
            hh = np.interp(ss - start_ramp, s, heights) + 0.02
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

        def main_height(xz, line=line, ss=ss, hh=hh):
            return np.interp(shapely.line_locate_point(line, shapely.points(xz)), ss, hh)

        profiles, branch_core = branch_profiles(way, main_height, sample_ground)
        if profiles:
            # A link mapped as bridge beyond the deck outline still carries its lane: its deck
            # runs on to the link's end on the branch profile, instead of a cliff to the ground.
            links = shapely.union_all([line.buffer(half - 1.0, cap_style="flat")
                                       for line, _, _, half in profiles])
            # Vertices every 2 m along the link, so the slab follows its descent, not a chord.
            footprint_poly = shapely.segmentize(footprint_poly.union(links), 2.0).difference(
                obstacles.buffer(0.15))

        links = shapely.union_all([line.buffer(half, cap_style="flat") for line, _, _, half in profiles]) \
            if profiles else shapely.Polygon()

        def height_at(xz, line=line, ss=ss, hh=hh, axis=axis, width=width, ramps=ramps,
                      profiles=profiles, links=links, branch_core=branch_core):
            points = shapely.points(xz)
            base = np.interp(shapely.line_locate_point(line, points), ss, hh)
            # Outline branches are mapped approach ramps: each follows its own way's profile
            # from the deck to the ground at its end (``branch_profiles``), not a blend by
            # distance from the main axis, which sloped the deck sideways under its lanes.
            blend = np.clip((shapely.distance(axis, points) - width / 2) / 2, 0, 1)
            blend *= shapely.intersects_xy(ramps, xz[:, 0], xz[:, 1])
            if profiles:
                on_link = shapely.intersects_xy(links, xz[:, 0], xz[:, 1])
                blend = np.where(on_link, np.clip(shapely.distance(branch_core, points) / 2, 0, 1), blend)
            if not blend.any():
                return base
            branch = branch_height(profiles, xz, sample_ground(xz) + 0.02)
            return base * (1 - blend) + branch * blend

        def link_only(xz, line=line, ss=ss, hh=hh, profiles=profiles):
            base = np.interp(shapely.line_locate_point(line, shapely.points(xz)), ss, hh)
            return branch_height(profiles, xz, base) if profiles else base

        def main_only(xz, line=line, ss=ss, hh=hh, axis=axis, width=width, ramps=ramps):
            points = shapely.points(xz)
            base = np.interp(shapely.line_locate_point(line, points), ss, hh)
            blend = np.clip((shapely.distance(axis, points) - width / 2) / 2, 0, 1)
            blend *= shapely.intersects_xy(ramps, xz[:, 0], xz[:, 1])
            return base * (1 - blend) + (sample_ground(xz) + 0.02) * blend if blend.any() else base

        deck_parts, cuts = deck_levels(footprint_poly, profiles, main_only, link_only, branch_core)
        if deck_parts[0][1] is None:
            deck_parts = [(footprint_poly, height_at)]
        meshes = slab(
            key + ":deck", deck_parts, line, ss, hh, material, height_at, spec["thickness"],
            outline=footprint_poly,
        )
        if meshes:
            details.appearance(meshes[0], spec["surface"])
            details.appearance(meshes[1], spec["body"])
        from .night_accents import bridge_profile, bridge_strips
        profile = bridge_profile(way)
        if profile.get("soffit") and len(meshes) > 1:
            meshes[1].metadata["night_soffit"] = True
        if len(deck_parts) > 1:
            meshes.extend(level_parapets(key, cuts, deck_parts[0][1], deck_parts[1][1]))
            # Lanes, surfaces, rails and furniture sit on the slab's own levels.
            height_at = levels_height(deck_parts, height_at)
        meshes.extend(details.surfaces(way, footprint_poly, height_at))
        rail_lines = rails[way["id"]].difference(obstacles.buffer(0.25))
        if way.get("on_grade"):
            rail_lines = drop_edges(rail_lines, height_at, sample_ground)
        meshes.extend(
            details.furniture(
                way,
                rail_lines,
                height_at,
                sample_ground,
                [*blocked, obstacles.buffer(0.6)],
                footprint_poly,
                obstacles=obstacles.buffer(0.6),
            )
        )
        meshes.extend(bridge_strips(way, rail_lines, height_at, spec["rail_height"]))
        # Every member's mapped land continuation shares the same end taper,
        # including side paths whose skew endpoint lies inside the main axis.
        from .bridge_join import approach_patches, grade
        from .inputs import CELLS
        from .walkable import sampler as walk_sampler

        protected = shapely.union_all([*blocked, obstacles.buffer(.15), *getattr(inputs, "water", ())])
        aprons = approach_patches(
            way, line, (start_ramp, end_ramp), height_at,
            walk_sampler(full_ground, getattr(inputs, "walkable", ())),
            protected, 2 * tile.size / CELLS, levels,
        )
        grid_pos[:] = grade(grid_pos, aprons)
        grid_pos[:] = clearance_cut(grid_pos, way, height_at, spec["thickness"], footprint_poly)
        meshes = [clipped for mesh in meshes if (clipped := clip_mesh(mesh, tile.bbox)) is not None]
        if meshes:
            decks.append(Deck(way, line, ss, hh, meshes, height_at, tuple(aprons)))
    return decks


def visual(decks, tile, stage, layer_files):
    from .bridge_materials import METRES, maps

    from .night_accents import bridge_profile
    meshes = [(m, details.style(d.way).get("material_set"), bridge_profile(d.way)) for d in decks for m in d.meshes]
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
                uvs=m.uvs / METRES if material_set == "weathered" and m.metadata.get("surface") != "asphalt" else m.uvs,
                **(maps(("paint" if m.metadata.get("surface") == "night_rail" else m.metadata.get("surface", "concrete"))) if material_set == "weathered"
                   else dict(image=details.texture(("paint" if m.metadata.get("surface") == "night_rail" else m.metadata.get("surface", "concrete"))))),
                color=(1, 1, 1, 1),
                repeat=True,
                emissive=tuple(profile.get("rail", [0, 0, 0])) if m.metadata.get("surface") == "night_rail"
                else tuple(profile.get("soffit", [0, 0, 0])) if m.metadata.get("night_soffit") else (0, 0, 0),
                roughness=0.48 if m.metadata.get("surface") == "steel" else 0.92,
                extras=dict(
                    layer="bridges",
                    object_id=m.object_id,
                    material_key="night_bridge_rail" if m.metadata.get("surface") == "night_rail"
                    else "night_bridge_soffit" if m.metadata.get("night_soffit")
                    else "ground_asphalt"
                    if m.metadata.get("surface") == "asphalt"
                    else "draft_bridge_" + ("paint" if m.metadata.get("surface") == "night_rail" else m.metadata.get("surface", "concrete")),
                ),
            )
            for m, material_set, profile in meshes
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
        way_id = (str(graph["edgeAttrs"][edge_index][graph["edgeAttrColumns"].index("way")])
                  if "way" in graph.get("edgeAttrColumns", []) else None)
        for d in decks:
            corridor = d.way.get("outline", d.line.buffer(d.way["width"] / 2)).buffer(0.3)
            projected = shapely.line_locate_point(d.line, shapely.points(coords))
            members = {str(m["id"]) for m in d.way.get("member_ways", [d.way])}
            # A deck's own member (a link leaving the main axis) takes the deck's surface too.
            # (its deck runs along the whole member, see branch_profiles).
            member = way_id in members
            if member or (
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
