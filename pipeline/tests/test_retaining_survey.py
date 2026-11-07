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


def test_tall_city_garden_retaining_walls_are_not_limited_to_quay_ledge_height():
    result=records(step=4)
    assert result
    assert all(np.median(abs(np.diff(tags['_survey']['heights'],axis=1)))>3.9 for _,tags,_ in result)


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
    meshes, cuts = retaining.produce(VisualTile(0, 0, 0), [record], lambda x,z: 1.2)
    assert any(not m.metadata.get("terrain") for m in meshes)
    assert any(m.metadata.get("terrain") for m in meshes)
    assert cuts
    assert max(m.positions[:, 1].max() for m in meshes) == 2


def test_apron_outer_rails_match_existing_terrain_instead_of_extrapolating_a_plane():
    survey = records()
    sample = lambda x,z: 4 + 0.01*x + 0.1*z*z
    meshes, _ = retaining.produce(VisualTile(0, 0, 0), survey, sample)
    apron = next(m for m in meshes if m.metadata.get("terrain"))
    outer = np.isclose(abs(apron.positions[:, 2]), 2.5)
    assert outer.any()
    p = apron.positions[outer]
    np.testing.assert_allclose(p[:, 1], [sample(x,z) for x,_,z in p])


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


def test_surveyed_ledge_edge_is_straight_and_parallel_while_lidar_sets_levels():
    """Per-section lidar choices must not zig-zag a straight quay ledge (issue 024)."""
    rng = np.random.default_rng(7)
    x, z = np.meshgrid(np.arange(-4, 45, 0.2), np.arange(-4, 4.01, 0.2))
    x, z = x.ravel(), z.ravel()
    # A straight 1.7 m quay step 0.15 m off the surveyed line, a 1 % ramp, lidar noise
    # and seated people along the cap.
    height = 1.1 + 0.01 * x + np.where(z > 0.15, 1.7, 0) + rng.normal(0, 0.02, len(x))
    people = (np.abs(x % 7 - 3) < 0.3) & (z > 0.3) & (z < 0.9)
    height[people] += 1.0
    points = np.c_[x, height, z]
    # A surveyed line with a vertex every 5 m, like WFS structure lines.
    xs = np.arange(0, 41, 5.0)
    line = shapely.LineString(np.c_[xs, np.zeros(len(xs))])
    result = retaining_survey.measure(line, points)
    assert result
    for _, profile in result:
        centres = np.asarray(profile["centres"])
        # One straight edge parallel to the survey: no per-section lateral jumps.
        assert np.ptp(centres[:, 1]) < 0.16
        assert np.abs(np.diff(centres[:, 1], 2)).max() < 0.05
        heights = np.asarray(profile["heights"])
        np.testing.assert_allclose(np.diff(heights, axis=1), 1.7, atol=0.12)
        # Levels follow the ramp smoothly; people never lift single sections.
        assert np.abs(np.diff(heights, 2, axis=0)).max() < 0.05
        np.testing.assert_allclose(heights[:, 0], 1.1 + 0.01 * centres[:, 0], atol=0.08)


def test_measured_ledge_run_continues_to_an_adjacent_flight_only():
    from turku3d.coarse.retaining import FLIGHT_REACH, reach_flights

    x = np.arange(0, 10.1, 2.0)
    centres = np.c_[x, np.zeros(len(x))]
    profile = (centres, centres - [0, 2.5], centres + [0, 2.5])
    levels = np.vstack([np.full(len(x), 1.2), np.full(len(x), 3.0)])
    # The survey stopped 3 m short of a flight: the ledge runs on into it.
    flight = shapely.box(13, -3, 16, 3)
    (c, left, right), extended = reach_flights(profile, levels, flight)
    assert len(c) == len(x) + 1 and flight.contains(shapely.Point(c[-1]))
    np.testing.assert_allclose(c[-1], [13.5, 0])
    np.testing.assert_allclose(left[-1], [13.5, -2.5])
    np.testing.assert_allclose(extended[:, -1], [1.2, 3.0])
    # Nothing within reach of either end: the measured run is unchanged.
    far = shapely.box(10 + FLIGHT_REACH + 1, -3, 30, 3)
    (c, _, _), same = reach_flights(profile, levels, far)
    np.testing.assert_allclose(c, centres)
    np.testing.assert_allclose(same, levels)


def test_unsurveyed_wall_at_a_hill_foot_keeps_its_step_and_aprons_follow_the_slope():
    # A mapped 1.5 m wall along a street, with a 40 degree slope rising behind it.
    slope = np.tan(np.radians(40))
    sample = lambda x, z: 0.0 if z < 0 else 1.5 + slope * z
    record = (shapely.LineString([(5, 0), (25, 0)]), {"barrier": "retaining_wall"}, False)
    meshes, _ = retaining.produce(VisualTile(0, 0, 0), [record], sample)
    wall = next(m for m in meshes if not m.metadata.get("terrain"))
    # Before, the upper rail 2.5 m up the slope set the wall top: a 3.6 m wall.
    np.testing.assert_allclose(wall.positions[:, 1].max(), 1.5, atol=1e-9)
    apron = next(m for m in meshes if m.metadata.get("terrain"))
    upper = apron.positions[apron.positions[:, 2] > 1e-6]
    np.testing.assert_allclose(upper[:, 1], [sample(x, z) for x, _, z in upper], atol=1e-9)


def test_a_bgt_wall_over_a_ramp_drop_stands_as_a_parapet_on_the_street_side():
    """Amsterdam Rokin: the garage ramps' BGT walls; a car on the street meets a parapet, not a 2 m drop."""
    deep = [(g, {**t, "_parapet": retaining_survey.BGT_PARAPET}, f) for g, t, f in records(step=2.0)]
    meshes, _ = retaining.produce(VisualTile(0, 0, 0), deep, lambda x, z: 1.2)
    walls = [m for m in meshes if not m.metadata.get("terrain")]
    upper = max(np.max(t["_survey"]["heights"]) for _, t, _ in deep)
    top = max(m.positions[:, 1].max() for m in walls)
    assert abs(top - (upper + retaining_survey.BGT_PARAPET)) < 0.15
    above = np.concatenate([m.positions[m.positions[:, 1] > upper + 0.1] for m in walls])
    assert (above[:, 2] >= -1e-6).all() and (above[:, 2] <= retaining.PARAPET_THICKNESS + 1e-6).all()  # street side
    # A shallow step (a ramp mouth, a kerb-high terrace) keeps no parapet, and neither does an unflagged survey wall.
    shallow = [(g, {**t, "_parapet": retaining_survey.BGT_PARAPET}, f) for g, t, f in records(step=0.6)]
    for survey in (shallow, records(step=2.0)):
        meshes, _ = retaining.produce(VisualTile(0, 0, 0), survey, lambda x, z: 1.2)
        heights = max(np.max(t["_survey"]["heights"]) for _, t, _ in survey)
        assert max(m.positions[:, 1].max() for m in meshes if not m.metadata.get("terrain")) <= heights + 1e-6


def test_upper_side_is_the_higher_survey_column():
    (_, tags, _), = records(step=2.0)  # the cloud rises towards +z, the survey's right
    assert retaining_survey.upper_side(tags["_survey"]) == 1
