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
