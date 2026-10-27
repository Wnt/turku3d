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
    vehicle = curbside_position(
        collision["ground"], blockers, feet, street_line, vehicle_roads(feet)
    )
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


VEHICLE_HIGHWAYS = {
    "primary",
    "secondary",
    "tertiary",
    "unclassified",
    "residential",
    "living_street",
    "service",
}


def vehicle_roads(feet, radius=40.0):
    """OSM carriageways near the start as (centreline, half width) pairs.

    The ground classes alone miss them: the office front is a pedestrian street, and the nearest
    motor-vehicle lanes are service roads paved like the sidewalk.
    """
    import geopandas as gpd

    roads = []
    for source in osm_sources():
        frame = gpd.read_parquet(source)
        if "highway" not in frame:
            continue
        for _, row in frame[frame["highway"].isin(VEHICLE_HIGHWAYS)].iterrows():
            tags = json.loads(row["tags"])
            line = row["geometry_local"]
            if line.geom_type != "LineString" or line.distance(Point(feet[[0, 2]])) > radius:
                continue
            if tags.get("access") in ("private", "no") or tags.get("motor_vehicle") == "no":
                continue
            width = float(tags.get("width", 3.5 if row["highway"] == "service" else 6.0))
            roads.append((line, width / 2))
    return roads


def curbside_position(
    ground, blockers, feet, street_line, roads=(), max_distance=25.0, min_distance=3.0
):
    """Park the player's car on a carriageway next to the start.

    A game start wants the car within a few steps of the player; legal parking may be far away.
    The car body must lie on flat ground that is road material or an OSM carriageway, clear of
    footprints and the player. It is aligned with its carriageway, or with the office street.
    """
    road = [m.id for m in table() if m.name == "road"]
    solid = [m.id for m in table() if m.cls == "solid" and "NOWALK" not in m.flags]
    coords = np.array(street_line.coords)
    street = coords[-1] - coords[0]
    street /= np.linalg.norm(street)
    origin = feet[[0, 2]]
    flat_ground, road_ground, candidates = [], [], []
    for layer in ground.values():
        triangles = layer.positions[layer.indices]
        centers = triangles.mean(axis=1)
        normals = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
        distances = np.linalg.norm(centers[:, [0, 2]] - origin, axis=1)
        near = (normals[:, 1] > np.linalg.norm(normals, axis=1) * 0.98) & (
            distances < max_distance + 4
        )
        on_road = near & np.isin(layer.material, road)
        flat_ground.extend(
            Polygon(t[:, [0, 2]]) for t in triangles[near & np.isin(layer.material, solid)]
        )
        road_ground.extend(Polygon(t[:, [0, 2]]) for t in triangles[on_road])
        keep = on_road & (distances >= min_distance) & (distances <= max_distance)
        candidates.extend(
            (d, c[[0, 2]], street) for d, c in zip(distances[keep], centers[keep], strict=True)
        )
    for line, _ in roads:
        for step in np.arange(0.0, line.length, 1.0):
            point = np.array(line.interpolate(step).coords[0])
            ahead = np.array(line.interpolate(min(step + 1.0, line.length)).coords[0])
            behind = np.array(line.interpolate(max(step - 1.0, 0.0)).coords[0])
            distance = np.linalg.norm(point - origin)
            if min_distance <= distance <= max_distance:
                tangent = (ahead - behind) / np.linalg.norm(ahead - behind)
                candidates.append((distance, point, tangent))
    if not flat_ground:
        return None
    lanes = unary_union(
        road_ground + [line.buffer(half_width, cap_style="flat") for line, half_width in roads]
    )
    drivable = lanes.buffer(0.05).intersection(unary_union(flat_ground).buffer(0.05))
    for _, point, tangent in sorted(candidates, key=lambda row: row[0]):
        normal = np.array([-tangent[1], tangent[0]])
        body = Polygon(
            [
                point + a * tangent + b * normal
                for a, b in ((2.5, 1.1), (-2.5, 1.1), (-2.5, -1.1), (2.5, -1.1))
            ]
        )
        if not drivable.covers(body) or blockers.intersects(body.buffer(0.5)):
            continue
        if body.distance(Point(origin)) < min_distance - 1.0:
            continue
        return {
            "position": [float(point[0]), ground_height(ground, point), float(point[1])],
            "yaw": math.atan2(-tangent[1], tangent[0]),
            "source": "curbside road next to the start",
        }
    return None


def ground_height(ground, point):
    """Highest ground triangle under an engine-xz point."""
    best = None
    for layer in ground.values():
        triangles = layer.positions[layer.indices]
        for t in triangles[np.linalg.norm(triangles.mean(axis=1)[:, [0, 2]] - point, axis=1) < 3]:
            if Polygon(t[:, [0, 2]]).buffer(1e-6).covers(Point(point)):
                best = max(best if best is not None else -1e9, float(t[:, 1].mean()))
    return best


def osm_sources():
    return sorted((C.data_root() / "raw/osm/work-3x3").glob("*.parquet"))


def parking_position(ground, blockers, feet):
    """Use mapped public parking, never the no-parking street in front of LR15."""
    import geopandas as gpd

    choices = []
    for source in osm_sources():
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
