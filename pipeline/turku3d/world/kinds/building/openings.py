"""Opening assemblies in local wall coordinates: the hole's bottom-left corner is the origin, d = 0 the wall face.

`build(spec, width, height, wall_material, palette)` returns a Soup with the reveal, frame, glass, sill, surround,
pediment, leaves and steps of one opening type at one size. The caller places it and cuts the hole.
"""

import numpy as np
from shapely import affinity
from shapely.geometry import Polygon
from shapely.geometry import box as rectangle
from shapely.ops import unary_union

from . import mesh
from .materials import Palette, shade

FRAME_DEPTH = 0.035
MULLION = 0.075
BAR = 0.032
STEP_RISE = 0.16
STEP_GOING = 0.3


def outline(spec: dict, width: float, height: float) -> Polygon:
    """The hole's outline with its bottom-left corner at the origin."""
    shape = spec.get("shape", "rect")
    if shape in ("circle", "oval", "hexagon", "octagon", "diamond"):
        sides = {"circle": 24, "oval": 24, "hexagon": 6, "octagon": 8, "diamond": 4}[shape]
        start = (
            np.pi / sides
            if shape == "octagon"
            else 0.0
            if shape == "hexagon"
            else np.pi / 2
            if shape == "diamond"
            else 0.0
        )
        angles = start + np.arange(sides) * 2 * np.pi / sides
        return Polygon(
            [
                (width / 2 + width / 2 * np.cos(a), height / 2 + height / 2 * np.sin(a))
                for a in angles
            ]
        )
    if shape == "lunette":
        return mesh.arch_polygon(width, height, "basket", rise=height * 0.999)
    return mesh.arch_polygon(width, height, spec.get("head", "flat"), spec.get("head_rise_m"))


def _split(cell, xs=(), ys=(), thickness=MULLION):
    """Cut a rectangle (u0, v0, u1, v1) at absolute u and v positions by bars of a thickness: the lights left."""
    u0, v0, u1, v1 = cell
    us = [u0] + [x for x in sorted(xs) if u0 < x < u1] + [u1]
    vs = [v0] + [y for y in sorted(ys) if v0 < y < v1] + [v1]
    half = thickness / 2
    out = []
    for i in range(len(us) - 1):
        for j in range(len(vs) - 1):
            a = us[i] + (half if i > 0 else 0.0)
            b = us[i + 1] - (half if i < len(us) - 2 else 0.0)
            c = vs[j] + (half if j > 0 else 0.0)
            d = vs[j + 1] - (half if j < len(vs) - 2 else 0.0)
            if b - a > 0.02 and d - c > 0.02:
                out.append((a, c, b, d))
    return out


def lights(spec: dict, width: float, height: float, frame_width: float) -> list:
    """The glazed rectangles of a window: the frame's inside divided by its mullions, transoms and bars."""
    inner = (frame_width, frame_width, width - frame_width, height - frame_width)
    division = spec.get("lights", {})
    pattern = division.get("pattern", "single")
    w = width - 2 * frame_width
    transom = height * division.get("transom_at", 0.7)
    if pattern == "two_leaf":
        cells = _split(inner, xs=[width / 2])
    elif pattern == "three_leaf":
        cells = _split(inner, xs=[frame_width + w / 3, frame_width + 2 * w / 3])
    elif pattern == "t_frame":
        top = _split(inner, ys=[transom])
        cells = [c for c in top if c[1] >= transom - 1e-6]
        for lower in (c for c in top if c[1] < transom - 1e-6):
            cells += _split(lower, xs=[width / 2])
    elif pattern == "cross":
        cells = _split(inner, xs=[width / 2], ys=[transom])
    elif pattern == "grid":
        cols, rows = division.get("cols", 2), division.get("rows", 3)
        cells = _split(
            inner,
            xs=[frame_width + w * k / cols for k in range(1, cols)],
            ys=[frame_width + (height - 2 * frame_width) * k / rows for k in range(1, rows)],
            thickness=BAR,
        )
    elif pattern == "side_vent":
        share = division.get("vent_share", 0.28)
        x = frame_width + w * (share if division.get("vent", "left") == "left" else 1 - share)
        cells = _split(inner, xs=[x])
    elif pattern == "top_vent":
        cells = _split(inner, ys=[height * division.get("transom_at", 0.74)])
    elif pattern == "ribbon":
        count = max(1, division.get("cols", round(width / 1.25)))
        cells = _split(inner, xs=[frame_width + w * k / count for k in range(1, count)])
    else:
        cells = [inner]
    bars = division.get("bars")
    if bars and pattern != "grid":
        fine = []
        for cell in cells:
            cols, rows = bars.get("cols", 1), bars.get("rows", 1)
            fine += _split(
                cell,
                xs=[cell[0] + (cell[2] - cell[0]) * k / cols for k in range(1, cols)],
                ys=[cell[1] + (cell[3] - cell[1]) * k / rows for k in range(1, rows)],
                thickness=BAR,
            )
        cells = fine
    return cells


