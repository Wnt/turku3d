"""Visible tree envelopes and readable forest paths, without rebuilding structural terrain."""

import json
from dataclasses import replace

import numpy as np
import pytest
import shapely

from turku3d import config as C
from turku3d.coarse.forests import clears_path, path_lines, path_surfaces, produce, trunk_base_radius
from turku3d.coarse.inputs import Inputs
from turku3d.col.materials import mat_id
from turku3d.vegetation.trees import Tree, VARIANTS


def inputs(flags=1):
    return Inputs(
        np.zeros((2, 2)), np.empty((0, 3)), np.zeros((2, 2, 3), np.uint8), [], [], [],
        {"json": json.dumps({"nodes": [0, 0, 250, 0], "edges": [[0, 1, flags, 250]]})}, {}, {},
    )


@pytest.mark.parametrize("source", ["mapped", "canopy", "scatter", "row"])
def test_all_sources_clear_visible_trunks_and_crowns(source, monkeypatch):
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
        assert tree.z >= 2 + trunk_base_radius(tree.variant) * tree.crown_radius
        assert tree.z >= 1 + tree.crown_radius
    assert not any(tree.z in (1.9, 2.1, 2.6) for tree in trees)


def test_variant_base_and_crown_clearance_are_separate_from_collision_radius():
    paths = shapely.LineString([(0, 0), (100, 0)])
    narrow = Tree("narrow", 10, 2.6, "osm", crown_radius=1.2)
    assert clears_path(narrow, paths)
    # Same collision radius and crown, but the oak's flared mesh base intrudes.
    assert not clears_path(replace(narrow, variant=VARIANTS[2]), paths)
    assert not clears_path(replace(narrow, crown_radius=2), paths)
    for z in (1.9, 2.1):
        assert not clears_path(replace(narrow, z=z), paths)


def test_car_road_clearance_stays_at_two_metres():
    data = inputs(flags=1 | 2 | (2 << 3))
    assert path_lines(data.roads) == []
    mapped = [Tree("roadside", 10, 2.1, "osm")]
    trees, _ = produce(C.VisualTile(0, 0, 0), data, lambda x, z: 0, mapped)
    assert trees == mapped


def test_forest_path_overrides_landcover_and_keeps_mapped_surface():
    from turku3d.coarse.surfaces import classify, split_boundaries

    data = replace(inputs(), forests=[("wood", shapely.box(0, 0, 100, 100), "wood", "")],
                   surfaces=[(shapely.box(0, 0, 250, 1.25), mat_id("road")),
                             (shapely.box(0, 0, 100, 100), mat_id("grass"))])
    regions = [*data.surfaces, *path_surfaces(data)]
    positions = np.array([[10., 0, 0], [20, 0, 0], [10, 0, 5]])
    assert classify(np.array([[15, 0, .5], [15, 0, 3]]), data.ortho,
                    C.VisualTile(0, 0, 0), [], regions).tolist() == [mat_id("road"), mat_id("grass")]
    pos, idx, mats, _ = split_boundaries(positions, np.array([[0, 1, 2]], np.uint32),
                                       np.array([mat_id("grass")], np.uint8), np.array([True]), regions)
    centers = pos[idx].mean(axis=1)
    assert np.any(mats == mat_id("road"))
    assert np.all(mats[centers[:, 2] < 1.25] == mat_id("road"))
    assert np.all(mats[centers[:, 2] > 1.25] == mat_id("grass"))


def test_forest_path_albedo_clears_aerial_foliage_only_inside_corridor():
    from turku3d.coarse.forests import path_ground

    tile = C.VisualTile(0, 0, 0)
    ortho = np.full((250, 250, 3), [10, 40, 10], np.uint8)
    regions = [(shapely.box(0, 0, 250, 2), mat_id("gravel"))]
    result = path_ground(tile, ortho, regions)
    assert not np.array_equal(result[:2], ortho[:2])
    np.testing.assert_array_equal(result[2:], ortho[2:])
    np.testing.assert_array_equal(path_ground(tile, ortho, []), ortho)
