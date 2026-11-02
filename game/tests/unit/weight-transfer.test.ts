import { expect, test } from 'vitest';
import * as R from '@dimforge/rapier3d';
import { vehicleFixture } from '../fixtures/vehicle';
import { MAT } from '../../src/shared/col/materials';
import { tyreRollImpulse } from '../../src/vehicle/weightTransfer';
import { rotate } from '../../src/vehicle/math';

for (const yaw of [0, 0.7, Math.PI / 2]) for (const steer of [-0.7, 0.7]) test(`cornering loads the outside springs and recentres (yaw ${yaw}, steer ${steer})`, () => {
  const { world, vehicle } = vehicleFixture(R, 0.001, MAT.road);
  try {
    vehicle.reset({ x: -5, y: 0.8, z: 0 }, yaw);
    const coast = { throttle: 0, steer: 0, brake: 0, handbrake: false };
    for (let i = 0; i < 120; i++) { vehicle.step(1 / 60, coast); world.step(); }
    vehicle.body.setLinvel(rotate({ x: 12, y: 0, z: 0 }, vehicle.body.rotation()), true);
    let peak = 0, axleDifference = 0;
    for (let i = 0; i < 120; i++) {
      vehicle.step(1 / 60, { ...coast, steer }); world.step();
      const s = vehicle.state(), q = s.rot;
      const forward = rotate({ x: 1, y: 0, z: 0 }, q), up = rotate({ x: 0, y: 1, z: 0 }, q);
      const lateralUp = (-up.x * forward.z + up.z * forward.x) / Math.hypot(forward.x, forward.z);
      peak = Math.max(peak, Math.abs(Math.atan2(lateralUp, up.y)));
      axleDifference = Math.max(axleDifference, (s.wheels[1].suspension - s.wheels[0].suspension) * Math.sign(steer));
    }
    expect(peak).toBeGreaterThan(1.5 * Math.PI / 180);
    expect(peak).toBeLessThan(7 * Math.PI / 180);
    expect(axleDifference).toBeGreaterThan(0.04);
    for (let i = 0; i < 240; i++) { vehicle.step(1 / 60, { ...coast, brake: 0.4 }); world.step(); }
    const q = vehicle.state().rot;
    expect(Math.hypot(q.x, q.z)).toBeLessThan(0.003);
  } finally { world.free(); }
});

test('tyre roll cannot add airborne control or unbounded landing impulses', () => {
  for (const contacts of [0, 1]) expect(tyreRollImpulse(1200, 2, 0.7, 1 / 60, 0.55, contacts, 1)).toBe(0);
  expect(tyreRollImpulse(1200, 2, 0.7, 1 / 60, 0.55, 4, 0)).toBe(0);
  expect(tyreRollImpulse(1200, 200, 0.7, 1 / 60, 0.55, 4, 1))
    .toBe(tyreRollImpulse(1200, 0.2, 0.7, 1 / 60, 0.55, 4, 1));
});
