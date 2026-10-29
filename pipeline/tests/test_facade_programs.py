"""Authority, stable sampling, measured scope and tile-local invalidation contracts."""

import copy
import json

import numpy as np
import pytest
from turku3d import config as C
from turku3d.facade_assets import opaque
from turku3d.facade_kit import parameter_hash
from turku3d.facade_program_geometry import wall_parts
from turku3d.facade_programs import compile_wall, matching_wall
from turkutex.facade_archetypes import ARCHETYPES, archetype, sample


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
    patches = [row for row in parts if row[2]["detail"][3] > 0 and row[2]["detail"][3] % 4 == 1]
    assert len(patches) == 2
    assert [row[2]["origin"] for row in patches] == [[1, 3], [8, 3]]
    assert [row[2]["size"] for row in patches] == [[2, 2], [3, 2]]
    blank = [row for row in parts if row[2]["module"] >= 42]
    # The blank background is cut around the two openings, never drawn behind them.
    import shapely

    assert max(row[0][:, 1].max() for row in blank) == 6
    assert sum(shapely.Polygon(row[1]).area for row in blank) == pytest.approx(23.3 * 4 - 10)
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


def test_fallback_periods_are_seeded_and_explicit_evidence_wins():
    from turkutex.facade_archetypes import inferred_period, recipe

    attrs = dict(district="853006", levels=4)
    names = {recipe(str(i), attrs)["archetype"] for i in range(100)}
    assert "jugend" in names and "sixties_slab" in names
    assert "contemporary_housing" not in names
    neighbours = [("old", 1890, 0), ("new", 2018, 70)]
    a = dict(attrs, register_id="stable", period_neighbours=neighbours)
    assert inferred_period(a) == inferred_period(dict(a, period_neighbours=neighbours[::-1]))
    assert archetype(a) == "neo_renaissance"
    assert archetype(dict(a, construction_year=2018)) == "contemporary_housing"
    assert archetype(dict(a, material_class="wood")) == "timber_traditional"
    assert archetype(dict(a, use="warehouse")) == "warehouse"
    assert archetype(dict(start_date="c. 1905")) == "jugend"
    assert inferred_period(dict(period_neighbours=[("far", 1890, 81)])) == (None, "plain")
    # A protected residential building is period evidence, not automatically a civic hall.
    assert archetype(dict(heritage="2", register_id="home")) in {
        "empire",
        "neo_renaissance",
        "jugend",
        "nordic_classicism",
    }


def test_period_evidence_uses_neighbours_across_tile_edge_without_mutating_dates():
    import shapely
    from turku3d.coarse.inputs import Building
    from turku3d.coarse.register import enrich, period_evidence

    b = Building("home", shapely.box(240, 0, 250, 20), 0, 12, "facade_plaster")
    b.attributes = dict(construction_year=1930, levels=4, material_class="brick")
    enrich(
        b,
        {},
        dict(
            buildings=[(b.footprint, dict(construction_year=2010, levels=8, material_class="wood"))]
        ),
    )
    assert b.attributes["construction_year"] == 1930
    assert b.attributes["levels"] == 4
    assert b.attributes["material_class"] == "brick"
    period_evidence(b, [])
    assert "period_neighbours" not in b.attributes
    b.attributes.pop("construction_year")
    period_evidence(
        b,
        [
            ("home", 1930, b.footprint),
            ("attached", 1890, shapely.box(250, 0, 260, 20)),
            ("remote", 2010, shapely.box(400, 0, 410, 20)),
        ],
    )
    assert b.attributes["period_neighbours"] == [("attached", 1890, 0.0)]
    assert "construction_year" not in b.attributes


