"""Fixed structure-only review form -> metric facade program.

The observer counts architectural bays, never pixels. Dimensions are explicit
layout priors scaled to the surveyed wall and register storeys, not measurements.
Unknown bays retain earlier treatment. Rows are ground-to-top and left-to-right.
"""

import copy
import math

from .facade_program import unknown, value

KINDS = {"window", "door", "shop", "blank", "arched portal", "unknown"}
HEADS = {"flat": "flat", "segmental": "segmental-arch", "round": "round-arch"}
SURROUNDS = {"none", "frame", "pediment", "rustication"}
FIELDS = {
    "floors_visible",
    "split",
    "material",
    "ornament",
    "cornice",
    "string_courses",
    "balconies",
    "occluded_parts",
}


def validate(form):
    if not isinstance(form.get("wall_id"), str):
        raise ValueError("wall_id required")
    if form.get("reject"):
        if not form.get("reason"):
            raise ValueError("rejection needs a reason")
        return
    if set(form.get("confidence", {})) != FIELDS:
        raise ValueError("confidence required for every observation field")
    for v in form["confidence"].values():
        if not isinstance(v, (int, float)) or not math.isfinite(v) or not 0 <= v <= 1:
            raise ValueError("confidence outside [0,1]")
    rows = form["floors"]
    if not rows or form["floors_visible"] != len(rows):
        raise ValueError("visible row count mismatch")
    if form["material"] not in (
        None,
        "render",
        "brick",
        "wood",
        "concrete_panel",
        "stone",
        "metal",
        "glass",
    ):
        raise ValueError("unknown material")
    if form["ornament"] not in (None, "none", "low", "medium", "high"):
        raise ValueError("ornament")
    if form["cornice"] not in (None, "none", "thin", "projecting", "ornate"):
        raise ValueError("cornice")
    for i in form["string_courses"]:
        if not isinstance(i, int) or not 1 <= i < len(rows):
            raise ValueError("course must be a floor boundary")
    for row in rows:
        if row["role"] not in ("base", "middle", "top"):
            raise ValueError("split role")
        if not row["bays"] or row["bay_count"] != len(row["bays"]):
            raise ValueError("bay count mismatch")
        for field, allowed in [("head", HEADS), ("surround", SURROUNDS)]:
            values = row[field] if isinstance(row[field], list) else [row[field]]
            if isinstance(row[field], list) and len(values) != row["bay_count"]:
                raise ValueError("per-bay shape count")
            if any(v is not None and v not in allowed for v in values):
                raise ValueError("opening shape")
        if any(b not in KINDS for b in row["bays"]):
            raise ValueError("bay kind")
        if set(row["confidence"]) != {"bay_count", "bay_kinds", "head", "surround"}:
            raise ValueError("row confidence")
        if any(
            not isinstance(v, (int, float)) or not math.isfinite(v) or not 0 <= v <= 1
            for v in row["confidence"].values()
        ):
            raise ValueError("row confidence outside [0,1]")
    for floor, bay in form["balconies"]:
        if not 0 <= floor < len(rows) or not 0 <= bay < len(rows[floor]["bays"]):
            raise ValueError("balcony location")


