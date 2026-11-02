"""Bounded historicist paint details inferred around observed openings.

These are labelled style priors, not newly measured geometry or photographed
artwork. One owner per wall point is enforced by the ordinary paint partition.
"""

import numpy as np

from turku3d.facade_programs import field


def details(wall, style, blank, boxes, quad, x0, x1, y0, y1, offset, sign):
    prior = wall.get("appearance_details", {})
    if not prior:
        return
    width, height = wall["measured_size"]
    form = wall.get("structure", {})
    rows = form.get("floors", [])
    ground = height / len(rows) if rows else style["ground"]
    trim = {
        **blank,
        "module": style.get("trim_module", blank["module"]),
        "wall": style["trim"],
        "base": style["trim"],
        "bands": [0, 0, 0, 0],
        "detail": [0, 0, 0, 0],
        "relief_m": 0.065,
    }

    def strip(x, y, w, h, paint=trim):
        left, right = sorted([offset + x * sign, offset + (x + w) * sign])
        part = quad(max(x0, left), min(x1, right), max(y0, y0 + y), min(y1, y0 + y + h))
        if part:
            yield *part, paint, 3

    if field(prior, "pilasters", False):
        count = max(1, wall["bays"]["count"])
        pitch = width / count
        thickness = min(0.48, pitch * 0.12)
        for x in np.linspace(0, width, count + 1):
            yield from strip(x - thickness / 2, ground, thickness, height - ground)
            yield from strip(x - thickness * 0.8, height - 0.65, thickness * 1.6, 0.16)
    if field(prior, "balustrades", False):
        # A shallow inset below first-upper windows, never a projecting balcony.
        for (x, y, w, _h), kind, *_ in boxes:
            if kind != 1 or not ground < y < ground * 1.5:
                continue
            bottom, rail_h = max(ground, y - 0.65), 0.48
            yield from strip(x, bottom, w, 0.08)
            yield from strip(x, bottom + rail_h, w, 0.08)
            for bar in np.linspace(x + 0.07, x + w - 0.07, max(2, round(w / 0.28))):
                yield from strip(bar - 0.035, bottom, 0.07, rail_h)
    if rows and "shop" in rows[0].get("bays", []):
        # Sign fascia is a style prior; no invented business name or lettering.
        dark = "#" + "".join(
            f"{round(int(style['trim'][i : i + 2], 16) * 0.55):02x}" for i in (1, 3, 5)
        )
        yield from strip(
            0, ground * 0.79, width, ground * 0.14, {**trim, "wall": dark, "base": dark}
        )
