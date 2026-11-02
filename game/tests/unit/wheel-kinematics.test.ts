import { test, expect } from 'vitest';
import { ackermann, wheelRotation } from '../../src/vehicle/wheelKinematics';
import * as THREE from 'three/webgpu';
import { FLEET } from '../../src/actors/fleet/catalog';
import { buildFleetWheel } from '../../src/actors/fleet/geometry';
import { addFleetWheels, applyFleetWheels } from '../../src/actors/fleet/wheels';

test('front wheel axes meet at a common rear-axle turn centre in either direction', () => {
  for (const steer of [-.5, -.1, .1, .5]) {
    const radius=2.7/Math.tan(steer);
    for(const side of [-.8,.8])expect(2.7/Math.tan(ackermann(steer,2.7,side))-side).toBeCloseTo(radius,8);
    expect(Math.abs(ackermann(steer,2.7,-Math.sign(steer)*.8))).toBeGreaterThan(Math.abs(steer));
  }
  expect(ackermann(0,2.7,.8)).toBe(0);
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
