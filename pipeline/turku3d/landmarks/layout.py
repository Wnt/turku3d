"""One physical opening layout shared by texture paint and arched relief."""

import numpy as np
import shapely


def arch_polygon(cx, low, width, height, pointed, segments=12):
    left, right, top = cx - width / 2, cx + width / 2, low + height
    if pointed is None:
        return shapely.box(left, low, right, top)
    rise = min(height, width / 2)
    if pointed:
        x = np.linspace(left, right, segments + 1)
        y = top - rise * (abs(x - cx) / (width / 2)) ** 1.6
    else:
        angle = np.linspace(np.pi, 0, segments + 1)
        x, y = cx + width / 2 * np.cos(angle), top - rise + rise * np.sin(angle)
    return shapely.Polygon([(left, low), *zip(x, y, strict=True), (right, low)])


def openings(width, height, bottom, d, tower):
    proposed = []
    if tower:
        for low, w, h, count in d["tower"]["opening_rows"]:
            count = int(count)
            while count > 1 and width / (count + 1) < w + 0.32:
                count -= 1
            proposed.extend(
                (width * (i + 1) / (count + 1), low, w, h, "gothic" in d["style"])
                for i in range(int(count))
            )
        # Only synthesize the belfry opening when no explicit row describes it.
        level = d["tower"]["copper_start_m"] + 1
        if d["roof"]["material"] == "copper" and not any(
            abs(row[0] - level) < 4 for row in d["tower"]["opening_rows"]
        ):
            proposed.append((width / 2, level, width * 0.42, 6, False))
    else:
        w = d["windows"]
        pointed = (
            True
            if any(k in w["type"] for k in ("gothic", "pointed", "lancet"))
            else False
            if "arch" in w["type"]
            else None
        )
        count = max(1, round(width / w["spacing_m"])) if width >= w["width_m"] + 0.92 else 0
        for low in w.get("rows_m", [w["sill_m"]]):
            proposed.extend(
                (width * (i + 0.5) / count, low, w["width_m"], w["height_m"], pointed)
                for i in range(count)
            )
    if d.get("show_entrance"):
        e = d["entrance"]
        proposed.append((width / 2, e.get("sill_m", 0.1), e["width_m"], e["height_m"], True))
    return [
        row
        for row in proposed
        if row[2] + 0.32 <= width
        and row[1] >= bottom + 0.3
        and row[1] + row[3] <= bottom + height - 0.3
    ]


def accepted_openings(width, height, bottom, d, tower, polygon, base):
    """One union-face plan accepted by both texture paint and physical recesses."""
    return [
        row
        for row in openings(width, height, bottom, d, tower)
        if polygon.covers(arch_polygon(row[0], base + row[1], row[2], row[3], row[4]).buffer(0.16))
    ]
