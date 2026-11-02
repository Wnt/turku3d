// Replace one landmark in the resident QA scene; streamed collision stays untouched.
import * as THREE from 'three/webgpu';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';

let overlay: THREE.Object3D | null = null;
const hidden = new Map<THREE.Object3D, boolean>();
let serial = Promise.resolve<unknown>(null);

function restore() {
  for (const [object, visible] of hidden) object.visible = visible;
  hidden.clear();
  if (!overlay) return;
  overlay.removeFromParent();
  const textures = new Set<THREE.Texture>();
  const materials = new Set<THREE.Material>();
  overlay.traverse(object => {
    const mesh = object as THREE.Mesh;
    if (!mesh.isMesh) return;
    mesh.geometry.dispose();
    for (const material of Array.isArray(mesh.material) ? mesh.material : [mesh.material]) {
      materials.add(material);
      for (const value of Object.values(material)) if (value instanceof THREE.Texture) textures.add(value);
    }
  });
  for (const texture of textures) texture.dispose();
  for (const material of materials) material.dispose();
  overlay = null;
}

async function apply(scene: THREE.Scene, buildingId: string, url: string | null) {
  if (!url) { restore(); return { restored: true }; }
  const gltf = await new GLTFLoader().loadAsync(url);
  restore();
  scene.traverse(object => {
    const ids = object.userData.objects?.map((part: { building_id?: string }) => part.building_id)
      ?? (object.userData.building_id ? [object.userData.building_id] : []);
    if (ids.length && ids.every((id: string | undefined) => id === buildingId)) {
      hidden.set(object, object.visible);
      object.visible = false;
    }
  });
  overlay = gltf.scene;
  if (!hidden.size) {
    restore();
    throw new Error(`Landmark ${buildingId} is not resident as independently owned geometry`);
  }
  overlay.name = `landmark-preview-${buildingId}`;
  let triangles = 0, meshes = 0;
  overlay.traverse(object => {
    const mesh = object as THREE.Mesh;
    if (!mesh.isMesh) return;
    mesh.receiveShadow = true;
    // Keep the resident tile's surveyed shadow shell during this appearance-only preview.
    mesh.castShadow = false;
    triangles += (mesh.geometry.index?.count ?? mesh.geometry.attributes.position.count) / 3;
    meshes++;
  });
  scene.add(overlay);
  return { buildingId, hidden: hidden.size, meshes, triangles, url, preview: true };
}

export function previewLandmark(scene: THREE.Scene, buildingId: string, url: string | null) {
  serial = serial.catch(() => null).then(() => apply(scene, buildingId, url));
  return serial;
}
