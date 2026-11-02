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


@pytest.mark.parametrize("levels,source,expected", [(None, None, 3), (4, "register", 4), (3, "osm", 3), (20, "osm", 3)])
def test_missing_storeys_get_visual_windows_without_changing_register(monkeypatch, levels, source, expected):
    from turku3d import facade_programs as fp, facade_learning

    monkeypatch.setattr(fp, "measured", lambda _: None)
    monkeypatch.setattr(fp, "read_description", lambda _: None)
    monkeypatch.setattr(facade_learning, "apply", lambda p: p)
    attrs = dict(levels=levels, levels_source=source, material_class="brick")
    building = SimpleNamespace(key="missing-levels", facade_key="missing-levels", base=2, top=11.6,
                               footprint=shapely.box(0, 0, 12, 5), attributes=attrs)
    vertices = np.array([[0, 2, 0], [12, 2, 0], [12, 11.6, 0], [0, 11.6, 0]])
    p = fp.resolve(building, vertices, {})
    assert p["storeys"]["value"] == expected
    assert fp.compile_wall(p["walls"][0], expected)["module"] < 42
    assert building.attributes == attrs and attrs["levels"] == levels
    if source == "register":
        assert p["storeys"]["source"] == "register" and p["storeys"]["support"] == 1
    else:
        assert p["storeys"]["support"] < 0.6
        assert "register" not in p["storeys"]["source"]


def test_learned_cladding_uses_its_material_palette(monkeypatch):
    from turku3d import facade_programs as fp, facade_learning
    from turkutex.wall_palette import sample

    monkeypatch.setattr(fp, "measured", lambda _: None)
    monkeypatch.setattr(fp, "read_description", lambda _: None)
    def learned(p):
        for w in p["walls"]:
            w["wall"]["material"]["value"] = "brick"
        return p
    monkeypatch.setattr(facade_learning, "apply", learned)
    b = SimpleNamespace(key="learned-brick", facade_key="learned-brick", base=0, top=9.6,
                        footprint=shapely.box(0, 0, 12, 5), attributes=dict(levels=3))
    p = fp.resolve(b, np.array([[0, 0, 0], [12, 0, 0], [12, 9.6, 0], [0, 9.6, 0]]), {})
    wall = p["walls"][0]
    assert wall["wall"]["colour"]["value"] == sample(b.key, {"material_class": "brick"})
    assert wall["wall"]["colour"]["value"] != sample(b.key, {})


def test_roof_fragments_clip_one_floor_grid_instead_of_rescaling_windows(monkeypatch):
    from turku3d import facade_programs as fp, facade_learning

    monkeypatch.setattr(fp, "measured", lambda _: None)
    monkeypatch.setattr(fp, "read_description", lambda _: None)
    monkeypatch.setattr(facade_learning, "apply", lambda p: p)
    b = SimpleNamespace(key="fragments", facade_key="fragments", base=0, top=12,
                        footprint=shapely.box(0, 0, 24, 5),
                        attributes=dict(levels=3, levels_source="register"))
    # Adjacent pieces of one original wall have different eave/occlusion cuts.
    vertices = np.array([[0, 0, 0], [12, 0, 0], [12, 12, 0], [0, 12, 0],
                         [12, 0, 0], [24, 0, 0], [24, 9, 0], [12, 9, 0]], float)
    p = fp.resolve(b, vertices, {})
    a, c = [fp.compile_wall(w, 3) for w in p["walls"]]
    assert a["ground"] == c["ground"]
    assert a["upper"] == c["upper"]
    assert a["opening"] == c["opening"]
    assert a["size"][1] == 12 and c["size"][1] == 9
    assert a["origin"][0] - c["origin"][0] == 12


def test_description_does_not_squeeze_a_window_into_a_narrow_roof_strip():
    from turku3d.facade_programs import describe
    from turkutex.facade_archetypes import sample
    from turku3d.facade_kit import renderer

    p = sample("narrow", dict(levels=3), [dict(width_m=0.5, height_m=12)])
    d = renderer().register_description("test", "test", {}, 30, 12)
    d["window_columns"] = 1
    describe(p, d, 30)
    assert p["walls"][0]["bays"]["count"] == 0
    assert compile_wall(p["walls"][0], 3)["module"] >= 42


