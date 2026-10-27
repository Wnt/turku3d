import { expect, test } from 'vitest';
import * as R from '@dimforge/rapier3d';
import { CAR, SCOOTER, SCOOTER_TARGET_SPEED } from '../../src/physics/constants';
import { CurbQueries } from '../../src/vehicle/curbQueries';
import { Vehicle } from '../../src/vehicle/vehicle';
import { GROUP, MAT } from '../../src/shared/col/materials';
import { scooterLean, MAX_LEAN } from '../../src/vehicle/lean';

const input = (v: Partial<{ throttle: number; brake: number; steer: number; handbrake: boolean }> = {}) =>
  ({ throttle: 0, brake: 0, steer: 0, handbrake: false, ...v });

/** Flat 1.2 km by 60 m ground with an optional curb step at x = 0 (whole width). */
function flat(curb = 0) {
  const world = new R.World({ x: 0, y: -9.81, z: 0 });
  world.timestep = 1 / 60;
  const groups = (GROUP.STATIC_SOLID << 16) | GROUP.VEHICLE;
  world.createCollider(R.ColliderDesc.cuboid(600, 0.5, 300).setTranslation(0, -0.5, 0).setCollisionGroups(groups));
  const surfaces = new CurbQueries(R, world);
  if (curb) {
    const c = world.createCollider(R.ColliderDesc.cuboid(40, curb / 2, 300).setTranslation(40, curb / 2, 0).setCollisionGroups(groups));
    surfaces.add(c, c.shape, () => ({ material: MAT.curb, top: curb }));
  }
  return { world, surfaces };
}

function ride(kind: 'car' | 'scooter', x = -500, curb = 0) {
  const { world, surfaces } = flat(curb);
  const vehicle = new Vehicle(R, world, { x, y: kind === 'car' ? 0.8 : 0.55, z: 0 }, 0, surfaces, { kind });
  const run = (seconds: number, i: ReturnType<typeof input>, each?: (s: ReturnType<Vehicle['state']>) => void) => {
    for (let f = 0; f < seconds * 60; f++) { vehicle.step(1 / 60, i); world.step(); each?.(vehicle.state()); }
    return vehicle.state();
  };
  run(1.5, input());
  return { world, vehicle, run };
}

const roll = (q: { x: number; y: number; z: number; w: number }) => {
  // Angle between the chassis up axis and world up.
  const upY = 1 - 2 * (q.x * q.x + q.z * q.z);
  return Math.acos(Math.max(-1, Math.min(1, upY))) * 180 / Math.PI;
};

test('scooter parameters: a light narrow two-wheeler with outriggers, slower and tighter than the car', () => {
  expect(SCOOTER.wheels).toHaveLength(4);
  expect(SCOOTER.wheels[0].z).toBe(0);
  expect(SCOOTER.wheels[1].z).toBe(0);
  expect(SCOOTER.steered).toEqual([0]);
  expect(SCOOTER.driven).toEqual([1]);
  expect(Math.abs(SCOOTER.wheels[2].z)).toBeLessThan(SCOOTER.halfExt.z + 0.1);
  expect(SCOOTER.mass).toBeLessThan(CAR.mass / 4);
  expect(SCOOTER_TARGET_SPEED * 3.6).toBeCloseTo(45);
  const wheelbase = (s: typeof SCOOTER | typeof CAR) => s.wheels[0].x - s.wheels[s.driven[0]].x;
  expect(wheelbase(SCOOTER) / Math.tan(SCOOTER.maxSteer)).toBeLessThan(wheelbase(CAR) / Math.tan(CAR.maxSteer) / 2);
});

test('scooter lean: follows lateral acceleration, capped at 15 degrees', () => {
  expect(scooterLean(0, 0.5)).toBe(0);
  expect(scooterLean(5, 0)).toBe(0);
  expect(scooterLean(6, 0.1)).toBeGreaterThan(0);
  expect(scooterLean(6, -0.1)).toBeLessThan(0);
  expect(scooterLean(12.5, 0.7)).toBeCloseTo(MAX_LEAN);
  expect(MAX_LEAN).toBeCloseTo(15 * Math.PI / 180);
});

test('scooter physics: 45 km/h top speed, moped acceleration and braking, upright in a full-lock turn', () => {
  const { world, run } = ride('scooter');
  let t30 = 0, time = 0;
  const top = run(20, input({ throttle: 1 }), s => { time += 1 / 60; if (!t30 && s.forwardSpeed >= 30 / 3.6) t30 = time; });
  expect(Math.abs(top.forwardSpeed - SCOOTER_TARGET_SPEED) / SCOOTER_TARGET_SPEED).toBeLessThan(0.05);
  expect(t30).toBeGreaterThan(2);
  expect(t30).toBeLessThan(6);
  let stopX = NaN;
  run(6, input({ brake: 1 }), s => { if (Number.isNaN(stopX) && s.forwardSpeed < 0.3) stopX = s.pos.x; });
  // 12.5 m/s to rest at 4-7 m/s^2: 11-20 m.
  expect(stopX - top.pos.x).toBeGreaterThan(9);
  expect(stopX - top.pos.x).toBeLessThan(22);
  let maxRoll = 0;
  run(8, input({ throttle: 1 }));
  run(6, input({ throttle: 1, steer: 1 }), s => { maxRoll = Math.max(maxRoll, roll(s.rot)); });
  expect(maxRoll).toBeLessThan(8);
  world.free();
});

test('scooter turns tighter than the car at walking pace', () => {
  const radius = (kind: 'car' | 'scooter') => {
    const { world, run } = ride(kind, 0);
    const xs: number[] = [], zs: number[] = [];
    run(2, input({ throttle: 0.4 }));
    run(8, input({ throttle: 0.25, steer: 1 }), s => { xs.push(s.pos.x); zs.push(s.pos.z); });
    world.free();
    return (Math.max(...xs) - Math.min(...xs) + Math.max(...zs) - Math.min(...zs)) / 4;
  };
  const scooter = radius('scooter'), car = radius('car');
  expect(scooter).toBeLessThan(car * 0.6);
});

test('scooter curbs: a 12 cm kerb is felt but passable at 4 m/s, a 30 cm step blocks', () => {
  const pass = ride('scooter', -6, 0.12);
  pass.vehicle.body.setLinvel({ x: 4, y: 0, z: 0 }, true);
  let strike = null as ReturnType<Vehicle['state']>['curbStrike'];
  const over = pass.run(4, input({ throttle: 0.3 }), s => { strike ??= s.curbStrike; });
  expect(strike?.blocked).toBe(false);
  expect(strike?.height).toBeCloseTo(0.12, 2);
  expect(over.pos.x).toBeGreaterThan(2);
  expect(roll(over.rot)).toBeLessThan(10);
  pass.world.free();
  const block = ride('scooter', -3, 0.3);
  block.vehicle.body.setLinvel({ x: 2, y: 0, z: 0 }, true);
  const held = block.run(4, input({ throttle: 0.4 }));
  expect(held.curbStrike?.height).toBeCloseTo(0.3, 2);
  expect(held.pos.x).toBeLessThan(0);
  expect(Math.abs(held.forwardSpeed)).toBeLessThan(0.1);
  block.world.free();
});
