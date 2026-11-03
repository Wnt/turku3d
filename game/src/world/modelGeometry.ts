import type * as THREE from 'three/webgpu';

// A prototype and its live placements own immutable geometry independently.
const users = new WeakMap<THREE.BufferGeometry, number>();
export function retainGeometry(geometry: THREE.BufferGeometry) {
  users.set(geometry, (users.get(geometry) ?? 0) + 1);
}
export function sharedGeometry(geometry: THREE.BufferGeometry) { return users.has(geometry); }
export function releaseGeometry(geometry: THREE.BufferGeometry) {
  const count = users.get(geometry);
  if (count !== undefined && count > 1) { users.set(geometry, count - 1); return; }
  users.delete(geometry); geometry.dispose();
}
