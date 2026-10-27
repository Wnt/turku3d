""".col collision tile format v1.0: writer and reader (docs/specs/col-format.md).

    from turku3d.col import ColTile, Primitive, write_col, read_col
    tile = ColTile.for_tile(i, j, positions_world_local, indices, materials, primitives=[...])
    data = write_col(tile, "c_1_2.col")          # meshopt when the encoder is available, else RAW
    back = read_col(data)                         # verifies magic, version, length and CRC

Positions in a ``ColTile`` are tile-local float32 (``origin + position`` = world-local engine coords).
"""

from __future__ import annotations

import json
import struct
import sys
import zlib
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from turku3d import config as C
from turku3d.col import materials as M
from turku3d.col import meshopt

MAGIC = b"TCOL"
VERSION_MAJOR = 1
VERSION_MINOR = 0
HEADER_BYTES = 96
SECTION_BYTES = 24
PRIM_BYTES = 48

FLAG_FIX_INTERNAL_EDGES = 1 << 0
FLAG_AOI_BORDER = 1 << 1

ENC_RAW, ENC_MESHOPT_VERTEX, ENC_MESHOPT_INDEX = 0, 1, 2
ALLOWED_ENCODINGS = {b"POSN": (0, 1), b"INDX": (0, 2), b"MATL": (0,), b"PRIM": (0,), b"META": (0,)}
SECTION_ORDER = (b"POSN", b"INDX", b"MATL", b"PRIM", b"META")
STRIDE = {b"POSN": 12, b"INDX": 4, b"MATL": 1, b"PRIM": PRIM_BYTES, b"META": 1}

PRIM_BOX, PRIM_CAPSULE, PRIM_CYLINDER = 1, 2, 3
PRIM_KINDS = {PRIM_BOX: "box", PRIM_CAPSULE: "capsule", PRIM_CYLINDER: "cylinder"}

MAX_TRIANGLES = 150_000
MAX_FILE_BYTES = 1_000_000
MIN_TRI_AREA = 1e-6

_HEADER = struct.Struct("<4sHHII3d2i3f3f3IHHII")
_SECTION = struct.Struct("<4sHHIIII")
_PRIM = struct.Struct("<BBH3f4f4f")
assert _HEADER.size == HEADER_BYTES and _SECTION.size == SECTION_BYTES and _PRIM.size == PRIM_BYTES


class ColFormatError(ValueError):
    pass


