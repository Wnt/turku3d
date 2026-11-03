import { expect, test, vi } from 'vitest';
import * as THREE from 'three/webgpu';
import { BuildingModelCache, updateBuildingDistances, type BuildingPlacements } from '../../src/world/buildingPlacements';
import { disposeModelResources } from '../../src/world/disposeModel';
import { SharedMaterials } from '../../src/render/sharedMaterials';
import { CoarseTiles, cellsOf } from '../../src/world/coarseTiles';
import type { Manifest } from '../../src/world/manifest';

const id = 'a'.repeat(64), base = new URL('http://localhost/coarse/t0_0_0/');
const data = (): BuildingPlacements => ({ version: 1,
  models: [{ id, uri: `../shared/${id}.glb`, bounds: { min: [-10, 0, -20], max: [10, 30, 20] },
    occluders: { version: 1, provenance: 'lod2-inset-boxes-v1', boxes: [] } }],
  placements: [{ id: 'building', model: id, position: [250, 0, 120], viewDistance: 300 }] });

function prototype() {
  const root = new THREE.Group(), geometry = new THREE.BoxGeometry();
  geometry.setAttribute('_kit_test', new THREE.Float32BufferAttribute(new Float32Array(geometry.attributes.position.count), 1));
  const mesh = new THREE.Mesh(geometry, new THREE.MeshStandardMaterial({ map: new THREE.Texture() }));
  mesh.userData = { layer: 'buildings', building_id: 'building' }; root.add(mesh);
  return { root, mesh };
}

test('content models decode once across generation URLs and survive either placement retiring', async () => {
  const { root, mesh } = prototype(), load = vi.fn(async () => root), shared = new SharedMaterials();
  const cache = new BuildingModelCache(load, root => disposeModelResources(root, shared));
  const first = new THREE.Group(), second = new THREE.Group();
  const [releaseFirst, releaseSecond] = await Promise.all([
    cache.attach(first, data(), base), cache.attach(second, data(), new URL('http://localhost/coarse/_v/new/t0_1_0/')),
  ]);
  expect(load).toHaveBeenCalledOnce();
  const a = first.children[0].children[0].children[0] as THREE.Mesh;
  const b = second.children[0].children[0].children[0] as THREE.Mesh;
  expect(a.geometry).toBe(b.geometry); expect(a.geometry).toBe(mesh.geometry);
  expect(a.material).not.toBe(b.material);
  expect(a.userData).toEqual(mesh.userData);
  expect(first.children[0].userData.occluders).toEqual(data().models[0].occluders);
  const disposed = vi.fn(); mesh.geometry.addEventListener('dispose', disposed);
  disposeModelResources(first, shared); releaseFirst(); await Promise.resolve();
  expect(disposed).not.toHaveBeenCalled();
  disposeModelResources(second, shared); releaseSecond(); await Promise.resolve();
  expect(disposed).toHaveBeenCalledOnce();
  releaseSecond(); expect(disposed).toHaveBeenCalledOnce();
});

test('placement culling uses model extents and preserves seam-crossing geometry', async () => {
  const shared = new SharedMaterials(), cache = new BuildingModelCache(async () => prototype().root, root => disposeModelResources(root, shared));
  const root = new THREE.Group(), release = await cache.attach(root, data(), base);
  updateBuildingDistances(root, 560, 120); expect(root.children[0].visible).toBe(true);
  updateBuildingDistances(root, 561, 120); expect(root.children[0].visible).toBe(false);
  updateBuildingDistances(root, 245, 120); expect(root.children[0].visible).toBe(true);
  root.updateMatrixWorld(true);
  expect(new THREE.Vector3().setFromMatrixPosition(root.children[0].matrixWorld).toArray()).toEqual([250, 0, 120]);
  disposeModelResources(root, shared); release();
});

test('failed model loads release successful siblings and can retry; malformed placements do not load', async () => {
  const shared = new SharedMaterials(), release = vi.fn(root => disposeModelResources(root, shared));
  let fail = true;
  const cache = new BuildingModelCache(async url => {
    if (url.includes('b'.repeat(64)) && fail) throw new Error('missing model');
    return prototype().root;
  }, release);
  const input = data(); input.models.push({ ...input.models[0], id: 'b'.repeat(64), uri: `../shared/${'b'.repeat(64)}.glb` });
  input.placements.push({ ...input.placements[0], id: 'other', model: 'b'.repeat(64) });
  const root = new THREE.Group();
  await expect(cache.attach(root, input, base)).rejects.toThrow('missing model');
  expect(root.children).toHaveLength(0); expect(release).toHaveBeenCalledOnce();
  fail = false; const done = await cache.attach(root, input, base);
  expect(root.children).toHaveLength(2); disposeModelResources(root, shared); done();
  input.placements[0].position[0] = NaN;
  await expect(cache.attach(new THREE.Group(), input, base)).rejects.toThrow('Invalid building placement');
});

test('a building-only revision swaps even when terrain and collision hashes do not change', async () => {
  const tile = 't0_0_0', scene = new THREE.Scene();
  const fragment = { coarse: true as const, tile, pass_kind: 'full' as const, buildingPlacements: data(),
    visual: { [tile]: { url: 'vis.glb', sha256: 'terrain' } },
    collision: Object.fromEntries(cellsOf(tile).map(cid => [cid, { url: `${cid}.col`, sha256: cid, bytes: 4 }])) };
  const checked = vi.fn(async () => {}), loaded: string[] = [];
  const coarse = new CoarseTiles({ scene, renderer: {} as THREE.WebGPURenderer,
    manifest: { collision: {} } as Manifest, pageUrl: 'http://localhost/',
    checkCollision: checked, refreshCollision: vi.fn(), unloadCollision: vi.fn(),
    loadModel: async url => { loaded.push(url); return new THREE.Group(); },
    fetch: (async url => String(url).endsWith('.col') ? new Response(new Uint8Array(4)) : Response.json(String(url).includes('/want') ? {} : fragment)) as typeof fetch });
  coarse.preload([{ x: 20, z: 20 }], 0);
  const settle = async () => { for (let i = 0; i < 15; i++) await new Promise(resolve => setTimeout(resolve, 0)); };
  coarse.update(20, 20, 0); await settle();
  const first = coarse.entries.get(tile)!.model!;
  expect(first.children.some(c => c.userData.archetype === id)).toBe(true);
  fragment.buildingPlacements.models[0].id = 'b'.repeat(64);
  fragment.buildingPlacements.models[0].uri = `../shared/${'b'.repeat(64)}.glb`;
  fragment.buildingPlacements.placements[0].model = 'b'.repeat(64);
  coarse.update(20, 20, 100000); await settle();
  expect(coarse.entries.get(tile)!.model).not.toBe(first);
  expect(first.parent).toBeNull(); expect(checked).toHaveBeenCalledTimes(16);
  expect(loaded.filter(url => url.includes('/shared/'))).toHaveLength(2);
});
