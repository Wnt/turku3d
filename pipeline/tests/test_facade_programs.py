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
    assert len(ARCHETYPES) == 22


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



def test_reviewed_pane_rhythm_does_not_stretch_onto_broad_shop_glazing():
    wall = program()["walls"][0]
    wall.update(measured_geometry=dict(p0=[C.ORIGIN_E, C.ORIGIN_N],
        p1=[C.ORIGIN_E + 23.3, C.ORIGIN_N], base_h=2), measured_size=[23.3, 4])
    wall["windows"]["rectangles_m"] = [[1, 1, 2, 2], [8, 1, 10, 2]]
    wall["windows"]["opening_roles"] = ["window", "base"]
    vertices = np.array([[0, 2, 0], [23.3, 2, 0], [23.3, 13.5, 0], [0, 13.5, 0]])
    style = compile_wall(wall, 4)
    style.update(glazing=[2, 1, 0, 0])
    patches = [r[2] for r in wall_parts(vertices, wall, style)
               if r[2]["detail"][3] > 0 and r[2]["detail"][3] % 4 == 1]
    assert [p["glazing"] for p in patches] == [[2, 1, 0, 0], [0, 0, 0, -1]]
    assert style["glazing"] == [2, 1, 0, 0]


def test_reversed_clipped_ribbon_keeps_original_pane_pitch_and_mirrors_split():
    wall = program()["walls"][0]
    wall.update(measured_geometry=dict(p0=[C.ORIGIN_E, C.ORIGIN_N],
        p1=[C.ORIGIN_E + 23.3, C.ORIGIN_N], base_h=2), measured_size=[23.3, 8])
    wall["windows"]["rectangles_m"] = [[1, 1, 20, 2], [7, 4, 8, 2]]
    vertices = np.array([[23.3, 2, 0], [10, 2, 0], [10, 10, 0], [23.3, 10, 0]])
    style = compile_wall(wall, 4)
    style.update(glazing=[5, 1, .7, 0], glazing_span=[1, 20])
    patches = [r[2] for r in wall_parts(vertices, wall, style)
               if r[2]["detail"][3] > 0 and r[2]["detail"][3] % 4 == 1]
    assert [p["glazing"][0] for p in patches] == [-4, -4]
    assert [p["glazing"][2] for p in patches] == pytest.approx([.3, .3])
    assert [p["glazing_phase"] for p in patches] == [-5, -3.5]
    assert [p["size"][0] for p in patches] == [20, 8]
    assert style["glazing"] == [5, 1, .7, 0]


def test_excluded_and_unobserved_regions_do_not_inherit_reviewed_upper_panes():
    wall = program()["walls"][0]
    wall.update(measured_geometry=dict(p0=[C.ORIGIN_E, C.ORIGIN_N],
        p1=[C.ORIGIN_E + 23.3, C.ORIGIN_N], base_h=2), measured_size=[23.3, 8])
    wall["windows"]["rectangles_m"] = [[1, 4, 2, 2]]
    wall["observation"].update(scope="partial", excluded_rectangles_m=[[0, 0, 23.3, 3]])
    vertices = np.array([[0, 2, 0], [23.3, 2, 0], [23.3, 14, 0], [0, 14, 0]])
    style = compile_wall(wall, 4)
    style.update(glazing=[2, 1, .3, 0], glazing_phase=2, glazing_span=[0, 12], frame="#102030")
    fallback = [r[2] for r in wall_parts(vertices, wall, style) if r[2].get("unreviewed")]
    assert len(fallback) >= 2  # excluded lower shop strip and outside upper extent
    assert all(p["glazing"] == [0, 0, 0, -1] and p["glazing_phase"] == 0
               and p["glazing_span"] is None and p["frame"] == style["trim"] for p in fallback)
    assert style["glazing"] == [2, 1, .3, 0] and style["frame"] == "#102030"

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

    # Generated cornice relief may remain above the reviewed shop strip; it
    # cannot turn the unobserved upper facade into an opaque wall background.
    assert all(row[2].get("relief_m") for row in blank if row[0][:, 1].min() >= 6)
    reviewed_blank = [row for row in blank if row[0][:, 1].max() <= 6]
    assert max(row[0][:, 1].max() for row in reviewed_blank) == 6
    assert sum(shapely.Polygon(row[1]).area for row in reviewed_blank) == pytest.approx(23.3 * 4 - 10)
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


