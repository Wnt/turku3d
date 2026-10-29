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
