"""Pure synthetic checks; no model loading, network or street imagery."""

import json

import numpy as np
import pytest
from turkutex.facade_cv import measure
from turkutex.facade_fast_render import validate
from turkutex.facade_models import Engine, metric_grid, parse_answer


def test_metric_opening_grid_rejects_mullion_frequency():
    mask = np.zeros((240, 360), bool)
    for y in (30, 90, 150, 210):
        for x in (30, 90, 150, 210, 270, 330):
            mask[y - 15 : y + 15, x - 10 : x + 10] = True
        mask[y - 2 : y + 2] = False
    assert metric_grid(mask, 18, 12) == dict(storeys=4, window_columns=6)
    assert metric_grid(np.zeros_like(mask), 18, 12) == dict(storeys=0, window_columns=0)


def test_json_rejects_invalid_values_instead_of_repairing():
    answer, _ = measure(np.full((80, 120, 3), 180, np.uint8), "synthetic_0", 9, 6)
    assert parse_answer("```json\n" + json.dumps(answer) + "\n```", "synthetic_0") == answer
    answer["storeys"] = 99
    with pytest.raises(ValueError, match="maximum"):
        parse_answer(json.dumps(answer), "synthetic_0")
    with pytest.raises(ValueError, match="JSON"):
        parse_answer("No answer", "synthetic_0")


def test_hybrid_does_not_claim_calibrated_acceptance():
    rgb = np.full((80, 120, 3), 180, np.uint8)
    answer, meta = measure(rgb, "synthetic_0", 9, 6)

    class Fake:
        def measure(self, *args):
            return dict(answer, storeys=1, window_columns=5), meta

    engine = Engine.__new__(Engine)
    engine.variant = "cv-models"
    engine.parser = engine.detector = Fake()
    result, sidecar = engine.measure(rgb, "synthetic_0", 9, 6)
    assert validate(result, "synthetic_0") == []
    assert result["window_columns"] == 0
    assert sidecar["needs_review"] is True
    assert set(sidecar["components"]) == {"cmp", "grounding-dino"}
