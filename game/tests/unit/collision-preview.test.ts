import { expect, test } from 'vitest';
import * as THREE from 'three/webgpu';
import { writeCol } from '../../scripts/colWriter.mjs';
import { collisionPreview } from '../../src/world/collisionPreview';

test('cached surfaces preserve tile origins and solid primitives without drawing query helpers', async () => {
  const bytes = writeCol({ i: 4, j: -1, positions: new Float32Array([0, 2, 0, 0, 2, 62.5, 62.5, 2, 0]),
    indices: new Uint32Array([0, 1, 2, 0, 1, 2]), materials: new Uint8Array([1, 17]), flags: 0, meta: {},
    prims: [{ kind: 1, material: 22, center: [10, 5, 10], rotation: [0, 0, 0, 1], params: [2, 3, 2, 0] }] });
  const data = bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength) as ArrayBuffer;
  const model = await collisionPreview(new Map([['c_4_-1', data]]));
  const mesh = model.children[0] as THREE.Mesh;
  expect(mesh.geometry.index!.count / 3).toBe(13); // Ground triangle and the actual solid box.
  mesh.geometry.computeBoundingBox();
  expect(mesh.geometry.boundingBox!.min.toArray()).toEqual([250, 2, -62.5]);
  expect(mesh.geometry.boundingBox!.max.toArray()).toEqual([312.5, 8, 0]);
  expect(mesh.geometry.attributes.color.count).toBe(mesh.geometry.attributes.position.count);
  expect((mesh.material as THREE.MeshStandardMaterial).map).toBeNull();
  mesh.geometry.dispose(); (mesh.material as THREE.Material).dispose();
});

test('a preview drops coplanar tessellation within 5 cm (borders locked), never a visible shape', async () => {
  // A 10 x 10 grid of flat quads (200 triangles) with a 2 m bump in the middle row.
  const n = 11, positions: number[] = [], indices: number[] = [];
  for (let z = 0; z < n; z++) for (let x = 0; x < n; x++) positions.push(x * 5, x === 5 && z === 5 ? 4 : 2, z * 5);
  for (let z = 0; z < n - 1; z++) for (let x = 0; x < n - 1; x++) {
    const a = z * n + x, b = a + 1, c = a + n, d = c + 1; indices.push(a, c, b, b, c, d);
  }
  const bytes = writeCol({ i: 0, j: 0, positions: new Float32Array(positions), indices: new Uint32Array(indices),
    materials: new Uint8Array(indices.length / 3).fill(1), flags: 0, meta: {}, prims: [] });
  const model = await collisionPreview(new Map([['c_0_0', bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength) as ArrayBuffer]]));
  const mesh = model.children[0] as THREE.Mesh, index = mesh.geometry.index!, position = mesh.geometry.attributes.position;
  expect(index.count / 3).toBeLessThan(200);
  // The bump's apex survives.
  let apex = false;
  for (let i = 0; i < index.count; i++) if (position.getY(index.getX(i)) > 3.9) apex = true;
  expect(apex).toBe(true);
});
