"""Ground fitting reserves tiles before inspecting colour and leaves unsupported classes neutral."""

import numpy as np

from fit_ground_colour import errors, fit, split


def rows():
    return [dict(quarter=f'q{i}', tile=f't{i // 2}', surface='asphalt', mosaic='ortho.vrt',
                 own_rgb=[90, 90, 90], reference_rgb=[110, 110, 110]) for i in range(30)]


def test_holdout_is_tile_disjoint_and_at_least_twenty_percent():
    values = rows()
    train, held, keys = split(values)
    assert len(keys) == 6
    assert {r['tile'] for r in train}.isdisjoint({r['tile'] for r in held})
    altered = [dict(r, reference_rgb=[255, 0, 0]) for r in held]
    assert split(train + altered)[2] == keys
    model = fit(train)
    assert max(r['after'] for r in errors(held, model)) < .0001
    assert max(abs(v) for v in model['groups']['asphalt']['delta_lab']) <= 15


def test_sparse_classes_and_mosaics_do_not_invent_corrections():
    values = rows()[:4]
    assert fit(values, per_mosaic=True)['groups'] == {}
    supported = fit(rows(), per_mosaic=True)
    assert set(supported['groups']) == {'asphalt'}
    assert 'grass' not in supported['groups']
    assert np.allclose(supported['mosaics']['ortho.vrt']['asphalt']['delta_lab'], 0, atol=1e-8)
