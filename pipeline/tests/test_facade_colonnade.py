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
