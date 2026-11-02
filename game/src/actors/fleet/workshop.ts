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
const workshop={
  async motionSetup() {
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
    curb.position.set(3,.075,-1);curb.receiveShadow=true;scene.add(curb);
    const model=FLEET[0], manifest=draftManifest(location.origin+'/');
    manifest.vehicles=[{object_id:'motion',model:model.id,color:'blue',x:0,y:0,z:0,yaw:0,width:model.width,height:model.height,length:model.length,clear_front_m:5,clear_rear_m:5}];
    motionVisuals=new ParkedCarVisuals(scene);await motionVisuals.load(manifest);motionVisuals.takeOver('motion');
    for(const mesh of meshes){mesh.removeFromParent();mesh.geometry.dispose();}meshes=[];
    camera.position.set(7,2.6,6);camera.lookAt(.7,.55,-.6);camera.fov=43;camera.updateProjectionMatrix();
    document.getElementById('label')!.style.display='none';
    return {frames:motionStates.length,model:model.id};
  },
  motionFrame(index:number, articulated=true) {
    const state=motionStates[index];if(!state||!motionVisuals)throw Error('Run motionSetup first');
    motionVisuals.update([state]);
    const actor=scene.getObjectByName('driven/motion') as THREE.Mesh;
    // Baseline uses the unchanged flattened geometry; both images use identical physical poses.
    if(articulated){actor.visible=true;for(const m of meshes)m.visible=false;}
    else {
      if(!meshes.length){const mesh=new THREE.Mesh(buildFleetGeometry(FLEET[0],0),fleetMaterial());paintActor(mesh,'blue');mesh.castShadow=true;mesh.receiveShadow=true;scene.add(mesh);meshes.push(mesh);}
      actor.visible=false;meshes[0].visible=true;meshes[0].position.copy(actor.position);meshes[0].quaternion.copy(actor.quaternion);meshes[0].scale.copy(actor.scale);
    }
    return {t:(index+1)/60,position:state.pos,wheels:state.wheels,draws:renderer.info.render.calls};
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
renderer.setAnimationLoop(()=>renderer.render(scene,camera));
if(import.meta.hot){
  import.meta.hot.accept();
  import.meta.hot.dispose(()=>{renderer.setAnimationLoop(null);renderer.domElement.remove();renderer.dispose();});
}
