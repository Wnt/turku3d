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


def test_service_requests_optional_osm_without_motion_prefetch(monkeypatch):
    from turku3d import config as C
    from turku3d.coarse.sources import Sources, Unit, block_of

    sources, tile = Sources(), C.VisualTile(0, 0, 0)
    missing = [Unit("wfs", block_of(tile))]
    monkeypatch.setattr(sources, "missing", lambda _: list(missing))
    monkeypatch.setattr(sources, "osm_source", lambda _: None)
    assert sources.missing_for_service(tile) == [*missing, Unit("osm", block_of(tile))]
    monkeypatch.setattr(sources, "osm_source", lambda _: "present")
    assert sources.missing_for_service(tile) == missing


def test_new_generator_is_not_served_from_old_manifest_during_rebuild(tmp_path):
    import json

    from turku3d.coarse.service import Builder

    builder = Builder(tmp_path, run=lambda *_: None, code="new", workers=0)
    path = builder.manifest("t0_0_0")
    path.parent.mkdir()
    path.write_text(json.dumps({"code": "old"}))
    builder.servable = frozenset({"t0_0_0"})
    builder.building["t0_0_0"] = 0
    assert builder.request("t0_0_0", 0)["state"] == "building"
    path.write_text(json.dumps({"code": "new"}))
    assert builder.request("t0_0_0", 0)["state"] == "built"
    builder.stop()


def test_artifact_stage_restores_outputs_without_rerunning(tmp_path):
    cache = StageCache(tmp_path / "cache")
    calls = []

    def operation(directory):
        calls.append(1)
        (directory / "layer.glb").write_bytes(b"geometry")
        return [2, 3]

    first, second = tmp_path / "first", tmp_path / "second"
    assert cache.files("layer", "v1", [], first, operation) == [2, 3]
    first.joinpath("layer.glb").write_bytes(b"bad")
    assert cache.files("layer", "v1", [], second, operation) == [2, 3]
    assert second.joinpath("layer.glb").read_bytes() == b"geometry"
    assert calls == [1]


def test_old_payloads_remain_addressable_while_generator_rebuilds():
    from turku3d.coarse.service import immutable_payload

    digest = "a" * 64
    assert immutable_payload(f"vis.{digest}.glb")
    assert immutable_payload(f"roads.{digest}.json")
    assert immutable_payload(f"col/c_1_-1.{digest}.col")
    assert not immutable_payload("manifest.json")
    assert not immutable_payload("vis.glb")
    assert not immutable_payload(f"../vis.{digest}.glb")


def test_bridge_cache_consumes_build_local_sampler_before_serialization(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from turku3d import config as C
    from turku3d.coarse import build as generator
    from turku3d.coarse.bridges import Deck

    def sampler(points):
        return np.full(len(points), 7.25)

    deck = Deck({}, None, np.array([0, 1]), np.array([7, 8]), [], sampler)
    monkeypatch.setattr(generator.bridges, "produce", lambda *_: [deck])

    def roads(_roads, decks):
        return {"json": str(decks[0].height_at(np.zeros((1, 2)))[0]).encode()}

    monkeypatch.setattr(generator.bridges, "road_heights", roads)
    grid = np.zeros((3, 3))
    cache = StageCache(tmp_path)
    args = (
        C.VisualTile(0, 1, 0),
        grid,
        np.array([[0, 1, 2]]),
        np.array([1]),
        np.array([False]),
        SimpleNamespace(roads={}),
    )
    result = cache.run("bridges", "v1", [], lambda: generator.deck_stage(*args))
    assert result[3] == {"json": b"7.25"}
    assert result[1][0].height_at is None
    assert cache.run("bridges", "v1", [], lambda: None)[3] == result[3]


def test_stale_draft_stays_servable_and_unfinished_requests_survive_restart(tmp_path):
    import json

    from turku3d import config as C
    from turku3d.coarse.service import Builder

    tile = C.VisualTile(0, 1, 0)
    directory = tmp_path / tile.id
    (directory / "col").mkdir(parents=True)
    (directory / "vis.glb").write_bytes(b"v")
    collision = {}
    for cell in tile.collision_tiles():
        url = f"col/{cell.id}.col"
        (directory / url).write_bytes(b"c")
        collision[cell.id] = {"url": url, "bytes": 1}
    (directory / "manifest.json").write_text(
        json.dumps(
            {
                "coarse": True,
                "tile": tile.id,
                "code": "old",
                "visual": {tile.id: {"url": "vis.glb", "bytes": 1}},
                "collision": collision,
            }
        )
    )
    builder = Builder(tmp_path, run=lambda *_: None, code="new", workers=0)
    state = builder.request(tile.id, 12)
    assert state["state"] == "built" and state["stale"]
    assert state["refresh"]["state"] == "queued"
    assert tile.id in builder.servable and tile.id in builder.priority
    assert builder.journal.path.is_file()
    builder.stop()
    restored = Builder(tmp_path, run=lambda *_: None, code="new", workers=0)
    assert tile.id in restored.priority
    assert restored._next() == (tile.id, 12)
    assert restored.cached(tile.id)
    restored.stop()
