"""Landmarks layer: allowlisted objects meshed from full-density lidar (see README.md).

One build per listed L0 tile (turku3d.tile_layer). A landmark belongs to the tile its
footprint mostly lies in, like a building. The layer's manifest names the buildings-layer
footprints it replaces (`params.replaces`); the assembler drops those shells.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict
from pathlib import Path

import numpy as np
import shapely
from shapely.geometry import Polygon

from turku3d import config as C
from turku3d import tile_layer
from turku3d.buildings.tiles import ownership_point
from turku3d.col.materials import mat_id
from turku3d.landmarks import allowlist, mesh, textures
from turku3d.landmarks.allowlist import Landmark
from turku3d.layers import CollisionLayer
from turku3d.package.gltf import GlbMesh
from turku3d.package.manifest import _atomic_write

LAYER = "landmarks"
REACH_M = 1.0  # Eaves and gutters overhang the surveyed footprint.
GROUND_RING_M = 3.0
BASE_BELOW_GROUND_M = 0.5
ROOF_MIN_NY = 0.2  # Steeper surfaces take the wall material, flatter ones the ortho.
ORTHO_RESOLUTION_M = 0.1


def code_hash():
    base = Path(__file__).parents[1]
    return tile_layer.code_hash(
        Path(__file__).parent,
        base / "tile_layer.py",
        base / "layers.py",
        base / "config.py",
        base / "package/gltf.py",
        base / "buildings/inputs.py",
        base / "buildings/tiles.py",
    )


def ortho_vrt(tile) -> Path:
    return C.raw_dir("ortho") / "ortho2022to" / f"{C.source_area(tile)}.vrt"


def laz_paths(bbox: C.BBoxLocal) -> list[Path]:
    return [C.raw_dir("laz") / "pistepilvi" / f"{t.name}.laz" for t in C.laz_tiles_for_bbox(bbox)]


def tile_key(tile):
    paths = {
        "allowlist": allowlist.DEFAULT_PATH,
        "kanta": tile_layer.wfs(tile, "kanta_Rakennus"),
        "gis": tile_layer.wfs(tile, "GIS_Rakennukset"),
        "ortho": ortho_vrt(tile),
    }
    for path in laz_paths(C.tile_input_area(tile).core):
        paths[path.name] = path
    return {"inputs": tile_layer.identities(paths)}


def footprint(landmark: Landmark, tile) -> Polygon:
    """The landmark's footprint in engine x/z, from the buildings inventory or its polygon."""
    if landmark.polygon_en is not None:
        return Polygon([C.en_to_local(e, n)[0::2] for e, n in landmark.polygon_en])
    from turku3d.buildings.inputs import read_playable_footprints

    found = [
        b.footprint
        for b in read_playable_footprints(
            tile_layer.wfs(tile, "kanta_Rakennus"),
            tile_layer.wfs(tile, "GIS_Rakennukset"),
            C.tile_input_area(tile).core,
        )
        if b.id == landmark.building
    ]
    if not found:
        return None
    return found[0]


def owner(polygon: Polygon) -> C.VisualTile:
    xmin, zmin, xmax, zmax = polygon.bounds
    tiles = [
        C.VisualTile(0, i, j)
        for i, j in C.grid_range(C.BBoxLocal(xmin, zmin, xmax, zmax), C.visual_tile_size(0))
    ]
    point = ownership_point(polygon, tiles)
    return C.VisualTile.at(point.x, point.y)


def read_points(paths: list[Path], polygon: Polygon):
    """Building returns (x, y, z) near the footprint and ground returns (x, y, z) around it."""
    import laspy

    reach = polygon.buffer(REACH_M)
    ring = polygon.buffer(GROUND_RING_M).difference(polygon)
    xmin, zmin, xmax, zmax = polygon.buffer(GROUND_RING_M).bounds
    building, ground = [], []
    for path in paths:
        if not path.is_file():
            continue
        with laspy.open(path) as reader:
            for chunk in reader.chunk_iterator(2_000_000):
                x, y, z = C.en_to_local(
                    np.asarray(chunk.x), np.asarray(chunk.y), np.asarray(chunk.z)
                )
                near = (x >= xmin) & (x <= xmax) & (z >= zmin) & (z <= zmax)
                if not near.any():
                    continue
                cls = np.asarray(chunk.classification)[near]
                x, y, z = x[near], y[near], z[near]
                b = (cls == 6) & shapely.contains_xy(reach, x, z)
                building.append(np.column_stack((x[b], y[b], z[b])))
                g = (cls == 2) & shapely.contains_xy(ring, x, z)
                ground.append(np.column_stack((x[g], y[g], z[g])))
    building = np.concatenate(building) if building else np.empty((0, 3))
    ground = np.concatenate(ground) if ground else np.empty((0, 3))
    if not len(building):
        raise ValueError("no building returns on the footprint")
    if not len(ground):
        raise ValueError("no ground returns around the footprint")
    return building, ground


