"""Bridge component geometry must stay attached, outward-facing and collidable."""

import numpy as np
import shapely

from turku3d.coarse.bridge_kit import entrance_points, entrances, tapered_post


def test_tapered_shaft_has_outward_normals_and_nonzero_triangles():
    mesh = tapered_post("post", [10, 4, 20], [3, 4], [(0, 2, 2), (4, .8, .8)])
    tri = mesh.positions[mesh.indices]
    normals = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    assert (np.linalg.norm(normals, axis=1) > 1e-6).all()
    assert (np.sum(normals * (tri.mean(axis=1) - [10, 6, 20]), axis=1) > 0).all()
    assert mesh.metadata["collision"]


def crossing():
    return dict(id="skew", line=shapely.LineString([(0, 0), (50, 0)]), width=12,
                outline=shapely.Polygon([(-3, -6), (47, -6), (53, 6), (3, 6)]))


def test_entrances_follow_skew_outline_instead_of_nominal_width():
    way = crossing()
    points = entrance_points(way, 1.2)
    assert len(points) == 4
    assert all(way["outline"].boundary.distance(shapely.Point(p)) < 1e-8 for p, _ in points)
    assert all(np.isclose(np.linalg.norm(d), 1) for _, d in points)
    assert all(np.isclose(abs(p[1]), 6) for p, _ in points)


def test_estimated_entrance_respects_deck_obstacles_and_uses_deck_elevation():
    way = crossing()
    height = lambda p: 5 + p[:, 0] * .01
    spec = {"entrance_height": 4.8}
    all_meshes = entrances(way, height, [], spec)
    shafts = [m for m in all_meshes if m.metadata["surface"] == "granite"]
    assert len(shafts) == 4
    for shaft, (p, _) in zip(shafts, entrance_points(way, 1.2, 1.05 * 1.22 / 2 + .02), strict=True):
        assert np.isclose(shaft.positions[:, 1].min(), height(np.array([p]))[0])
        assert np.isclose(np.ptp(shaft.positions[:, 1]), 4.8)
        assert shaft.metadata["collision"]
    blocked = [shapely.box(-5, -10, 5, 10)]
    kept = entrances(way, height, blocked, spec)
    assert len([m for m in kept if m.metadata["surface"] == "granite"]) == 2
    assert entrances(way, height, [], {}) == []


def test_lower_quay_passages_do_not_remove_pylons_above_deck():
    from turku3d.coarse.bridge_details import furniture

    way = crossing()
    way["tags"] = {"highway": "secondary", "wikidata": "Q11853512"}
    meshes = furniture(way, shapely.MultiLineString([]), lambda p: np.full(len(p), 6.),
                       lambda p: np.zeros(len(p)),
                       [shapely.box(-5, -10, 5, 10), shapely.box(45, -10, 55, 10)],
                       way["outline"])
    shafts = [m for m in meshes if m.metadata["surface"] == "granite"]
    assert shafts
    assert min(m.positions[:, 1].min() for m in shafts) == 6


def test_reversing_axis_keeps_the_same_entrance_positions():
    way = crossing()
    points = entrance_points(way, 1.2, .66)
    way["line"] = shapely.reverse(way["line"])
    reverse = entrance_points(way, 1.2, .66)
    assert sorted(tuple(p.round(6)) for p, _ in points) == sorted(tuple(p.round(6)) for p, _ in reverse)


def test_kit_changes_invalidate_both_visual_and_collision_freshness():
    from turku3d.coarse import bridge_kit, collision_key, packaging
    from pathlib import Path

    source = Path(bridge_kit.__file__)
    assert source in packaging.producer_paths()
    assert source in collision_key.paths()
