"""Per-tile layer builds: keys per tile, rebuild only stale tiles, strict file ownership."""

import json

import numpy as np
import pytest
from turku3d import config as C
from turku3d import tile_layer
from turku3d.layers import PRIMITIVE_DTYPE, CollisionLayer, read_manifest, write_collision

BUILT = []


def source(tile):
    return json.loads((C.work_root() / "source.json").read_text()).get(tile.id, 0)


def key(tile):
    return {"value": source(tile)}


def build(tile, staging):
    BUILT.append(tile.id)
    value = source(tile)
    cell = tile.collision_tiles()[0]
    primitive = np.zeros(1, PRIMITIVE_DTYPE)
    primitive["kind"], primitive["material"] = "cylinder", 25
    primitive["center"] = [*cell.origin[:1], value, cell.origin[2]]
    primitive["radius"] = primitive["height"] = 1
    empty = (np.empty((0, 3)), np.empty((0, 3), np.uint32), np.empty(0, np.uint8))
    write_collision(staging / f"{cell.id}.col.npz", CollisionLayer(*empty, primitive))
    return {"records": [{"tile": tile.id, "value": value}], "metrics": {"objects": 1}}


def combine(directory, tiles):
    rows = [r for entry in tiles.values() for r in entry["records"]]
    (directory / "all.json").write_text(json.dumps(rows))
    return {"objects": sum(e["metrics"]["objects"] for e in tiles.values())}


def trespass(tile, staging):
    (staging / "c_99_99.col.npz").write_bytes(b"")
    return {}


def code():
    return "code-1"


PRODUCER = tile_layer.Producer("synthetic", code, key, build, combine)


@pytest.fixture
def work(tmp_path, monkeypatch):
    monkeypatch.setenv("TURKU3D_WORK", str(tmp_path / "work"))
    monkeypatch.setenv("TURKU3D_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("TURKU3D_JOBS", "1")  # BUILT records builds in this process
    (tmp_path / "work").mkdir()
    BUILT.clear()
    return tmp_path / "work"


def values(work, **tiles):
    (work / "source.json").write_text(json.dumps(tiles))


def test_only_stale_tiles_rebuild_and_unlisted_tiles_leave(work):
    out = work / "build/layers/synthetic"
    values(work, t0_0_0=1, t0_1_0=2)
    result = tile_layer.run(out, PRODUCER, ["t0_0_0", "t0_1_0"])
    assert result["built"] == BUILT == ["t0_0_0", "t0_1_0"]
    manifest = read_manifest(out)
    assert set(manifest["tiles"]) == {"c_0_0.col.npz", "c_4_0.col.npz"}
    assert manifest["params"] == {
        "objects": 2,
        "seams": "objects",
        "visual_tiles": ["t0_0_0", "t0_1_0"],
    }
    assert set(manifest["tile_builds"]) == {"t0_0_0", "t0_1_0"}
    assert json.loads((out / "all.json").read_text())[1] == {"tile": "t0_1_0", "value": 2}
    assert tile_layer.stale(out, PRODUCER, ["t0_0_0", "t0_1_0"]) == []
    # Adding a tile builds only that tile; a changed input rebuilds only its tile.
    BUILT.clear()
    values(work, t0_0_0=1, t0_1_0=2, t0_0_1=3)
    assert tile_layer.stale(out, PRODUCER, ["t0_0_0", "t0_1_0", "t0_0_1"]) == ["t0_0_1"]
    tile_layer.run(out, PRODUCER, ["t0_0_0", "t0_1_0", "t0_0_1"])
    assert BUILT == ["t0_0_1"]
    BUILT.clear()
    values(work, t0_0_0=5, t0_1_0=2, t0_0_1=3)
    tile_layer.run(out, PRODUCER, ["t0_0_0", "t0_1_0", "t0_0_1"])
    assert BUILT == ["t0_0_0"]
    # A deleted file is repaired; a tile that left the list loses its files.
    (out / "c_4_0.col.npz").unlink()
    assert tile_layer.stale(out, PRODUCER, ["t0_0_0", "t0_1_0", "t0_0_1"]) == ["t0_1_0"]
    assert tile_layer.stale(out, PRODUCER, ["t0_0_0", "t0_0_1"]) == ["unlisted"]
    tile_layer.run(out, PRODUCER, ["t0_0_0", "t0_0_1"])
    assert set(read_manifest(out)["tiles"]) == {"c_0_0.col.npz", "c_0_4.col.npz"}
    assert not (out / "t0_1_0.records.json").exists()


def test_tiles_build_in_a_process_pool(work):
    out = work / "build/layers/synthetic"
    values(work, t0_0_0=1, t0_1_0=2, t0_0_1=3)
    result = tile_layer.run(out, PRODUCER, ["t0_0_0", "t0_1_0", "t0_0_1"], jobs=3)
    assert result["built"] == ["t0_0_0", "t0_0_1", "t0_1_0"] and BUILT == []
    assert [r["value"] for r in json.loads((out / "all.json").read_text())] == [1, 3, 2]


def test_code_change_rebuilds_every_tile(work):
    out = work / "build/layers/synthetic"
    values(work, t0_0_0=1)
    tile_layer.run(out, PRODUCER, ["t0_0_0"])
    changed = tile_layer.Producer("synthetic", lambda: "code-2", key, build, combine)
    assert tile_layer.stale(out, changed, ["t0_0_0"]) == ["t0_0_0"]


def test_a_build_may_write_only_its_own_tile(work):
    values(work)
    producer = tile_layer.Producer("synthetic", lambda: "code", key, trespass)
    with pytest.raises(ValueError, match="does not own"):
        tile_layer.run(work / "build/layers/synthetic", producer, ["t0_0_0"])


def test_tile_inputs_come_from_the_tiles_source_area(work):
    inside, outside = C.VisualTile.parse("t0_0_0"), C.VisualTile.parse("t0_5_0")
    assert tile_layer.wfs(inside, "GIS_Puut").parent.name == "work-3x3"
    assert tile_layer.wfs(outside, "GIS_Puut").parent.name == "tile-t0_5_0"
    with pytest.raises(FileNotFoundError, match="tile-t0_5_0"):
        tile_layer.osm(outside)
    assert tile_layer.owned(inside, "t0_0_0.vis.glb") and tile_layer.owned(inside, "c_3_3.col.npz")
    assert not tile_layer.owned(inside, "c_4_0.col.npz")
