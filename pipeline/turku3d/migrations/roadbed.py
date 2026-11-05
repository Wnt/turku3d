"""roadbed@1: the drivable roadbed under the car lanes (``turku3d.roads.roadbed``).

On the tile's finished collision: car-edge heights that left the floor go back onto it, gaps and
trenches under the lanes get collision slabs (oriented boxes in the cells' PRIM section; the meshopt
triangle sections stay byte for byte), and 4-30 cm steps on bridge and tunnel lanes get ramp slabs.
The rules read only the street graph and the collision, so the same faults anywhere in the city
are fixed the same way. Per-rule counts are in the non-public ``manifest.roadbed``."""

from __future__ import annotations

import json
from collections import Counter

import numpy as np

from turku3d import config as C

from .core import Migration, Product


def transform(product: Product) -> None:
    from turku3d.col.format import Primitive, read_col
    from turku3d.col.materials import mat_id
    from turku3d.roads import encode, roadbed

    from .furniture import splice_primitives

    m = product.manifest
    entry = m.get("roads")
    cells = m.get("collision") or {}
    if not entry or not cells:
        return
    graph = json.loads(product.read(entry["url"]))
    if graph.get("revision", 1) < 2 or not graph.get("edgeHeights"):
        return
    triangles, materials = [], []
    for cell_id in sorted(cells):
        col = read_col(product.read(cells[cell_id]["url"]))
        if len(col.indices):
            triangles.append(col.world_positions()[col.indices.astype(np.int64)])
            materials.append(col.materials)
    if not triangles:
        return
    floor = roadbed.Floor(np.concatenate(triangles), np.concatenate(materials))
    stats = Counter()
    roadbed.repair_heights(graph, floor, stats)
    tile = C.VisualTile.parse(product.tile)
    found = roadbed.slabs(graph, floor, tile.bbox.as_tuple(), stats)
    by_cell = {}
    road = mat_id("road")
    for centre, half, rotation, _ in found:
        cell = C.CollisionTile.at(centre[0], centre[2])
        if cell.id not in cells:
            stats["slabs_outside"] += 1
            continue
        origin = np.asarray(cell.origin, np.float64)
        by_cell.setdefault(cell.id, []).append(
            Primitive.box(road, tuple(float(v) for v in np.asarray(centre) - origin), half, rotation))
    for cell_id, boxes in sorted(by_cell.items()):
        cell = cells[cell_id]
        product.replace(cell, splice_primitives(product.read(cell["url"]), lambda p: True, boxes))
        cell["prims"] = cell.get("prims", 0) + len(boxes)
        if "h" in cell:
            ys = [b.center[1] + C.CollisionTile.parse(cell_id).origin[1] for b in boxes]
            cell["h"] = [min(cell["h"][0], min(ys) - 0.2), max(cell["h"][1], max(ys) + 0.2)]
    if stats["lane_height_edges"]:
        product.replace(entry, encode(graph))
    m["roadbed"] = dict(sorted(stats.items()))


MIGRATION = Migration("roadbed", 1, transform)
