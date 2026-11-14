import json

import numpy as np
from turku3d import config as C
from turku3d import facade_readings as fr
from turku3d import facade_readings_convert as conv
from turku3d.facade_assets import opaque
from turku3d.facade_kit import read_description, renderer

E, N = C.ORIGIN_E, C.ORIGIN_N


def reading(key="b1", **extra):
    return {
        "id": f"building/{key}",
        "schema": "turku-description/building/1",
        "tier": "observed",
        "conf": {"*": 0.7},
        "provenance": [{"by": "w2-observe", "key": "reading-1", "at": "2026-11-13T00:00:00Z", "source": "panorama"}],
        "style": {"type": "modern_apartment"},
        "materials": {
            "wall": {"family": "brick", "colour": "#8A4B3A", "conf": {"*": 0.8}},
            "base": {"family": "concrete", "colour": "#888888", "conf": {"*": 0.6}},
        },
        "opening_types": {
            "win": {"kind": "window", "w_m": 1.2, "h_m": 1.5, "frame": {"colour": "#FFFFFF"}},
            "door": {"kind": "door", "w_m": 1.0, "h_m": 2.1},
            "shop": {"kind": "shopfront", "w_m": 4.0, "h_m": 3.0},
        },
        "roof": {"form_hint": "flat", "parapet": {"height_m": 0.4}},
        "wall_defaults": {"material": "wall", "storeys": []},
        "walls": [
            {
                "id": "w00",
                "material": "wall",
                "frame": {"p0": [E, N], "p1": [E + 12, N], "base_h": 0, "height_m": 9},
                "observed": {"visible_share": 0.9},
                "ground_floor_m": 0.3,
                "bays": [{"u_m": 1.5}, {"u_m": 4.5}, {"u_m": 7.5}, {"u_m": 10.5}],
                "storeys": [
                    {"kind": "ground", "height_m": 3.0, "window": "win", "openings": [{"type": "door", "u_m": 6}]},
                    {"kind": "upper", "height_m": 2.8, "count": 2, "window": "win"},
                ],
                "bands": [{"kind": "string_course", "v_m": 3.3}],
                "balconies": [{"type": "cantilever", "u_m": 4.5, "w_m": 2.0, "storeys": [1, 2],
                               "railing": {"height_m": 1.1}}],
            },
            {
                "id": "w01",
                "material": "base",
                "frame": {"p0": [E + 12, N], "p1": [E + 12, N - 5], "base_h": 0, "height_m": 9},
                "observed": {"visible_share": 0.2},
            },
        ],
        **extra,
    }


def test_reading_converts_to_a_valid_description_row():
    row, why = conv.convert(reading())
    assert why == "converted"
    d = row["description"]
    assert renderer().validate(d, d["facade_id"]) == []
    assert d["material"] == "brick" and d["wall_colour"] == "#8a4b3a"  # the larger, better-seen wall
    assert d["trim_colour"] == conv.TRIM["brick"] and row["frame_colour"] == "#ffffff"
    assert d["storeys"] == 3 and d["window_columns"] == 4 and row["width_m"] == 12
    assert d["window_width"] == 0.4 and round(d["window_height"], 3) == round(1.5 / 2.8, 3)
    assert d["ground_floor"] == "doors" and d["ground_openings"] == 2
    assert d["roof_line"] == "parapet" and d["features"] == ["balconies", "string_courses"]
    assert row["source"] == fr.SOURCE
    # Rails at the storey floors above the ground floor level, x from the wall's first point.
    assert row["walls"][0]["balconies"] == [[3.5, 3.3, 2.0, 1.1], [3.5, 6.1, 2.0, 1.1]]


def test_light_reading_keeps_material_and_colour_and_a_regular_grid():
    doc = reading(walls=[], opening_types={})
    doc["materials"]["wall"]["family"] = "weatherboard"
    doc["style"] = {"type": "detached_house"}
    row, _ = conv.convert(doc)
    d = row["description"]
    assert d["material"] == "wood" and d["storeys"] == 0  # storeys stay the register's
    assert d["window_columns"] == round(conv.NOMINAL_WIDTH_M / 2.6) and d["ground_floor"] == "windows"
    assert "walls" not in row


