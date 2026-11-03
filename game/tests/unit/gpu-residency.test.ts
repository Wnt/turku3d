import { afterEach, expect, test } from 'vitest';
import * as THREE from 'three/webgpu';
import { GpuMeter, modelGpuBytes, rendererResidency } from '../../src/world/gpuBytes';
import { CoarseTiles } from '../../src/world/coarseTiles';
import { setRenderTier } from '../../src/render/quality';
import type { Manifest } from '../../src/world/manifest';

const KiB64 = 64 * 1024;
function block(vertices: number) {
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.BufferAttribute(new Float32Array(vertices * 3), 3));
  geometry.setIndex(new THREE.BufferAttribute(new Uint32Array(vertices), 1));
  return geometry;
}

test('the meter counts buffers at 64 KiB placement from counts (released arrays too), shared buffers and textures once', () => {
  const geometry = block(100_000); // 1.2 MB positions -> 19 placements; 0.4 MB index -> 7
  const mesh = new THREE.Mesh(geometry, new THREE.MeshBasicMaterial());
  expect(modelGpuBytes(mesh)).toBe(19 * KiB64 + 7 * KiB64);
  (geometry.attributes.position as THREE.BufferAttribute).array = new Float32Array(0); // CPU copy released
  expect(modelGpuBytes(mesh)).toBe(19 * KiB64 + 7 * KiB64);
  const twin = new THREE.Mesh(geometry, mesh.material);
  const meter = new GpuMeter();
  expect(meter.root(mesh) + meter.root(twin)).toBe(26 * KiB64); // the shared geometry counts once
  const texture = new THREE.DataTexture(new Uint8Array(256 * 256 * 4), 256, 256);
  const textured = new THREE.Mesh(block(10), new THREE.MeshBasicMaterial({ map: texture }));
  const again = new THREE.Mesh(block(10), new THREE.MeshBasicMaterial({ map: texture.clone() }));
  const counted = new GpuMeter();
  expect(counted.root(textured)).toBe(2 * KiB64 + 256 * 256 * 4);
  expect(counted.root(again)).toBe(2 * KiB64); // same texture source
});

afterEach(() => setRenderTier(null as never));

test('over its tier budget the streamer drops cached, then distant, then far tiles, never the window, and stops lead installs', () => {
  setRenderTier({ tier: 'reduced', source: 'device', gpu: null });
  const coarse = new CoarseTiles({ scene: new THREE.Scene(), manifest: { collision: {} } as unknown as Manifest,
    renderer: {} as THREE.WebGPURenderer, pageUrl: 'http://localhost/', unloadCollision: () => {}, refreshCollision: () => {},
    checkCollision: async () => {}, loadModel: async () => new THREE.Group(),
    fetch: (async () => new Response(null, { status: 404 })) as unknown as typeof fetch });
  const model = () => { const root = new THREE.Group(); root.add(new THREE.Mesh(block(1_000_000), new THREE.MeshBasicMaterial())); return root; };
  const each = modelGpuBytes(model());
  const internals = coarse as unknown as { gpu: { bytes: number }; lastWanted: string[]; lead: string[]; at: { x: number; z: number };
    applyGpuLimit(limit: number): void; overGpu: boolean };
  const tile = (id: string, extra: Record<string, unknown> = {}) => coarse.entries.set(id, { state: 'draft', collision: new Map(), nextPoll: 0, model: model(), pass: 'full', ...extra } as never);
  tile('t0_0_0'); tile('t0_1_0'); // the window
  tile('t0_5_0', { cachedAt: 10 }); tile('t0_6_0', { cachedAt: 5 });
  tile('t0_3_0', { distant: true }); tile('t0_4_0'); tile('t0_2_2');
  internals.lastWanted = ['t0_0_0', 't0_1_0']; internals.lead = ['t0_2_2']; internals.at = { x: 125, z: 125 };
  const limit = 0.8 * 2 ** 30;
  internals.gpu.bytes = limit + 2.5 * each;
  internals.applyGpuLimit(limit);
  expect([...coarse.entries.keys()].sort()).toEqual(['t0_0_0', 't0_1_0', 't0_2_2', 't0_4_0'].sort()); // cached x2, distant
  expect(internals.overGpu).toBe(true);
  internals.gpu.bytes = limit + 10 * each; // far beyond: off-lead first, then lead, never the window
  internals.applyGpuLimit(limit);
  expect([...coarse.entries.keys()].sort()).toEqual(['t0_0_0', 't0_1_0']);
  internals.gpu.bytes = 0.5 * limit;
  internals.applyGpuLimit(limit);
  expect(internals.overGpu).toBe(false);
});

