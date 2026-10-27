"""Tiny fixtures for register classification, transforms, ownership and instance reuse."""

import math

import numpy as np
import pytest
from shapely.geometry import LineString, box
from shapely.strtree import STRtree
from turku3d import config as C
from turku3d.layers import read_collision, read_visual
from turku3d.package.gltf import accessor_array, read_glb
from turku3d.props import Prop, classify, face_road, primitives, visual_bytes, write_tile
from turku3d.props.models import SUPPORTED_SIGNS, atlas, model
from turku3d.props.orientation import heading


@pytest.mark.parametrize(
    ("label", "kind"),
    [
        ("Kiintopenkki", "bench"),
        ("Roskakori", "bin"),
        ("Pollari", "bollard"),
        ("Polkupyöräteline", "bike_rack"),
        ("Unknown", "post"),
    ],
)
def test_classification(label, kind):
    assert classify({"Varustelaji": label}) == kind


def test_road_facing_and_collision_rotation():
    yaw, source = face_road(5, 5, STRtree([LineString([(10, 0), (10, 20)])]))
    assert source == "nearest_road"
    assert yaw == pytest.approx(math.pi / 2)
    p = Prop("bench", "bench", 5, 3, 5, yaw)
    collision = primitives(p)
    assert collision["kind"].tolist() == ["box", "box"]
    assert collision[1]["center"] == pytest.approx([4.76, 3.73, 5])
    assert collision[0]["yaw"] == pytest.approx(yaw)


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        ("sign", -math.pi),
        ("bus_stop", -math.pi),
        ("bench", -math.pi / 2),
        ("lamp", math.pi / 2),
        ("bin", 0),
        ("bollard", 0),
        ("bike_rack", 0),
        ("bike_station", 0),
        ("post", 0),
    ],
)
def test_class_heading_and_exported_front(kind, expected):
    roads = STRtree([LineString([(10, 0), (10, 20)])])
    walkway = box(3, 0, 8, 20)
    yaw, method = heading(kind, 5, 5, roads, walkway)
    assert yaw == pytest.approx(expected)
    assert method != "no_road_default"
    p = Prop(kind, kind, 5, 0, 5, yaw)
    js, _ = read_glb(visual_bytes([p], C.VisualTile(0, 0, 0)))
    rotation = js["nodes"][1]["rotation"]
    assert 2 * math.atan2(rotation[1], rotation[3]) == pytest.approx(expected)


def test_sign_faces_oncoming_traffic_on_both_sides_and_reversed_geometry():
    for coordinates in ([(10, 0), (10, 20)], [(10, 20), (10, 0)]):
        road = STRtree([LineString(coordinates)])
        left = heading("sign", 5, 5, road)[0]
        right = heading("sign", 15, 5, road)[0]
        assert math.cos(left) == pytest.approx(-1)
        assert math.cos(right) == pytest.approx(1)


def test_tile_files_and_shared_mesh_roundtrip(tmp_path):
    props = [
        Prop("one", "bench", 62.49, 4, 3),
        Prop("two", "bench", 62.51, 5, 3),
        Prop("corner", "lamp", 249.9, 6, 249.9),
    ]
    root = tmp_path / "props"
    root.mkdir()
    metrics = write_tile(props, root, C.VisualTile(0, 0, 0))
    assert metrics["objects"] == 3 and metrics["collision_primitives"] == 5
    assert sorted(p.name for p in root.iterdir()) == [
        "c_0_0.col.npz",
        "c_1_0.col.npz",
        "c_3_3.col.npz",
        "t0_0_0.vis.glb",
    ]
    a = read_collision(root / "c_0_0.col.npz")
    b = read_collision(root / "c_1_0.col.npz")
    assert len(a.primitives) == len(b.primitives) == 2
    assert a.positions.shape == (0, 3)
    js, blob = read_glb(read_visual(root / "t0_0_0.vis.glb"))
    assert len(js["meshes"]) == 2
    nodes = js["nodes"][1:]
    assert nodes[0]["translation"] == [62.49, 4, 3]
    assert nodes[1]["extras"]["object_id"] == "two"
    bench = [m for m in js["meshes"] if m["name"].startswith("bench")] or js["meshes"][:1]
    assert nodes[0]["mesh"] == nodes[1]["mesh"]
    pos = accessor_array(js, blob, bench[0]["primitives"][0]["attributes"]["POSITION"])
    assert pos[:, 0].max() == pytest.approx(0.9)
    with pytest.raises(ValueError, match="outside the tile"):
        write_tile([Prop("edge", "lamp", 250, 6, 250)], root, C.VisualTile(0, 0, 0))


