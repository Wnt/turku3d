"""Select a street-side office start from published facades and walkable ground."""

import json
import math

import numpy as np
from shapely import contains_xy
from shapely.geometry import LineString, Point, Polygon, shape
from shapely.ops import unary_union

from turku3d import config as C
from turku3d.col.materials import table


def derive_spawn(root, collision):
    footprints = root / "buildings/footprints.geojson"
    facades = root / "buildings/facades.geojson"
    if not footprints.exists() or not facades.exists() or not collision.get("ground"):
        return None
    inventory = json.loads(footprints.read_text())
    if inventory.get("turku3d_coords") != "engine-xz":
        raise ValueError("spawn requires engine-xz footprints")
    features = inventory["features"]
    address = Point(C.OFFICE_E - C.ORIGIN_E, C.ORIGIN_N - C.OFFICE_N)
    office = min(features, key=lambda f: shape(f["geometry"]).distance(address))
    if shape(office["geometry"]).distance(address) > 20:
        return None
    blockers = unary_union([shape(f["geometry"]) for f in features])
    street = []
    for f in json.loads(facades.read_text())["features"]:
        p = f["properties"]
        if p["building_id"] != office["properties"]["building_id"] or not p["street_facing"]:
            continue
        line = LineString(
            [(e - C.ORIGIN_E, C.ORIGIN_N - n) for e, n in f["geometry"]["coordinates"]]
        )
        street.append((line, p))
    if not street:
        raise ValueError("office has no published street-facing facade")
    # Sort cheap numeric candidates first; stop at the nearest valid triangle instead of
    # running footprint predicates for every point in the neighbourhood.
    candidates = []
    solid = [m.id for m in table() if m.cls == "solid" and "NOWALK" not in m.flags]
    for layer in collision["ground"].values():
        triangles = layer.positions[layer.indices]
        normals = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
        walkable = normals[:, 1] > np.linalg.norm(normals, axis=1) * 0.85
        walkable &= np.isin(layer.material, solid)
        centers = triangles.mean(axis=1)
        distances = np.linalg.norm(centers[:, [0, 2]] - [address.x, address.y], axis=1)
        candidates.extend(
            zip(
                distances[walkable & (distances < 45)],
                centers[walkable & (distances < 45)],
                strict=True,
            )
        )
    selected = None
    for _, center in sorted(candidates, key=lambda row: row[0]):
        point = Point(center[[0, 2]])
        if blockers.distance(point) < 1.2:
            continue
        line, facade = min(street, key=lambda item: item[0].distance(point))
        target = line.interpolate(line.project(point))
        delta = np.array([point.x - target.x, point.y - target.y])
        if delta @ np.array(facade["normal"])[[0, 2]] < 0.8 * np.linalg.norm(delta):
            continue
        if line.distance(point) > 12 or blockers.intersects(
            LineString([point, (target.x + delta[0] * 0.02, target.y + delta[1] * 0.02)])
        ):
            continue
        selected = center, target, facade
        break
    if selected is None:
        raise ValueError("no walkable street-side ground outside office footprints")
    feet, target, facade = selected
    heading = math.degrees(math.atan2(target.x - feet[0], feet[2] - target.y)) % 360
    street_line = min(street, key=lambda item: item[0].distance(Point(feet[[0, 2]])))[0]
    vehicle = curbside_position(collision["ground"], blockers, feet, street_line)
    if vehicle is None:
        vehicle = parking_position(collision["ground"], blockers, feet)
    if vehicle is None:
        raise ValueError("no mapped public parking with ground and vehicle clearance near office")
    return {
        "position": feet.tolist(),
        "headingDeg": heading,
        "vehicle": vehicle,
        "building_id": office["properties"]["building_id"],
        "facade_id": facade["object_id"],
        "source": "ground-triangle-centroids/street-facing-facade",
        "footprints": [f["geometry"] for f in features],
    }


