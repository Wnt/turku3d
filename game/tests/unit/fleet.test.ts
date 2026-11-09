import './fleet-assets.fixture';
import { expect,test,vi } from 'vitest';
import * as THREE from 'three/webgpu';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { MeshoptDecoder } from 'three/addons/libs/meshopt_decoder.module.js';
import { FALLBACK_MODELS, FLEET, FLEET_BY_ID, PLAYER_MODEL, SELECTABLE_FAMILIES, fleetModel } from '../../src/actors/fleet/catalog';
import { TRAFFIC_MODELS } from '../../src/traffic/constants';
import { AUTHORED_MODELS } from '../../src/actors/fleet/registry';
import { ParkedCarVisuals } from '../../src/actors/car';
import {fleetWheelLayout} from '../../src/actors/fleet/wheelLayouts';
import { draftManifest } from '../../src/world/manifest';
import { flattenFleetAsset, loadAuthoredFleet, loadAuthoredFleetParts } from '../../src/actors/fleet/authored';

test('every role draws a real model: everyday families, the starter car and light preloaded fallbacks',()=>{
  expect(FLEET.length).toBeGreaterThanOrEqual(50);
  expect(FLEET.map(m=>m.id).every(id=>AUTHORED_MODELS.some(a=>a.id===id))).toBe(true);
  for(const family of SELECTABLE_FAMILIES)expect(FLEET.filter(m=>m.family===family).length,family).toBeGreaterThanOrEqual(3);
  expect(FLEET_BY_ID.has(PLAYER_MODEL)).toBe(true);
  for(const id of FALLBACK_MODELS)expect(AUTHORED_MODELS.find(a=>a.id===id)!.triangles[0],id).toBeLessThan(16000);
});

test('parked and moving fleet draw real models, share one material and switch distance LODs',async()=>{
  const sample=['golf7','corolla-e180','carla-nissan-micra','prado-2010','euro-van','passat-b8'].map(id=>FLEET_BY_ID.get(id)!);
  const scene=new THREE.Scene(),visuals=new ParkedCarVisuals(scene),manifest=draftManifest('http://localhost/');
  manifest.vehicles=sample.map((m,i)=>({object_id:`test-${i}`,model:m.id,color:'silver',x:i*3,y:0,z:0,yaw:0,width:m.width,height:m.height,length:m.length,clear_front_m:5,clear_rear_m:5}));
  await visuals.load(manifest);const camera=new THREE.PerspectiveCamera(120,1,.1,500);camera.position.set(0,10,20);camera.lookAt(6,0,0);visuals.setView(camera);
  const rest={restingPose:(c:NonNullable<typeof manifest.vehicles>[number])=>({pos:{x:c.x,y:.75,z:c.z},rot:{x:0,y:0,z:0,w:1}})};
  visuals.update([],rest);expect(visuals.snapshot().models).toHaveLength(sample.length);expect(visuals.snapshot().lods).toEqual([sample.length,0,0]);
  expect(new Set(scene.children.map(m=>(m as THREE.Mesh).material)).size).toBe(1);
  camera.position.z=70;camera.lookAt(6,0,0);visuals.setView(camera);visuals.update([],rest);expect(visuals.snapshot().lods).toEqual([0,sample.length,0]);
  camera.position.z=200;camera.lookAt(6,0,0);visuals.setView(camera);visuals.update([],rest);expect(visuals.snapshot().lods).toEqual([0,0,sample.length]);
  visuals.takeOver('test-0');visuals.update([],rest);expect(visuals.snapshot().instances).toBe(sample.length-1);
  visuals.dispose();
});

test('parked cars beyond 320 m or off screen beyond 30 m are culled before drawing; near ones behind still draw',async()=>{
  const m=FLEET_BY_ID.get('golf7')!;
  const scene=new THREE.Scene(),visuals=new ParkedCarVisuals(scene),manifest=draftManifest('http://localhost/');
  const at:[number,number][]=[[0,-20],[0,-200],[0,-330],[0,25],[0,60],[300,-100]];
  manifest.vehicles=at.map(([x,z],i)=>({object_id:`cull-${i}`,model:m.id,color:'silver',x,y:0,z,yaw:0,width:m.width,height:m.height,length:m.length,clear_front_m:5,clear_rear_m:5}));
  await visuals.load(manifest);
  const camera=new THREE.PerspectiveCamera(60,1,.1,1000);camera.position.set(0,2,0);camera.lookAt(0,2,-10);visuals.setView(camera);
  const asked:string[]=[];
  const rest={restingPose:(c:NonNullable<typeof manifest.vehicles>[number])=>{asked.push(c.object_id);return {pos:{x:c.x,y:.75,z:c.z},rot:{x:0,y:0,z:0,w:1}};}};
  visuals.update([],rest);
  // In view: -20 (LOD0), -200 (LOD2); 25 m behind still draws (within 30 m); -330 is past 320 m; 60 m behind and
  // (300,-100) outside the 60-degree view are culled.
  expect(visuals.snapshot().lods).toEqual([2,0,1]);
  expect(asked).toHaveLength(at.length);
  visuals.dispose();
});

