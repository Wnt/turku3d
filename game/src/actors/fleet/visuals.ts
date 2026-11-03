import { headingYaw } from '../../vehicle/math';
import * as THREE from 'three/webgpu';
import type { VehicleState, Quat, WheelState } from '../../physics/protocol';
import type { Manifest, ParkedVehicle } from '../../world/manifest';
import type { ParkedPose } from '../../world/parkedVehicles';
import { TRAFFIC_MODELS, CHASSIS_LIFT } from '../../traffic/constants';
import { paintActor, paintInstances } from '../parkedPaint';
import { FLEET, fleetModel, type FleetModel } from './catalog';
import { buildFleetGeometry, buildFleetWheel } from './geometry';
import { AUTHORED_ESTATE, loadAuthoredFleet, loadAuthoredFleetParts } from './authored';
import type {AuthoredFleetParts} from './authoredWheelParts';
import { AUTHORED_BY_ID } from './registry';
import { addFleetWheels, addAuthoredFleetWheels, applyFleetWheels } from './wheels';
import { vehicleLamps } from '../../vehicle/lamps';
import { fleetLamps } from './lamps';
import { ackermann, wheelRotation } from '../../vehicle/wheelKinematics';
import { fleetGlassMaterial, fleetMaterial, fleetNight } from './material';

export interface ParkedRest { restingPose(car: ParkedVehicle): ParkedPose | null }
export interface TrafficInstance { model: string; color: string; x: number; y: number; z: number; q: Quat; seed?: string; wheelY?: readonly number[]; indicator?: -1 | 0 | 1; braking?: boolean }
interface WheelDesign {key:string;pivot:{x:number;y:number;z:number};sharedSide:boolean}
interface WheelBatch { mesh:THREE.InstancedMesh; count:number; dirty:boolean }
interface Batch { mesh: THREE.InstancedMesh; glass?: THREE.InstancedMesh; model: FleetModel; lod: number; paints: string[]; lamps: number[]; indicators: number[]; brakes: number[]; count: number; dirty: boolean }
interface Draw { id: string; model: FleetModel; color: string; pos: {x:number;y:number;z:number}; rot: Quat; wheels?: readonly WheelState[]; wheelY?: readonly number[]; spin?: number; steer?: number; size?: ParkedVehicle; powered?: boolean; indicator?: -1 | 0 | 1; braking?: boolean }
const AXIS = new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0,1,0), Math.PI/2);
const embeddedWheels=(id:string)=>id===AUTHORED_ESTATE||AUTHORED_BY_ID.has(id);
const HIDDEN = new THREE.Matrix4().makeScale(0,0,0);
/** Rim detail and tyre radius determine the wheel design, independently of body/paint. */
export const fleetWheelDesign=(model:FleetModel,lod:number)=>`${lod}/${model.wheel.toFixed(3)}/${lod===0?model.face%3:0}`;

/** Shared original parametric fleet. Near model/LODs share a body draw and pool wheel draws by tyre/rim design;
 * far cars retain one flattened draw. Paint and lamps do not add draws. Existing physics records remain authoritative. */