def _glazing(soup, shape, cells, depth, frame_key, glass_key, part="frames"):
    """A frame face at `depth` with the cells glazed FRAME_DEPTH behind it. Cells are clipped to the shape."""
    rectangular = len(shape.exterior.coords) == 5
    inset = (
        None if rectangular else shape.buffer(-0.5 * min(0.07, shape.bounds[2] / 8), join_style=2)
    )
    panes = []
    for cell in cells:
        pane = rectangle(*cell)
        if inset is not None:
            pane = pane.intersection(inset)
        panes += [p for p in mesh.polygons_of(pane) if p.area > 4e-4]
    frame = shape.difference(unary_union(panes)) if panes else shape
    mesh.face(soup, part, frame_key, frame, depth)
    for pane in panes:
        ring = np.asarray(pane.exterior.coords)[:-1]
        if Polygon(ring).exterior.is_ccw is False:
            ring = ring[::-1]
        mesh.ring_walls(soup, part, frame_key, ring, depth - FRAME_DEPTH, depth, outward=False)
        mesh.face(soup, "glass", glass_key, pane, depth - FRAME_DEPTH)


def _sill(soup, spec, width, palette: Palette, wall_material):
    sill = spec.get("sill", {})
    kind = sill.get("kind", "none")
    if kind == "none":
        return 0.0
    height = sill.get("height_m", 0.06)
    projection = sill.get("projection_m", 0.05)
    colour = sill.get("colour")
    if kind == "sheet_metal":
        key = palette.key({"family": "metal", "colour": colour or "#8a8d8f"})
    elif kind == "stone":
        key = palette.key(
            {"family": "granite", "colour": colour or "#9a958d", "finish": "slab", "scale_m": 2.0}
        )
    elif kind == "wood":
        key = palette.key({"family": "painted_wood", "colour": colour or "#efece4"})
    elif kind == "brick":
        key = palette.key({"family": "brick", "colour": colour or "#8f4a38", "bond": "header"})
    else:
        key = palette.paint(colour, wall_material, family="plaster")
    mesh.box(soup, "trim", key, -0.05, -height, width + 0.05, 0.0, 0.0, projection)
    return height


