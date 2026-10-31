import { expect, test, vi } from 'vitest';
import * as THREE from 'three/webgpu';
import { ScreenshotScenery } from '../../src/world/screenshotScenery';
import type { CoarseHost, Entry } from '../../src/world/coarseContracts';
import { PLACES } from '../../src/gameplay/content/places';
import { Environment } from '../../src/render/environment';

const settle = async () => { for (let i = 0; i < 20; i++) await Promise.resolve(); };
function setup() {
  const scene = new THREE.Scene(), nearby = new Map<string, Entry>();
  const host: CoarseHost = { scene, renderer: {} as THREE.WebGPURenderer, manifest: { collision: {} } as never,
    checkCollision: vi.fn(), unloadCollision: vi.fn(), refreshCollision: vi.fn(), trackModel: vi.fn() };
  const request = vi.fn(async (url: string) => {
    const id = url.split('/')[2];
    return new Response(JSON.stringify({ coarse: true, tile: id, pass_kind: 'full',
      visual: { [id]: { url: 'scene.glb', sha256: id } } }));
  });
  const load = vi.fn(async () => new THREE.Group());
  const dispose = vi.fn((model: THREE.Object3D) => model.removeFromParent());
  const scenery = new ScreenshotScenery(host, nearby, request, load, () => {}, dispose, '/coarse/', 'http://localhost/');
  return { scene, nearby, host, request, load, dispose, scenery };
}

test('kilometre scenery advances beyond one demand batch without adding physics and resets cleanly', async () => {
  const { scene, host, scenery, request, dispose } = setup();
  scenery.update(125, 125, 0); expect(request).not.toHaveBeenCalled();
  scenery.setRadius(1000);
  scenery.update(125, 125, 1000); await settle();
  expect(scenery.progress.total).toBeGreaterThan(64);
  expect(scenery.demand().length).toBeLessThanOrEqual(64);
  expect(scenery.progress.loaded).toBe(2);
  const first = request.mock.calls[0][0];
  for (let i = 2; i < 65; i++) { scenery.update(125, 125, i * 1000); await settle(); }
  expect(scenery.progress.loaded).toBe(scenery.progress.total);
  expect(request.mock.calls.filter(([url]) => url === first).length).toBeGreaterThan(1);
  expect(host.checkCollision).not.toHaveBeenCalled(); expect(host.manifest.collision).toEqual({});
  scenery.setRadius(0); expect(scene.children).toHaveLength(0); expect(dispose).toHaveBeenCalled();
  expect(scenery.demand()).toEqual([]);
});

test('normal residency takes over scenery; reset invalidates an in-flight model', async () => {
  const { scene, nearby, scenery, load, dispose } = setup();
  scenery.setRadius(1000); scenery.update(125, 125, 0); await settle();
  const first = scene.children[0];
  nearby.set('t0_0_0', { model: new THREE.Group(), state: 'draft', nextPoll: 0, collision: new Map() });
  scenery.update(125, 125, 1000); await settle();
  expect(first.parent).toBeNull();
  const releases: ((model: THREE.Group) => void)[] = [];
  load.mockImplementation(() => new Promise(resolve => { releases.push(resolve); }));
  scenery.setRadius(0); scenery.setRadius(1000); scenery.update(125, 125, 2000); await settle();
  scenery.setRadius(0);
  const late = new THREE.Group();
  for (const release of releases) release(late);
  await settle();
  expect(late.parent).toBeNull(); expect(dispose).toHaveBeenCalledWith(late, expect.any(String));
});

test('5 km from the supplied east-city viewpoint includes the cathedral and farther scenery', () => {
  const { scenery } = setup();
  scenery.setRadius(5000); scenery.update(3749.71, 436.7, 0, true);
  const cathedral = PLACES.find(p => p.id === 'tuomiokirkko')!;
  const id = `t0_${Math.floor((cathedral.E - 23459500) / 250)}_${Math.floor((6704500 - cathedral.N) / 250)}`;
  expect((scenery as unknown as { wanted: string[] }).wanted).toContain(id);
  expect(scenery.progress.total).toBeGreaterThan(400);
});

