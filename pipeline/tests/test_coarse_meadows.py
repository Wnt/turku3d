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


def test_farmland_is_a_hay_field_with_an_unmown_margin(tmp_path, monkeypatch):
    from turku3d.coarse.street_materials import FIELD
    monkeypatch.setenv("TURKU3D_DATA", str(tmp_path))
    osm = world.write_osm([({"landuse": "farmland"}, shapely.box(20, 100, 200, 300)),  # runs on past the tile edge
                           ({"landuse": "meadow"}, shapely.box(150, 150, 180, 180))])
    areas = meadows.prepare(TILE, None, osm)
    road = shapely.box(0, 95, 250, 99.5)
    result = dict((finish, g) for g, finish in meadows.regions(TILE, areas, road))
    field, meadow = result[FIELD], result[MEADOW]
    assert field.contains(shapely.Point(100, 200)) and not meadow.contains(shapely.Point(100, 200))
    for x, z in ((100, 101), (21, 200), (199, 120)):  # the margins along the road and the field's sides
        assert meadow.contains(shapely.Point(x, z)) and not field.contains(shapely.Point(x, z)), (x, z)
    assert field.contains(shapely.Point(100, 249.5))  # the tile border is no field edge
    assert meadow.contains(shapely.Point(165, 165)) and not field.contains(shapely.Point(165, 165))
    assert not field.intersects(road.buffer(-.1)) and not meadow.intersects(road.buffer(-.1))


def test_field_is_a_procedural_finish_with_the_panoramas_hay_tone():
    import colorsys

    from turku3d.coarse.street_materials import FIELD
    assert FINISH_KEYS[FIELD] == "field_hay"  # no 'grass' in the key: the client grows no lawn on a mown field
    albedo = ground_finishes.maps("field_hay", 256)["albedo"].reshape(-1, 3).mean(axis=0) / 255
    hue, light, saturation = colorsys.rgb_to_hls(*albedo)
    # Kurala hay field panoramas: hue 45-52 deg, saturation 0.18-0.35, lightness 0.35-0.45.
    assert 42 < hue * 360 < 55 and .15 < saturation < .38 and .33 < light < .47
    p = np.array([[0, 0, 0], [1, 0, 0], [0, 0, 1]], float)
    mesh = surfaces.visuals(TILE, p, np.array([[0, 2, 1]]), np.array([FIELD]), np.array([True]))[0]
    assert mesh.extras["material_key"] == "draft_field_hay"
