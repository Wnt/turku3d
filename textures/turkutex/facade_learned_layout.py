"""Expand sampled empirical fields into the same bounded instructions as reviewed bays."""

from .facade_program import value


def expand(wall, floors, geometry):
    if not floors or not wall["bays"]["count"]:
        return
    p = wall["learned_layout"]
    count = wall["bays"]["count"]
    pitch = wall["bays"]["pitch_m"]
    width, height = wall["width_m"], wall.get("layout_height_m", wall["height_m"])
    weights = [1.0] * floors
    weights[0] = p.get("ground_scale", 1.0)
    if floors > 1:
        weights[-1] = p.get("top_scale", 1.0)
    heights = [height * w / sum(weights) for w in weights]
    wall["split"]["base_height_m"] = value(heights[0], "learned row-height layout prior")
    wall["split"]["top_height_m"] = value(heights[-1], "learned row-height layout prior")
    courses = min(floors - 1, round(p.get("course_share", 0) * (floors - 1)))
    boundaries = sorted({round((i + 1) * floors / (courses + 1)) for i in range(courses)})
    wall["trim"]["string_courses"] = value(
        [sum(heights[:i]) for i in boundaries], "learned course frequency"
    )
    # The shader already handles regular frames, row heights and balcony rails.
    # Only spatial exceptions need partitioned opening geometry on inferred walls.
    complex_layout = (
        p.get("surround") in ("pediment", "rustication")
        or p.get("windows_per_bay", 1) >= 1.5
        or wall["bays"].get("rhythm") == "ribbon"
        or abs(p.get("top_scale", 1) - 1) > 0.12
        or 0 < courses < floors - 1
        or wall["trim"].get("ornament_level", {}).get("value") in ("medium", "high")
    )
    if not complex_layout:
        wall["trim"]["string_courses"] = value(0.12 if courses else 0, "learned course frequency")
        wall["balconies"] = value(
            [[0, 0, 1, 1]] if p.get("balcony") else [], "learned repeated rail presence"
        )
        return
    ww, wh = wall["windows"]["size_m"]["value"]
    head = wall["windows"]["head_shape"]["value"]
    kind = wall["base"]["variant"]["value"]
    share = wall["base"]["glazing_share"]["value"] or 0
    multiplicity = min(4, max(1, round(p.get("windows_per_bay", 1))))
    windows, heads, surrounds, doors, balconies = [], [], [], [], []
    for floor in range(floors):
        fh = heights[floor]
        y = sum(heights[:floor])
        for bay in range(count):
            center = (bay + 0.5) * pitch
            shop = floor == 0 and kind == "shops" and (bay + 0.5) / count <= max(share, 1 / count)
            door = floor == 0 and kind == "doors" and bay == count // 2
            if floor == 0 and kind == "plain":
                continue
            if door:
                doors.append(
                    dict(
                        rect_m=[center - pitch * 0.23, y, pitch * 0.46, fh * 0.76],
                        head_shape=head,
                        surround=p.get("surround", "none"),
                    )
                )
                continue
            n = multiplicity if floor else 1
            span = (
                pitch * 0.90
                if wall["bays"].get("rhythm") == "ribbon"
                else pitch * 0.76
                if n > 1 or shop
                else min(ww, pitch * 0.9)
            )
            opening_height = fh * 0.76 if shop else min(wh, fh * 0.72)
            for j in range(n):
                opening_width = span / n * (0.85 if n > 1 else 1)
                windows.append(
                    [
                        center - span / 2 + (j + 0.5) * span / n - opening_width / 2,
                        y + fh * 0.17,
                        opening_width,
                        opening_height,
                    ]
                )
                heads.append(head)
                surrounds.append(p.get("surround", "none"))
            if floor and p.get("balcony") and bay % 2 == 0:
                balconies.append([center - pitch * 0.35, y + 0.1, pitch * 0.7, min(1, fh * 0.3)])
    wall["windows"].update(rectangles_m=windows, head_shapes=heads, surround_shapes=surrounds)
    wall["doors"]["candidates"] = doors
    wall["balconies"] = value(balconies, "learned layout prior")
    wall["measured_geometry"] = geometry
    wall["measured_size"] = [width, height]
    wall["observation"].update(
        scope="full-wall", opening_extent="sampled learned grammar; no photo measurement"
    )
