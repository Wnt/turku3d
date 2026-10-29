"""Ground measurement exports use supported matching cells, not mismatch candidates."""

import sys
from types import SimpleNamespace

import numpy as np

from gmaps_ground_score import export_ground, summary


def test_exports_matching_material_medians_and_rejects_height_conflicts(monkeypatch):
    monkeypatch.setitem(sys.modules, 'compare', SimpleNamespace(
        delta_e=lambda a, b: np.linalg.norm(a - b)))
    h = np.zeros((10, 10))
    truth = SimpleNamespace(h=h, rgb=np.full((10, 10, 3), 80.))
    ours = SimpleNamespace(h=h.copy(), rgb=np.full((10, 10, 3), 90.),
                           material_keys=['asphalt_worn', 'grass'],
                           material_index=np.zeros((10, 10), int))
    ours.material_index[:4] = 1
    ours.h[:4] = 3
    masks = dict(valid=np.ones((10, 10), bool), calibration=np.ones((10, 10), bool), ground=h)
    result = export_ground(truth, ours, masks, np.ones((10, 10), bool))
    assert list(result) == ['asphalt_worn']
    assert result['asphalt_worn']['cells'] == 60
    assert result['asphalt_worn']['own_rgb'] == [90., 90., 90.]
    assert result['asphalt_worn']['reference_rgb'] == [80., 80., 80.]


def test_abstention_is_not_a_zero_score():
    assert summary({'a': dict(status='abstained'), 'b': dict(status='incomplete')}) == dict(
        scored=0, abstained=1, metrics={})
