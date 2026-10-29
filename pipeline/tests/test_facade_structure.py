"""Count-only review must preserve unknown bays, register authority and wall scope."""

import sys
from pathlib import Path

import numpy as np
import pytest
from turku3d import config as C
from turku3d.facade_program_geometry import wall_parts
from turku3d.facade_programs import compile_wall, overlay
from turkutex.facade_archetypes import sample
from turkutex.facade_program import merge_building
from turkutex.facade_structure import FIELDS, compile_structure, validate


def fixture():
    form = dict(
        wall_id="south",
        floors_visible=2,
        material="render",
        ornament="medium",
        cornice="projecting",
        string_courses=[1],
        balconies=[],
        occluded_parts="left ground hidden",
        confidence={k: 0.8 for k in FIELDS},
        floors=[],
    )
    for role, bays, head, surround in [
        ("base", ["unknown", "arched portal"], "round", "rustication"),
        ("top", ["window", "window"], ["flat", "segmental"], "pediment"),
    ]:
        form["floors"].append(
            dict(
                role=role,
                bays=bays,
                bay_count=2,
                head=head,
                surround=surround,
                confidence={k: 0.8 for k in ("bay_count", "bay_kinds", "head", "surround")},
            )
        )
    row = dict(
        wall_id="south",
        width_m=12,
        height_m=8,
        base_h=2,
        p0=[C.ORIGIN_E, C.ORIGIN_N],
        p1=[C.ORIGIN_E + 12, C.ORIGIN_N],
        normal=[0, -1],
    )
    return row, form


def test_counts_do_not_change_register_or_invent_hidden_openings():
    row, form = fixture()
    wall = compile_structure(row, form, 7)
    p = merge_building("building", dict(levels=7), [wall])
    assert p["storeys"]["value"] == 7
    assert wall["observation"]["register_height_conflict"]
    assert wall["observation"]["excluded_rectangles_m"] == [[0, 0, 6, 4]]
    assert len(wall["windows"]["rectangles_m"]) == 2
    assert len(wall["doors"]["candidates"]) == 1
    assert wall["wall"]["colour"]["value"] is None
    assert wall["windows"]["head_shapes"] == ["flat", "segmental-arch"]


def test_form_rejects_inconsistent_counts_and_missing_confidence():
    _, form = fixture()
    form["floors"][0]["bay_count"] = 3
    with pytest.raises(ValueError, match="bay count"):
        validate(form)
    _, form = fixture()
    del form["confidence"]["material"]
    with pytest.raises(ValueError, match="confidence"):
        validate(form)


def test_one_row_shop_strip_does_not_clear_unobserved_upper_storeys():
    row, form = fixture()
    form["floors"] = form["floors"][:1]
    form["floors_visible"] = 1
    form["string_courses"] = []
    row["height_m"] = 3.5
    wall = compile_structure(row, form, 8)
    assert wall["observation"]["scope"] == "ground-only"
    assert wall["height_m"] == 3.5


def test_paired_pointed_windows_and_short_rows_preserve_structural_counts():
    row, form = fixture()
    top = form["floors"][1]
    top.update(opening_counts=[2, 2], head="pointed", height_class="short", material="brick")
    top["confidence"].update(opening_counts=0.9, height_class=0.9, material=0.9)
    wall = compile_structure(row, form, 2)
    assert len(wall["windows"]["rectangles_m"]) == 4
    assert wall["windows"]["head_shapes"] == ["pointed-arch"] * 4
    assert wall["material_bands"][0]["material"] == "brick"
    assert wall["material_bands"][0]["height_m"] < 4
    assert wall["trim"]["string_courses"]["value"][0] > 4


def test_characterized_complex_wall_keeps_existing_artwork_until_renderer_support():
    row, _ = fixture()
    form = dict(
        wall_id="south",
        render_deferred="clock needs dedicated treatment",
        facade_observations=dict(
            sections=[dict(kind="clock", count=1)], confidence=dict(clock=0.99)
        ),
    )
    wall = compile_structure(row, form, 4)
    assert wall["observation"]["needs_review"]
    assert wall["structure"]["facade_observations"]["sections"][0]["kind"] == "clock"


def test_surrounds_stay_on_the_matching_wall_and_keep_opening_heads():
    row, form = fixture()
    measured = compile_structure(row, form, 2)
    wall = sample("building", dict(levels=2), [row])["walls"][0]
    overlay(wall, measured)
    wall.update(measured_geometry=measured["geometry"], measured_size=[12, 8])
    vertices = np.array([[0, 2, 0], [12, 2, 0], [12, 10, 0], [0, 10, 0]], float)
    parts = list(wall_parts(vertices, wall, compile_wall(wall, 2)))
    heads = [p[2]["detail"][3] for p in parts if p[2]["detail"][3] > 0]
    assert sorted(heads) == [1, 5, 10]
    assert len(parts) > 20
    assert all(p[0][:, 0].min() >= 0 and p[0][:, 0].max() <= 12 for p in parts)
    assert all(p[0][:, 1].min() >= 2 and p[0][:, 1].max() <= 10 for p in parts)


def test_pose_refinement_recovers_camera_rotation_from_line_rays():
    from scipy.spatial.transform import Rotation

    sys.path.insert(
        0, str(Path(__file__).resolve().parents[2] / "tools/research/facade_characterization")
    )
    from pose import fit_rotation

    camera = np.array([5, -15, 3.0])
    corners = np.array([[0, 0, 0], [20, 0, 0], [20, 0, 12], [0, 0, 12]], float)
    true = Rotation.from_euler("xyz", [0.7, -0.8, 1.2], degrees=True).as_matrix()
    lines = [[[x, 0, 1], [x, 0, 10]] for x in np.linspace(1, 19, 8)]
    lines += [[[1, 0, z], [19, 0, z]] for z in np.linspace(1, 11, 8)]
    rays = (np.array(lines) - camera) @ true
    fitted, report = fit_rotation(
        rays, np.array([True] * 8 + [False] * 8), np.eye(3), camera, corners
    )
    assert report["accepted"]
    assert report["median_after_degrees"] < 0.01
    assert np.max(abs(fitted - true)) < 1e-4
