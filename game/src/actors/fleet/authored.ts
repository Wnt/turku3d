import * as THREE from 'three/webgpu';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { MeshoptDecoder } from 'three/addons/libs/meshopt_decoder.module.js';
import { mergeGeometries, mergeVertices } from 'three/addons/utils/BufferGeometryUtils.js';

export const AUTHORED_ESTATE = 'fleet-estate-3';
const pending = new Map<number, Promise<THREE.BufferGeometry>>();

/** Bake authored transforms and material roles into the fleet's single instanced draw. */
export function flattenFleetAsset(root: THREE.Object3D): THREE.BufferGeometry {
  const parts: THREE.BufferGeometry[] = [];
  root.updateMatrixWorld(true);
  root.traverse(object => {
    if (!(object as THREE.Mesh).isMesh) return;
    const mesh = object as THREE.Mesh<THREE.BufferGeometry, THREE.MeshStandardMaterial>;
    if (Array.isArray(mesh.material)) throw Error('Expected one GLTF material per primitive');
    const material = mesh.material, role = material.name;
    const names = `${mesh.name} ${role}`.toLowerCase();
    const front = /lamp[-_ ]front|fleet_headlamp/.test(names), tail = /lamp[-_ ]tail|fleet_taillamp/.test(names);
    if (!role.startsWith('fleet_') && !front && !tail) throw Error(`Unexpected fleet material: ${role}`);
    const g = mesh.geometry.index ? mesh.geometry.toNonIndexed() : mesh.geometry.clone();
    g.applyMatrix4(mesh.matrixWorld);
    for (const key of Object.keys(g.attributes)) if (!['position', 'normal'].includes(key)) g.deleteAttribute(key);
    const n = g.getAttribute('position').count, colors = new Float32Array(n * 3), surfaces = new Float32Array(n * 4);
    const paint = role === 'fleet_paint' && !front && !tail;
    const color = front ? new THREE.Color(0xdde6e8) : tail ? new THREE.Color(0xb91108) : paint ? new THREE.Color(1, 1, 1) : material.color;
    const lamp = front ? 1 : tail ? 4 : role === 'fleet_indicator_left' ? 2 : role === 'fleet_indicator_right' ? 3 : 0;
    for (let i = 0; i < n; i++) {
      colors.set([color.r, color.g, color.b], i * 3);
      surfaces.set([Number(paint), material.roughness, material.metalness, lamp], i * 4);
    }
    g.setAttribute('color', new THREE.BufferAttribute(colors, 3));
    g.setAttribute('fleetSurface', new THREE.BufferAttribute(surfaces, 4));
    g.clearGroups(); parts.push(g);
  });
  if (!parts.length) throw Error('Empty authored fleet asset');
  const joined = mergeGeometries(parts, false)!;
  const geometry = mergeVertices(joined);
  joined.dispose(); for (const part of parts) part.dispose();
  geometry.computeBoundingBox(); geometry.computeBoundingSphere();
  return geometry;
}

export async function loadAuthoredFleet(model: string, lod: number): Promise<THREE.BufferGeometry | null> {
  if (model !== AUTHORED_ESTATE) return null;
  let request = pending.get(lod);
  if (!request) {
    const loader = new GLTFLoader().setMeshoptDecoder(MeshoptDecoder);
    request = loader.loadAsync(`/assets/cars/aava-estate/estate_lod${lod}.glb`).then(gltf => {
      const geometry = flattenFleetAsset(gltf.scene);
      gltf.scene.traverse(object => {
        const mesh = object as THREE.Mesh;
        if (mesh.isMesh) { mesh.geometry.dispose(); for (const material of [mesh.material].flat()) material.dispose(); }
      });
      return geometry;
    }).catch(error => { pending.delete(lod); throw error; });
    pending.set(lod, request);
  }
  return (await request).clone();
}
