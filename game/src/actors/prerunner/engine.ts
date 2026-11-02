import * as T from 'three/webgpu';
import { RoundedBoxGeometry } from 'three/addons/geometries/RoundedBoxGeometry.js';
import { Parts, box, tube, sweptTube, v } from './geometry';
import { racerMaterials } from './materials';

/** A complete original front-engine, supercharged V8 with exposed induction and protected filters. */
export function buildV8(parts:Parts,lod:number){
  const m=racerMaterials();
  const rounded=(x:number,y:number,z:number,sx:number,sy:number,sz:number,r=.025)=>new RoundedBoxGeometry(sx,sy,sz,1,r).translate(x,y,z);
  if(lod){
    parts.add(m.cast,box(1.28,.08,0,.94,.44,.68));
    parts.add(m.alloy,box(1.24,.45,0,.56,.28,.42));
    parts.add(m.black,box(1.19,.65,0,.38,.13,.38));
    return;
  }
  parts.add(m.cast,rounded(1.27,.04,0,.93,.38,.56,.04),rounded(1.27,-.24,0,.70,.18,.43,.03));
  parts.add(m.alloy,new T.CylinderGeometry(.18,.15,.22,16).rotateZ(Math.PI/2).translate(.70,-.08,0));
  // Distinct angled cylinder heads, gaskets, finned valve covers and bank fasteners.
  for(const s of [-1,1]){
    const head=new RoundedBoxGeometry(.94,.18,.30,1,.025).rotateX(s*.40).translate(1.27,.23,s*.27);
    const cover=new RoundedBoxGeometry(.94,.085,.28,1,.018).rotateX(s*.40).translate(1.27,.34,s*.29);
    parts.add(m.cast,head);parts.add(m.paint,cover);
    parts.add(m.black,new T.BoxGeometry(.95,.018,.30).rotateX(s*.40).translate(1.27,.30,s*.28));
    for(let i=0;i<5;i++){
      const x=.91+i*.18;parts.add(m.alloy,new T.CylinderGeometry(.015,.015,.026,6).translate(x,.397,s*.36));
    }
    parts.add(m.alloy,tube(v(.91,.40,s*.24),v(1.65,.40,s*.24),.018,8));
    for(let i=0;i<4;i++){
      const x=.96+i*.19;
      parts.add(m.black,sweptTube([[x,.39,s*.23],[x,.47,s*.31],[x+.04,.36,s*.40]],.009));
      parts.add(m.exhaust,sweptTube([[x,.16,s*.39],[x+.04,.04,s*.52],[1.12+(i-1.5)*.075,-.18,s*.64],[1.08,-.25,s*.67]],.028,8));
    }
    parts.add(m.exhaust,new T.ConeGeometry(.064,.25,12).rotateZ(Math.PI/2).translate(.98,-.25,s*.67));
    parts.add(m.exhaust,sweptTube([[1.09,-.25,s*.67],[.52,-.24,s*.67],[-.30,-.22,s*.66],[-.90,-.18,s*.67]],.056,10));
    // Curved 100mm ducts feed two foam-filter cans. Rings and clamps remain separate metal.
    parts.add(m.black,sweptTube([[1.15,.67,s*.16],[1.25,.74,s*.28],[1.48,.69,s*.37],[1.55,.60,s*.38]],.055,10));
    parts.add(m.black,new T.CylinderGeometry(.115,.115,.25,20,1,true).rotateZ(Math.PI/2).translate(1.68,.60,s*.38));
    for(let i=0;i<10;i++)parts.add(m.cast,new T.TorusGeometry(.113,.008,4,16).rotateY(Math.PI/2).translate(1.57+i*.025,.60,s*.38));
    parts.add(m.alloy,new T.CylinderGeometry(.12,.12,.018,20).rotateZ(Math.PI/2).translate(1.81,.60,s*.38),new T.RingGeometry(.057,.12,20).rotateY(-Math.PI/2).translate(1.55,.60,s*.38));
    // Rear radiator coolant return, braided fuel supply and actual engine support brackets.
    parts.add(m.black,sweptTube([[1.17,.19,s*.43],[.60,.06,s*.48],[-.28,.03,s*.51],[-1.00,.27,s*.55]],.024,8));
    parts.add(m.alloy,sweptTube([[1.55,.38,s*.23],[1.68,.33,s*.19],[1.66,.11,s*.16]],.010));
    parts.add(m.frame,tube(v(.95,-.15,s*.25),v(.88,-.27,s*.63),.037,8),tube(v(1.60,-.14,s*.25),v(1.71,-.26,s*.63),.037,8));
  }
  // Ribbed blower casing, plenum, throttle hat, belt and forward accessory train.
  parts.add(m.cast,rounded(1.23,.47,0,.57,.23,.44,.04));
  for(let i=0;i<9;i++)parts.add(m.alloy,box(.99+i*.058,.575,0,.016,.018,.42));
  parts.add(m.paint,rounded(1.16,.66,0,.40,.16,.38,.035));
  parts.add(m.alloy,box(1.16,.748,0,.24,.018,.23));
  const pulleys=[[.50,0,.084],[.03,.34,.074],[-.12,0,.11]];
  for(const [y,z,r]of pulleys){
    parts.add(m.alloy,new T.CylinderGeometry(r,r,.043,20).rotateZ(Math.PI/2).translate(1.78,y,z));
    parts.add(m.black,new T.TorusGeometry(r,.012,5,20).rotateY(Math.PI/2).translate(1.81,y,z));
  }
  parts.add(m.black,sweptTube([[1.82,.50,-.084],[1.82,.57,0],[1.82,.46,.075],[1.82,.06,.405],[1.82,-.03,.36],[1.82,-.21,.04],[1.82,-.19,-.09],[1.82,.50,-.084]],.012));
  parts.add(m.cast,new T.CylinderGeometry(.105,.105,.18,16).rotateZ(Math.PI/2).translate(1.65,.03,.34));
  parts.add(m.alloy,new T.CylinderGeometry(.075,.075,.20,16).translate(1.02,.18,.46));
  parts.add(m.black,sweptTube([[1.02,.28,.46],[.91,.34,.46],[.86,.23,.38]],.016));
}
