import { afterEach, expect, test, vi } from 'vitest';
import * as THREE from 'three/webgpu';
import type NodeBuilder from 'three/src/nodes/core/NodeBuilder.js';
import { GameRenderer } from '../../src/render/shadowMaterials';
import { Environment } from '../../src/render/environment';
import { RiverMaterial } from '../../src/render/water';
import { WATER_SWITCHES, parseWaterSwitches } from '../../src/render/waterSwitches';
import type { QualityTier } from '../../src/render/quality';
import { WATER_HISTORY } from '../../src/render/waterHistory';
import { mrt, output, vec4 } from 'three/tsl';

type Builder = NodeBuilder & { scene: THREE.Scene; camera: THREE.Camera; build(): void; fragmentShader: string };
const defaults = { ...WATER_SWITCHES }, history = { ...WATER_HISTORY };
afterEach(() => { Object.assign(WATER_SWITCHES, defaults); Object.assign(WATER_HISTORY, history); vi.unstubAllGlobals(); });

/** The river's WGSL as the game builds it for one tier and switch set, with a PMREM-shaped sky in place. */
function riverShader(tier: QualityTier, water: string | null, traa = false, sloped = false) {
  vi.stubGlobal('requestAnimationFrame', () => 1);
  Object.assign(WATER_SWITCHES, parseWaterSwitches(water));
  const renderer = new GameRenderer({ canvas: { width: 1, height: 1, style: {} } as HTMLCanvasElement });
  renderer.hasFeature = () => false;
  Object.assign(renderer.backend, { capabilities: { getUniformBufferLimit: () => 65536 }, renderer });
  const scene = new THREE.Scene(), env = new Environment(scene, renderer, tier);
  const sky = new THREE.Texture(); sky.mapping = THREE.CubeUVReflectionMapping;
  sky.image = { width: 768, height: 1024 }; // a 128 px PMREM's cube-UV atlas
  Object.defineProperty(env, 'environmentMap', { get: () => sky });
  // TRAA's history as post.ts publishes it, and medium's beauty MRT (colour, puddle normals, motion).
  Object.assign(WATER_HISTORY, traa ? { depth: new THREE.DepthTexture(4, 4), color: new THREE.Texture(),
    camera: { view: new THREE.Matrix4(), projection: new THREE.Matrix4() }, beautyVelocity: true }
    : { depth: null, color: null, camera: null, beautyVelocity: false });
  const target = new THREE.RenderTarget(4, 4, { count: 3, depthTexture: new THREE.DepthTexture(4, 4) });
  ['output', 'normal', 'velocity'].forEach((name, i) => { target.textures[i].name = name; });
  if (traa) { renderer.setRenderTarget(target); renderer.setMRT(mrt({ output, normal: vec4(0), velocity: vec4(0) })); }
  const mesh = new THREE.Mesh(new THREE.PlaneGeometry(40, 40).rotateX(-Math.PI / 2));
  if (sloped) mesh.userData.water_slope = true;
  const river = new RiverMaterial(mesh, env, tier, false, renderer);
  mesh.material = river.material;
  const backend = renderer.backend as typeof renderer.backend & { createNodeBuilder(object: THREE.Object3D, renderer: THREE.WebGPURenderer): Builder };
  const builder = backend.createNodeBuilder(mesh, renderer);
  builder.scene = scene; builder.camera = new THREE.PerspectiveCamera(); builder.build();
  const result = { name: river.material.name, fragment: builder.fragmentShader, depthWrite: river.material.depthWrite,
    motion: river.material.mrtNode !== null };
  renderer.setRenderTarget(null); renderer.setMRT(null); target.dispose();
  river.dispose(); env.dispose();
  return result;
}

test('?water= parses known tokens and rejects unknown ones', () => {
  expect(parseWaterSwitches(null).tightSun).toBe(true);
  expect(parseWaterSwitches('oldsun').tightSun).toBe(false);
  expect(() => parseWaterSwitches('glass')).toThrow(/bad \?water=/);
});

