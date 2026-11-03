"""Measured colonnade rhythm and round trim-painted columns between end piers."""

import numpy as np
import pytest
from test_facade_components import fixture
from turku3d.facade_component_geometry import geometry, linear_rgba, plan


def colonnade(**kwargs):
    v, w = fixture("arcade")
    reading = dict(
        bounds=[0.1, 0, 0.8, 0.5],
        count=4,
        column_shape="round",
        column_width_m=0.5,
        column_pitch_m=2.0,
        first_column_m=2.5,
    )
    w["geometry_components"][0].update({**reading, **kwargs})
    w["trim"] = dict(colour=dict(value="#b6ab8f", support=0.65, source="patch medians"))
    return v, w


def test_measured_rhythm_keeps_columns_inside_end_piers_with_trim_colour():
    v, w = colonnade()
    plans = plan(v, w)
    assert plans[0]["column_centres"] == pytest.approx([2.5, 4.5, 6.5, 8.5])
    meshes, boxes = geometry(v, plans, "test", np.zeros(3))
    columns = [m for m in meshes if m.extras["material_key"] == "component_column"]
    assert len(columns) == 4 and len(boxes) == 4
    assert not any(m.extras.get("component_box") for m in columns)
    assert columns[0].color == pytest.approx(linear_rgba("#b6ab8f"))
    for mesh, x in zip(columns, [2.5, 4.5, 6.5, 8.5], strict=True):
        tris = mesh.positions[mesh.indices]
        normals = np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0])
        centre = np.array([x, 0, tris[:, :, 2].mean()])
        side = abs(normals[:, 1]) < 0.5 * np.linalg.norm(normals, axis=1)
        outward = tris.mean(axis=1) - centre
        outward[:, 1] = 0
        assert np.all(np.sum(normals[side] * outward[side], axis=1) > 0)
        top = tris[:, :, 1].max()
        caps = tris[~side][:, 0, 1]
        # The abacus top faces the soffit; its overhanging underside faces down.
        assert np.all(np.sign(normals[~side][:, 1]) == np.where(np.isclose(caps, top), 1, -1))
        # The abacus front is flush with the entablature; the shaft stands behind it.
        assert tris[:, :, 2].min() == pytest.approx(0)
        assert tris[:, :, 1].max() == pytest.approx(plans[0]["metric"][3])


def test_invalid_measured_rhythm_falls_back_to_spread_columns_and_square_default():
    v, w = colonnade(first_column_m=9.0)  # Last centre would leave the surveyed wall.
    assert plan(v, w)[0]["column_centres"][0] == pytest.approx(1.2 + 0.04 + 0.18)
    v, w = fixture("arcade")
    meshes, _ = geometry(v, plan(v, w), "test", np.zeros(3))
    assert all(m.extras["material_key"] != "component_column" for m in meshes)
    assert any(m.extras.get("component_box") for m in meshes)


def test_reading_names_round_order_but_unnamed_or_square_columns_stay_boxes():
    from turku3d.facade_components import components

    def shape(kind):
        form = dict(facade_observations=dict(sections=[dict(position="ground", kind=kind)]))
        return components(form, 3)[0][0].get("column_shape")

    assert shape("recessed glazed frontage behind round columns") == "round"
    assert shape("deep recessed glazing behind square columns") == "square"
    assert shape("recessed entrance/glazing behind columns") is None


def modernist_wall():
    v = np.array([[0, 0, 0], [20, 0, 0], [20, 30, 0], [0, 30, 0]], float)
    from test_facade_components import fixture as base_fixture

    _, w = base_fixture("bay")
    w.update(width_m=20, height_m=30, geometry_components=[])
    w.update(
        windows={}, base={}, bays={"count": 0, "support": 0}, observation={"needs_review": False},
        modernist=dict(
            support=0.8,
            margins_m=[0.5, 0.5],
            ground=dict(height_m=4.5, pane_m=2.4),
            glazed_floors_m=[[4.8, 8.4]],
            bands=dict(first_m=8.4, pitch_m=3.2, count=6, sill_m=1.0, head_m=2.75, pane_m=1.3,
                       slab=dict(depth_m=1.0, thickness_m=0.22, parapet_m=0.95, colour="#b9bbbc")),
            top=dict(bottom_m=27.6, recess_m=1.5, roof_slab_m=0.6),
            awnings=[dict(span_m=[1, 19], attach_m=4.4, depth_m=1.8, drop_m=0.6, colour="#5c5a55")],
            panels=[dict(rect_m=[9, 8.4, 1.6, 19], outward_m=0.05, glass=True, colour="#6e8da6")],
        ),
    )
    return v, w


