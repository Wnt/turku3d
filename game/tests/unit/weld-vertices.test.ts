import './fleet-assets.fixture';
import { expect, test } from 'vitest';
import * as THREE from 'three/webgpu';
import { readFile } from 'node:fs/promises';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { MeshoptDecoder } from 'three/addons/libs/meshopt_decoder.module.js';
import { mergeGeometries, mergeVertices } from 'three/addons/utils/BufferGeometryUtils.js';
import { weldVertices } from '../../src/render/weldVertices';
import { AUTHORED_BY_ID } from '../../src/actors/fleet/registry';

const same = (a: THREE.BufferGeometry, b: THREE.BufferGeometry) => {
  expect(Object.keys(a.attributes)).toEqual(Object.keys(b.attributes));
  for (const name of Object.keys(a.attributes)) {
    const x = a.getAttribute(name), y = b.getAttribute(name);
    expect(x.constructor).toBe(y.constructor); expect(x.itemSize).toBe(y.itemSize); expect(x.normalized).toBe(y.normalized);
    expect(x.array.constructor).toBe(y.array.constructor); expect(Array.from(x.array)).toEqual(Array.from(y.array));
  }
  expect(a.index!.array.constructor).toBe(b.index!.array.constructor);
  expect(Array.from(a.index!.array)).toEqual(Array.from(b.index!.array));
};

test('weldVertices matches mergeVertices: duplicates, near values, signed zeros, indexed and not', () => {
  const positions = new Float32Array([0, 0, 0, 1, 0, 0, 0, 1, 0, 1e-5, 0, -0, 1, 0, 0, 0, 1.00004, 0, 0, 1.0002, 0, -0, -0, 0, 5, 5, 5]);
  const colors = new Float32Array(27).map((_, i) => (i % 7) / 7);
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));
  geometry.setAttribute('color', new THREE.BufferAttribute(colors.map((v, i) => i >= 9 && i < 18 ? colors[i - 9] : v), 3));
  same(weldVertices(geometry), mergeVertices(geometry));
  geometry.setIndex([0, 1, 2, 3, 4, 5, 8, 7, 6, 2, 1, 0]);
  same(weldVertices(geometry), mergeVertices(geometry));
  same(weldVertices(geometry, 0.01), mergeVertices(geometry, 0.01));
});

test('weldVertices matches mergeVertices on shipped fleet cars', async () => {
  for (const id of ['carla-ford-ambulance', 'carla-audi-a2']) {
    const entry = AUTHORED_BY_ID.get(id)!;
    const bytes = await readFile(new URL(`../../public${entry.assetBase}/lod0.glb`, import.meta.url));
    const gltf = await new GLTFLoader().setMeshoptDecoder(MeshoptDecoder).parseAsync(bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength), '');
    const parts: THREE.BufferGeometry[] = [];
    gltf.scene.updateMatrixWorld(true);
    gltf.scene.traverse(object => {
      const mesh = object as THREE.Mesh;
      if (!mesh.isMesh) return;
      const g = (mesh.geometry.index ? mesh.geometry.toNonIndexed() : mesh.geometry.clone()).applyMatrix4(mesh.matrixWorld);
      for (const key of Object.keys(g.attributes)) if (!['position', 'normal'].includes(key)) g.deleteAttribute(key);
      parts.push(g);
    });
    const joined = mergeGeometries(parts, false)!;
    same(weldVertices(joined), mergeVertices(joined));
  }
}, 30000);