def test_sparse_bays_keep_the_read_window_size_at_the_drawn_pitch():
    # Two bays over 12 m read as a 6 m rhythm; proven draws at most a 4.8 m bay, so the 1.2 m
    # window is a quarter of that bay, not a fifth of the source's.
    doc = reading()
    doc["walls"][0]["bays"] = [{"u_m": 3.0}, {"u_m": 9.0}]
    d = conv.convert(doc)[0]["description"]
    assert d["window_columns"] == 2 and d["window_width"] == 0.25


def test_read_pilasters_and_cornices_set_the_trim_and_frames_keep_their_colour():
    doc = reading()
    doc["opening_types"]["win"]["frame"] = {"colour": "#4B3429"}
    doc["walls"][0]["bands"] = [{"kind": "cornice", "v_m": 8.6, "colour": "#E2E0D6"}]
    doc["walls"][0]["verticals"] = [{"kind": "pilaster", "u_m": 0.2, "material": "white"}] * 2
    doc["materials"]["white"] = {"family": "plaster", "colour": "#E6E4DC"}
    row, _ = conv.convert(doc)
    assert row["description"]["trim_colour"] == "#e6e4dc" and row["frame_colour"] == "#4b3429"
    # Pilasters in the wall's own colour are relief, and a dark frame never becomes the trim.
    doc["materials"]["white"]["colour"] = "#8C4D3C"
    doc["walls"][0]["bands"] = []
    row, _ = conv.convert(doc)
    assert row["description"]["trim_colour"] == conv.TRIM["brick"] and row["frame_colour"] == "#4b3429"


def test_a_rows_frame_colour_paints_only_the_window_frames():
    from turku3d.facade_programs import compile_wall, describe
    from turkutex.facade_archetypes import sample

    row, _ = conv.convert(reading())
    p = sample("frames", dict(levels=3), [dict(width_m=12, height_m=9)])
    describe(p, row["description"], row["width_m"], frame_colour="#4b3429")
    wall = compile_wall(p["walls"][0], 3)
    assert (wall["frame"], wall["trim"]) == ("#4b3429", conv.TRIM["brick"])


def test_a_row_of_wide_and_narrow_windows_draws_their_mean_size():
    doc = reading()
    doc["opening_types"]["wide"] = {"kind": "window", "w_m": 2.4, "h_m": 1.5}
    doc["walls"][0]["storeys"][1] = {"kind": "upper", "height_m": 2.8, "count": 2,
                                     "row": ["win", "wide", "wide", "win"]}
    # One ground window and two rows of four: 5 x 1.2 m and 4 x 2.4 m over 3 m bays.
    d = conv.convert(doc)[0]["description"]
    assert d["window_width"] == round((5 * 1.2 + 4 * 2.4) / 9 / 3, 4)


def test_unsure_or_unread_walls_are_not_converted():
    doc = reading()
    doc["materials"]["wall"]["conf"] = {"*": 0.2}
    doc["materials"]["base"]["conf"] = {"*": 0.2}
    assert conv.convert(doc)[0] is None
    doc = reading()
    doc["materials"] = {"roof": {"family": "felt", "colour": "#333333"}}
    assert conv.convert(doc) == (None, "no wall material read")


