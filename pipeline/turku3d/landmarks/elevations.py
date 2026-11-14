"""Measured elevations in building coordinates, shared across fragmented roof walls.

Each elevation owns its material and openings in metres. Survey wall fragments
sample that same elevation, so a small roof triangle cannot become a whole shop.
Only parameters enter this module; reference photographs never become textures.
"""

import json
from functools import lru_cache

import numpy as np
from PIL import Image, ImageDraw

from .procedural import arch, fieldstone, maps, wall_surface
from .openings import panelled_door


def select(vertices, elevations):
    """The elevation a wall piece samples: among the rows facing its way, the one whose plane is
    nearest. A building with a courtyard has inner walls facing like its street fronts; each takes
    the row measured on its own plane, never a street front's openings.

    A row with a ``plane_band_m`` [behind, ahead] covers only the walls whose plane lies in that band
    around its own and that overlap its width; the side of a bay, a recess or a tower top elsewhere
    would sample a sliver of its openings (a dark window column) and takes the plain wall instead."""
    direction = vertices[1, [0, 2]] - vertices[0, [0, 2]]
    direction /= np.linalg.norm(direction)
    normal = np.array([direction[1], -direction[0]])
    centre = vertices[:2, [0, 2]].mean(axis=0)
    matches = []
    for row in elevations:
        if float(normal @ row["normal_xz"]) <= 0.9:
            continue
        plane = float((centre - row["origin_xz"]) @ row["normal_xz"])
        band = row.get("plane_band_m")
        if band is not None:
            u = (vertices[:2, [0, 2]] - row["origin_xz"]) @ np.asarray(row["axis_xz"]) / row["width_m"]
            if not band[0] <= plane <= band[1] or u.max() <= 0 or u.min() >= 1:
                continue
        matches.append((abs(plane), row))
    return min(matches, key=lambda pair: pair[0])[1] if matches else None


def coordinates(vertices, base, row):
    axis = np.asarray(row["axis_xz"])
    x = (vertices[:, [0, 2]] - row["origin_xz"]) @ axis
    return np.c_[x / row["width_m"],
                 1 - (vertices[:, 1] - base - row.get("base_offset_m", 0)) / row["height_m"]]


@lru_cache(maxsize=32)
def texture(description_json, elevation_json):
    d, row = json.loads(description_json), json.loads(elevation_json)
    width, height = row["width_m"], row["height_m"]
    rgb, relief = wall_surface(width, height, d, 17)
    ny, nx = relief.shape
    sx, sy = nx / width, ny / height
    # Stone-faced parts of a rendered front (a granite ground storey, granite bays): the description's
    # stone over each [x, width, low, height] zone, in elevation metres.
    if row.get("stone_zones"):
        stone, stone_relief = fieldstone(width, height, d["stone"], 17)
        for x, w, low, h in row["stone_zones"]:
            cols = slice(max(0, round(x * sx)), min(nx, round((x + w) * sx)))
            rows = slice(max(0, round((height - low - h) * sy)), min(ny, round((height - low) * sy)))
            rgb[rows, cols] = stone[rows, cols]
            relief[rows, cols] = stone_relief[rows, cols]
    albedo = Image.fromarray(np.clip(rgb, 0, 255).astype(np.uint8))
    depth = Image.fromarray(relief.astype(np.float32), "F")
    paint, bump = ImageDraw.Draw(albedo), ImageDraw.Draw(depth)
    trim = tuple(d["trim_colour"])

    def rect(x, low, w, h):
        return (x * sx, (height - low - h) * sy, (x + w) * sx, (height - low) * sy)

    def band(x, low, w, h, colour=trim, relief=.025):
        box = rect(x, low, w, h)
        paint.rectangle(box, fill=colour)
        bump.rectangle(box, fill=relief)

    band(0, 0, width, row.get("plinth_m", .55), tuple(d["stone"]["colour"]), .015)
    for low, h in row.get("bands", []):
        band(0, low, width, h)
    for x, w, low, high in row.get("pilasters", []):
        band(x - w / 2, low, w, high - low, tuple(d["brick"]["colour"]), .08)
        for level in np.arange(low + .3, high, .65):
            band(x - w * .6, float(level), w * 1.2, .14)
    openings = list(row.get("openings", []))
    for repeat in row.get("repeated_openings", []):
        for x in np.arange(repeat["start_m"], width - repeat.get("end_margin_m", 1), repeat["spacing_m"]):
            openings.append({**repeat, "x_m":float(x)})
    for opening in openings:
        x, low, w, h = (opening[k] for k in ("x_m", "sill_m", "width_m", "height_m"))
        frame = opening.get("trim_m", .12)
        shape = opening.get("shape", "round")
        pointed = None if shape == "rect" else shape == "pointed"
        outer = rect(x - w / 2 - frame, low - frame, w + 2 * frame, h + 2 * frame)
        inner = rect(x - w / 2, low, w, h)
        arch(paint, outer, trim, pointed, (w / 2 + frame) * sy)
        arch(bump, outer, .045, pointed, (w / 2 + frame) * sy)
        door = opening.get("kind") == "door"
        colour = tuple(opening.get("colour", [91, 63, 44] if door else [65, 70, 67]))
        arch(paint, inner, colour, pointed, w / 2 * sy)
        arch(bump, inner, -.06, pointed, w / 2 * sy)
        if door and opening.get("style") == "panelled_glazed":
            panelled_door(paint, bump, inner, opening, sx, sy)
            continue
        # Clip mullions to the opening: no bars protrude through the arch crown.
        mask = Image.new("L", (nx, ny)); mask_paint = ImageDraw.Draw(mask)
        arch(mask_paint, inner, 255, pointed, w / 2 * sy)
        bars = Image.new("RGB", (nx, ny)); bp = ImageDraw.Draw(bars)
        bar_mask = Image.new("L", (nx, ny)); bm = ImageDraw.Draw(bar_mask)
        bar_colour = (116, 94, 69) if door else tuple(opening.get("frame_colour", [180, 179, 160]))
        for k in range(1, opening.get("columns", 2)):
            xx = (x - w / 2 + k * w / opening.get("columns", 2)) * sx
            line = (xx, inner[1], xx, inner[3])
            bp.line(line, fill=bar_colour, width=max(1, round(.045 * sx)))
            bm.line(line, fill=255, width=max(1, round(.045 * sx)))
        for k in range(1, opening.get("rows", 3)):
            yy = inner[3] - h * k / opening.get("rows", 3) * sy
            line = (inner[0], yy, inner[2], yy)
            bp.line(line, fill=bar_colour, width=max(1, round(.045 * sy)))
            bm.line(line, fill=255, width=max(1, round(.045 * sy)))
        clipped = Image.fromarray(np.minimum(np.asarray(mask), np.asarray(bar_mask)))
        albedo.paste(bars, mask=clipped)
    for x, low, radius in row.get("medallions", []):
        box = rect(x - radius, low - radius, radius * 2, radius * 2)
        paint.ellipse(box, fill=trim); bump.ellipse(box, fill=.05)
    return maps(np.asarray(albedo), np.asarray(depth), width, height)
