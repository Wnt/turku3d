import * as T from 'three/webgpu';
import * as R from '@dimforge/rapier3d';
import { TRUCK, DT } from '../physics/constants';
import { Vehicle } from '../vehicle/vehicle';
import { CurbQueries } from '../vehicle/curbQueries';
import { GROUP, MAT } from '../shared/col/materials';
import type { VehicleInput } from '../physics/protocol';

/** Repeatable physical drive fixture: real Rapier vehicle, a kicker, granite kerbs and crawl rocks.
 * No pose animation or position resets during a run. This supplements the in-city driving clip.
 */
export function prerunnerCourse(scene:T.Scene){
  const world=new R.World({x:0,y:-9.81,z:0});world.timestep=DT;
  const groups=(GROUP.STATIC_SOLID<<16)|GROUP.VEHICLE;
  const surfaces=new CurbQueries(R,world);
  const root=new T.Group();root.name='prerunner-physical-course';scene.add(root);
  const concrete=new T.MeshStandardMaterial({color:0x77786f,roughness:.92});
  world.createCollider(R.ColliderDesc.cuboid(250,.5,50).setTranslation(0,-.5,0).setCollisionGroups(groups));
  const angle=Math.atan2(1.4,5.6),q={x:0,y:0,z:Math.sin(angle/2),w:Math.cos(angle/2)};
  const ramp=new T.Mesh(new T.BoxGeometry(5.78,.2,5),concrete);ramp.position.set(-2.8,.7-.1*Math.cos(angle),0);ramp.quaternion.set(q.x,q.y,q.z,q.w);ramp.castShadow=true;ramp.receiveShadow=true;root.add(ramp);
  world.createCollider(R.ColliderDesc.cuboid(2.89,.1,2.5).setTranslation(ramp.position.x,ramp.position.y,0).setRotation(q).setCollisionGroups(groups));
  for(const [x,h] of [[28,.16],[40,.24],[53,.30]]){
    const mesh=new T.Mesh(new T.BoxGeometry(2,h,4.4),concrete);mesh.position.set(x,h/2,0);mesh.castShadow=true;mesh.receiveShadow=true;root.add(mesh);
    const collider=world.createCollider(R.ColliderDesc.cuboid(1,h/2,2.2).setTranslation(x,h/2,0).setCollisionGroups(groups));
    surfaces.add(collider,collider.shape,()=>({material:MAT.curb,top:h}));
  }
  for(let i=0;i<8;i++){
    const radius=.14+i%3*.055,x=65+i*1.2,z=(i%2?1:-1)*1.0;
    const rock=new T.Mesh(new T.IcosahedronGeometry(radius,1),concrete);rock.position.set(x,radius*.4,z);rock.castShadow=true;root.add(rock);
    world.createCollider(R.ColliderDesc.ball(radius).setTranslation(x,radius*.4,z).setCollisionGroups(groups));
  }
  const vehicle=new Vehicle(R,world,{x:-32,y:TRUCK.wheelRadius+TRUCK.rest-TRUCK.wheels[0].y+.2,z:0},0,surfaces,{kind:'truck'});
  const rest={throttle:0,brake:0,steer:0,handbrake:false};
  for(let i=0;i<150;i++){vehicle.step(DT,rest);world.step();vehicle.afterStep(DT);}
  let accumulator=0,time=0,input:VehicleInput|undefined;
  const samples:{time:number;x:number;y:number;speed:number;contact:number;roll:number}[]=[];
  let flying=false,airStart=0,peakAir=0,landings=0,maxSpeed=0,maxTilt=0;
  return {
    step(dt:number){
      accumulator+=Math.min(.08,dt);
      while(accumulator>=DT){
        const at=vehicle.body.translation();
        // Brake after the fast landing, then walk the taller kerbs and rocks with low gearing.
        const speed=vehicle.state().forwardSpeed;
        const target=at.x<20?18:at.x<63?5:2.4;
        const control=input??{throttle:speed<target?.55:0,brake:speed>target+1?.4:0,steer:0,handbrake:false};
        vehicle.step(DT,control);world.step();vehicle.afterStep(DT);accumulator-=DT;time+=DT;
        const s=vehicle.state(),contact=s.wheels.filter(w=>w.contact).length;
        const tilt=Math.acos(Math.max(-1,Math.min(1,1-2*(s.rot.x*s.rot.x+s.rot.z*s.rot.z))))*180/Math.PI;
        maxSpeed=Math.max(maxSpeed,s.speed);maxTilt=Math.max(maxTilt,tilt);
        if(!contact&&!flying&&s.pos.x>-3){flying=true;airStart=time;}
        if(contact&&flying){flying=false;peakAir=Math.max(peakAir,time-airStart);landings++;}
        if(samples.length===0||time-samples.at(-1)!.time>.08)samples.push({time,x:s.pos.x,y:s.pos.y,speed:s.speed,contact,roll:tilt});
      }
      return vehicle.state();
    },
    input:(value:VehicleInput|undefined)=>{input=value;},
    snapshot:()=>({time,samples,maxSpeed,maxTilt,longestAir:peakAir,landings,bodyStrike:vehicle.state().impact}),
    dispose(){world.free();root.removeFromParent();root.traverse(o=>{if((o as T.Mesh).isMesh)(o as T.Mesh).geometry.dispose();});concrete.dispose();},
  };
}
