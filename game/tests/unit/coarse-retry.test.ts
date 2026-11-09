import { test, expect, vi } from 'vitest';
import * as THREE from 'three/webgpu';
import { CoarseTiles } from '../../src/world/coarseTiles';
import type { Manifest } from '../../src/world/manifest';

test('transient HTTP errors honor Retry-After, retain resident tiles and resume after a healthy probe', async () => {
  const scene = new THREE.Scene(), model = new THREE.Group();
  scene.add(model);
  let healthy = false;
  const urls: string[] = [];
  const fetch = vi.fn(async (url: string) => {
    urls.push(url);
    if (url.endsWith('/health')) return new Response(JSON.stringify({ state: 'online' }));
    return healthy ? new Response('{}', { status: 202 })
      : new Response('{}', { status: 500, headers: { 'Retry-After': '10' } });
  });
  const coarse = new CoarseTiles({ scene, manifest: { visual: {}, collision: {} } as unknown as Manifest,
    pageUrl: 'https://wnt.github.io/turku3d/', fetch: fetch as unknown as typeof globalThis.fetch,
    unloadCollision: vi.fn(), refreshCollision: vi.fn(), checkCollision: async () => 2,
    renderer: {} as THREE.WebGPURenderer, loadModel: async () => new THREE.Group() });
  coarse.entries.set('t0_0_0', { state: 'draft', model, collision: new Map(), nextPoll: 0 });
  const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
  const settle = async () => { for (let k = 0; k < 5; k++) await new Promise(r => setTimeout(r, 0)); };
  try {
    coarse.update(100, 100, 0); await settle();
    expect(coarse.outage?.nextProbe).toBe(10_000);
    expect(coarse.entries.get('t0_0_0')?.model).toBe(model);
    expect(scene.children).toContain(model);
    expect(coarse.entries.get('t0_0_0')?.state).toBe('draft');
    urls.length = 0;
    coarse.update(100, 100, 9000); await settle();
    expect(urls).toHaveLength(0);
    healthy = true;
    coarse.update(100, 100, 10_000); await settle();
    expect(coarse.outage).toBeNull();
    coarse.update(100, 100, 10_001); await settle();
    expect(urls.some(url => url.includes('collision.json'))).toBe(true);
    expect(warn).toHaveBeenCalledTimes(1);
  } finally { warn.mockRestore(); }
});

test('one unanswered request or a lost demand never pauses the tiles; a failed health check or three in a row do', async () => {
  const scene = new THREE.Scene();
  let mode: 'stall' | 'dead' = 'stall', healthOk = true;
  const urls: string[] = [];
  const fetch = vi.fn(async (url: string) => {
    urls.push(url);
    if (url.endsWith('/health')) { if (!healthOk) throw new DOMException('timed out', 'TimeoutError'); return new Response(JSON.stringify({ state: 'online' })); }
    // A farm stall: the demand and one manifest time out (no answer), other tiles still answer.
    if (url.includes('/demand') || (mode === 'stall' && url.includes('t0_0_0/')) || mode === 'dead') throw new DOMException('timed out', 'TimeoutError');
    return new Response('{}', { status: 202 });
  });
  const coarse = new CoarseTiles({ scene, manifest: { visual: {}, collision: {} } as unknown as Manifest,
    pageUrl: 'https://wnt.github.io/turku3d/', fetch: fetch as unknown as typeof globalThis.fetch,
    unloadCollision: vi.fn(), refreshCollision: vi.fn(), checkCollision: async () => 2,
    renderer: {} as THREE.WebGPURenderer, loadModel: async () => new THREE.Group() });
  const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
  const settle = async () => { for (let k = 0; k < 6; k++) await new Promise(r => setTimeout(r, 0)); };
  try {
    coarse.update(100, 100, 0); await settle();
    expect(urls.some(u => u.includes('/demand'))).toBe(true);
    expect(urls.some(u => u.endsWith('/health'))).toBe(true);  // the one unanswered manifest was checked
    expect(coarse.outage).toBeNull();
    // The other window tiles keep streaming.
    urls.length = 0;
    coarse.update(100, 100, 3000); await settle();
    expect(coarse.outage).toBeNull();
    expect(urls.some(u => u.includes('collision.json') && !u.includes('t0_0_0/'))).toBe(true);
    // Nothing answers and the health check fails as well: paused.
    mode = 'dead'; healthOk = false;
    for (let t = 6000; t <= 40_000 && !coarse.outage; t += 3000) { coarse.update(100, 100, t); await settle(); }
    expect(coarse.outage).not.toBeNull();
  } finally { warn.mockRestore(); }
});

test('an unbuildable tile (422 unbuildable) is no tile here: never asked again, settled, not awaited at startup', async () => {
  const scene = new THREE.Scene();
  const urls: string[] = [];
  const fetch = vi.fn(async (url: string) => {
    urls.push(url);
    if (url.endsWith('/health')) return new Response(JSON.stringify({ state: 'online' }));
    if (url.includes('t0_0_0/')) return new Response(JSON.stringify({ state: 'unbuildable', error: 'NoCoverage: t0_0_0: only 0 ground key points' }), { status: 422 });
    if (url.includes('t0_1_0/')) return new Response(JSON.stringify({ state: 'failed', error: 'generator exit 1' }), { status: 422 });
    return new Response('{}', { status: 202 });
  });
  const coarse = new CoarseTiles({ scene, manifest: { visual: {}, collision: {} } as unknown as Manifest,
    pageUrl: 'https://wnt.github.io/turku3d/', fetch: fetch as unknown as typeof globalThis.fetch,
    unloadCollision: vi.fn(), refreshCollision: vi.fn(), checkCollision: async () => 2,
    renderer: {} as THREE.WebGPURenderer, loadModel: async () => new THREE.Group() });
  const settle = async () => { for (let k = 0; k < 6; k++) await new Promise(r => setTimeout(r, 0)); };
  const asked = (tile: string) => urls.filter(u => u.includes(`${tile}/`)).length;
  coarse.update(100, 100, 0); await settle();
  expect(coarse.entries.get('t0_0_0')?.state).toBe('none');
  // The start tile is critical, yet startup does not wait for ground that will never exist (nor, as before, for the
  // failed neighbour): 9 window tiles less those two.
  expect(coarse.progressAt(100, 100).total).toBe(7);
  for (let t = 1000; t <= 30_000; t += 1000) { coarse.update(100, 100, t); await settle(); }
  expect(asked('t0_0_0')).toBe(1);
  // A build failure is not permanent: it is retried.
  expect(coarse.entries.get('t0_1_0')?.state).toBe('failed');
  expect(asked('t0_1_0')).toBeGreaterThan(1);
  expect(coarse.resolvedAt(100, 100)).toBe(true);
  expect(coarse.collisionComing('c_0_0')).toBe(false);
});
