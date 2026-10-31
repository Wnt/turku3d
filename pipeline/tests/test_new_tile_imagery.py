"""Blank imagery admission must never invalidate an already published neighbour."""

import hashlib
import io
import json

import numpy as np
import pytest
import rasterio
from PIL import Image
from test_coarse_prefetch import _real_raw, raw  # noqa: F401
from turku3d import config as C
from turku3d.coarse.imagery import ImagerySources, blank, fetch
from turku3d.coarse.sources import Sources, block_of, block_tiles, content_paths, tile_blocks
from turku3d.fetch import wms


@pytest.mark.parametrize("fallback_white", [False, True])
@pytest.mark.parametrize("repair", [False, True])
@pytest.mark.usefixtures("raw")
def test_new_tile_fallback_preserves_warm_neighbours(
    tmp_path, monkeypatch, fallback_white, repair
):
    warm = C.VisualTile(0, 1, 1)
    _real_raw(warm)
    root = tmp_path / "coarse"
    original = Sources()
    original._listed = {b.name for b in tile_blocks(warm)}
    vrt = original.ortho_source(warm)
    tif = next(p for p in content_paths([vrt]) if p.suffix == ".tif")
    with rasterio.open(tif, "r+") as ds:
        ds.write(np.full((3, ds.height, ds.width), 255, np.uint8))
    siblings = block_tiles(block_of(warm))
    new = next(t for t in siblings if t != warm)
    for tile in siblings:
        if tile != new or repair:
            path = root / tile.id / "manifest.json"
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps(dict(pass_kind="full")))
    before_key = original.input_key(warm)
    assert before_key
    before = {
        p: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in [*C.raw_dir().rglob("*"), *root.rglob("*")]
        if p.is_file() and p.name != ".retention.lock"
    }
    sources = ImagerySources(root)
    assert sources.ortho_source(new) == (vrt if repair else None)
    assert sources.ortho_source(warm) == vrt
    calls = []

    def get(url, params, **kwargs):
        calls.append(params["LAYERS"])
        white = "2022" in params["LAYERS"] or fallback_white
        image = Image.new(
            "RGBA",
            (params["WIDTH"], params["HEIGHT"]),
            (255, 255, 255, 255) if white else (45, 95, 35, 255),
        )
        buffer = io.BytesIO()
        image.save(buffer, "PNG")
        return buffer.getvalue(), {"content-type": "image/png"}

    real_fetch = wms.fetch_layer
    monkeypatch.setattr(wms, "fetch_layer", lambda *a, **kw: real_fetch(*a, **kw, get=get))
    if fallback_white:
        from turku3d.coarse.sources import NoCoverage

        with pytest.raises(NoCoverage, match="neither 2022 nor 2025"):
            fetch(
                block_of(warm),
                root=root,
                log=lambda *_: None,
                repair_tiles=[new.id] if repair else [],
            )
        assert ImagerySources(root).ortho_source(new) == (vrt if repair else None)
    else:
        fetch(
            block_of(warm), root=root, log=lambda *_: None, repair_tiles=[new.id] if repair else []
        )
        selected = ImagerySources(root).ortho_source(new)
        assert selected != vrt and not blank(new, selected)
        with rasterio.open(selected) as ds:
            assert ds.read()[:, 0, 0].tolist() == [45, 95, 35]
        state = json.loads(selected.with_suffix(".summary.json").read_text())
        assert state["layer"] == "Ilmakuva 2025"
        fetch(block_of(warm), root=root, log=lambda *_: None)
    assert calls == ["Ilmakuva 2022 True ortho", "Ilmakuva 2025"]
    assert original.input_key(warm) == before_key
    assert ImagerySources(root).ortho_source(warm) == vrt
    # Registry manifests record additive downloads; original imagery and published manifests stay intact.
    for path, digest in before.items():
        if path.name not in {"MANIFEST.jsonl", "SHA256SUMS"}:
            assert hashlib.sha256(path.read_bytes()).hexdigest() == digest, path
