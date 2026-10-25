"""Small synthetic fixtures: no network, real data, Codex calls or browser."""

import json
import os
import sys
import time

import pytest
from turku3d import config as C
from turku3d.region.areas import load_area
from turku3d.region.cache import atomic_target, collect, import_cache
from turku3d.region.estimate import estimate, guard_quota
from turku3d.region.runner import Step, execute


def test_ring_and_polygon(tmp_path):
    ring = load_area("next-ring")
    assert len(ring.visual_tiles) == 12
    assert len(ring.collision_tiles) == 192
    assert not set(ring.visual_tiles) & set(C.OFFICE_BLOCK.visual_tiles)
    (tmp_path / "triangle.yaml").write_text(
        "polygon_en: [[23459500,6704500], [23460000,6704500], [23459500,6704000]]\nmargin_m: 0\n"
    )
    assert len(load_area("triangle", tmp_path).visual_tiles) == 3
    with pytest.raises(ValueError):
        load_area("../escape", tmp_path)
    (tmp_path / "bad.yaml").write_text("tiles: [t1_0_0]\n")
    with pytest.raises(ValueError):
        load_area("bad", tmp_path)


def test_estimate_and_quota(tmp_path, monkeypatch):
    monkeypatch.setenv("TURKU3D_DATA", str(tmp_path))
    report = estimate(load_area("next-ring"))
    assert report["visual_tiles"] == 12
    assert report["buildings"] == 408
    assert len(report["laz_downloads"]) == 9
    assert report["jobs"]["facade_label"]["calls"] == 816
    for used, jobs in [
        (None, [{"weekly_pct": 1}]),
        (99, [{"weekly_pct": 2}]),
        (1, [{"weekly_pct": None}]),
        (float("nan"), []),
    ]:
        with pytest.raises(RuntimeError):
            guard_quota(jobs, used, 100)
    guard_quota([{"weekly_pct": 2}], 10, 100)


def test_resume_failure_invalidation_and_parallel_logs(tmp_path):
    source = tmp_path / "MANIFEST.json"
    source.write_text('{"hash":"a"}')
    out = tmp_path / "out.json"
    command = [
        sys.executable,
        "-c",
        f"from pathlib import Path; Path({str(out)!r}).write_text('ok')",
    ]
    step = Step("build", [command], [source], [out])
    report = tmp_path / "reports"
    assert execute([step], report)[0]["status"] == "done"
    assert execute([step], report)[0]["status"] == "cached"
    source.write_text('{"hash":"b"}')
    assert execute([step], report)[0]["status"] == "done"
    out.unlink()
    assert execute([step], report)[0]["status"] == "done"
    bad = Step("bad", [[sys.executable, "-c", "raise SystemExit(3)"]], [], [tmp_path / "absent"])
    rows = execute([step, bad, Step("never")], report)
    assert [r["status"] for r in rows] == ["cached", "failed"]
    assert not (report / "bad.json").exists()
    assert (report / "bad-0.log").exists()


def test_quota_blocks_before_subprocess(tmp_path):
    out = tmp_path / "should-not-exist"
    step = Step(
        "paid",
        [[sys.executable, "-c", f"open({str(out)!r},'w').close()"]],
        [],
        [out],
        jobs=[{"weekly_pct": 5}],
    )
    assert execute([step], tmp_path / "reports", usage=lambda: 99)[0]["status"] == "failed"
    assert not out.exists()


def test_atomic_cache_and_safe_gc(tmp_path):
    source, root = tmp_path / "old", tmp_path / "shared"
    source.mkdir()
    (source / "a.jpg").write_bytes(b"same-image")
    (source / "b.jpg").write_bytes(b"same-image")
    import_cache(source, root)
    assert (root / "a.jpg").stat().st_ino == (root / "b.jpg").stat().st_ino
    with pytest.raises(RuntimeError), atomic_target(root / "broken.jpg") as temp:
        temp.write_bytes(b"partial")
        raise RuntimeError("interrupted")
    assert not (root / "broken.jpg").exists()
    old = root / ".old.part.jpg"
    old.write_bytes(b"interrupted")
    os.utime(old, (time.time() - 90000,) * 2)
    assert collect(root)["stale_partials"] == 1
    assert old.exists()
    assert collect(root, apply=True)["reclaimable_bytes"] == 11
    assert not old.exists()
    assert (root / "a.jpg").read_bytes() == b"same-image"
    assert json.loads(json.dumps(collect(root)))["files"] == 3


def test_inventory_ignores_survey_lines_and_duplicate_polygons(tmp_path, monkeypatch):
    import geopandas as gpd
    from shapely.geometry import LineString, box

    monkeypatch.setenv("TURKU3D_DATA", str(tmp_path))
    dest = tmp_path / "raw/wfs/work-3x3/kanta_Rakennus.parquet"
    dest.parent.mkdir(parents=True)
    building = box(23459400, 6704600, 23459420, 6704620)
    gpd.GeoDataFrame(
        {"kplkoodi": ["66000", "66000", "60020"]},
        geometry=[building, building, LineString(building.exterior.coords)],
        crs=C.CRS_HORIZONTAL,
    ).to_parquet(dest)
    assert estimate(load_area("next-ring"))["buildings"] == 1


def test_parallel_tile_jobs_have_separate_logs(tmp_path):
    outputs = [tmp_path / f"tile-{i}.json" for i in range(2)]
    commands = [
        [sys.executable, "-c", f"from pathlib import Path; Path({str(p)!r}).write_text('done')"]
        for p in outputs
    ]
    rows = execute([Step("tiles", commands, [], outputs)], tmp_path / "reports", workers=2)
    assert rows[0]["status"] == "done"
    assert len(rows[0]["logs"]) == 2
    assert all(p.read_text() == "done" for p in outputs)
