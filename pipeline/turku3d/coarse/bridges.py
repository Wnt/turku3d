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
from turku3d.structures.geometry import profile_wall
from turku3d.structures.layer import tile_cells

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


def prepare(tile, osm):
    """Keep complete bridge ways plus crossed transport ways, including their layer and widths."""
    import pyarrow.parquet as pq

    names = pq.read_schema(osm).names
    table = pq.read_table(
        osm, columns=[c for c in ("osm_id", "tags", "geometry_local") if c in names]
    )
    window = shapely.box(*tile.bbox.buffered(100).as_tuple())
    ways = []
    for row_index, row in enumerate(table.to_pylist()):
        tags = json.loads(row["tags"] or "{}")
        if not tags.get("highway") and tags.get("railway") not in RAILS:
            continue
        line = shapely.force_2d(shapely.from_wkb(row["geometry_local"]))
        if line.geom_type != "LineString" or line.length < 0.5 or not line.intersects(window):
            continue
        bridge = tags.get("bridge", "no") not in ("no", "false", "0", "")
        ways.append(
            dict(
                id=str(row.get("osm_id", row_index)),
                tags=tags,
                line=line,
                bridge=bridge,
                layer=number(tags.get("layer"), 1 if bridge else 0),
                width=way_width(tags),
            )
        )
    bridges = [w for w in ways if w["bridge"]]
    if not bridges:
        return []
    tree = shapely.STRtree([w["line"] for w in ways])
    for b in bridges:
        b["under"] = [
            ways[i]
            for i in tree.query(b["line"].buffer(b["width"] / 2))
            if (
                ways[i]["layer"] < b["layer"]
                or (ways[i]["layer"] == b["layer"] and not ways[i]["bridge"])
            )
            and ways[i]["id"] != b["id"]
        ]
    # Avoid recursive references when bridges cross other bridges.
    return [
        {**b, "under": [{k: v for k, v in w.items() if k != "under"} for w in b["under"]]}
        for b in bridges
    ]


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


@dataclass
class Deck:
    way: dict
    line: object  # includes the approaches
    stations: np.ndarray
    heights: np.ndarray
    meshes: list


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

    decks = []
    for way in sorted(ways, key=lambda w: (w["layer"], w["id"])):
        axis, width = way["line"], way["width"]
        s, xz = samples(axis)
        delta = np.diff(xz, axis=0)
        normals = np.c_[-delta[:, 1], delta[:, 0]] / np.linalg.norm(delta, axis=1)[:, None]
        normals = np.vstack([normals, normals[-1]])
        terrain = np.max(
            [ground(xz + normals * offset) for offset in (-width / 2, 0, width / 2)], axis=0
        )
        full = extended(axis, 25)
        _, beyond = samples(full, 5)
        ends = [
            float(np.percentile(ground(beyond[:5]), 90)),
            float(np.percentile(ground(beyond[-5:]), 90)),
        ]
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
                    (axis.project(shapely.Point(p)), float(h), clearance(under["tags"]))
                    for p, h in zip(cross, ground(cross), strict=True)
                )
        for lower in decks:
            if lower.way["layer"] >= way["layer"]:
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
        heights = deck_profile(s, terrain, ends, requirements)
        # Approaches extend far enough to spread an artificial clearance lift over a gentle grade.
        ramp = float(np.clip(max(heights[0] - ends[0], heights[-1] - ends[1]) / 0.10, 12, 80))
        line = extended(axis, ramp)
        ss, xx = samples(line, 2)
        hh = np.interp(
            ss,
            np.r_[0, ramp + s, line.length],
            np.r_[float(ground(xx[:1])[0]), heights, float(ground(xx[-1:])[0])],
        )
        hh = np.maximum(hh, ground(xx)) + 0.02
        # Preserve a straight span, including the highest ground across the deck width.
        inside = (ss >= ramp) & (ss <= ramp + axis.length)
        hh[inside] = np.interp(ss[inside] - ramp, s, heights) + 0.02
        key = f"draft-bridge:{way['id']}"
        rail = way["tags"].get("railway") in RAILS
        material = (
            "gravel" if rail else ("sidewalk" if way["tags"].get("highway") in PATHS else "road")
        )
        # The full pipeline's closed, mitered wall prism is also a slab at deck width.
        meshes = [
            profile_wall(key + ":deck", xx, hh - THICKNESS, hh, thickness=width, material=material)
        ]
        for sign in (-1, 1):
            offset = shapely.offset_curve(line, sign * (width / 2 - 0.08))
            if offset.geom_type != "LineString" or offset.length < 0.5:
                continue
            _, side = samples(offset, 2)
            base = np.interp([line.project(shapely.Point(p)) for p in side], ss, hh)
            meshes.append(
                profile_wall(
                    key + f":rail:{sign}",
                    side,
                    base,
                    base + (1.6 if rail else 1.2),
                    thickness=0.18 if rail else 0.10,
                    material="railing",
                )
            )
        for k, station in enumerate(np.arange(15, axis.length - 8, 20)):
            p = axis.interpolate(float(station))
            top = float(np.interp(station, s, heights)) - THICKNESS
            bottom = float(ground(np.array([[p.x, p.y]]))[0])
            # Never place a pier in the crossed road's corridor.
            if top - bottom > 2 and not any(g.covers(p) for g in blocked):
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
            if ramp <= t <= ramp + axis.length or any(g.covers(p) for g in blocked):
                continue
            lateral = max(0.0, line.distance(p) - width / 2)
            target = float(np.interp(t, ss, hh)) - 0.02
            grid_pos[i, 1] = max(
                grid_pos[i, 1], target * (1 - lateral / 2) + grid_pos[i, 1] * lateral / 2
            )
        decks.append(Deck(way, line, ss, hh, meshes))
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
        matching = [
            d for d in decks if line.intersection(d.line.buffer(0.3)).length > line.length * 0.9
        ]
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
                round(float(np.interp(d.line.project(shapely.Point(p)), d.stations, d.heights)), 3)
            )
        heights.append(ys)
    graph["edgeHeights"] = heights
    return {**roads, "json": (json.dumps(graph, separators=(",", ":")) + "\n").encode()}
