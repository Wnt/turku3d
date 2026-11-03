"""Analytic ray and lighting cases; requires pipeline[bake], no tiles or browser."""

import numpy as np
import pytest

from turku3d.baked_visibility import TriangleVisibility, bake_meshes, sample_visibility, surface_origins
from turku3d.package.gltf import GlbMesh

pytest.importorskip("embreex", reason="offline visibility tests require pipeline[bake]")

CONTEXT = [[-100, -100], [100, 100]]


def roof(y=.5):
    return np.array([[[-100, y, -100], [100, y, -100], [100, y, 100]],
                     [[-100, y, -100], [100, y, 100], [-100, y, 100]]])


def test_first_hits_are_two_sided_parallel_safe_and_range_limited():
    scene = TriangleVisibility(np.concatenate([roof(5), roof(1)]))
    origins = np.array([[0, 0, 0], [0, 2, 0], [0, 0, 0], [200, 0, 0], [0, 6, 0]])
    directions = np.array([[0, 1, 0], [0, -1, 0], [1, 0, 0], [0, 1, 0], [0, -1, 0]])
    np.testing.assert_equal(scene.distances(origins, directions, 10), [1, 1, np.inf, np.inf, 1])
    assert np.isinf(scene.distances(origins[:1], directions[:1], .5)).all()


def test_bvh_branches_and_packet_boundaries_preserve_nearest_hits():
    scene = TriangleVisibility(np.concatenate([roof(y) for y in range(1, 40)]))
    origins = np.zeros((2051, 3))
    origins[:, 1] = np.arange(len(origins)) % 30 + .25
    directions = np.tile([0, 1, 0], (len(origins), 1))
    np.testing.assert_allclose(scene.distances(origins, directions, 50), .75, atol=1e-5)


def test_open_sky_is_white_and_near_roof_occludes_without_pigment_change():
    target = GlbMesh("floor", np.array([[0., 0, 0], [1., 0, 0], [0., 0, 1]]),
                     np.array([[0, 2, 1]]), colors=np.full((3, 3), .3))
    ceiling = GlbMesh("roof", roof().reshape(-1, 3), np.arange(6).reshape(-1, 3))
    open_mesh, = bake_meshes([target], [target], context_bounds=CONTEXT)
    covered, = bake_meshes([target], [target, ceiling], context_bounds=CONTEXT)
    np.testing.assert_array_equal(open_mesh.baked_visibility, np.ones((3, 3)))
    assert np.all((covered.baked_visibility[:, 0] > .2) & (covered.baked_visibility[:, 0] < .6))
    np.testing.assert_array_equal(covered.baked_visibility[:, 1:], [[0, 1]] * 3)
    np.testing.assert_array_equal(target.colors, covered.colors)
    assert target.baked_visibility is None
    assert covered.positions is target.positions and covered.indices is target.indices


def test_distant_roof_only_occludes_sky_and_missing_context_stays_uncovered():
    scene = TriangleVisibility(roof(4))
    values = sample_visibility([[0, 0, 0], [90, 0, 0], [0, 0, 0]],
                               [[0, 1, 0], [0, 1, 0], [0, 0, 0]], scene, context_bounds=CONTEXT)
    # The finite 32m sky radius leaves the most horizontal upward rays open.
    y = 1 - 2 * (np.arange(32) + .5) / 64
    expected_sky = y[(4 - .002) / y >= 32].sum() / y.sum()
    np.testing.assert_array_equal(values[0], [1, expected_sky, 1])
    np.testing.assert_array_equal(values[1:], [[1, 1, 0], [1, 1, 0]])


def test_duplicates_and_input_order_do_not_change_bake_and_normal_seams_remain():
    p = np.array([[0, 0, 0]] * 3)
    n = np.array([[0, 1, 0], [0, -1, 0], [0, 1, 0]])
    scene = TriangleVisibility(roof())
    a = sample_visibility(p, n, scene, context_bounds=CONTEXT)
    order = np.array([2, 0, 1])
    b = sample_visibility(p[order], n[order], scene, context_bounds=CONTEXT)
    np.testing.assert_array_equal(a[order], b)
    np.testing.assert_array_equal(a[0], a[2])
    np.testing.assert_array_equal(a[1], [1, 1, 1])


def test_ray_directions_must_be_unit_length():
    with pytest.raises(ValueError, match="unit directions"):
        TriangleVisibility(roof()).distances([[0, 0, 0]], [[0, 2, 0]], 10)


