import './fleet-assets.fixture';
import { expect,test } from 'vitest';
import * as THREE from 'three/webgpu';
import { createHash } from 'node:crypto';
import { FLEET, fleetModel } from '../../src/actors/fleet/catalog';
import { buildFleetGeometry } from '../../src/actors/fleet/geometry';
import { ParkedCarVisuals } from '../../src/actors/car';
import { draftManifest } from '../../src/world/manifest';
import { AUTHORED_ESTATE, loadAuthoredFleet } from '../../src/actors/fleet/authored';

test('shipped Blender estate has finite ground-aligned LODs and preserves all lamp roles',async()=>{
  const triangles:number[]=[];
  for(let lod=0;lod<3;lod++) {
    const g=(await loadAuthoredFleet(AUTHORED_ESTATE,lod))!, positions=g.getAttribute('position'), surface=g.getAttribute('fleetSurface');
    triangles.push((g.index?.count??positions.count)/3);
    expect(Array.from(positions.array).every(Number.isFinite)).toBe(true);
    expect(Array.from(g.getAttribute('normal').array).every(Number.isFinite)).toBe(true);
    expect(g.boundingBox!.min.y).toBeCloseTo(0,2);
    expect(g.boundingBox!.max.y).toBeCloseTo(1.545,2);
    expect(g.boundingBox!.max.z-g.boundingBox!.min.z).toBeGreaterThan(4.6);
    expect(new Set(Array.from({length:surface.count},(_,i)=>surface.getW(i)))).toEqual(new Set([0,1,2,3,4]));
    expect(g.groups).toHaveLength(0);
    g.dispose();
  }
  expect(triangles[0]).toBeLessThan(17000);expect(triangles[1]).toBeLessThan(5000);expect(triangles[2]).toBeLessThan(3300);
  expect(triangles[0]).toBeGreaterThan(triangles[1]);expect(triangles[1]).toBeGreaterThan(triangles[2]);
});

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
  visuals.update([],rest);expect(visuals.snapshot().models).toHaveLength(FLEET.length);expect(visuals.snapshot().lods).toEqual([FLEET.length,0,0]);
  expect(new Set(scene.children.map(m=>(m as THREE.Mesh).material)).size).toBe(1);
  camera.position.z=70;camera.lookAt(3,0,3);visuals.setView(camera);visuals.update([],rest);expect(visuals.snapshot().lods).toEqual([0,FLEET.length,0]);
  camera.position.z=200;camera.lookAt(3,0,3);visuals.setView(camera);visuals.update([],rest);expect(visuals.snapshot().lods).toEqual([0,0,FLEET.length]);
  visuals.takeOver('test-0');visuals.update([],rest);expect(visuals.snapshot().instances).toBe(FLEET.length-1);
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
  expect(models.size).toBe(FLEET.length);expect(pickups/2000).toBeGreaterThan(.05);expect(pickups/2000).toBeLessThan(.11);
  expect(old/2000).toBeGreaterThan(.07);expect(old/2000).toBeLessThan(.13);
});


test('registered authored car keeps glazing and lamps without duplicate procedural wheels',async()=>{
  const id='octavia-combi-4',model=fleetModel(id,id);
  const body=(await loadAuthoredFleet(id,0))!,glass=(await loadAuthoredFleet(id,0,'glass'))!;
  expect(glass).not.toBeNull();
  const surface=body.getAttribute('fleetSurface');
  const roles=new Set(Array.from({length:surface.count},(_,i)=>surface.getW(i)));
  expect(roles.has(1)).toBe(true);expect(roles.has(4)).toBe(true);
  const opacity=glass.getAttribute('fleetSurface');
  expect(Array.from({length:opacity.count},(_,i)=>opacity.getW(i)).every(a=>a>0&&a<1)).toBe(true);
  const scene=new THREE.Scene(),visuals=new ParkedCarVisuals(scene),manifest=draftManifest('http://localhost/');
  manifest.vehicles=[{object_id:'authored',model:id,color:'silver',x:0,y:0,z:0,yaw:0,width:model.width,height:model.height,length:model.length,clear_front_m:5,clear_rear_m:5}];
  await visuals.load(manifest);visuals.update([]);
  expect(scene.children.some(m=>m.name.startsWith('wheels/'))).toBe(false);
  const batch=scene.children.find(m=>m.name===`parked/${id}/0`) as THREE.InstancedMesh;
  const glazing=batch.children[0] as THREE.InstancedMesh;
  expect(glazing.instanceMatrix).toBe(batch.instanceMatrix);
  visuals.takeOver('authored');
  expect(scene.children.find(m=>m.name==='driven/authored')!.children).toHaveLength(1);
  visuals.dispose();body.dispose();glass.dispose();
});
