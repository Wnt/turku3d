import { expect, test, vi } from 'vitest';
import * as THREE from 'three/webgpu';
import { REFLECTION_LAYER, reflectionSurface, ReflectionShadows } from '../../src/render/reflections';
import { RiverMaterial } from '../../src/render/water';
import { Environment } from '../../src/render/environment';

test('river pass includes large static surfaces and excludes water, props and vegetation', () => {
  const owner = new THREE.Group(), mesh = new THREE.Mesh(); owner.add(mesh);
  for (const layer of ['ground', 'buildings', 'structures', 'massing', 'roads']) {
    owner.userData.layer = layer; expect(reflectionSurface(mesh)).toBe(true);
  }
  for (const layer of ['props', 'vegetation']) {
    owner.userData.layer = layer; expect(reflectionSurface(mesh)).toBe(false);
  }
  owner.userData.layer = 'ground'; mesh.userData.material_key = 'water';
  expect(reflectionSurface(mesh)).toBe(false);
  delete mesh.userData.material_key; mesh.userData.shadowBatch = true;
  expect(reflectionSurface(mesh)).toBe(false);
});

test('reflection warmup uses its real target and layer mask without leaking state across awaits', async () => {
  vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => { callback(0); return 1; });
  const renderer = new THREE.WebGPURenderer({ canvas: { width: 1, height: 1, style: {} } as HTMLCanvasElement });
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
    expect(renderer.getRenderTarget()).not.toBeNull(); return compilation;
  });
  const prepared = river.prepare(renderer, scene, scene, camera);
  expect(compile).toHaveBeenCalledOnce();
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
