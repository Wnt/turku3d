"""Per-tile count of visual surfaces the client draws without a real material.

Runs over the packaged visual GLB after the assembler has bound its library maps, with the
tile's layer GLBs (embedded images) for the flatness test. A surface is a glTF primitive;
its triangles are counted by material key and reason:

- ``untextured``: no base-colour texture, so it renders as one flat colour factor;
- ``no_uv``: a texture but no TEXCOORD_0, so every pixel samples one texel;
- ``flat``: a base-colour texture whose luminance barely varies (std below ``FLAT_STD``
  on 0-255), which reads as untextured on screen.

Surfaces whose look is not a texture by design are exempt: emissive lamps, water (the
client's planar river), night accents (``night_*``, the client's night surfaces), road paint
and vegetation (the client's species library replaces tile trees).

    python -m turku3d.coarse.material_audit VISUAL_GLB [LAYER_GLB_OR_STAGE_DIR...]
"""

from __future__ import annotations

import hashlib
import io
import json
import struct
import sys
from collections import defaultdict
from functools import lru_cache
from pathlib import Path

import numpy as np

FLAT_STD = 5.0
EXEMPT_LAYERS = {"vegetation", "markings"}
REASONS = ("untextured", "no_uv", "flat")


def exempt(layer, key, material):
    words = set(str(key).lower().replace("-", "_").split("_"))
    return (
        layer in EXEMPT_LAYERS
        or bool(words & {"water", "river"})
        or str(key).startswith("night_")
        or any(float(v) > 0 for v in material.get("emissiveFactor", ()))
    )


@lru_cache(maxsize=4096)
def _luminance_std(digest, data):
    from PIL import Image

    image = Image.open(io.BytesIO(data))
    image.draft("RGB", (64, 64))  # JPEG decodes at reduced scale; PNG ignores it
    image = image.convert("RGB")
    image.thumbnail((128, 128))
    pixels = np.asarray(image, np.float32)
    return float((pixels @ np.array([0.2126, 0.7152, 0.0722], np.float32)).std())


def image_std(data: bytes) -> float:
    return _luminance_std(hashlib.sha1(data).hexdigest(), data)


def read_glb(data: bytes):
    """(json, bin chunk); a GLB without a BIN chunk (external buffers only) has an empty one."""
    length = struct.unpack_from("<I", data, 12)[0]
    js = json.loads(data[20 : 20 + length])
    offset = 20 + length
    if offset + 8 > len(data):
        return js, b""
    size = struct.unpack_from("<I", data, offset)[0]
    return js, data[offset + 8 : offset + 8 + size]


def _image_bytes(js, binc, index):
    image = js["images"][index]
    if "bufferView" not in image:
        return None
    view = js["bufferViews"][image["bufferView"]]
    start = view.get("byteOffset", 0)
    return binc[start : start + view["byteLength"]]


def _texture_source(texture):
    if "source" in texture:
        return texture["source"]
    return texture.get("extensions", {}).get("KHR_texture_basisu", {}).get("source")


def _surfaces(data):
    """(layer, key, material, primitive, triangles, base texture image or None) per primitive."""
    js, binc = read_glb(data)
    materials = js.get("materials", [])
    textures = js.get("textures", [])
    for node in js.get("nodes", []):
        if "mesh" not in node:
            continue
        extras = node.get("extras", {})
        for prim in js["meshes"][node["mesh"]]["primitives"]:
            material = materials[prim["material"]] if prim.get("material") is not None else {}
            key = (material.get("extras") or {}).get("material_key") or extras.get("material_key") or "?"
            counted = prim["indices"] if "indices" in prim else prim["attributes"]["POSITION"]
            triangles = js["accessors"][counted]["count"] // 3
            base = material.get("pbrMetallicRoughness", {}).get("baseColorTexture")
            source = _texture_source(textures[base["index"]]) if base is not None else None
            image = js["images"][source] if source is not None else None
            yield extras.get("layer"), key, material, prim, triangles, image, (js, binc, source)


def flat_keys(layer_glbs):
    """Layer/key pairs whose own embedded base-colour image is near-uniform."""
    flat = set()
    for data in layer_glbs:
        for layer, key, material, _, _, image, (js, binc, source) in _surfaces(data):
            if image is None or exempt(layer, key, material):
                continue
            pixels = _image_bytes(js, binc, source)
            if pixels is not None and image_std(pixels) < FLAT_STD:
                flat.add((layer, key))
    return flat


def audit(visual: bytes, layer_glbs=()) -> dict:
    """Counts over a tile's packaged visual GLB, the bytes the client receives.

    The assembler binds library and facade maps, so untextured/no-UV are judged there.
    Flatness needs pixels: a packaged surface counts as flat when its texture is a layer image
    the assembler only moved to ``../shared/`` and that image (in ``layer_glbs``) is flat."""
    flat = flat_keys(layer_glbs)
    totals = dict(
        surfaces=0,
        triangles=0,
        exempt_triangles=0,
        **{r: dict(surfaces=0, triangles=0, keys=defaultdict(int)) for r in REASONS},
    )
    for layer, key, material, prim, triangles, image, _ in _surfaces(visual):
        totals["surfaces"] += 1
        totals["triangles"] += triangles
        if exempt(layer, key, material):
            totals["exempt_triangles"] += triangles
            continue
        if image is None:
            reason = "untextured"
        elif "TEXCOORD_0" not in prim["attributes"]:
            reason = "no_uv"
        elif (layer, key) in flat and str(image.get("uri", "")).startswith("../shared/"):
            reason = "flat"
        else:
            continue
        entry = totals[reason]
        entry["surfaces"] += 1
        entry["triangles"] += triangles
        entry["keys"][f"{layer}/{key}"] += triangles
    for reason in REASONS:
        keys = totals[reason]["keys"]
        totals[reason]["keys"] = dict(sorted(keys.items(), key=lambda kv: (-kv[1], kv[0])))
    return totals


def main(argv):
    """VISUAL_GLB [LAYER_GLB_OR_STAGE_DIR...]"""
    layers = []
    for arg in argv[1:]:
        p = Path(arg)
        layers.extend(sorted(p.glob("*.vis.glb")) if p.is_dir() else [p])
    report = audit(Path(argv[0]).read_bytes(), [p.read_bytes() for p in layers])
    print(json.dumps(report, indent=1))


if __name__ == "__main__":
    main(sys.argv[1:])
