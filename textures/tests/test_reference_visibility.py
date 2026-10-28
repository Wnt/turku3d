import numpy as np
from turkutex.building_jobs import view_height
from turkutex.streetview.evidence import problem
from turkutex.streetview.facades import Facade
from turkutex.streetview.refs import supported_wall, view_problem
from turkutex.streetview.visibility import Scene, blocked_rays


def rectangle(x0, x1, y, z0, z1):
    a, b, c, d = [x0, y, z0], [x1, y, z0], [x1, y, z1], [x0, y, z1]
    return np.array([[a, b, c], [a, c, d]], dtype=float)


def test_rays_ignore_target_and_objects_beyond_but_detect_intervening_wing():
    camera = np.array([0, -10, 2.0])
    targets = np.array([[0, 0, 2], [0, 0, 8.0]])
    target = rectangle(-5, 5, 0, 0, 10)
    beyond = rectangle(-5, 5, 2, 0, 10)
    assert not blocked_rays(camera, targets, np.concatenate([target, beyond])).any()
    wing = rectangle(-5, 5, -5, 0, 4)
    assert blocked_rays(camera, targets, wing).tolist() == [True, False]


def test_whole_extent_is_checked_when_centre_is_visible():
    scene = Scene(rectangle(2, 6, -1, 0, 12), "fixture")
    corners = np.array([[-5, 0, 0], [5, 0, 0], [5, 0, 10], [-5, 0, 10]], dtype=float)
    visibility = scene.measure(np.array([0, -10, 2]), corners)
    assert 0.5 < visibility["visible_fraction"] < 0.9
    assert visibility["edge_visible_fraction"] < 0.85


def test_semantic_occlusion_overrides_sharp_pixels():
    evidence = dict(
        state="measured", facade_share=0.75, occlusion_share=0.25, multiple_surfaces=False
    )
    view = dict(screen=dict(black_fraction=0, sharpness=1), semantic=evidence)
    assert "foreground" in view_problem(view)
    assert problem(dict(evidence, occlusion_share=0.1)) is None
    assert "support" in problem(dict(evidence, occlusion_share=0, facade_share=0.4))
    assert "multiple" in problem(dict(evidence, occlusion_share=0, multiple_surfaces=True))
    assert problem(dict(state="unavailable")) is not None
    assert "interior" in problem(dict(evidence, interior_share=0.2))


def test_target_support_rejects_rectangle_across_courtyard_gap():
    triangles = np.concatenate([rectangle(-5, -2, 0, 0, 10), rectangle(2, 5, 0, 0, 10)])
    scene = Scene(triangles, "fixture")
    corners = np.array([[-5, 0, 0], [5, 0, 0], [5, 0, 10], [-5, 0, 10]], dtype=float)
    visibility = scene.measure(np.array([0, -10, 2]), corners)
    assert visibility["visible_fraction"] == 1
    assert visibility["target_support"] < 0.7


def test_foreground_boxes_are_clipped_and_overlaps_count_once():
    from turkutex.streetview.evidence import box_coverage, clipped_opening

    assert box_coverage((10, 10, 3), [[-2, 0, 5, 5], [0, 0, 5, 5]]) == 0.25
    assert box_coverage((10, 10), []) == 0
    assert clipped_opening((100, 200), [dict(label="window", score=0.6, box=[194, 30, 200, 60])])
    assert not clipped_opening(
        (100, 200), [dict(label="window", score=0.3, box=[194, 30, 200, 60])]
    )
    assert not clipped_opening((100, 200), [dict(label="window", score=0.6, box=[15, 30, 45, 60])])


def test_concave_source_and_hole_are_not_filled_by_visibility_triangles():
    from turkutex.streetview.visibility import triangulate_rings

    outside = np.array([[0, 0, 0], [4, 0, 0], [4, 0, 4], [0, 0, 4]], dtype=float)
    hole = np.array([[1, 0, 1], [3, 0, 1], [3, 0, 3], [1, 0, 3]], dtype=float)
    points, indices = triangulate_rings([outside, hole])
    assert blocked_rays(np.array([2, -2, 2]), np.array([[2, 2, 2]]), points[indices]).tolist() == [
        False
    ]
    assert blocked_rays(
        np.array([0.5, -2, 0.5]), np.array([[0.5, 2, 0.5]]), points[indices]
    ).tolist() == [True]


def test_supported_wall_trims_a_roof_height_without_changing_its_segment():
    facade = Facade("w", np.array([0.0, 0.0]), np.array([10.0, 0.0]), 10.0, 2.0)

    def measure(_camera, corners):
        height = corners[3, 2] - corners[0, 2]
        return dict(
            state="measured",
            target_support=0.6 if height > 7 else 1.0,
            visible_fraction=1.0,
            edge_visible_fraction=1.0,
        )

    target, support = supported_wall(facade, 2.0, measure)
    assert target.length == facade.length
    assert target.height_m == 7.0
    assert support["target_support"] == 1.0


def test_description_height_matches_rendered_wall_plane():
    view = {"facade_corners_EN_H": [[0, 0, 5], [1, 0, 5], [1, 0, 13], [0, 0, 13]]}
    assert view_height(view, 14) == 8
    assert view_height({}, 14) == 14
