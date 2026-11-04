"""racks@1: bike-rack and city-bike-station rows along the nearest way (``props.racks``) on existing tiles.

Each drawn rack or station row of the props batch is fitted to its model (``props.models``: the
register variant's stands) at the tile's register heading or a quarter turn of it, then turned
about its own origin by ``props.racks.place`` with the tile's prepared ``roads`` graph. Only
vertices and normals of turned rows change (as plain buffer views; the rest stays meshopt-
compressed). Racks have no collision primitives and never move, so collision cells, ground masks
and parked-car slots are unchanged. Every rack row carries the rule's ``yaw_deg`` and
``orientation`` (EDIT-FORMAT §9). A tile without racks is unchanged.

The same step ends every generator build (``coarse.build`` applies the chain), so a regeneration
places racks alike."""

from __future__ import annotations

import copy
import math
import posixpath
from collections import Counter

import numpy as np

from turku3d import config as C

from .core import Migration, Product, glb_chunks, glb_pack
from .furniture import MATCH, _append_view, fit_origin, read_accessor


def _tile_inputs(tile):
    """The tile's prepared ``poles`` and ``roads`` (encoded graph), through the generator's loaders."""
    from turku3d.coarse.inputs import _prepare_one, _sources
    from turku3d.coarse.sources import Sources

    paths = _sources(tile, Sources())
    poles = _prepare_one(tile, "poles", paths["poles"]) if paths.get("poles") is not None else []
    roads = _prepare_one(tile, "roads", paths["roads"]) if paths.get("roads") is not None else None
    return poles, (roads or {}).get("json")


def fit_pose(world, source):
    """(x, z, yaw, residual) of a drawn rack row: its model at the register yaw or a quarter turn."""
    from turku3d.props.models import model

    local = np.asarray(model(*source.prototype).positions, np.float64)
    best = None
    lx, lz = local[:, 0].mean(), local[:, 2].mean()
    wx, wz = world[:, 0].mean(), world[:, 2].mean()
    for quarter in range(4):
        yaw = source.yaw + quarter * math.pi / 2
        c, s = math.cos(yaw), math.sin(yaw)
        # The drawn row may sit off the register point (moved out of a footprint): start from its centroid.
        guess = (wx - (c * lx + s * lz), wz - (-s * lx + c * lz))
        (x, z), residual = fit_origin(world, yaw, guess, local)
        if best is None or residual < best[3] - 1e-6:
            best = (float(x), float(z), yaw, residual)
    return best


def transform(product: Product) -> None:
    from turku3d.props import racks

    m = product.manifest
    entry = (m.get("visual") or {}).get(product.tile)
    if not entry:
        return
    document, binary = glb_chunks(product.read(entry["url"]))
    batches = [i for i, node in enumerate(document.get("nodes", []))
               if (node.get("extras") or {}).get("layer") == "props" and "objects" in node["extras"]
               and "mesh" in node]
    if not any(o.get("prop_type") in racks.KINDS for i in batches for o in document["nodes"][i]["extras"]["objects"]):
        return
    tile = C.VisualTile.parse(product.tile)
    poles, roads = _tile_inputs(tile)
    by_id = {p.object_id: p for p in poles}
    stats = Counter()

    decoded, items = {}, []
    for i in batches:
        node = document["nodes"][i]
        primitive = document["meshes"][node["mesh"]]["primitives"][0]
        rows = node["extras"]["objects"]
        if not any(o.get("prop_type") in racks.KINDS for o in rows):
            continue
        pos = read_accessor(document, binary, product, entry["url"], primitive["attributes"]["POSITION"])
        idx = read_accessor(document, binary, product, entry["url"], primitive["indices"])[:, 0].astype(np.int64)
        offset = np.asarray(node.get("translation", [0, 0, 0]), np.float64)
        decoded[i] = (primitive, pos.astype(np.float64), idx, offset)
        for k, row in enumerate(rows):
            if row.get("prop_type") not in racks.KINDS:
                continue
            source = by_id.get(row.get("object_id"))
            if source is None or source.kind != row["prop_type"]:
                stats["rack_unmatched"] += 1
                continue
            verts = np.unique(idx[row["start"]: row["start"] + row["count"]])
            x, z, yaw, residual = fit_pose(pos[verts].astype(np.float64) + offset, source)
            if residual > MATCH:
                stats["rack_unfitted"] += 1
                continue
            rack = copy.copy(source)
            rack.x, rack.z, rack.yaw = x, z, yaw
            items.append((rack, i, k, verts, yaw))
    if not items:
        return
    racks.place([rack for rack, *_ in items], roads, stats)

    normals, changed = {}, set()
    for rack, i, k, verts, drawn in items:
        primitive, positions, _, offset = decoded[i]
        row = document["nodes"][i]["extras"]["objects"][k]
        row["yaw_deg"], row["orientation"] = round(math.degrees(rack.yaw), 2), rack.orientation
        turn = rack.yaw - drawn
        if abs(math.remainder(turn, math.tau)) < 1e-4:
            continue
        stats["rack_turned"] += 1
        c, s = math.cos(turn), math.sin(turn)
        rot = np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]])
        centre = np.array([rack.x, 0.0, rack.z]) - offset
        positions[verts] = (positions[verts] - centre) @ rot.T + centre
        if i not in normals:
            normals[i] = read_accessor(document, binary, product, entry["url"],
                                       primitive["attributes"]["NORMAL"]).astype(np.float64)
        normals[i][verts] = normals[i][verts] @ rot.T
        changed.add(i)
    stats["racks"] = len(items)

    if changed:
        # As migrations.furniture: the BIN chunk is buffers[0] only without a uri; tiles whose
        # buffers are all shared files get one more content-addressed shared buffer.
        inline = "uri" not in document["buffers"][0]
        target = 0 if inline else len(document["buffers"])
        chunks, end = ([binary], len(binary)) if inline else ([], 0)
        for i in sorted(changed):
            primitive = decoded[i][0]
            data = decoded[i][1].astype("<f4")
            view, end = _append_view(document, target, chunks, end, data.tobytes())
            document["accessors"][primitive["attributes"]["POSITION"]].update(
                bufferView=view, byteOffset=0, min=[float(v) for v in data.min(0)], max=[float(v) for v in data.max(0)])
            view, end = _append_view(document, target, chunks, end, normals[i].astype("<f4").tobytes())
            document["accessors"][primitive["attributes"]["NORMAL"]].update(bufferView=view, byteOffset=0)
        if inline:
            binary = b"".join(chunks)
            document["buffers"][0]["byteLength"] = len(binary)
        else:
            extra = b"".join(chunks)
            shared = product.put("../shared/{hash}.bin", extra)
            uri = posixpath.relpath(shared, posixpath.dirname(entry["url"]) or ".")
            document["buffers"].append({"byteLength": len(extra), "uri": uri})
    data = glb_pack(document, binary)
    product.replace(entry, data)
    if "brotli_bytes" in entry:
        import brotli

        entry["brotli_bytes"] = len(brotli.compress(data, quality=9))
    m.setdefault("furniture", {})["racks"] = dict(sorted(stats.items()))


MIGRATION = Migration("racks", 1, transform)
