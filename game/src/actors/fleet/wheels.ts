import * as THREE from 'three/webgpu';
import type { Quat } from '../../physics/protocol';
import { fleetHash, FLEET, type FleetModel } from './catalog';
import { buildFleetWheel } from './geometry';
import { fleetMaterial } from './material';
import { fleetLamps } from './lamps';
import { paintInstances } from '../parkedPaint';
import { fleetOrigin } from './pose';
import { fleetWheelCenter } from './profile';

interface Batch {mesh:THREE.InstancedMesh;count:number}
interface Motion {x:number;z:number;yaw:number;angle:number;steer:number;seen:number}
const AXIS=new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0,1,0),Math.PI/2);
const UP=new THREE.Vector3(0,1,0),AXLE=new THREE.Vector3(1,0,0);
/** Fourteen shared near-wheel draws cover every fleet model: seven rim faces, two sides.
 * Radius scales only the circular plane; tyre width remains authored in metres. Far wheels
 * stay merged into the vehicle LOD and create no additional draw. */
export class FleetWheels {
  readonly meshes:THREE.InstancedMesh[]=[];
  private batches=new Map<string,Batch>();
  private motion=new Map<string,Motion>();
  private epoch=0;
  private base=new THREE.Quaternion();private origin=new THREE.Vector3();
  private q=new THREE.Quaternion();private turn=new THREE.Quaternion();private spin=new THREE.Quaternion();
  private position=new THREE.Vector3();private scale=new THREE.Vector3();private matrix=new THREE.Matrix4();
  constructor(readonly capacity:number){
    for(let face=0;face<7;face++)for(const side of [-1,1] as const){
      const model={...FLEET[face],wheel:1},geometry=buildFleetWheel(model,side);
      const mesh=new THREE.InstancedMesh(geometry,fleetMaterial(),Math.max(16,capacity*2));
      mesh.name=`fleet-moving-wheels/${face}/${side}`;mesh.count=0;mesh.castShadow=true;mesh.receiveShadow=true;
      paintInstances(mesh,[]);fleetLamps(mesh,[]);this.meshes.push(mesh);this.batches.set(`${face}/${side}`,{mesh,count:0});
    }
  }
  begin(){this.epoch++;for(const b of this.batches.values())b.count=0;}
  add(id:string,model:FleetModel,pos:{x:number;y:number;z:number},rotation:Quat){
    const yaw=Math.atan2(2*(rotation.w*rotation.y+rotation.x*rotation.z),1-2*(rotation.y*rotation.y+rotation.z*rotation.z));
    let motion=this.motion.get(id);
    if(!motion){motion={x:pos.x,z:pos.z,yaw,angle:fleetHash(id)/4294967296*Math.PI*2,steer:0,seen:this.epoch};this.motion.set(id,motion);}
    const distance=Math.hypot(pos.x-motion.x,pos.z-motion.z),dyaw=Math.atan2(Math.sin(yaw-motion.yaw),Math.cos(yaw-motion.yaw));
    if(distance>.001&&distance<10){motion.angle=(motion.angle+distance/model.wheel)%(Math.PI*2);motion.steer=THREE.MathUtils.clamp(Math.atan(model.wheelbase*dyaw/distance),-.5,.5);}
    else if(distance>=10)motion.steer=0;
    motion.x=pos.x;motion.z=pos.z;motion.yaw=yaw;motion.seen=this.epoch;
    const origin=fleetOrigin(pos,rotation);this.origin.set(origin.x,origin.y,origin.z);
    this.base.set(rotation.x,rotation.y,rotation.z,rotation.w).multiply(AXIS);
    for(const side of [-1,1])for(const front of [true,false]){
      const b=this.batches.get(`${model.face}/${side}`)!;if(b.count>=b.mesh.instanceMatrix.count)continue;
      this.position.set(...fleetWheelCenter(model,front,side)).applyQuaternion(this.base).add(this.origin);
      this.q.copy(this.base).multiply(this.turn.setFromAxisAngle(UP,front?motion.steer:0)).multiply(this.spin.setFromAxisAngle(AXLE,motion.angle));
      this.scale.set(1,model.wheel,model.wheel);this.matrix.compose(this.position,this.q,this.scale);b.mesh.setMatrixAt(b.count++,this.matrix);
    }
  }
  end(){
    for(const b of this.batches.values()){
      b.mesh.count=b.count;b.mesh.visible=b.count>0;if(!b.count)continue;
      b.mesh.instanceMatrix.addUpdateRange(0,b.count*16);b.mesh.instanceMatrix.needsUpdate=true;b.mesh.computeBoundingSphere();
    }
    for(const [id,m] of this.motion)if(m.seen!==this.epoch)this.motion.delete(id);
  }
  dispose(){for(const mesh of this.meshes){mesh.removeFromParent();mesh.geometry.dispose();mesh.dispose();}this.batches.clear();this.motion.clear();}
}
