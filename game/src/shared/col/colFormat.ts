// .col collision tile reader, v1.0 (docs/specs/col-format.md). Shared by the game physics worker
// and tools/validator. Pure TypeScript with erasable syntax only, so Node 24 runs it directly.
//
//   import { MeshoptDecoder } from 'meshoptimizer';
//   await MeshoptDecoder.ready;
//   const tile = readCol(arrayBuffer, { decoder: MeshoptDecoder });
//
// The meshopt decoder is injected (only needed for encodings 1/2) so this module has no package
// dependency and works with whatever copy of `meshoptimizer` the host bundles.

import { isShippableMaterial, materialClass } from './materials.ts';
import type { CollisionClass } from './materials.ts';

const COL_MAGIC = 0x4c4f4354; // "TCOL" little-endian
const COL_VERSION_MAJOR = 1;
const COL_HEADER_BYTES = 96;
const COL_SECTION_BYTES = 24;
const COL_PRIM_BYTES = 48;

export const COL_FLAG = { FIX_INTERNAL_EDGES: 1, AOI_BORDER: 2 } as const;
const COL_ENC = { RAW: 0, MESHOPT_VERTEX: 1, MESHOPT_INDEX: 2 } as const;
export const PRIM_KIND = { BOX: 1, CAPSULE: 2, CYLINDER: 3 } as const;

const ALLOWED: Record<string, readonly number[]> = {
  POSN: [0, 1],
  INDX: [0, 2],
  MATL: [0],
  PRIM: [0],
  META: [0],
};
const STRIDE: Record<string, number> = { POSN: 12, INDX: 4, MATL: 1, PRIM: 48, META: 1 };

/** Minimal interface of npm `meshoptimizer`'s MeshoptDecoder (after `await MeshoptDecoder.ready`). */
interface MeshoptDecoderLike {
  decodeVertexBuffer(target: Uint8Array, count: number, size: number, source: Uint8Array, filter?: string): void;
  decodeIndexBuffer(target: Uint8Array, count: number, size: number, source: Uint8Array): void;
}

export interface ColHeader {
  versionMajor: number;
  versionMinor: number;
  headerBytes: number;
  flags: number;
  origin: [number, number, number];
  tileI: number;
  tileJ: number;
  bboxMin: [number, number, number];
  bboxMax: [number, number, number];
  vertexCount: number;
  triangleCount: number;
  primitiveCount: number;
  sectionCount: number;
  materialTableVersion: number;
  fileBytes: number;
  crc32: number;
}

export interface ColSection {
  tag: string;
  encoding: number;
  offset: number;
  byteLength: number;
  elementCount: number;
  elementStride: number;
}

export interface ColPrimitive {
  kind: number;
  material: number;
  flags: number;
  center: [number, number, number];
  /** Unit quaternion (x, y, z, w), engine axes. */
  rotation: [number, number, number, number];
  /** box: half extents (x,y,z,0); capsule/cylinder: (half_height, radius, 0, 0), local Y axis. */
  params: [number, number, number, number];
}

export interface ColTile {
  header: ColHeader;
  id: string;
  sections: ColSection[];
  /** Tile-local float32 xyz; world-local = header.origin + position. */
  positions: Float32Array;
  /** 3 indices per triangle. */
  indices: Uint32Array;
  /** Material id per triangle (materials v1). */
  materials: Uint8Array;
  primitives: ColPrimitive[];
  meta: Record<string, unknown> | null;
  /** Primitive records skipped because their kind is unknown. */
  skippedPrimitives: number;
}

interface ReadColOptions {
  decoder?: MeshoptDecoderLike;
  /** Verify the CRC-32 (spec: optional in release, required in tests). Default true. */
  verifyCrc?: boolean;
  onWarning?: (msg: string) => void;
}

class ColFormatError extends Error {}

let crcTable: Uint32Array | null = null;
export function crc32(bytes: Uint8Array, skipFrom = -1, skipTo = -1): number {
  if (!crcTable) {
    crcTable = new Uint32Array(256);
    for (let n = 0; n < 256; n++) {
      let c = n;
      for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
      crcTable[n] = c >>> 0;
    }
  }
  const t = crcTable;
  let c = 0xffffffff;
  for (let i = 0; i < bytes.length; i++) {
    const b = i >= skipFrom && i < skipTo ? 0 : bytes[i];
    c = t[(c ^ b) & 0xff] ^ (c >>> 8);
  }
  return (c ^ 0xffffffff) >>> 0;
}

function u8view(buf: ArrayBuffer | ArrayBufferView): Uint8Array {
  return buf instanceof ArrayBuffer ? new Uint8Array(buf) : new Uint8Array(buf.buffer, buf.byteOffset, buf.byteLength);
}

