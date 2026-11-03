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
    np.testing.assert_array_equal(
        np.unique(result.material[abs(normals[:, 1]) > 1e-8]), np.unique(original.material)
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
