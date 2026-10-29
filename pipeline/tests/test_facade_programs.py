"""Authority, stable sampling, measured scope and tile-local invalidation contracts."""

import copy
import json

import numpy as np
import pytest

from turkutex.facade_archetypes import ARCHETYPES, archetype, sample

from turku3d import config as C
from turku3d.facade_assets import opaque
from turku3d.facade_kit import parameter_hash
from turku3d.facade_program_geometry import wall_parts
from turku3d.facade_programs import compile_wall, matching_wall


def program(levels=4, key="registry-123"):
    return sample(
        key,
        {"construction_year": 1890, "levels": levels},
        [dict(wall_id="south", width_m=23.3, height_m=11.5)],
    )


def test_seed_survives_order_and_unrelated_attributes_with_visible_neighbour_variance():
    a = program()
    assert a == program()
    other = sample(
        "registry-123",
        {"unused": 99, "construction_year": 1890, "levels": 4},
        [{k: a["walls"][0][k] for k in ("wall_id", "width_m", "height_m")}],
    )
    assert other["walls"][0]["wall"] == a["walls"][0]["wall"]
    neighbours = [program(key=str(i))["walls"][0] for i in range(30)]
    assert len({w["wall"]["colour"]["value"] for w in neighbours}) > 20
    assert len({tuple(w["windows"]["size_m"]["value"]) for w in neighbours}) == 30
    assert len(ARCHETYPES) == 20


@pytest.mark.parametrize("levels", [None, 1, 4, 8, 30])
def test_register_storeys_are_never_reestimated_even_for_conflicting_height(levels):
    p = program(levels)
    wall = p["walls"][0]
    style = compile_wall(wall, p["storeys"]["value"])
    assert p["storeys"]["value"] == levels
    assert style["ground"] + max(0, (levels or 1) - 1) * style["upper"] == pytest.approx(11.5)
    if levels is None:
        assert style["module"] >= 42
    else:
        assert style["bay"] * wall["bays"]["count"] == pytest.approx(23.3)


def test_use_material_and_period_rules():
    assert archetype(dict(use="1310", construction_year=1900)) == "industrial_brick"
    assert archetype(dict(use="1170", construction_year=1980)) == "shopping"
    assert archetype(dict(material_class="wood", construction_year=1820)) == "timber_traditional"
    assert archetype(dict(construction_year=1925)) == "nordic_classicism"
    assert archetype(dict(construction_year=1975)) == "panel_seventies"
    p = sample(
        "osm-only",
        dict(levels=8, levels_source="osm"),
        [{"wall_id": "test", "width_m": 12, "height_m": 10}],
    )
    assert p["storeys"]["value"] is None


def test_reviewed_strip_has_exact_windows_without_new_rows_or_bays():
    wall = program()["walls"][0]
    wall.update(
        measured_geometry=dict(
            p0=[C.ORIGIN_E, C.ORIGIN_N], p1=[C.ORIGIN_E + 23.3, C.ORIGIN_N], base_h=2
        ),
        measured_size=[23.3, 4],
    )
    wall["windows"]["rectangles_m"] = [[1, 1, 2, 2], [8, 1, 3, 2]]
    vertices = np.array([[0, 2, 0], [23.3, 2, 0], [23.3, 13.5, 0], [0, 13.5, 0]])
    style = compile_wall(wall, 4)
    style["origin"][1] = 2
    parts = list(wall_parts(vertices, wall, style))
    patches = [row for row in parts if row[2]["detail"][3] == 1]
    assert len(patches) == 2
    assert [row[2]["origin"] for row in patches] == [[1, 3], [8, 3]]
    assert [row[2]["size"] for row in patches] == [[2, 2], [3, 2]]
    blank = [row for row in parts if row[2]["module"] >= 42]
    assert len(blank) == 1 and blank[0][0][:, 1].max() == 6
    assert any(row[0][:, 1].min() == 6 and row[2]["module"] < 42 for row in parts)
    wall["observation"]["scope"] = "full-wall"
    full = list(wall_parts(vertices, wall, style))
    assert all(row[2]["module"] >= 42 for row in full if row[0][:, 1].min() >= 6)


def test_measurement_cannot_bind_another_orientation_or_unreviewed_wall():
    wall = program()["walls"][0]
    wall["geometry"] = dict(
        p0=[C.ORIGIN_E, C.ORIGIN_N], p1=[C.ORIGIN_E + 23.3, C.ORIGIN_N], base_h=2, normal=[0, 0, -1]
    )
    p = dict(needs_review=False, walls=[wall])
    vertices = np.array([[0, 2, 0], [23.3, 2, 0], [23.3, 13.5, 0], [0, 13.5, 0]])
    assert matching_wall(vertices, p) is wall
    assert matching_wall(vertices + [0, 0, 3], p) is None
    p["needs_review"] = True
    assert matching_wall(vertices, p) is None


def test_program_edit_invalidates_only_its_buildings_tiles(tmp_path, monkeypatch):
    monkeypatch.setenv("TURKU3D_WORK", str(tmp_path))
    monkeypatch.setenv("TURKU3D_DATA", str(tmp_path / "raw-inputs"))
    monkeypatch.setenv("TURKU3D_FACADE_ASSETS", str(tmp_path / "assets"))
    before = [parameter_hash([key]) for key in ("own", "other")]
    target = tmp_path / "facade-programs" / opaque("own")
    target.parent.mkdir()
    target.write_text(json.dumps(program(key="own")))
    after = [parameter_hash([key]) for key in ("own", "other")]
    assert before[0] != after[0] and before[1] == after[1]
    altered = copy.deepcopy(program(key="own"))
    altered["walls"][0]["windows"]["size_m"]["value"][0] += 0.1
    target.write_text(json.dumps(altered))
    assert after[0] != parameter_hash(["own"])
