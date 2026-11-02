import { expect, test } from 'vitest';
import * as T from 'three/webgpu';
import * as R from '@dimforge/rapier3d';
import { PhysicsWorld } from '../../src/physics/world';
import type NodeBuilder from 'three/src/nodes/core/NodeBuilder.js';
import { GameRenderer } from '../../src/render/shadowMaterials';
import { GameLighting } from '../../src/render/lighting';
import { VehicleBeams } from '../../src/vehicle/beams';
import { vehicleLamps } from '../../src/vehicle/lamps';
import { nightUniforms } from '../../src/render/nightLights';
import type { VehicleState } from '../../src/physics/protocol';

test('two shadowless beams stay in one packed shader across off, dipped and long', () => {
  const renderer = new GameRenderer({ canvas: { width: 1, height: 1, style: {} } as HTMLCanvasElement });
  renderer.hasFeature = () => false;
  Object.assign(renderer.backend, { capabilities: { getUniformBufferLimit: () => 65536 } });
  const scene = new T.Scene(), beam = new VehicleBeams(scene, { raycast: async () => null });
  expect(beam.lights).toHaveLength(2); expect(beam.lights.every(l => !l.castShadow)).toBe(true);
  const material = new T.MeshStandardNodeMaterial(), mesh = new T.Mesh(new T.BoxGeometry(), material);
  const lighting = new GameLighting({ point: 4 }), lights = [...beam.lights, new T.PointLight(), new T.HemisphereLight()];
  type Builder = NodeBuilder & { scene: T.Scene; camera: T.Camera; lightsNode: T.LightsNode; build(): void; fragmentShader: string };
  const backend = renderer.backend as typeof renderer.backend & { createNodeBuilder(object: T.Object3D, renderer: T.WebGPURenderer): Builder };
  const programs = [], lightsNode = lighting.createNode(lights);
  for (const mode of [0, 1, 2] as const) {
    nightUniforms.night.value = 1;
    beam.update({ id: 'player', lights: mode, pos: { x: 0, y: 1, z: 0 }, rot: { x: 0, y: 0, z: 0, w: 1 } } as VehicleState, 0);
    const builder = backend.createNodeBuilder(mesh, renderer);
    builder.scene = scene; builder.camera = new T.PerspectiveCamera(); builder.lightsNode = lightsNode;
    builder.build(); programs.push(builder.fragmentShader);
  }
  expect(new Set(programs).size).toBe(1);
  expect(programs[0]).toContain('array< vec4<f32>, 14 >');
  beam.dispose(); nightUniforms.night.value = 0;
});

test('named lens materials are independent for each actor and off means no emission', () => {
  const material = new T.MeshStandardMaterial({ name: 'lamp-front-left' });
  const source = new T.Group(); source.add(new T.Mesh(new T.BoxGeometry(.1,.1,.1), material));
  const remote = source.clone(true);
  vehicleLamps(source, 'car', 2); vehicleLamps(remote, 'car', 0);
  expect((source.children[0] as T.Mesh).material).not.toBe((remote.children[0] as T.Mesh).material);
  expect(((source.children[0] as T.Mesh).material as T.MeshStandardMaterial).emissiveIntensity).toBe(12);
  expect(((remote.children[0] as T.Mesh).material as T.MeshStandardMaterial).emissiveIntensity).toBe(0);
});

test('a long fleet chassis does not extinguish its beams, while the wall ahead still masks them', async () => {
  const world = new PhysicsWorld(R), scene = new T.Scene();
  world.spawnVehicle({ x: 0, y: .75, z: 0 }, 0, { id: 'van', halfExt: { x: 3, y: .3, z: 1 } });
  world.driveVehicle('van');
  world.world.createCollider(R.ColliderDesc.cuboid(.1, 5, 20).setTranslation(12, 4, 0));
  world.world.step();
  const beam = new VehicleBeams(scene, { raycast: async (origin, dir, max, filter) => world.raycast(origin, dir, max, filter) });
  try {
    nightUniforms.night.value = 1;
    const state = { ...world.vehicleState()!, lights: 2 as const };
    beam.update(state, 0);
    await new Promise(resolve => setTimeout(resolve, 0));
    beam.update(state, 50);
    const depths = beam.lights[0].userData.depths as number[];
    expect(depths).toHaveLength(9);
    expect(depths[4]).toBeCloseTo(9.77, 1);
    expect(depths.every(d => d > 9 && d < 12)).toBe(true);
  } finally { beam.dispose(); world.world.free(); nightUniforms.night.value = 0; }
});
