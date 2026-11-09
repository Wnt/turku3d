import math

import numpy as np
from turku3d.coarse import wires as W
from turku3d.migrations import wires as migration
from turku3d.migrations.core import Product

RNG = np.random.default_rng(7)


def catenary(a, b, c, step=0.12, noise=0.01):
    """Returns along a catenary of parameter ``c`` through plan points a -> b (x, y, z)."""
    a, b = np.asarray(a, float), np.asarray(b, float)
    length = math.hypot(*(b - a)[[0, 2]])
    s = np.arange(0, length, step)
    # Lowest point placed so that the curve passes through both end heights.
    dh = b[1] - a[1]
    m = length / 2 - c * math.asinh(dh / (2 * c * math.sinh(length / (2 * c))))
    y = a[1] + c * (np.cosh((s - m) / c) - np.cosh(m / c))
    t = s / length
    p = a[None, :] + (b - a)[None, :] * t[:, None]
    p[:, 1] = y
    return p + RNG.normal(0, noise, p.shape)


def wall(x, z0, z1, top, step=0.25):
    zz, yy = np.meshgrid(np.arange(z0, z1, step), np.arange(0, top, step))
    return np.c_[np.full(zz.size, x), yy.ravel(), zz.ravel()]


def ground(x0, z0, x1, z1, step=0.5):
    xx, zz = np.meshgrid(np.arange(x0, x1, step), np.arange(z0, z1, step))
    return np.c_[xx.ravel(), np.zeros(xx.size), zz.ravel()]


def scene(*parts):
    xyz = np.concatenate([p for p, _ in parts])
    classes = np.concatenate([np.full(len(p), c, np.uint8) for p, c in parts])
    return xyz, classes


def test_profile_fit_recovers_the_catenary_sag():
    c = 60.0
    points = catenary((0, 12, 0), (24, 12.5, 0), c, noise=0.005)
    piece = W.fit_piece(points)
    assert piece is not None
    wire = W.Wire(piece.at(piece.s0), piece.at(piece.s1), 0.0)
    mid = piece.height((piece.s0 + piece.s1) / 2)
    sag = (wire.a[1] + wire.b[1]) / 2 - mid
    true_sag = c * (math.cosh(12 / c) - 1)  # level span of 24 m, nearly
    assert abs(sag - true_sag) < 0.03
    assert abs(piece.profile[2] - 1 / (2 * c)) < 0.002


def test_a_hogging_fit_is_held_straight():
    s = np.linspace(0, 10, 50)
    profile = W.fit_profile(s, 10 - 0.01 * (s - 5) ** 2)
    assert profile[2] == 0.0


def test_span_wire_between_facades_carries_a_hung_lamp():
    lamp = (25.0, 13.0, 20.6)
    # The last 1.5 m before each wall and 0.8 m round the lamp give no returns, as in the survey.
    left = catenary((11.5, 14.0, 20.0), (24.2, 13.1, 20.57), 80)
    right = catenary((25.8, 13.1, 20.63), (38.5, 14.1, 21.0), 80)
    body = np.array(lamp) + RNG.uniform((-0.25, -0.75, -0.25), (0.25, -0.35, 0.25), (40, 3))
    xyz, classes = scene((ground(0, 0, 50, 40), 2), (wall(10, 0, 40, 18), 6), (wall(40, 0, 40, 18), 6),
                         (left, 1), (right, 1), (body, 1))
    found, lamps = W.detect(xyz, classes, (0, 0, 50, 40), search=0)
    # One span with the lamp's weight in it, or its two halves meeting at the lamp.
    assert len(found) in (1, 2)
    ends = sorted(e for w in found for e in w.ends)
    assert ends in (["facade", "facade"], ["facade", "facade", "knot", "knot"])
    assert all(w.kind == "span" for w in found)
    assert {round(w.a[0]) for w in found} | {round(w.b[0]) for w in found} >= {10, 40}
    assert len(lamps) == 1
    point, index = lamps[0]
    assert index in range(len(found))
    assert np.linalg.norm(point - np.array(lamp)) < 0.8
    field = W.record(found, lamps)
    assert field["version"] == 1 and len(field["wires"]) == len(found) and len(field["lamps"]) == 1
    assert all(len(w) == 8 for w in field["wires"])


def test_a_crown_of_overlap_returns_holds_no_wire():
    centre = np.array([20.0, 10.0, 20.0])
    crown = centre + RNG.normal(0, 2.0, (6000, 3))
    branches = [centre + np.outer(np.linspace(0, 3.5, 30), RNG.normal(0, 1, 3) / 1.7) for _ in range(12)]
    # Filed as strip overlap (12), so only the candidates' own crowding tells the crown from a wire.
    xyz, classes = scene((ground(0, 0, 40, 40), 2), (crown, 12), (np.concatenate(branches), 12))
    found, lamps = W.detect(xyz, classes, (0, 0, 40, 40), search=0)
    assert found == [] and lamps == []


def test_a_wire_ending_in_open_air_is_left_out():
    xyz, classes = scene((ground(0, 0, 50, 40), 2), (catenary((5, 12, 10), (30, 12, 12), 80), 1))
    found, _ = W.detect(xyz, classes, (0, 0, 50, 40), search=0)
    assert found == []


def test_a_tile_lists_only_the_wires_whose_middle_it_owns():
    wire = catenary((11.5, 14.0, 20.0), (38.5, 14.1, 21.0), 80)
    xyz, classes = scene((ground(-10, 0, 60, 40), 2), (wall(10, 0, 40, 18), 6), (wall(40, 0, 40, 18), 6), (wire, 1))
    west, _ = W.detect(xyz, classes, (0, 0, 25, 40), search=30)
    east, _ = W.detect(xyz, classes, (25, 0, 50, 40), search=30)
    assert len(west) + len(east) == 1


