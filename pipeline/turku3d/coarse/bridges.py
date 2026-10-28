"""Fast OSM bridge slabs, unobstructed underpasses and lidar-ground approaches."""

from __future__ import annotations

import json
from dataclasses import dataclass

import numpy as np
import shapely
from scipy.spatial import cKDTree

from turku3d.bridges.build import _box
from turku3d.col.materials import mat_id
from turku3d.layers import write_visual
from turku3d.package.gltf import GlbMesh
from turku3d.structures.geometry import Faces, profile_wall
from turku3d.structures.layer import tile_cells

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


def prepare(tile, osm):
    """Resolve physical crossings with full source ways before selecting this tile."""
    import pyarrow.parquet as pq

    names = pq.read_schema(osm).names
    table = pq.read_table(
        osm, columns=[c for c in ("osm_id", "tags", "geometry_local") if c in names]
    )
    window = shapely.box(*tile.bbox.buffered(500).as_tuple())
    ways, outlines, obstacles, piers = [], {}, [], []
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
    bridges = merge_crossings([w for w in ways if w["bridge"]], list(outlines.values()))
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
        b["under"] = [] if b["landing"] else [w for w in ways if passes_under(b, w)]
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
    return shapely.LineString([coords[0] - a * distance, *coords, coords[-1] + b * distance])


def slab(key, polygon, line, stations, heights, material, height_at=None):
    """Closed deck over the source outline, with holes for ships and building footprints."""
    import mapbox_earcut

    faces = Faces()
    from shapely.ops import split

    pieces = list(shapely.get_parts(polygon))
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
        poly = shapely.geometry.polygon.orient(poly, 1)
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
            faces.face(top[::-1] - [0, THICKNESS, 0], top[::-1][:, [0, 2]])
        for ring in rings:
            for a, b in zip(ring, np.roll(ring, -1, axis=0), strict=True):
                h = (
                    height_at(np.asarray([a, b]))
                    if height_at
                    else np.interp(
                        [line.project(shapely.Point(a)), line.project(shapely.Point(b))],
                        stations,
                        heights,
                    )
                )
                faces.face(
                    [
                        [a[0], h[0], a[1]],
                        [b[0], h[1], b[1]],
                        [b[0], h[1] - THICKNESS, b[1]],
                        [a[0], h[0] - THICKNESS, a[1]],
                    ]
                )
    return [faces.mesh(key, material)] if faces.indices else []


