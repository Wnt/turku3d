import './fleet-assets.fixture';
import { expect, test, vi } from 'vitest';
import * as THREE from 'three/webgpu';
import { ParkedCarVisuals } from '../../src/actors/car';
import { draftManifest } from '../../src/world/manifest';
import { GraphicsPreparation } from '../../src/render/graphicsPreparation';

test('each loaded design prepares one detached driven-car actor ahead of any takeover', async () => {
  const scene = new THREE.Scene(), manifest = draftManifest('http://localhost/');
  const car = { object_id: 'one', model: 'corolla-e180', color: 'blue', x: 1, y: 2, z: 3, yaw: 0,
    width: 2, length: 4, height: 1.5, clear_front_m: 10, clear_rear_m: 10 };
  const prepared: THREE.Object3D[] = [];
  const visuals = new ParkedCarVisuals(scene, vi.fn(async (model: THREE.Object3D) => { prepared.push(model); }));
  manifest.vehicles = [car, { ...car, object_id: 'two' }]; await visuals.load(manifest);
  await new Promise(resolve => setTimeout(resolve, 0));
  const keepers = prepared.filter(m => m.name === 'keeper/corolla-e180');
  expect(keepers).toHaveLength(1);
  expect(keepers[0].parent).toBeNull();
  expect(keepers[0].userData.preparesDetached).toBe(true);
  // The keeper is the same kind of draw a takeover adds: a plain Mesh with the shared fleet material.
  visuals.takeOver('one');
  const driven = scene.getObjectByName('driven/one') as THREE.Mesh;
  expect((driven as unknown as THREE.InstancedMesh).isInstancedMesh).toBeFalsy();
  expect(driven.material).toBe((keepers[0] as THREE.Mesh).material);
  expect(driven.geometry).toBe((keepers[0] as THREE.Mesh).geometry);
  // A second load of the same design adds no keeper.
  await visuals.load(manifest); await new Promise(resolve => setTimeout(resolve, 0));
  expect(prepared.filter(m => m.name.startsWith('keeper/'))).toHaveLength(1);
});

test('the graphics queue prepares detached keepers after the menu, and still skips models that left', async () => {
  const scene = new THREE.Scene(), done: string[] = [];
  const graphics = new GraphicsPreparation(scene, model => { done.push(model.name); }, release => release(), async () => {});
  const keeper = new THREE.Object3D(); keeper.name = 'keeper'; keeper.userData.preparesDetached = true;
  const gone = new THREE.Object3D(); gone.name = 'gone';
  graphics.defer(keeper, () => {}); graphics.defer(gone, () => {});
  graphics.flush(error => { throw error; });
  await new Promise(resolve => setTimeout(resolve, 10));
  expect(done).toEqual(['keeper']);
});