@pytest.mark.parametrize(
    "kind",
    [
        "bench",
        "bin",
        "bollard",
        "bike_rack",
        "bike_station",
        "parking_meter",
        "lamp",
        "bus_stop",
        "sign",
        "post",
    ],
)
def test_models_have_finite_non_degenerate_geometry(kind):
    m = model(kind, "E2" if kind == "sign" else "")
    assert np.isfinite(m.positions).all()
    assert len(m.indices) < 1000
    tri = m.positions[m.indices]
    assert (
        np.linalg.norm(np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0]), axis=1) > 0
    ).all()
    assert len(primitives(Prop("p", kind, 0, 0, 0))) > 0


def test_sign_faces_are_distinct_embedded_pngs():
    faces = [atlas(code) for code in sorted(SUPPORTED_SIGNS)]
    assert len(set(faces)) == len(SUPPORTED_SIGNS)
    assert all(face.startswith(b"\x89PNG") for face in faces)


def test_register_ingestion_and_missing_optional_sources(tmp_path, monkeypatch):
    import geopandas as gpd
    from shapely.geometry import Point
    from turku3d.props import read_sources

    monkeypatch.setattr(C, "data_root", lambda: tmp_path)
    root = tmp_path / "raw/wfs/work-3x3"
    root.mkdir(parents=True)

    def write(name, records, points):
        frame = gpd.GeoDataFrame(records, geometry=points, crs=C.CRS_HORIZONTAL)
        frame["geometry_local"] = gpd.GeoSeries(points, crs=C.CRS_HORIZONTAL)
        frame.to_parquet(root / (name + ".parquet"))

    write(
        "GIS_Varusteet",
        [
            dict(Id="a", feature_key="a", Tyyppi="Penkki"),
            dict(Id="b", feature_key="b", Tyyppi="Penkki"),
        ],
        [Point(10, 10, 80), Point(10, 10, 80)],
    )
    write(
        "GIS_Liikennemerkit",
        [
            dict(Id="s", feature_key="s", Varustelaji_koodi="E6"),
            dict(Id="u", feature_key="u", Varustelaji_koodi="XYZ"),
        ],
        [Point(15, 15), Point(20, 20)],
    )
    write("kanta_Pysakki", [dict(feature_key="stop")], [Point(15.1, 15.1)])
    props, meta = read_sources(C.BBoxLocal(0, 0, 25, 25))
    assert [p.kind for p in props] == ["sign", "post", "bench"]
    assert props[1].code == "XYZ"
    assert props[2].y == 0  # register point Z must not become a ground elevation
    assert meta["skipped"]["duplicates"] == meta["skipped"]["duplicate_stop"] == 1
    assert "kanta_Sahkoverkonosa.parquet" in meta["missing_sources"]
    assert all(p.orientation == "no_road_default" for p in props)


def test_published_surface_and_unsupported_distance():
    from turku3d.placement import Surfaces

    ground = Surfaces(
        [[[0, 3, 0], [2, 3, 0], [0, 3, 2]], [[2, 3, 0], [2, 3, 2], [0, 3, 2]]], [2, 2]
    )
    assert ground.at(1, 1)[0] == pytest.approx(3)
    assert ground.at(40, 40) is None