test('fleet batches are composed once: no per-frame matrix work, still placed under a moved parent',async()=>{
  const sample=['golf7','passat-b8'].map(id=>FLEET_BY_ID.get(id)!);
  const root=new THREE.Group();root.position.set(3,0,-2);root.updateMatrixWorld(true);root.matrixAutoUpdate=false;  // like play.ts's session root
  const visuals=new ParkedCarVisuals(root),manifest=draftManifest('http://localhost/');
  manifest.vehicles=sample.map((m,i)=>({object_id:`frozen-${i}`,model:m.id,color:'silver',x:i*3,y:0,z:0,yaw:0,width:m.width,height:m.height,length:m.length,clear_front_m:5,clear_rear_m:5}));
  await visuals.load(manifest);
  const batches:THREE.Object3D[]=[];root.traverse(o=>{if(o!==root)batches.push(o);});
  expect(batches.length).toBeGreaterThan(4);
  for(const b of batches){expect(b.matrixAutoUpdate).toBe(false);expect(b.matrixWorld.elements[12]).toBe(3);expect(b.matrixWorld.elements[14]).toBe(-2);}
  const compose=vi.spyOn(THREE.Matrix4.prototype,'compose'),multiply=vi.spyOn(THREE.Matrix4.prototype,'multiplyMatrices');
  try{root.updateMatrixWorld();expect(compose).not.toHaveBeenCalled();expect(multiply).not.toHaveBeenCalled();}finally{compose.mockRestore();multiply.mockRestore();}
  visuals.dispose();
});

test('a parking slot no real model fits stays empty instead of stretching a car',()=>{
  const slot={length:3,clear_front_m:0,clear_rear_m:0};
  for(let i=0;i<50;i++)expect(fleetModel('hatchback-sports',`short-${i}`,slot)).toBeNull();
  const roomy={length:4.15,clear_front_m:.6,clear_rear_m:.6};
  for(let i=0;i<50;i++){const m=fleetModel('hatchback-sports',`fit-${i}`,roomy)!;expect((m.length-4.15)/2).toBeLessThanOrEqual(.3+1e-9);}
});

// Solo play: a slot no model fits has no definition, yet ParkedVehicles.restingPose still gives it its slot pose
// (in multiplayer the id is a shared vehicle and gets none). Drawing it threw "reading 'id'" every frame.
test('a parked record no real model fits is never drawn, even with a resting pose',async()=>{
  const scene=new THREE.Scene(),visuals=new ParkedCarVisuals(scene),manifest=draftManifest('http://localhost/');
  const record=(object_id:string,model:string,length:number,gap:number)=>({object_id,model,color:'silver',x:0,y:0,z:0,yaw:0,width:1.8,height:1.5,length,clear_front_m:gap,clear_rear_m:gap});
  manifest.vehicles=[record('tight','hatchback-sports',3,0),record('fits',FALLBACK_MODELS[0],4,5)];
  await visuals.load(manifest);
  const rest={restingPose:(c:NonNullable<typeof manifest.vehicles>[number])=>({pos:{x:c.x,y:.75,z:c.z},rot:{x:0,y:0,z:0,w:1}})};
  for(let frame=0;frame<2;frame++)expect(()=>visuals.update([],rest)).not.toThrow();
  expect(visuals.snapshot().instances).toBe(1);
  expect(visuals.has('tight')).toBe(false);expect(visuals.renders('tight')).toBe(false);
  visuals.dispose();
});

test('model assignment is stable and legacy families remain compatible',()=>{
  for(const family of ['sedan','suv','van','hatchback-sports','sedan-sports'])for(let i=0;i<100;i++) {
    const model=fleetModel(family,String(i));expect(model.family).toBe(family==='sedan-sports'?'sedan':family);expect(fleetModel(family,String(i))).toBe(model);
  }
});


