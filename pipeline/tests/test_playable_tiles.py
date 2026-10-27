"""The playable area is a data file of L0 tile ids ($TURKU3D_DATA/playable-tiles.json)."""

import json

import pytest
from turku3d import config as C


@pytest.fixture
def roots(tmp_path, monkeypatch):
    data, work = tmp_path / "data", tmp_path / "work"
    monkeypatch.setenv("TURKU3D_DATA", str(data))
    monkeypatch.setenv("TURKU3D_WORK", str(work))
    return data, work


def test_tile_id_and_bbox_round_trip():
    east = C.VisualTile.parse("t0_2_0")
    assert east.bbox.to_en().as_tuple() == (23460000.0, 6704250.0, 23460250.0, 6704500.0)
    x, _, z = C.en_to_local(23460100.0, 6704254.0)
    assert C.VisualTile.at(x, z).id == "t0_2_0"
    x, _, z = C.en_to_local(23459900.0, 6704254.0)
    assert C.VisualTile.at(x, z).id == "t0_1_0"  # inside the core already
    for tile_id in ("t0_-1_3", "t0_7_-2"):
        tile = C.VisualTile.parse(tile_id)
        b = tile.bbox
        assert C.VisualTile.at((b.xmin + b.xmax) / 2, (b.zmin + b.zmax) / 2).id == tile_id


def test_missing_list_migrates_to_the_four_core_tiles(roots):
    data, _ = roots
    assert C.playable_tile_ids() == list(C.CORE_TILES)
    record = json.loads((data / C.PLAYABLE_TILES_FILE).read_text())
    assert record == {
        "level": 0,
        "tiles": list(C.CORE_TILES),
        "core_bbox_en": [23459500.0, 6704000.0, 23460000.0, 6704500.0],
    }
    area = C.area("playable-tiles")
    assert [t.id for t in area.visual_tiles] == list(C.CORE_TILES)
    assert area.core.as_tuple() == (0, 0, 500, 500)
    assert len(area.collision_tiles) == 64


def test_sandbox_list_shadows_the_data_root_list(roots):
    data, work = roots
    C.write_playable_tiles(data / C.PLAYABLE_TILES_FILE, C.CORE_TILES)
    C.write_playable_tiles(work / C.PLAYABLE_TILES_FILE, [*C.CORE_TILES, "t0_2_0"])
    area = C.playable_area()
    assert [t.id for t in area.visual_tiles][-1] == "t0_2_0"
    assert area.core.as_tuple() == (0, 0, 750, 500)
    # Collision tiles of the L-shaped union only, not of its bounding box.
    assert len(area.collision_tiles) == 80
    assert C.playable_tile_ids(data) == list(C.CORE_TILES)


@pytest.mark.parametrize(
    "record, message",
    [
        ({"level": 1, "tiles": list(C.CORE_TILES)}, "level"),
        ({"level": 0, "tiles": [*C.CORE_TILES, "t0_0_0"]}, "unique"),
        ({"level": 0, "tiles": [*C.CORE_TILES, "t1_1_0"]}, "unique L0"),
        ({"level": 0, "tiles": ["t0_0_0", "t0_2_0"]}, "core tiles"),
    ],
)
def test_invalid_lists_are_rejected(tmp_path, record, message):
    path = tmp_path / C.PLAYABLE_TILES_FILE
    path.write_text(json.dumps(record))
    with pytest.raises(ValueError, match=message):
        C.read_playable_tiles(path)
