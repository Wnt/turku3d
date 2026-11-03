"""Roof ortho crops in power-of-two size classes, so the client can stack a block's roofs in one texture array.

Each building's ``roof_ortho`` crop has its own arbitrary size (multiples of 4). Different sizes cannot share a
texture array, so every block drew each roof separately. A crop is resampled up to the next power-of-two square
of its longer side (8 px at least): never fewer texels along either axis, and the UVs (normalised) are unchanged.
Encoding follows the package step for draft sRGB textures (``package/visual.mjs``: ETC1S, qlevel 128, clevel 0).
The generator and the ``roof-classes`` migration both run :func:`classify_models`."""

import hashlib
import json
import posixpath
import struct
import subprocess
import tempfile
from pathlib import Path

from PIL import Image

MIN_CLASS = 8
TOKTX = ["--t2", "--genmipmap", "--threads", "1", "--assign_oetf", "srgb", "--encode", "etc1s", "--qlevel", "128", "--clevel", "0"]


def size_class(width: int, height: int) -> int:
    """The smallest power of two holding the crop's longer side."""
    side = max(int(width), int(height), MIN_CLASS)
    return 1 << (side - 1).bit_length()


def ktx2_size(data: bytes) -> tuple[int, int]:
    if data[:12] != b"\xabKTX 20\xbb\r\n\x1a\n":
        raise ValueError("not a KTX2 texture")
    return struct.unpack_from("<II", data, 20)


def _run(arguments: list[str]) -> None:
    subprocess.run(arguments, check=True, capture_output=True, text=True)


def resample(data: bytes, side: int, run=_run) -> bytes:
    """Decode a KTX2 crop, resample it to ``side`` x ``side`` (Lanczos; only ever up) and encode it again."""
    with tempfile.TemporaryDirectory(prefix="roof-class-") as temporary:
        directory = Path(temporary)
        source, decoded, square, output = (directory / name for name in ("in.ktx2", "in.png", "square.png", "out.ktx2"))
        source.write_bytes(data)
        run(["ktx", "extract", "--transcode", "rgba8", str(source), str(decoded)])
        with Image.open(decoded) as image:
            image.convert("RGBA").resize((side, side), Image.Resampling.LANCZOS).save(square)
        run(["toktx", *TOKTX, str(output), str(square)])
        return output.read_bytes()


def classify_models(product, resampler=resample) -> dict:
    """Rewrite every placement model whose roof_ortho crops are not yet square powers of two.

    ``product`` is a migration Product (read/put/glb_chunks semantics). Returns texel-density evidence per crop:
    ``{crop: {"from": [w, h], "to": side}}``. Model ids move with their content; placements and block LODs follow."""
    from turku3d.migrations.core import glb_chunks, glb_pack

    placements = product.manifest.get("buildingPlacements")
    if not placements:
        return {}
    evidence, resampled, renamed = {}, {}, {}
    for model in sorted(placements.get("models", []), key=lambda entry: entry["id"]):
        document, binary = glb_chunks(product.read(model["uri"]))
        directory = posixpath.dirname(model["uri"])
        changed = False
        for material in document.get("materials", []):
            if (material.get("extras") or {}).get("material_key") != "roof_ortho":
                continue
            reference = (material.get("pbrMetallicRoughness") or {}).get("baseColorTexture")
            if reference is None:
                continue
            texture = document["textures"][reference["index"]]
            source = (texture.get("extensions") or {}).get("KHR_texture_basisu", {}).get("source", texture.get("source"))
            image = document["images"][source]
            uri = image.get("uri")
            if not uri or uri.startswith("data:"):
                continue
            crop = posixpath.normpath(posixpath.join(directory, uri))
            data = product.read(crop)
            width, height = ktx2_size(data)
            side = size_class(width, height)
            if width == side and height == side:
                continue
            if crop not in resampled:
                encoded = resampler(data, side)
                if ktx2_size(encoded) != (side, side):
                    raise ValueError(f"{crop}: resampled to {ktx2_size(encoded)}, not {side}x{side}")
                resampled[crop] = product.put("../shared/{hash}.ktx2", encoded)
                evidence[posixpath.basename(crop)] = {"from": [width, height], "to": side}
            # Same convention as the generator's GLBs: the shared store addressed from the model's own directory.
            image["uri"] = posixpath.join(posixpath.dirname(uri), posixpath.basename(resampled[crop]))
            changed = True
        if not changed:
            continue
        data = glb_pack(document, binary)
        old = model["id"]
        model["uri"] = product.put("../shared/{hash}.glb", data)
        model["id"] = hashlib.sha256(data).hexdigest()
        if "bytes" in model:
            model["bytes"] = len(data)
        renamed[old] = model["id"]
    for placement in placements.get("placements", []):
        placement["model"] = renamed.get(placement["model"], placement["model"])
    for block in placements.get("blocks", []) or []:
        if block.get("lod") in renamed:
            block["lod"] = renamed[block["lod"]]
    return evidence


def evidence_json(evidence: dict) -> str:
    return json.dumps(evidence, indent=1, sort_keys=True)
