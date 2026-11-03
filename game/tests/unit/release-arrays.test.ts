import { expect, test } from 'vitest';
import * as THREE from 'three/webgpu';
import { arraysReleased, releaseRenderArrays, releaseTileArrays } from '../../src/world/releaseArrays';
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

test('uploaded render-only arrays shrink to empty arrays of their type; positions, indices and counts stay', () => {
  const geometry = facade(), count = geometry.attributes.position.count;
  const before = { normal: geometry.attributes.normal.array.byteLength, uv: geometry.attributes.uv.array.byteLength };
  const bytes = releaseRenderArrays(geometry, uploadedAll);
  expect(bytes).toBe(before.normal + before.uv + count * 8 * 4);
  expect(geometry.attributes.normal.array).toBeInstanceOf(Float32Array);
  expect(geometry.attributes.normal.array.length).toBe(0);
  expect(geometry.attributes.normal.count).toBe(count);
  expect((geometry.attributes._kit_wall as THREE.InterleavedBufferAttribute).count).toBe(count);
  expect(geometry.attributes.position.array.length).toBe(count * 3);
  expect(geometry.index!.array.length).toBeGreaterThan(0);
  expect(arraysReleased(geometry)).toBe(true);
  expect(releaseRenderArrays(geometry, uploadedAll)).toBe(0); // once
  // Nothing is released before its GPU buffer exists.
  const fresh = facade();
  expect(releaseRenderArrays(fresh, () => false)).toBe(0);
  expect(fresh.attributes.normal.array.length).toBeGreaterThan(0);
});

test('a committed tile releases its own drawn geometry and detached batch sources, not shared or prop geometry', () => {
  const model = new THREE.Group(), material = new THREE.MeshStandardMaterial();
  const own = new THREE.Mesh(facade(), material); own.userData.layer = 'ground'; model.add(own);
  const prop = new THREE.Mesh(facade(), material); prop.userData = { layer: 'structures', prop_type: 'pole' }; model.add(prop);
  const shared = new THREE.Mesh(facade(), material); retainGeometry(shared.geometry); shared.userData.layer = 'structures'; model.add(shared);
  const merged = [0, 1].map(x => { const m = new THREE.Mesh(facade(), new THREE.MeshStandardMaterial()); m.position.x = x * 2; m.userData.layer = 'buildings'; model.add(m); return m; });
  const sameMaterial = new THREE.MeshStandardMaterial(); for (const m of merged) m.material = sameMaterial;
  for (const step of staticBatchSteps(model)) void step;
  releaseTileArrays(model, uploadedAll);
  expect(own.geometry.attributes.normal.array.length).toBe(0);
  expect(prop.geometry.attributes.normal.array.length).toBeGreaterThan(0);
  expect(shared.geometry.attributes.normal.array.length).toBeGreaterThan(0);
  for (const source of merged) expect(source.geometry.attributes.position.array.length).toBe(0); // never drawn again
});

test('a released building template is read afresh for a new tile and kept while an unfinished tile holds it', async () => {
  const id = 'b'.repeat(64), base = new URL('http://localhost/coarse/t0_0_0/');
  const data = (): BuildingPlacements => ({ version: 1, models: [{ id, uri: `../shared/${id}.glb`, bounds: { min: [-1, 0, -1], max: [1, 1, 1] } }],
    placements: [{ id: 'p', model: id, position: [0, 0, 0], viewDistance: 300 }] });
  let loads = 0;
  const cache = new BuildingModelCache(async () => { loads++; const root = new THREE.Group(); root.add(new THREE.Mesh(facade(), new THREE.MeshStandardMaterial())); return root; },
    () => {}, new PreparationBudget(() => 0, () => Promise.resolve()));
  const a = new THREE.Group(), b = new THREE.Group(), c = new THREE.Group();
  await cache.attach(a, data(), base);
  await cache.attach(b, data(), base);
  cache.markCommitted(a);
  expect(cache.releaseCommitted(g => releaseRenderArrays(g, uploadedAll))).toBe(0); // b is still installing
  cache.markCommitted(b);
  expect(cache.releaseCommitted(g => releaseRenderArrays(g, uploadedAll))).toBeGreaterThan(0);
  expect(loads).toBe(1);
  await cache.attach(c, data(), base); // a new tile cannot clone released arrays: fresh template
  expect(loads).toBe(2);
  const mesh = c.children[0].children[0].children[0] as THREE.Mesh;
  expect(mesh.geometry.attributes.normal.array.length).toBeGreaterThan(0);
});
