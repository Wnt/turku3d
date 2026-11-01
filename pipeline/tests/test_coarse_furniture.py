"""G1 street furniture: register-first sources, placement, light records and static collision."""

import json
import math
from collections import Counter

import pandas as pd
import pytest
import shapely
from shapely.geometry import LineString, Point, box
from turku3d import config as C
from turku3d.props import Prop, deduplicate_lamps, off_carriageway, osm_props
from turku3d.props.lights import (
    CATEGORIES,
    POST_SHAPE,
    STREET_FLAG,
    decode,
    encode,
    post_light,
    street_edges,
    wall_lights,
)
from turku3d.roads import CAR, CLASS_SHIFT, FOOT

TILE = C.VisualTile(0, 0, 0)


def osm(rows):
    return pd.DataFrame(
        [
            {"osm_id": i, "tags": json.dumps(tags), "geometry_local": geometry}
            for i, (tags, geometry) in enumerate(rows, 1)
        ]
    )


def graph(edges):
    """roads.json of straight two-node edges: [(x0, z0, x1, z1, flags)]."""
    nodes, rows = [], []
    for x0, z0, x1, z1, flags in edges:
        a = len(nodes) // 2
        nodes += [x0, z0, x1, z1]
        rows.append([a, a + 1, flags, math.hypot(x1 - x0, z1 - z0)])
    return json.dumps({"nodes": nodes, "edges": rows})


def test_register_lamp_rows_collapse_to_the_best_surveyed_pole():
    skipped = Counter()
    lamps = [
        Prop("frn:digitised", "lamp", 10.4, 0, 10, rank=3),
        Prop("frn:measured", "lamp", 10, 0, 10, rank=0),
        Prop("frn:rr", "lamp", 11.0, 0, 10.3, rank=1),
        Prop("frn:next", "lamp", 40, 0, 10, rank=1),
    ]
    kept = deduplicate_lamps(lamps, skipped)
    assert [p.object_id for p in kept] == ["frn:measured", "frn:next"]
    assert skipped["duplicate_register_lamp"] == 2


def test_osm_furniture_defers_to_the_register_and_bollards_stay_barriers():
    register = [
        Prop("reg:bench", "bench", 50, 0, 50),
        Prop("reg:lamp", "lamp", 100, 0, 100),
    ]
    frame = osm(
        [
            ({"amenity": "bench"}, Point(51, 51)),  # the register bench, mapped again
            ({"amenity": "bench"}, Point(60, 50)),  # a bench the register lacks
            ({"amenity": "waste_basket"}, Point(50.5, 50)),  # no register bin there
            ({"highway": "street_lamp"}, Point(101, 100)),
            ({"barrier": "bollard"}, Point(70, 70)),
            ({"vending": "parking_tickets"}, Point(80, 80)),
        ]
    )
    skipped = Counter()
    found = osm_props(frame, TILE.bbox, register, shapely.Polygon(), skipped)
    assert sorted((p.kind, p.x) for p in found) == [
        ("bench", 60),
        ("bin", 50.5),
        ("parking_meter", 80),
    ]
    assert skipped["osm_bench_in_register"] == 1 and skipped["osm_lamp_in_register"] == 1


def test_lit_rule_fills_only_stations_without_any_lamp():
    street = LineString([(0, 100), (240, 100)])
    frame = osm([({"highway": "residential", "lit": "yes", "width": "8"}, street)])
    register = [Prop(f"reg:{x}", "lamp", x, 0, 105, rank=0) for x in (15, 45, 75)]
    found = osm_props(frame, TILE.bbox, register, shapely.Polygon(), Counter())
    rule = sorted(p.x for p in found if p.source == "osm_lit_street")
    assert rule and min(rule) > 75 + 20 - 1  # stations near register lamps get none


def test_carriageway_moves_survey_offsets_hangs_deep_lamps_and_drops_furniture():
    road = box(0, 0, 200, 12)
    props = [
        Prop("bench-edge", "bench", 50, 0, 11),  # 1 m inside: survey offset
        Prop("bench-deep", "bench", 60, 0, 6),
        Prop("lamp-deep", "lamp", 70, 0, 6),
        Prop("rule-deep", "lamp", 80, 0, 4, source="osm_lit_street"),
        Prop("sign", "sign", 90, 0, 6, code="E2"),
        Prop("bin-kerb", "bin", 95, 0, 14),
    ]
    skipped = Counter()
    kept = {p.object_id: p for p in off_carriageway(props, road, skipped)}
    assert "bench-deep" not in kept
    assert kept["bench-edge"].z == pytest.approx(12.35)
    assert kept["lamp-deep"].variant == "hung" and kept["lamp-deep"].z == 6
    assert kept["rule-deep"].z == pytest.approx(-0.35)  # synthetic: always to the kerb
    assert kept["sign"].z == 6 and kept["bin-kerb"].z == 14
    assert skipped["dropped_on_carriageway:bench"] == 1


