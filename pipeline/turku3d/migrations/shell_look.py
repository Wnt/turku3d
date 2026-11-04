"""shell-look@1: block LOD shells take their HD buildings' own roof and wall colours and window grid.

A block's LOD parent (``coarse.blocks.lod_meshes``) painted every roof with the fixed ROOF pigment and every wall
with the facade program's bare wall colour, so at the 280 m hand-over (and in the scenery cells that pass these
shells through, ``tools/remote-drafts/scenery.mjs``) light, red and brown roofs turned grey-green and trimmed or
textured facades changed colour. The GTA V study (``docs/handover/lodfade.md``) showed that LOD parity, not fade
length, is what hides a swap.

For every placement the measurer ``shell_colours.mjs`` reads the HD model as the chain left it (earlier steps'
writes included) and returns the area-weighted mean of its roof textures, of its wall textures or facade-kit paint
(module atlas tinted by wall and trim, trim bands mixed in), its kit grid (bay, storey, opening width and height
fractions) and its footprint. Each shell triangle is matched to the child building whose footprint holds its
centroid; roof triangles (ROOF pigment) take the roof mean, wall triangles the wall mean, and wall vertices gain
``_SHELL_GRID`` so the client places the procedural windows on the building's real bays and storeys. The rewritten
shell is a new shared GLB; the block's ``lod`` and the model entry follow. A tile without block shells is unchanged.

The generator runs this step at the end of every build, so a regeneration equals base + migration."""

from __future__ import annotations

import hashlib
import json
import os
import posixpath
import struct
import subprocess
import tempfile
from pathlib import Path

import numpy as np

from .core import Migration, Product, glb_chunks, glb_pack

HERE = Path(__file__).resolve().parent
MEASURER = HERE / "shell_colours.mjs"
# The measurer is JavaScript, outside the code hash's Python import closure: its hash is pinned here, so changing it
# changes this module and therefore the derived generation.
MEASURER_SHA256 = "d4c273a5ee5a2f0841d870ec122cfcdb78f6dfcab60eab7a5c007568b22364d6"
ROOF = np.array((0.20, 0.23, 0.22))  # coarse.blocks.ROOF
MATCH_M = 1.0  # a shell triangle's centroid lies within this of its building's footprint box


def _measure(product: Product, placements: list[dict], models: dict[str, dict]) -> dict:
    digest = hashlib.sha256(MEASURER.read_bytes()).hexdigest()
    if digest != MEASURER_SHA256:
        raise RuntimeError(f"shell_colours.mjs changed ({digest}); update MEASURER_SHA256 and bump the version")
    with tempfile.TemporaryDirectory(prefix="shell-look-") as work:
        root = Path(work) / "tile"
        written: set[str] = set()

        def materialise(url: str) -> None:
            path = posixpath.normpath(url)
            if path in written:
                return
            target = root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            data = product.read(url)
            target.write_bytes(data)
            written.add(path)
            if url.endswith(".glb"):
                document, _ = glb_chunks(data)
                for item in document.get("images", []) + document.get("buffers", []):
                    if item.get("uri"):
                        materialise(posixpath.join(posixpath.dirname(url), item["uri"]))

        uris = {}
        for model_id in sorted({p["model"] for p in placements if p.get("model") in models}):
            uri = models[model_id]["uri"]
            materialise(uri)
            uris[model_id] = posixpath.normpath(uri)
        spec = Path(work) / "spec.json"
        spec.write_text(json.dumps({"dir": str(root), "placements": placements, "models": uris}, sort_keys=True))
        out = subprocess.run(["node", str(MEASURER), str(spec)], capture_output=True, text=True, check=True,
                             env={**os.environ, "NODE_OPTIONS": ""})
        return json.loads(out.stdout)["buildings"]


def _views(document: dict, binary: bytes) -> list[bytes]:
    return [binary[v.get("byteOffset", 0):][: v["byteLength"]] for v in document["bufferViews"]]


def _array(document: dict, views: list[bytes], index: int) -> np.ndarray:
    acc = document["accessors"][index]
    width = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}[acc["type"]]
    dtype = {5126: "<f4", 5125: "<u4", 5123: "<u2"}[acc["componentType"]]
    data = np.frombuffer(views[acc["bufferView"]], dtype=dtype, offset=acc.get("byteOffset", 0),
                         count=acc["count"] * width)
    return data.reshape(acc["count"], width) if width > 1 else data


