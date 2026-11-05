"""Drivable roadbed rules (roads.roadbed, roadbed@1) and the bridge-end rules (bridge-ends@1)."""

import hashlib
import json

import numpy as np
import shapely

from turku3d.coarse.bridge_join import CURVE_KAPPA, vertical_curve, vertical_curve_rise
from turku3d.coarse.bridges import clear_of_lanes
from turku3d.roads import roadbed as RB


def graph(points, heights, *, width=7.0, flags=0, mode=RB.CAR | 1):
    """One car edge along ``points`` (x, z) with per-vertex ``heights``."""
    points = [list(map(float, p)) for p in points]
    columns = ["way", "s0", "s1", "length", "lanes_fwd", "lanes_bwd", "width", "kmh", "car_density",
               "foot_density", "flags", "highway", "setback0", "setback1"]
    length = float(shapely.LineString(points).length)
    return {
        "revision": 2,
        "nodes": [*points[0], *points[-1]],
        "edges": [[0, 1, mode, 0, *[v for p in points[1:-1] for v in p]]],
        "edgeIds": ["e"], "edgeEvents": [None], "nodeFlags": [0, 0], "controllers": [],
        "edgeAttrColumns": columns,
        "edgeAttrs": [[1, 0, length, length, 1, 1, width, 30, 0, 0, flags, 4, 0, 0]],
        "edgeHeights": [list(map(float, heights))],
        "bits": {"edge_flags": {"PAVED": 1, "BRIDGE": 4, "TUNNEL": 8}},
    }


def slab_floor(cells, y):
    """Floor triangles from (x0, z0, x1, z1) rectangles at height ``y`` (scalar or callable of x)."""
    tris = []
    for x0, z0, x1, z1 in cells:
        def h(x):
            return y(x) if callable(y) else y
        a, b, c, d = (x0, h(x0), z0), (x1, h(x1), z0), (x1, h(x1), z1), (x0, h(x0), z1)
        tris += [[a, c, b], [a, d, c]]
    return np.asarray(tris, np.float64)


def floor_of(rectangles, y=5.0):
    from turku3d.col.materials import mat_id

    tris = slab_floor(rectangles, y)
    return RB.Floor(tris, np.full(len(tris), mat_id("road")))


BBOX = (-50.0, -50.0, 200.0, 200.0)


def test_vertical_curve_meets_deck_and_ground_grades_without_breaks():
    s = np.linspace(0, 25, 2501)
    h = vertical_curve(s, 25.0, 5.92, 4.65, -0.01, 0.0)
    assert abs(h[0] - 5.92) < 1e-9 and abs(h[-1] - 4.65) < 1e-9
    slope = np.gradient(h, s)
    assert abs(slope[0] + 0.01) < 1e-3 and abs(slope[-1]) < 1e-3
    curvature = np.abs(np.gradient(slope, s))
    assert curvature.max() < 1.05 * 6 * (5.92 - 4.65) / 25.0**2
    # The reach rule picks a length whose curve stays within the comfort curvature.
    assert vertical_curve_rise(26.0) >= 1.27 and vertical_curve_rise(16.0) < 1.27
    assert abs(vertical_curve_rise(10.0) - CURVE_KAPPA * 100 / 6) < 1e-12


def test_deck_lamp_moves_off_the_lanes_or_is_not_built_without_room():
    deck = shapely.box(0, -12, 40, 12)
    lanes = [dict(line=shapely.LineString([(0, -4.4), (40, -4.4)]), width=10.75),
             dict(line=shapely.LineString([(0, 4.4), (40, 4.4)]), width=10.75)]
    kerb = ([10.0, -10.0], {"highway": "street_lamp"})  # outer edge, outside both lanes
    edge = ([20.0, -9.5], {})  # 0.4 m into the outer lane: moves out to the kerb
    median = ([30.0, 0.0], {})  # mapped lanes overlap the median: no room within 3 m
    out = clear_of_lanes([kerb, edge, median], deck, lanes)
    assert out[0] == kerb
    moved = shapely.Point(out[1][0])
    assert len(out) == 2 and moved.distance(shapely.Point(edge[0])) <= 3.0
    assert not shapely.union_all([w["line"].buffer(w["width"] / 2 - 0.5 + 0.2) for w in lanes]).contains(moved)


def test_a_trench_across_the_lane_gets_a_slab_at_the_level_of_its_rims():
    # Road on both sides of a 1 m gap at x 20..21; the lane runs along x.
    floor = floor_of([(0, -5, 20, 5), (21, -5, 50, 5)])
    g = graph([(2, 0), (48, 0)], [5.0, 5.0], width=6.0)
    stats = __import__("collections").Counter()
    found = RB.slabs(g, floor, BBOX, stats)
    pits = [f for f in found if f[3] == "pit"]
    assert stats["pit_slabs"] == len(pits) > 0
    for centre, half, rotation, _ in pits:
        top = centre[1] + half[1]
        assert abs(top - 5.0) < 1e-3 and 19.5 < centre[0] < 21.5
        assert abs(np.linalg.norm(rotation) - 1) < 1e-9


