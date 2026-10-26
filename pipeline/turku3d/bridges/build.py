"""Bridge footprints from city streets, semantics from OSM, elevations from lidar."""

from __future__ import annotations

import json
from dataclasses import dataclass

import numpy as np
from scipy.spatial import cKDTree
from shapely import contains_xy
from shapely.geometry import LineString, Point, box
from shapely.ops import triangulate, unary_union

from turku3d import config as C
from turku3d.structures.geometry import Faces, planar_uv, profile_wall

BOUNDS = C.BBoxLocal(0, 0, 500, 500)


@dataclass
class Crossing:
    name: str
    footprint: object
    axis: LineString
    road: bool
    source_ids: list[str]


def crossings(city, osm):
    """City bridge outlines win; OSM adds missing paths and bridge meaning."""
    region = box(*BOUNDS.as_tuple())
    roads = {}
    for row in osm.itertuples():
        tags = json.loads(row.tags)
        if tags.get("bridge") != "yes" or not row.geometry_local.intersects(region):
            continue
        if tags.get("highway") == "steps":
            continue  # Stair flights belong to structures.
        name = tags.get("name:fi") or tags.get("name") or f"osm:{row.osm_id}"
        roads.setdefault(name, []).append((row, tags))
    output = []
    for name, ways in sorted(roads.items()):
        road_ways = [w for w in ways if w[1].get("highway") not in ("footway", "path")]
        axis_row = max(road_ways or ways, key=lambda w: w[0].geometry_local.length)[0]
        axis = axis_row.geometry_local.intersection(region)
        if axis.geom_type == "MultiLineString":
            axis = max(axis.geoms, key=lambda part: part.length)
        if axis.geom_type != "LineString" or axis.length < 0.5:
            continue
        matching = city[
            (city.Katuosalaji == "Silta") & city.Kadunnimi.fillna("").str.lower().eq(name.lower())
        ]
        city_parts = [
            r.geometry_local for r in matching.itertuples() if r.geometry_local.intersects(region)
        ]
        if city_parts:
            footprint = unary_union(city_parts).intersection(region)
            ids = [r.feature_key for r in matching.itertuples()]
        else:
            width = float(ways[0][1].get("width", 2.0))
            footprint = axis.buffer(width / 2, cap_style="flat").intersection(region)
            ids = []
        if footprint.is_empty:
            continue
        road = any(
            w[1].get("highway") in ("secondary", "primary", "tertiary", "residential") for w in ways
        )
        output.append(Crossing(name, footprint, axis, road, ids + [str(w[0].osm_id) for w in ways]))
    named = [c for c in output if not c.name.startswith("osm:")]
    return [
        c
        for c in output
        if not c.name.startswith("osm:")
        or not any(n.footprint.buffer(1).covers(c.axis) for n in named)
    ]


def lidar_points(paths):
    import laspy

    points, classes = [], []
    for path in paths:
        with laspy.open(path) as reader:
            for chunk in reader.chunk_iterator(1_000_000):
                x = np.asarray(chunk.x) - C.ORIGIN_E
                z = C.ORIGIN_N - np.asarray(chunk.y)
                cls = np.asarray(chunk.classification)
                keep = (x >= -20) & (x <= 520) & (z >= -20) & (z <= 520)
                keep &= np.isin(cls, (2, 9, 12, 17))
                if keep.any():
                    points.append(np.column_stack((x[keep], np.asarray(chunk.z)[keep], z[keep])))
                    classes.append(cls[keep])
    if not points:
        raise ValueError("no bridge, non-ground or water lidar points")
    return np.concatenate(points), np.concatenate(classes)


