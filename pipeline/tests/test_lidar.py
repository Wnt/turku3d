"""Fast synthetic tests; no real city tiles or network services."""

import json

import laspy
import numpy as np
import pytest
from shapely.geometry import LineString, box
from turku3d import config as C
from turku3d.lidar import (
    PointCloud,
    Surface,
    TileStore,
    edge_lines,
    measure_profile,
    measured_breaklines,
    small_objects,
    surfaces,
)
from turku3d.lidar import walls as walls_mod


def step_cloud(step=0.12, slope=0.04):
    x, z = np.meshgrid(np.arange(-3, 3.01, 0.08), np.arange(-3, 3.01, 0.08))
    y = 4 + slope * x + 0.02 * z + np.where(x >= 0, step, 0)
    return PointCloud.from_xyz(np.c_[x.ravel(), y.ravel(), z.ravel()])


def write_laz(path, xyz):
    header = laspy.LasHeader(point_format=3, version="1.2")
    header.scales = [0.001] * 3
    header.offsets = [C.ORIGIN_E, C.ORIGIN_N, 0]
    data = laspy.LasData(header)
    data.x = xyz[:, 0] + C.ORIGIN_E
    data.y = C.ORIGIN_N - xyz[:, 2]
    data.z = xyz[:, 1]
    data.classification = np.full(len(xyz), 2, dtype=np.uint8)
    data.return_number = np.ones(len(xyz), dtype=np.uint8)
    data.number_of_returns = np.ones(len(xyz), dtype=np.uint8)
    data.write(path)


def test_queries_and_tree():
    cloud = PointCloud.from_xyz(
        [[0, 3, 0], [1, 4, 1], [2, 5, 2]],
        classification=[2, 12, 5],
        return_number=[1, 2, 1],
        number_of_returns=[2, 2, 1],
    )
    assert cloud.tree is cloud.tree
    assert len(cloud.query(bbox=C.BBoxLocal(0, 0, 2, 2))) == 2
    assert len(cloud.query(polygon=box(0.5, 0.5, 1.5, 1.5), classes=[12], returns=2)) == 1
    assert len(cloud.query(last_return=True)) == 2


def test_tile_cache_mmap_buffer_invalidation(tmp_path, monkeypatch):
    raw, cache = tmp_path / "raw", tmp_path / "cache"
    raw.mkdir()
    path = raw / "23459500_6704500.laz"
    points = np.array([[61, 4, 63], [63, 4.1, 63], [64, 4, 64], [126, 5, 63]])
    write_laz(path, points)
    store = TileStore(raw, cache)
    tile = store.tile("c_1_1")
    assert isinstance(tile.records, np.memmap)
    assert len(tile) == 2
    assert store.tile("c_1_1") is tile
    assert len(store.tile("c_1_1", buffer=50)) == 4

    def fail(*args, **kwargs):
        raise AssertionError("warm access must not decompress LAZ")

    monkeypatch.setattr(laspy, "open", fail)
    assert len(TileStore(raw, cache).tile("c_1_1")) == 2
    assert len(store.query(C.BBoxLocal(60, 60, 127, 65))) == 4
    monkeypatch.undo()
    write_laz(path, np.r_[points, [[65, 4, 65]]])
    assert len(store.tile("c_1_1")) == 3
    assert len(list(cache.glob("sources/*/MANIFEST.json"))) == 2
    with pytest.raises(ValueError):
        store.tile("c_1_1", buffer=-1)


def test_point_shards_share_identical_source_bytes_across_overlays(tmp_path, monkeypatch):
    import shutil
    monkeypatch.setenv('TURKU3D_SHARED_CACHE', str(tmp_path / 'shared'))
    first, second, cache = (tmp_path / name for name in ('first', 'second', 'cache'))
    first.mkdir()
    second.mkdir()
    name = '23459500_6704500.laz'
    write_laz(first / name, np.array([[63, 4.1, 63], [64, 4, 64]]))
    shutil.copyfile(first / name, second / name)
    a, b = TileStore(first, cache), TileStore(second, cache)
    tile = C.LazTile.parse(name.removesuffix('.laz'))
    assert a._source(tile)[1] == b._source(tile)[1]
    assert len(a.tile('c_1_1')) == 2
    def fail(*args, **kwargs):
        raise AssertionError('identical overlay must reuse decoded shards')
    monkeypatch.setattr(laspy, 'open', fail)
    assert len(b.tile('c_1_1')) == 2


