"""Skew bridge side connections use the same final visible and solid terrain."""

import numpy as np
import shapely

from turku3d.coarse.bridge_join import (
    approach_patches,
    connected_approaches,
    constrain,
    grade,
    sampler,
)
from turku3d.coarse.bridges import extended
from turku3d.coarse.street_profiles import HeightQuery
from turku3d.coarse.gardens import horizontal, mesh
from turku3d.structures.geometry import Faces


def way(key, coords, kind, bridge=False):
    return dict(
        id=key, line=shapely.LineString(coords), tags={"highway": kind}, width=3.0, bridge=bridge
    )


def fixture(blocked=None):
    road = way("road", [(20, 30), (60, 30)], "secondary", True)
    side = way("side", [(23, 35), (57, 35)], "cycleway", True)
    land = way("land", [(23, 35), (15, 39)], "cycleway")
    road.update(width=12.0, member_ways=[road.copy(), side], approaches=[land])
    patches = approach_patches(
        road,
        extended(road["line"], 3),
        (3, 3),
        lambda q: np.full(len(q), 6.02),
        lambda x, z: 6.02,
        blocked if blocked is not None else shapely.Polygon(),
        4.0,
    )
    return road, side, land, patches


def test_road_bridge_keeps_its_cycleway_continuation_without_claiming_crossed_path():
    road, side, land, _ = fixture()
    vehicle = way("vehicle", [(60, 30), (80, 30)], "secondary")
    crossed = way("below", [(40, 20), (40, 40)], "cycleway")
    assert {w["id"] for w in connected_approaches([road, side], [land, vehicle, crossed])} == {
        "land",
        "vehicle",
    }
    assert connected_approaches([road], [land]) == []


def test_skew_side_join_is_raised_inside_main_axis_range_but_midspan_stays_open():
    _, _, _, patches = fixture()
    positions = np.array([[22, 2, 36], [40, 2, 30], [30, 2, 40]])
    result = grade(positions, patches)
    assert abs(result[0, 1] - 6.02) < 1e-8
    np.testing.assert_array_equal(result[1:], positions[1:])


def test_lower_corridor_or_water_mask_remains_at_original_height():
    _, _, _, patches = fixture(shapely.box(21, 35, 24, 37))
    points = np.array([[22, 2, 36], [21, 2, 36]])
    np.testing.assert_array_equal(grade(points, patches), points)


def test_final_stage_reuses_original_tin_instead_of_compounding_the_feather():
    _, _, _, patches = fixture()
    points = np.array([[16, 2, 33.5]])
    baseline = points[:, 1].copy()
    first = grade(points, patches, baseline)
    assert 2 < first[0, 1] < 6
    np.testing.assert_array_equal(grade(first, patches, baseline), first)
    faces = Faces()
    horizontal(faces, shapely.box(10, 20, 70, 50), 2)
    base = mesh(faces, "grass")
    repaired = constrain(base, patches, base)
    twice = constrain(repaired, patches, base)
    query = np.array([[16, 33.5], [22, 36], [23, 35]])
    np.testing.assert_allclose(HeightQuery(twice)(query), HeightQuery(repaired)(query), atol=1e-8)


def test_final_terrain_inserts_contact_edge_and_keeps_holes_and_materials():
    _, _, _, patches = fixture()
    faces = Faces()
    hole = shapely.box(29, 29, 31, 31)
    horizontal(faces, shapely.box(10, 20, 70, 50).difference(hole), 2)
    original = mesh(faces, "grass")
    result = constrain(original, patches)
    query = HeightQuery(result)
    np.testing.assert_allclose(query([[23, 35], [22, 35.5]]), 6.02, atol=1e-7)
    assert np.isnan(query([[30, 30]])[0])
    from turku3d.col.materials import mat_id

    normals = np.cross(
        result.positions[result.indices][:, 1] - result.positions[result.indices][:, 0],
        result.positions[result.indices][:, 2] - result.positions[result.indices][:, 0],
    )
    # Walkable and sloped land keeps its materials; near-vertical exposed faces are masonry.
    from turku3d.coarse.surfaces import STEEP

    np.testing.assert_array_equal(
        np.unique(result.material[abs(normals[:, 1]) >= STEEP * np.linalg.norm(normals, axis=1)]),
        np.unique(original.material),
    )
    assert mat_id("quay") in result.material  # exposed abutment cap closes vertically
    # Clipping and shared sampling must consume this very same triangle field.
    assert abs(query([[40, 30]])[0] - 2) < 1e-8
    sample = sampler(lambda x, z: 2, result, patches)
    assert abs(sample(23, 35) - 6.02) < 1e-8
    assert sample(40, 30) == 2


