"""Offline tests for the Phase 0 packaging chain (turku3d.package + fixtures.ground), synthetic inputs only."""

import json

import numpy as np
import pytest
from fixtures import ground as G
from fixtures.stub_tile import CELLS_PER_COL, STEP, build, katualue_material
from turku3d import config as C
from turku3d.col import read_col
from turku3d.col.materials import mat_id
from turku3d.package import gltf
from turku3d.package.manifest import DistTree, build_tileset, verify_tree


def test_glb_roundtrip_and_root_translation():
    pos = np.array([[0, 0, 0], [1, 0, 0], [0, 0, 1]], np.float32)
    idx = np.array([[0, 2, 1]], np.uint32)
    jpeg = b"\xff\xd8" + b"\0" * 13
    m = gltf.GlbMesh(
        "g",
        pos,
        idx,
        uvs=np.zeros((3, 2), np.float32),
        image=jpeg,
        extras={"kind": "ground", "id": "g"},
    )
    data = gltf.build_glb(
        [m], root_translation=(250.0, 0.0, -250.0), root_extras={"tile": "t0_1_m1"}
    )
    js, binc = gltf.read_glb(data)
    assert len(data) % 4 == 0
    assert js["nodes"][0]["translation"] == [250.0, 0.0, -250.0]
    assert js["nodes"][0]["extras"] == {"tile": "t0_1_m1"}
    assert js["nodes"][1]["extras"]["kind"] == "ground"
    prim = js["meshes"][0]["primitives"][0]
    np.testing.assert_array_equal(
        gltf.accessor_array(js, binc, prim["attributes"]["POSITION"]), pos
    )
    np.testing.assert_array_equal(gltf.accessor_array(js, binc, prim["indices"]), idx.ravel())
    n = gltf.accessor_array(js, binc, prim["attributes"]["NORMAL"])
    np.testing.assert_allclose(
        n, [[0, 1, 0]] * 3, atol=1e-6
    )  # (0,0,0),(0,0,1),(1,0,0) is CCW from above
    assert js["images"][0]["mimeType"] == "image/jpeg"
    assert js["accessors"][prim["attributes"]["POSITION"]]["max"] == [1.0, 0.0, 1.0]


@pytest.mark.parametrize("method", ["scipy", "binned"])
def test_heightfield_reproduces_a_plane(method):
    if method == "scipy":
        pytest.importorskip("scipy")
    rng = np.random.default_rng(1)
    x = rng.uniform(-20, 90, 20000)
    z = rng.uniform(-20, 90, 20000)
    pts = np.stack([x, z, 3.0 + 0.02 * x - 0.01 * z], 1)
    H = G.heightfield(pts, 0.0, 0.0, 40, 40, 1.25, method=method)
    gx, gz = np.meshgrid(np.arange(41) * 1.25, np.arange(41) * 1.25)
    tol = (
        1e-6 if method == "scipy" else 0.05
    )  # binning snaps points up to half a cell (0.63 m) on the slope
    assert np.abs(H - (3.0 + 0.02 * gx - 0.01 * gz)).max() < tol


def test_grid_mesh_faces_up():
    pos, tris = G.grid_mesh(np.zeros((3, 3)), 0.0, 0.0, 1.0)
    p = pos[tris]
    n = np.cross(p[:, 1] - p[:, 0], p[:, 2] - p[:, 0])
    assert len(tris) == 8 and (n[:, 1] > 0).all()


def test_katualue_material_mapping():
    assert katualue_material("Ajorata", "Asfalttibetoni") == "road"
    assert katualue_material("Jalkakäytävä", "Betonilaatta") == "sidewalk"
    assert katualue_material("Välikaista", "Noppakivi") == "cobblestone"
    assert katualue_material("Muu liikennealue", "Sora") == "gravel"
    assert katualue_material("Odotustila", None) == "paving_slab"
    for name in (
        "road",
        "sidewalk",
        "cobblestone",
        "gravel",
        "paving_slab",
        "cycleway",
        "bridge_deck",
        "grass",
    ):
        assert mat_id(name) > 0