def _flat(positions, tris):
    """Unwelded triangles: three own vertices each, for flat shading and per-face UVs."""
    p = positions[tris.astype(np.int64)].reshape(-1, 3)
    return p, np.arange(len(p), dtype=np.uint32).reshape(-1, 3)


def _normals(p3):
    n = np.cross(p3[:, 1] - p3[:, 0], p3[:, 2] - p3[:, 0])
    return n / np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-12)


def visual_meshes(landmark: Landmark, shell: mesh.Shell, ortho, wall_rgb) -> list[GlbMesh]:
    """Roof (true ortho, planar from above) and walls (procedural, metre UVs); no base."""
    keep = shell.part != mesh.BASE
    tris = shell.indices[keep]
    part = shell.part[keep]
    normal = _normals(shell.positions[tris.astype(np.int64)])
    roof = (part == mesh.TOP) & (normal[:, 1] >= ROOF_MIN_NY)
    image, (xmin, zmin, xmax, zmax) = ortho
    extras = {
        "layer": LAYER,
        "kind": "building",
        "landmark_id": landmark.id,
        "building_id": landmark.building or landmark.id,
    }
    meshes = []
    p, idx = _flat(shell.positions, tris[roof])
    uv = np.column_stack(((p[:, 0] - xmin) / (xmax - xmin), (p[:, 2] - zmin) / (zmax - zmin)))
    oid = f"landmark/{landmark.id}/roof"
    meshes.append(
        GlbMesh(
            oid,
            p,
            idx,
            normals=np.repeat(_normals(p.reshape(-1, 3, 3)), 3, axis=0),
            uvs=uv,
            image=textures.jpeg(image),
            roughness=0.85,
            extras={
                **extras,
                "object_id": oid,
                "material_key": "landmark_ortho",
                "surface": "roof",
            },
        )
    )
    p, idx = _flat(shell.positions, tris[~roof])
    p3 = p.reshape(-1, 3, 3)
    n = _normals(p3)
    horizontal = n[:, [0, 2]]
    length = np.linalg.norm(horizontal, axis=1, keepdims=True)
    tangent = np.where(
        length > 1e-6,
        np.column_stack((-horizontal[:, 1], horizontal[:, 0])) / np.maximum(length, 1e-6),
        [1.0, 0.0],
    )
    tangent = np.repeat(tangent, 3, axis=0)
    u = (p[:, 0] * tangent[:, 0] + p[:, 2] * tangent[:, 1]) / textures.WALL_TILE_M
    v = -p[:, 1] / textures.WALL_TILE_M
    oid = f"landmark/{landmark.id}/walls"
    meshes.append(
        GlbMesh(
            oid,
            p,
            idx,
            normals=np.repeat(n, 3, axis=0),
            uvs=np.column_stack((u, v)),
            image=textures.jpeg(wall_rgb),
            roughness=0.95,
            repeat=True,
            extras={
                **extras,
                "object_id": oid,
                "material_key": f"landmark_{landmark.wall}",
                "surface": "wall",
            },
        )
    )
    return meshes


