"""The bridges layer, built per listed L0 tile (turku3d.tile_layer) from the tile's own inputs.

A tile owns the mapped crossings whose axis midpoint lies in it: OSM `bridge=*` ways (named
ways grouped, city `Silta` street-area outlines where the name matches) found within REACH of
the tile. Deck heights come from lidar (class 17, else class 12, else the abutments). A crossing
over water also gets shore contacts on the published ground collision and approach ramps; one
over a road or a railway keeps its lidar deck. A crossing that cannot be built is reported,
not fatal, so a new area never fails on one odd bridge."""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

import geopandas as gpd
import numpy as np
from shapely.geometry import box
from shapely.ops import unary_union

from turku3d import config as C
from turku3d import tile_layer
from turku3d.col.materials import mat_id
from turku3d.ground.water import bodies
from turku3d.layers import CollisionLayer, layer_root, write_collision, write_visual
from turku3d.package.gltf import GlbMesh
from turku3d.package.manifest import _atomic_write
from turku3d.structures.inputs import Ground, lidar_paths

from .build import crossings, geometry, lidar_points
from .shore import Survey, at

REACH = 150.0  # the longest mapped crossing in the city is about 270 m; its midpoint owns it
LIDAR_MARGIN = 30.0  # abutments are sampled up to 25 m beyond a deck end


def owned(tile):
    """The crossings whose axis midpoint lies in the tile, by name."""
    city = gpd.read_parquet(tile_layer.wfs(tile, "GIS_Katualueet"))
    osm = gpd.read_parquet(tile_layer.osm(tile))
    found = crossings(city, osm, tile.bbox.buffered(REACH))
    return [c for c in found if tile.bbox.contains(*at(c.axis, c.axis.length / 2))]


def lidar_bounds(found):
    return C.BBoxLocal(*unary_union([c.footprint for c in found]).buffer(LIDAR_MARGIN).bounds)


def code_hash():
    base = Path(__file__).parents[1]
    return tile_layer.code_hash(
        Path(__file__).parent,
        base / "tile_layer.py",
        base / "config.py",
        base / "layers.py",
        base / "col/materials.py",
        base / "col/materials_v1.json",
        base / "package/gltf.py",
        base / "ground/water.py",
        base / "structures/geometry.py",
        base / "structures/inputs.py",
        base / "structures/survey.py",
    )


def tile_key(tile):
    paths = {"streets": tile_layer.wfs(tile, "GIS_Katualueet"), "osm": tile_layer.osm(tile)}
    found = owned(tile)
    ground = {}
    if found:
        bounds = lidar_bounds(found)
        paths.update({f"laz:{p.stem}": p for p in lidar_paths(bounds)})
        root = layer_root() / "ground"
        manifest = json.loads((root / "MANIFEST.json").read_text())
        window = box(*bounds.as_tuple())
        ground = {
            name: entry["sha256"]
            for name, entry in manifest["tiles"].items()
            if name.endswith(".col.npz")
            and window.intersects(box(*C.CollisionTile.parse(name.split(".")[0]).bbox.as_tuple()))
        }
        paths["quays"] = root / "quays.json"
    return {
        "crossings": sorted(c.name for c in found),
        "sources": tile_layer.identities(paths),
        "ground": ground,
    }


def build_tile(tile, staging):
    found = owned(tile)
    if not found:
        return {"records": [], "metrics": {"crossings": 0}}
    bounds = lidar_bounds(found)
    points, classes = lidar_points(lidar_paths(bounds), bounds)
    water = unary_union(list(bodies(tile_layer.osm(tile), bounds).values()))
    ground = Ground(layer_root() / "ground")
    survey = Survey(points, classes, water, ground) if (classes == 9).any() else None
    meshes, records = [], []
    collision = defaultdict(list)
    for crossing in found:
        over_water = survey is not None and crossing.footprint.intersects(water)
        try:
            built, report = geometry(crossing, points, classes, survey if over_water else None)
        except ValueError as error:
            records.append({"name": crossing.name, "built": False, "reason": str(error)})
            continue
        lacking = [s["side"] for s in report["shores"] if not s["ground_available"]]
        if lacking:
            reason = f"shore contacts lack ground collision: {', '.join(lacking)}"
            records.append({"name": crossing.name, "built": False, "reason": reason})
            continue
        meshes.extend(built)
        # A whole crossing belongs to the collision tile of its axis midpoint (seams: objects).
        collision[C.CollisionTile.at(*at(crossing.axis, crossing.axis.length / 2))].extend(built)
        records.append({**report, "built": True})
    for cell, parts in sorted(collision.items()):
        offsets = np.cumsum([0] + [len(p.positions) for p in parts[:-1]])
        write_collision(
            Path(staging) / f"{cell.id}.col.npz",
            CollisionLayer(
                np.concatenate([p.positions for p in parts]),
                np.concatenate([p.indices + o for p, o in zip(parts, offsets, strict=True)]).astype(
                    np.uint32
                ),
                np.concatenate(
                    [np.full(len(p.indices), mat_id(p.material), np.uint8) for p in parts]
                ),
            ),
        )
    if meshes:
        write_visual(
            Path(staging) / f"{tile.id}.vis.glb",
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
                            "painted_steel" if p.material == "railing" else "bridge_stone",
                        ),
                    },
                )
                for p in meshes
            ],
            root_extras={"layer": "bridges"},
        )
    return {
        "records": records,
        "metrics": {
            "crossings": sum(r["built"] for r in records),
            "skipped": sum(not r["built"] for r in records),
            "triangles": sum(len(p.indices) for p in meshes),
        },
    }


def combine(directory, tiles):
    crossings_ = [r for entry in tiles.values() for r in entry["records"]]
    report = {
        "crossings": crossings_,
        "built": sum(r["built"] for r in crossings_),
        "skipped": [r for r in crossings_ if not r["built"]],
        "triangles": sum(e["metrics"].get("triangles", 0) for e in tiles.values()),
    }
    path = C.work_root() / "reports/bridges/report.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(path, (json.dumps(report, indent=2) + "\n").encode())
    return {"crossings": report["built"], "skipped": len(report["skipped"])}


PRODUCER = tile_layer.Producer("bridges", code_hash, tile_key, build_tile, combine)
