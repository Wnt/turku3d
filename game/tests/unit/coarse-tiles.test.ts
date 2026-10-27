// Draft (coarse) tiles: which cells are wanted, the published-parent swap rule and the game hook against a fake service.
import { test, expect } from 'vitest';
import * as THREE from 'three/webgpu';
import { CoarseTiles, aheadTiles, cellsOf, leafParents, parentCovered, wantedTiles, type CoarseHost } from '../../src/world/coarseTiles';
import { isLocalHost } from '../../src/core/localOnly';
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
  // t0_1_-1 lacks one collision cell; t0_2_-1 ships .col files the worker cannot decode (first byte 0xff).
  const fragment = (id: string) => ({ coarse: true, tile: id, visual: { [id]: { url: 'vis.glb', sha256: '', bytes: 3, tris: 1, h: [0, 1] } },
    roads: id === 't0_2_0' ? { url: 'roads.json', bytes: 100 } : null,
    collision: Object.fromEntries(cellsOf(id).slice(id === 't0_1_-1' ? 1 : 0)
      .map(cid => [cid, { url: `col/${cid}.col`, sha256: '', bytes: 4, tris: 2, h: [0, 1] }])) });
  let freshManifest: Manifest | null = null;
  const colUrls = new Map<ArrayBuffer, string>();
  const serve = (async (url: string) => {
    const u = new URL(url);
    const json = (status: number, body: unknown) => ({ ok: status < 300, status, json: async () => body }) as Response;
    if (u.pathname === '/coarse/status') return json(200, { state: 'idle' });
    if (u.pathname === '/tiles/manifest.json') return json(200, freshManifest ?? manifest);
    if (u.pathname === '/coarse/t0_2_0/roads.json') return json(200, { format: 'turku3d-roadgraph', version: 1, frame: 'engine-xz',
      bbox: [500, 0, 750, 250], nodes: [500, 100, 700, 100], edges: [[0, 1, 3, 200]] });
    const c = /^\/coarse\/(t0_-?\d+_-?\d+)\/col\/c_-?\d+_-?\d+\.col$/.exec(u.pathname);
    if (c) {
      const buffer = Uint8Array.of(c[1] === 't0_2_-1' ? 0xff : 1, 2, 3, 4).buffer;
      colUrls.set(buffer, u.pathname);
      return { ok: true, status: 200, arrayBuffer: async () => buffer } as unknown as Response;
    }
    const m = /^\/coarse\/(t0_-?\d+_-?\d+)\/manifest\.json$/.exec(u.pathname);
    if (!m) return json(404, { error: 'not found' });
    const n = (polls.get(m[1]) ?? 0) + 1;
    polls.set(m[1], n);
    if (m[1] === 't0_3_-1') return json(404, { state: 'failed', error: 'no coverage' });
    return n === 1 ? json(202, { state: 'queued' }) : json(200, fragment(m[1]));
  }) as typeof globalThis.fetch;
  return { fetch: serve, polls, colUrls, publish: (m: Manifest) => { freshManifest = m; } };
}

