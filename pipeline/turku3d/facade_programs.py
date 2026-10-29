"""Resolve facade programs and compile metric wall instructions for the shared atlas.

Reviewed walls remain generated in coarse.stored_facades. Supported photographed
fields override only their own wall; missing fields retain explicit appearance priors.
No measured count changes register storeys. Null fields never erase a supported prior.
"""

from __future__ import annotations

import copy
import json
from functools import lru_cache
from pathlib import Path

import numpy as np

from turku3d import config as C
from turku3d.facade_assets import aliases, opaque, root
from turku3d.facade_kit import MATERIALS, read_description, renderer
from turku3d.facade_inference import overlay


def source_paths():
    return [
        Path(__file__),
        Path(__file__).with_name("facade_inference.py"),
        Path(__file__).with_name("facade_style_geometry.py"),
        Path(__file__).with_name("facade_program_geometry.py"),
        Path(__file__).with_name("facade_sections.py"),
        Path(__file__).with_name("facade_components.py"),
        Path(__file__).with_name("facade_component_geometry.py"),
        Path(__file__).with_name("facade_learning.py"),
        C.REPO_ROOT / "textures/turkutex/facade_learning.py",
        C.REPO_ROOT / "textures/turkutex/facade_learned_layout.py",
        C.REPO_ROOT / "textures/turkutex/facade_sections.py",
        C.REPO_ROOT / "textures/turkutex/facade_structure.py",
        C.REPO_ROOT / "textures/turkutex/facade_archetypes.py",
        C.REPO_ROOT / "textures/turkutex/facade_program.py",
    ]


@lru_cache(maxsize=8)
def _bundled(path, stamp):
    rows = json.loads(Path(path).read_text())
    if isinstance(rows, dict):
        rows = [rows] if "building_id" in rows else rows.get("programs", [])
    renderer()
    from turku3d.facade_sections import prepare

    return {str(r["building_id"]): prepare(r) for r in rows}


def measured(key):
    """Private exported programs may override the reviewed, descriptors-only pilot."""
    for directory in (C.work_root() / "facade-programs", root() / "facade-programs"):
        for identity in aliases(key):
            path = directory / opaque(identity)
            if path.is_file():
                row = _bundled(str(path), (path.stat().st_mtime_ns, path.stat().st_size))
                row = next(iter(row.values()))
                if str(row.get("building_id")) in aliases(key):
                    return row
    path = C.REPO_ROOT / "textures/library/facade-programs.json"
    if path.exists():
        rows = _bundled(str(path), (path.stat().st_mtime_ns, path.stat().st_size))
        return next((rows[k] for k in aliases(key) if k in rows), None)
    return None


def program_hash(key):
    # A pilot JSON edit invalidates just the tiles containing its changed building.
    from turku3d.facade_learning import digest

    from turku3d.facade_inference import evidence

    return json.dumps([measured(key), evidence(key)], sort_keys=True, separators=(",", ":")).encode() + digest(key)


def field(section, key, default=None):
    item = section.get(key, {})
    result = item.get("value") if isinstance(item, dict) else item
    return default if result is None else result


def matching_wall(vertices, program):
    if not program or program.get("needs_review", True):
        return None
    from turku3d.coarse.stored_facades import match_prepared, prepare_walls

    facades = []
    for wall in program.get("walls", []):
        if wall.get("observation", {}).get("needs_review", True):
            continue
        g = wall.get("geometry", {})
        points = g.get("coordinates") or ([g["p0"], g["p1"]] if "p0" in g and "p1" in g else None)
        if points is not None:
            normal = g.get("normal")
            if normal is not None and len(normal) == 2:
                normal = [normal[0], 0, -normal[1]]  # measured EN outward -> engine xyz
            facades.append(
                dict(
                    coordinates=points,
                    normal=normal,
                    base_height=g.get("base_h", vertices[0, 1]),
                    metadata={},
                    wall=wall,
                )
            )
    # Roof fragments may extend beyond the surveyed span. Match any meaningful
    # collinear overlap; wall_parts clips the observation and preserves the rest.
    length = float(np.linalg.norm(vertices[1] - vertices[0]))
    match = match_prepared(
        vertices, prepare_walls(facades), min_coverage=max(0.02, 0.1 / max(length, 0.1))
    )
    return match[0]["wall"] if match else None


