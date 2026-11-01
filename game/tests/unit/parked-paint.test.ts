import { expect, test, vi } from 'vitest';
import { readFileSync } from 'node:fs';
import * as THREE from 'three/webgpu';
import type NodeBuilder from 'three/src/nodes/core/NodeBuilder.js';
import { GLTFLoader, type GLTF } from 'three/addons/loaders/GLTFLoader.js';
import { mrt, normalView, velocity } from 'three/tsl';
// @ts-expect-error r186 internal pipeline manager has no DefinitelyTyped declaration.
import Pipelines from 'three/src/renderers/common/Pipelines.js';
import { ParkedCarVisuals } from '../../src/actors/car';
import { PAINT_COLORS, PAINT_COLUMNS, PAINT_SWATCH, actorPaintOf, instancePaintOf, paintOf } from '../../src/actors/parkedPaint';
import { GameRenderer } from '../../src/render/shadowMaterials';
import { draftManifest, type ParkedVehicle } from '../../src/world/manifest';
import type { VehicleState } from '../../src/physics/protocol';

type ShaderBuilder = NodeBuilder & { scene: THREE.Scene; camera: THREE.Camera; build(): void; vertexShader: string; fragmentShader: string };
const MODELS = Object.keys(PAINT_COLUMNS), COLORS = Object.keys(PAINT_COLORS);
const settle = async () => { for (let i = 0; i < 8; i++) await new Promise(r => setTimeout(r, 0)); };
const car = (object_id: string, model: string, color: string, x = 0): ParkedVehicle => ({ object_id, model, color,
  x, y: 2, z: 5, yaw: 0, width: 1.85, length: 4.55, height: 1.48, clear_front_m: 10, clear_rear_m: 10 });
const paintAt = (mesh: THREE.InstancedMesh, i: number) => Array.from(instancePaintOf(mesh)!.array.slice(i * 3, i * 3 + 3));
const expected = (color: string) => paintOf(color).map(Math.fround);
const actorPaint = (mesh: THREE.Mesh) => actorPaintOf(mesh)!.toArray();

/** The generator's published GLBs: one per model/paint, same geometry per model, own palette texture. */
function garage(assets = [...MODELS, 'fixture'].flatMap(m => [...COLORS, 'purple'].map(c => `${m}/${c}`))) {
  const geometries = new Map<string, THREE.BufferGeometry>(), urls: string[] = [];
  const load = vi.spyOn(GLTFLoader.prototype, 'loadAsync').mockImplementation(async url => {
    urls.push(url);
    const model = new URL(url).pathname.slice(1).split('~')[0];
    if (!geometries.has(model)) geometries.set(model, new THREE.BoxGeometry(1.8, 1.5, 4.5));
    const scene = new THREE.Group();
    scene.add(new THREE.Mesh(geometries.get(model), new THREE.MeshStandardMaterial({ color: 0xcccccc, roughness: 0.42, metalness: 0,
      map: new THREE.DataTexture(new Uint8Array(4), 1, 1) })));
    return { scene } as GLTF;
  });
  const manifest = draftManifest('http://localhost/');
  manifest.vehicle_models = Object.fromEntries(assets.map(key => [key, { url: `${key.replace('/', '~')}.glb`, sha256: '', bytes: 1 }]));
  return { geometries, urls, load, manifest };
}

test('one draw per body model carries each paint per instance; other models keep one draw per GLB', async () => {
  const { geometries, urls, load, manifest } = garage();
  try {
    const cars = MODELS.flatMap(m => COLORS.flatMap(c => [0, 1].map(i => car(`${m}/${c}/${i}`, m, c))));
    manifest.vehicles = [...cars, car('fixture', 'fixture', 'blue'), car('odd', 'sedan', 'purple')];
    const scene = new THREE.Scene();
    await new ParkedCarVisuals(scene).load(manifest);
    const draws = scene.children as THREE.InstancedMesh[];
    // Per model × paint this was 24 + 2 resident draws, and 26 GLB downloads.
    expect(draws.map(d => d.name).sort()).toEqual([...MODELS.map(m => `parked/${m}`), 'parked/fixture/blue', 'parked/sedan/purple'].sort());
    expect(urls).toHaveLength(6);
    for (const model of MODELS) {
      const draw = draws.find(d => d.name === `parked/${model}`)!, members = cars.filter(c => c.model === model);
      expect(draw.count).toBe(members.length);
      expect(instancePaintOf(draw)!.count).toBe(draw.instanceMatrix.count);
      members.forEach((c, i) => expect(paintAt(draw, i)).toEqual(expected(c.color)));
      // The GLB's own geometry, authored texture and PBR values; shadows as before.
      expect(draw.geometry).toBe(geometries.get(model));
      const material = draw.material as THREE.MeshStandardNodeMaterial;
      expect(material.isMeshStandardNodeMaterial && !!material.map && material.roughness === 0.42 && material.color.getHex() === 0xcccccc).toBe(true);
      expect(draw.castShadow && draw.receiveShadow).toBe(true);
    }
    const fixture = draws.find(d => d.name === 'parked/fixture/blue')!;
    expect((fixture.material as THREE.MeshStandardMaterial).isMeshStandardMaterial).toBe(true);
    expect(instancePaintOf(fixture)).toBeUndefined();
  } finally { load.mockRestore(); }
});