def _surround(
    soup, spec, shape, width, height, sill_height, palette: Palette, wall_material, trim_colour
):
    """The surround's parts; returns the height of its top edge above the opening head."""
    surround = spec.get("surround", {})
    kind = surround.get("kind", "none")
    if kind == "none":
        return 0.0
    band = surround.get("width_m", 0.12)
    projection = surround.get("projection_m", 0.03)
    key = palette.paint(surround.get("colour", trim_colour), wall_material)
    flat_head = len(shape.exterior.coords) == 5
    top = band
    if kind in ("eared", "carved") and flat_head:
        ear = band * 0.6
        top = band * 1.25
        mesh.box(soup, "trim", key, -band, -sill_height, 0.0, height, 0.0, projection)
        mesh.box(soup, "trim", key, width, -sill_height, width + band, height, 0.0, projection)
        mesh.box(
            soup,
            "trim",
            key,
            -band - ear,
            height,
            width + band + ear,
            height + top,
            0.0,
            projection * 1.3,
        )
        mesh.box(
            soup,
            "trim",
            key,
            -band - ear,
            -sill_height - band,
            width + band + ear,
            -sill_height,
            0.0,
            projection * 1.3,
        )
        if kind == "carved":
            for side in (-1, 1):
                x = -band - ear if side < 0 else width + band + ear
                drop = Polygon(
                    [(x, height), (x - side * ear, height), (x - side * ear, height - band * 1.8)]
                )
                mesh.prism(soup, "trim", key, drop.buffer(0), 0.0, projection)
    else:
        outer = shape.buffer(band, join_style=2)
        ring = outer.difference(shape).intersection(
            rectangle(-band - 1, 0.0 if sill_height else -band, width + band + 1, height + band + 1)
        )
        mesh.prism(soup, "trim", key, ring, 0.0, projection)
        if kind == "moulded":
            edge = outer.difference(shape.buffer(band * 0.62, join_style=2)).intersection(
                rectangle(-band - 1, 0.0, width + band + 1, height + band + 1)
            )
            mesh.prism(soup, "trim", key, edge, projection, projection * 1.7, front=True)
        elif kind == "pilastered":
            mesh.box(
                soup,
                "trim",
                key,
                -band * 1.2,
                height,
                width + band * 1.2,
                height + band * 1.3,
                0.0,
                projection * 2.0,
            )
            top = band * 1.3
        elif kind == "rusticated":
            course = 0.3
            for k in range(int(height / course)):
                if k % 2 == 0:
                    for x0, x1 in ((-band * 1.7, -band), (width + band, width + band * 1.7)):
                        mesh.box(
                            soup,
                            "trim",
                            key,
                            x0,
                            k * course,
                            x1,
                            (k + 1) * course - 0.02,
                            0.0,
                            projection,
                        )
    if surround.get("keystone"):
        cx = width / 2
        stone = Polygon(
            [
                (cx - band * 0.45, height - band * 0.2),
                (cx + band * 0.45, height - band * 0.2),
                (cx + band * 0.7, height + top + band * 0.25),
                (cx - band * 0.7, height + top + band * 0.25),
            ]
        )
        mesh.prism(soup, "trim", key, stone, 0.0, projection * 2.2)
    apron = surround.get("apron")
    if apron and apron.get("height_m", 0) > 0:
        a0, a1 = (
            -sill_height - apron["height_m"],
            -sill_height - (band if kind in ("eared", "carved") else 0.0),
        )
        colour = apron.get("colour", surround.get("colour", trim_colour))
        if apron.get("kind") == "boarded":
            apron_key = palette.key(
                {
                    "family": "weatherboard",
                    "colour": colour,
                    "orientation": "vertical",
                    "profile": "beaded",
                    "scale_m": 0.09,
                }
            )
        else:
            apron_key = palette.paint(colour, wall_material)
        if a1 - a0 > 0.03:
            mesh.box(soup, "trim", apron_key, 0.0, a0, width, a1, 0.0, projection * 0.5)
            if kind not in ("eared", "carved"):
                # The surround runs down both sides of the apron: window and apron read as one tall recess.
                for x0, x1 in ((-band, 0.0), (width, width + band)):
                    mesh.box(soup, "trim", key, x0, a0, x1, 0.0, 0.0, projection)
            if apron.get("kind") == "panel":
                inner = rectangle(0.12, a0 + 0.1, width - 0.12, a1 - 0.1)
                rim = inner.buffer(0.05, join_style=2).difference(inner)
                mesh.prism(soup, "trim", key, rim, projection * 0.5, projection)
                if "colour2" in apron:
                    fill = palette.paint(apron["colour2"], wall_material)
                    mesh.face(soup, "trim", fill, inner, projection * 0.5 + 0.004)
                    r = min(width, a1 - a0) * 0.2
                    disc = Polygon(
                        [
                            (width / 2 + r * np.cos(t), (a0 + a1) / 2 + r * np.sin(t))
                            for t in np.linspace(0, 2 * np.pi, 13)[:-1]
                        ]
                    )
                    mesh.prism(
                        soup,
                        "trim",
                        key,
                        disc.difference(disc.buffer(-0.04)),
                        projection * 0.5,
                        projection,
                    )
    return top


