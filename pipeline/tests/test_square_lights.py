"""Mast lighting of dark open squares: OSM rule, mast layout, tile ownership and the migration."""

import json

import geopandas as gpd
import shapely
from turku3d import config as C
from turku3d.coarse import floodlights as F
from turku3d.coarse import square_lights as Q
from turku3d.migrations import core, registry
from turku3d.migrations import square_lights as migration


def osm(tmp_path, features):
    rows = [dict(osm_type=kind, osm_id=i + 1, tags=json.dumps(tags), geometry_local=geometry)
            for i, (kind, tags, geometry) in enumerate(features)]
    path = tmp_path / "osm.parquet"
    gpd.GeoDataFrame(rows, geometry="geometry_local").to_parquet(path)
    return path


def rect(cx, cz, length, width):
    return shapely.box(cx - length / 2, cz - width / 2, cx + length / 2, cz + width / 2)


def lamps_along(x0, x1, z, step=15.0):
    return [("node", {"highway": "street_lamp"}, shapely.Point(x0 + i * step, z))
            for i in range(int((x1 - x0) // step) + 1)]


SQUARE = {"place": "square", "name": "Tori"}
PEDESTRIAN = {"highway": "pedestrian", "area": "yes", "lit": "yes"}


def test_a_square_no_mapped_lamp_reaches_gets_masts_inside_it(tmp_path):
    path = osm(tmp_path, [
        # A 120 x 80 m square with lamps along its north edge only, and a pedestrian area mapped inside it.
        ("way", SQUARE, rect(125, 125, 120, 80)),
        ("way", PEDESTRIAN, rect(100, 110, 30, 20)),
        *lamps_along(65, 185, 165),
        # A square lit by lamps across it, one too small, one unlit by its tags, one under a roof and a
        # bent pedestrian street the client's even frame light would spill from.
        ("way", SQUARE, rect(125, -120, 80, 60)),
        *[lamp for z in (-140, -120, -100) for lamp in lamps_along(85, 165, z, 20.0)],
        ("way", SQUARE, rect(-120, 125, 40, 40)),
        ("way", {**SQUARE, "lit": "no"}, rect(-120, -120, 80, 60)),
        ("way", {**PEDESTRIAN, "covered": "yes"}, rect(125, 380, 80, 60)),
        ("way", PEDESTRIAN, shapely.union_all([rect(-150, -60, 120, 12), rect(-96, -10, 12, 100)])),
    ])
    found = Q.dark_squares(path, C.VisualTile.parse("t0_0_0").bbox)
    assert [v["id"] for v in found] == ["square:osm:way:1"]
    tori = found[0]
    assert tori["union"].area == 120 * 80
    masts = [shapely.Point(m["x"], m["z"]) for m in tori["masts"]]
    lamps = [shapely.Point(x, 165) for x in range(65, 186, 15)]
    assert len(masts) >= 4
    assert all(tori["union"].buffer(-Q.EDGE).contains(p) for p in masts)
    assert all(p.distance(q) >= Q.CLEAR for p in masts for q in lamps)


def test_a_square_across_tiles_is_listed_once_and_its_masts_by_the_tiles_they_stand_in(tmp_path):
    path = osm(tmp_path, [("way", SQUARE, rect(10, 125, 160, 90))])
    tiles = {t: Q.tile_square_lights(C.VisualTile.parse(t), path, lambda x, z: 3.0) for t in ("t0_0_0", "t0_-1_0")}
    venues = [v for vs, _ in tiles.values() for v in vs]
    masts = [m for _, ms in tiles.values() for m in ms]
    assert [v["id"] for v in venues] == ["square:osm:way:1"] and venues[0]["kind"] == Q.KIND
    assert venues[0]["level"] == Q.LEVEL and venues[0]["y"] == 3.0
    assert len({m["id"] for m in masts}) == len(masts) >= 4
    assert {m["x"] < 0 for m in tiles["t0_-1_0"][1]} == {True} and {m["x"] >= 0 for m in tiles["t0_0_0"][1]} == {True}
    for m in masts:
        assert m["venue"] == venues[0]["id"] and m["mesh"] == 0 and m["top"] == 3.0 + Q.HEIGHT
        assert m["aim"] == [venues[0]["x"], venues[0]["z"]] and m["lamps"] == F.lamps(Q.HEIGHT)
    # Each tile reads the square whole from its own bounds: both agree on every mast.
    whole = Q.dark_squares(path, C.VisualTile.parse("t0_1_0").bbox)[0]["masts"]
    assert sorted(m["id"] for m in masts) == sorted(m["id"] for m in whole)


def test_the_migration_adds_dark_squares_to_the_tile_floodlights(tmp_path, monkeypatch):
    from turku3d.coarse.sources import Sources

    path = osm(tmp_path, [("way", SQUARE, rect(125, 125, 120, 80))])
    monkeypatch.setattr(Sources, "osm_source", lambda self, tile: path)
    monkeypatch.setattr(F, "collision_ground", lambda cells, read: lambda x, z: 2.0)
    pitch = {"id": "osm:way:9", "kind": "field"}
    manifest = {"tile": "t0_0_0", "lights": {"count": 0, "floodlights": {"version": 1, "venues": [pitch], "masts": []}}}
    migration.transform(core.Product(tmp_path, manifest))
    record = manifest["lights"]["floodlights"]
    assert [v["id"] for v in record["venues"]] == ["osm:way:9", "square:osm:way:1"]
    assert record["masts"] and all(m["venue"] == "square:osm:way:1" for m in record["masts"])
    assert registry.CHAIN[-1] is migration.MIGRATION
