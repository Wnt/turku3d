import hashlib
import json

import geopandas as gpd
import pytest
from shapely.geometry import LineString
from test_assemble import assemble, fixture
from turku3d import config as C
from turku3d import roads
from turku3d.layers import write_manifest
from turku3d.package.manifest import DistTree, verify_tree
from turku3d.roads import CAR, CLASS_SHIFT, FOOT, ONEWAY, build_graph, way_flags
from turku3d.roads.__main__ import build as build_layer

STREET = 2 << CLASS_SHIFT


@pytest.fixture(autouse=True)
def isolated_data(tmp_path, monkeypatch):
    monkeypatch.setenv("TURKU3D_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("TURKU3D_WORK", str(tmp_path / "work"))


def test_way_flags_follow_highway_access_and_oneway_tags():
    assert way_flags("residential", {}) == FOOT | CAR | STREET
    assert way_flags("footway", {}) == FOOT
    assert way_flags("steps", {}) == FOOT
    assert way_flags("motorway", {}) == CAR | 3 << CLASS_SHIFT
    assert way_flags("service", {"foot": "no"}) == CAR | 1 << CLASS_SHIFT
    assert way_flags("pedestrian", {"motor_vehicle": "yes"}) == FOOT
    assert way_flags("residential", {"motor_vehicle": "no"}) == FOOT | STREET
    assert way_flags("residential", {"oneway": "yes"}) == FOOT | CAR | ONEWAY | STREET
    assert way_flags("tertiary", {"junction": "roundabout"}) & ONEWAY
    assert way_flags("footway", {"oneway": "yes"}) == FOOT  # oneway binds vehicles only
    for highway, tags in [
        ("corridor", {}),
        ("construction", {}),
        ("service", {"access": "private"}),
        ("footway", {"indoor": "yes"}),
        ("pedestrian", {"area": "yes"}),
        ("bus_stop", {}),
    ]:
        assert way_flags(highway, tags) == 0, highway


def test_graph_splits_ways_at_shared_vertices_and_keeps_polylines():
    street = ([(0, 0), (10, 0), (20, 0), (20, 15)], FOOT | CAR | STREET)
    path = ([(10, -10), (10, 0), (10, 10)], FOOT)
    graph = build_graph([street, path], (-50, -50, 50, 50), "test")
    nodes = graph["nodes"]
    points = {(nodes[2 * i], nodes[2 * i + 1]) for i in range(len(nodes) // 2)}
    assert points == {(0, 0), (10, 0), (20, 15), (10, -10), (10, 10)}
    edges = sorted(graph["edges"], key=lambda e: -e[3])
    assert len(edges) == 4
    bent = edges[0]
    assert bent[3] == 25.0 and bent[4:] == [20.0, 0.0]  # (10,0) -> (20,0) -> (20,15)
    assert sum(e[2] & CAR == CAR for e in edges) == 2
    assert all(len(e) == 4 for e in edges[1:])
    roads.validate(graph)


def test_graph_keeps_only_the_largest_network_per_mode():
    main = ([(0, 0), (100, 0)], FOOT | CAR | STREET)
    link = ([(100, 0), (100, 50)], FOOT)
    island = ([(300, 300), (305, 300)], FOOT | CAR | ONEWAY | STREET)
    car_island = ([(100, 50), (130, 50)], FOOT | CAR | ONEWAY | STREET)
    graph = build_graph([main, link, island, car_island], (-500, -500, 500, 500), "test")
    flags = sorted((e[3], e[2]) for e in graph["edges"])
    # The isolated pair is gone; the second car segment keeps walking but loses car and oneway.
    assert flags == [(30.0, FOOT | STREET), (50.0, FOOT), (100.0, FOOT | CAR | STREET)]


def test_validate_rejects_bad_graphs():
    good = build_graph([([(0, 0), (5, 0)], FOOT)], (0, 0, 10, 10), "test")
    for broken in (
        {**good, "format": "x"},
        {**good, "edges": [[0, 9, FOOT, 5.0]]},
        {**good, "edges": [[0, 1, 0, 5.0]]},
        {**good, "edges": [[0, 1, FOOT, 5.0, 1.0]]},
    ):
        with pytest.raises(ValueError):
            roads.validate(broken)


def osm_fixture(path):
    rows = [
        (
            "residential",
            {"highway": "residential", "oneway": "-1"},
            [(-20, 10), (40, 10), (80, 10)],
        ),
        ("footway", {"highway": "footway"}, [(40, 10), (40, 60)]),
        ("corridor", {"highway": "corridor"}, [(40, 60), (40, 90)]),
        ("bus_stop", {"highway": "bus_stop"}, None),
    ]
    frame = gpd.GeoDataFrame(
        {
            "highway": [r[0] for r in rows],
            "tags": [json.dumps(r[1]) for r in rows],
            "geometry_local": gpd.GeoSeries(
                [LineString(r[2]) if r[2] else LineString([(0, 0), (0, 0)]).centroid for r in rows]
            ),
        },
        geometry="geometry_local",
    )
    frame.to_parquet(path)


def test_layer_build_clips_to_area_and_reverses_backward_oneways(tmp_path, monkeypatch):
    osm = tmp_path / "osm.parquet"
    osm_fixture(osm)
    monkeypatch.setitem(C.AREAS, "tiny", C.Area("tiny", "test", C.BBoxLocal(0, 0, 70, 70)))
    out = tmp_path / "layers/roads"
    params = build_layer(out, osm, "tiny")
    graph = json.loads((out / "roadgraph.json").read_text())
    assert params["nodes"] == 4 and params["edges"] == 3 and params["graph"]["bytes"] < 1000
    xs = graph["nodes"][0::2]
    assert min(xs) == 0 and max(xs) == 70  # clipped at the area edge
    nodes = graph["nodes"]
    for a, b, flags, *_ in graph["edges"]:
        if flags & ONEWAY:
            assert nodes[2 * a] > nodes[2 * b]  # oneway=-1 runs against the drawn direction
    manifest = json.loads((out / "MANIFEST.json").read_text())
    assert manifest["code"] == roads.code_hash() and manifest["tiles"] == {}


def roads_layer(root, length):
    layer = root / "roads"
    graph = build_graph([([(10, 10), (10 + length, 10)], FOOT)], (0, 0, 250, 250), "test")
    data = roads.encode(graph)
    layer.mkdir(parents=True, exist_ok=True)
    (layer / "roadgraph.json").write_bytes(data)
    params = {"graph": {"file": "roadgraph.json", "sha256": hashlib.sha256(data).hexdigest()}}
    write_manifest(layer, inputs={}, params=params, code="test", tiles=[])


def test_publish_rejects_a_changed_graph_file(tmp_path):
    roads_layer(tmp_path, 20)
    (tmp_path / "roads/roadgraph.json").write_text("{}")
    with pytest.raises(ValueError, match="hash mismatch"):
        roads.publish(tmp_path / "roads", DistTree(tmp_path / "dist"))


def test_assembly_publishes_graph_without_rebuilding_tiles(tmp_path):
    layers, out = tmp_path / "layers", tmp_path / "dist"
    fixture(layers)
    roads_layer(layers, 20)
    assemble(["t0_0_0"], layers=layers, out=out)
    first = json.loads((out / "manifest.json").read_text())
    assert first["roads"]["nodes"] == 2 and first["roads"]["edges"] == 1
    assert json.loads((out / first["roads"]["url"]).read_text())["edges"][0][3] == 20.0
    roads_layer(layers, 30)
    result = assemble(["t0_0_0"], layers=layers, out=out)
    assert result["changed"] == []  # tile content keys exclude the road graph
    second = json.loads((out / "manifest.json").read_text())
    assert second["roads"]["url"] != first["roads"]["url"]
    assert second["visual"] == first["visual"] and not verify_tree(out)
    (out / second["roads"]["url"]).unlink()
    assert verify_tree(out) == [f"missing {second['roads']['url']}"]
