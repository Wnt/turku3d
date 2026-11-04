"""furniture@2: benches placed by ``props.seating`` (lidar snap, barrier back, water view, way heading) on existing tiles.

The rule is the furniture stage's (``coarse.furniture.produce`` calls the same ``seating.place``), so
a regeneration matches. Inputs come from the tile itself and the pipeline's own loaders over the
base's source version:

* each drawn bench row of the props batch (its pose: the generator's yaw from the tile's prepared
  ``poles`` input, its drawn origin fitted to the row's vertices);
* the tile's prepared ``barriers``, ``water`` and ``roads`` inputs, the LOD2 footprints and the survey LAZ.

Changed benches are rewritten in place: their batch vertices and normals (moved and turned about
their origin; a dropped bench's triangles collapse and its row leaves ``objects``), and their
static collision boxes (``coarse.furniture.BOXES``) in the tile's collision cells, whose meshopt
triangle sections are kept byte for byte. Every prop row gains the generator's ``yaw_deg`` and
``orientation`` (and ``adjusted`` when a rule moved it), so edits match placements exactly.
A tile without benches is unchanged."""

from __future__ import annotations

import copy
import math
import posixpath
import struct
import zlib
from collections import Counter

import numpy as np

from turku3d import config as C

from .core import Migration, Product, glb_chunks, glb_pack

FIT_ITERATIONS = 3
MATCH = 0.05  # m: drawn rows fit the posed model within ~3.5 cm (float32 batch, packager rounding); a wrong pose is decimetres off


def _buffer(document, binary, product, glb_url, index):
    uri = document["buffers"][index].get("uri")
    return binary if uri is None else product.read(posixpath.join(posixpath.dirname(glb_url), uri))


def read_accessor(document, binary, product, glb_url, index):
    """Accessor values as a (count, width) array, through EXT_meshopt_compression."""
    from turku3d.col.meshopt import decode_index_buffer, decode_vertex_buffer

    acc = document["accessors"][index]
    width = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}[acc["type"]]
    dtype = {5126: "<f4", 5125: "<u4", 5123: "<u2", 5121: "u1"}[acc["componentType"]]
    view = document["bufferViews"][acc["bufferView"]]
    meshopt = view.get("extensions", {}).get("EXT_meshopt_compression")
    if meshopt:
        if meshopt.get("filter", "NONE") != "NONE":
            raise ValueError(f"view {acc['bufferView']}: unsupported meshopt filter")
        source = _buffer(document, binary, product, glb_url, meshopt["buffer"])
        source = source[meshopt.get("byteOffset", 0):][: meshopt["byteLength"]]
        if meshopt["mode"] == "ATTRIBUTES":
            data = bytes(decode_vertex_buffer(source, meshopt["count"], meshopt["byteStride"]))
        elif meshopt["mode"] == "TRIANGLES":
            data = np.asarray(decode_index_buffer(source, meshopt["count"], meshopt["byteStride"]),
                              np.uint32).astype(f"<u{meshopt['byteStride']}").tobytes()
        else:
            raise ValueError(f"view {acc['bufferView']}: unsupported meshopt mode {meshopt['mode']}")
        stride = meshopt["byteStride"]
    else:
        data = _buffer(document, binary, product, glb_url, view["buffer"])
        data = data[view.get("byteOffset", 0):][: view["byteLength"]]
        stride = view.get("byteStride", np.dtype(dtype).itemsize * width)
    item = np.dtype(dtype).itemsize
    return np.ndarray((acc["count"], width), dtype=dtype, buffer=data, offset=acc.get("byteOffset", 0),
                      strides=(stride, item)).copy()


def _append_view(document, buffer, chunks, offset, data):
    """A plain (uncompressed) vertex view at the end of ``buffer``; returns (view index, new offset)."""
    pad = -offset % 4
    chunks.append(b"\0" * pad)
    offset += pad
    document["bufferViews"].append({"buffer": buffer, "byteOffset": offset, "byteLength": len(data), "byteStride": 12,
                                    "target": 34962})
    chunks.append(data)
    return len(document["bufferViews"]) - 1, offset + len(data)


