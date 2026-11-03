"""props.seating: benches against the survey and barriers; furniture@1's collision splice."""

import math
from collections import Counter

import numpy as np
import shapely

from turku3d.props import Prop, seating


def ground(x0, z0, x1, z1, step=0.15):
    xs, zs = np.meshgrid(np.arange(x0, x1, step), np.arange(z0, z1, step))
    pts = np.c_[xs.ravel(), np.zeros(xs.size), zs.ravel()]
    return pts, np.full(len(pts), 2)


def bench_returns(x, z, yaw, step=0.08):
    """Seat (0.45 m) and backrest (0.8 m, local -Z) returns of a 1.8 x 0.55 m bench."""
    c, s = math.cos(yaw), math.sin(yaw)
    pts = []
    for lx in np.arange(-0.85, 0.86, step):
        for lz in np.arange(-0.2, 0.26, step):
            pts.append((lx, 0.45, lz))
        pts.append((lx, 0.8, -0.24))
        pts.append((lx, 0.72, -0.24))
    local = np.asarray(pts)
    world = np.c_[x + c * local[:, 0] + s * local[:, 2], local[:, 1], z - s * local[:, 0] + c * local[:, 2]]
    return world, np.full(len(world), 4)


def cloud(*parts):
    return seating.Cloud(np.concatenate([p for p, _ in parts]), np.concatenate([c for _, c in parts]))


def bench(x, z, yaw=0.0, oid="GIS_Varusteet:a"):
    return Prop(oid, "bench", x, 0, z, yaw=yaw, source="GIS_Varusteet", orientation="nearest_road")


def angle(a, b):
    return abs((math.degrees(a - b) + 180) % 360 - 180)


def test_snaps_to_lidar_bench_and_backs_onto_hedge():
    hedge = shapely.LineString([(-10, -1.2), (10, -1.2)])  # behind the bench (smaller z)
    survey = cloud(ground(-8, -8, 8, 8), bench_returns(0.0, 0.0, 0.0))
    b = bench(2.0, 1.5, yaw=math.radians(120))
    stats = Counter()
    kept = seating.place([b], survey, [(hedge, {"barrier": "hedge"}, False)], stats=stats)
    assert kept == [b]
    assert math.hypot(b.x, b.z) < 0.15
    assert angle(b.yaw, 0.0) < 3  # front +z: away from the hedge
    assert b.adjusted == "lidar_snap" and b.orientation == "lidar_barrier_back"


def test_backrest_names_the_back_without_barriers():
    survey = cloud(ground(-8, -8, 8, 8), bench_returns(0.0, 0.0, math.pi / 2))
    b = bench(1.0, -1.0, yaw=0.3)
    seating.place([b], survey)
    assert b.orientation == "lidar_backrest"
    assert angle(b.yaw, math.pi / 2) < 5


def test_covered_point_without_bench_returns_is_dropped():
    survey = cloud(ground(-8, -8, 8, 8))
    stats = Counter()
    assert seating.place([bench(0, 0)], survey, stats=stats) == []
    assert stats["bench_lidar_absent"] == 1


def test_one_surveyed_bench_carries_one_record():
    survey = cloud(ground(-8, -8, 12, 8), bench_returns(0.0, 0.0, 0.0), (np.array([[1.0, 0.5, 1.0]] * 6), np.full(6, 4)))
    a, b = bench(0.5, 0.0, oid="GIS_Varusteet:a"), bench(1.2, 1.0, oid="osm:1")
    stats = Counter()
    kept = seating.place([a, b], survey, stats=stats)
    assert len(kept) == 1 and stats["bench_lidar_duplicate"] == 1


def test_tall_returns_above_are_not_a_bench():
    bush, cls = bench_returns(0.0, 0.0, 0.0)
    crown = bush.copy()
    crown[:, 1] = 1.6
    survey = cloud(ground(-8, -8, 8, 8), (bush, cls), (crown, cls))
    assert survey.blobs() == []


def test_without_survey_a_bench_backs_onto_the_nearest_barrier_and_leaves_it():
    hedge = shapely.LineString([(-10, 0.0), (10, 0.0)])
    b = bench(0.0, 0.2, yaw=math.radians(90))  # across the hedge
    kept = seating.place([b], None, [(hedge, {"barrier": "hedge"}, False)])
    assert kept == [b] and b.orientation == "barrier_back"
    assert angle(b.yaw, 0.0) < 1  # facing +z, the side it stood on
    assert abs(b.z - (seating.HALF["hedge"] + seating.BACK_GAP + seating.BENCH_BACK)) < 1e-6
    assert b.adjusted == "barrier_clearance"


def test_far_from_barriers_a_bench_faces_the_water():
    river = shapely.box(-20, 4, 20, 30)
    b = bench(0.0, 0.0, yaw=math.pi)
    seating.place([b], None, water=[river])
    assert b.orientation == "water_view" and angle(b.yaw, 0.0) < 1


def test_nothing_near_keeps_the_street_heading():
    b = bench(0.0, 0.0, yaw=1.0)
    stats = Counter()
    seating.place([b], None, stats=stats)
    assert b.yaw == 1.0 and stats["bench_street_heading"] == 1


def test_splice_primitives_keeps_triangle_sections():
    from turku3d.col.format import ColTile, Primitive, read_col, write_col
    from turku3d.col.materials import mat_id
    from turku3d.migrations.furniture import splice_primitives

    positions = np.array([[0, 0, 0], [10, 0, 0], [0, 0, 10]], np.float32)
    keep_box = Primitive.box(mat_id("street_furniture"), (1, 0.25, 1), (0.9, 0.25, 0.28))
    drop_box = Primitive.box(mat_id("street_furniture"), (5, 0.25, 5), (0.9, 0.25, 0.28))
    tile = ColTile.for_tile(0, 0, positions, np.array([[0, 2, 1]], np.uint32),
                            np.array([mat_id("grass")], np.uint8), primitives=[keep_box, drop_box])
    data = write_col(tile, encoding="raw")
    added = Primitive.box(mat_id("street_furniture"), (7, 0.25, 7), (0.9, 0.25, 0.28))
    out = splice_primitives(data, lambda p: p.center[0] != 5, [added])
    back = read_col(out)
    assert [p.center[0] for p in back.primitives] == [1, 7]
    np.testing.assert_array_equal(back.positions, positions)
    assert data[96 + 24 * 0: 96 + 24 * 0 + 4] == out[96: 100]  # POSN section first, as before
