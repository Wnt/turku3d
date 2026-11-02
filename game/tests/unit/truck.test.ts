import { expect, test } from 'vitest';
import * as R from '@dimforge/rapier3d';
import { CAR, TRUCK, TRUCK_TARGET_SPEED, VEHICLES } from '../../src/physics/constants';
import { CurbQueries } from '../../src/vehicle/curbQueries';
import { curbResponse } from '../../src/vehicle/curbStrike';
import { Vehicle } from '../../src/vehicle/vehicle';
import { chaseSettings } from '../../src/vehicle/chaseCamera';
import { GROUP, MAT } from '../../src/shared/col/materials';

const input = (v: Partial<{ throttle: number; brake: number; steer: number; handbrake: boolean }> = {}) =>
  ({ throttle: 0, brake: 0, steer: 0, handbrake: false, ...v });
const groups = (GROUP.STATIC_SOLID << 16) | GROUP.VEHICLE;

/** Flat 1.2 km by 600 m ground; an optional curb step at x = 0 or a 1 m high, 4 m long kicker ending at x = 0. */
function world(feature: { curb?: number; kicker?: boolean } = {}) {
  const w = new R.World({ x: 0, y: -9.81, z: 0 });
  w.timestep = 1 / 60;
  w.createCollider(R.ColliderDesc.cuboid(600, 0.5, 300).setTranslation(0, -0.5, 0).setCollisionGroups(groups));
  const surfaces = new CurbQueries(R, w);
  if (feature.curb) {
    const c = w.createCollider(R.ColliderDesc.cuboid(40, feature.curb / 2, 300).setTranslation(40, feature.curb / 2, 0).setCollisionGroups(groups));
    surfaces.add(c, c.shape, () => ({ material: MAT.curb, top: feature.curb! }));
  }
  if (feature.kicker) {
    const a = Math.atan2(1, 4);
    w.createCollider(R.ColliderDesc.cuboid(2.06, 0.2, 5).setTranslation(-2, 0.5 - 0.2 * Math.cos(a), 0)
      .setRotation({ x: 0, y: 0, z: Math.sin(a / 2), w: Math.cos(a / 2) }).setCollisionGroups(groups));
  }
  return { w, surfaces };
}

function drive(kind: 'car' | 'truck', x: number, feature: Parameters<typeof world>[0] = {}) {
  const { w, surfaces } = world(feature);
  const vehicle = new Vehicle(R, w, { x, y: 1.2, z: 0 }, 0, surfaces, { kind });
  const run = (seconds: number, i: ReturnType<typeof input>, each?: (s: ReturnType<Vehicle['state']>, t: number) => void) => {
    for (let f = 0; f < seconds * 60; f++) { vehicle.step(1 / 60, i); w.step(); each?.(vehicle.state(), f / 60); }
    return vehicle.state();
  };
  run(2, input());
  return { w, vehicle, run };
}

/** Angle between the chassis up axis and world up (degrees). */
const tilt = (q: { x: number; y: number; z: number; w: number }) =>
  Math.acos(Math.max(-1, Math.min(1, 1 - 2 * (q.x * q.x + q.z * q.z)))) * 180 / Math.PI;

test('truck tuning: heavy 4WD on big tyres and soft, strongly damped long-travel springs', () => {
  expect(VEHICLES.truck).toBe(TRUCK);
  expect(TRUCK_TARGET_SPEED * 3.6).toBeCloseTo(120);
  expect(TRUCK.driven).toEqual([0, 1, 2, 3]);
  expect(TRUCK.steered).toEqual([0, 1]);
  expect(TRUCK.frontTorque).toBeCloseTo(0.4);
  expect(TRUCK.suspensionTravel).toBeCloseTo(0.36);
  expect(TRUCK.mass).toBeGreaterThanOrEqual(2 * CAR.mass);
  expect(TRUCK.wheelRadius).toBeGreaterThan(CAR.wheelRadius * 1.3);
  // Spring rate per unit mass: softer. Damping ratio (4 c / (2 ω), ω = 2 √k for four wheels): higher.
  expect(TRUCK.suspensionStiffness).toBeLessThan(CAR.suspensionStiffness * 0.6);
  const zeta = (t: typeof CAR | typeof TRUCK, c: number) => 4 * c / (2 * 2 * Math.sqrt(t.suspensionStiffness));
  expect(zeta(TRUCK, TRUCK.suspensionCompression)).toBeGreaterThan(zeta(CAR, CAR.suspensionCompression));
  expect(zeta(TRUCK, TRUCK.suspensionRelaxation)).toBeGreaterThan(zeta(CAR, CAR.suspensionRelaxation));
  expect(TRUCK.curbFeel).toBeLessThan(CAR.curbFeel);
  // Static sag g / (4 k) leaves droop and bump travel either side of the ride height.
  const sag = 9.81 / (4 * TRUCK.suspensionStiffness);
  expect(sag).toBeGreaterThan(0.08);
  expect(sag).toBeLessThan(TRUCK.suspensionTravel - 0.08);
});

test('truck drivetrain: 40/60 torque split over all four wheels, handbrake on the rear axle only', () => {
  const { w, vehicle, run } = drive('truck', 0);
  run(0.2, input({ throttle: 1, handbrake: false }));
  const force = [0, 1, 2, 3].map(i => vehicle.controller.wheelEngineForce(i)!);
  const total = force.reduce((a, b) => a + b, 0);
  expect(total).toBeGreaterThan(0);
  expect((force[0] + force[1]) / total).toBeCloseTo(0.4, 5);
  expect(force[2]).toBeCloseTo(force[3], 5);
  run(0.2, input({ handbrake: true }));
  expect(vehicle.controller.wheelBrake(0)).toBe(0);
  expect(vehicle.controller.wheelBrake(2)).toBe(TRUCK.brakeForce);
  w.free();
});

