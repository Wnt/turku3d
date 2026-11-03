import { test, expect, vi, afterEach } from 'vitest';
import * as THREE from 'three/webgpu';
import { StreamTrace, SLICES, streamTrace, tileDistance } from '../../src/telemetry/streamTrace';
import { CoarseTiles } from '../../src/world/coarseTiles';
import type { Manifest } from '../../src/world/manifest';
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); streamTrace.on = false; streamTrace.reset(); });

test('a trace that is off records nothing and costs nothing; on, events carry camera distance and rounded fields', () => {
  const trace = new StreamTrace(3);
  trace.event('tile.new', 't0_0_0'); trace.slice('parse', 4); trace.begin('tile.glb', 't0_0_0')();
  expect([trace.drain(), trace.takeSlices(), trace.takeCost()]).toEqual([[], null, 0]);
  trace.on = true; trace.setCamera(-100, 120.4, 125);
  trace.event('tile.full', 't0_0_0', { tris: 1234.5678, pass: 'full' });
  const [row] = trace.drain();
  expect(row).toMatchObject({ k: 'tile.full', id: 't0_0_0', d: 100, h: 120, tris: 1234.57, pass: 'full' });
  expect(tileDistance('t2_0_0', 500, 500)).toBe(0); expect(tileDistance('c_1_2', 0, 0)).toBeUndefined();
  const end = trace.begin('cell.glb', 't0_1_0', { bytes: 10 }); end({ status: 200 });
  expect(trace.drain()[0]).toMatchObject({ k: 'cell.glb', bytes: 10, status: 200, ms: expect.any(Number) });
  for (let i = 0; i < 5; i++) trace.event('band.props');
  expect([trace.drain().length, trace.dropped]).toEqual([3, 2]);
  expect(trace.time('install', () => 7)).toBe(7);
  trace.slice('upload', 1.234);
  const slices = trace.takeSlices()!;
  expect(slices).toHaveLength(SLICES.length); expect(slices[SLICES.indexOf('upload')]).toBe(1.23);
  expect(trace.takeSlices()).toBeNull(); expect(trace.takeCost()).toBeGreaterThanOrEqual(0);
});

test('captures switch the trace on, deliver tile events and frame slices, report their own cost and switch it off', async () => {
  const { DriveCapture } = await import('../../src/telemetry/capture');
  const render = { afterFrame: vi.fn(), renderer: { info: { frame: 1, memory: {} }, getPixelRatio: () => 1, backend: {} },
    camera: { position: Object.assign(new THREE.Vector3(10, 300, 20)), quaternion: new THREE.Quaternion(), fov: 60 }, perf: {} };
  class Observer { static supportedEntryTypes: string[] = []; }
  vi.stubGlobal('window', { __render: render }); vi.stubGlobal('PerformanceObserver', Observer);
  vi.stubGlobal('location', { href: 'http://localhost/' }); vi.stubGlobal('navigator', { userAgent: 'test' });
  vi.stubGlobal('innerWidth', 800); vi.stubGlobal('innerHeight', 600);
  vi.stubGlobal('addEventListener', vi.fn()); vi.stubGlobal('removeEventListener', vi.fn());
  const fetch = vi.fn().mockResolvedValue({ ok: true }); vi.stubGlobal('fetch', fetch);
  const coverageSnapshot = vi.fn(() => ({ i0: -4, j0: -4, n: 9, cells: 'F'.repeat(81) }));
  const game = { state: () => ({ paused: false, feet: [0, 0, 0], vel: [0, 0, 0] }), input: { actions: {} },
    coarse: { entries: new Map(), coverageSnapshot } };
  const recorder = new DriveCapture(game);
  expect(streamTrace.on).toBe(true);
  render.afterFrame();
  streamTrace.event('tile.full', 't0_0_0', { tris: 5 }); streamTrace.slice('install', 2);
  render.afterFrame();
  await recorder.stop();
  expect(streamTrace.on).toBe(false);
  const bodies = fetch.mock.calls.map(([, options]) => JSON.parse(options.body));
  const tiles = bodies.flatMap(body => body.tiles ?? []);
  expect(tiles.map(row => row.k)).toEqual(expect.arrayContaining(['coverage', 'tile.full']));
  expect(tiles.find(row => row.k === 'tile.full')).toMatchObject({ id: 't0_0_0', d: 0, h: 300 });
  const slices = bodies.flatMap(body => body.slices ?? []);
  expect(slices).toHaveLength(1); expect(slices[0][2 + SLICES.indexOf('install')]).toBe(2);
  const stats = bodies.flatMap(body => body.stats ?? []);
  expect(stats[0].recorder).toMatchObject({ dropped: 0 });
  expect(bodies.find(body => body.meta)?.meta.trace).toMatchObject({ version: 1, slices: [...SLICES] });
  expect(coverageSnapshot).toHaveBeenCalledWith(10, 20);
  expect(bodies.find(body => body.meta)?.meta.build).toMatchObject({ pending: 0 });
});

test('the build status names the HEAD stamped into the page at load and counts newer builds seen since', async () => {
  const { buildStatus, noteServedHead } = await import('../../src/core/build');
  const meta = { getAttribute: () => 'abc123+dirty' };
  vi.stubGlobal('document', { querySelector: (selector: string) => selector === 'meta[name="turku3d-build"]' ? meta : null });
  expect(buildStatus()).toMatchObject({ loaded: 'abc123', dirty: true, pending: 0 });
  noteServedHead('abc123'); noteServedHead('def456'); noteServedHead('def456'); noteServedHead('0789ab');
  expect(buildStatus()).toMatchObject({ loaded: 'abc123', latest: '0789ab', pending: 2 });
});

test('coverage snapshots name the band drawing each 250 m square, including holes and double coverage', () => {
  const scene = new THREE.Scene();
  const coarse = new CoarseTiles({ scene, manifest: { collision: {} } as Manifest, pageUrl: 'http://localhost/',
    fetch: vi.fn() as unknown as typeof globalThis.fetch, unloadCollision: () => {}, refreshCollision: () => {},
    checkCollision: async () => 2, renderer: {} as THREE.WebGPURenderer, loadModel: async () => new THREE.Group() });
  const add = (id: string, preview = false) => {
    const model = new THREE.Group(); scene.add(model);
    coarse.entries.set(id, { state: 'draft', model, pass: 'full', nextPoll: Infinity, collision: new Map(), ...(preview ? { preview: {} as never } : {}) });
  };
  add('t0_0_0'); add('t0_1_0', true); add('t0_-1_0');
  const states: Record<string, 'C' | 'c' | 'U' | undefined> = { 't0_-1_0': 'C', 't0_0_1': 'C', 't0_1_1': 'c', 't0_-1_1': 'U' };
  vi.spyOn(coarse.screenshotScenery, 'cellState').mockImplementation(id => states[id]);
  const { i0, j0, n, cells } = coarse.coverageSnapshot(125, 125, 1);
  expect([i0, j0, n]).toEqual([-1, -1, 3]);
  // Rows by z (j = -1, 0, 1), columns by x (i = -1, 0, 1).
  expect(cells).toBe('...' + 'DFP' + 'UCc');
});
