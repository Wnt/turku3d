"""Retention must preserve published generations and never touch protected inputs."""
import json
import os

import pytest

from turku3d.coarse.retention import (
    GRACE_SECONDS, current, managed_build, prune, prune_tile, remember, tile_lock,
)
from turku3d.stage_cache import StageCache, prune_entries


def payload(out, digit):
    name = f"vis.{digit * 64}.glb"
    (out / name).write_bytes(b"visual")
    os.utime(out / name, (0, 0))
    return name


def publish(out, name):
    record = dict(visual={"t0_0_0": dict(url=name)})
    for file in ("manifest.json", "collision.json"):
        (out / file).write_text(json.dumps(record))


def test_current_and_all_recent_manifest_references_survive(tmp_path):
    a, b, c, orphan = [payload(tmp_path, d) for d in "abcd"]
    publish(tmp_path, a)
    remember(tmp_path, 1000)
    publish(tmp_path, b)
    remember(tmp_path, 1100)
    publish(tmp_path, c)
    assert prune_tile(tmp_path, 1200) == 6
    assert not (tmp_path / orphan).exists()
    assert all((tmp_path / n).exists() for n in (a, b, c))
    prune_tile(tmp_path, 1000 + GRACE_SECONDS + 1)
    assert not (tmp_path / a).exists()
    assert (tmp_path / b).exists()
    prune_tile(tmp_path, 1100 + GRACE_SECONDS + 1)
    assert list(tmp_path.glob("vis.*.glb")) == [tmp_path / c]


def test_recent_unknown_payload_and_both_public_pointers_protected(tmp_path):
    a, b, c = [payload(tmp_path, d) for d in "abc"]
    publish(tmp_path, a)
    (tmp_path / "collision.json").write_text(json.dumps({"url": b}))
    os.utime(tmp_path / c, (2000, 2000))
    assert current(tmp_path) == {a, b}
    prune_tile(tmp_path, 2001)
    assert all((tmp_path / n).exists() for n in (a, b, c))


def test_invalid_manifest_fails_closed(tmp_path):
    old = payload(tmp_path, "a")
    (tmp_path / "manifest.json").write_text("{")
    with pytest.raises(ValueError):
        prune_tile(tmp_path)
    assert (tmp_path / old).exists()


def test_sweep_skips_active_build_and_protected_stores(tmp_path):
    out = tmp_path / "t0_0_0"
    out.mkdir()
    old = payload(out, "a")
    protected = tmp_path / "data/cache/streetlevel/pano.jpg"
    protected.parent.mkdir(parents=True)
    protected.write_bytes(b"pano")
    with tile_lock(out):
        prune(tmp_path)
        assert (out / old).exists()
    prune(tmp_path)
    assert not (out / old).exists()
    assert protected.read_bytes() == b"pano"


def test_stage_retention_reuses_two_results_and_prunes_legacy(tmp_path):
    cache = StageCache(tmp_path, retain=True)
    for i in range(5):
        assert cache.run("mesh", "code", i, lambda: i) == i
    assert len(list((tmp_path / "mesh").glob("*.pkl"))) == 2
    assert cache.run("mesh", "code", 3, lambda: -1) == 3
    assert cache.metrics["mesh"]["cached"]
    (tmp_path / "mesh/.retained.json").unlink()
    prune_entries(tmp_path / "mesh")
    assert len(list((tmp_path / "mesh").glob("*.pkl"))) == 2


def test_managed_publish_keeps_old_and_removes_only_intermediates(tmp_path):
    out = tmp_path / "t0_0_0"
    out.mkdir()
    old = payload(out, "a")
    publish(out, old)
    @managed_build
    def build(tile, out):
        new = payload(out, "b")
        publish(out, new)
        (out / ".texture-cache").mkdir()
        (out / ".texture-cache/image.ktx2").write_bytes(b"encoded")
        return new
    new = build("t0_0_0", out)
    assert (out / old).exists() and (out / new).exists()
    assert (out / ".texture-cache/image.ktx2").read_bytes() == b"encoded"
    assert json.loads((out / ".retention.json").read_text())[0]["files"] == [old]


def test_last_full_survives_first_passes_until_next_full(tmp_path):
    from turku3d.coarse.retention import remember_full

    old, first, new = [payload(tmp_path, d) for d in "abc"]
    def full(name):
        publish(tmp_path, name)
        path = tmp_path / "manifest.json"
        record = json.loads(path.read_text())
        record["pass_kind"] = "full"
        path.write_text(json.dumps(record))
        remember_full(tmp_path)
    full(old)
    publish(tmp_path, first)
    remember_full(tmp_path)
    remember(tmp_path, 1000)
    prune_tile(tmp_path, 1000 + 2 * GRACE_SECONDS)
    assert (tmp_path / old).exists()
    assert json.loads((tmp_path / "last-full.json").read_text())["visual"]["t0_0_0"]["url"] == old
    new = payload(tmp_path, "c")
    full(new)
    prune_tile(tmp_path, 1000 + 3 * GRACE_SECONDS)
    assert not (tmp_path / old).exists()
    assert (tmp_path / new).exists()


def test_first_pass_comparison_survives_full_upgrade_and_pruning(tmp_path):
    first, full = [payload(tmp_path, d) for d in "ab"]
    (tmp_path / "first.json").write_text(json.dumps({"visual": {"t0_0_0": {"url": first}}}))
    publish(tmp_path, full)
    prune_tile(tmp_path, now=100000)
    assert (tmp_path / first).exists()
    assert current(tmp_path) == {first, full}