test('truck physics: high ride height, 0-100 km/h in about 6 s, 120 km/h top speed, upright at full lock', () => {
  const car = drive('car', -500), truck = drive('truck', -500);
  const rest = truck.vehicle.state();
  expect(rest.pos.y - car.vehicle.state().pos.y).toBeGreaterThan(0.15);
  let t100 = 0;
  const top = truck.run(30, input({ throttle: 1 }), (s, t) => { if (!t100 && s.forwardSpeed * 3.6 >= 100) t100 = t; });
  expect(t100).toBeGreaterThan(4.5);
  expect(t100).toBeLessThan(8);
  expect(Math.abs(top.forwardSpeed - TRUCK_TARGET_SPEED) / TRUCK_TARGET_SPEED).toBeLessThan(0.05);
  let stop = NaN;
  truck.run(8, input({ brake: 1 }), s => { if (Number.isNaN(stop) && s.forwardSpeed < 0.3) stop = s.pos.x - top.pos.x; });
  expect(stop).toBeGreaterThan(20);
  expect(stop).toBeLessThan(60);
  let worst = 0;
  truck.run(5, input({ throttle: 1 }));
  truck.run(6, input({ throttle: 1, steer: 1 }), s => { worst = Math.max(worst, tilt(s.rot)); });
  expect(worst).toBeLessThan(10);
  car.w.free(); truck.w.free();
});

test('truck curbs: a 12 cm kerb at 40 km/h costs it no speed and barely tilts it; the car loses more', () => {
  /** Speed 6 m past x = 0 (where the kerb is) relative to the same run on the flat, holding a cruising throttle. */
  const pass = (kind: 'car' | 'truck', curb: number) => {
    const { w, vehicle, run } = drive(kind, -12, { curb });
    vehicle.body.setLinvel({ x: 40 / 3.6, y: 0, z: 0 }, true);
    let before = 0, after = 0, worst = 0, strike = null as ReturnType<Vehicle['state']>['curbStrike'];
    run(3, input({ throttle: 0.3 }), s => {
      if (s.pos.x < -3) before = s.forwardSpeed;
      if (s.pos.x > 6 && !after) after = s.forwardSpeed;
      strike ??= s.curbStrike;
      worst = Math.max(worst, tilt(s.rot));
    });
    w.free();
    return { before, after, worst, strike };
  };
  const truck = pass('truck', 0.12), truckFlat = pass('truck', 0), car = pass('car', 0.12), carFlat = pass('car', 0);
  expect(truck.strike?.blocked).toBe(false);
  expect(truck.strike?.height).toBeCloseTo(0.12, 2);
  expect(truck.before * 3.6).toBeGreaterThan(38);
  expect(truck.after).toBeGreaterThanOrEqual(truck.before);
  const cost = (kerb: typeof truck, flat: typeof truck) => 1 - kerb.after / flat.after;
  expect(cost(truck, truckFlat)).toBeLessThan(0.03);
  expect(cost(car, carFlat)).toBeGreaterThan(cost(truck, truckFlat) * 3);
  expect(truck.worst).toBeLessThan(5);
  expect(car.worst).toBeGreaterThan(truck.worst * 2);
  expect(curbResponse(11, 0.12, 2400, TRUCK.curbFeel).loss).toBeCloseTo(curbResponse(11, 0.12, 2400).loss * TRUCK.curbFeel);
});

test('truck jump: a 1 m kicker at 43 km/h flies, lands on its wheels without a chassis strike and drives on', () => {
  const { w, vehicle, run } = drive('truck', -40, { kicker: true });
  vehicle.body.setLinvel({ x: 12, y: 0, z: 0 }, true);
  let air = 0, airtime = 0, peak = 0, low = Infinity, impact = false;
  const end = run(5, input({ throttle: 0.5 }), s => {
    const flying = s.wheels.every(wh => !wh.contact) && s.pos.x > -1;
    if (flying) air += 1 / 60;
    else if (air > 0 && !airtime) airtime = air;
    if (s.pos.x > 0) peak = Math.max(peak, s.pos.y);
    if (airtime) low = Math.min(low, s.pos.y);
    impact ||= !!s.impact;
  });
  expect(airtime).toBeGreaterThan(0.5);
  expect(peak).toBeGreaterThan(2.2);
  // The suspension takes the landing: the chassis bottom (y - 0.4) never reaches the ground.
  expect(low - TRUCK.halfExt.y).toBeGreaterThan(0.25);
  expect(impact).toBe(false);
  expect(tilt(end.rot)).toBeLessThan(3);
  expect(end.forwardSpeed).toBeGreaterThan(10);
  w.free();
});

test('truck chase camera: higher and farther back than the car at every speed', () => {
  for (const speed of [0, 10, 33]) {
    const car = chaseSettings(speed, 'car'), truck = chaseSettings(speed, 'truck');
    expect(truck.distance - car.distance).toBeCloseTo(1.5);
    expect(truck.height).toBeGreaterThan(car.height + 0.4);
    expect(truck.fov).toBe(car.fov);
  }
});
