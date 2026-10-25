"""Publish traffic_static from cached OSM, city street areas and measured ground heights."""

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq
from shapely import from_wkb
from shapely.geometry import box, mapping

from turku3d import config as C
from turku3d.layers import (
    PRIMITIVE_DTYPE,
    CollisionLayer,
    layer_root,
    write_collision,
    write_manifest,
    write_visual,
)
from turku3d.package.gltf import GlbMesh

from .models import ASSETS, model, palette
from .placement import Feature, footprint, place


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_features(path, bounds, osm=True):
    result = []
    for row in pq.read_table(path).to_pylist():
        g = from_wkb(row["geometry_local"])
        if not g.is_empty and g.intersects(bounds):
            tags = json.loads(row["tags"]) if osm else row
            key = f"osm:{row['osm_type']}:{row['osm_id']}" if osm else f"city:{row['feature_key']}"
            result.append(Feature(key, g, tags))
    return result


def heights(cars, bounds, inputs):
    import laspy
    from scipy.interpolate import LinearNDInterpolator
    from scipy.spatial import cKDTree

    points = []
    for tile in C.laz_tiles_for_bbox(C.BBoxLocal(*bounds.bounds).buffered(30)):
        path = C.raw_dir("laz") / "maanpinta" / f"{tile.name}.laz"
        if not path.exists():
            continue
        inputs[str(path)] = sha(path)
        laz = laspy.read(path)
        x, y, z = C.en_to_local(np.asarray(laz.x), np.asarray(laz.y), np.asarray(laz.z))
        points.append(np.column_stack((x, z, y)))
    points = np.concatenate(points)
    tin = LinearNDInterpolator(points[:, :2], points[:, 2])
    tree = cKDTree(points[:, :2])
    kept = []
    for car in cars:
        ring = np.array(
            footprint(car["x"], car["z"], car["yaw"], car["width"], car["length"]).exterior.coords
        )[:4]
        h = tin(ring)
        distance, _ = tree.query(ring)
        if not np.isfinite(h).all() or distance.max() > 20 or np.ptp(h) > 0.35:
            continue
        car["y"] = float(np.mean(h))
        car["ground_range_m"] = float(np.ptp(h))
        kept.append(car)
    return kept


def publish(cars, directory, inputs, seed):
    collision, visual = {}, {}
    for car in cars:
        x, y, z, yaw = (car[k] for k in ("x", "y", "z", "yaw"))
        tile = C.VisualTile.at(x, z)
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
        visual.setdefault(tile, []).append(
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
    names = []
    for tile, meshes in sorted(visual.items()):
        name = f"{tile.id}.vis.glb"
        write_visual(directory / name, meshes, root_translation=tile.origin)
        names.append(name)
    for tile, primitives in sorted(collision.items()):
        name = f"{tile.id}.col.npz"
        write_collision(
            directory / name,
            CollisionLayer(
                np.empty((0, 3), np.float64),
                np.empty((0, 3), np.uint32),
                np.empty(0, np.uint8),
                np.concatenate(primitives),
            ),
        )
        names.append(name)
    code = hashlib.sha256(
        b"".join(p.read_bytes() for p in sorted(Path(__file__).parent.glob("*.py")))
    ).hexdigest()
    return write_manifest(
        directory,
        inputs=inputs,
        params={
            "seed": seed,
            "seams": "objects",
            "cars": len(cars),
            "models": sorted({c["model"] for c in cars}),
        },
        code=code,
        tiles=names,
    )


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--osm", type=Path)
    ap.add_argument("--seed", type=int, default=16)
    ap.add_argument("--output", type=Path, default=layer_root() / "traffic_static")
    args = ap.parse_args()
    osm = args.osm or sorted((C.raw_dir("osm") / "work-3x3").glob("*.parquet"))[-1]
    city_path = C.raw_dir("wfs") / "work-3x3/GIS_Katualueet.parquet"
    bounds = box(*C.OFFICE_BLOCK.core.as_tuple())
    features = read_features(osm, bounds.buffer(30))
    city = read_features(city_path, bounds.buffer(30), False)
    spawn_x, _, spawn_z = C.en_to_local(C.OFFICE_E, C.OFFICE_N)
    cars, excluded, roads, rejected = place(features, city, bounds, (spawn_x, spawn_z), args.seed)
    inputs = {str(p): sha(p) for p in [osm, city_path, *sorted(ASSETS.iterdir())] if p.is_file()}
    before = len(cars)
    cars = heights(cars, bounds, inputs)
    if not cars:
        raise ValueError("No safe parking placements found")
    args.output.mkdir(parents=True, exist_ok=True)
    report = {
        "seed": args.seed,
        "cars": cars,
        "rejected": rejected,
        "ground_rejected": before - len(cars),
        "count": len(cars),
        "exclusions": mapping(excluded),
        "roads": mapping(roads),
    }
    (args.output / "placements.json").write_text(json.dumps(report, indent=2) + "\n")
    inputs["placements.json"] = sha(args.output / "placements.json")
    publish(cars, args.output, inputs, args.seed)
    print(
        json.dumps(
            {
                "cars": len(cars),
                "rejected": rejected,
                "ground_rejected": before - len(cars),
                "output": str(args.output),
            }
        )
    )


if __name__ == "__main__":
    main()
