"""Distribution identity and building-level statistical separation."""

import hashlib
import importlib.util
import json
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


def test_runtime_palette_keeps_one_colour_across_facade_indices():
    from turkutex.facade_fast_render import register_description

    a = register_description("wall0", "register-id", {}, 12, 12)
    b = register_description("wall9", "register-id", {}, 18, 15)
    assert a["wall_colour"] == b["wall_colour"]
    explicit = register_description("wall0", "register-id", {"wall_colour": "#123456"}, 12, 12)
    assert explicit["wall_colour"] == "#123456"


def test_palette_table_edit_invalidates_producer_and_building_visual_dependencies(
    tmp_path, monkeypatch
):
    from turku3d import facade_kit
    from turku3d.coarse.packaging import current_code

    script, data = tmp_path / "palette.py", tmp_path / "palette.json"
    script.write_text("sampler")
    data.write_text('{"groups":1}')
    monkeypatch.setattr(facade_kit, "palette_sources", lambda: (script, data))
    before = current_code()
    data.write_text('{"groups":123}')
    assert current_code() != before
    from turku3d.stage_cache import source_key

    key = source_key(*facade_kit.palette_sources())
    data.write_text('{"groups":456}')
    assert source_key(*facade_kit.palette_sources()) != key


def test_checkpoint_rejects_changed_retained_source(tmp_path):
    path = Path(__file__).resolve().parents[2] / "tools/refs/rescore_wall_palette.py"
    spec = importlib.util.spec_from_file_location("rescore_checkpoint", path)
    scorer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(scorer)
    payload = b"immutable deployed bytes"
    digest = hashlib.sha256(payload).hexdigest()
    source = tmp_path / f"own-score-{digest[:16]}" / "source.glb"
    source.parent.mkdir()
    source.write_bytes(payload)
    report = tmp_path / "compare-score-after" / "report.json"
    report.parent.mkdir()
    report.write_text(
        json.dumps(
            dict(reference_sha256="reference", build_sha256=digest, facades=dict(colour_groups={}))
        )
    )
    before = tmp_path / "before.json"
    before.write_bytes(report.read_bytes())
    result = dict(report=str(report), own_sha256=digest)
    assert scorer.verified_pair(dict(report=str(before)), result) == []
    source.write_bytes(b"changed deployed bytes")
    with pytest.raises(ValueError, match="Retained own source changed"):
        scorer.verified_pair(dict(report=str(before)), result)


def test_checkpoint_rejects_a_different_palette(tmp_path):
    repo = Path(__file__).resolve().parents[2]
    path = repo / "tools/refs/rescore_wall_palette.py"
    spec = importlib.util.spec_from_file_location("rescore_model_checkpoint", path)
    scorer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(scorer)
    coverage, output, job = tmp_path / "before.json", tmp_path / "after", tmp_path / "job"
    model = job / "repo/textures/turkutex/wall_palette.json"
    model.parent.mkdir(parents=True)
    model.write_bytes((repo / "textures/turkutex/wall_palette.json").read_bytes() + b" ")
    import os

    (job / "request.json").write_text(
        json.dumps(
            dict(
                name=os.environ.get("TURKU3D_SANDBOX", "test"),
                commands=[f"score --coverage {coverage} --out {output} --expected-quarters 28"],
            )
        )
    )
    with pytest.raises(ValueError, match="Checkpoint colour source changed"):
        scorer.resume_checkpoint(job, coverage, output, {}, 28)
