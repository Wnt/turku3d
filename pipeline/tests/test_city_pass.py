"""The city pass: centre-first, load-capped, resumable, with per-tile and per-step timings."""

import concurrent.futures
import importlib.util
import json
import sys

from test_migrations import ROADS, TINT, farm, tile_migrate  # noqa: F401  (fixtures and the farm layout)
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
    argv = ["tile_city_pass.py", "--farm-root", str(root), "--revision", meta["revision"], "--interval", "0", "--no-survey-wait", "--blocks", "0"]
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


def test_a_tile_waiting_for_its_spot_rebuild_asks_the_farm_and_is_retried(tmp_path, monkeypatch):
    monkeypatch.setattr(registry, "CHAIN", (TINT,))
    root, meta, _ = farm(tmp_path)
    monkeypatch.setattr(city, "ProcessPoolExecutor", concurrent.futures.ThreadPoolExecutor)
    monkeypatch.setattr(city.os, "nice", lambda increment: 0)
    monkeypatch.setattr(city, "surveyed", lambda tile: False)
    migrate, asked = city.M._worker, []
    calls = iter([dict(tile="t0_0_0", state="missing-rebuild", rebuild_revision="r1", error="needs it")] * 2)
    monkeypatch.setattr(city.M, "_worker", lambda args: next(calls, None) or migrate(args))
    monkeypatch.setattr(city.M, "request_rebuild", lambda url, row: asked.append((url, row["rebuild_revision"])))
    argv = ["tile_city_pass.py", "--farm-root", str(root), "--revision", meta["revision"], "--interval", "0",
            "--no-survey-wait", "--blocks", "0", "--rebuild-interval", "0", "--farm-url", "http://farm"]
    monkeypatch.setattr(sys, "argv", argv)
    assert city.main() == 0
    rows = [json.loads(line) for line in (root / "migrations" / meta["generation"] / "pass.jsonl").read_text().splitlines()]
    # The wait is logged once, the farm is asked on every try, and the tile migrates once its build is there.
    assert [r["state"] for r in rows] == ["missing-rebuild", "migrated"]
    assert asked == [("http://farm", "r1")] * 2
    assert json.loads((root / "migrations" / meta["generation"] / "pass.json").read_text())["finished"]


def test_estimate_weights_surveyed_and_unsurveyed_tiles():
    rows = [dict(tile="a", state="migrated", survey=True, cpu=100.0, wall=120.0, steps={"wires@2": [90, 80]}),
            dict(tile="b", state="migrated", survey=False, cpu=2.0, wall=3.0, steps={"wires@2": [1, 1]})]
    out = city.estimate(rows, ["a", "b", "c", "d"], survey_share=0.5)
    assert out["left"] == 2 and out["steps"]["surveyed"]["wires@2"] == 80
    assert out["cpu_hours_left"] == round(2 * (0.5 * 100 + 0.5 * 2) / 3600, 1)


def test_a_checkpoint_stores_the_prefix_that_a_changed_tail_resumes_from(tmp_path, monkeypatch):
    monkeypatch.setattr(registry, "CHAIN", (TINT, ROADS))
    root, meta, _ = farm(tmp_path)
    monkeypatch.setattr(city, "ProcessPoolExecutor", concurrent.futures.ThreadPoolExecutor)
    monkeypatch.setattr(city.os, "nice", lambda increment: 0)
    monkeypatch.setattr(city, "surveyed", lambda tile: False)
    argv = ["tile_city_pass.py", "--farm-root", str(root), "--revision", meta["revision"], "--interval", "0",
            "--no-survey-wait", "--blocks", "0", "--checkpoint", "test-tint@1"]
    monkeypatch.setattr(sys, "argv", argv)
    assert city.main() == 0
    prefix = meta["migrations"][0]["generation"]
    assert (root / "products" / prefix / "t0_0_0" / "ready.json").exists()
    assert (root / "products" / meta["generation"] / "t0_0_0" / "ready.json").exists()
    row = json.loads((root / "migrations" / meta["generation"] / "pass.jsonl").read_text().splitlines()[0])
    assert row["state"] == "migrated" and row["applied"] == ["test-roads@1"]
    assert set(row["steps"]) == {"test-tint@1", "test-roads@1"}


