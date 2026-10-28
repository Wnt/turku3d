import { expect, test, vi } from 'vitest';
import * as THREE from 'three/webgpu';
import { context, mrt, normalView, velocity } from 'three/tsl';
import { compileModel } from '../../src/render/compileModel';

test('one-draw compilation preserves material arrays, children, visibility and renderer state across awaits', async () => {
  vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => { callback(0); return 1; });
  const renderer = new THREE.WebGPURenderer({ canvas: { width: 1, height: 1, style: {} } as HTMLCanvasElement });
  Object.assign(renderer, { _renderContexts: { get: () => ({}) } });
  const originalContext = renderer.contextNode, passContext = context({}), target = new THREE.RenderTarget();
  const scene = new THREE.Scene(), camera = new THREE.PerspectiveCamera();
  const materials = [new THREE.MeshBasicMaterial(), new THREE.MeshBasicMaterial()];
  const parent = new THREE.Mesh(new THREE.BoxGeometry(), materials), child = new THREE.Mesh();
  const water = new THREE.Mesh(new THREE.BoxGeometry(), new THREE.MeshBasicMaterial()); water.material.name = 'river-planar'; water.material.transparent = true;
  parent.add(child, water); scene.add(parent);
  const children = parent.children;
  const compile = vi.spyOn(renderer, 'compileAsync').mockImplementation((mesh, drawCamera, world) => {
    expect(mesh.children).toEqual([]); expect(Array.isArray((mesh as THREE.Mesh).material)).toBe(false);
    expect(mesh.frustumCulled).toBe(false); expect(renderer.contextNode).toBe(passContext);
    expect(renderer.getRenderTarget()).toBe(target); expect(drawCamera).toBe(camera); expect(world).toBe(scene);
    return Promise.resolve();
  });
  const prepared = compileModel(renderer, scene, scene, camera, target, null, passContext);
  expect(renderer.contextNode).toBe(originalContext); expect(renderer.getRenderTarget()).toBeNull();
  expect(parent.material).toBe(materials); expect(parent.children).toBe(children);
  await prepared;
  expect(compile).toHaveBeenCalledTimes(4);
  compile.mockClear();
  await compileModel(renderer, scene, scene, camera, target, null, passContext, true);
  expect(compile).toHaveBeenCalledTimes(3);
  expect(compile.mock.calls.some(([mesh]) => mesh === water)).toBe(false);
  expect(parent.children).toEqual([child, water]); expect(parent.material).toBe(materials);
  expect(renderer.contextNode).toBe(originalContext); expect(parent.frustumCulled).toBe(true);
  target.dispose();
  vi.unstubAllGlobals();
});

test('async shader stages retain their pass state while live rendering restores its own state', async () => {
  vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => { callback(0); return 1; });
  const renderer = new THREE.WebGPURenderer({ canvas: { width: 1, height: 1, style: {} } as HTMLCanvasElement });
  Object.assign(renderer, { _renderContexts: { get: () => ({}) } });
  const originalContext = renderer.contextNode, passContext = context({}), target = new THREE.RenderTarget();
  const scene = new THREE.Scene(), camera = new THREE.PerspectiveCamera();
  const mesh = new THREE.Mesh(new THREE.BoxGeometry(), new THREE.MeshStandardMaterial()); scene.add(mesh);
  type Builder = Parameters<NonNullable<typeof renderer.debug.onNodeBuilderCreated>>[0];
  const passMrt = mrt({ output: normalView, velocity });
  let compilationRenderer: Builder['renderer'] | undefined, finish: (() => void) | undefined;
  const previousBuilder = vi.fn(); renderer.debug.onNodeBuilderCreated = previousBuilder;
  vi.spyOn(renderer, 'compileAsync').mockImplementation(() => {
    const builder = { renderer } as unknown as Builder;
    renderer.debug.onNodeBuilderCreated!(builder, { object: mesh, material: mesh.material, camera, context: { pass: 'pre' } });
    compilationRenderer = builder.renderer;
    return new Promise(resolve => { finish = resolve; });
  });
  const prepared = compileModel(renderer, mesh, scene, camera, target, passMrt, passContext);
  expect(renderer.getRenderTarget()).toBeNull(); expect(renderer.contextNode).toBe(originalContext);
  // Simulate the next asynchronous generation stage after ordinary rendering has resumed.
  await Promise.resolve();
  expect(compilationRenderer!.getRenderTarget()).toBe(target);
  expect(compilationRenderer!.contextNode).toBe(passContext);
  expect(compilationRenderer!.getMRT()).toBe(passMrt);
  expect(compilationRenderer!.isOutputTarget).toBe(false);
  expect(previousBuilder).toHaveBeenCalledTimes(1);
  // An unrelated live draw must not inherit this hidden model's pass snapshot.
  const liveBuilder = { renderer } as unknown as Builder;
  renderer.debug.onNodeBuilderCreated!(liveBuilder, { object: new THREE.Mesh(), material: mesh.material, camera });
  expect(liveBuilder.renderer).toBe(renderer);
  finish!(); await prepared;
  expect(renderer.debug.onNodeBuilderCreated).toBe(previousBuilder);
  target.dispose(); vi.unstubAllGlobals();
});

test('failed compilation restores the builder hook and pass state', async () => {
  const renderer = new THREE.WebGPURenderer({ canvas: { width: 1, height: 1, style: {} } as HTMLCanvasElement });
  Object.assign(renderer, { _renderContexts: { get: () => ({}) } });
  const previousBuilder = vi.fn(); renderer.debug.onNodeBuilderCreated = previousBuilder;
  const scene = new THREE.Scene(), camera = new THREE.PerspectiveCamera(), target = new THREE.RenderTarget();
  const mesh = new THREE.Mesh(new THREE.BoxGeometry(), new THREE.MeshStandardMaterial());
  vi.spyOn(renderer, 'compileAsync').mockRejectedValue(new Error('compile failed'));
  await expect(compileModel(renderer, mesh, scene, camera, target, null, renderer.contextNode)).rejects.toThrow('compile failed');
  expect(renderer.debug.onNodeBuilderCreated).toBe(previousBuilder); expect(renderer.getRenderTarget()).toBeNull();
  target.dispose();
});

test('compilation selects the observed nested render context and restores the context manager', async () => {
  const renderer = new THREE.WebGPURenderer({ canvas: { width: 1, height: 1, style: {} } as HTMLCanvasElement });
  const get = vi.fn((_target?: THREE.RenderTarget, _mrt?: unknown, _depth?: number) => ({})), contexts = { get }; Object.assign(renderer, { _renderContexts: contexts });
  const scene = new THREE.Scene(), camera = new THREE.PerspectiveCamera(), target = new THREE.RenderTarget();
  const mesh = new THREE.Mesh(new THREE.BoxGeometry(), new THREE.MeshStandardMaterial());
  vi.spyOn(renderer, 'compileAsync').mockImplementation(() => { contexts.get(target, null); return Promise.resolve(); });
  await compileModel(renderer, mesh, scene, camera, target, null, renderer.contextNode, false, 3);
  expect(get).toHaveBeenCalledWith(target, null, 3); expect(contexts.get).toBe(get);
  target.dispose();
});
