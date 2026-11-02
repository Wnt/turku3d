import type { V3 } from '../core/origin.ts';
import type { Quat } from '../physics/protocol.ts';

export function rotate(p: V3, q: Quat): V3 {
  const tx = 2 * (q.y * p.z - q.z * p.y), ty = 2 * (q.z * p.x - q.x * p.z), tz = 2 * (q.x * p.y - q.y * p.x);
  return { x: p.x + q.w * tx + q.y * tz - q.z * ty, y: p.y + q.w * ty + q.z * tx - q.x * tz, z: p.z + q.w * tz + q.x * ty - q.y * tx };
}

/** Heading of the chassis +X axis; roll must not leak into steering curvature. */
export function headingYaw(q: Quat): number {
  return Math.atan2(2*(q.w*q.y-q.x*q.z),1-2*(q.y*q.y+q.z*q.z));
}
