"""meshoptimizer vertex/index codecs for .col (docs/specs/col-format.md §5).

* **Decoding** is pure Python + numpy, ported from the reference decoder that ships with the npm
  package (``meshopt_decoder_reference.js``): vertex codec v0 and v1, index codec v1 (``0xe1``).
  No native build needed; this is what ``read_col`` uses.
* **Encoding** uses the official encoder (the WASM build in npm ``meshoptimizer``) through a small
  Node bridge, ``meshopt_bridge.mjs``. The PyPI ``meshoptimizer`` bindings (0.2.30a0, sdist only)
  do not build on this Mac (the Command Line Tools linker cannot read the macOS 27 SDK ``.tbd``
  files), and the Node route uses the exact codec the game decodes with.
  The bridge looks for the npm package in ``$TURKU3D_MESHOPT_DIR`` or
  ``<repo>/tools/validator/node_modules/meshoptimizer`` (``make col-deps`` installs it).
  When Node or the package is missing, ``encoder_available()`` is False and ``write_col`` with
  ``encoding="auto"`` falls back to RAW (always valid per spec §5) and says so on stderr.
"""

from __future__ import annotations

import os
import atexit
import json
import select
import threading
import shutil
import subprocess
import tempfile
from pathlib import Path

import numpy as np

from turku3d import config as C

BRIDGE = Path(__file__).with_name("meshopt_bridge.mjs")


class MeshoptError(ValueError):
    pass


# --------------------------------------------------------------------------------------
# Decoder (port of meshopt_decoder_reference.js)
# --------------------------------------------------------------------------------------

_HEADER_MODES = ((0, 2, 4, 8), (0, 1, 2, 4), (1, 2, 4, 8))


def _dezig(v: np.ndarray, mask: int) -> np.ndarray:
    v = v.astype(np.int64)
    return ((v >> 1) ^ -(v & 1)) & mask


