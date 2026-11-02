import { test, expect } from 'vitest';
import { ackermann, wheelRotation } from '../../src/vehicle/wheelKinematics';
import * as THREE from 'three/webgpu';
import { FLEET } from '../../src/actors/fleet/catalog';
import { buildFleetWheel } from '../../src/actors/fleet/geometry';
import { addFleetWheels, applyFleetWheels } from '../../src/actors/fleet/wheels';
import { ParkedCarVisuals } from '../../src/actors/fleet/visuals';
import { draftManifest } from '../../src/world/manifest';

test('front wheel axes meet at a common rear-axle turn centre in either direction', () => {
  for (const steer of [-.5, -.1, .1, .5]) {
    const radius=2.7/Math.tan(steer);
    for(const side of [-.8,.8])expect(2.7/Math.tan(ackermann(steer,2.7,side))-side).toBeCloseTo(radius,8);
    expect(Math.abs(ackermann(steer,2.7,-Math.sign(steer)*.8))).toBeGreaterThan(Math.abs(steer));
  }
  expect(ackermann(0,2.7,.8)).toBe(0);
});

test('near fleet wheels batch four hubs per car and distant cars stay a single draw', async () => {
  const scene=new THREE.Scene(),visuals=new ParkedCarVisuals(scene),manifest=draftManifest('http://localhost/');
  const model=FLEET[0];
  manifest.vehicles=Array.from({length:120},(_,i)=>({object_id:`wheel-${i}`,model:model.id,color:'silver',x:0,y:0,z:0,yaw:0,width:model.width,height:model.height,length:model.length,clear_front_m:5,clear_rear_m:5}));
  await visuals.load(manifest);
  const camera=new THREE.PerspectiveCamera(120,1,.1,500);camera.position.set(0,2,20);camera.lookAt(0,0,0);visuals.setView(camera);
  const rest={restingPose:()=>({pos:{x:0,y:.75,z:0},rot:{x:0,y:0,z:0,w:1}})};
  visuals.update([],rest);
  expect(visuals.snapshot().draws).toBe(3);expect(visuals.snapshot().instances).toBe(120);
  const wheels=scene.children.filter(c=>c.name.startsWith('wheels/')&&(c as THREE.InstancedMesh).count>0) as THREE.InstancedMesh[];
  expect(wheels).toHaveLength(2);expect(wheels.map(w=>w.count)).toEqual([240,240]);
  const versions=wheels.map(w=>w.instanceMatrix.version);visuals.update([],rest);
  expect(wheels.map(w=>w.instanceMatrix.version)).toEqual(versions);
  camera.position.z=200;camera.lookAt(0,0,0);visuals.setView(camera);visuals.update([],rest);
  expect(visuals.snapshot().draws).toBe(1);expect(wheels.every(w=>!w.visible)).toBe(true);
  visuals.dispose();expect(scene.children).toHaveLength(0);
});

test('traffic wheel instance transforms advance by travelled distance divided by tyre radius',async()=>{
  const scene=new THREE.Scene(),visuals=new ParkedCarVisuals(scene),manifest=draftManifest('http://localhost/'),model=FLEET[0];
  manifest.vehicles=[{object_id:'preload',model:model.id,color:'silver',x:0,y:0,z:0,yaw:0,width:model.width,height:model.height,length:model.length,clear_front_m:5,clear_rear_m:5}];
  await visuals.load(manifest);
  const car={seed:'moving',model:model.id,color:'blue',x:0,y:.75,z:0,q:{x:0,y:0,z:0,w:1}};
  visuals.setTraffic([car]);visuals.update([]);
  const wheel=scene.getObjectByName(`wheels/${model.id}/0/1`) as THREE.InstancedMesh;
  const first=new THREE.Matrix4(),second=new THREE.Matrix4();wheel.getMatrixAt(0,first);
  car.x=model.wheel*Math.PI/2;visuals.update([]);wheel.getMatrixAt(0,second);
  const a=new THREE.Quaternion().setFromRotationMatrix(first),b=new THREE.Quaternion().setFromRotationMatrix(second);
  expect(a.angleTo(b)).toBeCloseTo(Math.PI/2,5);
  visuals.dispose();
});

test('one circumference is one signed wheel revolution for every fleet tyre', () => {
  for(const model of FLEET)for(const sign of [-1,1])expect(wheelRotation(sign*2*Math.PI*model.wheel,model.wheel)).toBeCloseTo(sign*2*Math.PI);
});

test('hub pivots preserve wheel radius and independent steering, spin and suspension', () => {
  const model=FLEET[0],actor=new THREE.Group();
  addFleetWheels(actor,model,side=>buildFleetWheel(model,0,side),new THREE.MeshBasicMaterial());
  const geometry=(actor.children[0].children[0] as THREE.Mesh).geometry;
  geometry.computeBoundingBox();expect(geometry.boundingBox!.min.y).toBeCloseTo(-model.wheel,5);
  const wheels=Array.from({length:4},(_,i)=>({pos:{x:0,y:-.45+i*.03,z:0},steer:i<2?.3:0,rotation:1+i,contact:true,suspension:.3}));
  applyFleetWheels(actor,wheels);
  actor.children.forEach((hub,i)=>{
    expect(hub.position.y).toBeCloseTo(.3+i*.03);expect(hub.rotation.y).toBe(wheels[i].steer);
    expect(hub.children[0].rotation.x).toBe(1+i);
  });
});
