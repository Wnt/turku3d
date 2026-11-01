// Day and night share every pipeline: real r186 CPU shader builders (no browser) generate identical programs and
// cache keys at 13:00 and 23:30, and with idle or active light slots (docs/reports/perf-air-loop.md pattern).
import { expect, test } from 'vitest';
import * as THREE from 'three/webgpu';
import type NodeBuilder from 'three/src/nodes/core/NodeBuilder.js';
// @ts-expect-error r186 internal node manager has no DefinitelyTyped declaration.
import NodeManager from 'three/src/renderers/common/nodes/NodeManager.js';
import { GameRenderer } from '../../src/render/shadowMaterials';
import { GameLighting } from '../../src/render/lighting';
import { Environment } from '../../src/render/environment';
import { MaterialStyler } from '../../src/render/materials';
import { facadeKitMaterial, tintDraft } from '../../src/render/facadeKit';
import { QUALITY, type QualityTier } from '../../src/render/quality';
import { cycleAt } from '../../src/core/dayCycle';
import { LampPools } from '../../src/render/lights/pools';

type ShaderBuilder = NodeBuilder & { build(): void; vertexShader: string; fragmentShader: string };
interface Nodes {
  _createNodeBuilder(renderObject: object, material: THREE.Material): ShaderBuilder;
  getCacheKey(scene: THREE.Scene, lights: THREE.LightsNode): number;
}

const atlas = () => new THREE.DataTexture(new Uint8Array(4), 1, 1);
const v3 = { _kit_layout: [7, 3, 3.2], _kit_wall: [.8, .7, .6], _kit_trim: [1, 1, 1], _kit_origin: [2, 0], _kit_floors: [4, 3.2, 1] };
const v4 = { _kit_layout: [42, 3, 4], _kit_origin: [0, 0], _kit_extent: [0, 0, 12, 16], _kit_floors: [4, 4, 1],
  _kit_wall: [.8, .7, .6], _kit_trim: [1, 1, 1], _kit_opening: [.45, .5, .2, .1], _kit_detail: [0, 0, 2, -3],
  _kit_bands: [.5, .2, .1, .2], _kit_base: [.7, .6, .5] };

function wall(fields: Record<string, number[]> = {}) {
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.Float32BufferAttribute([0, 0, 0, 1, 0, 0, 0, 1, 0], 3));
  geometry.setAttribute('normal', new THREE.Float32BufferAttribute([0, 0, 1, 0, 0, 1, 0, 0, 1], 3));
  geometry.setAttribute('uv', new THREE.Float32BufferAttribute([0, 0, 1, 0, 0, 1], 2));
  for (const [name, values] of Object.entries(fields)) geometry.setAttribute(name,
    new THREE.Float32BufferAttribute([...values, ...values, ...values], values.length));
  return geometry;
}

/** Uniform-array buffers are named after global node ids, which differ between separately built worlds. */
function canonical(code: string) {
  const names = new Map<string, string>();
  return code.replace(/\b(NodeBuffer_|buffer)(\d+)/g, (_match, prefix: string, id: string) => {
    if (!names.has(prefix + id)) names.set(prefix + id, `${prefix}#${names.size}`);
    return names.get(prefix + id)!;
  });
}