def describe(program, description, source_width):
    """Keep existing AI/kit results, adapting only the bay count to each real wall."""
    from turkutex.facade_program import value

    d = description
    pitch = source_width / max(1, d["window_columns"])
    features = d["features"]
    for w in program["walls"]:
        width, height = w["width_m"], w["height_m"]
        count = max(1, round(width / pitch)) if d["window_columns"] else 0
        bay = width / max(count, 1)
        floors = program["storeys"]["value"]
        ground = height / floors if floors else height
        if floors and floors > 1 and d["ground_floor"] == "shops":
            ground = min(ground * 1.25, height * 0.6)
        upper = (height - ground) / (floors - 1) if floors and floors > 1 else ground
        v = lambda x: value(x, "existing description", 1)  # noqa: E731
        w["wall"].update(colour=v(d["wall_colour"]), material=v(d["material"]))
        w["bays"].update(
            count=count,
            pitch_m=bay,
            centers_m=[(i + 0.5) * bay for i in range(count)],
            rhythm="regular",
        )
        w["windows"].update(
            size_m=v([bay * max(0.1, d["window_width"]), upper * max(0.1, d["window_height"])]),
            sill_fraction=v(0.21),
            frame_colour=v(d["trim_colour"]),
        )
        w["split"]["base_height_m"] = v(ground)
        w["base"].update(
            variant=v(d["ground_floor"]),
            shopfront=v(d["ground_floor"] == "shops"),
            colour=v(d["wall_colour"]),
            plinth_height_m=v(0.6 if d["plinth"] else 0),
        )
        w["trim"].update(
            colour=v(d["trim_colour"]),
            cornice=v(0.35 if d["roof_line"] == "cornice" else 0.08),
            string_courses=v(0.15 if "string_courses" in features else 0),
            window_surrounds=v(0.12 if "ornament" in features else 0.04),
        )
        w["corners"]["quoins"] = v(
            0.30 if "pilasters" in features else 0.15 if d["material"] == "wood" else 0
        )
        w["balconies"] = v([])  # Existing generated balcony maps remain bound before this painter.


def resolve(building, vertices, eaves):
    renderer()  # Make the shared textures package importable in the OCI pipeline interpreter.
    from turkutex.facade_archetypes import sample

    geometry = []
    ring = np.asarray(building.footprint.exterior.coords)[:, :2]
    edge_a, edge_b = ring[:-1], ring[1:]
    vectors = edge_b - edge_a
    lengths = np.linalg.norm(vectors, axis=1)
    tangents = vectors / np.maximum(lengths[:, None], 1e-9)
    normal = np.column_stack((tangents[:, 1], -tangents[:, 0]))
    for index in range(0, len(vertices), 4):
        p = vertices[index : index + 4]
        width = float(np.linalg.norm(p[1, [0, 2]] - p[0, [0, 2]]))
        direction = (p[1, [0, 2]] - p[0, [0, 2]]) / max(width, 1e-9)
        along = np.sum((p[0, [0, 2]] - edge_a) * tangents, axis=1)
        distance = np.abs(np.sum((p[0, [0, 2]] - edge_a) * normal, axis=1))
        candidates = np.flatnonzero(
            (tangents @ direction > 0.995)
            & (distance < 0.1)
            & (along > -0.05)
            & (along + width < lengths + 0.05)
        )
        whole = max(candidates, key=lambda j: lengths[j]) if len(candidates) else None
        geometry.append(
            dict(
                wall_id=f"{building.facade_key}:{index // 4}",
                width_m=float(lengths[whole]) if whole is not None else width,
                uv_offset=float(along[whole]) if whole is not None else 0,
                height_m=max(0.1, float(eaves.get(index, min(p[2:, 1])) - building.base)),
            )
        )
    from turku3d.facade_inference import apply_prior, evidence, reading_fields, supported

    record = evidence(building.facade_key)
    attrs = dict(building.attributes)
    for key, item in record.get("building", {}).items():
        if supported(item):
            attrs[key] = item["value"]
    area = building.footprint.area
    attrs.update(
        E=float(building.footprint.centroid.x + C.ORIGIN_E),
        N=float(C.ORIGIN_N - building.footprint.centroid.y),
        log_area=float(np.log(max(area, 1))),
        footprint_m2=float(area),
        compactness=float(4 * np.pi * area / building.footprint.length**2),
        frontage_m=max(g["width_m"] for g in geometry),
    )
    from turku3d.facade_learning import apply as apply_learned

    d = read_description(building.facade_key)
    program = sample(building.facade_key, attrs, geometry)
    if not d:
        program = apply_learned(program)
    program["part_id"] = building.key
    # The calibrated register fallback owns colour; archetypes own layout and material priors.
    fallback = renderer().register_description(
        str(building.facade_key),
        str(building.facade_key),
        attrs,
        attrs["frontage_m"],
        building.top - building.base,
    )
    for wall in program["walls"]:
        wall["wall"]["colour"].update(value=fallback["wall_colour"], source="register palette")
        wall["base"]["colour"] = dict(wall["wall"]["colour"])
    from turku3d.facade_assets import description_row

    if d:
        row = description_row(building.facade_key) or {}
        describe(program, d, row.get("width_m") or attrs["frontage_m"])
        program["description_source"] = row.get("source", "existing")
    observed = measured(building.facade_key)
    for index, wall in enumerate(program["walls"]):
        own = matching_wall(vertices[index * 4 : index * 4 + 4], observed)
        apply_prior(wall, program["archetype"], attrs, own, record)
        if own:
            # Geometry dimensions and register storeys belong to this actual generated shell.
            colour = copy.deepcopy(wall["wall"]["colour"])
            base_colour = copy.deepcopy(wall["base"]["colour"])
            overlay(
                wall,
                {
                    k: v
                    for k, v in reading_fields(own).items()
                    if k not in ("wall_id", "width_m", "height_m", "geometry", "observation")
                },
            )
            for key in ("structure", "render_sections", "geometry_components", "geometry_pending", "unsupported"):
                if key in own:
                    wall[key] = copy.deepcopy(own[key])
            if own.get("bays", {}).get("support", 0) >= 0.6:
                wall["niches"] = copy.deepcopy(own.get("niches", []))
            wall["material_bands"] = [copy.deepcopy(band) for band in own.get("material_bands", []) if band.get("support", 0) >= 0.6]
            wall["observation"] = copy.deepcopy(own["observation"])
            wall["measured_wall_id"] = own["wall_id"]
            wall["measured_geometry"] = copy.deepcopy(own.get("geometry", {}))
            wall["measured_size"] = [own["width_m"], own["height_m"]]
            wall["paint_source"] = "reviewed-program"
            # Uncalibrated photographed RGB includes light/shadow; do not call it pigment.
            if "calibrated albedo" != own.get("observation", {}).get("colour_space"):
                wall["wall"]["colour"] = colour
                wall["base"]["colour"] = base_colour
        elif wall.get("learned_layout") and not d:
            from turkutex.facade_learned_layout import expand

            points = vertices[index * 4 : index * 4 + 4]
            tangent = (points[1] - points[0]) / max(
                float(np.linalg.norm(points[1] - points[0])), 1e-6
            )
            start = points[0] - tangent * wall.get("uv_offset", 0)
            end = start + tangent * wall["width_m"]
            expand(
                wall,
                program["storeys"]["value"],
                dict(
                    p0=[float(start[0] + C.ORIGIN_E), float(C.ORIGIN_N - start[2])],
                    p1=[float(end[0] + C.ORIGIN_E), float(C.ORIGIN_N - end[2])],
                    base_h=building.base,
                ),
            )
    reviewed = [w for w in program["walls"] if w.get("measured_wall_id")]
    if reviewed:
        program["source"] = "reviewed-overlay"
        program["representative_wall"] = reviewed[0]["measured_wall_id"]
    return program