def test_native_version_is_pinned_instead_of_silently_changing_bake(monkeypatch):
    monkeypatch.setattr("turku3d.baked_visibility.version", lambda _: "different")
    with pytest.raises(RuntimeError, match="install pipeline"):
        TriangleVisibility(roof())


def test_recentered_native_rays_preserve_metric_distances_at_geographic_offsets():
    offset = np.array([23459500., 0, -6704500.])
    scene = TriangleVisibility(roof(1.75) + offset)
    np.testing.assert_allclose(scene.distances([offset], [[0, 1, 0]], 2), [1.75], atol=1e-5)


def test_oblique_bvh_hits_match_independent_linear_system_reference():
    rng = np.random.default_rng(19)
    triangles = rng.uniform(-3, 3, (35, 3, 3))
    origins = rng.uniform(-4, 4, (25, 3))
    directions = rng.normal(size=(25, 3))
    directions /= np.linalg.norm(directions, axis=1)[:, None]
    expected = np.full(len(origins), np.inf)
    for i, (origin, direction) in enumerate(zip(origins, directions, strict=True)):
        for a, b, c in triangles:
            # Solve origin + t*direction = a + u*(b-a) + v*(c-a).
            t, u, v = np.linalg.solve(np.c_[direction, a-b, a-c], a-origin)
            if 0 <= t < 10 and u >= 0 and v >= 0 and u+v <= 1:
                expected[i] = min(expected[i], t)
    actual = TriangleVisibility(triangles).distances(origins, directions, 10)
    assert np.isfinite(expected).any() and np.isinf(expected).any()
    # Native rays use recentered float32 coordinates, matching shipped geometry precision.
    np.testing.assert_allclose(actual, expected, atol=1e-5)


@pytest.mark.parametrize("reverse", [False, True])
def test_floor_wall_seam_samples_from_free_space_even_with_reversed_occluder_winding(reverse):
    floor = GlbMesh("floor", np.array([[0., 0, -2], [4., 0, -2], [4., 0, 2], [0., 0, 2]]),
                    np.array([[0, 2, 1], [0, 3, 2]]), normals=np.tile([0., 1, 0], (4, 1)))
    wall = np.array([[[0., -10, -100], [0., 100, -100], [0., 100, 100]],
                     [[0., -10, -100], [0., 100, 100], [0., -10, 100]]])
    scene = TriangleVisibility(wall[:, ::-1] if reverse else wall)
    # Without face information the exact-boundary origin is ambiguous, never black.
    unresolved = sample_visibility(floor.positions[:1], floor.normals[:1], scene, context_bounds=CONTEXT)
    np.testing.assert_array_equal(unresolved, [[1, 1, 0]])
    origins = surface_origins(floor.positions, floor.indices, floor.normals, .002)
    baked = sample_visibility(floor.positions, floor.normals, scene,
                              context_bounds=CONTEXT, sample_origins=origins)
    reference = sample_visibility(floor.positions + [.001, 0, 0], floor.normals,
                                  scene, context_bounds=CONTEXT)
    np.testing.assert_allclose(baked[[0, 3]], reference[[0, 3]], atol=.003)
    assert np.all(baked[[0, 3], 0] > .45) and np.all(baked[[0, 3], 2] == 1)


@pytest.mark.parametrize("concave", [False, True])
def test_hard_corner_origins_preserve_shading_normal_and_incident_face_side(concave):
    # Duplicated corner belongs to two perpendicular faces with hard normals.
    sign = 1 if concave else -1
    p = np.array([[0., 0, 0], [3., 0, 0], [0., 0, 3],
                  [0., 0, 0], [0., 3*sign, 0], [0., 0, 3]])
    ix = np.array([[0, 2, 1], [3, 4, 5]])
    n = np.array([[0., 1, 0]] * 3 + [[1., 0, 0]] * 3)
    scene = TriangleVisibility(p[ix])
    origins = surface_origins(p, ix, n, .002)
    np.testing.assert_array_equal(origins, surface_origins(p, ix[::-1], n, .002))
    values = sample_visibility(p, n, scene, context_bounds=CONTEXT, sample_origins=origins)
    assert values[[0, 3], 2].min() == 1
    if concave:
        assert np.all((values[[0, 3], 0] > .4) & (values[[0, 3], 0] < .7))
    else:
        np.testing.assert_array_equal(values[0], [1, 1, 1])
    # Hemisphere normals remain hard; only sampling origins are inset.
    np.testing.assert_array_equal(n[[0, 3]], [[0, 1, 0], [1, 0, 0]])
