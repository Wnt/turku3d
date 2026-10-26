import hashlib
import json
import os

import pytest
from turku3d import refresh
from turku3d.package.assemble import require_office_layers


def test_office_assembly_requires_accepted_structures_and_bridges():
    with pytest.raises(ValueError, match="bridges.*make refresh"):
        require_office_layers({"ground": {}, "buildings": {}})
    with pytest.raises(ValueError, match="structures.*make refresh"):
        require_office_layers({"ground": {}, "buildings": {}, "bridges": {}})
    with pytest.raises(ValueError, match="structures failed real-data acceptance"):
        require_office_layers(
            {"ground": {}, "buildings": {}, "bridges": {}, "structures": {"params": {}}}
        )
    accepted = {
        "ground": {},
        "buildings": {},
        "bridges": {"tiles": {}},
        "structures": {
            "params": {"acceptance_passed": True},
            "tiles": {"c_0_0.col.npz": {}, "t0_0_0.vis.glb": {}},
        },
    }
    with pytest.raises(ValueError, match="bridges layer has no visual or collision tiles"):
        require_office_layers(accepted)
    accepted["bridges"]["tiles"] = {"c_0_0.col.npz": {}, "t0_0_0.vis.glb": {}}
    accepted["structures"]["tiles"] = {}
    with pytest.raises(ValueError, match="structures layer has no visual or collision tiles"):
        require_office_layers(accepted)


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


def test_rebuild_detaches_linked_layer_without_writing_shared_data(tmp_path, monkeypatch):
    shared = tmp_path / "data/ground"
    shared.mkdir(parents=True)
    (shared / "MANIFEST.json").write_text('{"code":"old"}')
    work = tmp_path / "work"
    layers = work / "build/layers"
    layers.mkdir(parents=True)
    (layers / "ground").symlink_to(shared, target_is_directory=True)
    monkeypatch.setattr(refresh, "manifest_current", lambda *args: False)
    monkeypatch.setattr(refresh, "ground_code_hash", lambda: "new")

    def build(*args):
        (layers / "ground/MANIFEST.json").write_text("new")

    monkeypatch.setattr(refresh, "run", build)
    assert refresh.ground(layers) == "rebuilt"
    assert not (layers / "ground").is_symlink()
    assert (layers / "ground/MANIFEST.json").read_text() == "new"
    assert (shared / "MANIFEST.json").read_text() == '{"code":"old"}'


def test_refresh_refuses_published_root_without_detaching_links(tmp_path, monkeypatch):
    data = tmp_path / "data"
    (data / "live/layers").mkdir(parents=True)
    (data / "build").mkdir()
    (data / "build/layers").symlink_to(data / "live/layers", target_is_directory=True)
    (data / "textures").symlink_to(data / "live/textures", target_is_directory=True)
    monkeypatch.setattr(refresh.C, "data_root", lambda: data)
    with pytest.raises(RuntimeError, match="refresh runs in sandboxes"):
        refresh.refresh(data)
    with pytest.raises(RuntimeError, match="refresh runs in sandboxes"):
        refresh.local_directory(data / "build/layers/ground", data)
    assert (data / "build/layers").is_symlink()
    assert (data / "textures").is_symlink()


def test_parallel_rebuilds_detach_shared_layers_ancestor_once(tmp_path, monkeypatch):
    shared = tmp_path / "data/layers"
    for name in ("ground", "buildings"):
        (shared / name).mkdir(parents=True)
    work = tmp_path / "work"
    (work / "build").mkdir(parents=True)
    (work / "build/layers").symlink_to(shared, target_is_directory=True)
    monkeypatch.setattr(refresh, "manifest_current", lambda *args: False)
    monkeypatch.setattr(refresh, "ground_code_hash", lambda: "new")
    monkeypatch.setattr(refresh, "buildings_code_hash", lambda: "new")
    for name in (
        "ground",
        "buildings",
        "structures",
        "ground_textures",
        "facades",
        "props",
        "bridges",
        "bridge_textures",
        "outer_buildings",
    ):
        monkeypatch.setattr(refresh, name, lambda *args: "rebuilt")
    refresh.refresh(work)
    assert not (work / "build/layers").is_symlink()
    assert (shared / "ground").is_dir()


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
    assert calls == [("turku3d.buildings", "--jobs", os.environ.get("TURKU3D_JOBS", "3"))]


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
    monkeypatch.setattr(refresh, "structures", node("structures", ("ground",)))
    monkeypatch.setattr(refresh, "bridge_textures", node("bridge-textures"))
    monkeypatch.setattr(refresh, "outer_buildings", node("buildings_outer", ("buildings",)))
    monkeypatch.setattr(refresh, "roads", node("roads"))
    first = refresh.refresh(tmp_path)
    assert set(calls) == set(first)
    second = refresh.refresh(tmp_path)
    assert set(second.values()) == {"cached"}
    assert len(calls) == 10


def test_structures_refresh_requires_passed_real_acceptance(tmp_path, monkeypatch):
    from turku3d.layers import write_manifest

    layers = tmp_path / "layers"
    source = tmp_path / "ground.json"
    source.write_text("ground-v1")
    directory = layers / "structures"
    write_manifest(
        directory,
        inputs={str(source): refresh.sha(source)},
        params={"acceptance_passed": False},
        code=refresh.structures_code_hash(),
        tiles=[],
    )
    calls = []
    monkeypatch.setattr(refresh, "run", lambda *args: calls.append(args))
    assert refresh.structures(layers) == "rebuilt"
    assert calls == [
        ("turku3d.structures", "--area", "office-block", "--ground-layer", str(layers / "ground"))
    ]
    calls.clear()
    write_manifest(
        directory,
        inputs={str(source): refresh.sha(source)},
        params={"acceptance_passed": True},
        code=refresh.structures_code_hash(),
        tiles=[],
    )
    assert refresh.structures(layers) == "cached"
    assert not calls


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


def test_stage_ground_library_uses_shared_assets_without_replacing_local_build(
    monkeypatch, tmp_path
):
    source = tmp_path / "data/textures/library"
    source.mkdir(parents=True)
    for name in ("albedo", "normal", "orm"):
        (source / f"{name}.array.ktx2").write_bytes(name.encode())
    local = tmp_path / "work/textures/library"
    local.mkdir(parents=True)
    (local / "normal.array.ktx2").write_bytes(b"local")
    monkeypatch.setattr(refresh.C, "data_root", lambda: tmp_path / "data")
    refresh.stage_ground_library(local.parent)
    assert (local / "albedo.array.ktx2").read_bytes() == b"albedo"
    assert (local / "normal.array.ktx2").read_bytes() == b"local"
    assert (local / "orm.array.ktx2").read_bytes() == b"orm"


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