test('visual cars rest where told, claimed cars stay hidden, and driven and remote cars keep their paint', async () => {
  const { load, manifest } = garage();
  try {
    manifest.vehicles = [car('a', 'sedan', 'red', 10), car('b', 'sedan', 'blue', 20), car('c', 'sedan', 'white', 30)];
    const scene = new THREE.Scene(), visuals = new ParkedCarVisuals(scene);
    await visuals.load(manifest);
    const draw = scene.children[0] as THREE.InstancedMesh;
    const rest = { restingPose: (c: ParkedVehicle) => c.object_id === 'c' ? null : { pos: { x: c.x, y: c.y + .75, z: c.z }, rot: { x: 0, y: 0, z: 0, w: 1 } } };
    visuals.update([{ id: 'a', pos: { x: 11, y: 3, z: 5 }, rot: { x: 0, y: 0, z: 0, w: 1 } } as VehicleState], rest);
    const at = (i: number) => { const m = new THREE.Matrix4(); draw.getMatrixAt(i, m); return m; };
    expect(new THREE.Vector3().setFromMatrixPosition(at(0)).toArray()).toEqual([11, 2.25, 5]);
    expect(new THREE.Vector3().setFromMatrixPosition(at(1)).toArray()).toEqual([20, 2, 5]);
    expect(at(2).determinant()).toBe(0);
    visuals.takeOver('a');
    const driven = scene.getObjectByName('driven/a') as THREE.Mesh;
    expect(driven.material).toBe(draw.material);
    expect(actorPaint(driven)).toEqual([...paintOf('red')]);
    expect(driven.geometry).toBe(draw.geometry);
    expect([paintAt(draw, 0), paintAt(draw, 1)]).toEqual([expected('blue'), expected('white')]);
    expect(draw.count).toBe(2);
    const remote = visuals.remoteModel('sedan/beige')!;
    expect(remote.material).toBe(draw.material);
    expect(actorPaint(remote)).toEqual([...paintOf('beige')]);
    expect(remote.castShadow && remote.receiveShadow).toBe(true);
  } finally { load.mockRestore(); }
});

test('a remote paint that no resident draft uses loads any GLB of its body model', async () => {
  const { urls, load, manifest } = garage(['suv/blue', 'fixture/red']);
  try {
    const visuals = new ParkedCarVisuals(new THREE.Scene());
    await visuals.load(manifest);
    expect(visuals.remoteModel('suv/red')).toBeNull();
    visuals.requestRemoteModel('suv/red'); visuals.requestRemoteModel('suv/white'); visuals.requestRemoteModel('fixture/red');
    await settle();
    expect(urls.map(url => new URL(url).pathname).sort()).toEqual(['/fixture~red.glb', '/suv~blue.glb']);
    expect(actorPaint(visuals.remoteModel('suv/red')!)).toEqual([...paintOf('red')]);
    const fixture = visuals.remoteModel('fixture/red')!;
    expect((fixture.material as THREE.MeshStandardMaterial).isMeshStandardMaterial && !actorPaintOf(fixture)).toBe(true);
  } finally { load.mockRestore(); }
});

test('paint table and swatch match the generator that bakes the parked-car palettes', () => {
  const source = readFileSync(new URL('../../../pipeline/turku3d/parked/models.py', import.meta.url), 'utf8');
  const colors = Object.fromEntries([...source.match(/COLORS = \{([^}]*)\}/)![1].matchAll(/"(\w+)": \((\d+), (\d+), (\d+)\)/g)]
    .map(([, name, r, g, b]) => [name, [+r, +g, +b]]));
  expect(colors).toEqual(PAINT_COLORS);
  const columns = Object.fromEntries([...source.match(/column = \{([^}]*)\}\[name\]/)![1].matchAll(/"([\w-]+)": (\d+)/g)].map(([, m, c]) => [m, +c]));
  expect(columns).toEqual(PAINT_COLUMNS);
  const { atlas, column, firstRow, rows, darkest } = PAINT_SWATCH;
  expect(source).toContain(`shade = np.linspace(1.0, ${darkest}, ${rows})`);
  expect(source).toContain(`a[${firstRow}:${firstRow + rows}, column * ${column} : (column + 1) * ${column}]`);
  const png = readFileSync(new URL('../../../pipeline/turku3d/parked/assets/colormap.png', import.meta.url));
  expect([png.readUInt32BE(16), png.readUInt32BE(20)]).toEqual([atlas, atlas]);
});

