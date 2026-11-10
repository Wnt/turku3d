import './fleet-assets.fixture';
import { expect, test, vi } from 'vitest';
import * as THREE from 'three/webgpu';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { ParkedCarVisuals, prefetchFleet } from '../../src/actors/fleet/visuals';
import { FALLBACK_MODELS, PLAYER_MODEL } from '../../src/actors/fleet/catalog';
import { draftManifest, type ParkedVehicle } from '../../src/world/manifest';

const car=(id:string,model:string,x:number,z:number):ParkedVehicle=>({object_id:id,model,color:'blue',x,y:0,z,yaw:0,
  width:2,length:5,height:2,clear_front_m:10,clear_rear_m:10});
const rest={restingPose:(c:ParkedVehicle)=>({pos:{x:c.x,y:.75,z:c.z},rot:{x:0,y:0,z:0,w:1}})};
function view(visuals:ParkedCarVisuals) {
  const camera=new THREE.PerspectiveCamera(70,1,.1,1000);camera.position.set(0,3,0);camera.lookAt(0,3,-20);visuals.setView(camera);
  return camera;
}

test('bootstrap requests only far fallbacks and starter, never every selectable design',async()=>{
  const loads=vi.mocked(GLTFLoader.prototype.loadAsync);loads.mockClear();
  await prefetchFleet(true);
  expect(loads.mock.calls.map(([url])=>url).sort()).toEqual([...FALLBACK_MODELS,PLAYER_MODEL].map(id=>`/assets/cars/${id}/lod2.glb`).sort());
});

test('streamed records load only when visible, far first; a prepared far mesh stays drawn during near preparation',async()=>{
  const scene=new THREE.Scene(),manifest=draftManifest('http://localhost/');
  manifest.vehicles=[car('near','corolla-e180',0,-15),car('unseen','carla-ford-ambulance',2000,0)];
  let release!:()=>void,nearPreparing=false;
  const visuals=new ParkedCarVisuals(scene,async model=>{
    if(model.name==='parked/corolla-e180/0'){nearPreparing=true;await new Promise<void>(r=>{release=r;});}
  });visuals.reserveTraffic(4);
  const loads=vi.mocked(GLTFLoader.prototype.loadAsync);loads.mockClear();
  await visuals.stream(manifest);view(visuals);
  expect(visuals.has('near')).toBe(false); // physics cannot spawn an unseen collider
  expect(loads.mock.calls.some(([url])=>url.includes('corolla-e180'))).toBe(false);
  const tick=()=>visuals.update([],rest);
  tick();
  await vi.waitFor(()=>{tick();expect(nearPreparing).toBe(true);});
  expect(visuals.has('near')).toBe(true);
  expect(visuals.snapshot().lods).toEqual([0,0,1]);
  const requested=loads.mock.calls.map(([url])=>url).filter(url=>url.includes('corolla-e180'));
  expect(requested).toEqual(['/assets/cars/corolla-e180/lod2.glb','/assets/cars/corolla-e180/lod0.glb']);
  expect(loads.mock.calls.some(([url])=>url.includes('carla-ford-ambulance'))).toBe(false);
  release();await vi.waitFor(()=>{tick();expect(visuals.snapshot().lods).toEqual([1,0,0]);});
  visuals.takeOver('near');tick();
  expect(visuals.driven('near')?.model).toBe('corolla-e180');
  expect(scene.getObjectByName('driven/near')).toBeTruthy();
  visuals.dispose();
});

test('traffic selects its actual design on demand and a remote-only model still loads for a peer',async()=>{
  const visuals=new ParkedCarVisuals(new THREE.Scene());visuals.reserveTraffic(4);
  await visuals.stream(draftManifest('http://localhost/'));view(visuals);
  visuals.setTraffic([{seed:'traffic-one',model:'octavia2',color:'silver',x:0,y:.75,z:-70,q:{x:0,y:0,z:0,w:1}}]);
  const tick=()=>visuals.update([]);tick();
  await vi.waitFor(()=>{tick();expect(visuals.snapshot().models).toContain('octavia2');});
  await vi.waitFor(()=>{tick();expect(visuals.snapshot().lods).toEqual([0,1,0]);});
  expect(visuals.remoteModel('carla-carlamotors-firetruck/red')).toBeNull();
  visuals.requestRemoteModel('carla-carlamotors-firetruck/red');
  await vi.waitFor(()=>expect(visuals.remoteModel('carla-carlamotors-firetruck/red')).not.toBeNull());
  visuals.dispose();
});

test('a streamed batch that finishes preparation after the session ends cannot reattach',async()=>{
  const scene=new THREE.Scene(),manifest=draftManifest('http://localhost/');
  manifest.vehicles=[car('pending','yaris-2001',0,-15)];
  let release!:()=>void,preparing=false;
  const visuals=new ParkedCarVisuals(scene,async model=>{
    if(model.name==='parked/yaris-2001/2'){preparing=true;await new Promise<void>(r=>{release=r;});}
  });visuals.reserveTraffic(4);await visuals.stream(manifest);view(visuals);visuals.update([],rest);
  await vi.waitFor(()=>expect(preparing).toBe(true));
  visuals.dispose();release();
  await vi.waitFor(()=>expect(scene.children).toHaveLength(0));
});

test('new parked bodies wait for capacity when a streamed tile adds copies of a resident design',async()=>{
  const manifest=draftManifest('http://localhost/'),scene=new THREE.Scene();
  let growing=false,release!:()=>void;
  const visuals=new ParkedCarVisuals(scene,async model=>{
    if(growing&&model.name==='parked/passat-b8/2')await new Promise<void>(r=>{release=r;});
  });visuals.reserveTraffic(4);
  manifest.vehicles=[car('old','passat-b8',0,-120)];await visuals.stream(manifest);
  expect(visuals.has('old')).toBe(true);
  growing=true;
  manifest.vehicles.push(...Array.from({length:20},(_,i)=>car(`extra-${i}`,'passat-b8',i,-120)));
  const loaded=visuals.stream(manifest);
  await vi.waitFor(()=>expect(release).toBeTypeOf('function'));
  expect(visuals.has('old')).toBe(true);
  expect(visuals.has('extra-0')).toBe(false);
  release();await loaded;
  expect(visuals.has('extra-0')).toBe(true);
  view(visuals);visuals.update([],rest);expect(visuals.snapshot().instances).toBe(21);
  visuals.dispose();
});
