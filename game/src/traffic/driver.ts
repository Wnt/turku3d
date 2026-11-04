// GTA-style driver decisions as pure functions (docs/handover/gta-traffic-study.md), shared by the ambient traffic
// flow (traffic/flow.ts) and anything else that drives on the lane graph (delivery bots, autopilot). No state, no
// clock, no camera: the same inputs give the same answer on every peer.
//
//   unseen(watchers, x, z)        may a car at (x, z) be recycled: no watcher (camera or player) can see it
//   planPass(car, at, laneLength, obstacles, traffic, self?)
//                                 the lateral offset and end of a pass round stopped obstacles, or null when the
//                                 corridor beside them is not clear (same-way cars, or oncoming ones within
//                                 PASS_LOOKAHEAD s), it would leave the lane by more than PASS_MAX m, or would not end
//                                 PASS_CLEAR_LINE m before the lane's end (never pass a queue at a junction)
// Related pure helpers: traffic/pedestrians.ts vehicleStop / pedestrianStop (where to stop for a player's vehicle or a
// walker on the path) and TrafficFlow.stopFront(lane) (a junction stop point clear of crossing paths).

/** Hidden from a watcher: beyond HIDE_FAR m, or beyond HIDE_NEAR m outside its view cone widened by HIDE_MARGIN rad.
 *  The near radius sits beyond the ring's out-of-view draw radius (RING.removeOutOfView 85 m), the far one beyond its
 *  in-view radius (RING.removeInView 300 m), so a recycled car was not drawn. */
export const HIDE_FAR = 320, HIDE_NEAR = 90, HIDE_MARGIN = 0.35;
/** A pass keeps PASS_MARGIN m beside the obstacle, never leaves the lane by more than PASS_MAX m, needs the corridor
 *  clear for the pass plus PASS_LOOKAHEAD s of oncoming travel, and ends PASS_CLEAR_LINE m before the lane's end. */
export const PASS_MARGIN = 0.6, PASS_MAX = 3.8, PASS_LOOKAHEAD = 9, PASS_CLEAR_LINE = 15;

/** Someone who can see cars: a camera (position, unit forward, half field of view) or a position seen all round. */
export interface TrafficWatcher { x: number; z: number; fx?: number; fz?: number; halfFov?: number }

/** True when no watcher can see (x, z). */
export function unseen(watchers: readonly TrafficWatcher[], x: number, z: number): boolean {
  for (const w of watchers) {
    const dx = x - w.x, dz = z - w.z, d = Math.sqrt(dx * dx + dz * dz);
    if (d <= HIDE_NEAR) return false;
    if (d > HIDE_FAR) continue;
    if (w.fx === undefined || w.fz === undefined || w.halfFov === undefined) return false;
    if ((dx * w.fx + dz * w.fz) / d >= Math.cos(Math.min(Math.PI, w.halfFov + HIDE_MARGIN))) return false;
  }
  return true;
}

/** A point on the driven lane at arc length s, with its unit heading. */
export interface LanePoint { x: number; z: number; hx: number; hz: number }
/** An obstacle's footprint (centre, unit heading, size); `car` identifies it among `traffic` (skipped there). */
export interface Obstacle<C = unknown> { x: number; z: number; hx: number; hz: number; length: number; width: number; car?: C }
/** Other traffic the corridor must be clear of. */
export interface Mover { pose: { x: number; z: number; hx: number; hz: number }; width: number; speed: number }
/** Lateral offset from the lane centre (m, + right) and the arc length at which to steer back. */
export interface PassPlan { offset: number; end: number }

export function planPass<C extends Mover>(car: { s: number; length: number; width: number }, at: (s: number) => LanePoint,
  laneLength: number, obstacles: readonly Obstacle<C>[], traffic: Iterable<C>, self: (c: C) => boolean = () => false): PassPlan | null {
  const here = at(car.s);
  let lo = Infinity, hi = -Infinity, end = -Infinity;
  for (const v of obstacles) {
    const sv = car.s + (v.x - here.x) * here.hx + (v.z - here.z) * here.hz, p = at(sv);
    for (const a of [-0.5, 0.5]) for (const b of [-0.5, 0.5]) {
      const x = v.x + v.hx * a * v.length - v.hz * b * v.width, z = v.z + v.hz * a * v.length + v.hx * b * v.width;
      const across = -(x - p.x) * p.hz + (z - p.z) * p.hx;
      lo = Math.min(lo, across); hi = Math.max(hi, across);
    }
    end = Math.max(end, sv + v.length / 2 + car.length / 2 + 3);
  }
  const left = lo - car.width / 2 - PASS_MARGIN, right = hi + car.width / 2 + PASS_MARGIN;
  // Prefers the left (towards oncoming traffic) on a tie.
  const offset = Math.abs(left) <= Math.abs(right) ? left : right;
  if (Math.abs(offset) > PASS_MAX || end + PASS_CLEAR_LINE > laneLength) return null;
  const skip = new Set(obstacles.map(o => o.car));
  for (const other of traffic) {
    if (self(other) || skip.has(other)) continue;
    const dx = other.pose.x - here.x, dz = other.pose.z - here.z;
    const along = dx * here.hx + dz * here.hz, across = -dx * here.hz + dz * here.hx;
    const oncoming = Math.max(0, -(other.pose.hx * here.hx + other.pose.hz * here.hz) * other.speed);
    if (along < -car.length || along > end - car.s + 10 + oncoming * PASS_LOOKAHEAD) continue;
    if (Math.abs(across - offset) < (car.width + other.width) / 2 + 0.8) return null;
  }
  return { offset, end };
}
