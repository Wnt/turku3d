import { BufferAttribute, type TypedArray } from 'three/src/core/BufferAttribute.js';

export interface ProgramField { array: TypedArray; itemSize: number; normalized: boolean }
/** Pure packing kernel shared by the worker and the standalone synchronous path. */
export function packProgram(fields: ProgramField[]): Float32Array {
  const stride = fields.reduce((sum, field) => sum + field.itemSize, 0);
  const count = fields[0].array.length / fields[0].itemSize;
  const packed = new Float32Array(count * stride);
  let offset = 0;
  for (const field of fields) {
    const attribute = new BufferAttribute(field.array, field.itemSize, field.normalized);
    for (let vertex = 0; vertex < count; vertex++) for (let component = 0; component < field.itemSize; component++)
      packed[vertex * stride + offset + component] = attribute.getComponent(vertex, component);
    offset += field.itemSize;
  }
  return packed;
}
