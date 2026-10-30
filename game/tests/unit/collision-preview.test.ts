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
