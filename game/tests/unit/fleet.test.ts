import './fleet-assets.fixture';
import { expect,test } from 'vitest';
import * as THREE from 'three/webgpu';
import { createHash } from 'node:crypto';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { MeshoptDecoder } from 'three/addons/libs/meshopt_decoder.module.js';
import { FLEET, SELECTABLE_FAMILIES, fleetModel } from '../../src/actors/fleet/catalog';
import { AUTHORED_MODELS } from '../../src/actors/fleet/registry';
import { buildFleetGeometry } from '../../src/actors/fleet/geometry';
import { ParkedCarVisuals } from '../../src/actors/car';
import {fleetWheelLayout} from '../../src/actors/fleet/wheelLayouts';
import { draftManifest } from '../../src/world/manifest';
import { AUTHORED_ESTATE, flattenFleetAsset, loadAuthoredFleet, loadAuthoredFleetParts } from '../../src/actors/fleet/authored';

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


test('ordinary traffic reaches every selectable model without overpopulating pickups or old cars',()=>{
  const models=new Set<string>();let pickups=0,old=0;
  for(const family of ['sedan','suv','van','hatchback-sports'])for(let i=0;i<2000;i++){
    const m=fleetModel(family,`tr:tile:lane:${i}`);models.add(m.id);pickups+=Number(m.body==='pickup');old+=Number(m.body==='classic');
  }
  expect(models.size).toBe(FLEET.filter(m=>SELECTABLE_FAMILIES.has(m.family)).length);expect(pickups/2000).toBeGreaterThan(.05);expect(pickups/2000).toBeLessThan(.11);
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
  expect(scene.children.some(m=>m.name.startsWith('wheels/authored/'))).toBe(true);
  const batch=scene.children.find(m=>m.name===`parked/${id}/0`) as THREE.InstancedMesh;
  const glazing=batch.children[0] as THREE.InstancedMesh;
  expect(glazing.instanceMatrix).toBe(batch.instanceMatrix);
  visuals.takeOver('authored');
  const actor=scene.children.find(m=>m.name==='driven/authored')!;
  expect(actor.children).toHaveLength(5);
  const layout=(await loadAuthoredFleetParts(id,0))!;
  const state={id:'authored',pos:{x:0,y:.75,z:0},rot:{x:0,y:0,z:0,w:1},linvel:{x:0,y:0,z:0},speed:0,forwardSpeed:0,curbStrike:null,
    wheels:layout.wheels.map((w,i)=>({pos:{x:w.pivot.z,y:w.pivot.y-.75,z:-w.pivot.x},steer:i<2?.3:0,rotation:1.2,contact:true,suspension:.2}))};
  visuals.update([state]);
  for(let i=0;i<4;i++){
    const hub=actor.getObjectByName(`fleet-wheel-${i}`)!;
    expect(hub.rotation.y).toBe(i<2?.3:0);
    expect(hub.children[0].rotation.x).toBe(1.2);
    expect(hub.position.y).toBeCloseTo(layout.wheels[i].pivot.y,5);
  }
  const remote=visuals.remoteModel(id+'/blue')!;
  expect(remote.children.filter(c=>c.name.startsWith('fleet-wheel-'))).toHaveLength(4);
  layout.body.dispose();layout.wheels.forEach(w=>w.geometry.dispose());
  visuals.dispose();body.dispose();glass.dispose();
});


test('authored body and four centred hubs conserve every triangle at every LOD',async()=>{
  const triangles=(g:THREE.BufferGeometry)=>(g.index?.count??g.getAttribute('position').count)/3;
  for(const id of [AUTHORED_ESTATE,'octavia-combi-4'])for(let lod=0;lod<3;lod++){
    const original=(await loadAuthoredFleet(id,lod))!,parts=(await loadAuthoredFleetParts(id,lod))!;
    expect(parts.wheels).toHaveLength(4);
    expect(triangles(parts.body)+parts.wheels.reduce((n,w)=>n+triangles(w.geometry),0)).toBe(triangles(original));
    for(const wheel of parts.wheels){
      expect(wheel.geometry.getAttribute('fleetSurface')).toBeDefined();
      // LOD decimation is asymmetric around the fixed hub; it must never move that pivot.
      const pivot=fleetWheelLayout(id)!.pivots[wheel.name as 'wheel_FL'];
      expect(wheel.pivot).toEqual({x:expect.closeTo(pivot[0],5),y:expect.closeTo(pivot[1],5),z:expect.closeTo(pivot[2],5)});
      expect(wheel.radius).toBe(fleetWheelLayout(id)!.radius);
      expect(Math.abs(wheel.geometry.boundingBox!.min.y)).toBeLessThanOrEqual(wheel.radius+.005);
      expect(wheel.geometry.boundingBox!.max.y).toBeLessThanOrEqual(wheel.radius+.005);
      expect(wheel.geometry.boundingBox!.max.y-wheel.geometry.boundingBox!.min.y).toBeGreaterThan(wheel.radius*1.8);
      expect(wheel.pivot.y).toBeGreaterThan(.3);
      wheel.geometry.dispose();
    }
    expect(parts.wheels[0].pivot.z).toBeGreaterThan(parts.wheels[2].pivot.z);
    expect(parts.wheels[0].pivot.x).toBeGreaterThan(parts.wheels[1].pivot.x);
    original.dispose();parts.body.dispose();
  }
});


test('lazy whole-car assembly preserves the shipped paint, glass, lamp and oriented triangle surfaces', async () => {
  const gltf = await new GLTFLoader().setMeshoptDecoder(MeshoptDecoder).loadAsync('/assets/cars/octavia-combi-4/lod1.glb');
  const expected = flattenFleetAsset(gltf.scene, 'body');
  const actual = (await loadAuthoredFleet('octavia-combi-4', 1))!;
  const triangles = (geometry: THREE.BufferGeometry) => {
    const vertices = Array.from({ length: geometry.getAttribute('position').count }, (_, i) =>
      ['position', 'normal', 'color', 'fleetSurface'].flatMap(name => {
        const attr = geometry.getAttribute(name);
        return Array.from({ length: attr.itemSize }, (_, k) => Math.round(attr.getComponent(i, k) * 1000)).join(',');
      }).join('/'));
    const result: string[] = [], index = geometry.index!;
    for (let i = 0; i < index.count; i += 3) {
      const corners = [vertices[index.getX(i)], vertices[index.getX(i + 1)], vertices[index.getX(i + 2)]];
      // Cyclic rotations preserve winding; a reversed triangle is a different surface.
      result.push([corners.join(';'), [corners[1], corners[2], corners[0]].join(';'),
        [corners[2], corners[0], corners[1]].join(';')].sort()[0]);
    }
    return result.sort();
  };
  expect(triangles(actual)).toEqual(triangles(expected));
  actual.dispose(); expected.dispose();
});

test('converted models keep their true length and only take parking slots they fit', () => {
  const authored = new Set(AUTHORED_MODELS.map(m => m.id));
  const tight = { length: 4.55, clear_front_m: .35, clear_rear_m: .35 }, roomy = { length: 4.55, clear_front_m: 3, clear_rear_m: 3 };
  let longInRoomy = 0;
  for (let i = 0; i < 3000; i++) {
    const t = fleetModel('sedan', `slot-${i}`, true, tight);
    if (authored.has(t.id)) expect((t.length - tight.length) / 2, t.id).toBeLessThanOrEqual(.05 + 1e-9);
    const r = fleetModel('sedan', `slot-${i}`, true, roomy);
    longInRoomy += Number(authored.has(r.id) && r.length > 5);
  }
  expect(longInRoomy).toBeGreaterThan(0);
});
