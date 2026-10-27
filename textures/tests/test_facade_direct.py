import json
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image
from turkutex.facade_direct import best_view, clean_reference


def test_masked_bay_uses_same_wall_and_library_edges():
    rgb = np.full((64, 128, 3), 100, dtype=np.uint8)
    rgb[10:50, 12:24] = (20, 30, 50)
    rgb[10:50, 44:56] = (20, 30, 50)
    rgb[10:50, 76:88] = (20, 30, 50)
    rgb[10:50, 108:120] = (20, 30, 50)
    mask = np.full((64, 128), 255, dtype=np.uint8)
    mask[8:56, 40:64] = 0
    rgb[8:56, 40:64] = (240, 0, 0)
    plinth = Image.new("RGB", (8, 8), (90, 80, 70))
    roof = Image.new("RGB", (8, 8), (70, 80, 90))
    result, coverage = clean_reference(
        Image.fromarray(rgb), Image.fromarray(mask), plinth, roof, (128, 64), 8
    )
    out = np.asarray(result)
    assert coverage > 0.8
    assert out[30, 50, 0] < 100  # the obscured window returns, without red occluder
    np.testing.assert_array_equal(out[0, 0], (70, 80, 90))
    np.testing.assert_array_equal(out[-1, 0], (90, 80, 70))


def test_reference_with_too_little_visible_wall_is_rejected():
    photo = Image.new("RGB", (32, 32), "red")
    mask = Image.new("L", (32, 32), 0)
    with pytest.raises(ValueError, match="35%"):
        clean_reference(photo, mask, photo, photo, (32, 32), 8)


def test_preferred_alternate_view_must_exist(tmp_path, monkeypatch):
    directory = tmp_path / "wall"
    directory.mkdir()
    (directory / "refs.json").write_text("{}")
    facade = SimpleNamespace(id="wall")
    monkeypatch.setattr("turkutex.building_jobs.reference_matches", lambda *_: True)
    views = [
        {"view_id": "primary", "part": "full", "score": 1.0},
        {"view_id": "alternate", "part": "full", "score": 0.5},
    ]
    monkeypatch.setattr("turkutex.facade_direct._views", lambda *_: views)
    assert best_view(facade, tmp_path)["view_id"] == "primary"
    (directory / "preferred.json").write_text(json.dumps({"view_id": "alternate"}))
    assert best_view(facade, tmp_path)["view_id"] == "alternate"
    (directory / "preferred.json").write_text(json.dumps({"view_id": "missing"}))
    with pytest.raises(ValueError, match="unavailable"):
        best_view(facade, tmp_path)
