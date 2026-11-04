"""Dutch source adapters (turku3d.nl): vocabulary maps, AHN sub-tile grid, key-point thinning, 3DBAG rows."""

import gzip
import json

import numpy as np
import pytest

from turku3d.nl import convert, fetch


def test_bgt_vocabulary_maps_to_register_values():
    assert convert.kind("rijbaan lokale weg") == ("Ajorata", "1")
    assert convert.kind("fietspad") == ("Pyörätie", "3")
    assert convert.kind("voetpad op trap") == ("Portaat", "8")
    assert convert.kind("onbekend") == ("Muu", "6")
    assert convert.surface("open verharding", "gebakken klinkers") == ("Maatiili", "21")
    assert convert.surface("gesloten verharding", None) == ("Asfalttibetoni", "1")
    assert convert.surface("open verharding", "betonstraatstenen") == ("Betonikivi", "9")
    assert convert.surface(None, None) == ("Ei tietoa", "0")


def test_brick_paving_gets_the_brick_finish():
    from turku3d.coarse.street_materials import BRICK, street_finish

    assert street_finish("Maatiili") == BRICK


def test_ahn_subtiles_follow_the_sheet_grid(tmp_path):
    index = tmp_path / "index.json"
    ring = [[120000, 481250], [120000, 487500], [125000, 487500], [125000, 481250], [120000, 481250]]
    index.write_text(json.dumps({"features": [
        {"properties": {"kaartbladNr": "M_25GN1"}, "geometry": {"coordinates": [ring]}}]}))
    tiles = dict(fetch.ahn_subtiles(fetch.Area((120500, 487000, 121500, 487400)), index))
    # GeoTiles 25GN1_01 is x 120000-121000, y 486250-487500 (its lasinfo bounds minus the 20 m buffer).
    assert tiles["25GN1_01"] == (120000, 486250, 121000, 487500)
    assert set(tiles) == {"25GN1_01", "25GN1_02"}


def test_circle_area_skips_corner_blocks():
    area = fetch.Area((-1000, -1000, 1000, 1000), (0, 0), 600)
    blocks = list(area.blocks((0, 0)))
    assert len(blocks) == 12  # 4 x 4 blocks of 500 m minus the 4 corners outside the circle


def test_lowest_return_per_cell():
    xyz = np.array([[0.1, 0.1, 5.0], [0.2, 0.3, 1.0], [2.0, 0.1, 3.0]])
    out = convert._lowest_per_cell(xyz, 1.5)
    assert sorted(out[:, 2].tolist()) == [1.0, 3.0]


@pytest.fixture
def bag_tile(tmp_path, monkeypatch):
    cube = [[0, 0, 0], [10000, 0, 0], [10000, 10000, 0], [0, 10000, 0],
            [0, 0, 9000], [10000, 0, 9000], [10000, 10000, 9000], [0, 10000, 9000]]
    shell = [[[0, 3, 2, 1]], [[4, 5, 6, 7]], [[0, 1, 5, 4]], [[1, 2, 6, 5]], [[2, 3, 7, 6]], [[3, 0, 4, 7]]]
    cj = {
        "transform": {"scale": [0.001, 0.001, 0.001], "translate": [121000, 487000, 1.0]},
        "vertices": cube,
        "CityObjects": {
            "NL.IMBAG.Pand.1": {"type": "Building", "children": ["NL.IMBAG.Pand.1-0"],
                                "attributes": {"b3_bouwlagen": 3, "oorspronkelijkbouwjaar": 1650,
                                               "b3_h_maaiveld": 1.0, "b3_h_dak_70p": 10.0}},
            "NL.IMBAG.Pand.1-0": {"type": "BuildingPart", "parents": ["NL.IMBAG.Pand.1"], "geometry": [
                {"lod": "2.2", "type": "Solid", "boundaries": [shell],
                 "semantics": {"surfaces": [{"type": "GroundSurface"}, {"type": "RoofSurface"},
                                            {"type": "WallSurface"}],
                               "values": [[0, 1, 2, 2, 2, 2]]}}]},
        },
    }
    path = tmp_path / "t.city.json.gz"
    path.write_bytes(gzip.compress(json.dumps(cj).encode()))
    monkeypatch.setattr(convert, "_ensure", lambda *a, **k: None)
    monkeypatch.setattr(convert, "_files", lambda *a, **k: [path])
    return path


def test_3dbag_rows_carry_semantic_surfaces(bag_tile):
    rows = convert.buildings_3dbag((120990, 486990, 121020, 487020))
    kinds = sorted(r["surface_type"] for r in rows)
    assert kinds == ["GroundSurface", "RoofSurface"] + ["WallSurface"] * 4
    ground = next(r for r in rows if r["surface_type"] == "GroundSurface")
    assert ground["geometry"].area == pytest.approx(100.0)
    assert ground["geometry"].exterior.coords[0][2] == pytest.approx(1.0)
    assert ground["storeysAboveGround"] == 3 and ground["measuredHeight"] == 9.0
    assert convert.buildings_3dbag((0, 0, 10, 10)) == []  # footprint outside the window
