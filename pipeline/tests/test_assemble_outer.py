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


def test_massing_ring_is_built_per_tile_from_lod2(tmp_path, monkeypatch):
    import json

    import producer_world as world
    from shapely.geometry import box
    from turku3d import config as C
    from turku3d import tile_layer
    from turku3d.buildings.outer import PRODUCER, ring
    from turku3d.layers import layer_root, read_manifest, read_visual

    monkeypatch.setenv("TURKU3D_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("TURKU3D_WORK", str(tmp_path / "work"))
    monkeypatch.setenv("TURKU3D_JOBS", "1")
    assert len(ring(C.CORE_TILES)) == 32  # work-3x3 around the core
    world.lod2(
        [
            ("far", box(600, 20, 620, 40), 2.0, 14.0),  # t0_2_0
            ("edge", box(500.5, 100, 510, 110), 2.0, 9.0),  # t0_2_0, 0.5 m from listed t0_1_0
            ("listed", box(300, 300, 320, 320), 2.0, 9.0),  # inside listed t0_1_1
        ]
    )
    out = layer_root() / "buildings_outer"
    tiles = ring(C.CORE_TILES)
    tile_layer.run(out, PRODUCER, tiles)
    metrics = json.loads((out / "metrics.json").read_text())
    assert metrics["buildings"] == 1 and metrics["by_tile"]["t0_2_0"] == 1
    assert list(read_manifest(out)["tiles"]) == ["t0_2_0.vis.glb"]
    doc, blob = read_glb(read_visual(out / "t0_2_0.vis.glb"))
    assert doc["nodes"][1]["extras"]["object_id"] == "outer-lod2-far"
    # Listing t0_2_0 drops its massing; the ring grows east and only changed tiles rebuild.
    listed = [*C.CORE_TILES, "t0_2_0"]
    C.write_playable_tiles(tmp_path / "work" / C.PLAYABLE_TILES_FILE, listed)
    grown = ring(C.playable_tile_ids())
    stale = tile_layer.stale(out, PRODUCER, grown)
    assert "unlisted" in stale and "t0_4_0" in stale and "t0_3_0" in stale
    assert "t0_-2_-2" not in stale
    tile_layer.run(out, PRODUCER, grown)
    metrics = json.loads((out / "metrics.json").read_text())
    assert metrics["buildings"] == 0
    # East of work-3x3 the ring has no LOD2 yet; add-tile reads it through (fetch_missing).
    assert metrics["missing_lod2"] == sorted(f"t0_4_{j}" for j in range(-2, 3))
