// COPC (Cloud-Optimized Point Cloud: LAZ 1.4 + an octree hierarchy, https://copc.io) reading without the network:
// the header, the hierarchy pages and the decompressed point records. The worker (copc.worker.ts) fetches byte
// ranges and runs laz-perf; everything here is pure and unit-tested on a synthetic fixture.
import { E0, N0 } from '../../core/origin';

/** Bytes that hold the LAS 1.4 header plus the COPC info VLR, which the spec requires first. */
export const HEADER_BYTES = 375 + 54 + 160;

export interface CopcHeader {
  pointFormat: number;
  recordLength: number;
  pointCount: number;
  scale: [number, number, number];
  offset: [number, number, number];
  /** Survey bounds (EPSG:3877 E, N, N2000 H). */
  min: [number, number, number];
  max: [number, number, number];
  /** Octree cube: centre and half size (survey frame), and the root node's point spacing. */
  cube: { centre: [number, number, number]; half: number };
  spacing: number;
  rootPage: { offset: number; size: number };
}

export interface CopcNode {
  /** "D-X-Y-Z" octree key. */
  key: string;
  depth: number; x: number; y: number; z: number;
  offset: number;
  byteSize: number;
  pointCount: number;
}

export interface HierarchyPage { nodes: CopcNode[]; pages: { key: string; offset: number; size: number }[] }

const u64 = (view: DataView, at: number) => Number(view.getBigUint64(at, true));

export function parseHeader(buffer: ArrayBuffer): CopcHeader {
  const view = new DataView(buffer);
  if (buffer.byteLength < HEADER_BYTES) throw new Error('COPC header truncated');
  if (String.fromCharCode(...new Uint8Array(buffer, 0, 4)) !== 'LASF') throw new Error('not a LAS file');
  if (view.getUint8(24) !== 1 || view.getUint8(25) !== 4) throw new Error('COPC requires LAS 1.4');
  const userId = new TextDecoder().decode(new Uint8Array(buffer, 375 + 2, 16)).replace(/\0+$/, '');
  if (userId !== 'copc' || view.getUint16(375 + 18, true) !== 1) throw new Error('first VLR is not COPC info');
  // Bits 6-7 of the format byte flag LAZ compression.
  const pointFormat = view.getUint8(104) & 0x3f;
  const f64 = (at: number) => view.getFloat64(at, true);
  const info = 375 + 54;
  return {
    pointFormat,
    recordLength: view.getUint16(105, true),
    pointCount: u64(view, 247),
    scale: [f64(131), f64(139), f64(147)],
    offset: [f64(155), f64(163), f64(171)],
    min: [f64(187), f64(203), f64(219)],
    max: [f64(179), f64(195), f64(211)],
    cube: { centre: [f64(info), f64(info + 8), f64(info + 16)], half: f64(info + 24) },
    spacing: f64(info + 32),
    rootPage: { offset: u64(view, info + 40), size: u64(view, info + 48) },
  };
}

/** One hierarchy page: 32-byte entries; pointCount -1 refers to a child page, 0 to an empty node. */
export function parseHierarchyPage(buffer: ArrayBuffer): HierarchyPage {
  const view = new DataView(buffer);
  const nodes: CopcNode[] = [], pages: HierarchyPage['pages'] = [];
  for (let at = 0; at + 32 <= buffer.byteLength; at += 32) {
    const depth = view.getInt32(at, true), x = view.getInt32(at + 4, true), y = view.getInt32(at + 8, true), z = view.getInt32(at + 12, true);
    const offset = u64(view, at + 16), byteSize = view.getInt32(at + 24, true), pointCount = view.getInt32(at + 28, true);
    const key = `${depth}-${x}-${y}-${z}`;
    if (pointCount === -1) pages.push({ key, offset, size: byteSize });
    else if (pointCount > 0) nodes.push({ key, depth, x, y, z, offset, byteSize, pointCount });
  }
  return { nodes, pages };
}

/** Survey-frame bounds of an octree node: [minE, minN, minH, maxE, maxN, maxH]. */
export function nodeBounds(header: Pick<CopcHeader, 'cube'>, node: Pick<CopcNode, 'depth' | 'x' | 'y' | 'z'>): number[] {
  const { centre: [cx, cy, cz], half } = header.cube;
  const size = (2 * half) / 2 ** node.depth;
  const e = cx - half + node.x * size, n = cy - half + node.y * size, h = cz - half + node.z * size;
  return [e, n, h, e + size, n + size, h + size];
}

export function childKeys(node: Pick<CopcNode, 'depth' | 'x' | 'y' | 'z'>): string[] {
  const keys: string[] = [];
  for (let i = 0; i < 8; i++) keys.push(`${node.depth + 1}-${node.x * 2 + (i & 1)}-${node.y * 2 + ((i >> 1) & 1)}-${node.z * 2 + ((i >> 2) & 1)}`);
  return keys;
}