def test_profile_removes_slope_and_recovers_signed_step():
    cloud = step_cloud()
    surface = Surface(cloud.points)
    fit = measure_profile(surface, [0, 0], [1, 0], snap=False)
    assert fit is not None
    assert fit.step == pytest.approx(0.12, abs=0.002)
    opposite = measure_profile(surface, [0, 0], [-1, 0], snap=False)
    assert opposite.step == pytest.approx(-0.12, abs=0.002)
    assert fit.confidence > 0.9
    plane = Surface(step_cloud(step=0, slope=0.2).points)
    assert abs(measure_profile(plane, [0, 0], [1, 0]).step) < 0.002
    assert measure_profile(surface, [20, 20], [1, 0]) is None
    with pytest.raises(ValueError):
        measure_profile(surface, [0, 0], [2, 0])


def test_outlier_and_ground_class12():
    cloud = step_cloud(step=0)
    points = np.r_[cloud.points, [[0.1, 4.05, 0.1], [0.1, 8, 0.1]]]
    classes = np.r_[np.full(len(cloud), 2), [12, 12]]
    raster = surfaces(PointCloud.from_xyz(points, classes), C.BBoxLocal(-5, -5, 5, 5), 0.25)
    assert np.nanmax(raster.ground) < 4.3
    assert np.isnan(raster.ground[0, 0])
    assert raster.confidence[0, 0] == 0
    assert np.nanmax(raster.ndsm) > 3.8
    assert raster.density.max() > 0
    h, _ = Surface(cloud.points).sample([[100, 100]], max_distance=2.0)
    assert np.isnan(h[0])


def test_edges_and_breakline_contract():
    cloud = step_cloud()
    raster = surfaces(cloud, C.BBoxLocal(-2, -2, 2, 2), 0.25)
    lines = edge_lines(raster)
    assert sum(line.length for line in lines) >= 3
    surface = Surface(cloud.points)
    fitted, _ = measured_breaklines(surface, LineString([[0, -2], [0, 2]]), "test", "lidar")
    assert fitted
    json.dumps(fitted, allow_nan=False)
    assert sum(LineString(f["centers_xz"]).length for f in fitted) >= 3
    for feature in fitted:
        assert len(feature["centers_xz"]) == len(feature["side_heights"])
        assert np.allclose(np.linalg.norm(feature["normals_xz"], axis=1), 1)
        assert feature["relief_class"] == "curb"
    slope = step_cloud(step=0, slope=0.2)
    assert not edge_lines(surfaces(slope, C.BBoxLocal(-2, -2, 2, 2), 0.25))


def test_measured_high_step_is_a_visible_wall_not_a_clamped_curb():
    cloud = step_cloud(step=0.55)
    features, _ = measured_breaklines(
        Surface(cloud.points), LineString([[0, -2], [0, 2]]), "wall", "lidar"
    )
    assert features
    assert all(feature["kind"] == "wall" for feature in features)


def test_no_bridge_across_unsupported_profile_gap():
    cloud = step_cloud()
    points = cloud.points[np.abs(cloud.points[:, 2]) > 1.5]
    result, measured = measured_breaklines(
        Surface(points), LineString([[0, -2.5], [0, 2.5]]), "gap", "vector", spacing=0.5
    )
    assert measured[5] is None
    for feature in result:
        chainage = feature["source_chainage"]
        assert not (min(chainage) < 2.5 < max(chainage))


def test_small_object_candidates():
    cloud = step_cloud(step=0, slope=0)
    raster = surfaces(cloud, C.BBoxLocal(-2, -2, 2, 2), 0.25)
    raster.ndsm[:] = 0
    raster.ndsm[3, 3] = 0.9
    raster.ndsm[10:12, 4:10] = 0.7
    objects = small_objects(raster)
    assert {o.kind for o in objects} == {"bollard", "bench"}
    assert all(0 < o.confidence <= 0.6 for o in objects)


def test_chm_excludes_building_and_split_is_stable():
    from turku3d.lidar.surface import split_points

    base = step_cloud(step=0, slope=0)
    points = np.r_[base.points, [[0.1, 7, 0.1], [0.3, 10, 0.3]]]
    classes = np.r_[np.full(len(base), 2), [5, 6]]
    raster = surfaces(PointCloud.from_xyz(points, classes), C.BBoxLocal(-2, -2, 2, 2), 0.25)
    assert np.nanmax(raster.chm) == pytest.approx(3.0, abs=0.02)
    assert np.nanmax(raster.ndsm) == pytest.approx(6.0, abs=0.02)
    a, b = split_points(points, classes)
    c, d = split_points(points[::-1], classes[::-1])
    assert np.array_equal(a[0], c[0][::-1])
    assert np.array_equal(b[0], d[0][::-1])
    assert 0 < len(b[0]) < len(points)