def build_landmark(landmark: Landmark, polygon: Polygon, tile) -> dict:
    """Visual meshes, collision arrays and measurements of one landmark."""
    timings = {}
    started = time.perf_counter()
    xmin, zmin, xmax, zmax = polygon.buffer(GROUND_RING_M).bounds
    building, ground = read_points(laz_paths(C.BBoxLocal(xmin, zmin, xmax, zmax)), polygon)
    timings["lidar"] = time.perf_counter() - started
    begin = time.perf_counter()
    field = mesh.heightfield(building, polygon, landmark.cell_m, REACH_M)
    base = float(ground[:, 1].min()) - BASE_BELOW_GROUND_M
    visual = mesh.shell(field, polygon, base, landmark.visual_triangles)
    collision = mesh.shell(field, polygon, base, landmark.collision_triangles)
    timings["mesh"] = time.perf_counter() - begin
    begin = time.perf_counter()
    seed = int.from_bytes(landmark.id.encode()[:8].ljust(8, b"\0"), "little")
    wall_rgb = textures.wall_image(landmark.wall, landmark.wall_rgb, seed)
    ortho = textures.ortho_crop(ortho_vrt(tile), polygon.buffer(REACH_M).bounds, ORTHO_RESOLUTION_M)
    meshes = visual_meshes(landmark, visual, ortho, wall_rgb)
    timings["textures"] = time.perf_counter() - begin
    material = np.where(collision.part == mesh.TOP, mat_id("roof"), mat_id("building")).astype(
        np.uint8
    )
    point = polygon.representative_point()
    record = {
        "id": landmark.id,
        "name": landmark.name,
        "building": landmark.building,
        "method": landmark.method,
        "footprint_m2": round(polygon.area, 1),
        "points_building": int(len(building)),
        "points_ground": int(len(ground)),
        "measured_cells": round(float(field.measured.mean()), 3),
        "base_m": round(base, 3),
        "top_m": round(float(visual.positions[:, 1].max()), 3),
        "visual": {
            "budget": landmark.visual_triangles,
            "triangles": int(len(visual.indices)),
            "shipped_triangles": int(sum(len(m.indices) for m in meshes)),
            "max_error_m": round(visual.max_error_m, 3),
        },
        "collision": {
            "budget": landmark.collision_triangles,
            "triangles": int(len(collision.indices)),
            "max_error_m": round(collision.max_error_m, 3),
        },
        "seconds": {k: round(v, 3) for k, v in timings.items()},
        "representative_point": [round(point.x, 3), round(point.y, 3)],
    }
    return {
        "meshes": meshes,
        "collision": CollisionLayer(
            collision.positions.astype(np.float64), collision.indices.astype(np.uint32), material
        ),
        "cell": C.CollisionTile.at(*ownership_point(polygon, [owner(polygon)]).coords[0]),
        "record": record,
    }


def build_tile(tile, staging):
    from turku3d.layers import write_visual

    records, meshes, cells = [], [], {}
    missing = []
    for landmark in allowlist.load():
        polygon = footprint(landmark, tile)
        if polygon is None:
            missing.append(landmark.id)
            continue
        if owner(polygon) != tile:
            continue
        built = build_landmark(landmark, polygon, tile)
        records.append(built["record"])
        meshes.extend(built["meshes"])
        cells.setdefault(built["cell"].id, []).append((landmark, built["collision"]))
    for cell, items in cells.items():
        positions, indices, material, owners = [], [], [], []
        count = 0
        for k, (_, layer) in enumerate(items):
            positions.append(layer.positions)
            indices.append(layer.indices + count)
            material.append(layer.material)
            owners.append(np.full(len(layer.indices), k, np.uint32))
            count += len(layer.positions)
        arrays = {
            "positions": np.concatenate(positions),
            "indices": np.concatenate(indices).astype(np.uint32),
            "material": np.concatenate(material),
            "building_ids": np.array([lm.building or lm.id for lm, _ in items], dtype=str),
            "building_index": np.concatenate(owners),
        }
        CollisionLayer(arrays["positions"], arrays["indices"], arrays["material"]).validate()
        np.savez_compressed(staging / f"{cell}.col.npz", **arrays)
    if meshes:
        write_visual(
            staging / f"{tile.id}.vis.glb", meshes, root_extras={"layer": LAYER, "tile": tile.id}
        )
    return {
        "records": records,
        "metrics": {"landmarks": len(records), "outside_input_area": missing},
    }


def combine(directory, tiles):
    records = [r for entry in tiles.values() for r in entry["records"]]
    metrics = {
        "landmarks": records,
        "visual_tiles": {
            tid: [r["id"] for r in entry["records"]]
            for tid, entry in tiles.items()
            if entry["records"]
        },
    }
    _atomic_write(
        Path(directory) / "metrics.json",
        (json.dumps(metrics, indent=2, sort_keys=True) + "\n").encode(),
    )
    return {
        "allowlist": [asdict(lm) for lm in allowlist.load()],
        "replaces": sorted(r["building"] for r in records if r["building"]),
    }


PRODUCER = tile_layer.Producer(LAYER, code_hash, tile_key, build_tile, combine)
