import json

import numpy as np
import producer_world as world
import pytest
from shapely.geometry import Point
from turku3d import config as C
from turku3d import tile_layer
from turku3d.layers import layer_root, read_collision, read_visual, write_manifest
from turku3d.package.assemble import assemble
from turku3d.package.gltf import read_glb
from turku3d.vegetation.layer import PRODUCER, write_tile
from turku3d.vegetation.trees import (
    Tree,
    deduplicate,
    first_per_cell,
    highest_per_cell,
    measure,
    variant,
)
from turku3d.vegetation.visual import array


def test_register_priority_and_negative_tiles():
    a = Tree("register", -0.2, 62.5, "GIS_Puut")
    b = Tree("mapped", 0.1, 62.5, "kanta_Lehtipuu")
    c = Tree("separate", 2.1, 62.5, "GIS_Puut")
    assert deduplicate([a, b, c]) == [a, c]
    assert (a.x, a.z) == (-0.2, 62.5)
    assert C.CollisionTile.at(a.x, a.z).id == "c_-1_1"
    assert variant("Picea abies", 20) == "tree_pineDefaultA"
    assert variant("Ulmus glabra", 20) == "tree_oak_fall"


def test_measured_crowns_missing_support_and_lidar_only():
    grid = np.stack(np.meshgrid(np.arange(40), np.arange(40)), axis=-1).reshape(-1, 2)
    ground = np.column_stack((grid[:, 0], np.full(len(grid), 2.0), grid[:, 1]))
    rng = np.random.default_rng(3)
    canopy = []
    for x, z in ((8, 8), (28, 28)):
        disk = rng.uniform(-2, 2, (100, 2))
        canopy.extend(
            np.column_stack((disk[:, 0] + x, 12 - np.linalg.norm(disk, axis=1), disk[:, 1] + z))
        )
    trees = measure(
        [
            Tree("real", 8, 8, "GIS_Puut"),
            Tree("young", 2, 30, "GIS_Puut"),
            Tree("row", 36, 2, "kanta_Lehtipuurivi"),
        ],
        ground,
        np.array(canopy),
        C.BBoxLocal(0, 0, 40, 40),
    )
    assert len(trees) == 3
    assert trees[0].measured and 8 < trees[0].height < 11
    assert 1 < trees[0].crown_radius < 3
    assert trees[1].object_id == "young" and not trees[1].measured
    assert trees[2].source == "lidar" and trees[2].measured


def test_cell_reductions_keep_the_first_ground_and_the_highest_canopy_return():
    points = np.array([[0.1, 1, 0.1], [0.2, 5, 0.2], [0.7, 3, 0.1], [0.3, 2, 0.4]])
    assert first_per_cell(points).tolist() == [[0.1, 1, 0.1], [0.7, 3, 0.1]]
    assert sorted(highest_per_cell(points).tolist()) == [[0.2, 5, 0.2], [0.7, 3, 0.1]]


