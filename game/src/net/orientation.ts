import { Quaternion, Vector3 } from 'three';
import type { NetPose } from './client';
import type { Quat } from '../physics/protocol';

/** Legacy network yaw faces -Z; vehicle physics and starter visuals face +X. */
export function chassisRotation(pose: Pick<NetPose, 'yaw' | 'rot'>): Quaternion {
  return pose.rot ? new Quaternion(pose.rot.x, pose.rot.y, pose.rot.z, pose.rot.w).normalize()
    : new Quaternion().setFromAxisAngle(new Vector3(0, 1, 0), pose.yaw + Math.PI / 2);
}

/** Parked meshes are authored +Z forward; starter, scooter and truck roots use the physics frame. */
export function vehicleRotation(pose: Pick<NetPose, 'yaw' | 'rot'>, parked: boolean): Quaternion {
  const rotation = chassisRotation(pose);
  return parked ? rotation.multiply(new Quaternion().setFromAxisAngle(new Vector3(0, 1, 0), Math.PI / 2)) : rotation;
}

export function interpolateRotation(a: Pick<NetPose, 'yaw' | 'rot'>, b: Pick<NetPose, 'yaw' | 'rot'>, f: number): Quat {
  const q = chassisRotation(a).slerp(chassisRotation(b), f);
  return { x: q.x, y: q.y, z: q.z, w: q.w };
}
