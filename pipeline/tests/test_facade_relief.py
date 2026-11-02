import numpy as np
import pytest

from turku3d.facade_relief import opening_parts


def patch():
    points = np.array([[0, 2, 0], [2, 2, 0], [2, 5, 0], [0, 5, 0]], float)
    uv = points[:, :2].copy()
    style = dict(module=0, size=[2, 3], origin=[0, 2], detail=[0, 0, 0, 1], trim="#ddd9cc")
    return points, uv, style


def test_recess_preserves_layout_and_returns_face_into_opening():
    points, uv, style = patch()
    parts = list(opening_parts(points, uv, style))
    assert len(parts) == 5
    assert np.allclose(parts[0][0][:, 2], .16)
    assert np.array_equal(parts[0][1], uv) and parts[0][2] == style
    # Actual GLB winding is 0,3,2 / 0,2,1. All returns must face the opening.
    center = np.array([1, 3.5, .08])
    for q, _, paint in parts[1:]:
        normal = np.cross(q[3] - q[0], q[2] - q[0])
        assert np.dot(normal, center - q.mean(axis=0)) > 0
        assert paint["module"] >= 42 and paint["detail"] == [0, 0, 0, 0]
    assert np.all(points[:, 2] == 0)


@pytest.mark.parametrize("kind", [0, 5, 9, 13])
def test_nonrectangular_openings_stay_flat(kind):
    points, uv, style = patch()
    style["detail"][3] = kind
    assert len(list(opening_parts(points, uv, style))) == 1


def test_clipped_openings_do_not_grow_a_return_across_the_seam():
    points, uv, style = patch()
    points[[1, 2], 0] = uv[[1, 2], 0] = 1
    assert len(list(opening_parts(points, uv, style))) == 1


def test_sectioned_pairs_keep_groups_and_flat_heads():
    from turku3d.facade_programs import measured, compile_wall
    from turkutex.facade_archetypes import sample
    from turku3d.facade_program_geometry import wall_parts
    from turku3d import config as C

    program = measured("103304677X")
    wall = next(w for w in program["walls"] if w["wall_id"] == "103304677X_4f98a8e9f2")
    sections = wall["render_sections"]
    assert sections[0]["kind"] == "shop" and sections[0]["count"] == 3
    assert sections[1]["opening_counts"] == [2, 2, 2]
    assert sections[1]["head"] == "flat"
    assert len(sections) == 2  # Clipped attic remains unresolved.
    g = wall["geometry"]
    a, b = [np.array([p[0] - C.ORIGIN_E, g["base_h"], C.ORIGIN_N - p[1]]) for p in (g["p0"], g["p1"])]
    h = wall["height_m"]
    vertices = np.array([a, b, b + [0, h, 0], a + [0, h, 0]])
    wall.update(measured_geometry=g, measured_size=[wall["width_m"], h])
    prior = sample("test", {"levels": 3}, [{"wall_id": wall["wall_id"], "width_m": wall["width_m"], "height_m": h}])["walls"][0]
    prior.update(wall)
    parts = list(wall_parts(vertices, prior, compile_wall(prior, 3)))
    windows = [p for p in parts if p[2]["detail"][3] == 1 and p[2]["origin"][1] > g["base_h"] + h/3]
    assert len(windows) == 6
    centers = sorted(p[0][:, [0, 2]].mean(axis=0).dot((b-a)[[0, 2]]) for p in windows)
    gaps = np.diff(centers)
    assert gaps[1] > gaps[0] and gaps[3] > gaps[2]


@pytest.mark.parametrize("section", [
    dict(kind="glazed balcony", architectural_bays=3, openings_per_bay=2, head="flat"),
    dict(kind="window", architectural_bays=3, openings_per_bay=12, head="flat"),
])
def test_grouped_readings_do_not_flatten_structures_or_expand_invalid_counts(section):
    from turkutex.facade_sections import sections
    form = dict(facade_observations=dict(sections=[dict(position="middle", material="render", **section)]))
    parts, _ = sections(form, 3)
    assert parts[0]["kind"] is None
    assert "opening_counts" not in parts[0]
