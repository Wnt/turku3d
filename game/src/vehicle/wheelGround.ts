import type { V3 } from '../core/origin.ts';
import type { Quat } from '../physics/protocol.ts';
import { rotate } from './math.ts';

export interface WheelGroundPose { pos: V3; rot: Quat; wheelY: number[]; heights: number[] }
export interface WheelFootprint { wheelbase: number; track: number; radius: number }

/** Four probes in physics order: front -Z/+Z, rear -Z/+Z. */
export function wheelGroundProbes(at:V3,yaw:number,footprint:WheelFootprint):V3[] {
  const c=Math.cos(yaw),s=Math.sin(yaw);
  return [1,-1].flatMap(front=>[-1,1].map(side=>{
    const x=front*footprint.wheelbase/2,z=side*footprint.track/2;
    return {x:at.x+x*c+z*s,y:at.y+2,z:at.z-x*s+z*c};
  }));
}

/** Least-squares body plane; each hub keeps its individual contact on a nonplanar kerb.
 * Missing collision samples use the lane/slot ground, never a fabricated zero height. */
export function wheelGroundPose(at:V3,yaw:number,footprint:WheelFootprint,samples:readonly (number|null)[],
  pitchExtra=0,rollExtra=0):WheelGroundPose|null {
  if(samples.length!==4||samples.every(y=>y===null||!Number.isFinite(y)))return null;
  const heights=samples.map(y=>y!==null&&Number.isFinite(y)?y:at.y);
  const mean=heights.reduce((sum,y)=>sum+y,0)/4;
  const pitch=Math.atan2((heights[0]+heights[1]-heights[2]-heights[3])/2,footprint.wheelbase)+pitchExtra;
  const roll=-Math.atan2((heights[1]+heights[3]-heights[0]-heights[2])/2,footprint.track)+rollExtra;
  const sy=Math.sin(yaw/2),cy=Math.cos(yaw/2),sp=Math.sin(pitch/2),cp=Math.cos(pitch/2),sr=Math.sin(roll/2),cr=Math.cos(roll/2);
  const qx=sy*sp,qy=sy*cp,qz=cy*sp,qw=cy*cp;
  const rot={x:qw*sr+qx*cr,y:qy*cr+qz*sr,z:qz*cr-qy*sr,w:qw*cr-qx*sr};
  const inverse={x:-rot.x,y:-rot.y,z:-rot.z,w:rot.w},pos={x:at.x,y:mean+.75,z:at.z};
  const probes=wheelGroundProbes(at,yaw,footprint);
  const wheelY=probes.map((p,i)=>rotate({x:p.x-pos.x,y:heights[i]+footprint.radius-pos.y,z:p.z-pos.z},inverse).y);
  return {pos,rot,wheelY,heights};
}

export function sampleWheelGround(at:V3,yaw:number,footprint:WheelFootprint,
  sample:(x:number,top:number,z:number,depth:number)=>number|null):WheelGroundPose|null {
  return wheelGroundPose(at,yaw,footprint,wheelGroundProbes(at,yaw,footprint).map(p=>sample(p.x,p.y,p.z,4)));
}
