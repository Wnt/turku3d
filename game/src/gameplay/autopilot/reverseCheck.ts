// The ground behind a stuck car before the autopilot backs it out (follow.ts reverse-out, autopilot.ts): a stuck
// recovery used to reverse blind, and after a nose-in against a lamp post on a quay edge it backed the car over the
// edge into the canal (Amsterdam, Grimburgwal). The reverse arc is checked with downward rays at the rear bumper's
// centre and corners: drivable ground all the way, no step or drop larger than a kerb, nothing that is not there.
import type { RayHit } from '../../physics/protocol';
import { MAT } from '../../shared/col/materials';
import type { VehicleGeometry, VehiclePose } from './follow';

type V3 = { x: number; y: number; z: number };
export type GroundRay = (origin: V3, dir: V3, maxToi: number) => Promise<RayHit | null>;

/** Rear-axle travel checked (m): the follower's reverse-out distance (REVERSE_M 4) and a margin. */
export const REVERSE_CHECK_M = 5;
/** Largest height change (m) between neighbouring checks a reversing car may cross: a kerb, not a quay edge. */
export const REVERSE_STEP_M = 0.4;
/** Rays start this far (m) above the last ground and look this far down. */
const RAY_UP = 1.5, RAY_DOWN = 4;

/** Leading-bumper check points along the arc the car drives with `steer` (the follower's command, + right): `dir` -1
 *  backwards (rear bumper), 1 forwards (front bumper), a row every `every` metres of rear-axle travel up to `length`:
 *  centre and both corners. The rear axle moves along the steering circle. */
export function arcPoints(pose: VehiclePose, steer: number, g: VehicleGeometry, dir: 1 | -1, length: number, every: number) {
  const half = g.back ?? g.rearOffset + 1;
  const bumper = dir < 0 ? half - g.rearOffset : half + g.rearOffset, side = (g.halfWidth ?? 0.9) - 0.05;
  const curvature = Math.tan(g.maxSteer * steer) / g.wheelbase;
  let hx = pose.hx, hz = pose.hz, x = pose.x - hx * g.rearOffset, z = pose.z - hz * g.rearOffset;
  const rows: { x: number; z: number }[][] = [];
  const step = 0.25;
  for (let d = step; d <= length + 1e-6; d += step) {
    // Along the circle by ds = dir * step: the heading turns toward its right side by curvature * ds.
    x += hx * step * dir; z += hz * step * dir;
    const rx = -hz, rz = hx;
    hx += rx * curvature * step * dir; hz += rz * curvature * step * dir;
    const l = Math.hypot(hx, hz) || 1; hx /= l; hz /= l;
    if (Math.abs(d / every - Math.round(d / every)) > 1e-6) continue;
    const bx = x + hx * bumper * dir, bz = z + hz * bumper * dir;
    rows.push([-1, 0, 1].map(k => ({ x: bx - hz * side * k, z: bz + hx * side * k })));
  }
  return rows;
}

/** Rear-bumper check points along the reverse arc, every metre of rear-axle travel up to REVERSE_CHECK_M. */
export function reversePoints(pose: VehiclePose, steer: number, g: VehicleGeometry): { x: number; z: number }[][] {
  return arcPoints(pose, steer, g, -1, REVERSE_CHECK_M, 1);
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

/** Carriageway surfaces a turn in the street keeps the car's middle on: never a pavement, a quay or a lawn. */
const CARRIAGEWAY = new Set<number>([MAT.road, MAT.crossing, MAT.cobblestone, MAT.bridge_deck, MAT.ramp, MAT.curb_dropped]);
/** Spacing (m) of the rows a turn leg checks. */
const LEG_ROW_M = 0.5;

/** Metres (0..length) the car at `pose` may drive along `steer` in `dir` during a turn in the street: up to the first
 *  row whose middle leaves the carriageway, or whose middle or corners lose the ground (no hit, not facing up, a step
 *  or drop larger than a kerb). The car itself must stand on the carriageway. */
export async function arcRoom(pose: VehiclePose, steer: number, g: VehicleGeometry, ray: GroundRay, dir: 1 | -1,
  length: number): Promise<number> {
  const down = { x: 0, y: -1, z: 0 };
  const under = await ray({ x: pose.x, y: pose.y + RAY_UP, z: pose.z }, down, RAY_DOWN + 2);
  if (!under || under.normal.y < 0.7 || !CARRIAGEWAY.has(under.material)) return 0;
  const rows = arcPoints(pose, steer, g, dir, length, LEG_ROW_M);
  const hits = await Promise.all(rows.map(row => Promise.all(row.map(p => ray({ x: p.x, y: under.point.y + RAY_UP, z: p.z }, down, RAY_UP + RAY_DOWN)))));
  const last = [under.point.y, under.point.y, under.point.y];
  for (let r = 0; r < hits.length; r++) {
    for (let k = 0; k < 3; k++) {
      const hit = hits[r][k];
      if (!hit || hit.normal.y < 0.7 || Math.abs(hit.point.y - last[k]) > REVERSE_STEP_M || (k === 1 && !CARRIAGEWAY.has(hit.material))) return r * LEG_ROW_M;
      last[k] = hit.point.y;
    }
  }
  return rows.length * LEG_ROW_M;
}