test('photo range keeps fog and sky beyond scenery and Normal restores defaults', () => {
  const scene = new THREE.Scene();
  const renderer = new THREE.WebGPURenderer({ canvas: { width: 1, height: 1, style: {} } as HTMLCanvasElement });
  const env = new Environment(scene, renderer, 'medium');
  const camera = new THREE.PerspectiveCamera(60, 1, .1, 6000);
  env.setScreenshotDistance(camera, 5000);
  expect(camera.far).toBeGreaterThan(5000 * Math.sqrt(3));
  expect(env.sky.scale.x / 2).toBeGreaterThan(5000);
  expect((scene.fog as THREE.Fog).near).toBe(5000);
  env.setScreenshotDistance(camera, 0);
  expect(camera.far).toBe(6000); expect(env.sky.scale.x).toBe(4500);
  expect((scene.fog as THREE.Fog).near).toBe(500); expect((scene.fog as THREE.Fog).far).toBe(3500);
  env.dispose();
});

test('large views use derivative sources without requesting source regeneration', async () => {
  const { scenery, request, host, load } = setup();
  request.mockImplementation(async (url: string) => {
    if (url.includes('/scenery?')) return new Response(JSON.stringify({ tiles: [{ tile: 't0_-6_5', h: [0, 107], key: 'a'.repeat(64), source_sha256: 'base' }], missing: [] }));
    return new Response(JSON.stringify({ coarse: true, scenery: true, tile: 't0_-6_5', pass_kind: 'full',
      visual: { 't0_-6_5': { url: '../scenery/lod.test.glb', sha256: 'derived' } } }));
  });
  const view = { x: 3751.26, y: 164.15, z: 430.05, dx: -1, dz: 0, halfAngle: .8 };
  scenery.setRadius(6000, view); view.dx = 1;
  await settle(); scenery.update(3751, 430, 1000); await settle();
  expect(scenery.heading).toBe(270);
  expect(scenery.demand()).toEqual([]);
  expect(scenery.progress.loaded).toBe(1);
  expect(request.mock.calls.every(([url]) => url.includes('/scenery'))).toBe(true);
  expect(load).toHaveBeenCalledWith('http://localhost/coarse/scenery/lod.test.glb');
  expect(host.checkCollision).not.toHaveBeenCalled();
  scenery.update(5000, 1000, 40000); await settle();
  expect(load).toHaveBeenCalledTimes(1);
});

test('a ready skyline retry completes before hundreds of untouched scenery tiles', async () => {
  const { scenery, request, load } = setup();
  const chimney = 't0_-6_5';
  const attempts = new Map<string, number>();
  request.mockImplementation(async (url: string) => {
    if (url.includes('/scenery?')) {
      const tiles = new URL(url, 'http://localhost').searchParams.get('tiles')!.split(',');
      return new Response(JSON.stringify({ tiles: tiles.map(tile => ({ tile, key: tile,
        h: [0, tile === chimney ? 107 : 0], source_sha256: 'base' })), missing: [] }));
    }
    const tile = url.split('/').at(-1)!.replace('.json', '');
    const count = (attempts.get(tile) ?? 0) + 1; attempts.set(tile, count);
    if (tile !== chimney || count === 1) return new Response('{}', { status: 202 });
    return new Response(JSON.stringify({ coarse: true, tile, pass_kind: 'full',
      visual: { [tile]: { url: '../scenery/chimney.glb', sha256: 'derived' } } }));
  });
  scenery.setRadius(6000, { x: 3751.26, y: 164.15, z: 430.05, dx: -1, dz: 0, halfAngle: .8 });
  await settle();
  scenery.update(3751.26, 430.05, 0); await settle();
  expect(attempts.get(chimney)).toBe(1);
  expect(scenery.progress.total).toBeGreaterThan(100);
  scenery.update(3751.26, 430.05, 1000); await settle();
  expect(attempts.size).toBe(32); // Other scenery advances while the skyline retry is cooling down.
  scenery.update(3751.26, 430.05, 2000); await settle();
  expect(attempts.get(chimney)).toBe(2);
  expect(load).toHaveBeenCalledWith('http://localhost/coarse/scenery/chimney.glb');
  expect(scenery.progress.loaded).toBe(1);
});

