"""Expand a reviewed modernist commercial profile into openings and wall components.

A profile states metric storey levels measured from references: ribbon windows
between continuous slab/balcony bands, full-height glazed floors, a glazed (and
optionally recessed) top storey, a glazed shopfront ground floor, punched
window rows, awnings and flat panels (stair glazing, generic screens). Only the
profile's own wall is expanded; nothing is copied to another wall, and an
invalid or weakly supported profile leaves the wall unchanged.
"""

import copy
import math

MIN_SUPPORT = 0.6


def _number(value, low=0.0, high=math.inf):
    return isinstance(value, (int, float)) and math.isfinite(value) and low <= value <= high


def _colour(value):
    return value if isinstance(value, str) and len(value) == 7 and value.startswith("#") else None


def expand(wall):
    profile = wall.get("modernist")
    if not isinstance(profile, dict) or profile.get("support", 0) < MIN_SUPPORT:
        return wall
    width, height = wall["width_m"], wall["height_m"]
    m0, m1 = profile.get("margins_m", [0.3, 0.3])
    if not (_number(m0, 0, width / 3) and _number(m1, 0, width / 3)):
        return wall
    span = width - m0 - m1
    windows, base_openings, components = [], [], []
    support = profile["support"]

    def frac(x, w, y, h):
        return [x / width, y / height, w / width, h / height]

    def ribbon(bottom, top):
        if _number(bottom, 0, height) and _number(top, bottom + 0.3, height):
            windows.append([m0, bottom, span, top - bottom])

    bands = profile.get("bands")
    levels = []
    if isinstance(bands, dict):
        first, pitch, count = bands.get("first_m"), bands.get("pitch_m"), bands.get("count")
        sill, head = bands.get("sill_m"), bands.get("head_m")
        if not (
            _number(first, 0, height)
            and _number(pitch, 2.4, 6)
            and type(count) is int
            and 0 < count <= 40
            and _number(sill, 0, pitch)
            and _number(head, sill + 0.4, pitch)
            and first + pitch * count <= height + 0.01
        ):
            return wall
        levels = [first + pitch * k for k in range(count + 1)]
        for level in levels[:-1]:
            ribbon(level + sill, level + head)
        slab = bands.get("slab", {})
        if _number(slab.get("depth_m"), 0.1, 3) and _number(slab.get("thickness_m"), 0.05, 0.6):
            components.append(
                dict(
                    kind="slab_band",
                    bounds=frac(m0, span, 0, height),
                    levels_m=levels,
                    depth_m=slab["depth_m"],
                    thickness_m=slab["thickness_m"],
                    parapet_m=slab.get("parapet_m", 0) if _number(slab.get("parapet_m"), 0, 1.5) else 0,
                    colour=_colour(slab.get("colour")),
                    dimensions_source=profile.get("source", "reviewed modernist profile"),
                    support=support,
                )
            )
    for bottom, top in profile.get("glazed_floors_m", []):
        ribbon(bottom + 0.15, top - 0.15)
    top = profile.get("top")
    if isinstance(top, dict) and _number(top.get("bottom_m"), 0, height - 1):
        bottom = top["bottom_m"]
        ribbon(bottom + 0.15, height - 0.35)
        if _number(top.get("recess_m"), 0.2, 4):
            components.append(
                dict(
                    kind="recess",
                    bounds=frac(m0, span, bottom, height - bottom - 0.25),
                    depth_m=top["recess_m"],
                    rows=1,
                    count=1,
                    head="flat",
                    dimensions_source="reviewed recessed glazed top storey",
                    support=support,
                )
            )
        if _number(top.get("roof_slab_m"), 0.1, 3):
            components.append(
                dict(
                    kind="slab_band",
                    bounds=frac(0, width, 0, height),
                    levels_m=[height],
                    depth_m=top["roof_slab_m"],
                    thickness_m=0.25,
                    parapet_m=0,
                    colour=_colour(top.get("colour")),
                    dimensions_source="reviewed thin flat roof slab",
                    support=support,
                )
            )
    punched = profile.get("windows")
    if isinstance(punched, dict):
        keys = ("first_m", "pitch_m", "bottom_m", "width_m", "height_m")
        if all(_number(punched.get(k), 0, max(width, height)) for k in keys) and (
            type(punched.get("count")) is int and 0 < punched["count"] <= 64
        ):
            for i in range(punched["count"]):
                x = punched["first_m"] + i * punched["pitch_m"]
                if x + punched["width_m"] <= width - m1:
                    windows.append([x, punched["bottom_m"], punched["width_m"], punched["height_m"]])
    ground = profile.get("ground")
    if isinstance(ground, dict) and _number(ground.get("height_m"), 2, 10):
        pane = ground.get("pane_m", 2.4) if _number(ground.get("pane_m"), 0.8, 6) else 2.4
        count = max(1, round(span / pane))
        pane = span / count
        for i in range(count):
            base_openings.append([m0 + i * pane + 0.06, 0.1, pane - 0.12, ground["height_m"] - 0.6])
    for awning in profile.get("awnings", []):
        x0, x1 = awning.get("span_m", [m0, width - m1])
        if not (
            _number(x0, 0, width)
            and _number(x1, x0 + 0.5, width)
            and _number(awning.get("attach_m"), 1.5, height)
            and _number(awning.get("depth_m"), 0.3, 4)
            and _number(awning.get("drop_m", 0.5), 0, 2)
        ):
            continue
        components.append(
            dict(
                kind="awning",
                bounds=frac(x0, x1 - x0, awning["attach_m"] - 0.5, 0.5),
                attach_m=awning["attach_m"],
                depth_m=awning["depth_m"],
                drop_m=awning.get("drop_m", 0.5),
                colour=_colour(awning.get("colour")),
                support=support,
            )
        )
    for panel in profile.get("panels", []):
        rect = panel.get("rect_m")
        if not (
            isinstance(rect, list)
            and len(rect) == 4
            and all(_number(v, 0) for v in rect)
            and rect[0] + rect[2] <= width
            and rect[1] + rect[3] <= height
            and _number(panel.get("outward_m", 0.06), 0.01, 0.6)
        ):
            continue
        components.append(
            dict(
                kind="panel",
                bounds=frac(*rect),
                outward_m=panel.get("outward_m", 0.06),
                glass=bool(panel.get("glass")),
                colour=_colour(panel.get("colour")),
                role=str(panel.get("role", "panel")),
                support=support,
            )
        )
    if not (windows or base_openings or components):
        return wall
    result = copy.deepcopy(wall)
    result.pop("modernist", None)
    w = result["windows"]
    w["rectangles_m"] = windows
    w["head_shapes"] = ["flat"] * len(windows)
    w["surround_shapes"] = ["none"] * len(windows)
    w["opening_roles"] = ["window"] * len(windows)
    pane = bands.get("pane_m") if isinstance(bands, dict) else None
    if _number(pane, 0.6, 4) and windows:
        w["divisions"] = dict(value=[min(32, max(1, round(span / pane))), 1], source="reviewed mullion pitch", support=support)
        w["pane_span_m"] = dict(value=[m0, span], source="reviewed ribbon extent", support=support)
    if base_openings:
        result["base"]["openings_m"] = base_openings
        result["base"]["head_shapes"] = ["flat"] * len(base_openings)
        result["base"]["surround_shapes"] = ["none"] * len(base_openings)
        result["base"]["variant"] = dict(value="shops", source="reviewed glazed shopfront", support=support)
        result["base"]["shopfront"] = dict(value=True, source="reviewed glazed shopfront", support=support)
    result["bays"]["support"] = max(result["bays"].get("support", 0), support)
    result["geometry_components"] = result.get("geometry_components", []) + components
    result["observation"].update(
        needs_review=False,
        scope="full-wall",
        dimension_source="reviewed modernist storey profile",
    )
    return result