def test_cached_dated_footprints_and_osm_tags_feed_fallback(tmp_path):
    import pyarrow as pa
    import pyarrow.parquet as pq
    import shapely
    from turku3d.coarse.inputs import Building, _prepare_one
    from turku3d.coarse.register import enrich, period_evidence

    lod2 = tmp_path / "bldg.parquet"
    pq.write_table(
        pa.Table.from_pylist(
            [
                dict(
                    feature_key="near",
                    pysyvarakennustunnus="dated",
                    yearOfConstruction="1890",
                    geometry_local=shapely.to_wkb(shapely.box(250, 0, 260, 20)),
                ),
            ]
        ),
        lod2,
    )
    osm = tmp_path / "osm.parquet"
    pq.write_table(
        pa.Table.from_pylist(
            [
                dict(
                    tags=json.dumps(
                        dict(
                            building="apartments",
                            start_date="1905",
                            heritage="2",
                            **{"building:cladding": "brick", "roof:shape": "mansard"},
                        )
                    ),
                    geometry_local=shapely.to_wkb(shapely.box(240, 0, 250, 20)),
                ),
                dict(
                    tags=json.dumps(dict(entrance="yes", amenity="cafe")),
                    geometry_local=shapely.to_wkb(shapely.Point(240, 5)),
                ),
                dict(
                    tags=json.dumps(dict(entrance="yes", shop="bakery")),
                    geometry_local=shapely.to_wkb(shapely.Point(240, 10)),
                ),
            ]
        ),
        osm,
    )
    evidence = _prepare_one(C.VisualTile(0, 0, 0), "appearance", [osm, lod2])
    b = Building("home", shapely.box(240, 0, 250, 20), 0, 12, "facade_plaster")
    enrich(b, {}, evidence)
    assert b.attributes["construction_year"] == 1905
    assert b.attributes["material_class"] == "brick"
    assert b.attributes["heritage"] == "2"
    assert b.attributes["roof_shape"] == "mansard"
    assert b.attributes["ground_floor"] == "shops"
    assert archetype(b.attributes) == "jugend"
    b.attributes.pop("construction_year")
    b.attributes.pop("era")
    b.attributes.pop("heritage")
    period_evidence(b, evidence["dated_footprints"])
    assert b.attributes["period_neighbours"] == [("dated", 1890, 0.0)]
    assert archetype(b.attributes) == "neo_renaissance"
    # Missing OSM still allows register/LOD2 neighbourhood inference.
    without_osm = _prepare_one(C.VisualTile(0, 0, 0), "appearance", [None, lod2])
    assert without_osm["dated_footprints"][0][:2] == ("dated", 1890)


def test_reviewed_partial_wall_overrides_stored_map_and_description_only_on_its_wall(monkeypatch):
    from types import SimpleNamespace

    import shapely
    from turku3d import facade_programs as fp
    from turku3d.coarse import stored_facades as sf
    from turku3d.facade_kit import renderer

    reviewed = program()["walls"][0]
    reviewed.update(
        width_m=8,
        height_m=12,
        geometry=dict(
            p0=[C.ORIGIN_E, C.ORIGIN_N],
            p1=[C.ORIGIN_E + 8, C.ORIGIN_N],
            base_h=3,
            normal=[0, 0, -1],
        ),
        observation=dict(needs_review=False, scope="full-wall"),
    )
    reviewed["bays"]["support"] = 0.9
    reviewed["windows"].update(
        rectangles_m=[[1, 1, 2, 2]], head_shape={"value": "round-arch", "support": 0.9}
    )
    reviewed["doors"].update(
        candidates=[{"rect_m": [4, 0, 1, 3]}],
        head_shape={"value": "segmental-arch", "support": 0.9},
    )
    reviewed["split"]["base_height_m"] = {"value": 2, "support": 0.9}
    monkeypatch.setattr(fp, "measured", lambda key: dict(needs_review=False, walls=[reviewed]))
    description = renderer().register_description("stable", "stable", {}, 10, 12)
    description["wall_colour"] = "#123456"
    monkeypatch.setattr(fp, "read_description", lambda key: description)
    facades = [
        dict(
            coordinates=[[C.ORIGIN_E, C.ORIGIN_N - z], [C.ORIGIN_E + 10, C.ORIGIN_N - z]],
            base_height=3,
            metadata=dict(facade_index=i),
        )
        for i, z in enumerate((0, 2))
    ]
    from turku3d import facade_assets

    monkeypatch.setattr(facade_assets, "building", lambda key: dict(facades=facades))
    vertices = np.array([[0, 3, 0], [10, 3, 0], [10, 15, 0], [0, 15, 0]], float)
    b = SimpleNamespace(
        key="part",
        facade_key="stable",
        base=3,
        top=15,
        footprint=shapely.box(0, 0, 10, 2),
        attributes={"levels": 4},
    )
    meshes = sf.wall_meshes(b, np.concatenate([vertices, vertices + [0, 0, 2]]), np.zeros(3))
    diagnostic = meshes[0].extras["facade_program"]
    assert diagnostic["storeys"]["value"] == 4
    assert diagnostic["source"] == "reviewed-overlay"
    assert [w["paint_source"] for w in diagnostic["walls"]] == [
        "reviewed-program",
        "existing-description",
    ]
    stored = [m for m in meshes if m.extras["material_key"] == "facade_stored"]
    assert stored == []  # Unobserved strips also use generated material and layout.
    from turku3d.wall_surfaces import validate_meshes

    assert validate_meshes(meshes, "fixture")["violations"] == 0
    wall = diagnostic["walls"][0]
    assert wall["wall_id"] == "stable:0"
    assert wall["wall"]["colour"]["source"] == "existing description"
    assert wall["wall"]["colour"]["value"] == "#123456"
    assert compile_wall(wall, 4)["ground"] == 2
    instructions = meshes[-1].extras["facade_kit_walls"]
    assert [p["detail"][3] for p in instructions if p["detail"][3] > 0] == [9, 6]
    # 80% map coverage is deliberately unnecessary for a clipped reviewed span.
    reviewed["geometry"]["p1"][0] = C.ORIGIN_E + 4
    assert matching_wall(vertices, dict(needs_review=False, walls=[reviewed])) is reviewed