@dataclass
class Primitive:
    kind: int
    material: int
    center: tuple[float, float, float]
    rotation: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 1.0)
    params: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    flags: int = 0

    @staticmethod
    def box(material: int, center, half_extents, rotation=(0, 0, 0, 1)) -> Primitive:
        hx, hy, hz = half_extents
        return Primitive(PRIM_BOX, material, tuple(center), tuple(rotation), (hx, hy, hz, 0.0))

    @staticmethod
    def capsule(material: int, center, half_height, radius, rotation=(0, 0, 0, 1)) -> Primitive:
        return Primitive(
            PRIM_CAPSULE, material, tuple(center), tuple(rotation), (half_height, radius, 0.0, 0.0)
        )

    @staticmethod
    def cylinder(material: int, center, half_height, radius, rotation=(0, 0, 0, 1)) -> Primitive:
        return Primitive(
            PRIM_CYLINDER, material, tuple(center), tuple(rotation), (half_height, radius, 0.0, 0.0)
        )

    def aabb(self) -> tuple[np.ndarray, np.ndarray]:
        x, y, z, w = self.rotation
        R = np.array(
            [
                [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
            ]
        )
        c = np.asarray(self.center, np.float64)
        if self.kind == PRIM_BOX:
            ext = np.abs(R) @ np.asarray(self.params[:3], np.float64)
        else:
            hh, r = self.params[0], self.params[1]
            axis = R[:, 1] * hh
            if self.kind == PRIM_CAPSULE:
                ext = np.abs(axis) + r
            else:  # cylinder: disc radius projected on each axis
                ext = np.abs(axis) + r * np.sqrt(np.clip(1 - R[:, 1] ** 2, 0, 1))
        return c - ext, c + ext


@dataclass
class ColTile:
    tile_i: int
    tile_j: int
    positions: np.ndarray  # (N,3) float32 tile-local
    indices: np.ndarray  # (M,3) uint32
    materials: np.ndarray  # (M,) uint8
    primitives: list[Primitive] = field(default_factory=list)
    origin: tuple[float, float, float] | None = None  # default: (i*62.5, 0, j*62.5)
    aoi_border: bool = False
    fix_internal_edges: bool | None = None  # None: derive from FIX_EDGES materials (spec §2.1)
    meta: dict | None = None
    # Filled by the reader:
    flags: int = 0
    version_minor: int = VERSION_MINOR
    material_table_version: int = M.MATERIAL_TABLE_VERSION
    bbox_min: tuple | None = None
    bbox_max: tuple | None = None
    sections: list[dict] = field(default_factory=list)

    def __post_init__(self):
        self.positions = np.ascontiguousarray(self.positions, np.float32).reshape(-1, 3)
        self.indices = np.ascontiguousarray(self.indices, np.uint32).reshape(-1, 3)
        self.materials = np.ascontiguousarray(self.materials, np.uint8).reshape(-1)
        if self.origin is None:
            s = C.COLLISION_TILE_SIZE
            self.origin = (self.tile_i * s, 0.0, self.tile_j * s)
        self.origin = tuple(float(v) for v in self.origin)

    @property
    def id(self) -> str:
        return f"c_{self.tile_i}_{self.tile_j}"

    @classmethod
    def for_tile(
        cls, tile_i: int, tile_j: int, world_positions, indices, materials, **kw
    ) -> ColTile:
        """Build from world-local (engine) float64 positions: subtracts the tile origin in float64."""
        s = C.COLLISION_TILE_SIZE
        origin = (tile_i * s, 0.0, tile_j * s)
        local = np.asarray(world_positions, np.float64).reshape(-1, 3) - np.asarray(origin)
        return cls(
            tile_i, tile_j, local.astype(np.float32), indices, materials, origin=origin, **kw
        )

    def world_positions(self) -> np.ndarray:
        return self.positions.astype(np.float64) + np.asarray(self.origin)

    def computed_flags(self) -> int:
        f = 0
        fix = self.fix_internal_edges
        if fix is None:
            fix = any(M.has_flag(int(m), "FIX_EDGES") for m in np.unique(self.materials))
        if fix:
            f |= FLAG_FIX_INTERNAL_EDGES
        if self.aoi_border:
            f |= FLAG_AOI_BORDER
        return f

    def compute_bbox(self) -> tuple[np.ndarray, np.ndarray]:
        lo = np.full(3, np.inf)
        hi = np.full(3, -np.inf)
        if len(self.indices):
            used = self.positions[np.unique(self.indices)].astype(np.float64)
            lo, hi = np.minimum(lo, used.min(0)), np.maximum(hi, used.max(0))
        for p in self.primitives:
            a, b = p.aabb()
            lo, hi = np.minimum(lo, a), np.maximum(hi, b)
        if not np.isfinite(lo).all():
            lo = hi = np.zeros(3)
        return lo, hi


# --------------------------------------------------------------------------------------
# Validation (spec §3)
# --------------------------------------------------------------------------------------


def check_tile(tile: ColTile, *, shippable: bool = True) -> list[str]:
    """Return a list of rule violations (empty = OK)."""
    errs = []
    n, m = len(tile.positions), len(tile.indices)
    if len(tile.materials) != m:
        errs.append(f"MATL has {len(tile.materials)} entries for {m} triangles")
    if m and int(tile.indices.max()) >= n:
        errs.append(f"index {int(tile.indices.max())} >= vertex_count {n}")
        return errs
    if not np.isfinite(tile.positions).all():
        errs.append("non-finite positions")
    if m:
        p = tile.positions.astype(np.float64)
        a, b, c = p[tile.indices[:, 0]], p[tile.indices[:, 1]], p[tile.indices[:, 2]]
        area = 0.5 * np.linalg.norm(np.cross(b - a, c - a), axis=1)
        bad = np.nonzero(area < MIN_TRI_AREA)[0]
        if len(bad):
            errs.append(
                f"{len(bad)} degenerate triangles (area < 1e-6 m²), first {bad[:5].tolist()}"
            )
        key = np.sort(tile.indices, axis=1)
        if len(np.unique(key, axis=0)) != m:
            errs.append("duplicate triangles")
    if shippable:
        mats = set(np.unique(tile.materials).tolist()) | {p.material for p in tile.primitives}
        bad = sorted(x for x in mats if not M.is_shippable(x))
        if bad:
            errs.append(
                f"unshippable material ids {bad} (0 = unknown, unassigned and 200-255 are not allowed)"
            )
        if m > MAX_TRIANGLES:
            errs.append(f"{m} triangles > budget {MAX_TRIANGLES}")
    for k, p in enumerate(tile.primitives):
        if p.kind not in PRIM_KINDS:
            errs.append(f"primitive {k}: unknown kind {p.kind}")
        q = np.asarray(p.rotation, np.float64)
        if abs(np.linalg.norm(q) - 1) > 1e-4:
            errs.append(f"primitive {k}: rotation is not a unit quaternion")
    return errs


# --------------------------------------------------------------------------------------
# Writer
# --------------------------------------------------------------------------------------


def _pad8(n: int) -> int:
    return (n + 7) & ~7


def write_col(
    tile: ColTile,
    path: str | Path | None = None,
    *,
    encoding: str = "auto",
    optimize: bool = True,
    strict: bool = True,
    vertex_version: int = 0,
) -> bytes:
    """Serialise ``tile``. encoding: 'meshopt', 'raw' or 'auto' (meshopt if the encoder is available).

    ``optimize`` reorders vertices/triangles (meshopt reorderMesh) before meshopt encoding, permuting
    MATL with the triangles; the tile object is updated to the written order. ``strict`` enforces the
    shipping rules (materials, budgets); set it False only for negative test fixtures.
    """
    errs = check_tile(tile, shippable=strict)
    if errs:
        raise ColFormatError(f"{tile.id}: " + "; ".join(errs))
    if encoding == "auto":
        encoding = "meshopt" if meshopt.encoder_available() else "raw"
        if encoding == "raw":
            print("turku3d.col: meshopt encoder unavailable, writing RAW sections", file=sys.stderr)
    if encoding not in ("meshopt", "raw"):
        raise ValueError(encoding)

    pos, idx, mat = tile.positions, tile.indices, tile.materials
    posn_enc, indx_enc = ENC_RAW, ENC_RAW
    if encoding == "meshopt" and len(idx):
        pos, idx, order, posn_b, indx_b = meshopt.encode_mesh(
            pos, idx, optimize=optimize, vertex_version=vertex_version
        )
        mat = mat[order]
        posn_enc, indx_enc = ENC_MESHOPT_VERTEX, ENC_MESHOPT_INDEX
        tile.positions, tile.indices, tile.materials = pos, idx, mat
    else:
        posn_b, indx_b = pos.tobytes(), idx.tobytes()

    prim_b = b"".join(
        _PRIM.pack(p.kind, p.material, p.flags, *p.center, *p.rotation, *p.params)
        for p in tile.primitives
    )
    sections = [
        (b"POSN", posn_enc, posn_b, len(pos), 12),
        (b"INDX", indx_enc, indx_b, idx.size, 4),
        (b"MATL", ENC_RAW, mat.tobytes(), len(mat), 1),
        (b"PRIM", ENC_RAW, prim_b, len(tile.primitives), PRIM_BYTES),
    ]
    if tile.meta is not None:
        mb = json.dumps(tile.meta, separators=(",", ":"), sort_keys=True).encode()
        if len(mb) > 4096:
            raise ColFormatError("META JSON must stay under 4 kB")
        sections.append((b"META", ENC_RAW, mb, len(mb), 1))

    off = _pad8(HEADER_BYTES + SECTION_BYTES * len(sections))
    table, payload = [], bytearray()
    for tag, enc, data, count, stride in sections:
        table.append(_SECTION.pack(tag, enc, 0, off, len(data), count, stride))
        chunk = data + b"\0" * (_pad8(len(data)) - len(data))
        payload += chunk
        off += len(chunk)
    file_bytes = off

    lo, hi = tile.compute_bbox()
    flags = tile.computed_flags()
    header = _HEADER.pack(
        MAGIC,
        VERSION_MAJOR,
        VERSION_MINOR,
        HEADER_BYTES,
        flags,
        *tile.origin,
        tile.tile_i,
        tile.tile_j,
        *lo.astype(np.float32),
        *hi.astype(np.float32),
        len(pos),
        len(idx),
        len(tile.primitives),
        len(sections),
        M.MATERIAL_TABLE_VERSION,
        file_bytes,
        0,
    )
    head = header + b"".join(table)
    buf = bytearray(head + b"\0" * (_pad8(len(head)) - len(head)) + payload)
    assert len(buf) == file_bytes
    struct.pack_into("<I", buf, 92, zlib.crc32(buf) & 0xFFFFFFFF)
    if strict and file_bytes > MAX_FILE_BYTES:
        raise ColFormatError(f"{tile.id}: {file_bytes} bytes > budget {MAX_FILE_BYTES}")
    out = bytes(buf)
    tile.flags = flags
    tile.bbox_min, tile.bbox_max = tuple(lo), tuple(hi)
    if path is not None:
        Path(path).write_bytes(out)
    return out


# --------------------------------------------------------------------------------------
# Reader (spec §7)
# --------------------------------------------------------------------------------------


def crc_of(buf: bytes) -> int:
    b = bytearray(buf)
    b[92:96] = b"\0\0\0\0"
    return zlib.crc32(b) & 0xFFFFFFFF


def read_header(buf: bytes) -> dict:
    if len(buf) < HEADER_BYTES:
        raise ColFormatError("file shorter than the 96-byte header")
    v = _HEADER.unpack_from(buf, 0)
    keys = (
        "magic",
        "version_major",
        "version_minor",
        "header_bytes",
        "flags",
        "ox",
        "oy",
        "oz",
        "tile_i",
        "tile_j",
        "bx0",
        "by0",
        "bz0",
        "bx1",
        "by1",
        "bz1",
        "vertex_count",
        "triangle_count",
        "primitive_count",
        "section_count",
        "material_table_version",
        "file_bytes",
        "crc32",
    )
    return dict(zip(keys, v, strict=True))


def read_col(data: bytes | str | Path, *, verify_crc: bool = True) -> ColTile:
    buf = Path(data).read_bytes() if isinstance(data, (str, Path)) else bytes(data)
    h = read_header(buf)
    if h["magic"] != MAGIC:
        raise ColFormatError(f"bad magic {h['magic']!r}")
    if h["version_major"] != VERSION_MAJOR:
        raise ColFormatError(f"unsupported version_major {h['version_major']}")
    if h["file_bytes"] != len(buf):
        raise ColFormatError(f"file_bytes {h['file_bytes']} != buffer length {len(buf)}")
    if verify_crc and crc_of(buf) != h["crc32"]:
        raise ColFormatError("CRC-32 mismatch")
    nv, nt, npr = h["vertex_count"], h["triangle_count"], h["primitive_count"]
    got: dict[bytes, np.ndarray | bytes] = {}
    sections = []
    for k in range(h["section_count"]):
        o = h["header_bytes"] + k * SECTION_BYTES
        if o + SECTION_BYTES > len(buf):
            raise ColFormatError("section table runs past the end of the file")
        tag, enc, sflags, off, blen, count, stride = _SECTION.unpack_from(buf, o)
        sections.append(
            dict(
                tag=tag.decode("latin1"),
                encoding=enc,
                offset=off,
                byte_length=blen,
                element_count=count,
                element_stride=stride,
            )
        )
        if tag not in ALLOWED_ENCODINGS:
            continue  # unknown tag: skip (forward compatibility)
        if tag in got:
            raise ColFormatError(f"duplicate section {tag!r}")
        if enc not in ALLOWED_ENCODINGS[tag]:
            raise ColFormatError(f"section {tag!r}: encoding {enc} not allowed")
        if off % 8 or off + blen > len(buf):
            raise ColFormatError(f"section {tag!r}: bad offset/length")
        if stride != STRIDE[tag]:
            raise ColFormatError(f"section {tag!r}: stride {stride} != {STRIDE[tag]}")
        raw = buf[off : off + blen]
        if enc == ENC_RAW:
            if blen != count * stride:
                raise ColFormatError(f"section {tag!r}: byte_length != count*stride")
            got[tag] = raw
        elif enc == ENC_MESHOPT_VERTEX:
            got[tag] = meshopt.decode_vertex_buffer(raw, count, stride).tobytes()
        else:
            got[tag] = meshopt.decode_index_buffer(raw, count, stride).tobytes()
    for tag in (b"POSN", b"INDX", b"MATL", b"PRIM"):
        if tag not in got:
            raise ColFormatError(f"missing required section {tag!r}")
    pos = np.frombuffer(got[b"POSN"], np.float32).reshape(-1, 3).copy()
    idx = np.frombuffer(got[b"INDX"], np.uint32).reshape(-1, 3).copy()
    mat = np.frombuffer(got[b"MATL"], np.uint8).copy()
    if len(pos) != nv or len(idx) != nt or len(mat) != nt:
        raise ColFormatError("section element counts disagree with the header")
    if nt and int(idx.max()) >= nv:
        raise ColFormatError("index out of range")
    prims = []
    pb = got[b"PRIM"]
    if len(pb) != npr * PRIM_BYTES:
        raise ColFormatError("PRIM count disagrees with the header")
    for k in range(npr):
        v = _PRIM.unpack_from(pb, k * PRIM_BYTES)
        if v[0] not in PRIM_KINDS:
            print(f"turku3d.col: skipping primitive {k} of unknown kind {v[0]}", file=sys.stderr)
            continue
        prims.append(Primitive(v[0], v[1], v[3:6], v[6:10], v[10:14], v[2]))
    meta = json.loads(got[b"META"].decode()) if b"META" in got else None
    return ColTile(
        h["tile_i"],
        h["tile_j"],
        pos,
        idx,
        mat,
        prims,
        origin=(h["ox"], h["oy"], h["oz"]),
        aoi_border=bool(h["flags"] & FLAG_AOI_BORDER),
        fix_internal_edges=bool(h["flags"] & FLAG_FIX_INTERNAL_EDGES),
        meta=meta,
        flags=h["flags"],
        version_minor=h["version_minor"],
        material_table_version=h["material_table_version"],
        bbox_min=(h["bx0"], h["by0"], h["bz0"]),
        bbox_max=(h["bx1"], h["by1"], h["bz1"]),
        sections=sections,
    )


def canonical_triangles(tile: ColTile) -> list[tuple]:
    """Order- and rotation-independent triangle list: (corner bits rotated to min first, material)."""
    raw = tile.positions.view(np.uint32)[tile.indices].tolist()
    out = []
    for t, m in zip(raw, tile.materials.tolist(), strict=True):
        t = [tuple(v) for v in t]
        k = t.index(min(t))
        out.append((tuple(t[k:] + t[:k]), m))
    return sorted(out)
