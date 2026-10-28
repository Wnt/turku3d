import * as THREE from 'three/webgpu';
import { COLLISION_TILE } from '../core/origin';

/** Partition indices only: a triangle stays whole, and its complete bounds determine shadow-camera culling. */
export function shadowChunks(positions: Float32Array, indices: Uint16Array | Uint32Array, world: THREE.Matrix4): THREE.BufferGeometry[] {
  return drain(shadowChunkSteps(positions, indices, world));
}

export function* shadowChunkSteps(positions: Float32Array, indices: Uint16Array | Uint32Array, world: THREE.Matrix4): Generator<void, THREE.BufferGeometry[]> {
  const attribute = new THREE.BufferAttribute(positions, 3);
  const bounds = new THREE.Box3().setFromBufferAttribute(attribute).applyMatrix4(world);
  const x0 = Math.floor(bounds.min.x / COLLISION_TILE), z0 = Math.floor(bounds.min.z / COLLISION_TILE);
  const columns = Math.floor(bounds.max.x / COLLISION_TILE) - x0 + 1;
  const groups = new Map<number, { count: number; geometry?: THREE.BufferGeometry; indices?: Uint16Array | Uint32Array; bounds: THREE.Box3 }>();
  const keys = new Uint32Array(indices.length / 3), center = new THREE.Vector3(), point = new THREE.Vector3();
  for (let i = 0; i < indices.length; i += 3) {
    if (i % 3072 === 0) yield;
    center.set(0, 0, 0);
    for (let j = 0; j < 3; j++) center.add(point.fromBufferAttribute(attribute, indices[i + j]));
    center.multiplyScalar(1 / 3).applyMatrix4(world);
    const key = Math.floor(center.x / COLLISION_TILE) - x0 + columns * (Math.floor(center.z / COLLISION_TILE) - z0);
    keys[i / 3] = key;
    let group = groups.get(key);
    if (!group) { group = { count: 0, bounds: new THREE.Box3() }; groups.set(key, group); }
    group.count += 3;
  }
  for (const group of groups.values()) {
    group.indices = positions.length / 3 > 65535 ? new Uint32Array(group.count) : new Uint16Array(group.count);
    group.count = 0;
    group.geometry = new THREE.BufferGeometry();
    group.geometry.setAttribute('position', attribute);
    group.geometry.setIndex(new THREE.BufferAttribute(group.indices, 1));
  }
  for (let i = 0; i < indices.length; i += 3) {
    if (i % 3072 === 0) yield;
    const group = groups.get(keys[i / 3])!;
    for (let j = 0; j < 3; j++) {
      const index = indices[i + j];
      group.indices![group.count++] = index;
      group.bounds.expandByPoint(point.fromBufferAttribute(attribute, index));
    }
  }
  const remap = new Uint32Array(positions.length / 3), seen = new Uint32Array(remap.length);
  let generation = 0;
  const geometries: THREE.BufferGeometry[] = [];
  for (const group of groups.values()) {
    yield;
    generation++;
    let vertices = 0;
    const index = group.indices!;
    for (let i = 0; i < index.length; i++) {
      if (i % 4096 === 0) yield;
      const original = index[i];
      if (seen[original] !== generation) { seen[original] = generation; remap[original] = vertices++; }
    }
    // Compact each chunk. Sharing the full tile attribute makes both upload lifetime and LRU byte estimates
    // depend on unrelated chunks (the upstream estimator counts each geometry's attributes independently).
    const compact = new Float32Array(vertices * 3);
    for (let i = 0; i < index.length; i++) {
      if (i % 4096 === 0) yield;
      const original = index[i], local = remap[original];
      compact[local * 3] = positions[original * 3];
      compact[local * 3 + 1] = positions[original * 3 + 1];
      compact[local * 3 + 2] = positions[original * 3 + 2];
      index[i] = local;
    }
    const geometry = group.geometry!;
    geometry.setAttribute('position', new THREE.BufferAttribute(compact, 3));
    geometry.boundingBox = group.bounds;
    geometry.boundingSphere = group.bounds.getBoundingSphere(new THREE.Sphere());
    geometries.push(geometry);
  }
  return geometries;
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
