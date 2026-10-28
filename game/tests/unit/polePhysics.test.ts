import { expect, test } from 'vitest';
import * as R from '@dimforge/rapier3d';
import { DT, PhysicsWorld } from '../../src/physics/world';
import { GROUP } from '../../src/shared/col/materials';

const idle = { vx: 0, vz: 0, jump: false };
const coast = { throttle: 0, steer: 0, brake: 0, handbrake: false };

function world() {
  const pw = new PhysicsWorld(R);
  pw.world.createCollider(R.ColliderDesc.cuboid(20, 0.5, 10).setTranslation(0, -0.5, 0)
    .setCollisionGroups((GROUP.STATIC_SOLID << 16) | 0xffff));
  pw.setPoles([{ id: 'sign', base: { x: 5, y: 0, z: 0 }, yaw: 0, height: 2.7 }], []);
  pw.spawnVehicle({ x: 0, y: 0.8, z: 0 }, 0);
  for (let i = 0; i < 20; i++) pw.fixedStep(idle, coast);
  return pw;
}

test('a vehicle strikes a solid pole, breaks it once above 10 km/h and leaves falling debris', () => {
  const pw = world();
  pw.vehicle!.body.setLinvel({ x: 5, y: 0, z: 0 }, true);
  let broke = 0, stopped = false, falling = false;
  for (let i = 0; i < 120; i++) {
    pw.fixedStep(idle, coast);
    const car = pw.vehicle!.body.linvel();
    if (car.x < 4) stopped = true;
    const state = pw.poleStates()[0];
    if (state.brokeNow) broke++;
    if (state.broken && Math.abs(state.rot.x) + Math.abs(state.rot.z) > 0.02) falling = true;
  }
  expect(stopped).toBe(true);
  expect(broke).toBe(1);
  expect(falling).toBe(true);
  pw.world.free();
});

test('walking into a pole remains blocked and never breaks it', () => {
  const pw = world();
  pw.spawnCharacter({ x: 3, y: 0, z: 0 });
  for (let i = 0; i < 120; i++) pw.fixedStep({ vx: 3, vz: 0, jump: false }, null);
  expect(Math.abs(pw.characterState()!.pos.z)).toBeGreaterThan(0.3);
  expect(pw.poleStates()[0].broken).toBe(false);
  expect(pw.step(DT, idle, null)).toBe(1);
  pw.world.free();
});