def decode_vertex_buffer(source: bytes | memoryview, count: int, stride: int) -> np.ndarray:
    """Decode a meshopt vertex buffer; returns uint8 array of count*stride bytes."""
    src = bytes(source)
    if not src or src[0] not in (0xA0, 0xA1):
        raise MeshoptError(f"vertex codec: bad header byte {src[:1].hex()}")
    if stride <= 0 or stride > 256 or stride % 4:
        raise MeshoptError(f"vertex codec: bad stride {stride}")
    version = src[0] & 0x0F
    max_block = min((0x2000 // stride) & ~0x0F, 0x100)
    tail_size = stride if version == 0 else stride + stride // 4
    tail_pad = max(tail_size, 32 if version == 0 else 24)
    if len(src) < 1 + tail_pad:
        raise MeshoptError("vertex codec: truncated")
    tail = len(src) - tail_size
    temp = np.frombuffer(src[tail:tail + stride], np.uint8).astype(np.int64).copy()
    channels = None if version == 0 else src[tail + stride:tail + tail_size]
    out = np.empty(count * stride, np.uint8)
    out2 = out.reshape(count, stride) if count else out.reshape(0, stride)
    off = 1
    end_data = len(src) - tail_pad
    for base in range(0, count, max_block):
        n = min(count - base, max_block)
        groups = (n + 15) >> 4
        header_bytes = (groups + 3) >> 2
        ctrl_off = off
        if version != 0:
            off += stride // 4
        deltas = np.zeros((stride, groups * 16), np.uint8)
        for byte in range(stride):
            cmode = 0 if version == 0 else (src[ctrl_off + (byte >> 2)] >> ((byte & 3) << 1)) & 3
            if cmode == 2:
                continue
            if cmode == 3:
                deltas[byte, :n] = np.frombuffer(src, np.uint8, n, off)
                off += n
                continue
            hdr = off
            off += header_bytes
            modes = _HEADER_MODES[0 if version == 0 else cmode + 1]
            row = deltas[byte]
            for g in range(groups):
                bits = modes[(src[hdr + (g >> 2)] >> ((g & 3) << 1)) & 3]
                d0 = g << 4
                if bits == 0:
                    continue
                if bits == 8:
                    row[d0:d0 + 16] = np.frombuffer(src, np.uint8, 16, off)
                    off += 16
                    continue
                sb = off
                if bits == 1:
                    off += 2
                    vals = [(src[sb + (m >> 3)] >> (m & 7)) & 1 for m in range(16)]
                    sentinel = 1
                elif bits == 2:
                    off += 4
                    vals = [(src[sb + (m >> 2)] >> (6 - ((m & 3) << 1))) & 3 for m in range(16)]
                    sentinel = 3
                else:  # 4
                    off += 8
                    vals = [(src[sb + (m >> 1)] >> (4 - ((m & 1) << 2))) & 15 for m in range(16)]
                    sentinel = 15
                for m in range(16):
                    if vals[m] == sentinel:
                        vals[m] = src[off]
                        off += 1
                row[d0:d0 + 16] = vals
        d = deltas[:, :n].astype(np.int64)  # (stride, n)
        blk = out2[base:base + n]
        for bg in range(0, stride, 4):
            ch = 0 if version == 0 else channels[bg >> 2]
            cm = ch & 3
            if cm == 0:
                for b in range(bg, bg + 4):
                    v = (temp[b] + np.cumsum(_dezig(d[b], 0xFF))) & 0xFF
                    blk[:, b] = v
                    temp[b] = v[-1]
            elif cm == 1:
                for b in (bg, bg + 2):
                    t16 = int(temp[b]) | (int(temp[b + 1]) << 8)
                    v = (t16 + np.cumsum(_dezig(d[b] | (d[b + 1] << 8), 0xFFFF))) & 0xFFFF
                    blk[:, b] = v & 0xFF
                    blk[:, b + 1] = v >> 8
                    temp[b], temp[b + 1] = v[-1] & 0xFF, v[-1] >> 8
            elif cm == 2:
                rot = ch >> 4
                delta = (d[bg] | (d[bg + 1] << 8) | (d[bg + 2] << 16) | (d[bg + 3] << 24)).astype(np.uint64)
                if rot:
                    delta = ((delta >> np.uint64(rot)) | (delta << np.uint64(32 - rot))) & np.uint64(0xFFFFFFFF)
                t32 = np.uint64(int(temp[bg]) | (int(temp[bg + 1]) << 8) | (int(temp[bg + 2]) << 16)
                                | (int(temp[bg + 3]) << 24))
                v = np.bitwise_xor.accumulate(np.concatenate([[t32], delta]))[1:]
                for k in range(4):
                    blk[:, bg + k] = (v >> np.uint64(8 * k)) & np.uint64(0xFF)
                    temp[bg + k] = int(v[-1] >> np.uint64(8 * k)) & 0xFF
            else:
                raise MeshoptError("vertex codec: invalid channel mode 3")
    if off != end_data:
        raise MeshoptError(f"vertex codec: consumed {off} bytes, expected {end_data}")
    return out


def decode_index_buffer(source: bytes | memoryview, count: int, stride: int = 4) -> np.ndarray:
    """Decode a meshopt triangle index buffer (codec v1); returns uint32 array of `count` indices."""
    src = bytes(source)
    if count % 3:
        raise MeshoptError("index codec: count must be a multiple of 3")
    if not src or src[0] != 0xE1:
        raise MeshoptError(f"index codec: unsupported header byte {src[:1].hex()} (need v1, 0xe1)")
    if len(src) < 1 + count // 3 + 16:
        raise MeshoptError("index codec: truncated")
    tri_count = count // 3
    code = 1
    data = code + tri_count
    codeaux = len(src) - 16
    dst = [0] * count
    edge = [0] * 32
    eoff = 0
    vf = [0] * 16
    voff = 0
    nxt = 0
    last = 0
    M = 0xFFFFFFFF

    def dz(v):
        return (v >> 1) ^ -(v & 1)

    def leb():
        nonlocal data
        n = 0
        sh = 0
        while True:
            b = src[data]
            data += 1
            n |= (b & 0x7F) << sh
            if b < 0x80:
                return n
            sh += 7

    o = 0
    for _ in range(tri_count):
        c0 = src[code]
        code += 1
        b0, b1 = c0 >> 4, c0 & 15
        if b0 < 15:
            a = edge[(eoff - 1 - (b0 << 1)) & 31]
            b = edge[(eoff - 2 - (b0 << 1)) & 31]
            if b1 == 0:
                c = nxt
                nxt += 1
                vf[voff] = c
                voff = (voff + 1) & 15
            elif b1 < 13:
                c = vf[(voff - 1 - b1) & 15]
            else:
                if b1 == 13:
                    last = (last - 1) & M
                elif b1 == 14:
                    last = (last + 1) & M
                else:
                    last = (last + dz(leb())) & M
                c = last
                vf[voff] = c
                voff = (voff + 1) & 15
            for v in (b, c, c, a):
                edge[eoff] = v
                eoff = (eoff + 1) & 31
        else:
            if b1 < 14:
                e = src[codeaux + b1]
                z, w = e >> 4, e & 15
                a = nxt
                nxt += 1
                if z == 0:
                    b = nxt
                    nxt += 1
                else:
                    b = vf[(voff - z) & 15]
                if w == 0:
                    c = nxt
                    nxt += 1
                else:
                    c = vf[(voff - w) & 15]
                push = [a] + ([b] if z == 0 else []) + ([c] if w == 0 else [])
            else:
                e = src[data]
                data += 1
                if e == 0:
                    nxt = 0
                z, w = e >> 4, e & 15
                if b1 == 14:
                    a = nxt
                    nxt += 1
                else:
                    last = (last + dz(leb())) & M
                    a = last
                if z == 0:
                    b = nxt
                    nxt += 1
                elif z == 15:
                    last = (last + dz(leb())) & M
                    b = last
                else:
                    b = vf[(voff - z) & 15]
                if w == 0:
                    c = nxt
                    nxt += 1
                elif w == 15:
                    last = (last + dz(leb())) & M
                    c = last
                else:
                    c = vf[(voff - w) & 15]
                push = [a] + ([b] if z in (0, 15) else []) + ([c] if w in (0, 15) else [])
            for v in push:
                vf[voff] = v
                voff = (voff + 1) & 15
            for v in (a, b, b, c, c, a):
                edge[eoff] = v
                eoff = (eoff + 1) & 31
        dst[o] = a
        dst[o + 1] = b
        dst[o + 2] = c
        o += 3
    if data > codeaux:
        raise MeshoptError("index codec: data overran the aux table")
    return np.asarray(dst, dtype=np.uint32)


# --------------------------------------------------------------------------------------
# Encoder (Node bridge to the official npm WASM encoder)
# --------------------------------------------------------------------------------------


def meshopt_dir() -> Path | None:
    env = os.environ.get("TURKU3D_MESHOPT_DIR")
    cands = [Path(env)] if env else []
    cands.append(C.REPO_ROOT / "tools" / "validator" / "node_modules" / "meshoptimizer")
    for p in cands:
        if (p / "meshopt_encoder.js").exists():
            return p
    return None


def encoder_available() -> bool:
    return shutil.which("node") is not None and meshopt_dir() is not None


class _Encoder:
    """One WASM instance per Python process, reused across tiles; requests are serialized."""

    def __init__(self, mdir: Path):
        self.process = subprocess.Popen(["node", str(BRIDGE), str(mdir)], stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.lock = threading.Lock()
        atexit.register(self.close)

    def close(self):
        p = self.process
        if p.stdin and not p.stdin.closed:
            p.stdin.close()
        try:
            p.wait(timeout=2)
        except subprocess.TimeoutExpired:
            p.kill()
            p.wait()
        for stream in (p.stdout, p.stderr):
            if stream:
                stream.close()

    def encode(self, **request):
        with self.lock:
            p = self.process
            try:
                p.stdin.write(json.dumps(request) + "\n")
                p.stdin.flush()
                if not select.select([p.stdout], [], [], 60)[0]:
                    self.close()
                    raise MeshoptError("meshopt bridge timed out")
                line = p.stdout.readline()
                if not line:
                    raise MeshoptError(f"meshopt bridge exited: {p.stderr.read().strip()}")
                result = json.loads(line)
                if not result.get("ok"):
                    raise MeshoptError(f"meshopt bridge failed: {result.get('error')}")
            except (BrokenPipeError, OSError) as exc:
                raise MeshoptError(f"meshopt bridge failed: {exc}") from exc


_encoders: dict[tuple[int, str], _Encoder] = {}


def _encoder(mdir: Path) -> _Encoder:
    key = (os.getpid(), str(mdir.resolve()))
    if key not in _encoders or _encoders[key].process.poll() is not None:
        _encoders[key] = _Encoder(mdir)
    return _encoders[key]


def encode_mesh(positions: np.ndarray, indices: np.ndarray, *, optimize: bool = True,
                vertex_version: int = 0):
    """Encode a mesh with the official meshopt encoder.

    positions: (N,3) float32, indices: (M,3) uint32.
    Returns (positions_out (N',3) f32, indices_out (M,3) u32, tri_order (M,) int, posn_bytes, indx_bytes).
    With ``optimize`` the mesh is reordered with ``reorderMesh`` (vertex cache + vertex fetch);
    ``tri_order[k]`` is the input triangle index of output triangle k (use it to permute MATL).
    Unreferenced vertices are dropped when optimizing.
    """
    mdir = meshopt_dir()
    if shutil.which("node") is None or mdir is None:
        raise MeshoptError("meshopt encoder unavailable: need `node` and npm meshoptimizer "
                           "(run `make col-deps` or set TURKU3D_MESHOPT_DIR)")
    pos = np.ascontiguousarray(positions, np.float32).reshape(-1, 3)
    idx = np.ascontiguousarray(indices, np.uint32).reshape(-1, 3)
    with tempfile.TemporaryDirectory(prefix="colenc") as td:
        t = Path(td)
        (t / "pos.bin").write_bytes(pos.tobytes())
        (t / "idx.bin").write_bytes(idx.tobytes())
        _encoder(mdir).encode(dir=td, vc=len(pos), ic=idx.size, optimize=optimize, version=vertex_version)
        pos_o = np.frombuffer((t / "pos_out.bin").read_bytes(), np.float32).reshape(-1, 3).copy()
        idx_o = np.frombuffer((t / "idx_out.bin").read_bytes(), np.uint32).reshape(-1, 3).copy()
        posn = (t / "posn.enc").read_bytes()
        indx = (t / "indx.enc").read_bytes()
    if optimize:
        tri_order = _match_triangles(pos, idx, pos_o, idx_o)
    else:
        tri_order = np.arange(len(idx))
    return pos_o, idx_o, tri_order, posn, indx


def _match_triangles(pos, idx, pos_o, idx_o) -> np.ndarray:
    """Recover the triangle permutation after reorderMesh (keys = sorted original vertex ids)."""
    # Match on the position bits of the three corners, sorted per triangle: independent of the
    # vertex remap and of index rotation.
    def keys(p, ix):
        raw = p.view(np.uint32)[ix]  # (M,3,3)
        return [tuple(sorted(map(tuple, t))) for t in raw.tolist()]
    kin = keys(pos, idx)
    lut: dict[tuple, int] = {}
    for t, k in enumerate(kin):
        if k in lut:
            raise MeshoptError(f"duplicate triangles {lut[k]} and {t} (spec §3 forbids them)")
        lut[k] = t
    try:
        return np.array([lut[k] for k in keys(pos_o, idx_o)], dtype=np.int64)
    except KeyError as e:
        raise MeshoptError("reorder produced a triangle not in the input") from e