function readColHeader(buf: ArrayBuffer | ArrayBufferView): ColHeader {
  const u8 = u8view(buf);
  if (u8.byteLength < COL_HEADER_BYTES) throw new ColFormatError('file shorter than the 96-byte header');
  const dv = new DataView(u8.buffer, u8.byteOffset, u8.byteLength);
  if (dv.getUint32(0, true) !== COL_MAGIC) throw new ColFormatError('bad magic (expected "TCOL")');
  return {
    versionMajor: dv.getUint16(4, true),
    versionMinor: dv.getUint16(6, true),
    headerBytes: dv.getUint32(8, true),
    flags: dv.getUint32(12, true),
    origin: [dv.getFloat64(16, true), dv.getFloat64(24, true), dv.getFloat64(32, true)],
    tileI: dv.getInt32(40, true),
    tileJ: dv.getInt32(44, true),
    bboxMin: [dv.getFloat32(48, true), dv.getFloat32(52, true), dv.getFloat32(56, true)],
    bboxMax: [dv.getFloat32(60, true), dv.getFloat32(64, true), dv.getFloat32(68, true)],
    vertexCount: dv.getUint32(72, true),
    triangleCount: dv.getUint32(76, true),
    primitiveCount: dv.getUint32(80, true),
    sectionCount: dv.getUint16(84, true),
    materialTableVersion: dv.getUint16(86, true),
    fileBytes: dv.getUint32(88, true),
    crc32: dv.getUint32(92, true),
  };
}

/** Read and decode a whole .col file (spec §7). Throws ColFormatError on any violation. */
export function readCol(buf: ArrayBuffer | ArrayBufferView, opts: ReadColOptions = {}): ColTile {
  const u8 = u8view(buf);
  const h = readColHeader(u8);
  if (h.versionMajor !== COL_VERSION_MAJOR) throw new ColFormatError(`unsupported version_major ${h.versionMajor}`);
  if (h.fileBytes !== u8.byteLength) throw new ColFormatError(`file_bytes ${h.fileBytes} != buffer length ${u8.byteLength}`);
  if (opts.verifyCrc !== false && crc32(u8, 92, 96) !== h.crc32) throw new ColFormatError('CRC-32 mismatch');
  const dv = new DataView(u8.buffer, u8.byteOffset, u8.byteLength);
  const sections: ColSection[] = [];
  const got = new Map<string, Uint8Array>();
  for (let k = 0; k < h.sectionCount; k++) {
    const o = h.headerBytes + k * COL_SECTION_BYTES;
    if (o + COL_SECTION_BYTES > u8.byteLength) throw new ColFormatError('section table runs past the end of the file');
    const tag = String.fromCharCode(u8[o], u8[o + 1], u8[o + 2], u8[o + 3]);
    const s: ColSection = {
      tag,
      encoding: dv.getUint16(o + 4, true),
      offset: dv.getUint32(o + 8, true),
      byteLength: dv.getUint32(o + 12, true),
      elementCount: dv.getUint32(o + 16, true),
      elementStride: dv.getUint32(o + 20, true),
    };
    sections.push(s);
    const allowed = ALLOWED[tag];
    if (!allowed) continue; // unknown tag: skip (forward compatibility)
    if (got.has(tag)) throw new ColFormatError(`duplicate section ${tag}`);
    if (!allowed.includes(s.encoding)) throw new ColFormatError(`section ${tag}: encoding ${s.encoding} not allowed`);
    if (s.offset % 8 !== 0 || s.offset + s.byteLength > u8.byteLength) throw new ColFormatError(`section ${tag}: bad offset/length`);
    if (s.elementStride !== STRIDE[tag]) throw new ColFormatError(`section ${tag}: stride ${s.elementStride}`);
    const src = u8.subarray(s.offset, s.offset + s.byteLength);
    const out = new Uint8Array(s.elementCount * s.elementStride);
    if (s.encoding === COL_ENC.RAW) {
      if (s.byteLength !== out.byteLength) throw new ColFormatError(`section ${tag}: byte_length != count*stride`);
      out.set(src);
    } else {
      const d = opts.decoder;
      if (!d) throw new ColFormatError(`section ${tag} is meshopt-encoded but no decoder was given`);
      if (s.encoding === COL_ENC.MESHOPT_VERTEX) d.decodeVertexBuffer(out, s.elementCount, s.elementStride, src);
      else d.decodeIndexBuffer(out, s.elementCount, s.elementStride, src);
    }
    got.set(tag, out);
  }
  for (const t of ['POSN', 'INDX', 'MATL', 'PRIM']) if (!got.has(t)) throw new ColFormatError(`missing required section ${t}`);
  const posB = got.get('POSN')!, idxB = got.get('INDX')!, matB = got.get('MATL')!, primB = got.get('PRIM')!;
  // Decoded buffers are fresh, so they are 4-byte aligned at offset 0.
  const positions = new Float32Array(posB.buffer, 0, posB.byteLength / 4);
  const indices = new Uint32Array(idxB.buffer, 0, idxB.byteLength / 4);
  const materials = matB;
  if (positions.length !== h.vertexCount * 3 || indices.length !== h.triangleCount * 3 || materials.length !== h.triangleCount)
    throw new ColFormatError('section element counts disagree with the header');
  for (let i = 0; i < indices.length; i++) if (indices[i] >= h.vertexCount) throw new ColFormatError('index out of range');
  if (primB.byteLength !== h.primitiveCount * COL_PRIM_BYTES) throw new ColFormatError('PRIM count disagrees with the header');
  const pv = new DataView(primB.buffer, 0, primB.byteLength);
  const primitives: ColPrimitive[] = [];
  let skipped = 0;
  for (let k = 0; k < h.primitiveCount; k++) {
    const o = k * COL_PRIM_BYTES;
    const kind = pv.getUint8(o);
    if (kind < 1 || kind > 3) {
      skipped++;
      opts.onWarning?.(`primitive ${k}: unknown kind ${kind}, skipped`);
      continue;
    }
    const f = (i: number) => pv.getFloat32(o + i, true);
    primitives.push({
      kind,
      material: pv.getUint8(o + 1),
      flags: pv.getUint16(o + 2, true),
      center: [f(4), f(8), f(12)],
      rotation: [f(16), f(20), f(24), f(28)],
      params: [f(32), f(36), f(40), f(44)],
    });
  }
  let meta: Record<string, unknown> | null = null;
  const mb = got.get('META');
  if (mb) {
    try {
      meta = JSON.parse(new TextDecoder().decode(mb));
    } catch {
      opts.onWarning?.('META is not valid JSON; ignored');
    }
  }
  return { header: h, id: `c_${h.tileI}_${h.tileJ}`, sections, positions, indices, materials, primitives, meta, skippedPrimitives: skipped };
}

