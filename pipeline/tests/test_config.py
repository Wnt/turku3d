from pathlib import Path
import csv
import math

import numpy as np
import pytest

from turku3d import config as C
from turku3d.config import BBoxEN, BBoxLocal, CollisionTile, LazTile, VisualTile


def test_origin_and_axes():
    assert C.en_to_local(C.ORIGIN_E, C.ORIGIN_N, 0.0) == (0.0, 0.0, 0.0)
    x, y, z = C.en_to_local(C.OFFICE_E, C.OFFICE_N, 5.0)
    assert (x, y, z) == (168.0, 5.0, 246.0)  # ARCHITECTURE.md D3: x ~ 168, z ~ 246
    # east -> +x, north -> -z
    assert C.en_to_local(C.ORIGIN_E + 1, C.ORIGIN_N, 0)[0] == 1
    assert C.en_to_local(C.ORIGIN_E, C.ORIGIN_N + 1, 0)[2] == -1


def test_local_en_roundtrip_numpy():
    rng = np.random.default_rng(0)
    e = C.ORIGIN_E + rng.uniform(-5000, 5000, 1000)
    n = C.ORIGIN_N + rng.uniform(-5000, 5000, 1000)
    h = rng.uniform(0, 60, 1000)
    x, y, z = C.en_to_local(e, n, h)
    e2, n2, h2 = C.local_to_en(x, y, z)
    assert np.allclose(e, e2, atol=1e-6) and np.allclose(n, n2, atol=1e-6)
    assert np.allclose(h, h2)


def test_wgs84_office():
    lat, lon = C.OFFICE_LATLON
    e, n = C.wgs84_to_en(lat, lon)
    # The documented E/N are rounded to 1 m from the documented lat/lon (5 decimals ~ 0.6 m).
    assert abs(e - C.OFFICE_E) < 1.5 and abs(n - C.OFFICE_N) < 1.5
    lat2, lon2 = C.en_to_wgs84(e, n)
    assert lat2 == pytest.approx(lat, abs=1e-9) and lon2 == pytest.approx(lon, abs=1e-9)


def test_wgs84_origin_matches_research():
    # research/prior-art.md: tile 23459500_6705000 spans ~60.4505-60.4551 N, 22.2641-22.2731 E,
    # so O (its SW corner) is at ~60.4505 N, 22.2641 E.
    lat, lon = C.en_to_wgs84(C.ORIGIN_E, C.ORIGIN_N)
    assert lat == pytest.approx(60.4505, abs=2e-4)
    assert lon == pytest.approx(22.2641, abs=2e-4)
    x, z = C.local_to_wgs84(0.0, 0.0)
    assert (x, z) == (lat, lon)
    lx, lz = C.wgs84_to_local(lat, lon)
    assert abs(lx) < 1e-6 and abs(lz) < 1e-6


def test_visual_tiles():
    t = VisualTile.at(168.0, 246.0)
    assert t.id == "t0_0_0" and t.bbox == BBoxLocal(0, 0, 250, 250)
    assert VisualTile.parse("t0_-1_3") == VisualTile(0, -1, 3)
    assert VisualTile.at(-0.001, 0.0).id == "t0_-1_0"
    assert VisualTile.at(250.0, 0.0).id == "t0_1_0"  # half-open
    assert VisualTile(0, 3, -3).parent() == VisualTile(1, 1, -2)
    assert VisualTile(3, 0, 0).parent() is None
    kids = VisualTile(1, 1, -2).children()
    assert len(kids) == 4 and all(k.parent() == VisualTile(1, 1, -2) for k in kids)
    assert VisualTile(2, 0, 0).size == 1000.0
    for bad in ("t4_0_0", "x0_0_0", "t0_0", "c_0_0"):
        with pytest.raises(ValueError):
            VisualTile.parse(bad)