def recolour(data: bytes, position: list[float], children: list[dict]) -> bytes | None:
    """The shell GLB with per-building colours and ``_SHELL_GRID``; None when nothing matched.

    ``children``: [{box: [minX, minZ, maxX, maxZ], roof, wall, grid}] in tile coordinates; ``position`` is the
    block's placement offset."""
    document, binary = glb_chunks(data)
    if document.get("extensionsUsed"):
        return None  # compressed shells are not produced by blocks.py; leave anything else alone
    views = _views(document, binary)
    boxes = [(np.array(c["box"], float), c) for c in children if c.get("box")]
    changed = False
    extra: list[tuple[int, bytes, dict]] = []  # (mesh primitive key, bytes, accessor)
    for mi, mesh in enumerate(document["meshes"]):
        for pi, prim in enumerate(mesh["primitives"]):
            attrs = prim["attributes"]
            if "COLOR_0" not in attrs or "POSITION" not in attrs or "indices" not in prim:
                continue
            pos = _array(document, views, attrs["POSITION"]).astype(float) + np.asarray(position, float)
            colour = _array(document, views, attrs["COLOR_0"]).astype(np.float32).copy()
            index = _array(document, views, prim["indices"]).astype(np.int64).reshape(-1, 3)
            grid = np.zeros((len(pos), 4), np.float32)
            done = np.zeros(len(pos), bool)
            for tri in index:
                centre = pos[tri].mean(axis=0)
                best, gap = None, MATCH_M
                for box, child in boxes:
                    d = max(box[0] - centre[0], 0, centre[0] - box[2]) + max(box[1] - centre[2], 0, centre[2] - box[3])
                    if d < gap or (d == gap == 0 and best is None):
                        best, gap = child, d
                if best is None:
                    continue
                roof = np.abs(colour[tri].astype(float) - ROOF).max(axis=1) < 2e-3
                for vertex, is_roof in zip(tri, roof):
                    if done[vertex]:
                        continue
                    done[vertex] = True
                    value = best.get("roof") if is_roof else best.get("wall")
                    if value is not None:
                        colour[vertex] = value
                        changed = True
                    if not is_roof and best.get("grid"):
                        grid[vertex] = best["grid"]
            views[document["accessors"][attrs["COLOR_0"]]["bufferView"]] = colour.astype("<f4").tobytes()
            extra.append(((mi, pi), grid.astype("<f4").tobytes(), {"componentType": 5126, "count": len(pos), "type": "VEC4"}))
    if not changed:
        return None
    for (mi, pi), payload, accessor in extra:
        views.append(payload)
        document["bufferViews"].append({"buffer": 0, "byteLength": len(payload)})
        document["accessors"].append({**accessor, "bufferView": len(document["bufferViews"]) - 1})
        document["meshes"][mi]["primitives"][pi]["attributes"]["_SHELL_GRID"] = len(document["accessors"]) - 1
    packed = b""
    for view, payload in zip(document["bufferViews"], views):
        packed += b"\0" * (-len(packed) % 4)
        view["byteOffset"] = len(packed)
        view["byteLength"] = len(payload)
        packed += payload
    document["buffers"] = [{"byteLength": len(packed)}]
    return glb_pack(document, packed)


def transform(product: Product) -> None:
    placements_doc = product.manifest.get("buildingPlacements")
    if not placements_doc or not placements_doc.get("blocks"):
        return
    models = {m["id"]: m for m in placements_doc.get("models", [])}
    hd = [p for p in placements_doc.get("placements", []) if models.get(p.get("model"), {}).get("kind") != "block-lod"]
    blocks = [b for b in placements_doc["blocks"] if b.get("lod") in models]
    if not blocks or not hd:
        return
    measured = _measure(product, hd, {k: v for k, v in models.items() if v.get("kind") != "block-lod"})
    for block in sorted(blocks, key=lambda b: b["id"]):
        children = [measured[c] for c in block.get("children", []) if c in measured]
        if not children:
            continue
        entry = models[block["lod"]]
        data = recolour(product.read(entry["uri"]), block["position"], children)
        if data is None:
            continue
        old = entry["id"]
        product.replace(entry, data)
        block["lod"] = entry["id"]
        models.pop(old, None)
        models[entry["id"]] = entry


MIGRATION = Migration("shell-look", 1, transform)
