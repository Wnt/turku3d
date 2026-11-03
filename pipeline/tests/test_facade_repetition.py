"""Measured bay phase and arcade continuity across real shell fragments."""

import copy
import numpy as np
import pytest
from test_facade_components import fixture
from turku3d.facade_repetition import expand
from turku3d.facade_component_geometry import plan, geometry, surface_parts


def test_repeated_windows_stay_inside_observed_frontage_and_preserve_source():
    wall = dict(
        width_m=27,
        height_m=8,
        windows={},
        observation={},
        repeated_bay=dict(support=0.8, pitch_m=12, windows_m=[[3, 4, 1, 1.5], [5, 4, 1, 1.5]]),
    )
    before = copy.deepcopy(wall)
    result = expand(wall)
    assert [r[0] for r in result["windows"]["rectangles_m"]] == [3, 5, 15, 17]
    assert wall == before
    assert result["observation"]["scope"] == "full-wall"
    for bad in [0, float("nan"), -0.5]:
        wall["repeated_bay"]["pitch_m"] = bad
        assert expand(wall) is wall
    wall["repeated_bay"].update(pitch_m=12, support=0.5)
    assert expand(wall) is wall


def test_arcade_fragments_keep_column_phase_and_no_internal_return_wall():
    v, w = fixture("arcade")
    w["geometry_components"][0].update(bounds=[0, 0, 1, 0.5], count=5)
    full = plan(v, w)[0]
    split = 5.7
    fragments = [
        np.array([[a, 0, 0], [b, 0, 0], [b, 12, 0], [a, 12, 0]], float)
        for a, b in [(0, split), (split, 12)]
    ]
    centres = []
    for i, fragment in enumerate(fragments):
        p = plan(fragment, w)
        assert len(p) == 1
        assert p[0]["returns"][:2] == ([True, False] if i == 0 else [False, True])
        meshes, boxes = geometry(fragment, p, "test", np.zeros(3))
        centres.extend(
            m.extras["component_box"]["center"][0] for m in meshes if m.extras.get("component_box")
        )
        cut = list(surface_parts(fragment, np.zeros((4, 2)), p, collision=True))
        assert any(abs(points[:, 2]).max() > 1 for points, _ in cut)
    assert sorted(centres) == pytest.approx(full["column_centres"])


def test_reviewed_through_arcade_cuts_front_and_back_without_copying_rear_paint():
    from turku3d.facade_repetition import through_arcade

    v, w = fixture("arcade")
    w.update(wall_id="survey", windows={"rectangles_m": [[1, 8, 2, 2]]}, observation={})
    w["geometry_components"][0].update(bounds=[0, 0, 1, 0.5], through=True, depth_m=4, support=0.9)
    # Fixture faces -z, so the opposite wall is at +4 m and faces +z.
    w["geometry"]["normal"] = [0, 1]
    back = through_arcade(w)[0]
    assert "windows" not in back and back["render_sections"] == []
    rear = np.array([[12, 0, 4], [0, 0, 4], [0, 12, 4], [12, 12, 4]], float)
    for points, wall in [(v, w), (rear, back)]:
        plans = plan(points, wall)
        meshes, _ = geometry(points, plans, "test", np.zeros(3))
        parts = list(surface_parts(points, np.zeros((4, 2)), plans, collision=True))
        assert plans and all(
            p[:, 1].min() >= 5.9 or p[:, 0].min() < 0.05 or p[:, 0].max() > 11.95 for p, _ in parts
        )
        # No moved opaque back panel survives inside the aperture.
        assert all(np.ptp(p[:, 2]) < 1e-6 for p, _ in parts)
    assert (
        through_arcade(
            {**w, "geometry_components": [{**w["geometry_components"][0], "support": 0.4}]}
        )
        == []
    )
