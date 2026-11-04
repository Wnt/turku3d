"""Traffic-sign posts (props.signposts) and their migration (signs@1)."""

import hashlib
import json
import math
import struct

import numpy as np
import shapely
from turku3d.props import signposts as sp
from turku3d.props.models import model

S = sp.Sign


def test_a_code_recorded_twice_is_drawn_once_and_the_smaller_id_stays():
    posts, stats = sp.posts([S("b", "E1", 0, 0), S("a", "E1", 0.3, 0), S("c", "E1", 5, 0)])
    assert stats["sign_duplicate"] == 1
    assert sorted(p.plates[0].object_id for p in posts) == ["a", "c"]
    assert [m.object_id for p in posts for m in p.merged] == ["b"]


def test_plates_at_one_spot_share_a_post_main_signs_above_additional_plates():
    posts, stats = sp.posts([S("1", "H19.1", 0, 0), S("2", "C38", 0.02, 0), S("3", "B5", 3, 0)])
    stack = next(p for p in posts if len(p.plates) > 1)
    assert [s.code for s in stack.plates] == ["C38", "H19.1"] and stats["sign_stacked"] == 1
    assert (stack.x, stack.z) == (0.02, 0)


def test_a_lone_additional_plate_hangs_under_the_nearest_main_sign_within_reach():
    posts, _ = sp.posts([S("1", "H20", 0.9, 0), S("2", "C38", 0, 0), S("3", "H20", 9, 0)])
    assert sorted(len(p.plates) for p in posts) == [1, 2]
    # Separate main signs further than POST apart keep their own posts.
    posts, _ = sp.posts([S("1", "E1", 0, 0), S("2", "B5", 0.9, 0)])
    assert len(posts) == 2


def test_a_post_on_the_carriageway_moves_past_its_nearest_kerb():
    road = shapely.box(-50, -6, 50, 6)
    posts, stats = sp.posts([S("1", "C38", 0, 4.0), S("2", "E1", 0, 9)], road)
    moved = next(p for p in posts if p.rule)
    assert math.isclose(moved.z, 6 + 0.35) and stats["sign_post_kerb"] == 1
    posts, _ = sp.posts([S("1", "D3.1", 0, 4.0)], road)
    assert not posts[0].rule  # an island sign marks an island the register drew as carriageway
    posts, stats = sp.posts([S("1", "C38", 0, 0)], shapely.box(-50, -20, 50, 20))
    assert not posts[0].rule and stats["sign_post_deep"] == 1


def _batch_glb(rows):
    """A props batch like the packager's: one mesh, one index range per sign row (plain views)."""
    positions, normals, indices, objects = [], [], [], []
    for object_id, code, x, z, yaw in rows:
        mesh = model("sign", code)
        c, s = math.cos(yaw), math.sin(yaw)
        local = np.asarray(mesh.positions, float)
        world = np.c_[x + c * local[:, 0] + s * local[:, 2], local[:, 1], z - s * local[:, 0] + c * local[:, 2]]
        start = len(indices)
        indices.extend((np.asarray(mesh.indices).ravel() + len(positions)).tolist())
        positions.extend(world.tolist())
        normals.extend([[0, 0, 1]] * len(world))
        objects.append({"start": start, "count": len(indices) - start, "prop_type": "sign", "sign_code": code,
                        "object_id": object_id, "pole_base": [x, 0.0, z], "pole_id": f"t0_0_0:{object_id}"})
    pos = np.asarray(positions, "<f4").tobytes()
    nrm = np.asarray(normals, "<f4").tobytes()
    idx = np.asarray(indices, "<u4").tobytes()
    blob = pos + nrm + idx
    doc = {"asset": {"version": "2.0"}, "buffers": [{"byteLength": len(blob)}],
           "bufferViews": [{"buffer": 0, "byteOffset": 0, "byteLength": len(pos), "byteStride": 12},
                           {"buffer": 0, "byteOffset": len(pos), "byteLength": len(nrm), "byteStride": 12},
                           {"buffer": 0, "byteOffset": len(pos) + len(nrm), "byteLength": len(idx)}],
           "accessors": [{"bufferView": 0, "componentType": 5126, "count": len(positions), "type": "VEC3"},
                         {"bufferView": 1, "componentType": 5126, "count": len(positions), "type": "VEC3"},
                         {"bufferView": 2, "componentType": 5125, "count": len(indices), "type": "SCALAR"}],
           "meshes": [{"primitives": [{"attributes": {"POSITION": 0, "NORMAL": 1}, "indices": 2}]}],
           "nodes": [{"mesh": 0, "extras": {"layer": "props", "objects": objects}}]}
    text = json.dumps(doc).encode()
    text += b" " * (-len(text) % 4)
    return (struct.pack("<III", 0x46546C67, 2, 28 + len(text) + len(blob)) + struct.pack("<II", len(text), 0x4E4F534A)
            + text + struct.pack("<II", len(blob), 0x004E4942) + blob)


def test_the_migration_merges_stacks_drops_doubles_and_keeps_whole_ranges(tmp_path, monkeypatch):
    from turku3d.migrations import signs
    from turku3d.migrations.core import Product, glb_chunks
    from turku3d.migrations.furniture import read_accessor
    from turku3d.roads import signal_posts

    data = _batch_glb([("a", "C38", 10, 10, 0.0), ("b", "H19.1", 10.02, 10, 0.4), ("c", "E1", 30, 10, 0.0),
                       ("d", "E1", 30.2, 10, 0.0)])
    name = f"vis.{hashlib.sha256(data).hexdigest()}.glb"
    (tmp_path / name).write_bytes(data)
    product = Product(tmp_path, {"tile": "t0_0_0", "visual": {"t0_0_0": {"url": name, "sha256": "", "bytes": 0, "tris": 999}}})

    class Found:
        def wfs_source(self, name, tile):
            return False, None

    monkeypatch.setattr("turku3d.coarse.sources.Sources", Found)
    monkeypatch.setattr(signal_posts, "load_carriageway", lambda path, bbox: shapely.Polygon())
    signs.transform(product)
    entry = product.manifest["visual"]["t0_0_0"]
    doc, binary = glb_chunks(product.read(entry["url"]))
    rows = doc["nodes"][0]["extras"]["objects"]
    assert [r["object_id"] for r in rows] == ["a", "c"]
    assert rows[0]["plates"] == ["C38", "H19.1"] and rows[1]["merged"] == ["d"]
    pos = read_accessor(doc, binary, product, entry["url"], 0)
    idx = read_accessor(doc, binary, product, entry["url"], 2)[:, 0]
    assert len(idx) == sum(r["count"] for r in rows) and rows[1]["start"] == rows[0]["count"]
    stack = pos[np.unique(idx[: rows[0]["count"]])]
    # One pole at the post, the H plate a level below the C38 plate, both facing the post's way.
    assert np.allclose(stack[np.hypot(stack[:, 0] - 10, stack[:, 2] - 10) <= 0.04][:, [0, 2]].mean(0), [10, 10], atol=0.01)
    plates = stack[np.hypot(stack[:, 0] - 10, stack[:, 2] - 10) > 0.04]
    assert math.isclose(plates[:, 1].min(), 2.4 - 0.33 - sp.STACK, abs_tol=1e-3)
    assert np.allclose(plates[:, 2].min(), 10.035, atol=1e-3)  # every plate turned to yaw 0 (+z)
    assert entry["tris"] < 999 and product.manifest["furniture"]["signs"]["sign_duplicate"] == 1