def test_modernist_profile_expands_ribbons_slabs_top_recess_shopfront_and_panels():
    from turku3d.facade_modernist import expand

    v, w = modernist_wall()
    out = expand(w)
    assert "modernist" not in out and "modernist" in w  # source reading unchanged
    ribbons = out["windows"]["rectangles_m"]
    assert len(ribbons) == 6 + 1 + 1 and all(r[2] == pytest.approx(19) for r in ribbons)
    assert ribbons[0][1] == pytest.approx(9.4) and ribbons[0][3] == pytest.approx(1.75)
    assert out["windows"]["divisions"]["value"] == [15, 1]
    assert len(out["base"]["openings_m"]) == 8 and out["base"]["variant"]["value"] == "shops"
    kinds = [c["kind"] for c in out["geometry_components"]]
    assert kinds == ["slab_band", "recess", "slab_band", "awning", "panel"]
    plans = plan(v, out)
    assert [p["kind"] for p in plans] == kinds
    meshes, boxes = geometry(v, plans, "test", np.zeros(3))
    assert not boxes  # Upper slabs, awnings and panels are visual only.
    slabs = [m for m in meshes if m.extras.get("component_box") and m.color == pytest.approx(linear_rgba("#b9bbbc"))]
    assert len(slabs) == 7 * 2  # 7 levels, slab plus parapet each
    # A fragment split at 12 m keeps every slab exactly once.
    lower = np.array([[0, 0, 0], [20, 0, 0], [20, 12, 0], [0, 12, 0]], float)
    upper = np.array([[0, 12, 0], [20, 12, 0], [20, 30, 0], [0, 30, 0]], float)
    count = 0
    for frag in (lower, upper):
        ms, _ = geometry(frag, plan(frag, out), "test", np.zeros(3))
        count += sum(1 for m in ms if m.extras.get("component_box") and m.color == pytest.approx(linear_rgba("#b9bbbc")))
    assert count == 14


def test_invalid_or_weak_modernist_profile_leaves_wall_unchanged():
    from turku3d.facade_modernist import expand

    v, w = modernist_wall()
    w["modernist"]["support"] = 0.5
    assert expand(w) is w
    v, w = modernist_wall()
    w["modernist"]["bands"]["count"] = 30  # Bands would leave the surveyed wall.
    assert expand(w) is w


def test_full_wall_reading_outranks_partial_characterization_of_same_frontage():
    from turku3d import config as C
    from turku3d.facade_programs import matching_wall

    def wall(wid, scope, offset):
        return dict(
            wall_id=wid,
            observation=dict(needs_review=False, scope=scope),
            geometry=dict(p0=[C.ORIGIN_E, C.ORIGIN_N - offset], p1=[C.ORIGIN_E + 20, C.ORIGIN_N - offset],
                          normal=[0, 1], base_h=0),
        )

    v = np.array([[0, 0, 0], [20, 0, 0], [20, 10, 0], [0, 10, 0]], float)
    partial = wall("partial", "partial-sections", 0.0)  # exactly on the shell line
    full = wall("full", "full-wall", 0.3)
    program = dict(needs_review=False, walls=[partial, full])
    assert matching_wall(v, program)["wall_id"] == "full"
    assert matching_wall(v, dict(needs_review=False, walls=[partial]))["wall_id"] == "partial"


def test_modernist_panels_read_rect_as_x_y_w_h_and_keep_a_sloped_bottom():
    from turku3d.facade_modernist import expand

    v, w = modernist_wall()
    w["modernist"] = dict(support=0.8, margins_m=[0, 0], panels=[
        dict(rect_m=[9, 8.4, 1.6, 19], outward_m=0.05, glass=True),  # tall narrow stair glazing
        dict(rect_m=[2, 20, 16, 6], outward_m=0.6, colour="#975a3c", slope_m=[20, 25]),
    ])
    out = expand(w)
    glazing, fascia = out["geometry_components"]
    assert glazing["bounds"] == pytest.approx([9 / 20, 8.4 / 30, 1.6 / 20, 19 / 30])
    assert fascia["slope_m"] == [20, 25] and "slope_m" not in glazing
    plans = plan(v, out)
    bottoms = plans[1]["slope_bottoms"]
    assert bottoms[0] < bottoms[1] and abs(bottoms[1] - 25) < 0.5
    meshes, _ = geometry(v, plans, "test", np.zeros(3))
    fascia_mesh = meshes[-1]
    low_end = fascia_mesh.positions[fascia_mesh.positions[:, 0] < 3]
    assert low_end[:, 1].min() == pytest.approx(bottoms[0], abs=1e-6)


def test_parking_garage_walls_get_slanted_parapet_bands_and_open_levels_not_a_grid():
    import shapely
    from turku3d.coarse.inputs import Building
    from turku3d.facade_garage import garage_wall, is_garage

    b = Building("g", shapely.box(0, 0, 30, 20), 20.5, 32.3, "facade_plaster")
    b.attributes = {"osm_use": "parking", "parking": "multi-storey"}
    assert is_garage(b)
    v = np.array([[0, 20.5, 0], [30, 20.5, 0], [30, 32.3, 0], [0, 32.3, 0]], float)
    w = garage_wall(dict(width_m=30, height_m=11.8, windows={}, base={}, bays={"count": 9, "support": 0},
                         wall={}, observation={}), v, b)
    assert w["bays"]["count"] == 0 and len(w["windows"]["rectangles_m"]) == 4  # one open gap per level
    meshes, boxes = geometry(v, plan(v, w), "g", np.zeros(3))
    slanted = [m for m in meshes if m.extras["material_key"] == "component_slab" and np.ptp(m.positions[:, 2]) > 0.3]
    assert slanted and not boxes  # leaning parapet faces; visual only
    b.attributes = {"osm_use": "parking", "parking": "underground"}
    assert not is_garage(b)
