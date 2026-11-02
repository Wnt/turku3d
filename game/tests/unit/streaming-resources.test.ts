import { expect, test, vi } from 'vitest';
import * as THREE from 'three/webgpu';
import type NodeBuilder from 'three/src/nodes/core/NodeBuilder.js';
import { GLTFLoader, type GLTF } from 'three/addons/loaders/GLTFLoader.js';
import { mrt, normalView, velocity } from 'three/tsl';
// @ts-expect-error r186 internal pipeline manager has no DefinitelyTyped declaration.
import Pipelines from 'three/src/renderers/common/Pipelines.js';
import { SharedMaterials } from '../../src/render/sharedMaterials';
import { retainPrograms } from '../../src/render/programCache';
import { tintDraft } from '../../src/render/facadeKit';
import { CoarseTiles, cellsOf, wantedTiles } from '../../src/world/coarseTiles';
import { draftManifest } from '../../src/world/manifest';
import { GameRenderer } from '../../src/render/shadowMaterials';
import { CarVisual, ParkedCarVisuals } from '../../src/actors/car';

test.each([false, true])('r186 generates identical instanced vertex programs for different batch sizes (velocity=%s)', motion => {
  type ShaderBuilder = NodeBuilder & { scene: THREE.Scene; camera: THREE.Camera; build(): void; vertexShader: string };
  const renderer = new GameRenderer({ canvas: { width: 1, height: 1, style: {} } as HTMLCanvasElement });
  Object.assign(renderer.backend, { capabilities: { getUniformBufferLimit: () => 65536 } });
  if (motion) { renderer.setRenderTarget(new THREE.RenderTarget()); renderer.setMRT(mrt({ output: normalView, velocity })); }
  const shaders = (shared: boolean) => [3, 17].map(count => {
    const mesh = new THREE.InstancedMesh(new THREE.BoxGeometry(), new THREE.MeshBasicNodeMaterial(), count);
    const backend = renderer.backend as typeof renderer.backend & { createNodeBuilder(object: THREE.Object3D, renderer: THREE.WebGPURenderer): ShaderBuilder };
    const builder = backend.createNodeBuilder(mesh, renderer);
    builder.scene = new THREE.Scene(); builder.camera = new THREE.PerspectiveCamera();
    if (shared) renderer.debug.onNodeBuilderCreated!(builder, { object: mesh });
    builder.build();
    return builder.vertexShader;
  });
  const original = shaders(false), fixed = shaders(true);
  expect(original[0]).not.toBe(original[1]);
  expect(fixed[0]).toBe(fixed[1]);
  expect(fixed[0]).not.toMatch(/array<mat4x4<f32>,\s*(3|17)>/);
});

test('updating parked inventory preserves existing draws and their instance buffers', async () => {
  const asset = new THREE.Group(); asset.add(new THREE.Mesh(new THREE.BoxGeometry(), new THREE.MeshStandardMaterial()));
  const load = vi.spyOn(GLTFLoader.prototype, 'loadAsync').mockResolvedValue({ scene: asset } as GLTF);
  try {
    const scene = new THREE.Scene(), visuals = new ParkedCarVisuals(scene), manifest = draftManifest('http://localhost/');
    manifest.vehicle_models = { 'sedan/blue': { url: 'sedan.glb', sha256: 'same', bytes: 1 } };
    const car = { object_id: 'one', model: 'sedan', color: 'blue', x: 1, y: 2, z: 3, yaw: 0,
      width: 2, length: 4, height: 1.5, clear_front_m: 10, clear_rear_m: 10 };
    manifest.vehicles = [car]; await visuals.load(manifest);
    const draw = scene.children[0] as THREE.InstancedMesh, buffer = draw.instanceMatrix;
    manifest.vehicles.push({ ...car, object_id: 'two' }); await visuals.load(manifest);
    expect(scene.children).toEqual([draw]); expect(draw.instanceMatrix).toBe(buffer); expect(draw.count).toBe(2);
    manifest.vehicles = []; await visuals.load(manifest);
    expect(scene.children).toHaveLength(0); expect(load).toHaveBeenCalledOnce();
  } finally { load.mockRestore(); }
});

test('separate tile instances share exact PBR materials and release only after the final owner', () => {
  const shared = new SharedMaterials(), renderer = {} as THREE.WebGPURenderer;
  const models = [new THREE.Group(), new THREE.Group()];
  for (const model of models) for (let n = 0; n < 2; n++) {
    const material = new THREE.MeshStandardMaterial({ color: 0x335533 }); material.name = 'tree_pine';
    model.add(new THREE.InstancedMesh(new THREE.BoxGeometry(), material, n + 1));
  }
  for (const model of models) tintDraft(model, renderer, shared);
  const material = (models[0].children[0] as THREE.Mesh).material as THREE.Material;
  for (const model of models) for (const mesh of model.children) expect((mesh as THREE.Mesh).material).toBe(material);
  const dispose = vi.fn(); material.addEventListener('dispose', dispose);
  shared.releaseModel(models[0], new Set([material])); expect(dispose).not.toHaveBeenCalled();
  shared.releaseModel(models[1], new Set([material])); expect(dispose).toHaveBeenCalledOnce();
  const textured = new THREE.MeshStandardMaterial({ map: new THREE.Texture() });
  expect(shared.acquire(textured.clone())).not.toBe(shared.acquire(textured));
  const red = shared.acquire(new THREE.MeshStandardMaterial({ color: 'red' }));
  expect(shared.acquire(new THREE.MeshStandardMaterial({ color: 'blue' }))).not.toBe(red);
});

