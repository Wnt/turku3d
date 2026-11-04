"""signs@1: one post per register sign stack, each plate once, never on the carriageway (``props.signposts``).

The rule groups the drawn sign rows of each props batch into posts (doubles dropped, stacked
plates on one post, posts off the carriageway; GIS:Katualueet from the base's source version
through the pipeline's own loaders). The batch is rewritten in place:

* a post keeps its first plate's row; the row's index range now holds its pole, its plate and the
  plates hung under it, so a post breaks and falls as one;
* a stacked plate turns to the post's facing and hangs ``STACK`` lower per level; its own pole and
  row are gone;
* a dropped double's triangles and row are gone;
* a post moved to the kerb shifts with all its plates (its height follows the tile's collision).

Rewritten views are plain (POSITION, NORMAL, indices); other views stay byte for byte. Rows gain
``plates`` (codes, top first), ``plate_ids``, ``merged`` (dropped doubles) and ``adjusted`` when the
post moved. Signs have no baked collision (their poles are session-owned bodies), so only the visual
changes. A tile without sign rows is unchanged; ``manifest.furniture.signs`` has the counts."""

from __future__ import annotations

import math
import posixpath
from collections import Counter

import numpy as np

from turku3d import config as C

from .core import Migration, Product, glb_chunks, glb_pack
from .furniture import read_accessor

POLE_RADIUS = 0.04  # m: a triangle whose vertices all lie this close to the pole axis is the pole
WINDOW = 60.0  # m around the tile: carriageway polygons a post near the seam can reach


def _view(document, buffer, chunks, offset, data, stride=None, target=34962):
    pad = -offset % 4
    chunks.append(b"\0" * pad)
    offset += pad
    view = {"buffer": buffer, "byteOffset": offset, "byteLength": len(data), "target": target}
    if stride:
        view["byteStride"] = stride
    document["bufferViews"].append(view)
    chunks.append(data)
    return len(document["bufferViews"]) - 1, offset + len(data)


def _row_parts(row, idx, pos):
    """A sign row's triangles (as index triples) split into pole and plate, and the plate's yaw."""
    tris = idx[row["start"]: row["start"] + row["count"]].reshape(-1, 3)
    bx, _, bz = row["pole_base"]
    radial = np.hypot(pos[tris, 0] - bx, pos[tris, 2] - bz)  # (n, 3)
    pole = (radial <= POLE_RADIUS).all(axis=1)
    plate = np.unique(tris[~pole])
    dx, dz = pos[plate, 0].mean() - bx, pos[plate, 2].mean() - bz
    return tris[pole], tris[~pole], math.atan2(dx, dz)


