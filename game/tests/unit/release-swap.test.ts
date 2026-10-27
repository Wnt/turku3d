// A new tile release applied in place (world/releaseSwap.ts) and the collision reload it drives.
import { test, expect, vi } from 'vitest';
import { releaseDelta, swapRelease } from '../../src/world/releaseSwap';
import { CollisionStreamer } from '../../src/world/collisionStreaming';
import type { Manifest } from '../../src/world/manifest';

const entry = (sha: string, url = `${sha}.col`) => ({ url, sha256: sha, bytes: 4, tris: 1, h: [0, 1] as [number, number] });

function manifest(visual: Record<string, string>, collision: Record<string, string>, tileset = 'ts1'): Manifest {
  return { visual: Object.fromEntries(Object.entries(visual).map(([id, sha]) => [id, entry(sha)])),
    collision: Object.fromEntries(Object.entries(collision).map(([id, sha]) => [id, entry(sha)])),
    tileset: { url: 'tileset.json', sha256: tileset, bytes: 1 }, baseUrl: 'http://x/tiles/' } as unknown as Manifest;
}

test('the delta names changed and added tiles, removed collision tiles and a changed tileset; drafts are ignored', () => {
  const old = manifest({ t0_0_0: 'a', t0_0_1: 'b' }, { c_0_0: 'x', c_0_1: 'y', c_0_2: 'z' });
  old.collision.c_9_9 = entry('draft', 'http://localhost:5171/coarse/t0_2_2/col/c_9_9.col');
  const fresh = manifest({ t0_0_0: 'a', t0_0_1: 'B', t0_1_0: 'c' }, { c_0_0: 'x', c_0_1: 'Y', c_1_0: 'n' }, 'ts2');
  expect(releaseDelta(old, fresh)).toEqual({ visual: ['t0_0_1', 't0_1_0'], collision: ['c_0_1', 'c_1_0'], removed: ['c_0_2'], tileset: true });
  expect(releaseDelta(old, manifest({ t0_0_0: 'a', t0_0_1: 'b' }, { c_0_0: 'x', c_0_1: 'y', c_0_2: 'z' })))
    .toEqual({ visual: [], collision: [], removed: [], tileset: false });
});

test('a swap updates the shared manifest in place, reloads collision and the tileset, then retires drafts', async () => {
  const live = manifest({ t0_0_0: 'a' }, { c_0_0: 'x', c_0_1: 'y' });
  live.collision.c_9_9 = entry('draft', 'http://localhost:5171/coarse/t0_2_2/col/c_9_9.col');
  const collision = live.collision;
  const fresh = manifest({ t0_0_0: 'A', t0_1_0: 'n' }, { c_0_0: 'X', c_1_0: 'n' }, 'ts2');
  const order: string[] = [];
  const host = {
    manifest: live, manifestUrl: '/tiles/manifest.json', tilesetUrl: '/tiles/tileset.json',
    load: vi.fn(async () => structuredClone(fresh)),
    tiles: { reload: vi.fn(async (url: string) => { order.push(`reload ${url}`); }) },
    streamer: { refresh: vi.fn((ids: Iterable<string>) => { order.push(`refresh ${[...ids]}`); }) },
    unloadCollision: vi.fn((id: string) => { order.push(`unload ${id}`); }),
    coarse: { release: vi.fn((ids: Iterable<string>) => { order.push(`release ${[...ids]}`); }), held: () => new Set<string>() },
  };
  const delta = await swapRelease(host);
  expect(delta.visual).toEqual(['t0_0_0', 't0_1_0']);
  expect(live.collision).toBe(collision); // SafeReset and CoarseTiles hold this object
  expect(Object.keys(live.collision).sort()).toEqual(['c_0_0', 'c_1_0', 'c_9_9']);
  expect(live.collision.c_0_0.sha256).toBe('X');
  expect(live.visual.t0_1_0.sha256).toBe('n');
  expect(live.tileset?.sha256).toBe('ts2');
  expect(live.baseUrl).toBe('http://x/tiles/');
  expect(order).toEqual(['unload c_0_1', 'refresh c_0_0,c_1_0', 'reload /tiles/tileset.json', 'release t0_0_0,t0_1_0']);
});

test('a cell a drawn draft holds keeps the draft collision; the draft gets the release entry to return later', async () => {
  const live = manifest({ t0_0_0: 'a' }, { c_0_0: 'x' });
  const draft = entry('draft', 'http://localhost:5171/coarse/t0_2_2/col/c_8_8.col');
  live.collision.c_8_8 = draft;
  const fresh = manifest({ t0_0_0: 'a' }, { c_0_0: 'x', c_8_8: 'margin' });
  const refresh = vi.fn(), release = vi.fn();
  await swapRelease({ manifest: live, manifestUrl: 'm', tilesetUrl: 't', tiles: { reload: async () => {} }, streamer: { refresh },
    unloadCollision: () => {}, load: async () => fresh, coarse: { release, held: () => new Set(['c_8_8']) } });
  expect(live.collision.c_8_8).toBe(draft);
  expect(refresh).toHaveBeenCalledWith([]);
  expect(release.mock.calls[0][1].c_8_8.sha256).toBe('margin');
});

test('a collision-only release does not reload the visual tileset', async () => {
  const live = manifest({ t0_0_0: 'a' }, { c_0_0: 'x' });
  const tiles = { reload: vi.fn(async () => {}) };
  await swapRelease({ manifest: live, manifestUrl: 'm', tilesetUrl: 't', tiles, streamer: { refresh: () => {} },
    unloadCollision: () => {}, load: async () => manifest({ t0_0_0: 'a' }, { c_0_0: 'X' }) });
  expect(tiles.reload).not.toHaveBeenCalled();
});

test('refresh reloads only loaded tiles, in place, and clears failures', async () => {
  const collision: Record<string, { url: string; bytes: number }> = { c_0_0: { url: 'a.col', bytes: 4 }, c_0_1: { url: 'b.col', bytes: 4 } };
  const loaded = new Map<string, unknown>([['c_0_0', {}]]);
  const physics = { loaded, loadTile: vi.fn(async (id: string) => { loaded.set(id, {}); }), unloadTile: vi.fn() };
  vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, arrayBuffer: async () => new ArrayBuffer(4) })));
  const s = new CollisionStreamer({ collision, baseUrl: 'http://x/' } as never, physics as never, 100, 50);
  s.refresh(['c_0_0', 'c_0_1', 'c_7_7']);
  await new Promise(r => setTimeout(r, 0));
  expect(physics.loadTile).toHaveBeenCalledTimes(1);
  expect(physics.loadTile.mock.calls[0][0]).toBe('c_0_0');
  expect(physics.unloadTile).not.toHaveBeenCalled(); // the worker replaces the colliders in the same message
  vi.unstubAllGlobals();
});
