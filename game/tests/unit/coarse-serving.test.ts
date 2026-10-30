import { test, expect, vi } from 'vitest';
import * as THREE from 'three/webgpu';
import { CollisionStreamer } from '../../src/world/collisionStreaming';
import { CoarseTiles, cellsOf } from '../../src/world/coarseTiles';
import type { Manifest, ManifestEntry } from '../../src/world/manifest';

test('stale full stays visible until current full is prepared, with unchanged collision retained', async () => {
  const id = 't0_2_0', manifest = { visual: {}, collision: {} } as unknown as Manifest;
  const scene = new THREE.Scene(), previous = new THREE.Group(), latest = new THREE.Group();
  const checkCollision = vi.fn(async () => 2), refreshCollision = vi.fn();
  let stale = true, upgraded = false, release!: (model: THREE.Object3D) => void;
  const fetch = (async (url: string) => {
    const name = new URL(url).pathname;
    if (name === `/coarse/${id}/manifest.json`) return new Response(JSON.stringify({
      coarse: true, tile: id, pass_kind: 'full', stale,
      visual: { [id]: { url: `vis.${upgraded ? 'latest' : 'previous'}.glb`, sha256: upgraded ? 'latest' : 'previous' } },
      collision: Object.fromEntries(cellsOf(id).map(cid => [cid, {url: `col/${cid}.col`, sha256: 'same', bytes: 4}])),
    }));
    if (name.endsWith('.col')) return new Response(new ArrayBuffer(4));
    if (name.endsWith('/want')) return new Response('{}');
    return new Response('{}', {status: 404});
  }) as typeof globalThis.fetch;
  const coarse = new CoarseTiles({scene, manifest, renderer: {} as THREE.WebGPURenderer,
    pageUrl: 'http://127.0.0.1:5171/', fetch, checkCollision, refreshCollision,
    unloadCollision: vi.fn(), loadModel: async () => upgraded ? new Promise(r => { release = r; }) : previous});
  const settle = async () => { for (let i = 0; i < 6; i++) await new Promise(r => setTimeout(r, 0)); };
  coarse.update(600, 100, 0); await settle();
  const entry = coarse.entries.get(id)!;
  expect(entry.stale).toBe(true); expect(entry.staleSince).toBeTypeOf('number');
  expect(previous.parent).toBe(scene);
  stale = false; // Revalidation may produce identical bytes; freshness must still clear.
  coarse.update(600, 100, 3000); await settle();
  expect(entry.stale).toBe(false); expect(entry.staleSince).toBeUndefined();
  expect(entry.staleSeconds).toBeGreaterThanOrEqual(0);
  upgraded = true;
  coarse.update(600, 100, 6000); await settle();
  expect(previous.parent).toBe(scene); expect(latest.parent).toBeNull();
  latest.addEventListener('added', () => expect(previous.parent).toBeNull());
  release(latest); await settle();
  expect(latest.parent).toBe(scene); expect(refreshCollision).not.toHaveBeenCalled();
  expect(checkCollision).toHaveBeenCalledTimes(16);
});


test('retained neighbour registers and loads its collision, removing the reported west edge', async () => {
  const id = 't0_-2_1', manifest = { visual: {}, collision: {}, baseUrl: 'http://127.0.0.1:5171/' } as unknown as Manifest;
  const scene = new THREE.Scene(), previous = new THREE.Group(), latest = new THREE.Group();
  const loaded = new Map<string, unknown>([['c_-4_7', {}]]), known = new Set<string>();
  const physics = { loaded, loadTile: vi.fn(async (cid: string) => { loaded.set(cid, {}); }),
    unloadTile: vi.fn((cid: string) => loaded.delete(cid)),
    setTileLoading: () => {}, setKnown: (add: string[], remove: string[]) => { add.forEach(cid => known.add(cid)); remove.forEach(cid => known.delete(cid)); } };
  const streamer = new CollisionStreamer(manifest, physics as never, 80);
  const checkCollision = vi.fn(async () => 2);
  let phase = 'retained', release!: (model: THREE.Object3D) => void;
  const fetch = (async (url: string) => {
    const name = new URL(url).pathname;
    if (name === `/coarse/${id}/collision.json` || name === `/coarse/${id}/manifest.json`) return new Response(JSON.stringify({
      coarse: true, tile: id, pass_kind: 'full', stale: phase === 'retained', generation: phase,
      visual: { [id]: { url: `vis.${phase}.glb`, sha256: phase } },
      collision: Object.fromEntries(cellsOf(id).map(cid => [cid, {url: `col/${cid}.${phase}.col`, sha256: phase, bytes: 4}])),
    }));
    if (name.endsWith('.col')) return new Response(new ArrayBuffer(4));
    if (name.endsWith('/want')) return new Response('{}');
    return new Response('{}', {status: 404});
  }) as typeof globalThis.fetch;
  const coarse = new CoarseTiles({scene, manifest, renderer: {} as THREE.WebGPURenderer,
    pageUrl: manifest.baseUrl, fetch, checkCollision, refreshCollision: ids => streamer.refresh(ids),
    retainCollision: (cid, entry, buffer) => streamer.retain(cid, entry, buffer), unloadCollision: physics.unloadTile,
    loadModel: async () => phase === 'retained' ? previous : new Promise(r => { release = r; })});
  const settle = async () => { for (let i = 0; i < 6; i++) await new Promise(r => setTimeout(r, 0)); };
  previous.addEventListener('added', () => expect(Object.keys(manifest.collision)).toHaveLength(16));
  coarse.update(-245, 478, 0); await settle();
  expect(previous.parent).toBe(scene);
  expect(coarse.entries.get(id)?.generation).toBe('retained');
  for (let i = 0; i < 10; i++) { streamer.update(-245, 478, true); await settle(); }
  expect(known.has('c_-5_7')).toBe(true);
  expect(loaded.has('c_-5_7')).toBe(true);
  expect(streamer.trueEdges()).not.toContainEqual([-4, 7, 1]);
  expect(physics.loadTile).toHaveBeenCalled();
  expect(checkCollision).toHaveBeenCalledTimes(16);
  phase = 'current';
  coarse.update(-245, 478, 3000); await settle();
  expect(previous.parent).toBe(scene);
  expect(manifest.collision['c_-5_7'].sha256).toBe('retained');
  latest.addEventListener('added', () => {
    expect(previous.parent).toBeNull();
    expect(manifest.collision['c_-5_7'].sha256).toBe('current');
  });
  release(latest); await settle();
  expect(latest.parent).toBe(scene);
  expect(coarse.entries.get(id)?.generation).toBe('current');
  expect(loaded.has('c_-5_7')).toBe(true);
  expect(physics.unloadTile).not.toHaveBeenCalled();
});

