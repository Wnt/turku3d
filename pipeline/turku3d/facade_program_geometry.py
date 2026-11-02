"""Paint reviewed opening rectangles without repeating them into unobserved floor bands."""

import copy

import numpy as np

from turku3d import config as C
from turku3d.facade_inference import material as normalize_material
from turku3d.facade_kit import MATERIALS
from turku3d.facade_programs import HEAD_SHAPES, field


def _paint_layers(vertices, wall, style):
    """Yield (quad, metric UVs, atlas instruction), preserving the measured wall's scope.

    Most walls keep their original four vertices. Reviewed windows are small atlas
    patches, so they add no image encoding or unique GPU textures. Their coordinates
    are measured from the source wall, never from a neighbouring facade or a tile edge.
    """
    a, b = vertices[0], vertices[1]
    horizontal = b - a
    horizontal[1] = 0
    width = float(np.linalg.norm(horizontal))
    direction = horizontal / max(width, 1e-6)

    def quad(x0, x1, y0, y1):
        left, right = a + x0 * direction, a + x1 * direction
        tops = [vertices[3, 1] + x / width * (vertices[2, 1] - vertices[3, 1]) for x in (x0, x1)]
        bottoms = [a[1] + x / width * (b[1] - a[1]) for x in (x0, x1)]
        low = np.maximum(bottoms, y0)
        high = np.minimum(tops, y1)
        if x1 - x0 < 0.01 or min(high - low) < 0 or max(high - low) < 0.01:
            return None
        points = np.array([left, right, right, left])
        points[:, 1] = [low[0], low[1], high[1], high[0]]
        uv = np.c_[[x0, x1, x1, x0], points[:, 1]]
        return points, uv

    from turku3d.facade_relief import bands

    yield from bands(vertices, style, quad)
    geometry = wall.get("measured_geometry")
    if not geometry:
        yield (
            vertices,
            np.array([[0, a[1]], [width, a[1]], [width, vertices[2, 1]], [0, vertices[3, 1]]]),
            style,
            0,
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
    if "render_sections" in wall:
        from turku3d.facade_sections import paint

        yield from paint(vertices, wall, style, quad, offset, sign, y0)
        return
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
            yield *part, blank if above_full_wall else {**style, "unreviewed": True}, 0
    part = quad(x0, x1, y0, y1)
    if part:
        yield *part, blank, 0
    for band in wall.get("material_bands", []):
        material = normalize_material(band["material"])
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
                part[0],
                part[1],
                {**blank, "module": 42 + MATERIALS.index(material)},
                1,
            )
    # Occluded regions have no measured negative: retain their existing/rule treatment.
    for x, y, w, h in wall.get("observation", {}).get("excluded_rectangles_m", []):
        left, right = sorted([offset + x * sign, offset + (x + w) * sign])
        part = quad(max(x0, left), min(x1, right), max(y0, y0 + y), min(y1, y0 + y + h))
        if part:
            yield *part, {**style, "unreviewed": True}, 2
    courses = wall["trim"].get("string_courses", {}).get("value")
    if isinstance(courses, list):
        for height in courses:
            part = quad(x0, x1, y0 + height - 0.06, y0 + height + 0.06)
            if part:
                band = {
                    **style,
                    "module": style.get("trim_module", blank["module"]),
                    "wall": style["trim"],
                    "base": style["trim"],
                    "bands": [0, 0, 0, 0],
                    "detail": [0, 0, 0, 0],
                    "relief_m": 0.07,
                }
                yield *part, band, 3
    windows = wall["windows"]
    shapes = windows.get("head_shapes", [])
    surrounds = windows.get("surround_shapes", [])
    boxes = [
        (
            r,
            1,
            shapes[i]
            if i < len(shapes) and shapes[i] is not None
            else field(windows, "head_shape", "flat"),
            surrounds[i]
            if i < len(surrounds) and surrounds[i] is not None
            else field(windows, "surround_shape"),
        )
        for i, r in enumerate(windows.get("rectangles_m", []))
    ]
    base = wall["base"]
    base_shapes = base.get("head_shapes", [])
    base_surrounds = base.get("surround_shapes", [])
    boxes += [
        (
            r,
            1,
            base_shapes[i]
            if i < len(base_shapes) and base_shapes[i] is not None
            else field(base, "head_shape", "flat"),
            base_surrounds[i] if i < len(base_surrounds) else None,
        )
        for i, r in enumerate(base.get("openings_m", []))
    ]
    boxes += [
        (
            d["rect_m"],
            3 if d.get("opaque") else 2,
            d.get("head_shape") or field(wall["doors"], "head_shape", "flat"),
            d.get("surround"),
        )
        for d in wall["doors"].get("candidates", [])
        if "rect_m" in d
    ]
    boxes += [
        (n["rect_m"], 4, n.get("head_shape", "flat"), n.get("surround"))
        for n in wall.get("niches", [])
    ]
    for (x, y, w, h), kind, shape, surround in boxes:
        source_left, source_right = sorted([offset + x * sign, offset + (x + w) * sign])
        left, right = max(x0, source_left), min(x1, source_right)
        bottom, top = max(y0, y0 + y), min(y1, y0 + y + h)
        part = quad(left, right, bottom, top)
        if not part:
            continue
        points, uv = part
        patch = copy.deepcopy(style)
        if kind == 3:
            patch.update(
                module=48, wall="#55534e", base="#55534e", bands=[0, 0, 0, 0], detail=[0, 0, 0, 0]
            )
            yield points, uv, patch, 4
            continue
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
        if kind == 4:
            # A blind niche is an opaque recessed wall panel, never glass or a
            # ground-level door. Keep its counted window-like position and arch.
            colour = "#" + "".join(
                f"{round(int(style['wall'][i : i + 2], 16) * 0.92):02x}" for i in (1, 3, 5)
            )
            niche = {
                **blank,
                "wall": colour,
                "base": colour,
                "bands": [0, 0, 0, 0],
                "detail": [0, 0, 0, 0],
            }
            radius = {
                "round-arch": min(w / 2, h / 2),
                "segmental-arch": min(w / 4, h / 3),
                "pointed-arch": min(w / 2, h / 2),
            }.get(shape, 0)
            xs = np.linspace(source_left, source_right, 17 if radius else 2)
            for xa, xb in zip(xs[:-1], xs[1:], strict=True):
                xa, xb = max(left, xa), min(right, xb)
                roof = []
                for xx in (xa, xb):
                    t = np.clip(2 * (xx - source_left) / w - 1, -1, 1)
                    curve = 1 - abs(t) if shape == "pointed-arch" else np.sqrt(max(0, 1 - t * t))
                    roof.append(min(top, y0 + y + h - radius + radius * curve))
                strip = quad(xa, xb, bottom, max(roof))
                if strip is not None and min(roof) >= bottom:
                    strip[0][2:, 1] = np.minimum(strip[0][2:, 1], roof[::-1])
                    strip[1][:, 1] = strip[0][:, 1]
                    yield *strip, niche, 4
        else:
            yield points, uv, patch, 4
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
                "module": style.get("trim_module", blank["module"]),
                "wall": style["trim"],
                "base": style["trim"],
                "bands": [0, 0, 0, 0],
                "detail": [0, 0, 0, 0],
                "relief_m": 0.055,
            }
            for left, bottom, rw, rh in strips:
                left, right = sorted([offset + left * sign, offset + (left + rw) * sign])
                part = quad(
                    max(x0, left), min(x1, right), max(y0, y0 + bottom), min(y1, y0 + bottom + rh)
                )
                if part:
                    yield *part, decoration, 3
    from turku3d.facade_style_geometry import details

    yield from details(wall, style, blank, boxes, quad, x0, x1, y0, y1, offset, sign)

    # Ornament levels are bounded layout priors, not invented sculptures or lettering.
    ornament = field(wall["trim"], "ornament_level", "none")
    if ornament in ("low", "medium", "high"):
        thickness = {"low": 0.035, "medium": 0.07, "high": 0.11}[ornament]
        decoration = {
            **blank,
            "module": style.get("trim_module", blank["module"]),
            "wall": style["trim"],
            "base": style["trim"],
            "bands": [0, 0, 0, 0],
            "detail": [0, 0, 0, 0],
            "relief_m": 0.06,
        }
        for (x, y, w, h), _, _, _ in boxes:
            ornaments = [(x, y + h + 0.18, w, thickness)]
            if ornament in ("medium", "high"):
                ornaments.append((x + w * 0.2, y - 0.16, w * 0.6, thickness))
            if ornament == "high":
                ornaments.append((x + w * 0.4, y + h + 0.18, w * 0.2, 0.22))
            for left, bottom, rw, rh in ornaments:
                left, right = sorted([offset + left * sign, offset + (left + rw) * sign])
                part = quad(
                    max(x0, left), min(x1, right), max(y0, y0 + bottom), min(y1, y0 + bottom + rh)
                )
                if part:
                    yield *part, decoration, 3
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
                yield *part, rail_style, 5


def wall_parts(vertices, wall, style):
    """One paint owner at every wall point, including reviewed overlapping details."""
    from turku3d.wall_surfaces import paint_partition

    direction = vertices[1] - vertices[0]
    direction[1] = 0
    direction /= np.linalg.norm(direction)
    yield from paint_partition(list(_paint_layers(vertices, wall, style)), vertices[0], direction)
