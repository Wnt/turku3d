"""Tiny fixtures for register classification, transforms, ownership and instance reuse."""

import math

import numpy as np
import pytest
from shapely.geometry import LineString, box
from shapely.strtree import STRtree
from turku3d import config as C
from turku3d.layers import read_collision, read_manifest, read_visual
from turku3d.package.gltf import accessor_array, read_glb
from turku3d.props import Prop, classify, face_road, primitives, publish, visual_bytes
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


def test_boundary_ownership_and_shared_mesh_roundtrip(tmp_path):
    props = [
        Prop("one", "bench", 62.49, 4, 3),
        Prop("two", "bench", 62.51, 5, 3),
        Prop("edge", "lamp", 250, 6, 250),
        Prop("negative", "post", -0.01, 4, -0.01),
    ]
    root = tmp_path / "props"
    metrics = publish(props, root, {}, {})
    manifest = read_manifest(root)
    assert metrics["objects"] == 4
    assert len(manifest["tiles"]) == 7
    a = read_collision(root / "c_0_0.col.npz")
    b = read_collision(root / "c_1_0.col.npz")
    assert len(a.primitives) == len(b.primitives) == 2
    assert a.positions.shape == (0, 3)
    js, blob = read_glb(read_visual(root / "t0_0_0.vis.glb"))
    assert len(js["meshes"]) == 1
    nodes = js["nodes"][1:]
    assert [n["mesh"] for n in nodes] == [0, 0]
    assert nodes[0]["translation"] == [62.49, 4, 3]
    assert nodes[1]["extras"]["object_id"] == "two"
    pos = accessor_array(js, blob, js["meshes"][0]["primitives"][0]["attributes"]["POSITION"])
    assert pos[:, 0].max() == pytest.approx(0.9)
    assert metrics["collision_primitives"] == 6
    # Re-publication with fewer objects must not expose old files in the manifest.
    publish(props[:1], root, {}, {})
    assert set(read_manifest(root)["tiles"]) == {"t0_0_0.vis.glb", "c_0_0.col.npz"}


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
    props, inputs, meta = read_sources(C.BBoxLocal(0, 0, 25, 25))
    assert [p.kind for p in props] == ["sign", "post", "bench"]
    assert props[1].code == "XYZ"
    assert props[2].y == 0  # register point Z must not become a ground elevation
    assert meta["skipped"]["duplicates"] == meta["skipped"]["duplicate_stop"] == 1
    assert "kanta_Sahkoverkonosa.parquet" in meta["missing_sources"]
    assert len(inputs) == 3
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

    from turku3d.package.assemble import assemble

    monkeypatch.setenv("TURKU3D_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("TURKU3D_WORK", str(tmp_path / "work"))
    props = [Prop("one", "bench", 10, 3, 20), Prop("two", "bench", 30, 5, 40)]
    root = tmp_path / "layers/props"
    publish(props, root, {}, {})
    out = tmp_path / "dist"
    assemble(["t0_0_0"], layers=root.parent, out=out, area="work-3x3")
    entry = json.loads((out / "manifest.json").read_text())["visual"]["t0_0_0"]
    expected = np.concatenate([model("bench").positions + [p.x, p.y, p.z] for p in props])
    np.testing.assert_allclose(entry["bounds"], [expected.min(0), expected.max(0)], atol=0.002)
