import { expect, test, vi } from 'vitest';
import * as THREE from 'three/webgpu';
import { staticBatchSteps, releaseStaticBatches } from '../../src/world/staticBatch';
import { NormalPrepass } from '../../src/render/normalPrepass';
import { mergeGeometries } from 'three/addons/utils/BufferGeometryUtils.js';

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

test('placed models merge compatible draws across owners while preserving restoration and conservative visibility', async () => {
  const { staticBatchPlacements, updateStaticBatchVisibility } = await import('../../src/world/staticBatch');
  const model = new THREE.Group(), material = new THREE.MeshStandardMaterial();
  const placements = [10, 30].map((x, i) => {
    const owner = new THREE.Group(); owner.position.set(x, 0, 15);
    owner.userData = { archetype: `hash-${i}`, placementId: `building-${i}` };
    const inner = new THREE.Group(); inner.position.y = 3; owner.add(inner); model.add(owner);
    const geometry = new THREE.BoxGeometry(4, 6, 4); geometry.clearGroups();
    geometry.setAttribute('color_1', new THREE.Float32BufferAttribute(new Float32Array(24 * 3).fill(.5 + i * .1), 3));
    const mesh = new THREE.Mesh(geometry, material); mesh.userData = { layer: 'buildings', object_id: `wall-${i}` };
    inner.add(mesh); return { owner, inner, mesh };
  });
  batch(model);
  const merged = model.getObjectByName('static-placement-batch') as THREE.Mesh;
  expect(merged).toBeDefined();
  expect(staticBatchPlacements(merged)).toEqual(placements.map(p => p.owner));
  expect(merged.geometry.boundingBox!.min.toArray()).toEqual([8, 0, 13]);
  expect(merged.geometry.boundingBox!.max.toArray()).toEqual([32, 6, 17]);
  expect(merged.geometry.getAttribute('color_1').getX(24)).toBeCloseTo(.6);
  placements[0].owner.visible = false; updateStaticBatchVisibility(model); expect(merged.visible).toBe(true);
  placements[1].owner.visible = false; updateStaticBatchVisibility(model); expect(merged.visible).toBe(false);
  placements[0].owner.visible = true; updateStaticBatchVisibility(model); expect(merged.visible).toBe(true);
  releaseStaticBatches(model);
  for (const { mesh, inner } of placements) expect(mesh.parent).toBe(inner);
  expect(staticBatchPlacements(merged)).toBeUndefined();
});

test('placements batch per generator block, else per 125 m cell', async () => {
  const { staticBatchSteps, staticBatchPlacements } = await import('../../src/world/staticBatch');
  const material = new THREE.MeshStandardMaterial();
  const model = new THREE.Group();
  const place = (x: number, block?: string) => {
    const wrapper = new THREE.Group(); wrapper.position.set(x, 0, 10);
    wrapper.userData = { archetype: 'a'.repeat(64), placementId: `p${x}`, block };
    const geometry = new THREE.BoxGeometry(); geometry.clearGroups();
    const mesh = new THREE.Mesh(geometry, material); mesh.userData.layer = 'buildings';
    wrapper.add(mesh); model.add(wrapper); return wrapper;
  };
  place(10); place(40); place(130); place(140, 'blockA'); place(300, 'blockA'); place(310, 'blockB');
  for (const _ of staticBatchSteps(model)) void _;
  const batches = model.children.filter(o => o.name === 'static-placement-batch');
  const owners = batches.map(b => staticBatchPlacements(b)!.map(p => p.userData.placementId).sort());
  expect(owners.sort()).toEqual([['p10', 'p40'], ['p140', 'p300']]);
});

test('the batch scan yields within a big tile, and a second run cannot start while one is scanning', () => {
  const model = new THREE.Group();
  for (let i = 0; i < 200; i++) {
    const mesh = new THREE.Mesh(new THREE.BoxGeometry(1, 1, 1), new THREE.MeshStandardMaterial());
    mesh.position.set(i * 3, 0, 0); mesh.userData.layer = 'ground'; model.add(mesh);
  }
  const first = staticBatchSteps(model);
  first.next();
  // Mid-scan: another preparation of the same model ends at once instead of batching it twice.
  expect([...staticBatchSteps(model)].length).toBe(0);
  let yields = 1;
  while (!first.next().done) yields++;
  // Every mesh has its own material: nothing merges, so every yield is the scan's.
  expect(yields).toBe(Math.floor(200 / 64));
  expect(model.children.length).toBe(200);
});

test('a batch computes its bounds once, from the merged vertices (two passes), and the scan keeps loaded bounds', () => {
  const model = new THREE.Group(), material = new THREE.MeshStandardMaterial();
  for (const [x, angle] of [[2, 0.3], [9, 1.1], [4, 2]]) {
    const geometry = new THREE.SphereGeometry(1.5, 12, 8); geometry.clearGroups(); geometry.computeBoundingBox();
    const mesh = new THREE.Mesh(geometry, material); mesh.position.set(x, 1, -x); mesh.rotation.set(angle, angle / 2, 0);
    mesh.userData.layer = 'buildings'; model.add(mesh);
  }
  // The merger builds its geometry from the addon's 'three' import, which tests may resolve to a second copy.
  const merged = mergeGeometries([new THREE.BoxGeometry()])!;
  const prototypes = new Set([THREE.BufferGeometry.prototype, Object.getPrototypeOf(merged)]);
  const boxes = [...prototypes].map(p => vi.spyOn(p, 'computeBoundingBox'));
  const spheres = [...prototypes].map(p => vi.spyOn(p, 'computeBoundingSphere'));
  const calls = (spies: typeof boxes) => spies.reduce((sum, spy) => sum + spy.mock.calls.length, 0);
  try { batch(model); expect(calls(boxes)).toBe(0); expect(calls(spheres)).toBe(0); }
  finally { for (const spy of [...boxes, ...spheres]) spy.mockRestore(); }
  const draw = model.children[0] as THREE.Mesh, fresh = draw.geometry.clone();
  fresh.computeBoundingBox(); fresh.computeBoundingSphere();
  expect(draw.geometry.boundingBox).toEqual(fresh.boundingBox);
  expect(draw.geometry.boundingSphere!.center).toEqual(fresh.boundingSphere!.center);
  expect(draw.geometry.boundingSphere!.radius).toBeCloseTo(fresh.boundingSphere!.radius, 12);
});
