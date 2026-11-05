"""City profiles: Turku stays the default frame; another city changes only through its profile."""

import json
import os
import subprocess
import sys

import pytest

from turku3d import cities, config


def test_turku_default_frame_is_unchanged():
    assert cities.current() is cities.TURKU
    assert (config.ORIGIN_E, config.ORIGIN_N, config.CRS_HORIZONTAL) == (23459500.0, 6704500.0, "EPSG:3877")
    assert (config.OFFICE_E, config.OFFICE_N) == (23459668.0, 6704254.0)
    assert config.WORK_3X3.core.to_en().as_tuple() == (23459000, 6703500, 23460500, 6705000)
    assert config.LAZ_TILES_CSV.name == "laz-tiles.csv"


SITE = config.REPO_ROOT / "tools" / "cities" / "site"


def _probe(env):
    """A city process as tools/cities/run.sh starts it (its sitecustomize applies the frame)."""
    code = "from turku3d import config as C; print(C.CITY.name, C.ORIGIN_E, C.CRS_HORIZONTAL)"
    path = os.pathsep.join([str(SITE), os.environ.get("PYTHONPATH", "")])
    return subprocess.run([sys.executable, "-c", code], env={**os.environ, "PYTHONPATH": path, **env},
                          capture_output=True, text=True)


def test_config_source_is_turku_literal():
    """config.py is in every migration step's code hash: city support must not touch it."""
    text = (config.REPO_ROOT / "pipeline" / "turku3d" / "config.py").read_text()
    assert "cities" not in text and "ORIGIN_E = 23459500.0" in text


def test_city_from_marker_and_mismatch(tmp_path):
    (tmp_path / "city.json").write_text(json.dumps({"city": "espoo"}))
    env = {"TURKU3D_DATA": str(tmp_path), "TURKU3D_CITY": ""}
    assert _probe(env).stdout.split() == ["espoo", "25489000.0", "EPSG:3879"]
    bad = _probe({**env, "TURKU3D_CITY": "turku"})
    assert bad.returncode != 0 and "holds espoo" in bad.stderr


@pytest.mark.parametrize("city", list(cities.CITIES.values()), ids=lambda c: c.name)
def test_profiles_nest_on_the_block_grid(city):
    assert city.origin_e % 500 == 0 and city.origin_n % 500 == 0
    e0, n0, e1, n1 = city.work_3x3
    assert e0 < city.spawn_e < e1 + 500 and n0 < city.spawn_n <= n1
    assert not set(city.wfs_absent) & set(city.wfs_derived)


def test_infstreet_maps_to_the_turku_street_schema():
    import geopandas as gpd
    from shapely.geometry import box

    from turku3d.fetch.adapters import infstreet_rows
    from turku3d.ground.materials import surface_material

    frame = gpd.GeoDataFrame(
        {
            "feature_key": ["a", "b", "c", "d"],
            "PARTCLASSTEXT": ["Ajorata", "Ajorata", "Kevyt liikenne", "Kevyt liikenne"],
            "USETYPETEXT": ["Pääkatu", "Pysäköintialue", "Tori/Aukio", "Uusi luokka"],
            "PAVINGCLASSTEXT": ["Asfalttibetoni AB", "Asfalttibetoni AB", "Luonnonkivi", "Kivituhka / Sora / Murske"],
            "LABEL": ["049Tapiontori", "1Ajotie", None, "7Polku"],
            "ID": ["1", "2", "3", "4"],
        },
        geometry=[box(0, 0, 1, 1)] * 4,
    )
    out, unmapped = infstreet_rows(frame)
    assert list(out.Katuosalaji) == ["Ajorata", "Muu liikennealue", "Kävelytie", "Yhdistetty kevyen liikenteen väylä"]
    assert list(out.Katuosalaji_koodi) == ["1", "12", "16", "4"]
    assert list(out.Kadunnimi)[:2] == ["Tapiontori", "Ajotie"]
    assert unmapped == {"part:Kevyt liikenne/Uusi luokka": 1}
    mats = [surface_material(k, m) for k, m in zip(out.Katuosalaji, out.Pintamateriaali, strict=True)]
    assert mats == ["road", "road", "cobblestone", "gravel"]


