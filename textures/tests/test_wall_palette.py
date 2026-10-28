"""Distribution identity and building-level statistical separation."""

import importlib.util
from pathlib import Path

import numpy as np
import pytest
from turkutex.wall_palette import group_keys, lab, rgb, sample


def model():
    return {
        "spread_scale": 0.25,
        "groups": {
            "[]": {
                "mean_lab": [65, 3, 8],
                "covariance_lab": [[20, 1, 0], [1, 8, 0], [0, 0, 9]],
                "lower_lab": [45, -5, -5],
                "upper_lab": [85, 15, 20],
            }
        },
    }


def test_colour_space_roundtrip_and_stable_building_draw():
    colours = np.array([[0, 0, 0], [255, 255, 255], [180, 80, 20], [35, 90, 170]])
    assert np.allclose(rgb(lab(colours)), colours, atol=0.001)
    first = sample("stable-register-id", {}, model())
    assert first == sample("stable-register-id", {"levels": 8, "footprint_m2": 400}, model())
    assert len({sample(str(i), {}, model()) for i in range(20)}) > 15


def test_sparse_district_falls_back_and_supported_district_wins():
    attrs = {"material_class": "brick", "era": "1960+", "district": "853001"}
    own = model()
    global_colour = sample("id", attrs, own)
    group = {**own["groups"]["[]"], "mean_lab": [50, 10, 10]}
    own["groups"][group_keys(attrs)[0]] = group
    assert sample("id", attrs, own) != global_colour
    assert sample("id", {**attrs, "district": "853002"}, own) == global_colour


def test_holdout_colours_never_enter_fit():
    path = Path(__file__).resolve().parents[2] / "tools/refs/fit_wall_palette.py"
    spec = importlib.util.spec_from_file_location("fit_wall_palette", path)
    fit = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fit)
    rows = [
        dict(attributes={}, reference_rgb=[150 + i, 140, 130], held_out=False) for i in range(12)
    ]
    rows += [dict(attributes={}, reference_rgb=[255, 0, 0], held_out=True)]
    first = fit.fit(rows)
    rows[-1]["reference_rgb"] = [0, 255, 0]
    assert fit.fit(rows) == first
    assert first["groups"]["[]"]["buildings"] == 12
    assert fit.held_out("register") == fit.held_out("register")


def test_paired_score_requires_unchanged_shared_reference_support():
    path = Path(__file__).resolve().parents[2] / "tools/refs/rescore_wall_palette.py"
    spec = importlib.util.spec_from_file_location("rescore_wall_palette", path)
    scorer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(scorer)
    before = {
        "wall": {str(i): dict(own=[200, 200, 200], reference=[120, 120, 120]) for i in range(12)}
    }
    after = {
        "wall": {str(i): dict(own=[130, 130, 130], reference=[120, 120, 120]) for i in range(1, 13)}
    }
    rows = scorer.paired(before, after)
    assert rows[0]["shared_reference_patches"] == 11
    assert rows[0]["after"] < rows[0]["before"]
    after["wall"]["1"]["reference"] = [121, 120, 120]
    with pytest.raises(ValueError, match="Reference changed"):
        scorer.paired(before, after)
