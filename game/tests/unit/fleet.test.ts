import { lampState } from '../../src/actors/fleet/lamps';
import { expect,test } from 'vitest';
import * as THREE from 'three/webgpu';
import { createHash } from 'node:crypto';
import { FLEET, fleetModel, fleetAppearance, FLEET_SIZE } from '../../src/actors/fleet/catalog';
import { buildFleetGeometry } from '../../src/actors/fleet/geometry';
import { ParkedCarVisuals } from '../../src/actors/car';
import { draftManifest } from '../../src/world/manifest';

test('56 distinct finite vehicle geometries have three decreasing LODs, ground contact and realistic dimensions',()=>{
  const hashes=new Set<string>();
  expect(FLEET.length).toBeGreaterThanOrEqual(50);
  for(const model of FLEET) {
    const counts=[];
    for(const lod of [0,1,2] as const) {
      const g=buildFleetGeometry(model,lod),p=g.getAttribute('position');counts.push((g.index?.count??p.count)/3);
      expect(Array.from(p.array).every(Number.isFinite)).toBe(true);
      expect(g.getAttribute('fleetSurface').count).toBe(p.count);
      expect(g.boundingBox!.min.y).toBeGreaterThanOrEqual(-.01);
      expect(g.boundingBox!.min.y).toBeLessThan(.06);
      expect(g.boundingBox!.max.y).toBeGreaterThan(model.height-.1);
      if(!lod)hashes.add(createHash('sha256').update(Buffer.from(p.array.buffer)).digest('hex'));
      g.dispose();
    }
    expect(counts[0]).toBeGreaterThan(counts[1]);expect(counts[1]).toBeGreaterThan(counts[2]);expect(counts[0]).toBeLessThan(6000);
  }
  expect(hashes.size).toBe(FLEET.length);
});

test('parked and moving fleet use all models, share one material and switch distance LODs',async()=>{
  const scene=new THREE.Scene(),visuals=new ParkedCarVisuals(scene),manifest=draftManifest('http://localhost/');
  manifest.vehicles=FLEET.map((m,i)=>({object_id:`test-${i}`,model:m.id,color:'silver',x:i%7,y:0,z:Math.floor(i/7),yaw:0,width:m.width,height:m.height,length:m.length,clear_front_m:5,clear_rear_m:5}));
  await visuals.load(manifest);const camera=new THREE.PerspectiveCamera(120,1,.1,500);camera.position.set(0,10,20);camera.lookAt(3,0,3);visuals.setView(camera);
  const rest={restingPose:(c:NonNullable<typeof manifest.vehicles>[number])=>({pos:{x:c.x,y:.75,z:c.z},rot:{x:0,y:0,z:0,w:1}})};
  visuals.update([],rest);expect(visuals.snapshot().models).toHaveLength(56);expect(visuals.snapshot().lods).toEqual([56,0,0]);
  expect(new Set(scene.children.map(m=>(m as THREE.Mesh).material)).size).toBe(1);
  camera.position.z=70;camera.lookAt(3,0,3);visuals.setView(camera);visuals.update([],rest);expect(visuals.snapshot().lods).toEqual([0,56,0]);
  camera.position.z=200;camera.lookAt(3,0,3);visuals.setView(camera);visuals.update([],rest);expect(visuals.snapshot().lods).toEqual([0,0,56]);
  visuals.takeOver('test-0');visuals.update([],rest);expect(visuals.snapshot().instances).toBe(55);
});

test('model assignment is stable and legacy families remain compatible',()=>{
  for(const family of ['sedan','suv','van','hatchback-sports','sedan-sports'])for(let i=0;i<100;i++) {
    const model=fleetModel(family,String(i));expect(model.family).toBe(family==='sedan-sports'?'sedan':family);expect(fleetModel(family,String(i))).toBe(model);
  }
});


test('ordinary traffic reaches all 56 models without overpopulating pickups or old cars',()=>{
  const models=new Set<string>();let pickups=0,old=0;
  for(const family of ['sedan','suv','van','hatchback-sports'])for(let i=0;i<2000;i++){
    const m=fleetModel(family,`tr:tile:lane:${i}`);models.add(m.id);pickups+=Number(m.body==='pickup');old+=Number(m.body==='classic');
  }
  expect(models.size).toBe(56);expect(pickups/2000).toBeGreaterThan(.05);expect(pickups/2000).toBeLessThan(.11);
  expect(old/2000).toBeGreaterThan(.07);expect(old/2000).toBeLessThan(.13);
});