test.each([['high', 'river-planar'], ['mobile', 'river-sky']] as const)('the %s river builds its tight sun lobe and wrap diffuse', (tier, name) => {
  const tight = riverShader(tier, null), old = riverShader(tier, 'oldsun');
  expect(tight.name).toBe(name);
  // The lobe's near exponent (1800) and the earlier lobe's 240 are both literal constants of their graphs.
  expect(tight.fragment).toMatch(/1800\.0/);
  expect(old.fragment).not.toMatch(/1800\.0/);
  expect(old.fragment).toMatch(/240\.0/);
});

test('with TRAA the river fades over the previous frame\'s bed depth and writes its own motion', () => {
  const faded = riverShader('medium', null, true);
  expect(faded.depthWrite).toBe(false);
  expect(faded.motion).toBe(true);
  expect(faded.fragment).toMatch(/texture_depth_2d/);
  const opaque = riverShader('medium', 'planar,nodepth,nomotion', true);
  expect(opaque.depthWrite).toBe(true);
  expect(opaque.motion).toBe(false);
  expect(opaque.fragment).not.toMatch(/texture_depth_2d/);
  // Without TRAA (fxaa, idPass) nothing changes: no history to read, no motion target.
  const plain = riverShader('medium', null, false);
  expect([plain.depthWrite, plain.motion]).toEqual([true, false]);
});

test('medium and reduced reflect by a screen-space march over TRAA\'s last frame instead of the planar pass', () => {
  const ssr = riverShader('reduced', null, true);
  expect(ssr.name).toBe('river-ssr');
  expect(riverShader('medium', null, true).name).toBe('river-ssr');
  expect(riverShader('high', null, true).name).toBe('river-planar');
  expect(ssr.depthWrite).toBe(false);
  // The march's loop and its colour fetch from the history (a filtered sample at level 0).
  expect(ssr.fragment).toMatch(/for \(/);
  expect(ssr.fragment).toMatch(/textureSampleLevel/);
  // Without history (fxaa) reduced falls back to the sky, and ?water= picks any path on any reflecting tier.
  expect(riverShader('reduced', null, false).name).toBe('river-sky');
  expect(riverShader('reduced', 'planar', true).name).toBe('river-planar');
  expect(riverShader('medium', 'ssr', true).name).toBe('river-ssr');
  expect(riverShader('medium', 'sky', true).name).toBe('river-sky');
  // Even with ?water=nodepth the march keeps the river out of the stored depth.
  expect(riverShader('reduced', 'nodepth', true).depthWrite).toBe(false);
  expect(riverShader('mobile', 'ssr', true).name).toBe('river-sky');
});

test('rain rings build behind a uniform branch, so dry water skips their arithmetic', () => {
  const { fragment } = riverShader('medium', null, true);
  // The ring grids' cell sizes and the branch on the shared rain uniform.
  expect(fragment).toMatch(/0\.55/);
  expect(fragment).toMatch(/if \( \( render\.\w+ > 0\.0 \) \)/);
});

test('sloped reaches flow downhill from their own triangles and break white where steep; flat reaches do not', () => {
  const sloped = riverShader('high', null, true, true), flat = riverShader('high', null, true);
  expect(sloped.name).toBe('river-sky');
  // Medium's march needs no mirror plane: its sloped reaches keep their screen-space reflection.
  expect(riverShader('medium', null, true, true).name).toBe('river-ssr');
  expect(sloped.fragment).toMatch(/dpdx/);
  expect(flat.fragment).not.toMatch(/dpdx/);
});

test('sky fallbacks show the sky at its visible display scale, and screen-space hits are soft four-tap reads', () => {
  // The visible sky's day display scale ramps over sun heights 0.07-0.26; the PMREM bake is at brightness 1.
  for (const [tier, water] of [['mobile', null], ['medium', 'sky'], ['reduced', null]] as const)
    expect(riverShader(tier, water, true).fragment).toMatch(/smoothstep\( 0\.07, 0\.26,/);
  const ssr = riverShader('reduced', null, true).fragment;
  expect((ssr.match(/textureSampleLevel/g) ?? []).length).toBeGreaterThanOrEqual(4);
});
