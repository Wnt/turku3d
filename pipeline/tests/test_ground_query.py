"""Mesh height queries on synthetic triangulations."""

import numpy as np
from turku3d.ground.mesh import HeightQuery, Mesh


def test_height_query_matches_exhaustive_triangles_and_ignores_triangle_order():
    import triangle

    rng = np.random.default_rng(7)
    xz = np.r_[rng.uniform(0, 20, (300, 2)), [[0, 0], [20, 0], [0, 20], [20, 20]]]
    out = triangle.triangulate({"vertices": xz}, "pc")
    p = np.column_stack((out["vertices"][:, 0], rng.uniform(0, 3, len(out["vertices"]))))
    p = np.column_stack((p[:, 0], p[:, 1], out["vertices"][:, 1]))
    f = out["triangles"].astype(np.uint32)
    mesh = Mesh(p, f, np.zeros(len(f), np.uint8))
    queries = np.r_[rng.uniform(0, 20, (400, 2)), xz[:5], [[-1, 5], [25, 25]]]
    expected = np.full(len(queries), np.nan)
    for k, (x, z) in enumerate(queries):
        for tri in p[f]:
            a, b, c = tri[:, [0, 2]]
            m = np.column_stack((b - a, c - a))
            u, v = np.linalg.solve(m, [x, z] - a)
            if u >= -1e-12 and v >= -1e-12 and u + v <= 1 + 1e-12:
                expected[k] = tri[0, 1] + u * (tri[1, 1] - tri[0, 1]) + v * (tri[2, 1] - tri[0, 1])
                break
    heights = HeightQuery(mesh)(queries)
    np.testing.assert_allclose(heights, expected, atol=1e-12)
    assert np.isnan(heights[-2:]).all()
    # The same triangles, rotated and reordered as another envelope's mesh has them.
    shuffled = np.roll(f[rng.permutation(len(f))], 1, axis=1)
    again = HeightQuery(Mesh(p, shuffled, np.zeros(len(f), np.uint8)))(queries)
    assert again.tobytes() == heights.tobytes()
