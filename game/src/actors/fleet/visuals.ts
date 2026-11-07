import { headingYaw } from '../../vehicle/math';
import * as THREE from 'three/webgpu';
import type { VehicleState, Quat, WheelState } from '../../physics/protocol';
import type { Manifest, ParkedVehicle } from '../../world/manifest';
import type { ParkedPose } from '../../world/parkedVehicles';
import { CHASSIS_LIFT, CLAIM_PREFIX } from '../../traffic/constants';
import { hazardOn, mechanicsOf, wrecked } from '../../vehicle/mechanics';
import { instancePaintOf, paintActor, paintInstances } from '../parkedPaint';
import { FALLBACK_MODELS, FLEET, FLEET_BY_ID, PLAYER_MODEL, SELECTABLE_FAMILIES, fallbackModel, fleetModel, type FleetModel } from './catalog';
import { loadAuthoredFleet, loadAuthoredFleetParts } from './authored';
import type {AuthoredFleetParts} from './authoredWheelParts';
import { AUTHORED_BY_ID } from './registry';
import { addAuthoredFleetWheels, applyFleetWheels } from './wheels';
import { vehicleLamps } from '../../vehicle/lamps';
import { fleetLamps, lampStateOf } from './lamps';
import { shareInstanceProgram } from '../../render/instancePrograms';
import { ackermann, wheelRotation } from '../../vehicle/wheelKinematics';
import { fleetDamageMaterial, fleetGlassDamageMaterial, fleetGlassMaterial, fleetMaterial, fleetNight } from './material';
import { DamageRig, damageBatch, modelLayout, rigOf, skinToCage, skinToCageAsync } from './damageRig';
import { shareParts } from './damageParts';
import { ownWheelBox, withoutOwnWheel } from '../driver/ownWheel';
import { AMBIENT_DISTANCE } from '../driver/ambient';

export interface ParkedRest { restingPose(car: ParkedVehicle): ParkedPose | null }
/** A drawn moving car. `model` is a real fleet model id (catalog.ts fleetModel picks it within the traffic family). */
export interface TrafficInstance { model: string; color: string; x: number; y: number; z: number; q: Quat; seed?: string; wheelY?: readonly number[]; indicator?: -1 | 0 | 1; braking?: boolean;
  /** A just-claimed car's body damage (traffic/client.ts draws it from its physics body until its record loads). */
  damage?: string }
interface WheelDesign {key:string;pivot:{x:number;y:number;z:number};sharedSide:boolean}
/** Plain fleet batches (parked and traffic bodies, their glazing and wheels) share one program per material, geometry
 *  layout and pass instead of one per model, LOD and wheel design (r186 keys every InstancedMesh by its uuid: ~450 of
 *  the ~740 programs a city.lab join built, 100 s of deferred builds on an M1 Air). Each draw binds its own instance
 *  matrices, previous matrices, paints and lamp states (render/instancePrograms.ts). Damaged batches keep their own. */
function shareBatchProgram(mesh:THREE.InstancedMesh){
  const paint=instancePaintOf(mesh),lamps=lampStateOf(mesh);
  if(!paint||!lamps||!(paint as THREE.InstancedBufferAttribute).isInstancedBufferAttribute)return;
  shareInstanceProgram(mesh,false,{moving:true,attributes:{paint:paint as THREE.InstancedBufferAttribute,lamps}});
}
interface WheelBatch { mesh:THREE.InstancedMesh; count:number; dirty:boolean }
interface Batch { mesh: THREE.InstancedMesh; glass?: THREE.InstancedMesh; model: FleetModel; lod: number; paints: string[]; lamps: number[]; indicators: number[]; brakes: number[]; count: number; dirty: boolean;
  /** A batch of damaged cars (damageRig.ts damageBatch): each instance's pool slot. */
  slots?: THREE.InterleavedBufferAttribute }