def test_light_records_roundtrip_with_street_lights_and_posts_first():
    edges, trees = street_edges(graph([(0, 0, 200, 0, CAR | FOOT | 2 << CLASS_SHIFT)]))
    post = post_light(50, 3, 8, edges, trees["car"], trees["path"])
    from turku3d.props.lights import Light

    wall = Light(20, 6, -9, "wall", "wall", False)
    hung = Light(30, 10, 0, "hung", "led", True)
    field = encode([wall, hung, post])
    assert (field["count"], field["posts"], field["street"]) == (3, 1, 2)
    lights, posts = decode(field)
    assert [light["category"] for light in lights] == ["street", "hung", "wall"]
    assert [light["street"] for light in lights] == [True, True, False]
    assert len(json.dumps(field)) < 200
    # The arm reaches from the kerb toward the centreline (-z), the light at its tip.
    (p,) = posts
    assert (p["x"], p["y"], p["z"]) == pytest.approx((50, 3, 8))
    assert p["scale"] == 1.0
    assert math.cos(p["yaw"]) == pytest.approx(0, abs=1e-3)
    assert -math.sin(p["yaw"]) == pytest.approx(-1, abs=1e-3)
    assert lights[0]["z"] == pytest.approx(8 - POST_SHAPE["arm"], abs=1e-4)
    assert lights[0]["y"] == pytest.approx(3 + POST_SHAPE["light"], abs=1e-4)
    assert lights[2]["rgb"][0] == pytest.approx(1.8, abs=0.04)
    assert CATEGORIES["wall"] | 0 == 3 and STREET_FLAG == 0x80


def test_path_lamps_get_small_posts_and_lamps_far_from_streets_a_hash_heading():
    edges, trees = street_edges(graph([(0, 50, 200, 50, FOOT)]))
    path = post_light(100, 0, 53, edges, trees["car"], trees["path"])
    assert path.category == "path" and path.post[4] == 0.6
    assert path.z == pytest.approx(53 - 0.6 * POST_SHAPE["arm"])
    alone = post_light(100, 0, 200, edges, trees["car"], trees["path"])
    assert alone.category == "path" and alone.post[4] == 0.6


def test_wall_lights_mount_in_front_of_the_first_facade_with_a_seeded_share():
    edges, _ = street_edges(graph([(0, 100, 250, 100, CAR | FOOT | 2 << CLASS_SHIFT)]))
    north = box(0, 80, 250, 92)  # facade 8 m from the centreline
    south = box(0, 130, 250, 140)  # beyond the 14 m search
    lights = wall_lights(TILE, edges, [north, south], lambda x, z: 2.0)
    assert lights
    assert all(light.z == pytest.approx(92 + 0.75, abs=1e-6) for light in lights)
    assert all(3.2 + 2 <= light.y <= 4.4 + 2 for light in lights)
    xs = sorted(light.x for light in lights)
    assert all(b - a >= 4 for a, b in zip(xs, xs[1:], strict=False))
    assert len(lights) < 250 / 16  # the share leaves some facades dark
    again = wall_lights(TILE, edges, [north, south], lambda x, z: 2.0)
    assert [(a.x, a.y, a.z) for a in lights] == [(b.x, b.y, b.z) for b in again]


