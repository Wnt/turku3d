"""Meadows: the green register's unmown parts and OSM meadows become the meadow finish; mown green stays lawn."""

import numpy as np
import producer_world as world
import shapely
from turku3d import config as C
from turku3d.coarse import ground_finishes, meadows, surfaces
from turku3d.coarse.street_materials import FINISH_KEYS, MEADOW

TILE = C.VisualTile.parse("t0_0_0")


def test_unmown_register_parts_and_osm_meadows_are_meadows(tmp_path, monkeypatch):
    monkeypatch.setenv("TURKU3D_DATA", str(tmp_path))
    register = world.write_wfs("GIS_Viheralueet", [
        {"feature_key": "a", "Kunnossapitoluokka_koodi": "A2", "Viheralueosalaji": "Viheralue"},  # use meadow
        {"feature_key": "b", "Kunnossapitoluokka_koodi": "R3", "Viheralueosalaji": "Viheralue"},  # mown park lawn
        {"feature_key": "c", "Kunnossapitoluokka_koodi": "R4", "Viheralueosalaji": "Niitty"},  # surveyed as meadow
        {"feature_key": "d", "Kunnossapitoluokka_koodi": "EH", "Viheralueosalaji": "Viheralue"},  # not maintained
        {"feature_key": "e", "Kunnossapitoluokka_koodi": "A3", "Viheralueosalaji": "Viheralue"},  # a sliver
    ], [shapely.box(0, 0, 60, 60), shapely.box(60, 0, 120, 60), shapely.box(0, 60, 60, 120),
        shapely.box(60, 60, 120, 120), shapely.box(200, 200, 202, 210)],
        columns=("feature_key", "Kunnossapitoluokka_koodi", "Viheralueosalaji"))
    osm = world.write_osm([({"landuse": "meadow"}, shapely.box(150, 0, 250, 80)),
                           ({"leisure": "park"}, shapely.box(150, 100, 250, 180))])
    areas = meadows.prepare(TILE, register, osm)
    path = shapely.LineString([(0, 30), (60, 30)]).buffer(1.5)
    result = meadows.regions(TILE, areas, path)
    assert [m for _, m in result] == [MEADOW]
    meadow = result[0][0]
    for x, z in ((30, 10), (30, 90), (90, 90), (200, 40)):
        assert meadow.contains(shapely.Point(x, z)), (x, z)
    for x, z in ((90, 30), (200, 140), (201, 205), (30, 30)):  # lawn, park, sliver, the path
        assert not meadow.contains(shapely.Point(x, z)), (x, z)
    assert meadow.within(shapely.box(*TILE.bbox.as_tuple()).buffer(1e-6))
    assert meadows.regions(TILE, [], None) == [] and meadows.prepare(TILE, None, None) == []


def test_meadow_is_a_procedural_visual_finish_the_client_grows_grass_on():
    assert FINISH_KEYS[MEADOW] == "grass_meadow"  # 'grass' in the key: the client's grass surface style
    albedo = ground_finishes.maps("grass_meadow", 128)["albedo"].astype(float) / 255
    mean = (((albedo + .055) / 1.055) ** 2.4).reshape(-1, 3).mean(axis=0)
    # Green over red over blue, darker than a lawn's sunlit blades, warmer from straw and seed heads.
    assert mean[1] > mean[0] > mean[2] and .05 < mean.mean() < .15
    p = np.array([[0, 0, 0], [1, 0, 0], [0, 0, 1]], float)
    mesh = surfaces.visuals(TILE, p, np.array([[0, 2, 1]]), np.array([MEADOW]), np.array([True]))[0]
    assert mesh.extras["material_key"] == "draft_grass_meadow"
