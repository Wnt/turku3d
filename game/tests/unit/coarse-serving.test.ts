import { test, expect, vi } from 'vitest';
import * as THREE from 'three/webgpu';
import { CoarseTiles, cellsOf } from '../../src/world/coarseTiles';
import type { Manifest } from '../../src/world/manifest';

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