def test_assembled_shared_prototypes_keep_each_world_transform(tmp_path, monkeypatch):
    import json

    from turku3d.layers import write_manifest
    from turku3d.package.assemble import assemble

    monkeypatch.setenv("TURKU3D_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("TURKU3D_WORK", str(tmp_path / "work"))
    props = [Prop("one", "bench", 10, 3, 20), Prop("two", "bench", 30, 5, 40)]
    root = tmp_path / "layers/props"
    root.mkdir(parents=True)
    write_tile(props, root, C.VisualTile(0, 0, 0))
    names = [p.name for p in root.iterdir()]
    write_manifest(root, inputs={}, params={"seams": "objects"}, code="test", tiles=names)
    out = tmp_path / "dist"
    assemble(["t0_0_0"], layers=root.parent, out=out, area="work-3x3")
    entry = json.loads((out / "manifest.json").read_text())["visual"]["t0_0_0"]
    expected = np.concatenate([model("bench").positions + [p.x, p.y, p.z] for p in props])
    np.testing.assert_allclose(entry["bounds"], [expected.min(0), expected.max(0)], atol=0.002)


def test_props_build_per_tile_from_each_tiles_inputs(tmp_path, monkeypatch):
    """Two tiles: each owns its props; adding a register record rebuilds only its tile."""
    import json

    import producer_world as world
    from shapely.geometry import Point
    from turku3d import tile_layer
    from turku3d.layers import layer_root
    from turku3d.props import PRODUCER

    monkeypatch.setenv("TURKU3D_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("TURKU3D_WORK", str(tmp_path / "work"))
    monkeypatch.setenv("TURKU3D_JOBS", "1")
    world.city(streets=[("Jalkakäytävä", box(0, 0, 500, 250))])
    world.ground(["t0_0_0", "t0_1_0"])
    world.ortho()
    benches = [Point(100, 100), Point(350, 100)]
    world.write_wfs(
        "GIS_Varusteet",
        [dict(Id=f"b{i}", feature_key=f"b{i}", Tyyppi="Penkki") for i in range(2)],
        benches,
    )
    world.write_osm([({"highway": "street_lamp"}, Point(120, 60))])
    out = layer_root() / "props"
    result = tile_layer.run(out, PRODUCER, ["t0_0_0", "t0_1_0"])
    assert result["built"] == ["t0_0_0", "t0_1_0"]
    instances = json.loads((out / "instances.json").read_text())
    assert sorted((r["object_id"], r["y"]) for r in instances) == [
        ("GIS_Varusteet:b0", 3.0),
        ("GIS_Varusteet:b1", 3.0),
        ("osm:1", 3.0),
    ]
    tiles = json.loads((out / "MANIFEST.json").read_text())["tiles"]
    assert {"t0_0_0.vis.glb", "t0_1_0.vis.glb"} <= set(tiles)
    assert (tmp_path / "work/reports/placement/props/t0_1_0/placement.json").is_file()
    world.write_wfs(
        "GIS_Varusteet",
        [dict(Id=f"b{i}", feature_key=f"b{i}", Tyyppi="Penkki") for i in range(3)],
        [*benches, Point(130, 140)],
    )
    # The register file is shared by both tiles, so both keys change; a tile list change
    # alone (a third tile without props) adds only that tile.
    assert tile_layer.stale(out, PRODUCER, ["t0_0_0", "t0_1_0"]) == ["t0_0_0", "t0_1_0"]
    tile_layer.run(out, PRODUCER, ["t0_0_0", "t0_1_0"])
    world.ground(["t0_0_0", "t0_1_0", "t0_0_1"])
    assert tile_layer.stale(out, PRODUCER, ["t0_0_0", "t0_1_0", "t0_0_1"]) == ["t0_0_1"]


def test_osm_signs_move_to_the_roadside_and_defer_to_the_register():
    import json
    from collections import Counter

    import pandas as pd
    from shapely.geometry import Point
    from turku3d.props import osm_props, sign_codes

    assert sign_codes({"highway": "give_way"}) == ["B5"]
    assert sign_codes({"highway": "stop", "traffic_sign": "FI:B6;Fi:H20, FI:X99"}) == [
        "B6",
        "H20",
        "X99",
    ]
    carriageway = box(0, 40, 200, 47)
    frame = pd.DataFrame(
        [
            {"osm_id": 1, "tags": json.dumps({"highway": "stop"}), "geometry_local": Point(50, 42)},
            {
                "osm_id": 2,
                "tags": json.dumps({"highway": "give_way"}),
                "geometry_local": Point(150, 46),
            },
            {
                "osm_id": 3,
                "tags": json.dumps({"traffic_sign": "FI:X99"}),
                "geometry_local": Point(100, 30),
            },
        ]
    )
    register = [Prop("city:b5", "sign", 160, 0, 49, code="B5")]
    skipped = Counter()
    found = osm_props(frame, C.BBoxLocal(0, 0, 250, 250), register, carriageway, skipped)
    stop = next(p for p in found if p.object_id == "osm:1:B6")
    assert (stop.kind, stop.code) == ("sign", "B6")
    assert stop.x == pytest.approx(50) and stop.z == pytest.approx(40 - 0.7)
    assert [p.object_id for p in found if p.kind == "post"] == ["osm:3:X99"]
    assert skipped["osm_sign_in_register"] == 1 and skipped["unsupported_sign:X99"] == 1


def test_lit_streets_without_lamps_get_lamps_by_spacing():
    import json
    from collections import Counter

    import pandas as pd
    from shapely.geometry import Point
    from turku3d.props import LAMP_SPACING, osm_props

    street = LineString([(0, 100), (240, 100)])
    frame = pd.DataFrame(
        [
            {
                "osm_id": 7,
                "tags": json.dumps({"highway": "residential", "lit": "yes", "width": "8"}),
                "geometry_local": street,
            },
            {
                "osm_id": 8,
                "tags": json.dumps({"highway": "street_lamp"}),
                "geometry_local": Point(210, 103),
            },
            {
                "osm_id": 9,
                "tags": json.dumps({"highway": "footway"}),
                "geometry_local": LineString([(0, 0), (99, 0)]),
            },
        ]
    )
    found = osm_props(frame, C.BBoxLocal(0, 0, 250, 250), [], box(0, 0, 0, 0), Counter())
    spaced = sorted((p for p in found if p.source == "osm_lit_street"), key=lambda p: p.x)
    assert len(spaced) == 6  # 8 stations, two within 20 m of the mapped lamp at x = 210
    assert all(p.kind == "lamp" and p.z == pytest.approx(105) for p in spaced)
    assert spaced[1].x - spaced[0].x == pytest.approx(LAMP_SPACING)
