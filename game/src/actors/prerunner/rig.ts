import * as T from 'three/webgpu';
import { Fn, attribute, instancedBufferAttribute, positionGeometry, vec3, vec4, buffer, instanceIndex, element } from 'three/tsl';
import type Node from 'three/src/nodes/core/Node.js';
import type ArrayNode from 'three/src/nodes/core/ArrayNode.js';
import type NodeBuilder from 'three/src/nodes/core/NodeBuilder.js';
import type { VehicleState } from '../../physics/protocol';
import { TRUCK } from '../../physics/constants';
import { racerMaterials } from './materials';
import { v } from './geometry';
import { shockTop, wishbonePivot, uprightJoint } from './hardpoints';

type Wheel = Pick<VehicleState['wheels'][number], 'pos' | 'steer' | 'rotation'>;
const UP=v(0,1,0), direction=v(0,0,0), middle=v(0,0,0), scale=v(1,1,1), rotation=new T.Quaternion(), matrix=new T.Matrix4();
const lengths=new WeakMap<T.Object3D,T.InstancedBufferAttribute>();
const rigs=new WeakMap<T.Object3D,RaceRig>();
let cylinder:T.BufferGeometry, spring:T.BufferGeometry, caliper:T.BufferGeometry, springMaterial:T.MeshStandardNodeMaterial;

function templates(){
  if(cylinder)return;
  cylinder=new T.CylinderGeometry(1,1,1,8);caliper=new T.BoxGeometry(1,1,1);
  const points:T.Vector3[]=[];for(let i=0;i<=192;i++){const a=i/192*Math.PI*27;points.push(v(Math.cos(a)*.080,i/192-.5,Math.sin(a)*.080));}
  spring=new T.TubeGeometry(new T.CatmullRomCurve3(points),192,.013,6,false);
  // Store the helix centreline separately from the wire cross-section. Inverse Y correction
  // cancels the instance's axial scaling for the wire; only the spring pitch changes.
  const axis=new Float32Array(spring.attributes.position.count);
  for(let i=0;i<axis.length;i++)axis[i]=Math.floor(i/7)/192-.5;
  spring.setAttribute('springAxis',new T.BufferAttribute(axis,1));
  const extension=Fn((builder:NodeBuilder)=>instancedBufferAttribute(lengths.get(builder.object)!,'float')) as unknown as ()=>Node<'float'>;
  const material=racerMaterials().orange;
  springMaterial=new T.MeshStandardNodeMaterial({name:'korpi-coil-wire',color:material.color,roughness:.43,metalness:0});
  const y=attribute('springAxis','float');
  const instanceTransform=Fn((builder:NodeBuilder)=>element(buffer((builder.object as T.InstancedMesh).instanceMatrix.array,'mat4',(builder.object as T.InstancedMesh).count) as unknown as ArrayNode<'mat4'>,instanceIndex)) as unknown as ()=>Node<'mat4'>;
  // positionNode executes AFTER Three's normal instancing transform. Read raw geometry
  // coordinates and explicitly apply the instance matrix once, rather than deforming chassis-space Y.
  const local=vec3(positionGeometry.x,positionGeometry.y.sub(y).div(extension()).add(y),positionGeometry.z);
  springMaterial.positionNode=instanceTransform().mul(vec4(local,1)).xyz;
}
function instanced(name:string,geometry:T.BufferGeometry,material:T.Material,count:number){
  const mesh=new T.InstancedMesh(geometry,material,count);mesh.name=name;mesh.boundingSphere=new T.Sphere(v(0,-.3,0),3.4);
  // Explicit needsUpdate after a pose change; StaticDrawUsage avoids r186's unconditional per-frame upload.
  mesh.castShadow=true;mesh.receiveShadow=true;return mesh;
}
function segment(mesh:T.InstancedMesh,index:number,a:T.Vector3,b:T.Vector3,radius:number,length?:number){
  direction.subVectors(b,a);const distance=direction.length();middle.copy(a).addScaledVector(direction,.5);
  rotation.setFromUnitVectors(UP,direction.multiplyScalar(1/Math.max(.0001,distance)));
  scale.set(radius,length??distance,radius);matrix.compose(middle,rotation,scale);mesh.setMatrixAt(index,matrix);
}
export function createRaceRig(){
  templates();const m=racerMaterials(),root=new T.Group();root.name='korpi-suspension';
  root.add(instanced('korpi-links',cylinder,m.frame,24),instanced('korpi-dampers',cylinder,m.shaft,60),instanced('korpi-fittings',cylinder,m.orange,32),instanced('korpi-springs',spring,springMaterial,4),instanced('korpi-calipers',caliper,m.orange,4));
  return root;
}

