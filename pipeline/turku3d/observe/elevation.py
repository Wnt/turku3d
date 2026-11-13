"""A flat drawing of what the generator builds on a wall, on the rulers of that wall's photo crop.

Render and compare without a browser: the generator's own layout of a described wall (storeys, bays, every opening
with its outline and lights, plinth, zones) is drawn at the scale and extent of a rectified crop, and moved back onto
that crop's rulers with the wall's registration. Where the description is right, the drawing and the photograph
agree place by place; the reader is shown both and asked for the differences. The drawing says nothing about
materials in light: that is the game render's part.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from .crops import RULER_PX, _ruler

SKY = (232, 236, 240)
GLASS = (58, 70, 84)


def _rgb(colour, fallback=(200, 200, 200)):
    if isinstance(colour, str) and len(colour) == 7 and colour.startswith("#"):
        try:
            return tuple(int(colour[i:i + 2], 16) for i in (1, 3, 5))
        except ValueError:
            return fallback
    return fallback


def localised(doc: dict) -> dict:
    """The description as the generator sees it: laid over its priors, in the building's own coordinates."""
    from turku3d.world.kinds.building import compile as compiler
    from turku3d.world.kinds.building import describe, workbench

    key = doc["id"].split("/", 1)[1]
    near = next((w["frame"]["p0"] for w in doc.get("walls", []) if w.get("frame")), None)
    shell, register = workbench.shell_for(key, near)
    return compiler._localise(describe.effective(key, shell, register, doc), compiler.building_origin(shell))


def _laid_out(local: dict, wall_id: str):
    """(wall, shell wall, where the described wall starts on it, resolved wall, layout), or None without a shell wall."""
    from turku3d.world.kinds.building import facade as facades
    from turku3d.world.kinds.building import priors
    from turku3d.world.kinds.building import shell as shells

    wall = next((w for w in local.get("walls", []) if w["id"] == wall_id), None)
    hit = shells.match(shells.read(local["shell"]).facades, wall["frame"]["p0"], wall["frame"]["p1"]) if wall else None
    if hit is None:
        return None
    start = float((np.asarray(wall["frame"]["p0"], float) - hit.origin[:2]) @ hit.u_dir[:2])
    resolved = priors.resolve_wall(local, wall)
    return wall, hit, start, resolved, facades.layout(hit, resolved, local, [])


def draw_wall(doc: dict, wall_id: str, crop: dict, registration: tuple[float, float, float], path: Path) -> bool:
    """Write the drawing of one described wall over the extent of `crop`; false when the wall has no shell wall."""
    from turku3d.world.kinds.building import facade as facades
    from turku3d.world.kinds.building import openings, priors
    from turku3d.world.kinds.building.materials import Palette

    local = localised(doc)
    laid = _laid_out(local, wall_id)
    if laid is None:
        return False
    wall, hit, start, resolved, plan = laid
    palette = Palette(local.get("materials"))
    offset, scale, shift = registration
    ppm, x_left, z_high = crop["px_per_m"], crop["x_left_m"], crop["z_high_m"]
    width, height = crop["size"][0] - RULER_PX, crop["size"][1] - RULER_PX

    def px(u, v):  # shell-wall coordinates to the crop's pixels, through the crop's own ruler
        ruler_u, ruler_v = (u - start) / scale + offset, (v - plan.datum) + shift
        return (ruler_u - x_left) * ppm, (z_high - ruler_v) * ppm

    def fill(polygon, colour, outline=None, line=1):
        for part in getattr(polygon, "geoms", [polygon]):
            if part.is_empty or part.geom_type != "Polygon":
                continue
            draw.polygon([px(u, v) for u, v in part.exterior.coords], fill=colour, outline=outline, width=line)

    image = Image.new("RGB", (width, height), SKY)
    draw = ImageDraw.Draw(image)
    wall_colour = _rgb(palette.record(resolved.get("material")).get("colour"))
    fill(hit.outline, wall_colour, outline=(60, 60, 60))
    from shapely.geometry import box

    for floor, storey_height, storey in plan.floors:  # a storey with its own material, and the floor lines
        if storey.get("material"):
            fill(box(0, floor, hit.width, floor + storey_height).intersection(hit.outline),
                 _rgb(palette.record(storey["material"]).get("colour"), wall_colour))
    for zone in resolved.get("zones", []):
        u0 = start + zone.get("u_from_m", -start)
        u1 = start + zone.get("u_to_m", hit.width - start)
        v0, v1 = plan.datum + zone.get("from_m", 0.0), plan.datum + zone.get("to_m", hit.height)
        fill(box(u0, v0, u1, v1).intersection(hit.outline), _rgb(palette.record(zone.get("material")).get("colour"), wall_colour))
    ground_floor = resolved.get("ground_floor_m", resolved.get("plinth", {}).get("height_m", 0.0))
    if ground_floor > 0.05:
        plinth = palette.record(resolved.get("plinth", {}).get("material"), fallback={"family": "granite"})
        fill(box(0, 0, hit.width, plan.datum + ground_floor).intersection(hit.outline), _rgb(plinth.get("colour"), (150, 150, 150)))
    trim = _rgb(priors.trim_colour(local), tuple(int(c * 0.85) for c in wall_colour))
    for band in resolved.get("bands", []):
        v = facades.anchor(plan, band["at"], local) if "at" in band else plan.datum + band.get("v_m", 0.0)
        u0, u1 = start + band.get("u_from_m", -start), start + band.get("u_to_m", hit.width - start)
        fill(box(u0, v, u1, v + band.get("height_m", 0.15)).intersection(hit.outline), _rgb(band.get("colour"), trim))
    for floor, _, _ in plan.floors:
        draw.line([px(0, floor), px(hit.width, floor)], fill=tuple(int(c * 0.8) for c in wall_colour))
    for p in plan.placed:
        frame = p.spec.get("frame", {})
        frame_colour, frame_width = _rgb(frame.get("colour"), (245, 245, 240)), float(frame.get("width_m", 0.07))
        shape = openings.outline(p.spec, p.width, p.height)
        from shapely import affinity

        fill(affinity.translate(shape, p.u0, p.v0), frame_colour, outline=(40, 40, 40))
        kind = p.spec.get("kind", "window")
        if kind in ("door", "gate") and not p.spec.get("glazed"):
            inner = box(p.u0 + frame_width, p.v0, p.u0 + p.width - frame_width, p.v0 + p.height - frame_width)
            fill(inner, _rgb(p.spec.get("leaf", {}).get("colour") or p.spec.get("colour"), (90, 70, 55)))
            continue
        try:
            cells = openings.lights(p.spec, p.width, p.height, frame_width)
        except Exception:  # noqa: BLE001 - a light pattern this drawing does not know stays one pane
            cells = [(frame_width, frame_width, p.width - frame_width, p.height - frame_width)]
        for c in cells:
            if len(c) == 4:
                fill(box(p.u0 + c[0], p.v0 + c[1], p.u0 + c[2], p.v0 + c[3]).intersection(affinity.translate(shape, p.u0, p.v0)), GLASS)
    path.parent.mkdir(parents=True, exist_ok=True)
    length = float(np.linalg.norm(np.subtract(wall["frame"]["p1"], wall["frame"]["p0"])))
    _ruler(image, x_left, z_high, ppm, length, wall["frame"].get("height_m", hit.height)).save(path, quality=90)
    return True
