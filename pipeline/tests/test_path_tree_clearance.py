"""Visible tree envelopes and readable forest paths, without rebuilding structural terrain."""

import json
from dataclasses import replace

import numpy as np
import pytest
import shapely
from turku3d import config as C
from turku3d.coarse.forest_paths import path_lines, path_surfaces, read_paths
from turku3d.coarse.forests import clears_path, produce, trunk_base_radius
from turku3d.coarse.inputs import Inputs
from turku3d.col.materials import mat_id
from turku3d.vegetation.trees import VARIANTS, Tree


def inputs(flags=1):
    return Inputs(
        np.zeros((2, 2)),
        np.empty((0, 3)),
        np.zeros((2, 2, 3), np.uint8),
        [],
        [],
        [],
        {"json": json.dumps({"nodes": [0, 0, 250, 0], "edges": [[0, 1, flags, 250]]})},
        {},
        {},
    )


@pytest.mark.parametrize("source", ["mapped", "canopy", "scatter", "row"])
def test_all_sources_clear_visible_trunks_at_path_edge(source, monkeypatch):
    data = inputs()
    points = [(20, 1.9), (40, 2.1), (60, 2.6), (80, 8)]
    mapped = []
    if source == "mapped":
        mapped = [Tree(str(x), x, z, "osm", crown_radius=3) for x, z in points]
    else:
        geom = shapely.box(0, 0, 100, 100)
        kind = "forest"
        if source == "row":
            geom = shapely.LineString([(20, 2.6), (80, 2.6), (80, 20)])
            kind = "tree_row"
        data = replace(data, forests=[("wood", geom, kind, "broadleaved")])
        if source == "canopy":
            data = replace(data, canopy=np.array([[x, 10, z] for x, z in points]))
        elif source == "scatter":
            monkeypatch.setattr("turku3d.coarse.forests.scatter", lambda *a, **kw: points)
    trees, bushes = produce(C.VisualTile(0, 0, 0), data, lambda x, z: 0, mapped)
    assert trees
    for tree in trees + bushes:
        assert tree.z >= 1.25 + trunk_base_radius(tree.variant) * tree.crown_radius


def test_variant_base_clearance_allows_crown_overhang():
    paths = shapely.LineString([(0, 0), (100, 0)]).buffer(1.25)
    narrow = Tree("narrow", 10, 1.7, "osm", crown_radius=1.2)
    assert clears_path(narrow, paths)
    # The oak's wider flared base still intrudes; upper foliage is allowed overhead.
    assert not clears_path(replace(narrow, variant=VARIANTS[2]), paths)
    assert clears_path(replace(narrow, z=2.6, crown_radius=3), paths)
    assert not clears_path(replace(narrow, z=1.3), paths)


def test_car_road_clearance_stays_at_two_metres():
    data = inputs(flags=1 | 2 | (2 << 3))
    assert path_lines(data.roads) == []
    mapped = [Tree("roadside", 10, 2.1, "osm")]
    trees, _ = produce(C.VisualTile(0, 0, 0), data, lambda x, z: 0, mapped)
    assert trees == mapped


def test_forest_path_overrides_landcover_and_keeps_mapped_surface():
    from turku3d.coarse.surfaces import classify, split_boundaries

    data = replace(
        inputs(),
        forests=[("wood", shapely.box(0, 0, 100, 100), "wood", "")],
        surfaces=[
            (shapely.box(0, 0, 250, 1.25), mat_id("road")),
            (shapely.box(0, 0, 100, 100), mat_id("grass")),
        ],
    )
    regions = [*data.surfaces, *path_surfaces(data)]
    positions = np.array([[10.0, 0, 0], [20, 0, 0], [10, 0, 5]])
    assert classify(
        np.array([[15, 0, 0.5], [15, 0, 3]]), data.ortho, C.VisualTile(0, 0, 0), [], regions
    ).tolist() == [mat_id("road"), mat_id("grass")]
    pos, idx, mats, _ = split_boundaries(
        positions,
        np.array([[0, 1, 2]], np.uint32),
        np.array([mat_id("grass")], np.uint8),
        np.array([True]),
        regions,
    )
    centers = pos[idx].mean(axis=1)
    assert np.any(mats == mat_id("road"))
    assert np.all(mats[centers[:, 2] < 1.25] == mat_id("road"))
    assert np.all(mats[centers[:, 2] > 1.25] == mat_id("grass"))


def test_mapped_path_widths_preserve_dense_park_between_paths(tmp_path, monkeypatch):
    import producer_world as world

    monkeypatch.setenv("TURKU3D_DATA", str(tmp_path))
    tile = C.VisualTile(0, 0, 0)
    path = world.write_osm(
        [
            ({"highway": "path", "width": "1 m"}, shapely.LineString([(0, 0), (250, 0)])),
            ({"highway": "cycleway", "width": "4"}, shapely.LineString([(0, 12), (250, 12)])),
            ({"highway": "footway", "width": "nan"}, shapely.LineString([(0, 30), (250, 30)])),
            ({"highway": "path", "bridge": "yes"}, shapely.LineString([(0, 50), (250, 50)])),
            ({"highway": "path", "tunnel": "yes"}, shapely.LineString([(0, 60), (250, 60)])),
            ({"highway": "pedestrian", "area": "yes"}, shapely.box(20, 70, 40, 80)),
        ]
    )
    corridors = read_paths(tile, path)
    assert len(corridors) == 4
    footprint = shapely.union_all(corridors)
    assert footprint.covers(shapely.Point(100, 0.49))
    assert not footprint.covers(shapely.Point(100, 0.51))
    assert footprint.covers(shapely.Point(100, 13.99))
    assert not footprint.covers(shapely.Point(100, 14.01))
    assert footprint.covers(shapely.Point(100, 31.24))
    assert not footprint.covers(shapely.Point(100, 50))
    assert not footprint.covers(shapely.Point(100, 60))
    assert footprint.covers(shapely.Point(30, 75))
    # Dense 12 m path spacing still permits mature trees between the actual paths.
    data = replace(
        inputs(),
        vegetation_paths=corridors,
        forests=[("park", shapely.box(0, 0, 250, 24), "forest", "")],
    )
    mapped = [Tree("overhang", 100, 5, "osm", crown_radius=6)]
    trees, _ = produce(tile, data, lambda x, z: 0, mapped)
    assert mapped[0] in trees
    assert len(trees) > 25
    assert all(clears_path(t, footprint) for t in trees)