// A traffic car carries the real model the catalog picks within its family. A family name or an id this build does
// not know (a stale dev server's model glob listed 3 models: every frame threw reading `id`) draws as a fallback.
test('traffic draws real model ids; a family name or unknown id draws as a fallback with one warning, never a throw',async()=>{
  for(const family of TRAFFIC_MODELS){
    expect(SELECTABLE_FAMILIES.has(family),family).toBe(true);
    for(let i=0;i<50;i++)expect(FLEET_BY_ID.get(fleetModel(family,String(i)).id)!.family).toBe(family);
  }
  const warn=vi.spyOn(console,'warn').mockImplementation(()=>{});
  const scene=new THREE.Scene(),visuals=new ParkedCarVisuals(scene),manifest=draftManifest('http://localhost/');
  manifest.vehicles=FALLBACK_MODELS.map((model,i)=>({object_id:`f${i}`,model,color:'silver',x:i*3,y:0,z:0,yaw:0,width:2,length:4.6,height:1.5,clear_front_m:5,clear_rear_m:5}));
  await visuals.load(manifest);
  const car=(model:string,seed:string,x:number)=>({seed,model,color:'blue',x,y:.75,z:6,q:{x:0,y:0,z:0,w:1}});
  visuals.setTraffic([car('sedan','a',0),car('suv','b',4),car('no-such-car','c',8),car(FALLBACK_MODELS[0],'d',12)]);
  for(let frame=0;frame<3;frame++)expect(()=>visuals.update([])).not.toThrow();
  // Parked cars have no pose here; all four moving cars draw, each as a resident fallback model.
  const drawn=visuals.snapshot();
  expect(drawn.instances).toBe(4);
  expect(drawn.models.every(id=>(FALLBACK_MODELS as readonly string[]).includes(id))).toBe(true);
  expect(warn.mock.calls.map(([m])=>String(m)).filter(m=>m.startsWith('[fleet] traffic model')))
    .toEqual(['sedan','suv','no-such-car'].map(id=>`[fleet] traffic model "${id}" is not in the catalog; drawn as a fallback model`));
  warn.mockRestore();visuals.dispose();
});

test('moving traffic drawn in view within 40 m are the ambient drivers\' seats (model and model-frame matrix)',async()=>{
  const scene=new THREE.Scene(),visuals=new ParkedCarVisuals(scene),manifest=draftManifest('http://localhost/');
  manifest.vehicles=FALLBACK_MODELS.map((model,i)=>({object_id:`f${i}`,model,color:'silver',x:i*3,y:0,z:0,yaw:0,width:2,length:4.6,height:1.5,clear_front_m:5,clear_rear_m:5}));
  await visuals.load(manifest);
  const car=(seed:string,x:number)=>({seed,model:FALLBACK_MODELS[0],color:'blue',x,y:.75,z:6,q:{x:0,y:0,z:0,w:1}});
  visuals.setTraffic([car('a',0),car('b',6),car('c',60)]);
  const camera=new THREE.PerspectiveCamera(60,1.6,.1,1000);camera.position.set(3,4,-14);camera.lookAt(3,0,6);
  visuals.setView(camera);visuals.update([]);
  expect(visuals.trafficSeatCount).toBe(2);
  expect(visuals.trafficSeats.slice(0,2).map(s=>s.model)).toEqual([FALLBACK_MODELS[0],FALLBACK_MODELS[0]]);
  expect(new THREE.Vector3().setFromMatrixPosition(visuals.trafficSeats[1].matrix).toArray().map(v=>+v.toFixed(3))).toEqual([6,0,6]);
  // Behind the camera: none.
  camera.lookAt(3,0,-40);visuals.setView(camera);visuals.update([]);
  expect(visuals.trafficSeatCount).toBe(0);
  visuals.dispose();
});

test('a registry without the traffic families or the fallbacks (a stale dev glob) still yields catalog models',async()=>{
  const stale=AUTHORED_MODELS.filter(m=>['golf7','garbage-truck','transit-bus'].includes(m.id));
  vi.resetModules();vi.doMock('../../src/actors/fleet/registry',()=>({AUTHORED_MODELS:stale,AUTHORED_BY_ID:new Map(stale.map(m=>[m.id,m]))}));
  try{
    const catalog=await import('../../src/actors/fleet/catalog');
    expect(catalog.FLEET.map(m=>m.id).sort()).toEqual(['garbage-truck','golf7','transit-bus']);
    for(const family of TRAFFIC_MODELS)for(let i=0;i<20;i++)expect(catalog.FLEET).toContain(catalog.fleetModel(family,String(i)));
    for(let i=0;i<20;i++)expect(catalog.FLEET).toContain(catalog.fallbackModel(String(i)));
  }finally{vi.doUnmock('../../src/actors/fleet/registry');vi.resetModules();}
});

