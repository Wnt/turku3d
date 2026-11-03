import * as THREE from 'three/webgpu';
import { mergeVertices } from 'three/addons/utils/BufferGeometryUtils.js';

import { weldArrays, type WeldInput, type WeldOutput } from './weldArrays';

/** Plain (non-interleaved, non-normalized) Float32 attributes without morph targets. */
function weldable(geometry: THREE.BufferGeometry) {
  return !Object.keys(geometry.morphAttributes).length && Object.values(geometry.attributes).every(value => {
    const attribute = value as THREE.BufferAttribute;
    return !(attribute as unknown as THREE.InterleavedBufferAttribute).isInterleavedBufferAttribute && !attribute.normalized
      && attribute.array instanceof Float32Array && attribute.array.length === attribute.count * attribute.itemSize;
  });
}

export function weldInput(geometry: THREE.BufferGeometry): WeldInput {
  const names = Object.keys(geometry.attributes), itemSizes: number[] = [], arrays: Float32Array[] = [];
  for (const name of names) {
    const attribute = geometry.attributes[name] as THREE.BufferAttribute;
    itemSizes.push(attribute.itemSize); arrays.push(attribute.array as Float32Array);
  }
  const index = geometry.index ? Uint32Array.from(geometry.index.array as ArrayLike<number>) : null;
  return { names, itemSizes, arrays, index };
}

export function weldResult(geometry: THREE.BufferGeometry, input: WeldInput, output: WeldOutput) {
  const result = new THREE.BufferGeometry();
  result.name = geometry.name; result.userData = geometry.userData;
  for (const group of geometry.groups) result.addGroup(group.start, group.count, group.materialIndex);
  result.setDrawRange(geometry.drawRange.start, geometry.drawRange.count);
  input.names.forEach((name, i) => result.setAttribute(name, new THREE.BufferAttribute(output.arrays[i], input.itemSizes[i])));
  result.setIndex(new THREE.BufferAttribute(output.index, 1));
  return result;
}

/** Main-thread weld (tests, environments without workers). */
export function weldGeometry(geometry: THREE.BufferGeometry, tolerance = 1e-4) {
  if (!weldable(geometry)) return mergeVertices(geometry, tolerance);
  const input = weldInput(geometry);
  return weldResult(geometry, input, weldArrays(input, tolerance));
}

let worker: Worker | undefined, sequence = 0;
const pending = new Map<number, { resolve(output: WeldOutput): void; reject(error: Error): void }>();
/** Weld in a worker. The source attribute arrays are transferred: the caller must discard `geometry`. */
export async function weldGeometryAsync(geometry: THREE.BufferGeometry, tolerance = 1e-4) {
  if (typeof Worker === 'undefined' || !weldable(geometry)) return weldGeometry(geometry, tolerance);
  const input = weldInput(geometry);
  if (!worker) {
    worker = new Worker(new URL('./weldGeometry.worker.ts', import.meta.url), { type: 'module', name: 'weld-geometry' });
    worker.onmessage = ({ data }) => {
      const job = pending.get(data.id); pending.delete(data.id);
      if (data.error) job?.reject(new Error(data.error)); else job?.resolve(data.output);
    };
    worker.onerror = event => {
      for (const job of pending.values()) job.reject(new Error(event.message));
      pending.clear(); worker?.terminate(); worker = undefined;
    };
  }
  const id = ++sequence;
  const output = await new Promise<WeldOutput>((resolve, reject) => {
    pending.set(id, { resolve, reject });
    // Transfer whole owned buffers; copy views into shared (e.g. glTF) buffers.
    const arrays = input.arrays.map(array => array.byteOffset === 0 && array.byteLength === array.buffer.byteLength ? array : array.slice());
    const transfer = [...new Set([...arrays.map(array => array.buffer), ...(input.index ? [input.index.buffer] : [])])] as ArrayBuffer[];
    worker!.postMessage({ id, input: { ...input, arrays }, tolerance }, transfer);
  });
  return weldResult(geometry, input, output);
}
