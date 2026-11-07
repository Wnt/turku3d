import { expect, test, vi } from 'vitest';
import type * as THREE from 'three/webgpu';
import { Object3D, PerspectiveCamera, Scene } from 'three/webgpu';
import { asyncFirstUseBuilds } from '../../src/render/asyncNodeBuilds';

test('a live-frame draw without a built program is skipped and built after the frame; built, cached and quad draws go ahead', async () => {
  vi.stubGlobal('requestAnimationFrame', (callback: () => void) => setTimeout(callback, 0));
  const states = new Map<object, { nodeBuilderState?: unknown }>();
  const cache = new Map<number, unknown>();
  const renderObjects = new Map<object, { isRenderObject: true; key: number }>();
  let builds = 0, finish: (() => void) | undefined;
  const nodes = {
    nodeBuilderCache: cache,
    get(object: object) { let data = states.get(object); if (!data) states.set(object, data = {}); return data; },
    getForRenderCacheKey: (draw: { key: number }) => draw.key,
    getForRenderAsync(draw: { key: number }) {
      builds++;
      return new Promise<void>(resolve => { finish = () => { cache.set(draw.key, 'state'); this.get(draw).nodeBuilderState = 'state'; resolve(); }; });
    },
  };
  const objects = { get: (object: { key: number }) => {
    let draw = renderObjects.get(object);
    if (!draw) renderObjects.set(object, draw = { isRenderObject: true, key: object.key });
    return draw;
  } };
  const renderer = { _nodes: nodes, _objects: objects, debug: { onNodeBuilderCreated: null } } as unknown as THREE.WebGPURenderer;
  let live = true;
  const { ready, settled } = asyncFirstUseBuilds(renderer, () => live);
  const draw = (object: object) => ready(object as THREE.Object3D, {} as THREE.Material, {} as THREE.Scene, {} as THREE.Camera, null, null, null);
  const facade = { key: 1 }, sameProgram = { key: 1 }, quad = { key: 2, isQuadMesh: true }, known = { key: 3 };
  cache.set(3, 'state');
  expect(draw(facade)).toBe(false);      // unbuilt: skipped this frame
  expect(draw(sameProgram)).toBe(false); // another draw of the same program waits for the same build
  expect(draw(quad)).toBe(true);         // effect quads keep r186's synchronous first frame
  expect(draw(known)).toBe(true);        // a cached build (retained or shared) draws at once
  live = false;
  expect(draw({ key: 4 })).toBe(true);   // preparation and other renders build synchronously as before
  expect(builds).toBe(0);                // nothing is built inside the frame
  for (let i = 0; i < 40 && builds === 0; i++) await new Promise(resolve => setTimeout(resolve, 5));
  await new Promise(resolve => setTimeout(resolve, 10));
  expect(builds).toBe(1);                // one build after the frame for both draws
  let done = false; const waiting = settled().then(() => { done = true; });
  await Promise.resolve(); expect(done).toBe(false);
  finish!(); await waiting;
  live = true;
  expect(draw(facade)).toBe(true);
  expect(draw(sameProgram)).toBe(true);
  vi.unstubAllGlobals();
});

