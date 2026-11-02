import { headingYaw } from '../../vehicle/math';
/** Local visual QA workshop; imported only by fleet-gallery.html. */
import * as THREE from 'three/webgpu';
import { RoomEnvironment } from 'three/addons/environments/RoomEnvironment.js';
import { FLEET } from './catalog';
import { buildFleetGeometry } from './geometry';
import { fleetMaterial, fleetNight } from './material';
import { paintActor } from '../parkedPaint';
import { ParkedCarVisuals } from './visuals';
import { draftManifest } from '../../world/manifest';
import type { VehicleState } from '../../physics/protocol';
import {sampleWheelGround,wheelGroundPose} from '../../vehicle/wheelGround';

const renderer=new THREE.WebGPURenderer({antialias:true});
renderer.setSize(innerWidth,innerHeight);renderer.setPixelRatio(1);renderer.shadowMap.enabled=true;
renderer.toneMapping=THREE.ACESFilmicToneMapping;renderer.toneMappingExposure=1.1;
document.body.appendChild(renderer.domElement);await renderer.init();
const scene=new THREE.Scene();scene.background=new THREE.Color('#858e95');
const room=new RoomEnvironment(),pmrem=new THREE.PMREMGenerator(renderer);scene.environment=(await pmrem.fromSceneAsync(room)).texture;room.dispose();
const hemi=new THREE.HemisphereLight(0xd7e5ff,0x68625b,2);scene.add(hemi);
const sun=new THREE.DirectionalLight(0xfff4dd,3);sun.position.set(10,15,8);sun.castShadow=true;sun.shadow.mapSize.set(2048,2048);sun.shadow.camera.left=-15;sun.shadow.camera.right=15;sun.shadow.camera.top=15;sun.shadow.camera.bottom=-15;sun.shadow.normalBias=.035;scene.add(sun);
const floor=new THREE.Mesh(new THREE.PlaneGeometry(180,180),new THREE.MeshStandardMaterial({color:0x606568,roughness:.95}));floor.rotation.x=-Math.PI/2;floor.receiveShadow=true;scene.add(floor);
const camera=new THREE.PerspectiveCamera(40,innerWidth/innerHeight,.1,200);camera.position.set(7,3.4,9);camera.lookAt(0,.75,0);
let meshes:THREE.Mesh[]=[];
let motionVisuals: ParkedCarVisuals | undefined;
let motionStates: VehicleState[] = [];
let motionCurb:THREE.Mesh|undefined;
const workshop={
  groundFrame(index:number,articulated=true) {
    const state=motionStates[index],model=FLEET[0];if(!state||!motionVisuals)throw Error('Run motionSetup first');
    const yaw=headingYaw(state.rot);
    const at={x:state.pos.x,y:0,z:state.pos.z},footprint={wheelbase:model.wheelbase,track:model.width-.15,radius:model.wheel};
    const ground=(x:number,_top:number,z:number)=>x>=0&&x<=6&&z>=-3&&z<=1?.15:0;
    const sampled=sampleWheelGround(at,yaw,footprint,ground)!;
    const centre=sampleWheelGround(at,yaw,{...footprint,track:0},ground)!;
    const pose=articulated?sampled:wheelGroundPose(at,yaw,footprint,centre.heights)!;
    motionVisuals.setTraffic([{seed:'ground-motion',model:model.id,color:'blue',...pose.pos,q:pose.rot,wheelY:articulated?pose.wheelY:undefined}]);
    motionVisuals.update([]);
    const instances=scene.children.filter(o=>o.name.startsWith('parked/')).reduce((n,o)=>n+(o as THREE.InstancedMesh).count,0);
    if(instances!==1)throw Error(`Ground fixture has ${instances} cars`);
    return {instances,t:(index+1)/60,position:pose.pos,wheelY:pose.wheelY,heights:sampled.heights};
  },
  async batchBench() {
    motionVisuals?.dispose();
    for(const mesh of meshes){mesh.removeFromParent();mesh.geometry.dispose();}meshes=[];
    const manifest=draftManifest(location.origin+'/');
    manifest.vehicles=Array.from({length:112},(_,i)=>{
      const model=FLEET[i%FLEET.length];
      return {object_id:`bench-${i}`,model:model.id,color:'silver',x:(i%14-6.5)*3.2,y:0,z:(Math.floor(i/14)-3.5)*6,yaw:0,width:model.width,height:model.height,length:model.length,clear_front_m:5,clear_rear_m:5};
    });
    motionVisuals=new ParkedCarVisuals(scene);await motionVisuals.load(manifest);
    camera.position.set(27,20,37);camera.lookAt(0,0,0);camera.fov=58;camera.updateProjectionMatrix();
    motionVisuals.setView(camera);motionVisuals.update([],{restingPose:c=>({pos:{x:c.x,y:.75,z:c.z},rot:{x:0,y:0,z:0,w:1}})});
    const batches=scene.children.filter(o=>o.name.startsWith('parked/')) as THREE.InstancedMesh[];
    const wheels=scene.children.filter(o=>o.name.startsWith('wheels/'));
    const original=batches.map(b=>b.geometry),baseline=batches.map(b=>{
      const [,id,lod]=b.name.split('/');return buildFleetGeometry(FLEET.find(m=>m.id===id)!,Number(lod) as 0|1|2);
    });
    const results=[];
    for(const articulated of [false,true]){
      batches.forEach((b,i)=>b.geometry=articulated?original[i]:baseline[i]);
      wheels.forEach(w=>w.visible=articulated&&(w as THREE.InstancedMesh).count>0);
      const samples:number[]=[],calls:number[]=[];let previous=performance.now();
      for(let i=0;i<180;i++){
        await new Promise<void>(resolve=>requestAnimationFrame(()=>resolve()));
        const now=performance.now();if(i>=60){samples.push(now-previous);calls.push(renderer.info.render.drawCalls);}previous=now;
      }
      results.push({articulated,fps:1000/(samples.reduce((a,b)=>a+b,0)/samples.length),draws:Math.max(...calls),instances:112});
    }
    baseline.forEach(g=>g.dispose());return {results,batches:motionVisuals.snapshot()};
  },
  async motionSetup(instanced=false) {
    motionVisuals?.dispose();
    if(motionCurb){motionCurb.removeFromParent();motionCurb.geometry.dispose();}
    const R = await import('@dimforge/rapier3d');
    const { Vehicle } = await import('../../vehicle/vehicle');
    const { CurbQueries } = await import('../../vehicle/curbQueries');
    const { GROUP } = await import('../../shared/col/materials');
    const world = new R.World({x:0,y:-9.81,z:0}); world.timestep=1/60;
    const groups=(GROUP.STATIC_SOLID<<16)|GROUP.VEHICLE;
    world.createCollider(R.ColliderDesc.cuboid(100,.5,100).setTranslation(0,-.5,0).setCollisionGroups(groups));
    world.createCollider(R.ColliderDesc.cuboid(3,.075,2).setTranslation(3,.075,-1).setCollisionGroups(groups));
    const vehicle=new Vehicle(R,world,{x:-2,y:.8,z:0},0,new CurbQueries(R,world));
    for(let i=0;i<90;i++){vehicle.step(1/60,null);world.step();}
    vehicle.body.setLinvel({x:3,y:0,z:0},true);motionStates=[];
    for(let i=0;i<100;i++){
      vehicle.step(1/60,{throttle:.18,brake:0,steer:-.8,handbrake:false});world.step();
      motionStates.push({...vehicle.state(),id:'motion'});
    }
    world.free();
    const curb=new THREE.Mesh(new THREE.BoxGeometry(6,.15,4),new THREE.MeshStandardMaterial({color:0x93938b,roughness:.9}));
    curb.position.set(3,.075,-1);curb.receiveShadow=true;scene.add(curb);motionCurb=curb;
    const model=FLEET[0], manifest=draftManifest(location.origin+'/');
    manifest.vehicles=[{object_id:'motion',model:model.id,color:'blue',x:0,y:0,z:0,yaw:0,width:model.width,height:model.height,length:model.length,clear_front_m:5,clear_rear_m:5}];
    motionVisuals=new ParkedCarVisuals(scene);await motionVisuals.load(manifest);if(!instanced)motionVisuals.takeOver('motion');
    for(const mesh of meshes){mesh.removeFromParent();mesh.geometry.dispose();}meshes=[];
    camera.position.set(7,2.6,6);camera.lookAt(.7,.55,-.6);camera.fov=43;camera.updateProjectionMatrix();
    document.getElementById('label')!.style.display='none';
    return {revision:4,frames:motionStates.length,model:model.id};
  },
  motionFrame(index:number, articulated=true) {
    const state=motionStates[index];if(!state||!motionVisuals)throw Error('Run motionSetup first');
    motionVisuals.update([state]);
    const actor=(scene.getObjectByName('driven/motion')??scene.getObjectByName('parked/fleet-hatch-0/0')) as THREE.Mesh;
    if(!scene.getObjectByName('driven/motion')){
      const source=new THREE.Object3D();source.position.set(state.pos.x,state.pos.y-.75,state.pos.z);
      source.quaternion.set(state.rot.x,state.rot.y,state.rot.z,state.rot.w).multiply(new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0,1,0),Math.PI/2));
      actor.userData.baselinePose=source;
    }
    // Baseline uses the unchanged flattened geometry; both images use identical physical poses.
    if(articulated){actor.visible=true;for(const child of scene.children)if(child.name.startsWith('wheels/'))child.visible=true;for(const m of meshes)m.visible=false;}
    else {
      if(!meshes.length){const mesh=new THREE.Mesh(buildFleetGeometry(FLEET[0],0),fleetMaterial());paintActor(mesh,'blue');mesh.castShadow=true;mesh.receiveShadow=true;scene.add(mesh);meshes.push(mesh);}
      actor.visible=false;for(const child of scene.children)if(child.name.startsWith('wheels/'))child.visible=false;meshes[0].visible=true;const source=actor.userData.baselinePose??actor;meshes[0].position.copy(source.position);meshes[0].quaternion.copy(source.quaternion);meshes[0].scale.copy(source.scale);
    }
    return {t:(index+1)/60,position:state.pos,wheels:state.wheels,draws:renderer.info.render.drawCalls};
  },
  models:FLEET.map(m=>({id:m.id,name:m.name,body:m.body})),
  show(index=0,lod:0|1|2=0,color='silver') {
    for(const mesh of meshes){mesh.removeFromParent();mesh.geometry.dispose();}meshes=[];
    const model=FLEET[index],mesh=new THREE.Mesh(buildFleetGeometry(model,lod),fleetMaterial());paintActor(mesh,color);mesh.castShadow=true;mesh.receiveShadow=true;scene.add(mesh);meshes.push(mesh);
    document.getElementById('label')!.textContent=`${model.name} · ${model.body} · LOD ${lod} · ${(mesh.geometry.index?.count??mesh.geometry.getAttribute('position').count)/3} triangles`;
    return {model:model.id,triangles:(mesh.geometry.index?.count??mesh.geometry.getAttribute('position').count)/3};
  },
  grid() {
    for(const mesh of meshes){mesh.removeFromParent();mesh.geometry.dispose();}meshes=[];
    FLEET.forEach((m,i)=>{const mesh=new THREE.Mesh(buildFleetGeometry(m,0),fleetMaterial());paintActor(mesh,['silver','white','charcoal','grey','black','blue','red'][i%7]);mesh.position.set((i%7-3)*3.3,0,(Math.floor(i/7)-3.5)*6.2);mesh.castShadow=true;mesh.receiveShadow=true;scene.add(mesh);meshes.push(mesh);});
    camera.position.set(30,36,42);camera.lookAt(0,0,0);camera.fov=50;camera.updateProjectionMatrix();document.getElementById('label')!.textContent='56 original Finnish fleet models';
  },
  pose(x:number,y:number,z:number){camera.position.set(x,y,z);camera.lookAt(0,.75,0);camera.fov=40;camera.updateProjectionMatrix();},
  night(value:number){fleetNight.value=value;hemi.intensity=value? .2:2;sun.intensity=value?.1:3;},
  renderer,scene,camera,
};
Object.assign(window,{fleetWorkshop:workshop});workshop.show();
renderer.setAnimationLoop(()=>{renderer.info.reset();renderer.render(scene,camera);});
if(import.meta.hot){
  import.meta.hot.accept();
  import.meta.hot.dispose(()=>{renderer.setAnimationLoop(null);renderer.domElement.remove();renderer.dispose();});
}
