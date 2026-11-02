import * as T from 'three/webgpu';
import { Fn, attribute, instancedBufferAttribute, positionLocal, vec3 } from 'three/tsl';
import type Node from 'three/src/nodes/core/Node.js';
import type NodeBuilder from 'three/src/nodes/core/NodeBuilder.js';
import type { VehicleState } from '../../physics/protocol';
import { TRUCK } from '../../physics/constants';
import { racerMaterials } from './materials';
import { v } from './geometry';

type Wheel = Pick<VehicleState['wheels'][number], 'pos' | 'steer' | 'rotation'>;
const UP=v(0,1,0), direction=v(0,0,0), middle=v(0,0,0), scale=v(1,1,1), rotation=new T.Quaternion(), matrix=new T.Matrix4();
const lengths=new WeakMap<T.Object3D,T.InstancedBufferAttribute>();
const rigs=new WeakMap<T.Object3D,RaceRig>();
let cylinder:T.BufferGeometry, spring:T.BufferGeometry, springMaterial:T.MeshStandardNodeMaterial;

function templates(){
  if(cylinder)return;
  cylinder=new T.CylinderGeometry(1,1,1,8);
  const points:T.Vector3[]=[];for(let i=0;i<=128;i++){const a=i/128*Math.PI*18;points.push(v(Math.cos(a)*.069,i/128-.5,Math.sin(a)*.069));}
  spring=new T.TubeGeometry(new T.CatmullRomCurve3(points),128,.011,6,false);
  // Store the helix centreline separately from the wire cross-section. Inverse Y correction
  // cancels the instance's axial scaling for the wire; only the spring pitch changes.
  const axis=new Float32Array(spring.attributes.position.count);
  for(let i=0;i<axis.length;i++)axis[i]=Math.floor(i/7)/128-.5;
  spring.setAttribute('springAxis',new T.BufferAttribute(axis,1));
  const extension=Fn((builder:NodeBuilder)=>instancedBufferAttribute(lengths.get(builder.object)!,'float')) as unknown as ()=>Node<'float'>;
  const material=racerMaterials().orange;
  springMaterial=new T.MeshStandardNodeMaterial({name:'korpi-coil-wire',color:material.color,roughness:.43,metalness:0});
  const y=attribute('springAxis','float');
  springMaterial.positionNode=vec3(positionLocal.x,positionLocal.y.sub(y).div(extension()).add(y),positionLocal.z);
}
function instanced(name:string,geometry:T.BufferGeometry,material:T.Material,count:number){
  const mesh=new T.InstancedMesh(geometry,material,count);mesh.name=name;mesh.frustumCulled=false;
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
  root.add(instanced('korpi-links',cylinder,m.frame,24),instanced('korpi-dampers',cylinder,m.alloy,28),instanced('korpi-fittings',cylinder,m.orange,16),instanced('korpi-springs',spring,springMaterial,4));
  return root;
}

