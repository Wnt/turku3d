// Draft (coarse) tiles: which cells are wanted, the published-parent swap rule and the game hook against a fake service.
import { test, expect } from 'vitest';
import * as THREE from 'three/webgpu';
import { CoarseTiles, isLocalHost, leafParents, parentCovered, wantedTiles, type CoarseHost } from '../../src/world/coarseTiles';
import type { Manifest } from '../../src/world/manifest';

const published = ['t0_0_0', 't0_1_0', 't0_0_1', 't0_1_1', 't1_0_0', 't1_1_0', 't1_0_-1'];

test('only localhost pages use draft tiles', () => {
  expect(isLocalHost('127.0.0.1')).toBe(true);
  expect(isLocalHost('localhost')).toBe(true);
  expect(isLocalHost('wnt.github.io')).toBe(false);
});

test('wanted cells: 3x3 window minus published tiles, widened to whole published leaf parents, nearest first', () => {
  const leaves = leafParents(published);
  expect([...leaves].sort()).toEqual(['t1_0_-1', 't1_1_0']);
  const want = wantedTiles(480, 100, new Set(published), leaves);
  expect(want[0]).toBe('t0_2_0');
  expect(want).not.toContain('t0_1_0');
  // t1_1_0 is a leaf: all four of its quadrants, including t0_3_0 two cells away.
  for (const id of ['t0_2_0', 't0_3_0', 't0_2_1', 't0_3_1', 't0_0_-1', 't0_1_-1', 't0_0_-2', 't0_1_-2', 't0_2_-1']) expect(want).toContain(id);
  expect(parentCovered('t1_1_0', new Set(['t0_2_0', 't0_3_0', 't0_2_1']))).toBe(false);
  expect(parentCovered('t1_1_0', new Set(['t0_2_0', 't0_3_0', 't0_2_1', 't0_3_1']))).toBe(true);
});

function fakeService(manifest: Manifest) {
  const polls = new Map<string, number>();
  const fragment = (id: string) => ({ coarse: true, tile: id, visual: { [id]: { url: 'vis.glb', sha256: '', bytes: 3, tris: 1, h: [0, 1] } },
    collision: { [`c_${8 + (id === 't0_2_0' ? 0 : 4)}_0`]: { url: 'col/c.col', sha256: '', bytes: 4, tris: 2, h: [0, 1] } } });
  let freshManifest: Manifest | null = null;
  const serve = (async (url: string) => {
    const u = new URL(url);
    const json = (status: number, body: unknown) => ({ ok: status < 300, status, json: async () => body }) as Response;
    if (u.pathname === '/coarse/status') return json(200, { state: 'idle' });
    if (u.pathname === '/tiles/manifest.json') return json(200, freshManifest ?? manifest);
    const m = /^\/coarse\/(t0_-?\d+_-?\d+)\/manifest\.json$/.exec(u.pathname);
    if (!m) return json(404, { error: 'not found' });
    const n = (polls.get(m[1]) ?? 0) + 1;
    polls.set(m[1], n);
    if (m[1] === 't0_3_-1') return json(404, { state: 'failed', error: 'no coverage' });
    return n === 1 ? json(202, { state: 'queued' }) : json(200, fragment(m[1]));
  }) as typeof globalThis.fetch;
  return { fetch: serve, polls, publish: (m: Manifest) => { freshManifest = m; } };
}

