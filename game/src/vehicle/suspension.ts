// Long-travel suspension of the 4400 car (SPEC §3.3), as force curves in newtons per corner. Pure and Node-importable.
//
// Rapier 0.21's ray-cast wheel (verified in tests/unit/trophy4400-dynamics.test.ts "Rapier 0.21 suspension"):
//  (a) force = chassisMass · (k · (rest − L) + c · ḃ), with c the compression coefficient while the wheel compresses
//      and the relaxation coefficient while it extends, then clamped to [0, maxSuspensionForce];
//  (b) a contact length is clamped to [rest − maxSuspensionTravel, rest + maxSuspensionTravel]; past the short end
//      the spring force stops growing and the chassis collider takes the rest;
//  (c) a wheel without contact reports L = rest;
//  (d) the ray is rest + radius long, so a contact never reports L > rest.
// Each tick `schedule` therefore sets k and c so the linear Rapier spring through `rest` equals F(b) at the length
// predicted for the coming raycast, and the dampers equal the position-sensitive bypass zones.
import type { VehicleTuning } from '../physics/constants.ts';

export interface SuspensionAxle {
  wheels: readonly number[];
  /** Bump and droop travel from static ride (m). */
  bump: number;
  droop: number;
  /** Ride frequency near static (Hz). */
  frequency: number;
}
export interface SuspensionCurve {
  /** Static suspension length (connection point to hub, m). */
  staticLength: number;
  axles: readonly SuspensionAxle[];
}

/** One corner's force curve: F0 static load (N), K1 primary rate (N/m), `stop` the quadratic bump-stop gain (N/m²),
 *  `critical` the critical damping near static (N·s/m). */
export interface Corner { F0: number; K1: number; bump: number; droop: number; stop: number; critical: number; staticLength: number }

const G = 9.81;
const CROSSOVER = 0.5, STOP = 0.88, DROOP_FLOOR = 0.15, FULL_BUMP = 7, BLEND = 0.03;
/** Upper bound of the scheduled Rapier stiffness (per unit chassis mass); only reached right at `rest`. */
export const STIFFNESS_CAP = 1500;

function rawCorner(F0: number, K1: number, bump: number, droop: number, staticLength: number): Corner {
  const linear = F0 + K1 * CROSSOVER * bump + 2.2 * K1 * (1 - CROSSOVER) * bump;
  const stop = (FULL_BUMP * F0 - linear) / ((1 - STOP) * bump) ** 2;
  return { F0, K1, bump, droop, stop, critical: 2 * Math.sqrt(K1 * F0 / G), staticLength };
}

/** Per-wheel corners from the tuning: the static load follows the axle positions and `comAhead`. */
export function corners(t: VehicleTuning): Corner[] {
  const curve = t.suspensionCurve;
  if (!curve) return [];
  const axleX = curve.axles.map(a => a.wheels.reduce((s, i) => s + t.wheels[i].x, 0) / a.wheels.length);
  const com = t.comAhead ?? 0, [x0, x1] = axleX;
  const share = curve.axles.length === 2 ? [(com - x1) / (x0 - x1), (x0 - com) / (x0 - x1)] : curve.axles.map(() => 1 / curve.axles.length);
  const out: Corner[] = [];
  curve.axles.forEach((a, k) => {
    const F0 = t.mass * G * share[k] / a.wheels.length, K1 = (2 * Math.PI * a.frequency) ** 2 * F0 / G;
    for (const i of a.wheels) out[i] = rawCorner(F0, K1, a.bump, a.droop, curve.staticLength);
  });
  return out;
}

const smooth = (x: number, edge: number, width = BLEND) => {
  const u = Math.min(1, Math.max(0, (x - edge + width / 2) / width));
  return u * u * (3 - 2 * u);
};

/** Spring force (N) at compression b from static (+ bump). 0 beyond full droop: the wheel hangs from the limit strap. */
export function springForce(c: Corner, b: number): number {
  if (b < -c.droop) return 0;
  if (b <= 0) return c.F0 * (DROOP_FLOOR + (1 - DROOP_FLOOR) * (b + c.droop) / c.droop);
  const cross = CROSSOVER * c.bump;
  if (b <= cross) return c.F0 + c.K1 * b;
  const stopAt = STOP * c.bump;
  return c.F0 + c.K1 * cross + 2.2 * c.K1 * (b - cross) + (b > stopAt ? c.stop * (b - stopAt) ** 2 : 0);
}

/** Bypass damping coefficients (N·s/m) at compression b, by zone (SPEC §3.3 table), edges blended over 3 cm. */
export function damping(c: Corner, b: number): { compression: number; rebound: number } {
  if (b < -c.droop) return { compression: 0, rebound: 0 };
  const zone = (edge: number) => smooth(b, edge * c.bump);
  // 0.30 / 0.65 / 1.10 of critical (the spec's middle zone is 0.55; 0.65 keeps the 60 km/h kicker under 95% bump).
  const compression = c.critical * (0.30 + 0.35 * zone(0.40) + 0.45 * zone(0.75)) + 25000 * zone(STOP);
  const rebound = c.critical * (0.75 + 0.45 * smooth(-b, 0.85 * c.droop));
  return { compression, rebound };
}

/** Rapier per-unit-mass suspension parameters for the coming tick. `length` is the latest reported suspension length,
 *  `rate` its compression speed (m/s, + bump), `rest` Rapier's rest length. */
export function schedule(c: Corner, length: number, rate: number, rest: number, mass: number, dt: number, maxForce = Infinity) {
  const b = c.staticLength - length + rate * dt;
  const predicted = Math.min(c.bump, Math.max(-c.droop - 0.05, b));
  const spring = springForce(c, predicted), d = damping(c, predicted);
  // Rapier samples the contact velocity after the step's gravity: a steady bias on ḃ that its damper turns into a
  // lift of about c·0.29·g·dt (measured: the static ride then sits at b = 0); take it off the spring.
  const coef = rate >= 0 ? d.compression : d.rebound;
  const force = spring > 0 ? Math.max(0, spring - coef * 0.29 * G * dt) : 0;
  // Blow-off: the bypass valves open before spring plus damper reach the force limit, so a hard landing is soaked
  // by travel instead of bottoming against the clamp.
  const blow = rate > 0 ? Math.max(0, Math.min(d.compression, (maxForce - spring) / rate)) : d.compression;
  const lever = rest - (c.staticLength - predicted);
  const stiffness = force <= 0 ? 0 : Math.min(STIFFNESS_CAP, force / (mass * Math.max(1e-3, lever)));
  return { stiffness, compression: blow / mass, relaxation: d.rebound / mass, compressionFrom: predicted };
}

/** Static suspension length of a tuning: the curve's, or the linear Rapier spring's rest minus g / (4k). */
export const staticLength = (t: VehicleTuning) => t.suspensionCurve?.staticLength ?? t.rest - G / (4 * t.suspensionStiffness);

/** Full-droop length of wheel `i`: published hubs never hang lower than the limit strap. */
export function droopLength(t: VehicleTuning, i: number): number {
  const axle = t.suspensionCurve?.axles.find(a => a.wheels.includes(i));
  return axle ? t.suspensionCurve!.staticLength + axle.droop : t.rest;
}
