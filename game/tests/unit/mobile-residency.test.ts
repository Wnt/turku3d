import { test, expect } from 'vitest';
import * as THREE from 'three/webgpu';
import { CoarseTiles } from '../../src/world/coarseTiles';
import type { Manifest } from '../../src/world/manifest';

test('mobile surface reserve does not pin a distant detailed model after leaving the menu', async () => {
  const manifest = { visual: {}, collision: {} } as unknown as Manifest;
  const scene = new THREE.Scene(), far = new THREE.Group(), near = new THREE.Group();
  scene.add(far, near);
  const fetch: typeof globalThis.fetch = async () => Response.json({ state: 'queued' }, { status: 202 });
  const coarse = new CoarseTiles({ scene, manifest, detailRadius: 80, pageUrl: 'http://127.0.0.1:5171/',
    renderer: {} as THREE.WebGPURenderer, fetch, loadModel: async () => new THREE.Group(),
    unloadCollision: () => {}, refreshCollision: () => {}, checkCollision: async () => {},
    previewCollision: async () => new THREE.Group() });
  const old = { state: 'draft' as const, model: far, nextPoll: Infinity, resident: true, collision: new Map() };
  coarse.entries.set('t0_-1_1', old);
  coarse.entries.set('t0_0_1', { ...old, model: near, collision: new Map() });
  coarse.update(171, 253, 0);
  await new Promise(resolve => setTimeout(resolve, 0));
  expect(far.parent).toBeNull();
  expect(near.parent).toBe(scene);
  expect(coarse.entries.get('t0_-1_1')).not.toBe(old);
  expect(coarse.entries.has('t0_-1_1')).toBe(true); // It still belongs to the lightweight surface reserve.
});
