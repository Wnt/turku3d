import json

import numpy as np
from turku3d import config as C
from turku3d import facade_readings as fr
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
    row, why = fr.convert(reading())
    assert why == "converted"
    d = row["description"]
    assert renderer().validate(d, d["facade_id"]) == []
    assert d["material"] == "brick" and d["wall_colour"] == "#8a4b3a"  # the larger, better-seen wall
    assert d["trim_colour"] == "#ffffff"  # the main window's frame
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
    row, _ = fr.convert(doc)
    d = row["description"]
    assert d["material"] == "wood" and d["storeys"] == 0  # storeys stay the register's
    assert d["window_columns"] == round(fr.NOMINAL_WIDTH_M / 2.6) and d["ground_floor"] == "windows"
    assert "walls" not in row


def test_sparse_bays_keep_the_read_window_size_at_the_drawn_pitch():
    # Two bays over 12 m read as a 6 m rhythm; proven draws at most a 4.8 m bay, so the 1.2 m
    # window is a quarter of that bay, not a fifth of the source's.
    doc = reading()
    doc["walls"][0]["bays"] = [{"u_m": 3.0}, {"u_m": 9.0}]
    d = fr.convert(doc)[0]["description"]
    assert d["window_columns"] == 2 and d["window_width"] == 0.25


def test_unsure_or_unread_walls_are_not_converted():
    doc = reading()
    doc["materials"]["wall"]["conf"] = {"*": 0.2}
    doc["materials"]["base"]["conf"] = {"*": 0.2}
    assert fr.convert(doc)[0] is None
    doc = reading()
    doc["materials"] = {"roof": {"family": "felt", "colour": "#333333"}}
    assert fr.convert(doc) == (None, "no wall material read")


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
    report = fr.run_convert(readings, proven, fr.directory())
    assert (report["read"], report["converted"], report["skipped_proven_program"]) == (2, 1, 1)
    assert fr.keys() == {"b1"}
    assert read_description("gis-b1")["material"] == "brick"
    assert read_description("b2") is None
    retained = tmp_path / "textures/library/facade-params"
    retained.mkdir(parents=True)
    d = renderer().register_description("x", "b1", {}, 10, 9)
    (retained / opaque("b1")).write_text(json.dumps(dict(building_key="b1", description=d, width_m=10)))
    assert read_description("b1") == d


def test_reading_balconies_stay_on_their_own_wall_only():
    from turku3d.facade_programs import compile_wall, describe, described_balconies
    from turkutex.facade_archetypes import sample

    row, _ = fr.convert(reading())
    # Quad 0 is the read wall drawn from its far end; quad 1 faces another way.
    front = np.array([[12, 0, 0], [0, 0, 0], [0, 9, 0], [12, 9, 0]], float)
    side = np.array([[12, 0, 0], [12, 0, 5], [12, 9, 5], [12, 9, 0]], float)
    rails = described_balconies(np.concatenate([front, side]), row)
    assert list(rails) == [0] and rails[0][0] == [6.5, 3.3, 2.0, 1.1]
    p = sample("rails", dict(levels=3), [dict(width_m=12, height_m=9)] * 2)
    describe(p, row["description"], row["width_m"], balconies=rails)
    assert compile_wall(p["walls"][0], 3)["detail"][1] == 1
    assert compile_wall(p["walls"][1], 3)["detail"][1] == 0


def test_migration_selects_only_tiles_holding_a_read_building(monkeypatch):
    from turku3d.migrations import facade_readings

    monkeypatch.setattr(fr, "keys", lambda: frozenset({"b1"}))
    assert facade_readings.affected(dict(building_keys=["x", "gis-b1"]))
    assert not facade_readings.affected(dict(building_keys=["x"]))
    assert not facade_readings.affected({})
