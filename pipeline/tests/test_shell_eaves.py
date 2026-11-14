import numpy as np
import pytest

from turku3d.coarse import stored_facades as sf
from turku3d.facade_programs import floor_grid_height


def quad(a, b, base, top_a, top_b=None):
    top_b = top_a if top_b is None else top_b
    return np.array([[a[0], base, a[1]], [b[0], base, b[1]], [b[0], top_b, b[1]], [a[0], top_a, a[1]]], float)


def ring(corners, base, top):
    return np.concatenate([quad(a, b, base, top) for a, b in zip(corners, corners[1:] + corners[:1])])


def test_a_trimmed_sliver_at_the_base_no_longer_sets_the_rings_eave():
    # A 20 x 10 m flat-roofed slab, 14 m tall. Ownership trimming leaves a zero-width trapezoid with one top
    # corner at the base on the second wall (Varkkavuorenkatu 36): read on the pieces, the ring's eave was
    # the base and every wall drew 0.1 m tall, blank above.
    corners = [(0, 0), (20, 0), (20, 10), (0, 10)]
    shell = ring(corners, 5, 19)
    sliver = np.array([[20, 5, 4], [20, 5, 4], [20, 19, 4], [20, 5, 4]], float)
    pieces = np.concatenate([shell[:4], quad((20, 0), (20, 4), 5, 19), sliver, quad((20, 4), (20, 10), 5, 19),
                             shell[8:]])
    assert set(sf.ring_eaves(pieces).values()) == {5}
    eaves = sf.exposed_eaves(shell, pieces)
    assert sorted(eaves) == list(range(0, len(pieces), 4))
    assert set(eaves.values()) == {19}


def test_exposed_pieces_keep_each_rings_own_eave_and_a_gable_its_eaves_line():
    gable = [quad((0, 0), (10, 0), 0, 6), quad((10, 0), (10, 4), 0, 6, 9), quad((10, 4), (10, 8), 0, 9, 6),
             quad((10, 8), (0, 8), 0, 6), quad((0, 8), (0, 4), 0, 6, 9), quad((0, 4), (0, 0), 0, 9, 6)]
    annex = ring([(10, 0), (14, 0), (14, 8), (10, 8)], 0, 3)
    shell = np.concatenate([*gable, annex])
    # The gable end over the annex lost its lower 3 m; the annex's party wall is gone.
    pieces = np.concatenate([gable[0], quad((10, 0), (10, 4), 3, 6, 9), quad((10, 4), (10, 8), 3, 9, 6),
                             *gable[3:], annex[:4], annex[8:]])
    eaves = sf.exposed_eaves(shell, pieces)
    assert [eaves[i] for i in range(0, len(pieces), 4)] == [6] * 6 + [3] * 3


def test_a_tower_over_wide_low_wings_spans_the_register_storeys():
    # Koulukatu 19: 10 register storeys in a 28.7 m tower; wider 14 m wings and a podium held the median.
    rows = [(28.7, 40), (30.2, 99), (14.1, 300), (3.4, 15)]
    assert floor_grid_height(rows) == pytest.approx(14.1)
    assert floor_grid_height(rows, 10) == pytest.approx(28.7)
    assert floor_grid_height(rows, 4) == pytest.approx(14.1)  # 3.5 m storeys: the median stands
    # A narrow sliver at a plausible height does not carry the grid.
    assert floor_grid_height([(14.1, 300), (25.0, 1.5), (30.2, 20)], 10) == pytest.approx(30.2)
    # Nothing tall enough: the median stands.
    assert floor_grid_height([(14.1, 300), (16.0, 20)], 10) == pytest.approx(14.1)
