export interface ColPrimitiveSpec { kind: number; material: number; center: number[]; rotation: number[]; params: number[] }
export function writeCol(tile: {
  i: number; j: number; positions: Float32Array; indices: Uint32Array; materials: Uint8Array;
  prims: ColPrimitiveSpec[]; flags: number; meta: unknown;
}): Buffer;
