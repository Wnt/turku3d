// Draft (coarse) tiles: which cells are wanted, the published-parent swap rule and the game hook against a fake service.
import { test, expect, vi } from 'vitest';
import * as THREE from 'three/webgpu';
import { CoarseTiles, RESTARTING, aheadTiles, cellsOf, wantedTiles, type CoarseHost } from '../../src/world/coarseTiles';
import { arrivalToL0 } from '../../src/world/coarseRegion';
import { isLocalHost } from '../../src/core/localOnly';
import { draftsUrl } from '../../src/net/config';
import type { Manifest } from '../../src/world/manifest';

const published = ['t0_0_0', 't0_1_0', 't0_0_1', 't0_1_1', 't1_0_0', 't1_1_0', 't1_0_-1'];

test('localhost drafts stay same-origin; Pages uses HTTPS remote drafts', () => {
  expect(isLocalHost('127.0.0.1')).toBe(true);
  expect(isLocalHost('localhost')).toBe(true);
  expect(isLocalHost('wnt.github.io')).toBe(false);
  expect(draftsUrl({ hostname: 'localhost' })).toBe('/coarse/');
  expect(draftsUrl({ hostname: 'wnt.github.io' })).toBe('https://turku.lab.madekivi.fi/coarse/');
  expect(draftsUrl({ hostname: 'example.com' })).toBeNull();
  vi.stubEnv('VITE_DRAFT_URL', 'https://drafts.example.com/coarse');
  expect(draftsUrl({ hostname: 'wnt.github.io' })).toBe('https://drafts.example.com/coarse/');
  vi.stubEnv('VITE_DRAFT_URL', 'http://drafts.example.com/coarse');
  expect(() => draftsUrl({ hostname: 'wnt.github.io' })).toThrow('HTTPS');
  vi.unstubAllEnvs();
});

