"""Ground fitting reserves tiles before inspecting colour and leaves unsupported classes neutral."""

import numpy as np

from fit_ground_colour import compose, errors, fit, split


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


def test_residual_composes_with_deployed_correction():
    base = fit(rows(), .5)
    residual = fit(rows(), .25, per_mosaic=True)
    total = compose(base, residual)
    expected = np.asarray(base['groups']['asphalt']['delta_lab']) + residual['groups']['asphalt']['delta_lab']
    assert np.allclose(total['groups']['asphalt']['delta_lab'], expected)
    assert 'ortho.vrt' in total['mosaics']


def test_prior_pilot_holdout_stays_blind_to_deployed_model():
    values = rows()
    old_train, old_held, pinned = split(values[:24])
    old_train_keys = {r['quarter'] for r in old_train}
    eligible = set(pinned) | {r['quarter'] for r in values[24:]}
    _, final_held, keys = split(values, pinned=pinned, holdout_pool=eligible)
    assert set(pinned) <= set(keys)
    assert old_train_keys.isdisjoint(keys)
    assert len(keys) >= 6
    assert {r['tile'] for r in final_held}.isdisjoint({r['tile'] for r in values if r['quarter'] not in keys})
