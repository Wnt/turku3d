"""trees-lidar@3: every drawn tree's crown from the classified lidar (``vegetation.crowns``).

Reads the tile's vegetation instances (EXT_mesh_gpu_instancing TRANSLATION and SCALE of each variant's nodes) and the
class-5 canopy of the LAZ blocks under the tile (``crowns.canopy_cells``, streamed, nothing cached), and
writes ``instance_crowns`` into the extras of every vegetation node: per instance ``[height, crown radius, shear x,
shear z, stretch, angle, base, open]`` or null where the lidar shows no crown; ``base`` (version 2) is the crown base
as a fraction of the height where open ground lets the lidar measure it, else null (``crowns.crown_base``, from the
leaf cells of the same blocks); ``open`` (version 3) the ground share of the returns under the crown, the sky it lets
through (``crowns.openness``, from the blocks' gap cells). The game draws a tree with these instead of the instance
scale, leans it by the shear, stretches its footprint to the crown's ellipse, lowers its crown to a lower measured
base and opens its sun gaps and see-through by the openness (``game/src/world/vegetationLod.ts`` ``shapeTree``,
``lowerCrown``; ``nature/materials.ts`` crown gaps). Only the visual GLB's JSON changes; trunk
collision keeps the generator's placement. A tile without lidar on disk is not changed."""

from __future__ import annotations

import posixpath

import numpy as np

from turku3d import config as C

from .core import Migration, Product, glb_chunks, glb_pack


def accessor(document: dict, binary: bytes, product: Product, glb_url: str, index: int) -> np.ndarray:
    """Float accessor values (count, components), through EXT_meshopt_compression when the view is compressed."""
    from turku3d.col.meshopt import decode_vertex_buffer

    acc = document["accessors"][index]
    if acc["componentType"] != 5126:
        raise ValueError(f"instance accessor {index} is not float")
    width = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}[acc["type"]]
    view = document["bufferViews"][acc["bufferView"]]

    def buffer(i: int) -> bytes:
        uri = document["buffers"][i].get("uri")
        return binary if uri is None else product.read(posixpath.join(posixpath.dirname(glb_url), uri))

    meshopt = view.get("extensions", {}).get("EXT_meshopt_compression")
    if meshopt:
        if meshopt.get("mode") != "ATTRIBUTES" or meshopt.get("filter", "NONE") != "NONE":
            raise ValueError(f"instance view {acc['bufferView']}: unsupported meshopt {meshopt}")
        source = buffer(meshopt["buffer"])[meshopt.get("byteOffset", 0):][: meshopt["byteLength"]]
        data = bytes(decode_vertex_buffer(source, meshopt["count"], meshopt["byteStride"]))
        stride = meshopt["byteStride"]
    else:
        data = buffer(view["buffer"])[view.get("byteOffset", 0):][: view["byteLength"]]
        stride = view.get("byteStride", 4 * width)
    return np.ndarray((acc["count"], width), dtype="<f4", buffer=data, offset=acc.get("byteOffset", 0),
                      strides=(stride, 4)).astype(float)


def vegetation_nodes(document: dict) -> dict[str, list[int]]:
    """Instanced vegetation node indices by variant (bark and crown nodes of a variant share their placements)."""
    groups: dict[str, list[int]] = {}
    for i, node in enumerate(document.get("nodes", [])):
        extras = node.get("extras") or {}
        if extras.get("layer") == "vegetation" and "EXT_mesh_gpu_instancing" in node.get("extensions", {}):
            groups.setdefault(str(extras.get("variant", node.get("name", ""))), []).append(i)
    return dict(sorted(groups.items()))


def tile_canopy(tile: C.VisualTile):
    from turku3d.vegetation.crowns import canopy_cells

    return canopy_cells(tile)


def tile_leaves(tile: C.VisualTile):
    from turku3d.vegetation.crowns import leaf_cells

    return leaf_cells(tile)


