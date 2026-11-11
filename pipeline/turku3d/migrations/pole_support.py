"""Anchor sign/stop posts after all sign placement and finished-terrain migrations.

Only the packed post and its runtime pole_base move vertically. Unsupported posts
leave both the visual ranges and runtime inventory. Existing stacks, source positions,
furniture, lamps and terrain remain authoritative.
"""

from __future__ import annotations

import math
import posixpath
from collections import Counter

import numpy as np
import shapely

from turku3d import config as C
from turku3d.col.format import read_col
from turku3d.col.materials import mat_id

from .core import Migration, Product, glb_chunks, glb_pack
from .furniture import read_accessor
from .signs import _view

SUPPORT = {
    mat_id(name)
    for name in (
        "road",
        "sidewalk",
        "curb",
        "curb_dropped",
        "crossing",
        "cycleway",
        "cobblestone",
        "paving_slab",
        "gravel",
        "grass",
        "soil",
        "sand",
        "rock",
        "quay",
        "ramp",
        "stair_slope",
        "bridge_deck",
        "wood_deck",
    )
}
RADIUS = 0.05  # Runtime upright pole cylinder.


def support_sampler(product):
    loaded = {}

    def sample(x, z, reference):
        cid = C.CollisionTile.at(x, z).id
        if cid not in loaded:
            entry = product.manifest.get("collision", {}).get(cid)
            if entry is None:
                loaded[cid] = None
            else:
                col = read_col(product.read(entry["url"]))
                tri = col.world_positions()[col.indices]
                normal = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
                valid = (normal[:, 1] > 1e-9) & (
                    np.linalg.norm(normal[:, [0, 2]], axis=1)
                    <= normal[:, 1] * math.tan(math.radians(40))
                )
                valid &= np.isin(col.materials, [*SUPPORT, mat_id("water")])
                tri, materials = tri[valid], col.materials[valid]
                loaded[cid] = tri, materials, shapely.STRtree(shapely.polygons(tri[:, :, [0, 2]]))
        if loaded[cid] is None:
            return None, "unavailable_cell"
        tri, materials, tree = loaded[cid]
        hits = tree.query(shapely.Point(x, z), predicate="intersects")
        if not len(hits):
            return None, "missing_support"
        a, b, c = tri[hits, 0], tri[hits, 1], tri[hits, 2]
        u, v = b - a, c - a
        det = u[:, 0] * v[:, 2] - u[:, 2] * v[:, 0]
        height = a[:, 1] + ((x - a[:, 0]) * v[:, 2] - (z - a[:, 2]) * v[:, 0]) / det * u[:, 1]
        height += (u[:, 0] * (z - a[:, 2]) - u[:, 2] * (x - a[:, 0])) / det * v[:, 1]
        wet = materials[hits] == mat_id("water")
        dry = height[~wet]
        if wet.any():
            dry = dry[dry >= height[wet].max() - 0.01]
        if not len(dry):
            return None, "water" if wet.any() else "missing_support"
        # A lower-road post must not jump to an unrelated bridge above it.
        return float(dry[np.argmin(abs(dry - reference))]), None

    return sample


def supported_base(sample, base):
    x, y, z = base
    angles = np.arange(8) * math.pi / 4
    offsets = np.vstack(([0, 0], RADIUS * np.c_[np.cos(angles), np.sin(angles)]))
    heights = []
    for dx, dz in offsets:
        height, reason = sample(x + dx, z + dz, y)
        if height is None:
            return None, reason
        heights.append(height)
    if max(heights) - min(heights) > 0.05:
        return None, "uneven_support"
    return heights[0], None


def translation(document, index):
    parents = {child: i for i, n in enumerate(document["nodes"]) for child in n.get("children", [])}
    offset = np.zeros(3)
    while index is not None:
        node = document["nodes"][index]
        if any(key in node for key in ("matrix", "rotation", "scale")):
            raise ValueError("packed pole batch must have translation-only transforms")
        offset += node.get("translation", [0, 0, 0])
        index = parents.get(index)
    return offset