def test_stub_tile_chain_synthetic(tmp_path):
    out = tmp_path / "tiles"
    s = build("t0_0_0", out, synthetic=True, code="test")
    assert (
        s["collision_tiles"] == 16 and s["visual_tris"] == 2 * int(C.VISUAL_TILE_SIZE / STEP) ** 2
    )
    assert verify_tree(out) == []
    m = json.loads((out / "manifest.json").read_text())
    assert m["format"] == "turku3d-manifest" and m["origin_en"] == [
        C.ORIGIN_E,
        C.ORIGIN_N,
        C.ORIGIN_H,
    ]
    assert m["grid"] == {"visual_l0": 250.0, "collision": 62.5, "levels": 4}
    assert sorted(m["collision"]) == sorted(
        t.id for t in C.VisualTile.parse("t0_0_0").collision_tiles()
    )
    assert m["visual"]["t0_0_0"]["url"] == "visual/0/t0_0_0.glb"
    assert m["collision"]["c_2_3"]["url"] == "collision/t0_0_0/c_2_3.col"

    # .col tiles: valid, tile-local + origin, every triangle has a material, seams bitwise identical
    tiles = {cid: read_col(out / e["url"]) for cid, e in m["collision"].items()}
    for cid, t in tiles.items():
        assert t.id == cid and (t.materials > 0).all()
        assert t.origin == (t.tile_i * 62.5, 0.0, t.tile_j * 62.5)
        assert t.flags == m["collision"][cid]["flags"]
    a, b = tiles["c_1_1"].world_positions(), tiles["c_2_1"].world_positions()
    ea = {tuple(v) for v in a[np.isclose(a[:, 0], 125.0)]}
    eb = {tuple(v) for v in b[np.isclose(b[:, 0], 125.0)]}
    assert len(ea) == CELLS_PER_COL + 1 and ea == eb

    # visual GLB: root translation = tile origin, tile-local positions, same heights as collision
    js, binc = gltf.read_glb((out / "visual/0/t0_0_0.glb").read_bytes())
    assert js["nodes"][0]["translation"] == [0.0, 0.0, 0.0]
    assert js["nodes"][1]["extras"] == {
        "kind": "ground",
        "id": "ground_t0_0_0",
        "tile": "t0_0_0",
        "material": "ground",
    }
    vp = gltf.accessor_array(js, binc, js["meshes"][0]["primitives"][0]["attributes"]["POSITION"])
    assert vp[:, 0].min() == 0.0 and vp[:, 0].max() == 250.0
    probe = a[np.argmin(np.abs(a[:, 0] - 100.0) + np.abs(a[:, 2] - 100.0))]
    vh = vp[np.argmin(np.abs(vp[:, 0] - probe[0]) + np.abs(vp[:, 2] - probe[2]))]
    assert abs(float(vh[1]) - probe[1]) < 1e-5

    # tileset: flat, 3D Tiles frame (Z-up, Y = north)
    ts = json.loads((out / "tileset.json").read_text())
    assert ts["asset"]["version"] == "1.0"
    box = ts["root"]["children"][0]["boundingVolume"]["box"]
    assert box[:2] == [125.0, -125.0] and box[3] == 125.0 and box[7] == 125.0

    # a second tile is added to the same tree; the first is kept
    build("t0_1_0", out, synthetic=True, code="test")
    m2 = json.loads((out / "manifest.json").read_text())
    assert set(m2["visual"]) == {"t0_0_0", "t0_1_0"} and len(m2["collision"]) == 32
    assert verify_tree(out) == []
    assert len(json.loads((out / "tileset.json").read_text())["root"]["children"]) == 2


