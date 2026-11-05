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
/** Shape of every corner's spring and bypass damping, relative to its static load F0 and critical damping. */
export interface SuspensionShape {
  /** Spring force at full droop as a share of F0, and the tender knee: at `droopKnee[0]` of droop the force is
   *  `droopKnee[1]` × F0. The soft tender lets a corner's inside wheel hang low and stay loaded; past the knee the
   *  limit section falls steeply to the floor, so roll firms up near full droop. */
  droopFloor: number;
  droopKnee: readonly [number, number];
  /** Fraction of bump where the main spring takes over at `rate2` × K1. */
  crossover: number;
  rate2: number;
  /** Fraction of bump where the hydraulic bump stop starts; it reaches `fullBump` × F0 at full bump. */
  stop: number;
  fullBump: number;
  /** Compression damping as shares of critical: from the droop side to 40 % bump, 40–75 %, 75–100 %. */
  compression: readonly [number, number, number];
  /** Rebound as a share of critical, and in the last 15 % of droop (top-out). */
  rebound: number;
  topOut: number;
  /** Extra compression damping (N·s/m) of the bump-stop zone. */
  stopDamping: number;
  /** High-speed compression circuit: N·s/m added per corner above `knee` m/s of shaft speed (big landings). */
  highSpeed: { knee: number; coef: number };
}
export interface SuspensionCurve {
  /** Static suspension length (connection point to hub, m). */
  staticLength: number;
  axles: readonly SuspensionAxle[];
  shape: SuspensionShape;
}

/** One corner's force curve: F0 static load (N), K1 primary rate (N/m), `stop` the quadratic bump-stop gain (N/m²),
 *  `critical` the critical damping near static (N·s/m). */
export interface Corner { F0: number; K1: number; bump: number; droop: number; stop: number; critical: number; staticLength: number; shape: SuspensionShape }

const G = 9.81;
const BLEND = 0.03;
/** Upper bound of the scheduled Rapier stiffness (per unit chassis mass); only reached right at `rest`. */
export const STIFFNESS_CAP = 1500;

function rawCorner(F0: number, K1: number, bump: number, droop: number, staticLength: number, shape: SuspensionShape): Corner {
  const linear = F0 + K1 * shape.crossover * bump + shape.rate2 * K1 * (1 - shape.crossover) * bump;
  const stop = (shape.fullBump * F0 - linear) / ((1 - shape.stop) * bump) ** 2;
  return { F0, K1, bump, droop, stop, critical: 2 * Math.sqrt(K1 * F0 / G), staticLength, shape };
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
    for (const i of a.wheels) out[i] = rawCorner(F0, K1, a.bump, a.droop, curve.staticLength, curve.shape);
  });
  return out;
}

const smooth = (x: number, edge: number, width = BLEND) => {
  const u = Math.min(1, Math.max(0, (x - edge + width / 2) / width));
  return u * u * (3 - 2 * u);
};

/** Spring force (N) at compression b from static (+ bump). 0 beyond full droop: the wheel hangs from the limit strap. */
export function springForce(c: Corner, b: number): number {
  const s = c.shape;
  if (b < -c.droop) return 0;
  if (b <= 0) {
    const d = -b / c.droop, [kx, ky] = s.droopKnee;
    return c.F0 * (d <= kx ? 1 - (1 - ky) * d / kx : ky - (ky - s.droopFloor) * (d - kx) / (1 - kx));
  }
  const cross = s.crossover * c.bump;
  if (b <= cross) return c.F0 + c.K1 * b;
  const stopAt = s.stop * c.bump;
  return c.F0 + c.K1 * cross + s.rate2 * c.K1 * (b - cross) + (b > stopAt ? c.stop * (b - stopAt) ** 2 : 0);
}

/** Bypass damping coefficients (N·s/m) at compression b, by zone (SPEC §3.3 table), edges blended over 3 cm. */
export function damping(c: Corner, b: number): { compression: number; rebound: number } {
  if (b < -c.droop) return { compression: 0, rebound: 0 };
  const s = c.shape, [z0, z1, z2] = s.compression;
  const zone = (edge: number) => smooth(b, edge * c.bump);
  const compression = c.critical * (z0 + (z1 - z0) * zone(0.40) + (z2 - z1) * zone(0.75)) + s.stopDamping * zone(s.stop);
  const rebound = c.critical * (s.rebound + (s.topOut - s.rebound) * smooth(-b, 0.85 * c.droop));
  return { compression, rebound };
}

/** Rapier per-unit-mass suspension parameters for the coming tick. `length` is the latest reported suspension length,
 *  `rate` its compression speed (m/s, + bump), `rest` Rapier's rest length. */
export function schedule(c: Corner, length: number, rate: number, rest: number, mass: number, dt: number, maxForce = Infinity) {
  const b = c.staticLength - length + rate * dt;
  const predicted = Math.min(c.bump, Math.max(-c.droop - 0.05, b));
  const spring = springForce(c, predicted), d = damping(c, predicted);
  // Rapier samples the contact velocity after the step's gravity: a steady bias on ḃ that its damper turns into a
  // lift of about c·0.12·g·dt with the shape in constants.ts (measured: the static ride then sits at b = 0, D1); take it
  // off the spring. The factor depends on the damper-to-spring ratio near static: re-measure D1 after a retune.
  // High-speed compression: above the knee shaft speed the piston's blow-off stack adds damping, so a big landing
  // is braked from first touch on the droop side instead of arriving at the bump stop at full speed.
  const hs = c.shape.highSpeed, compression = d.compression + (rate > hs.knee ? hs.coef * (1 - hs.knee / rate) : 0);
  const coef = rate >= 0 ? compression : d.rebound;
  const force = spring > 0 ? Math.max(0, spring - coef * 0.12 * G * dt) : 0;
  // Blow-off: the bypass valves open before spring plus damper reach the force limit, so a hard landing is soaked
  // by travel instead of bottoming against the clamp.
  const blow = rate > 0 ? Math.max(0, Math.min(compression, (maxForce - spring) / rate)) : compression;
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
