import './fleet-assets.fixture';
import {test,expect} from 'vitest';
import {wheelGroundProbes,wheelGroundPose,sampleWheelGround} from '../../src/vehicle/wheelGround';
import {rotate} from '../../src/vehicle/math';
import {ParkedVehicles} from '../../src/world/parkedVehicles';
import {draftManifest} from '../../src/world/manifest';

const footprint={wheelbase:2.8,track:1.6,radius:.33};
test('four independent world probes respect chassis yaw and axle spacing',()=>{
  const at={x:20,y:7,z:30};
  for(const yaw of [0,Math.PI/2,-.7]){
    const points=wheelGroundProbes(at,yaw,footprint);
    expect(Math.hypot(points[0].x-points[2].x,points[0].z-points[2].z)).toBeCloseTo(2.8);
    expect(Math.hypot(points[0].x-points[1].x,points[0].z-points[1].z)).toBeCloseTo(1.6);
    expect(points.every(p=>p.y===9)).toBe(true);
  }
});

test('flat ground and arbitrary planar slopes put every tyre contact on its sample',()=>{
  const at={x:10,y:3,z:20};
  for(const yaw of [0,Math.PI/2,.7]){
    const probes=wheelGroundProbes(at,yaw,footprint);
    const heights=probes.map(p=>3+.12*(p.x-at.x)+.04*(p.z-at.z));
    const pose=wheelGroundPose(at,yaw,footprint,heights)!;
    expect(pose.pos.y).toBeCloseTo(3.75);
    // Inverse hub transforms agree with the surface even under pitch and roll.
    const inverse={x:-pose.rot.x,y:-pose.rot.y,z:-pose.rot.z,w:pose.rot.w};
    probes.forEach((p,i)=>{
      const local=rotate({x:p.x-pose.pos.x,y:heights[i]+footprint.radius-pose.pos.y,z:p.z-pose.pos.z},inverse);
      expect(local.y).toBeCloseTo(pose.wheelY[i]);
      const world=rotate(local,pose.rot);
      expect(world.y+pose.pos.y-footprint.radius).toBeCloseTo(heights[i]);
    });
  }
});

test('a single wheel on a kerb leaves independent suspension and a pitched/rolled body',()=>{
  const at={x:0,y:10,z:0},pose=wheelGroundPose(at,0,footprint,[10.15,10,10,10])!;
  expect(pose.pos.y).toBeCloseTo(10.7875);
  expect(Math.abs(pose.rot.x)).toBeGreaterThan(.01);expect(Math.abs(pose.rot.z)).toBeGreaterThan(.01);
  expect(Math.max(...pose.wheelY)-Math.min(...pose.wheelY)).toBeGreaterThan(.06);
});

test('missing collision never pulls a car to zero and unknown areas retain the lane pose',()=>{
  const at={x:0,y:18,z:0};
  expect(wheelGroundPose(at,0,footprint,[null,null,null,null])).toBeNull();
  expect(wheelGroundPose(at,0,footprint,[18.1,null,NaN,null])!.pos.y).toBeCloseTo(18.775);
  let calls=0;const pose=sampleWheelGround(at,0,footprint,(_x,top,_z,depth)=>{calls++;expect(top).toBe(20);expect(depth).toBe(4);return 18;});
  expect(calls).toBe(4);expect(pose!.pos.y).toBe(18.75);
});

test('parked cars cache four static-ground rays instead of querying each render frame',async()=>{
  const manifest=draftManifest('http://localhost/');
  const car={object_id:'ground-park',model:'golf7',color:'blue',x:0,y:10,z:0,yaw:Math.PI/2,width:1.75,length:3.9,height:1.5,clear_front_m:5,clear_rear_m:5};
  manifest.vehicles=[car];let queries=0;
  const physics={loaded:{has:()=>true},latest:{vehicles:[]},spawnVehicle(){},removeVehicle(){},
    async raycast(origin:{x:number;y:number;z:number}){
      queries++;return {point:{...origin,y:origin.x>0&&origin.z<0?10.15:10},normal:{x:0,y:1,z:0},toi:2,material:0,tile:'test'};
    }};
  const parked=new ParkedVehicles(manifest,physics as never,{stream:async()=>{},has:()=>true},{addTruck:async()=>{},retainTrucks(){}},0);
  parked.update(0,0,null,1000);parked.restingPose(car);
  await new Promise(resolve=>setTimeout(resolve,0));
  const pose=parked.restingPose(car)!;
  expect(queries).toBe(4);expect(pose.wheels).toHaveLength(4);expect(pose.pos.y).toBeCloseTo(10.7875);
  for(let i=0;i<120;i++)parked.restingPose(car);
  expect(queries).toBe(4);
});


test('unequal axle offsets and tracks probe the authored hubs and fit the root height on a slope',()=>{
  const hubs=[{x:1.43,z:-.77},{x:1.43,z:.77},{x:-1.25,z:-.76},{x:-1.25,z:.76}];
  const shape={wheelbase:2.68,track:1.53,radius:.33,hubs},at={x:0,y:10,z:0};
  const probes=wheelGroundProbes(at,0,shape);
  probes.forEach((p,i)=>{expect(p.x).toBe(hubs[i].x);expect(p.z).toBe(hubs[i].z);});
  const pose=wheelGroundPose(at,0,shape,hubs.map(p=>10+p.x*.1+p.z*.05))!;
  expect(pose.pos.y).toBeCloseTo(10.75,6);
  expect(pose.rot.z).toBeGreaterThan(0);expect(pose.rot.x).toBeLessThan(0);
});
