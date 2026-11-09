import { expect, test } from 'vitest';
import * as T from 'three/webgpu';
import * as R from '@dimforge/rapier3d';
import { PhysicsWorld } from '../../src/physics/world';
import type NodeBuilder from 'three/src/nodes/core/NodeBuilder.js';
import { GameRenderer } from '../../src/render/shadowMaterials';
import { GameLighting } from '../../src/render/lighting';
import { VehicleBeams } from '../../src/vehicle/beams';
import { flattenFleetAsset } from '../../src/actors/fleet/authored';
import { vehicleLamps } from '../../src/vehicle/lamps';
import { nightUniforms } from '../../src/render/nightLights';
import type { VehicleState } from '../../src/physics/protocol';

test('the beams of the driven car and two remote cars stay in one packed shader across off, dipped and long', () => {
  const renderer = new GameRenderer({ canvas: { width: 1, height: 1, style: {} } as HTMLCanvasElement });
  renderer.hasFeature = () => false;
  Object.assign(renderer.backend, { capabilities: { getUniformBufferLimit: () => 65536 } });
  const scene = new T.Scene(), beam = new VehicleBeams(scene, { raycast: async () => null });
  expect(beam.lights).toHaveLength(6); expect(beam.lights.every(l => !l.castShadow)).toBe(true);
  const material = new T.MeshStandardNodeMaterial(), mesh = new T.Mesh(new T.BoxGeometry(), material);
  const lighting = new GameLighting({ point: 4 }), lights = [...beam.lights, new T.PointLight(), new T.HemisphereLight()];
  type Builder = NodeBuilder & { scene: T.Scene; camera: T.Camera; lightsNode: T.LightsNode; build(): void; fragmentShader: string };
  const backend = renderer.backend as typeof renderer.backend & { createNodeBuilder(object: T.Object3D, renderer: T.WebGPURenderer): Builder };
  const programs = [], lightsNode = lighting.createNode(lights);
  for (const mode of [0, 1, 2] as const) {
    nightUniforms.night.value = 1;
    beam.update({ id: 'player', lights: mode, pos: { x: 0, y: 1, z: 0 }, rot: { x: 0, y: 0, z: 0, w: 1 } } as VehicleState, 0,
      [{ id: 'bot', lights: mode, pos: { x: 20, y: 1, z: 0 }, rot: { x: 0, y: 0, z: 0, w: 1 } }], { x: 0, y: 2, z: 5 });
    const builder = backend.createNodeBuilder(mesh, renderer);
    builder.scene = scene; builder.camera = new T.PerspectiveCamera(); builder.lightsNode = lightsNode;
    builder.build(); programs.push(builder.fragmentShader);
  }
  expect(new Set(programs).size).toBe(1);
  expect(programs[0]).toContain('array< vec4<f32>, 42 >');
  beam.dispose(); nightUniforms.night.value = 0;
});

test('named lens materials are independent for each actor and off means no emission', () => {
  const material = new T.MeshStandardMaterial({ name: 'lamp-front-left' });
  const source = new T.Group(); source.add(new T.Mesh(new T.BoxGeometry(.1,.1,.1), material));
  const remote = source.clone(true);
  vehicleLamps(source, 'car', 2); vehicleLamps(remote, 'car', 0);
  expect((source.children[0] as T.Mesh).material).not.toBe((remote.children[0] as T.Mesh).material);
  expect(((source.children[0] as T.Mesh).material as T.MeshStandardMaterial).emissiveIntensity).toBe(6);
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

test('authored fleet GLBs retain named front and running-tail roles in their single draw', () => {
  const root = new T.Group(), paint = new T.MeshStandardMaterial({ name: 'fleet_paint' });
  const front = new T.Mesh(new T.BoxGeometry(), paint); front.name = 'lamp-front-left';
  const tail = new T.Mesh(new T.BoxGeometry(), new T.MeshStandardMaterial({ name: 'lamp-tail-right' }));
  root.add(front, tail);
  const geometry = flattenFleetAsset(root), tags = geometry.getAttribute('fleetSurface');
  const lamps = Array.from({ length: tags.count }, (_, i) => tags.getW(i));
  expect(lamps).toContain(4);
  expect(lamps).toContain(1);
  expect(Array.from({ length: tags.count }, (_, i) => tags.getX(i)).every(v => v === 0)).toBe(true);
  geometry.dispose();
});

test("remote players' lit cars get the spare beams: nearest first, off and far ones none, a car keeps its pair", () => {
  const beam = new VehicleBeams(new T.Scene(), { raycast: async () => null });
  const car = (id: string, x: number, lights: 0 | 1 | 2 = 1) => ({ id, lights, pos: { x, y: 1, z: 0 }, rot: { x: 0, y: 0, z: 0, w: 1 } });
  const lit = () => beam.lights.map(l => l.intensity > 0 ? Math.round(l.parent!.position.x) : null);
  try {
    nightUniforms.night.value = 1;
    beam.update(null, 0, [car('far', 400), car('dark', 10, 0), car('b', 60), car('a', 30, 2), car('c', 90)], { x: 0, y: 1, z: 0 });
    expect(lit()).toEqual([null, null, 30, 30, 60, 60]);
    // b is now the nearer one: both keep the pair they had (and its occlusion fan).
    beam.update(null, 16, [car('a', 70, 2), car('b', 60), car('c', 90)], { x: 0, y: 1, z: 0 });
    expect(lit()).toEqual([null, null, 70, 70, 60, 60]);
    // a falls behind c: c takes a's pair.
    beam.update(null, 24, [car('a', 120, 2), car('b', 60), car('c', 90)], { x: 0, y: 1, z: 0 });
    expect(lit()).toEqual([null, null, 90, 90, 60, 60]);
    nightUniforms.night.value = 0;
    beam.update(null, 32, [car('a', 30, 2)], { x: 0, y: 1, z: 0 });
    expect(lit().every(x => x === null)).toBe(true);
  } finally { beam.dispose(); nightUniforms.night.value = 0; }
});
