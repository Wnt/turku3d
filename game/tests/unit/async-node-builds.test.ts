import { expect, test, vi } from 'vitest';
import type * as THREE from 'three/webgpu';
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
  await new Promise(resolve => setTimeout(resolve, 5));
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
