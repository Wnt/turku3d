"""The city pass: centre-first, load-capped, resumable, with per-tile and per-step timings."""

import concurrent.futures
import importlib.util
import json
import sys

from test_migrations import TINT, farm, tile_migrate  # noqa: F401  (fixtures and the farm layout)
from turku3d.migrations import registry

spec = importlib.util.spec_from_file_location("tile_city_pass", tile_migrate.__file__.replace("tile_migrate", "tile_city_pass"))
city = importlib.util.module_from_spec(spec)
spec.loader.exec_module(city)


def test_pass_logs_step_timings_and_resumes(tmp_path, monkeypatch):
    monkeypatch.setattr(registry, "CHAIN", (TINT,))
    root, meta, _ = farm(tmp_path)
    monkeypatch.setattr(city, "ProcessPoolExecutor", concurrent.futures.ThreadPoolExecutor)
    monkeypatch.setattr(city.os, "nice", lambda increment: 0)
    monkeypatch.setattr(city, "surveyed", lambda tile: False)
    argv = ["tile_city_pass.py", "--farm-root", str(root), "--revision", meta["revision"], "--interval", "0"]
    monkeypatch.setattr(sys, "argv", argv)
    assert city.main() == 0
    log = root / "migrations" / meta["generation"]
    rows = [json.loads(line) for line in (log / "pass.jsonl").read_text().splitlines()]
    assert [(r["tile"], r["state"]) for r in rows] == [("t0_0_0", "migrated")]
    assert set(rows[0]["steps"]) == {"test-tint@1"} and len(rows[0]["steps"]["test-tint@1"]) == 2
    progress = json.loads((log / "pass.json").read_text())
    assert progress["finished"] and progress["done"] == 1 and progress["left"] == 0
    assert progress["steps"]["unsurveyed"]["tiles"] == 1
    # A second pass finds the tile done and migrates nothing.
    assert city.main() == 0
    assert len((log / "pass.jsonl").read_text().splitlines()) == 1


def test_estimate_weights_surveyed_and_unsurveyed_tiles():
    rows = [dict(tile="a", state="migrated", survey=True, cpu=100.0, wall=120.0, steps={"wires@2": [90, 80]}),
            dict(tile="b", state="migrated", survey=False, cpu=2.0, wall=3.0, steps={"wires@2": [1, 1]})]
    out = city.estimate(rows, ["a", "b", "c", "d"], survey_share=0.5)
    assert out["left"] == 2 and out["steps"]["surveyed"]["wires@2"] == 80
    assert out["cpu_hours_left"] == round(2 * (0.5 * 100 + 0.5 * 2) / 3600, 1)
