"""Tests for turku3d.col: writer/reader, meshopt codecs, materials copies, fixtures."""

from __future__ import annotations

import struct
import tempfile
from pathlib import Path

import numpy as np
import pytest

from turku3d import config as C
from turku3d.col import (ColFormatError, ColTile, Primitive, canonical_triangles, check_tile, read_col,
                         write_col)
from turku3d.col import fixtures, materials as M, meshopt
from turku3d.col.format import FLAG_AOI_BORDER, FLAG_FIX_INTERNAL_EDGES, crc_of, _HEADER, _PRIM, _SECTION

needs_encoder = pytest.mark.skipif(not meshopt.encoder_available(),
                                   reason="meshopt encoder needs node + tools/validator/node_modules (make col-deps)")


def grid(n=12, size=62.5, seed=0, mat=None):
    rng = np.random.default_rng(seed)
    xs = np.linspace(0, size, n)
    gx, gz = np.meshgrid(xs, xs)
    pos = np.stack([gx, 2 + 0.3 * rng.random(gx.shape), gz], -1).reshape(-1, 3)
    tris = []
    for r in range(n - 1):
        for c in range(n - 1):
            a = r * n + c
            tris += [(a, a + n, a + 1), (a + 1, a + n, a + n + 1)]
    tris = np.array(tris, np.uint32)
    mats = np.full(len(tris), M.mat_id("road") if mat is None else mat, np.uint8)
    if mat is None:
        mats[::3] = M.mat_id("sidewalk")
    return pos, tris, mats


def test_struct_sizes_match_spec():
    assert _HEADER.size == 96 and _SECTION.size == 24 and _PRIM.size == 48


def test_raw_round_trip_is_bit_exact():
    pos, tri, mat = grid()
    t = ColTile(1, 2, pos, tri, mat, [Primitive.box(M.mat_id("fence"), (1, 2, 3), (0.5, 0.6, 0.05))],
                meta={"tile": "c_1_2"})
    data = write_col(t, encoding="raw")
    assert len(data) % 8 == 0
    r = read_col(data)
    assert r.positions.tobytes() == t.positions.tobytes()
    assert np.array_equal(r.indices, t.indices) and np.array_equal(r.materials, t.materials)
    assert r.origin == (62.5, 0.0, 125.0) and r.id == "c_1_2"
    assert r.meta == {"tile": "c_1_2"}
    assert r.primitives[0].kind == 1 and r.primitives[0].params[:3] == pytest.approx((0.5, 0.6, 0.05))
    h = struct.unpack_from("<I", data, 88)[0]
    assert h == len(data)
    for s in r.sections:
        assert s["offset"] % 8 == 0
    assert [s["tag"] for s in r.sections] == ["POSN", "INDX", "MATL", "PRIM", "META"]


@needs_encoder
@pytest.mark.parametrize("vv", [0, 1])
@pytest.mark.parametrize("optimize", [True, False])
def test_meshopt_round_trip(vv, optimize):
    pos, tri, mat = grid(n=40, seed=vv)
    t = ColTile(0, 0, pos, tri, mat)
    canon = canonical_triangles(t)
    data = write_col(t, encoding="meshopt", optimize=optimize, vertex_version=vv)
    r = read_col(data)
    assert [s["encoding"] for s in r.sections[:2]] == [1, 2]
    assert canonical_triangles(r) == canon  # same float32 bits, same materials, modulo order/rotation
    if not optimize:
        assert r.positions.tobytes() == pos.astype(np.float32).tobytes()
    assert len(data) < len(write_col(ColTile(0, 0, pos, tri, mat), encoding="raw"))


