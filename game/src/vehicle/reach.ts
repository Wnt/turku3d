import type { Quat } from '../physics/protocol';

/** How far (m) a player on foot reaches to get into a vehicle, measured from its footprint rather than its centre:
 *  a bus or lorry is entered at its front door as readily as a car beside its door. */
export const ENTER_REACH = 3;

/** Horizontal distance (m) from `at` to a vehicle's footprint: the rectangle of half extents `half` (x along the
 *  chassis forward +X, z across it) centred at `pos` and turned by `rot`; 0 inside it. A body on its nose or tail has
 *  no horizontal forward and counts as a disc of its smaller half extent. */
export function footprintDistance(at: { x: number; z: number }, pos: { x: number; z: number }, rot: Quat, half: { x: number; z: number }) {
  const fx = 1 - 2 * (rot.y * rot.y + rot.z * rot.z), fz = 2 * (rot.x * rot.z - rot.w * rot.y), l = Math.hypot(fx, fz);
  const dx = at.x - pos.x, dz = at.z - pos.z;
  if (l < 0.5) return Math.max(0, Math.hypot(dx, dz) - Math.min(half.x, half.z));
  const along = (dx * fx + dz * fz) / l, across = (dz * fx - dx * fz) / l;
  return Math.hypot(Math.max(0, Math.abs(along) - half.x), Math.max(0, Math.abs(across) - half.z));
}

/** Chassis rotation of a vehicle turned `yaw` (rad) about +Y, as physics spawns it. */
export const yawRotation = (yaw: number): Quat => ({ x: 0, y: Math.sin(yaw / 2), z: 0, w: Math.cos(yaw / 2) });
