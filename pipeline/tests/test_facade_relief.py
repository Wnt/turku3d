import numpy as np
import pytest

from turku3d.facade_relief import relief_parts


def patch():
    points = np.array([[0, 2, 0], [2, 2, 0], [2, 5, 0], [0, 5, 0]], float)
    uv = points[:, :2].copy()
    style = dict(module=0, size=[2, 3], origin=[0, 2], detail=[0, 0, 0, 1], trim="#ddd9cc")
    return points, uv, style


def test_recess_preserves_layout_and_returns_face_into_opening():
    points, uv, style = patch()
    parts = list(relief_parts(points, uv, style))
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
    assert len(list(relief_parts(points, uv, style))) == 1


def test_clipped_openings_do_not_grow_a_return_across_the_seam():
    points, uv, style = patch()
    points[[1, 2], 0] = uv[[1, 2], 0] = 1
    assert len(list(relief_parts(points, uv, style))) == 1


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


@pytest.mark.parametrize("angle", [0, np.pi / 2, np.pi])
def test_projected_trim_returns_face_out_of_solid_and_strip_internal_hint(angle):
    points, uv, style = patch()
    rotation = np.array([[np.cos(angle), 0, np.sin(angle)], [0, 1, 0], [-np.sin(angle), 0, np.cos(angle)]])
    points = points @ rotation.T
    style.update(module=42, detail=[0, 0, 0, 0], relief_m=0.08)
    parts = list(relief_parts(points, uv, style))
    assert len(parts) == 5
    assert np.linalg.norm(parts[0][0][0] - points[0]) == pytest.approx(0.08)
    center = (parts[0][0].mean(axis=0) + points.mean(axis=0)) / 2
    for q, _, paint in parts[1:]:
        normal = np.cross(q[3] - q[0], q[2] - q[0])
        assert np.dot(normal, center - q.mean(axis=0)) < 0
        assert "relief_m" not in paint
    assert style["relief_m"] == 0.08


def test_bands_preserve_flat_opening_ownership_and_bound_to_sloping_wall():
    from turkutex.facade_archetypes import sample
    from turku3d.facade_programs import compile_wall
    from turku3d.facade_program_geometry import wall_parts

    wall = sample("bands", {"levels": 2}, [{"wall_id": "test", "width_m": 12, "height_m": 8}])["walls"][0]
    style = compile_wall(wall, 2)
    style.update(origin=[0, 2], bands=[0.6, 0.4, 0, 0])
    vertices = np.array([[0, 2, 0], [12, 2, 0], [12, 10, 0], [0, 9.8, 0]], float)
    parts = list(wall_parts(vertices, wall, style))
    raised = [p for p in parts if p[2].get("relief_m")]
    assert len(raised) == 2
    assert max(p[0][:, 1].max() for p in raised) == 10
    assert min(p[0][:, 1].min() for p in raised) == 2
    assert all(p[2]["module"] >= 42 for p in raised)
    # One paint owner: partition areas still equal the original trapezoid.
    import shapely
    assert sum(shapely.Polygon(uv).area for _, uv, _ in parts) == pytest.approx(12 * 7.9)


@pytest.mark.parametrize("module", [1, 2, 4, 5])
def test_projected_plinth_cannot_block_shader_only_doors_or_shopfronts(module):
    from turkutex.facade_archetypes import sample
    from turku3d.facade_programs import compile_wall
    from turku3d.facade_program_geometry import wall_parts
    w = sample("shop", {"levels": 2}, [{"wall_id": "test", "width_m": 12, "height_m": 8}])["walls"][0]
    style = compile_wall(w, 2)
    style.update(module=module, origin=[0, 0], bands=[0.6, 0, 0, 0], detail=[0, 0, 0, 0])
    vertices = np.array([[0, 0, 0], [12, 0, 0], [12, 8, 0], [0, 8, 0]], float)
    parts = list(wall_parts(vertices, w, style))
    assert len(parts) == 1 and parts[0][2]["module"] == module