def test_open_ground_off_the_carriageway_and_long_openings_are_left_alone():
    # A 6 m gap is an opening (a lower crossing), never bridged.
    floor = floor_of([(0, -5, 20, 5), (26, -5, 50, 5)])
    g = graph([(2, 0), (48, 0)], [5.0, 5.0], width=6.0)
    stats = __import__("collections").Counter()
    assert [f for f in RB.slabs(g, floor, BBOX, stats) if f[3] == "pit"] == []


def test_a_step_on_a_bridge_lane_gets_a_ramp_on_its_low_side_and_not_on_a_street():
    floor = floor_of([(0, -5, 20, 5)], 5.0)
    high = floor_of([(20, -5, 50, 5)], 5.12)
    both = RB.Floor(np.concatenate([floor.triangles, high.triangles]),
                    np.concatenate([floor.materials, high.materials]))
    street = graph([(2, 0), (48, 0)], [5.0, 5.12], width=6.0)
    deck = graph([(2, 0), (48, 0)], [5.0, 5.12], width=6.0, flags=4)
    stats = __import__("collections").Counter()
    assert [f for f in RB.slabs(street, both, BBOX, stats) if f[3] == "step"] == []
    ramps = [f for f in RB.slabs(deck, both, BBOX, stats) if f[3] == "step"]
    assert ramps
    for centre, half, _, _ in ramps:
        assert centre[0] < 20.2  # on the low side, before the step
        assert 2 * half[0] >= 0.12 / RB.STEP_GRADE - 0.5


def test_lane_heights_that_jumped_to_a_roof_go_back_onto_the_floor():
    floor = RB.Floor(np.concatenate([slab_floor([(0, -5, 40, 5)], 9.6), slab_floor([(10, -5, 20, 5)], 35.2)]))
    points = [(x, 0) for x in range(2, 39)]
    heights = [35.15 if 12 <= x <= 15 else 9.6 for x, _ in points]
    g = graph(points, heights)
    stats = __import__("collections").Counter()
    RB.repair_heights(g, floor, stats)
    assert max(g["edgeHeights"][0]) < 9.7 and stats["lane_height_vertices"] == 4
    ramp = graph([(x, 0) for x in range(0, 40, 2)], [9.6 + 0.15 * x for x in range(0, 40, 2)])
    before = list(ramp["edgeHeights"][0])
    RB.repair_heights(ramp, floor, stats)
    assert ramp["edgeHeights"][0] == before  # a steady grade is the lane's own


def test_roadbed_migration_adds_slabs_to_the_cells_and_records_counts(tmp_path):
    from turku3d.col.format import ColTile, read_col, write_col
    from turku3d.col.materials import mat_id
    from turku3d.migrations import roadbed
    from turku3d.migrations.core import Product

    tris = slab_floor([(1, 1, 20, 30), (21, 1, 60, 30)], 5.0)
    positions = tris.reshape(-1, 3)
    cell = ColTile.for_tile(0, 0, positions, np.arange(len(positions)).reshape(-1, 3),
                            np.full(len(tris), mat_id("road")))
    data = write_col(cell, encoding="raw")
    digest = hashlib.sha256(data).hexdigest()
    (tmp_path / "col").mkdir()
    (tmp_path / "col" / f"c_0_0.{digest}.col").write_bytes(data)
    g = graph([(3, 15), (58, 15)], [5.0, 5.0], width=6.0)
    g["nodes"] = [3.0, 15.0, 58.0, 15.0]
    text = (json.dumps(g) + "\n").encode()
    rd = hashlib.sha256(text).hexdigest()
    (tmp_path / f"roads.{rd}.json").write_bytes(text)
    manifest = dict(tile="t0_0_0", roads=dict(url=f"roads.{rd}.json", sha256=rd, bytes=len(text)),
                    collision={"c_0_0": dict(url=f"col/c_0_0.{digest}.col", sha256=digest, bytes=len(data),
                                             prims=0, h=[5.0, 5.0])})
    pristine = json.loads(json.dumps(manifest))
    product = Product(tmp_path, manifest)
    roadbed.transform(product)
    entry = product.manifest["collision"]["c_0_0"]
    assert entry["sha256"] != digest and entry["prims"] == product.manifest["roadbed"]["pit_slabs"] > 0
    out = read_col(product.read(entry["url"]))
    assert len(out.primitives) == entry["prims"] and len(out.indices) == len(cell.indices)
    # Deterministic: the same product gives the same bytes.
    again = Product(tmp_path, pristine)
    roadbed.transform(again)
    assert again.manifest["collision"]["c_0_0"]["sha256"] == entry["sha256"]


