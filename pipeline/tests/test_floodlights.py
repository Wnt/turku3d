"""Floodlights of lit sports venues: OSM rule, mast layout, aim, tile ownership and ground heights."""

import json
import math

import geopandas as gpd
import numpy as np
import shapely

from turku3d import config as C
from turku3d.coarse import floodlights as F
from turku3d.col.format import ColTile, write_col


def osm(tmp_path, features):
    rows = [dict(osm_type=kind, osm_id=i + 1, tags=json.dumps(tags), geometry_local=geometry)
            for i, (kind, tags, geometry) in enumerate(features)]
    path = tmp_path / "osm.parquet"
    gpd.GeoDataFrame(rows, geometry="geometry_local").to_parquet(path)
    return path


def rect(cx, cz, length, width):
    return shapely.box(cx - length / 2, cz - width / 2, cx + length / 2, cz + width / 2)


def flat(y):
    return lambda x, z: y


def test_rule_layout_and_ownership(tmp_path):
    corners = [(10 - 58, 120 - 40), (10 + 58, 120 - 40), (10 + 58, 120 + 40), (10 - 58, 120 + 40)]
    path = osm(tmp_path, [
        # A lit football pitch straddling the seam of t0_0_0 and t0_-1_0, with its four mapped masts.
        ("way", {"leisure": "pitch", "sport": "soccer", "lit": "yes"}, rect(10, 120, 105, 68)),
        *[("node", {"man_made": "mast", "tower:type": "lighting"}, shapely.Point(p)) for p in corners],
        # A lit tennis block of two touching courts: one venue, layout masts.
        ("way", {"leisure": "pitch", "sport": "tennis", "lit": "yes"}, rect(150, 40, 36, 18)),
        ("way", {"leisure": "pitch", "sport": "tennis", "lit": "yes"}, rect(150, 58, 36, 18)),
        # Unlit and untagged grass pitches, a lamp-lit skating loop and a hall mapped as a stadium.
        ("way", {"leisure": "pitch", "sport": "soccer", "lit": "no"}, rect(150, 200, 100, 60)),
        ("way", {"leisure": "pitch", "sport": "soccer", "surface": "grass"}, rect(-150, 200, 100, 60)),
        ("way", {"leisure": "track", "lit": "yes", "area": "no"}, rect(60, 230, 80, 30).exterior),
        ("way", {"leisure": "stadium", "name": "hall"}, rect(-150, 60, 80, 50)),
        ("way", {"building": "yes"}, rect(-150, 60, 80, 50)),
    ])
    found = F.venues(path, C.VisualTile.parse("t0_0_0").bbox)
    kinds = sorted((v["kind"], len(v["members"]), len(v["masts"])) for v in found)
    assert kinds == [("court", 2, 4), ("large", 1, 4)]
    pitch = next(v for v in found if v["kind"] == "large")
    assert all(m["mesh"] == 1 for m in pitch["masts"])
    court = next(v for v in found if v["kind"] == "court")
    assert all(m["mesh"] == 0 and m["height"] == F.LAYOUT["court"][2] for m in court["masts"])

    tiles = {t: F.tile_floodlights(C.VisualTile.parse(t), path, flat(20.0), {"osm:2": 38.0})
             for t in ("t0_0_0", "t0_-1_0")}
    venues = [v for r in tiles.values() if r for v in r["venues"]]
    masts = [m for r in tiles.values() if r for m in r["masts"]]
    # Each venue and each mast is listed by exactly one tile; the seam splits the pitch's masts.
    assert sorted(v["id"] for v in venues) == sorted({v["id"] for v in venues})
    assert len(venues) == 2 and len(masts) == 8
    assert {m["id"] for m in tiles["t0_-1_0"]["masts"]} == {"osm:2", "osm:5"}
    for m in masts:
        venue = next(v for v in found if v["id"] == m["venue"])
        assert m["aim"] == [round(float(venue["centre"][0]), 2), round(float(venue["centre"][1]), 2)]
        assert m["y"] == 20.0 and m["lamps"] == F.lamps(m["top"] - m["y"])
    # A mapped mast's heads sit on the harbour stage's drawn height, else on its default.
    assert next(m for m in masts if m["id"] == "osm:2")["top"] == 58.0
    assert next(m for m in masts if m["id"] == "osm:3")["top"] == 20.0 + F.MAST_DEFAULT


def test_layout_masts_face_the_field_from_outside():
    venue = dict(id="v", kind="large", centre=np.array([0.0, 0.0]), u=np.array([1.0, 0.0]), length=110, width=70)
    masts = F._layout(venue)
    assert len(masts) == 6
    assert all(abs(m["z"]) == 35 + F.LAYOUT["large"][1] for m in masts)
    stadium = F._layout({**venue, "kind": "stadium"})
    assert len(stadium) == 4
    assert all(math.hypot(m["x"], m["z"]) > math.hypot(55, 35) for m in stadium)


def test_collision_ground_reads_open_ground_only():
    x0, z0 = 0.0, 0.0
    ground = [(x0, 10.0, z0), (x0 + 62.5, 10.0, z0), (x0 + 62.5, 12.0, z0 + 62.5), (x0, 12.0, z0 + 62.5)]
    roof = [(x0 + 10, 30.0, z0 + 10), (x0 + 20, 30.0, z0 + 10), (x0 + 20, 30.0, z0 + 20), (x0 + 10, 30.0, z0 + 20)]
    positions = np.array(ground + roof, dtype=np.float64)
    indices = np.array([[0, 2, 1], [0, 3, 2], [4, 6, 5], [4, 7, 6]], dtype=np.uint32)
    materials = np.array([10, 10, 23, 23], dtype=np.uint8)  # grass, grass, roof
    body = write_col(ColTile.for_tile(0, 0, positions, indices, materials), encoding="raw", optimize=False)
    at = F.collision_ground({"c_0_0": {"url": "col/c_0_0.col"}, "c_1_0": {"url": "col/c_1_0.col"}},
                            lambda url: body if url == "col/c_0_0.col" else b"fixture")
    assert abs(at(15.0, 15.0) - (10 + 2 * 15 / 62.5)) < 1e-4  # the roof is not ground
    assert at(70.0, 10.0) is None  # unreadable cell
    assert at(10.0, 200.0) is None  # no cell