@dataclass
class Deck:
    way: dict
    line: object  # includes the approaches
    stations: np.ndarray
    heights: np.ndarray
    meshes: list
    height_at: object = None


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

    def sample_ground(xz):
        return np.asarray([full_ground(x, z) for x, z in xz])

    decks = []
    for way in sorted(ways, key=lambda w: (w["layer"], w["id"])):
        axis, width = way["line"], way["width"]
        s, xz = samples(axis)
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
        heights = (
            np.interp(s, [0, axis.length], ends)
            if landing
            else deck_profile(s, terrain, ends, requirements)
        )
        # A small blend joins the mapped span; a clearance error must never create a viaduct.
        ramp = min(3.0, axis.length * 0.1) if not landing else 0.0
        line = extended(axis, ramp) if ramp else axis
        ss, xx = samples(line, 2)
        hh = np.interp(
            ss,
            np.r_[0, ramp + s, line.length],
            np.r_[float(sample_ground(xx[:1])[0]), heights, float(sample_ground(xx[-1:])[0])],
        )
        hh = np.maximum(hh, sample_ground(xx)) + 0.02
        # Preserve a straight span, including the highest ground across the deck width.
        inside = (ss >= ramp) & (ss <= ramp + axis.length)
        hh[inside] = np.interp(ss[inside] - ramp, s, heights) + 0.02
        key = f"draft-bridge:{way['id']}"
        rail = way["tags"].get("railway") in RAILS
        material = (
            "gravel" if rail else ("sidewalk" if way["tags"].get("highway") in PATHS else "road")
        )
        # The full pipeline's closed, mitered wall prism is also a slab at deck width.
        footprint_poly = way.get(
            "outline", axis.buffer(width / 2, cap_style="flat", join_style="mitre")
        )
        if ramp:
            footprint_poly = footprint_poly.union(
                line.buffer(width / 2, cap_style="flat", join_style="mitre").difference(
                    axis.buffer(width / 2, cap_style="flat", join_style="mitre")
                )
            )
        footprint_poly = footprint_poly.difference(obstacles.buffer(0.15))

        def height_at(xz, line=line, ss=ss, hh=hh, axis=axis, width=width):
            points = shapely.points(xz)
            base = np.interp(shapely.line_locate_point(line, points), ss, hh)
            # Outline branches are mapped approach ramps. Join the deck to measured ground
            # across the branch instead of extending the main span's elevation to its tip.
            blend = np.clip((shapely.distance(axis, points) - width / 2) / 10, 0, 1)
            return base * (1 - blend) + (sample_ground(xz) + 0.02) * blend

        meshes = slab(key + ":deck", footprint_poly, line, ss, hh, material, height_at)
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
        rail_faces = Faces()
        for part in shapely.get_parts(rail_lines):
            if part.geom_type not in ("LineString", "LinearRing") or part.length < 0.5:
                continue
            _, side = samples(part, 2)
            base = height_at(side)
            for i in range(len(side) - 1):
                section = profile_wall(
                    key + ":rail",
                    side[i : i + 2],
                    base[i : i + 2],
                    base[i : i + 2] + (1.6 if rail else 1.2),
                    thickness=0.18 if rail else 0.10,
                    material="railing",
                )
                for tri in section.indices:
                    rail_faces.face(section.positions[tri], section.uvs[tri])
        if rail_faces.indices:
            meshes.append(rail_faces.mesh(key + ":rail", "railing"))
        for k, station in enumerate(np.arange(15, axis.length - 8, 20)):
            p = axis.interpolate(float(station))
            top = float(np.interp(station, s, heights)) - THICKNESS
            bottom = float(sample_ground(np.array([[p.x, p.y]]))[0])
            # Never place a pier in the crossed road's corridor.
            if (
                not landing
                and top - bottom > 2
                and not obstacles.buffer(0.6).covers(p)
                and not any(g.covers(p) for g in blocked)
            ):
                meshes.append(
                    _box(
                        key + f":pier:{k}",
                        (p.x, (top + bottom) / 2, p.y),
                        (0.45, (top - bottom) / 2, 0.45),
                        "wall",
                    )
                )
        # Blend only the approaches into the ground. The entire span below stays traversable.
        corridor = line.buffer(width / 2 + 2, cap_style="flat")
        candidates = np.flatnonzero(shapely.contains_xy(corridor, grid_pos[:, 0], grid_pos[:, 2]))
        for i in candidates:
            p = shapely.Point(grid_pos[i, [0, 2]])
            t = line.project(p)
            if (
                ramp <= t <= ramp + axis.length
                or obstacles.buffer(width / 2).covers(p)
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
    keys = {
        "road": "bridge_asphalt",
        "sidewalk": "bridge_stone",
        "gravel": "gravel",
        "railing": "metal_railing",
        "wall": "concrete_wall",
    }
    path = stage / "bridges.vis.glb"
    write_visual(
        path,
        [
            GlbMesh(
                m.object_id,
                m.positions - tile.origin,
                m.indices,
                uvs=m.uvs,
                extras=dict(layer="bridges", object_id=m.object_id, material_key=keys[m.material]),
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
            if ct not in tile_cells(mesh, tile):
                continue
            positions.append(mesh.positions)
            indices.append(mesh.indices + count)
            materials.append(np.full(len(mesh.indices), mat_id(mesh.material), np.uint8))
            count += len(mesh.positions)
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
