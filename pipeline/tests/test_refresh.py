import hashlib
import json

import pytest
from turku3d import refresh


def test_declared_input_and_code_hashes(tmp_path):
    source = tmp_path / "source.json"
    source.write_text('{"value":1}')
    output = tmp_path / "MANIFEST.json"
    manifest = {
        "code": "version-1",
        "inputs": {str(source): hashlib.sha256(source.read_bytes()).hexdigest()},
    }
    output.write_text(json.dumps(manifest))
    assert refresh.manifest_current(output, "version-1")
    assert not refresh.manifest_current(output, "version-2")
    source.write_text('{"value":2}')
    assert not refresh.manifest_current(output, "version-1")


def test_texture_source_hash_cache_detects_changes(tmp_path, monkeypatch):
    source = tmp_path / "ortho.tif"
    source.write_bytes(b"source")
    manifest = tmp_path / "MANIFEST.json"
    manifest.write_text(
        json.dumps(
            {
                "inputs": [
                    {"path": str(source), "sha256": hashlib.sha256(source.read_bytes()).hexdigest()}
                ]
            }
        )
    )
    cache = tmp_path / "cache/source.json"
    assert refresh.texture_sources_current(manifest, cache)
    original_sha = refresh.sha
    monkeypatch.setattr(
        refresh,
        "sha",
        lambda path: (_ for _ in ()).throw(AssertionError("unchanged source was hashed again")),
    )
    assert refresh.texture_sources_current(manifest, cache)
    source.write_bytes(b"changed")
    with pytest.raises(AssertionError, match="unchanged source"):
        refresh.texture_sources_current(manifest, cache)
    monkeypatch.setattr(refresh, "sha", original_sha)
    assert not refresh.texture_sources_current(manifest, cache)


def test_buildings_refresh_requires_source_inventory_for_approved_facades(tmp_path, monkeypatch):
    layers = tmp_path / "build/layers"
    layers.mkdir(parents=True)
    export = tmp_path / "textures/assembly/MANIFEST.json"
    export.parent.mkdir(parents=True)
    export.write_text(json.dumps({"bound": ["building_0"]}))
    monkeypatch.setattr(refresh, "run", lambda *args: pytest.fail("producer ran"))
    with pytest.raises(RuntimeError, match="source inventory is unavailable"):
        refresh.buildings(layers)


def test_buildings_refresh_snapshots_approved_geometry_before_rebuild(tmp_path, monkeypatch):
    layers = tmp_path / "build/layers"
    inventory = layers / "buildings/facades.geojson"
    inventory.parent.mkdir(parents=True)
    inventory.write_text('{"features":[]}')
    export = tmp_path / "textures/assembly"
    export.mkdir(parents=True)
    (export / "MANIFEST.json").write_text(
        json.dumps({"bound": ["building_0"], "inventory_sha256": refresh.sha(inventory)})
    )
    monkeypatch.setattr(refresh, "manifest_current", lambda *args: False)
    calls = []
    monkeypatch.setattr(refresh, "run", lambda *args: calls.append(args))
    assert refresh.buildings(layers) == "rebuilt"
    assert (export / "APPROVED_INVENTORY.geojson").read_bytes() == inventory.read_bytes()
    assert calls == [("turku3d.buildings",)]


def test_refresh_graph_is_noop_after_first_run(monkeypatch, tmp_path):
    calls = []
    ready = set()

    def node(name, dependencies=()):
        def execute(*args):
            assert set(dependencies) <= ready
            if name in ready:
                return "cached"
            ready.add(name)
            calls.append(name)
            return "rebuilt"

        return execute

    monkeypatch.setattr(refresh, "ground", node("ground"))
    monkeypatch.setattr(refresh, "buildings", node("buildings"))
    monkeypatch.setattr(refresh, "ground_textures", node("ground-textures", ("ground",)))
    monkeypatch.setattr(refresh, "facades", node("facades", ("buildings",)))
    monkeypatch.setattr(refresh, "props", node("props", ("ground", "buildings")))
    monkeypatch.setattr(refresh, "bridges", node("bridges", ("ground",)))
    monkeypatch.setattr(refresh, "bridge_textures", node("bridge-textures"))
    monkeypatch.setattr(refresh, "outer_buildings", node("buildings_outer", ("buildings",)))
    first = refresh.refresh(tmp_path)
    assert set(calls) == set(first)
    second = refresh.refresh(tmp_path)
    assert set(second.values()) == {"cached"}
    assert len(calls) == 8


def test_bridge_refresh_rebuilds_for_stale_ground_or_code(tmp_path, monkeypatch):
    from turku3d.layers import write_manifest

    layers = tmp_path / "layers"
    source = tmp_path / "ground.json"
    source.write_text("ground-v1")
    bridge = layers / "bridges"
    write_manifest(
        bridge,
        inputs={str(source): refresh.sha(source)},
        params={},
        code=refresh.bridges_code_hash(),
        tiles=[],
    )
    calls = []
    monkeypatch.setattr(refresh, "run", lambda *args: calls.append(args))
    assert refresh.bridges(layers) == "cached"
    source.write_text("ground-v2")
    assert refresh.bridges(layers) == "rebuilt"
    assert calls == [("turku3d.bridges",)]
    calls.clear()
    monkeypatch.setattr(refresh, "bridges_code_hash", lambda: "changed-code")
    assert refresh.bridges(layers) == "rebuilt"
    assert calls == [("turku3d.bridges",)]


def test_ground_target_ignores_unrelated_stale_buildings(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(refresh, "ground", lambda layers: calls.append("ground") or "cached")
    monkeypatch.setattr(
        refresh,
        "ground_textures",
        lambda layers, textures: calls.append("ground-textures") or "cached",
    )
    monkeypatch.setattr(refresh, "buildings", lambda layers: pytest.fail("buildings ran"))
    result = refresh.refresh(tmp_path, "ground-textures")
    assert list(result) == calls == ["ground", "ground-textures"]


def test_facade_target_runs_buildings_before_migration(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(refresh, "buildings", lambda layers: calls.append("buildings") or "cached")
    monkeypatch.setattr(
        refresh, "facades", lambda layers, textures: calls.append("facades") or "rebuilt"
    )
    monkeypatch.setattr(refresh, "ground", lambda layers: pytest.fail("ground ran"))
    result = refresh.refresh(tmp_path, "facades")
    assert calls == ["buildings", "facades"]
    assert result == {"buildings": "cached", "facades": "rebuilt"}
