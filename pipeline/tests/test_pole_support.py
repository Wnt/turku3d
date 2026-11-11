"""Final packed posts and runtime bases follow collision after every terrain migration."""

import hashlib

import numpy as np
import pytest
from test_signposts import _batch_glb
from turku3d.col.format import ColTile, write_col
from turku3d.col.materials import mat_id
from turku3d.migrations.core import Product, glb_chunks, glb_pack
from turku3d.migrations.furniture import read_accessor
from turku3d.migrations.pole_support import support_sampler, supported_base, transform


def plane(height=3, xmin=0, xmax=60, material="sidewalk"):
    pos = np.array(
        [[xmin, height, 0], [xmax, height, 0], [xmin, height, 60], [xmax, height, 60]], float
    )
    return pos, np.array([[0, 2, 1], [1, 2, 3]], np.uint32), np.full(2, mat_id(material), np.uint8)


def product(tmp_path, planes=(), rows=None, external=False, stack=False, bus_stop=False):
    data = _batch_glb(rows or [("one", "E1", 10, 10, 0)])
    doc, binary = glb_chunks(data)
    if stack:
        doc["nodes"][0]["extras"]["objects"][0]["plates"] = ["E1", "H19.1"]
    if bus_stop:
        doc["nodes"][0]["extras"]["objects"][0]["prop_type"] = "bus_stop"
    if external:
        (tmp_path / "source.bin").write_bytes(binary)
        doc["buffers"][0]["uri"] = "source.bin"
        binary = b""
    visual = glb_pack(doc, binary)
    digest = hashlib.sha256(visual).hexdigest()
    name = f"vis.{digest}.glb"
    (tmp_path / name).write_bytes(visual)
    collision = {}
    if planes:
        pos, idx, mats, offset = [], [], [], 0
        for p, i, m in planes:
            pos.append(p)
            idx.append(i + offset)
            mats.append(m)
            offset += len(p)
        col = ColTile.for_tile(0, 0, np.concatenate(pos), np.concatenate(idx), np.concatenate(mats))
        data = write_col(col, encoding="raw", optimize=False)
        (tmp_path / "ground.col").write_bytes(data)
        collision["c_0_0"] = {"url": "ground.col"}
    return Product(
        tmp_path,
        {
            "tile": "t0_0_0",
            "collision": collision,
            "visual": {
                "t0_0_0": {
                    "url": name,
                    "sha256": digest,
                    "bytes": len(visual),
                }
            },
        },
    )


@pytest.mark.parametrize("external", [False, True])
def test_final_terrain_height_updates_geometry_runtime_base_and_preserves_stacked_ids(
    tmp_path, external
):
    p = product(tmp_path, [plane(4.2)], external=external, stack=True)
    collision_before = (tmp_path / "ground.col").read_bytes()
    transform(p)
    entry = p.manifest["visual"]["t0_0_0"]
    doc, binary = glb_chunks(p.read(entry["url"]))
    node = doc["nodes"][0]
    row = node["extras"]["objects"][0]
    assert row["pole_base"] == pytest.approx([10, 4.2, 10])
    assert row["pole_id"] == "t0_0_0:one" and row["plates"] == ["E1", "H19.1"]
    primitive = doc["meshes"][node["mesh"]]["primitives"][0]
    positions = read_accessor(doc, binary, p, entry["url"], primitive["attributes"]["POSITION"])
    assert positions[:, 1].min() == pytest.approx(4.2)
    assert (tmp_path / "ground.col").read_bytes() == collision_before
    stats = p.manifest["furniture"]["pole_support"]
    assert (stats["source"], stats["placed"], stats["shifted"], stats["omitted"]) == (1, 1, 1, 0)


def test_absent_support_removes_visible_and_runtime_post_without_empty_accessor(tmp_path):
    p = product(tmp_path, [plane(xmin=20)])
    transform(p)
    doc, _ = glb_chunks(p.read(p.manifest["visual"]["t0_0_0"]["url"]))
    assert doc["nodes"][0]["extras"]["objects"] == []
    assert "mesh" not in doc["nodes"][0]
    assert all(accessor["count"] > 0 for accessor in doc["accessors"])
    assert p.manifest["furniture"]["pole_support"]["reasons"] == {"missing_support": 1}


def test_only_unsupported_range_is_removed_while_supported_bus_stop_survives(tmp_path):
    p = product(
        tmp_path,
        [plane(xmin=0, xmax=20)],
        rows=[("a", "E1", 10, 10, 0), ("b", "E1", 30, 10, 0)],
        bus_stop=True,
    )
    transform(p)
    entry = p.manifest["visual"]["t0_0_0"]
    doc, binary = glb_chunks(p.read(entry["url"]))
    rows = doc["nodes"][0]["extras"]["objects"]
    assert len(rows) == 1 and rows[0]["pole_id"] == "t0_0_0:a"
    assert rows[0]["prop_type"] == "bus_stop"
    idx = read_accessor(doc, binary, p, entry["url"], 2)
    assert len(idx) == rows[0]["count"] and rows[0]["start"] == 0


def test_bridge_support_above_water_is_valid_but_submerged_riverbed_is_not(tmp_path):
    p = product(
        tmp_path,
        [
            plane(-3, material="soil"),
            plane(0, material="water"),
            plane(4, xmin=20, material="bridge_deck"),
        ],
    )
    sample = support_sampler(p)
    assert supported_base(sample, [10, 0, 10]) == (None, "water")
    assert supported_base(sample, [30, 3, 10]) == (4, None)


def test_lower_road_retains_its_level_under_bridge_and_roofs_do_not_support_poles(tmp_path):
    p = product(
        tmp_path, [plane(2), plane(8, material="bridge_deck"), plane(9, material="building")]
    )
    sample = support_sampler(p)
    assert supported_base(sample, [10, 1, 10]) == (2, None)


def test_whole_runtime_cylinder_base_requires_support_and_cannot_straddle_a_curb(tmp_path):
    p = product(tmp_path, [plane(2, xmax=10), plane(2.12, xmin=10)])
    assert supported_base(support_sampler(p), [10, 2, 10]) == (None, "uneven_support")


def test_support_migration_is_last_after_finished_terrain_and_sign_stacking():
    from turku3d.migrations.registry import CHAIN

    assert CHAIN[-1].key == "pole-support@1"
    assert next(i for i, m in enumerate(CHAIN) if m.name == "signs") < len(CHAIN) - 1
    assert next(i for i, m in enumerate(CHAIN) if m.name == "finished-ground") < len(CHAIN) - 1


def test_unavailable_neighbour_is_unresolved_and_does_not_delete_or_move_post(tmp_path):
    p = product(tmp_path, [plane(xmax=62.5)], rows=[("seam", "E1", 62.49, 10, 0)])
    before = p.manifest["visual"]["t0_0_0"]["url"]
    transform(p)
    assert p.manifest["visual"]["t0_0_0"]["url"] == before
    stats = p.manifest["furniture"]["pole_support"]
    assert (stats["placed"], stats["unresolved"], stats["omitted"]) == (1, 1, 0)