test('live turns request the new sector, reuse brief look-backs and release old scenery', async () => {
  const { scenery, request, scene, load, dispose } = setup();
  const west = 't0_-6_5', east = 't0_30_1';
  request.mockImplementation(async (url: string) => {
    if (url.includes('/scenery?')) {
      const ids = new URL(url, 'http://localhost').searchParams.get('tiles')!.split(',');
      return new Response(JSON.stringify({ tiles: [west, east].filter(tile => ids.includes(tile))
        .map(tile => ({ tile, key: tile, h: [0, 107], source_sha256: 'base' })), missing: [] }));
    }
    const tile = url.split('/').at(-1)!.replace('.json', '');
    return new Response(JSON.stringify({ coarse: true, tile, pass_kind: 'full',
      visual: { [tile]: { url: `../scenery/${tile}.glb`, sha256: tile } } }));
  });
  const view = { x: 3751.26, y: 164.15, z: 430.05, dx: -1, dz: 0, halfAngle: .8 };
  scenery.setRadius(6000, view); await settle();
  scenery.update(view.x, view.z, 1000); await settle();
  expect(scenery.progress.loaded).toBe(1);
  const original = scene.children[0];
  scenery.setView({ ...view, dx: 1 }, 1200);
  await settle(); scenery.update(view.x, view.z, 1216); await settle();
  expect(scenery.heading).toBe(90); expect(scenery.progress.loaded).toBe(1);
  expect(load).toHaveBeenCalledWith('http://localhost/coarse/scenery/t0_30_1.glb');
  expect(scene.children).toContain(original);
  const catalogues = request.mock.calls.filter(([url]) => url.includes('/scenery?')).length;
  scenery.setView(view, 1500); scenery.update(view.x, view.z, 1500); await settle();
  expect(scenery.heading).toBe(270); expect(scenery.progress.loaded).toBe(1);
  expect(request.mock.calls.filter(([url]) => url.includes('/scenery?'))).toHaveLength(catalogues);
  expect(load).toHaveBeenCalledTimes(2); expect(scene.children).toContain(original);
  scenery.setView({ ...view, dx: 1 }, 8000); scenery.update(view.x, view.z, 8000); await settle();
  expect(dispose).toHaveBeenCalledWith(original, west);
  expect(scene.children).toHaveLength(1);
  expect(request.mock.calls.every(([url]) => url.includes('/scenery'))).toBe(true);
  scenery.setRadius(0); expect(scene.children).toHaveLength(0);
});

test('a turn during catalogue loading cannot restore the old sector', async () => {
  const { scenery, request, load } = setup();
  let release!: (response: Response) => void;
  request.mockImplementationOnce(() => new Promise(resolve => { release = resolve; }));
  request.mockImplementation(async (url: string) => {
    if (url.includes('/scenery?')) return new Response(JSON.stringify({ tiles: [{ tile: 't0_30_1', key: 'east', h: [0, 10], source_sha256: 'base' }], missing: [] }));
    return new Response(JSON.stringify({ coarse: true, tile: 't0_30_1', pass_kind: 'full',
      visual: { 't0_30_1': { url: '../scenery/east.glb', sha256: 'east' } } }));
  });
  const view = { x: 3751.26, y: 164.15, z: 430.05, dx: -1, dz: 0, halfAngle: .4 };
  scenery.setRadius(6000, view);
  scenery.setView({ ...view, dx: 1 }, 1000);
  release(new Response(JSON.stringify({ tiles: [{ tile: 't0_-6_5', key: 'west', h: [0, 107], source_sha256: 'base' }], missing: [] })));
  await settle(); scenery.update(view.x, view.z, 1000); await settle();
  scenery.update(view.x, view.z, 1016); await settle();
  expect(scenery.heading).toBe(90);
  expect(load).toHaveBeenCalledTimes(1);
  expect(load).toHaveBeenCalledWith('http://localhost/coarse/scenery/east.glb');
});
