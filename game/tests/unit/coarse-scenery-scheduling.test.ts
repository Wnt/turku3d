import { test, expect, vi } from 'vitest';
import * as THREE from 'three/webgpu';
import { CoarseTiles } from '../../src/world/coarseTiles';
import type { Manifest } from '../../src/world/manifest';

test('cached screenshot scenery fills free slots between normal one-second world scans', async () => {
  const rows = Array.from({ length: 40 }, (_, i) => ({ tile: `t0_${-6 + Math.floor(i / 4)}_${i % 4}`,
    key: `t0_${-6 + Math.floor(i / 4)}_${i % 4}`, h: [0, 107], source_sha256: 'base' }));
  const loadModel = vi.fn(async () => new THREE.Group());
  const fetch = vi.fn(async (url: string) => {
    const pathname = new URL(url).pathname;
    if (pathname.endsWith('/health')) return new Response(JSON.stringify({ state: 'idle' }));
    if (pathname.endsWith('/scenery')) return new Response(JSON.stringify({ tiles: rows, missing: [] }));
    if (pathname.includes('/scenery/')) {
      const tile = pathname.split('/').at(-1)!.replace('.json', '');
      return new Response(JSON.stringify({ coarse: true, tile, pass_kind: 'full',
        visual: { [tile]: { url: '../scenery/derived.glb', sha256: tile } } }));
    }
    return new Response('{}', { status: 202 });
  });
  const coarse = (await CoarseTiles.start({ scene: new THREE.Scene(), manifest: { visual: {}, collision: {} } as Manifest,
    renderer: {} as THREE.WebGPURenderer, pageUrl: 'https://wnt.github.io/turku-game/game/', fetch: fetch as typeof globalThis.fetch,
    loadModel, checkCollision: async () => 0, unloadCollision: () => {}, refreshCollision: () => {} }))!;
  const settle = async () => { for (let i = 0; i < 30; i++) await Promise.resolve(); };
  coarse.screenshotScenery.setRadius(6000, { x: 3751, y: 164, z: 430, dx: -1, dz: 0, halfAngle: .8 });
  await settle();
  coarse.update(3751, 430, 1000); await settle();
  expect(loadModel).toHaveBeenCalledTimes(16);
  coarse.update(3751, 430, 1016); await settle();
  expect(loadModel).toHaveBeenCalledTimes(32);
});
