"""Small synthetic compiler-cache key and invalidation contracts."""

import os

import numpy as np
from turku3d.stage_cache import StageCache, content_key


def test_key_tracks_content_and_type_not_mapping_order_or_array_strides():
    a = np.arange(12, dtype=np.float64).reshape(3, 4)
    assert content_key({"a": a, "b": 1}) == content_key({"b": 1, "a": a.copy(order="F")})
    assert content_key(a) != content_key(a.astype(np.float32))
    assert content_key(a) != content_key(a.reshape(4, 3))
    assert content_key([1, 2]) != content_key([2, 1])
    assert content_key([1]) != content_key((1,))
    assert content_key(1) != content_key(1.0)


def test_stage_dag_stops_invalidation_when_upstream_output_is_identical(tmp_path):
    cache = StageCache(tmp_path)
    calls = []

    def run(code, data):
        upstream = cache.run("slice", code, data, lambda: calls.append("slice") or data[0])
        return cache.run("mesh", "v1", upstream, lambda: calls.append("mesh") or upstream * 2)

    assert run("v1", [3, 4]) == 6
    assert calls == ["slice", "mesh"]
    run("v1", [3, 4])
    assert all(m["cached"] for m in cache.metrics.values())
    run("v2", [3, 5])
    assert calls == ["slice", "mesh", "slice"]
    assert cache.metrics["mesh"]["cached"]
    run("v2", [4, 5])
    assert calls[-2:] == ["slice", "mesh"]
    run("v1", [3, 4])
    assert all(m["cached"] for m in cache.metrics.values())


def test_corruption_rebuilds_and_cached_values_are_not_shared_mutable_objects(tmp_path):
    cache = StageCache(tmp_path)
    value = cache.run("stage", "v1", [], lambda: [1])
    value.append(2)
    assert cache.run("stage", "v1", [], lambda: [9]) == [1]
    next((tmp_path / "stage").glob("*.pkl")).write_bytes(b"interrupted")
    assert cache.run("stage", "v1", [], lambda: [9]) == [9]
    assert not cache.metrics["stage"]["cached"]


def test_prepared_raw_key_uses_bytes_not_location_or_mtime(tmp_path, monkeypatch):
    from turku3d.coarse.inputs import _key

    monkeypatch.setenv("TURKU3D_WORK", str(tmp_path / "work"))
    a, b = tmp_path / "a", tmp_path / "b"
    a.write_bytes(b"input")
    b.write_bytes(b"input")
    key = _key([a], "ground")
    assert key == _key([b], "ground")
    os.utime(a, ns=(1, 1))
    assert key == _key([a], "ground")
    a.write_bytes(b"other")
    assert key != _key([a], "ground")
