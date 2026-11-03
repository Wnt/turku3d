import { expect, test } from 'vitest';
import * as THREE from 'three/webgpu';
import { TileArrays, arraysReleased, drawnGeometries, releaseRenderArrays } from '../../src/world/releaseArrays';
import { retainGeometry } from '../../src/world/modelGeometry';
import { staticBatchSteps } from '../../src/world/staticBatch';
import { BuildingModelCache, type BuildingPlacements } from '../../src/world/buildingPlacements';
import { PreparationBudget } from '../../src/render/preparationBudget';

const uploadedAll = () => true;
function facade() {
  const geometry = new THREE.BoxGeometry(); geometry.clearGroups();
  const kit = new THREE.InterleavedBuffer(new Float32Array(geometry.attributes.position.count * 8), 8);
  geometry.setAttribute('_kit_opening', new THREE.InterleavedBufferAttribute(kit, 4, 0));
  geometry.setAttribute('_kit_wall', new THREE.InterleavedBufferAttribute(kit, 4, 4));
  return geometry;
}

test('uploaded render-only arrays shrink to empty arrays of their type, with no version bump; positions, indices and counts stay', () => {
  const geometry = facade(), count = geometry.attributes.position.count, version = (geometry.attributes.normal as THREE.BufferAttribute).version;
  const before = { normal: geometry.attributes.normal.array.byteLength, uv: geometry.attributes.uv.array.byteLength };
  const bytes = releaseRenderArrays(geometry, uploadedAll);
  expect(bytes).toBe(before.normal + before.uv + count * 8 * 4);
  expect(geometry.attributes.normal.array).toBeInstanceOf(Float32Array);
  expect(geometry.attributes.normal.array.length).toBe(0);
  expect((geometry.attributes.normal as THREE.BufferAttribute).version).toBe(version); // no re-upload
  expect(geometry.attributes.normal.count).toBe(count);
  expect((geometry.attributes._kit_wall as THREE.InterleavedBufferAttribute).count).toBe(count);
  expect(geometry.attributes.position.array.length).toBe(count * 3);
  expect(geometry.index!.array.length).toBeGreaterThan(0);
  expect(arraysReleased(geometry)).toBe(true);
  expect(releaseRenderArrays(geometry, uploadedAll)).toBe(0); // once
});

test('a committed tile releases what the GPU has, queues the rest until its upload, and decides each target once', () => {
  const model = new THREE.Group(), material = new THREE.MeshStandardMaterial();
  const own = new THREE.Mesh(facade(), new THREE.MeshStandardMaterial()); own.userData.layer = 'ground'; model.add(own);
  const later = new THREE.Mesh(facade(), new THREE.MeshStandardMaterial()); later.userData.layer = 'ground'; model.add(later);
  const prop = new THREE.Mesh(facade(), material); prop.userData = { layer: 'structures', prop_type: 'pole' }; model.add(prop);
  const shared = new THREE.Mesh(facade(), material); retainGeometry(shared.geometry); model.add(shared);
  const shadow = new THREE.Mesh(facade(), material); shadow.userData.shadowBatch = true; model.add(shadow);
  const sameMaterial = new THREE.MeshStandardMaterial();
  const merged = [0, 1].map(x => { const m = new THREE.Mesh(facade(), sameMaterial); m.position.x = x * 2; m.userData.layer = 'buildings'; model.add(m); return m; });
  for (const step of staticBatchSteps(model)) void step;
  const uploads = new Set<object>();
  const mark = (geometry: THREE.BufferGeometry) => { for (const a of Object.values(geometry.attributes)) uploads.add((a as THREE.InterleavedBufferAttribute).data ?? a); if (geometry.index) uploads.add(geometry.index); };
  model.traverse(o => { const m = o as THREE.Mesh; if (m.isMesh && m !== later) mark(m.geometry); });
  const arrays = new TileArrays(target => uploads.has(target));
  expect(arrays.commit(model)).toBeGreaterThan(0);
  expect(own.geometry.attributes.normal.array.length).toBe(0);
  expect(shadow.geometry.attributes.position.array.length).toBe(0); // the shadow batch releases everything
  expect(prop.geometry.attributes.normal.array.length).toBeGreaterThan(0);
  expect(shared.geometry.attributes.normal.array.length).toBeGreaterThan(0);
  for (const source of merged) expect(source.geometry.attributes.position.array.length).toBe(0); // merged away
  expect(later.geometry.attributes.normal.array.length).toBeGreaterThan(0); // not uploaded yet: queued
  expect(arrays.check()).toBe(0);
  mark(later.geometry);
  expect(arrays.check()).toBeGreaterThan(0);
  expect(later.geometry.attributes.normal.array.length).toBe(0);
  const total = arrays.releasedBytes;
  expect(total).toBeGreaterThan(0);
  expect(arrays.commit(model)).toBe(0); // a second commit touches nothing
  expect(arrays.releasedBytes).toBe(total);
});

test('templates release once every holder committed: drawn geometry keeps positions, merged-only geometry releases all, a new tile reads afresh', async () => {
  const id = 'b'.repeat(64), base = new URL('http://localhost/coarse/t0_0_0/');
  const data = (): BuildingPlacements => ({ version: 1, models: [{ id, uri: `../shared/${id}.glb`, bounds: { min: [-1, 0, -1], max: [1, 1, 1] } }],
    placements: [{ id: 'p', model: id, position: [0, 0, 0], viewDistance: 300 }] });
  let loads = 0;
  const cache = new BuildingModelCache(async () => { loads++; const root = new THREE.Group(); root.add(new THREE.Mesh(facade(), new THREE.MeshStandardMaterial())); return root; },
    () => {}, new PreparationBudget(() => 0, () => Promise.resolve()));
  const a = new THREE.Group(), b = new THREE.Group(), c = new THREE.Group();
  await cache.attach(a, data(), base);
  await cache.attach(b, data(), base);
  const arrays = new TileArrays(uploadedAll);
  const release = () => cache.releaseCommitted((root, owners) => arrays.template(root, g => owners.some(o => drawnGeometries(o as THREE.Object3D).has(g))));
  cache.markCommitted(a);
  expect(release()).toBe(0); // b is still installing
  cache.markCommitted(b);
  expect(release()).toBeGreaterThan(0);
  const drawn = (a.children[0].children[0].children[0] as THREE.Mesh).geometry;
  expect(drawn.attributes.position.array.length).toBeGreaterThan(0); // drawn by a and b: positions stay
  expect(drawn.attributes.normal.array.length).toBe(0);
  expect(release()).toBe(0); // once
  await cache.attach(c, data(), base); // a new tile cannot clone released arrays: fresh template
  expect(loads).toBe(2);
  expect((c.children[0].children[0].children[0] as THREE.Mesh).geometry.attributes.normal.array.length).toBeGreaterThan(0);
  // A template whose clones were all merged away (no holder draws it) releases everything.
  const merged = new THREE.Group(); merged.add(new THREE.Mesh(facade(), new THREE.MeshStandardMaterial()));
  const geometry = (merged.children[0] as THREE.Mesh).geometry;
  expect(arrays.template(merged, () => false)).toBeGreaterThan(0);
  expect(geometry.attributes.position.array.length).toBe(0);
  expect(geometry.index!.array.length).toBe(0);
});