export class ParkedCarVisuals {
  private cars: ParkedVehicle[]=[];
  private groups=new Map<string,Batch>();
  private wheelGroups=new Map<string,WheelBatch>();
  private designs=new Map<string,WheelDesign[]>();
  private authoredParts=new Map<string,AuthoredFleetParts>();
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
  /** One detached, prepared driven-car actor per loaded design: r186 keys a plain Mesh's programs apart from the
   *  instanced batches, so the first takeOver of a design built its beauty, prepass, shadow and reflection programs
   *  inside that frame (live play only: replays never take a car over). Held so r186 keeps the builds; never drawn. */
  private keepers=new Map<string,THREE.Mesh>();
  private wheelMotion=new Map<string,{x:number;z:number;yaw:number;spin:number;steer:number}>();
  private wheelMatrix=new THREE.Matrix4(); private hubMatrix=new THREE.Matrix4();
  private hubPosition=new THREE.Vector3(); private hubRotation=new THREE.Quaternion();
  private hubEuler=new THREE.Euler(0,0,0,'YXZ'); private unitScale=new THREE.Vector3(1,1,1);
  private remoteRequests=new Map<string,Promise<void>>();
  constructor(private readonly scene: THREE.Object3D, private readonly prepare: (model: THREE.Object3D)=>Promise<void> = async()=>{}) {}
  reserveTraffic(count:number) { this.reserve=Math.max(0,count); }
  trafficModels(): string[] { return this.loaded ? [...TRAFFIC_MODELS] : []; }
  setTraffic(cars:readonly TrafficInstance[]) { this.traffic=cars; }
  setView(camera: THREE.Camera, night=0) { this.camera.copy(camera.position); camera.updateMatrixWorld();
    this.frustum.setFromProjectionMatrix(this.viewMatrix.multiplyMatrices(camera.projectionMatrix,camera.matrixWorldInverse),camera.coordinateSystem);this.hasView=true;
    fleetNight.value=Math.max(0,Math.min(1,night)); }
  renders(id:string) { return this.dynamic.has(id)||this.cars.some(c=>c.object_id===id); }
  private wheelDesigns(model:FleetModel,lod:number):WheelDesign[] {
    const key=`${model.id}/${lod}`,cached=this.designs.get(key);if(cached)return cached;
    const parts=this.authoredParts.get(key);
    const designs=parts?parts.wheels.map(w=>({key:w.name,pivot:w.pivot,sharedSide:false}))
      :lod===2?[]:Array.from({length:4},(_,i)=>({key:fleetWheelDesign(model,lod),sharedSide:true,
        pivot:{x:(i%2?-1:1)*(model.width/2-.075),y:model.wheel,z:(i<2?1:-1)*model.wheelbase/2}}));
    this.designs.set(key,designs);return designs;
  }
  private async authored(model:FleetModel,lod:number) {
    if(!embeddedWheels(model.id)||this.geometries.has(`${model.id}/${lod}`))return;
    const key=`${model.id}/${lod}`;
    const parts=lod<2?await loadAuthoredFleetParts(model.id,lod):null;
    const body=parts?.body??await loadAuthoredFleet(model.id,lod);
    if(body)this.geometries.set(key,body);
    if(parts){
      for(const wheel of parts.wheels){
        // Hash all baked vertex attributes, including material roles. Wheels of one exact
        // design share a draw; independently decimated LODs retain their own geometry.
        let hash=2166136261;
        for(const attribute of Object.values(wheel.geometry.attributes)){
          for(const value of attribute.array){hash=Math.imul(hash^Math.round(value*1e5),16777619);}
        }
        if(wheel.geometry.index)for(const value of wheel.geometry.index.array)hash=Math.imul(hash^value,16777619);
        wheel.name=`authored/${model.id}/${lod}/${hash>>>0}`;
        const geometryKey=`wheel-design/${wheel.name}`,existing=this.geometries.get(geometryKey);
        if(existing){wheel.geometry.dispose();wheel.geometry=existing;}else this.geometries.set(geometryKey,wheel.geometry);
      }
      this.authoredParts.set(key,parts);this.designs.delete(key);
    }
    const glass=await loadAuthoredFleet(model.id,lod,'glass');
    if(glass)this.geometries.set(`${key}/glass`,glass);
  }
  private geometry(model:FleetModel,lod:number) {
    const key=`${model.id}/${lod}`; let g=this.geometries.get(key);
    if(!g) { g=buildFleetGeometry(model,lod as 0|1|2,lod===2); this.geometries.set(key,g); } return g;
  }
  load(manifest:Manifest):Promise<void> {
    // Tile streaming can request another load while GPU preparation yields. Publish serially.
    const cars=(manifest.vehicles??[]).filter(c=>c.model!=='truck').map(c=>({...c}));
    const run=this.loading.catch(()=>{}).then(()=>this.loadNow(cars)); this.loading=run; return run;
  }
  private async loadNow(cars:ParkedVehicle[]) {
    const counts=new Map<string,number>();
    for(const car of cars) {
      const model=fleetModel(car.model,car.object_id);
      counts.set(model.id,(counts.get(model.id)??0)+1);
    }
    const models=this.reserve?FLEET:FLEET.filter(m=>counts.has(m.id));
    const replacements: [string,Batch][]=[];
    const wheelReplacements:[string,WheelBatch][]=[];
    const plans=new Map<string,{model:FleetModel;lod:number;count:number;reserve:number}>();
    for(const model of models)for(const lod of [0,1] as const)await this.authored(model,lod);
    for(const model of models)for(const lod of [0,1] as const){
      const uses=new Map<string,number>();
      for(const wheel of this.wheelDesigns(model,lod))uses.set(wheel.key,(uses.get(wheel.key)??0)+1);
      for(const [key,multiplicity] of uses){
        const plan=plans.get(key),count=(counts.get(model.id)??0)*multiplicity;
        if(plan){plan.count+=count;plan.reserve=Math.max(plan.reserve,multiplicity);}
        else plans.set(key,{model,lod,count,reserve:multiplicity});
      }
    }
    try {
      for(const [key,plan] of plans){
        const required=plan.count+this.reserve*plan.reserve;
        if((this.wheelGroups.get(key)?.mesh.instanceMatrix.count??0)>=required)continue;
        const capacity=Math.max(64,2**Math.ceil(Math.log2(Math.max(1,required))));
        const geometryKey=`wheel-design/${key}`;
        if(!this.geometries.has(geometryKey))this.geometries.set(geometryKey,buildFleetWheel(plan.model,plan.lod as 0|1,1));
        const mesh=new THREE.InstancedMesh(this.geometries.get(geometryKey)!,fleetMaterial(),capacity);
        // Empty batches hide (count 0); compileModel still prepares them (prepareHidden), shadow pass included.
        mesh.name=`wheels/${key}`;mesh.castShadow=plan.lod===0;mesh.receiveShadow=true;mesh.userData.prepareHidden=true;
        mesh.count=0;mesh.setMatrixAt(0,HIDDEN);paintInstances(mesh,[]);fleetLamps(mesh,[]);
        wheelReplacements.push([key,{mesh,count:0,dirty:true}]);await this.prepare(mesh);
      }
      for(const model of models) for(let lod=0;lod<3;lod++) {
        const key=`${model.id}/${lod}`, required=(counts.get(model.id)??0)+this.reserve;
        if((this.groups.get(key)?.mesh.instanceMatrix.count??0)>=required) continue;
        const capacity=Math.max(16,2**Math.ceil(Math.log2(Math.max(1,required))));
        if(!this.geometries.has(key)) {
          await this.authored(model,lod);
        }
        const mesh=new THREE.InstancedMesh(this.geometry(model,lod),fleetMaterial(),capacity);
        mesh.name=`parked/${key}`; mesh.castShadow=lod===0; mesh.receiveShadow=true; mesh.userData.prepareHidden=true;
        mesh.count=0; mesh.setMatrixAt(0,HIDDEN); paintInstances(mesh,[]); fleetLamps(mesh,[]);
        const glassGeometry=this.geometries.get(`${key}/glass`);
        const glass=glassGeometry?new THREE.InstancedMesh(glassGeometry,fleetGlassMaterial(),capacity):undefined;
        if(glass){glass.name=`parked-glass/${key}`;glass.instanceMatrix=mesh.instanceMatrix;glass.count=0;glass.receiveShadow=true;mesh.add(glass);}
        const batch={mesh,glass,model,lod,paints:[],lamps:[],indicators:[],brakes:[],count:0,dirty:true}; replacements.push([key,batch]);
        await this.prepare(mesh);
      }
    } catch(error) { for(const [,b] of replacements){b.glass?.dispose();b.mesh.dispose();} for(const [,b] of wheelReplacements)b.mesh.dispose(); throw error; }
    // Keep every old draw until ALL replacement materials and passes are ready.
    for(const [key,batch] of replacements) {
      const old=this.groups.get(key); if(old) {old.mesh.removeFromParent();old.glass?.dispose();old.mesh.dispose();}
      this.groups.set(key,batch); this.scene.add(batch.mesh);
    }
    for(const [key,batch] of wheelReplacements){
      const old=this.wheelGroups.get(key);if(old){old.mesh.removeFromParent();old.mesh.dispose();}
      this.wheelGroups.set(key,batch);this.scene.add(batch.mesh);
    }
    for(const car of cars) this.definitions.set(car.object_id,{model:fleetModel(car.model,car.object_id),color:car.color,size:car});
    const resident=new Set(cars.map(c=>c.object_id));
    for(const id of this.definitions.keys())if(!resident.has(id)&&!this.dynamic.has(id))this.definitions.delete(id);
    this.cars=cars.filter(c=>!this.dynamic.has(c.object_id)); this.loaded=true;
    void this.warmActors(models).catch(error=>console.warn(`[fleet] actor warmup: ${error}`));
  }
  /** Prepare a takeOver actor of each design ahead (see `keepers`); a design whose geometry is not built yet waits for
   *  its own load. */
  private async warmActors(models:readonly FleetModel[]) {
    for(const model of models) {
      if(this.keepers.has(model.id)||!this.geometries.has(`${model.id}/0`))continue;
      const keeper=this.actor(model,'silver',`keeper/${model.id}`);
      // Detached on purpose: the graphics queue prepares it even after the menu (render/graphicsPreparation.ts).
      keeper.userData.preparesDetached=true;
      this.keepers.set(model.id,keeper);
      await this.prepare(keeper);
    }
  }
  private actor(model:FleetModel,color:string,name:string) {
    const parts=this.authoredParts.get(`${model.id}/0`);
    const actor=new THREE.Mesh(this.geometry(model,0),fleetMaterial()); paintActor(actor,color);
    const glass=this.geometries.get(`${model.id}/0/glass`);
    if(glass){const window=new THREE.Mesh(glass,fleetGlassMaterial());window.receiveShadow=true;actor.add(window);}
    if(parts)addAuthoredFleetWheels(actor,model,parts.wheels,fleetMaterial());
    else addFleetWheels(actor,model,side=>{
      const wheelKey=`${model.id}/wheel/${side}`;
      if(!this.geometries.has(wheelKey))this.geometries.set(wheelKey,buildFleetWheel(model,0,side));
      return this.geometries.get(wheelKey)!;
    },fleetMaterial());
    actor.name=name;actor.castShadow=true;actor.receiveShadow=true;return actor;
  }
  takeOver(id:string) {
    if(this.dynamic.has(id))return;
    const def=this.definitions.get(id);if(!def)return;
    const actor=this.actor(def.model,def.color,`driven/${id}`);this.scene.add(actor);this.dynamic.set(id,actor);
    this.cars=this.cars.filter(c=>c.object_id!==id);
  }
  requestRemoteModel(key:string) {
    const model=fleetModel(key.split('/')[0],key), geometryKey=`${model.id}/0`;
    if((model.id!==AUTHORED_ESTATE&&!AUTHORED_BY_ID.has(model.id))||this.geometries.has(geometryKey)||this.remoteRequests.has(geometryKey))return;
    this.remoteRequests.set(geometryKey,this.authored(model,0)
      .catch(error=>console.error('Remote fleet asset failed',error)).finally(()=>this.remoteRequests.delete(geometryKey)));
  }
  remoteModel(key:string):THREE.Mesh|null {
    const [id,color]=key.split('/'), model=fleetModel(id,key);
    if((model.id===AUTHORED_ESTATE||AUTHORED_BY_ID.has(model.id))&&!this.geometries.has(`${model.id}/0`))return null;
    return this.actor(model,color??'silver',`remote/${key}`);
  }
  private transform(draw:Draw) {
    this.position.set(draw.pos.x,draw.pos.y-CHASSIS_LIFT,draw.pos.z);
    this.rotation.set(draw.rot.x,draw.rot.y,draw.rot.z,draw.rot.w).multiply(AXIS);
    // Existing slot/collider dimensions bound replacement vehicles; accessories are visual only.
    const {model,size}=draw;
    this.scale.set(size?size.width/model.width:1,size?size.height/model.height:1,size?size.length/model.length:1);
    return this.matrix.compose(this.position,this.rotation,this.scale);
  }
  private draw(draw:Draw) {
    const d=Math.hypot(draw.pos.x-this.camera.x,draw.pos.z-this.camera.z);
    if(d>320)return;
    this.sphere.center.set(draw.pos.x,draw.pos.y,draw.pos.z);
    if(this.hasView&&d>30&&!this.frustum.intersectsSphere(this.sphere))return;
    const lod=d<42?0:d<105?1:2, group=this.groups.get(`${draw.model.id}/${lod}`);
    if(!group||group.count>=group.mesh.instanceMatrix.count)return;
    const matrix=this.transform(draw),offset=group.count*16,array=group.mesh.instanceMatrix.array;
    let changed=false;
    for(let i=0;i<16;i++)if(array[offset+i]!==Math.fround(matrix.elements[i])){changed=true;break;}
    if(changed){group.mesh.setMatrixAt(group.count,matrix);group.dirty=true;}
    const designs=this.wheelDesigns(draw.model,lod);
    for(let i=0;i<designs.length;i++){
      const design=designs[i],wheelBatch=this.wheelGroups.get(design.key);if(!wheelBatch)continue;
      const side=i%2?-1:1,wheel=wheelBatch.mesh,index=wheelBatch.count++;
      if(index>=wheel.instanceMatrix.count)throw Error('Wheel design batch capacity exceeded');
      const state=draw.wheels?.[i],steer=state?.steer??(i<2?ackermann(draw.steer??0,draw.model.wheelbase,-side*(draw.model.width/2-.075)):0);
      this.hubPosition.set(design.pivot.x,(state||draw.wheelY)?((state?.pos.y??draw.wheelY![i])+CHASSIS_LIFT)/this.scale.y:design.pivot.y,design.pivot.z);
      const spin=state?.rotation??draw.spin??0;
      // Rotate the shared outward rim to the opposite side. Reverse its local spin
      // so all four tyres still roll forward, with the parked spoke phase preserved.
      this.hubEuler.set(design.sharedSide&&side<0?Math.PI-spin:spin,steer+(design.sharedSide&&side<0?Math.PI:0),0,'YXZ');this.hubRotation.setFromEuler(this.hubEuler);
      this.hubMatrix.compose(this.hubPosition,this.hubRotation,this.unitScale);
      this.wheelMatrix.multiplyMatrices(matrix,this.hubMatrix);
      const offset=index*16,array=wheel.instanceMatrix.array;
      for(let k=0;k<16;k++)if(array[offset+k]!==Math.fround(this.wheelMatrix.elements[k])){
        wheel.setMatrixAt(index,this.wheelMatrix);wheelBatch.dirty=true;break;
      }
    }
    group.paints[group.count]=draw.color;group.indicators[group.count]=draw.indicator??0;
    group.brakes[group.count]=draw.braking?1:0;group.lamps[group.count++]=draw.powered?1:0;
  }
  update(states:VehicleState[],rest?:ParkedRest) {
    const byId=new Map(states.map(s=>[s.id,s]));
    for(const b of this.groups.values()){b.count=0;b.dirty=false;}
    for(const b of this.wheelGroups.values()){b.count=0;b.dirty=false;}
    for(const car of this.cars) {
      const pose=byId.get(car.object_id)??rest?.restingPose(car); if(!pose)continue;
      const def=this.definitions.get(car.object_id)!;
      this.draw({id:car.object_id,...def,pos:pose.pos,rot:pose.rot,wheels:byId.get(car.object_id)?.wheels??pose.wheels});
    }
    for(let i=0;i<this.traffic.length;i++) {
      const car=this.traffic[i], id=car.seed??`${car.model}/${i}`;
      const key=car.model+'/'+id;
      let model=this.variants.get(key);
      if(!model){model=fleetModel(car.model,id);if(this.variants.size>512)this.variants.clear();this.variants.set(key,model);}
      const yaw=headingYaw(car.q);
      let motion=this.wheelMotion.get(id);
      if(!motion){motion={x:car.x,z:car.z,yaw,spin:0,steer:0};this.wheelMotion.set(id,motion);}
      const dx=car.x-motion.x,dz=car.z-motion.z,distance=Math.hypot(dx,dz);
      if(distance>.002&&distance<5){
        const signed=(dx*Math.cos(yaw)-dz*Math.sin(yaw))<0?-distance:distance;
        motion.spin+=wheelRotation(signed,model.wheel);
        const turn=Math.atan2(Math.sin(yaw-motion.yaw),Math.cos(yaw-motion.yaw));
        motion.steer=Math.max(-.65,Math.min(.65,Math.atan(model.wheelbase*turn/signed)));
      }else if(distance>=5){motion.spin=0;motion.steer=0;}
      motion.x=car.x;motion.z=car.z;motion.yaw=yaw;
      this.draw({id,model,wheelY:car.wheelY,spin:motion.spin,steer:motion.steer,color:car.color,pos:car,rot:car.q,powered:true,indicator:car.indicator,braking:car.braking});
    }
    const liveMotion=new Set(this.traffic.map((c,i)=>c.seed??`${c.model}/${i}`));
    for(const id of this.wheelMotion.keys())if(!liveMotion.has(id))this.wheelMotion.delete(id);
    for(const b of this.groups.values()) {
      const changedCount=b.mesh.count!==b.count;
      b.mesh.count=b.count;b.mesh.visible=b.count>0;
      if(b.glass)b.glass.count=b.count;
      if(!b.count)continue;
      b.paints.length=b.count;b.lamps.length=b.count;b.indicators.length=b.count;b.brakes.length=b.count;paintInstances(b.mesh,b.paints);fleetLamps(b.mesh,b.lamps,b.indicators,b.brakes);
      if(b.dirty)b.mesh.instanceMatrix.needsUpdate=true;
      if(b.dirty||changedCount){b.mesh.computeBoundingSphere();b.glass?.computeBoundingSphere();}
    }
    for(const b of this.wheelGroups.values()){
      const changedCount=b.mesh.count!==b.count;
      b.mesh.count=b.count;b.mesh.visible=b.count>0;
      if(b.dirty)b.mesh.instanceMatrix.needsUpdate=true;
      if(b.count&&(b.dirty||changedCount))b.mesh.computeBoundingSphere();
    }
    for(const [id,actor] of this.dynamic) {
      const state=byId.get(id);actor.visible=!!state;if(!state)continue;
      vehicleLamps(actor, 'car', state.lights ?? 0);
      actor.matrix.copy(this.transform({id,...this.definitions.get(id)!,pos:state.pos,rot:state.rot}));
      actor.matrix.decompose(actor.position,actor.quaternion,actor.scale);
      applyFleetWheels(actor,state.wheels);
    }
  }
  dispose() {
    for(const b of this.groups.values()){b.mesh.removeFromParent();b.glass?.dispose();b.mesh.dispose();}
    for(const b of this.wheelGroups.values()){b.mesh.removeFromParent();b.mesh.dispose();}
    for(const actor of this.dynamic.values())actor.removeFromParent();
    this.designs.clear();this.authoredParts.clear();this.groups.clear();this.wheelGroups.clear();this.dynamic.clear();this.definitions.clear();this.wheelMotion.clear();
    for(const geometry of this.geometries.values())geometry.dispose();this.geometries.clear();
  }
  snapshot() {
    const active=[...this.groups.values()].filter(b=>b.count),wheels=[...this.wheelGroups.values()].filter(b=>b.count);
    const triangles=(mesh:THREE.Mesh)=>(mesh.geometry.index?.count??mesh.geometry.getAttribute('position').count)/3;
    return {catalogue:FLEET.length,models:[...new Set(active.map(b=>b.model.id))],instances:active.reduce((s,b)=>s+b.count,0),draws:active.reduce((s,b)=>s+1+Number(!!b.glass),0)+wheels.length,
      lods:[0,1,2].map(lod=>active.filter(b=>b.lod===lod).reduce((s,b)=>s+b.count,0)),
      triangles:active.reduce((s,b)=>s+(triangles(b.mesh)+(b.glass?triangles(b.glass):0))*b.count,0)+wheels.reduce((s,b)=>s+triangles(b.mesh)*b.count,0)};
  }
  inspect(id:string,camera:THREE.Camera) {
    const actor=this.dynamic.get(id);if(!actor)return null;
    camera.updateMatrixWorld();actor.updateMatrixWorld();
    const frustum=new THREE.Frustum().setFromProjectionMatrix(new THREE.Matrix4().multiplyMatrices(camera.projectionMatrix,camera.matrixWorldInverse));
    const projected=actor.position.clone().project(camera);
    return {parented:actor.parent===this.scene,visible:actor.visible&&frustum.intersectsObject(actor),position:actor.position.toArray(),projected:projected.toArray(),castShadow:actor.castShadow,staticInstances:this.cars.filter(c=>c.object_id===id).length};
  }
}

if(import.meta.hot)import.meta.hot.accept();
