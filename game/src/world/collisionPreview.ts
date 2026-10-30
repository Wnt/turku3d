import * as THREE from 'three/webgpu';
import { mergeGeometries } from 'three/addons/utils/BufferGeometryUtils.js';
import { readCol } from '../shared/col/colFormat';
import { MeshoptDecoder } from 'three/addons/libs/meshopt_decoder.module.js';
import { PreparationBudget } from '../render/preparationBudget';

// A texture-free presentation of the exact cached collision surfaces. Full generated
// facades replace it atomically; ground never waits for their textures or shader programs.
const palette = [0xaaa292, 0x55575a, 0xa7a399, 0xb9b5aa, 0xb9b5aa, 0x666768, 0x777470,
  0x8c877d, 0xaaa599, 0x958974, 0x748357, 0x82745a, 0xc3b48d, 0x8c8d87, 0x557e88,
  0x99958b, 0xa4a098, 0xa4a098, 0x99958b, 0xb7afa2, 0x777773, 0x777773, 0xc2b8a6,
  0x8b8076, 0x777775, 0x777773, 0x706852, 0x777773, 0x928d80, 0x748357, 0x777773, 0x9a8969];
const colors = palette.map(value => new THREE.Color(value));
const budget = new PreparationBudget();

export async function collisionPreview(buffers: ReadonlyMap<string, ArrayBuffer>): Promise<THREE.Object3D> {
  await MeshoptDecoder.ready;
  const geometries: THREE.BufferGeometry[] = [];
  const tint = (geometry: THREE.BufferGeometry, material: number) => {
    const color = colors[material] ?? colors[0], array = new Float32Array(geometry.attributes.position.count * 3);
    for (let i = 0; i < array.length; i += 3) color.toArray(array, i);
    geometry.setAttribute('color', new THREE.BufferAttribute(array, 3));
    geometry.deleteAttribute('uv');
    geometries.push(geometry);
  };
  try {
    for (const buffer of buffers.values()) {
      const raw = readCol(buffer, { decoder: MeshoptDecoder });
      const col = { ...raw, origin: raw.header.origin, prims: raw.primitives }, indices: number[] = [];
      const rgb = new Float32Array(col.positions.length);
      for (let t = 0; t < col.materials.length; t++) {
        const material = col.materials[t];
        if (material === 17 || material === 32) continue; // Query helpers and invisible borders remain invisible.
        for (let k = 0; k < 3; k++) {
          const vertex = col.indices[3 * t + k]; indices.push(vertex);
          (colors[material] ?? colors[0]).toArray(rgb, vertex * 3);
        }
      }
      const geometry = new THREE.BufferGeometry();
      geometry.setAttribute('position', new THREE.BufferAttribute(col.positions, 3));
      geometry.setAttribute('color', new THREE.BufferAttribute(rgb, 3));
      geometry.setIndex(indices); geometry.computeVertexNormals(); geometry.translate(...col.origin);
      geometries.push(geometry);
      for (const p of col.prims) {
        if (p.material === 17 || p.material === 32) continue;
        const [h, r, z] = p.params;
        const shape = p.kind === 1 ? new THREE.BoxGeometry(h * 2, r * 2, z * 2)
          : p.kind === 2 ? new THREE.CapsuleGeometry(r, h * 2, 2, 6)
          : new THREE.CylinderGeometry(r, r, h * 2, 6);
        shape.applyQuaternion(new THREE.Quaternion(...p.rotation));
        shape.translate(col.origin[0] + p.center[0], col.origin[1] + p.center[1], col.origin[2] + p.center[2]);
        tint(shape, p.material);
      }
      const pause = budget.checkpoint(); if (pause) await pause;
    }
    const merged = mergeGeometries(geometries);
    if (!merged) throw new Error('Cannot combine cached collision surfaces');
    const model = new THREE.Group();
    model.add(new THREE.Mesh(merged, new THREE.MeshStandardMaterial({ vertexColors: true, roughness: 1, side: THREE.DoubleSide })));
    model.userData.collisionPreview = true;
    return model;
  } finally { for (const geometry of geometries) geometry.dispose(); }
}