def test_collision_tiles():
    c = CollisionTile.at(168.0, 246.0)
    assert c.id == "c_2_3"
    assert c.bbox == BBoxLocal(125.0, 187.5, 187.5, 250.0)
    assert c.origin == (125.0, 0.0, 187.5)
    assert c.visual_parent() == VisualTile(0, 0, 0)
    assert CollisionTile(-1, -5).visual_parent() == VisualTile(0, -1, -2)
    assert CollisionTile.parse("c_-3_7") == CollisionTile(-3, 7)
    sub = VisualTile(0, 1, 2).collision_tiles()
    assert len(sub) == 16 and all(s.visual_parent() == VisualTile(0, 1, 2) for s in sub)
    assert len(VisualTile(1, 0, 0).collision_tiles()) == 64
    # tile edges are exact in float32 (shared seam vertices)
    assert float(np.float32(C.COLLISION_TILE_SIZE)) == C.COLLISION_TILE_SIZE


def test_laz_tiles():
    t = LazTile.at_en(C.OFFICE_E, C.OFFICE_N)
    assert t.name == "23459500_6704500"
    assert t.bbox_en == BBoxEN(23459500, 6704000, 23460000, 6704500)
    assert t.url() == "https://turku.asiointi.fi/3d/pistepilvi/23459500_6704500.laz"
    assert LazTile.at_en(23459500.0, 6704500.01).name == "23459500_6705000"
    assert LazTile.parse("23459500_6705000") == LazTile(23459500, 6705000)
    with pytest.raises(ValueError):
        LazTile.parse("23459501_6705000")
    # LAZ tile == visual level-1 tile
    v1 = VisualTile.at(168.0, 246.0, level=1)
    assert v1.bbox.to_en() == t.bbox_en