def transform(product: Product) -> None:
    from turku3d.coarse.floodlights import collision_ground
    from turku3d.coarse.sources import Sources
    from turku3d.props import signposts
    from turku3d.roads.signal_posts import load_carriageway

    m = product.manifest
    entry = (m.get("visual") or {}).get(product.tile)
    if not entry:
        return
    document, binary = glb_chunks(product.read(entry["url"]))
    batches = [i for i, node in enumerate(document.get("nodes", []))
               if (node.get("extras") or {}).get("layer") == "props" and "mesh" in node
               and any(o.get("prop_type") == "sign" and o.get("pole_base") and "plates" not in o
                       for o in node["extras"].get("objects", ()))]
    if not batches:
        return
    tile = C.VisualTile.parse(product.tile)
    found, path = Sources().wfs_source("GIS:Katualueet", tile)
    road = load_carriageway(path if found and path else None, tile.bbox.buffered(WINDOW))
    ground = collision_ground(m.get("collision") or {}, product.read)
    stats = Counter()
    inline = "uri" not in document["buffers"][0]
    target = 0 if inline else len(document["buffers"])
    chunks, end = ([binary], len(binary)) if inline else ([], 0)
    dropped_tris = 0
    for i in batches:
        node = document["nodes"][i]
        primitive = document["meshes"][node["mesh"]]["primitives"][0]
        offset = np.asarray(node.get("translation", [0, 0, 0]), np.float64)
        pos = read_accessor(document, binary, product, entry["url"], primitive["attributes"]["POSITION"]).astype(np.float64)
        nrm = read_accessor(document, binary, product, entry["url"], primitive["attributes"]["NORMAL"]).astype(np.float64)
        idx = read_accessor(document, binary, product, entry["url"], primitive["indices"])[:, 0].astype(np.int64)
        world = pos + offset
        rows = node["extras"]["objects"]
        signs, parts, dropped = [], {}, 0
        for k, row in enumerate(rows):
            if row.get("prop_type") != "sign" or not row.get("pole_base"):
                continue
            parts[k] = _row_parts(row, idx, world)
            signs.append(signposts.Sign(row["object_id"], row.get("sign_code") or "", row["pole_base"][0],
                                        row["pole_base"][2], ref=k))
        posts, counts = signposts.posts(signs, road)
        stats.update(counts)
        # Each vertex belongs to one row (one prototype instance per row); a stack moves plates bodily.
        owner, shared = {}, False
        for k, (pole, plate, _) in parts.items():
            for v in np.unique(np.concatenate([pole.ravel(), plate.ravel()])):
                shared |= owner.setdefault(int(v), k) != k
        if shared:  # an unexpected batch layout: leave the batch as it is
            stats["sign_batch_shared_vertices"] += 1
            continue
        lead, skip, moved = {}, set(), False
        for post in posts:
            k0 = post.plates[0].ref
            pole0, plate0, yaw0 = parts[k0]
            row = rows[k0]
            bx, by, bz = row["pole_base"]
            dy = 0.0
            if post.rule:
                g0, g1 = ground(bx, bz), ground(post.x, post.z)
                dy = g1 - g0 if g0 is not None and g1 is not None else 0.0
            shift = np.array([post.x - bx, dy, post.z - bz])
            tris = [pole0, plate0]
            verts = np.unique(np.concatenate([pole0.ravel(), plate0.ravel()]))
            world[verts] += shift
            for level, sign in enumerate(post.plates[1:], 1):
                _, plate, yaw = parts[sign.ref]
                sb = rows[sign.ref]["pole_base"]
                turn = yaw0 - yaw
                c, s = math.cos(turn), math.sin(turn)
                rot = np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]])
                v = np.unique(plate.ravel())
                world[v] = (world[v] - sb) @ rot.T + [post.x, by + dy - signposts.STACK * level, post.z]
                nrm[v] = nrm[v] @ rot.T
                tris.append(plate)
                skip.add(sign.ref)
                dropped += len(parts[sign.ref][0])
            for sign in post.merged:
                skip.add(sign.ref)
                dropped += sum(len(t) for t in parts[sign.ref][:2])
            lead[k0] = np.concatenate(tris)
            if len(post.plates) > 1 or post.merged:
                row["plates"] = [s.code for s in post.plates]
                row["plate_ids"] = [s.object_id for s in post.plates]
                if post.merged:
                    row["merged"] = sorted(s.object_id for s in post.merged)
            if post.rule:
                moved = True
                row["adjusted"] = "sign_" + post.rule
                row["pole_base"] = [round(post.x, 3), round(by + dy, 3), round(post.z, 3)]
        if not skip and not moved:
            continue
        dropped_tris += dropped
        # New index order: every row in its old place, a post's row holding its whole stack.
        covered = sorted(range(len(rows)), key=lambda k: rows[k]["start"])
        out, cursor, kept = [], 0, []
        for k in covered:
            row = rows[k]
            if row["start"] > cursor:
                out.append(idx[cursor: row["start"]])
            cursor = max(cursor, row["start"] + row["count"])
            if k in skip:
                continue
            tris = lead[k].ravel() if k in lead else idx[row["start"]: row["start"] + row["count"]]
            row["start"] = int(sum(len(a) for a in out))
            row["count"] = int(len(tris))
            out.append(tris)
            kept.append(k)
        if cursor < len(idx):
            out.append(idx[cursor:])
        node["extras"]["objects"] = [rows[k] for k in sorted(kept, key=lambda k: rows[k]["start"])]
        indices = np.concatenate(out)
        acc = document["accessors"][primitive["indices"]]
        dtype = {5125: "<u4", 5123: "<u2", 5121: "u1"}[acc["componentType"]]
        view, end = _view(document, target, chunks, end, indices.astype(dtype).tobytes(), target=34963)
        acc.update(bufferView=view, byteOffset=0, count=int(len(indices)))
        local = (world - offset).astype("<f4")
        view, end = _view(document, target, chunks, end, local.tobytes(), stride=12)
        document["accessors"][primitive["attributes"]["POSITION"]].update(
            bufferView=view, byteOffset=0, min=[float(v) for v in local.min(0)], max=[float(v) for v in local.max(0)])
        view, end = _view(document, target, chunks, end, nrm.astype("<f4").tobytes(), stride=12)
        document["accessors"][primitive["attributes"]["NORMAL"]].update(bufferView=view, byteOffset=0)
    if chunks == ([binary] if inline else []):
        return
    if inline:
        binary = b"".join(chunks)
        document["buffers"][0]["byteLength"] = len(binary)
    else:
        extra = b"".join(chunks)
        shared = product.put("../shared/{hash}.bin", extra)
        document["buffers"].append({"byteLength": len(extra), "uri": posixpath.relpath(shared, posixpath.dirname(entry["url"]) or ".")})
    data = glb_pack(document, binary)
    product.replace(entry, data)
    if "tris" in entry:
        entry["tris"] -= dropped_tris
    if "brotli_bytes" in entry:
        import brotli

        entry["brotli_bytes"] = len(brotli.compress(data, quality=9))
    m.setdefault("furniture", {})["signs"] = dict(sorted(stats.items()))


MIGRATION = Migration("signs", 1, transform)