def test_pilot_bank_has_three_observed_rows_and_arches_without_changing_register():
    from turku3d.facade_programs import measured

    bank = measured("1032959104")
    wall = bank["walls"][0]
    assert len(wall["windows"]["rectangles_m"]) == 14
    assert wall["windows"]["head_shapes"].count("round-arch") == 7
    assert wall["windows"]["head_shapes"].count("flat") == 7
    assert len(wall["base"]["openings_m"]) == 6
    assert wall["base"]["head_shape"]["value"] == "round-arch"
    assert wall["doors"]["head_shape"]["value"] == "round-arch"


def test_arch_opening_split_across_fragments_keeps_whole_opening_origin():
    wall = program()["walls"][0]
    wall.update(
        measured_geometry=dict(
            p0=[C.ORIGIN_E, C.ORIGIN_N], p1=[C.ORIGIN_E + 23.3, C.ORIGIN_N], base_h=2
        ),
        measured_size=[23.3, 10],
    )
    wall["windows"].update(rectangles_m=[[3, 1, 4, 3]], head_shapes=["round-arch"])
    vertices = np.array([[5, 2, 0], [10, 2, 0], [10, 12, 0], [5, 12, 0]], float)
    parts = list(wall_parts(vertices, wall, compile_wall(wall, 4)))
    patch = next(p for p in parts if p[2]["detail"][3] == 9)
    assert patch[2]["origin"] == [-2, 3]
    assert patch[2]["size"] == [4, 3]
    assert patch[0][:, 0].min() == 5
    assert patch[0][:, 0].max() == 7


def test_reviewed_paint_stays_inside_sloping_overlap_remainder():
    wall = program()["walls"][0]
    wall.update(
        measured_geometry=dict(
            p0=[C.ORIGIN_E, C.ORIGIN_N], p1=[C.ORIGIN_E + 23.3, C.ORIGIN_N], base_h=2
        ),
        measured_size=[23.3, 12],
    )
    vertices = np.array([[0, 4, 0], [23.3, 7, 0], [23.3, 13.5, 0], [0, 13.5, 0]])
    style = compile_wall(wall, 4)
    for points, uv, _ in wall_parts(vertices, wall, style):
        assert np.all(points[:, 1] >= 4 + points[:, 0] / 23.3 * 3 - 1e-9)
        np.testing.assert_allclose(uv[:, 0], points[:, 0])
