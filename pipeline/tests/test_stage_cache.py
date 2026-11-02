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
    monkeypatch.setattr(sources, "missing", lambda _, **kw: list(missing))
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
    from turku3d.coarse.stages import deck_stage

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
    result = cache.run("bridges", "v1", [], lambda: deck_stage(*args))
    assert result[3] == {"json": b"7.25"}
    assert result[1][0].height_at is None
    assert cache.run("bridges", "v1", [], lambda: None)[3] == result[3]


def test_outdated_draft_serves_its_visual_collision_pair_during_rebuild(tmp_path):
    import json
    import time

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
                "pass_kind": "full",
                "visual": {tile.id: {"url": "vis.glb", "bytes": 1}},
                "collision": collision,
            }
        )
    )
    builder = Builder(tmp_path, run=lambda *_: None, code="new", workers=0)
    state = builder.request(tile.id, 12)
    assert state["state"] == "built"
    assert state["fragment"]["generation"] == "old"
    assert state["fragment"]["collision"] == collision
    assert tile.id in builder.servable and tile.id in builder.priority
    assert builder.journal.path.is_file()
    builder.stop()
    restored = Builder(tmp_path, run=lambda *_: None, code="new", workers=0)
    assert tile.id in restored.priority
    from turku3d.coarse.upgrade_queue import next_job

    restored.two_pass = True
    assert next_job(restored) == ((tile.id, 12), False)
    restored.building[tile.id] = 1
    for _ in range(3):  # A fresh client re-enters while the replacement is running.
        assert restored.request(tile.id, 12)["fragment"]["collision"] == collision
        assert json.loads(restored.manifest(tile.id).read_text())["pass_kind"] == "full"
    restored.building.clear()
    assert restored.cached(tile.id)
    import threading
    from types import SimpleNamespace

    restored.two_pass = False
    restored.run = lambda *_: SimpleNamespace(returncode=1, stderr="broken generator", stdout="")
    restored.request(tile.id, 12)
    worker = threading.Thread(target=restored._run, daemon=True)
    worker.start()
    deadline = time.monotonic() + 2
    while tile.id not in restored.failed and time.monotonic() < deadline:
        time.sleep(.01)
    assert restored.status()["failure_count"] == 1
    restored.stop()
    worker.join(2)
    fallback = restored.request(tile.id, 12)
    assert fallback["fragment"]["failure"]["error"] == "broken generator"
    assert fallback["fragment"]["collision"] == collision
    assert tile.id in restored.servable
    restored.code = lambda: "fixed"
    assert restored.request(tile.id, 12)["fragment"]["collision"] == collision
    assert tile.id in restored.priority


def test_generator_identity_ignores_service_edits_but_tracks_geometry(monkeypatch):
    from pathlib import Path

    from turku3d.coarse.packaging import code_hash

    before = code_hash()
    original = Path.read_bytes

    def service_edit(path):
        return original(path) + (
            b"\n# service edit"
            if path.name
            in (
                "service.py",
                "request_journal.py",
                "upgrade_queue.py",
                "prefetch.py",
                "worker_pool.py",
            )
            else b""
        )

    monkeypatch.setattr(Path, "read_bytes", service_edit)
    assert code_hash() == before

    def geometry_edit(path):
        return original(path) + (b"\n# geometry edit" if path.name == "bridges.py" else b"")

    monkeypatch.setattr(Path, "read_bytes", geometry_edit)
    assert code_hash() != before


def test_unfinished_demand_survives_a_long_restart(tmp_path):
    import json
    import time

    from turku3d.coarse.request_journal import RequestJournal

    rows = {"t0_0_0": {"at": time.time() - 86400, "distance": 25}}
    (tmp_path / ".requests.json").write_text(json.dumps(rows))
    journal = RequestJournal(tmp_path)
    assert journal.rows == rows
    journal.record("t0_1_0", 30)
    assert RequestJournal(tmp_path).rows["t0_0_0"] == rows["t0_0_0"]
    journal.discard("t0_0_0")
    assert "t0_0_0" not in RequestJournal(tmp_path).rows


