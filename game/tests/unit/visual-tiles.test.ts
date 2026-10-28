import { expect, test, vi } from 'vitest';
import * as THREE from 'three/webgpu';
import { VisualTiles } from '../../src/world/tiles';

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
