import * as THREE from 'three/webgpu';
import { mergeVertices } from 'three/addons/utils/BufferGeometryUtils.js';
import type { FleetModel } from './catalog';

type V = [number, number, number];
type Surface = [number, number, number, number]; // paint mask, roughness, metalness, lamp emission
const PAINT: Surface = [1, .26, .32, 0], RUBBER: Surface = [0, .91, 0, 0], GLASS: Surface = [0, .12, .25, 0];
const METAL: Surface = [0, .29, .8, 0], TRIM: Surface = [0, .62, .08, 0];
const WHITE: V = [1, 1, 1], DARK: V = [.018, .023, .029], WINDOW: V = [.016, .030, .041];
/** A compact merged mesh with per-vertex surface properties: every part uses one shared node material. */
class Builder {
  flip=false;
  positions: number[] = []; colors: number[] = []; surfaces: number[] = [];
  tri(a: V, b: V, c: V, color: V = WHITE, surface = PAINT) {
    this.positions.push(...a, ...(this.flip?c:b), ...(this.flip?b:c));
    for (let i = 0; i < 3; i++) { this.colors.push(...color); this.surfaces.push(...surface); }
  }
  quad(a: V, b: V, c: V, d: V, color: V = WHITE, surface = PAINT) { this.tri(a,b,c,color,surface); this.tri(a,c,d,color,surface); }
  box(x: number, y: number, z: number, w: number, h: number, d: number, color: V = WHITE, surface = PAINT) {
    const previous=this.flip;this.flip=false;
    const a: V = [x-w/2,y-h/2,z-d/2], b: V = [x+w/2,y-h/2,z-d/2], c: V = [x+w/2,y+h/2,z-d/2], e: V = [x-w/2,y+h/2,z-d/2];
    const f: V = [a[0],a[1],z+d/2], g: V = [b[0],b[1],z+d/2], i: V = [c[0],c[1],z+d/2], j: V = [e[0],e[1],z+d/2];
    this.quad(a,e,c,b,color,surface); this.quad(f,g,i,j,color,surface); this.quad(a,f,j,e,color,surface);
    this.quad(g,b,c,i,color,surface); this.quad(e,j,i,c,color,surface); this.quad(a,b,g,f,color,surface);
    this.flip=previous;
  }
  rod(a: V, b: V, radius: number, color: V, surface: Surface, n = 6) {
    const previous=this.flip;this.flip=false;
    const av = new THREE.Vector3(...a), bv = new THREE.Vector3(...b), axis = bv.clone().sub(av).normalize();
    const u = new THREE.Vector3(0,1,0); if (Math.abs(axis.y) > .95) u.set(1,0,0);
    u.cross(axis).normalize().multiplyScalar(radius); const v = axis.clone().cross(u);
    const point = (base: THREE.Vector3, angle: number): V => base.clone().addScaledVector(u, Math.cos(angle)).addScaledVector(v, Math.sin(angle)).toArray() as V;
    for (let i=0; i<n; i++) { const aa=i/n*Math.PI*2, bb=(i+1)/n*Math.PI*2; this.quad(point(av,aa),point(av,bb),point(bv,bb),point(bv,aa),color,surface); }
    this.flip=previous;
  }
  geometry() {
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.Float32BufferAttribute(this.positions,3));
    g.setAttribute('color', new THREE.Float32BufferAttribute(this.colors,3));
    g.setAttribute('fleetSurface', new THREE.Float32BufferAttribute(this.surfaces,4));
    const indexed=mergeVertices(g,1e-5);g.dispose();
    indexed.computeVertexNormals(); indexed.computeBoundingBox(); indexed.computeBoundingSphere(); return indexed;
  }
}
function wheel(b: Builder, x: number, y: number, z: number, r: number, side: number, face: number, lod: number) {
  const n = lod === 0 ? 20 : lod === 1 ? 10 : 6, w=.205, rim=r*.64;
  const point = (xx: number, rr: number, angle: number): V => [xx,y+Math.sin(angle)*rr,z+Math.cos(angle)*rr];
  for (let i=0;i<n;i++) {
    const a=i/n*Math.PI*2-Math.PI/2, c=(i+1)/n*Math.PI*2-Math.PI/2;
    b.quad(point(x-w/2,r,a),point(x+w/2,r,a),point(x+w/2,r,c),point(x-w/2,r,c),DARK,RUBBER);
    for (const s of [-1,1]) {
      b.flip=s<0;
      const outside=x+s*w/2, lip=x+s*(w/2+.018);
      if(lod===2){b.tri([outside,y,z],point(outside,r,a),point(outside,r,c),[.04,.045,.05],RUBBER);continue;}
      b.quad(point(outside,r,a),point(lip,r*.85,a),point(lip,r*.85,c),point(outside,r,c),DARK,RUBBER);
      b.quad(point(lip,r*.85,a),point(lip,rim,a),point(lip,rim,c),point(lip,r*.85,c),[.028,.031,.035],RUBBER);
      b.tri([lip,y,z],point(lip,rim,a),point(lip,rim,c),lod===2?[.28,.3,.32]:[.055,.06,.065],METAL);
    }
  }
  b.flip=false;
  if (lod === 0) {
    const outside=x+side*(w/2+.023), spokes=face%3===0?5:face%3===1?10:7;
    for(let i=0;i<spokes;i++) {
      const a=i/spokes*Math.PI*2, d=.10;
      b.quad(point(outside,rim*.22,a-d*2),point(outside,rim*.94,a-d),point(outside,rim*.94,a+d),point(outside,rim*.22,a+d*2),[.52,.55,.59],METAL);
    }
    b.rod([outside-.008,y,z],[outside+.008,y,z],rim*.23,[.32,.35,.38],METAL,8);
  }
}
/** +Z forward, tyre bottoms at Y=0. No imported geometry, logos or photo textures. */
export function buildFleetGeometry(m: FleetModel, lod: 0|1|2): THREE.BufferGeometry {
  const b = new Builder(), L=m.length, W=m.width/2, H=m.height, belt=m.belt, r=m.wheel;
  const front=m.wheelbase/2, rear=-m.wheelbase/2, low=.25+(r-.315), arch=r+.055;
  const n=lod===0?28:lod===1?10:2;
  const zf=L*m.hood, zr=L*m.rear;
  const classic=m.body==='classic', commercial=m.body==='van';
  const widthAt=(z:number)=>W*((classic||commercial?.95:.86)+(classic||commercial?.05:.14)*Math.sin(Math.PI*(z/L+.5)));
  const topAt=(z:number)=>belt-(z>zf?(classic?.10:commercial?.14:.23)*Math.pow((z-zf)/(L/2-zf),1.15)
    :z<zr?(m.body==='sedan'||classic?.16:.07)*Math.pow((zr-z)/(L/2+zr),1.2):0);
  const bottomAt=(z:number)=>Math.max(low,...[front,rear].map(wz=>Math.abs(z-wz)<arch ? r+Math.sqrt(arch*arch-(z-wz)**2) : low));
  // Wheel arches are actual cut-outs, not black circles pasted onto a solid cuboid.
  const stations=Array.from({length:n+1},(_,i)=>-L/2+L*i/n);
  const archSteps=lod===0?12:lod===1?8:4;
  for(const axle of [front,rear])for(let i=0;i<=archSteps;i++)stations.push(axle+Math.cos(i/archSteps*Math.PI)*arch);
  const zs=[...new Set(stations.filter(z=>z>=-L/2&&z<=L/2))].sort((a,b)=>a-b);
  for(let i=0;i<zs.length-1;i++) {
    const z0=zs[i],z1=zs[i+1],x0=widthAt(z0),x1=widthAt(z1),y0=topAt(z0),y1=topAt(z1),a0=bottomAt(z0),a1=bottomAt(z1);
    for(const s of [-1,1]) {
      b.flip=s>0;
      const bx0=x0*(a0>low+.01?1:.91),bx1=x1*(a1>low+.01?1:.91);
      const mid0=Math.max(a0,y0-.25),mid1=Math.max(a1,y1-.25);
      b.quad([s*bx0,a0,z0],[s*bx1,a1,z1],[s*x1,mid1,z1],[s*x0,mid0,z0],[.86,.87,.88],PAINT);
      b.quad([s*x0,mid0,z0],[s*x1,mid1,z1],[s*x1*.98,y1-.06,z1],[s*x0*.98,y0-.06,z0],[.95,.96,.97],PAINT);
      b.quad([s*x0*.98,y0-.06,z0],[s*x1*.98,y1-.06,z1],[s*x1*.90,y1,z1],[s*x0*.90,y0,z0],WHITE,PAINT);
      if(lod<2 && (a0>low || a1>low)) b.quad([s*x0,a0,z0],[s*x0*.91,a0,z0],[s*x1*.91,a1,z1],[s*x1,a1,z1],DARK,TRIM);
      b.quad([s*x0*.90,y0,z0],[s*x1*.90,y1,z1],[0,y1+.025,z1],[0,y0+.025,z0],WHITE,PAINT);
    }
  }
  for(const s of [-1,1]) {
    b.flip=s<0;
    const z=s*L/2,w=widthAt(z),y=topAt(z);
    b.quad([-w*.91,low,z],[w*.91,low,z],[w,y-.25,z],[-w,y-.25,z],[.86,.87,.88],PAINT);
    b.quad([-w,y-.25,z],[w,y-.25,z],[w*.98,y-.06,z],[-w*.98,y-.06,z],[.95,.96,.97],PAINT);
    b.quad([-w*.98,y-.06,z],[w*.98,y-.06,z],[w*.90,y,z],[-w*.90,y,z],WHITE,PAINT);
    b.tri([-w*.90,y,z],[w*.90,y,z],[0,y+.025,z],WHITE,PAINT);
  }
  b.flip=false;
  const rf=zf-(commercial?.28:classic?.39:.62), rr=zr+(m.body==='sedan'||classic?.52:commercial?.16:.32);
  const roofW=W*m.taper, baseW=W*.90, baseY=topAt(zf), rearY=topAt(zr);
  const roofAt=(z:number)=>H-.045*Math.pow(2*(z-rr)/(rf-rr)-1,2);
  const roofEdge=H-.105;
  // Glasshouse: slanted windscreens and narrower roof, with separate painted pillars.
  const cross=lod===2?[-1,0,1]:[-1,-.75,-.35,.35,.75,1];
  for(let i=0;i<cross.length-1;i++) {
    const x0=cross[i],x1=cross[i+1];
    for(const [z,rz] of [[zf,rf],[zr,rr]]) {
      b.flip=z<0;
      const point=(x:number,upper:boolean):V=>[x*(upper?roofW:baseW),upper?roofAt(rz)-.06*x*x:topAt(z+(z>0?1:-1)*.065*(1-x*x))+.025*(1-x*x),
        (upper?rz:z)+(z>0?1:-1)*.065*(1-x*x)];
      b.quad(point(x0,false),point(x1,false),point(x1,true),point(x0,true),WINDOW,GLASS);
    }
    b.flip=true;
    const steps=lod===0?4:2;
    for(let j=0;j<steps;j++){
      const za=rr+(rf-rr)*j/steps,zb=rr+(rf-rr)*(j+1)/steps;
      b.quad([x0*roofW,roofAt(za)-.06*x0*x0,za+(2*j/steps-1)*.065*(1-x0*x0)],[x1*roofW,roofAt(za)-.06*x1*x1,za+(2*j/steps-1)*.065*(1-x1*x1)],
        [x1*roofW,roofAt(zb)-.06*x1*x1,zb+(2*(j+1)/steps-1)*.065*(1-x1*x1)],[x0*roofW,roofAt(zb)-.06*x0*x0,zb+(2*(j+1)/steps-1)*.065*(1-x0*x0)],WHITE,PAINT);
    }
  }
  for(const s of [-1,1]) {
    b.flip=s>0;
    for(let j=0;j<4;j++){
      const a=rr+(rf-rr)*j/4,c=rr+(rf-rr)*(j+1)/4;
      b.quad([s*roofW,roofEdge-.012,a],[s*roofW,roofEdge-.012,c],[s*roofW,roofAt(c)-.06,c],[s*roofW,roofAt(a)-.06,a],WHITE,PAINT);
    }
    if(m.body==='van') {
      b.quad([s*baseW,topAt(-.05),-.05],[s*baseW,baseY,zf],[s*roofW,roofEdge,rf],[s*roofW,roofEdge,-.05],WINDOW,GLASS);
      b.quad([s*baseW,rearY,zr],[s*baseW,topAt(-.05),-.05],[s*roofW,roofEdge,-.05],[s*roofW,roofEdge,rr],[.9,.91,.92],PAINT);
    } else b.quad([s*baseW,rearY,zr],[s*baseW,baseY,zf],[s*roofW,roofEdge,rf],[s*roofW,roofEdge,rr],WINDOW,GLASS);
    if(lod<2) {
      // A, B and C pillars, lower window seals, chrome waist trim, door seams and handles.
      b.rod([s*baseW,baseY,zf],[s*roofW,roofEdge,rf],.035,WHITE,PAINT);
      b.rod([s*baseW,rearY,zr],[s*roofW,roofEdge,rr],m.body==='estate'?.065:.05,WHITE,PAINT);
      const mid=(rf+rr)*.5;
      b.quad([s*(baseW+.003),baseY,mid-.045],[s*(baseW+.003),baseY,mid+.045],[s*(roofW+.003),roofAt(mid)-.06,mid+.045],[s*(roofW+.003),roofAt(mid)-.06,mid-.045],DARK,TRIM);
      if(m.body==='estate'||m.body==='mpv'||m.body==='suv') b.rod([s*baseW,baseY,rr+.26],[s*roofW,roofAt(rr+.26)-.06,rr+.26],.025,DARK,TRIM);
      b.rod([s*baseW,rearY,zr],[s*baseW,baseY,zf],.014,[.38,.4,.42],METAL);
      if(lod===0)for(const z of [mid,zf-.1,zr+.2]) b.rod([s*(widthAt(z)+.004),belt-.12,z],[s*(widthAt(z)+.004),Math.max(low+.05,bottomAt(z)),z],.006,[.08,.09,.10],TRIM,4);
      if(lod===0)for(const z of [mid+.13,Math.max(zr+.3,mid-.6)]) b.box(s*(widthAt(z)+.012),belt-.12,z,.025,.035,.15,[.45,.47,.48],METAL);
      b.box(s*(W+.11),belt+.12,zf-.10,.20,.12,.27,WHITE,PAINT);
      b.box(s*(W+.12),belt+.12,zf-.241,.15,.085,.009,[.28,.36,.42],GLASS);
      // Lower panels carry restrained road grime; no random bright rust on the modern fleet.
      const dirt=.68-m.age*.15;
      b.box(s*(W*.94),low+.045,0,.014,.065,L*.40,[dirt,dirt*.97,dirt*.91],[1,.82,.03,0]);
    }
  }
  b.flip=false;
  if(m.body==='van') {
    // Solid cargo panels behind the driver's compartment; long side crease and sliding-door track.
    for(const s of [-1,1]) {

      if(lod<2) b.rod([s*(W+.005),belt-.09,zr+.15],[s*(W+.005),belt-.09,-.08],.011,DARK,TRIM,4);
    }
  }
  if(m.body==='pickup') {
    b.box(0,belt-.08,-L*.32,W*1.75,.12,L*.28,[.035,.04,.045],RUBBER);
    for(const s of [-1,1]) b.box(s*W*.91,belt+.065,-L*.32,.12,.20,L*.31,WHITE,PAINT);
  }
  for(const s of [-1,1]) for(const z of [front,rear]) wheel(b,s*(W-.075),r,z,r,s,m.face,lod);
  // Body-specific lamp signatures, grille, bumpers and fictional Finnish-style plates.
  for(const end of [-1,1]) {
    b.flip=end<0;
    const z=end*(L/2+.006), lampY=topAt(end*L/2)-.09;
    b.box(0,low+.14,z,W*1.70,.13,.04,DARK,TRIM);
    b.box(0,lampY-.12,z+end*.013,W*(m.face%2?.79:.65),.15,.025,[.022,.026,.03],TRIM);
    for(const s of [-1,1]) {
      const lh=m.face%3===0?.13:.085, lw=W*(m.face%3===1?.46:.39);
      const cx=s*W*.64,zz=z+end*.022;
      b.quad([cx-lw/2,lampY-lh/2,zz],[cx+lw/2,lampY-lh/2,zz],[cx+lw/2-s*.045,lampY+lh/2+.018,zz],
        [cx-lw/2+s*.045,lampY+lh/2,zz],end>0?[.65,.76,.86]:[.35,.008,.006],[0,.17,.25,end>0?1:.55]);
      if(lod===0) b.box(s*W*.83,lampY-.09,z+end*.022,.075,.035,.026,[.64,.21,.015],[0,.30,.1,0]);
    }
    if(lod===0) {
      b.box(0,low+.18,z+end*.04,.51,.11,.018,[.9,.92,.87],[0,.55,0,0]);
      b.box(-.225,low+.18,z+end*.051,.045,.10,.004,[.02,.08,.36],TRIM);
      // Close-range fictional identifiers. These geometric strokes are removed beyond LOD0.
      const digits=[m.face+2,3,5,1,m.face%5+2,7];
      for(let k=0;k<6;k++) {
        const x=-.17+k*.064;
        b.box(x,low+.18,z+end*.053,.008,.065,.003,DARK,TRIM);
        b.box(x+.015,low+.207,z+end*.053,.032,.007,.003,DARK,TRIM);
        if(digits[k]%2) b.box(x+.028,low+.18,z+end*.053,.007,.060,.003,DARK,TRIM);
        b.box(x+.014,low+.153,z+end*.053,.03,.007,.003,DARK,TRIM);
      }
      // Fictional diamond badge, deliberately no real brand mark.
      b.box(0,lampY-.04,z+end*.031,.055,.045,.008,[.55,.59,.62],METAL);
    }
  }
  b.flip=false;
  if(lod===0) {
    // Wipers and dark headrest silhouettes just inside the opaque, tinted windshield.
    for(const s of [-1,1]) {
      b.rod([s*.12,baseY+.018,zf+.008],[s*.62,baseY+.07,zf-.045],.009,DARK,TRIM,4);
      b.box(s*.39,baseY+.10,zf-.045,.22,.14,.03,[.025,.035,.041],GLASS);
    }
  }
  if(m.accessory==='box' && lod<2) {
    for(const z of [rr+.2,rf-.1]) b.box(0,H+.07,z,W*1.8,.04,.055,DARK,TRIM);
    b.box(0,H+.22,(rf+rr)*.5,W*1.35,.28,Math.min(1.75,rf-rr+.3),[.045,.05,.055],TRIM);
  }
  if(m.accessory==='taxi') b.box(0,H+.11,(rf+rr)*.35,.36,.17,.17,[.82,.57,.10],[0,.48,0,.25]);
  if(m.accessory==='rack' && lod===0) {
    const z=-L/2-.27;
    b.box(0,.49,z,1.4,.08,.30,DARK,TRIM);
    // Two visible bicycle wheels and a diamond frame on the rear carrier.
    for(const x of [-.52,.52]) {
      for(let i=0;i<20;i++) {
        const a=i/20*Math.PI*2,c=(i+1)/20*Math.PI*2;
        b.rod([x+Math.cos(a)*.31,.85+Math.sin(a)*.31,z],[x+Math.cos(c)*.31,.85+Math.sin(c)*.31,z],.021,DARK,RUBBER,4);
      }
    }
    for(const [a,c] of [[[-.52,.85,z],[-.12,1.23,z]],[[-.12,1.23,z],[.10,.85,z]],[[.10,.85,z],[-.52,.85,z]],[[-.12,1.23,z],[.34,1.23,z]],[[.34,1.23,z],[.52,.85,z]],[[.52,.85,z],[.10,.85,z]],[[.10,.85,z],[.34,1.23,z]]] as [V,V][]) b.rod(a,c,.018,[.15,.23,.24],METAL,5);
    b.box(-.12,1.28,z,.19,.045,.10,DARK,TRIM);
  }
  const geometry=b.geometry(); geometry.name=`${m.id}/lod${lod}`; return geometry;
}