test('r186 reuses compiled programs/pipelines after unloading a tile and bounds its idle cache', () => {
  const backend = { createProgram: vi.fn(), createRenderPipeline: vi.fn(), needsRenderUpdate: () => false,
    getRenderCacheKey: () => 'layout-and-pass' };
  const pipelines = new Pipelines(backend, {}, { createProgram: vi.fn(), destroyProgram: vi.fn() });
  retainPrograms(pipelines, 4);
  const object = (code: string) => ({ material: { name: code }, getNodeBuilderState: () => ({ vertexShader: `v${code}`, fragmentShader: `f${code}` }) });
  const first = object('tree'), next = object('tree');
  const pipeline = pipelines.getForRender(first); pipelines.delete(first);
  expect(pipelines.getForRender(next)).toBe(pipeline);
  expect(backend.createProgram).toHaveBeenCalledTimes(2);
  expect(backend.createRenderPipeline).toHaveBeenCalledOnce();
  // An active draw survives idle eviction, even if it was previously cached while unused.
  for (let i = 0; i < 20; i++) { const tile = object(String(i)); pipelines.getForRender(tile); pipelines.delete(tile); }
  expect(pipelines.getForRender(next)).toBe(pipeline);
  expect(pipelines.programs.vertex.size + pipelines.programs.fragment.size).toBeLessThanOrEqual(6);
  expect(pipelines.caches.size).toBeLessThanOrEqual(5);
  pipelines.delete(next); pipelines.dispose();
  expect(pipelines.caches.size).toBe(0);
  expect(pipelines.programs.vertex.size).toBe(0);
});

test('boundary jitter retains models, while travel and teleports release departed draws even offline', async () => {
  const scene = new THREE.Scene(), manifest = draftManifest('http://localhost/'), disposed = vi.fn();
  const coarse = new CoarseTiles({ scene, manifest, pageUrl: 'http://localhost/', renderer: {} as THREE.WebGPURenderer,
    unloadCollision: vi.fn(), refreshCollision: vi.fn(), checkCollision: async () => 2,
    loadModel: async () => {
      const group = new THREE.Group(), mesh = new THREE.InstancedMesh(new THREE.BoxGeometry(), new THREE.MeshStandardMaterial(), 2);
      mesh.addEventListener('dispose', disposed); group.add(mesh); return group;
    },
    fetch: (async (url: string) => {
      if (url.endsWith('.col')) return new Response(new ArrayBuffer(4));
      const id = /\/(t0_-?\d+_-?\d+)\//.exec(url)?.[1];
      return new Response(JSON.stringify(id ? { coarse: true, tile: id, visual: { [id]: { url: 'vis.glb', sha256: 'same' } },
        collision: Object.fromEntries(cellsOf(id).map(cid => [cid, { url: `${cid}.col`, sha256: 'same', bytes: 4 }])) } : {}));
    }) as typeof fetch });
  const settle = async () => { for (let i = 0; i < 6; i++) await new Promise(r => setTimeout(r, 0)); };
  coarse.update(180, 270, 0); await settle();
  expect(coarse.drawn().sort()).toEqual(wantedTiles(180, 270).sort());
  const initial = [...scene.children];
  coarse.update(249, 270, 1000); await settle();
  coarse.update(251, 270, 2000); await settle();
  expect(scene.children).toHaveLength(12);
  coarse.update(249, 270, 3000); await settle();
  expect(scene.children).toHaveLength(12);
  expect(initial.every(model => scene.children.includes(model))).toBe(true);
  expect(disposed).not.toHaveBeenCalled();
  coarse.update(480, 270, 4000); await settle();
  expect(coarse.drawn().sort()).toEqual(wantedTiles(480, 270).sort());
  coarse.update(180, 270, 6000); await settle();
  expect(wantedTiles(180, 270).every(id => coarse.drawn().includes(id))).toBe(true);
  expect(scene.children).toHaveLength(12); expect(Object.keys(manifest.collision)).toHaveLength(192);
  expect(disposed).toHaveBeenCalledTimes(3);
  coarse.down(); coarse.update(5000, 5000, 7000); await settle();
  expect(coarse.drawn()).toEqual([]); expect(scene.children).toHaveLength(0);
  expect(Object.keys(manifest.collision)).toHaveLength(0); expect(disposed).toHaveBeenCalledTimes(15);
});


test('player cars prepare one prototype even for concurrent loads, sharing geometry but not wheel transforms', async () => {
  const asset = new THREE.Group();
  const body = new THREE.Mesh(new THREE.BoxGeometry(), new THREE.MeshStandardMaterial());
  body.name = 'body'; asset.add(body);
  for (const name of ['wheel-front-left', 'wheel-front-right', 'wheel-back-left', 'wheel-back-right']) {
    const wheel = new THREE.Mesh(new THREE.BoxGeometry(), body.material); wheel.name = name; asset.add(wheel);
  }
  const load = vi.spyOn(GLTFLoader.prototype, 'loadAsync').mockResolvedValue({ scene: asset } as GLTF);
  const normals = vi.spyOn(THREE.BufferGeometry.prototype, 'computeVertexNormals');
  try {
    const scene = new THREE.Scene(), cars = [new CarVisual(scene), new CarVisual(scene)];
    await Promise.all(cars.map(car => car.load('shared-sedan-test.glb')));
    expect(load).toHaveBeenCalledOnce(); expect(normals).toHaveBeenCalledOnce();
    const bodies = cars.map(car => car.root.getObjectByName('body') as THREE.Mesh);
    expect(bodies[0]).not.toBe(bodies[1]); expect(bodies[0].geometry).toBe(bodies[1].geometry);
    const wheels = cars.map(car => car.root.getObjectByName('vehicle-wheel-0')!);
    wheels[0].rotation.y = 1;
    expect(wheels[1].rotation.y).toBe(0);
  } finally { load.mockRestore(); normals.mockRestore(); }
});