def _bench_model():
    from turku3d.props.models import model

    return np.asarray(model("bench", "").positions, np.float64)


def fit_origin(world, yaw, guess, local):
    """Drawn origin (x, z) of a bench row whose vertices are the model turned by ``yaw``."""
    from scipy.spatial import cKDTree

    c, s = math.cos(yaw), math.sin(yaw)
    posed = np.c_[c * local[:, 0] + s * local[:, 2], -s * local[:, 0] + c * local[:, 2]]
    tree = cKDTree(posed)
    t = np.asarray(guess, np.float64)
    xz = world[:, [0, 2]]
    for _ in range(FIT_ITERATIONS):
        _, nearest = tree.query(xz - t)
        t = t + (xz - t - posed[nearest]).mean(0)
    residual, _ = tree.query(xz - t)
    return t, float(residual.max())


def _ground(cloud, x, z):
    if cloud is None or cloud.ground_tree is None:
        return None
    near = cloud.ground_tree.query_ball_point([x, z], 1.0)
    return float(np.median(cloud.ground[near, 1])) if len(near) >= 3 else None


def _tile_inputs(tile):
    """The furniture stage's inputs of the tile, through the generator's own loaders."""
    from turku3d.coarse.inputs import _prepare_one, _sources, buildings
    from turku3d.coarse.sources import Sources

    sources = Sources()
    paths = _sources(tile, sources)
    poles = _prepare_one(tile, "poles", paths["poles"]) if paths.get("poles") is not None else []
    barriers = _prepare_one(tile, "barriers", paths["barriers"]) if paths.get("barriers") is not None else []
    water = _prepare_one(tile, "water", paths["water"]) if paths.get("water") is not None else []
    found, bldg = sources.wfs_source("bldg:Building_LOD2", tile)
    footprints = [b.footprint for b in buildings(bldg, tile, context=True)] if found and bldg else []
    roads = _prepare_one(tile, "roads", paths["roads"]) if paths.get("roads") is not None else None
    return poles, barriers, water, footprints, (roads or {}).get("json")


def _bench_boxes(p):
    """(centre, half extents, rotation) of a bench's collision boxes in world-local metres."""
    from turku3d.coarse.furniture import BOXES, _yaw_quaternion

    c, s = math.cos(p.yaw), math.sin(p.yaw)
    for (cx, cy, cz), half in BOXES["bench"]:
        yield (p.x + c * cx + s * cz, p.y + cy, p.z - s * cx + c * cz), half, _yaw_quaternion(p.yaw)


def splice_primitives(data: bytes, keep, add) -> bytes:
    """A .col file with its PRIM section replaced, every other section's bytes unchanged.

    ``keep(primitive) -> bool`` filters the existing primitives; ``add`` lists new ones."""
    from turku3d.col.format import _HEADER, _PRIM, _SECTION, HEADER_BYTES, PRIM_BYTES, SECTION_BYTES, _pad8, read_col

    tile = read_col(data)
    tile.primitives = [p for p in tile.primitives if keep(p)] + list(add)
    header = _HEADER.unpack_from(data, 0)
    count = header[19]
    sections = []
    for k in range(count):
        tag, enc, flags, off, length, n, stride = _SECTION.unpack_from(data, HEADER_BYTES + SECTION_BYTES * k)
        body = data[off:off + length]
        if tag == b"PRIM":
            body = b"".join(_PRIM.pack(p.kind, p.material, p.flags, *p.center, *p.rotation, *p.params)
                            for p in tile.primitives)
            n = len(tile.primitives)
            stride = PRIM_BYTES
        sections.append((tag, enc, flags, body, n, stride))
    off = _pad8(HEADER_BYTES + SECTION_BYTES * len(sections))
    table, payload = [], bytearray()
    for tag, enc, flags, body, n, stride in sections:
        table.append(_SECTION.pack(tag, enc, flags, off, len(body), n, stride))
        chunk = body + b"\0" * (_pad8(len(body)) - len(body))
        payload += chunk
        off += len(chunk)
    lo, hi = tile.compute_bbox()
    values = list(header)
    values[10:13] = [float(v) for v in np.asarray(lo, np.float32)]
    values[13:16] = [float(v) for v in np.asarray(hi, np.float32)]
    values[18] = len(tile.primitives)
    values[21] = off
    values[22] = 0
    head = _HEADER.pack(*values) + b"".join(table)
    buf = bytearray(head + b"\0" * (_pad8(len(head)) - len(head)) + payload)
    struct.pack_into("<I", buf, 92, zlib.crc32(buf) & 0xFFFFFFFF)
    return bytes(buf)