def test_reading_rows_rank_below_every_proven_description(tmp_path, monkeypatch):
    monkeypatch.setenv("TURKU3D_WORK", str(tmp_path / "work"))
    monkeypatch.setenv("TURKU3D_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("TURKU3D_FACADE_ASSETS", str(tmp_path / "assets"))
    monkeypatch.setattr(C, "REPO_ROOT", tmp_path)
    readings = tmp_path / "readings"
    readings.mkdir()
    (readings / "b1.json").write_text(json.dumps(reading("b1")))
    (readings / "b2.json").write_text(json.dumps(reading("b2")))
    (readings / "b3.json").write_text(json.dumps({**reading("b3"), "provenance": [{"by": "w2-colour batch 1"}]}))
    proven = {"b1": dict(description=None, program=False), "b2": dict(description=None, program=True)}
    report = conv.run_convert(readings, proven, fr.directory())
    assert (report["read"], report["converted"], report["skipped_proven_program"]) == (2, 1, 1)
    assert fr.keys() == {"b1"}
    assert read_description("gis-b1")["material"] == "brick"
    assert read_description("b2") is None
    retained = tmp_path / "textures/library/facade-params"
    retained.mkdir(parents=True)
    d = renderer().register_description("x", "b1", {}, 10, 9)
    (retained / opaque("b1")).write_text(json.dumps(dict(building_key="b1", description=d, width_m=10)))
    assert read_description("b1") == d


FRONT = np.array([[12, 0, 0], [0, 0, 0], [0, 9, 0], [12, 9, 0]], float)  # the read wall w00, reversed
SIDE = np.array([[12, 0, 0], [12, 0, 5], [12, 9, 5], [12, 9, 0]], float)  # faces another way


def test_reading_balconies_stay_on_their_own_wall_only():
    from turku3d.facade_programs import compile_wall, describe, described_walls
    from turkutex.facade_archetypes import sample

    row, _ = conv.convert(reading())
    walls = described_walls(np.concatenate([FRONT, SIDE]), row)
    assert list(walls) == [0] and walls[0]["rails"][0] == [6.5, 3.3, 2.0, 1.1]
    p = sample("rails", dict(levels=3), [dict(width_m=12, height_m=9)] * 2)
    describe(p, row["description"], row["width_m"], walls=walls)
    assert compile_wall(p["walls"][0], 3)["detail"][1] == 1
    assert compile_wall(p["walls"][1], 3)["detail"][1] == 0


def test_a_read_wall_draws_its_own_rhythm_and_ground_storey():
    from turku3d.facade_programs import compile_wall, describe, described_walls
    from turkutex.facade_archetypes import sample

    doc = reading()
    # The large wall reads doors; a well-seen street front of 3 bays has shops.
    doc["walls"][1] = {
        "id": "w01", "material": "wall", "observed": {"visible_share": 0.6}, "conf": {"*": 0.6},
        "frame": {"p0": [E + 12, N], "p1": [E + 12, N - 5], "base_h": 0, "height_m": 9},
        "bays": [{"u_m": 0.8}, {"u_m": 2.5}, {"u_m": 4.2}],
        "storeys": [{"kind": "ground", "height_m": 3.0, "row": ["shop", "shop", "door"]},
                    {"kind": "upper", "height_m": 2.8, "count": 2, "window": "win"}],
    }
    row, _ = conv.convert(doc)
    assert row["description"]["ground_floor"] == "doors" and row["description"]["window_columns"] == 4
    side = row["walls"][1]
    assert (side["ground_floor"], side["window_columns"], side["width_m"]) == ("shops", 3, 5.0)
    walls = described_walls(np.concatenate([FRONT, SIDE]), row)
    p = sample("own", dict(levels=3), [dict(width_m=12, height_m=9), dict(width_m=5, height_m=9)])
    describe(p, row["description"], row["width_m"], walls=walls)
    front, end = p["walls"]
    assert (front["bays"]["count"], front["base"]["variant"]["value"]) == (4, "doors")
    assert (end["bays"]["count"], end["base"]["variant"]["value"]) == (3, "shops")
    # One floor grid: the shop storey keeps the building's ground split.
    assert end["split"]["base_height_m"] == front["split"]["base_height_m"]
    assert compile_wall(end, 3)["module"] % 3 == 2
    # A wall seen too little keeps the building's reference rhythm.
    doc["walls"][1]["observed"]["visible_share"] = 0.1
    row, _ = conv.convert(doc)
    assert "walls" not in row or all("window_columns" not in w for w in row["walls"][1:])


def test_shop_glass_read_as_windows_opens_shops_but_dwelling_windows_do_not():
    doc = reading()
    doc["opening_types"]["glass"] = {"kind": "window", "w_m": 2.3, "h_m": 1.9, "glass": {"interior": "dark"}}
    doc["walls"][0]["storeys"][0] = {"kind": "ground", "height_m": 3.0, "sill_m": 0.2,
                                     "row": ["glass", "door", "glass", "glass"]}
    assert conv.convert(doc)[0]["description"]["ground_floor"] == "shops"
    doc["opening_types"]["glass"]["glass"]["interior"] = "dwelling"
    assert conv.convert(doc)[0]["description"]["ground_floor"] == "doors"
    doc["opening_types"]["glass"]["glass"]["interior"] = "dark"
    doc["walls"][0]["storeys"][0]["sill_m"] = 0.9
    assert conv.convert(doc)[0]["description"]["ground_floor"] == "doors"
    # A few shop windows among a house's doors and windows keep its ground storey punched (Puutarhakatu 32).
    doc["walls"][0]["storeys"][0].update(sill_m=0.2, row=["glass", "door", "win", "door", "win", "door"])
    assert conv.convert(doc)[0]["description"]["ground_floor"] == "doors"


def test_dark_trim_read_on_a_light_rendered_wall_draws_light():
    doc = reading()
    doc["materials"]["render"] = {"family": "plaster", "colour": "#dfa996", "conf": {"*": 0.8}}
    doc["walls"][0]["material"] = doc["walls"][1]["material"] = "render"
    doc["walls"][0]["verticals"] = [{"kind": "lesene", "colour": "#3b3e40"}] * 4
    d = conv.convert(doc)[0]["description"]
    assert d["material"] == "plaster" and d["trim_colour"] == conv.TRIM["plaster"]  # Puolalanpuisto 2a
    doc["walls"][0]["verticals"] = [{"kind": "lesene", "colour": "#f4f1ea"}] * 4
    assert conv.convert(doc)[0]["description"]["trim_colour"] == "#f4f1ea"  # light read trim stays
    assert not conv.dark_trim("#3b3e40", "#9a8f87", "plaster")  # a dark wall keeps its dark bands
    assert not conv.dark_trim("#3b3e40", "#dfa996", "brick")


def test_grouped_windows_draw_at_their_group_width_with_their_lights():
    from turku3d.facade_programs import compile_wall, describe, described_walls
    from turkutex.facade_archetypes import sample

    doc = reading()
    doc["opening_types"]["win"]["multiple"] = {"count": 3, "gap_m": 0.3}
    row, _ = conv.convert(doc)
    # 3 x 1.2 m lights and two 0.3 m piers in each 3 m bay.
    assert row["description"]["window_width"] == 0.9 and row["lights"] == 3
    assert row["walls"][0]["lights"] == 3
    p = sample("groups", dict(levels=3), [dict(width_m=12, height_m=9)])
    describe(p, row["description"], row["width_m"], walls=described_walls(FRONT, row), lights=row["lights"])
    assert compile_wall(p["walls"][0], 3)["glazing"][0] == 3


def test_a_reading_material_outranks_register_cladding_but_not_proven_descriptions():
    from turku3d.facade_inference import BUILDING_READING, building_material

    d = conv.convert(reading())[0]["description"]
    found = building_material(dict(material_class="render"), {}, None, d, reading=True)
    assert (found["value"], found["source"]) == ("brick", BUILDING_READING)
    # A proven AI description keeps ranking below the register's cladding.
    assert building_material(dict(material_class="render"), {}, None, d)["value"] == "plaster"
    # Reviewed walls keep ranking above a reading.
    observed = dict(walls=[dict(observation=dict(needs_review=False),
                                wall=dict(material=dict(value="granite", support=0.9)))])
    assert building_material({}, {}, observed, d, reading=True)["value"] == "stone"


def test_migration_selects_only_tiles_holding_a_read_building(monkeypatch):
    from turku3d.migrations import facade_readings

    monkeypatch.setattr(fr, "keys", lambda: frozenset({"b1"}))
    assert facade_readings.affected(dict(building_keys=["x", "gis-b1"]))
    assert not facade_readings.affected(dict(building_keys=["x"]))
    assert not facade_readings.affected({})
