"""Leakage, conditional/local distribution and partial evidence contracts."""

import copy

import numpy as np
from test_facade_structure import fixture
from turku3d.facade_programs import compile_wall
from turku3d.facade_sections import prepare
from turkutex.facade_archetypes import sample
from turkutex.facade_learning import apply, fit, predict, targets, weights
from turkutex.facade_sections import sections
from turkutex.facade_structure import compile_structure


def building(key, year, east, bays=2):
    row, form = fixture()
    form["floors"][0]["bays"] = ["window", "window"]
    form["floors"][1].update(bay_count=bays, bays=["window"] * bays, head="flat")
    return dict(
        building_id=key,
        attributes=dict(construction_year=year, E=east, N=0, levels=2),
        walls=[compile_structure(row, form, 2)],
    )


def test_fit_balances_buildings_excludes_unread_fields_and_is_order_independent():
    a, b = building("a", 1900, 0), building("b", 1980, 100, 4)
    duplicated = copy.deepcopy(a)
    duplicated["walls"] *= 8
    assert fit([a, b]) == fit([b, duplicated])
    model = fit([a, b])
    assert all(d[0]["id"] != "a" for d in weights("a", a["attributes"], model))
    assert predict("new", a["attributes"], model, sampled=True) == predict(
        "new", a["attributes"], fit([b, a]), sampled=True
    )
    assert "bay_m" not in targets(
        dict(
            structure=dict(
                render_deferred="partial",
                facade_observations=dict(
                    sections=[dict(window_count_lower_bound=3, material="render or panel")]
                ),
            )
        )
    )


def test_nearby_same_era_and_street_has_stronger_weight_than_distant_donor():
    model = fit([building("near", 1900, 10), building("far", 1900, 1000)])
    rows = weights("new", dict(construction_year=1900, E=0, N=0), model)
    result = {d["id"]: w for d, w in rows}
    assert result["near"] > result["far"] * 2
    model["donors"][1]["attributes"]["construction_year"] = 2000
    assert predict("new", dict(construction_year=1900), model)


def test_readings_and_register_storeys_remain_authoritative():
    model = fit([building("known", 1900, 10)])
    p = sample("known", dict(levels=7), [dict(wall_id="w", width_m=12, height_m=10)])
    before = copy.deepcopy(p)
    assert apply(p, model) == before
    p = sample("unknown", dict(levels=None), [dict(wall_id="w", width_m=12, height_m=10)])
    assert apply(p, model)["storeys"]["value"] is None
    assert compile_wall(p["walls"][0], None)["module"] >= 42


def test_partial_material_does_not_invent_complete_openings_or_current_geometry():
    form = dict(
        render_deferred="partial",
        facade_observations=dict(
            sections=[
                dict(
                    position="upper",
                    material="pale timber",
                    kind="windows",
                    window_count_lower_bound=3,
                )
            ]
        ),
    )
    parts, missing = sections(form, 4)
    assert len(parts) == 1 and parts[0]["material"] == "wood"
    assert parts[0]["kind"] is None and missing
    form["render_deferred"] = "Historical facade absent in current view"
    assert sections(form, 4)[0] == []


def test_partial_sections_are_prepared_without_mutating_private_programs():
    row, _ = fixture()
    form = dict(
        wall_id=row["wall_id"],
        render_deferred="partial upper",
        facade_observations=dict(
            sections=[dict(position="upper", material="brick", kind="blank wall")],
            confidence=dict(material=0.9),
        ),
    )
    wall = compile_structure(row, form, 4)
    original = dict(building_id="test", needs_review=True, storeys=dict(value=4), walls=[wall])
    prepared = prepare(original)
    assert original["needs_review"]
    assert not prepared["needs_review"]
    assert prepared["walls"][0]["observation"]["scope"] == "partial-sections"
    assert prepared["walls"][0]["render_sections"][0]["kind"] == "blank"


