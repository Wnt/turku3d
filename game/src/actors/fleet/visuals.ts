import * as THREE from 'three/webgpu';
import type { VehicleState, Quat } from '../../physics/protocol';
import type { Manifest, ParkedVehicle } from '../../world/manifest';
import type { ParkedPose } from '../../world/parkedVehicles';
import { TRAFFIC_MODELS } from '../../traffic/constants';
import { fleetOrigin } from './pose';
import { paintActor, paintInstances } from '../parkedPaint';
import { FLEET, fleetAppearance, baseFleetId, type FleetModel } from './catalog';
import { buildFleetGeometry } from './geometry';
import { fleetLamps, fleetActorLamps, lampState } from './lamps';
import { fleetMaterial, fleetNight } from './material';

export interface ParkedRest { restingPose(car: ParkedVehicle): ParkedPose | null }
export interface TrafficInstance { model: string; color: string; x: number; y: number; z: number; q: Quat; seed?: string; indicator?: -1|0|1; braking?: boolean; speed?: number }
interface Batch { mesh: THREE.InstancedMesh; model: FleetModel; lod: number; paints: string[]; lamps: number[]; count: number; dirty: boolean; slots: string[]; versions: number[]; first: number; last: number }
interface Draw { id: string; model: FleetModel; color: string; pos: {x:number;y:number;z:number}; rot: Quat; size?: ParkedVehicle; powered?: boolean; indicator?: -1|0|1; braking?: boolean }
const AXIS = new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0,1,0), Math.PI/2);
const HIDDEN = new THREE.Matrix4().makeScale(0,0,0);

/** Shared original parametric fleet. Each visible model/LOD costs one instanced draw regardless
 * of colour, glazing, wheels or lamps. Existing physics records remain authoritative. */