test('streams a draft tile into the scene and the collision manifest, hides the covered parent, swaps on publish', async () => {
  const manifest = { visual: Object.fromEntries(published.map(id => [id, { url: `visual/${id}.glb` }])), collision: {} } as unknown as Manifest;
  const service = fakeService(manifest);
  const scene = new THREE.Scene();
  const tiles = new THREE.EventDispatcher() as CoarseHost['tiles'];
  const unloaded: string[] = [];
  const loadedUrls: string[] = [];
  const checked = new Set<string>();
  const shownBeforeCheck: string[] = [];
  const host: CoarseHost = {
    scene, tiles, manifest, manifestUrl: 'tiles/manifest.json', pageUrl: 'http://127.0.0.1:5171/',
    renderer: { library: { fromMaterial: () => new THREE.MeshStandardNodeMaterial() } } as unknown as THREE.WebGPURenderer,
    unloadCollision: id => unloaded.push(id), fetch: service.fetch,
    // The physics worker accepts a buffer unless it starts with 0xff.
    checkCollision: async (buffer) => {
      if (new Uint8Array(buffer)[0] === 0xff) throw new Error('CRC mismatch');
      checked.add(service.colUrls.get(buffer)!);
      return 2;
    },
    loadModel: async (url) => {
      loadedUrls.push(url);
      const tile = /coarse\/(t0_-?\d+_-?\d+)\//.exec(url)?.[1];
      if (tile && cellsOf(tile).some(cid => !checked.has(`/coarse/${tile}/col/${cid}.col`))) shownBeforeCheck.push(tile);
      const g = new THREE.Group(); g.add(new THREE.Mesh(new THREE.BoxGeometry(), new THREE.MeshStandardMaterial())); return g; },
  };
  expect(await CoarseTiles.start({ ...host, pageUrl: 'https://wnt.github.io/turku-game/' })).toBeNull();
  const offline = (async () => ({ ok: true, status: 200, json: async () => ({ state: 'offline' }) })) as unknown as typeof globalThis.fetch;
  expect(await CoarseTiles.start({ ...host, fetch: offline })).toBeNull();
  const html = (async () => ({ ok: true, status: 200, json: async () => { throw new SyntaxError('<!doctype'); } })) as unknown as typeof globalThis.fetch;
  expect(await CoarseTiles.start({ ...host, fetch: html })).toBeNull();
  const coarse = (await CoarseTiles.start(host))!;
  let mapChanges = 0;
  coarse.onMapChange = () => mapChanges++;
  const parent = new THREE.Group();
  tiles.dispatchEvent({ type: 'load-model', scene: parent, tile: { extras: { id: 't1_1_0' } } });
  const settle = () => new Promise(r => setTimeout(r, 0));

  coarse.update(600, 100, 0); await settle();
  expect(coarse.entries.get('t0_2_0')?.state).toBe('pending'); // 202: queued
  coarse.update(600, 100, 3000); await settle(); await settle();
  expect(coarse.entries.get('t0_2_0')?.state).toBe('draft');
  expect(coarse.entries.get('t0_3_-1')?.state).toBe('failed');
  expect(loadedUrls).toContain('http://127.0.0.1:5171/coarse/t0_2_0/vis.glb');
  expect(manifest.collision.c_8_0.url).toBe('http://127.0.0.1:5171/coarse/t0_2_0/col/c_8_0.col');
  // Collision before visibility: each drawn draft had all 16 .col files accepted before its GLB was even loaded;
  // a draft with a missing cell or an undecodable .col is never drawn and its cells stay out of the manifest.
  expect(shownBeforeCheck).toEqual([]);
  expect(coarse.entries.get('t0_1_-1')).toMatchObject({ state: 'failed', error: 'no collision for 1 of 16 cells (c_4_-4)' });
  expect(coarse.entries.get('t0_2_-1')).toMatchObject({ state: 'failed', error: 'collision c_8_-4: CRC mismatch' });
  for (const id of ['t0_1_-1', 't0_2_-1']) {
    expect(coarse.entries.get(id)!.model).toBeUndefined();
    expect(loadedUrls).not.toContain(`http://127.0.0.1:5171/coarse/${id}/vis.glb`);
    expect(cellsOf(id).filter(cid => manifest.collision[cid])).toEqual([]);
  }
  coarse.update(600, 100, 4100); await settle(); // the badge follows at the next 1 s check
  expect(coarse.status).toBe('draft tile t0_2_0 · drafts t0_0_-1 t0_0_-2 t0_1_-2 t0_2_0 t0_2_1 t0_3_0 t0_3_1');
  // Its street graph is on the map: one graph (t0_2_0's; the other drafts have none), announced once.
  expect(coarse.roadGraphs().map(g => g.bbox)).toEqual([[500, 0, 750, 250]]);
  expect(mapChanges).toBe(1);
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
  // The published tile's streets come with the release's graph: the draft's leave the map.
  expect(coarse.roadGraphs()).toEqual([]);
  expect(mapChanges).toBeGreaterThanOrEqual(2);
  expect(unloaded).toContain('c_8_0');
  expect(manifest.collision.c_8_0.url).toBe('http://127.0.0.1:5171/tiles/collision/t0_2_0/c_8_0.col');
  expect(loadedUrls).toContain('http://127.0.0.1:5171/tiles/visual/0/t0_2_0.glb');

  // Driving far away drops drafts and their collision entries.
  coarse.update(5000, 5000, 70_000); await settle();
  expect([...coarse.entries.keys()].filter(id => /^t0_[0-3]_/.test(id))).toEqual([]);
  expect(manifest.collision.c_8_0).toBeUndefined();
  expect(parent.visible).toBe(true);
});