test('Apple reduced (unified memory) budgets JS heap + GPU bytes; a discrete reduced card gets 1.2 GiB; URL overrides win', () => {
  const coarse = new CoarseTiles({ scene: new THREE.Scene(), manifest: { collision: {} } as unknown as Manifest,
    renderer: {} as THREE.WebGPURenderer, pageUrl: 'http://localhost/', unloadCollision: () => {}, refreshCollision: () => {},
    checkCollision: async () => {}, loadModel: async () => new THREE.Group(),
    fetch: (async () => new Response(null, { status: 404 })) as unknown as typeof fetch });
  const GiB = 2 ** 30, host = (coarse as unknown as { host: { gpuBudget?: number; memoryBudget?: number } }).host;
  const limit = () => (coarse as unknown as { gpuLimit(): number | undefined }).gpuLimit();
  const heap = (bytes: number | undefined) => Object.defineProperty(performance, 'memory', { value: bytes === undefined ? undefined : { usedJSHeapSize: bytes }, configurable: true });
  try {
    heap(1.5 * GiB);
    setRenderTier({ tier: 'reduced', source: 'device', gpu: { vendor: 'amd', architecture: 'gcn-4' } });
    expect(limit()).toBe(1.2 * GiB); // the 560X: GPU bytes only, whatever the heap
    setRenderTier({ tier: 'reduced', source: 'device', gpu: { vendor: 'apple', architecture: 'metal-3' } });
    expect(limit()).toBe(1.5 * GiB); // 3 GiB total - 1.5 GiB heap
    expect(coarse.gpuResidency().rule).toBe('apple-memory');
    heap(1.8 * GiB); expect(limit()).toBeCloseTo(1.2 * GiB, -3);
    heap(2.6 * GiB); expect(limit()).toBe(0.9 * GiB); // floor: the window, lead and scenery still fit
    heap(0.5 * GiB); expect(limit()).toBe(1.5 * GiB); // ceiling
    host.memoryBudget = 2.5 * GiB; heap(1.5 * GiB); expect(limit()).toBe(1.0 * GiB); // ?memBudget=2.5
    host.memoryBudget = undefined;
    heap(undefined); expect(limit()).toBe(1.2 * GiB); // no heap reading: the tier's GPU budget
    setRenderTier({ tier: 'medium', source: 'default', gpu: { vendor: 'apple', architecture: 'metal-3' } });
    expect(limit()).toBeUndefined(); // medium (the M1 Max): no budget
    host.gpuBudget = 0.6 * GiB; expect(limit()).toBe(0.6 * GiB); // ?gpuBudget=0.6
    expect(coarse.gpuResidency().rule).toBe('url');
    host.gpuBudget = 0; expect(limit()).toBeUndefined(); // ?gpuBudget=0
  } finally { Reflect.deleteProperty(performance, 'memory'); }
});

test('with the renderer record the meter counts only uploaded content: own buffers at GPU size with placement, textures with a GPU texture', () => {
  const data = new WeakMap<object, { buffer?: { size: number }; texture?: object }>();
  const residency = rendererResidency({ backend: { data } })!;
  const drawn = block(1000), hidden = block(1000); // 12 KB positions, 4 KB index each
  data.set(drawn.attributes.position, { buffer: { size: 12_000 } }); data.set(drawn.index!, { buffer: { size: 4000 } });
  const map = new THREE.DataTexture(new Uint8Array(64 * 64 * 4), 64, 64), unsent = new THREE.DataTexture(new Uint8Array(32 * 32 * 4), 32, 32);
  data.set(map, { texture: {} });
  const root = new THREE.Group();
  root.add(new THREE.Mesh(drawn, new THREE.MeshBasicMaterial({ map })), new THREE.Mesh(hidden, new THREE.MeshBasicMaterial({ map: unsent })));
  expect(modelGpuBytes(root, residency)).toBe(2 * KiB64 + 64 * 64 * 4); // the hidden merge source and unsent texture: nothing
  expect(modelGpuBytes(root)).toBe(4 * KiB64 + 64 * 64 * 4 + 32 * 32 * 4); // the estimate without the record
  // A pooled target (render/geometryPool.ts) counts its slot, no placement.
  const pooled = new GpuMeter({ buffer: target => target === drawn.attributes.position ? 12_032 : 0, texture: () => false });
  expect(pooled.root(new THREE.Mesh(drawn, new THREE.MeshBasicMaterial()))).toBe(12_032);
  expect(rendererResidency({})).toBeUndefined();
});

test('startup installs are never stopped or evicted by the budget; over it, settling waits for the window only', () => {
  setRenderTier({ tier: 'reduced', source: 'device', gpu: null });
  const coarse = new CoarseTiles({ scene: new THREE.Scene(), manifest: { collision: {} } as unknown as Manifest,
    renderer: {} as THREE.WebGPURenderer, pageUrl: 'http://localhost/', unloadCollision: () => {}, refreshCollision: () => {},
    checkCollision: async () => {}, loadModel: async () => new THREE.Group(),
    fetch: (async () => new Response(null, { status: 404 })) as unknown as typeof fetch });
  const internals = coarse as unknown as { gpu: { bytes: number }; lastWanted: string[]; lead: string[]; at: { x: number; z: number };
    applyGpuLimit(limit: number): void; overGpu: boolean; startup: unknown };
  const model = () => { const root = new THREE.Group(); root.add(new THREE.Mesh(block(1_000_000), new THREE.MeshBasicMaterial())); return root; };
  coarse.entries.set('t0_4_0', { state: 'draft', collision: new Map(), nextPoll: 0, model: model(), pass: 'full', cachedAt: 1 } as never);
  internals.at = { x: 125, z: 125 };
  internals.startup = { points: [{ x: 125, z: 125 }], radius: 250 }; // preload: the menu waits for these tiles
  internals.gpu.bytes = 10 * 2 ** 30;
  internals.applyGpuLimit(2 ** 30);
  expect(internals.overGpu).toBe(false);
  expect(coarse.entries.has('t0_4_0')).toBe(true);
  internals.startup = null;
  internals.applyGpuLimit(2 ** 30);
  expect(internals.overGpu).toBe(true);
  expect(coarse.entries.has('t0_4_0')).toBe(false);
});
