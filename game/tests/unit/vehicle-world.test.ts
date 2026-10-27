import { test, expect } from 'vitest';
import * as R from '@dimforge/rapier3d';
import fs from 'node:fs';
import { PhysicsWorld, DT } from '../../src/physics/world';
import { GROUP } from '../../src/shared/col/materials';

const idle = { vx: 0, vz: 0, jump: false };
const coast = { throttle: 0, steer: 0, brake: 0, handbrake: false };

test('real .col curb faces resolve by contact point, survive unloading, and do not change KCC topology', async () => {
  const pw = new PhysicsWorld(R);
  const bytes = fs.readFileSync(new URL('../../public/dev-tiles/collision/t0_0_0/c_2_3.col', import.meta.url));
  await pw.loadTile('curb', bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength));
  pw.spawnVehicle({ x: 174, y: 2.8, z: 243 }, Math.PI / 2);
  for (let i = 0; i < 90; i++) pw.fixedStep(idle, coast);
  pw.vehicle!.body.setLinvel({ x: 0, y: 0, z: -6 }, true);
  let pitch = 0;
  for (let i = 0; i < 100; i++) {
    pw.fixedStep(idle, coast);
    const q = pw.vehicleState()!.rot;
    // +X body axis transformed to world; elevation is independent of heading.
    pitch = Math.max(pitch, Math.abs(Math.asin(2 * (q.x * q.y + q.w * q.z))) * 180 / Math.PI);
  }
  const s = pw.vehicleState()!;
  expect(s.curbStrike?.height).toBeCloseTo(0.12, 3);
  expect(s.curbStrike!.id).toBeLessThanOrEqual(4);
  expect(s.pos.z).toBeLessThan(236);
  expect(pitch).toBeGreaterThan(2);
  pw.unloadTile('curb'); pw.world.step();
  expect(pw.raycast({ x: 174, y: 10, z: 238 }, { x: 0, y: -1, z: 0 }, 20)).toBeNull();
  pw.resetVehicle({ x: 174, y: 2.8, z: 243 }, Math.PI / 2);
  for (let i = 0; i < 20; i++) pw.fixedStep(idle, coast);
  expect(pw.vehicleState()!.curbStrike).toBeNull();
  expect(pw.vehicleState()!.pos.y).toBeLessThan(2.8);
  pw.world.free();
});

test('camera sphere keeps the near plane away from a building, including off-centre obstacles', () => {
  const pw = new PhysicsWorld(R);
  pw.world.createCollider(R.ColliderDesc.cuboid(0.015, 5, 5).setTranslation(3, 3, 0)
    .setCollisionGroups((GROUP.STATIC_SOLID << 16) | 0xff00));
  pw.world.step();
  const distance = pw.cameraCast({ x: 0, y: 3, z: 0 }, { x: 1, y: 0, z: 0 }, 8);
  expect(distance).toBeGreaterThan(2.6);
  expect(distance).toBeLessThan(2.7);
  expect(pw.cameraCast({ x: 0, y: 3, z: 5.2 }, { x: 1, y: 0, z: 0 }, 8)).toBeLessThan(3);
  expect(pw.cameraCast({ x: 0, y: 3, z: 6 }, { x: 1, y: 0, z: 0 }, 8)).toBe(8);
  expect(pw.step(DT, idle, null)).toBe(1);
  pw.world.free();
});
