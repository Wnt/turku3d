"""Small synthetic fixtures: no network, real data, Codex calls or browser."""

import json
import os
import sys
import time

import pytest
from turku3d import config as C
from turku3d.region.areas import load_area
from turku3d.region.cache import atomic_target, collect, enforce_limit, import_cache
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


def test_shared_cache_content_hash_and_lru(tmp_path):
    root = tmp_path / "shared"
    older, newer = root / "google/a/pano.jpg", root / "google/b/pano.jpg"
    with atomic_target(older, root) as temp:
        temp.write_bytes(b"old")
    with atomic_target(newer, root) as temp:
        temp.write_bytes(b"newer")
    (older.parent / "meta.json").write_text('{"source":"google"}')
    assert (root / ".objects").is_dir()
    os.utime(older, (1, 1))
    os.utime(older.parent / "meta.json", (1, 1))
    enforce_limit(root, 5)
    assert not older.exists()
    assert not (older.parent / "meta.json").exists()
    assert newer.read_bytes() == b"newer"
    assert len(list((root / ".objects").iterdir())) == 1


def test_cache_calls_scan_for_the_limit_at_most_once_per_interval(tmp_path, monkeypatch):
    from turku3d.region import cache

    scans = []
    monkeypatch.setattr(cache, "_enforce_limit", lambda root, limit: scans.append(root))
    monkeypatch.setattr(cache, "_LAST_ENFORCED", {})

    class Panos:
        root = tmp_path / "shared"

        @cache.synchronized
        def read(self):
            return 1

    panos = Panos()
    assert [panos.read() for _ in range(3)] == [1, 1, 1]
    assert scans == [panos.root]
    monkeypatch.setitem(cache._LAST_ENFORCED, panos.root, -2 * cache.ENFORCE_INTERVAL_S)
    panos.read()
    assert len(scans) == 2


def test_import_preserves_metadata_and_source(tmp_path):
    old, shared = tmp_path / "old", tmp_path / "shared"
    (old / "google/a").mkdir(parents=True)
    (old / "google/a/pano.jpg").write_bytes(b"image")
    (old / "google/a/meta.json").write_text('{"copyright":"source"}')
    import_cache(old, shared)
    import_cache(old, shared)
    assert (shared / "google/a/meta.json").read_text() == '{"copyright":"source"}'
    assert (old / "google/a/pano.jpg").read_bytes() == b"image"
    assert (old / "google/a/pano.jpg").stat().st_ino == (shared / "google/a/pano.jpg").stat().st_ino


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


def test_landed_layer_adapter_and_assembler_area(tmp_path, monkeypatch):
    from turku3d.region.runner import plan

    monkeypatch.setenv("TURKU3D_WORK", str(tmp_path / "work"))
    monkeypatch.setattr(C, "REPO_ROOT", tmp_path)
    code = tmp_path / "pipeline/turku3d"
    for module in ("ground/build.py", "buildings/build.py", "package/assemble.py"):
        path = code / module
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    steps = {s.name: s for s in plan(C.OFFICE_BLOCK, "work-3x3", 3, tmp_path / "reports")}
    assert steps["ground"].commands
    assert steps["buildings"].commands[0][-2:] == ["--jobs", "3"]
    assert code / "layers.py" in steps["ground"].inputs
    assert "office-block" in steps["assemble"].commands[0]
    assert "work-3x3" not in steps["assemble"].commands[0]
    assert len(steps["validate"].commands) == 4


def test_eviction_skips_busy_keys_while_cache_readers_remain_active(tmp_path, monkeypatch):
    from turku3d.region import cache
    from turku3d.region.cache import cache_lock, entry_lock

    monkeypatch.setattr(cache, "cache_limit", lambda root=None: 0)

    busy = tmp_path / "google/busy/pano.jpg"
    idle = tmp_path / "google/idle/pano.jpg"
    for path in (busy, idle):
        with atomic_target(path, tmp_path) as temp:
            temp.write_bytes(b"pixels")
    with cache_lock(tmp_path, shared=True), entry_lock(tmp_path, busy):
        cache._try_enforce(tmp_path)
        assert busy.read_bytes() == b"pixels"
        assert not idle.exists()
    enforce_limit(tmp_path, 0)
    assert not busy.exists()
    assert not list((tmp_path / ".objects").iterdir())


def test_eviction_reclaims_legacy_decoded_arrays_before_compressed_sources(tmp_path):
    source = tmp_path / "google/a/pano.jpg"
    decoded = tmp_path / "decoded/a.npy"
    for path in (source, decoded):
        with atomic_target(path, tmp_path) as temp:
            temp.write_bytes(b"pixels")
    os.utime(source, (1, 1))
    enforce_limit(tmp_path, 6)
    assert source.exists() and not decoded.exists()


def test_nested_cache_calls_pin_paths_until_outer_reader_finishes(tmp_path):
    from turku3d.region.cache import pin_entry, synchronized

    path = tmp_path / "google/a/pano.jpg"
    with atomic_target(path, tmp_path) as temp:
        temp.write_bytes(b"pixels")

    class Reader:
        root = tmp_path

        @synchronized
        def inner(self):
            pin_entry(self.root, path)
            return path

        @synchronized
        def outer(self):
            held = self.inner()
            enforce_limit(self.root, 0)
            assert held.read_bytes() == b"pixels"

    Reader().outer()
    enforce_limit(tmp_path, 0)
    assert not path.exists()


def test_sandbox_streetlevel_cache_has_a_smaller_default_limit(tmp_path, monkeypatch):
    from turku3d.region.cache import cache_limit

    data, work = tmp_path / "data", tmp_path / "sandbox/work"
    monkeypatch.setenv("TURKU3D_DATA", str(data))
    monkeypatch.setenv("TURKU3D_WORK", str(work))
    monkeypatch.delenv("TURKU3D_STREETLEVEL_CACHE_GB", raising=False)
    assert cache_limit(data / "cache/streetlevel") == 60 * 1024**3
    assert cache_limit(work / "cache/streetview") == 5 * 1024**3
    monkeypatch.setenv("TURKU3D_STREETLEVEL_CACHE_GB", "2")
    assert cache_limit(work / "cache/streetview") == 2 * 1024**3
    assert cache_limit(data / "cache/streetlevel") == 2 * 1024**3