def test_laz_tiles_in_manifest():
    with open(C.LAZ_TILES_CSV, newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 2564
    for r in rows:
        t = LazTile.parse(r["tile"])
        assert t.bbox_en.as_tuple() == tuple(
            float(r[k]) for k in ("xmin", "ymin", "xmax", "ymax")
        )
    names = {r["tile"] for r in rows if r["dataset"] == "pistepilvi"}
    assert len(names) == 1282
    assert all(t.name in names for t in C.OFFICE_BLOCK.laz_tiles)


def test_grid_range():
    b = BBoxLocal(0, 0, 250, 250)
    assert len(C.collision_tiles_for_bbox(b)) == 16
    assert C.visual_tiles_for_bbox(b) == [VisualTile(0, 0, 0)]
    assert len(C.visual_tiles_for_bbox(BBoxLocal(-1, -1, 251, 1))) == 6
    en = BBoxEN(23459000, 6704000, 23460000, 6705000)
    assert [t.name for t in C.laz_tiles_for_bbox(en)] == [
        "23459000_6705000",
        "23459500_6705000",
        "23459000_6704500",
        "23459500_6704500",
    ]
    assert en.to_local().to_en() == en


def test_office_aoi():
    a = C.OFFICE_BLOCK
    assert a.core == BBoxLocal(62.5, 125.0, 312.5, 375.0)
    assert a.core.xmax - a.core.xmin == 250 and a.core.zmax - a.core.zmin == 250
    assert a.core.contains(168.0, 246.0)
    cx, cz = (a.core.xmin + a.core.xmax) / 2, (a.core.zmin + a.core.zmax) / 2
    assert math.hypot(cx - 168, cz - 246) < C.COLLISION_TILE_SIZE / 2 * math.sqrt(2)
    assert len(a.collision_tiles) == 16
    assert a.collision_tiles[0].id == "c_1_2" and a.collision_tiles[-1].id == "c_4_5"
    assert [t.id for t in a.visual_tiles] == ["t0_0_0", "t0_1_0", "t0_0_1", "t0_1_1"]
    assert a.with_margin == BBoxLocal(12.5, 75.0, 362.5, 425.0)
    assert [t.name for t in a.laz_tiles] == ["23459500_6704500"]
    assert a.core.to_en() == BBoxEN(23459562.5, 6704125.0, 23459812.5, 6704375.0)


def test_area_yaml_is_committed_and_current():
    path = C.DATA_MANIFESTS / "areas" / "office-block.yaml"
    assert path.read_text() == C.area_yaml(C.OFFICE_BLOCK), (
        "regenerate: make foundation-areas"
    )


def test_paths(monkeypatch, tmp_path):
    monkeypatch.delenv("TURKU3D_WORK", raising=False)
    monkeypatch.setenv("TURKU3D_DATA", str(tmp_path))
    assert C.raw_dir("laz") == tmp_path / "raw" / "laz"
    assert C.cache_dir("cdt") == tmp_path / "cache" / "cdt"
    assert C.dist_dir() == tmp_path / "dist"
    monkeypatch.delenv("TURKU3D_DATA")
    assert C.data_root() == Path.home() / "turku3d/data"
    monkeypatch.setenv("TURKU3D_WORK", str(tmp_path / "worker"))
    assert C.dist_dir() == tmp_path / "worker/dist"
    assert C.cache_dir() == tmp_path / "worker/cache"
    assert (C.REPO_ROOT / "docs" / "ARCHITECTURE.md").exists()


def test_cli(capsys):
    C.main(["--where", str(C.OFFICE_E), str(C.OFFICE_N)])
    out = capsys.readouterr().out
    assert "visual=t0_0_0" in out and "collision=c_2_3" in out and "laz=23459500_6704500" in out


def test_crs_strings_resolve():
    from pyproj import CRS

    assert "GK23FIN" in CRS(C.CRS_HORIZONTAL).name
    assert "N2000" in CRS(C.CRS_VERTICAL).name
    assert CRS(C.CRS_COMPOUND).is_compound


def test_meridian_convergence():
    """True north is +0.64 deg in grid terms at O: moving due north in WGS84 drifts east in EPSG:3877."""
    import math

    lat, lon = C.en_to_wgs84(C.ORIGIN_E, C.ORIGIN_N)
    e1, n1 = C.wgs84_to_en(lat + 0.01, lon)
    expect = math.degrees(math.atan2(e1 - C.ORIGIN_E, n1 - C.ORIGIN_N))
    assert abs(C.grid_bearing_of_true_north(C.ORIGIN_E, C.ORIGIN_N) - expect) < 1e-3
    assert abs(expect - 0.640) < 0.005
    assert abs(C.true_to_grid_bearing(C.ORIGIN_E, C.ORIGIN_N, 359.9) - 0.540) < 0.005


def test_game_origin_ts_mirrors_config():
    """game/src/core/origin.ts duplicates the origin/grid constants; keep them equal to config.py."""
    import re

    path = C.REPO_ROOT / "game/src/core/origin.ts"
    ts = path.read_text()

    def num(name):
        return float(re.search(rf"export const {name} = ([-0-9.]+);", ts)[1])

    assert (num("E0"), num("N0"), num("H0")) == C.ORIGIN
    assert num("VISUAL_L0") == C.VISUAL_TILE_SIZE and num("COLLISION_TILE") == C.COLLISION_TILE_SIZE
    m = re.search(r"OFFICE_EN = \{ E: ([0-9.]+), N: ([0-9.]+) \}", ts)
    assert (float(m[1]), float(m[2])) == (C.OFFICE_E, C.OFFICE_N)
    assert abs(num("TRUE_NORTH_GRID_BEARING_DEG") - C.grid_bearing_of_true_north(C.ORIGIN_E, C.ORIGIN_N)) < 0.01


def test_work_area():
    assert len(C.WORK_3X3.laz_tiles) == 9
    assert C.WORK_3X3.core.to_en() == BBoxEN(23459000, 6703500, 23460500, 6705000)
    assert (C.DATA_MANIFESTS / "areas/work-3x3.yaml").read_text() == C.area_yaml(C.WORK_3X3)


def test_work_paths_are_isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("TURKU3D_DATA", str(tmp_path / "shared"))
    monkeypatch.setenv("TURKU3D_WORK", str(tmp_path / "worker"))
    assert C.raw_dir("laz") == tmp_path / "shared/raw/laz"
    assert C.cache_dir("cdt") == tmp_path / "worker/cache/cdt"
    assert C.build_dir("layers") == tmp_path / "worker/build/layers"
    assert C.dist_dir() == tmp_path / "worker/dist"