@needs_encoder
def test_meshopt_decoder_matches_on_random_vertex_data():
    rng = np.random.default_rng(5)
    for n in (1, 15, 16, 17, 255, 256, 257, 1000, 3000):
        pos = (rng.random((n, 3)) * 62.5).astype(np.float32)
        tri = np.array([[0, 0, 0]], np.uint32)[:0]
        # encode vertices only through the bridge (no triangles -> optimize off)
        p2, _, _, posn, _ = meshopt.encode_mesh(pos, np.zeros((0, 3), np.uint32), optimize=False)
        dec = meshopt.decode_vertex_buffer(posn, n, 12)
        assert dec.tobytes() == pos.tobytes(), n
        del tri, p2


def test_reader_rejections():
    pos, tri, mat = grid()
    good = bytearray(write_col(ColTile(1, 2, pos, tri, mat), encoding="raw"))

    def fix(b):
        struct.pack_into("<I", b, 92, crc_of(bytes(b)))
        return bytes(b)

    b = bytearray(good)
    b[-1] ^= 1
    with pytest.raises(ColFormatError, match="CRC"):
        read_col(bytes(b))
    read_col(bytes(b), verify_crc=False)
    b = bytearray(good)
    struct.pack_into("<H", b, 4, 2)
    with pytest.raises(ColFormatError, match="version_major"):
        read_col(fix(b))
    b = bytearray(good)
    struct.pack_into("<H", b, 6, 9)
    assert read_col(fix(b)).version_minor == 9
    b = bytearray(good)
    b[:4] = b"XCOL"
    with pytest.raises(ColFormatError, match="magic"):
        read_col(bytes(b))
    with pytest.raises(ColFormatError, match="file_bytes"):
        read_col(bytes(good[:-8]))
    # MATL with a meshopt encoding is not allowed
    b = bytearray(good)
    for k in range(4):
        o = 96 + 24 * k
        if b[o:o + 4] == b"MATL":
            struct.pack_into("<H", b, o + 4, 1)
    with pytest.raises(ColFormatError, match="encoding"):
        read_col(fix(b))
    # unknown tag is skipped; renaming PRIM makes a required section missing
    b = bytearray(good)
    for k in range(4):
        o = 96 + 24 * k
        if b[o:o + 4] == b"PRIM":
            b[o:o + 4] = b"ZZZZ"
    with pytest.raises(ColFormatError, match="missing required section"):
        read_col(fix(b))


def test_unknown_tag_and_primitive_kind_are_skipped():
    pos, tri, mat = grid()
    t = ColTile(0, 0, pos, tri, mat, [Primitive.box(M.mat_id("fence"), (1, 1, 1), (1, 1, 1))], meta={"a": 1})
    b = bytearray(write_col(t, encoding="raw"))
    for k in range(5):
        o = 96 + 24 * k
        if b[o:o + 4] == b"META":
            b[o:o + 4] = b"FUTR"
        if b[o:o + 4] == b"PRIM":
            prim_off = struct.unpack_from("<I", b, o + 8)[0]
    b[prim_off] = 9  # unknown primitive kind
    struct.pack_into("<I", b, 92, crc_of(bytes(b)))
    r = read_col(bytes(b))
    assert r.meta is None and r.primitives == []
    assert any(s["tag"] == "FUTR" for s in r.sections)


def test_writer_rules_and_flags():
    pos, tri, mat = grid()
    bad = mat.copy()
    bad[0] = 0
    with pytest.raises(ColFormatError, match="unshippable"):
        write_col(ColTile(0, 0, pos, tri, bad), encoding="raw")
    write_col(ColTile(0, 0, pos, tri, bad), encoding="raw", strict=False)
    with pytest.raises(ColFormatError, match="out of range|vertex_count"):
        write_col(ColTile(0, 0, pos[:5], tri, mat), encoding="raw")
    dup = np.vstack([tri, tri[:1]])
    assert any("duplicate" in e for e in check_tile(ColTile(0, 0, pos, dup, np.r_[mat, mat[:1]])))
    degen = tri.copy()
    degen[0] = (0, 0, 1)
    assert any("degenerate" in e for e in check_tile(ColTile(0, 0, pos, degen, mat)))
    # FIX_INTERNAL_EDGES derives from FIX_EDGES materials; AOI_BORDER from the tile
    r = read_col(write_col(ColTile(0, 0, pos, tri, mat), encoding="raw"))
    assert r.flags == 0
    curb = mat.copy()
    curb[5] = M.mat_id("curb")
    r = read_col(write_col(ColTile(0, 0, pos, tri, curb, aoi_border=True), encoding="raw"))
    assert r.flags == FLAG_FIX_INTERNAL_EDGES | FLAG_AOI_BORDER