def test_migration_adds_the_wires_beside_the_light_list(monkeypatch, tmp_path):
    field = {"version": 1, "kinds": list(W.KINDS), "wires": [[0, 10, 0, 20, 10, 0, 0.3, 0]], "lamps": []}
    monkeypatch.setattr(W, "tile_wires", lambda tile: field)
    lights = {"format": "turku3d-lights", "version": 1, "count": 1, "posts": 0, "street": 1, "data": "AAAA"}
    product = Product(tmp_path, {"tile": "t0_0_0", "lights": dict(lights)})
    migration.transform(product)
    assert product.manifest["lights"] == {**lights, "wires": field}
    monkeypatch.setattr(W, "tile_wires", lambda tile: None)
    bare = Product(tmp_path, {"tile": "t0_0_0"})
    migration.transform(bare)
    assert "lights" not in bare.manifest


def test_shared_decode_reads_the_same_returns_without_reopening(tmp_path, monkeypatch):
    import laspy
    from turku3d import config as C

    header = laspy.LasHeader(point_format=3, version="1.2")
    header.scales, header.offsets = np.array([0.01, 0.01, 0.01]), np.array([C.ORIGIN_E, C.ORIGIN_N - 500, 0.0])
    las = laspy.LasData(header)
    n = 5000
    las.x = C.ORIGIN_E + RNG.uniform(0, 500, n)
    las.y = C.ORIGIN_N - RNG.uniform(0, 500, n)
    las.z = RNG.uniform(0, 30, n)
    las.classification = RNG.integers(1, 7, n).astype(np.uint8)
    path = tmp_path / "block.laz"
    las.write(path)
    bbox = C.BBoxLocal(100.0, 100.0, 300.0, 350.0)
    monkeypatch.setattr(W, "CHUNK", 777)
    monkeypatch.setenv('TURKU3D_SHARED_CACHE', str(tmp_path / 'cache'))
    direct = laspy.read(path)
    x, y, z = C.en_to_local(np.asarray(direct.x), np.asarray(direct.y), np.asarray(direct.z))
    keep = (x >= bbox.xmin) & (x < bbox.xmax) & (z >= bbox.zmin) & (z < bbox.zmax)
    streamed = np.c_[x[keep], y[keep], z[keep]], np.asarray(direct.classification)[keep]
    first = W.survey({"b": path}, bbox)
    opened = []
    real = laspy.open
    monkeypatch.setattr(laspy, "open", lambda *a, **k: opened.append(a) or real(*a, **k))
    again = W.survey({"b": path}, bbox)
    assert opened == []
    for got in (first, again):
        assert np.array_equal(got[0], streamed[0]) and np.array_equal(got[1], streamed[1])
    assert len(streamed[0]) > 0


def test_voxel_crowding_matches_every_return_count_and_boundary():
    from turku3d.wire_points import uncrowded, voxel_keys

    rng = np.random.default_rng(32)
    candidates = rng.integers(-2, 12, (3, 20000), dtype=np.int64)
    other = rng.integers(-3, 13, (3, 700), dtype=np.int64)
    occupied, counts = np.unique(voxel_keys(*other), return_counts=True)
    for weights in (counts, None):
        total = np.zeros(candidates.shape[1], np.int64)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    keys = voxel_keys(*(candidates + np.array([[dx], [dy], [dz]])))
                    index = np.minimum(np.searchsorted(occupied, keys), len(occupied) - 1)
                    hit = occupied[index] == keys
                    total += hit if weights is None else np.where(hit, weights[index], 0)
        for limit in (0, 1, 6, 27):
            np.testing.assert_array_equal(uncrowded(*candidates, occupied, weights, limit), total <= limit)
    assert uncrowded(*candidates, np.empty(0, np.int64), None, 1).all()


def test_segment_crop_keeps_every_possible_query_return_in_source_order():
    rng = np.random.default_rng(12)
    points = rng.uniform(-80, 80, (50000, 3))
    points = np.concatenate((points, [[-12, 100, -2], [12, -100, 2], [12, -100, 2]]))
    points = points[np.lexsort((points[:, 1], points[:, 2], points[:, 0]))]
    segments = [(np.array([-10, 0, 0]), np.array([10, 10, 0])),
                (np.array([30, 1, 20]), np.array([40, 3, 30]))]
    expected = np.zeros(len(points), bool)
    for a, b in segments:
        lo, hi = np.minimum(a, b) - 2, np.maximum(a, b) + 2
        expected |= ((points[:, 0] >= lo[0]) & (points[:, 0] <= hi[0]) &
                     (points[:, 2] >= lo[2]) & (points[:, 2] <= hi[2]))
    assert np.array_equal(W.near_segments(points, segments, 2), points[expected])
    assert W.near_segments(points, [], 2).shape == (0, 3)


def test_wire_records_equal_full_search_with_distant_clutter(monkeypatch):
    rng = np.random.default_rng(26)
    line = catenary((11.5, 14, 20), (38.5, 14, 20), 80)
    clutter = rng.uniform([100, 3, 100], [180, 20, 180], (12000, 3))
    xyz, classes = scene((ground(0, 0, 200, 200, step=2), 2),
                         (wall(10, 0, 40, 18), 6), (wall(40, 0, 40, 18), 6),
                         (line, 1), (clutter, 5))
    bounded = W.record(*W.detect(xyz, classes, (0, 0, 200, 200), search=0))
    monkeypatch.setattr(W, "near_segments", lambda points, segments, margin: points)
    full = W.record(*W.detect(xyz, classes, (0, 0, 200, 200), search=0))
    assert bounded is not None and bounded == full