def test_stub_l0_seams_bitwise_identical(tmp_path):
    """Seams between collision tiles of DIFFERENT L0 tiles must match too (review 2026-10-25: per-tile
    point cropping gave up to 2.76 m steps across L0 seams on the real office-block build)."""
    pytest.importorskip("scipy")  # the numpy-only binned fallback fills empty nodes per tile
    out = tmp_path / "tiles"
    for t in ("t0_0_0", "t0_1_0", "t0_0_1"):
        build(t, out, synthetic=True, code="test")
    m = json.loads((out / "manifest.json").read_text())
    tiles = {cid: read_col(out / e["url"]).world_positions() for cid, e in m["collision"].items()}

    def edge(cid, axis, v):
        w = tiles[cid]
        return {tuple(p) for p in w[w[:, axis] == v]}

    for j in range(4):  # x = 250: t0_0_0 | t0_1_0
        a, b = edge(f"c_3_{j}", 0, 250.0), edge(f"c_4_{j}", 0, 250.0)
        assert len(a) == CELLS_PER_COL + 1 and a == b
    for i in range(4):  # z = 250: t0_0_0 | t0_0_1
        a, b = edge(f"c_{i}_3", 2, 250.0), edge(f"c_{i}_4", 2, 250.0)
        assert len(a) == CELLS_PER_COL + 1 and a == b


def test_tileset_root_box_covers_children():
    ts = build_tileset(
        {"t0_0_0": {"url": "a", "h": [0, 10]}, "t0_1_1": {"url": "b", "h": [2, 30]}}, "x"
    )
    box = ts["root"]["boundingVolume"]["box"]
    assert box[:3] == [250.0, -250.0, 15.0] and box[3] == 250.0 and box[11] == 15.0


def test_dist_tree_rejects_foreign_manifest(tmp_path):
    (tmp_path / "manifest.json").write_text('{"format": "other", "version": 1}')
    with pytest.raises(ValueError):
        DistTree(tmp_path)


def test_all_64_collision_seams_and_aoi_flags(tmp_path):
    from turku3d.col.format import FLAG_AOI_BORDER
    from turku3d.package.seams import verify_seams

    out = tmp_path / "tiles"
    for t in C.OFFICE_BLOCK.visual_tiles:
        build(t.id, out, synthetic=True, code="test")
    assert verify_seams(out) == []
    m = json.loads((out / "manifest.json").read_text())
    assert len(m["collision"]) == 64
    for cid, e in m["collision"].items():
        c = C.CollisionTile.parse(cid)
        decoded = read_col(out / e["url"])
        assert decoded.flags == e["flags"]
        assert bool(e["flags"] & FLAG_AOI_BORDER) == (c.i in (0, 7) or c.j in (0, 7))
    from turku3d.col.format import write_col

    path = out / m["collision"]["c_3_3"]["url"]
    broken = read_col(path)
    broken.positions[0, 1] += 1
    path.write_bytes(write_col(broken, encoding="raw"))
    assert verify_seams(out)  # a real seam step must fail the validator


def test_real_input_grid_is_shared_cached_and_invalidated(tmp_path, monkeypatch):
    monkeypatch.setenv("TURKU3D_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("TURKU3D_WORK", str(tmp_path / "work"))
    area = C.Area("test", "test", C.BBoxLocal(0, 0, 500, 500), margin=0)
    src = C.raw_dir("laz") / "maanpinta/23459500_6704500.laz"
    src.parent.mkdir(parents=True)
    src.write_bytes(b"first source identity")
    calls = []

    def load(bb, margin):
        calls.append(bb)
        return np.array([[-50, -50, 0], [-50, 550, 1], [550, -50, 2], [550, 550, 3]], dtype=float)

    monkeypatch.setattr(G, "load_ground_points", load)
    H, bb, count = G.area_heightfield(area)
    again, _, _ = G.area_heightfield(area)
    assert len(calls) == 1 and H.tobytes() == again.tobytes() and count == 4
    src.write_bytes(b"changed source identity")
    G.area_heightfield(area)
    assert len(calls) == 2
    assert not (tmp_path / "data/cache").exists()


def test_nullable_wfs_materials():
    import pandas as pd

    assert katualue_material(pd.NA, pd.NA) == "paving_slab"
    assert katualue_material("Ajorata", pd.NA) == "road"
