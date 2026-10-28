"""Pure facade parameter mapping and opaque store keys; no tiles, services or encoders."""

from turku3d.facade_kit import parameters, store_path


def test_parameters_keep_storeys_and_material():
    p = parameters("example", 12.8, 18, {"levels": 4, "material_class": "brick"})
    assert 6 <= p["module"] < 12
    assert p["storey"] == 3.2
    assert p["ground"] == p["upper"] == p["storey"]
    assert p["bay"] == 3
    assert p == parameters("example", 12.8, 18, {"levels": 4, "material_class": "brick"})


def test_parameter_key_is_stable_and_cannot_escape(tmp_path):
    path = store_path("../../unsafe/id", tmp_path)
    assert path.parent == tmp_path
    assert path == store_path("../../unsafe/id", tmp_path)
    assert path != store_path("unsafe/id", tmp_path)


def test_shop_ground_floor_is_taller_without_repeating_upper_floor_at_roof():
    p = parameters("shop", 12.8, 18, {"levels": 4, "ground_floor": "shops"})
    assert p["ground"] == 4
    assert p["upper"] < p["ground"]
    assert abs(p["ground"] + 3 * p["upper"] - 12.8) < 1e-8


def test_named_yellow_uses_material_era_palette_while_measured_hex_is_preserved():
    attrs = {"wall_colour": "#b9b946", "wall_colour_name": "yellow"}
    historic = parameters(
        "historic", 9, 18, {**attrs, "material_class": "render", "era": "pre-1850"}
    )
    modern = parameters("modern", 9, 18, {**attrs, "material_class": "render", "era": "1960+"})
    wood = parameters("wood", 6, 12, {**attrs, "material_class": "wood"})
    brick = parameters("brick", 9, 18, {**attrs, "material_class": "brick"})
    assert historic["wall"] == "#d8bf7a"
    assert modern["wall"] == "#d3c58e"
    assert wood["wall"] == "#d9c48e"
    assert brick["wall"] == "#c4a777"
    explicit = parameters("measured", 9, 18, {"wall_colour": "#b9b946", "material_class": "render"})
    assert explicit["wall"] == "#b9b946"


def test_osm_preserves_named_paint_family_without_relabelling_measured_hex(tmp_path):
    import json

    import pyarrow as pa
    import pyarrow.parquet as pq
    import shapely
    from turku3d import config as C
    from turku3d.coarse.register import read_osm

    path = tmp_path / "osm.parquet"
    pq.write_table(
        pa.Table.from_pylist(
            [
                {
                    "tags": json.dumps({"building": "yes", "building:colour": value}),
                    "geometry_local": shapely.to_wkb(shapely.box(10 + i * 20, 10, 20 + i * 20, 20)),
                }
                for i, value in enumerate(("yellow", "#b9b946"))
            ]
        ),
        path,
    )
    named, measured = [
        attrs for _, attrs in read_osm(C.VisualTile.parse("t0_0_0"), path)["buildings"]
    ]
    assert named["wall_colour_name"] == "yellow"
    assert measured["wall_colour"] == "#b9b946"
    assert "wall_colour_name" not in measured


def test_stored_ai_style_keeps_precedence_over_named_paint_fallback(tmp_path, monkeypatch):
    from turku3d.facade_kit import renderer, write_description

    monkeypatch.setenv("TURKU3D_WORK", str(tmp_path))
    d = renderer().register_description(
        "wall", "building", {"material_class": "wood", "wall_colour": "#123456"}, 18, 9
    )
    write_description("building", d, "test", "hash", width=18)
    p = parameters(
        "building",
        9,
        18,
        {"material_class": "render", "wall_colour_name": "yellow", "wall_colour": "#b9b946"},
    )
    assert p["wall"] == "#123456"
    assert p["trim"] == d["trim_colour"]
    assert 18 <= p["module"] < 24


def test_atlas_mask_separates_white_frames_wall_and_unpainted_mortar():
    import numpy as np
    from turku3d.facade_kit import module_maps, renderer

    d = renderer().register_description("test", "test", {}, 3, 6.4)
    d.update(material="brick", roof_line="none", plinth=False, features=[])
    image = module_maps(d, 6, size=(60, 128))["albedo"]
    alpha = image[:, :, 3]
    assert np.count_nonzero(alpha < 5) > 100  # painted brick
    assert np.count_nonzero(alpha > 250) > 10  # neutral white frames, tinted only once
    assert np.count_nonzero(abs(alpha.astype(float) - 127.5) < 2) > 100  # glass and mortar
    assert image[:, :, :3][alpha > 250].mean() > 230


def test_pigment_alpha_does_not_erase_wall_rgb_when_resizing():
    import numpy as np
    from turku3d.facade_kit import resize_map

    image = np.full((4, 4, 4), 255, np.uint8)
    image[:, :2, 3] = 0
    resized = resize_map(image, (7, 7))
    assert np.all(resized[:, :, :3] == 255)
    assert resized[:, 0, 3].max() == 0
    assert resized[:, -1, 3].min() == 255


def test_default_parameter_store_is_sandboxed(monkeypatch, tmp_path):
    monkeypatch.setenv("TURKU3D_DATA", str(tmp_path / "shared"))
    monkeypatch.setenv("TURKU3D_WORK", str(tmp_path / "own"))
    assert store_path("building").parent == tmp_path / "own" / "facade-params"