test('ordinary traffic reaches every selectable real model with only a few pickups and older cars',()=>{
  const models=new Set<string>();let pickups=0,old=0;
  for(const family of ['sedan','suv','van','hatchback-sports'])for(let i=0;i<2000;i++){
    const m=fleetModel(family,`tr:tile:lane:${i}`);models.add(m.id);pickups+=Number(m.body==='pickup');old+=Number(m.body==='classic');
  }
  expect(models.size).toBe(FLEET.filter(m=>SELECTABLE_FAMILIES.has(m.family)&&(m.weight??1)>0).length);expect(pickups/2000).toBeLessThan(.05);
  expect(old/2000).toBeGreaterThan(.04);expect(old/2000).toBeLessThan(.2);
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
  for(const id of ['octavia-combi-4','golf7'])for(let lod=0;lod<3;lod++){
    const original=(await loadAuthoredFleet(id,lod))!,parts=(await loadAuthoredFleetParts(id,lod))!;
    expect(parts.wheels).toHaveLength(4);
    expect(triangles(parts.body)+parts.wheels.reduce((n,w)=>n+triangles(w.geometry),0)).toBe(triangles(original));
    for(const wheel of parts.wheels){
      expect(wheel.geometry.getAttribute('fleetSurface')).toBeDefined();
      // LOD decimation is asymmetric around the fixed hub; it must never move that pivot.
      const pivot=fleetWheelLayout(id)!.pivots[wheel.name as 'wheel_FL'];
      expect(wheel.pivot).toEqual({x:expect.closeTo(pivot[0],3),y:expect.closeTo(pivot[1],3),z:expect.closeTo(pivot[2],3)});
      expect(wheel.radius).toBe(fleetWheelLayout(id)!.radius);
      expect(Math.abs(wheel.geometry.boundingBox!.min.y)).toBeLessThanOrEqual(wheel.radius+.005);
      expect(wheel.geometry.boundingBox!.max.y).toBeLessThanOrEqual(wheel.radius+.005);
      expect(wheel.geometry.boundingBox!.max.y-wheel.geometry.boundingBox!.min.y).toBeGreaterThan(wheel.radius*(lod===2?1.5:1.8));
      expect(wheel.pivot.y).toBeGreaterThan(.25);
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
    const t = fleetModel('sedan', `slot-${i}`, tight);
    if (t && authored.has(t.id)) expect((t.length - tight.length) / 2, t.id).toBeLessThanOrEqual(.05 + 1e-9);
    const r = fleetModel('sedan', `slot-${i}`, roomy);
    longInRoomy += Number(!!r && authored.has(r.id) && r.length > 5);
  }
  expect(longInRoomy).toBeGreaterThan(0);
});

// The CARLA MINI arrived at 4.55 m, with a 3.01 m wheelbase. Normalize the whole articulated asset to the
// manufacturer's 2021 length; keep each LOD's measured hubs and the physics in agreement with that asset.
test('MINI scale matches the manufacturer length across all LODs and keeps its drivetrain', async () => {
  const m = FLEET_BY_ID.get('carla-mini-cooper-s-2021')!;
  const entry = AUTHORED_MODELS.find(model => model.id === m.id)!;
  expect(m.length).toBeCloseTo(3.876, 3);
  expect(Math.abs(m.wheelbase / 2.495 - 1)).toBeLessThan(0.03); // the source mesh retains its proportions
  for (const lod of [0, 1, 2]) {
    const whole = (await loadAuthoredFleet(m.id, lod))!, parts = (await loadAuthoredFleetParts(m.id, lod))!;
    whole.computeBoundingBox();
    // LOD simplification may remove a few millimetres from the bumper extrema.
    expect(Math.abs((whole.boundingBox!.max.z - whole.boundingBox!.min.z) / m.length - 1)).toBeLessThan(0.01);
    expect(parts.wheels).toHaveLength(4);
    const axle = (names: string[]) => parts.wheels.filter(w => names.includes(w.name)).reduce((sum, w) => sum + w.pivot.z, 0) / 2;
    expect(axle(['wheel_FL', 'wheel_FR']) - axle(['wheel_RL', 'wheel_RR'])).toBeCloseTo(m.wheelbase, 3);
    for (const wheel of parts.wheels) {
      const pivot = entry.wheels.pivots[wheel.name as keyof typeof entry.wheels.pivots];
      expect(wheel.pivot.x).toBeCloseTo(pivot[0], 3); expect(wheel.pivot.y).toBeCloseTo(pivot[1], 3); expect(wheel.pivot.z).toBeCloseTo(pivot[2], 3);
      if (lod === 0) {
        wheel.geometry.computeBoundingBox();
        expect(wheel.geometry.boundingBox!.max.y).toBeCloseTo(entry.wheels.radius, 2);
        expect(wheel.geometry.boundingBox!.min.y).toBeCloseTo(-entry.wheels.radius, 2);
      }
    }
    parts.body.dispose(); for (const wheel of parts.wheels) wheel.geometry.dispose(); whole.dispose();
  }
  const { modelTuning } = await import('../../src/vehicle/modelTuning');
  const t = modelTuning(m.id)!;
  expect(entry.spec.torqueNm).toBe(280); expect(entry.spec.turningCircleM).toBe(10.8);
  expect(t.mass).toBe(1235);
  expect(t.enginePower).toBe(141000 * 0.86);
  expect(t.driven).toEqual([0, 1]);
});