def profile(crossing, points, classes):
    """Sample a smooth longitudinal profile, preferring bridge-class returns."""
    axis = crossing.axis
    stations = np.linspace(0, axis.length, max(3, int(axis.length / 4) + 1))
    xy = np.array([axis.interpolate(float(s)).coords[0][:2] for s in stations])
    xmin, zmin, xmax, zmax = crossing.footprint.bounds
    within = (points[:, 0] >= xmin - 1) & (points[:, 0] <= xmax + 1)
    within &= (points[:, 2] >= zmin - 1) & (points[:, 2] <= zmax + 1)
    candidates = np.flatnonzero(within)
    within[candidates] = contains_xy(
        crossing.footprint.buffer(1), points[candidates, 0], points[candidates, 2]
    )
    source = 17 if np.count_nonzero(within & (classes == 17)) >= 20 else 12
    selected = points[within & (classes == source)]
    tree = cKDTree(selected[:, [0, 2]]) if len(selected) >= 12 else None
    heights = []
    for p in xy:
        nearby = tree.query_ball_point(p, 2.5) if tree is not None else []
        if len(nearby) < 4 and tree is not None:
            nearby = tree.query_ball_point(p, 5)
        heights.append(float(np.median(selected[nearby, 1])) if len(nearby) >= 4 else np.nan)
    heights = np.asarray(heights, float)
    if not np.isfinite(heights).all() or np.ptp(heights) > 0.3 * crossing.axis.length:
        if np.ptp(heights) > 0.3 * crossing.axis.length:
            heights[:] = np.nan
        near = (points[:, 0] >= xmin - 8) & (points[:, 0] <= xmax + 8)
        near &= (points[:, 2] >= zmin - 8) & (points[:, 2] <= zmax + 8)
        abutment = points[near & (classes == 2)]
        if len(abutment) < 4:
            raise ValueError(f"{crossing.name}: no abutment ground returns")
        support = cKDTree(abutment[:, [0, 2]])
        for endpoint in (0, -1):
            if np.isfinite(heights[endpoint]):
                continue
            nearby = support.query_ball_point(xy[endpoint], 1.5)
            if len(nearby) < 4:
                nearby = support.query_ball_point(xy[endpoint], 5)
            if len(nearby) < 4:
                raise ValueError(f"{crossing.name}: unsupported abutment")
            heights[endpoint] = float(np.median(abutment[nearby, 1]))
        water = points[classes == 9]
        if len(water):
            river_height = float(np.median(water[:, 1]))
            for endpoint in (0, -1):
                if heights[endpoint] <= river_height + 0.5:
                    heights[endpoint] = np.nan
        if np.isfinite(heights).sum() == 1 and crossing.axis.length <= 6:
            heights[:] = heights[np.isfinite(heights)][0]
        known = np.flatnonzero(np.isfinite(heights))
        if not len(known):
            raise ValueError(f"{crossing.name}: both abutments lack a deck elevation")
        heights = np.interp(stations, stations[known], heights[known])
        source = "class12_and_abutments" if len(selected) >= 12 else "abutments"
    # Local lidar returns include edges and furniture; smooth only the profile, not its endpoints.
    if len(heights) > 2:
        heights[1:-1] = (heights[:-2] + 2 * heights[1:-1] + heights[2:]) / 4
    return stations, heights, source


def _triangles(polygon):
    parts = list(polygon.geoms) if polygon.geom_type == "MultiPolygon" else [polygon]
    for part in parts:
        for rough in triangulate(part):
            clipped = rough.intersection(part)
            fragments = list(clipped.geoms) if clipped.geom_type == "MultiPolygon" else [clipped]
            for fragment in fragments:
                if fragment.geom_type != "Polygon" or fragment.area < 1e-5:
                    continue
                verts = np.asarray(fragment.exterior.coords[:-1], float)
                for i in range(1, len(verts) - 1):
                    yield verts[[0, i, i + 1]]


def _box(object_id, centre, half, material):
    cx, cy, cz = centre
    hx, hy, hz = half
    vertices = np.array(
        [
            [cx + sx * hx, cy + sy * hy, cz + sz * hz]
            for sx in (-1, 1)
            for sy in (-1, 1)
            for sz in (-1, 1)
        ]
    )
    faces = Faces()
    for ids in ((0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)):
        faces.face(vertices[list(ids)])
    return faces.mesh(object_id, material)


