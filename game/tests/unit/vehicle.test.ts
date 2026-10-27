import { test, expect } from 'vitest';
import * as R from '@dimforge/rapier3d';
import { curbResponse, isCurbMaterial } from '../../src/vehicle/curbStrike';
import { handling } from '../../src/vehicle/handling';
import { MAT } from '../../src/shared/col/materials';
import { CURB_SCENARIOS, vehicleFixture, runCurbScenario, bumpsAlong, trimeshWallFixture } from '../fixtures/vehicle';
import { IMPACT_MIN_DELTA_V } from '../../src/vehicle/vehicle';
import { STEP_WINDOW } from '../../src/vehicle/bumps';

const coast = { throttle: 0, brake: 0, steer: 0, handbrake: false };

test('curb maths: proportional impulse, bounded losses, semantic selection and high-step block', () => {
  expect(curbResponse(3, 0.07, 1200).liftImpulse).toBeCloseTo(4 * curbResponse(1.5, 0.035, 1200).liftImpulse);
  for (const { height, speed, blocked } of CURB_SCENARIOS) {
    const r = curbResponse(speed, height, 1200);
    expect(r.blocked).toBe(blocked);
    if (!blocked) { const totalLoss = 1 - (1 - r.loss) ** 4; expect(totalLoss).toBeGreaterThanOrEqual(0.05); expect(totalLoss).toBeLessThanOrEqual(0.2); }
  }
  expect(isCurbMaterial(MAT.curb)).toBe(true);
  expect(isCurbMaterial(MAT.quay)).toBe(true);
  expect(isCurbMaterial(MAT.curb_dropped)).toBe(false);
  expect(isCurbMaterial(MAT.road)).toBe(false);
});

test('handling: analog pedals, speed-sensitive steering, LT reverse and handbrake grip', () => {
  expect(handling({ ...coast, throttle: 0.5 }, 0).engine).toBe(handling({ ...coast, throttle: 1 }, 0).engine / 2);
  expect(handling({ ...coast, brake: 1 }, 10).brake).toBeGreaterThan(0);
  expect(handling({ ...coast, brake: 0.5 }, 0).engine).toBeLessThan(0);
  expect(Math.abs(handling({ ...coast, steer: 1 }, 25).steer)).toBeLessThan(0.1);
  expect(handling({ ...coast, handbrake: true }, 10).rearGrip).toBeLessThan(handling(coast, 10).rearGrip / 2);
  expect(handling({ ...coast, throttle: 1 }, 30).engine).toBe(0);
});

for (const scenario of CURB_SCENARIOS) {
  test(`physical curb ${scenario.height} m at ${scenario.speed} m/s`, () => {
    const result = runCurbScenario(R, scenario);
    const { state: s, maxPitch, loss, strikes } = result;
    expect(strikes).toBeGreaterThan(0);
    if (scenario.blocked) {
      expect(s.pos.x).toBeLessThan(-1.5);
      expect(Math.abs(s.forwardSpeed)).toBeLessThan(0.1);
      expect(s.curbStrike?.blocked).toBe(true);
    } else {
      expect(maxPitch).toBeGreaterThanOrEqual(2);
      expect(loss).toBeGreaterThanOrEqual(0.05);
      expect(loss).toBeLessThanOrEqual(0.2);
      expect(s.pos.x).toBeGreaterThan(1.7);
    }
  });
}

test('CCD stops a car at 30 m/s at a thin building wall', () => {
  const { world, vehicle } = vehicleFixture(R, 8, MAT.building, false, 0.03);
  vehicle.body.setLinvel({ x: 30, y: 0, z: 0 }, true);
  let maxX = -Infinity;
  for (let i = 0; i < 120; i++) { vehicle.step(1 / 60, coast); world.step(); maxX = Math.max(maxX, vehicle.state().pos.x); }
  expect(maxX).toBeLessThan(-2);
  expect(vehicle.state().curbStrike).toBeNull();
  world.free();
});


test('one wheel curb strike rolls the chassis and quay uses the same response', () => {
  const { world, vehicle } = vehicleFixture(R, 0.12, MAT.quay, true);
  vehicle.body.setLinvel({ x: 6, y: 0, z: 0 }, true);
  let roll = 0;
  for (let i = 0; i < 120; i++) {
    vehicle.step(1 / 60, coast); world.step();
    const q = vehicle.state().rot;
    roll = Math.max(roll, Math.abs(Math.atan2(2 * (q.w * q.x + q.y * q.z), 1 - 2 * (q.x ** 2 + q.z ** 2))) * 180 / Math.PI);
  }
  expect(vehicle.state().curbStrike?.rumble).toBeGreaterThan(0);
  expect(roll).toBeGreaterThan(2);
  world.free();
});

for (const material of [MAT.curb_dropped, MAT.sidewalk]) {
  test(`geometry alone never creates a curb response (material ${material})`, () => {
    const { world, vehicle } = vehicleFixture(R, 0.12, material);
    vehicle.body.setLinvel({ x: 6, y: 0, z: 0 }, true);
    for (let i = 0; i < 100; i++) { vehicle.step(1 / 60, coast); world.step(); }
    expect(vehicle.state().pos.x).toBeGreaterThan(2);
    expect(vehicle.state().curbStrike).toBeNull();
    world.free();
  });
}

