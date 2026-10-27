// Edge walls (physics/edgeWalls.ts): the character and a car stop at the edge of the loaded collision, rays and the
// camera pass through, and loading the neighbour opens the edge. Synthetic dev tiles (public/dev-tiles, ground H 2).
import { test, expect } from 'vitest';
import * as R from '@dimforge/rapier3d';
import fs from 'node:fs';
import { PhysicsWorld, DT } from '../../src/physics/world';

const idle = { vx: 0, vz: 0, jump: false };
const coast = { throttle: 0, steer: 0, brake: 0, handbrake: false };
const col = (id: string) => {
  const b = fs.readFileSync(new URL(`../../public/dev-tiles/collision/t0_0_0/${id}.col`, import.meta.url));
  return b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength) as ArrayBuffer;
};
const steps = (pw: PhysicsWorld, seconds: number, input = idle, vehicle: typeof coast | null = null) => {
  for (let k = 0; k < Math.round(seconds / DT); k++) pw.fixedStep(input, vehicle);
};

test('open edges follow the loaded cells; a neighbour closes the shared edge and unloading reopens it', async () => {
  const pw = new PhysicsWorld(R);
  await pw.loadTile('c_3_2', col('c_3_2'));
  expect(pw.edges.edges()).toEqual([[3, 2, 0], [3, 2, 1], [3, 2, 2], [3, 2, 3]]);
  await pw.loadTile('c_3_3', col('c_3_3'));
  expect(pw.edges.edges()).toEqual([[3, 2, 0], [3, 2, 1], [3, 2, 3], [3, 3, 0], [3, 3, 1], [3, 3, 2]]);
  pw.unloadTile('c_3_3');
  expect(pw.edges.edges()).toEqual([[3, 2, 0], [3, 2, 1], [3, 2, 2], [3, 2, 3]]);
  // Reloading the same id replaces its colliders and keeps one wall per open edge.
  await pw.loadTile('c_3_2', col('c_3_2'));
  expect(pw.edges.edges()).toHaveLength(4);
  pw.world.free();
});

test('the character walking east stops at the edge of the loaded collision instead of falling off', async () => {
  const pw = new PhysicsWorld(R);
  await pw.loadTile('c_3_2', col('c_3_2'));
  pw.world.step();
  pw.spawnCharacter({ x: 240, y: 2.05, z: 156 });
  steps(pw, 0.5);
  steps(pw, 4, { vx: 5, vz: 0, jump: false });
  const s = pw.characterState()!;
  expect(s.pos.x).toBeLessThan(250);
  expect(s.pos.x).toBeGreaterThan(248);
  expect(s.pos.y).toBeCloseTo(2, 1);
  expect(s.grounded).toBe(true);
  // Scene queries ignore the wall: a ray east from inside the tile hits nothing, the camera cast is not shortened.
  expect(pw.raycast({ x: 245, y: 3, z: 156 }, { x: 1, y: 0, z: 0 }, 20)).toBeNull();
  expect(pw.cameraCast({ x: 245, y: 3, z: 156 }, { x: 1, y: 0, z: 0 }, 20)).toBe(20);
  pw.world.free();
});

test('a car driven at speed into the edge stays on the tile; with the neighbour loaded it drives on', async () => {
  const pw = new PhysicsWorld(R);
  await pw.loadTile('c_2_2', col('c_2_2'));
  await pw.loadTile('c_3_2', col('c_3_2'));
  pw.world.step();
  pw.spawnVehicle({ x: 140, y: 2.8, z: 156 }, 0);
  steps(pw, 1, idle, coast);
  pw.vehicle!.body.setLinvel({ x: 30, y: 0, z: 0 }, true);
  steps(pw, 4, idle, coast);
  let s = pw.vehicleState()!;
  expect(s.pos.x).toBeLessThan(250);
  expect(s.pos.y).toBeGreaterThan(2);

  // The c_2_2 | c_3_2 edge is closed: a car from c_2_2 drives across it.
  pw.resetVehicle({ x: 170, y: 2.8, z: 156 }, 0);
  steps(pw, 1, idle, coast);
  pw.vehicle!.body.setLinvel({ x: 12, y: 0, z: 0 }, true);
  steps(pw, 2, idle, coast);
  s = pw.vehicleState()!;
  expect(s.pos.x).toBeGreaterThan(190);
  expect(s.pos.x).toBeLessThan(250);
  pw.world.free();
});

test('checkTile decodes without adding colliders and rejects a damaged tile', async () => {
  const pw = new PhysicsWorld(R);
  expect((await pw.checkTile(col('c_3_2'))).tris).toBeGreaterThan(0);
  expect(pw.hasTile('c_3_2')).toBe(false);
  expect(pw.edges.edges()).toEqual([]);
  const bad = col('c_3_2'); new Uint8Array(bad)[200] ^= 0xff;
  await expect(pw.checkTile(bad)).rejects.toThrow(/CRC/);
  pw.world.free();
});
