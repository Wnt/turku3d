import * as THREE from 'three/webgpu';
import { CAR } from '../../physics/constants';
import type { VehicleState } from '../../physics/protocol';
import { fleetAppearance } from './catalog';
import { buildFleetGeometry, buildFleetWheel } from './geometry';
import { fleetMaterial } from './material';
import { paintActor } from '../parkedPaint';
import { fleetActorLamps } from './lamps';

let bodyGeometry:THREE.BufferGeometry;
const wheelGeometries=new Map<number,THREE.BufferGeometry>();
/** Starter sedan uses the same original fleet surfaces, with the existing physical wheel
 * locations and radii. No suspension, handling or contact values are altered. */
export function createPlayerFleet(){
  const source=fleetAppearance('fleet-sedan-2','player',{width:2.08,height:1.48,length:4.55});
  const model={...source,wheelbase:CAR.wheels[0].x-CAR.wheels[2].x,wheel:CAR.wheelRadius};
  if(!bodyGeometry){bodyGeometry=buildFleetGeometry(model,0,false);bodyGeometry.translate(0,-.75,0);bodyGeometry.rotateY(Math.PI/2);}
  const root=new THREE.Group(),body=new THREE.Mesh(bodyGeometry,fleetMaterial());
  body.name='fleet-player-body';paintActor(body,'charcoal');fleetActorLamps(body);body.castShadow=true;body.receiveShadow=true;root.add(body);
  const wheels=CAR.wheels.map((at,i)=>{
    const side=at.z<0?1:-1;
    let geometry=wheelGeometries.get(side);
    if(!geometry){geometry=buildFleetWheel(model,side);geometry.rotateY(Math.PI/2);wheelGeometries.set(side,geometry);}
    const wheel=new THREE.Mesh(geometry,fleetMaterial());paintActor(wheel,'charcoal');fleetActorLamps(wheel,false);
    wheel.castShadow=true;wheel.receiveShadow=true;
    const steering=new THREE.Group();steering.name=`vehicle-wheel-${i}`;steering.position.set(at.x,at.y-CAR.rest,at.z);steering.add(wheel);root.add(steering);return steering;
  });
  return {root,wheels};
}

export class FleetPlayerVisual {
  readonly root=new THREE.Group();
  private wheels:THREE.Group[]=[];
  loaded=false;
  constructor(scene:THREE.Object3D){this.root.name='car';scene.add(this.root);}
  async load(_url?:string){const rig=createPlayerFleet();this.root.clear();this.root.add(rig.root);this.wheels=rig.wheels;this.loaded=true;}
  update(state:VehicleState){
    this.root.position.set(state.pos.x,state.pos.y,state.pos.z);this.root.quaternion.set(state.rot.x,state.rot.y,state.rot.z,state.rot.w);
    state.wheels.forEach((w,i)=>{const group=this.wheels[i];if(!group)return;group.position.set(w.pos.x,w.pos.y,w.pos.z);group.rotation.set(0,w.steer,0);group.children[0].rotation.z=-w.rotation;});
  }
}