interface ClassMesh {
  cls: CollisionClass;
  /** Compacted tile-local vertices used by this class. */
  positions: Float32Array;
  indices: Uint32Array;
  /** Material per triangle of this sub-mesh. */
  materials: Uint8Array;
  /** Index of each sub-mesh triangle in the tile's triangle list. */
  triangleIds: Uint32Array;
}

/** Split the triangle soup by material collision class (spec §7: one trimesh per class present). */
export function splitByClass(tile: ColTile): ClassMesh[] {
  const out: ClassMesh[] = [];
  const nt = tile.header.triangleCount;
  for (const cls of ['solid', 'query', 'sensor'] as const) {
    const tris: number[] = [];
    for (let t = 0; t < nt; t++) if (materialClass(tile.materials[t]) === cls) tris.push(t);
    if (!tris.length) continue;
    const remap = new Int32Array(tile.header.vertexCount).fill(-1);
    const idx = new Uint32Array(tris.length * 3);
    const mats = new Uint8Array(tris.length);
    let nv = 0;
    tris.forEach((t, k) => {
      mats[k] = tile.materials[t];
      for (let c = 0; c < 3; c++) {
        const v = tile.indices[t * 3 + c];
        if (remap[v] < 0) remap[v] = nv++;
        idx[k * 3 + c] = remap[v];
      }
    });
    const pos = new Float32Array(nv * 3);
    for (let v = 0; v < remap.length; v++) {
      const r = remap[v];
      if (r >= 0) pos.set(tile.positions.subarray(v * 3, v * 3 + 3), r * 3);
    }
    out.push({ cls, positions: pos, indices: idx, materials: mats, triangleIds: Uint32Array.from(tris) });
  }
  return out;
}

/** Shipping-rule check of decoded content (materials, budgets). Returns violations. */
export function checkShippable(tile: ColTile): string[] {
  const errs: string[] = [];
  const bad = new Set<number>();
  for (const m of tile.materials) if (!isShippableMaterial(m)) bad.add(m);
  for (const p of tile.primitives) if (!isShippableMaterial(p.material)) bad.add(p.material);
  if (bad.size) errs.push(`unshippable material ids ${[...bad].sort((a, b) => a - b).join(',')}`);
  if (tile.header.triangleCount > 150_000) errs.push(`${tile.header.triangleCount} triangles > 150000`);
  if (tile.header.fileBytes > 1_000_000) errs.push(`${tile.header.fileBytes} bytes > 1 MB`);
  if (tile.header.materialTableVersion !== 1) errs.push(`material_table_version ${tile.header.materialTableVersion} != 1`);
  return errs;
}
