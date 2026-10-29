"""Generated facade regressions: per-field support, wall scope and stone paint."""

import copy
from types import SimpleNamespace

import numpy as np
import pytest
import shapely
from test_facade_structure import fixture
from turku3d import config as C
from turku3d.facade_inference import overlay, reading_fields
from turku3d.facade_programs import compile_wall, resolve
from turku3d.facade_sections import prepare
from turkutex.facade_structure import compile_structure


def test_supported_leaves_and_explicit_negatives_do_not_erase_richer_fields():
    prior = dict(
        wall=dict(material=dict(value="stone", support=0.4)),
        trim=dict(cornice=dict(value=0.4, support=0.4)),
        balconies=dict(value=[[1, 2, 3, 1]], support=0.4),
    )
    source = dict(
        wall=dict(material=dict(value="render", support=0.2)),
        trim=dict(cornice=dict(value=None, support=0.9)),
        balconies=dict(value=[], support=0.9),
    )
    original = copy.deepcopy(source)
    overlay(prior, source)
    assert prior["wall"]["material"]["value"] == "stone"
    assert prior["trim"]["cornice"]["value"] == 0.4
    assert prior["balconies"]["value"] == []
    assert source == original


def test_low_confidence_row_and_shapes_do_not_override_supported_other_row():
    row, form = fixture()
    form["floors"][0]["confidence"]["bay_count"] = 0.1
    form["floors"][1]["confidence"].update(head=0.1, surround=0.1)
    wall = compile_structure(row, form, 2)
    assert wall["doors"]["candidates"] == []
    assert len(wall["windows"]["rectangles_m"]) == 2
    assert wall["windows"]["head_shapes"] == [None, None]
    assert wall["windows"]["surround_shapes"] == [None, None]
    assert reading_fields(wall)["windows"]["support"] == 0.8


@pytest.mark.parametrize("material", ["stone", "granite", "rusticated-stone", "rusticated_stone"])
def test_stone_reading_compiles_to_stone_painter_module(material):
    row, form = fixture()
    form["material"] = material
    wall = compile_structure(row, form, 2)
    assert compile_wall(wall, 2)["module"] // 6 == 2


def test_material_colour_and_structure_stay_on_the_observed_wall(monkeypatch):
    from turku3d import facade_programs as fp

    row, form = fixture()
    row["normal"] = [0, 1]
    form["material"] = "brick"
    observed = prepare(
        dict(building_id="test", storeys=dict(value=2), walls=[compile_structure(row, form, 2)])
    )
    monkeypatch.setattr(fp, "measured", lambda _: observed)
    monkeypatch.setattr(fp, "read_description", lambda _: None)
    from turku3d import facade_inference

    monkeypatch.setattr(
        facade_inference,
        "evidence",
        lambda _: dict(
            building=dict(material_class=dict(value="stone", support=1, source="owner"))
        ),
    )
    vertices = np.array([[0, 2, 0], [12, 2, 0], [12, 10, 0], [0, 10, 0]], float)
    b = SimpleNamespace(
        key="test",
        facade_key="test",
        base=2,
        top=10,
        footprint=shapely.box(0, 0, 12, 5),
        attributes=dict(levels=2, construction_year=1887, material_class="stone"),
    )
    program = resolve(b, np.concatenate([vertices, vertices + [0, 0, 5]]), {})
    own, other = program["walls"]
    assert own["wall"]["material"]["value"] == "brick"
    assert other["wall"]["material"]["value"] == "stone"
    assert own["bays"]["count"] == 2
    assert not other.get("measured_wall_id")


def test_target_descriptors_are_wall_local_and_hashed(tmp_path, monkeypatch):
    from turku3d.facade_inference import apply_prior, evidence
    from turku3d.facade_programs import program_hash
    from turkutex.facade_archetypes import sample

    record = evidence("103295896N")
    program = sample(
        "test",
        dict(levels=3, construction_year=1887),
        [dict(wall_id="test", width_m=40, height_m=15)],
    )
    own, other = copy.deepcopy(program["walls"][0]), copy.deepcopy(program["walls"][0])
    apply_prior(
        own, "neo_renaissance", {}, dict(wall_id="103295896N_770004661f", structure={}), record
    )
    apply_prior(other, "neo_renaissance", {}, dict(wall_id="unrelated"), record)
    assert own["wall"]["colour"]["value"] == "#ac9686"
    assert other["wall"]["colour"]["value"] != "#ac9686"
    before = program_hash("103295896N")
    monkeypatch.setattr(C, "REPO_ROOT", tmp_path)
    assert program_hash("103295896N") != before


