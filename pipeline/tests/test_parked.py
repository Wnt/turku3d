"""Small synthetic acceptance for parking clearance, determinism and published collisions."""

import numpy as np
from shapely.geometry import LineString, Point, box
from turku3d.layers import read_collision, read_manifest, read_visual
from turku3d.parked.__main__ import publish
from turku3d.parked.models import MODELS, model
from turku3d.parked.placement import Feature, footprint, place


def fixture():
    return [
        Feature("lot", box(0, 0, 65, 40), {"amenity": "parking"}),
        Feature(
            "drive",
            LineString([(30, -10), (30, 45)]),
            {"highway": "service", "service": "driveway"},
        ),
        Feature("cross", Point(15, 15), {"highway": "crossing"}),
        Feature("house", box(45, 0, 60, 12), {"building": "yes"}),
    ]


def test_determinism_and_whole_footprint_access_clearance():
    features = fixture()
    a, excluded, _, _ = place(features, [], box(-10, -10, 70, 50), (5, 30))
    b, _, _, _ = place(list(reversed(features)), [], box(-10, -10, 70, 50), (5, 30))
    assert a == b and len(a) > 5
    shapes = [footprint(c["x"], c["z"], c["yaw"], c["width"], c["length"]) for c in a]
    assert all(not excluded.intersects(p) for p in shapes)
    assert all(a.distance(b) >= 0.55 for i, a in enumerate(shapes) for b in shapes[i + 1 :])
    different, _, _, _ = place(features, [], box(-10, -10, 70, 50), (5, 30), seed=17)
    assert a != different


def test_no_underground_and_no_parking_street():
    features = [
        Feature(
            "underground", box(0, 0, 100, 100), {"amenity": "parking", "parking": "underground"}
        ),
        Feature(
            "street",
            LineString([(0, 0), (100, 0)]),
            {"highway": "residential", "parking:both": "no"},
        ),
    ]
    assert place(features, [], box(-20, -20, 120, 120), (200, 200))[0] == []


def test_parallel_lane_clearance_and_yaw():
    f = Feature(
        "street", LineString([(0, 0), (80, 0)]), {"highway": "residential", "parking:right": "lane"}
    )
    cars, excluded, _, _ = place([f], [], box(-20, -20, 100, 100), (200, 200))
    assert cars
    for c in cars:
        shape = footprint(c["x"], c["z"], c["yaw"], c["width"], c["length"])
        assert not excluded.intersects(shape)
        assert shape.bounds[2] - shape.bounds[0] > shape.bounds[3] - shape.bounds[1]


def test_four_models_dimensions_and_layer_roundtrip(tmp_path):
    cars = []
    for i, name in enumerate(MODELS):
        pos, idx, normals, uv = model(name)
        np.testing.assert_allclose(np.ptp(pos, axis=0), MODELS[name], atol=1e-8)
        assert idx.max() < len(pos) and len(uv) == len(normals) == len(pos)
        w, h, length = MODELS[name]
        cars.append(
            dict(
                object_id=name,
                model=name,
                color="silver",
                x=61 + i * 9,
                y=5,
                z=30,
                yaw=0.7,
                width=w,
                height=h,
                length=length,
            )
        )
    root = tmp_path / "traffic_static"
    publish(cars, root, {"fixture": "synthetic"}, 16)
    m = read_manifest(root)
    primitives = []
    for name in m["tiles"]:
        if name.endswith(".npz"):
            primitives.extend(read_collision(root / name).primitives)
        else:
            read_visual(root / name)
    assert len(primitives) == 4  # edge-crossing object appears exactly once
    assert all(p["kind"] == "box" and p["material"] == 28 for p in primitives)
    for p in primitives:
        assert p["center"][1] - p["half_extents"][1] == 5


def test_published_boxes_stop_player_and_vehicle_shapes(tmp_path):
    import json
    import subprocess

    from turku3d import config as C
    from turku3d.package.assemble import collision_tile, load_layers

    layer = tmp_path / "layers" / "traffic_static"
    cars = []
    for i, (name, (width, height, length)) in enumerate(MODELS.items()):
        cars.append(
            dict(
                object_id=name,
                model=name,
                color="silver",
                x=10 + i * 15,
                y=4,
                z=20,
                yaw=i * 0.6,
                width=width,
                height=height,
                length=length,
            )
        )
    publish(cars, layer, {"fixture": "synthetic"}, 16)
    manifests, layers = load_layers(layer.parent)
    out = tmp_path / "collision"
    out.mkdir()
    ids = set(layers["traffic_static"])
    for tid in ids:
        data, _ = collision_tile(C.CollisionTile.parse(tid), layers, manifests, ids)
        (out / f"{tid}.col").write_bytes(data)
    script = C.REPO_ROOT / "tools/validator/parked.mjs"
    result = subprocess.run(
        ["node", str(script), str(out)], capture_output=True, text=True, check=True
    )
    report = json.loads(result.stdout.strip().splitlines()[-1])
    assert report["pass"] is True
    assert report["boxes"] == 4 and report["shapeSweeps"] == 8
