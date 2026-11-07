// The ground behind a stuck car before the autopilot backs it out (follow.ts reverse-out, autopilot.ts): a stuck
// recovery used to reverse blind, and after a nose-in against a lamp post on a quay edge it backed the car over the
// edge into the canal (Amsterdam, Grimburgwal). The reverse arc is checked with downward rays at the rear bumper's
// centre and corners: drivable ground all the way, no step or drop larger than a kerb, nothing that is not there.
import type { RayHit } from '../../physics/protocol';
import type { VehicleGeometry, VehiclePose } from './follow';

type V3 = { x: number; y: number; z: number };
export type GroundRay = (origin: V3, dir: V3, maxToi: number) => Promise<RayHit | null>;

/** Rear-axle travel checked (m): the follower's reverse-out distance (REVERSE_M 4) and a margin. */
export const REVERSE_CHECK_M = 5;
/** Largest height change (m) between neighbouring checks a reversing car may cross: a kerb, not a quay edge. */
export const REVERSE_STEP_M = 0.4;
/** Rays start this far (m) above the last ground and look this far down. */
const RAY_UP = 1.5, RAY_DOWN = 4;

/** Rear-bumper check points along the reverse arc for `steer` (the follower's command, + right), every metre of
 *  rear-axle travel: centre and both corners. The rear axle moves along the steering circle backwards. */
export function reversePoints(pose: VehiclePose, steer: number, g: VehicleGeometry): { x: number; z: number }[][] {
  const back = (g.back ?? g.rearOffset + 1) - g.rearOffset, half = (g.halfWidth ?? 0.9) - 0.05;
  const curvature = Math.tan(g.maxSteer * steer) / g.wheelbase;
  let hx = pose.hx, hz = pose.hz, x = pose.x - hx * g.rearOffset, z = pose.z - hz * g.rearOffset;
  const rows: { x: number; z: number }[][] = [];
  const step = 0.25;
  for (let d = step; d <= REVERSE_CHECK_M + 1e-6; d += step) {
    // Backwards (ds < 0) along the circle: the heading turns toward its right side by curvature * ds.
    x -= hx * step; z -= hz * step;
    const rx = -hz, rz = hx;
    hx -= rx * curvature * step; hz -= rz * curvature * step;
    const l = Math.hypot(hx, hz) || 1; hx /= l; hz /= l;
    if (Math.abs(d - Math.round(d)) > 1e-6) continue;
    const bx = x - hx * back, bz = z - hz * back;
    rows.push([-1, 0, 1].map(side => ({ x: bx - hz * half * side, z: bz + hx * half * side })));
  }
  return rows;
}

/** Whether the car at `pose` may back out along `steer`: every check point has ground that faces up, within
 *  REVERSE_STEP_M of the previous one on its line (starting from the ground under the car). */
export async function reverseClear(pose: VehiclePose, steer: number, g: VehicleGeometry, ray: GroundRay): Promise<boolean> {
  const down = { x: 0, y: -1, z: 0 };
  const under = await ray({ x: pose.x, y: pose.y + RAY_UP, z: pose.z }, down, RAY_DOWN + 2);
  if (!under || under.normal.y < 0.7) return false;
  const rows = reversePoints(pose, steer, g);
  const hits = await Promise.all(rows.map(row => Promise.all(row.map(p => ray({ x: p.x, y: under.point.y + RAY_UP, z: p.z }, down, RAY_UP + RAY_DOWN)))));
  for (let side = 0; side < 3; side++) {
    let last = under.point.y;
    for (const row of hits) {
      const hit = row[side];
      if (!hit || hit.normal.y < 0.7 || Math.abs(hit.point.y - last) > REVERSE_STEP_M) return false;
      last = hit.point.y;
    }
  }
  return true;
}
