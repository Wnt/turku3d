"""The facade ground audit samples the top ground surface."""

import numpy as np
from turku3d.layers import CollisionLayer, write_collision
from turku3d.package.facade_ground_audit import ground_height


def test_ground_height_is_the_water_surface_above_a_synthetic_river_bed(tmp_path):
    square = np.array([[0.0, 0, 0], [62.5, 0, 0], [0, 0, 62.5], [62.5, 0, 62.5]])
    bed, water = square + [0, -2.83, 0], square + [0, 0.17, 0]
    triangles = np.array([[0, 2, 1], [1, 2, 3]], np.uint32)
    # Canonical tiles sort triangles by material: the bed (soil) comes before the water.
    layer = CollisionLayer(
        np.r_[bed, water],
        np.r_[triangles, triangles + 4],
        np.array([11, 11, 14, 14], np.uint8),
    )
    write_collision(tmp_path / "c_0_0.col.npz", layer)
    assert ground_height(tmp_path, 10.0, 20.0, {}) == 0.17