HEAD_SHAPES = {"flat": 0, "segmental-arch": 1, "round-arch": 2, "pointed-arch": 3}


def compile_wall(wall, floors):
    width, height = wall["width_m"], wall["height_m"]
    material = field(wall["wall"], "material", "plaster")
    from turku3d.facade_inference import material as normalize_material
    material = normalize_material(material)
    material = material if material in MATERIALS else "plaster"
    count = max(0, int(wall["bays"]["count"]))
    count = count if floors else 0
    bay = width / max(1, count)
    ground_kind = field(wall["base"], "variant", "windows")
    if field(wall["base"], "shopfront", False):
        ground_kind = "shops"
    ground = field(wall["split"], "base_height_m", height / floors if floors else height)
    ground = min(height, max(0.1, ground))
    upper = (height - ground) / (floors - 1) if floors and floors > 1 else ground
    upper = max(0.1, upper)
    ww, wh = field(wall["windows"], "size_m", [bay * 0.45, upper * 0.5])
    ww = min(0.90, max(0.15, ww / bay))
    sill = min(0.6, max(0.03, field(wall["windows"], "sill_fraction", 0.21)))
    wh = min(1 - sill - 0.04, max(0.15, wh / upper))
    module = (
        MATERIALS.index(material) * 6
        + int(ww > 0.55) * 3
        + {"doors": 1, "shops": 2}.get(ground_kind, 0)
    )
    if not count:
        module = 42 + MATERIALS.index(material)

    def dimension(section, key, default=0):
        x = field(section, key, default)
        return float(x) if isinstance(x, (int, float)) else default

    return dict(
        module=module,
        bay=bay,
        storey=height / floors if floors else height,
        ground=ground,
        upper=upper,
        wall=field(wall["wall"], "colour", "#c5c8bd"),
        base=field(wall["base"], "colour", field(wall["wall"], "colour", "#c5c8bd")),
        trim=field(wall["trim"], "colour", "#deded3"),
        plinth=False,
        size=[width, height],
        origin=[-wall.get("uv_offset", 0), wall.get("base_h", 0)],
        opening=[ww, wh, sill, dimension(wall["trim"], "window_surrounds", 0.04)],
        bands=[
            dimension(wall["base"], "plinth_height_m"),
            dimension(wall["trim"], "cornice"),
            dimension(wall["trim"], "string_courses"),
            dimension(wall["corners"], "quoins"),
        ],
        detail=[
            0.1 if wall["bays"].get("rhythm") == "paired" else 0,
            int(bool(field(wall, "balconies", []))),
            int(ground_kind == "plain")
            + 2
            * HEAD_SHAPES.get(
                field(
                    wall["doors"] if ground_kind == "doors" else wall["base"], "head_shape", "flat"
                ),
                0,
            ),
            -HEAD_SHAPES.get(field(wall["windows"], "head_shape", "flat"), 0),
        ],
    )
