// Kerb-side parking poses on the drivable street network: where the pizzeria's kerb spot comes from, and where
// "Bring my vehicle here" (pause menu) parks the player's vehicle beside them. Pure; the caller ground-checks each
// candidate (gameplay/vehicleSave.ts vehicleGround) before it parks anything there.
import { CAR, type RoadGraph } from './roadGraph';
import type { VehicleSpot } from './vehicleSave';

/** Gap (m) between a parked vehicle's side and the kerb line. */
const KERB_GAP_M = 0.4;
/** Samples along each street segment, this many metres apart. */
const SAMPLE_STEP_M = 3;
/** No parking this close (arc metres) to a junction node (three or more streets meet). */
const JUNCTION_CLEAR_M = 10;

interface Point { x: number; y?: number; z: number }

/**
 * Parking poses within `radius` of `near`, nearest first: points every SAMPLE_STEP_M along each drivable segment,
 * moved from the centreline to both kerbs (half the carriageway minus the vehicle's `halfWidth` and KERB_GAP_M), each
 * facing the travel direction of its side (right-hand traffic: the right kerb faces a→b), never inside a junction.
 * `y` is the street surface where the graph has heights, else `near.y` (0 when unknown).
 */
export function kerbSpots(graph: RoadGraph, near: Point, halfWidth: number, radius: number): VehicleSpot[] {
  const spots: (VehicleSpot & { d: number })[] = [];
  for (const edge of graph.edges) {
    if (!(edge.flags & CAR)) continue;
    const length = edge.cumulative[edge.cumulative.length - 1];
    const junction = (node: number) => (graph.adjacency[node]?.length ?? 0) >= 3;
    const clearA = junction(edge.a) ? JUNCTION_CLEAR_M : 0, clearB = junction(edge.b) ? length - JUNCTION_CLEAR_M : length;
    const offset = Math.max(0, edge.lanes.width / 2 - halfWidth - KERB_GAP_M);
    for (let i = 0; i + 1 < edge.cumulative.length; i++) {
      const [x0, z0, x1, z1] = edge.points.subarray(i * 2, i * 2 + 4);
      const dx = x1 - x0, dz = z1 - z0, len = Math.hypot(dx, dz);
      if (!len) continue;
      // Quick reject: the whole segment is farther than radius + its length from `near`.
      if (Math.hypot((x0 + x1) / 2 - near.x, (z0 + z1) / 2 - near.z) > radius + len / 2 + offset) continue;
      // The right-hand normal (gameplay/roadGraph.ts laneOffset) and the a→b heading.
      const nx = -dz / len, nz = dx / len, yaw = Math.atan2(-dz, dx);
      for (let t = 0; t <= len; t += SAMPLE_STEP_M) {
        const s = edge.cumulative[i] + t;
        if (s < clearA || s > clearB) continue;
        const f = t / len;
        const y = edge.heights ? edge.heights[i] + f * (edge.heights[i + 1] - edge.heights[i]) : near.y ?? 0;
        for (const side of [1, -1]) {
          const x = x0 + f * dx + side * offset * nx, z = z0 + f * dz + side * offset * nz;
          const d = Math.hypot(x - near.x, z - near.z);
          if (d <= radius) spots.push({ x, y, z, yaw: side > 0 ? yaw : yaw + Math.PI, d });
        }
      }
    }
  }
  return spots.sort((a, b) => a.d - b.d).map(({ d: _d, ...spot }) => spot);
}

export interface View {
  /** Camera position and horizontal view direction (need not be normalised). */
  camera: Point;
  forward: { x: number; z: number };
  /** Half the horizontal field of view (radians). */
  halfFov: number;
}

/** Spots no closer than this to the player, so the vehicle never lands on them. */
export const BRING_MIN_M = 5;

/** Whether `spot` lies outside the camera's horizontal view cone (with a 10° margin). */
export function offScreen(spot: Point, view: View): boolean {
  const vx = spot.x - view.camera.x, vz = spot.z - view.camera.z;
  const fl = Math.hypot(view.forward.x, view.forward.z) || 1, vl = Math.hypot(vx, vz) || 1;
  const cos = (vx * view.forward.x + vz * view.forward.z) / (vl * fl);
  return cos < Math.cos(Math.min(Math.PI, view.halfFov + Math.PI / 18));
}

/** "Bring my vehicle here" candidates: kerb spots at least BRING_MIN_M from the player, the ones off screen first,
 *  each group nearest first. */
export function bringSpots(spots: readonly VehicleSpot[], player: Point, view: View): VehicleSpot[] {
  const usable = spots.filter(s => Math.hypot(s.x - player.x, s.z - player.z) >= BRING_MIN_M);
  return [...usable.filter(s => offScreen(s, view)), ...usable.filter(s => !offScreen(s, view))];
}
