import type * as THREE from 'three/webgpu';
import { disposeDraw, type SharedMaterials } from '../render/sharedMaterials';

/** Release all render contexts before tile-owned buffers; shared materials keep their other owners. */
export function disposeModelResources(model: THREE.Object3D, shared: SharedMaterials) {
  const materials = new Set<THREE.Material>(), geometries = new Set<THREE.BufferGeometry>(), textures = new Set<THREE.Texture>();
  model.traverse(o => {
    const mesh = o as THREE.Mesh;
    if (!mesh.isMesh) return;
    disposeDraw(mesh); geometries.add(mesh.geometry);
    for (const m of Array.isArray(mesh.material) ? mesh.material : [mesh.material]) {
      materials.add(m);
      for (const v of Object.values(m)) if ((v as THREE.Texture)?.isTexture) textures.add(v as THREE.Texture);
    }
  });
  for (const geometry of geometries) geometry.dispose();
  for (const texture of textures) texture.dispose();
  shared.releaseModel(model, materials);
}
