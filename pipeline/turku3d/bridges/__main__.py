"""Publish the four-L0 bridge layer from read-only city, OSM and lidar inputs."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from pathlib import Path

import geopandas as gpd
import numpy as np
from shapely.geometry import MultiPoint

from turku3d import config as C
from turku3d.col.materials import mat_id
from turku3d.layers import CollisionLayer, write_collision, write_manifest, write_visual
from turku3d.package.gltf import GlbMesh
from turku3d.structures.inputs import lidar_paths

from .build import BOUNDS, crossings, geometry, lidar_points


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    city_path = C.raw_dir("wfs") / "work-3x3/GIS_Katualueet.parquet"
    osm_path = C.raw_dir("osm") / "work-3x3/work-3x3-261024.parquet"
    laz = lidar_paths(BOUNDS)
    city, osm = gpd.read_parquet(city_path), gpd.read_parquet(osm_path)
    found = crossings(city, osm)
    if not found or not any(c.name == "Auransilta" for c in found):
        raise ValueError("Auransilta missing from mapped bridge inputs")
    points, classes = lidar_points(laz)
    meshes, reports = [], []
    for crossing in found:
        built, report = geometry(crossing, points, classes)
        meshes.extend(built)
        reports.append(report)
    out = C.work_root() / "build/layers/bridges"
    out.mkdir(parents=True, exist_ok=True)
    collisions, visuals = defaultdict(list), defaultdict(list)
    for mesh in meshes:
        centre = MultiPoint(mesh.positions[:, [0, 2]]).convex_hull.centroid
        collisions[C.CollisionTile.at(centre.x, centre.y).id].append(mesh)
        visuals[C.VisualTile.at(centre.x, centre.y).id].append(mesh)
    files = []
    for tile, parts in sorted(collisions.items()):
        positions = np.concatenate([p.positions for p in parts])
        offsets = np.cumsum([0] + [len(p.positions) for p in parts[:-1]])
        indices = np.concatenate(
            [p.indices + offset for p, offset in zip(parts, offsets, strict=True)]
        ).astype(np.uint32)
        materials = np.concatenate(
            [np.full(len(p.indices), mat_id(p.material), np.uint8) for p in parts]
        )
        name = f"{tile}.col.npz"
        write_collision(out / name, CollisionLayer(positions, indices, materials))
        files.append(name)
    for tile, parts in sorted(visuals.items()):
        name = f"{tile}.vis.glb"
        write_visual(
            out / name,
            [
                GlbMesh(
                    p.object_id,
                    p.positions,
                    p.indices,
                    uvs=p.uvs,
                    extras={
                        "layer": "bridges",
                        "object_id": p.object_id,
                        "material_key": p.metadata.get(
                            "material_key",
                            "metal_railing" if p.material == "railing" else "concrete_wall",
                        ),
                    },
                )
                for p in parts
            ],
            root_extras={"layer": "bridges"},
        )
        files.append(name)
    code = hashlib.sha256(
        "".join(digest(p) for p in sorted(Path(__file__).parent.glob("*.py"))).encode()
    ).hexdigest()
    manifest = write_manifest(
        out,
        inputs={str(p): digest(p) for p in [city_path, osm_path, *laz]},
        params={"bounds": list(BOUNDS.as_tuple()), "seams": "objects"},
        code=code,
        tiles=files,
    )
    report = {
        "crossings": reports,
        "collision_tiles": len(collisions),
        "visual_tiles": len(visuals),
        "triangles": sum(len(p.indices) for p in meshes),
        "manifest": str(out / "MANIFEST.json"),
    }
    report_path = C.work_root() / "reports/bridges/report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    return 0 if manifest["tiles"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