test('full throttle settles at 20–30 m/s and analog brake stops before reversing', () => {
  const { world, vehicle } = vehicleFixture(R, 0.01, MAT.road);
  for (let i = 0; i < 1500; i++) { vehicle.step(1 / 60, { ...coast, throttle: 1 }); world.step(); }
  expect(vehicle.state().speed).toBeGreaterThan(20);
  expect(vehicle.state().speed).toBeLessThan(30);
  for (let i = 0; i < 400; i++) { vehicle.step(1 / 60, { ...coast, brake: 0.7 }); world.step(); }
  expect(vehicle.state().forwardSpeed).toBeLessThan(-1);
  world.free();
});

test('full steering at road speed keeps the chassis upright', () => {
  const { world, vehicle } = vehicleFixture(R, 0.01, MAT.road);
  vehicle.body.setLinvel({ x: 25, y: 0, z: 0 }, true);
  let minUp = 1;
  for (let i = 0; i < 120; i++) {
    vehicle.step(1 / 60, { ...coast, steer: 1 }); world.step();
    const q = vehicle.state().rot;
    minUp = Math.min(minUp, 1 - 2 * (q.x * q.x + q.z * q.z));
  }
  expect(minUp).toBeGreaterThan(Math.cos(Math.PI / 4));
  world.free();
});


test('solver collision impulses produce one bounded-rate impact event, with quiet resting contact', () => {
  const { world, vehicle } = vehicleFixture(R, 8, MAT.building, false, 0.03);
  for (let i = 0; i < 30; i++) { vehicle.step(1 / 60, coast); world.step(); vehicle.afterStep(1 / 60); }
  expect(vehicle.state().impact).toBeNull();
  vehicle.body.setLinvel({ x: 15, y: 0, z: 0 }, true);
  let maxImpulse = 0;
  const ids = new Set<number>();
  for (let i = 0; i < 60; i++) {
    vehicle.step(1 / 60, coast); world.step(); vehicle.afterStep(1 / 60);
    const impact = vehicle.state().impact;
    if (impact) { maxImpulse = Math.max(maxImpulse, impact.impulse); ids.add(impact.id); }
  }
  expect(maxImpulse).toBeGreaterThan(600);
  expect(ids.size).toBeGreaterThan(0); expect(ids.size).toBeLessThanOrEqual(7);
  vehicle.reset({ x: -5, y: 0.8, z: 0 }, 0); expect(vehicle.state().impact).toBeNull();
  world.free();
});

// A lidar kerb: 15 cm over 0.6 m, sidewalk material (no curb strike). Road: 2 cm undulation over 5 m. Hill: 10 % grade.
const KERB = [[-60, 0], [0, 0], [0.6, 0.15], [40, 0.15]] as const;
const ROAD = Array.from({ length: 121 }, (_, k) => [-60 + k, 0.02 * Math.sin(k * 2 * Math.PI / 5)] as const);
const HILL = [[-60, 0], [0, 0], [40, 4]] as const;
const steps = (bumps: { height: number }[]) => bumps.filter(b => b.height).map(b => b.height);

for (const kind of ['car', 'scooter'] as const) {
  test(`${kind}: a kerb slope reports one step per axle at 2–12 m/s; road texture and a steady hill report no kerb`, () => {
    for (const speed of [2, 5, 12]) {
      const { bumps, state } = bumpsAlong(R, KERB, speed, 62 / speed, kind);
      expect(state.pos.x).toBeGreaterThan(2);
      expect(state.curbStrike).toBeNull();
      const up = steps(bumps);
      expect(up.length, `${kind} ${speed} m/s: ${up}`).toBeGreaterThanOrEqual(1);
      expect(up.length).toBeLessThanOrEqual(2);
      for (const h of up) { expect(h).toBeGreaterThan(0.11); expect(h).toBeLessThan(0.17); }
      expect(bumps.every(b => Math.abs(b.speed - speed) < 0.1 * speed + 0.3)).toBe(true);
    }
    for (const speed of [8, 14]) expect(bumpsAlong(R, ROAD, speed, 4, kind).bumps.filter(b => b.height || b.jolt > 1)).toEqual([]);
    expect(steps(bumpsAlong(R, HILL, 8, 5, kind).bumps).every(h => Math.abs(h) < 0.07)).toBe(true);
  });
}

test('the step measure ignores its own trailing lobe: no downward step follows a kerb within the lobe distance', () => {
  const { bumps } = bumpsAlong(R, KERB, 3, 22, 'car');
  expect(bumps.filter(b => b.height < 0)).toEqual([]);
  expect(STEP_WINDOW).toBeGreaterThan(0.5);
});

for (const [kind, speed] of [['car', 8], ['scooter', 2.5]] as const) {
  test(`${kind} at ${speed} m/s into a trimesh wall reports an impact of the momentum it loses`, () => {
    const { world, vehicle } = trimeshWallFixture(R, 6, kind);
    expect(vehicle.state().impact).toBeNull();
    vehicle.body.setLinvel({ x: speed, y: 0, z: 0 }, true);
    let peak = 0;
    for (let i = 0; i < 150; i++) {
      vehicle.step(1 / 60, coast); world.step(); vehicle.afterStep(1 / 60);
      peak = Math.max(peak, vehicle.state().impact?.impulse ?? 0);
    }
    const mass = vehicle.body.mass();
    expect(peak).toBeGreaterThan(IMPACT_MIN_DELTA_V * mass);
    expect(peak / mass).toBeGreaterThan(speed * 0.6);
    expect(peak / mass).toBeLessThan(speed * 1.3);
    world.free();
  });
}
