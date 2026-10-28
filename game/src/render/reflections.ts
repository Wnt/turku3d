import type * as THREE from 'three/webgpu';

/** Static large surfaces only; actor, vegetation, pole and small-prop work stays out of the river pass. */
export const REFLECTION_LAYER = 2;
const SURFACES = new Set(['ground', 'buildings', 'structures', 'massing', 'roads']);
export function reflectionSurface(mesh: THREE.Mesh): boolean {
  if (mesh.userData.shadowBatch || (mesh as THREE.InstancedMesh).isInstancedMesh) return false;
  for (let node: THREE.Object3D | null = mesh; node; node = node.parent) {
    if (node.userData.material_key === 'water' || node.userData.material_key === 'river') return false;
    if (node.userData.layer) return SURFACES.has(node.userData.layer);
  }
  return false;
}