def test_massive_pre_industrial_masonry_is_a_measured_class_without_shopfronts():
    from turkutex.facade_archetypes import MASONRY, MEASURED_PIGMENT, early_year

    castle = dict(construction_year=1280, historic="yes", osm_use="yes", use="2120",
                  ground_floor="shops", levels=4, footprint_m2=4193, register_id="fort")
    assert early_year("c. 1280") == 1280 and early_year(1700) is None
    assert archetype(castle) == MASONRY
    assert archetype(dict(osm_use="castle", footprint_m2=900)) == MASONRY
    # Small dated sheds, timber and light cladding keep their ordinary rules.
    assert archetype(dict(castle, footprint_m2=47)) != MASONRY
    assert archetype(dict(castle, material_class="wood")) == "timber_traditional"
    assert archetype(dict(construction_year=1733, footprint_m2=443)) == "empire"
    p = sample("fort", castle, [dict(wall_id="w", width_m=40.0, height_m=21.0)])
    wall = p["walls"][0]
    assert wall["base"]["variant"]["value"] != "shops"
    assert wall["wall"]["colour"]["source"] == MEASURED_PIGMENT
    assert wall["doors"]["head_shape"]["value"] == "round-arch"
    style = compile_wall(wall, p["storeys"]["value"])
    assert style["module"] in (0, 1) and style["bands"] == [0, 0, 0, 0]
    assert style["glazing"][:2] == [3, 3]
    assert style["frame"] != style["trim"] and max(int(style["frame"][i:i + 2], 16) for i in (1, 3, 5)) < 120
    assert min(int(style["wall"][i:i + 2], 16) for i in (1, 3, 5)) > 200


def test_modern_civic_buildings_never_sample_arched_openings():
    for i in range(40):
        attrs = dict(construction_year=1965, use="2120", material_class="concrete", levels=3)
        p = sample(f"museum-{i}", attrs, [dict(wall_id="w", width_m=30.0, height_m=12.0)])
        assert p["archetype"] == "civic"
        w = p["walls"][0]
        assert {w[s]["head_shape"]["value"] for s in ("windows", "base", "doors")} == {"flat"}
    old = {sample(f"hall-{i}", dict(construction_year=1905, use="2120"),
                  [dict(wall_id="w", width_m=30.0, height_m=12.0)])["walls"][0]["windows"]["head_shape"]["value"]
           for i in range(40)}
    assert "round-arch" in old


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
    description["wall_visible"] = True  # This fixture supplies a visible, authoritative wall observation.
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


def test_classical_order_is_chosen_from_evidence_or_civic_historicist_data():
    from turkutex.facade_archetypes import classical_order

    civic = dict(construction_year=1903, material_class="brick", use="2120", footprint_m2=1200)
    assert classical_order(civic)
    assert classical_order(dict(civic, use="1000", monumental=True))
    assert not classical_order(dict(civic, use="1000"))  # Ordinary historicist housing.
    assert not classical_order(dict(civic, construction_year=1958))
    assert not classical_order(dict(civic, material_class="wood"))
    assert not classical_order(dict(civic, construction_year=1280))  # Massive masonry.
    housing = dict(civic, use="1000", construction_year=1888)
    assert classical_order(housing, "giant pale pilasters between red brick bays")
    assert not classical_order(dict(housing, material_class="glass"), "pilasters")


def test_classical_order_keeps_supported_readings_and_existing_pilaster_details():
    from turku3d.facade_programs import classical
    from turkutex.facade_archetypes import ORDER, sample
    from turkutex.facade_program import value

    attrs = dict(levels=2, construction_year=1903, material_class="brick", use="2120",
                 footprint_m2=1200)
    geometry = [dict(wall_id=str(i), width_m=12, height_m=12) for i in range(2)]
    program = sample("keep", attrs, geometry)
    first, second = program["walls"]
    first["windows"]["frame_colour"] = value("#123456", "photo", 0.9)
    second["appearance_details"] = dict(pilasters=value(True, "historicist style prior", 0.4))
    classical(program, attrs, "")
    assert program["classical_order"]
    assert first["windows"]["frame_colour"]["value"] == "#123456"
    assert first["windows"]["divisions"]["value"] == ORDER["panes"]
    assert first["corners"]["quoins"]["value"] == 0
    assert "order" not in second  # Its reviewed pilasters are painted elsewhere.