def test_a_bridge_deck_narrower_than_its_lanes_is_widened_at_its_own_level():
    # Deck 2 m wide (z -1..1) under a one-lane bridge edge whose lanes sweep 2.65 m each side.
    floor = RB.Floor(np.concatenate([slab_floor([(0, -1, 40, 1)], 8.0), slab_floor([(0, -6, 40, 6)], 6.5)]))
    from turku3d.col.materials import mat_id
    floor.materials[:] = mat_id("bridge_deck")
    deck = graph([(2, 0), (38, 0)], [8.0, 8.0], width=4.25, flags=4)
    deck["edgeAttrs"][0][4:6] = [1, 0]
    stats = __import__("collections").Counter()
    slabs = [f for f in RB.slabs(deck, floor, BBOX, stats) if f[3] == "deck"]
    assert slabs and stats["deck_slabs"] == len(slabs)
    for centre, half, _, _ in slabs:
        assert abs(centre[1] + half[1] - 8.0) < 1e-3 and 1.0 <= abs(centre[2]) <= 2.7
    street = graph([(2, 0), (38, 0)], [8.0, 8.0], width=4.25)
    assert [f for f in RB.slabs(street, floor, BBOX, stats) if f[3] == "deck"] == []


def test_a_skew_road_split_under_a_deck_passes_under_it():
    from turku3d.coarse.bridges import passes_under

    deck = dict(id="deck", line=shapely.LineString([(0, 0), (100, 0)]), layer=1, bridge=True, width=12.0)
    # 18 deg off the deck, and OSM split it right under the deck: the crossing is at its end.
    a = (50 - 30 * np.cos(np.radians(18)), -30 * np.sin(np.radians(18)))
    road = dict(id="road", tags={"highway": "trunk"}, line=shapely.LineString([a, (50, 0)]), layer=0, bridge=False)
    longer = dict(road, line=shapely.LineString([a, (50 + 30 * np.cos(np.radians(18)), 30 * np.sin(np.radians(18)))]))
    assert passes_under(deck, longer)
    along = dict(road, line=shapely.LineString([(40, -0.5), (60, 0.5)]))  # 3 deg: runs along the deck
    assert not passes_under(deck, along)


def test_a_link_leaving_a_deck_follows_its_own_profile_down_to_the_ground():
    from turku3d.coarse.bridges import branch_height, branch_profiles

    main = dict(id="main", line=shapely.LineString([(0, 0), (100, 0)]), width=10.0)
    link = dict(id="link", line=shapely.LineString([(40, 3), (60, 20), (90, 40)]), width=3.5)
    way = dict(main, member_ways=[dict(main), link])
    profiles, _ = branch_profiles(way, lambda xz: np.full(len(xz), 25.7), lambda xz: np.full(len(xz), 18.0))
    assert len(profiles) == 1
    line, stations, heights, _ = profiles[0]
    assert abs(heights[0] - 25.7) < 1e-9 and abs(heights[-1] - 18.02) < 1e-9
    assert np.all(np.diff(heights) <= 1e-9)  # one descent, no dip into the ground and back
    mid = np.asarray(line.interpolate(line.length / 2).coords)
    assert 18.0 < branch_height(profiles, mid, np.array([0.0]))[0] < 25.7


def test_only_lamps_on_a_lower_road_under_a_deck_are_dropped():
    from turku3d.coarse.furniture import deck_cover

    lower = dict(line=shapely.LineString([(0, -30), (0, 30)]), width=7.0, tags={"highway": "trunk"})
    deck = dict(line=shapely.LineString([(-40, 0), (40, 0)]), width=12.0, under=[lower], below=[])
    cover = deck_cover({"bridge_ways": [deck]})
    assert cover.covers(shapely.Point(4.5, 2))  # kerbside of the lower road, under the deck
    assert not cover.covers(shapely.Point(20, 2))  # on the deck away from the lower road: stays
    assert deck_cover({"bridge_ways": [dict(deck, under=[])]}) is None


def test_bridge_end_rebuilds_include_tiles_with_only_a_lower_road():
    from turku3d.migrations.bridge_ends import affected
    from turku3d.migrations.bridge_ends_tiles import TILES

    assert affected({"tile": "t0_99_99", "bridges": 2})
    assert not affected({"tile": "t0_99_99", "bridges": 0})
    assert all(affected({"tile": t, "bridges": 0}) for t in list(TILES)[:5])