/** Per-instance bindings are re-created for a scene clone; all peers use the same physical wheel pose. */
class RaceRig {
  readonly links:T.InstancedMesh;
  readonly dampers:T.InstancedMesh;
  readonly fittings:T.InstancedMesh;
  readonly springs:T.InstancedMesh;
  readonly calipers:T.InstancedMesh;
  readonly springLengths=new T.InstancedBufferAttribute(new Float32Array(4).fill(1),1);
  readonly wheels:T.Object3D[];
  private wheelBatches:T.InstancedMesh[]=[];
  private detail:T.Object3D;
  private previous=new Float64Array(20).fill(NaN);
  private pendingDetail=true;
  private top=v(0,0,0);private bottom=v(0,0,0);private end=v(0,0,0);private start=v(0,0,0);
  private lower=v(0,0,0);private upper=v(0,0,0);private pivot=v(0,0,0);private axis=v(0,0,0);
  private barrelEnd=v(0,0,0);private bypassTop=v(0,0,0);private bypassBottom=v(0,0,0);
  private wheelRotation=new T.Quaternion();private steerRotation=new T.Quaternion();
  private spinRotation=new T.Quaternion();private zero=v(0,0,0);private one=v(1,1,1);
  constructor(root:T.Object3D){
    this.links=root.getObjectByName('korpi-links') as T.InstancedMesh;
    this.dampers=root.getObjectByName('korpi-dampers') as T.InstancedMesh;
    this.fittings=root.getObjectByName('korpi-fittings') as T.InstancedMesh;
    this.springs=root.getObjectByName('korpi-springs') as T.InstancedMesh;
    this.calipers=root.getObjectByName('korpi-calipers') as T.InstancedMesh;
    this.detail=root.getObjectByName('korpi-suspension')!;
    this.wheels=TRUCK.wheels.map((_,i)=>root.getObjectByName(`vehicle-wheel-${i}`)!);
    root.traverse(object=>{if(object.name.startsWith('korpi-wheel-batch-'))this.wheelBatches.push(object as T.InstancedMesh);});
    lengths.set(this.springs,this.springLengths);
    this.wheelRotation.setFromEuler(new T.Euler(-Math.PI/2,.24,0));
    matrix.compose(v(-1.75,.19,0),this.wheelRotation,this.one);
    for(const mesh of this.wheelBatches)mesh.setMatrixAt(4,matrix);
  }
  update(wheels:readonly Wheel[]){
    let changed=false;
    wheels.forEach((w,i)=>{
      const values=[w.pos.x,w.pos.y,w.pos.z,w.steer,w.rotation];
      for(let j=0;j<5;j++){if(this.previous[i*5+j]!==values[j])changed=true;this.previous[i*5+j]=values[j];}
    });
    if(changed){
      wheels.forEach((w,i)=>{
        const g=this.wheels[i];if(!g)return;
        g.position.set(w.pos.x,w.pos.y,w.pos.z);g.rotation.set(0,w.steer,0);g.children[0].rotation.z=-w.rotation;
        const side=Math.sign(TRUCK.wheels[i].z);
        this.steerRotation.setFromAxisAngle(UP,w.steer+(side<0?Math.PI:0));this.spinRotation.setFromAxisAngle(this.zero.set(0,0,1),-w.rotation*side);
        this.wheelRotation.copy(this.steerRotation).multiply(this.spinRotation);
        matrix.compose(g.position,this.wheelRotation,this.one);
        for(const mesh of this.wheelBatches)mesh.setMatrixAt(i,matrix);
      });
      for(const mesh of this.wheelBatches)mesh.instanceMatrix.needsUpdate=true;
    }
    if(!this.detail.visible){this.pendingDetail ||= changed;return;}
    if(!changed&&!this.pendingDetail)return;
    this.pendingDetail=false;
    wheels.forEach((w,i)=>{
      const at=TRUCK.wheels[i],side=Math.sign(at.z),base=i*15;
      uprightJoint(w.pos,side,false,w.steer,this.lower);uprightJoint(w.pos,side,true,w.steer,this.upper);
      for(let j=0;j<4;j++){
        wishbonePivot(at,j>=2,j%2===0,this.pivot);
        const joint=j>=2?this.upper:this.lower;
        this.axis.subVectors(joint,this.pivot).normalize();this.start.copy(joint).addScaledVector(this.axis,-.65);
        segment(this.links,i*6+j,this.start,joint,.030);
        // The short inner sleeve absorbs the vertical raycast model's linkage approximation.
        segment(this.dampers,base+8+j,this.pivot,this.start,.022);
      }
      segment(this.links,i*6+4,this.lower,this.upper,.043);
      this.end.set(w.pos.x,w.pos.y,w.pos.z);segment(this.links,i*6+5,this.lower,this.end,.065);
      shockTop(at,this.top);
      this.pivot.set(at.x,-.39,side*.43);this.bottom.copy(this.pivot).lerp(this.lower,.70);
      this.bottom.x-=Math.sign(at.x)*.07;
      this.axis.subVectors(this.bottom,this.top).normalize();this.barrelEnd.copy(this.top).addScaledVector(this.axis,.78);
      segment(this.dampers,base,this.top,this.barrelEnd,.045);
      segment(this.dampers,base+1,this.barrelEnd,this.bottom,.019);
      this.start.copy(this.top).lerp(this.bottom,.06);this.end.copy(this.top).lerp(this.bottom,.80);
      const springLength=this.start.distanceTo(this.end);
      segment(this.springs,i,this.start,this.end,1);this.springLengths.setX(i,Math.max(.1,springLength));
      // Spring seats remain perpendicular to the damper, with real endpoints at both eyes.
      for(const [slot,point] of [[2,this.start],[3,this.end]] as const){
        this.pivot.copy(point).addScaledVector(this.axis,-.012);this.barrelEnd.copy(point).addScaledVector(this.axis,.012);
        segment(this.fittings,i*8+slot,this.pivot,this.barrelEnd,.092);
      }
      this.bypassTop.copy(this.top);this.bypassTop.x-=.17;
      this.bypassBottom.copy(this.bottom);this.bypassBottom.x-=.12;
      this.axis.subVectors(this.bypassBottom,this.bypassTop).normalize();this.barrelEnd.copy(this.bypassTop).addScaledVector(this.axis,.76);
      segment(this.dampers,base+2,this.bypassTop,this.barrelEnd,.042);
      segment(this.dampers,base+3,this.barrelEnd,this.bypassBottom,.019);
      // External bypass plumbing is fixed to the barrel rather than stretching with the shaft.
      this.start.copy(this.bypassTop).addScaledVector(this.axis,.12);this.start.z+=side*.06;
      this.end.copy(this.bypassTop).addScaledVector(this.axis,.63);this.end.z+=side*.06;
      segment(this.dampers,base+5,this.start,this.end,.012);
      this.start.x-=.04;this.end.x-=.04;segment(this.dampers,base+6,this.start,this.end,.010);
      this.start.copy(this.bypassTop);this.start.x-=.12;
      this.end.copy(this.start).addScaledVector(this.axis,.31);segment(this.dampers,base+4,this.start,this.end,.052);
      this.pivot.copy(this.bypassTop).addScaledVector(this.axis,.06);this.end.copy(this.start);this.end.y-=.04;
      segment(this.dampers,base+13,this.pivot,this.end,.014);
      this.pivot.copy(this.end);this.end.copy(this.start).addScaledVector(this.axis,.12);segment(this.dampers,base+14,this.pivot,this.end,.014);
      this.start.set(at.x,-.28,0);this.end.set(w.pos.x,w.pos.y,w.pos.z-side*.17);
      segment(this.dampers,base+7,this.start,this.end,.036);
      const armX=-.17,armZ=-side*.13;
      this.end.set(w.pos.x+Math.cos(w.steer)*armX+Math.sin(w.steer)*armZ,w.pos.y+.06,w.pos.z-Math.sin(w.steer)*armX+Math.cos(w.steer)*armZ);
      this.pivot.set(at.x-.30,-.23,side*.40);segment(this.dampers,base+12,this.pivot,this.end,.018);
      for(const [slot,point] of [[0,this.top],[1,this.bottom],[4,this.bypassTop],[5,this.bypassBottom]] as const){
        this.start.copy(point);this.end.copy(point);this.start.z-=.055;this.end.z+=.055;
        segment(this.fittings,i*8+slot,this.start,this.end,.035);
      }
      this.start.set(at.x,-.28,side*.05);this.end.set(at.x,-.28,side*.16);segment(this.fittings,i*8+6,this.start,this.end,.066);
      this.start.set(w.pos.x,w.pos.y,w.pos.z-side*.20);this.end.set(w.pos.x,w.pos.y,w.pos.z-side*.12);segment(this.fittings,i*8+7,this.start,this.end,.066);
      this.start.set(w.pos.x-.13*Math.cos(w.steer),w.pos.y+.09,w.pos.z+side*.10+.13*Math.sin(w.steer));
      this.steerRotation.setFromAxisAngle(UP,w.steer);matrix.compose(this.start,this.steerRotation,scale.set(.10,.18,.09));this.calipers.setMatrixAt(i,matrix);
    });
    for(const mesh of [this.links,this.dampers,this.fittings,this.springs,this.calipers])mesh.instanceMatrix.needsUpdate=true;
    this.springLengths.needsUpdate=true;
  }
}

export function applyRaceWheels(root:T.Object3D,wheels:readonly Wheel[]){
  let rig=rigs.get(root);if(!rig){if(!root.getObjectByName('korpi-links'))return;rig=new RaceRig(root);rigs.set(root,rig);}
  rig.update(wheels);
}

if(import.meta.hot)import.meta.hot.dispose(()=>{
  cylinder?.dispose();spring?.dispose();caliper?.dispose();springMaterial?.dispose();
});