def test_only_own_building_parameters_invalidate(tmp_path):
    from turku3d.facade_kit import parameter_hash

    before = parameter_hash(["a"], tmp_path)
    store_path("b", tmp_path).write_text('{"changed":true}')
    assert parameter_hash(["a"], tmp_path) == before
    store_path("a", tmp_path).write_text('{"changed":true}')
    assert parameter_hash(["a"], tmp_path) != before


def test_kit_vertex_styles_batch_together():
    import subprocess

    from turku3d import config as C

    subprocess.run(
        ["node", "--test", str(C.REPO_ROOT / "pipeline/turku3d/package/facade_kit.test.mjs")],
        check=True,
    )


def test_appearance_merge_accepts_only_measured_appearance():
    from turku3d.coarse.facades import appearance_fields
    from turku3d.facade_kit import renderer

    register = renderer().register_description("b_1", "b", {"levels": 4}, 12, 14)
    fields = {
        "wall_colour": dict(state="measured", value="#aabbcc"),
        "trim_colour": dict(state="measured", value="#000000"),
        "material": dict(state="measured", value="brick"),
        "storeys": dict(state="measured", value=2),
        "window_columns": dict(state="measured", value=9),
    }
    result, sources = appearance_fields(register, fields)
    assert result == {**register, "wall_colour": "#aabbcc", "material": "brick"}
    assert sources["wall_colour"] == sources["material"] == "hybrid"
    assert sources["trim_colour"] == sources["storeys"] == sources["window_columns"] == "register"
    assert register["material"] != "brick"


def test_appearance_merge_missing_or_null_fields_keep_register():
    from turku3d.coarse.facades import appearance_fields

    register = dict(wall_colour="#ffffff", trim_colour="#eeeeee", material="plaster")
    result, sources = appearance_fields(register, {"material": dict(state="measured", value=None)})
    assert result == register
    assert set(sources.values()) == {"register"}


def test_hybrid_record_provenance_invalidation_and_astra_eligibility(tmp_path, monkeypatch):
    from turku3d import config as C
    from turku3d.coarse.facades import completed
    from turku3d.facade_kit import parameter_hash, read_description, renderer, write_description

    monkeypatch.setattr(C, "data_root", lambda: tmp_path)
    d = renderer().register_description("b_1", "b", {"levels": 4}, 12, 14)
    before = parameter_hash(["b"])
    write_description(
        "b", d, "test", "hash", engine="hybrid", field_sources={"storeys": "register"}
    )
    assert parameter_hash(["b"]) != before
    assert completed("b", "hybrid")
    assert not completed("b", "astra")
    assert read_description("b") == d
    write_description("b", d, "test", "hash")
    assert completed("b", "astra")
    assert completed("b", "hybrid")


def test_mapped_blank_sheds_and_shopfronts_select_distinct_modules():
    blank = parameters(
        "shed", 3, 10, {"material_class": "wood", "blank": True, "wall_colour": "#89765a"}
    )
    shop = parameters("retail", 9, 18, {"ground_floor": "shops", "levels": 3})
    assert blank["module"] == 45
    assert blank["wall"] == "#89765a"
    assert blank["bay"] == 3
    wide_blank = parameters("shed", 3, 45, {"material_class": "wood", "blank": True})
    assert wide_blank["bay"] == blank["bay"]
    assert shop["module"] % 3 == 2


def test_astra_selects_most_seen_usable_wall_then_widest_unseen():
    from turku3d import config as C
    from turku3d.coarse.facades import preferred_job, wall_distance

    jobs = [{"meta": {"facade_id": fid}} for fid in ("b_1", "b_2")]
    widths = {"b_1": 20, "b_2": 10}
    priority = {
        "buildings": [
            {
                "building_id": "b",
                "walls": [
                    {"facade_id": "b_2", "score": 4},
                    {"facade_id": "b_missing", "score": 9},
                ],
            }
        ]
    }
    assert preferred_job(jobs, widths, priority, "b") is jobs[1]
    assert preferred_job(jobs, widths, {}, "b") is jobs[0]
    nearby = {"b_1": (25.0, 0.0), "b_2": (2.0, 0.0)}
    assert preferred_job(jobs, widths, {"player_EN": [0.0, 0.0]}, "b", nearby) is jobs[1]
    wall = {"geometry": {"coordinates": [[C.ORIGIN_E, C.ORIGIN_N], [C.ORIGIN_E + 10, C.ORIGIN_N]]}}
    assert wall_distance(wall, (5, 3, 0, 0)) == 3
    assert wall_distance(wall, (20, 0, 0, 0)) == 10


def test_queue_rate_includes_idle_time_and_counts_only_actual_calls():
    from turku3d.coarse.facades import queue_metrics

    events = [
        dict(
            started_at=0, finished_at=10, state="upgraded", building="a", engine="hybrid", calls=0
        ),
        dict(started_at=20, finished_at=30, state="quota-paused", building="a", engine="astra"),
        dict(
            started_at=40, finished_at=50, state="upgraded", building="b", engine="hybrid", calls=0
        ),
        dict(
            started_at=60,
            finished_at=70,
            state="description-failed",
            building="b",
            engine="astra",
            calls=1,
        ),
    ]
    result = queue_metrics(events, 120)
    assert result["hybrid_buildings_per_minute"] == 1
    assert result["actual_calls_last_hour"] == 1
    assert queue_metrics(events, 4000)["hybrid_buildings"] == 0
