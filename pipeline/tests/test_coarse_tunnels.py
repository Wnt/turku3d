"""Synthetic tunnel profile, ground-cut and passage rules; no tile packaging."""

import json

import numpy as np
import shapely
from turku3d import config as C
from turku3d.coarse.build import extrude
from turku3d.coarse.inputs import Building
from turku3d.coarse.tunnels import cut_required, dimensions, floor_profile, produce, selected
from turku3d.roads import build_graph, encode


def test_selection_and_clearance():
    assert selected({"highway": "footway", "tunnel": "building_passage"})
    assert selected({"railway": "rail", "tunnel": "yes"})
    assert selected({"highway": "residential", "covered": "yes", "layer": "-1"})
    assert not selected({"highway": "residential", "covered": "yes", "layer": "0"})
    assert not selected({"waterway": "stream", "tunnel": "culvert"})
    assert dimensions({"highway": "footway"}) == (3, 2.5)
    assert dimensions({"highway": "residential", "width": "8 m"}) == (8, 4.5)


def test_measured_dip_and_buried_profile():
    d = np.arange(5)
    np.testing.assert_equal(floor_profile(np.array([5, 3, 2, 3, 5]), d), [5, 3, 2, 3, 5])
    np.testing.assert_equal(floor_profile(np.array([5, 9, 10, 9, 6]), d), [5, 5.25, 5.5, 5.75, 6])
    np.testing.assert_equal(cut_required(np.array([2, 4, 7]), 2, 2.5), [True, True, False])


def scene(height=2, passage=False):
    tile = C.VisualTile(0, 0, 0)
    pos = np.array([[0, height, 0], [20, height, 0], [0, height, 20], [20, height, 20]], float)
    idx = np.array([[0, 2, 1], [1, 2, 3]], np.uint32)
    line = shapely.LineString([(0, 10), (20, 10)])
    tags = {"highway": "service", "width": "4", "tunnel": "building_passage" if passage else "yes"}
    building = Building("b", shapely.box(5, 5, 15, 15), 2, 14, "facade_plaster")
    shells = [(building, extrude(building))] if passage else []
    return produce(tile, [("test", line, tags)], pos, idx, shells, extrude, lambda x, z: 2)


def test_ground_cut_preserves_outside_and_roof_seals_inside():
    result = scene()
    assert not result.keep.any()
    edge = next(m for m in result.meshes if m.object_id.endswith("ground-edge"))
    tris = edge.positions[edge.indices][:, :, [0, 2]]
    assert abs(sum(shapely.Polygon(t).area for t in tris) - 320) < 1e-8
    roof = next(m for m in result.meshes if m.object_id.endswith(":roof"))
    assert roof.positions[:, 1].min() == 6.5
    assert result.collision(
        C.CollisionTile(0, 0), np.zeros((0, 3)), np.zeros((0, 3), np.uint32), np.zeros(0, np.uint8)
    )[1].size
    # Overburden above the tube remains available to walk on.
    assert scene(height=10).keep.all()


def test_building_passage_keeps_upper_shell_and_road_floor_metadata():
    result = scene(passage=True)
    assert len(result.shells) == 3
    for b, _ in result.shells:
        if b.footprint.contains(shapely.Point(10, 10)):
            assert b.base >= 6.5
    graph = build_graph([([(0, 10), (20, 10)], 3)], [0, 0, 20, 20], "test", connected=False)
    roads = result.roads({"json": encode(graph)})
    encoded = json.loads(roads["json"])
    assert encoded["edgeHeights"] == [[2] * 11]
    assert len(encoded["edges"][0]) // 2 == len(encoded["edgeHeights"][0])
