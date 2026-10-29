"""Fixed structure-only review form -> metric facade program.

The observer counts architectural bays, never pixels. Dimensions are explicit
layout priors scaled to the surveyed wall and register storeys, not measurements.
Unknown bays retain earlier treatment. Rows are ground-to-top and left-to-right.
"""

import copy
import math

from .facade_program import unknown, value

KINDS = {"window", "door", "shop", "blank", "arched portal", "opaque door", "unknown"}
HEADS = {
    "flat": "flat",
    "segmental": "segmental-arch",
    "round": "round-arch",
    "pointed": "pointed-arch",
}
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
    if form.get("render_deferred"):
        observations = form.get("facade_observations", {})
        if not observations.get("sections") or not observations.get("confidence"):
            raise ValueError("deferred rendering still requires structured sections and confidence")
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
        unknown_row = row["bay_count"] is None and (
            row["bays"] == ["unknown"]
            or (row["bays"] == ["window"] and row.get("rhythm") == "ribbon")
        )
        if not row["bays"] or (not unknown_row and row["bay_count"] != len(row["bays"])):
            raise ValueError("bay count mismatch")
        for field, allowed in [("head", HEADS), ("surround", SURROUNDS)]:
            values = row[field] if isinstance(row[field], list) else [row[field]]
            if isinstance(row[field], list) and len(values) != row["bay_count"]:
                raise ValueError("per-bay shape count")
            if any(v is not None and v not in allowed for v in values):
                raise ValueError("opening shape")
        if any(b not in KINDS for b in row["bays"]):
            raise ValueError("bay kind")
        required = {"bay_count", "bay_kinds", "head", "surround"}
        for field in (
            "opening_counts",
            "bay_units",
            "rhythm",
            "height_class",
            "material",
            "opening_class",
        ):
            if field in row:
                required.add(field)
        if set(row["confidence"]) != required:
            raise ValueError("row confidence")
        for field in ("opening_counts", "bay_units"):
            if field in row:
                values = row[field]
                if len(values) != row["bay_count"] or any(
                    type(v) is not int or not 1 <= v <= 8 for v in values
                ):
                    raise ValueError(f"invalid {field}")
        if row.get("rhythm", "regular") not in ("regular", "ribbon"):
            raise ValueError("unknown opening rhythm")
        if row.get("height_class", "regular") not in ("short", "regular", "tall"):
            raise ValueError("unknown row height class")
        classes = row.get("opening_class", "regular")
        if isinstance(classes, list) and len(classes) != row["bay_count"]:
            raise ValueError("per-bay opening class count")
        if any(
            c not in ("small", "regular", "wide", "tall")
            for c in (classes if isinstance(classes, list) else [classes])
        ):
            raise ValueError("unknown opening class")
        if row.get("material") not in (
            None,
            "render",
            "brick",
            "stone",
            "wood",
            "concrete_panel",
            "metal",
            "glass",
        ):
            raise ValueError("unknown row material")
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
    if form.get("render_deferred"):
        return dict(
            wall_id=row["wall_id"],
            width_m=row["width_m"],
            height_m=row["height_m"],
            geometry={k: row[k] for k in ("p0", "p1", "normal", "base_h")},
            structure=copy.deepcopy(form),
            wall=dict(colour=unknown(), material=unknown()),
            windows=dict(rectangles_m=[]),
            base=dict(openings_m=[]),
            observation=dict(
                needs_review=True,
                scope="characterized; renderer deferred",
                wall_share=0,
                notes=form["render_deferred"],
                colour_space="unmeasured",
            ),
        )
    W, H = row["width_m"], row["height_m"]
    rows = form["floors"]
    count = len(rows)
    ground_only = count == 1 and register_floors and register_floors > 1
    # Count conflicts remain visible in provenance and never change shell height
    # or storeys. The observed rows partition only this measured facade extent.
    weights = [
        {"short": 0.55, "regular": 1.0, "tall": 1.3}[f.get("height_class", "regular")] for f in rows
    ]
    heights = [H * w / sum(weights) for w in weights]
    bottoms = [sum(heights[:i]) for i in range(count)]
    confidence = form["confidence"]

    def observed(v, field):
        return (
            value(v, "direct structure-only review", confidence[field])
            if v is not None
            else unknown()
        )

    windows, heads, surrounds, doors, excluded, balconies = [], [], [], [], [], []
    material_bands = []
    for floor, reading in enumerate(rows):
        floor_height = heights[floor]
        if reading.get("material"):
            material_bands.append(
                dict(
                    bottom_m=bottoms[floor],
                    height_m=floor_height,
                    material=reading["material"],
                    support=reading["confidence"]["material"],
                )
            )
        units = reading.get("bay_units", [1] * len(reading["bays"]))
        module_width = W / sum(units)
        offset = 0
        for bay, kind in enumerate(reading["bays"]):
            pitch = module_width * units[bay]
            x = offset
            offset += pitch
            y = bottoms[floor]
            if kind == "unknown":
                excluded.append([x, y, pitch, floor_height])
                continue
            if kind == "blank":
                continue
            is_door = kind in ("door", "arched portal", "opaque door")
            multiplicity = reading.get("opening_counts", [1] * len(reading["bays"]))[bay]
            if is_door and multiplicity != 1:
                raise ValueError("door groups require one opening")
            classes = reading.get("opening_class", "regular")
            opening_class = classes[bay] if isinstance(classes, list) else classes
            width_fraction, height_fraction = {
                "small": (0.2, 0.28),
                "regular": (0.48, 0.55),
                "wide": (0.76, 0.55),
                "tall": (0.48, 0.8),
            }[opening_class]
            span = pitch * (
                0.9
                if reading.get("rhythm") == "ribbon"
                else 0.76
                if kind == "shop" or multiplicity > 1
                else width_fraction
            )
            width = span / multiplicity * (1 if multiplicity == 1 else 0.85)
            height = floor_height * (0.76 if is_door or kind == "shop" else height_fraction)
            bottom = y + (
                0 if is_door else floor_height * (0.42 if opening_class == "small" else 0.17)
            )
            head_value = (
                reading["head"][bay] if isinstance(reading["head"], list) else reading["head"]
            )
            head = (
                "round-arch"
                if kind == "arched portal" and head_value in (None, "flat")
                else HEADS.get(head_value, "flat")
            )
            surround = (
                reading["surround"][bay]
                if isinstance(reading["surround"], list)
                else reading["surround"]
            ) or "none"
            for opening in range(multiplicity):
                box = [
                    round(
                        x + (pitch - span) / 2 + (opening + 0.5) * span / multiplicity - width / 2,
                        3,
                    ),
                    round(bottom, 3),
                    round(width, 3),
                    round(height, 3),
                ]
                if is_door:
                    doors.append(
                        dict(
                            rect_m=box,
                            opaque=kind == "opaque door",
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
        material_bands=material_bands,
        observation=dict(
            needs_review=False,
            scope="ground-only" if ground_only else "full-wall",
            visible_share=1 - sum(b[2] * b[3] for b in excluded) / (W * H),
            wall_share=0,
            register_height_conflict=register_floors is not None and register_floors != count,
            colour_space="unmeasured; preserve calibrated pigment",
            excluded_rectangles_m=excluded,
            notes=form["occluded_parts"],
            opening_extent="counted rows and bays; dimensions are layout priors, not measured rectangles",
            dimension_source="LOD2 wall extent, register storeys checked, qualitative row heights and counted bay units; dimensions remain layout priors",
        ),
        split=dict(
            base_height_m=value(heights[0], "qualitative row-height layout prior", 0.2),
            middle_repeats=None if ground_only else max(0, count - 2),
            top_height_m=unknown(),
        ),
        wall=dict(colour=unknown(), material=observed(form["material"], "material")),
        bays=dict(
            count=max(r["bay_count"] or 0 for r in rows),
            centers_m=[],
            pitch_m=None,
            symmetry=None,
            support=confidence["floors_visible"],
        ),
        windows=dict(
            rectangles_m=[] if ground_only else windows,
            head_shapes=[] if ground_only else heads,
            surround_shapes=[] if ground_only else surrounds,
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
            openings_m=windows if ground_only else [],
            head_shapes=heads if ground_only else [],
            surround_shapes=surrounds if ground_only else [],
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
                [round(bottoms[n], 3) for n in form["string_courses"]],
                "counted floor boundaries",
                confidence["string_courses"],
            ),
            window_surrounds=unknown(),
            colour=unknown(),
            ornament_level=observed(form["ornament"], "ornament"),
        ),
        corners={k: unknown() for k in ("quoins", "shape", "tower")},
        balconies=observed(balconies, "balconies") if confidence["balconies"] > 0 else unknown(),
        bay_windows=unknown(),
        style_era=unknown(),
    )