def test_large_stage_entry_is_compressed_and_legacy_entry_is_read(tmp_path):
    import hashlib
    import pickle

    from turku3d.stage_cache import COMPRESSED, decompress_payload

    cache = StageCache(tmp_path)
    value = b'wall-atlas-' * 100000
    assert cache.run('wall', 'v1', [], lambda: value) == value
    path = next((tmp_path / 'wall').glob('*.pkl'))
    encoded = path.read_bytes()
    assert encoded[32:].startswith(COMPRESSED)
    assert len(encoded) < len(value) // 4
    assert decompress_payload(encoded[32:]) == pickle.dumps(value, protocol=5)
    path.write_bytes(hashlib.sha256(pickle.dumps(value, protocol=5)).digest()
                     + pickle.dumps(value, protocol=5))
    assert cache.run('wall', 'v1', [], lambda: b'changed') == value
    assert cache.metrics['wall']['cached']


def test_existing_stage_entries_compress_once(tmp_path):
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location('cache_storage', Path(__file__).resolve().parents[2]
                                              / 'tools/remote-drafts/cache-storage.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    cache = StageCache(tmp_path)
    cache.run('large', 'v1', [], lambda: b'brick' * 200000)
    target = next((tmp_path / 'large').glob('*.pkl'))
    import hashlib
    import pickle

    legacy = pickle.dumps(b'brick' * 200000, protocol=5)
    target.write_bytes(hashlib.sha256(legacy).digest() + legacy)
    first = module.compress_existing(tmp_path)
    assert first['files'] == 1
    assert first['after'] < first['before'] // 4
    assert cache.run('large', 'v1', [], lambda: b'wrong') == b'brick' * 200000
    second = module.compress_existing(tmp_path)
    assert second['before'] == second['after'] == target.stat().st_size


def test_live_stage_report_survives_failure_and_external_termination(tmp_path):
    import json
    import pytest
    from turku3d.stage_cache import finish_report

    path = tmp_path / "stage-times.json"
    cache = StageCache(tmp_path / "cache", report_path=path)
    def failed():
        assert json.loads(path.read_text())["mesh"]["status"] == "running"
        raise ValueError("broken geometry")
    with pytest.raises(ValueError, match="broken geometry"):
        cache.run("mesh", "code", [], failed)
    assert json.loads(path.read_text())["mesh"]["status"] == "failed"
    cache.run("good", "code", [], lambda: 42)
    assert json.loads(path.read_text())["good"]["status"] == "complete"
    cache.metrics["pack"] = dict(status="running", started=0, seconds=0)
    cache.flush()
    finish_report(path, "cancelled", "SIGKILL")
    report = json.loads(path.read_text())
    assert report["good"]["status"] == "complete"
    assert report["pack"]["status"] == "cancelled"
    assert report["pack"]["seconds"] > 0
    assert report["_build"]["error"] == "SIGKILL"


def test_shared_stage_store_reuses_across_worktrees_and_retains_multiple_versions(tmp_path, monkeypatch):
    monkeypatch.setenv('TURKU3D_SHARED_CACHE', str(tmp_path / 'shared'))
    first = StageCache(tmp_path / 'one' / '.stages' / 't0_0_0', retain=True)
    second = StageCache(tmp_path / 'two' / '.stages' / 't0_0_0', retain=True)
    calls = []
    for code in ('v1', 'v2', 'v3'):
        assert first.run('walls', code, [], lambda: calls.append(code) or code) == code
    assert second.run('walls', 'v1', [], lambda: 'incorrect') == 'v1'
    assert second.metrics['walls']['cached']
    assert len(list((tmp_path / 'shared/stages/t0_0_0/walls').glob('*.pkl'))) == 3
    assert calls == ['v1', 'v2', 'v3']


def test_shared_prepared_inputs_do_not_delete_other_versions(tmp_path, monkeypatch):
    from turku3d.coarse.inputs import _save, _load
    monkeypatch.setenv('TURKU3D_SHARED_CACHE', str(tmp_path))
    first, second = tmp_path / 'ground.one.pkl', tmp_path / 'ground.two.pkl'
    _save(first, 'one', [1])
    _save(second, 'two', [2])
    assert _load(first, 'one') == [1]
    assert _load(second, 'two') == [2]


def test_shared_source_hashes_reuse_across_revisions_and_detect_same_size_edits(tmp_path, monkeypatch):
    import hashlib
    from turku3d.tile_layer import identities
    source = tmp_path / 'source'
    source.write_bytes(b'one')
    monkeypatch.setenv('TURKU3D_SHARED_CACHE', str(tmp_path / 'shared'))
    monkeypatch.setenv('TURKU3D_WORK', str(tmp_path / 'first'))
    calls = []
    digest = hashlib.file_digest
    def count(*args, **kwargs):
        calls.append(1)
        return digest(*args, **kwargs)
    monkeypatch.setattr(hashlib, 'file_digest', count)
    first = identities({'source': source})
    monkeypatch.setenv('TURKU3D_WORK', str(tmp_path / 'second'))
    assert identities({'source': source}) == first
    assert len(calls) == 1
    stat = source.stat()
    source.write_bytes(b'two')
    os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    assert identities({'source': source}) != first
    assert len(calls) == 2


def test_ground_producer_identity_ignores_cache_telemetry_but_tracks_interpolation(monkeypatch):
    from pathlib import Path
    from turku3d.coarse import inputs
    from turku3d.coarse.input_keys import producer_code
    before = producer_code('ground')
    original = Path.read_bytes
    source = Path(inputs.__file__)
    def telemetry(path):
        data = original(path)
        return data + b'\n# extra cache metrics\n' if path == source else data
    monkeypatch.setattr(Path, 'read_bytes', telemetry)
    assert producer_code('ground') == before
    def geometry(path):
        data = original(path)
        return data.replace(b'h[missing] = points[nearest, 1]', b'h[missing] = points[nearest, 1] + 0.01') if path == source else data
    monkeypatch.setattr(Path, 'read_bytes', geometry)
    assert producer_code('ground') != before


def test_prepared_wfs_ignores_texture_pixels_but_tracks_visual_material_assignment(tmp_path, monkeypatch):
    from pathlib import Path
    from turku3d.coarse import inputs, ground_finishes, street_materials
    monkeypatch.setenv('TURKU3D_SHARED_CACHE', str(tmp_path / 'shared'))
    before = inputs._key([], 'wfs')
    original = Path.read_bytes
    source = Path(ground_finishes.__file__)
    def pixels(path):
        data = original(path)
        return data.replace(b'def maps(key, size=None):', b'def maps(key, size=256):') if path == source else data
    monkeypatch.setattr(Path, 'read_bytes', pixels)
    assert inputs._key([], 'wfs') == before
    monkeypatch.setattr(street_materials, 'FIELDSTONE', 130)
    assert inputs._key([], 'wfs') != before


def test_registering_unrelated_dataset_helper_preserves_existing_inputs(monkeypatch):
    from pathlib import Path
    from turku3d.coarse import input_keys
    before = input_keys.producer_code('ground')
    original = Path.read_bytes
    source = Path(input_keys.__file__)
    def extra_helper(path):
        data = original(path)
        return data.replace(b'HELPERS = {', b'HELPERS = {"new_dataset": ("new_helper",),') if path == source else data
    monkeypatch.setattr(Path, 'read_bytes', extra_helper)
    assert input_keys.producer_code('ground') == before


def test_shared_preparation_reads_published_result_after_waiting_for_another_version(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    import time
    from turku3d.coarse.inputs import _input_lock, _load, _save, _ABSENT
    target = tmp_path / 'shared/inputs/t0_0_0/wfs.same-key.pkl'
    started = threading.Event()
    prepared = []
    def prepare():
        with _input_lock(target):
            value = _load(target, 'same-key')
            if value is _ABSENT:
                prepared.append(1)
                started.set()
                time.sleep(.03)
                _save(target, 'same-key', {'buildings': [1, 2]})
                value = _load(target, 'same-key')
            return value
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(prepare)
        assert started.wait(2)
        second = pool.submit(prepare)
        assert first.result() == second.result() == {'buildings': [1, 2]}
    assert len(prepared) == 1


def test_building_layer_uses_mesh_content_not_upstream_producer_key(tmp_path):
    from turku3d import config as C
    from turku3d.package.gltf import GlbMesh
    from turku3d.wall_assembly import assemble_layers
    tile = C.VisualTile(0, 0, 0)
    cache = StageCache(tmp_path / '.stages' / tile.id)
    out = tmp_path / tile.id
    stage = out / '.stage'
    stage.mkdir(parents=True)
    for producer in ('one', 'two'):
        cache.metrics['building_visual'] = {'key': producer}
        mesh = GlbMesh('wall', np.array([[0., 0, 0], [10, 0, 0], [0, 10, 0]]),
                       np.array([[0, 1, 2]]), extras={'building_id': 'building', 'layer': 'buildings',
                                                       'object_id': 'wall', 'material_key': 'plaster'})
        assemble_layers(tile, [], [mesh], [], out, stage, cache)
        assert cache.metrics['building_layer']['cached'] == (producer == 'two')
