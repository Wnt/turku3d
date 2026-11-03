/** Typed-array form of a geometry's attributes: transferable to and from a worker. */
export interface WeldInput { names: string[]; itemSizes: number[]; arrays: Float32Array[]; index: Uint16Array | Uint32Array | null }
export interface WeldOutput { arrays: Float32Array[]; index: Uint16Array | Uint32Array }

/** r186 BufferGeometryUtils.mergeVertices on plain Float32 arrays: the same per-vertex hash, first-seen
 * order and index width, so the welded geometry is identical, without per-element accessor calls. */
export function weldArrays({ names, itemSizes, arrays, index }: WeldInput, tolerance = 1e-4): WeldOutput {
  tolerance = Math.max(tolerance, Number.EPSILON);
  const count = index ? index.length : arrays[names.indexOf('position')].length / itemSizes[names.indexOf('position')];
  const halfTolerance = tolerance * 0.5, exponent = Math.log10(1 / tolerance);
  const hashMultiplier = Math.pow(10, exponent), hashAdditive = halfTolerance * hashMultiplier;
  const sources = arrays.map((array, i) => ({ array, size: itemSizes[i], out: new Float32Array(array.length) }));
  const hashToIndex = new Map<string, number>(), indices = new Uint32Array(count);
  let next = 0;
  for (let i = 0; i < count; i++) {
    const vertex = index ? index[i] : i;
    let hash = '';
    for (const { array, size } of sources)
      for (let k = 0, base = vertex * size; k < size; k++) hash += `${Math.trunc(array[base + k] * hashMultiplier + hashAdditive)},`;
    const found = hashToIndex.get(hash);
    if (found !== undefined) { indices[i] = found; continue; }
    for (const { array, size, out } of sources) out.set(array.subarray(vertex * size, vertex * size + size), next * size);
    hashToIndex.set(hash, next); indices[i] = next++;
  }
  // BufferGeometry.setIndex(Array) chooses Uint32 once any index reaches 65535.
  return { arrays: sources.map(({ out, size }) => out.slice(0, next * size)),
    index: next - 1 >= 65535 ? indices : Uint16Array.from(indices) };
}

