import { expect, test, vi } from 'vitest';
import * as THREE from 'three/webgpu';
import { context, mrt, normalView, velocity } from 'three/tsl';
import { compileModel } from '../../src/render/compileModel';
import { GameRenderer } from '../../src/render/shadowMaterials';

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

test.each([false, true])('async shader stages retain pass state with override=%s while live rendering resumes', async override => {
  vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => { callback(0); return 1; });
  const renderer = new THREE.WebGPURenderer({ canvas: { width: 1, height: 1, style: {} } as HTMLCanvasElement });
  Object.assign(renderer, { _renderContexts: { get: () => ({}) } });
  const originalContext = renderer.contextNode, passContext = context({}), target = new THREE.RenderTarget();
  const scene = new THREE.Scene(), camera = new THREE.PerspectiveCamera();
  const mesh = new THREE.Mesh(new THREE.BoxGeometry(), new THREE.MeshStandardMaterial()); scene.add(mesh);
  type Builder = Parameters<NonNullable<typeof renderer.debug.onNodeBuilderCreated>>[0];
  const passMrt = mrt({ output: normalView, velocity });
  const passMaterial = override ? new THREE.MeshBasicNodeMaterial() : null;
  let compilationRenderer: Builder['renderer'] | undefined, finish: (() => void) | undefined;
  const previousBuilder = vi.fn(); renderer.debug.onNodeBuilderCreated = previousBuilder;
  vi.spyOn(renderer, 'compileAsync').mockImplementation(() => {
    const builder = { renderer } as unknown as Builder;
    expect(scene.overrideMaterial).toBe(passMaterial);
    renderer.debug.onNodeBuilderCreated!(builder, { object: mesh, material: passMaterial ?? mesh.material, camera, context: { pass: 'pre' } });
    compilationRenderer = builder.renderer;
    return new Promise(resolve => { finish = resolve; });
  });
  const prepared = compileModel(renderer, mesh, scene, camera, target, passMrt, passContext, false, 0, passMaterial);
  expect(scene.overrideMaterial).toBeNull();
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

test('compiles in one frame share one light walk of the world; a new frame walks again', async () => {
  vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => { callback(0); return 1; });
  const renderer = new THREE.WebGPURenderer({ canvas: { width: 1, height: 1, style: {} } as HTMLCanvasElement });
  Object.assign(renderer, { _renderContexts: { get: () => ({}) } });
  const scene = new THREE.Scene(), camera = new THREE.PerspectiveCamera(), target = new THREE.RenderTarget();
  for (let i = 0; i < 200; i++) scene.add(new THREE.Group().add(new THREE.Object3D(), new THREE.Object3D()));
  const sun = new THREE.DirectionalLight(), hidden = new THREE.PointLight(), beam = new THREE.SpotLight();
  hidden.visible = false;
  scene.add(sun, hidden, new THREE.Group().add(beam));
  const model = new THREE.Group();
  for (let i = 0; i < 3; i++) model.add(new THREE.Mesh(new THREE.BoxGeometry(), new THREE.MeshBasicMaterial()));
  scene.add(model);
  let worldWalks = 0;
  const walk = scene.traverseVisible;
  vi.spyOn(THREE.Object3D.prototype, 'traverseVisible').mockImplementation(function (this: THREE.Object3D, callback) {
    if (this === scene) worldWalks++;
    walk.call(this, callback);
  });
  const seen: THREE.Object3D[][] = [];
  vi.spyOn(renderer, 'compileAsync').mockImplementation((_mesh, _camera, world) => {
    // As r186 compileAsync: include lights from the target scene.
    const lights: THREE.Object3D[] = [];
    world!.traverseVisible(object => { if ((object as THREE.Light).isLight) lights.push(object); });
    seen.push(lights);
    return Promise.resolve();
  });
  await compileModel(renderer, model, scene, camera, target, null, renderer.contextNode);
  expect(seen).toEqual([[sun, beam], [sun, beam], [sun, beam]]);
  expect(worldWalks).toBe(1); // r186 alone walks the world once per compile: three here
  expect(Object.hasOwn(scene, 'traverseVisible')).toBe(false);
  const lamp = new THREE.PointLight(); scene.add(lamp);
  (renderer.info as { frame: number }).frame++;
  seen.length = 0;
  await compileModel(renderer, model, scene, camera, target, null, renderer.contextNode);
  expect(seen).toEqual([[sun, beam, lamp], [sun, beam, lamp], [sun, beam, lamp]]);
  expect(worldWalks).toBe(2);
  target.dispose();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

test('a live cascade shadow draw records its pass for preparation; precompiles and repeats do not', () => {
  const renderer = new GameRenderer({ canvas: { width: 1, height: 1, style: {} } as HTMLCanvasElement });
  const base = vi.spyOn(THREE.WebGPURenderer.prototype, 'renderObject').mockImplementation(() => {});
  try {
    const scene = new THREE.Scene(), camera = new THREE.OrthographicCamera(), target = new THREE.RenderTarget();
    const mesh = new THREE.Mesh(new THREE.BoxGeometry(), new THREE.MeshStandardMaterial());
    const override = Object.assign(new THREE.NodeMaterial(), { isShadowPassMaterial: true, name: 'ShadowMaterial' });
    scene.overrideMaterial = override; renderer.setRenderTarget(target);
    const draw = () => renderer.renderObject(mesh, scene, camera, mesh.geometry, mesh.material, null, null as never);
    draw();
    const pass = renderer.shadowPasses.get(override)!;
    expect(pass.camera).toBe(camera); expect(pass.target).toBe(target); expect(pass.override).toBe(override);
    expect(scene.overrideMaterial).toBe(override); // restored after the draw
    draw(); expect(renderer.shadowPasses.size).toBe(1); expect(renderer.shadowPasses.get(override)).toBe(pass);
    const rebuilt = new THREE.RenderTarget(); renderer.setRenderTarget(rebuilt); draw();
    expect(renderer.shadowPasses.get(override)!.target).toBe(rebuilt); // a rebuilt cascade map replaces the record
    const other = Object.assign(new THREE.NodeMaterial(), { isShadowPassMaterial: true });
    scene.overrideMaterial = other; (renderer as unknown as { _isPreCompiling: boolean })._isPreCompiling = true; draw();
    expect(renderer.shadowPasses.has(other)).toBe(false);
    target.dispose(); rebuilt.dispose();
  } finally { base.mockRestore(); }
});

test('shadow preparation compiles casters only, with the cascade\'s override and context', async () => {
  const renderer = new THREE.WebGPURenderer({ canvas: { width: 1, height: 1, style: {} } as HTMLCanvasElement });
  Object.assign(renderer, { _renderContexts: { get: () => ({}) } });
  const scene = new THREE.Scene(), camera = new THREE.OrthographicCamera(), target = new THREE.RenderTarget(), shadowContext = context({});
  const caster = new THREE.Mesh(new THREE.BoxGeometry(), new THREE.MeshStandardMaterial()), receiver = new THREE.Mesh(new THREE.BoxGeometry(), new THREE.MeshStandardMaterial());
  caster.castShadow = true; scene.add(caster, receiver);
  const override = Object.assign(new THREE.NodeMaterial(), { isShadowPassMaterial: true });
  const compiled: THREE.Object3D[] = [];
  vi.spyOn(renderer, 'compileAsync').mockImplementation(mesh => {
    expect(scene.overrideMaterial).toBe(override); expect(renderer.contextNode).toBe(shadowContext); expect(renderer.getRenderTarget()).toBe(target);
    compiled.push(mesh); return Promise.resolve();
  });
  await compileModel(renderer, scene, scene, camera, target, null, shadowContext, false, 2, override, true);
  expect(compiled).toEqual([caster]); expect(scene.overrideMaterial).toBeNull();
  target.dispose();
});

test('draws that share a program compile once per pass; object-specific instancing still compiles each', async () => {
  const renderer = new THREE.WebGPURenderer({ canvas: { width: 1, height: 1, style: {} } as HTMLCanvasElement });
  Object.assign(renderer, { _renderContexts: { get: () => ({}) } });
  const scene = new THREE.Scene(), camera = new THREE.PerspectiveCamera(), target = new THREE.RenderTarget();
  const material = new THREE.MeshStandardMaterial(), geometry = new THREE.BoxGeometry();
  const same = [new THREE.Mesh(geometry, material), new THREE.Mesh(new THREE.BoxGeometry(2, 2, 2), material)];
  const receiver = new THREE.Mesh(geometry, material); receiver.receiveShadow = true;
  const own = [new THREE.InstancedMesh(geometry, material, 4), new THREE.InstancedMesh(geometry, material, 4)];
  const shared = [new THREE.InstancedMesh(geometry, material, 4), new THREE.InstancedMesh(geometry, material, 4)];
  for (const mesh of shared) mesh.userData.sharedInstanceProgram = true;
  scene.add(...same, receiver, ...own, ...shared);
  const compiled: THREE.Object3D[] = [];
  vi.spyOn(renderer, 'compileAsync').mockImplementation(mesh => { compiled.push(mesh); return Promise.resolve(); });
  await compileModel(renderer, scene, scene, camera, target, null, renderer.contextNode);
  expect(compiled).toEqual([same[0], receiver, ...own, shared[0]]);
  target.dispose();
});
