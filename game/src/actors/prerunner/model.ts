import * as T from 'three/webgpu';
import { TRUCK } from '../../physics/constants';
import { Parts, box, tube, v, panel, fender, strap, reflectPanelZ } from './geometry';
import { racerMaterials } from './materials';
import { buildV8 } from './engine';
import { shockTop } from './hardpoints';

export interface RacerModel { body: T.Group; wheel: T.Group; spare: T.Group; lod: number }
const prototypes = new Map<number,RacerModel>();
/** Original KORPI 440: shared parametric race chassis, +X forward, wheel axle +Z. */
export function racerModel(lod = 0): RacerModel {
  const cached=prototypes.get(lod);if(cached)return cached;
  const m=racerMaterials(), p=new Parts(), segments=lod===2?5:lod?8:12;
  const rail=(a:number[],b:number[],r=.022,mat=m.frame)=>p.add(mat,tube(v(a[0],a[1],a[2]),v(b[0],b[1],b[2]),r,segments));
  // A triangulated spaceframe, a narrowing nose and rear engine cradle.
  for(const s of [-1,1]) {
    rail([-2.32,-.28,s*.72],[2.2,-.28,s*.72],.055);
    rail([-2.32,-.28,s*.72],[-1.1,.65,s*.68],.045);
    rail([-1.1,.65,s*.68],[-.65,1.04,s*.68],.05);
    rail([-.65,1.04,s*.68],[.55,1.04,s*.67],.05);
    rail([.55,1.04,s*.67],[1.12,.42,s*.77],.05);
    rail([1.12,.42,s*.77],[2.33,-.15,s*.64],.045);
    rail([-1.1,.65,s*.68],[.8,-.24,s*.72],.038);
    rail([-.9,-.24,s*.72],[.9,.43,s*.75],.038);
    rail([-.65,1.04,s*.68],[-2.1,.02,s*.7],.041);
    rail([-2.1,.02,s*.7],[-.9,-.24,s*.72],.04);
    rail([-2.32,-.28,s*.72],[-2.45,.05,s*.7],.045);
    rail([2.2,-.28,s*.72],[2.46,-.05,s*.65],.045);
  }
  for(const [x,y,z] of [[-.65,1.04,.68],[.55,1.04,.67],[-1.1,.65,.68],[-2.32,-.28,.72],[2.46,-.05,.65],[1.12,.42,.77]])rail([x,y,-z],[x,y,z],.043);
  rail([-.65,1.04,-.68],[.55,1.04,.67]);rail([-.65,1.04,.68],[.55,1.04,-.67]);
  rail([-2.1,.02,-.7],[-1.1,.65,.68]);rail([-2.1,.02,.7],[-1.1,.65,-.68]);
  // Thin sculpted bodywork leaves the suspension, engine and rear spaceframe exposed.
  p.add(m.paint,box(-.03,1.08,0,1.32,.023,1.49),box(.91,.38,0,.28,.08,1.49),box(1.98,.22,0,.66,.027,1.62));
  p.add(m.black,box(0,-.21,0,1.85,.07,1.42),box(.79,.28,0,.18,.22,1.37));
  for(const s of [-1,1]) {
    const side=panel([[-1,-.18],[.86,-.18],[1.04,.19],[.74,.37],[-.65,.40],[-1.06,.07]],.025);
    const pos=side.attributes.position,uv=side.attributes.uv;
    for(let i=0;i<pos.count;i++)uv.setXY(i,(pos.getX(i)+1.06)/2.10,(pos.getY(i)+.18)/.58);
    if(s<0)reflectPanelZ(side);side.translate(0,0,s*.77);p.add(m.decal,side);
    // Short front fenders flare above the tyres without hiding the visible wishbones.
    p.add(m.paint,fender(TRUCK.wheels[0].x,s*1.15,.20),fender(TRUCK.wheels[2].x,s*1.15,.20));
    p.add(m.orange,box(.12,-.19,s*.798,1.8,.055,.012));
    for(const axle of [TRUCK.wheels[0],TRUCK.wheels[2]])for(const dx of [-.38,.38])rail([axle.x+dx,-.15,s*.75],[axle.x+dx,-.43+Math.sqrt(.75*.75-dx*dx),s*1.10],.016);
    // Bucket seats with five-point harness, head restraints and visible dark cabin.
    const bucket=panel([[.15,-.13],[-.53,-.13],[-.69,.53],[-.63,.82],[-.45,.82],[-.32,.10],[.15,.02]],.42);bucket.translate(0,0,s*.37-.21);p.add(m.black,bucket);
    if(!lod)for(const dz of [-.10,.10])p.add(m.orange,strap(v(-.50,.70,s*.37+dz),v(-.23,.03,s*.37+dz)));

  }
  if(!lod){
    const net=new T.BufferGeometry();net.setAttribute('position',new T.Float32BufferAttribute([-.67,.45,-.745,.76,.45,-.745,.48,.99,-.69,-.63,.99,-.69],3));
    net.setAttribute('uv',new T.Float32BufferAttribute([0,0,1,0,1,1,0,1],2));net.setIndex([0,2,1,0,3,2]);net.computeVertexNormals();p.add(m.net,net);

  }
  buildV8(p,lod);
  // Rear fuel cell, tilted radiator, recovery hooks, extinguishers and spare mount.
  p.add(m.alloy,box(-1.35,-.02,0,.85,.23,.91));
  for(const side of [-1,1])p.add(m.alloy,new T.CylinderGeometry(.05,.05,.60,lod?8:12).rotateZ(.25).translate(-1.02,.37,side*.55));
  p.add(m.black,new T.BoxGeometry(.11,.6,1.07).rotateZ(.25).translate(-1.09,.37,0));
  if(!lod)for(let i=0;i<16;i++){const y=.1+i*.032;p.add(m.alloy,box(-1.02-(y-.37)*.25,y,0,.024,.012,1.02));}
  p.add(m.orange,tube(v(-.82,.04,-.54),v(-.82,.37,-.54),.07),box(2.45,-.14,-.42,.1,.06,.13),box(2.45,-.14,.42,.1,.06,.13));
  if(!lod)for(const side of [-1,1]){p.add(m.orange,strap(v(-1.12,-.02,side*.47),v(-1.74,.45,side*.12),.05),strap(v(-1.74,.45,side*.12),v(-2.30,-.03,-side*.45),.05));}
  // Front light bar, pod optics, rear lamps and grille slots.
  p.add(m.frame,box(.51,1.16,0,.17,.14,1.28),box(2.34,.13,0,.04,.18,1.12));
  const lights=lod?6:18;for(let i=0;i<lights;i++)p.add(m.lamp,box(.604,1.16,(i/(lights-1)-.5)*1.04,.013,.073,.046));
  for(const s of [-1,1]) {
    p.add(m.lamp,box(2.367,.15,s*.67,.04,.105,.25));p.add(m.red,box(-2.34,.06,s*.64,.04,.09,.2));
    if(!lod)for(let i=0;i<4;i++)p.add(m.alloy,box(2.369,.07+i*.037,s*.3,.015,.012,.36));
  }
  // A flat skidplate with raised nose, differential and propshaft visible between the arms.
  p.add(m.alloy,box(.15,-.33,0,3.55,.045,.81),new T.BoxGeometry(.56,.04,.8).rotateZ(.36).translate(2.04,-.25,0));
  for(const at of [TRUCK.wheels[0],TRUCK.wheels[2]]){
    p.add(m.frame,new T.SphereGeometry(.15,12,8).scale(1,.8,1.2).translate(at.x,-.26,0));
    for(const side of [-1,1]){const top=shockTop({...at,z:Math.abs(at.z)*side},v(0,0,0));rail([at.x-.46,-.18,side*.57],top.toArray(),.026);rail([at.x+.40,-.18,side*.57],top.toArray(),.026);p.add(m.alloy,box(top.x,top.y,top.z-.04,.09,.07,.012),box(top.x,top.y,top.z+.04,.09,.07,.012));}
  }
  if(!lod){p.add(m.frame,new T.TorusGeometry(.18,.022,8,20).rotateY(Math.PI/2).rotateZ(-.25).translate(.53,.28,-.37));rail([.56,.25,-.37],[.77,.16,-.37],.028);}
  rail([-1.6,-.35,0],[1.6,-.35,0],.065,m.frame);
  // Original sidewall profile with actual shoulder, deep block tread, recessed hubs and beadlock bolts.
  const w=new Parts(), radius=TRUCK.wheelRadius, width=.46;
  const profile=[v(.22,-width/2-.012,0),v(.43,-width/2,0),v(.505,-.13,0),v(.54,-.085,0),v(.54,.085,0),v(.505,.13,0),v(.43,width/2,0),v(.22,width/2+.012,0)];
  const tyre=new T.LatheGeometry(profile.map(a=>new T.Vector2(a.x*(radius-.039)/.54,a.y)),lod===2?10:lod?16:40).rotateX(Math.PI/2);
  w.add(m.rubber,tyre);
  const tread=lod===2?0:lod?20:32;
  for(let i=0;i<tread;i++)for(const row of lod?[0]:[-1.5,-.5,.5,1.5]) {
    const a=i/tread*Math.PI*2+row*.035;
    const g=new T.BoxGeometry(.075,lod===2?.02:.046,.100).rotateZ(-row*.21).translate(0,radius-.028-Math.abs(row)*.012,row*.116).rotateZ(a);
    w.add(m.rubber,g);
  }
  w.add(m.frame,new T.CylinderGeometry(radius*.405,radius*.405,width*.90,lod===2?10:lod?16:32,1,true).rotateX(Math.PI/2));
  if(!lod)for(const side of [-1,1])w.add(m.alloy,new T.RingGeometry(.075,.16,24).rotateY(side<0?Math.PI:0).translate(0,0,side*.035));
  // The external beadlock and spokes face +Z; the actor orients each wheel outwards.
  for(const s of [1]){
    const ring=new T.TorusGeometry(radius*.42,.028,6,lod===2?10:lod?16:36).translate(0,0,s*(width/2+.012));w.add(m.orange,ring);

    w.add(m.alloy,new T.CylinderGeometry(.092,.092,.07,12).rotateX(Math.PI/2).translate(0,0,s*(width/2+.004)));
    for(let i=0;i<(lod===2?0:6);i++){
      const a=i*Math.PI/3;w.add(m.alloy,new T.BoxGeometry(.19,.05,.026).translate(.108,0,0).rotateZ(a).translate(0,0,s*(width/2-.006)));
    }
    if(!lod)for(let i=0;i<24;i++){const a=i*Math.PI/12;w.add(m.alloy,new T.CylinderGeometry(.012,.012,.018,6).rotateX(Math.PI/2).translate(Math.cos(a)*radius*.42,Math.sin(a)*radius*.42,s*(width/2+.036)));}
  }
  const wheel=w.build(`korpi-wheel-lod${lod}`,lod?m.distant:undefined),spare=wheel.clone(true);spare.name='korpi-spare';spare.rotation.x=-Math.PI/2;spare.rotation.y=.24;spare.position.set(-1.75,.19,0);
  const body=p.build(`korpi-440-lod${lod}`,lod?m.distant:undefined);body.add(spare);
  const result={body,wheel,spare,lod};prototypes.set(lod,result);return result;
}

if(import.meta.hot)import.meta.hot.dispose(()=>{
  const geometries=new Set<T.BufferGeometry>();
  for(const prototype of prototypes.values())for(const root of [prototype.body,prototype.wheel])root.traverse(object=>{if((object as T.Mesh).isMesh)geometries.add((object as T.Mesh).geometry);});
  for(const geometry of geometries)geometry.dispose();prototypes.clear();
});
