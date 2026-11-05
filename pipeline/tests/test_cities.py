"""City profiles: Turku stays the default frame; another city changes only through its profile."""

import json
import os
import subprocess
import sys

import pytest

from turku3d import cities, config


def test_turku_default_frame_is_unchanged():
    assert config.CITY is cities.TURKU
    assert (config.ORIGIN_E, config.ORIGIN_N, config.CRS_HORIZONTAL) == (23459500.0, 6704500.0, "EPSG:3877")
    assert (config.OFFICE_E, config.OFFICE_N) == (23459668.0, 6704254.0)
    assert config.WORK_3X3.core.to_en().as_tuple() == (23459000, 6703500, 23460500, 6705000)
    assert config.LAZ_TILES_CSV.name == "laz-tiles.csv"


def _probe(env):
    code = "from turku3d import config as C; print(C.CITY.name, C.ORIGIN_E, C.CRS_HORIZONTAL)"
    return subprocess.run([sys.executable, "-c", code], env={**os.environ, **env}, capture_output=True, text=True)


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
