import { expect, test, vi } from 'vitest';
import * as THREE from 'three/webgpu';
import { VisualTiles } from '../../src/world/tiles';
import { FrameRetirement } from '../../src/render/retirement';

test('untracking is immediate but shadow buffers wait for the next complete GPU frame', async () => {
  const queue = new FrameRetirement(), tiles = new VisualTiles(false, release => queue.retire(release));
  const model = new THREE.Group();
  model.add(new THREE.Mesh(new THREE.BoxGeometry(), new THREE.MeshStandardMaterial()));
  tiles.trackModel(model, 'old');
  const proxy = model.children.find(object => object.userData.shadowBatch) as THREE.Mesh;
  const geometry = vi.spyOn(proxy.geometry, 'dispose'), draw = vi.fn();
  proxy.addEventListener('dispose', draw);
  tiles.untrackModel(model, 'old');
  expect(tiles.models.size).toBe(0); expect(proxy.parent).toBeNull();
  expect(geometry).not.toHaveBeenCalled();
  let complete!: () => void;
  const frame = queue.submitted(() => new Promise<void>(resolve => { complete = resolve; }));
  const later = vi.fn(); queue.retire(later);
  expect(geometry).not.toHaveBeenCalled();
  complete(); await frame;
  expect(draw).toHaveBeenCalledOnce(); expect(geometry).toHaveBeenCalledOnce();
  expect(later).not.toHaveBeenCalled();
  await queue.submitted(() => Promise.resolve()); expect(later).toHaveBeenCalledOnce();
});

test('publication preserves warmed shadow flags and excludes derived proxy triangles from residency', () => {
  const tiles = new VisualTiles(), model = new THREE.Group();
  const building = new THREE.Mesh(new THREE.BoxGeometry(), new THREE.MeshStandardMaterial());
  const farTree = new THREE.Mesh(new THREE.BoxGeometry(), new THREE.MeshStandardMaterial());
  farTree.userData.layer = 'vegetation'; model.add(building, farTree);
  tiles.prepareModel(model);
  expect(building.receiveShadow).toBe(true);
  expect(building.castShadow).toBe(false);
  const proxy = model.children.find(object => object.userData.shadowBatch)!;
  expect(proxy).toBeDefined();
  // Vegetation preparation selects shadow settings per LOD before GPU warmup.
  farTree.receiveShadow = false; farTree.castShadow = false;
  const flags = model.children.map(object => [(object as THREE.Mesh).receiveShadow, (object as THREE.Mesh).castShadow]);
  tiles.trackModel(model, 'fixture'); tiles.trackModel(model, 'fixture');
  expect(model.children.map(object => [(object as THREE.Mesh).receiveShadow, (object as THREE.Mesh).castShadow])).toEqual(flags);
  expect(tiles.residentModels).toBe(1); expect(tiles.residentTris).toBe(24);
  tiles.untrackModel(model, 'fixture');
  expect(proxy.parent).toBeNull(); expect(tiles.residentTris).toBe(0);
});

test('cancelled unpublished preparation disposes its shadow resources exactly once', () => {
  const tiles = new VisualTiles(), model = new THREE.Group();
  model.add(new THREE.Mesh(new THREE.BoxGeometry(), new THREE.MeshStandardMaterial()));
  tiles.prepareModel(model); tiles.prepareModel(model);
  const proxy = model.children.find(object => object.userData.shadowBatch) as THREE.Mesh;
  const geometry = vi.spyOn(proxy.geometry, 'dispose'), material = vi.spyOn(proxy.material as THREE.Material, 'dispose');
  tiles.releasePreparedModel(model); tiles.releasePreparedModel(model);
  expect(geometry).toHaveBeenCalledTimes(1); expect(material).toHaveBeenCalledTimes(1);
  expect(tiles.residentModels).toBe(0); expect(tiles.models.size).toBe(0);
});


test('streaming shadow preparation yields before publication and retains its derived geometry', async () => {
  let clock = 0, frames = 0;
  const now = vi.spyOn(performance, 'now').mockImplementation(() => clock += 3);
  vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => { frames++; callback(0); return 1; });
  const tiles = new VisualTiles(), model = new THREE.Group();
  model.add(new THREE.Mesh(new THREE.BoxGeometry(), new THREE.MeshStandardMaterial()));
  await tiles.prepareModelAsync(model);
  expect(frames).toBeGreaterThan(0);
  now.mockRestore();
  const proxy = model.children.find(object => object.userData.shadowBatch)!;
  expect(proxy).toBeDefined();
  tiles.trackModel(model, 'streamed');
  expect(model.children.filter(object => object.userData.shadowBatch)).toEqual([proxy]);
  expect(tiles.residentTris).toBe(12);
  tiles.untrackModel(model, 'streamed');
  expect(proxy.parent).toBeNull();
  vi.unstubAllGlobals();
});
