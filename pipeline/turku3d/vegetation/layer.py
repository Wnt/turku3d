"""The vegetation layer, built per listed L0 tile (turku3d.tile_layer).

A tile reads the city tree register of its own input area and the lidar under the tile plus
READ_MARGIN, so trunks near its edges compete for canopy returns as they would in one large
build; it keeps the trees whose trunk lies in the tile."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import numpy as np

from turku3d import config as C
from turku3d import tile_layer
from turku3d.layers import PRIMITIVE_DTYPE, CollisionLayer, write_collision
from turku3d.package.manifest import _atomic_write
from turku3d.placement.inputs import context_key, place_objects
from turku3d.vegetation.trees import SOURCES, Tree, laz_paths, load_points, measure, register
from turku3d.vegetation.visual import ASSETS, write_instances

# Canopy returns are assigned to trunks within 10 m and ground heights need support within
# 12 m, so a tile reads 12 m past its edges (a whole number keeps the 0.5 m cell grid).
READ_MARGIN = 12.0
PARAMS = {"deduplicate_m": 2, "canopy_class": 5, "season": "autumn", "licence": "CC0-1.0"}


def code_hash():
    base = Path(__file__).parents[1]
    return tile_layer.code_hash(
        Path(__file__).parent,
        base / "placement",
        base / "tile_layer.py",
        base / "config.py",
        base / "layers.py",
        base / "package/gltf.py",
        base / "package/manifest.py",
    )


def tile_key(tile):
    area = C.source_area(tile)
    paths = {name: tile_layer.wfs(tile, name) for name in SOURCES}
    paths.update({f"laz:{p.stem}": p for p in laz_paths(tile.bbox.buffered(READ_MARGIN))})
    paths.update({f"asset:{p.name}": p for p in sorted(ASSETS.glob("*")) if p.is_file()})
    return {
        "sources": tile_layer.identities(paths),
        "placement": context_key(tile.bbox, "vegetation", area),
        "params": PARAMS,
    }


def write_tile(trees, directory, tile):
    """The tile's instanced visual and trunk cylinders; every trunk lies in the tile."""
    if any(C.VisualTile.at(t.x, t.z) != tile for t in trees):
        raise ValueError(f"{tile.id}: trees outside the tile")
    if not trees:
        return None
    budget = write_instances(Path(directory) / f"{tile.id}.vis.glb", tile, trees)
    for cell in sorted({C.CollisionTile.at(t.x, t.z) for t in trees}):
        batch = [t for t in trees if C.CollisionTile.at(t.x, t.z) == cell]
        primitives = np.zeros(len(batch), PRIMITIVE_DTYPE)
        for p, t in zip(primitives, batch, strict=True):
            p["kind"], p["material"] = "cylinder", 26
            p["height"] = min(t.height * 0.55, 8)
            p["center"] = [t.x, t.y + p["height"] / 2, t.z]
            p["radius"] = t.trunk_radius
        empty = (np.empty((0, 3), np.float64), np.empty((0, 3), np.uint32), np.empty(0, np.uint8))
        write_collision(Path(directory) / f"{cell.id}.col.npz", CollisionLayer(*empty, primitives))
    return budget


def build_tile(tile, staging):
    area = C.source_area(tile)
    bounds = tile.bbox.buffered(READ_MARGIN)
    trees = register(bounds, area)
    ground, canopy = load_points(bounds)
    print(f"{tile.id}: measuring {len(trees)} mapped trees against {len(canopy)} canopy cells")
    trees = [t for t in measure(trees, ground, canopy, bounds) if tile.bbox.contains(t.x, t.z)]
    placed = (
        place_objects(trees, "vegetation", tile.bbox, area, report=f"vegetation/{tile.id}")
        if trees
        else []
    )
    # A repair may move a trunk up to 2 m; one that left its tile is dropped, not handed over.
    inside = [t for t in placed if tile.bbox.contains(t.x, t.z)]
    budget = write_tile(inside, staging, tile)
    return {
        "records": [t.record() for t in inside],
        "metrics": {
            "budget": budget,
            "omitted": len(trees) - len(placed),
            "left_tile": len(placed) - len(inside),
        },
    }


def combine(directory, tiles):
    trees = [Tree(**r) for entry in tiles.values() for r in entry["records"]]
    budgets = {tid: e["metrics"]["budget"] for tid, e in tiles.items() if e["metrics"]["budget"]}
    report = {
        "trees": len(trees),
        "measured": sum(t.measured for t in trees),
        "sources": dict(Counter(t.source for t in trees)),
        "tiles": budgets,
        "trees_without_lidar": [t.object_id for t in trees if not t.measured],
        "records": [t.record() for t in trees],
    }
    data = (json.dumps(report, indent=2) + "\n").encode()
    _atomic_write(Path(directory) / "trees.json", data)
    return {**PARAMS, "budgets": budgets, "inventory_sha256": tile_layer.digest_bytes(data)}


PRODUCER = tile_layer.Producer("vegetation", code_hash, tile_key, build_tile, combine)