function world(webgl: boolean, tier: QualityTier, lighting: THREE.Lighting = new GameLighting(QUALITY[tier].lights)) {
  const renderer = new GameRenderer({ forceWebGL: webgl, canvas: { width: 1, height: 1, style: {} } as HTMLCanvasElement });
  renderer.hasFeature = () => false;
  Object.assign(renderer.backend, { capabilities: { getUniformBufferLimit: () => 65536 } });
  renderer.lighting = lighting;
  const scene = new THREE.Scene(), camera = new THREE.PerspectiveCamera(60, 1, 0.1, 6000);
  const env = new Environment(scene, renderer, tier);
  // Play reuses one PMREM target for every IBL refresh; a fixed CubeUV texture stands in for it here.
  scene.environment = Object.assign(new THREE.DataTexture(new Uint8Array(4 * 64 * 64), 64, 64), { mapping: THREE.CubeUVReflectionMapping });
  const nodes = new NodeManager(renderer, renderer.backend) as Nodes;
  const lightsNode = renderer.lighting.getNode(scene);
  const meshes: THREE.Mesh[] = [];
  const add = (name: string, mesh: THREE.Mesh) => { mesh.name = name; mesh.receiveShadow = mesh.castShadow = true; meshes.push(mesh); scene.add(mesh); };

  // Facade kit v3 and v4, through the same binding code as streamed tiles.
  for (const [name, version, fields] of [['kit-v3', 3, v3], ['kit-v4', 4, v4]] as const) {
    const source = new THREE.MeshStandardMaterial({ map: atlas(), normalMap: atlas(), roughnessMap: atlas() });
    source.userData.facade_kit = { version };
    add(name, new THREE.Mesh(wall(fields), facadeKitMaterial(source, renderer)!));
  }
  // A non-kit generated facade wall, a non-wall library surface, plain prop/car colours.
  const tile = new THREE.Group();
  const facade = new THREE.Mesh(wall(), new THREE.MeshStandardMaterial({ map: atlas(), roughnessMap: atlas() }));
  facade.userData.layer = 'buildings';
  const slabs = new THREE.Mesh(wall(), new THREE.MeshStandardMaterial({ map: atlas(), roughnessMap: atlas() }));
  slabs.userData.layer = 'ground';
  const prop = new THREE.Mesh(wall(), new THREE.MeshStandardMaterial({ color: 0x884422 }));
  tile.add(facade, slabs, prop);
  tintDraft(tile, renderer);
  add('draft-facade', facade); add('ground-slabs', slabs); add('prop-car', prop);
  // Styled road/grass surfaces and the river (sky-PMREM water on mobile, planar reflections elsewhere are their own pass).
  const styler = new MaterialStyler(renderer, env, 'mobile', true);
  const surfaces = new THREE.Group();
  for (const key of ['draft_asphalt_worn', 'draft_grass', 'water']) {
    const mesh = new THREE.Mesh(wall(), new THREE.MeshStandardMaterial({ map: atlas() }));
    mesh.userData.material_key = key; mesh.name = key; surfaces.add(mesh);
  }
  styler.apply(surfaces);
  for (const mesh of [...surfaces.children] as THREE.Mesh[]) add(mesh.name, mesh);
  add('sky', env.sky);

  const lightSet = () => {
    const lights: THREE.Light[] = [];
    scene.traverseVisible(o => { if ((o as THREE.Light).isLight) lights.push(o as THREE.Light); });
    lightsNode.setLights(lights);
    (renderer.info as { calls: number }).calls++; // a new draw call: the environment key is recomputed, not served from its per-call cache
  };
  /** Everything r186 compares before reusing a render object's pipeline. */
  const keys = () => {
    lightSet();
    return Object.fromEntries(meshes.map(mesh => {
      const material = mesh.material as THREE.NodeMaterial;
      return [mesh.name, { version: material.version, program: material.customProgramCacheKey(),
        environment: nodes.getCacheKey(scene, lightsNode), lights: lightsNode.getCacheKey(true) }];
    }));
  };
  /** The generated programs a first build of each material produces. */
  const shaders = () => {
    lightSet();
    return Object.fromEntries(meshes.map(mesh => {
      const material = mesh.material as THREE.NodeMaterial;
      const builder = nodes._createNodeBuilder({ object: mesh, material, scene, camera, lightsNode, clippingContext: null }, material);
      builder.build();
      return [mesh.name, { vertex: canonical(builder.vertexShader), fragment: canonical(builder.fragmentShader) }];
    }));
  };
  const at = (hour: number) => { const c = cycleAt(hour); env.setSun(c.sun.azimuth, c.sun.elevation, c.hour); };
  const lamps = () => { env.lamps.point[0].intensity = env.lamps.point[1].intensity = 20; };
  return { env, meshes, at, keys, shaders, lamps };
}