test('prefetch: the cells along the next 30 s of travel and a one-tile ring, nearest first; none when standing', () => {
  const none = new Set<string>();
  expect(aheadTiles(100, 100, 0.5, 0, none)).toEqual([]);
  // 20 m/s east from x 100: 600 m ahead reaches cell i = 2, and the ring i = 3; rows j -1..1 around the path.
  const ahead = aheadTiles(100, 100, 20, 0, none);
  expect(ahead.length).toBe(5 * 3);
  expect(ahead[0]).toBe('t0_0_0');
  for (const id of ['t0_-1_0', 't0_2_-1', 't0_3_1']) expect(ahead).toContain(id);
  expect(ahead).not.toContain('t0_4_0');
  expect(aheadTiles(100, 100, 20, 0, new Set(['t0_1_0']))).not.toContain('t0_1_0');
  // 40 m/s north-east covers the diagonal out to 1200 m.
  expect(aheadTiles(100, 100, 28.3, -28.3, none)).toContain('t0_3_-4');
});

test('a moving player gets builds ahead requested without loading them; they load once inside the window', async () => {
  const manifest = { visual: Object.fromEntries(published.map(id => [id, { url: `visual/${id}.glb` }])), collision: {} } as unknown as Manifest;
  const service = fakeService(manifest);
  const loaded: string[] = [];
  const coarse = new CoarseTiles({
    scene: new THREE.Scene(), tiles: new THREE.EventDispatcher() as CoarseHost['tiles'], manifest, manifestUrl: 'tiles/manifest.json',
    pageUrl: 'http://127.0.0.1:5171/', fetch: service.fetch, unloadCollision: () => {}, checkCollision: async () => 2,
    renderer: { library: { fromMaterial: () => new THREE.MeshStandardNodeMaterial() } } as unknown as THREE.WebGPURenderer,
    loadModel: async (url) => { loaded.push(/(t0_[^/]+)\/vis/.exec(url)![1]); return new THREE.Group(); },
  });
  const settle = async () => { for (let k = 0; k < 4; k++) await new Promise(r => setTimeout(r, 0)); };
  // Driving east at 25 m/s along z 100 from x 300.
  for (let t = 0; t <= 8; t++) { coarse.update(300 + 25 * t, 100, 1000 * t); await settle(); }
  expect(coarse.velocity.x).toBeCloseTo(25, 0);
  expect(service.polls.get('t0_5_0')).toBeGreaterThan(0); // 750 m ahead of x 500
  expect(coarse.entries.get('t0_5_0')?.state).toBe('built');
  expect(loaded).not.toContain('t0_5_0');
  expect(loaded).toContain('t0_2_0');
  expect(coarse.status).toMatch(/drafts .*t0_2_0/);
  // A teleport is no speed: no prefetch from it.
  coarse.update(5000, 5000, 9000); await settle();
  expect(coarse.velocity).toEqual({ x: 0, z: 0 });
});

test('reports the player motion once a second and names the data a waiting tile needs', async () => {
  const manifest = { visual: Object.fromEntries(published.map(id => [id, { url: `visual/${id}.glb` }])), collision: {} } as unknown as Manifest;
  const wants: string[] = [];
  const fetch = (async (url: string) => {
    const u = new URL(url);
    const json = (status: number, body: unknown) => ({ ok: status < 300, status, json: async () => body }) as Response;
    if (u.pathname === '/coarse/want') { wants.push(u.search); return json(200, { queued: 3, fetching: [] }); }
    if (u.pathname === '/coarse/status') return json(200, { state: 'idle' });
    return json(202, { state: 'pending-data', missing: ['lidar/23460000_6704500', 'lidar/23460500_6704500', 'wfs/23460000_6704500'] });
  }) as typeof globalThis.fetch;
  const coarse = (await CoarseTiles.start({ scene: new THREE.Scene(), tiles: new THREE.EventDispatcher() as CoarseHost['tiles'], manifest,
    manifestUrl: 'tiles/manifest.json', pageUrl: 'http://127.0.0.1:5171/', fetch, unloadCollision: () => {},
    checkCollision: async () => 2, renderer: {} as THREE.WebGPURenderer, loadModel: async () => new THREE.Group() }))!;
  const settle = () => new Promise(r => setTimeout(r, 0));
  coarse.update(600, 100, 0); await settle(); await settle();
  coarse.update(610, 100, 500); await settle();
  expect(wants).toEqual(['?x=600.0&z=100.0&vx=0.0&vz=0.0']); // throttled to the 1 s check
  expect(coarse.entries.get('t0_2_0')?.state).toBe('pending');
  coarse.update(627, 100, 1000); await settle();
  expect(wants[1]).toBe('?x=627.0&z=100.0&vx=13.5&vz=0.0'); // the smoothed ground velocity
  expect(coarse.status).toMatch(/^draft tile t0_2_0 waiting for lidar, wfs data/);
});