def test_passage_solids_share_original_base_frontage_and_floor_grid(monkeypatch):
    from turku3d.coarse.stored_facades import facade_layouts
    from turku3d import facade_programs as fp, facade_learning

    monkeypatch.setattr(fp, "measured", lambda _: None)
    monkeypatch.setattr(fp, "read_description", lambda _: None)
    monkeypatch.setattr(facade_learning, "apply", lambda p: p)
    def part(x0, x1, base, top):
        b = SimpleNamespace(key="passage", facade_key="passage", base=base, top=12,
                            footprint=shapely.box(x0, 0, x1, 5),
                            attributes=dict(levels=3, levels_source="register"))
        wall = np.array([[x0, base, 0], [x1, base, 0], [x1, top, 0], [x0, top, 0]], float)
        return b, (None, None, None, None, wall, None, None)
    solids = [part(0, 10, 0, 12), part(10, 12, 4, 10), part(12, 24, 0, 12)]
    owner, height = facade_layouts(solids)["passage"]
    assert owner.base == 0 and height == 12
    assert owner.footprint.area == 120
    styles = []
    for _, shell in solids:
        p = fp.resolve(owner, shell[4], {}, layout_height=height)
        styles.append(fp.compile_wall(p["walls"][0], 3))
    assert len({(s["bay"], s["ground"], s["upper"], tuple(s["opening"])) for s in styles}) == 1
    assert [s["origin"][0] for s in styles] == [0, -10, -12]


def test_learned_rules_keep_shared_floor_and_window_dimensions(monkeypatch):
    from turkutex import facade_learning
    from turkutex.facade_learned_layout import expand
    from turkutex.facade_archetypes import sample

    prediction = dict(bay_m=3, ground="windows", ground_scale=1.2,
                      top_scale=1.0, window_width=.5, window_height=.5)
    monkeypatch.setattr(facade_learning, "predict", lambda *a, **k: prediction)
    for multiplicity in (1, 2):
        prediction["windows_per_bay"] = multiplicity
        p = sample("learned-fragments", dict(levels=3), [
            dict(width_m=12, height_m=h, layout_height_m=12) for h in (12, 9)])
        facade_learning.apply(p, {})
        for wall in p["walls"]:
            expand(wall, 3, dict(p0=[0, 0], p1=[12, 0], base_h=0))
        a, b = [compile_wall(w, 3) for w in p["walls"]]
        assert a["ground"] == b["ground"] and a["upper"] == b["upper"]
        assert a["opening"] == b["opening"]
        assert p["walls"][0]["windows"]["rectangles_m"] == p["walls"][1]["windows"]["rectangles_m"]


def test_reviewed_glazing_pattern_and_frame_colour_survive_compilation():
    from turkutex.facade_archetypes import sample
    from turkutex.facade_program import value

    wall = sample("panes", dict(levels=3), [dict(width_m=12, height_m=10)])["walls"][0]
    wall["windows"].update(
        divisions=value([5, 1], "reviewed window groups", .8),
        frame_colour=value("#b5c3bd", "uncalibrated frame appearance", .65),
        pane_split_fraction=value(.7, "reviewed broad/narrow groups", .7),
        transom_fraction=value(0, "no horizontal transom in observed panes", .8),
    )
    style = compile_wall(wall, 3)
    assert style["glazing"] == [5, 1, .7, 0]
    assert style["frame"] == "#b5c3bd"
    wall["windows"]["divisions"]["support"] = .2
    assert compile_wall(wall, 3)["glazing"][:2] == [0, 0]


def test_subdividing_a_shorter_wall_does_not_change_building_floor_height(monkeypatch):
    from turku3d import facade_programs as fp, facade_learning

    monkeypatch.setattr(fp, "measured", lambda _: None)
    monkeypatch.setattr(fp, "read_description", lambda _: None)
    monkeypatch.setattr(facade_learning, "apply", lambda p: p)
    b = SimpleNamespace(key="span", facade_key="span", base=0, top=12,
                        footprint=shapely.box(0, 0, 24, 5),
                        attributes=dict(levels=3, levels_source="register"))
    tall = [[0, 0, 0], [24, 0, 0], [24, 12, 0], [0, 12, 0]]
    layouts = []
    for cuts in ([24, 12], [24, 20, 16, 12]):
        points = list(tall)
        for a, c in zip(cuts[:-1], cuts[1:]):
            points.extend([[a, 0, 5], [c, 0, 5], [c, 9, 5], [a, 9, 5]])
        p = fp.resolve(b, np.array(points, float), {})
        layouts.append({w["layout_height_m"] for w in p["walls"]})
    assert layouts == [{12}, {12}]


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
