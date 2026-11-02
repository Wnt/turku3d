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


def joint_coverage(position, period, joint, footprint):
    """Exact box-filter coverage of a periodic mortar strip in physical metres."""

    def integral(value):
        shifted = value + joint / 2
        return np.floor(shifted / period) * joint + np.minimum(shifted % period, joint)

    return np.clip(
        (integral(position + footprint / 2) - integral(position - footprint / 2)) / footprint,
        0,
        1,
    )


def masonry(width, height, spec, seed, density=40):
    nx, ny = max(32, min(2048, round(width * density))), max(32, min(2048, round(height * density)))
    x, y = np.meshgrid((np.arange(nx) + 0.5) * width / nx, (np.arange(ny) + 0.5) * height / ny)
    bw, bh = spec["size_m"]
    joint = spec["mortar_m"]
    row = np.floor(y / bh).astype(int)
    offset = np.where(row % 2, bw / 2, 0)
    xx = x + offset
    col = np.floor(xx / bw).astype(int)
    # Headers make the medieval bonds materially different from modern running bond.
    bond = spec.get("bond", "running")
    if bond in ("monk", "flemish"):
        period = bw * (2.5 if bond == "monk" else 1.5)
        boundaries = [0, bw, 2 * bw] if bond == "monk" else [0, bw]
        vertical_joints = np.clip(
            sum(joint_coverage(xx - edge, period, joint, width / nx) for edge in boundaries), 0, 1
        )
    else:
        vertical_joints = joint_coverage(xx, bw, joint, width / nx)
    bed_joints = joint_coverage(y, bh, joint, height / ny)
    # Preserve physical mortar area when a joint is narrower than one texel.
    # Point samples plus a fixed bevel made tall walls acquire pale zebra bands.
    relief = (1 - vertical_joints) * (1 - bed_joints)
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


def fieldstone(width, height, spec, seed):
    """Jittered Voronoi rubble, with independent rounded joints and physical scale."""
    from scipy.ndimage import distance_transform_edt

    rgb, relief = masonry(width, height, spec, seed)
    ny, nx = relief.shape
    bw, bh = spec["size_m"]
    x, y = np.meshgrid(
        (np.arange(nx) + 0.5) * width / nx / bw, (np.arange(ny) + 0.5) * height / ny / bh
    )
    ix, iy = np.floor(x), np.floor(y)
    best = np.full((ny, nx), np.inf)
    ids = np.zeros((ny, nx))
    for dz in (-1, 0, 1):
        for dx in (-1, 0, 1):
            cx, cy = ix + dx, iy + dz
            h = np.sin(cx * 127.1 + cy * 311.7 + seed) * 43758.5453 % 1
            k = np.sin(cx * 269.5 + cy * 183.3 + seed) * 23421.631 % 1
            distance = (x - cx - 0.15 - 0.7 * h) ** 2 + (y - cy - 0.15 - 0.7 * k) ** 2
            take = distance < best
            best[take] = distance[take]
            ids[take] = h[take]
    edge = np.zeros_like(ids, dtype=bool)
    edge[1:] |= ids[1:] != ids[:-1]
    edge[:, 1:] |= ids[:, 1:] != ids[:, :-1]
    distance = distance_transform_edt(~edge, sampling=(height / ny, width / nx))
    body = np.clip((distance - spec["mortar_m"] * 0.5) / 0.025, 0, 1)
    grain = np.random.default_rng(seed).normal(0, 2, (ny, nx))
    pigment = (
        np.asarray(spec["colour"])[None, None, :] * (0.78 + 0.44 * ids[:, :, None])
        + grain[:, :, None]
    )
    rgb = np.asarray(spec["mortar_colour"]) * (1 - body[:, :, None]) + pigment * body[:, :, None]
    return rgb, body * 0.025 + grain * 0.0006


def wall_surface(width, height, d, seed):
    kind = d.get("wall_material", "brick")
    if kind == "stone":
        return fieldstone(width, height, d["stone"], seed)
    if kind in ("render", "concrete"):
        rgb, relief = masonry(width, height, d["brick"], seed)
        noise = np.random.default_rng(seed).normal(0, 1.5, relief.shape)
        return np.asarray(d["brick"]["colour"]) + noise[:, :, None], noise * 0.0003
    if kind == "wood":
        return masonry(width, height, dict(d["brick"], size_m=[0.16, 100], mortar_m=0.006), seed)
    return masonry(width, height, d["brick"], seed)


def roof_surface(width, height, spec, seed):
    if spec["material"] in ("copper", "metal", "zinc"):
        return copper(width, height, spec, seed)
    return masonry(
        width,
        height,
        dict(
            colour=spec["colour"],
            size_m=[0.32, 0.22],
            mortar_m=0.008,
            mortar_colour=[46, 49, 47],
            variation=0.2,
        ),
        seed,
    )


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


@lru_cache(maxsize=64)
def material_maps(description_json, kind):
    d = json.loads(description_json)
    if kind == "roof":
        return maps(
            *roof_surface(4, 4, d["roof"], 11),
            4,
            4,
            metal=d["roof"]["material"] in ("copper", "metal", "zinc"),
        )
    if kind == "stone":
        return maps(*fieldstone(4, 4, d["stone"], 7), 4, 4)
    return maps(*wall_surface(4, 4, d, 7), 4, 4)


