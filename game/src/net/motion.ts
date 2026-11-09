// The `w`, `ctl` and `st` pose fields of a car (server/PROTOCOL.md): what a receiver needs to simulate another player's
// car forward to its own present, beyond the pose and linear velocity. `server/pose.js` validates the same ranges.
import type { V3 } from '../core/origin';
import type { VehicleInput, VehicleState } from '../physics/protocol';

/** [throttle percent −100..100, brake percent 0..100, steer per mille −1000..1000, flags 0..7]. Flags: 1 handbrake,
 *  2 digital key steering (the car slews it, VehicleInput.steerKey), 4 driven. Every car pose carries it, so a
 *  shallow pose patch never leaves a former driver's controls behind: an undriven car is [0, 0, 0, 0]. */
export type Ctl = [throttle: number, brake: number, steer: number, flags: number];
/** Angular velocity (rad/s) in the pose's frame: about E, about N, about H (up). */
export type Spin = [e: number, n: number, h: number];

const LIMITS: readonly (readonly [number, number])[] = [[-100, 100], [0, 100], [-1000, 1000], [0, 7]];
export const SPIN_MAX = 100;
const clampInt = (v: number, [lo, hi]: readonly [number, number]) => Math.min(hi, Math.max(lo, Math.round(Number.isFinite(v) ? v : 0)));
const milli = (v: number) => Math.round(Math.max(-SPIN_MAX, Math.min(SPIN_MAX, Number.isFinite(v) ? v : 0)) * 1000) / 1000;

export const UNDRIVEN: Ctl = [0, 0, 0, 0];

export function encodeCtl(input: VehicleInput | null | undefined): Ctl {
  if (!input) return [...UNDRIVEN];
  const flags = (input.handbrake ? 1 : 0) | (input.steerKey ? 2 : 0) | 4;
  return [clampInt(input.throttle * 100, LIMITS[0]), clampInt(input.brake * 100, LIMITS[1]), clampInt(input.steer * 1000, LIMITS[2]), flags];
}

/** A well-formed `ctl`: four integers within their ranges. */
export const validCtl = (c: unknown): c is Ctl => Array.isArray(c) && c.length === 4
  && c.every((n, i) => Number.isInteger(n) && n >= LIMITS[i][0] && n <= LIMITS[i][1]);

/** The driver input a `ctl` stands for, or null for an undriven car (or a malformed or absent field). */
export function decodeCtl(c: unknown): VehicleInput | null {
  if (!validCtl(c) || !(c[3] & 4)) return null;
  return { throttle: c[0] / 100, brake: c[1] / 100, steer: c[2] / 1000, handbrake: (c[3] & 1) !== 0, ...(c[3] & 2 ? { steerKey: true } : {}) };
}

export const validSpin = (w: unknown): w is Spin => Array.isArray(w) && w.length === 3
  && w.every(n => typeof n === 'number' && Number.isFinite(n) && Math.abs(n) <= SPIN_MAX);

/** Engine-frame angular velocity (x east, y up, z south) of a pose's `w`. */
export function spinOf(w: unknown): V3 | null {
  return validSpin(w) ? { x: w[0], y: w[2], z: -w[1] } : null;
}

/** A car state's `w` and `ctl` pose fields. */
export function motionOf(state: Pick<VehicleState, 'angvel' | 'controls'>): { w?: Spin; ctl: Ctl } {
  const w = state.angvel;
  return { ...(w ? { w: [milli(w.x), milli(-w.z), milli(w.y)] as Spin } : {}), ctl: encodeCtl(state.controls) };
}