interface Draw { id: string; model: FleetModel; color: string; pos: {x:number;y:number;z:number}; rot: Quat; wheels?: readonly WheelState[]; wheelY?: readonly number[]; spin?: number; steer?: number; size?: ParkedVehicle; powered?: boolean; indicator?: -1 | 0 | 1 | 2; braking?: boolean;
  /** Body damage (vehicle/damage.ts encodeDamage): the car draws in its model's damaged batch. */
  damage?: string }
/** A damaged car drawn in a batch: its cage pose, and stand-ins for its body and glazing (never drawn) that carry its
 *  pose, paint and geometry for the rig's swing, debris and glass shards (damageRig.ts onDamage). */
interface Wreck { rig: DamageRig; body: THREE.Mesh; model: string }
/** Damaged batches start with room for this many cars of a model and grow by doubling. */
const WRECK_CAPACITY = 8;
const AXIS = new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0,1,0), Math.PI/2);
const HIDDEN = new THREE.Matrix4().makeScale(0,0,0);

/** Shared original parametric fleet. Near model/LODs share a body draw and pool wheel draws by tyre/rim design;
 * far cars retain one flattened draw. Paint and lamps do not add draws. Existing physics records remain authoritative. */
export class ParkedCarVisuals {
  private cars: ParkedVehicle[]=[];
  private groups=new Map<string,Batch>();
  /** Per model and LOD 0/1: the batch of its damaged parked cars, on the damage material (built after its cage). */
  private damaged=new Map<string,Batch>();
  private growing=new Set<string>();
  private wrecks=new Map<string,Wreck>();
  private hubOffset=new THREE.Vector3();
  private wheelGroups=new Map<string,WheelBatch>();
  private designs=new Map<string,WheelDesign[]>();
  private authoredParts=new Map<string,AuthoredFleetParts>();
  private geometries=new Map<string,THREE.BufferGeometry>();
  private dynamic=new Map<string,THREE.Mesh>();
  private definitions=new Map<string,{model:FleetModel;color:string;size?:ParkedVehicle}>();
  private failed=new Set<string>();
  /** Traffic model ids not in the catalog, warned about once each. */
  private unknown=new Set<string>();
  private traffic: readonly TrafficInstance[]=[];
  /** Moving traffic cars drawn this frame in view within AMBIENT_DISTANCE of the camera: their drawn model and its
   *  model-frame matrix (+Z forward, tyres on Y = 0), the seats of the ambient drivers (actors/driver/ambient.ts). */
  readonly trafficSeats:{model:string;matrix:THREE.Matrix4}[]=[];
  trafficSeatCount=0;
  private reserve=0;
  private camera=new THREE.Vector3();
  private frustum=new THREE.Frustum();
  private viewMatrix=new THREE.Matrix4();
  private sphere=new THREE.Sphere(new THREE.Vector3(),6);
  private hasView=false;
  private matrix=new THREE.Matrix4(); private position=new THREE.Vector3();
  private rotation=new THREE.Quaternion(); private scale=new THREE.Vector3(1,1,1);
  private loading: Promise<void>=Promise.resolve();
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
  setTraffic(cars:readonly TrafficInstance[]) { this.traffic=cars; }
  setView(camera: THREE.Camera, night=0) { this.camera.copy(camera.position); camera.updateMatrixWorld();
    this.frustum.setFromProjectionMatrix(this.viewMatrix.multiplyMatrices(camera.projectionMatrix,camera.matrixWorldInverse),camera.coordinateSystem);this.hasView=true;
    fleetNight.value=Math.max(0,Math.min(1,night)); }
  /** The appearance of parked car `id` is loaded (ParkedVehicles spawns bodies only for these). */
  has(id:string) { return this.definitions.has(id); }
  renders(id:string) { return this.dynamic.has(id)||this.cars.some(c=>c.object_id===id); }
  private wheelDesigns(model:FleetModel,lod:number):WheelDesign[] {
    const key=`${model.id}/${lod}`,cached=this.designs.get(key);if(cached)return cached;
    const parts=this.authoredParts.get(key);
    // LOD2 keeps its wheels merged into the far body; nearer LODs articulate their four authored hubs.
    const designs=parts?parts.wheels.map(w=>({key:w.name,pivot:w.pivot,sharedSide:false})):[];
    this.designs.set(key,designs);return designs;
  }
  private async authored(model:FleetModel,lod:number) {
    if(!AUTHORED_BY_ID.has(model.id)||this.failed.has(model.id)||this.geometries.has(`${model.id}/${lod}`))return;
    const key=`${model.id}/${lod}`;
    const parts=lod<2?await loadAuthoredFleetParts(model.id,lod):null;
    const body=parts?.body??await loadAuthoredFleet(model.id,lod);
    // The asset failed after its fetch retries (authored.ts warned once): the model is drawn as a fallback (resolve).
    if(!body){this.failed.add(model.id);return;}
    this.geometries.set(key,body);
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
    const g=this.geometries.get(`${model.id}/${lod}`);if(!g)throw Error(`Fleet model ${model.id} LOD${lod} is not loaded`);return g;
  }
  /** A model whose asset failed to load is drawn as one of the always-resident light fallback models. */
  private resolve(model:FleetModel,seed:string):FleetModel {
    return this.failed.has(model.id)?fallbackModel(seed):model;
  }
  /** A traffic car's model: a model id outside the catalog (a family name, a stale build's id) is drawn as a fallback
   *  model with one warning per id, never a throw in the frame loop. */
  private trafficModel(id:string,seed:string):FleetModel {
    const model=FLEET_BY_ID.get(id);if(model)return model;
    if(!this.unknown.has(id)){this.unknown.add(id);console.warn(`[fleet] traffic model "${id}" is not in the catalog; drawn as a fallback model`);}
    return fallbackModel(seed);
  }
  load(manifest:Manifest):Promise<void> {
    // Tile streaming can request another load while GPU preparation yields. Publish serially.
    const cars=(manifest.vehicles??[]).filter(c=>c.model!=='truck').map(c=>({...c}));
    const run=this.loading.catch(()=>{}).then(()=>this.loadNow(cars)); this.loading=run; return run;
  }
  private async loadNow(cars:ParkedVehicle[]) {
    const counts=new Map<string,number>();
    for(const car of cars) {
      const model=fleetModel(car.model,car.object_id,car);if(!model)continue;
      counts.set(model.id,(counts.get(model.id)??0)+1);
    }
    // Reserved traffic may draw any model of a traffic/parking family; special roles (bus, truck, ambulance) load
    // only when a record names them, so every client does not download them.
    // With traffic (the game) the fallback set and the starter car (remote players') are always resident.
    const alwaysLoaded=new Set<string>(this.reserve?[...FALLBACK_MODELS,PLAYER_MODEL]:[]);
    let models=FLEET.filter(m=>!this.failed.has(m.id)&&(counts.has(m.id)||alwaysLoaded.has(m.id)||this.reserve&&SELECTABLE_FAMILIES.has(m.family)));
    const replacements: [string,Batch][]=[];
    const wheelReplacements:[string,WheelBatch][]=[];
    const plans=new Map<string,{model:FleetModel;lod:number;count:number;reserve:number}>();
    for(const model of models)for(let lod=0;lod<3;lod++)await this.authored(model,lod);
    // The fallbacks and the starter car must load; any other model whose asset failed is drawn as a fallback.
    const required=[...alwaysLoaded].filter(id=>this.failed.has(id));
    if(required.length)throw Error(`Fleet model ${required.join(', ')} failed to load`);
    models=models.filter(m=>!this.failed.has(m.id));
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
        const mesh=new THREE.InstancedMesh(this.geometries.get(geometryKey)!,fleetMaterial(),capacity);
        // Empty batches hide (count 0); compileModel still prepares them (prepareHidden), shadow pass included.
        mesh.name=`wheels/${key}`;mesh.castShadow=plan.lod===0;mesh.receiveShadow=true;mesh.userData.prepareHidden=true;
        mesh.count=0;mesh.setMatrixAt(0,HIDDEN);paintInstances(mesh,[]);fleetLamps(mesh,[]);shareBatchProgram(mesh);
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
        mesh.count=0; mesh.setMatrixAt(0,HIDDEN); paintInstances(mesh,[]); fleetLamps(mesh,[]); shareBatchProgram(mesh);
        const glassGeometry=this.geometries.get(`${key}/glass`);
        const glass=glassGeometry?new THREE.InstancedMesh(glassGeometry,fleetGlassMaterial(),capacity):undefined;
        if(glass){glass.name=`parked-glass/${key}`;glass.instanceMatrix=mesh.instanceMatrix;glass.count=0;glass.receiveShadow=true;mesh.add(glass);shareInstanceProgram(glass,false,{moving:true});}
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
    // A slot no real model fits stays empty: no definition, so no draw and no body either (world/parkedVehicles.ts),
    // although ParkedVehicles.restingPose still gives a solo record its slot pose.
    const resident=new Set<string>();
    for(const car of cars){
      const model=fleetModel(car.model,car.object_id,car);
      if(model){this.definitions.set(car.object_id,{model,color:car.color,size:car});resident.add(car.object_id);}
    }
    for(const id of this.definitions.keys())if(!resident.has(id)&&!this.dynamic.has(id))this.definitions.delete(id);
    for(const [id,wreck] of this.wrecks)if(!this.definitions.has(id)||this.dynamic.has(id)){wreck.rig.release();this.wrecks.delete(id);}
    this.cars=cars.filter(c=>resident.has(c.object_id)&&!this.dynamic.has(c.object_id));
    void this.warmActors(models).catch(error=>console.warn(`[fleet] actor warmup: ${error}`));
  }
  /** Prepare a takeOver actor of each design ahead (see `keepers`); a design whose geometry is not built yet waits for
   *  its own load. */
  private async warmActors(models:readonly FleetModel[]) {
    for(const model of models) {
      if(this.keepers.has(model.id)||!this.geometries.has(`${model.id}/0`))continue;
      // Its damage cage weights first, in frame-sized phases (actor() would compute them in one go).
      const layout=modelLayout(model),glass=this.geometries.get(`${model.id}/0/glass`);
      await skinToCageAsync(this.geometry(model,0),layout);if(glass)await skinToCageAsync(glass,layout,'glass');
      if(this.keepers.has(model.id))continue;
      // LOD1 on the cage too: damaged parked cars keep their dents out to 105 m.
      const lod1=this.geometries.get(`${model.id}/1`),glass1=this.geometries.get(`${model.id}/1/glass`);
      if(lod1)await skinToCageAsync(lod1,layout);if(glass1)await skinToCageAsync(glass1,layout,'glass');
      for(const lod of [0,1])await this.damagedBatch(model,lod,WRECK_CAPACITY);
      const keeper=this.actor(model,'silver',`keeper/${model.id}`);
      // Detached on purpose: the graphics queue prepares it even after the menu (render/graphicsPreparation.ts).
      keeper.userData.preparesDetached=true;
      this.keepers.set(model.id,keeper);
      await this.prepare(keeper);
    }
  }
  /** The damaged batch of `model` at `lod` with room for `capacity` cars, prepared before it replaces a smaller one
   *  (until then the extra wrecks draw in the plain batch). */
  private async damagedBatch(model:FleetModel,lod:number,capacity:number) {
    const key=`${model.id}/${lod}`,body=this.geometries.get(key);
    if(!body?.getAttribute('skinIndex')||(this.damaged.get(key)?.mesh.instanceMatrix.count??0)>=capacity||this.growing.has(key))return;
    this.growing.add(key);
    try {
      const mesh=new THREE.InstancedMesh(body,fleetDamageMaterial(),capacity);
      mesh.name=`wrecks/${key}`;mesh.castShadow=lod===0;mesh.receiveShadow=true;mesh.userData.prepareHidden=true;
      mesh.count=0;mesh.setMatrixAt(0,HIDDEN);
      const glassGeometry=this.geometries.get(`${key}/glass`);
      const glass=glassGeometry?.getAttribute('skinIndex')?new THREE.InstancedMesh(glassGeometry,fleetGlassDamageMaterial(),capacity):undefined;
      if(glass){glass.name=`wrecks-glass/${key}`;glass.instanceMatrix=mesh.instanceMatrix;glass.count=0;glass.receiveShadow=true;mesh.add(glass);}
      // Paint and slot share one instanced buffer (damageRig.ts damageBatch), so register it before the paint.
      const slots=damageBatch(mesh,modelLayout(model),glass);paintInstances(mesh,[]);fleetLamps(mesh,[]);
      await this.prepare(mesh);
      if(this.disposed){glass?.dispose();mesh.dispose();return;}
      const old=this.damaged.get(key);if(old){old.mesh.removeFromParent();old.glass?.dispose();old.mesh.dispose();}
      this.damaged.set(key,{mesh,glass,model,lod,paints:[],lamps:[],indicators:[],brakes:[],count:0,dirty:true,slots});this.scene.add(mesh);
    } finally { this.growing.delete(key); }
  }
  private disposed=false;
  /** The cage pose and stand-ins of damaged car `id` (created on its first damaged frame). */
  private wreck(id:string,model:FleetModel,color:string):Wreck|null {
    let wreck=this.wrecks.get(id);
    if(wreck?.model===model.id)return wreck;
    wreck?.rig.release();
    const body=this.geometries.get(`${model.id}/0`);
    if(!body?.getAttribute('skinIndex'))return null;
    const rig=new DamageRig(modelLayout(model)),proxy=new THREE.Mesh(body);proxy.matrixAutoUpdate=false;proxy.name=`wreck/${id}`;
    paintActor(proxy,color);rig.bind(proxy);
    const glass=this.geometries.get(`${model.id}/0/glass`);
    if(glass?.getAttribute('skinIndex')){const window=new THREE.Mesh(glass);window.matrixAutoUpdate=false;proxy.add(window);rig.bind(window);}
    wreck={rig,body:proxy,model:model.id};this.wrecks.set(id,wreck);return wreck;
  }
  /** The damaged batches in use, for the developer console: instances, visibility and the first one's place and slot. */
  wreckBatches() {
    const m=new THREE.Matrix4(),p=new THREE.Vector3();
    return [...this.damaged].filter(([,b])=>b.mesh.count).map(([key,b])=>{b.mesh.getMatrixAt(0,m);p.setFromMatrixPosition(m);
      return {key,count:b.mesh.count,visible:b.mesh.visible,inScene:!!b.mesh.parent,first:p.toArray().map(v=>Math.round(v*10)/10),slot:b.slots?.getX(0),
        sphere:b.mesh.boundingSphere?[...b.mesh.boundingSphere.center.toArray().map(v=>Math.round(v)),Math.round(b.mesh.boundingSphere.radius)]:null,
        glass:b.glass?.count??null};});
  }
  /** Damaged cars drawn in batches (parked wrecks), for the developer console: their rigs and stand-in bodies. */
  wreckBodies():{id:string;model:string;body:THREE.Mesh;rig:DamageRig}[] { return [...this.wrecks].map(([id,w])=>({id,...w})); }
  /** Frame-cost bench (developer console): the `count` parked cars nearest `at` draw with damage state `wire` (visual
   *  only: no physics or room state changes); count 0 ends it. Returns how many. */
  benchWrecks(count:number,wire:string|null,at:{x:number;z:number}) {
    this.bench.clear();
    if(!count||!wire)return 0;
    const near=[...this.cars].sort((a,b)=>Math.hypot(a.x-at.x,a.z-at.z)-Math.hypot(b.x-at.x,b.z-at.z)).slice(0,count);
    for(const car of near)this.bench.set(car.object_id,wire);
    return near.length;
  }
  private bench=new Map<string,string>();
  /** A car drawn on its own (the driven car, a remote player's car): body and glazing follow the model's damage cage
   *  (damageRig.ts), so a damage state from physics or the network shows on it (showFleetDamage). */
  private actor(model:FleetModel,color:string,name:string) {
    const parts=this.authoredParts.get(`${model.id}/0`), layout=modelLayout(model), rig=new DamageRig(layout);
    const actor=new THREE.Mesh(this.actorBody(model,layout),fleetDamageMaterial()); paintActor(actor,color);rig.bind(actor);
    const glass=this.geometries.get(`${model.id}/0/glass`);
    if(glass){skinToCage(glass,layout,'glass');const window=new THREE.Mesh(glass,fleetGlassDamageMaterial());window.receiveShadow=true;actor.add(window);rig.bind(window);}
    if(parts)addAuthoredFleetWheels(actor,model,parts.wheels,fleetMaterial());

    actor.name=name;actor.castShadow=true;actor.receiveShadow=true;return actor;
  }
  /** The body of a car drawn on its own: LOD0 on the damage cage, without the model's own steering wheel where its
   *  seated driver draws a turning one in its place (actors/driver/ownWheel.ts); once per model. */
  private actorBody(model:FleetModel,layout:ReturnType<typeof modelLayout>) {
    const body=this.geometry(model,0);skinToCage(body,layout);
    const box=ownWheelBox(model.id);if(!box)return body;
    const key=`${model.id}/0/actor`;let actor=this.geometries.get(key);
    if(!actor){actor=withoutOwnWheel(body,box);shareParts(body,actor);this.geometries.set(key,actor);}
    return actor;
  }
  /** Draws vehicle `id` as its own actor from now on, at the pose `update` is given (the driven car's render pose).
   *  `record`: its vehicle record, for a car whose batch load has not caught up yet (a just-claimed traffic car):
   *  without it the car would stay drawn from its raw physics body (traffic/client.ts) until that load. */
  takeOver(id:string,record?:ParkedVehicle) {
    if(this.dynamic.has(id))return;
    // A parked wreck driven away draws on its own from now on: its batch rig gives its slot back.
    this.wrecks.get(id)?.rig.release();this.wrecks.delete(id);
    if(!this.definitions.has(id)&&record){const model=fleetModel(record.model,record.object_id,record);if(model)this.definitions.set(id,{model,color:record.color,size:record});}
    const def=this.definitions.get(id);if(!def)return;
    const model=this.resolve(def.model,id);
    if(this.geometries.has(`${model.id}/0`)){const actor=this.actor(model,def.color,`driven/${id}`);this.scene.add(actor);this.dynamic.set(id,actor);}
    else {
      // A just-claimed car whose model is not resident yet: an empty actor holds its pose (update) until the model
      // loads, then the real model takes its place.
      const holder=new THREE.Mesh();holder.name=`driven/${id}`;this.scene.add(holder);this.dynamic.set(id,holder);
      void this.authored(model,0).then(()=>{
        if(this.dynamic.get(id)!==holder)return;
        // An asset that failed meanwhile is drawn as a resident fallback model.
        const actor=this.actor(this.resolve(model,id),def.color,`driven/${id}`);actor.matrix.copy(holder.matrix);actor.matrixAutoUpdate=holder.matrixAutoUpdate;
        actor.position.copy(holder.position);actor.quaternion.copy(holder.quaternion);
        holder.removeFromParent();this.scene.add(actor);this.dynamic.set(id,actor);
      }).catch(error=>{console.error(`Fleet model ${model.id} failed to load`,error);this.failed.add(model.id);});
    }
    this.cars=this.cars.filter(c=>c.object_id!==id);
  }
  /** Driven car `id`'s own actor in the fleet model frame and its drawn model id, once its real model is resident
   *  (the seated driver's parent, actors/driver). */
  driven(id:string):{actor:THREE.Object3D;model:string}|null {
    const actor=this.dynamic.get(id),def=this.definitions.get(id);
    if(!actor||!def||!(actor.geometry as THREE.BufferGeometry|undefined)?.getAttribute('position'))return null;
    return {actor,model:this.resolve(def.model,id).id};
  }
  requestRemoteModel(key:string) {
    const model=fleetModel(key.split('/')[0],key), geometryKey=`${model.id}/0`;
    if(!AUTHORED_BY_ID.has(model.id)||this.geometries.has(geometryKey)||this.remoteRequests.has(geometryKey))return;
    this.remoteRequests.set(geometryKey,this.authored(model,0)
      .catch(error=>console.error('Remote fleet asset failed',error)).finally(()=>this.remoteRequests.delete(geometryKey)));
  }
  remoteModel(key:string):THREE.Mesh|null {
    const [id,color]=key.split('/'), model=fleetModel(id,key);
    if(!this.geometries.has(`${model.id}/0`))return null;
    return this.actor(model,color??'silver',`remote/${key}`);
  }
  private transform(draw:Draw) {
    this.position.set(draw.pos.x,draw.pos.y-CHASSIS_LIFT,draw.pos.z);
    this.rotation.set(draw.rot.x,draw.rot.y,draw.rot.z,draw.rot.w).multiply(AXIS);
    // Real models keep their true size; a parking slot only receives a model that fits (catalog.ts fitsSlot).
    this.scale.set(1,1,1);
    return this.matrix.compose(this.position,this.rotation,this.scale);
  }
  /** Draws one car; returns its drawn model (this.matrix then holds its transform), or null when it is not drawn. */
  /** Beyond 320 m, or off screen beyond 30 m, a car is not drawn; null then, else its camera distance. */
  private seen(pos:{x:number;y:number;z:number}):number|null {
    const d=Math.hypot(pos.x-this.camera.x,pos.z-this.camera.z);
    if(d>320)return null;
    this.sphere.center.set(pos.x,pos.y,pos.z);
    if(this.hasView&&d>30&&!this.frustum.intersectsSphere(this.sphere))return null;
    return d;
  }
  private draw(draw:Draw):FleetModel|null {
    const d=this.seen(draw.pos);
    if(d===null)return null;
    draw={...draw,model:this.resolve(draw.model,draw.id)};
    const lod=d<42?0:d<105?1:2;
    const matrix=this.transform(draw);
    // A damaged car draws in its model's damaged batch while it holds a pool slot (within 105 m); its rig follows the
    // state every frame it is drawn (loose parts swing) and its hubs move with the cage.
    let group=this.groups.get(`${draw.model.id}/${lod}`),wreck:Wreck|null=null;
    // Beyond LOD1 a wreck draws plain and leaves its slot to nearer ones.
    if(draw.damage&&lod<2){
      wreck=this.wreck(draw.id,draw.model,draw.color);
      if(wreck){
        wreck.body.matrix.copy(matrix);wreck.body.matrixWorld.copy(matrix);wreck.rig.seenAt=performance.now();
        wreck.rig.show(draw.damage);
        const key=`${draw.model.id}/${lod}`,damaged=wreck.rig.slot?this.damaged.get(key):undefined;
        if(damaged&&damaged.count<damaged.mesh.instanceMatrix.count)group=damaged;
        else if(damaged)void this.damagedBatch(draw.model,lod,damaged.mesh.instanceMatrix.count*2).catch(error=>console.warn(`[fleet] wreck batch: ${error}`));
        if(!wreck.rig.state.damaged)wreck=null;
      }
    }
    if(!group||group.count>=group.mesh.instanceMatrix.count)return null;
    if(group.slots&&group.slots.getX(group.count)!==wreck!.rig.slot){group.slots.setX(group.count,wreck!.rig.slot);group.slots.needsUpdate=true;}
    const offset=group.count*16,array=group.mesh.instanceMatrix.array;
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
      if(wreck){wreck.rig.offsetAt(design.pivot.x,design.pivot.y,design.pivot.z,this.hubOffset);this.hubPosition.x+=this.hubOffset.x;this.hubPosition.z+=this.hubOffset.z;}
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
    return draw.model;
  }
  update(states:VehicleState[],rest?:ParkedRest) {
    const byId=new Map(states.map(s=>[s.id,s]));
    for(const b of this.groups.values()){b.count=0;b.dirty=false;}
    for(const b of this.damaged.values()){b.count=0;b.dirty=false;}
    for(const b of this.wheelGroups.values()){b.count=0;b.dirty=false;}
    this.trafficSeatCount=0;
    for(const car of this.cars) {
      const state=byId.get(car.object_id),pose=state??rest?.restingPose(car); if(!pose)continue;
      // Every parked car of every resident tile passes here each frame: cull before building its draw (two object
      // spreads and lookups per car were 3 % of the M1 Max main thread on a Turku drive, frame-mp mac-drive-1).
      if(this.seen(pose.pos)===null)continue;
      const def=this.definitions.get(car.object_id)!;
      const damage=this.bench.get(car.object_id)??(state?state.damage:pose.damage);
      // A claimed traffic car too damaged to drive stands with its hazard lights on (vehicle/mechanics.ts wrecked).
      const hazard=!!damage&&car.object_id.startsWith(CLAIM_PREFIX)&&wrecked(mechanicsOf(damage));
      this.draw({id:car.object_id,...def,pos:pose.pos,rot:pose.rot,wheels:state?.wheels??pose.wheels,damage,
        ...(hazard?{powered:true,indicator:hazardOn()?2:0}:{})});
    }
    for(let i=0;i<this.traffic.length;i++) {
      const car=this.traffic[i], id=car.seed??`${car.model}/${i}`;
      const model=this.trafficModel(car.model,id);
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
      // A just-claimed car already shows its dents (and its hazards when wrecked), keyed like its record.
      const hazard=!!car.damage&&wrecked(mechanicsOf(car.damage));
      const drawn=this.draw({id,model,wheelY:car.wheelY,spin:motion.spin,steer:motion.steer,color:car.color,pos:car,rot:car.q,powered:true,
        indicator:hazard?(hazardOn()?2:0):car.indicator,braking:car.braking,...(car.damage?{damage:car.damage}:{})});
      if(drawn&&this.hasView&&Math.hypot(car.x-this.camera.x,car.y-this.camera.y,car.z-this.camera.z)<AMBIENT_DISTANCE&&this.frustum.intersectsSphere(this.sphere)){
        let seat=this.trafficSeats[this.trafficSeatCount];
        if(!seat)this.trafficSeats.push(seat={model:'',matrix:new THREE.Matrix4()});
        seat.model=drawn.id;seat.matrix.copy(this.matrix);this.trafficSeatCount++;
      }
    }
    const liveMotion=new Set(this.traffic.map((c,i)=>c.seed??`${c.model}/${i}`));
    for(const id of this.wheelMotion.keys())if(!liveMotion.has(id))this.wheelMotion.delete(id);
    for(const b of [...this.groups.values(),...this.damaged.values()]) {
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
      showFleetDamage(actor,state.damage);
    }
  }
  dispose() {
    this.disposed=true;
    for(const b of [...this.groups.values(),...this.damaged.values()]){b.mesh.removeFromParent();b.glass?.dispose();b.mesh.dispose();}
    for(const w of this.wrecks.values())w.rig.release();this.wrecks.clear();this.damaged.clear();
    for(const b of this.wheelGroups.values()){b.mesh.removeFromParent();b.mesh.dispose();}
    for(const actor of this.dynamic.values())actor.removeFromParent();
    this.designs.clear();this.authoredParts.clear();this.groups.clear();this.wheelGroups.clear();this.dynamic.clear();this.definitions.clear();this.wheelMotion.clear();
    for(const geometry of this.geometries.values())geometry.dispose();this.geometries.clear();
  }
  snapshot() {
    const active=[...this.groups.values(),...this.damaged.values()].filter(b=>b.count),wheels=[...this.wheelGroups.values()].filter(b=>b.count);
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

/** Shows a damage state on a car drawn by `actor` (after applyFleetWheels: the hubs follow the cage). */
export function showFleetDamage(actor:THREE.Object3D,damage:string|undefined) {
  const rig=rigOf(actor);
  if(!rig)return;
  rig.show(damage);rig.placeWheels(actor);
}

if(import.meta.hot)import.meta.hot.accept();
