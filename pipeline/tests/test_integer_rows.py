import itertools

import numpy as np

from turku3d import integer_rows


def _cases():
    rng = np.random.default_rng(7)
    return [
        rng.integers(-50, 50, (1000, 2)), rng.integers(0, 5, (500, 3)), rng.integers(-3, 3, (200, 1)),
        np.zeros((0, 2), np.int64), rng.integers(0, 2**40, (300, 2)), rng.integers(-2**62, 2**62, (100, 2)),
        rng.integers(0, 200, (1000, 2)).astype(np.int32), rng.integers(0, 200, (1000, 2)).astype(np.uint16),
        rng.integers(0, 3000, (20000, 2)), rng.integers(0, 3000, (50, 2)),
    ]


def test_unique_rows_are_numpys_rows_indices_inverse_and_counts():
    for rows in _cases():  # dense tables, sorted keys and the row-compare fallback
        for flags in itertools.product([False, True], repeat=3):
            kw = dict(zip(("return_index", "return_inverse", "return_counts"), flags, strict=True))
            want = np.unique(rows, axis=0, **kw)
            got = integer_rows.unique(rows, **kw)
            want, got = (want, got) if isinstance(want, tuple) else ((want,), (got,))
            assert len(want) == len(got)
            for i, (w, g) in enumerate(zip(want, got, strict=True)):
                w = w.reshape(-1) if i else w
                assert w.shape == g.shape and np.array_equal(w, g), (rows.shape, kw, i)


def test_highest_per_row_is_the_first_of_the_lexsorted_group_with_ties():
    rng = np.random.default_rng(3)
    for n, span in ((1000, 30), (5000, 3), (200000, 4000), (10, 1), (100, 10**12)):
        rows = rng.integers(0, span, (n, 2))
        height = np.round(rng.normal(0, 1, n), 1)
        height[: n // 10], height[n // 10: n // 5] = 0.0, -0.0  # ties, signed zeros among them
        order = np.lexsort((-height, *rows.T[::-1]))
        ordered = rows[order]
        keep = np.ones(n, bool)
        keep[1:] = np.any(ordered[1:] != ordered[:-1], axis=1)
        assert np.array_equal(integer_rows.highest(rows, height), order[keep]), (n, span)
