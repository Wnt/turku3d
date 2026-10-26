import hashlib
import json

import pytest
from turku3d.package.ground_textures import publish_ground


def test_ground_publication_fallback_hashes_and_unchanged_writes(tmp_path):
    root, out = tmp_path / "textures/ground", tmp_path / "dist"
    assert publish_ground(root, out, ["t0_0_0"]) == {}
    library = root.parent / "library"
    library.mkdir(parents=True)
    for name in ("albedo", "normal", "orm"):
        (library / f"{name}.array.ktx2").write_bytes(b"array fixture")
    tile = root / "t0_0_0"
    tile.mkdir(parents=True)
    maps = {}
    for name in ("ortho", "splat0", "splat1"):
        data = name.encode()
        (tile / f"{name}.ktx2").write_bytes(data)
        maps[name] = {"uri": f"{name}.ktx2", "sha256": hashlib.sha256(data).hexdigest()}
    spec = {"textures": maps, "ground_inputs": []}
    (tile / "material.json").write_text(json.dumps(spec))
    (library / "normal.array.ktx2").unlink()
    assert publish_ground(root, out, ["t0_0_0"]) == {}
    spec["ground_inputs"] = [{"path": "c_0_0.col.npz", "sha256": "fixture"}]
    (tile / "material.json").write_text(json.dumps(spec))
    with pytest.raises(FileNotFoundError, match="normal.array.ktx2"):
        publish_ground(root, out, ["t0_0_0"])
    (library / "normal.array.ktx2").write_bytes(b"array fixture")
    result = publish_ground(root, out, ["t0_0_0", "t0_1_0"])
    assert result["t0_0_0"]["spec"].startswith("ground/t0_0_0/")
    assert len(result) == 1
    target = out / result["t0_0_0"]["spec"]
    mtime = target.stat().st_mtime_ns
    assert publish_ground(root, out, ["t0_0_0"]) == result
    assert target.stat().st_mtime_ns == mtime
    (tile / "splat0.ktx2").write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="hash mismatch"):
        publish_ground(root, out, ["t0_0_0"])


def test_stale_ground_binding_names_refresh_target(tmp_path):
    root, out, layer = tmp_path / "ground", tmp_path / "dist", tmp_path / "layer"
    tile = root / "t0_0_0"
    tile.mkdir(parents=True)
    layer.mkdir()
    (layer / "c_0_0.col.npz").write_bytes(b"new geometry")
    (tile / "material.json").write_text(
        json.dumps(
            {
                "ground_inputs": [{"path": "c_0_0.col.npz", "sha256": "old"}],
                "textures": {},
            }
        )
    )
    library = root.parent / "library"
    library.mkdir()
    for name in ("albedo", "normal", "orm"):
        (library / f"{name}.array.ktx2").write_bytes(b"array")
    with pytest.raises(ValueError, match="stale ground binding.*make refresh"):
        publish_ground(root, out, ["t0_0_0"], layer)


def test_ground_texture_code_change_blocks_package(tmp_path):
    root = tmp_path / "ground"
    root.mkdir()
    (root / "MANIFEST.json").write_text(json.dumps({"code_version": "obsolete"}))
    with pytest.raises(ValueError, match="ground texture code changed; run make refresh"):
        publish_ground(root, tmp_path / "dist", ["t0_0_0"])
