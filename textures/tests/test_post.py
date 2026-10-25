import json
import shutil

import numpy as np
import pytest
from PIL import Image

from turkutex.post import maps, qa
from turkutex.post.pipeline import ktx2_header, pow2_floor, process, target_size
from turkutex.post.tileable import flatten_lowfreq, make_tileable


def textured(H=256, W=256, seed=0, gradient=60.0):
    """Non-tileable test texture: periodic-ish bricks + noise + a left-right lighting gradient."""
    rng = np.random.default_rng(seed)
    y, x = np.mgrid[0:H, 0:W]
    bricks = ((x // 23 + (y // 11) % 2) % 2) * 40.0
    noise = rng.normal(0, 12, (H, W))
    lum = 90 + bricks + noise + gradient * x / W
    img = np.stack([lum, lum * 0.8, lum * 0.6], -1)
    return np.clip(img, 0, 255).astype(np.uint8)


def test_make_tileable_fixes_seams():
    img = textured()
    assert qa.seam_ratio(img, "x") > 1.5 or qa.seam_rank(img, "x") > 95
    out = make_tileable(img, "xy")
    m = qa.metrics(out, "xy")
    assert m["pass_seam"], m
    assert m["seam_x"] < 1.5 and m["seam_y"] < 1.5
    # the image stays mostly the original content
    assert np.mean(np.abs(out.astype(int) - img.astype(int)) < 3) > 0.5


def test_make_tileable_x_only_keeps_vertical_structure():
    img = textured()
    img[:40] = 30  # plinth band at the top must survive (x-only tiling never mixes rows)
    out = make_tileable(img, "x")
    assert qa.seam_ratio(out, "x") < 1.5
    assert np.all(out[:40] == 30)


def test_flatten_removes_gradient():
    img = textured(gradient=120.0)
    before = qa.lowfreq(img, "x")
    after = qa.lowfreq(flatten_lowfreq(img, "xy"), "x")
    assert before > 0.1 and after < before / 3
    after_x = qa.lowfreq(flatten_lowfreq(img, "x"), "x")
    assert after_x < before / 3


def test_seam_rank_natural_joint():
    img = np.zeros((32, 64), np.uint8)
    img[:, ::8] = 200  # vertical joints every 8 px; wrap seam joins a joint column to plain
    assert qa.seam_rank(img, "x") <= 100


def test_sobel_normals_and_orm():
    img = textured(64, 64)
    n = maps.normals_sobel(img)
    assert n.shape == (64, 64, 3) and abs(n[..., 2].mean() - 255) < 60
    v = n.astype(float) / 255 * 2 - 1
    assert np.allclose(np.linalg.norm(v, axis=-1), 1, atol=0.02)
    h = maps.height_from_luminance(img)
    o = maps.orm(img, h, "wood_painted")
    assert o.shape == (64, 64, 3) and o[..., 2].max() == 0  # wood is not metallic
    assert maps.orm(img, h, "metal_painted")[..., 2].min() > 100


def test_target_size():
    assert pow2_floor(1254) == 1024 and pow2_floor(724) == 512
    assert target_size(2172, 724, None) == (2048, 512)
    assert target_size(10, 10, "512x256") == (512, 256)


def test_process_sobel_no_ktx2(tmp_path):
    src = tmp_path / "in.png"
    Image.fromarray(textured(300, 600)).save(src)
    r = process(src, tmp_path / "out", tile="x", flatten="x", ktx2=False, sobel=True)
    assert r["size"] == [512, 256] and r["qa_after"]["pass_seam"]
    for f in ("albedo.png", "normal.png", "height.png", "orm.png", "tile2x2.jpg", "post.json"):
        assert (tmp_path / "out" / f).exists()
    assert json.loads((tmp_path / "out" / "post.json").read_text())["files"]["albedo.png"]["sha256"]


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_ktx2_encode(tmp_path):
    from turkutex.paths import NODE_DIR
    if not (NODE_DIR / "node_modules" / "ktx2-encoder").exists():
        pytest.skip("run `make textures-setup` (npm install in textures/node)")
    src = tmp_path / "in.png"
    Image.fromarray(textured(128, 128)).save(src)
    r = process(src, tmp_path / "out", tile="xy", flatten="xy", ktx2=True, sobel=True)
    a, n = r["ktx2"]["albedo"]["header"], r["ktx2"]["normal"]["header"]
    assert a["valid"] and a["width"] == 128 and a["levels"] == 8 and a["supercompression"] == "BasisLZ(ETC1S)"
    assert n["valid"] and n["supercompression"] == "zstd"
    assert ktx2_header(tmp_path / "out" / "orm.ktx2")["valid"]


@pytest.mark.skipif(not maps.deepbump_available(), reason="DeepBump not installed (make textures-deepbump)")
def test_deepbump_normals():
    img = textured(256, 256)
    n = maps.normals_deepbump(img, "SMALL")
    assert n.shape == (256, 256, 3)
    h = maps.height_deepbump(n)
    assert h.shape == (256, 256)


def test_period_crop_and_feather():
    from turkutex.post.tileable import crop_to_period, detect_period_x, feather_wrap_x
    H, W, p = 64, 700, 100
    x = np.arange(W)
    row = np.where((x % p) < 30, 40.0, 180.0)  # a dark "window" every 100 px
    img = np.repeat(np.stack([row] * 3, -1)[None], H, 0).astype(np.uint8)
    img[:10] = 90  # cornice band
    assert detect_period_x(img) in (100, 200, 300)
    c, info = crop_to_period(img)
    assert c.shape[1] % 100 == 0 and info["repeats"] >= 2
    # the cut avoids vertical edges (window jambs): the columns around both cut positions are uniform
    for x0 in (info["crop"][0], info["crop"][1]):
        seg = img[20, max(0, x0 - 2):x0 + 3, 0].astype(int)
        assert seg.max() - seg.min() == 0
    f = feather_wrap_x(c, 4)
    assert qa.seam_ratio(f, "x") < 1.5
