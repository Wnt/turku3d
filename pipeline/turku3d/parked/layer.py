"""traffic_static (parked cars), built per listed L0 tile (turku3d.tile_layer).

A tile reads its own OSM clip and city street areas and places cars whose whole footprint lies
in the tile, at least SEAM_CLEARANCE from its edges, so cars of separately built tiles keep the
0.55 m spacing at every seam. Each candidate's draw is keyed by its bay and the seed, so a new
tile adds cars without reshuffling the others."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from shapely.geometry import box, mapping
from shapely.ops import unary_union

from turku3d import config as C
from turku3d import tile_layer
from turku3d.layers import PRIMITIVE_DTYPE, CollisionLayer, write_collision, write_visual
from turku3d.package.gltf import GlbMesh
from turku3d.package.manifest import _atomic_write
from turku3d.placement.clearance import car_box
from turku3d.placement.inputs import context, context_key, place_objects

from .models import ASSETS, model, palette
from .placement import Feature, place

SEED = 16
SEAM_CLEARANCE = 0.3  # half the 0.55 m car spacing, rounded up
FEATURE_MARGIN = 30.0  # roads, entrances and crossings this close mask bays in the tile


def read_features(path, bounds, osm=True):
    import pyarrow.parquet as pq
    from shapely import from_wkb

    result = []
    for row in pq.read_table(path).to_pylist():
        g = from_wkb(row["geometry_local"])
        if not g.is_empty and g.intersects(bounds):
            tags = json.loads(row["tags"]) if osm else row
            key = f"osm:{row['osm_type']}:{row['osm_id']}" if osm else f"city:{row['feature_key']}"
            result.append(Feature(key, g, tags))
    return result


def measure_clearance(cars, obstacles):
    """Record the clear driving distance ahead of and behind each car, other parked cars included."""
    keys = ("x", "y", "z", "yaw", "width", "height", "length")
    boxes = [car_box(*(car[k] for k in keys)) for car in cars]
    for i, car in enumerate(cars):
        others = obstacles.extended(boxes[:i] + boxes[i + 1 :])
        x, y, z, yaw, width, height, length = (car[k] for k in keys)
        for name, direction in (("clear_front_m", 1), ("clear_rear_m", -1)):
            car[name] = others.free_length(x, z, yaw, width, length, y, height, direction)


def write_tile(cars, directory, tile):
    """One mesh per car in the tile's visual and one solid box per car in its collision tile."""
    directory = Path(directory)
    if any(C.VisualTile.at(c["x"], c["z"]) != tile for c in cars):
        raise ValueError(f"{tile.id}: cars outside the tile")
    meshes, collision = [], {}
    for car in cars:
        x, y, z, yaw = (car[k] for k in ("x", "y", "z", "yaw"))
        pos, idx, normals, uv = model(car["model"])
        s, c = np.sin(yaw), np.cos(yaw)
        rotation = np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]])
        pos = pos @ rotation.T + [x, y, z] - tile.origin
        extras = {
            "layer": "traffic_static",
            "object_id": car["object_id"],
            "material_key": "parked_car_paint",
            "model": car["model"],
            "color": car["color"],
        }
        meshes.append(
            GlbMesh(
                car["object_id"],
                pos,
                idx,
                normals=normals @ rotation.T,
                uvs=uv,
                image=palette(car["model"], car["color"]),
                roughness=0.42,
                extras=extras,
            )
        )
        p = np.zeros(1, PRIMITIVE_DTYPE)
        p["kind"] = "box"
        p["center"] = [x, y + car["height"] / 2, z]
        p["half_extents"] = [car["width"] / 2, car["height"] / 2, car["length"] / 2]
        p["yaw"] = yaw
        p["material"] = 28  # metal, solid for both player and vehicle
        collision.setdefault(C.CollisionTile.at(x, z), []).append(p)
    if meshes:
        write_visual(directory / f"{tile.id}.vis.glb", meshes, root_translation=tile.origin)
    empty = (np.empty((0, 3), np.float64), np.empty((0, 3), np.uint32), np.empty(0, np.uint8))
    for cell, primitives in sorted(collision.items()):
        write_collision(
            directory / f"{cell.id}.col.npz", CollisionLayer(*empty, np.concatenate(primitives))
        )


def code_hash():
    base = Path(__file__).parents[1]
    return tile_layer.code_hash(
        Path(__file__).parent,
        base / "placement",
        base / "tile_layer.py",
        base / "config.py",
        base / "layers.py",
    )


def tile_key(tile):
    area = C.source_area(tile)
    paths = {"osm": tile_layer.osm(tile), "streets": tile_layer.wfs(tile, "GIS_Katualueet")}
    paths.update({f"asset:{p.name}": p for p in sorted(ASSETS.iterdir()) if p.is_file()})
    return {
        "sources": tile_layer.identities(paths),
        "placement": context_key(tile.bbox, "traffic_static", area),
        "seed": SEED,
    }


def build_tile(tile, staging):
    area = C.source_area(tile)
    inner = tile.bbox.buffered(-SEAM_CLEARANCE)
    bounds = box(*inner.as_tuple())
    features = read_features(tile_layer.osm(tile), bounds.buffer(FEATURE_MARGIN))
    city = read_features(
        tile_layer.wfs(tile, "GIS_Katualueet"), bounds.buffer(FEATURE_MARGIN), False
    )
    spawn_x, _, spawn_z = C.en_to_local(C.OFFICE_E, C.OFFICE_N)
    cars, excluded, roads, rejected = place(features, city, bounds, (spawn_x, spawn_z), SEED)
    before = len(cars)
    validator = context(tile.bbox, area, layer="traffic_static")
    if cars:
        cars = place_objects(
            cars,
            "traffic_static",
            tile.bbox,
            area,
            validator=validator,
            report=f"traffic_static/{tile.id}",
        )
    cars = [c for c in cars if C.VisualTile.at(c["x"], c["z"]) == tile]
    measure_clearance(cars, validator.obstacles)
    write_tile(cars, staging, tile)
    return {
        "records": cars,
        "metrics": {
            "rejected": rejected,
            "ground_rejected": before - len(cars),
            "exclusions": mapping(excluded),
            "roads": mapping(roads),
        },
    }


def combine(directory, tiles):
    cars = [car for entry in tiles.values() for car in entry["records"]]
    rejected = {}
    for entry in tiles.values():
        for reason, count in entry["metrics"]["rejected"].items():
            rejected[reason] = rejected.get(reason, 0) + count

    def union(name):
        from shapely.geometry import shape

        return mapping(unary_union([shape(e["metrics"][name]) for e in tiles.values()]))

    report = {
        "seed": SEED,
        "cars": cars,
        "rejected": rejected,
        "ground_rejected": sum(e["metrics"]["ground_rejected"] for e in tiles.values()),
        "count": len(cars),
        "exclusions": union("exclusions"),
        "roads": union("roads"),
    }
    data = (json.dumps(report, indent=2) + "\n").encode()
    _atomic_write(Path(directory) / "placements.json", data)
    return {
        "seed": SEED,
        "cars": len(cars),
        "models": sorted({c["model"] for c in cars}),
        "placements_sha256": tile_layer.digest_bytes(data),
    }


PRODUCER = tile_layer.Producer("traffic_static", code_hash, tile_key, build_tile, combine)