test('r186 routes every draw through _renderObjectDirect via _handleObjectFunction (the hook GameRenderer overrides)', async () => {
  const fs = await import('node:fs');
  const source = fs.readFileSync(new URL('../../node_modules/three/src/renderers/common/Renderer.js', import.meta.url), 'utf8');
  expect(source).toMatch(/this\._handleObjectFunction = this\._renderObjectDirect;/);
  expect(source).toMatch(/_renderObjectDirect\( object, material, scene, camera, lightsNode, group, clippingContext, passId \) \{\s*const renderObject = this\._objects\.get\( object, material, scene, camera, lightsNode, this\._currentRenderContext, clippingContext, passId \);/);
});

test('deferred builds run one at a time: the next starts only after the previous settled, on its own turn', async () => {
  vi.stubGlobal('requestAnimationFrame', (callback: () => void) => setTimeout(callback, 0));
  const cache = new Map<number, unknown>(), states = new Map<object, { nodeBuilderState?: unknown }>();
  const started: number[] = [], finish: (() => void)[] = [];
  const nodes = {
    nodeBuilderCache: cache,
    get(object: object) { let data = states.get(object); if (!data) states.set(object, data = {}); return data; },
    getForRenderCacheKey: (draw: { key: number }) => draw.key,
    getForRenderAsync(draw: { key: number }) {
      started.push(draw.key);
      // The second build fails: the queue still goes on.
      return new Promise<void>((resolve, reject) => finish.push(() => { if (draw.key === 2) reject(new Error('lost')); else { cache.set(draw.key, 1); resolve(); } }));
    },
  };
  const renderer = { _nodes: nodes, _objects: { get: (object: { key: number }) => ({ isRenderObject: true, key: object.key }) },
    debug: { onNodeBuilderCreated: null } } as unknown as THREE.WebGPURenderer;
  const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
  const { ready, settled } = asyncFirstUseBuilds(renderer, () => true);
  for (const key of [1, 2, 3]) ready({ key } as unknown as THREE.Object3D, {} as THREE.Material, {} as THREE.Scene, {} as THREE.Camera, null, null, null);
  const until = async (n: number) => { for (let i = 0; i < 40 && started.length < n; i++) await new Promise(resolve => setTimeout(resolve, 5));
    await new Promise(resolve => setTimeout(resolve, 10)); };
  await until(1); expect(started).toEqual([1]);
  finish[0](); await until(2); expect(started).toEqual([1, 2]);
  finish[1](); await until(3); expect(started).toEqual([1, 2, 3]);
  finish[2](); await settled();
  expect(warn).toHaveBeenCalledTimes(1);
  warn.mockRestore(); vi.unstubAllGlobals();
});

test('visible builds jump the queue, nearest first; detached objects leave unbuilt; the share grows with a visible wait', async () => {
  vi.stubGlobal('requestAnimationFrame', (callback: () => void) => setTimeout(callback, 0));
  let t = 0;
  const cache = new Map<number, unknown>(), states = new Map<object, { nodeBuilderState?: unknown }>();
  const started: number[] = [], finish: (() => void)[] = [];
  const nodes = {
    nodeBuilderCache: cache,
    get(object: object) { let data = states.get(object); if (!data) states.set(object, data = {}); return data; },
    getForRenderCacheKey: (draw: { key: number }) => draw.key,
    getForRenderAsync(draw: { key: number }) { started.push(draw.key); return new Promise<void>(resolve => finish.push(() => { cache.set(draw.key, 1); resolve(); })); },
  };
  const renderer = { _nodes: nodes, _objects: { get: (object: { key: number }) => ({ isRenderObject: true, key: object.key }) },
    debug: { onNodeBuilderCreated: null } } as unknown as THREE.WebGPURenderer;
  const scene = new Scene(), camera = new PerspectiveCamera();
  const mesh = (key: number, z: number, attached = true) => { const m = new Object3D() as THREE.Object3D & { key: number }; m.key = key; m.position.z = z;
    if (attached) scene.add(m); m.updateMatrixWorld(); return m; };
  const builds = asyncFirstUseBuilds(renderer, () => true, () => t);
  const draw = (o: THREE.Object3D) => builds.ready(o, {} as THREE.Material, scene, camera, null, null, null);
  // A warm-up burst at t = 0, all drawn: the nearest (3) builds first. 4 is drawn once and its tile then leaves.
  const [a, b, c] = [mesh(1, -300), mesh(2, -200), mesh(3, -100)], gone = mesh(4, -500);
  for (const o of [a, b, c, gone]) draw(o);
  const tick = async () => { const n = started.length; for (let i = 0; i < 40 && started.length === n && n < 3; i++) await new Promise(resolve => setTimeout(resolve, 5));
    await new Promise(resolve => setTimeout(resolve, 10)); };
  await tick(); expect(started).toEqual([3]);
  // Seconds later the camera sees 1 again; 2 and 4 are not drawn, and 4's tile left the scene.
  t = 3000; scene.remove(gone); draw(a);
  expect(builds.share).toBe(6);              // visible just now: the base share
  t = 3400; draw(a);
  expect(builds.share).toBeGreaterThan(6);   // visible and waiting 0.4 s: more
  expect(builds.share).toBeLessThanOrEqual(120);
  finish[0](); await tick(); expect(started).toEqual([3, 1]);   // the visible one ahead of 2, queued earlier
  finish[1](); await tick(); expect(started).toEqual([3, 1, 2]);
  finish[2](); await builds.settled();
  expect(started).toEqual([3, 1, 2]);    // 4 left unbuilt
  expect(builds.stats()).toMatchObject({ queued: 0, built: 3, dropped: 1 });
  t = 4000; expect(builds.share).toBe(0);
  vi.unstubAllGlobals();
});

test('a draw that goes ahead hands its render object to r186\'s next lookup with the same arguments, once', () => {
  const states = new Map<object, { nodeBuilderState?: unknown }>();
  let lookups = 0;
  const nodes = { nodeBuilderCache: new Map<number, unknown>(), get(object: object) { let d = states.get(object); if (!d) states.set(object, d = { nodeBuilderState: 'built' }); return d; },
    getForRenderCacheKey: () => 1, getForRenderAsync: () => Promise.resolve() };
  const objects = { get: (object: object) => { lookups++; return { isRenderObject: true, of: object }; } };
  const renderer = { _nodes: nodes, _objects: objects, _currentRenderContext: 'ctx', debug: { onNodeBuilderCreated: null } } as unknown as THREE.WebGPURenderer;
  const { ready } = asyncFirstUseBuilds(renderer, () => true);
  const internal = renderer as unknown as { _objects: { get(...a: unknown[]): unknown } };
  const object = new Object3D(), material = {} as THREE.Material, scene = new Scene(), camera = new PerspectiveCamera();
  expect(ready(object, material, scene, camera, null, null, null, 'p')).toBe(true);
  expect(lookups).toBe(1);
  const first = internal._objects.get(object, material, scene, camera, null, 'ctx', null, 'p');   // r186's own lookup
  expect(lookups).toBe(1); expect((first as { of: object }).of).toBe(object);
  internal._objects.get(object, material, scene, camera, null, 'ctx', null, 'p');                  // handed over once only
  expect(lookups).toBe(2);
  expect(ready(object, material, scene, camera, null, null, null, 'p')).toBe(true);
  internal._objects.get(object, material, scene, camera, null, 'ctx', null, 'other pass');         // different arguments: a real lookup
  expect(lookups).toBe(4);
});