test('streams a draft tile into the scene and the collision manifest, hides the covered parent, swaps on publish', async () => {
  const manifest = { visual: Object.fromEntries(published.map(id => [id, { url: `visual/${id}.glb` }])), collision: {} } as unknown as Manifest;
  const service = fakeService(manifest);
  const scene = new THREE.Scene();
  const tiles = new THREE.EventDispatcher() as CoarseHost['tiles'];
  const unloaded: string[] = [];
  const loadedUrls: string[] = [];
  const host: CoarseHost = {
    scene, tiles, manifest, manifestUrl: 'tiles/manifest.json', pageUrl: 'http://127.0.0.1:5171/',
    renderer: { library: { fromMaterial: () => new THREE.MeshStandardNodeMaterial() } } as unknown as THREE.WebGPURenderer,
    unloadCollision: id => unloaded.push(id), fetch: service.fetch,
    loadModel: async (url) => { loadedUrls.push(url); const g = new THREE.Group(); g.add(new THREE.Mesh(new THREE.BoxGeometry(), new THREE.MeshStandardMaterial())); return g; },
  };
  expect(await CoarseTiles.start({ ...host, pageUrl: 'https://wnt.github.io/turku-game/' })).toBeNull();
  const offline = (async () => ({ ok: true, status: 200, json: async () => ({ state: 'offline' }) })) as unknown as typeof globalThis.fetch;
  expect(await CoarseTiles.start({ ...host, fetch: offline })).toBeNull();
  const html = (async () => ({ ok: true, status: 200, json: async () => { throw new SyntaxError('<!doctype'); } })) as unknown as typeof globalThis.fetch;
  expect(await CoarseTiles.start({ ...host, fetch: html })).toBeNull();
  const coarse = (await CoarseTiles.start(host))!;
  const parent = new THREE.Group();
  tiles.dispatchEvent({ type: 'load-model', scene: parent, tile: { extras: { id: 't1_1_0' } } });
  const settle = () => new Promise(r => setTimeout(r, 0));

  coarse.update(600, 100, 0); await settle();
  expect(coarse.entries.get('t0_2_0')?.state).toBe('pending'); // 202: queued
  coarse.update(600, 100, 3000); await settle(); await settle();
  expect(coarse.entries.get('t0_2_0')?.state).toBe('draft');
  expect(coarse.entries.get('t0_3_-1')?.state).toBe('failed');
  expect(loadedUrls).toContain('http://127.0.0.1:5171/coarse/t0_2_0/vis.glb');
  expect(manifest.collision.c_8_0.url).toBe('http://127.0.0.1:5171/coarse/t0_2_0/col/c.col');
  expect(coarse.status).toMatch(/^draft tile t0_2_0/);
  const draft = coarse.entries.get('t0_2_0')!.model!;
  expect(draft.parent).toBe(scene);
  const material = (draft.children[0] as THREE.Mesh).material as THREE.MeshStandardNodeMaterial;
  expect(material.colorNode).toBeTruthy();
  expect(material.polygonOffset).toBe(true);
  // All four quadrants of t1_1_0 are drafts now (the fake builds every tile), so the published leaf is hidden.
  expect(parent.visible).toBe(false);

  // The pipeline publishes t0_2_0: after the 60 s manifest poll the published GLB and collision replace the draft.
  service.publish({ ...manifest, visual: { ...manifest.visual, t0_2_0: { url: 'visual/0/t0_2_0.glb' } },
    collision: { c_8_0: { url: 'collision/t0_2_0/c_8_0.col' } } } as unknown as Manifest);
  coarse.update(600, 100, 61_000); await settle(); await settle(); await settle();
  expect(coarse.entries.get('t0_2_0')?.state).toBe('published');
  expect(draft.parent).toBeNull();
  expect(unloaded).toContain('c_8_0');
  expect(manifest.collision.c_8_0.url).toBe('http://127.0.0.1:5171/tiles/collision/t0_2_0/c_8_0.col');
  expect(loadedUrls).toContain('http://127.0.0.1:5171/tiles/visual/0/t0_2_0.glb');

  // Driving far away drops drafts and their collision entries.
  coarse.update(5000, 5000, 70_000); await settle();
  expect([...coarse.entries.keys()].filter(id => /^t0_[0-3]_/.test(id))).toEqual([]);
  expect(manifest.collision.c_8_0).toBeUndefined();
  expect(parent.visible).toBe(true);
});
