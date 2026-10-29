import { expect, test } from 'vitest';
import * as THREE from 'three/webgpu';
import { staticBatchSteps, releaseStaticBatches } from '../../src/world/staticBatch';
import { NormalPrepass } from '../../src/render/normalPrepass';

const batch = (model: THREE.Object3D) => { for (const step of staticBatchSteps(model)) void step; };

test('static batches preserve transforms, custom facade attributes and semantic ranges, then release draws', () => {
  const model = new THREE.Group(), material = new THREE.MeshStandardMaterial();
  const sources = [2, 5].map(x => {
    const geometry = new THREE.BoxGeometry(); geometry.clearGroups();
    geometry.setAttribute('_kit_opening', new THREE.Float32BufferAttribute(new Float32Array(geometry.attributes.position.count * 4), 4));
    geometry.setAttribute('_kit_wall', new THREE.Float32BufferAttribute(new Float32Array(geometry.attributes.position.count * 3).fill(x), 3));
    const mesh = new THREE.Mesh(geometry, material); mesh.position.x = x;
    mesh.userData = { layer: 'buildings', object_id: `building-${x}` }; model.add(mesh); return mesh;
  });
  batch(model); expect(model.children).toHaveLength(1);
  const draw = model.children[0] as THREE.Mesh;
  expect(draw.geometry.index!.count).toBe(72);
  expect(draw.geometry.getAttribute('_kit_wall').getX(24)).toBe(5);
  expect((draw.geometry.getAttribute('_kit_wall') as THREE.InterleavedBufferAttribute).data)
    .toBe((draw.geometry.getAttribute('_kit_opening') as THREE.InterleavedBufferAttribute).data);
  expect(draw.geometry.boundingBox!.min.x).toBe(1.5);
  expect(draw.geometry.boundingBox!.max.x).toBe(5.5);
  expect(draw.userData.objects.map((row: { start: number }) => row.start)).toEqual([0, 36]);
  let disposed = 0; draw.addEventListener('dispose' as never, () => disposed++);
  releaseStaticBatches(model); releaseStaticBatches(model);
  expect(disposed).toBe(1); expect(model.children).toEqual(sources);
});

test('static batching preserves transparent ordering, mutable props and separated spatial chunks', () => {
  const model = new THREE.Group(), material = new THREE.MeshStandardMaterial();
  for (const x of [2, 200]) {
    const geometry = new THREE.BoxGeometry(); geometry.clearGroups();
    const mesh = new THREE.Mesh(geometry, material); mesh.position.x = x; mesh.userData.layer = 'buildings'; model.add(mesh);
  }
  for (let i = 0; i < 2; i++) {
    const mesh = new THREE.Mesh(new THREE.BoxGeometry(), material); mesh.geometry.clearGroups();
    mesh.userData = { layer: 'structures', prop_type: 'sign' }; model.add(mesh);
  }
  batch(model); expect(model.children).toHaveLength(4);
});

test('normal prepass reuses sided opaque variants and preserves authored silhouette paths', () => {
  const pre = new NormalPrepass(), a = new THREE.MeshStandardNodeMaterial(), b = a.clone();
  expect(pre.select(a)).toBe(pre.select(b));
  b.side = THREE.DoubleSide; expect(pre.select(b)).not.toBe(pre.select(a));
  b.alphaTest = .5; expect(pre.select(b)).toBeNull();
  b.alphaTest = 0; b.displacementMap = new THREE.Texture(); expect(pre.select(b)).toBeNull();
  pre.dispose();
});