def test_unsupported_approach_uses_available_length_instead_of_a_short_steep_drop():
    road, _, _, _ = fixture()
    road["approaches"][0]["line"] = shapely.LineString([(23, 35), (-17, 55)])
    patches = approach_patches(
        road,
        extended(road["line"], 3),
        (3, 3),
        lambda q: np.full(len(q), 6.02),
        lambda x, z: 0,
        shapely.Polygon(),
        4,
    )
    assert abs(patches[-1]["line"].length - 30) < 1e-8


def test_neighbouring_terrain_tiles_agree_at_side_and_axis_approach_seams():
    from turku3d.ground.mesh import Mesh
    from turku3d.col.materials import mat_id

    _, _, _, patches = fixture()
    x, z = np.meshgrid(np.arange(10, 71, 2), np.arange(20, 51, 2))
    p = np.c_[x.ravel(), np.full(x.size, 2), z.ravel()]
    ix = []
    for row in range(15):
        for column in range(30):
            a = row * 31 + column
            ix.extend([[a, a + 31, a + 1], [a + 1, a + 31, a + 32]])
    base = Mesh(p, np.asarray(ix, np.uint32), np.full(len(ix), mat_id("grass"), np.uint8))
    # Cross the land tapers. The vertical span/abutment cap itself intentionally
    # has a lower floor on one side; the separate bridge deck covers that side.
    for edge in (22, 62):
        heights = []
        for keep in (
            (p[base.indices, 0] <= edge).all(axis=1),
            (p[base.indices, 0] >= edge).all(axis=1),
        ):
            half = Mesh(p, base.indices[keep], base.material[keep]).compact()
            result = constrain(half, patches, base)
            sample = sampler(lambda x, z: 2, result, patches)
            heights.append([sample(edge, z) for z in np.linspace(21, 49, 57)])
        np.testing.assert_allclose(*heights, atol=1e-8)



def lower_walk_fixture(height):
    """A side path taper with a measured walk and a flight foot 3 m beside it."""
    from turku3d.coarse.bridge_join import walk_levels

    road, _, land, _ = fixture()
    walk = dict(
        key="walk",
        line=shapely.LineString([(24.0, 37.9), (19.5, 40.15)]),
        width=3.0,
        knots=np.array([0.0, 5.03]),
        cross=np.array([-7.5, -4.5, -1.5, 0, 1.5, 4.5, 7.5]),
        heights=np.full((2, 7), height),
    )
    flight = ("flight", shapely.LineString([(21.0, 31.0), (16.0, 29.0)]),
              {"_profile": {"stations": [0.0, 5.39], "heights": [height, 6.0]}})
    patches = approach_patches(
        road,
        extended(road["line"], 3),
        (3, 3),
        lambda q: np.full(len(q), 6.02),
        lambda x, z: 6.02,
        shapely.Polygon(),
        4.0,
        walk_levels([walk], [flight]),
    )
    return walk, patches


def test_measured_lower_walk_beside_an_approach_keeps_its_level_and_a_vertical_abutment():
    from turku3d.col.materials import mat_id

    walk, patches = lower_walk_fixture(2.0)
    faces = Faces()
    horizontal(faces, shapely.box(10, 20, 70, 50), 2)
    result = constrain(mesh(faces, "grass"), patches, mesh(faces, "grass"))
    query = HeightQuery(result)
    # The walk and the landing in front of the flight's lower step keep their level.
    np.testing.assert_allclose(query([[22.0, 38.9], [20.5, 39.65], [21.9, 31.4]]), 2.0, atol=1e-7)
    # The approach's own surface is still raised to the deck end.
    assert abs(query([[22, 36]])[0] - 6.02) < 1e-7
    # No slanted wedge leans into the walk corridor: vertical quay faces close the drop.
    triangles = result.positions[result.indices]
    normals = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
    centres = triangles.mean(axis=1)[:, [0, 2]].T
    inside = shapely.contains_xy(walk["line"].buffer(1.75), *centres)
    slope = abs(normals[:, 1]) / np.linalg.norm(normals, axis=1)
    assert not (inside & (slope > 1e-6) & (np.ptp(triangles[:, :, 1], axis=1) > 1e-6)).any()
    # Along the protected walk the raised land ends at one clean open edge for the
    # surface closure to seal: no per-edge vertical quads (a sliver comb, issue 023).
    band = shapely.contains_xy(walk["line"].buffer(1.9), *centres)  # protected half width 1.8 m
    assert not (band & (slope < 1e-6) & (np.ptp(triangles[:, :, 1], axis=1) > 1e-6)).any()
    from turku3d.ground.mesh import HeightQuery as _H

    assert abs(_H(result)([[22.0, 36.6]])[0] - 6.02) < 0.3  # raised land right up to the edge


def test_walk_at_the_approach_level_is_still_graded_into_the_taper():
    _, patches = lower_walk_fixture(5.5)
    points = np.array([[22.0, 5.5, 38.9]])
    assert grade(points, patches)[0, 1] > 5.5