def _pediment(soup, spec, width, base, palette: Palette, wall_material, trim_colour, band):
    pediment = spec.get("pediment", {})
    kind = pediment.get("kind", "none")
    if kind == "none":
        return
    height = pediment.get("height_m", 0.25)
    projection = pediment.get("projection_m", 0.1)
    key = palette.paint(
        pediment.get("colour", spec.get("surround", {}).get("colour", trim_colour)), wall_material
    )
    u0, u1 = -band - 0.08, width + band + 0.08
    if kind == "cornice":
        # A tall one is a frieze board under a cornice on two consoles: the hood of a wooden house's window.
        frieze = height * 0.5 if height > 0.3 else 0.0
        if frieze:
            mesh.box(soup, "trim", key, -band, base, width + band, base + frieze, 0.0, 0.03)
            for x in (-band, width + band - 0.09):
                mesh.extrude_profile(
                    soup,
                    "trim",
                    key,
                    x,
                    x + 0.09,
                    [
                        (0.0, base + frieze * 0.25),
                        (projection * 0.35, base + frieze * 0.4),
                        (projection * 0.8, base + frieze),
                        (0.0, base + frieze),
                    ],
                )
        low = base + frieze
        tall = height - frieze
        profile = [
            (0.0, low),
            (projection * 0.45, low),
            (projection * 0.6, low + tall * 0.35),
            (projection, low + tall * 0.7),
            (projection, low + tall),
            (0.0, low + tall),
        ]
        mesh.extrude_profile(soup, "trim", key, u0, u1, profile)
    elif kind == "hood":
        profile = [(0.0, base), (projection, base), (projection, base + 0.04), (0.0, base + height)]
        mesh.extrude_profile(soup, "trim", key, u0, u1, profile)
    elif kind in ("triangular", "carved_crest"):
        if kind == "carved_crest":
            u0, u1 = width * 0.2, width * 0.8
        shelf = min(0.06, height * 0.3)
        mesh.box(soup, "trim", key, u0, base, u1, base + shelf, 0.0, projection)
        mesh.prism(
            soup,
            "trim",
            key,
            Polygon([(u0, base + shelf), (u1, base + shelf), ((u0 + u1) / 2, base + height)]),
            0.0,
            projection * 0.7,
        )
    elif kind == "segmental":
        arc = mesh.arch_polygon(u1 - u0, height, "segmental", rise=height * 0.98)
        mesh.prism(soup, "trim", key, affinity.translate(arc, u0, base), 0.0, projection)
    elif kind == "ornament":
        cx, r = width / 2, height * 0.42
        disc = Polygon(
            [
                (cx + r * np.cos(a), base + height * 0.5 + r * np.sin(a))
                for a in np.linspace(0, 2 * np.pi, 13)[:-1]
            ]
        )
        wings = [
            Polygon(
                [
                    (cx - side * r * 0.8, base + height * 0.2),
                    (cx - side * r * 2.4, base + height * 0.08),
                    (cx - side * r * 1.0, base + height * 0.62),
                ]
            )
            for side in (-1, 1)
        ]
        mesh.prism(
            soup, "trim", key, unary_union([disc] + [w.buffer(0) for w in wings]), 0.0, projection
        )


def _leaf_panels(soup, key, u0, v0, u1, v1, depth, rows=3):
    """Raised panels on a door leaf."""
    margin = 0.11
    span = (v1 - v0 - margin) / rows
    for k in range(rows):
        a, b = v0 + margin + k * span, v0 + (k + 1) * span
        if b - a > 0.12 and u1 - u0 > 2 * margin + 0.1:
            mesh.box(soup, "doors", key, u0 + margin, a, u1 - margin, b, depth, depth + 0.018)