export class ParkedCarVisuals {
  private cars: ParkedVehicle[]=[];
  private disposed=false;
  private transforms=new Map<string,{values:number[];matrix:THREE.Matrix4;version:number}>();
  private groups=new Map<string,Batch>();
  private geometries=new Map<string,THREE.BufferGeometry>();
  private dynamic=new Map<string,THREE.Mesh>();
  private definitions=new Map<string,{model:FleetModel;color:string;size?:ParkedVehicle}>();
  private traffic: readonly TrafficInstance[]=[];
  private reserve=0;
  private camera=new THREE.Vector3();
  private frustum=new THREE.Frustum();
  private viewMatrix=new THREE.Matrix4();
  private sphere=new THREE.Sphere(new THREE.Vector3(),6);
  private hasView=false;
  private variants=new Map<string,FleetModel>();
  private matrix=new THREE.Matrix4(); private position=new THREE.Vector3();
  private rotation=new THREE.Quaternion(); private scale=new THREE.Vector3(1,1,1);
  private loading: Promise<void>=Promise.resolve();
  private loaded=false;
  constructor(private readonly scene: THREE.Object3D, private readonly prepare: (model: THREE.Object3D)=>Promise<void> = async()=>{}) {}
  reserveTraffic(count:number) { this.reserve=Math.max(0,count); }
  trafficModels(): string[] { return this.loaded ? [...TRAFFIC_MODELS] : []; }
  setTraffic(cars:readonly TrafficInstance[]) { this.traffic=cars; }
  setView(camera: THREE.Camera, night=0) { this.camera.copy(camera.position); camera.updateMatrixWorld();
    this.frustum.setFromProjectionMatrix(this.viewMatrix.multiplyMatrices(camera.projectionMatrix,camera.matrixWorldInverse),camera.coordinateSystem);this.hasView=true;
    fleetNight.value=Math.max(0,Math.min(1,night)); }
  renders(id:string) { return this.dynamic.has(id)||this.cars.some(c=>c.object_id===id); }
  private geometry(model:FleetModel,lod:number) {
    const key=`${model.id}/${lod}`; let g=this.geometries.get(key);
    if(!g) { g=buildFleetGeometry(model,lod as 0|1|2); this.geometries.set(key,g); } return g;
  }
  load(manifest:Manifest):Promise<void> {
    if(this.disposed)return Promise.reject(new Error('Fleet visuals disposed'));
    // Tile streaming can request another load while GPU preparation yields. Publish serially.
    const cars=(manifest.vehicles??[]).filter(c=>c.model!=='truck').map(c=>({...c}));
    const run=this.loading.catch(()=>{}).then(()=>this.loadNow(cars)); this.loading=run; return run;
  }
  private async loadNow(cars:ParkedVehicle[]) {
    if(this.disposed)throw new Error('Fleet visuals disposed');
    const counts=new Map<string,number>(),models=new Map<string,FleetModel>();
    for(const car of cars) {
      const model=fleetAppearance(car.model,car.object_id,car);
      counts.set(model.id,(counts.get(model.id)??0)+1);models.set(model.id,model);
    }
    if(this.reserve)for(const m of FLEET){const fit=fleetAppearance(m.id,m.id);models.set(fit.id,fit);}
    const replacements: [string,Batch][]=[];
    try {
      for(const model of models.values()) for(let lod=0;lod<3;lod++) {
        const key=`${model.id}/${lod}`, required=(counts.get(model.id)??0)+this.reserve;
        if((this.groups.get(key)?.mesh.instanceMatrix.count??0)>=required) continue;
        const capacity=Math.max(16,2**Math.ceil(Math.log2(Math.max(1,required))));
        const mesh=new THREE.InstancedMesh(this.geometry(model,lod),fleetMaterial(lod),capacity);
        mesh.name=`parked/${key}`; mesh.castShadow=lod===0; mesh.receiveShadow=true;
        mesh.count=0; mesh.setMatrixAt(0,HIDDEN); paintInstances(mesh,[]); fleetLamps(mesh,[]);
        const batch={mesh,model,lod,paints:[],lamps:[],count:0,dirty:true,slots:[],versions:[],first:Infinity,last:-1}; replacements.push([key,batch]);
        await this.prepare(mesh);
        if(this.disposed)throw new Error('Fleet visuals disposed');
      }
    } catch(error) { for(const [,b] of replacements) b.mesh.dispose(); throw error; }
    // Keep every old draw until ALL replacement materials and passes are ready.
    for(const [key,batch] of replacements) {
      const old=this.groups.get(key); if(old) {old.mesh.removeFromParent();old.mesh.dispose();}
      this.groups.set(key,batch); this.scene.add(batch.mesh);
    }
    for(const car of cars) this.definitions.set(car.object_id,{model:fleetAppearance(car.model,car.object_id,car),color:car.color,size:car});
    const resident=new Set(cars.map(c=>c.object_id));
    for(const id of this.definitions.keys())if(!resident.has(id)&&!this.dynamic.has(id)){this.definitions.delete(id);this.transforms.delete(id);}
    this.cars=cars.filter(c=>!this.dynamic.has(c.object_id)); this.loaded=true;
  }
  private actor(model:FleetModel,color:string,name:string) {
    const actor=new THREE.Mesh(this.geometry(model,0),fleetMaterial()); paintActor(actor,color);fleetActorLamps(actor);
    actor.name=name;actor.castShadow=true;actor.receiveShadow=true;return actor;
  }
  takeOver(id:string) {
    if(this.dynamic.has(id))return;
    const def=this.definitions.get(id);if(!def)return;
    const actor=this.actor(def.model,def.color,`driven/${id}`);this.scene.add(actor);this.dynamic.set(id,actor);
    this.cars=this.cars.filter(c=>c.object_id!==id);
  }
  release(id:string){
    const actor=this.dynamic.get(id);if(!actor)return;
    actor.removeFromParent();this.dynamic.delete(id);this.transforms.delete(id);
    const car=this.definitions.get(id)?.size;if(car&&!this.cars.some(c=>c.object_id===id))this.cars.push(car);
  }
  resetRound(){for(const id of this.dynamic.keys())this.release(id);}
  requestRemoteModel(_key:string) { /* The code-authored fleet is resident; no network asset request. */ }
  remoteModel(key:string):THREE.Mesh|null {
    const [model,color]=key.split('/');
    try{return this.actor(fleetAppearance(model,key),color??'silver',`remote/${key}`);}catch{return null;}
  }
  private transform(draw:Draw) {
    const origin=fleetOrigin(draw.pos,draw.rot);this.position.set(origin.x,origin.y,origin.z);
    this.rotation.set(draw.rot.x,draw.rot.y,draw.rot.z,draw.rot.w).multiply(AXIS);
    // Existing slot/collider dimensions bound replacement vehicles; accessories are visual only.
    this.scale.set(1,1,1);
    return this.matrix.compose(this.position,this.rotation,this.scale);
  }
  private draw(draw:Draw) {
    const d=Math.hypot(draw.pos.x-this.camera.x,draw.pos.z-this.camera.z);
    if(d>320)return;
    this.sphere.center.set(draw.pos.x,draw.pos.y,draw.pos.z);
    if(this.hasView&&d>30&&!this.frustum.intersectsSphere(this.sphere))return;
    const lod=d<42?0:d<105?1:2, group=this.groups.get(`${draw.model.id}/${lod}`);
    if(!group||group.count>=group.mesh.instanceMatrix.count)return;
    const values=[draw.pos.x,draw.pos.y,draw.pos.z,draw.rot.x,draw.rot.y,draw.rot.z,draw.rot.w];
    let cached=this.transforms.get(draw.id);
    if(!cached){cached={values,matrix:this.transform(draw).clone(),version:1};this.transforms.set(draw.id,cached);}
    else if(values.some((value,i)=>value!==cached!.values[i])){cached.values=values;cached.matrix.copy(this.transform(draw));cached.version++;}
    const index=group.count;
    if(group.slots[index]!==draw.id||group.versions[index]!==cached.version){
      group.mesh.setMatrixAt(index,cached.matrix);group.slots[index]=draw.id;group.versions[index]=cached.version;
      group.dirty=true;group.first=Math.min(group.first,index);group.last=index;
    }
    group.paints[group.count]=draw.color;const lamps=lampState(!!draw.powered,draw.indicator,draw.braking);for(let i=0;i<4;i++)group.lamps[group.count*4+i]=lamps[i];group.count++;
  }
  update(states:VehicleState[],rest?:ParkedRest) {
    const byId=new Map(states.map(s=>[s.id,s]));
    for(const b of this.groups.values()){b.count=0;b.dirty=false;b.first=Infinity;b.last=-1;}
    for(const car of this.cars) {
      const pose=byId.get(car.object_id)??rest?.restingPose(car); if(!pose)continue;
      const def=this.definitions.get(car.object_id)!;
      this.draw({id:car.object_id,...def,pos:pose.pos,rot:pose.rot});
    }
    const moving=new Set<string>();
    for(let i=0;i<this.traffic.length;i++) {
      const car=this.traffic[i], id=car.seed??`${car.model}/${i}`;
      moving.add(id);const key=car.model+'/'+id;
      let model=this.variants.get(key);
      if(!model){model=fleetAppearance(car.model,id);if(this.variants.size>512)this.variants.clear();this.variants.set(key,model);}
      this.draw({id,model,color:car.color,pos:car,rot:car.q,powered:true,indicator:car.indicator,braking:car.braking});
    }
    for(const id of this.transforms.keys())if(!this.definitions.has(id)&&!moving.has(id))this.transforms.delete(id);
    for(const b of this.groups.values()) {
      const changedCount=b.mesh.count!==b.count;
      b.mesh.count=b.count;b.mesh.visible=b.count>0;
      if(!b.count)continue;
      b.paints.length=b.count;b.lamps.length=b.count*4;paintInstances(b.mesh,b.paints);fleetLamps(b.mesh,b.lamps);
      if(b.dirty){b.mesh.instanceMatrix.addUpdateRange(b.first*16,(b.last-b.first+1)*16);b.mesh.instanceMatrix.needsUpdate=true;}
      if(b.dirty||changedCount)b.mesh.computeBoundingSphere();
    }
    for(const [id,actor] of this.dynamic) {
      const state=byId.get(id);actor.visible=!!state;if(!state)continue;
      actor.matrix.copy(this.transform({id,...this.definitions.get(id)!,pos:state.pos,rot:state.rot}));
      actor.matrix.decompose(actor.position,actor.quaternion,actor.scale);
    }
  }
  dispose() {
    this.disposed=true;this.cars=[];this.traffic=[];this.transforms.clear();
    for(const b of this.groups.values()){b.mesh.removeFromParent();b.mesh.dispose();}
    for(const actor of this.dynamic.values())actor.removeFromParent();
    this.groups.clear();this.dynamic.clear();this.definitions.clear();
    for(const geometry of this.geometries.values())geometry.dispose();this.geometries.clear();
  }
  snapshot() {
    const active=[...this.groups.values()].filter(b=>b.count);
    return {catalogue:FLEET.length,models:[...new Set(active.map(b=>baseFleetId(b.model.id)))],instances:active.reduce((s,b)=>s+b.count,0),draws:active.length,
      lods:[0,1,2].map(lod=>active.filter(b=>b.lod===lod).reduce((s,b)=>s+b.count,0)),triangles:active.reduce((s,b)=>s+(b.mesh.geometry.index?.count??b.mesh.geometry.getAttribute('position').count)/3*b.count,0)};
  }
  inspect(id:string,camera:THREE.Camera) {
    const actor=this.dynamic.get(id);if(!actor)return null;
    camera.updateMatrixWorld();actor.updateMatrixWorld();
    const frustum=new THREE.Frustum().setFromProjectionMatrix(new THREE.Matrix4().multiplyMatrices(camera.projectionMatrix,camera.matrixWorldInverse));
    const projected=actor.position.clone().project(camera);
    return {parented:actor.parent===this.scene,visible:actor.visible&&frustum.intersectsObject(actor),position:actor.position.toArray(),projected:projected.toArray(),castShadow:actor.castShadow,staticInstances:this.cars.filter(c=>c.object_id===id).length};
  }
}