def tile_gaps(tile: C.VisualTile):
    from turku3d.vegetation.crowns import gap_cells

    return gap_cells(tile)


def local_gaps(tile: C.VisualTile):
    """The tile's gap cells in the tile-local frame of its instances (None without every LAZ block)."""
    gaps = tile_gaps(tile)
    if gaps is None:
        return None
    return gaps - [tile.origin[0], tile.origin[2], 0.0, 0.0]


def local_leaves(tile: C.VisualTile):
    """The tile's leaf cells in the tile-local frame of its instances (None without every LAZ block)."""
    leaves = tile_leaves(tile)
    if leaves is None:
        return None
    return leaves - [tile.origin[0], tile.origin[2], 0.0, 0.0, 0.0, 0.0]


def crowns(document: dict, binary: bytes, product: Product, glb_url: str, origin, canopy,
           leaves=None, gaps=None) -> dict[str, list]:
    """``instance_crowns`` per variant, for the variants' instances in accessor order."""
    from turku3d.vegetation.crowns import measure

    groups = vegetation_nodes(document)
    rows, spans = [], {}
    for variant, nodes in groups.items():
        attributes = document["nodes"][nodes[0]]["extensions"]["EXT_mesh_gpu_instancing"]["attributes"]
        t = accessor(document, binary, product, glb_url, attributes["TRANSLATION"])
        s = accessor(document, binary, product, glb_url, attributes["SCALE"])
        ids = (document["nodes"][nodes[0]].get("extras") or {}).get("instance_object_ids") or []
        spans[variant] = (len(rows), len(t))
        for k in range(len(t)):
            # Bushes keep their scatter size: their 0.7-1.6 m crowns are not canopy.
            bush = k < len(ids) and str(ids[k]).startswith("bush:")
            rows.append([t[k, 0] + origin[0], t[k, 2] + origin[2], t[k, 1], s[k, 1], max(s[k, 0], s[k, 2]), bush])
    if not rows:
        return {}
    table = np.asarray(rows, dtype=float)
    trees = np.flatnonzero(table[:, 5] == 0)
    measured = measure(table[trees, :5], canopy, leaves, gaps)
    result = [None] * len(table)
    for i, crown in zip(trees, measured, strict=True):
        result[i] = crown
    return {variant: result[start:start + count] for variant, (start, count) in spans.items()}


def transform(product: Product) -> None:
    m = product.manifest
    entry = (m.get("visual") or {}).get(product.tile)
    if not entry:
        return
    document, binary = glb_chunks(product.read(entry["url"]))
    if not vegetation_nodes(document):
        return
    tile = C.VisualTile.parse(product.tile)
    canopy = tile_canopy(tile)
    if canopy is None or not len(canopy):
        return
    # Tile-local canopy, as the instances.
    per_variant = crowns(document, binary, product, entry["url"], (0.0, 0.0, 0.0),
                         canopy - [tile.origin[0], 0.0, tile.origin[2]], local_leaves(tile), local_gaps(tile))
    if not any(c is not None for values in per_variant.values() for c in values):
        return
    top = None
    for variant, nodes in vegetation_nodes(document).items():
        values = per_variant[variant]
        for i in nodes:
            document["nodes"][i].setdefault("extras", {})["instance_crowns"] = values
        attributes = document["nodes"][nodes[0]]["extensions"]["EXT_mesh_gpu_instancing"]["attributes"]
        base = accessor(document, binary, product, entry["url"], attributes["TRANSLATION"])[:, 1]
        for y, crown in zip(base, values, strict=True):
            if crown is not None:
                top = max(top or -np.inf, float(y) + crown[0])
    product.replace(entry, glb_pack(document, binary))
    if top is not None and "h" in entry and len(entry["h"]) == 2 and top > entry["h"][1]:
        entry["h"] = [entry["h"][0], top]


MIGRATION = Migration("trees-lidar", 3, transform)