def geometry(crossing, points, classes):
    stations, heights, source = profile(crossing, points, classes)

    def elevation(xz):
        s = crossing.axis.project(Point(float(xz[0]), float(xz[1])))
        return float(np.interp(s, stations, heights))

    road_part = (
        crossing.footprint.intersection(crossing.axis.buffer(4, cap_style="flat"))
        if crossing.road
        else None
    )
    surfaces = (
        [("road", road_part, 0.0), ("sidewalk", crossing.footprint.difference(road_part), 0.12)]
        if crossing.road
        else [("sidewalk", crossing.footprint, 0.0)]
    )
    meshes = []
    for material_key, polygon, lift in surfaces:
        deck = Faces()
        for triangle in _triangles(polygon):
            xyz = np.column_stack(
                (triangle[:, 0], [elevation(p) + lift for p in triangle], triangle[:, 1])
            )
            if np.cross(xyz[1] - xyz[0], xyz[2] - xyz[0])[1] < 0:
                xyz = xyz[[0, 2, 1]]
            deck.face(xyz, planar_uv(xyz))
        if deck.indices:
            meshes.append(
                deck.mesh(
                    f"bridge:{crossing.name}:{material_key}",
                    "bridge_deck",
                    metadata={"kind": "bridge_deck", "material_key": material_key},
                )
            )
    if road_part is not None:
        road_boundary = road_part.boundary
        road_direction = (
            np.asarray(crossing.axis.coords[-1])[:2] - np.asarray(crossing.axis.coords[0])[:2]
        )
        road_direction /= np.linalg.norm(road_direction)
        edges = (
            list(road_boundary.geoms)
            if road_boundary.geom_type == "MultiLineString"
            else [road_boundary]
        )
        for edge in edges:
            coords = np.asarray(edge.coords)
            for k, (a, b) in enumerate(zip(coords[:-1], coords[1:], strict=True)):
                length = np.linalg.norm(b - a)
                if length < 1 or abs((b - a) @ road_direction) / length < 0.75:
                    continue
                low = [elevation(a), elevation(b)]
                meshes.append(
                    profile_wall(
                        f"bridge:{crossing.name}:kerb:{len(meshes)}:{k}",
                        [a, b],
                        low,
                        np.asarray(low) + 0.12,
                        thickness=0.12,
                        material="curb",
                    )
                )
    boundary = crossing.footprint.boundary
    lines = list(boundary.geoms) if boundary.geom_type == "MultiLineString" else [boundary]
    axis_direction = (
        np.asarray(crossing.axis.coords[-1])[:2] - np.asarray(crossing.axis.coords[0])[:2]
    )
    axis_direction /= np.linalg.norm(axis_direction)
    for line_index, line in enumerate(lines):
        coordinates = np.asarray(line.coords, float)
        for segment_index, (a, b) in enumerate(zip(coordinates[:-1], coordinates[1:], strict=True)):
            direction = b - a
            length = np.linalg.norm(direction)
            if length < 1 or abs(direction @ axis_direction) / length < 0.75:
                continue
            base = np.array([elevation(a), elevation(b)])
            meshes.append(
                profile_wall(
                    f"bridge:{crossing.name}:rail:{line_index}:{segment_index}",
                    np.array([a, b]),
                    base,
                    base + 1.2,
                    thickness=0.08,
                    material="railing",
                )
            )
    water = points[classes == 9]
    water_height = float(np.median(water[:, 1])) if len(water) else None
    if water_height is not None:
        for i, fraction in enumerate((1 / 3, 2 / 3)):
            xz = np.asarray(
                crossing.axis.interpolate(crossing.axis.length * fraction).coords[0][:2]
            )
            top = elevation(xz) - 0.45
            if top > water_height + 0.5 and crossing.footprint.covers(Point(xz)):
                meshes.append(
                    _box(
                        f"bridge:{crossing.name}:pier:{i}",
                        (xz[0], (top + water_height) / 2, xz[1]),
                        (0.45, (top - water_height) / 2, 0.45),
                        "wall",
                    )
                )
    report = {
        "name": crossing.name,
        "source_ids": crossing.source_ids,
        "deck_source_class": source,
        "deck_height_range_m": [float(heights.min()), float(heights.max())],
        "deck_area_m2": float(crossing.footprint.area),
        "road": crossing.road,
        "meshes": len(meshes),
    }
    return meshes, report