test('a retained response without collision cannot expose new geometry or replace a resident pair', async () => {
  const id = 't0_2_0', manifest = { visual: {}, collision: {} } as unknown as Manifest;
  const scene = new THREE.Scene(), model = new THREE.Group();
  let incomplete = true;
  const cells = Object.fromEntries(cellsOf(id).map(cid => [cid, {url: `col/${cid}.col`, sha256: 'old', bytes: 4}])) as Record<string, ManifestEntry>;
  const loadModel = vi.fn(async () => model), unloadCollision = vi.fn();
  const fetch = (async (url: string) => {
    const name = new URL(url).pathname;
    if (name === `/coarse/${id}/manifest.json`) return new Response(JSON.stringify({
      coarse: true, tile: id, stale: true, collision_pending: incomplete,
      visual: { [id]: { url: 'vis.retained.glb', sha256: 'retained' } }, collision: incomplete ? {} : cells,
    }));
    if (name.endsWith('.col')) return new Response(new ArrayBuffer(4));
    if (name.endsWith('/want')) return new Response('{}');
    return new Response('{}', {status: 404});
  }) as typeof globalThis.fetch;
  const coarse = new CoarseTiles({scene, manifest, renderer: {} as THREE.WebGPURenderer, pageUrl: 'http://127.0.0.1:5171/',
    fetch, checkCollision: async () => 2, refreshCollision: vi.fn(), unloadCollision, loadModel});
  const settle = async () => { for (let i = 0; i < 6; i++) await new Promise(r => setTimeout(r, 0)); };
  const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
  try {
    coarse.update(600, 100, 0); await settle();
    expect(model.parent).toBeNull(); expect(loadModel).not.toHaveBeenCalled();
    expect(manifest.collision).toEqual({});
    incomplete = false;
    coarse.update(600, 100, 6000); await settle();
    expect(model.parent).toBe(scene); expect(Object.keys(manifest.collision)).toHaveLength(16);
    incomplete = true;
    coarse.update(600, 100, 9000); await settle();
    expect(model.parent).toBe(scene); expect(Object.keys(manifest.collision)).toHaveLength(16);
    expect(unloadCollision).not.toHaveBeenCalled();
  } finally { warn.mockRestore(); }
});

test('cached collision surfaces are usable while detailed geometry is blocked, then swap without removing support', async () => {
  const id = 't0_2_0', scene = new THREE.Scene(), preview = new THREE.Group(), detailed = new THREE.Group();
  const manifest = { visual: {}, collision: {} } as unknown as Manifest;
  const cells = Object.fromEntries(cellsOf(id).map(cid => [cid, { url: `col/${cid}.col`, bytes: 4, sha256: cid }]));
  let release!: (model: THREE.Object3D) => void;
  const fetch = (async (url: string) => {
    const path = new URL(url).pathname;
    if (path === `/coarse/${id}/collision.json`) return new Response(JSON.stringify({ coarse: true, tile: id,
      pass_kind: 'full', visual: { [id]: { url: 'vis.full.glb', sha256: 'full' } }, collision: cells }));
    if (path.endsWith('.col')) return new Response(new ArrayBuffer(4));
    return new Response('{"state":"queued"}', { status: 202 });
  }) as typeof globalThis.fetch;
  const checkCollision = vi.fn(async () => 1), unloadCollision = vi.fn();
  const loadModel = vi.fn(() => new Promise<THREE.Object3D>(resolve => { release = resolve; }));
  const coarse = new CoarseTiles({ scene, manifest, renderer: {} as THREE.WebGPURenderer,
    pageUrl: 'http://127.0.0.1:5171/', fetch, loadModel, checkCollision, unloadCollision, refreshCollision: vi.fn(),
    previewCollision: async buffers => { expect(buffers.size).toBe(16); expect(checkCollision).toHaveBeenCalledTimes(16); return preview; } });
  const settle = async () => { for (let i = 0; i < 8; i++) await new Promise(resolve => setTimeout(resolve, 0)); };
  coarse.update(600, 100, 0); await settle();
  expect(preview.parent).toBe(scene); expect(Object.keys(manifest.collision)).toHaveLength(16);
  expect(loadModel).not.toHaveBeenCalled();
  coarse.update(600, 100, 3000); await settle();
  expect(loadModel).toHaveBeenCalledOnce(); expect(preview.parent).toBe(scene);
  detailed.addEventListener('added', () => expect(preview.parent).toBeNull());
  release(detailed); await settle();
  expect(detailed.parent).toBe(scene); expect(unloadCollision).not.toHaveBeenCalled();
  expect(coarse.entries.get(id)?.preview).toBeUndefined();
});
