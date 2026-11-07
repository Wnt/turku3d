import * as THREE from 'three/webgpu';
import { mergeVertices } from 'three/addons/utils/BufferGeometryUtils.js';
import { drain } from '../world/shadowChunks';

/** BufferGeometryUtils.mergeVertices with the same result (vertex order, kept values, index), without building a
 *  string key per vertex: those keys were half of a fleet car's preparation (about 100 ms for a 59k-triangle LOD0 on
 *  the M1 Max, in the meshopt decoder's message task). Normalized attributes hash and copy through the same
 *  denormalize/normalize round trip as getX/setX. Interleaved or morphed geometry goes through mergeVertices. */
export function weldVertices(geometry: THREE.BufferGeometry, tolerance = 1e-4): THREE.BufferGeometry {
  return drain(weldVertexSteps(geometry, tolerance));
}
/** weldVertices in slices of `slice` vertices, for main-thread streaming. */
export function* weldVertexSteps(geometry: THREE.BufferGeometry, tolerance = 1e-4, slice = 8192): Generator<void, THREE.BufferGeometry> {
  const names = Object.keys(geometry.attributes);
  const attributes = names.map(name => geometry.attributes[name]);
  if (Object.keys(geometry.morphAttributes).length || !attributes.length
    || attributes.some(a => !(a as THREE.BufferAttribute).isBufferAttribute || a.itemSize > 4)) return mergeVertices(geometry, tolerance);
  tolerance = Math.max(tolerance, Number.EPSILON);
  const halfTolerance = tolerance * 0.5, exponent = Math.log10(1 / tolerance);
  const hashMultiplier = Math.pow(10, exponent), hashAdditive = halfTolerance * hashMultiplier;
  const index = geometry.getIndex(), count = index ? index.count : geometry.getAttribute('position').count;
  const arrays = attributes.map(a => a.array as THREE.TypedArray), sizes = attributes.map(a => a.itemSize);
  const normalized = attributes.map(a => a.normalized);
  // MathUtils types only the integer arrays a normalized attribute can hold.
  type Scaled = Parameters<typeof THREE.MathUtils.denormalize>[1];
  const denormalize = (value: number, array: THREE.TypedArray) => THREE.MathUtils.denormalize(value, array as Scaled);
  const normalize = (value: number, array: THREE.TypedArray) => THREE.MathUtils.normalize(value, array as Scaled);
  const width = sizes.reduce((sum, n) => sum + n, 0);
  // The integer tuple mergeVertices joins into its string key, per first-seen vertex.
  const keys = new Float64Array(count * width), key = new Float64Array(width);
  const table = new Int32Array(2 ** Math.ceil(Math.log2(Math.max(2, count * 2)))).fill(-1), mask = table.length - 1;
  const firstSource = new Int32Array(count), newIndices: number[] = new Array(count);
  let next = 0;
  for (let i = 0; i < count; i++) {
    if (i % slice === slice - 1) yield;
    const source = index ? index.getX(i) : i;
    let h = 0x811c9dc5, w = 0;
    for (let a = 0; a < arrays.length; a++) {
      const array = arrays[a], size = sizes[a], at = source * size, scaled = normalized[a];
      for (let k = 0; k < size; k++) {
        const value = Math.trunc((scaled ? denormalize(array[at + k], array) : array[at + k]) * hashMultiplier + hashAdditive);
        key[w++] = value;
        h = Math.imul(h ^ (value | 0), 0x01000193) ^ Math.imul((value / 4294967296) | 0, 0x9e3779b1);
      }
    }
    let slot = h & mask, found = -1;
    for (;;) {
      const candidate = table[slot];
      if (candidate < 0) break;
      // As the string keys compare: -0 equals 0, NaN equals NaN.
      let same = true;
      for (let k = 0, at = candidate * width; k < width; k++) {
        const x = keys[at + k], y = key[k];
        if (x !== y && !(x !== x && y !== y)) { same = false; break; }
      }
      if (same) { found = candidate; break; }
      slot = (slot + 1) & mask;
    }
    if (found < 0) {
      found = next++;
      table[slot] = found; keys.set(key, found * width); firstSource[found] = source;
    }
    newIndices[i] = found;
  }
  yield;
  const result = geometry.clone();
  attributes.forEach((attribute, a) => {
    const size = sizes[a], array = arrays[a], scaled = normalized[a];
    const values = new (array.constructor as new (n: number) => THREE.TypedArray)(next * size);
    for (let v = 0; v < next; v++) for (let k = 0, from = firstSource[v] * size; k < size; k++)
      values[v * size + k] = scaled ? normalize(denormalize(array[from + k], array), array) : array[from + k];
    result.setAttribute(names[a], new (attribute.constructor as typeof THREE.BufferAttribute)(values, size, attribute.normalized));
  });
  result.setIndex(newIndices);
  return result;
}
