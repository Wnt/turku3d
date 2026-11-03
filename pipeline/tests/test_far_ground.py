import numpy as np

from turku3d.coarse.far_ground import skirt, survey_colours


def test_skirt_hangs_two_sided_quads_under_every_outer_grid_edge():
    count = 4
    xs, zs = np.meshgrid(np.linspace(0, 250, count + 1), np.linspace(0, 250, count + 1))
    grid = np.c_[xs.ravel(), np.full(xs.size, 7.0), zs.ravel()]
    triangles = skirt(grid, count, 3.0)
    assert triangles.shape == (4 * count * 4, 3, 3)
    # Every skirt vertex lies on the cell border, at the surface or 3 m below it; no interior vertex moves.
    xz = triangles[..., [0, 2]].reshape(-1, 2)
    on_border = np.isclose(xz[:, 0], 0) | np.isclose(xz[:, 0], 250) | np.isclose(xz[:, 1], 0) | np.isclose(xz[:, 1], 250)
    assert on_border.all()
    assert set(np.round(triangles[..., 1].ravel(), 6)) == {7.0, 4.0}


def test_survey_classes_come_from_the_shared_far_palette():
    palette = survey_colours()
    assert set(palette) >= {'land', 'street', 'woods', 'water', 'built'}
    assert all(len(rgb) == 3 and all(0 <= c <= 1 for c in rgb) for rgb in palette.values())