def curbside_position(ground, blockers, feet, street_line, max_distance=25.0, min_distance=3.0):
    """Park the player's car on the road next to the start, aligned with the office street.

    A game start wants the car within a few steps of the player; legal parking may be far away.
    The car body must lie on flat road triangles, clear of footprints and the player.
    """
    road = [m.id for m in table() if m.name == "road"]
    coords = np.array(street_line.coords)
    tangent = coords[-1] - coords[0]
    tangent /= np.linalg.norm(tangent)
    normal = np.array([-tangent[1], tangent[0]])
    origin = feet[[0, 2]]
    surfaces = []
    candidates = []
    for layer in ground.values():
        triangles = layer.positions[layer.indices]
        centers = triangles.mean(axis=1)
        normals = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
        distances = np.linalg.norm(centers[:, [0, 2]] - origin, axis=1)
        flat = normals[:, 1] > np.linalg.norm(normals, axis=1) * 0.98
        on_road = np.isin(layer.material, road) & flat & (distances < max_distance + 4)
        surfaces.extend(Polygon(t[:, [0, 2]]) for t in triangles[on_road])
        keep = on_road & (distances >= min_distance) & (distances <= max_distance)
        candidates.extend(zip(distances[keep], centers[keep], strict=True))
    if not surfaces:
        return None
    surface = unary_union(surfaces).buffer(0.05)
    for _, center in sorted(candidates, key=lambda row: row[0]):
        point = center[[0, 2]]
        body = Polygon(
            [
                point + a * tangent + b * normal
                for a, b in ((2.5, 1.1), (-2.5, 1.1), (-2.5, -1.1), (2.5, -1.1))
            ]
        )
        if not surface.covers(body) or blockers.intersects(body.buffer(0.5)):
            continue
        if body.distance(Point(origin)) < min_distance - 1.0:
            continue
        return {
            "position": center.tolist(),
            "yaw": math.atan2(-tangent[1], tangent[0]),
            "source": "curbside road next to the start",
        }
    return None


def parking_sources():
    return sorted((C.data_root() / "raw/osm/work-3x3").glob("*.parquet"))


def parking_position(ground, blockers, feet):
    """Use mapped public parking, never the no-parking street in front of LR15."""
    import geopandas as gpd

    choices = []
    for source in parking_sources():
        frame = gpd.read_parquet(source)
        for _, row in frame[frame["amenity"] == "parking"].iterrows():
            tags = json.loads(row["tags"])
            area = row["geometry_local"]
            if area.geom_type not in ("Polygon", "MultiPolygon") or tags.get("access") in (
                "private",
                "no",
                "customers",
            ):
                continue
            if area.distance(Point(feet[[0, 2]])) > 200:
                continue
            rectangle = np.array(area.minimum_rotated_rectangle.exterior.coords)[:4]
            edges = np.roll(rectangle, -1, axis=0) - rectangle
            tangent = edges[np.argmax(np.linalg.norm(edges, axis=1))]
            tangent /= np.linalg.norm(tangent)
            if tags.get("orientation") == "perpendicular":
                tangent = np.array([-tangent[1], tangent[0]])
            normal = np.array([-tangent[1], tangent[0]])
            for layer in ground.values():
                triangles = layer.positions[layer.indices]
                centers = triangles.mean(axis=1)
                normals = np.cross(
                    triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0]
                )
                near = np.linalg.norm(centers[:, [0, 2]] - feet[[0, 2]], axis=1) < 200
                near &= normals[:, 1] > np.linalg.norm(normals, axis=1) * 0.98
                near &= np.isin(
                    layer.material,
                    [m.id for m in table() if m.cls == "solid" and "NOWALK" not in m.flags],
                )
                near &= contains_xy(area, centers[:, 0], centers[:, 2])
                ordered = centers[near]
                order = np.argsort(np.linalg.norm(ordered[:, [0, 2]] - feet[[0, 2]], axis=1))
                for center in ordered[order]:
                    point = center[[0, 2]]
                    if not area.contains(Point(point)):
                        continue
                    body = Polygon(
                        [
                            point + a * tangent + b * normal
                            for a, b in ((2.5, 1.1), (-2.5, 1.1), (-2.5, -1.1), (2.5, -1.1))
                        ]
                    )
                    if not area.covers(body) or blockers.intersects(body):
                        continue
                    choices.append(
                        (
                            np.linalg.norm(point - feet[[0, 2]]),
                            {
                                "position": center.tolist(),
                                "yaw": math.atan2(-tangent[1], tangent[0]),
                                "osm_id": int(row["osm_id"]),
                                "source": "OSM amenity=parking",
                                "tags": tags,
                            },
                        )
                    )
                    break
    return min(choices, key=lambda item: item[0])[1] if choices else None