def test_a_tile_waits_for_catalogued_survey_blocks_and_takes_verified_ones(tmp_path, monkeypatch):
    import hashlib

    from turku3d import config as C
    from turku3d.coarse import wires

    monkeypatch.setenv("TURKU3D_DATA", str(tmp_path / "data"))
    names = [b.name for b in C.laz_tiles_for_bbox(C.VisualTile.parse("t0_0_0").bbox.buffered(wires.SEARCH))]
    supply = tmp_path / "downloads"
    supply.mkdir()
    blocks = {n: f"laz {n}".encode() for n in names}
    for n in names[1:]:
        (supply / f"{n}.laz").write_bytes(blocks[n] if n != names[-1] else b"truncated")
    catalog = {f"raw/laz/pistepilvi/{n}.laz": dict(bytes=len(b), sha256=hashlib.sha256(b).hexdigest())
               for n, b in blocks.items()}
    (tmp_path / "data" / "raw" / "laz" / "pistepilvi").mkdir(parents=True)
    (tmp_path / "data" / "source-version.json").write_text(json.dumps({"files": catalog}))
    survey = city.Survey([str(supply)])
    # The first block is nowhere, the last one's download does not match the catalog: both keep the tile waiting.
    assert survey.missing("t0_0_0") == [names[0], names[-1]] if len(names) > 1 else [names[0]]
    assert survey.linked == len(names) - 2
    (C.raw_dir("laz") / "pistepilvi" / f"{names[0]}.laz").write_bytes(blocks[names[0]])
    (supply / f"{names[-1]}.laz").unlink()
    (supply / f"{names[-1]}.laz").write_bytes(blocks[names[-1]])
    assert survey.missing("t0_0_0") == []


def test_a_supplied_block_on_another_filesystem_is_copied_only_when_verified(tmp_path):
    import hashlib

    source, target = tmp_path / "bulk" / "b.laz", tmp_path / "farm" / "b.laz"
    source.parent.mkdir()
    target.parent.mkdir()
    source.write_bytes(b"block bytes")
    assert city.supply_block(source, target, hashlib.sha256(b"other").hexdigest(), link=False) is None
    assert list(target.parent.iterdir()) == []  # no partial copy left behind
    assert city.supply_block(source, target, hashlib.sha256(b"block bytes").hexdigest(), link=False) == "copy"
    assert target.read_bytes() == b"block bytes" and target.stat().st_ino != source.stat().st_ino
    linked = tmp_path / "farm" / "c.laz"
    assert city.supply_block(source, linked, hashlib.sha256(b"block bytes").hexdigest()) == "link"
    assert linked.stat().st_ino == source.stat().st_ino


def test_a_lane_walks_its_ring_from_block_to_neighbouring_block():
    pending = sorted(["t0_0_0", "t0_1_1", "t0_-1_0", "t0_2_0", "t0_3_1", "t0_-2_-2", "t0_2_2"],
                     key=lambda t: (city.ring(t), t))
    assert city.pick(pending, None) == 0  # the innermost ring first
    outer = [t for t in pending if city.ring(t) >= 2]  # ['t0_-2_-2', 't0_2_0', 't0_2_2', 't0_3_1']
    # In ring 2 the tile of the lane's own block wins over the first in order; ring 3 waits.
    assert outer[city.pick(outer, city.home("t0_3_1"))] == "t0_2_0"
    assert outer[city.pick(outer, city.home("t0_2_3"))] == "t0_2_2"
    assert city.home("t0_-1_-1") == (-1, -1) and city.home("t0_1_0") == (0, 0)