def test_espoo_registers_map_to_turku_schemas():
    import geopandas as gpd
    from shapely.geometry import Point

    from turku3d.fetch.adapters import puut_rows, varusteet_rows
    from turku3d.props import classify

    def frame(rows, layer):
        f = gpd.GeoDataFrame(rows, geometry=[Point(r.pop("_xy")) for r in rows])
        f["geometry_local"] = gpd.GeoSeries(list(f.geometry), crs=None)
        f["layer"] = layer
        return f

    register = frame([{"_xy": (0, 0), "feature_key": "a", "VEGETATIONGROUPTEXT": "Lehtipuu",
                       "VEGETATIONCLASSTEXT": "Acer platanoides", "PLANTINGYEAR": "2023"},
                      {"_xy": (9, 9), "feature_key": "b", "VEGETATIONGROUPTEXT": "Lehtipensas",
                       "VEGETATIONCLASSTEXT": "x", "PLANTINGYEAR": "0"}], "GIS:InfVegetationPoint")
    lehti = frame([{"_xy": (0.5, 0), "feature_key": "c"}, {"_xy": (20, 0), "feature_key": "d"}], "kanta:Lehtipuu")
    havu = frame([{"_xy": (40, 0), "feature_key": "e"}], "kanta:Havupuu")
    trees = puut_rows(register, lehti, havu)
    assert sorted(trees.feature_key) == ["a", "d", "e"]  # shrub dropped, duplicate base-map tree dropped
    assert dict(zip(trees.feature_key, trees.Kasviryhma, strict=True))["e"] == "Havupuu"
    assert dict(zip(trees.feature_key, trees.Istutusvuosi, strict=True))["a"] == "2023"
    equipment = frame([{"_xy": (0, 0), "feature_key": "f", "EQUIPMENTCLASSTEXT": "Penkki",
                        "EQUIPMENTGROUPTEXT": "Penkit ja pöydät", "MODELTEXT": "Penkki, Tapiola"},
                       {"_xy": (1, 0), "feature_key": "g", "EQUIPMENTCLASSTEXT": "Roska-astia",
                        "EQUIPMENTGROUPTEXT": "Jäte", "MODELTEXT": "Roska-astia Siti"},
                       {"_xy": (2, 0), "feature_key": "h", "EQUIPMENTCLASSTEXT": "Pyöräilyvarusteet",
                        "EQUIPMENTGROUPTEXT": "Rakenteet", "MODELTEXT": "Pyöräteline"}], "GIS:InfEquipment")
    kinds = [classify(r) for r in varusteet_rows(equipment).to_dict("records")]
    assert kinds == ["bench", "bin", "bike_rack"]


def test_mml_reclassification_guards_changed_buildings():
    import numpy as np
    from shapely.geometry import box

    from turku3d.fetch.mml_lidar import reclassify

    ground = lambda e, n: np.zeros_like(e)  # noqa: E731
    buildings = [(box(0, 0, 10, 10), 12.0), (box(20, 0, 30, 10), 30.0)]  # second: rebuilt since the scan
    e = np.array([5, 5, 25, 25, 50, 50, 5.0])
    n = np.array([5, 6, 5, 6, 50, 51, 5.0])
    z = np.array([12.1, 11.9, 12.0, 12.2, 8.0, 0.5, 0.0])
    cls = np.array([1, 1, 1, 1, 1, 1, 2])
    one = np.ones(7, int)
    (ke, kn, kz, kc, _, _), stats = reclassify(None, (e, n, z, cls, one, one), ground, buildings, None)
    assert stats["changed_buildings"] == 1
    assert list(kc) == [6, 6, 5, 3, 2]
