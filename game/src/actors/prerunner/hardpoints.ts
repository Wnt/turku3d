import type { Vector3 } from 'three/webgpu';

export interface AxlePoint { x:number; y:number; z:number }
/** One hardpoint contract for frame geometry and local/remote articulation; chassis +X forward. */
export function shockTop(at:AxlePoint,out:Vector3){
  return out.set(at.x-Math.sign(at.x)*.25,.55,at.z*.64);
}
export function wishbonePivot(at:AxlePoint,upper:boolean,fore:boolean,out:Vector3){
  return out.set(at.x+(fore?.38:-.38),upper?-.15:-.39,Math.sign(at.z)*.43);
}
export function uprightJoint(hub:AxlePoint,side:number,upper:boolean,steer:number,out:Vector3){
  const z=-side*.13;
  return out.set(hub.x+Math.sin(steer)*z,hub.y+(upper?.20:-.14),hub.z+Math.cos(steer)*z);
}
