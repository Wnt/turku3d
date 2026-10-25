// .col v1.0 decoding for the physics worker. The reader itself is the shared one in src/shared/col
// (col-format workstream); this module only adapts its ColTile to what physics/world.ts needs and supplies
// the meshopt decoder.
import { MeshoptDecoder } from 'meshoptimizer';
import { readCol as readShared, COL_FLAG, crc32 } from '../shared/col/colFormat';

export interface ColPrimitive { kind: number; material: number; center: [number, number, number]; rotation: [number, number, number, number]; params: [number, number, number, number] }
export interface ColTile {
  id: string;
  origin: [number, number, number];
  tileI: number; tileJ: number;
  flags: number;
  positions: Float32Array;
  indices: Uint32Array;
  materials: Uint8Array;
  prims: ColPrimitive[];
  meta: unknown;
}
export const COL_FLAG_FIX_INTERNAL_EDGES = COL_FLAG.FIX_INTERNAL_EDGES;
export { crc32 };
export const colReaderName = 'src/shared/col/colFormat.ts#readCol';

export async function readCol(buffer: ArrayBuffer, opts: { checkCrc?: boolean } = {}): Promise<ColTile> {
  await MeshoptDecoder.ready;
  const t = readShared(buffer, { decoder: MeshoptDecoder, verifyCrc: opts.checkCrc ?? true, onWarning: (w) => console.warn(`[col] ${w}`) });
  return {
    id: t.id, origin: t.header.origin, tileI: t.header.tileI, tileJ: t.header.tileJ, flags: t.header.flags,
    positions: t.positions, indices: t.indices, materials: t.materials,
    prims: t.primitives.map((p) => ({ kind: p.kind, material: p.material, center: p.center, rotation: p.rotation, params: p.params })),
    meta: t.meta,
  };
}