def test_comparison_counts_missing_mesh_as_failure():
    from turku3d.lidar.compare import summarize

    report = summarize(
        [
            {"truth": 0.1, "length": 2.0, "before": 0.01, "after": 0.1},
            {"truth": 0.12, "length": 1.0, "before": None, "after": None},
            {"truth": 0.01, "length": 3.0, "before": 0.0, "after": 0.0},
        ]
    )
    assert report["raised_length_m"] == 3
    assert report["before"]["mesh_step_fraction"] == 0
    assert report["after"]["mesh_step_fraction"] == pytest.approx(2 / 3)
    assert report["after"]["missing_mesh_profiles"] == 1


# Wall-frame projection of lidar points (turku3d.lidar.walls).

E0, N0 = 23459600.0, 6704300.0


def wall(outward="right"):
    # 10 m wall running east; "right" of p0->p1 is south.
    return walls_mod.Wall("b_1", (E0, N0), (E0 + 10, N0), 5.0, 8.0, outward)


def points(rows):
    xyz = np.array([r[:3] for r in rows], float)
    rgb = np.array([r[3] for r in rows], np.uint8)
    n = len(rows)
    return xyz, rgb, np.full(n, 6, np.uint8), (np.ones(n, np.uint8), np.ones(n, np.uint8))


@pytest.mark.parametrize("outward", ["right", "left"])
def test_project_wall_frame(outward):
    # Seen from outside, u runs from the viewer's left; d is positive on the outward side.
    south = -1 if outward == "right" else 1
    xyz, rgb, cls, ret = points(
        [
            (E0 + 2, N0 + 0.2 * south, 7.0, (10, 20, 30)),  # on the wall, 0.2 m out
            (E0 + 2, N0 - 0.4 * south, 7.0, (1, 2, 3)),  # 0.4 m inside
            (E0 + 2, N0 + 1.0 * south, 7.0, (9, 9, 9)),  # too far out
            (E0 + 2, N0, 14.0, (9, 9, 9)),  # above the top
            (E0 - 1, N0, 7.0, (9, 9, 9)),  # beyond the end
        ]
    )
    out = walls_mod.project([wall(outward)], xyz, rgb, cls, ret, walls_mod.Window())["b_1"]
    assert len(out) == 2
    u = 2.0 if outward == "right" else 8.0
    np.testing.assert_allclose(out["u"], [u, u], atol=1e-5)
    np.testing.assert_allclose(out["h"], [2.0, 2.0], atol=1e-5)
    np.testing.assert_allclose(sorted(out["d"]), [-0.4, 0.2], atol=1e-5)
    assert out["rgb"].tolist()[0] == [10, 20, 30]


def test_window_keeps_context():
    xyz, rgb, cls, ret = points([(E0 + 5, N0 + 2.0, 13.5, (5, 5, 5))])  # roof, 2 m inside
    tight = walls_mod.project([wall()], xyz, rgb, cls, ret, walls_mod.Window())
    wide = walls_mod.project([wall()], xyz, rgb, cls, ret, walls_mod.Window(inside=3, above=3))
    assert "b_1" not in tight and len(wide["b_1"]) == 1


def test_walls_from_features_normal_side():
    feature = {
        "geometry": {"coordinates": [[E0, N0], [E0 + 10, N0]]},
        "properties": {
            "building_id": "b",
            "facade_index": 1,
            "base_height": 5.0,
            "top_height": 13.0,
            "normal": [0.0, 0.0, -1.0],  # engine -z is north
        },
    }
    (w,) = walls_mod.walls_from_features([feature])
    assert w.id == "b_1" and w.outward == "left" and w.height == 8.0


def test_wall_points_reads_laz_colour(tmp_path):
    header = laspy.LasHeader(point_format=3, version="1.2")
    header.scales = [0.001] * 3
    header.offsets = [23440000.0, 6700000.0, 0.0]
    las = laspy.LasData(header)
    las.x = np.array([E0 + 3, E0 + 300])
    las.y = np.array([N0 - 0.1, N0])
    las.z = np.array([8.0, 8.0])
    las.red, las.green, las.blue = (np.array([v << 8, 0], np.uint16) for v in (200, 100, 50))
    las.classification = np.array([6, 6], np.uint8)
    las.write(tmp_path / "23459500_6704500.laz")
    found = walls_mod.wall_points([wall()], source_dir=tmp_path)["b_1"]
    assert len(found) == 1 and found["rgb"][0].tolist() == [200, 100, 50]
    walls_mod.save(tmp_path / "p.npz", {"b_1": found})
    assert walls_mod.load(tmp_path / "p.npz")["b_1"]["u"][0] == pytest.approx(3.0)


def test_missing_source_tile_is_an_error(tmp_path):
    with pytest.raises(FileNotFoundError):
        walls_mod.wall_points([wall()], source_dir=tmp_path)