def maps(rgb, relief, width, height, metal=False):
    dy, dx = np.gradient(relief, height / len(relief), width / relief.shape[1])
    normal = np.stack([-dx, dy, np.ones_like(dx)], axis=-1)
    normal /= np.linalg.norm(normal, axis=-1)[:, :, None]
    # Unlit occlusion follows recess depth, not baked directional lighting.
    ao = np.clip(1 + relief * 5, 0.65, 1)
    roughness = np.full_like(ao, 0.73 if metal else 0.88)
    orm = np.stack([ao, roughness, np.full_like(ao, 0.25 if metal else 0)], axis=-1)
    return png(np.clip(rgb, 0, 255)), png((normal * 0.5 + 0.5) * 255), png(orm * 255)


def arch(draw, box, fill, pointed=True, rise=None):
    left, top, right, bottom = box
    half = (right - left) / 2
    head = min(bottom - top, half if rise is None else rise)
    if pointed is None:
        draw.rectangle(box, fill=fill)
    elif pointed:
        pts = [(left, bottom), (left, top + head)]
        # Two circular arcs form a pointed Gothic head, no blurry image resize.
        for t in np.linspace(0, 1, 16):
            pts.append((left + half * t, top + head * (1 - t) ** 1.6))
        for t in np.linspace(0, 1, 16):
            pts.append((left + half + half * t, top + head * t**1.6))
        pts.extend([(right, bottom)])
        draw.polygon(pts, fill=fill)
    else:
        draw.rectangle((left, top + head, right, bottom), fill=fill)
        draw.pieslice((left, top, right, top + 2 * head), 180, 360, fill=fill)


@lru_cache(maxsize=96)
def wall_maps(width, height, bottom, description_json, tower, seed=7):
    d = json.loads(description_json)
    rgb, relief = wall_surface(width, height, d, seed)
    ny, nx = relief.shape
    sx = nx / width
    sy = ny / height
    # Raster y grows down; physical height grows up.
    heights = bottom + height - (np.arange(ny) + 0.5) / sy
    if tower:
        mask = heights < d["tower"]["stone_top_m"]
        stone, srelief = fieldstone(width, height, d["stone"], seed)
        rgb[mask] = stone[mask]
        relief[mask] = srelief[mask]
        mask = heights >= d["tower"]["copper_start_m"]
        metal, mrelief = roof_surface(width, height, d["roof"], seed)
        rgb[mask] = metal[mask]
        relief[mask] = mrelief[mask]
    else:
        mask = heights < 1.5
        stone, srelief = fieldstone(width, height, d["stone"], seed)
        rgb[mask] = stone[mask]
        relief[mask] = srelief[mask]
    x, y = np.meshgrid(np.arange(nx) / nx, np.arange(ny) / ny)
    weather = (
        np.sin(x * 19 + np.sin(y * 31)) * np.sin(y * 13 + x * 7) * d.get("weathering", 0) * 0.25
    )
    rgb *= 1 + weather[:, :, None]
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
        if w + 0.32 > width or low < bottom + 0.3 or low + h > bottom + height - 0.3:
            return
        outer = box(cx, low - 0.16, w + 0.32, h + 0.32)
        arch(paint, outer, tuple(d["trim_colour"]), pointed, (w + 0.32) * 0.5 * sy)
        arch(bump, outer, 0.035, pointed, (w + 0.32) * 0.5 * sy)
        inner = box(cx, low, w, h)
        arch(paint, inner, (35, 39, 37), pointed, w * 0.5 * sy)
        arch(bump, inner, -0.07, pointed, w * 0.5 * sy)
        for k in range(1, d["windows"]["mullions"] + 1):
            xx = (cx - w / 2 + w * k / (d["windows"]["mullions"] + 1)) * sx
            yy = (bottom + height - low - h + 0.5) * sy
            paint.line((xx, yy, xx, inner[3]), fill=(101, 105, 94), width=max(1, round(0.065 * sx)))

    if tower:
        for low, w, h, count in d["tower"]["opening_rows"]:
            for i in range(int(count)):
                opening(width * (i + 1) / (count + 1), low, w, h, "gothic" in d["style"])
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
        if r > 0 and bottom + r < level < bottom + height - r and width > r * 3:
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
        if d["roof"]["material"] == "copper" and bottom + height > d["tower"]["copper_start_m"] + 4:
            opening(width / 2, d["tower"]["copper_start_m"] + 1, width * 0.42, 6, False)
    else:
        w = d["windows"]
        pointed = (
            True
            if any(k in w["type"] for k in ("gothic", "pointed", "lancet"))
            else False
            if "arch" in w["type"]
            else None
        )
        count = max(0, int(width / w["spacing_m"]))
        for low in w.get("rows_m", [w["sill_m"]]):
            for i in range(count):
                opening(width * (i + 0.5) / count, low, w["width_m"], w["height_m"], pointed)
    if d.get("show_entrance"):
        entry = d["entrance"]
        opening(width / 2, entry.get("sill_m", 0.1), entry["width_m"], entry["height_m"], True)
    return maps(np.asarray(albedo), np.asarray(depth), width, height)