def test_bbox_covers_primitives_and_rotation():
    q = (0.0, np.sin(np.pi / 8), 0.0, np.cos(np.pi / 8))  # 45 deg about Y
    p = Primitive.box(M.mat_id("fence"), (0, 1, 0), (1, 1, 0.1), q)
    lo, hi = p.aabb()
    assert hi[0] == pytest.approx((1 + 0.1) / np.sqrt(2), abs=1e-6)
    t = ColTile(0, 0, np.zeros((0, 3)), np.zeros((0, 3)), np.zeros(0),
                [Primitive.capsule(M.mat_id("tree_trunk"), (5, 3, 5), 2, 0.3)])
    r = read_col(write_col(t, encoding="raw"))
    assert r.bbox_min == pytest.approx((4.7, 0.7, 4.7), abs=1e-6)
    assert r.bbox_max == pytest.approx((5.3, 5.3, 5.3), abs=1e-6)


def test_for_tile_subtracts_origin_in_float64():
    world = np.array([[125.0 + 1e-4, 2.5, -62.5 + 3.25], [130.0, 2.5, -60.0], [126.0, 2.6, -55.0]])
    t = ColTile.for_tile(2, -1, world, [[0, 1, 2]], [M.mat_id("road")])
    assert t.origin == (125.0, 0.0, -62.5)
    assert np.allclose(t.world_positions(), world, atol=1e-5)
    seam = ColTile.for_tile(1, -1, np.array([[125.0, 0, -60.0]]), np.zeros((0, 3)), np.zeros(0))
    # the shared seam vertex is exactly on x = 62.5 (tile-local) in the western tile and 0 in the eastern
    assert seam.positions[0, 0] == np.float32(62.5)


def test_materials_copies_are_current():
    mats = M.parse_spec()
    assert M.JSON_PATH.read_text() == M.render_json(mats), "run: python -m turku3d.col.materials --write"
    assert M.TS_PATH.read_text() == M.render_ts(mats), "run: python -m turku3d.col.materials --write"
    assert M.material_class(M.mat_id("water")) == "sensor"
    assert M.material_class(M.mat_id("stair_tread_helper")) == "query"
    assert M.material_class(250) == "solid" and not M.is_shippable(250) and not M.is_shippable(0)
    assert M.has_flag(M.mat_id("curb"), "FIX_EDGES") and M.has_flag(M.mat_id("curb"), "CURB")


@needs_encoder
def test_fixtures_are_current_and_deterministic():
    committed = C.REPO_ROOT / "tools" / "validator" / "fixtures"
    with tempfile.TemporaryDirectory() as td:
        written = fixtures.write_all(Path(td))
        for p in written:
            rel = p.relative_to(td)
            assert (committed / rel).read_bytes() == p.read_bytes(), f"stale fixture {rel}: make col-fixtures"


def test_fixture_tiles_read_back():
    good = fixtures.scene_c_1_2("good")
    data = write_col(good, encoding="raw")
    r = read_col(data)
    mats = set(np.unique(r.materials).tolist())
    for name in ("curb", "stair_slope", "stair_tread_helper", "building", "roof", "water", "wall"):
        assert M.mat_id(name) in mats
    assert len(r.primitives) == 3
    assert r.flags & FLAG_FIX_INTERNAL_EDGES
