import { afterEach, expect, test, vi } from 'vitest';
import { PerformanceObserver } from 'node:perf_hooks';
import * as THREE from 'three/webgpu';
import type { PlayWorld } from '../../src/play';

afterEach(() => vi.unstubAllGlobals());

test.each([true, false])('startup uploads textures and uses one warmup view (mobile=%s)', async mobile => {
  vi.stubGlobal('PerformanceObserver', PerformanceObserver);
  vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => queueMicrotask(() => callback(0)));
  const { preloadWorld } = await import('../../src/world/preload');
  const scene = new THREE.Scene(), camera = new THREE.PerspectiveCamera();
  camera.position.set(10, 5, 20); camera.rotation.set(0, 1, 0);
  const position = camera.position.clone(), rotation = camera.quaternion.clone();
  const texture = new THREE.DataTexture(new Uint8Array([255, 255, 255, 255]), 1, 1);
  const mesh = new THREE.Mesh(new THREE.PlaneGeometry(), new THREE.MeshBasicMaterial({ map: texture }));
  mesh.position.copy(camera.position).add(camera.getWorldDirection(new THREE.Vector3()).multiplyScalar(5));
  const actor = new THREE.Mesh(new THREE.BoxGeometry(), mesh.material);
  actor.position.set(1000, 0, 0);
  scene.add(mesh, actor);
  let uploaded = false, draws = 0;
  const world = {
    params: { mobile }, scene, camera, spawn: { x: 0, y: 0, z: 0 },
    env: { follow: vi.fn() }, loadingScreen: { update: vi.fn() },
    coarse: { update: vi.fn(), progressAt: () => ({ loaded: 5, total: 5, errors: [] }) },
    streamer: { update: vi.fn(), progressAt: () => ({ loaded: 25, total: 25, errors: [] }) },
    renderer: { backend: { device: { queue: { onSubmittedWorkDone: async () => {} } } },
      info: { memory: { textures: 1 } }, initTexture: () => { uploaded = true; } },
    renderFrame: () => {
      expect(uploaded).toBe(true);
      expect(mesh.frustumCulled).toBe(true);
      expect(actor.frustumCulled).toBe(draws > 0);
      expect(camera.position.equals(position)).toBe(true);
      expect(camera.quaternion.equals(rotation)).toBe(true);
      draws++;
    },
  } as unknown as PlayWorld;
  const prepare = vi.fn(async () => {});
  await preloadWorld(world, { x: 0, z: 0 }, prepare, [actor]);
  expect(prepare).toHaveBeenCalledOnce();
  expect(draws).toBe(9);
  expect(mesh.frustumCulled).toBe(true);
  expect(actor.frustumCulled).toBe(true);
});