def test_opaque_service_door_is_not_glazing():
    from turku3d.facade_program_geometry import wall_parts

    row, form = fixture()
    form["floors"][0]["bays"] = ["unknown", "opaque door"]
    wall = compile_structure(row, form, 2)
    assert wall["doors"]["candidates"][0]["opaque"]
    wall.update(measured_geometry=wall["geometry"], measured_size=[12, 8])
    vertices = np.array([[0, 2, 0], [12, 2, 0], [12, 10, 0], [0, 10, 0]], float)
    parts = list(wall_parts(vertices, wall, compile_wall(wall, 2)))
    assert any(p[2]["module"] == 48 and p[2]["wall"] == "#55534e" for p in parts)


def test_learned_multiple_windows_surrounds_and_balconies_reach_metric_painter():
    from turkutex.facade_learned_layout import expand

    row, _ = fixture()
    wall = sample("synthetic", dict(levels=2), [row])["walls"][0]
    wall["bays"].update(count=4, pitch_m=3, rhythm="ribbon")
    wall["base"].update(variant=dict(value="windows"), glazing_share=dict(value=0))
    wall["learned_layout"] = dict(windows_per_bay=2, surround="pediment", balcony=True)
    expand(wall, 2, {k: row[k] for k in ("p0", "p1", "base_h")})
    assert len(wall["windows"]["rectangles_m"]) == 12
    assert wall["windows"]["surround_shapes"] == ["pediment"] * 12
    assert len(wall["balconies"]["value"]) == 2
    assert not wall.get("measured_wall_id")
    assert wall["observation"]["opening_extent"] == "sampled learned grammar; no photo measurement"


def test_ornament_levels_add_distinct_bounded_trim():
    from turku3d.facade_program_geometry import wall_parts

    row, form = fixture()
    vertices = np.array([[0, 2, 0], [12, 2, 0], [12, 10, 0], [0, 10, 0]], float)
    sizes = []
    for level in ("none", "low", "medium", "high"):
        form["ornament"] = level
        wall = compile_structure(row, form, 2)
        wall.update(measured_geometry=wall["geometry"], measured_size=[12, 8])
        sizes.append(len(list(wall_parts(vertices, wall, compile_wall(wall, 2)))))
    assert all(a < b for a, b in zip(sizes, sizes[1:], strict=False))


def test_partial_sections_partition_parent_uv_frame_and_preserve_unseen_area():
    from turku3d.facade_program_geometry import wall_parts
    from turku3d.wall_surfaces import overlaps

    row, _ = fixture()
    form = dict(
        wall_id=row["wall_id"],
        render_deferred="mixed right bay",
        facade_observations=dict(
            sections=[
                dict(position="right upper", kind="windows", count=2, rows=2, material="brick")
            ],
            confidence=dict(material=0.9),
        ),
    )
    own = prepare(
        dict(building_id="p", storeys=dict(value=4), walls=[compile_structure(row, form, 4)])
    )["walls"][0]
    wall = sample("p", dict(levels=4), [row])["walls"][0]
    from turku3d.facade_programs import overlay

    from turku3d.facade_inference import reading_fields
    overlay(wall, reading_fields(own))
    wall["render_sections"] = own["render_sections"]
    wall.update(measured_geometry=own["geometry"], measured_size=[12, 8])
    vertices = np.array([[0, 2, 0], [12, 2, 0], [12, 10, 0], [0, 10, 0]], float)
    parts = list(wall_parts(vertices, wall, compile_wall(wall, 4)))
    assert not overlaps([p for p, _, _ in parts], list(range(len(parts))))
    assert any(s.get("unreviewed") for _, _, s in parts)
    openings = [(p, uv, s) for p, uv, s in parts if s["detail"][3] > 0]
    assert openings
    for points, uv, style in openings:
        assert np.allclose(points[:, 0], uv[:, 0])
        assert style["origin"][0] > 8
        assert points[:, 1].min() >= 4
