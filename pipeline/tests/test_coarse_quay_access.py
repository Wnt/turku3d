"""Quay profile blending and lower-passage floor ownership."""

import numpy as np
import shapely

from turku3d.coarse import walkable


def patch(key, z, height, width=3):
    return dict(
        key=key,
        line=shapely.LineString([(0, z), (12, z)]),
        width=width,
        knots=np.array([0.0, 12.0]),
        cross=np.array([-7.5, -4.5, -1.5, 0, 1.5, 4.5, 7.5]),
        heights=np.full((2, 7), height),
    )


def test_outer_bank_blends_continuously_between_walks_and_flat_caps_end():
    scene = [patch("lower", 0, 1), patch("upper", 6, 4)]
    q = np.array([[6, 2, z] for z in (2.999, 3, 3.001)])
    h = walkable.grade(q, scene)[:, 1]
    assert np.ptp(h) < 0.01
    outside = walkable.grade(np.array([[-0.1, 8, 0]]), scene)
    assert outside[0, 1] == 8


def test_vault_floor_anchors_choose_lower_quay_not_parallel_upper_walk():
    from turku3d.coarse.underpasses import floor_anchors

    line = shapely.LineString([(0, 3), (12, 3)])
    levels, supported = floor_anchors(line, [patch("upper", 5, 6), patch("lower", 3, 1.2)], [7, 7])
    np.testing.assert_allclose(levels, 1.2)
    assert supported.all()