test('all world cells use the nearest-first draft window, including the starting tile', () => {
  const want = wantedTiles(480, 100);
  expect(want).toHaveLength(9);
  expect(want[0]).toBe('t0_1_0');
  expect(want).toContain('t0_0_0');
  expect(want).not.toContain('t0_3_0');
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
    if (u.pathname === '/coarse/status' || u.pathname === '/coarse/health') return json(200, { state: 'idle' });
    if (u.pathname === '/coarse/want') return json(200, {});
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

test('streams only drafts, validates all collision before visibility and unloads on departure', async () => {
  const manifest = { visual: Object.fromEntries(published.map(id => [id, { url: `visual/${id}.glb` }])), collision: {} } as unknown as Manifest;
  const service = fakeService(manifest);
  const scene = new THREE.Scene();
  const unloaded: string[] = [];
  const loadedUrls: string[] = [];
  const checked = new Set<string>();
  const shownBeforeCheck: string[] = [];
  const host: CoarseHost = {
    scene, manifest, pageUrl: 'http://127.0.0.1:5171/',
    renderer: { library: { fromMaterial: () => new THREE.MeshStandardNodeMaterial() } } as unknown as THREE.WebGPURenderer,
    unloadCollision: id => unloaded.push(id), refreshCollision: () => {}, fetch: service.fetch,
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
  expect(await CoarseTiles.start({ ...host, pageUrl: 'https://example.com/' })).toBeNull();
  const offline = (async () => ({ ok: true, status: 200, json: async () => ({ state: 'offline' }) })) as unknown as typeof globalThis.fetch;
  expect((await CoarseTiles.start({ ...host, fetch: offline }))?.outage).not.toBeNull();
  const html = (async () => ({ ok: true, status: 200, json: async () => { throw new SyntaxError('<!doctype'); } })) as unknown as typeof globalThis.fetch;
  expect((await CoarseTiles.start({ ...host, fetch: html }))?.outage).not.toBeNull();
  const coarse = (await CoarseTiles.start(host))!;
  let mapChanges = 0;
  coarse.onMapChange = () => mapChanges++;
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
  expect(coarse.status).toContain('draft tile t0_2_0');
  // Its street graph is on the map: one graph (t0_2_0's; the other drafts have none), announced once.
  expect(coarse.roadGraphs().map(g => g.bbox)).toEqual([[500, 0, 750, 250]]);
  expect(mapChanges).toBe(1);
  const draft = coarse.entries.get('t0_2_0')!.model!;
  expect(draft.parent).toBe(scene);
  const material = (draft.children[0] as THREE.Mesh).material as THREE.MeshStandardNodeMaterial;
  // Draft status no longer injects a desaturation or blue-cast colour node.
  expect(material.colorNode).toBeNull();
  expect(material.color.getHex()).toBe(0xffffff);
  expect(material.polygonOffset).toBe(true);
  // Driving far away drops drafts and their collision entries.
  coarse.update(5000, 5000, 70_000); await settle();
  expect([...coarse.entries.keys()].filter(id => /^t0_[0-3]_/.test(id))).toEqual([]);
  expect(manifest.collision.c_8_0).toBeUndefined();
});

test('prefetch: the cells along the next 30 s of travel and a one-tile ring, nearest first; none when standing', () => {
  expect(aheadTiles(100, 100, 0.5, 0)).toEqual([]);
  // 20 m/s east from x 100: 600 m ahead reaches cell i = 2, and the ring i = 3; rows j -1..1 around the path.
  const ahead = aheadTiles(100, 100, 20, 0);
  expect(ahead.length).toBe(5 * 3);
  expect(ahead[0]).toBe('t0_0_0');
  for (const id of ['t0_-1_0', 't0_2_-1', 't0_3_1']) expect(ahead).toContain(id);
  expect(ahead).not.toContain('t0_4_0');
  expect(aheadTiles(100, 100, 20, 0)).toContain('t0_1_0');
  // 40 m/s north-east covers the diagonal out to 1200 m.
  expect(aheadTiles(100, 100, 28.3, -28.3)).toContain('t0_3_-4');
});

test('a moving player gets builds ahead requested without loading them; they load once inside the window', async () => {
  const manifest = { visual: Object.fromEntries(published.map(id => [id, { url: `visual/${id}.glb` }])), collision: {} } as unknown as Manifest;
  const service = fakeService(manifest);
  const loaded: string[] = [];
  const coarse = new CoarseTiles({
    scene: new THREE.Scene(), manifest,
    pageUrl: 'http://127.0.0.1:5171/', fetch: service.fetch, unloadCollision: () => {}, refreshCollision: () => {}, checkCollision: async () => 2,
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
  const coarse = (await CoarseTiles.start({ scene: new THREE.Scene(), manifest,
    pageUrl: 'http://127.0.0.1:5171/', fetch, unloadCollision: () => {}, refreshCollision: () => {},
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

test('a restarting service pauses the drafts: one console line, only status probes, then they resume', async () => {
  const manifest = { visual: Object.fromEntries(published.map(id => [id, { url: `visual/${id}.glb` }])), collision: {} } as unknown as Manifest;
  // 'up': a running service; 'down': the dev server's answers for a stopped one (status 200 offline, everything else
  // 503); 'old': a running service without /coarse/want (404). `busy` /coarse/status requests outlast their timeout.
  let mode: 'up' | 'down' | 'old' = 'up', code = 'a', busy = 0;
  const requests: string[] = [];
  const fetch = (async (url: string) => {
    const u = new URL(url);
    requests.push(u.pathname);
    const json = (status: number, body: unknown) => ({ ok: status < 300, status, json: async () => body }) as Response;
    if (u.pathname === '/__localhost/status') return json(200, { service: true, coarse: { commit: 'c', state: 'restarting' } });
    if (u.pathname === '/coarse/status' && busy > 0) { busy--; throw new DOMException('The operation timed out.', 'TimeoutError'); }
    // A restarted service with a new producer: nothing built yet, no queue.
    if (u.pathname === '/coarse/status') return json(200, mode === 'down' ? { state: 'offline' } : { state: 'idle', code, built: [], queue: [] });
    if (mode === 'down') return json(503, { state: 'offline' });
    if (u.pathname === '/coarse/want') return mode === 'old' ? json(404, { error: 'not found' }) : json(200, {});
    return json(202, { state: 'queued' });
  }) as typeof globalThis.fetch;
  const host: CoarseHost = { scene: new THREE.Scene(), manifest,
    pageUrl: 'http://127.0.0.1:5171/', fetch, unloadCollision: () => {}, refreshCollision: () => {},
    checkCollision: async () => 2, renderer: {} as THREE.WebGPURenderer, loadModel: async () => new THREE.Group() };
  const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
  const info = vi.spyOn(console, 'info').mockImplementation(() => {});
  try {
    const settle = async () => { for (let k = 0; k < 4; k++) await new Promise(r => setTimeout(r, 0)); };
    const coarse = (await CoarseTiles.start(host))!;
    coarse.update(600, 100, 0); await settle();
    expect(requests).toContain('/coarse/want');
    expect(requests).toContain('/coarse/t0_2_0/manifest.json');

    // A landing restarts the service: the dev server answers 503 for it.
    mode = 'down';
    coarse.update(600, 100, 2000); await settle();
    expect(coarse.outage).not.toBeNull();
    expect(coarse.status).toBe(RESTARTING);
    requests.length = 0;
    for (let t = 3000; t <= 12_000; t += 1000) { coarse.update(600, 100, t); await settle(); }
    expect(new Set(requests)).toEqual(new Set(['/coarse/status'])); // no tile or prefetch request while it is down
    expect(requests.length).toBeLessThanOrEqual(5); // one probe per 2 s
    expect(warn).toHaveBeenCalledTimes(1);
    expect(coarse.status).toBe(RESTARTING);

    // The supervisor started a process with new producer code: any answer resumes, and the ring is asked for at once.
    mode = 'up'; code = 'b';
    requests.length = 0;
    coarse.update(600, 100, 14_000); await settle();
    expect(coarse.outage).toBeNull();
    expect(info).toHaveBeenCalledTimes(1);
    coarse.update(600, 100, 14_100); await settle(); // the next frame, not the next 1 s check
    expect(requests).toContain('/coarse/want');
    expect(requests).toContain('/coarse/t0_2_0/manifest.json');
    expect(coarse.status).not.toBe(RESTARTING);

    // A still-running older process without /coarse/want: only the prefetch is skipped, drafts keep being asked for.
    mode = 'old';
    requests.length = 0;
    for (let t = 16_000; t <= 20_000; t += 1000) { coarse.update(600, 100, t); await settle(); }
    expect(coarse.outage).toBeNull();
    expect(requests.filter(p => p === '/coarse/t0_2_0/manifest.json').length).toBeGreaterThan(1);
    expect(warn).toHaveBeenCalledTimes(1);
  } finally { warn.mockRestore(); info.mockRestore(); }

  // A page loaded while the supervised service restarts starts paused; without a supervising service, no drafts.
  mode = 'down';
  const paused = (await CoarseTiles.start(host))!;
  expect(paused.outage).not.toBeNull();
  const unsupervised = (async (url: string) => ({ ok: true, status: 200,
    json: async () => new URL(url).pathname === '/__localhost/status' ? { service: true, coarse: { commit: null, state: 'off' } } : { state: 'offline' } })) as unknown as typeof globalThis.fetch;
  expect((await CoarseTiles.start({ ...host, fetch: unsupervised }))?.outage).not.toBeNull();

  // A page loaded while the service is too busy to answer /coarse/status in 2 s starts paused, not without drafts,
  // and resumes as soon as a probe gets through.
  mode = 'up'; busy = 2;
  const warn2 = vi.spyOn(console, 'warn').mockImplementation(() => {});
  const info2 = vi.spyOn(console, 'info').mockImplementation(() => {});
  try {
    const settle = async () => { for (let k = 0; k < 4; k++) await new Promise(r => setTimeout(r, 0)); };
    const slow = (await CoarseTiles.start(host))!;
    expect(slow.outage).not.toBeNull();
    slow.update(600, 100, 2000); await settle(); // the first probe is slow as well
    expect(slow.outage).not.toBeNull();
    requests.length = 0;
    slow.update(600, 100, 4000); await settle();
    expect(slow.outage).toBeNull();
    slow.update(600, 100, 4100); await settle();
    expect(requests).toContain('/coarse/t0_2_0/manifest.json');
  } finally { warn2.mockRestore(); info2.mockRestore(); }
});

test('a finished build reaches the game at the next check, and the badge shows when the next tile is ready', async () => {
  const manifest = { visual: Object.fromEntries(published.map(id => [id, { url: `visual/${id}.glb` }])), collision: {} } as unknown as Manifest;
  let version = 4, polls = 0;
  const fetch = (async (url: string) => {
    const u = new URL(url);
    const json = (status: number, body: unknown) => ({ ok: status < 300, status, json: async () => body }) as Response;
    if (u.pathname === '/coarse/want') return json(200, { queued: 0, fetching: [], version });
    if (u.pathname === '/coarse/status') return json(200, { state: 'idle' });
    if (u.pathname === '/coarse/t0_2_0/manifest.json') { polls++; return json(202, { state: 'queued', position: 0, eta: 6.2 }); }
    return json(404, { error: 'not here' });
  }) as typeof globalThis.fetch;
  const coarse = (await CoarseTiles.start({ scene: new THREE.Scene(), manifest,
    pageUrl: 'http://127.0.0.1:5171/', fetch, unloadCollision: () => {}, refreshCollision: () => {},
    checkCollision: async () => 2, renderer: {} as THREE.WebGPURenderer, loadModel: async () => new THREE.Group() }))!;
  const settle = async () => { for (let k = 0; k < 4; k++) await new Promise(r => setTimeout(r, 0)); };
  coarse.update(600, 100, 0); await settle();
  expect(polls).toBe(1);
  coarse.update(600, 100, 1000); await settle();
  expect(coarse.status).toMatch(/^draft tile t0_2_0 ready in 6 s/);
  expect(polls).toBe(1); // its own schedule: every 2 s
  version = 5; // a build finished; the next check's /coarse/want reports it
  coarse.update(600, 100, 2000); await settle();
  coarse.update(600, 100, 2050); await settle();
  expect(polls).toBe(3); // the 2 s poll, then at once on the new version instead of at 4000
});

test('Pages retries offline and queue-full states, then draws remote drafts after collision checks', async () => {
  const manifest = { visual: { t0_0_0: { url: 'vis.glb' } }, collision: {} } as unknown as Manifest;
  const service = fakeService(manifest);
  const requests: string[] = [];
  let state: 'offline' | 'full' | 'online' = 'offline';
  let checks = 0;
  let loads = 0;
  const host: CoarseHost = {
    scene: new THREE.Scene(), manifest,
    pageUrl: 'https://wnt.github.io/turku-game/',
    renderer: { library: { fromMaterial: () => new THREE.MeshStandardNodeMaterial() } } as unknown as THREE.WebGPURenderer,
    unloadCollision: () => {}, refreshCollision: () => {},
    checkCollision: async () => { checks++; },
    loadModel: async url => {
      expect(url).toMatch(/^https:\/\/turku.lab.madekivi.fi\/coarse\//);
      expect(checks).toBeGreaterThanOrEqual(16);
      loads++;
      return new THREE.Group();
    },
    fetch: (async (url: string) => {
      requests.push(url);
      if (state === 'offline') throw new TypeError('offline');
      if (state === 'full' && url.includes('/manifest.json')) return new Response('{"state":"queue-full"}', { status: 429 });
      return service.fetch(url);
    }) as typeof globalThis.fetch,
  };
  const coarse = (await CoarseTiles.start(host))!;
  expect(coarse.progressAt(100, 100)).toEqual({ loaded: 0, total: 9, errors: [] });
  expect(coarse.progressAt(600, 100)).toEqual({ loaded: 0, total: 9, errors: [] });
  const settle = async () => { for (let i = 0; i < 5; i++) await new Promise(r => setTimeout(r, 0)); };
  coarse.update(600, 100, 0);
  expect(coarse.status).toBe('drafts: remote service offline');
  state = 'full';
  coarse.update(600, 100, 3000); await settle();
  coarse.update(600, 100, 4000); await settle();
  coarse.update(600, 100, 5000);
  expect(coarse.status).toContain('remote queue full');
  expect(loads).toBe(0);
  state = 'online';
  coarse.update(600, 100, 10_000); await settle();
  expect(coarse.status).toContain('building');
  coarse.update(600, 100, 13_000); await settle();
  expect(coarse.drawn()).toContain('t0_2_0');
  expect(loads).toBeGreaterThan(0);
  expect(requests.some(url => /\/coarse\/(status|want)/.test(url))).toBe(false);
  expect(requests.filter(url => url.includes('/coarse/')).every(url => url.startsWith('https://turku.lab.madekivi.fi/'))).toBe(true);
});

test('Kaskenkatu requests actual southeast crossings before nearer lateral cells', () => {
  const arrival = (id: string) => arrivalToL0(id, 349, 284, 8.34, 12.47);
  expect(arrival('t0_1_2')).toBeCloseTo(216 / 12.47);
  expect(arrival('t0_2_2')).toBeCloseTo(151 / 8.34);
  expect(arrival('t0_2_1')).toBe(Infinity);
  expect(arrival('t0_1_2')).toBeLessThan(arrival('t0_2_2'));
  expect(arrivalToL0('t0_2_0', 490, 125, -27, 0)).toBe(Infinity);
  expect(arrivalToL0('t0_2_0', 490, 125, 27, 0)).toBeCloseTo(10 / 27);

});

test('a full draft upgrades geometry, changed collision and streets only after the replacement is ready', async () => {
  const id = 't0_2_0', manifest = { visual: {}, collision: {} } as unknown as Manifest;
  let phase = 'first', release!: (model: THREE.Object3D) => void;
  const first = new THREE.Group(), full = new THREE.Group(), refreshed: string[] = [];
  const scene = new THREE.Scene();
  const graph = () => ({ format: 'turku3d-roadgraph', version: 1, frame: 'engine-xz',
    nodes: phase === 'first' ? [500, 100, 700, 100] : [500, 101, 700, 101], edges: [[0, 1, 3, 200]] });
  const fetch = (async (url: string) => {
    const name = new URL(url).pathname;
    const json = (status: number, body: unknown) => ({ ok: status < 300, status, json: async () => body }) as Response;
    if (name.endsWith('/want')) return json(200, {});
    if (name === `/coarse/${id}/manifest.json`) return json(200, { coarse: true, tile: id,
      visual: { [id]: { url: `vis.${phase}.glb`, sha256: phase } }, roads: { url: `roads.${phase}.json` },
      collision: Object.fromEntries(cellsOf(id).map(cid => [cid, { url: `col/${cid}.${cid === 'c_8_0' ? phase : 'same'}.col`,
        sha256: cid === 'c_8_0' ? phase : 'same', bytes: 4, tris: 2, h: [0, 1] }])) });
    if (name.endsWith('.col')) return { ok: true, arrayBuffer: async () => new ArrayBuffer(4) } as Response;
    if (name.endsWith('.json') && name.includes('/roads.')) return json(200, graph());
    return json(404, {});
  }) as typeof globalThis.fetch;
  const coarse = new CoarseTiles({ scene, manifest,
    pageUrl: 'http://127.0.0.1:5171/', renderer: {} as THREE.WebGPURenderer,
    fetch, unloadCollision: () => {}, refreshCollision: ids => refreshed.push(...ids), checkCollision: async () => 2,
    loadModel: async () => phase === 'first' ? first : new Promise(resolve => { release = resolve; }) });
  const settle = async () => { for (let i = 0; i < 6; i++) await new Promise(r => setTimeout(r, 0)); };
  coarse.update(600, 100, 0); await settle();
  expect(first.parent).toBe(scene);
  phase = 'full';
  coarse.update(600, 100, 16_000); await settle();
  expect(first.parent).toBe(scene);
  expect(manifest.collision.c_8_0.sha256).toBe('first');
  release(full); await settle();
  expect(first.parent).toBeNull();
  expect(full.parent).toBe(scene);
  expect(manifest.collision.c_8_0.sha256).toBe('full');
  expect(coarse.roadGraphs()[0].nodes).toEqual([500, 101, 700, 101]);
  expect(refreshed).toEqual(['c_8_0']);
});