def test_public_monolith_needs_photographed_blank_walls_and_keeps_own_evidence(monkeypatch):
    import shapely
    from types import SimpleNamespace

    from turku3d import facade_learning, facade_monolith
    from turku3d import facade_programs as fp
    from turkutex.facade_archetypes import MONOLITH

    library = dict(construction_year=2006, use="2120", osm_use="civic", entrance_use="library",
                   levels=4, levels_source="register", material_class="render", footprint_m2=2774)
    assert archetype(library) == "civic"  # register use alone never blanks a building
    assert archetype(dict(library, monolith_blank=True)) == MONOLITH
    assert archetype(dict(library, monolith_blank=True, construction_year=1912)) != MONOLITH
    assert archetype(dict(library, monolith_blank=True, footprint_m2=300)) != MONOLITH
    assert archetype(dict(library, monolith_blank=True, use="1000", osm_use="apartments",
                          entrance_use=None)) != MONOLITH
    # Evidence: a reviewed mostly-blank reading, and panel-joint text on any reviewed wall.
    monkeypatch.setattr(facade_monolith, "own_descriptions", lambda key: [])
    blank = dict(observation=dict(needs_review=False), structure=dict(facade_observations=dict(
        sections=[dict(position="upper majority", kind="blank wall", material="pale smooth render"),
                  dict(position="lower left", kind="localized retail glazing strip")])))
    panels = dict(observation=dict(needs_review=False), structure=dict(
        floors=[dict(bays=["blank"])], features=[dict(kind="large rectangular panel grid")]))
    assert facade_monolith.evidence("k", dict(walls=[blank])) == dict(blank=True, panels=False)
    assert facade_monolith.evidence("k", dict(walls=[panels])) == dict(blank=True, panels=True)
    fragment = copy.deepcopy(blank)
    fragment["structure"]["facade_observations"]["sections"][0]["position"] = "lower right"
    assert facade_monolith.evidence("k", dict(walls=[fragment]))["blank"] is False
    # Resolve: the wall with its own curtain-wall description glazes, the unobserved wall is blank.
    curtain = dict(facade_id="f1", wall_visible=True, problem="", material="glass", wall_colour="#88abb3",
                   trim_colour="#adb5af", storeys=3, window_columns=11, window_width=0.96,
                   window_height=0.93, ground_floor="plain", ground_openings=0, roof_line="none",
                   plinth=True, features=[])
    facade = dict(coordinates=[[40 + C.ORIGIN_E, C.ORIGIN_N - 0], [40 + C.ORIGIN_E, C.ORIGIN_N - 30]],
                  normal=None, base_height=0, metadata=dict(facade_id="f1", width_m=30))
    monkeypatch.setattr(facade_monolith, "own_descriptions", lambda key: [(facade, curtain)])
    monkeypatch.setattr(facade_monolith, "evidence", lambda key, observed: dict(blank=True, panels=False))
    monkeypatch.setattr(fp, "measured", lambda _: None)
    monkeypatch.setattr(fp, "read_description", lambda _: None)
    monkeypatch.setattr(facade_learning, "apply", lambda p: pytest.fail("donor layouts on a monolith"))
    b = SimpleNamespace(key="lib", facade_key="lib", base=0, top=18, footprint=shapely.box(0, 0, 40, 30),
                        attributes=library)
    vertices = np.array([[0, 0, 0], [40, 0, 0], [40, 18, 0], [0, 18, 0],
                         [40, 0, 0], [40, 0, 30], [40, 18, 30], [40, 18, 0]], float)
    p = fp.resolve(b, vertices, {})
    assert p["archetype"] == MONOLITH and p["monolith"]["walls"]["blank"] == 1
    plain, glazed = [compile_wall(w, 4) for w in p["walls"]]
    assert plain["module"] == 42 and plain["bands"] == [0, 0, 0, 0]
    assert plain["base"] != plain["wall"]  # granite base course under the render
    assert glazed["module"] // 6 == 5 and glazed["opening"][0] >= 0.97
    assert glazed["opening"][1] + glazed["opening"][2] >= 0.98 and abs(glazed["bay"] - 2.14) < 0.05
    # A panel-clad blank wall carries its joint grid in the opening slot.
    p["walls"][0]["panel_grid"] = dict(value=[2.23, 1.22, 0.03], source="test", support=0.6)
    assert compile_wall(p["walls"][0], 4)["opening"] == [2.23, 1.22, 0.03, -1.0]


