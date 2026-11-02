"""Bound landmark draw calls with padded PBR atlases and per-building batches."""

import hashlib
import io
from dataclasses import replace

import numpy as np
from PIL import Image

from .procedural import png


def merge(meshes, **changes):
    if len(meshes) == 1:
        return replace(meshes[0], **changes)
    offsets = np.cumsum([0, *[len(m.positions) for m in meshes[:-1]]])
    return replace(
        meshes[0],
        positions=np.concatenate([m.positions for m in meshes]),
        indices=np.concatenate(
            [m.indices + offset for m, offset in zip(meshes, offsets, strict=True)]
        ),
        uvs=np.concatenate([m.uvs for m in meshes]),
        **changes,
    )


def batch(meshes, size=2048, padding=8):
    """Preserve texel density; clamp face UVs inside an eight-texel edge extrusion.

    Repeating strips cannot enter a clamp atlas, so each shared map triplet has
    its own batch. Non-repeating walls are packed tallest first into shelves.
    """
    if len(meshes) < 2:
        return meshes
    repeated, faces = {}, []
    for mesh in meshes:
        if mesh.repeat:
            key = hashlib.sha256(mesh.image + mesh.normal_image + mesh.orm_image).digest()
            repeated.setdefault(key, []).append(mesh)
        else:
            maps = [
                Image.open(io.BytesIO(data)).convert("RGB")
                for data in (mesh.image, mesh.normal_image, mesh.orm_image)
            ]
            w, h = maps[0].size
            ratio = min(1, (size - 2 * padding) / max(w, h))
            if ratio < 1:
                maps = [
                    image.resize((round(w * ratio), round(h * ratio)), Image.Resampling.LANCZOS)
                    for image in maps
                ]
            faces.append((mesh, maps))
    result = [merge(group) for group in repeated.values()]
    pages = []
    for mesh, maps in sorted(faces, key=lambda item: item[1][0].height, reverse=True):
        w, h = maps[0].size
        packed_w, packed_h = w + 2 * padding, h + 2 * padding
        placed = False
        for page in pages:
            for shelf in page["shelves"]:
                if packed_h <= shelf["h"] and shelf["x"] + packed_w <= size:
                    x, y = shelf["x"], shelf["y"]
                    shelf["x"] += packed_w
                    placed = True
                    break
            if not placed and page["height"] + packed_h <= size:
                x, y = 0, page["height"]
                page["shelves"].append(dict(x=packed_w, y=y, h=packed_h))
                page["height"] += packed_h
                placed = True
            if placed:
                break
        if not placed:
            page = dict(shelves=[dict(x=packed_w, y=0, h=packed_h)], height=packed_h, faces=[])
            pages.append(page)
            x, y = 0, 0
        page["faces"].append((mesh, maps, x, y))
    for page in pages:
        # Crop unused rows to avoid compressing and uploading empty texels.
        height = page["height"]
        atlases = [np.zeros((height, size, 3), np.uint8) for _ in range(3)]
        group = []
        for mesh, maps, x, y in page["faces"]:
            w, h = maps[0].size
            for atlas, image in zip(atlases, maps, strict=True):
                padded = np.pad(
                    np.asarray(image), ((padding, padding), (padding, padding), (0, 0)), mode="edge"
                )
                atlas[y : y + h + 2 * padding, x : x + w + 2 * padding] = padded
            uv = (mesh.uvs * [w - 1, h - 1] + [x + padding + 0.5, y + padding + 0.5]) / [
                size,
                height,
            ]
            group.append(replace(mesh, uvs=uv))
        result.append(
            merge(
                group,
                image=png(atlases[0]),
                normal_image=png(atlases[1]),
                orm_image=png(atlases[2]),
            )
        )
    return result