def test_structural_component_does_not_flatten_neighbouring_window():
    from turku3d.facade_relief import composed_parts
    from turku3d.facade_component_geometry import plan, geometry
    from test_facade_components import fixture
    vertices, wall = fixture("bay")
    wall["geometry_components"][0]["bounds"] = [0, 0, 0.3, 0.8]
    plans = plan(vertices, wall)
    geometry(vertices, plans, "test", np.zeros(3))
    points, uv, style = patch()
    points[:, 0] += 6
    uv[:, 0] += 6
    style["origin"][0] = 6
    parts = list(composed_parts(points, uv, style, plans))
    assert len(parts) == 5
    assert np.allclose(parts[0][0][:, 2], .16)


def _order_wall(width=15.36, height=14.0, bays=4, offset=0.0, bottom=0.0):
    from turku3d.facade_program_geometry import wall_parts
    from turku3d.facade_programs import classical, compile_wall
    from turkutex.facade_archetypes import ORDER, sample

    attrs = dict(levels=2, construction_year=1903, material_class="brick", use="2120",
                 monumental=True, footprint_m2=1200)
    program = sample("order-test", attrs, [dict(wall_id="w", width_m=width, height_m=height,
                                                 uv_offset=offset)])
    wall = program["walls"][0]
    wall["bays"].update(count=bays, pitch_m=width / bays)
    classical(program, attrs, "")
    assert wall["order"] == ORDER
    style = compile_wall(wall, 2)
    style["origin"][1] = 0.0
    a, b = np.array([0, bottom, 0.0]), np.array([width - offset, bottom, 0.0])
    vertices = np.array([a, b, [b[0], height, 0], [0, height, 0]])
    return style, list(wall_parts(vertices, wall, style))


def test_classical_order_puts_pilasters_on_the_kit_bay_grid_with_frieze_and_dentils():
    from turkutex.facade_archetypes import ORDER

    style, parts = _order_wall()
    assert style["glazing"][:2] == ORDER["panes"] and style["frame"] == ORDER["frame"]
    assert style["module"] % 3 == 0  # Ordered ground storey: windows over a plinth.
    bay = style["bay"]
    shaft = min(ORDER["pilaster"] * bay, ORDER["pilaster_max_m"])
    pilasters = [p for p, _, s in parts if s["wall"] == ORDER["trim"]
                 and np.ptp(p[:, 1]) > 5 and np.ptp(p[:, 0]) <= shaft + 1e-6]
    centres = sorted({round(float(p[:, 0].mean()), 3) for p in pilasters})
    # Half pilasters at both corners and one at every interior bay boundary.
    assert np.allclose(centres, [shaft / 4, bay, 2 * bay, 3 * bay, 4 * bay - shaft / 4], atol=2e-3)
    # Windows centred in each bay never sit under a pilaster.
    window = style["opening"][0] * bay
    assert bay - window > shaft
    plinth = [s for p, _, s in parts if s["wall"] == ORDER["plinth_colour"]]
    assert plinth and plinth[0]["bands"][0] >= ORDER["plinth_m"]
    dentils = [p for p, _, s in parts if s["wall"] == ORDER["trim"]
               and abs(np.ptp(p[:, 0]) - ORDER["dentil_m"][0]) < 1e-6
               and abs(np.ptp(p[:, 1]) - ORDER["dentil_m"][1]) < 1e-6]
    dentil, _, pitch = ORDER["dentil_m"]
    assert len(dentils) == len(np.arange(pitch / 2, style["size"][0] - dentil / 2, pitch))


def test_classical_order_follows_fragment_offset_and_skips_roof_setbacks():
    from turkutex.facade_archetypes import ORDER

    style, parts = _order_wall(offset=5.0)
    bay = style["bay"]
    shaft = min(ORDER["pilaster"] * bay, ORDER["pilaster_max_m"])
    centres = sorted({round(float(p[:, 0].mean()), 3) for p, _, s in parts
                      if s["wall"] == ORDER["trim"] and np.ptp(p[:, 1]) > 5
                      and np.ptp(p[:, 0]) <= shaft + 1e-6})
    assert np.allclose(centres, [2 * bay - 5, 3 * bay - 5, 4 * bay - 5 - shaft / 4], atol=2e-3)
    _, raised = _order_wall(bottom=6.0)
    assert not [s for _, _, s in raised if s["wall"] == ORDER["plinth_colour"]]
    assert not [p for p, _, s in raised if np.ptp(p[:, 1]) > 5 and s["wall"] == ORDER["trim"]]