def _door(soup, spec, shape, width, height, depth, palette: Palette):
    leaf = spec.get("leaf", {})
    frame = spec.get("frame", {})
    frame_width = frame.get("width_m", 0.08)
    colour = leaf.get("colour", "#5a4334")
    style = leaf.get("style", "panelled")
    frame_key = palette.key({"family": "painted_wood", "colour": frame.get("colour", colour)})
    leaf_key = (
        palette.key(
            {
                "family": "weatherboard",
                "colour": colour,
                "orientation": "vertical",
                "profile": "tongue_groove",
                "scale_m": 0.11,
            }
        )
        if style == "boarded"
        else palette.key({"family": "painted_wood", "colour": colour, "finish": "gloss"})
    )
    glass_key = palette.key(
        {"family": "glass"},
        **{
            k: v
            for k, v in spec.get("glass", {}).items()
            if k in ("tint", "reflectivity", "interior")
        },
    )
    fanlight = leaf.get("fanlight", "none")
    round_head = len(shape.exterior.coords) != 5
    transom = height
    if fanlight != "none" or round_head:
        transom = height - leaf.get("fanlight_h_m", width / 2 if round_head else 0.45)
        transom = max(transom, height * 0.6)
    leaves = max(1, leaf.get("leaves", 1))
    inner_w = width - 2 * frame_width
    leaf_rects = [
        (
            frame_width + inner_w * k / leaves + (0.01 if k else 0),
            0.02,
            frame_width + inner_w * (k + 1) / leaves - (0.01 if k < leaves - 1 else 0),
            transom - 0.03,
        )
        for k in range(leaves)
    ]
    solid = shape
    cells = []
    if transom < height:
        fan = rectangle(frame_width, transom + 0.03, width - frame_width, height - frame_width)
        cells += _split(
            fan.bounds, xs=[width / 2] if leaves > 1 or width > 1.3 else [], thickness=BAR * 1.5
        )
    if style in ("glazed", "half_glazed"):
        for u0, v0, u1, v1 in leaf_rects:
            rail = 0.1
            low = v0 + ((v1 - v0) * 0.45 if style == "half_glazed" else 0.25)
            cells.append((u0 + rail, low, u1 - rail, v1 - rail))
    if style == "none":
        cells = []
    _glazing(
        soup,
        solid,
        cells,
        depth,
        leaf_key
        if style in ("glazed", "half_glazed", "flush", "boarded", "panelled")
        else frame_key,
        glass_key,
        part="doors",
    )
    if style == "panelled":
        for u0, v0, u1, v1 in leaf_rects:
            _leaf_panels(soup, leaf_key, u0, v0, u1, v1, depth)
    elif style == "half_glazed":
        for u0, v0, u1, v1 in leaf_rects:
            _leaf_panels(soup, leaf_key, u0, v0, u1, v0 + (v1 - v0) * 0.42, depth, rows=1)
    # The frame: a rim round the opening standing proud of the leaf.
    rim = shape.difference(shape.buffer(-frame_width, join_style=2))
    mesh.prism(soup, "doors", frame_key, rim, depth, depth + 0.03)
    if transom < height:
        mesh.box(
            soup,
            "doors",
            frame_key,
            frame_width,
            transom - 0.03,
            width - frame_width,
            transom + 0.03,
            depth,
            depth + 0.03,
        )
    if leaves > 1:
        for k in range(1, leaves):
            x = frame_width + inner_w * k / leaves
            mesh.box(
                soup,
                "doors",
                frame_key,
                x - 0.012,
                0.0,
                x + 0.012,
                transom - 0.03,
                depth,
                depth + 0.012,
            )


def _gate(soup, spec, shape, width, height, depth, palette: Palette, wall_material):
    """A gateway: the passage walls, a dark end, and the gate leaves at the front."""
    passage = spec.get("passage", {})
    leaf = spec.get("leaf", {})
    deep = passage.get("depth_m", 3.5 if passage.get("depth", "through") == "through" else 0.6)
    # Little daylight reaches into a passage: unless somebody described its lining, it is the wall in deep shade.
    wall_record = palette.records.get(wall_material, {})
    shaded = {**wall_record, "colour": shade(wall_record.get("colour", "#8a857d"), 0.38)}
    lining = palette.key(passage.get("lining"), fallback=shaded)
    ring = np.asarray(shape.exterior.coords)[:-1]
    mesh.ring_walls(soup, "walls", lining, ring, -deep, -depth, outward=False)
    end = palette.flat("#141414") if passage.get("depth", "through") == "through" else lining
    mesh.face(soup, "walls", end, shape, -deep)
    floor = palette.flat("#3a3937", roughness=0.95)
    mesh.box(soup, "walls", floor, 0.0, -0.02, width, 0.015, -deep, -depth)
    style = leaf.get("style", "none")
    colour = leaf.get("colour", "#2e2e2e")
    if style == "bars":
        key = palette.key({"family": "metal", "colour": colour})
        mesh.prism(
            soup,
            "doors",
            key,
            shape.difference(shape.buffer(-0.05, join_style=2)),
            -depth - 0.04,
            -depth,
        )
        inside = shape.buffer(-0.05, join_style=2)
        count = max(2, int(width / 0.13))
        bars = [
            rectangle(
                width * (k + 0.5) / count - 0.011, 0.0, width * (k + 0.5) / count + 0.011, height
            ).intersection(inside)
            for k in range(count)
        ]
        rails = [
            rectangle(0.0, y - 0.02, width, y + 0.02).intersection(inside)
            for y in (0.12, height * 0.55)
        ]
        mesh.face(soup, "doors", key, unary_union(bars + rails), -depth - 0.02)
    elif style in ("boarded", "flush", "panelled", "roller"):
        family = (
            {"family": "metal", "colour": colour}
            if style == "roller"
            else {
                "family": "weatherboard",
                "colour": colour,
                "orientation": "vertical",
                "profile": "tongue_groove",
                "scale_m": 0.12,
            }
            if style == "boarded"
            else {"family": "painted_wood", "colour": colour}
        )
        key = palette.key(family)
        mesh.face(soup, "doors", key, shape, -depth)
        frame_key = palette.key(
            {
                "family": "painted_wood",
                "colour": spec.get("frame", {}).get("colour", shade(colour, 0.8)),
            }
        )
        mesh.prism(
            soup,
            "doors",
            frame_key,
            shape.difference(shape.buffer(-0.09, join_style=2)),
            -depth,
            -depth + 0.03,
        )
        leaves = max(1, leaf.get("leaves", 2))
        for k in range(1, leaves):
            x = width * k / leaves
            mesh.box(
                soup,
                "doors",
                frame_key,
                x - 0.02,
                0.0,
                x + 0.02,
                height * 0.98,
                -depth,
                -depth + 0.02,
            )
        if style == "boarded":
            for y in (height * 0.18, height * 0.72):
                mesh.box(
                    soup,
                    "doors",
                    frame_key,
                    0.09,
                    y - 0.06,
                    width - 0.09,
                    y + 0.06,
                    -depth,
                    -depth + 0.025,
                )


