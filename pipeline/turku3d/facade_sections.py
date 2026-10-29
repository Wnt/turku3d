"""Compile partial readings over generated treatment for unobserved regions."""

import copy

import numpy as np

from turku3d.facade_programs import compile_wall


def prepare(program):
    from turkutex.facade_sections import sections
    from turkutex.facade_structure import compile_structure

    from turku3d.facade_components import components

    program = copy.deepcopy(program)
    for wall in program.get("walls", []):
        form = wall.get("structure", {})
        if form.get("floors"):
            # Recompile retained forms so old published arrays cannot bypass the
            # current per-row confidence policy. Never mutate the source files.
            wall.update(
                compile_structure(
                    {**wall, **wall["geometry"]}, form, program.get("storeys", {}).get("value")
                )
            )
        if not form.get("render_deferred"):
            # An explicitly reviewed negative clears sampled balcony rails.
            if form.get("balconies") == [] and form.get("confidence", {}).get("balconies", 0) > 0:
                wall["balconies"] = dict(
                    value=[],
                    source="direct structure-only review",
                    support=form["confidence"]["balconies"],
                )
            continue
        parts, unsupported = sections(form, program.get("storeys", {}).get("value"))
        features, pending = components(form, program.get("storeys", {}).get("value"))
        wall["geometry_components"] = features
        wall["geometry_pending"] = pending
        wall["render_sections"] = parts
        wall["unsupported"] = unsupported
        wall["observation"].update(
            needs_review=not bool(parts or features), scope="partial-sections"
        )
    program["needs_review"] = all(
        w.get("observation", {}).get("needs_review", True) for w in program.get("walls", [])
    )
    return program


def paint(vertices, wall, style, quad, offset, sign, y0):
    from turkutex.facade_structure import FIELDS, compile_structure

    from turku3d.facade_kit import MATERIALS
    from turku3d.facade_program_geometry import _paint_layers

    mw, mh = wall["measured_size"]
    width = float(((vertices[1] - vertices[0]) ** 2).sum() ** 0.5)
    # The prior remains behind all unresolved sections; no full-wall blank is emitted.
    yield (
        vertices,
        np.array(
            [
                [0, vertices[0, 1]],
                [width, vertices[1, 1]],
                [width, vertices[2, 1]],
                [0, vertices[3, 1]],
            ]
        ),
        {**style, "unreviewed": True},
        0,
    )
    for section in wall["render_sections"]:
        x, y, w, h = section["bounds"]
        x, y, w, h = x * mw, y * mh, w * mw, h * mh
        ends = sorted([offset + x * sign, offset + (x + w) * sign])
        part = quad(max(0, ends[0]), min(width, ends[1]), y0 + y, y0 + y + h)
        if part is None:
            continue
        from turku3d.facade_inference import material

        mat = material(section["material"])
        if section["kind"] is None:
            module = style["module"]
            if mat in MATERIALS:
                module = (
                    42 + MATERIALS.index(mat)
                    if module >= 42
                    else MATERIALS.index(mat) * 6 + module % 6
                )
            # Material-only evidence cannot erase openings in preserved artwork.
            # With no map, the existing layout still gains the reviewed material.
            yield *part, {**style, "module": module, "unreviewed": True}, 1
            continue
        count, rows = section["count"], section["rows"]
        form = dict(
            wall_id=wall["wall_id"],
            floors_visible=rows,
            material={"plaster": "render", "concrete": "concrete_panel"}.get(mat, mat),
            ornament=None,
            cornice=None,
            string_courses=[],
            balconies=[],
            occluded_parts="Section extents are qualitative layout priors",
            confidence={k: 0.5 for k in FIELDS},
            floors=[
                dict(
                    role="base" if i == 0 else "middle",
                    bay_count=count,
                    bays=section.get("bays") or [section["kind"]] * count,
                    head=section["head"],
                    surround=section["surround"],
                    confidence={k: 0.5 for k in ("bay_count", "bay_kinds", "head", "surround")},
                )
                for i in range(rows)
            ],
        )
        g = wall["measured_geometry"]
        coords = g.get("coordinates") or [g["p0"], g["p1"]]
        p0 = [coords[0][j] + (coords[1][j] - coords[0][j]) * x / mw for j in range(2)]
        p1 = [coords[0][j] + (coords[1][j] - coords[0][j]) * (x + w) / mw for j in range(2)]
        row = dict(
            wall_id=wall["wall_id"],
            width_m=w,
            height_m=h,
            p0=p0,
            p1=p1,
            base_h=y0 + y,
            normal=g.get("normal"),
        )
        compiled = compile_structure(row, form, rows, min_support=0)
        compiled.update(measured_geometry=compiled["geometry"], measured_size=[w, h])
        section_style = compile_wall(compiled, rows)
        section_style.update(wall=style["wall"], base=style["base"], trim=style["trim"])
        section_style["origin"] = [0, y0 + y]
        shift = max(0, ends[0])
        for points, uv, instruction, priority in _paint_layers(part[0], compiled, section_style):
            # The compositor and shader both need the parent wall's metric UV frame.
            uv = uv.copy()
            uv[:, 0] += shift
            instruction = {
                **instruction,
                "origin": [instruction["origin"][0] + shift, instruction["origin"][1]],
            }
            yield points, uv, instruction, priority + 1
