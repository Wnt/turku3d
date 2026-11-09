import { afterEach, expect, test, vi } from 'vitest';
import { REGIONS, loadWalkerAsset, prefetchWalkerAsset } from '../../src/world/walkers/asset';

/** A valid walker asset with no variants and no atlas. */
function emptyAsset() {
  const meta = new TextEncoder().encode(JSON.stringify({ format: 'turku3d-walkers', version: 3, fps: 30, atlasFormat: 'albedo-trim',
    vat: { offset: 0, length: 0, width: 0, height: 0, format: 'rgba16float', normal: { encoding: 'spherical-fibonacci', directions: 1 } },
    regions: REGIONS, variants: [] }).padEnd(4 * Math.ceil(400 / 4)));
  const buffer = new ArrayBuffer(12 + meta.length);
  new Uint32Array(buffer, 0, 3).set([0x4b4c5754, 3, meta.length]);
  new Uint8Array(buffer, 12).set(meta);
  return buffer;
}
afterEach(() => vi.unstubAllGlobals());

test('a prefetched walker asset is taken by the next load once; a later load fetches again', async () => {
  const fetches: (RequestInit | undefined)[] = [];
  vi.stubGlobal('fetch', vi.fn(async (_url: string, init?: RequestInit) => { fetches.push(init); return new Response(emptyAsset()); }));
  prefetchWalkerAsset('/w1.bin');
  prefetchWalkerAsset('/w1.bin');
  expect(fetches).toHaveLength(1);
  expect(fetches[0]?.priority).toBe('low'); // the start tiles' requests go first
  expect((await loadWalkerAsset('/w1.bin')).fps).toBe(30);
  expect(fetches).toHaveLength(1);
  await loadWalkerAsset('/w1.bin');
  expect(fetches).toHaveLength(2);
});

test('a failed prefetch is fetched again by the load that takes it', async () => {
  let n = 0;
  vi.stubGlobal('fetch', vi.fn(async () => (++n === 1 ? new Response('', { status: 503 }) : new Response(emptyAsset()))));
  prefetchWalkerAsset('/w2.bin');
  expect((await loadWalkerAsset('/w2.bin')).variants).toEqual([]);
  expect(n).toBe(2);
});