test('ambient, claimed and remote appearances have identical dimensions and circular tyres',async()=>{
  for(const family of ['sedan','suv','van','hatchback-sports']) {
    const size=FLEET_SIZE[family],model=fleetAppearance(family,'shared-slot',size);
    expect(fleetAppearance(model.id,'different-client')).toBe(model);
    const geometry=buildFleetGeometry(model,0),p=geometry.getAttribute('position'),surface=geometry.getAttribute('fleetSurface');
    const width=geometry.boundingBox!.max.x-geometry.boundingBox!.min.x;expect(width).toBeLessThanOrEqual(size.width+.015);
    const axle=model.wheelbase/2,r=model.wheel;let outer=0;
    for(let i=0;i<p.count;i++)if(surface.getY(i)>.9&&Math.abs(p.getZ(i)-axle)<r+.001&&p.getY(i)<2*r+.001){
      const radius=Math.hypot(p.getY(i)-r,p.getZ(i)-axle);expect(radius).toBeLessThan(r+.001);outer=Math.max(outer,radius);
    }
    expect(outer).toBeCloseTo(r,5);geometry.dispose();
    const scene=new THREE.Scene(),visuals=new ParkedCarVisuals(scene),manifest=draftManifest('http://localhost/');
    manifest.vehicles=[{object_id:'shared-slot',model:model.id,color:'silver',x:0,y:0,z:0,yaw:0,...size,clear_front_m:5,clear_rear_m:5}];
    await visuals.load(manifest);visuals.takeOver('shared-slot');
    const actor=scene.getObjectByName('driven/shared-slot') as THREE.Mesh,remote=visuals.remoteModel(`${model.id}/silver`)!;
    expect(actor.geometry).toBe(remote.geometry);expect(actor.scale.toArray()).toEqual([1,1,1]);expect(remote.scale.toArray()).toEqual([1,1,1]);
    expect(visuals.remoteModel('fleet-sedan-0-d0-0-0/silver')).toBeNull();
    visuals.dispose();
  }
  expect(()=>fleetAppearance('sedan','slot',{width:NaN,height:1.5,length:4.5})).toThrow('Invalid fleet dimensions');
});


test('traffic lamps distinguish powered, turn and brake state without re-gating worker blink state',()=>{
  expect(lampState(false)).toEqual([0,0,0,0]);expect(lampState(true,-1,true)).toEqual([1,1,0,1]);
  expect(lampState(true,1,false)).toEqual([1,0,1,0]);
});

test('stationary transforms remain untouched and a moving tail uploads only its changed range',async()=>{
  const scene=new THREE.Scene(),visuals=new ParkedCarVisuals(scene),manifest=draftManifest('http://localhost/');
  const model=fleetAppearance('fleet-estate-0','shared');
  manifest.vehicles=[{object_id:'parked',model:model.id,color:'silver',x:0,y:0,z:0,yaw:0,...FLEET_SIZE.sedan,clear_front_m:5,clear_rear_m:5}];
  visuals.reserveTraffic(1);await visuals.load(manifest);
  const rest={restingPose:()=>({pos:{x:0,y:.75,z:0},rot:{x:0,y:0,z:0,w:1}})};
  const moving={seed:'traffic-source',model:model.id,color:'black',x:2,y:.75,z:0,q:{x:0,y:0,z:0,w:1}};
  visuals.setTraffic([moving]);visuals.update([],rest);
  const batch=scene.getObjectByName(`parked/${model.id}/0`) as THREE.InstancedMesh;
  const version=batch.instanceMatrix.version;batch.instanceMatrix.clearUpdateRanges();
  visuals.update([],rest);expect(batch.instanceMatrix.version).toBe(version);expect(batch.instanceMatrix.updateRanges).toEqual([]);
  moving.x=3;visuals.update([],rest);expect(batch.instanceMatrix.version).toBe(version+1);
  expect(batch.instanceMatrix.updateRanges).toEqual([{start:16,count:16}]);visuals.dispose();
});