def compile_structure(row, form, register_floors):
    validate(form)
    if form.get("reject"):
        raise ValueError("cannot compile rejected evidence")
    W, H = row["width_m"], row["height_m"]
    rows = form["floors"]
    count = len(rows)
    # Count conflicts remain visible in provenance and never change shell height
    # or storeys. The observed rows partition only this measured facade extent.
    floor_height = H / count
    confidence = form["confidence"]

    def observed(v, field):
        return (
            value(v, "direct structure-only review", confidence[field])
            if v is not None
            else unknown()
        )

    windows, heads, surrounds, doors, excluded, balconies = [], [], [], [], [], []
    for floor, reading in enumerate(rows):
        pitch = W / len(reading["bays"])
        for bay, kind in enumerate(reading["bays"]):
            x = bay * pitch
            y = floor * floor_height
            if kind == "unknown":
                excluded.append([x, y, pitch, floor_height])
                continue
            if kind == "blank":
                continue
            is_door = kind in ("door", "arched portal")
            width = pitch * (0.76 if kind == "shop" else 0.48)
            height = floor_height * (0.76 if is_door or kind == "shop" else 0.55)
            bottom = y + (0 if is_door else floor_height * 0.17)
            box = [
                round(x + (pitch - width) / 2, 3),
                round(bottom, 3),
                round(width, 3),
                round(height, 3),
            ]
            head_value = (
                reading["head"][bay] if isinstance(reading["head"], list) else reading["head"]
            )
            head = "round-arch" if kind == "arched portal" else HEADS.get(head_value, "flat")
            surround = (
                reading["surround"][bay]
                if isinstance(reading["surround"], list)
                else reading["surround"]
            ) or "none"
            if is_door:
                doors.append(
                    dict(
                        rect_m=box,
                        head_shape=head,
                        surround=surround,
                        source="counted bay; dimensions are layout priors",
                        support=reading["confidence"]["bay_kinds"],
                    )
                )
            else:
                windows.append(box)
                heads.append(head)
                surrounds.append(surround)
            if [floor, bay] in form["balconies"]:
                balconies.append(
                    [x + pitch * 0.1, y + floor_height * 0.1, pitch * 0.8, floor_height * 0.25]
                )
    return dict(
        wall_id=row["wall_id"],
        width_m=W,
        height_m=H,
        geometry={k: row[k] for k in ("p0", "p1", "normal", "base_h")},
        structure=copy.deepcopy(form),
        observation=dict(
            needs_review=False,
            scope="full-wall",
            visible_share=1 - sum(b[2] * b[3] for b in excluded) / (W * H),
            wall_share=0,
            register_height_conflict=register_floors is not None and register_floors != count,
            colour_space="unmeasured; preserve calibrated pigment",
            excluded_rectangles_m=excluded,
            notes=form["occluded_parts"],
            opening_extent="counted rows and bays; dimensions are layout priors, not measured rectangles",
            dimension_source="LOD2 wall extent, register storeys checked, equal row/bay layout priors",
        ),
        split=dict(
            base_height_m=value(floor_height, "equal-height layout prior", 0.2),
            middle_repeats=max(0, count - 2),
            top_height_m=unknown(),
        ),
        wall=dict(colour=unknown(), material=observed(form["material"], "material")),
        bays=dict(
            count=max(r["bay_count"] for r in rows),
            centers_m=[],
            pitch_m=None,
            symmetry=None,
            support=confidence["floors_visible"],
        ),
        windows=dict(
            rectangles_m=windows,
            head_shapes=heads,
            surround_shapes=surrounds,
            head_shape=unknown(),
            size_m=unknown(),
            aspect=unknown(),
            sill_fraction=unknown(),
            frame_colour=unknown(),
            divisions=unknown(),
            recess_m=unknown(),
        ),
        base=dict(
            colour=unknown(),
            material=unknown(),
            plinth_height_m=unknown(),
            glazing_share=unknown(),
            shopfront=unknown(),
            openings_m=[],
            variant=unknown(),
        ),
        doors=dict(candidates=doors, type=unknown(), canopy=unknown()),
        trim=dict(
            cornice=value(
                {"none": 0, "thin": 0.12, "projecting": 0.3, "ornate": 0.45}.get(form["cornice"]),
                "qualitative cornice layout prior",
                confidence["cornice"],
            ),
            string_courses=value(
                [round(n * floor_height, 3) for n in form["string_courses"]],
                "counted floor boundaries",
                confidence["string_courses"],
            ),
            window_surrounds=unknown(),
            colour=unknown(),
            ornament_level=observed(form["ornament"], "ornament"),
        ),
        corners={k: unknown() for k in ("quoins", "shape", "tower")},
        balconies=observed(balconies, "balconies") if balconies else unknown(),
        bay_windows=unknown(),
        style_era=unknown(),
    )