/** Per-instance bindings are re-created for a scene clone; all peers use the same physical wheel pose. */
class RaceRig {
  readonly links:T.InstancedMesh;
  readonly dampers:T.InstancedMesh;
  readonly fittings:T.InstancedMesh;
  readonly springs:T.InstancedMesh;
  readonly springLengths=new T.InstancedBufferAttribute(new Float32Array(4).fill(1),1);
  readonly wheels:T.Object3D[];
  private top=v(0,0,0);private bottom=v(0,0,0);private end=v(0,0,0);private start=v(0,0,0);
  private lower=v(0,0,0);private upper=v(0,0,0);private pivot=v(0,0,0);
  constructor(root:T.Object3D){
    this.links=root.getObjectByName('korpi-links') as T.InstancedMesh;
    this.dampers=root.getObjectByName('korpi-dampers') as T.InstancedMesh;
    this.fittings=root.getObjectByName('korpi-fittings') as T.InstancedMesh;
    this.springs=root.getObjectByName('korpi-springs') as T.InstancedMesh;
    this.wheels=TRUCK.wheels.map((_,i)=>root.getObjectByName(`vehicle-wheel-${i}`)!);
    lengths.set(this.springs,this.springLengths);
  }
  update(wheels:readonly Wheel[]){
    wheels.forEach((w,i)=>{
      const at=TRUCK.wheels[i],side=Math.sign(at.z),g=this.wheels[i];
      if(!g)return;
      g.position.set(w.pos.x,w.pos.y,w.pos.z);g.rotation.set(0,w.steer,0);g.children[0].rotation.z=-w.rotation;
      // A vertical raycast contact cannot describe the small lateral arc of a wishbone.
      // Solve fixed-length arms on that arc; the outboard upright/hub connects to the authoritative tyre centre.
      const knuckle=(out:T.Vector3,y:number,pivotY:number,armLength:number)=>{
        const dy=y-pivotY;out.set(at.x,y,side*(.43+Math.sqrt(Math.max(.06,armLength*armLength-.38*.38-dy*dy))));
      };
      knuckle(this.lower,w.pos.y-.045,-.39,.91);
      knuckle(this.upper,w.pos.y+.22,-.15,.97);
      for(let j=0;j<4;j++){
        const upper=j>=2;this.pivot.set(at.x+(j%2?-.38:.38),upper?-.15:-.39,side*.43);
        segment(this.links,i*6+j,this.pivot,upper?this.upper:this.lower,.028,upper?.97:.91);
      }
      segment(this.links,i*6+4,this.lower,this.upper,.043);
      this.bottom.set(w.pos.x,w.pos.y,w.pos.z);
      segment(this.links,i*6+5,this.lower,this.bottom,.065);
      // Fixed-length damper barrel; only its chromed shaft telescopes. Top and bottom eye fittings stay fixed.
      this.top.set(at.x-Math.sign(at.x)*.25,.55,at.z*.64);
      this.bottom.copy(this.lower);this.bottom.x-=Math.sign(at.x)*.07;
      this.end.copy(this.top).lerp(this.bottom,.55);
      segment(this.dampers,i*7,this.top,this.end,.045,.48);
      segment(this.dampers,i*7+1,this.end,this.bottom,.019);
      const springLength=this.top.distanceTo(this.bottom)*.74;
      this.end.copy(this.top).lerp(this.bottom,.80);
      this.start.copy(this.top).lerp(this.bottom,.06);
      segment(this.springs,i,this.start,this.end,1);
      this.springLengths.setX(i,Math.max(.1,springLength));
      // Separate bypass shock, piggyback reservoir and two bypass tubes.
      this.top.x-=.16;this.bottom.x-=.11;this.end.copy(this.top).lerp(this.bottom,.53);
      segment(this.dampers,i*7+2,this.top,this.end,.042,.45);
      segment(this.dampers,i*7+3,this.end,this.bottom,.019);
      this.pivot.copy(this.top);this.pivot.x-=.10;this.end.copy(this.pivot);this.end.y-=.29;
      segment(this.dampers,i*7+4,this.pivot,this.end,.05);
      this.pivot.copy(this.top);this.pivot.z+=side*.05;this.end.copy(this.bottom).lerp(this.top,.35);this.end.z+=side*.05;
      segment(this.dampers,i*7+5,this.pivot,this.end,.012);
      // Half-shaft from the differential to the physical wheel hub.
      this.pivot.set(at.x,-.28,0);this.end.set(w.pos.x,w.pos.y,w.pos.z*.96);
      segment(this.dampers,i*7+6,this.pivot,this.end,.036);
      for(let j=0;j<4;j++){
        const a=j<2?this.top:this.bottom;this.pivot.copy(a);this.end.copy(a);this.end.y+=.045;
        this.pivot.x+=(j%2?-.045:.045);this.end.x=this.pivot.x;
        segment(this.fittings,i*4+j,this.pivot,this.end,.055);
      }
    });
    for(const mesh of [this.links,this.dampers,this.fittings,this.springs])mesh.instanceMatrix.needsUpdate=true;
    this.springLengths.needsUpdate=true;
  }
}
export function applyRaceWheels(root:T.Object3D,wheels:readonly Wheel[]){
  let rig=rigs.get(root);if(!rig){if(!root.getObjectByName('korpi-links'))return;rig=new RaceRig(root);rigs.set(root,rig);}
  rig.update(wheels);
}