def test_existing_uncalibrated_patch_is_a_wall_local_prior_not_discarded():
    from turku3d.facade_inference import apply_prior
    from turkutex.facade_archetypes import sample

    wall = sample(
        "test",
        dict(levels=3, construction_year=1887),
        [dict(wall_id="test", width_m=20, height_m=12)],
    )["walls"][0]
    own = dict(
        wall_id="own",
        wall=dict(colour=dict(value="#ac958b", support=0.65, source="reviewed wall patch")),
    )
    other = copy.deepcopy(wall)
    apply_prior(wall, "neo_renaissance", {}, own, {})
    apply_prior(other, "neo_renaissance", {}, dict(wall_id="other"), {})
    assert wall["wall"]["colour"]["value"] == "#ac958b"
    assert "uncalibrated" in wall["wall"]["colour"]["source"]
    assert other["wall"]["colour"]["value"] != "#ac958b"
    assert wall["appearance_details"]["pilasters"]["value"]


def test_real_historic_retail_attributes_keep_style_and_shops():
    from turkutex.facade_archetypes import archetype, sample

    attrs = dict(
        construction_year=1887,
        era="1850-1919",
        levels=3,
        levels_source="register",
        material_class="brick",
        use="1150",
        osm_use="retail",
        ground_floor="shops",
        register_id="103295896N",
    )
    assert archetype(attrs) == "neo_renaissance"
    assert archetype({**attrs, "construction_year": 1909}) == "jugend"
    assert archetype({**attrs, "construction_year": 1980}) == "shopping"
    program = sample("103295896N", attrs, [dict(wall_id="street", width_m=40, height_m=15)])
    assert program["walls"][0]["base"]["variant"]["value"] == "shops"
    from turku3d.facade_inference import apply_prior

    wall = program["walls"][0]
    apply_prior(wall, program["archetype"], attrs, dict(wall_id="street"), {})
    assert wall["appearance_details"]["pilasters"]["value"]


@pytest.mark.parametrize("support,expected", [(0.2, "stone"), (0.9, "brick")])
def test_stone_family_reaches_unread_fragments_trim_and_partial_zones(
    monkeypatch, support, expected
):
    from turku3d import facade_inference as fi
    from turku3d import facade_programs as fp
    from turku3d.coarse.stored_facades import wall_meshes
    from turku3d.facade_kit import MATERIALS, renderer
    from turku3d.facade_program_geometry import wall_parts

    row, form = fixture()
    row["normal"] = [0, 1]
    form["material"] = None
    form["render_deferred"] = "partial upper band"
    form["facade_observations"] = dict(
        confidence=dict(material=0.2),
        sections=[dict(position="upper", material="brick", confidence=dict(material=support))],
    )
    observed = prepare(
        dict(building_id="test", storeys=dict(value=2), walls=[compile_structure(row, form, 2)])
    )
    original = copy.deepcopy(observed)
    monkeypatch.setattr(fp, "measured", lambda _: observed)
    monkeypatch.setattr(
        fi,
        "evidence",
        lambda _: dict(
            building=dict(material_class=dict(value="stone", support=1, source="owner"))
        ),
    )
    d = renderer().register_description("test", "test", {}, 12, 8)
    d.update(material="plaster", wall_colour="#dfc477", trim_colour="#ffe099")
    monkeypatch.setattr(fp, "read_description", lambda _: d)
    vertices = np.array([[0, 2, 0], [12, 2, 0], [12, 10, 0], [0, 10, 0]], float)
    b = SimpleNamespace(
        key="test",
        facade_key="test",
        base=2,
        top=10,
        footprint=shapely.box(0, 0, 12, 5),
        attributes=dict(levels=2, material_class="brick"),
    )
    meshes = wall_meshes(b, np.concatenate([vertices, vertices + [0, 0, 5]]), np.zeros(3))
    assert {m.extras["material_key"] for m in meshes} == {"facade_kit"}
    program = meshes[0].extras["facade_program"]
    for w in program["walls"]:
        assert w["wall"]["material"]["value"] == "stone"
        assert w["base"]["material"]["value"] == w["trim"]["material"]["value"] == "stone"
        style = compile_wall(w, 2)
        assert style["trim_module"] == 44
        assert style["wall"] != "#dfc477" and style["trim"] != "#ffe099"
    own = program["walls"][0]
    assert own["render_sections"][0]["material"] == expected
    paints = list(wall_parts(vertices, own, compile_wall(own, 2)))
    materials = {
        MATERIALS[s["module"] // 6 if s["module"] < 42 else s["module"] - 42] for _, _, s in paints
    }
    assert materials == {"stone", expected}
    assert observed == original


def test_unopposed_supported_stone_reading_supplies_building_family():
    from turku3d.facade_inference import building_material

    wall = dict(
        observation=dict(needs_review=False), wall=dict(material=dict(value="granite", support=0.9))
    )
    observed = dict(walls=[wall])
    assert building_material(dict(material_class="render"), {}, observed)["value"] == "stone"
    contrary = copy.deepcopy(wall)
    contrary["wall"]["material"]["value"] = "brick"
    observed["walls"].append(contrary)
    assert building_material(dict(material_class="render"), {}, observed)["value"] == "plaster"
