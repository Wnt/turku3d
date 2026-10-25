"""Tiny synthetic fixtures: material precedence, conservation, orientation and seam support."""
import numpy as np
import pytest
from turku3d.col.materials import mat_id
from turkutex.ground.splat import weights, pack_weights, rasterize_triangles
from turkutex.ground.ortho import colour_calibration, fuse, gpu_bytes


def test_semantics_override_green_colour_and_unknown_uses_segmentation():
    ids = np.array([[mat_id('road'), mat_id('curb'), 0, mat_id('cobblestone'), mat_id('quay')]], np.uint8)
    rgb = np.broadcast_to([35, 110, 40], (*ids.shape, 3))
    w = weights(ids, rgb)
    assert w[0, 0, :2].sum() == pytest.approx(1)
    assert w[0, 1, 2] == w[0, 2, 5] == w[0, 3, 3] == w[0, 4, 7] == 1
    np.testing.assert_allclose(w.sum(-1), 1)


def test_quantization_conserves_weight_including_random_blends():
    rng = np.random.default_rng(12)
    a = rng.random((17, 29, 8))
    a /= a.sum(-1, keepdims=True)
    lo, hi = pack_weights(a)
    packed = np.concatenate([lo, hi], -1)
    assert packed.dtype == np.uint8
    assert np.all(packed.astype(int).sum(-1) == 255)
    assert np.max(np.abs(packed / 255 - a)) < 1 / 255
    with pytest.raises(ValueError):
        pack_weights(np.zeros((1, 1, 8)))


def test_invalid_and_non_ground_ids_do_not_silently_become_grass():
    for mid in [-1, 200, mat_id('water'), mat_id('building')]:
        with pytest.raises(ValueError):
            weights(np.array([[mid]]), np.zeros((1, 1, 3)))


def test_triangle_rasterization_axis_orientation_and_neighbour_overlap():
    p = np.array([[0, 7, 0], [4, 7, 0], [0, 7, 4], [4, 7, 4]], float)
    tri = np.array([[0, 1, 2], [1, 3, 2]], np.uint32)
    material = np.array([mat_id('grass'), mat_id('road')], np.uint8)
    full = rasterize_triangles(p, tri, material, shape=(4, 4), x0=0, z0=0, pixel_m=1)
    right = rasterize_triangles(p, tri, material, shape=(4, 3), x0=1, z0=0, pixel_m=1)
    assert full[0, 0] == mat_id('grass') and full[-1, -1] == mat_id('road')
    np.testing.assert_array_equal(full[:, 1:], right)


def test_ortho_shadow_reduction_and_missing_coverage():
    p = np.full((64, 64, 3), 110, np.uint8)
    p[20:44, 20:44] = 45
    r = np.full_like(p, 110)
    valid = np.ones(p.shape[:2], bool)
    calibration = {'gain': [1, 1, 1], 'offset': [0, 0, 0]}
    fused, recent, uncertainty = fuse(p, r, valid, valid, calibration)
    assert fused[32, 32].mean() > 100
    np.testing.assert_array_equal(recent, r)
    assert not uncertainty.any()
    with pytest.raises(ValueError, match='uncovered'):
        fuse(p, r, ~valid, ~valid, calibration)


def test_calibration_is_shared_and_halo_pixels_are_identical():
    rng = np.random.default_rng(42)
    p = rng.integers(30, 180, (96, 160, 3), dtype=np.uint8)
    r = np.clip(p.astype(float) * 1.1 + 8, 0, 255).astype(np.uint8)
    v = np.ones(p.shape[:2], bool)
    calibration = colour_calibration(p, r, v)
    full = fuse(p, r, v, v, calibration, pixel_m=.1)[0]
    # 16px overlap is larger than all sequential filter support (4*.24/.1 + 4*.06/.1).
    left = fuse(p[:, :96], r[:, :96], v[:, :96], v[:, :96], calibration, pixel_m=.1)[0]
    right = fuse(p[:, 64:], r[:, 64:], v[:, 64:], v[:, 64:], calibration, pixel_m=.1)[0]
    np.testing.assert_array_equal(left[:, 80], right[:, 16])
    np.testing.assert_array_equal(full[:, 80], right[:, 16])


def test_gpu_budget_counts_small_mips_and_four_resident_tiles():
    assert gpu_bytes(4, 4, 16) == 48
    assert gpu_bytes(1, 1, 8) == 8
    tile = gpu_bytes(6378, 6378) + 2 * gpu_bytes(1152, 1152)
    shared = 8 * 3 * gpu_bytes(1024, 1024)
    assert 4 * tile + shared < 300_000_000