def test_placement_moves_drops_mounts_and_collides(monkeypatch):
    from turku3d.coarse.furniture import produce
    from turku3d.package.gltf import read_glb

    house = box(100, 100, 120, 120)
    lake = box(200, 200, 240, 240)
    props = [
        Prop("bench-wall", "bench", 100.5, 0, 110),  # 0.5 m inside the house
        Prop("bench-in", "bench", 110, 0, 110),  # deep inside: dropped
        Prop("lamp-wall", "lamp", 119, 0, 105),  # register pole 1 m inside the footprint
        Prop("lamp", "lamp", 50, 0, 58),
        Prop("bin-lake", "bin", 220, 0, 220),
        Prop("bench-quay", "bench", 201, 0, 220),  # 1 m into the lake: the quay edge
        Prop("unsupported", "post", 70, 0, 70),
        Prop("table", "table", 30, 0, 30, yaw=math.pi / 2),
        Prop("stop", "bus_stop", 60, 0, 58),
    ]
    roads = graph([(0, 50, 250, 50, CAR | FOOT | 2 << CLASS_SHIFT)])
    result = produce(TILE, props, lambda x, z: 1.0, [house], [lake], roads, shapely.Polygon())
    stats = result["stats"]
    assert stats["dropped_in_building:bench"] == 1
    assert stats["moved_out_of_building:bench"] == 1
    assert stats["dropped_on_water:bin"] == 1
    assert stats["moved_out_of_water:bench"] == 1 and stats["skipped_unsupported_sign"] == 1
    quay = next(p for p in result["props"] if p.object_id == "bench-quay")
    assert quay.x == pytest.approx(199.7)
    assert stats["moved_out_of_building:lamp"] == 1
    bench = next(p for p in result["props"] if p.object_id == "bench-wall")
    assert bench.x == pytest.approx(99.7) and bench.y == 1.0
    assert {p.kind for p in result["props"]} == {"bench", "table", "bus_stop"}
    assert [p.object_id for p in result["poles"]] == ["stop"]  # parked-car bay masks
    lights, posts = decode(result["lights"])
    assert len(posts) == 2 and not any(light["category"] == "hung" for light in lights)
    assert sorted(round(p["x"], 2) for p in posts) == [50, 120.3]  # by the wall, not on the roof
    prims = [p for cell in result["collision"].values() for p in cell]
    kinds = Counter(p.kind for p in prims)
    from turku3d.col.format import PRIM_BOX, PRIM_CYLINDER

    assert kinds[PRIM_CYLINDER] == 2 and kinds[PRIM_BOX] == 5  # 2 posts; 2 benches x2, table
    js, _ = read_glb(result["glb"])
    types = sorted(n["extras"]["prop_type"] for n in js["nodes"][1:])
    assert types == ["bench", "bench", "bus_stop", "table"]
    stop = next(n for n in js["nodes"][1:] if n["extras"]["prop_type"] == "bus_stop")
    assert stop["extras"]["pole_base"] == [60, 1.0, 58]


def test_register_trees_win_over_nearby_osm_trees(tmp_path):
    import geopandas as gpd
    from turku3d.coarse.inputs import prepare_trees

    register = gpd.GeoDataFrame(
        {
            "feature_key": ["a", "b"],
            "Istutusvuosi": ["2016", "0"],
            "Kasviryhma": ["Lehtipuu", "Havupuu"],
            "Kasvilaji": ["Tilia cordata (metsälehmus)", "Pinus sylvestris"],
        },
        geometry=[Point(10, 10), Point(30, 30)],
    )
    register["geometry_local"] = gpd.GeoSeries([Point(10, 10), Point(30, 30)])
    register.to_parquet(tmp_path / "puut.parquet")
    osm_frame = gpd.GeoDataFrame(
        {
            "osm_id": [1, 2],
            "tags": [json.dumps({"natural": "tree"}), json.dumps({"natural": "tree"})],
        },
        geometry=[Point(11, 11), Point(60, 60)],
    )
    osm_frame["geometry_local"] = gpd.GeoSeries([Point(11, 11), Point(60, 60)])
    osm_frame.to_parquet(tmp_path / "osm.parquet")
    trees = prepare_trees(TILE, tmp_path / "osm.parquet", tmp_path / "puut.parquet")
    assert [t[0] for t in trees] == ["puut:a", "puut:b", "osm:2"]
    assert trees[0][3] == pytest.approx(4 + 0.35 * 10) and not trees[0][4]
    assert trees[1][3] == 10 and trees[1][4]
    streets = gpd.GeoDataFrame(
        {"Katuosalaji": ["Ajorata", "Välikaista"]},
        geometry=[box(25, 25, 35, 35), box(5, 5, 15, 15)],
    )
    streets["geometry_local"] = gpd.GeoSeries(list(streets.geometry))
    streets.to_parquet(tmp_path / "streets.parquet")
    kept = prepare_trees(
        TILE, tmp_path / "osm.parquet", tmp_path / "puut.parquet", tmp_path / "streets.parquet"
    )
    assert [t[0] for t in kept] == ["puut:a", "osm:2"]  # the verge tree stays, the road one goes


def test_register_trees_keep_their_verges_where_mapped_trees_give_way():
    from types import SimpleNamespace

    from turku3d.coarse.forests import produce
    from turku3d.vegetation.trees import Tree

    verge = box(0, 0, 250, 20)  # a street part (e.g. Välikaista) along the tile
    inputs = SimpleNamespace(
        water=[],
        buildings=[],
        streets=[(verge, 0)],
        roads=None,
        vegetation_paths=[],
        forests=[],
        canopy=(),
    )
    mapped = [
        Tree("puut:a", 50, 10, "osm", height=8),
        Tree("osm:1", 100, 10, "osm", height=8),
        Tree("puut:b", 150, 100, "osm", height=8),
    ]
    trees, _ = produce(TILE, inputs, lambda x, z: 0.0, mapped)
    assert sorted(t.object_id for t in trees) == ["puut:a", "puut:b"]
