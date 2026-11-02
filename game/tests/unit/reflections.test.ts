import { expect, test, vi } from 'vitest';
import * as THREE from 'three/webgpu';
import { REFLECTION_LAYER, reflectionSurface, ReflectionShadows, ReflectionCadence } from '../../src/render/reflections';
import { RiverMaterial } from '../../src/render/water';
import { Environment } from '../../src/render/environment';

test('river pass includes large static surfaces and excludes water, props and vegetation', () => {
  const owner = new THREE.Group(), mesh = new THREE.Mesh(new THREE.BoxGeometry(10, 10, 10)); owner.add(mesh);
  for (const layer of ['ground', 'buildings', 'structures', 'massing', 'roads', 'boats']) {
    owner.userData.layer = layer; expect(reflectionSurface(mesh)).toBe(true);
  }
  for (const layer of ['props', 'vegetation']) {
    owner.userData.layer = layer; expect(reflectionSurface(mesh)).toBe(false);
  }
  owner.userData.layer = 'boats'; mesh.userData.reflection_surface = false;
  expect(reflectionSurface(mesh)).toBe(false);
  delete mesh.userData.reflection_surface;
  owner.userData.layer = 'ground'; mesh.userData.material_key = 'water';
  expect(reflectionSurface(mesh)).toBe(false);
  delete mesh.userData.material_key; mesh.userData.shadowBatch = true;
  expect(reflectionSurface(mesh)).toBe(false);
});

test('reflection warmup uses its real target and layer mask without leaking state across awaits', async () => {
  vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => { callback(0); return 1; });
  const renderer = new THREE.WebGPURenderer({ canvas: { width: 1, height: 1, style: {} } as HTMLCanvasElement });
  const getContext = vi.fn((_target?: THREE.RenderTarget | null, _mrt?: unknown, _depth?: number) => ({}));
  const contexts = { get: getContext }; Object.assign(renderer, { _renderContexts: contexts });
  const scene = new THREE.Scene(), camera = new THREE.PerspectiveCamera();
  const env = new Environment(scene, renderer, 'medium');
  env.sky.layers.disable(REFLECTION_LAYER);
  const water = new THREE.Mesh(new THREE.PlaneGeometry(10, 10).rotateX(-Math.PI / 2));
  const river = new RiverMaterial(water, env, 'medium', true, renderer);
  const model = new THREE.Group(), surface = new THREE.Mesh();
  model.add(surface); scene.add(model); model.visible = false; surface.layers.enable(REFLECTION_LAYER);
  let complete!: () => void;
  const compilation = new Promise<void>(resolve => { complete = resolve; });
  const compile = vi.spyOn(renderer, 'compileAsync').mockImplementation((object, reflected, targetScene) => {
    expect(object).toBe(surface); expect(surface.visible).toBe(true); expect(surface.frustumCulled).toBe(false);
    expect(reflected.layers.mask).toBe(1 << REFLECTION_LAYER); expect(targetScene).toBe(scene);
    expect(renderer.getRenderTarget()).not.toBeNull();
    contexts.get(renderer.getRenderTarget(), null); return compilation;
  });
  const prepared = river.prepare(renderer, scene, scene, camera);
  expect(compile).toHaveBeenCalledOnce();
  expect(getContext).toHaveBeenCalledWith(expect.any(THREE.RenderTarget), null, 3);
  expect(renderer.getRenderTarget()).toBeNull(); expect(renderer.getMRT()).toBeNull();
  expect(model.visible).toBe(false); expect(surface.frustumCulled).toBe(true);
  complete(); await prepared;
  river.dispose(); env.dispose();
  vi.unstubAllGlobals();
});

test('river reuse preserves explicit and automatic CSM update requests', () => {
  const a = new THREE.DirectionalLight(), b = new THREE.DirectionalLight();
  a.shadow.autoUpdate = true; a.shadow.needsUpdate = true;
  b.shadow.autoUpdate = false; b.shadow.needsUpdate = false;
  const reuse = new ReflectionShadows();
  reuse.pause([a, b]);
  expect([a.shadow.autoUpdate, a.shadow.needsUpdate, b.shadow.autoUpdate, b.shadow.needsUpdate]).toEqual([false, false, false, false]);
  reuse.restore([a, b]);
  expect([a.shadow.autoUpdate, a.shadow.needsUpdate, b.shadow.autoUpdate, b.shadow.needsUpdate]).toEqual([true, true, false, false]);
});


test('reflection restore leaves newly initialized cascades at their own defaults', () => {
  const lights: THREE.DirectionalLight[] = [], reuse = new ReflectionShadows();
  reuse.pause(lights);
  const light = new THREE.DirectionalLight(); lights.push(light);
  reuse.restore(lights);
  expect(light.shadow.autoUpdate).toBe(true); expect(light.shadow.needsUpdate).toBe(false);
});

test('reflection excludes blended/transmissive and small structural pieces, keeping scaled surfaces', () => {
  const mesh = new THREE.Mesh(new THREE.BoxGeometry(), new THREE.MeshPhysicalMaterial());
  mesh.userData.layer = 'buildings';
  expect(reflectionSurface(mesh)).toBe(false);
  mesh.scale.setScalar(10); mesh.updateMatrixWorld();
  expect(reflectionSurface(mesh)).toBe(true);
  mesh.material.transparent = true; expect(reflectionSurface(mesh)).toBe(false);
  mesh.material.transparent = false; mesh.material.transmission = 1; expect(reflectionSurface(mesh)).toBe(false);
  mesh.material.transmission = 0; mesh.userData.objects = [{ prop_type: 'pole' }];
  expect(reflectionSurface(mesh)).toBe(false);
});

test('reflection cadence refreshes on every camera change and throttles only a still view (regression of 3a287573)', () => {
  const cadence = new ReflectionCadence(12), camera = new THREE.PerspectiveCamera();
  camera.updateMatrixWorld();
  expect(cadence.shouldUpdate(camera, 0, false)).toBe(true);
  // Any movement, however small or soon after the last refresh, re-renders: a stale image trails the view.
  for (const [t, x] of [[5, 0.01], [10, 0.02], [15, 5]] as const) {
    camera.position.x = x; camera.updateMatrixWorld();
    expect(cadence.shouldUpdate(camera, t, true)).toBe(true);
  }
  camera.rotation.y = 0.001; camera.updateMatrixWorld();
  expect(cadence.shouldUpdate(camera, 20, true)).toBe(true);
  // A still camera reuses the image until the slow rate is due; forced or missing output refreshes.
  expect(cadence.shouldUpdate(camera, 40, true)).toBe(false);
  expect(cadence.shouldUpdate(camera, 20 + 1000 / 12, true)).toBe(true);
  expect(cadence.shouldUpdate(camera, 110, true, true)).toBe(true);
  expect(cadence.shouldUpdate(camera, 111, false)).toBe(true);
});
