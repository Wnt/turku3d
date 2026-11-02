"""Measured quay ledges survive smoothing/masks without copying transient returns."""

import numpy as np
import shapely

from turku3d.config import VisualTile
from turku3d.coarse import retaining, retaining_survey


def cloud(step=0.8, objects=False):
    x, z = np.meshgrid(np.arange(-4, 25, 0.2), np.arange(-4, 4.01, 0.2))
    x, z = x.ravel(), z.ravel()
    height = 1.2 + 0.002*x + np.where(z > 0, step + 0.22*z, 0)
    points = np.c_[x, height, z]
    if objects:
        # Dense misclassified people atop the cap do not replace its plane.
        extra = points[(abs(x-10) < 0.3) & (z > 0.1) & (z < 0.8)].copy()
        extra[:, 1] += 1.1
        points = np.r_[points, extra, extra + [0.01, 0, 0.01]]
    return points


def records(step=0.8, objects=False):
    line = shapely.LineString([(0, 0), (20, 0)])
    return [(g, {"barrier": "retaining_wall", "_survey": p}, False)
            for g, p in retaining_survey.measure(line, cloud(step, objects))]


def test_sustained_step_uses_two_ground_planes_and_rejects_seated_people():
    base, noisy = records(), records(objects=True)
    assert len(base) == len(noisy) == 1
    for result in (base, noisy):
        data = result[0][1]["_survey"]
        np.testing.assert_allclose(np.diff(data["heights"], axis=1), 0.8, atol=0.08)
        assert np.max(data["heights"][:, 1]) < 2.2


def test_continuous_grass_slope_does_not_become_a_wall():
    points = cloud(step=0)
    points[:, 1] = 1.2 + points[:, 0]*0.002 + points[:, 2]*0.4
    assert retaining_survey.measure(shapely.LineString([(0, 0), (20, 0)]), points) == []


def test_measured_ledge_survives_water_and_broad_paving_masks_but_not_stairs():
    tile = VisualTile(0, 0, 0)
    survey = records()
    water = [shapely.box(-10, -10, 30, -1)]
    path = [shapely.box(-2, -2, 22, 2)]
    stairs = [shapely.box(8, -3, 12, 3)]
    meshes, cuts = retaining.produce(tile, survey, lambda x,z: -99,
                                     stairs=stairs, water=water, paving=path)
    walls = [m for m in meshes if not m.metadata.get("terrain")]
    assert walls and cuts
    for mesh in walls:
        centres = mesh.positions[mesh.indices].mean(axis=1)
        assert not shapely.contains_xy(stairs[0], centres[:, 0], centres[:, 2]).any()
        assert mesh.positions[:, 1].min() > 1
        assert mesh.positions[:, 1].max() < 2.2
        normal = np.cross(mesh.positions[mesh.indices[:, 1]]-mesh.positions[mesh.indices[:, 0]],
                          mesh.positions[mesh.indices[:, 2]]-mesh.positions[mesh.indices[:, 0]])
        np.testing.assert_allclose(normal[:, 1], 0, atol=1e-10)  # vertical wall/collision faces


def test_landing_bend_has_supported_wall_faces_and_both_ground_aprons():
    # A surveyed wall turns around a small landing, using the same emitter as
    # long quay ledges. Missing raw evidence cannot create an extension.
    line = shapely.LineString([(5, 10), (15, 10), (15, 15)])
    centres, left, right = retaining.sections(line)
    levels = np.tile([1.2, 2.0], (len(centres), 1))
    record = (line, {"barrier": "retaining_wall", "_survey": dict(
        centres=centres, left=left, right=right, heights=levels, outer=levels)}, False)
    meshes, cuts = retaining.produce(VisualTile(0, 0, 0), [record], lambda x,z: -99)
    assert any(not m.metadata.get("terrain") for m in meshes)
    assert any(m.metadata.get("terrain") for m in meshes)
    assert cuts
    assert max(m.positions[:, 1].max() for m in meshes) == 2


def test_missing_or_one_sided_lidar_does_not_invent_a_ledge():
    line = shapely.LineString([(0, 0), (20, 0)])
    points = cloud()
    assert retaining_survey.measure(line, points[points[:, 2] > 1.5]) == []


def test_overlap_class_canopy_and_people_are_not_ground_but_step_sides_survive():
    from turku3d.lidar import PointCloud

    ground = cloud()
    near = ground + [0.01, 0.02, 0.01]
    above = ground + [0, 8, 0]
    people = ground[(abs(ground[:, 0]-10)<0.3) & (ground[:, 2]>0)] + [0, 1, 0]
    cloud_data = PointCloud.from_xyz(np.r_[ground, near, above, people])
    cloud_data.records["classification"][len(ground):] = 12
    result = retaining_survey.ground_points(cloud_data)
    assert len(result) == len(ground)+len(near)
    np.testing.assert_array_equal(result[:len(ground)], ground)


def test_emitted_vertical_faces_reach_collision_and_aprons_do_not_fill_water():
    from turku3d.config import CollisionTile
    from turku3d.coarse import stairs

    wet = shapely.box(0, -10, 20, -1)
    meshes, cuts = retaining.produce(VisualTile(0, 0, 0), records(), lambda x,z: -99, water=[wet])
    assert shapely.union_all(cuts).intersection(wet).area < 1e-8
    walls = [m for m in meshes if not m.metadata.get("terrain")]
    p, ix, _ = stairs.collision(CollisionTile(0, 0), walls, np.empty((0,3)),
                                np.empty((0,3), np.uint32), np.empty(0,np.uint8))
    assert len(ix) > 0
    assert np.ptp(p[:, 1]) > 0.7


def test_survey_code_invalidates_visual_and_collision_content(monkeypatch):
    from pathlib import Path
    from turku3d.coarse import collision_key, packaging

    path = Path(retaining_survey.__file__)
    read = Path.read_bytes
    before = packaging.code_hash(), collision_key.code_hash()
    monkeypatch.setattr(Path, "read_bytes", lambda p: read(p)+b"\n# change\n" if p==path else read(p))
    assert packaging.code_hash() != before[0]
    assert collision_key.code_hash() != before[1]