def _shopfront(soup, spec, width, height, depth, palette: Palette, wall_material, trim_colour):
    shop = spec.get("shopfront", {})
    frame = spec.get("frame", {})
    frame_width = frame.get("width_m", 0.07)
    frame_key = palette.key({"family": "metal", "colour": frame.get("colour", "#3a3a3a")})
    glass_key = palette.key(
        {"family": "glass", "interior": "shop"},
        **{
            k: v
            for k, v in spec.get("glass", {}).items()
            if k in ("tint", "reflectivity", "interior")
        },
    )
    riser = shop.get("stallriser_m", 0.45)
    panes = max(1, shop.get("panes", 2))
    door = shop.get("door", "none")
    transom_h = shop.get("transom_h_m", 0.0)
    top = height - frame_width
    us = [frame_width + (width - 2 * frame_width) * k / panes for k in range(panes + 1)]
    door_index = {"left": 0, "centre": panes // 2, "right": panes - 1}.get(door)
    cells = []
    for k in range(panes):
        a, b = us[k] + (MULLION / 2 if k else 0), us[k + 1] - (MULLION / 2 if k < panes - 1 else 0)
        low = 0.28 if k == door_index else riser
        if transom_h > 0:
            cells.append((a, top - transom_h + MULLION / 2, b, top))
            cells.append((a, low, b, top - transom_h - MULLION / 2))
        else:
            cells.append((a, low, b, top))
    shape = rectangle(0.0, 0.0, width, height)
    riser_key = palette.key(
        shop.get("stallriser"),
        fallback={"family": "granite", "colour": "#55524e", "finish": "polished", "scale_m": 1.2},
    )
    solid = shape
    if riser > 0.05:
        base = rectangle(0.0, 0.0, width, riser)
        if door_index is not None:
            base = base.difference(rectangle(us[door_index], 0.0, us[door_index + 1], riser))
        mesh.face(soup, "frames", riser_key, base, -depth + 0.01)
    _glazing(soup, solid, cells, -depth, frame_key, glass_key)
    fascia = shop.get("fascia")
    if fascia and fascia.get("height_m", 0) > 0:
        key = palette.flat(fascia.get("colour", "#2f3a45"), roughness=0.6)
        mesh.box(soup, "trim", key, 0.0, height, width, height + fascia["height_m"], 0.0, 0.07)
    awning = shop.get("awning")
    if awning and awning.get("kind", "none") != "none":
        reach = awning.get("projection_m", 1.0)
        key = palette.flat(awning.get("colour", "#7a2e2a"), roughness=0.85)
        mesh.extrude_profile(
            soup,
            "attached",
            key,
            0.0,
            width,
            [
                (0.0, height - 0.05),
                (reach, height - 0.45),
                (reach, height - 0.4),
                (0.0, height + 0.02),
            ],
        )


def _steps(soup, count, width, palette: Palette):
    key = palette.key({"family": "granite", "colour": "#9d968c", "finish": "slab", "scale_m": 2.0})
    for k in range(count):
        mesh.box(
            soup,
            "plinth",
            key,
            -0.25,
            -(k + 1) * STEP_RISE,
            width + 0.25,
            -k * STEP_RISE,
            0.0,
            STEP_GOING * (k + 1),
        )


def build(
    spec: dict, width: float, height: float, wall_material: str, palette: Palette, trim_colour: str
) -> mesh.Soup:
    """One opening of a resolved type at a size, in local coordinates."""
    soup = mesh.Soup()
    kind = spec["kind"]
    shape = outline(spec, width, height)
    depth = spec.get("recess_m", 0.1)
    ring = np.asarray(mesh.orient(shape, 1.0).exterior.coords)[:-1]
    if kind != "gateway":
        # The reveal is in the wall's shade: the deeper the opening sits, the less sky its sides see.
        reveal = (
            wall_material
            if depth < 0.06
            else palette.occluded(wall_material, 0.78 if depth < 0.18 else 0.62)
        )
        mesh.ring_walls(soup, "walls", reveal, ring, -depth, 0.0, outward=False)
    if kind in ("window", "vent"):
        frame = spec.get("frame", {})
        frame_width = frame.get("width_m", 0.07)
        frame_key = palette.key(
            {"family": "painted_wood", "colour": frame.get("colour", "#ece8de")}
        )
        if kind == "vent":
            dark = palette.flat("#1b1b1b")
            mesh.face(soup, "frames", dark, shape, -depth)
            mesh.prism(
                soup,
                "frames",
                frame_key,
                shape.difference(shape.buffer(-frame_width, join_style=2)),
                -depth,
                -depth + 0.02,
            )
            inner = shape.buffer(-frame_width, join_style=2)
            slats = [
                rectangle(0.0, y, width, y + 0.025).intersection(inner)
                for y in np.arange(frame_width + 0.04, height - frame_width, 0.075)
            ]
            if slats:
                mesh.face(soup, "frames", frame_key, unary_union(slats), -depth + 0.01)
        else:
            glass = {
                k: v
                for k, v in spec.get("glass", {}).items()
                if k in ("tint", "reflectivity", "interior")
            }
            glass_key = palette.key({"family": "glass"}, **glass)
            cells = (
                lights(spec, width, height, frame_width)
                if spec.get("shape", "rect") == "rect"
                else [tuple(shape.buffer(-frame_width).bounds)]
            )
            _glazing(soup, shape, cells, -depth, frame_key, glass_key)
    elif kind == "door":
        _door(soup, spec, shape, width, height, -depth, palette)
        steps = spec.get("leaf", {}).get("steps", 0)
        if steps:
            _steps(soup, steps, width, palette)
    elif kind == "garage":
        leaf = spec.get("leaf", {})
        key = palette.key(
            {
                "family": "metal",
                "colour": leaf.get("colour", "#8c8c88"),
                "relief": "banded",
                "course_m": 0.4,
            }
        )
        mesh.face(soup, "doors", key, shape, -depth)
    elif kind == "gateway":
        mesh.ring_walls(soup, "walls", wall_material, ring, -depth, 0.0, outward=False)
        _gate(soup, spec, shape, width, height, depth, palette, wall_material)
    elif kind == "shopfront":
        _shopfront(soup, spec, width, height, depth, palette, wall_material, trim_colour)
    elif kind == "blind":
        mesh.face(
            soup,
            "walls",
            palette.key(spec.get("material"), fallback=palette.records.get(wall_material)),
            shape,
            -depth,
        )
    sill_height = (
        _sill(soup, spec, width, palette, wall_material)
        if kind in ("window", "blind", "vent")
        else 0.0
    )
    top = _surround(
        soup, spec, shape, width, height, sill_height, palette, wall_material, trim_colour
    )
    _pediment(
        soup,
        spec,
        width,
        height + top,
        palette,
        wall_material,
        trim_colour,
        spec.get("surround", {}).get("width_m", 0.12) if top else 0.0,
    )
    # Glass UVs run 0..1 across the opening, so the client can draw curtains and blinds per window.
    for (part, _material), group in soup.groups.items():
        if part == "glass":
            group["uv"] = [uv / np.array([width, height]) for uv in group["uv"]]
    return soup
