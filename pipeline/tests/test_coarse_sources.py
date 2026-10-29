"""Structural source admission remains independent of decorative data arrivals."""

from test_coarse_prefetch import _real_raw, raw  # noqa: F401 (shared isolated raw fixture)
from turku3d import config as C
from turku3d.coarse.sources import Sources, WFS_LAYERS, block_of, tile_blocks


def test_structural_inputs_do_not_wait_for_orthophoto_or_signs(raw, tmp_path):
    from turku3d.coarse.inputs import gather

    tile = C.VisualTile(0, 1, 1)
    _real_raw(tile)
    sources = Sources()
    sources._listed = {b.name for b in tile_blocks(tile)}
    ortho = sources.ortho_source(tile)
    saved = ortho.read_bytes()
    ortho.unlink()
    decorative = (
        C.raw_dir("wfs")
        / f"coarse-{block_of(tile).name}"
        / (WFS_LAYERS[-1].replace(":", "_") + ".summary.json")
    )
    summary = decorative.read_bytes()
    decorative.unlink()
    sources.invalidate()
    assert {u.dataset for u in sources.missing_for_service(tile)} == {"ortho", "wfs"}
    assert sources.missing_for_service(tile, first_pass=True) == []
    key = sources.input_key(tile, first_pass=True)
    assert key and sources.input_key(tile) is None
    prepared = gather(tile, tmp_path / "coarse", sources, first_pass=True)
    assert prepared.key == key and prepared.buildings and prepared.roads
    ortho.write_bytes(saved)
    decorative.write_bytes(summary)
    sources.invalidate()
    assert sources.input_key(tile, first_pass=True) == key
    assert sources.input_key(tile) is not None


def test_prepared_keys_track_only_affected_input_producers(monkeypatch):
    from pathlib import Path
    from turku3d.coarse import inputs

    path = Path(inputs.__file__)
    original = path.read_bytes()
    read = Path.read_bytes
    edited = [original]
    monkeypatch.setattr(Path, "read_bytes", lambda p: edited[0] if p == path else read(p))
    before = {name: inputs._key([], name) for name in ("ground", "wfs", "roads")}
    edited[0] = original.replace(b"top = base + 3.0", b"top = base + 4.0")
    assert edited[0] != original
    assert inputs._key([], "wfs") != before["wfs"]
    assert inputs._key([], "ground") == before["ground"]
    assert inputs._key([], "roads") == before["roads"]
    edited[0] = original.replace(
        b"missing = sources.missing(tile, first_pass=first_pass)", b"missing = []"
    )
    assert edited[0] != original
    assert {name: inputs._key([], name) for name in before} == before
    edited[0] = original.replace(b"step = tile.size / CELLS", b"step = tile.size / (CELLS * 2)")
    assert inputs._key([], "ground") != before["ground"]
    assert inputs._key([], "wfs") == before["wfs"]


def test_producer_identity_is_independent_of_symlink_entrypoint(tmp_path):
    import importlib.util
    from turku3d.coarse import packaging

    link = tmp_path / "repo"
    link.symlink_to(C.REPO_ROOT, target_is_directory=True)
    spec = importlib.util.spec_from_file_location(
        "turku3d.coarse.linked_packaging", link / "pipeline/turku3d/coarse/packaging.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.code_hash() == packaging.code_hash()


def test_named_source_index_refreshes_changed_and_removed_summaries(tmp_path):
    import json
    from turku3d.coarse.sources import _Files

    summary = tmp_path / "a.summary.json"
    state = {"status": "empty", "bbox_en": [0, 0, 10, 10]}
    summary.write_text(json.dumps(state))
    files = _Files(tmp_path, "*.json")
    assert files.within(summary.name, (2, 2, 8, 8)) == [(summary, state)]
    assert not files.within(summary.name, (8, 8, 12, 12))
    assert files.within(summary.name, (8, 8, 12, 12), intersect=True) == [(summary, state)]
    state = {"status": "ok", "bbox_en": [0, 0, 5, 5]}
    summary.write_text(json.dumps(state))
    files.invalidate()
    assert files.named(summary.name) == [(summary, state)]
    assert not files.within(summary.name, (2, 2, 8, 8))
    summary.unlink()
    files.invalidate()
    assert files.named(summary.name) == ()


def test_nested_vrt_dependencies_follow_child_edits(tmp_path):
    from turku3d.coarse.sources import content_paths

    parent, child = tmp_path / "parent.vrt", tmp_path / "child.vrt"

    def vrt(name):
        return f'<VRTDataset><SourceFilename relativeToVRT="1">{name}</SourceFilename></VRTDataset>'

    parent.write_text(vrt(child.name))
    child.write_text(vrt("old.tif"))
    assert content_paths([parent]) == [parent, child, tmp_path / "old.tif"]
    child.write_text(vrt("replacement.tif"))
    assert content_paths([parent]) == [parent, child, tmp_path / "replacement.tif"]


def test_collision_identity_ignores_visual_changes_but_tracks_structure(monkeypatch):
    from pathlib import Path
    from turku3d.coarse import collision_key, packaging

    read = Path.read_bytes
    changes = {}
    monkeypatch.setattr(Path, "read_bytes", lambda p: changes.get(p, read(p)))
    before = collision_key.code_hash()
    visual = packaging.BRIDGE
    changes[visual] = read(visual) + b"\n// texture encoder changed\n"
    assert collision_key.code_hash() == before
    build = Path(collision_key.__file__).with_name("build.py")
    original = read(build)
    changes[build] = original.replace(b"if not first_pass:\n", b"if first_pass is False:\n")
    assert changes[build] != original
    assert collision_key.code_hash() == before
    changes[build] = original.replace(b"primitives = trunks(trees, ct)", b"primitives = []")
    assert changes[build] != original
    assert collision_key.code_hash() != before


def test_collision_remains_current_during_visual_only_deployment(tmp_path):
    import json
    from turku3d.coarse.collision import fragment
    from turku3d.coarse.service import Builder

    tile = C.VisualTile(0, 1, 2)
    builder = Builder(tmp_path, code="visual-new", workers=0)
    builder.collision_code = lambda: "structure-v1"
    directory = tmp_path / tile.id
    directory.mkdir()
    record = dict(
        coarse=True,
        tile=tile.id,
        code="visual-old",
        collision_code="structure-v1",
        collision={cell.id: {} for cell in tile.collision_tiles()},
        pass_kind="first",
    )
    (directory / "collision.json").write_text(json.dumps(record))
    assert fragment(builder, tile.id) is not None
    builder.collision_code = lambda: "structure-v2"
    assert fragment(builder, tile.id) is None
