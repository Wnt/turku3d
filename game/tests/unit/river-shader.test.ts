import { afterEach, expect, test, vi } from 'vitest';
import * as THREE from 'three/webgpu';
import type NodeBuilder from 'three/src/nodes/core/NodeBuilder.js';
import { GameRenderer } from '../../src/render/shadowMaterials';
import { Environment } from '../../src/render/environment';
import { RiverMaterial } from '../../src/render/water';
import { WATER_SWITCHES, parseWaterSwitches } from '../../src/render/waterSwitches';
import type { QualityTier } from '../../src/render/quality';

type Builder = NodeBuilder & { scene: THREE.Scene; camera: THREE.Camera; build(): void; fragmentShader: string };
const defaults = { ...WATER_SWITCHES };
afterEach(() => { Object.assign(WATER_SWITCHES, defaults); vi.unstubAllGlobals(); });

/** The river's WGSL as the game builds it for one tier and switch set, with a PMREM-shaped sky in place. */
function riverShader(tier: QualityTier, water: string | null) {
  vi.stubGlobal('requestAnimationFrame', () => 1);
  Object.assign(WATER_SWITCHES, parseWaterSwitches(water));
  const renderer = new GameRenderer({ canvas: { width: 1, height: 1, style: {} } as HTMLCanvasElement });
  renderer.hasFeature = () => false;
  Object.assign(renderer.backend, { capabilities: { getUniformBufferLimit: () => 65536 } });
  const scene = new THREE.Scene(), env = new Environment(scene, renderer, tier);
  const sky = new THREE.Texture(); sky.mapping = THREE.CubeUVReflectionMapping;
  sky.image = { width: 768, height: 1024 }; // a 128 px PMREM's cube-UV atlas
  Object.defineProperty(env, 'environmentMap', { get: () => sky });
  const mesh = new THREE.Mesh(new THREE.PlaneGeometry(40, 40).rotateX(-Math.PI / 2));
  const river = new RiverMaterial(mesh, env, tier, false, renderer);
  mesh.material = river.material;
  const backend = renderer.backend as typeof renderer.backend & { createNodeBuilder(object: THREE.Object3D, renderer: THREE.WebGPURenderer): Builder };
  const builder = backend.createNodeBuilder(mesh, renderer);
  builder.scene = scene; builder.camera = new THREE.PerspectiveCamera(); builder.build();
  const result = { name: river.material.name, fragment: builder.fragmentShader };
  river.dispose(); env.dispose();
  return result;
}

test('?water= parses known tokens and rejects unknown ones', () => {
  expect(parseWaterSwitches(null).tightSun).toBe(true);
  expect(parseWaterSwitches('oldsun').tightSun).toBe(false);
  expect(() => parseWaterSwitches('glass')).toThrow(/bad \?water=/);
});

test.each([['medium', 'river-planar'], ['mobile', 'river-sky']] as const)('the %s river builds its tight sun lobe and wrap diffuse', (tier, name) => {
  const tight = riverShader(tier, null), old = riverShader(tier, 'oldsun');
  expect(tight.name).toBe(name);
  // The lobe's near exponent (1800) and the earlier lobe's 240 are both literal constants of their graphs.
  expect(tight.fragment).toMatch(/1800\.0/);
  expect(old.fragment).not.toMatch(/1800\.0/);
  expect(old.fragment).toMatch(/240\.0/);
});