test.each([false, true])('instance counts, paints and body models never add a program or pipeline (velocity=%s)', async motion => {
  const renderer = new GameRenderer({ canvas: { width: 1, height: 1, style: {} } as HTMLCanvasElement });
  renderer.hasFeature = () => false;
  Object.assign(renderer.backend, { capabilities: { getUniformBufferLimit: () => 65536 } });
  if (motion) { renderer.setRenderTarget(new THREE.RenderTarget()); renderer.setMRT(mrt({ output: normalView, velocity })); }
  const backend = renderer.backend as typeof renderer.backend & { createNodeBuilder(object: THREE.Object3D, renderer: THREE.WebGPURenderer): ShaderBuilder };
  // The real r186 CPU builder, with the game's instanced-program hook, as NodeManager runs it.
  const program = (object: THREE.Mesh) => {
    const builder = backend.createNodeBuilder(object, renderer);
    renderer.debug.onNodeBuilderCreated!(builder, { object } as never);
    builder.scene = new THREE.Scene(); builder.camera = new THREE.PerspectiveCamera(); builder.build();
    const generated = (builder as unknown as { bufferAttributes: { node: { value: unknown } }[] }).bufferAttributes.map(a => a.node.value);
    return { vertexShader: builder.vertexShader, fragmentShader: builder.fragmentShader, generated };
  };
  const { load, manifest } = garage();
  try {
    const draws = async (cars: ParkedVehicle[]) => {
      const scene = new THREE.Scene(), visuals = new ParkedCarVisuals(scene);
      manifest.vehicles = cars; await visuals.load(manifest);
      return { visuals, scene, meshes: [...scene.children] as THREE.InstancedMesh[] };
    };
    const few = await draws(MODELS.flatMap(m => [0, 1, 2].map(i => car(`${m}${i}`, m, 'red'))));
    const many = await draws(MODELS.flatMap(m => Array.from({ length: 150 }, (_, i) => car(`${m}${i}`, m, COLORS[i % 6]))));
    expect([...few.meshes, ...many.meshes].map(m => m.count)).toEqual([3, 3, 3, 3, 150, 150, 150, 150]);
    many.visuals.takeOver(`${MODELS[0]}7`);
    const standalone = [many.scene.getObjectByName(`driven/${MODELS[0]}7`) as THREE.Mesh,
      ...MODELS.flatMap(m => ['white', 'charcoal'].map(c => many.visuals.remoteModel(`${m}/${c}`)!))];
    // The former layout: one batch per model × paint, each with its own palette texture.
    const former = MODELS.flatMap(m => COLORS.map((c, i) => new THREE.InstancedMesh(new THREE.BoxGeometry(1.8, 1.5, 4.5),
      renderer.library.fromMaterial(new THREE.MeshStandardMaterial({ name: `${m}/${c}`, color: 0xcccccc, roughness: 0.42,
        map: new THREE.DataTexture(new Uint8Array(4), 1, 1) })) as THREE.Material, 1 + i * 5)));
    const variants = (states: { vertexShader: string; fragmentShader: string }[]) => new Set(states.map(s => s.vertexShader + s.fragmentShader)).size;
    const batched = [...few.meshes, ...many.meshes].map(program), single = standalone.map(program);
    expect(variants(batched)).toBe(1);
    expect(variants(single)).toBe(1);
    expect(variants(former.map(program))).toBe(1);
    expect(batched[0].fragmentShader).toContain('sRGBTransferEOTF');
    // Each batch binds its own paint buffer as a generated attribute (owned and released with the
    // draw, like its instance matrices); standalone cars read an object uniform instead.
    [...few.meshes, ...many.meshes].forEach((mesh, i) => expect(batched[i].generated).toContain(instancePaintOf(mesh)));
    expect(single.every(state => state.generated.length === 0)).toBe(true);
    expect(batched[0].vertexShader).not.toMatch(/array<mat4x4<f32>,\s*\d+>/);
    // The paint mask is arithmetic: the palette sample stays in uniform control flow.
    expect(batched[0].fragmentShader).not.toMatch(/\bif\s*\(/);
    // Native pipelines are keyed by program pair and pass state: 2 for 8 batches and 9 standalone cars.
    const pipelineBackend = { createProgram: vi.fn(), createRenderPipeline: vi.fn(), needsRenderUpdate: () => false, getRenderCacheKey: () => 'pass' };
    const pipelines = new Pipelines(pipelineBackend, {}, { createProgram: vi.fn(), destroyProgram: vi.fn() });
    for (const state of [...batched, ...single]) pipelines.getForRender({ material: { name: 'parked' }, getNodeBuilderState: () => state });
    expect(pipelineBackend.createRenderPipeline).toHaveBeenCalledTimes(2);
    pipelines.dispose();
  } finally { load.mockRestore(); }
});