test.each([false, true])('13:00 and 23:30 keep every cache key, idle or lit slots (WebGL=%s)', webgl => {
  const { env, meshes, at, keys, shaders, lamps } = world(webgl, 'medium');
  expect(meshes.map(m => m.name)).toEqual(['kit-v3', 'kit-v4', 'draft-facade', 'ground-slabs', 'prop-car',
    'draft_asphalt_worn', 'draft_grass', 'water', 'sky']);
  // Window light is part of each wall's one graph; non-wall surfaces carry none.
  for (const name of ['kit-v3', 'kit-v4', 'draft-facade']) {
    expect((meshes.find(m => m.name === name)!.material as THREE.MeshStandardNodeMaterial).emissiveNode, name).toBeTruthy();
  }
  expect((meshes.find(m => m.name === 'ground-slabs')!.material as THREE.MeshStandardNodeMaterial).emissiveNode).toBeFalsy();
  at(13);
  shaders(); // compile once, as startup warmup does
  const day = keys();
  expect(env.sun.intensity).toBeGreaterThan(3); expect(env.nightFactor).toBe(0);
  at(23.5);
  expect(env.sun.intensity).toBe(0); expect(env.sun.visible).toBe(true); expect(env.nightFactor).toBe(1);
  expect(env.hemisphere.intensity).toBeGreaterThan(0);
  expect(keys()).toEqual(day);
  // Phase 2 lamps switch on through the fixed slots without new keys.
  lamps();
  expect(keys()).toEqual(day);
  // Sanity: the keys do see real light-set changes, such as the former night switch that hid the sun.
  env.sun.visible = false;
  expect(keys()['kit-v3'].lights).not.toBe(day['kit-v3'].lights);
  env.sun.visible = true;
  expect(keys()).toEqual(day);
  env.lamps.point[0].castShadow = true;
  expect(keys()['kit-v3'].lights).not.toBe(day['kit-v3'].lights);
});

test.each([false, true])('lamp pools moving the slots along a street keep every key and program (WebGL=%s)', webgl => {
  const { env, at, keys, shaders } = world(webgl, 'medium');
  at(13);
  const programs = shaders();
  const day = keys();
  const pools = new LampPools(env.lamps.point);
  pools.setLamps(Array.from({ length: 30 }, (_, i) => ({ key: `l${i}`, x: i * 30, y: 5, z: 4, r: 1, g: 0.8, b: 0.58, intensity: 20 })));
  at(23.5);
  const active = new Set<number>(), positions = new Set<string>();
  for (let frame = 0; frame <= 40 * 30; frame++) {
    const t = frame / 30;
    pools.update(t * 15, 0, 1, 0, env.nightFactor, t * 1000);
    active.add(env.lamps.point.filter(l => l.intensity > 0).length);
    for (const l of env.lamps.point) positions.add(l.position.toArray().join());
    if (frame % 60 === 0) expect(keys(), `${t} s`).toEqual(day);
  }
  // The slots really moved (dozens of lamps, 0 to 4 lit) while the keys held.
  expect(positions.size).toBeGreaterThan(20);
  expect(Math.min(...active)).toBe(0); expect(Math.max(...active)).toBe(4);
  expect(env.lamps.point).toHaveLength(4);
  expect(shaders()).toEqual(programs);
  // Dawn: the night level is 0 again, every slot leaves the shader loop.
  at(13);
  pools.update(600, 0, 1, 0, env.nightFactor, 41_000);
  expect(env.lamps.point.every(l => l.intensity === 0)).toBe(true);
  expect(keys()).toEqual(day);
});

test('batched roof ranges get no window light; batches with wall ranges do', () => {
  const renderer = new GameRenderer({ canvas: { width: 1, height: 1, style: {} } as HTMLCanvasElement });
  const batch = (surfaces: (string | undefined)[]) => {
    const tile = new THREE.Group();
    const mesh = new THREE.Mesh(wall(), new THREE.MeshStandardMaterial({ map: atlas(), roughnessMap: atlas() }));
    mesh.userData = { layer: 'buildings', material_key: 'roof_ortho', objects: surfaces.map((surface, i) => ({ start: i * 3, count: 3, surface })) };
    tile.add(mesh);
    tintDraft(tile, renderer);
    return (mesh.material as THREE.Material as THREE.MeshStandardNodeMaterial).emissiveNode;
  };
  expect(batch(['roof', 'roof'])).toBeFalsy();
  expect(batch(['roof', 'wall'])).toBeTruthy();
  expect(batch([undefined])).toBeTruthy();
});

