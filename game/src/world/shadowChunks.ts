import * as THREE from 'three/webgpu';
import { COLLISION_TILE } from '../core/origin';

/** A tile's shadow geometry: one shared position buffer and one index buffer whose triangles are sorted by 62.5 m
 *  chunk. `chunks` draw one chunk's index range each (per-chunk culling for the nearest cascade); `whole` draws every
 *  range at once (one draw per tile for the outer cascades). Both share the same two buffers: no extra GPU memory,
 *  and less than the old per-chunk compacted copies, which repeated every vertex on a chunk border. */
export interface ShadowGeometry { whole: THREE.BufferGeometry; chunks: THREE.BufferGeometry[] }

/** Partition indices only: a triangle stays whole, and its complete bounds determine shadow-camera culling. */
export function shadowChunks(positions: Float32Array, indices: Uint16Array | Uint32Array, world: THREE.Matrix4): ShadowGeometry {
  return drain(shadowChunkSteps(positions, indices, world));
}

export function* shadowChunkSteps(positions: Float32Array, indices: Uint16Array | Uint32Array, world: THREE.Matrix4): Generator<void, ShadowGeometry> {
  const attribute = new THREE.BufferAttribute(positions, 3);
  const bounds = new THREE.Box3().setFromBufferAttribute(attribute).applyMatrix4(world);
  const x0 = Math.floor(bounds.min.x / COLLISION_TILE), z0 = Math.floor(bounds.min.z / COLLISION_TILE);
  const columns = Math.floor(bounds.max.x / COLLISION_TILE) - x0 + 1;
  const groups = new Map<number, { count: number; start: number; bounds: THREE.Box3 }>();
  const keys = new Uint32Array(indices.length / 3), center = new THREE.Vector3(), point = new THREE.Vector3();
  for (let i = 0; i < indices.length; i += 3) {
    if (i % 3072 === 0) yield;
    center.set(0, 0, 0);
    for (let j = 0; j < 3; j++) center.add(point.fromBufferAttribute(attribute, indices[i + j]));
    center.multiplyScalar(1 / 3).applyMatrix4(world);
    const key = Math.floor(center.x / COLLISION_TILE) - x0 + columns * (Math.floor(center.z / COLLISION_TILE) - z0);
    keys[i / 3] = key;
    let group = groups.get(key);
    if (!group) { group = { count: 0, start: 0, bounds: new THREE.Box3() }; groups.set(key, group); }
    group.count += 3;
  }
  // Chunks in first-seen order, each a contiguous range of the sorted index buffer.
  let offset = 0;
  for (const group of groups.values()) { group.start = offset; offset += group.count; group.count = 0; }
  const sorted = new (indices.constructor as new (n: number) => Uint16Array | Uint32Array)(indices.length);
  for (let i = 0; i < indices.length; i += 3) {
    if (i % 3072 === 0) yield;
    const group = groups.get(keys[i / 3])!;
    for (let j = 0; j < 3; j++) {
      const index = indices[i + j];
      sorted[group.start + group.count++] = index;
      group.bounds.expandByPoint(point.fromBufferAttribute(attribute, index));
    }
  }
  const index = new THREE.BufferAttribute(sorted, 1);
  const share = (start: number, count: number, box: THREE.Box3) => {
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', attribute);
    geometry.setIndex(index);
    geometry.setDrawRange(start, count);
    geometry.boundingBox = box;
    geometry.boundingSphere = box.getBoundingSphere(new THREE.Sphere());
    return geometry;
  };
  const chunks: THREE.BufferGeometry[] = [], all = new THREE.Box3();
  for (const group of groups.values()) {
    yield;
    chunks.push(share(group.start, group.count, group.bounds));
    all.union(group.bounds);
  }
  return { whole: share(0, sorted.length, all), chunks };
}

export function disposeShadowBatch(root: THREE.Mesh) {
  root.removeFromParent();
  root.traverse(object => { if ((object as THREE.Mesh).isMesh) (object as THREE.Mesh).geometry.dispose(); });
  (root.material as THREE.Material).dispose();
}

/** Retain the synchronous path for already prepared ownership and small standalone models. */
export function drain<T>(steps: Generator<void, T>): T {
  let next = steps.next();
  while (!next.done) next = steps.next();
  return next.value;
}
