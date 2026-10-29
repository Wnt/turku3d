// Bound vertex views before meshopt encoding; preserve every triangle and attribute.
// VEC4 float attributes stay below 2 MB, leaving room for lossless codec overhead.
export const MAX_BATCH_INDICES = 120_000;

export function primitiveChunks(primitive, compactPrimitive, limit = MAX_BATCH_INDICES) {
  const indices = primitive.getIndices();
  const count = indices?.getCount() ?? primitive.getAttribute('POSITION').getCount();
  if (count <= limit) return [primitive];
  const step = Math.floor(limit / 3) * 3;
  if (step < 3) throw new Error('A primitive chunk must contain a triangle');
  const source = indices?.getArray() ?? Uint32Array.from({ length:count }, (_, i) => i);
  const chunks = [];
  for (let start = 0; start < count; start += step) {
    const part = primitive.clone();
    // The temporary index accessor is owned by this part. compactPrimitive
    // copies all referenced vertex attributes, including the facade program.
    const index = (indices ?? primitive.getAttribute('POSITION')).clone()
      .setType('SCALAR').setArray(source.slice(start, start + step));
    part.setIndices(index);
    compactPrimitive(part);
    chunks.push(part);
  }
  return chunks;
}