def _is_box_of(prim, centre, half, origin):
    from turku3d.col.format import PRIM_BOX

    if prim.kind != PRIM_BOX:
        return False
    world = np.asarray(prim.center, np.float64) + origin
    return (np.abs(world - np.asarray(centre)).max() < 0.05
            and np.abs(np.asarray(prim.params[:3]) - np.asarray(half)).max() < 1e-3)


def transform(product: Product) -> None:
    from turku3d.props import seating

    m = product.manifest
    entry = (m.get("visual") or {}).get(product.tile)
    if not entry:
        return
    document, binary = glb_chunks(product.read(entry["url"]))
    batches = [i for i, node in enumerate(document.get("nodes", []))
               if (node.get("extras") or {}).get("layer") == "props" and "objects" in node["extras"]
               and "mesh" in node]
    if not any(o.get("prop_type") == "bench" for i in batches for o in document["nodes"][i]["extras"]["objects"]):
        return
    tile = C.VisualTile.parse(product.tile)
    poles, barriers, water, footprints, roads = _tile_inputs(tile)
    by_id = {p.object_id: p for p in poles}
    local = _bench_model()
    stats = Counter()

    # Decode each bench row's pose: the generator's yaw, the drawn origin and base height.
    decoded = {}
    for i in batches:
        node = document["nodes"][i]
        primitive = document["meshes"][node["mesh"]]["primitives"][0]
        pos = read_accessor(document, binary, product, entry["url"], primitive["attributes"]["POSITION"])
        idx = read_accessor(document, binary, product, entry["url"], primitive["indices"])[:, 0].astype(np.int64)
        offset = np.asarray(node.get("translation", [0, 0, 0]), np.float64)
        decoded[i] = (primitive, pos, idx, offset)
    benches, rows = [], []
    for i in batches:
        primitive, pos, idx, offset = decoded[i]
        for k, row in enumerate(document["nodes"][i]["extras"]["objects"]):
            source = by_id.get(row.get("object_id"))
            if row.get("prop_type") != "bench" or source is None:
                continue
            verts = np.unique(idx[row["start"]: row["start"] + row["count"]])
            world = pos[verts].astype(np.float64) + offset
            (x, z), residual = fit_origin(world, source.yaw, (source.x, source.z), local)
            if residual > MATCH:
                stats["bench_unfitted"] += 1
                continue
            bench = copy.copy(source)
            bench.x, bench.z, bench.y = float(x), float(z), float(world[:, 1].min())
            benches.append(bench)
            rows.append((i, k, verts, (bench.x, bench.y, bench.z, bench.yaw)))
    if not benches:
        return
    from turku3d.ground.inputs import laz_paths

    cloud = seating.survey_cloud(tile.bbox, benches, laz_paths(tile.bbox.buffered(seating.WINDOW)))
    kept = {id(b): b for b in seating.place(benches, cloud, barriers, footprints, water, stats, roads)}

    changed_nodes, dropped_rows, collision_edits = set(), set(), []
    positions = {i: decoded[i][1].astype(np.float64) for i in batches}
    normals = {}
    for bench, (i, k, verts, (ox, oy, oz, oyaw)) in zip(benches, rows, strict=True):
        primitive, _, _, offset = decoded[i]
        row = document["nodes"][i]["extras"]["objects"][k]
        old = copy.copy(bench)
        old.x, old.y, old.z, old.yaw = ox, oy, oz, oyaw
        if id(bench) not in kept:
            positions[i][verts] = positions[i][verts[0]]  # zero-area triangles: nothing drawn
            dropped_rows.add((i, k))
            changed_nodes.add(i)
            collision_edits.append((old, None))
            continue
        row["yaw_deg"], row["orientation"] = round(math.degrees(bench.yaw), 2), bench.orientation
        if bench.adjusted:
            row["adjusted"] = bench.adjusted
        if math.hypot(bench.x - ox, bench.z - oz) < 1e-3 and abs(bench.yaw - oyaw) < 1e-4:
            continue
        g0, g1 = _ground(cloud, ox, oz), _ground(cloud, bench.x, bench.z)
        bench.y = oy + (g1 - g0 if g0 is not None and g1 is not None else 0.0)
        turn = bench.yaw - oyaw
        c, s = math.cos(turn), math.sin(turn)
        rot = np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]])
        v = positions[i][verts] + offset - [ox, oy, oz]
        positions[i][verts] = v @ rot.T + [bench.x, bench.y, bench.z] - offset
        if i not in normals:
            normals[i] = read_accessor(document, binary, product, entry["url"],
                                       primitive["attributes"]["NORMAL"]).astype(np.float64)
        normals[i][verts] = normals[i][verts] @ rot.T
        changed_nodes.add(i)
        collision_edits.append((old, bench))
    for i in batches:  # every prop row carries the generator's yaw and rule (EDIT-FORMAT §9)
        for row in document["nodes"][i]["extras"]["objects"]:
            source = by_id.get(row.get("object_id"))
            if source is not None and "yaw_deg" not in row:
                row["yaw_deg"], row["orientation"] = round(math.degrees(source.yaw), 2), source.orientation
    for i, k in sorted(dropped_rows, reverse=True):
        del document["nodes"][i]["extras"]["objects"][k]

    if changed_nodes:
        # The GLB's BIN chunk is buffers[0] only when that buffer has no uri; tiles whose buffers are all
        # shared external files get one more content-addressed shared buffer for the rewritten views.
        inline = "uri" not in document["buffers"][0]
        target = 0 if inline else len(document["buffers"])
        chunks, end = ([binary], len(binary)) if inline else ([], 0)
        for i in sorted(changed_nodes):
            primitive = decoded[i][0]
            data = positions[i].astype("<f4")
            view, end = _append_view(document, target, chunks, end, data.tobytes())
            acc = document["accessors"][primitive["attributes"]["POSITION"]]
            acc.update(bufferView=view, byteOffset=0, min=[float(v) for v in data.min(0)],
                       max=[float(v) for v in data.max(0)])
            if i in normals:
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

    if collision_edits:
        _collision(product, m, collision_edits, stats)
    m.setdefault("furniture", {})["benches"] = dict(sorted(stats.items()))


