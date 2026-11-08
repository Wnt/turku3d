import { COLLISION_TILE, collisionTileId } from '../core/origin';
import type { VehicleInput } from './protocol';

/** Every cell touched by an actor's swept ground footprint must have a collider in this worker.
 * Manifest membership and decoded draft buffers are insufficient. Includes stationary actors and negative cells. */
export function collisionCovers(loaded: { has(id: string): boolean }, x: number, z: number,
  nextX = x, nextZ = z, radius = 0): boolean {
  const i0 = Math.floor((Math.min(x, nextX) - radius) / COLLISION_TILE);
  const i1 = Math.floor((Math.max(x, nextX) + radius) / COLLISION_TILE);
  const j0 = Math.floor((Math.min(z, nextZ) - radius) / COLLISION_TILE);
  const j1 = Math.floor((Math.max(z, nextZ) + radius) / COLLISION_TILE);
  for (let i = i0; i <= i1; i++) for (let j = j0; j <= j1; j++) {
    if (!loaded.has(collisionTileId(i, j))) return false;
  }
  return true;
}

/** A driven car is eased down at this deceleration (m/s²) before collision that has not arrived, so it stops short of
 *  the gap instead of being frozen at its edge (the hold below stays the guard against falling through). */
export const COVERAGE_EASE_DECEL = 5;
/** The eased car comes to rest this far (m) before its footprint would touch the missing cell. */
const COVERAGE_EASE_MARGIN = 2;
/** Search step along the heading, and the resolution the gap is then found to (m). */
const COVERAGE_REACH_STEP = 4, COVERAGE_REACH_FINE = 0.5;

/** Distance (m) a circle of `radius` at (x, z) can travel along the unit direction (dx, dz) with every touched cell in
 *  `loaded`, searched up to `limit` m (Infinity when all of it is covered), to within COVERAGE_REACH_FINE m below. */
export function coverageReach(loaded: { has(id: string): boolean }, x: number, z: number, dx: number, dz: number,
  radius: number, limit: number): number {
  return reachAlong(s => collisionCovers(loaded, x + dx * s, z + dz * s, x + dx * s, z + dz * s, radius), limit);
}

/** Margin (m) around a chassis box where only the body meets what is ahead (an edge wall): tyres and some slack. */
export const BODY_MARGIN = 0.5;

/** A car's chassis footprint: half length `a` along its heading (ux, uz), half width `b` across it (m). */
export interface BodyBox { ux: number; uz: number; a: number; b: number }

/** Distance (m) the box centred at (x, z) can travel along the unit direction (dx, dz) before it overlaps a cell in
 *  `blocked`, searched like coverageReach. A car driving along such a cell (a wall beside the road) is not stopped. */
export function bodyReach(blocked: { has(id: string): boolean }, x: number, z: number, dx: number, dz: number,
  box: BodyBox, limit: number): number {
  return reachAlong(s => !boxTouches(blocked, x + dx * s, z + dz * s, box), limit);
}

/** Does the box centred at (x, z) overlap a cell in `cells`? Separating axes: the cell range comes from the box's
 *  bounds, then the box's own two axes are tested. */
export function boxTouches(cells: { has(id: string): boolean }, x: number, z: number, box: BodyBox): boolean {
  const { ux, uz, a, b } = box, ax = Math.abs(ux), az = Math.abs(uz);
  const ex = a * ax + b * az, ez = a * az + b * ax, cell = COLLISION_TILE / 2 * (ax + az);
  for (let i = Math.floor((x - ex) / COLLISION_TILE); i <= Math.floor((x + ex) / COLLISION_TILE); i++)
    for (let j = Math.floor((z - ez) / COLLISION_TILE); j <= Math.floor((z + ez) / COLLISION_TILE); j++) {
      if (!cells.has(collisionTileId(i, j))) continue;
      const mx = (i + 0.5) * COLLISION_TILE - x, mz = (j + 0.5) * COLLISION_TILE - z;
      if (Math.abs(mx * ux + mz * uz) <= a + cell && Math.abs(mz * ux - mx * uz) <= b + cell) return true;
    }
  return false;
}

/** March `clear(s)` out to `limit` m in COVERAGE_REACH_STEP steps, then refine the first failing step. */
function reachAlong(clear: (s: number) => boolean, limit: number): number {
  for (let s = 0; s <= limit; s += COVERAGE_REACH_STEP) {
    if (clear(s)) continue;
    // Refine within the last step: a coarse answer makes the eased speed fall in steps (a jolt of full braking).
    let lo = Math.max(0, s - COVERAGE_REACH_STEP);
    while (lo + COVERAGE_REACH_FINE < s && clear(lo + COVERAGE_REACH_FINE)) lo += COVERAGE_REACH_FINE;
    return lo;
  }
  return Infinity;
}

/** The distance (m) to search ahead of a car at `speed` (m/s): its eased stopping distance and a margin. */
export function easeReachLimit(speed: number): number {
  return speed * speed / (2 * COVERAGE_EASE_DECEL) + COVERAGE_EASE_MARGIN + 2 * COVERAGE_REACH_STEP;
}

/** Highest speed (m/s) from which a car with `reach` m of collision ahead still stops smoothly before the gap. */
export function easedSpeed(reach: number): number {
  return Math.sqrt(2 * COVERAGE_EASE_DECEL * Math.max(0, reach - COVERAGE_EASE_MARGIN));
}

/** The driver's input eased for `allowed` m/s towards missing collision: no drive towards the gap close to it, and
 *  braking (the brake pedal going forwards, forward drive rolling backwards: vehicle/handling.ts) in proportion to the
 *  overspeed. Steering and the handbrake stay the driver's. `forwardSpeed` is signed along the chassis. */
export function easeInput(input: VehicleInput, forwardSpeed: number, speed: number, allowed: number): VehicleInput {
  if (speed < 0.9 * allowed) return input;
  const brake = speed > allowed ? Math.min(1, 0.25 + (speed - allowed) / 4) : 0;
  if (forwardSpeed >= 0) return { ...input, throttle: Math.min(0, input.throttle), brake: Math.max(input.brake, brake) };
  return { ...input, throttle: Math.max(brake, input.throttle), brake: 0 };
}

/** Maximum simulated seconds waiting for active collision loads before safe reset. */
export const COVERAGE_HOLD_SECONDS = 2.5;

/** Current player's hold survives skipped snapshots; placement or restored coverage clears it. */
export class PlayerCoverageHold {
  state: { actor: string; recover: boolean } | null = null;
  private since = 0;

  block(actor: string, time: number, loading: boolean) {
    if (this.state?.actor !== actor) {
      this.since = time;
      this.state = { actor, recover: false };
    }
    this.state.recover ||= !loading || time - this.since >= COVERAGE_HOLD_SECONDS;
  }

  clear(actor?: string) {
    if (actor === undefined || this.state?.actor === actor) this.state = null;
  }
}