def transform(product: Product):
    manifest = product.manifest
    entry = manifest.get("visual", {}).get(product.tile)
    if not entry:
        return
    document, binary = glb_chunks(product.read(entry["url"]))
    sample = support_sampler(product)
    inline = "uri" not in document["buffers"][0]
    target = 0 if inline else len(document["buffers"])
    chunks, end = ([binary], len(binary)) if inline else ([], 0)
    stats, reasons = Counter(), Counter()
    maximum, dropped = 0.0, 0
    changed = False
    for n, node in enumerate(document.get("nodes", [])):
        rows = node.get("extras", {}).get("objects", [])
        poles = [
            row
            for row in rows
            if row.get("prop_type") in ("sign", "bus_stop") and row.get("pole_base")
        ]
        if not poles or "mesh" not in node:
            continue
        primitives = document["meshes"][node["mesh"]]["primitives"]
        if len(primitives) != 1:
            raise ValueError("packed pole batch must have one primitive")
        primitive = primitives[0]
        pos = read_accessor(
            document, binary, product, entry["url"], primitive["attributes"]["POSITION"]
        ).astype(np.float64)
        idx = read_accessor(document, binary, product, entry["url"], primitive["indices"])[:, 0]
        # Preserve arbitrary translated batch roots; pole_base is already world-local.
        translation(document, n)
        batch_changed, remove = False, set()
        owners = {}
        for row in rows:
            for vertex in np.unique(idx[row["start"] : row["start"] + row["count"]]):
                if int(vertex) in owners:
                    raise ValueError("packed pole range shares vertices with another object")
                owners[int(vertex)] = row["start"]
        for row in poles:
            stats["source"] += 1
            height, reason = supported_base(sample, row["pole_base"])
            if reason == "unavailable_cell":
                # A five-centimetre rim can cross the owning tile's boundary.
                # Missing neighbour data is not evidence that the post has no support.
                stats["placed"] += 1
                stats["unresolved"] += 1
                continue
            if reason:
                remove.add(row["start"])
                reasons[reason] += 1
                stats["omitted"] += 1
                batch_changed = True
                continue
            stats["placed"] += 1
            dy = height - row["pole_base"][1]
            maximum = max(maximum, abs(dy))
            if abs(dy) < 0.001:
                continue
            verts = np.unique(idx[row["start"] : row["start"] + row["count"]])
            pos[verts, 1] += dy
            row["pole_base"][1] = round(height, 4)
            row["base_support"] = "final_collision"
            stats["shifted"] += 1
            batch_changed = True
        if not batch_changed:
            continue
        changed = True
        kept, output, cursor, count = [], [], 0, 0
        for row in sorted(rows, key=lambda r: r["start"]):
            start, stop = row["start"], row["start"] + row["count"]
            gap = idx[cursor:start]
            output.append(gap)
            count += len(gap)
            cursor = stop
            if start in remove:
                dropped += row["count"] // 3
                continue
            output.append(idx[start:stop])
            row["start"] = count
            count += row["count"]
            kept.append(row)
        output.append(idx[cursor:])
        indices = np.concatenate(output)
        node["extras"]["objects"] = kept
        if not len(indices):
            node.pop("mesh")  # An empty accessor is invalid glTF; retain the harmless group.
            continue
        acc = document["accessors"][primitive["indices"]]
        dtype = {5125: "<u4", 5123: "<u2", 5121: "u1"}[acc["componentType"]]
        view, end = _view(
            document, target, chunks, end, indices.astype(dtype).tobytes(), target=34963
        )
        acc.update(bufferView=view, byteOffset=0, count=len(indices))
        view, end = _view(document, target, chunks, end, pos.astype("<f4").tobytes(), stride=12)
        document["accessors"][primitive["attributes"]["POSITION"]].update(
            bufferView=view, byteOffset=0, min=pos.min(0).tolist(), max=pos.max(0).tolist()
        )
    if not stats["source"]:
        return
    manifest.setdefault("furniture", {})["pole_support"] = {
        **{key: stats[key] for key in ("source", "placed", "omitted", "shifted", "unresolved")},
        "reasons": dict(reasons),
        "max_adjustment_m": round(maximum, 4),
    }
    if not changed:
        return
    if inline:
        binary = b"".join(chunks)
        document["buffers"][0]["byteLength"] = len(binary)
    else:
        data = b"".join(chunks)
        shared = product.put("../shared/{hash}.bin", data)
        document["buffers"].append(
            {
                "byteLength": len(data),
                "uri": posixpath.relpath(shared, posixpath.dirname(entry["url"]) or "."),
            }
        )
    data = glb_pack(document, binary)
    product.replace(entry, data)
    if "tris" in entry:
        entry["tris"] -= dropped
    if "brotli_bytes" in entry:
        import brotli

        entry["brotli_bytes"] = len(brotli.compress(data, quality=9))


MIGRATION = Migration("pole-support", 1, transform)
