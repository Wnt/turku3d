import { expect, test, vi } from 'vitest';
import * as THREE from 'three/webgpu';
import { GLTFLoader, type GLTF } from 'three/addons/loaders/GLTFLoader.js';
import { ParkedCarVisuals } from '../../src/actors/car';
import { draftManifest } from '../../src/world/manifest';

test('new and expanded parked batches stay hidden until preparation completes, retaining the old draw on failure', async () => {
  const asset = new THREE.Group(); asset.add(new THREE.Mesh(new THREE.BoxGeometry(), new THREE.MeshStandardMaterial()));
  const load = vi.spyOn(GLTFLoader.prototype, 'loadAsync').mockResolvedValue({ scene: asset } as GLTF);
  const scene = new THREE.Scene(), manifest = draftManifest('http://localhost/');
  manifest.vehicle_models = { 'sedan/blue': { url: 'sedan.glb', sha256: 'same', bytes: 1 } };
  const car = { object_id: 'one', model: 'sedan', color: 'blue', x: 1, y: 2, z: 3, yaw: 0,
    width: 2, length: 4, height: 1.5, clear_front_m: 10, clear_rear_m: 10 };
  let release!: () => void, reject!: (error: Error) => void;
  const prepare = vi.fn((mesh: THREE.Object3D) => {
    expect(mesh.parent).toBeNull(); expect(mesh.castShadow && mesh.receiveShadow).toBe(true);
    return new Promise<void>((resolve, fail) => { release = resolve; reject = fail; });
  });
  const visuals = new ParkedCarVisuals(scene, prepare);
  const settle = async () => { for (let i = 0; i < 8; i++) await Promise.resolve(); };
  try {
    manifest.vehicles = [car];
    const initial = visuals.load(manifest); await settle();
    expect(prepare).toHaveBeenCalledOnce(); expect(scene.children).toHaveLength(0);
    release(); await initial;
    const old = scene.children[0] as THREE.InstancedMesh;
    manifest.vehicles.push({ ...car, object_id: 'two' }); await visuals.load(manifest);
    expect(scene.children).toEqual([old]); expect(prepare).toHaveBeenCalledOnce();
    manifest.vehicles = Array.from({ length: 17 }, (_, i) => ({ ...car, object_id: String(i) }));
    const failed = visuals.load(manifest); await settle();
    expect(scene.children).toEqual([old]);
    const pending = prepare.mock.calls.at(-1)![0] as THREE.InstancedMesh, dispose = vi.spyOn(pending, 'dispose');
    reject(new Error('GPU preparation failed'));
    await expect(failed).rejects.toThrow('GPU preparation failed');
    expect(dispose).toHaveBeenCalledOnce(); expect(scene.children).toEqual([old]);
    const replaced = visuals.load(manifest); await settle(); release(); await replaced;
    expect(scene.children).toHaveLength(1); expect(scene.children[0]).not.toBe(old);
    expect(old.parent).toBeNull(); expect(load).toHaveBeenCalledOnce();
  } finally { load.mockRestore(); }
});
