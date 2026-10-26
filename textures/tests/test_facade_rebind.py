import json

import numpy as np
from turkutex.facade_rebind import match_score, migrate, reproject_uv, sha
from turkutex.streetview.facades import Facade, save_facades


def wall(name, start, end, *, base=3, height=10, building="a"):
    return Facade(
        name,
        np.array(start, dtype=float),
        np.array(end, dtype=float),
        height,
        base,
        {"building_id": building, "facade_index": 0},
    )


def test_split_and_shift_use_same_source_photo_coordinates():
    old = wall("a_0", [0, 0], [20, 0])
    split = wall("a_9", [5, 0.2], [12, 0.2], base=3.2, height=9.8)
    assert match_score(old, split) is not None
    uv = reproject_uv({"uv": {"scale": [0.05, -0.1], "offset": [0, 1]}}, old, split)
    assert np.allclose(uv["scale"], [0.05, -0.1 * 10 / 9.8])
    assert np.allclose(uv["offset"], [0.25, 1])


def test_different_wall_or_building_is_not_reused():
    old = wall("a_0", [0, 0], [20, 0])
    assert match_score(old, wall("b_0", [0, 0], [20, 0], building="b")) is None
    assert match_score(old, wall("a_1", [0, 2], [20, 2])) is None
    assert match_score(old, wall("a_2", [20, 0], [0, 0])) is None
    assert match_score(old, wall("a_3", [18, 0], [28, 0])) is None


def test_migrate_split_reuses_verified_maps_and_updates_manifest(tmp_path):
    original = wall("a_0", [0, 0], [20, 0])
    rebuilt = [wall("a_5", [0, 0], [10, 0]), wall("a_6", [10, 0], [20, 0])]
    for index, facade in enumerate(rebuilt, start=5):
        facade.props["facade_index"] = index
    old_inventory, new_inventory = tmp_path / "old.geojson", tmp_path / "new.geojson"
    save_facades([original], old_inventory)
    save_facades(rebuilt, new_inventory)
    assembly = tmp_path / "assembly"
    directory = assembly / "facades/a"
    directory.mkdir(parents=True)
    files = {"albedo": "0.ktx2", "normal": "0.normal.ktx2", "orm": "0.orm.ktx2"}
    for name in files.values():
        (directory / name).write_bytes(name.encode())
    (directory / "0.json").write_text(
        json.dumps(
            {
                "uv": {"scale": [0.05, -0.1], "offset": [0, 1]},
                "files": files,
                "hashes": {name: sha(directory / name) for name in files.values()},
            }
        )
    )
    (assembly / "MANIFEST.json").write_text(
        json.dumps({"bound": ["a_0"], "inventory_sha256": sha(old_inventory)})
    )
    result = migrate(old_inventory, new_inventory, assembly)
    assert (result["kept"], result["reprojected"], result["new"], result["failed"]) == (0, 2, 0, 0)
    assert json.loads((assembly / "MANIFEST.json").read_text())["bound"] == ["a_5", "a_6"]
    assert json.loads((directory / "6.json").read_text())["uv"]["offset"][0] == 0.5
    assert not (directory / "0.json").exists()


def test_unmatched_approval_is_archived_for_later_review(tmp_path):
    original = wall("a_0", [0, 0], [20, 0])
    replacement = wall("a_7", [0, 3], [20, 3])
    replacement.props.update(facade_index=7, street_facing=True)
    old_inventory, new_inventory = tmp_path / "old.geojson", tmp_path / "new.geojson"
    save_facades([original], old_inventory)
    save_facades([replacement], new_inventory)
    assembly = tmp_path / "assembly"
    directory = assembly / "facades/a"
    directory.mkdir(parents=True)
    (directory / "0.ktx2").write_bytes(b"approved")
    (directory / "0.json").write_text(
        json.dumps(
            {
                "uv": {"scale": [0.05, -0.1], "offset": [0, 1]},
                "files": {"albedo": "0.ktx2"},
                "hashes": {"0.ktx2": sha(directory / "0.ktx2")},
            }
        )
    )
    (assembly / "MANIFEST.json").write_text(
        json.dumps({"bound": ["a_0"], "inventory_sha256": sha(old_inventory)})
    )
    result = migrate(old_inventory, new_inventory, assembly)
    assert result["failed_ids"] == ["a_0"]
    assert result["new_ids"] == ["a_7"]
    assert (assembly / "retired" / sha(old_inventory)[:16] / "a/0.ktx2").read_bytes() == b"approved"
