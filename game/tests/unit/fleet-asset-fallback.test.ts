import { expect, test, vi } from 'vitest';
import * as THREE from 'three/webgpu';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { ParkedCarVisuals } from '../../src/actors/car';
import { AUTHORED_MODELS } from '../../src/actors/fleet/registry';
import { draftManifest } from '../../src/world/manifest';

test('a car model that still fails after the fetch retries drops to the procedural fleet with one warning', async () => {
  const loads = vi.spyOn(GLTFLoader.prototype, 'loadAsync')
    .mockRejectedValue(new Error('fetch for "/assets/cars/x/lod0.glb" responded with 502'));
  const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
  const model = AUTHORED_MODELS[0].id;
  const scene = new THREE.Scene(), manifest = draftManifest('http://localhost/');
  manifest.vehicles = [{ object_id: 'one', model, color: 'blue', x: 1, y: 2, z: 3, yaw: 0,
    width: 2, length: 4.6, height: 1.5, clear_front_m: 10, clear_rear_m: 10 }];
  const visuals = new ParkedCarVisuals(scene);
  await expect(visuals.load(manifest)).resolves.toBeUndefined();
  await visuals.load(manifest); // a later tile load neither refetches nor warns again
  expect(visuals.has('one')).toBe(true);
  expect(scene.getObjectByName(`parked/${model}/0`)).toBeTruthy();
  const fleetWarnings = warn.mock.calls.filter(([m]) => String(m).startsWith('[fleet]') && String(m).includes('procedural'));
  expect(fleetWarnings.map(([m]) => String(m).split(' ')[1].split('/lod')[0])).toEqual([AUTHORED_MODELS[0].assetBase]);
  expect(loads.mock.calls.length).toBeLessThanOrEqual(3);
  loads.mockRestore(); warn.mockRestore();
});
