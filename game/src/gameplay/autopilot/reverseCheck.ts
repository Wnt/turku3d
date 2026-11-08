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
  return arcRows(pose, steer, g, dir, length, every).map(r => r.bumper);
}

/** One row of a leg's arc: the leading bumper's corners and centre, the leading axle's centre (where the wheels
 *  run), and guard points GUARD_M ahead of the bumper's corners and centre. */
interface ArcRow { bumper: { x: number; z: number }[]; axle: { x: number; z: number }; guard: { x: number; z: number }[] }

function arcRows(pose: VehiclePose, steer: number, g: VehicleGeometry, dir: 1 | -1, length: number, every: number): ArcRow[] {
  const half = g.back ?? g.rearOffset + 1;
  const bumper = dir < 0 ? half - g.rearOffset : half + g.rearOffset, side = (g.halfWidth ?? 0.9) - 0.05;
  const axle = dir < 0 ? 0 : g.wheelbase;
  const curvature = Math.tan(g.maxSteer * steer) / g.wheelbase;
  let hx = pose.hx, hz = pose.hz, x = pose.x - hx * g.rearOffset, z = pose.z - hz * g.rearOffset;
  const rows: ArcRow[] = [];
  const step = 0.25;
  for (let d = step; d <= length + 1e-6; d += step) {
    // Along the circle by ds = dir * step: the heading turns toward its right side by curvature * ds.
    x += hx * step * dir; z += hz * step * dir;
    const rx = -hz, rz = hx;
    hx += rx * curvature * step * dir; hz += rz * curvature * step * dir;
    const l = Math.hypot(hx, hz) || 1; hx /= l; hz /= l;
    if (Math.abs(d / every - Math.round(d / every)) > 1e-6) continue;
    const bx = x + hx * bumper * dir, bz = z + hz * bumper * dir, gx = bx + hx * GUARD_M * dir, gz = bz + hz * GUARD_M * dir;
    rows.push({
      bumper: [-1, 0, 1].map(k => ({ x: bx - hz * side * k, z: bz + hx * side * k })),
      axle: { x: x + hx * axle * dir, z: z + hz * axle * dir },
      guard: [-1, 0, 1].map(k => ({ x: gx - hz * side * k, z: gz + hx * side * k })),
    });
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

/** Surfaces a turn in the street keeps the car's wheels on: the carriageway, including the paved (klinker) streets
 *  of Dutch cities, which the collision marks as paving slabs; never a pavement, a quay or a lawn. The bumpers may
 *  swing out over a kerb. */
const CARRIAGEWAY = new Set<number>([MAT.road, MAT.crossing, MAT.cobblestone, MAT.paving_slab, MAT.bridge_deck, MAT.ramp,
  MAT.curb_dropped]);
/** Surfaces a bumper may swing over: the carriageway and the kerb and pavement beside it. */
const BESIDE = new Set<number>([...CARRIAGEWAY, MAT.curb, MAT.sidewalk, MAT.cycleway, MAT.gravel, MAT.grass, MAT.soil,
  MAT.metal_grate]);
/** Spacing (m) of the rows a turn leg checks. */
const LEG_ROW_M = 0.5;
/** Clearance (m) a turn leg keeps beyond its bumper from an edge: a drop deeper than a kerb (a quay into a canal, a
 *  retaining wall), water or no ground at all. A wall or a building there is no edge: the bumper rows stop at it.
 *  Canal railings are thin enough to fall between the rows; the drop behind them is not (Amsterdam Oudezijds
 *  Voorburgwal: a back leg wedged the car on one at 1.5 m/s with a 0.6 m guard's predecessor, none). */
const GUARD_M = 1.0;

/** A turn leg keeps its wheels on the carriageway while that leaves it at least this much travel (m); a shorter leg
 *  may run them up a kerb onto the pavement beside (a 5-6 m street has no room otherwise). */
const ON_ROAD_LEG_M = 2;

/** Metres (0..length) the car at `pose` may drive along `steer` in `dir` during a turn in the street: up to the first
 *  row whose bumper corners, bumper centre or leading axle lose the ground (no hit, not facing up, a step or drop
 *  larger than a kerb, a surface no car goes over) or whose guard points find an edge, and while the leg is at least
 *  ON_ROAD_LEG_M long, up to where the leading axle leaves the carriageway. The car must stand on a surface a car may
 *  go over (a turn may have run it up onto the pavement), never a quay. */
export async function arcRoom(pose: VehiclePose, steer: number, g: VehicleGeometry, ray: GroundRay, dir: 1 | -1,
  length: number): Promise<number> {
  const down = { x: 0, y: -1, z: 0 };
  const under = await ray({ x: pose.x, y: pose.y + RAY_UP, z: pose.z }, down, RAY_DOWN + 2);
  if (!under || under.normal.y < 0.7 || !BESIDE.has(under.material)) return 0;
  const rows = arcRows(pose, steer, g, dir, length, LEG_ROW_M);
  const cast = (p: { x: number; z: number }, from: number) => ray({ x: p.x, y: from + RAY_UP, z: p.z }, down, RAY_UP + RAY_DOWN);
  const hits = await Promise.all(rows.map(row => Promise.all([...row.bumper, row.axle].map(p => cast(p, under.point.y)))));
  const guards = await Promise.all(rows.map(row => Promise.all(row.guard.map(p => cast(p, under.point.y)))));
  const last = [under.point.y, under.point.y, under.point.y, under.point.y];
  let onRoad: number | null = null;
  const room = (r: number) => onRoad !== null && onRoad >= ON_ROAD_LEG_M ? onRoad : r * LEG_ROW_M;
  for (let r = 0; r < hits.length; r++) {
    for (let k = 0; k < 4; k++) {
      const hit = hits[r][k];
      // A kerb's chamfer faces sideways; it is still only a kerb.
      if (!hit || (hit.normal.y < 0.7 && hit.material !== MAT.curb && hit.material !== MAT.curb_dropped)
        || Math.abs(hit.point.y - last[k]) > REVERSE_STEP_M || !BESIDE.has(hit.material)) return room(r);
      if (k === 3 && onRoad === null && !CARRIAGEWAY.has(hit.material)) onRoad = r * LEG_ROW_M;
      last[k] = hit.point.y;
    }
    // An edge near the bumper: ground more than a kerb below the bumper's, water, a quay, or nothing within reach.
    const floor = Math.min(last[0], last[1], last[2]) - REVERSE_STEP_M;
    if (guards[r].some(hit => !hit || hit.point.y < floor || hit.material === MAT.water || hit.material === MAT.quay)) return room(r);
  }
  return room(rows.length);
}

/** Metres (0..length) pursuit may drive along `steer` before the front bumper or the ground just ahead of it meets an
 *  edge: no ground, or a drop deeper than a kerb (follow.ts edge watch: a slow full-lock swing over a quay into a
 *  canal). Surfaces do not count here; the lanes chose them. */
export async function edgeRoom(pose: VehiclePose, steer: number, g: VehicleGeometry, ray: GroundRay, length: number): Promise<number> {
  const down = { x: 0, y: -1, z: 0 };
  const under = await ray({ x: pose.x, y: pose.y + RAY_UP, z: pose.z }, down, RAY_DOWN + 2);
  if (!under) return length;
  const rows = arcRows(pose, steer, g, 1, length, LEG_ROW_M);
  const cast = (p: { x: number; z: number }) => ray({ x: p.x, y: under.point.y + RAY_UP, z: p.z }, down, RAY_UP + RAY_DOWN);
  const hits = await Promise.all(rows.map(row => Promise.all([...row.bumper, ...row.guard].map(cast))));
  const last = [under.point.y, under.point.y, under.point.y];
  for (let r = 0; r < hits.length; r++) {
    for (let k = 0; k < 3; k++) {
      const hit = hits[r][k];
      if (!hit || hit.point.y < last[k] - REVERSE_STEP_M) return r * LEG_ROW_M;
      // The top of a post or a wall is no new ground level: only the ground the car rolls on is followed.
      if (hit.point.y <= last[k] + REVERSE_STEP_M) last[k] = hit.point.y;
    }
    const floor = Math.min(last[0], last[1], last[2]) - REVERSE_STEP_M;
    if (hits[r].slice(3).some(hit => !hit || hit.point.y < floor)) return r * LEG_ROW_M;
  }
  return rows.length * LEG_ROW_M;
}
