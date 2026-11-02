import * as THREE from 'three/webgpu';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { MeshoptDecoder } from 'three/addons/libs/meshopt_decoder.module.js';
import { mergeGeometries, mergeVertices } from 'three/addons/utils/BufferGeometryUtils.js';
import { AUTHORED_BY_ID } from './registry';

export const AUTHORED_ESTATE = 'fleet-estate-3';
const pending = new Map<string, Promise<{ body: THREE.BufferGeometry; glass: THREE.BufferGeometry | null }>>();

/** Bake authored transforms and material roles into shared body or glazing geometry. */
export function flattenFleetAsset(root: THREE.Object3D, part: 'all'|'body'|'glass' = 'all'): THREE.BufferGeometry {
  const parts: THREE.BufferGeometry[] = [];
  root.updateMatrixWorld(true);
  root.traverse(object => {
    if (!(object as THREE.Mesh).isMesh) return;
    const mesh = object as THREE.Mesh<THREE.BufferGeometry, THREE.MeshStandardMaterial>;
    if (Array.isArray(mesh.material)) throw Error('Expected one GLTF material per primitive');
    const material = mesh.material, role = material.name;
    const glass = role === 'fleet_glass' && material.opacity < 1;
    if (part === 'body' && glass || part === 'glass' && !glass) return;
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
    const positions = g.getAttribute('position'), normals = g.getAttribute('normal');
    for (let i = 0; i < n; i++) {
      // Road splash on the lower panels and a thin top-facing dust layer, baked once into
      // vertices. No extra texture fetches or per-frame noise in the fleet shader.
      const grime = paint ? Math.max(0, Math.min(1, (.65-positions.getY(i))/.45)) : 0;
      const dust = paint ? Math.max(0, normals.getY(i))*.055 : 0;
      const shade = 1-grime*.22-dust*.35;
      colors.set([color.r*shade, color.g*shade*(1-grime*.025), color.b*shade*(1-grime*.06)], i * 3);
      surfaces.set([Number(paint), Math.min(1,material.roughness+grime*.22+dust),
        material.metalness*(1-grime*.55-dust), part === 'glass' ? material.opacity : lamp], i * 4);
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

export async function loadAuthoredFleet(model: string, lod: number, part: 'body'|'glass' = 'body'): Promise<THREE.BufferGeometry | null> {
  const entry = AUTHORED_BY_ID.get(model);
  if (model !== AUTHORED_ESTATE && !entry) return null;
  const key = `${model}/${lod}`;
  let request = pending.get(key);
  if (!request) {
    const loader = new GLTFLoader().setMeshoptDecoder(MeshoptDecoder);
    const url = entry ? `${entry.assetBase}/lod${lod}.glb` : `/assets/cars/aava-estate/estate_lod${lod}.glb`;
    request = loader.loadAsync(url).then(gltf => {
      const body = flattenFleetAsset(gltf.scene, 'body');
      let hasGlass = false;
      gltf.scene.traverse(object => {
        const m = object as THREE.Mesh<THREE.BufferGeometry, THREE.MeshStandardMaterial>;
        if (m.isMesh && !Array.isArray(m.material) && m.material.name === 'fleet_glass' && m.material.opacity < 1) hasGlass = true;
      });
      const glass = hasGlass ? flattenFleetAsset(gltf.scene, 'glass') : null;
      gltf.scene.traverse(object => {
        const mesh = object as THREE.Mesh;
        if (mesh.isMesh) { mesh.geometry.dispose(); for (const material of [mesh.material].flat()) material.dispose(); }
      });
      return { body, glass };
    }).catch(error => { pending.delete(key); throw error; });
    pending.set(key, request);
  }
  return (await request)[part]?.clone() ?? null;
}
