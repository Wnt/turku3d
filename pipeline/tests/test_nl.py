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


def test_generic_paving_follows_dutch_convention():
    assert convert.appearance("rijbaan lokale weg", "open verharding", None) == ("Maatiili", "21")
    assert convert.appearance("voetpad", "open verharding", None) == ("Betonilaatta", "8")
    assert convert.appearance("parkeervlak", "open verharding", None) == ("Betonikivi", "9")
    assert convert.appearance("fietspad", "gesloten verharding", None)[1] == "20"  # red asphalt
    # An explicit BGT appearance wins over OSM, OSM over the convention.
    sett = convert.OSM_SURFACE["sett"]
    assert convert.appearance("rijbaan lokale weg", "open verharding", "betonstraatstenen", sett) == ("Betonikivi", "9")
    assert convert.appearance("rijbaan lokale weg", "open verharding", None, sett) == ("Noppakivi", "6")
    assert convert.appearance("rijbaan lokale weg", "asfalt", None, sett) == ("Asfalttibetoni", "1")


def test_bgt_points_become_register_trees_and_lamps(monkeypatch):
    import shapely

    feats = [("vegetatieobject_punt", "t1", {"plus_type": "boom"}, shapely.Point(1, 2)),
             ("vegetatieobject_punt", "t2", {"plus_type": "haag"}, shapely.Point(3, 4)),
             ("paal", "p1", {"plus_type": "lichtmast"}, shapely.Point(5, 6))]
    monkeypatch.setattr(convert, "bgt_features", lambda cols, bbox, log: [f for f in feats if f[0] in cols])
    trees = convert.trees_bgt((0, 0, 10, 10))
    lamps = convert.lamps_bgt((0, 0, 10, 10))
    assert [t["feature_key"] for t in trees] == ["t1"] and trees[0]["layer"] == "GIS:Puut"
    assert [l["feature_key"] for l in lamps] == ["p1"] and lamps[0]["Tasonimi"].startswith("rr:js")


def test_ahn_unclassified_splits_into_vegetation_and_the_rest():
    """AHN class 1: facades, boats, vehicles and wires stay 1; the crown becomes vegetation by height."""
    from turku3d.nl import lidar

    rng = np.random.default_rng(3)
    parts = []

    def add(xyz, cls, hag, street=False, water=False):
        parts.append((np.asarray(xyz, float), cls, np.asarray(hag, float), street, water))

    gx, gy = np.meshgrid(np.arange(0, 40, 0.5), np.arange(0, 40, 0.5))
    add(np.c_[gx.ravel(), gy.ravel(), np.zeros(gx.size)], 2, np.zeros(gx.size))
    add([[1.0, 1.0, 2.0]], 26, [2.0])
    rx, ry = np.meshgrid(np.arange(30, 40, 0.5), np.arange(0, 10, 0.5))  # roof at 12 m
    add(np.c_[rx.ravel(), ry.ravel(), np.full(rx.size, 12.0)], 6, np.full(rx.size, 12.0))
    fz = np.arange(1, 11, 0.5)
    facade = np.c_[np.full(fz.size, 29.7), np.full(fz.size, 5.0), fz]
    add(facade, 1, fz)
    crown = np.c_[rng.normal(10, 1.5, 400), rng.normal(10, 1.5, 400), rng.uniform(4, 9, 400)]
    add(crown, 1, crown[:, 2])
    boat = np.c_[rng.uniform(5, 9, 50), rng.uniform(30, 32, 50), np.full(50, 1.5)]
    add(boat, 1, boat[:, 2], water=True)
    car = np.c_[rng.uniform(20, 24, 50), rng.uniform(30, 32, 50), np.full(50, 1.4)]
    add(car, 1, car[:, 2], street=True)
    s = np.arange(0, 30, 0.15)
    wire = np.c_[2 + s, np.full(s.size, 20.0), 6.5 + rng.normal(0, 0.01, s.size)]
    add(wire, 1, wire[:, 2])
    xyz = np.concatenate([p[0] for p in parts])
    cls = np.concatenate([np.full(len(p[0]), p[1], np.uint8) for p in parts])
    hag = np.concatenate([p[2] for p in parts])
    street = np.concatenate([np.full(len(p[0]), p[3]) for p in parts])
    water = np.concatenate([np.full(len(p[0]), p[4]) for p in parts])
    n = 40
    tops = lidar.building_tops(xyz[:, 0], xyz[:, 1], xyz[:, 2], cls, np.zeros((n, n), bool), 0.0, 40.0, n, n)
    col = np.clip(xyz[:, 0].astype(int), 0, n - 1)
    row = np.clip((40.0 - xyz[:, 1]).astype(int), 0, n - 1)
    out = lidar.split_unclassified(xyz[:, 0], xyz[:, 1], xyz[:, 2], cls, hag, tops[row, col], street, water,
                                   origin=(0.0, 0.0))
    spans = np.cumsum([0] + [len(p[0]) for p in parts])
    ground, deck, roof, facade, crown, boat, car, wire = (out[a:b] for a, b in zip(spans[:-1], spans[1:]))
    assert (ground == 2).all() and (roof == 6).all() and (deck == 17).all()
    assert (facade == 1).all() and (boat == 1).all() and (car == 1).all()
    assert (crown == 5).mean() > 0.95
    assert (wire == 1).mean() > 0.9