def _collision(product, manifest, edits, stats):
    """Move each changed bench's boxes in the tile's collision cells (dropped: removed)."""
    from turku3d.col.format import Primitive
    from turku3d.col.materials import mat_id

    cells = manifest.get("collision") or {}
    removals, additions = {}, {}
    for old, new in edits:
        cell = C.CollisionTile.at(old.x, old.z)
        removals.setdefault(cell.id, []).extend(_bench_boxes(old))
        if new is not None:
            target = C.CollisionTile.at(new.x, new.z)
            if target.id not in cells:
                target = cell  # past the tile's cells: the box stays in its old cell's file
            origin = np.asarray(target.origin, np.float64)
            for centre, half, rotation in _bench_boxes(new):
                additions.setdefault(target.id, []).append(
                    Primitive.box(mat_id("street_furniture"), tuple(np.asarray(centre) - origin), half, rotation))
    for cell_id in sorted(set(removals) | set(additions)):
        entry = cells.get(cell_id)
        if entry is None:
            continue
        origin = np.asarray(C.CollisionTile.parse(cell_id).origin, np.float64)
        gone = removals.get(cell_id, [])
        found = [False] * len(gone)

        def keep(prim):
            for n, (centre, half, _) in enumerate(gone):
                if not found[n] and _is_box_of(prim, centre, half, origin):
                    found[n] = True
                    return False
            return True

        data = splice_primitives(product.read(entry["url"]), keep, additions.get(cell_id, []))
        stats["collision_boxes_unmatched"] += found.count(False)
        product.replace(entry, data)
        entry["prims"] = entry.get("prims", 0) - found.count(True) + len(additions.get(cell_id, []))


MIGRATION = Migration("furniture", 2, transform)