def test_layer_instancing_collision_and_packaged_world_bounds(tmp_path, monkeypatch):
    monkeypatch.setenv("TURKU3D_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("TURKU3D_WORK", str(tmp_path / "work"))
    trees = [
        Tree("a", 251, 3, "GIS_Puut", y=4, height=12, crown_radius=3),
        Tree("b", 499, 240, "GIS_Puut", y=6, height=20, crown_radius=7),
    ]
    layer = tmp_path / "layers/vegetation"
    layer.mkdir(parents=True)
    budget = write_tile(trees, layer, C.VisualTile(0, 1, 0))
    write_manifest(
        layer, inputs={}, params={}, code="test", tiles=[p.name for p in layer.iterdir()]
    )
    js, blob = read_glb(read_visual(layer / "t0_1_0.vis.glb"))
    for node in js["nodes"][1:]:
        attributes = node["extensions"]["EXT_mesh_gpu_instancing"]["attributes"]
        assert np.allclose(array(js, blob, attributes["TRANSLATION"]), [[1, 4, 3], [249, 6, 240]])
        assert node["extras"]["instance_object_ids"] == ["a", "b"]
    collision = read_collision(layer / "c_4_0.col.npz")
    assert len(collision.positions) == 0 and len(collision.primitives) == 1
    assert collision.primitives[0]["kind"] == "cylinder"
    assert collision.primitives[0]["material"] == 26
    assert collision.primitives[0]["center"][0] == 251
    assert collision.primitives[0]["radius"] < trees[0].crown_radius
    out = tmp_path / "dist"
    assemble(["t0_1_0"], layers=layer.parent, out=out, area="work-3x3")
    manifest = json.loads((out / "manifest.json").read_text())
    entry = manifest["visual"]["t0_1_0"]
    assert entry["tris"] == budget["triangles_per_color_pass"]
    assert entry["max_error_m"] < 0.002
    assert entry["bounds"][0][0] < 251 and entry["bounds"][1][0] > 499
    assert abs(entry["bounds"][1][1] - 26) < 0.002
    packed, _ = read_glb((out / entry["url"]).read_bytes())
    assert "EXT_mesh_gpu_instancing" in packed["extensionsRequired"]
    assert sum("EXT_mesh_gpu_instancing" in n.get("extensions", {}) for n in packed["nodes"]) == 2
    with pytest.raises(ValueError, match="outside the tile"):
        write_tile(trees, layer, C.VisualTile(0, 0, 0))


def test_trees_are_built_per_tile_from_the_register_and_lidar(tmp_path, monkeypatch):
    """A register tree with a lidar crown in t0_1_1; adding t0_1_0 builds only t0_1_0."""
    monkeypatch.setenv("TURKU3D_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("TURKU3D_WORK", str(tmp_path / "work"))
    monkeypatch.setenv("TURKU3D_JOBS", "1")
    world.city()
    world.ortho()
    world.ground(["t0_1_0", "t0_1_1"], material="grass")
    world.write_osm([({"amenity": "bench"}, Point(0, 0))])
    world.write_wfs(
        "GIS_Puut", [dict(Id="t1", feature_key="t1", Kasvilaji="Tilia")], [Point(300, 300)]
    )
    for name in ("kanta_Lehtipuu", "kanta_Havupuu", "kanta_Lehtipuurivi"):
        world.write_wfs(name, [], [])
    xs, zs = np.meshgrid(np.arange(230.5, 520, 1.0), np.arange(-20.5, 520, 1.0))
    ground = np.column_stack((xs.ravel(), np.full(xs.size, world.GROUND_Y), zs.ravel()))
    rng = np.random.default_rng(1)
    disk = rng.uniform(-3, 3, (600, 2))
    crown = np.column_stack(
        (disk[:, 0] + 300, world.GROUND_Y + 12 - np.linalg.norm(disk, axis=1), disk[:, 1] + 300)
    )
    points = np.concatenate([ground, crown])
    classes = np.r_[np.full(len(ground), 2), np.full(len(crown), 5)]
    world.lidar(points, classes, C.BBoxLocal(230, -20, 520, 520))
    out = layer_root() / "vegetation"
    tile_layer.run(out, PRODUCER, ["t0_1_1"])
    report = json.loads((out / "trees.json").read_text())
    assert report["trees"] == report["measured"] == 1
    tree = report["records"][0]
    assert tree["object_id"] == "GIS_Puut:t1:0" and tree["y"] == world.GROUND_Y
    assert 9 < tree["height"] < 12.5
    assert set(json.loads((out / "MANIFEST.json").read_text())["tiles"]) == {
        "t0_1_1.vis.glb",
        "c_4_4.col.npz",
    }
    assert tile_layer.stale(out, PRODUCER, ["t0_1_1", "t0_1_0"]) == ["t0_1_0"]
    result = tile_layer.run(out, PRODUCER, ["t0_1_1", "t0_1_0"])
    assert (
        result["built"] == ["t0_1_0"] and json.loads((out / "trees.json").read_text())["trees"] == 1
    )