/** Survey (E, N, H: EPSG:3877 + N2000) to the engine frame: x = E - E0, y = H, z = -(N - N0) (core/origin.ts). */
export function surveyToEngine(e: number, n: number, h: number): [number, number, number] {
  return [e - E0, h, -(n - N0)];
}

export interface DecodedPoints {
  count: number;
  /** Engine-frame positions relative to `anchor`, xyz per point. */
  positions: Float32Array;
  /** Per point: r, g, b (8-bit sRGB) and the LAS classification. */
  colors: Uint8Array;
  /** Per point: intensity (top 8 bits), return number, number of returns, flags (bit 0 synthetic, 1 key point, 2 withheld, 3 overlap). */
  extra: Uint8Array;
  /** Engine-frame anchor the positions are relative to (the node's centre). */
  anchor: [number, number, number];
  /** Engine-y (N2000) range of the points. */
  minY: number; maxY: number;
}

/** Point data record formats 6, 7 and 8 (COPC requires one of them); 7 and 8 carry RGB at byte 30. */
export function decodeRecords(records: Uint8Array, count: number, header: Pick<CopcHeader, 'pointFormat' | 'recordLength' | 'scale' | 'offset'>,
  anchor: [number, number, number]): DecodedPoints {
  const { pointFormat, recordLength: stride, scale: [sx, sy, sz], offset: [ox, oy, oz] } = header;
  if (pointFormat < 6 || pointFormat > 8) throw new Error(`point format ${pointFormat} is not COPC`);
  const rgb = pointFormat >= 7;
  const view = new DataView(records.buffer, records.byteOffset, records.byteLength);
  const positions = new Float32Array(count * 3), colors = new Uint8Array(count * 4), extra = new Uint8Array(count * 4);
  const [ax, ay, az] = anchor;
  let minY = Infinity, maxY = -Infinity;
  for (let i = 0, at = 0; i < count; i++, at += stride) {
    const e = view.getInt32(at, true) * sx + ox, n = view.getInt32(at + 4, true) * sy + oy, h = view.getInt32(at + 8, true) * sz + oz;
    positions[i * 3] = e - E0 - ax;
    positions[i * 3 + 1] = h - ay;
    positions[i * 3 + 2] = -(n - N0) - az;
    if (h < minY) minY = h;
    if (h > maxY) maxY = h;
    const returns = view.getUint8(at + 14), flags = view.getUint8(at + 15);
    if (rgb) {
      // LAS colour is 16-bit; the survey stores 8-bit values times 256.
      colors[i * 4] = view.getUint16(at + 30, true) >> 8;
      colors[i * 4 + 1] = view.getUint16(at + 32, true) >> 8;
      colors[i * 4 + 2] = view.getUint16(at + 34, true) >> 8;
    } else colors.fill(160, i * 4, i * 4 + 3);
    colors[i * 4 + 3] = view.getUint8(at + 16);
    extra[i * 4] = view.getUint16(at + 12, true) >> 8;
    extra[i * 4 + 1] = returns & 0x0f;
    extra[i * 4 + 2] = returns >> 4;
    extra[i * 4 + 3] = flags & 0x0f;
  }
  return { count, positions, colors, extra, anchor, minY: count ? minY : 0, maxY: count ? maxY : 0 };
}

/** The node's engine-frame centre: the anchor its positions are stored relative to (float32 keeps millimetres). */
export function nodeAnchor(header: Pick<CopcHeader, 'cube'>, node: Pick<CopcNode, 'depth' | 'x' | 'y' | 'z'>): [number, number, number] {
  const [e0, n0, h0, e1, n1, h1] = nodeBounds(header, node);
  return surveyToEngine((e0 + e1) / 2, (n0 + n1) / 2, (h0 + h1) / 2);
}

/** Minimal laz-perf surface used for one COPC chunk (laz-perf's ChunkDecoder). */
export interface LazPerfModule {
  HEAPU8: Uint8Array;
  _malloc(bytes: number): number;
  _free(pointer: number): void;
  ChunkDecoder: new () => { open(format: number, length: number, pointer: number): void; getPoint(pointer: number): void; delete(): void };
}

/** Decompress one COPC chunk (a node's LAZ bytes) into raw point records. */
export function decompressChunk(laz: LazPerfModule, compressed: Uint8Array, count: number, format: number, length: number): Uint8Array {
  const input = laz._malloc(compressed.byteLength), point = laz._malloc(length);
  const out = new Uint8Array(count * length);
  const decoder = new laz.ChunkDecoder();
  try {
    laz.HEAPU8.set(compressed, input);
    decoder.open(format, length, input);
    for (let i = 0; i < count; i++) {
      decoder.getPoint(point);
      // HEAPU8 is re-read every point: wasm memory growth replaces the buffer.
      out.set(laz.HEAPU8.subarray(point, point + length), i * length);
    }
  } finally {
    decoder.delete();
    laz._free(input); laz._free(point);
  }
  return out;
}
