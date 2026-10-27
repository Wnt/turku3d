import json

import numpy as np
from turku3d import config as C
from turku3d.layers import read_collision, read_manifest, read_visual
from turku3d.package.assemble import assemble
from turku3d.package.gltf import read_glb
from turku3d.vegetation.__main__ import publish
from turku3d.vegetation.trees import Tree, deduplicate, measure, variant
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


def test_layer_instancing_collision_and_packaged_world_bounds(tmp_path, monkeypatch):
    monkeypatch.setenv("TURKU3D_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("TURKU3D_WORK", str(tmp_path / "work"))
    trees = [
        Tree("a", 251, 3, "GIS_Puut", y=4, height=12, crown_radius=3),
        Tree("b", 499, 240, "GIS_Puut", y=6, height=20, crown_radius=7),
    ]
    layer = tmp_path / "layers/vegetation"
    report = publish(trees, layer, {}, {})
    read_manifest(layer)
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
    assert entry["tris"] == report["tiles"]["t0_1_0"]["triangles_per_color_pass"]
    assert entry["max_error_m"] < 0.002
    assert entry["bounds"][0][0] < 251 and entry["bounds"][1][0] > 499
    assert abs(entry["bounds"][1][1] - 26) < 0.002
    packed, _ = read_glb((out / entry["url"]).read_bytes())
    assert "EXT_mesh_gpu_instancing" in packed["extensionsRequired"]
    assert sum("EXT_mesh_gpu_instancing" in n.get("extensions", {}) for n in packed["nodes"]) == 2