def test_vector_markings_keep_width_and_phase_when_clipped():
    from shapely.geometry import LineString, box
    from turkutex.ground.markings import paint_polygons
    line = LineString([(0, 0), (6, 0)])
    stripes = paint_polygons(line, 'crosswalk', width=3, stripe=.5, gap=.5)
    assert len(stripes) == 6
    assert sum(p.area for p in stripes) == pytest.approx(9)
    assert stripes[0].bounds == pytest.approx((0, -1.5, .5, 1.5))
    # Clip a pre-generated marking, never restart stripes at a tile border.
    left, right = box(-1, -2, 2.25, 2), box(2.25, -2, 7, 2)
    assert sum(p.intersection(left).area + p.intersection(right).area for p in stripes) == pytest.approx(9)
    stop = paint_polygons(line, 'stop_line', width=.5)
    assert stop[0].area == pytest.approx(3)


def test_ground_stone_does_not_inherit_facade_glass_roughness():
    from turkutex.post.maps import orm
    dark = np.full((8, 8, 3), 25, np.uint8)
    height = np.zeros((8, 8), np.uint8)
    assert orm(dark, height, 'ground_stone')[..., 1].min() > 180


def test_georeferenced_ortho_read_preserves_north_first_rows_and_nodata(tmp_path):
    from turku3d import config as C
    from turku3d.fetch.wms import write_geotiff
    from turkutex.ground.build import read_ortho
    rgb = np.zeros((8, 8, 3), np.uint8)
    rgb[..., 0] = np.arange(8)[:, None] * 20 + 10
    rgb[..., 1] = np.arange(8)[None, :] * 20 + 10
    valid = np.ones((8, 8), bool)
    valid[0, 0] = False
    path = tmp_path / 'ortho.tif'
    write_geotiff(path, rgb, valid, (C.ORIGIN_E, C.ORIGIN_N - 8, C.ORIGIN_E + 8, C.ORIGIN_N), 1, 'DEFLATE')
    actual, mask = read_ortho(path, 2, 3, 1, 4, 3)
    np.testing.assert_array_equal(actual, rgb[3:6, 2:6])
    assert mask.all()
    _, mask = read_ortho(path, 0, 0, 1, 8, 8)
    assert not mask[0, 0] and mask[7, 7]


def test_semantics_loader_reads_neighbouring_collision_tiles(tmp_path):
    from turku3d.config import VisualTile
    from turkutex.ground.build import semantic_raster
    p = np.array([[248, 0, 0], [252, 0, 0], [248, 0, 250], [252, 0, 250]], np.float64)
    np.savez(tmp_path / 'c_4_0.col.npz', positions=p, indices=np.array([[0, 1, 2], [1, 3, 2]], np.uint32),
             material=np.array([mat_id('curb'), mat_id('curb')], np.uint8))
    ids, inputs = semantic_raster(tmp_path, VisualTile(0, 0, 0), 250, 2)
    assert ids[100, -1] == mat_id('curb')  # gutter east of target tile
    assert len(inputs) == 1 and inputs[0]['sha256']


def test_native_encoder_cache_and_failure_are_atomic(tmp_path, monkeypatch):
    import json
    from pathlib import Path
    from PIL import Image
    from turkutex.ground import encode as codec
    source = tmp_path / 'source.png'
    Image.fromarray(np.full((4, 4, 3), 80, np.uint8)).save(source)
    output = tmp_path / 'test.ktx2'
    calls = []
    def successful(command, **kwargs):
        calls.append(command)
        Path(command[-2]).write_bytes(b'encoded-output')
    monkeypatch.setattr(codec.subprocess, 'run', successful)
    monkeypatch.setattr(codec, 'ktx2_header', lambda p: {'valid': True, 'levels': 3})
    first = codec.encode([source], output)
    assert codec.encode([source], output) == first and len(calls) == 1
    Image.fromarray(np.full((4, 4, 3), 90, np.uint8)).save(source)
    def failure(command, **kwargs):
        Path(command[-2]).write_bytes(b'partial')
        raise RuntimeError('encoder failed')
    monkeypatch.setattr(codec.subprocess, 'run', failure)
    with pytest.raises(RuntimeError):
        codec.encode([source], output)
    assert output.read_bytes() == b'encoded-output'
    assert not output.with_suffix('.tmp.ktx2').exists()
    assert json.loads(output.with_suffix('.encode.json').read_text()) == first


def test_native_normal_encoding_preserves_rgb_xyz(tmp_path):
    import subprocess
    from PIL import Image
    from turkutex.ground.encode import encode
    source = tmp_path / 'normal.png'
    expected = np.broadcast_to([128, 128, 255], (8, 8, 3)).astype(np.uint8)
    Image.fromarray(expected).save(source)
    output = tmp_path / 'normal.ktx2'
    encode([source], output, linear=True, uastc=True, normal=True)
    decoded = tmp_path / 'decoded.png'
    subprocess.run(['ktx', 'extract', '--transcode', 'rgb8', str(output), str(decoded)], check=True, capture_output=True)
    actual = np.asarray(Image.open(decoded).convert('RGB')).astype(int)
    assert np.max(np.abs(actual - expected)) <= 2  # catches toktx's default RGB=X / A=Y repack
