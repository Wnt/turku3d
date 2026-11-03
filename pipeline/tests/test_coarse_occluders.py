"""Occluder boxes stay inside opaque LoD2 volumes and never seal holes or glass."""
from types import SimpleNamespace

import numpy as np
import shapely

from turku3d.coarse.occluders import building_occluders
from turku3d.package.gltf import GlbMesh


def fixture(footprint, roof=15.0):
    building = SimpleNamespace(footprint=footprint, base=2, facade_key="test", attributes={})
    points = np.asarray([[-50, roof, -50], [70, roof, -50], [70, roof, 70], [-50, roof, 70]], float)
    walls = np.zeros((4, 3))
    shell = (points, np.array([[0, 1, 2], [0, 2, 3]]), None, None, walls, None, np.array([[0, 1, 2]]))
    return building, shell


def mesh(**extras):
    return GlbMesh("wall", np.zeros((3, 3)), np.array([[0, 1, 2]]), extras=dict(layer="buildings", **extras))


def footprint_of(row, origin):
    """World xz rectangle of a yawed model-local box (spec: x = u cos - v sin, z = u sin + v cos)."""
    c, s = np.cos(row["yaw"]), np.sin(row["yaw"])
    (u0, _, v0), (u1, _, v1) = row["min"], row["max"]
    points = [(origin[0] + u * c - v * s, origin[2] + u * s + v * c) for u, v in ((u0, v0), (u1, v0), (u1, v1), (u0, v1))]
    return shapely.Polygon(points)


def no_survey(monkeypatch):
    monkeypatch.setattr("turku3d.facade_programs.measured", lambda _: None)


def test_boxes_are_model_local_inside_the_inset_footprint_above_the_ground_storey(monkeypatch):
    no_survey(monkeypatch)
    footprint = shapely.affinity.rotate(shapely.box(1, 1, 19, 9), 30, origin=(10, 5))
    data = building_occluders([fixture(footprint)], [mesh(material_key="facade_kit")], [10, 0, 5])
    assert data["provenance"] == "lod2-inset-boxes-v1"
    assert data["boxes"]
    area = 0
    for row in data["boxes"]:
        polygon = footprint_of(row, [10, 0, 5])
        assert footprint.buffer(-.249).covers(polygon)
        assert row["min"][1] == 6.25 and row["max"][1] == 14.75
        area += polygon.area
    # Oriented boxes fill most of a rotated block instead of an axis-aligned staircase.
    assert area > .8 * footprint.buffer(-.25).area


def test_courtyards_concavities_and_surveyed_passages_stay_open(monkeypatch):
    no_survey(monkeypatch)
    passage = shapely.box(2, 0, 6, 20)
    monkeypatch.setattr("turku3d.facade_apertures.volumes", lambda _: [(passage, 2, 8)])
    footprint = shapely.box(0, 0, 20, 20).difference(shapely.box(10, 10, 20, 20)).difference(shapely.box(8, 4, 12, 8))
    data = building_occluders([fixture(footprint)], [mesh()], [0, 0, 0])
    assert data["boxes"]
    for row in data["boxes"]:
        low, high = row["min"], row["max"]
        polygon = footprint_of(row, [0, 0, 0])
        assert footprint.buffer(-.249).covers(polygon)
        assert not passage.intersects(polygon)
        assert low[1] >= 6.25 and high[1] <= 14.75


def test_box_tops_follow_the_roof_above_each_area(monkeypatch):
    no_survey(monkeypatch)
    building, shell = fixture(shapely.box(0, 0, 40, 20))
    # A low annex roof over x < 10 must not cap the tall part's boxes, nor be exceeded.
    roof = np.asarray([[0, 9, 0], [10, 9, 0], [10, 9, 20], [0, 9, 20], [10, 20, 0], [40, 20, 0], [40, 20, 20], [10, 20, 20]], float)
    shell = (roof, np.array([[0, 1, 2], [0, 2, 3], [4, 5, 6], [4, 6, 7]]), *shell[2:])
    data = building_occluders([(building, shell)], [mesh()], [0, 0, 0])
    tops = sorted({row['max'][1] for row in data['boxes']})
    assert tops == [8.75, 19.75]
    for row in data['boxes']:
        polygon = footprint_of(row, [0, 0, 0])
        if row['max'][1] > 9:
            assert polygon.bounds[0] >= 10 - 1e-6


def test_glass_translucent_low_or_unclosed_buildings_emit_nothing(monkeypatch):
    no_survey(monkeypatch)
    part = fixture(shapely.box(0, 0, 20, 20))
    assert not building_occluders([part], [mesh(material_key="glass_curtain")], [0, 0, 0])["boxes"]
    clear = mesh(); clear.color = (1, 1, 1, .5)
    assert not building_occluders([part], [clear], [0, 0, 0])["boxes"]
    assert not building_occluders([part], [], [0, 0, 0])["boxes"]
    assert not building_occluders([fixture(shapely.box(0, 0, 20, 20), roof=7)], [mesh()], [0, 0, 0])["boxes"]
    building, shell = part
    assert not building_occluders([(building, (*shell[:6], np.empty((0, 3), int)))], [mesh()], [0, 0, 0])["boxes"]
    assert not building_occluders([part], [mesh()], [float("nan"), 0, 0])["boxes"]
