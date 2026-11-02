"""Deterministic physical-scale masonry, copper and architectural PBR maps.

No reference pixels are used. Height drives tangent normals and local occlusion;
colour, dimensions and opening arrangements come only from building parameters.
"""

import io
import json
import math
from functools import lru_cache

import numpy as np
from PIL import Image, ImageDraw


def png(array):
    stream = io.BytesIO()
    Image.fromarray(np.asarray(array, np.uint8)).save(stream, format="PNG")
    return stream.getvalue()


def masonry(width, height, spec, seed, density=40):
    nx, ny = max(32, min(2048, round(width * density))), max(32, min(2048, round(height * density)))
    x, y = np.meshgrid((np.arange(nx) + 0.5) * width / nx, (np.arange(ny) + 0.5) * height / ny)
    bw, bh = spec["size_m"]
    joint = spec["mortar_m"]
    row = np.floor(y / bh).astype(int)
    offset = np.where(row % 2, bw / 2, 0)
    xx = x + offset
    col = np.floor(xx / bw).astype(int)
    distance = np.minimum.reduce([xx % bw, bw - xx % bw, y % bh, bh - y % bh])
    relief = np.clip((distance - joint / 2) / 0.009, 0, 1)
    rng = np.random.default_rng(seed)
    variation = np.sin(col * 127.1 + row * 311.7 + seed) * 43758.5453 % 1
    grain = rng.normal(0, 1.8, (ny, nx))
    base = np.asarray(spec["colour"])[None, None, :]
    rgb = (
        base * (1 + (variation[:, :, None] - 0.5) * spec.get("variation", 0.25)) + grain[:, :, None]
    )
    mortar = np.asarray(spec["mortar_colour"])
    rgb = mortar * (1 - relief[:, :, None]) + rgb * relief[:, :, None]
    return rgb, relief * 0.008 + grain * 0.00015


def copper(width, height, spec, seed):
    rgb, relief = masonry(
        width,
        height,
        dict(
            colour=spec["colour"],
            size_m=[spec.get("seam_m", 0.58), 2.0],
            mortar_m=0.008,
            mortar_colour=[48, 65, 61],
        ),
        seed,
    )
    ny, nx = relief.shape
    x, y = np.meshgrid(np.arange(nx) / max(1, nx), np.arange(ny) / max(1, ny))
    mottling = np.sin(x * 51 + y * 19) * np.sin(x * 17 - y * 31) * 0.07
    rgb *= 1 + mottling[:, :, None]
    return rgb, relief * 0.2


def maps(rgb, relief, width, height, metal=False):
    dy, dx = np.gradient(relief, height / len(relief), width / relief.shape[1])
    normal = np.stack([-dx, dy, np.ones_like(dx)], axis=-1)
    normal /= np.linalg.norm(normal, axis=-1)[:, :, None]
    # Unlit occlusion follows recess depth, not baked directional lighting.
    ao = np.clip(1 + relief * 5, 0.65, 1)
    roughness = np.full_like(ao, 0.73 if metal else 0.88)
    orm = np.stack([ao, roughness, np.full_like(ao, 0.25 if metal else 0)], axis=-1)
    return png(np.clip(rgb, 0, 255)), png((normal * 0.5 + 0.5) * 255), png(orm * 255)


def arch(draw, box, fill, pointed=True):
    left, top, right, bottom = box
    half = (right - left) / 2
    if pointed:
        pts = [(left, bottom), (left, top + half)]
        # Two circular arcs form a pointed Gothic head, no blurry image resize.
        for t in np.linspace(0, 1, 16):
            pts.append((left + half * t, top + half * (1 - t) ** 1.6))
        for t in np.linspace(0, 1, 16):
            pts.append((left + half + half * t, top + half * t**1.6))
        pts.extend([(right, bottom)])
        draw.polygon(pts, fill=fill)
    else:
        draw.rectangle((left, top + half, right, bottom), fill=fill)
        draw.pieslice((left, top, right, top + 2 * half), 180, 360, fill=fill)


