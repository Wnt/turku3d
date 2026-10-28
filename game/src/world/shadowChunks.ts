import * as THREE from 'three/webgpu';
import { COLLISION_TILE } from '../core/origin';

/** Partition indices only: a triangle stays whole, and its complete bounds determine shadow-camera culling. */
export function shadowChunks(positions: Float32Array, indices: Uint16Array | Uint32Array, world: THREE.Matrix4): THREE.BufferGeometry[] {
  const attribute = new THREE.BufferAttribute(positions, 3);
  const bounds = new THREE.Box3().setFromBufferAttribute(attribute).applyMatrix4(world);
  const x0 = Math.floor(bounds.min.x / COLLISION_TILE), z0 = Math.floor(bounds.min.z / COLLISION_TILE);
  const columns = Math.floor(bounds.max.x / COLLISION_TILE) - x0 + 1;
  const groups = new Map<number, { count: number; geometry?: THREE.BufferGeometry; indices?: Uint16Array | Uint32Array; bounds: THREE.Box3 }>();
  const keys = new Uint32Array(indices.length / 3), center = new THREE.Vector3(), point = new THREE.Vector3();
  for (let i = 0; i < indices.length; i += 3) {
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
    const group = groups.get(keys[i / 3])!;
    for (let j = 0; j < 3; j++) {
      const index = indices[i + j];
      group.indices![group.count++] = index;
      group.bounds.expandByPoint(point.fromBufferAttribute(attribute, index));
    }
  }
  return [...groups.values()].map(group => {
    const geometry = group.geometry!;
    geometry.boundingBox = group.bounds;
    geometry.boundingSphere = group.bounds.getBoundingSphere(new THREE.Sphere());
    return geometry;
  });
}

export function disposeShadowBatch(root: THREE.Mesh) {
  root.removeFromParent();
  root.traverse(object => { if ((object as THREE.Mesh).isMesh) (object as THREE.Mesh).geometry.dispose(); });
  (root.material as THREE.Material).dispose();
}
