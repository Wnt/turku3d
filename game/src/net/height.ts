import type { NetPose } from './client';
import { CHARACTER } from '../physics/constants';

/** Network H is the character's feet or the vehicle chassis centre minus this amount. */
export const VEHICLE_POSE_HEIGHT = 0.6;

/** Height of the visual/physics root above the transmitted H. */
export function remoteRootHeight(mode: NetPose['mode']): number {
  return mode === 'foot' ? 0 : VEHICLE_POSE_HEIGHT;
}

/** The proxy capsule is centred above the feet; vehicle cuboids are chassis-centred. */
export function remoteColliderHeight(mode: NetPose['mode']): number {
  return mode === 'foot' ? CHARACTER.halfHeight + CHARACTER.radius + CHARACTER.skin : 0;
}
