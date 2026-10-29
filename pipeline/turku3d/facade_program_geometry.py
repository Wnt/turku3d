"""Paint reviewed opening rectangles without repeating them into unobserved floor bands."""

import copy

import numpy as np

from turku3d import config as C
from turku3d.facade_kit import MATERIALS
from turku3d.facade_programs import HEAD_SHAPES, field


def wall_parts(vertices, wall, style):
    """Yield (quad, metric UVs, atlas instruction), preserving the measured wall's scope.

    Most walls keep their original four vertices. Reviewed windows are small atlas
    patches, so they add no image encoding or unique GPU textures. Their coordinates
    are measured from the source wall, never from a neighbouring facade or a tile edge.
    """
    a, b = vertices[0], vertices[1]
    width = float(np.linalg.norm(b - a))
    direction = (b - a) / max(width, 1e-6)
    normal = np.array([direction[2], 0, -direction[0]])

    def quad(x0, x1, y0, y1):
        left, right = a + x0 * direction, a + x1 * direction
        tops = [vertices[3, 1] + x / width * (vertices[2, 1] - vertices[3, 1]) for x in (x0, x1)]
        lo, hi = max(a[1], y0), min(max(tops), y1)
        if x1 - x0 < 0.01 or hi - lo < 0.01:
            return None
        points = np.array([left, right, right, left])
        points[:, 1] = [lo, lo, min(tops[1], hi), min(tops[0], hi)]
        if min(points[2:, 1]) < lo + 0.01:
            return None
        uv = np.c_[[x0, x1, x1, x0], points[:, 1]]
        return points, uv

    geometry = wall.get("measured_geometry")
    if not geometry:
        yield (
            vertices,
            np.array([[0, a[1]], [width, a[1]], [width, vertices[2, 1]], [0, vertices[3, 1]]]),
            style,
        )
        return
    coordinates = geometry.get("coordinates") or [geometry["p0"], geometry["p1"]]
    source = np.asarray(coordinates, float)[:, :2]
    source = np.c_[source[:, 0] - C.ORIGIN_E, np.zeros(2), C.ORIGIN_N - source[:, 1]]
    source_direction = source[1] - source[0]
    source_direction /= np.linalg.norm(source_direction)
    offset = float((source[0] - a) @ direction)
    sign = float(source_direction @ direction)
    mw, mh = wall["measured_size"]
    ends = sorted([offset, offset + mw * sign])
    x0, x1 = max(0, ends[0]), min(width, ends[1])
    y0 = float(geometry.get("base_h", a[1]))
    y1 = y0 + mh
    blank = {
        **style,
        "module": 42 + style["module"] // 6 if style["module"] < 42 else style["module"],
    }
    # Retain sampled/AI layouts outside the observed rectangle, especially above shop strips.
    for bounds in [
        (0, x0, a[1], max(vertices[:, 1])),
        (x1, width, a[1], max(vertices[:, 1])),
        (x0, x1, a[1], y0),
        (x0, x1, y1, max(vertices[:, 1])),
    ]:
        part = quad(*bounds)
        if part:
            above_full_wall = (
                bounds[2] == y1 and wall.get("observation", {}).get("scope") == "full-wall"
            )
            yield *part, blank if above_full_wall else style
    part = quad(x0, x1, y0, y1)
    if part:
        yield *part, blank
    for band in wall.get("material_bands", []):
        material = {"render": "plaster", "concrete_panel": "concrete"}.get(
            band["material"], band["material"]
        )
        if material not in MATERIALS:
            continue
        part = quad(
            x0,
            x1,
            max(y0, y0 + band["bottom_m"]),
            min(y1, y0 + band["bottom_m"] + band["height_m"]),
        )
        if part:
            yield (
                part[0] + normal * 0.0005,
                part[1],
                {**blank, "module": 42 + MATERIALS.index(material)},
            )
    # Occluded regions have no measured negative: retain their existing/rule treatment.
    for x, y, w, h in wall.get("observation", {}).get("excluded_rectangles_m", []):
        left, right = sorted([offset + x * sign, offset + (x + w) * sign])
        part = quad(max(x0, left), min(x1, right), max(y0, y0 + y), min(y1, y0 + y + h))
        if part:
            yield part[0] + normal * 0.001, part[1], style
    courses = wall["trim"].get("string_courses", {}).get("value")
    if isinstance(courses, list):
        for height in courses:
            part = quad(x0, x1, y0 + height - 0.06, y0 + height + 0.06)
            if part:
                band = {
                    **style,
                    "module": 42,
                    "wall": style["trim"],
                    "base": style["trim"],
                    "bands": [0, 0, 0, 0],
                    "detail": [0, 0, 0, 0],
                }
                yield part[0] + normal * 0.002, part[1], band
    windows = wall["windows"]
    shapes = windows.get("head_shapes", [])
    surrounds = windows.get("surround_shapes", [])
    boxes = [
        (
            r,
            1,
            shapes[i] if i < len(shapes) else field(windows, "head_shape", "flat"),
            surrounds[i] if i < len(surrounds) else None,
        )
        for i, r in enumerate(windows.get("rectangles_m", []))
    ]
    boxes += [
        (r, 1, field(wall["base"], "head_shape", "flat"), None)
        for r in wall["base"].get("openings_m", [])
    ]
    boxes += [
        (
            d["rect_m"],
            2,
            d.get("head_shape", field(wall["doors"], "head_shape", "flat")),
            d.get("surround"),
        )
        for d in wall["doors"].get("candidates", [])
        if "rect_m" in d
    ]
    for (x, y, w, h), kind, shape, surround in boxes:
        source_left, source_right = sorted([offset + x * sign, offset + (x + w) * sign])
        left, right = max(x0, source_left), min(x1, source_right)
        bottom, top = max(y0, y0 + y), min(y1, y0 + y + h)
        part = quad(left, right, bottom, top)
        if not part:
            continue
        points, uv = part
        points = points + normal * 0.003
        patch = copy.deepcopy(style)
        material = style["module"] // 6 if style["module"] < 42 else style["module"] - 42
        patch.update(
            module=material * 6 + (1 if kind == 2 else 0),
            bay=w,
            storey=h,
            ground=2 * h if kind == 2 else 0.001,
            upper=h,
            size=[w, h],
            origin=[source_left, y0 + y],
            bands=[0, 0, 0, 0],
            opening=[0.98, 0.95, 0.02, 0],
            detail=[0, 0, 0, kind + 4 * HEAD_SHAPES.get(shape, 0)],
        )
        yield points, uv, patch
        # Qualitative reviewed surrounds use bounded metric layout priors.
        # Pediments are triangular strips; rustication is interrupted stonework.
        if surround in ("frame", "pediment", "rustication"):
            thickness = min(0.14, w * 0.09)
            strips = [
                (x - thickness, y - thickness, w + 2 * thickness, thickness),
                (x - thickness, y + h, w + 2 * thickness, thickness),
            ]
            if surround == "rustication":
                strips += [
                    (xx, yy, thickness, min(0.24, y + h - yy))
                    for xx in (x - thickness, x + w)
                    for yy in np.arange(y, y + h, 0.32)
                ]
            else:
                strips += [(xx, y, thickness, h) for xx in (x - thickness, x + w)]
            if surround == "pediment":
                for t in np.linspace(0, 1, 16, endpoint=False):
                    strips.append(
                        (x + w * t, y + h + thickness + min(t, 1 - t) * w * 0.35, w / 16, thickness)
                    )
            decoration = {
                **blank,
                "wall": style["trim"],
                "base": style["trim"],
                "bands": [0, 0, 0, 0],
                "detail": [0, 0, 0, 0],
            }
            for left, bottom, rw, rh in strips:
                left, right = sorted([offset + left * sign, offset + (left + rw) * sign])
                part = quad(
                    max(x0, left), min(x1, right), max(y0, y0 + bottom), min(y1, y0 + bottom + rh)
                )
                if part:
                    yield part[0] + normal * 0.002, part[1], decoration
    # Reviewed balcony rails are positioned rectangles, not a repeated bay pattern.
    balconies = wall.get("balconies", {}).get("value") or []
    rail_style = {
        **blank,
        "wall": style["trim"],
        "base": style["trim"],
        "bands": [0, 0, 0, 0],
        "detail": [0, 0, 0, 0],
    }
    for x, y, w, h in balconies:
        strips = [(x, y, w, min(0.15, h)), (x, y + h - 0.06, w, 0.06)]
        strips += [(bar, y, 0.04, h) for bar in np.arange(x, x + w, 0.22)]
        for left, bottom, rw, rh in strips:
            left, right = sorted([offset + left * sign, offset + (left + rw) * sign])
            part = quad(
                max(x0, left), min(x1, right), max(y0, y0 + bottom), min(y1, y0 + bottom + rh)
            )
            if part:
                yield part[0] + normal * 0.004, part[1], rail_style
