// Slip-curve tyres (vehicle/tyres.ts): the solver's force laws on a synthetic body, and the starter car on them.
import { expect, test } from 'vitest';
import { loadFactor, slipMu, solveTyres, TARMAC, tyreState, type BodyState, type TyrePatch, type TyreResult } from '../../src/vehicle/tyres';

const DT = 1 / 60, deg = Math.PI / 180, up = { x: 0, y: 1, z: 0 };
/** A 1350 kg car, CoM 0.72 m up, wheels at ±1.3 m and ±0.77 m; `steer` turns the front pair to the left. */
function car(v: { x: number; z: number }, steer = 0, patch: Partial<TyrePatch> = {}) {
  const wheel = (x: number, z: number, axle: 0 | 1): TyrePatch => {
    const s = axle ? 0 : steer;
    return { point: { x, y: 0, z }, normal: up, forward: { x: Math.cos(s), y: 0, z: -Math.sin(s) }, side: { x: Math.sin(s), y: 0, z: Math.cos(s) },
      load: 3300, axle, drive: 0, brake: 0, locked: false, grip: 1, ...patch };
  };
  const body: BodyState = { lin: { x: v.x, y: 0, z: v.z }, ang: { x: 0, y: 0, z: 0 }, com: { x: 0, y: 0.72, z: 0 }, invMass: 1 / 1350,
    invInertia: [1 / 425, 0, 0, 0, 1 / 2186, 0, 0, 0, 1 / 1841], gravity: { x: 0, y: -9.81, z: 0 } };
  return { body, wheels: [wheel(1.3, -0.77, 0), wheel(1.3, 0.77, 0), wheel(-1.3, -0.77, 1), wheel(-1.3, 0.77, 1)] };
}
const solve = (c: ReturnType<typeof car>, state = tyreState(4), t = TARMAC) => {
  const out: TyreResult[] = [];
  const r = solveTyres(c.body, c.wheels, t, 3300, DT, out, state);
  return { out, ...r };
};

test('slip curve and load sensitivity follow the Wreckfest 2 fits', () => {
  const [front, rear] = TARMAC.lateral;
  expect(slipMu(front, 0)).toBe(0);
  // No fall-off: 25° of slip carries more than 12°, both close to the 1.16 / 1.09 asymptotes.
  expect(slipMu(front, 25 * deg)).toBeGreaterThan(slipMu(front, 12 * deg));
  expect(slipMu(front, 25 * deg)).toBeCloseTo(1.16, 2);
  expect(slipMu(rear, 9 * deg)).toBeGreaterThan(1.0);
  // Small-slip stiffness about 0.3-0.4 μ per degree.
  expect(slipMu(front, 1 * deg)).toBeGreaterThan(0.28); expect(slipMu(front, 1 * deg)).toBeLessThan(0.4);
  // A lightly loaded wheel grips harder per newton than a heavily loaded one.
  expect(loadFactor(TARMAC, 1500, 2600)).toBeGreaterThan(loadFactor(TARMAC, 5000, 2600) * 1.1);
});

test('at road speed the side force is μ(α, Fz)·Fz; at walking pace a tyre holds instead', () => {
  // 60 km/h with the front wheels steered 3°: each front tyre gives the curve's force, the rear none.
  const fast = solve(car({ x: 16.7, z: 0 }, 3 * deg));
  // The first step starts the relaxation: a fresh contact's slip angle relaxes from zero over `relaxation` metres.
  const relaxed = 3 * deg * Math.min(1, Math.max(16.7 / TARMAC.relaxation, 1 / TARMAC.relaxationTime) * DT);
  const expected = slipMu(TARMAC.lateral[0], relaxed) * loadFactor(TARMAC, 3300, 3300) * 3300;
  for (const i of [0, 1]) expect(-fast.out[i].fy).toBeCloseTo(expected, -1);
  // A sideways drift at 0.3 m/s is stopped within a few steps at about μg, like static friction, without reversing.
  // (This body has no suspension to resist roll, so the side force acts at the centre of mass's height here.)
  const slow = car({ x: 0, z: 0.3 }), held = tyreState(4), level = { ...TARMAC, rollCentre: [0.72, 0.72] as const };
  for (let i = 0; i < 4; i++) { solve(slow, held, level); expect(slow.body.lin.z).toBeGreaterThan(-0.01); }
  expect(Math.abs(slow.body.lin.z)).toBeLessThan(0.01);
});

test('braking stops at the anti-lock share and keeps side grip; full drive still leaves side force', () => {
  const braking = car({ x: 20, z: 0 }, 0, { brake: 20000 });
  const b = solve(braking);
  const peak = TARMAC.longitudinal.peak * loadFactor(TARMAC, 3300, 3300) * 3300;
  expect(-b.out[0].fx).toBeCloseTo(TARMAC.brakeShare * peak, -1);
  // Cornering at the limit on full throttle: the driven front tyres share the ellipse instead of losing all side force.
  const state = tyreState(4);
  state.lag.fill(8 * deg);
  const powered = car({ x: 15, z: 0 }, 8 * deg, { drive: 10000 });
  const p = solve(powered, state);
  expect(p.out[0].fx).toBeGreaterThan(0.5 * peak);
  expect(-p.out[0].fy).toBeGreaterThan(0.5 * slipMu(TARMAC.lateral[0], 8 * deg) * 3300);
  // Traction control: never more than the peak.
  expect(Math.hypot(p.out[0].fx, p.out[0].fy)).toBeLessThan(1.05 * Math.max(peak, TARMAC.lateral[0].peak * 3300 * loadFactor(TARMAC, 3300, 3300)));
});

test('the starter car steers about 25° at 60 km/h and corners on its tyres, not its lock', async () => {
  const R = await import('@dimforge/rapier3d');
  const { PhysicsWorld } = await import('../../src/physics/world');
  const { modelTuning } = await import('../../src/vehicle/modelTuning');
  const { PLAYER_MODEL } = await import('../../src/actors/fleet/catalog');
  const { handling } = await import('../../src/vehicle/handling');
  const t = modelTuning(PLAYER_MODEL)!;
  expect(t.tyre).toBeDefined();
  const lock60 = -handling({ throttle: 0, brake: 0, steer: 1, handbrake: false }, 60 / 3.6, t).steer / deg;
  expect(lock60).toBeGreaterThan(22); expect(lock60).toBeLessThan(28);
  const pw = new PhysicsWorld(R);
  pw.world.createCollider(R.ColliderDesc.cuboid(2000, .5, 2000).setTranslation(0, -.5, 0));
  pw.spawnVehicle({ x: 0, y: .8, z: 0 }, 0, { tuning: t });
  pw.driveVehicle('player');
  for (let i = 0; i < 60; i++) pw.fixedStep(null, { throttle: 0, brake: 0, steer: 0, handbrake: false });
  const body = pw.vehicle!.body;
  body.setLinvel({ x: 50 / 3.6, y: 0, z: 0 }, true);
  // A quarter stick at 50 km/h for 2.5 s: real slip angles (Rapier's tyre had under 0.2°) and a bounded yaw rate.
  let slip = 0, yaw = 0;
  for (let i = 0; i < 150; i++) {
    pw.fixedStep(null, { throttle: 0.3, brake: 0, steer: -0.25, handbrake: false });
    if (i >= 120) { slip = Math.max(slip, Math.abs(pw.vehicle!.state().wheels[1].tyre!.lateral)); yaw = body.angvel().y; }
  }
  expect(slip).toBeGreaterThan(0.2);
  expect(yaw).toBeGreaterThan(0.3); expect(yaw).toBeLessThan(1.2);
  pw.world.free();
});
