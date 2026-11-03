import type * as THREE from 'three/webgpu';

// A prototype and its live placements own immutable geometry independently.
const users = new WeakMap<THREE.BufferGeometry, number>();
export function retainGeometry(geometry: THREE.BufferGeometry) {
  users.set(geometry, (users.get(geometry) ?? 0) + 1);
}
export function sharedGeometry(geometry: THREE.BufferGeometry) { return users.has(geometry); }
/** Bytes of typed arrays only this model owns (released with it): its geometry attributes, indices and instance data.
 *  Shared prototype geometry is excluded, as dropping the model does not free it. */
export function ownedGeometryBytes(model: THREE.Object3D) {
  const seen = new Set<THREE.BufferGeometry>();
  let bytes = 0;
  model.traverse(object => {
    const mesh = object as THREE.Mesh;
    if (!mesh.isMesh) return;
    const instanced = mesh as unknown as THREE.InstancedMesh;
    if (instanced.isInstancedMesh) bytes += instanced.instanceMatrix.array.byteLength + (instanced.instanceColor?.array.byteLength ?? 0);
    const geometry = mesh.geometry;
    if (seen.has(geometry) || users.has(geometry)) return;
    seen.add(geometry);
    for (const attribute of Object.values(geometry.attributes)) bytes += (attribute as THREE.BufferAttribute).array?.byteLength ?? 0;
    bytes += geometry.index?.array.byteLength ?? 0;
  });
  return bytes;
}
export function releaseGeometry(geometry: THREE.BufferGeometry) {
  const count = users.get(geometry);
  if (count !== undefined && count > 1) { users.set(geometry, count - 1); return; }
  users.delete(geometry); geometry.dispose();
}