/** Uniform buffers bound to one shader stage: WebGPU guarantees 12 (maxUniformBuffersPerShaderStage). */
const uniformBuffers = (code: string, webgl: boolean) => (code.match(webgl ? /\buniform\s+\w+\s*\{/g : /var<uniform>/g) ?? []).length;

test.each([false, true])('13:00, 23:30 and lit slots generate the same programs (WebGL=%s)', webgl => {
  const build = (hour: number, lit: boolean) => { const w = world(webgl, 'medium'); w.at(hour); if (lit) w.lamps(); return w.shaders(); };
  const day = build(13, false);
  expect(Object.keys(day)).toHaveLength(9);
  for (const [name, program] of Object.entries(day)) {
    expect(uniformBuffers(program.fragment, webgl), `${name} fragment uniform buffers`).toBeLessThanOrEqual(10);
    expect(uniformBuffers(program.vertex, webgl), `${name} vertex uniform buffers`).toBeLessThanOrEqual(10);
  }
  expect(build(23.5, false)).toEqual(day);
  expect(build(23.5, true)).toEqual(day);
});

test.each([false, true])('batched lighting keeps the scene IBL of the default lighting path (WebGL=%s)', webgl => {
  // Sampled textures per fragment stage: albedo/normal/ORM, shadow maps and the scene environment map.
  const textures = (lighting?: THREE.Lighting) => {
    const w = world(webgl, 'medium', lighting); w.at(13);
    return Object.fromEntries(Object.entries(w.shaders()).map(([name, program]) =>
      [name, (program.fragment.match(webgl ? /uniform\s+(?:\w+\s+)?sampler\w+/g : /:\s*texture_\w+/g) ?? []).length]));
  };
  const batched = textures(), reference = textures(new THREE.Lighting());
  expect(batched['kit-v3']).toBeGreaterThan(3);
  expect(batched).toEqual(reference);
});

test('idle light slots cost no shader loop iterations; active ones are packed first', () => {
  const { env, shaders } = world(false, 'high');
  shaders();
  const scene = env.sun.parent as THREE.Scene;
  type Data = { countNode: { value: number }; update(frame: { camera: THREE.Camera }): void };
  const lighting = (env as unknown as { renderer: THREE.WebGPURenderer }).renderer.lighting;
  const data = (lighting.getNode(scene) as unknown as { _dataNodes: Map<string, Data> })._dataNodes;
  const camera = new THREE.PerspectiveCamera();
  data.get('PointLight')!.update({ camera });
  expect(data.get('PointLight')!.countNode.value).toBe(0);
  env.lamps.point[5].intensity = 10; env.lamps.point[2].intensity = 3;
  data.get('PointLight')!.update({ camera });
  expect(data.get('PointLight')!.countNode.value).toBe(2);
  expect(env.lamps.point).toHaveLength(QUALITY.high.lights.point);
  expect(data.has('SpotLight')).toBe(false);
});

test('cascades freeze only in play while the sun is dark, and resume at dawn', () => {
  const { env, at } = world(false, 'medium');
  const cascades = [new THREE.DirectionalLight(), new THREE.DirectionalLight(), new THREE.DirectionalLight()];
  env.csm.lights.push(...cascades);
  at(23.5);
  env.updateShadows(false);
  expect(env.shadowsFrozen).toBe(false); expect(cascades.every(l => l.shadow.autoUpdate)).toBe(true);
  env.updateShadows(true);
  expect(env.shadowsFrozen).toBe(true); expect(cascades.every(l => !l.shadow.autoUpdate)).toBe(true);
  at(19.5);
  env.updateShadows(true);
  expect(env.shadowsFrozen).toBe(false); expect(cascades.every(l => l.shadow.autoUpdate)).toBe(true);
});
