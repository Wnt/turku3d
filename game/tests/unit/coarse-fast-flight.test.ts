import { expect, test, vi } from 'vitest';
import * as THREE from 'three/webgpu';
import { FLY } from '../../src/gameplay/flyMode';
import { CoarseTiles } from '../../src/world/coarseTiles';
import { aheadTiles, preparationTiles, wantedTiles } from '../../src/world/coarseRegion';
import type { Manifest } from '../../src/world/manifest';

test.each([[1, 0], [-1, 0], [0, 1], [0, -1], [Math.SQRT1_2, Math.SQRT1_2]])(
  'maximum dev flight keeps predictive demand in direction (%s, %s)', async (dx, dz) => {
    const demands: URLSearchParams[] = [];
    const fetch = (async (url: string) => {
      const u = new URL(url);
      if (u.pathname.endsWith('/demand')) {
        demands.push(u.searchParams);
        return new Response('{}');
      }
      return new Response('{"state":"queued"}', { status: 202 });
    }) as typeof globalThis.fetch;
    const coarse = new CoarseTiles({
      scene: new THREE.Scene(), renderer: {} as THREE.WebGPURenderer,
      manifest: { visual: {}, collision: {} } as unknown as Manifest,
      pageUrl: 'https://wnt.github.io/turku-game/game/?dev=1', fetch,
      checkCollision: async () => 0, refreshCollision: vi.fn(), unloadCollision: vi.fn(),
      loadModel: async () => new THREE.Group(),
    });
    for (let second = 0; second <= 4; second++) {
      coarse.update(125 + dx * FLY.fastSpeed * second, 125 + dz * FLY.fastSpeed * second, second * 1000);
      await new Promise(resolve => setTimeout(resolve, 0));
    }
    expect(Math.hypot(coarse.velocity.x, coarse.velocity.z)).toBeGreaterThan(FLY.fastSpeed * 0.9);
    const urgent = demands.at(-1)!.get('urgent')!.split(',');
    const x = 125 + dx * FLY.fastSpeed * 6, z = 125 + dz * FLY.fastSpeed * 6;
    expect(urgent).toContain(`t0_${Math.floor(x / 250)}_${Math.floor(z / 250)}`);
    // Actual relocation must still clear the previous flight direction.
    coarse.update(20000, 20000, 5000);
    await new Promise(resolve => setTimeout(resolve, 0));
    expect(coarse.velocity).toEqual({ x: 0, z: 0 });
  },
);

test('fast flight prepares several upcoming tiles while ordinary movement stays bounded', () => {
  const x = 125, z = 125;
  const forward = (speed: number) => preparationTiles(wantedTiles(x, z), aheadTiles(x, z, speed, 0), x, z, speed, 0);
  expect(forward(FLY.fastSpeed).slice(0, 7)).toEqual(['t0_2_0', 't0_3_0', 't0_4_0', 't0_5_0', 't0_6_0', 't0_7_0', 't0_8_0']);
  expect(forward(FLY.fastSpeed)).toHaveLength(8);
  expect(forward(14)[0]).toBe('t0_2_0');
  expect(forward(0)).toHaveLength(8);
  for (const id of ['t0_0_-2', 't0_0_2', 't0_-2_0', 't0_2_0']) expect(forward(0)).toContain(id);
});


test('stationary surface reserve covers the first north crossing from the office boundary', () => {
  const x = 175, z = 257;
  expect(preparationTiles(wantedTiles(x, z), [], x, z, 0, 0)[0]).toBe('t0_0_-1');
});