def test_monolith_reviewed_glazing_strips_become_ribbons_and_panels_keep_joints():
    from turku3d import facade_monolith
    from turkutex.facade_program import value

    m = lambda x, s: value(x, s, 0.6)  # noqa: E731
    reading = dict(structure=dict(facade_observations=dict(sections=[
        dict(position="upper majority", kind="blank grey rectangular panel cladding"),
        dict(position="lower", kind="narrow recessed dark glazing strip behind pale supports"),
        dict(position="foreground", kind="concrete boundary wall", belongs_to_facade=False),
        dict(position="lower right", kind="small service door")])))
    wall = dict(render_sections=[dict(index=0, bounds=[0, 1 / 3, 1, 2 / 3], kind="blank")])
    facade_monolith.reviewed_wall(wall, reading, 2, True, m)
    ribbon = wall["render_sections"][1]
    assert ribbon["rhythm"] == "ribbon" and ribbon["count"] is None and ribbon["kind"] == "window"
    assert ribbon["bounds"] == [0.0, 0.0, 1.0, 1 / 3]  # narrow strip at the base, full width
    assert len(wall["render_sections"]) == 2  # doors and foreground structures are not glazing
    assert wall["panel_grid"]["value"] == [2.23, 1.22, 0.03]
    # A size measured on this wall's own photograph wins over the class prior.
    reading["structure"]["facade_observations"]["sections"][0]["features"] = [
        "panel grid 1.50 x 1.21 m with joints about 3 cm"]
    facade_monolith.reviewed_wall(wall, reading, 2, True, m)
    assert wall["panel_grid"]["value"] == [1.5, 1.21, 0.03]


def test_monolith_channel_glass_reading_becomes_tinted_profiled_cladding():
    from turku3d import facade_monolith
    from turkutex.facade_archetypes import MONOLITH_CHANNEL_GLASS
    from turkutex.facade_program import value

    m = lambda x, s: value(x, s, 0.6)  # noqa: E731
    reading = dict(structure=dict(facade_observations=dict(sections=[
        dict(position="left half", kind="blank wall", material="pale smooth render"),
        dict(position="top left edge", kind="translucent channel glass panel", material="channel glass")])))
    wall = dict(render_sections=[dict(index=0, bounds=[0, 0, .5, 1], kind="blank", material="render"),
                                 dict(index=1, bounds=[0, .75, .12, .25], kind=None, material="glass")])
    facade_monolith.reviewed_wall(wall, reading, 4, False, m)
    channel = wall["render_sections"][1]
    assert channel["kind"] == "blank" and channel["colour"] == MONOLITH_CHANNEL_GLASS["colour"]
    assert channel["panel_grid"][0] == 0.33 and "panel_grid" not in wall  # render volume has no joints
    assert wall["render_sections"][0].get("colour") is None


def test_classical_order_skips_walls_off_the_footprint_outline():
    from turku3d.facade_programs import classical
    from turkutex.facade_archetypes import sample

    attrs = dict(levels=2, construction_year=1903, material_class="brick", use="2120",
                 footprint_m2=1200)
    geometry = [dict(wall_id="outer", width_m=30, height_m=14.5, outline=True),
                dict(wall_id="ring", width_m=29.4, height_m=15.0, outline=False)]
    program = sample("rings", attrs, geometry)
    classical(program, attrs, "")
    outer, ring = program["walls"]
    assert "order" in outer and "order" not in ring