@lru_cache(maxsize=96)
def wall_maps(width, height, bottom, description_json, tower, seed=7):
    d = json.loads(description_json)
    rgb, relief = masonry(width, height, d["brick"], seed)
    ny, nx = relief.shape
    sx = nx / width
    sy = ny / height
    # Raster y grows down; physical height grows up.
    heights = bottom + height - (np.arange(ny) + 0.5) / sy
    if tower:
        mask = heights < d["tower"]["stone_top_m"]
        stone, srelief = masonry(width, height, d["stone"], seed)
        rgb[mask] = stone[mask]
        relief[mask] = srelief[mask]
        mask = heights >= d["tower"]["copper_start_m"]
        metal, mrelief = copper(width, height, d["roof"], seed)
        rgb[mask] = metal[mask]
        relief[mask] = mrelief[mask]
    else:
        mask = heights < 1.5
        stone, srelief = masonry(width, height, d["stone"], seed)
        rgb[mask] = stone[mask]
        relief[mask] = srelief[mask]
    albedo = Image.fromarray(np.clip(rgb, 0, 255).astype("uint8"))
    depth = Image.fromarray(relief.astype("float32"), "F")
    paint = ImageDraw.Draw(albedo)
    bump = ImageDraw.Draw(depth)

    def box(cx, low, w, h):
        return (
            (cx - w / 2) * sx,
            (bottom + height - low - h) * sy,
            (cx + w / 2) * sx,
            (bottom + height - low) * sy,
        )

    def opening(cx, low, w, h, pointed):
        if low < bottom + 0.3 or low + h > bottom + height - 0.3:
            return
        outer = box(cx, low - 0.16, w + 0.32, h + 0.32)
        arch(paint, outer, tuple(d["trim_colour"]), pointed)
        arch(bump, outer, 0.035, pointed)
        inner = box(cx, low, w, h)
        arch(paint, inner, (35, 39, 37), pointed)
        arch(bump, inner, -0.07, pointed)
        for k in range(1, d["windows"]["mullions"] + 1):
            xx = (cx - w / 2 + w * k / (d["windows"]["mullions"] + 1)) * sx
            yy = (bottom + height - low - h + 0.5) * sy
            paint.line((xx, yy, xx, inner[3]), fill=(101, 105, 94), width=max(1, round(0.065 * sx)))

    if tower:
        for low, w, h, count in d["tower"]["opening_rows"]:
            for i in range(count):
                opening(width * (i + 1) / (count + 1), low, w, h, False)
        for level in d["tower"]["cornices_m"]:
            if bottom < level < bottom + height:
                rect = (
                    0,
                    (bottom + height - level - 0.22) * sy,
                    nx,
                    (bottom + height - level) * sy,
                )
                paint.rectangle(rect, fill=tuple(d["trim_colour"]))
                bump.rectangle(rect, fill=0.06)
        level = d["tower"]["clock_height_m"]
        r = d["tower"]["clock_radius_m"]
        if bottom + r < level < bottom + height - r and width > r * 3:
            cx = nx / 2
            cy = (bottom + height - level) * sy
            paint.rectangle((cx - r * sx, cy - r * sy, cx + r * sx, cy + r * sy), fill=(43, 48, 43))
            paint.ellipse(
                (cx - r * sx, cy - r * sy, cx + r * sx, cy + r * sy),
                outline=(190, 169, 94),
                width=max(2, round(0.09 * sx)),
            )
            for i in range(12):
                a = i * math.pi / 6
                paint.line(
                    (
                        cx + math.sin(a) * r * sx * 0.78,
                        cy - math.cos(a) * r * sy * 0.78,
                        cx + math.sin(a) * r * sx * 0.94,
                        cy - math.cos(a) * r * sy * 0.94,
                    ),
                    fill=(205, 185, 110),
                    width=max(2, round(0.09 * sx)),
                )
            for a, length in [(math.pi / 6, 0.58), (0, 0.8)]:
                paint.line(
                    (
                        cx,
                        cy,
                        cx + math.sin(a) * r * sx * length,
                        cy - math.cos(a) * r * sy * length,
                    ),
                    fill=(215, 192, 110),
                    width=max(2, round(0.1 * sx)),
                )
        if bottom + height > d["tower"]["copper_start_m"] + 4:
            opening(width / 2, d["tower"]["copper_start_m"] + 1, width * 0.42, 6, False)
    else:
        w = d["windows"]
        count = max(0, int(width / w["spacing_m"]))
        for i in range(count):
            opening(width * (i + 0.5) / count, w["sill_m"], w["width_m"], w["height_m"], True)
    return maps(np.asarray(albedo), np.asarray(depth), width, height)
