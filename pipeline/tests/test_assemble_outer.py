"""Outer building massing must survive both coarse skyline levels."""

import numpy as np
from test_assemble import fixture
from turku3d.layers import write_manifest, write_visual
from turku3d.package.assemble import assemble
from turku3d.package.gltf import GlbMesh, read_glb


def test_outer_building_massing_survives_coarse_parent_packaging(tmp_path, monkeypatch):
    monkeypatch.setenv("TURKU3D_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("TURKU3D_WORK", str(tmp_path / "work"))
    layers, out = tmp_path / "layers", tmp_path / "dist"
    fixture(layers)
    outer = layers / "buildings_outer"
    mesh = GlbMesh(
        "outer-house",
        np.array([[-20, 2, 10], [-10, 2, 10], [-20, 12, 10]], dtype=float),
        np.array([[0, 1, 2]], np.uint32),
        uvs=np.zeros((3, 2), np.float32),
        extras=dict(
            layer="buildings_outer",
            object_id="outer-house",
            building_id="outer-house",
            material_key="facade_plaster",
        ),
    )
    write_visual(outer / "t0_-1_0.vis.glb", [mesh])
    write_manifest(outer, inputs={}, params={}, code="test", tiles=["t0_-1_0.vis.glb"])
    assemble(["t0_0_0"], layers=layers, out=out, area="work-3x3")
    for level, tile in ((1, "t1_-1_0"), (2, "t2_-1_0")):
        doc, _ = read_glb((out / f"visual/{level}/{tile}.glb").read_bytes())
        identities = {
            item["object_id"]
            for node in doc["nodes"]
            if "mesh" in node
            for item in [node.get("extras", {}), *node.get("extras", {}).get("objects", [])]
            if "object_id" in item
        }
        assert "outer-house" in identities
