import * as THREE from 'three/webgpu';
import { mergeVertices } from 'three/addons/utils/BufferGeometryUtils.js';
import { fleetHash, type FleetModel } from './catalog';
import { fleetHalfWidth } from './profile';

type V = [number, number, number];
type Surface = [number, number, number, number]; // paint mask, roughness, metalness, lamp emission
const PAINT: Surface = [1, .26, .32, 0], RUBBER: Surface = [0, .91, 0, 0], GLASS: Surface = [0, .10, 0, 0];
const METAL: Surface = [0, .29, .8, 0], TRIM: Surface = [0, .62, .08, 0];
const WHITE: V = [1, 1, 1], DARK: V = [.018, .023, .029], WINDOW: V = [.016, .030, .041];
/** A compact merged mesh with per-vertex surface properties: every part uses one shared node material. */
class Builder {
  flip=false; smooth=false; smoothing:number[]=[]; map:((point:V)=>V)|undefined;
  positions: number[] = []; colors: number[] = []; surfaces: number[] = [];
  tri(a: V, b: V, c: V, color: V = WHITE, surface = PAINT) {
    if(this.map){a=this.map(a);b=this.map(b);c=this.map(c);}
    this.positions.push(...a, ...(this.flip?c:b), ...(this.flip?b:c));
    for (let i = 0; i < 3; i++) { this.colors.push(...color); this.surfaces.push(...surface); this.smoothing.push(this.smooth&&surface===PAINT?1:0); }
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
    g.setAttribute('fleetSmooth',new THREE.Float32BufferAttribute(this.smoothing,1));
    const indexed=mergeVertices(g,1e-5);g.dispose();
    indexed.computeVertexNormals();
    // Body bands deliberately have different vertex colours. Smooth their shared shell
    // normals before discarding the authoring group, rather than creasing at each colour.
    const p=indexed.getAttribute('position'),normals=indexed.getAttribute('normal'),smooth=indexed.getAttribute('fleetSmooth');
    const sums=new Map<string,THREE.Vector3>(),keys=new Map<number,string>();
    for(let i=0;i<p.count;i++)if(smooth.getX(i)){
      const key=[p.getX(i),p.getY(i),p.getZ(i)].map(v=>Math.round(v*1e5)).join('/');keys.set(i,key);
      let sum=sums.get(key);if(!sum){sum=new THREE.Vector3();sums.set(key,sum);}sum.add(new THREE.Vector3().fromBufferAttribute(normals,i));
    }
    for(const [i,key] of keys){const normal=sums.get(key)!.clone().normalize();normals.setXYZ(i,normal.x,normal.y,normal.z);}
    indexed.deleteAttribute('fleetSmooth');indexed.computeBoundingBox();indexed.computeBoundingSphere();return indexed;
  }
}
function wheel(b: Builder, x: number, y: number, z: number, r: number, side: number, face: number, lod: number) {
  const n = lod === 0 ? 20 : lod === 1 ? 10 : 6, w=.205, rim=r*(face===6?.65:.70);
  const point = (xx: number, rr: number, angle: number): V => [xx,y+Math.sin(angle)*rr,z+Math.cos(angle)*rr];
  for (let i=0;i<n;i++) {
    b.flip=false;
    const a=i/n*Math.PI*2-Math.PI/2, c=(i+1)/n*Math.PI*2-Math.PI/2;
    const profile=[[-w/2,r*.93],[-w*.34,r],[w*.34,r],[w/2,r*.93]];
    for(let j=0;j<profile.length-1;j++){
      const [xa,ra]=profile[j],[xb,rb]=profile[j+1];
      b.quad(point(x+xa,ra,a),point(x+xb,rb,a),point(x+xb,rb,c),point(x+xa,ra,c),DARK,RUBBER);
    }
    for (const s of [-1,1]) {
      b.flip=s<0;
      const outside=x+s*w/2, lip=x+s*(w/2+.018);
      if(lod===2){b.tri([outside,y,z],point(outside,r,c),point(outside,r,a),[.04,.045,.05],RUBBER);continue;}
      b.quad(point(outside,r*.93,a),point(lip,r*.85,a),point(lip,r*.85,c),point(outside,r*.93,c),DARK,RUBBER);
      b.quad(point(lip,r*.85,a),point(lip,rim,a),point(lip,rim,c),point(lip,r*.85,c),[.028,.031,.035],RUBBER);
      const recess=lip-s*.035;
      b.quad(point(lip,rim,a),point(recess,rim*.88,a),point(recess,rim*.88,c),point(lip,rim,c),[.38,.41,.44],METAL);
      b.tri([recess,y,z],point(recess,rim*.88,c),point(recess,rim*.88,a),[.055,.06,.065],METAL);
    }
  }
  b.flip=false;
  if (lod < 2) {
    b.flip=side>0;
    const outside=x+side*(w/2+.010), spokes=lod===1?5:face%3===0?5:face%3===1?10:7;
    for(let i=0;i<spokes;i++) {
      const a=i/spokes*Math.PI*2, d=.10;
      b.quad(point(outside,rim*.22,a-d*2),point(outside,rim*.94,a-d),point(outside,rim*.94,a+d),point(outside,rim*.22,a+d*2),[.33,.36,.40],METAL);
    }
    b.rod([outside-.008,y,z],[outside+.008,y,z],rim*.23,[.32,.35,.38],METAL,8);
  }
  b.flip=false;
}
/** +Z forward, tyre bottoms at Y=0. No imported geometry, logos or photo textures. */
export function buildFleetGeometry(m: FleetModel, lod: 0|1|2, includeWheels=true): THREE.BufferGeometry {
  const b = new Builder(), L=m.length, W=m.width/2, H=m.height, belt=m.belt, r=m.wheel;
  // Rounded front/rear corners apply to the shared shell, lenses and trim together.
  // Axles remain outside this short bumper transition, keeping tyres exactly circular.
  b.map=([x,y,z])=>{
    const nearEnd=THREE.MathUtils.smoothstep(Math.abs(z)/(L/2),.88,1);
    const curve=z>0?[.14,.18,.12,.11,.20,.16,.08][m.face]:[.10,.12,.08,.09,.13,.10,.065][m.face];
    return [x,y,z-Math.sign(z)*curve*Math.pow(Math.min(1,Math.abs(x)/W),4)*nearEnd];
  };
  const front=m.wheelbase/2, rear=-m.wheelbase/2, low=.25+(r-.315), arch=r+.055;
  const n=lod===0?28:lod===1?10:2;
  const zf=L*m.hood, zr=L*m.rear;
  const classic=m.body==='classic', commercial=m.body==='van';
  const widthAt=(z:number)=>fleetHalfWidth(m,z);
  const topAt=(z:number)=>belt-(z>zf?(classic?.10:commercial?.14:.23)*Math.pow((z-zf)/(L/2-zf),1.15)
    :z<zr?(m.body==='sedan'||classic?.16:.07)*Math.pow((zr-z)/(L/2+zr),1.2):0);
  const bottomAt=(z:number)=>Math.max(low,...[front,rear].map(wz=>Math.abs(z-wz)<arch ? r+Math.sqrt(arch*arch-(z-wz)**2) : low));
  const bodyPoint=(z:number,y:number,side:number,offset=0):V=>{
    const w=widthAt(z),bottom=bottomAt(z),top=topAt(z),mid=Math.max(bottom,top-.25),sill=w*(bottom>low+.01?1:.91);
    let x:number;
    if(y<=mid){const t=Math.max(0,Math.min(1,(y-bottom)/Math.max(.001,mid-bottom)));x=sill+(w-sill)*t+(bottom<=low+.01?.018*Math.sin(t*Math.PI):0);}
    else if(y<=top-.06)x=w*(1-.02*(y-mid)/Math.max(.001,top-.06-mid));
    else x=w*(.98-.08*(y-(top-.06))/.06);
    return [side*(x+offset),y,z];
  };
  // Wheel arches are actual cut-outs, not black circles pasted onto a solid cuboid.
  const stations=[zf,zr,front,rear,...Array.from({length:n+1},(_,i)=>-L/2+L*i/n)];
  const archSteps=lod===0?12:lod===1?8:4;
  for(const axle of [front,rear])for(let i=0;i<=archSteps;i++)stations.push(axle+Math.cos(i/archSteps*Math.PI)*arch);
  const zs=[...new Set(stations.filter(z=>z>=-L/2&&z<=L/2))].sort((a,b)=>a-b);
  b.smooth=true;
  for(let i=0;i<zs.length-1;i++) {
    const z0=zs[i],z1=zs[i+1],x0=widthAt(z0),x1=widthAt(z1),y0=topAt(z0),y1=topAt(z1),a0=bottomAt(z0),a1=bottomAt(z1);
    for(const s of [-1,1]) {
      b.flip=s>0;
      const mid0=Math.max(a0,y0-.25),mid1=Math.max(a1,y1-.25);
      const belly0=a0+(mid0-a0)*.5,belly1=a1+(mid1-a1)*.5;
      b.quad(bodyPoint(z0,a0,s),bodyPoint(z1,a1,s),bodyPoint(z1,belly1,s),bodyPoint(z0,belly0,s),[.84-m.age*.08,.85-m.age*.09,.86-m.age*.11],PAINT);
      b.quad(bodyPoint(z0,belly0,s),bodyPoint(z1,belly1,s),bodyPoint(z1,mid1,s),bodyPoint(z0,mid0,s),[.91,.92,.93],PAINT);
      b.quad([s*x0,mid0,z0],[s*x1,mid1,z1],[s*x1*.98,y1-.06,z1],[s*x0*.98,y0-.06,z0],[.95,.96,.97],PAINT);
      b.quad([s*x0*.98,y0-.06,z0],[s*x1*.98,y1-.06,z1],[s*x1*.90,y1,z1],[s*x0*.90,y0,z0],WHITE,PAINT);
      if(lod<2 && (a0>low || a1>low)) b.quad([s*x0,a0,z0],[s*x0*.91,a0,z0],[s*x1*.91,a1,z1],[s*x1,a1,z1],DARK,TRIM);
      b.quad([s*x0*.90,y0,z0],[s*x1*.90,y1,z1],[0,y1+.025,z1],[0,y0+.025,z0],WHITE,PAINT);
    }
  }
  for(const s of [-1,1]) {
    b.flip=s<0;
    const z=s*L/2,w=widthAt(z),y=topAt(z);
    const cross=lod===0?[-1,-.85,-.65,-.3,0,.3,.65,.85,1]:[-1,-.65,0,.65,1];
    for(let i=0;i<cross.length-1;i++){
      const a=cross[i],c=cross[i+1];
      b.quad([a*w*.91,low,z],[c*w*.91,low,z],[c*w,y-.25,z],[a*w,y-.25,z],[.86-m.age*.08,.87-m.age*.09,.88-m.age*.11],PAINT);
      b.quad([a*w,y-.25,z],[c*w,y-.25,z],[c*w*.98,y-.06,z],[a*w*.98,y-.06,z],[.95,.96,.97],PAINT);
      b.quad([a*w*.98,y-.06,z],[c*w*.98,y-.06,z],[c*w*.90,y+.025*(1-c*c),z],[a*w*.90,y+.025*(1-a*a),z],WHITE,PAINT);
    }
  }
  b.flip=false;
  b.smooth=false;
  const rf=zf-(commercial?.28:classic?.39:.62), rr=zr+(m.body==='sedan'||classic?.52:commercial?.16:.32);
  const roofW=W*m.taper, baseY=topAt(zf), rearY=topAt(zr);
  const roofAt=(z:number)=>H-.045*Math.pow(2*(z-rr)/(rf-rr)-1,2);
  const roofEdge=H-.105;
  // Glasshouse: slanted windscreens and narrower roof, with separate painted pillars.
  const cross=lod===2?[-1,0,1]:[-1,-.75,-.35,.35,.75,1];
  for(let i=0;i<cross.length-1;i++) {
    const x0=cross[i],x1=cross[i+1];
    for(const [z,rz] of [[zf,rf],[zr,rr]]) {
      b.flip=z<0;
      const point=(x:number,upper:boolean):V=>[x*(upper?roofW:widthAt(z)*.90),upper?roofAt(rz)-.06*x*x:topAt(z+(z>0?1:-1)*.065*(1-x*x))+.025*(1-x*x),
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
    // Connected painted side with real openings, inset glass and seals. Shared boundaries
    // prevent a dark continuous glass strip or floating cylindrical pillars.
    const mid=rr+(rf-rr)*.57, quarter=rr+(rf-rr)*.22;
    const outer=[new THREE.Vector2(zr,rearY),new THREE.Vector2(zf,baseY),new THREE.Vector2(rf,roofEdge),new THREE.Vector2(rr,roofEdge)];
    type P=[number,number];
    const panes:P[][]=[[[mid+.05,baseY+.04],[zf-.12,baseY+.04],[rf-.075,roofEdge-.04],[mid+.05,roofEdge-.04]]];
    if(m.body!=='van'){
      if(m.body==='estate'||m.body==='suv'||m.body==='mpv'){
        panes.push([[quarter+.06,baseY+.04],[mid-.05,baseY+.04],[mid-.05,roofEdge-.04],[quarter+.06,roofEdge-.04]]);
        panes.push([[zr+.17,rearY+.045],[quarter-.055,baseY+.04],[quarter-.055,roofEdge-.04],[rr+.085,roofEdge-.04]]);
      }else panes.push([[zr+.22,rearY+.045],[mid-.05,baseY+.04],[mid-.05,roofEdge-.04],[rr+.12,roofEdge-.04]]);
    }
    const holes=panes.map(pane=>pane.map(([z,y])=>new THREE.Vector2(z,y)));
    const points=[...outer,...holes.flat()];
    const sidePoint=(p:THREE.Vector2,inset=0):V=>[s*(widthAt(p.x)*.90+(roofW-widthAt(p.x)*.90)*(p.y-baseY)/(roofEdge-baseY)-inset),p.y,p.x];
    for(const [a,c,d] of THREE.ShapeUtils.triangulateShape(outer,holes))b.tri(sidePoint(points[a]),sidePoint(points[c]),sidePoint(points[d]));
    for(const pane of holes){
      const center=pane.reduce((sum,p)=>sum.add(p),new THREE.Vector2()).multiplyScalar(1/pane.length);
      const inner=pane.map(p=>p.clone().lerp(center,Math.min(.08,.015/p.distanceTo(center))));
      for(let i=0;i<pane.length;i++){
        const j=(i+1)%pane.length;
        b.quad(sidePoint(pane[i]),sidePoint(pane[j]),sidePoint(inner[j],.024),sidePoint(inner[i],.024),DARK,TRIM);
      }
      for(const [a,c,d] of THREE.ShapeUtils.triangulateShape(inner,[]))b.tri(sidePoint(inner[a],.024),sidePoint(inner[c],.024),sidePoint(inner[d],.024),WINDOW,GLASS);
      if(lod===0){
        // Opaque glass keeps sorting and draw cost fixed. Subdued seat silhouettes lie on
        // the inner pane and share its reflection response, clipped to the actual opening.
        const clip=(subject:THREE.Vector2[])=>{
          let result=subject;
          for(let e=0;e<inner.length;e++){
            const a=inner[e],c=inner[(e+1)%inner.length],distance=(p:THREE.Vector2)=>(c.x-a.x)*(p.y-a.y)-(c.y-a.y)*(p.x-a.x);
            const input=result;result=[];
            for(let i=0;i<input.length;i++){
              const p=input[i],q=input[(i+1)%input.length],dp=distance(p),dq=distance(q);
              if(dp>=0)result.push(p);
              if((dp>=0)!==(dq>=0))result.push(p.clone().lerp(q,dp/(dp-dq)));
            }
          }
          return result;
        };
        for(const z of [mid+.16,quarter+.18]){
          const top=Math.min(roofEdge-.11,baseY+.25),bottom=baseY+.10;
          const silhouette=clip([new THREE.Vector2(z-.075,bottom),new THREE.Vector2(z+.075,bottom),new THREE.Vector2(z+.075,top),new THREE.Vector2(z-.075,top)]);
          for(const [a,c,d] of THREE.ShapeUtils.triangulateShape(silhouette,[]))b.tri(sidePoint(silhouette[a],.023),sidePoint(silhouette[c],.023),sidePoint(silhouette[d],.023),[.013,.024,.033],GLASS);
        }
      }
    }
    // The B pillar is a flat painted/black frame, bounded by the adjacent openings.
    if(m.body!=='van')b.quad(sidePoint(new THREE.Vector2(mid-.047,baseY+.025),-.002),sidePoint(new THREE.Vector2(mid+.047,baseY+.025),-.002),
      sidePoint(new THREE.Vector2(mid+.047,roofEdge-.018),-.002),sidePoint(new THREE.Vector2(mid-.047,roofEdge-.018),-.002),DARK,TRIM);
    if(lod<2) {
      // Waist seals, door seams and handles follow the authored openings.
      const waist=zs.filter(z=>z>=zr&&z<=zf);
      for(let i=0;i<waist.length-1;i++){
        const a=waist[i],c=waist[i+1];b.quad(bodyPoint(a,topAt(a)-.009,s,.002),bodyPoint(c,topAt(c)-.009,s,.002),
          bodyPoint(c,topAt(c),s,.002),bodyPoint(a,topAt(a),s,.002),[.26,.29,.32],METAL);
      }
      if(lod===0)for(const z of [mid,zf-.1,m.body==='estate'?quarter:zr+.2]){
        const bottom=Math.max(low+.025,bottomAt(z)+.015),top=topAt(z);
        const levels=[bottom,bottom+(top-bottom)*.35,bottom+(top-bottom)*.7,top-.06,top].filter(y=>y>=bottom).sort((a,c)=>a-c);
        for(let i=0;i<levels.length-1;i++)b.quad(bodyPoint(z-.003,levels[i],s,.002),bodyPoint(z+.003,levels[i],s,.002),
          bodyPoint(z+.003,levels[i+1],s,.002),bodyPoint(z-.003,levels[i+1],s,.002),[.035,.04,.045],TRIM);
      }
      if(lod===0)for(const z of [mid+.13,Math.max(zr+.3,mid-.6)]) b.box(bodyPoint(z,belt-.12,s,.014)[0],belt-.12,z,.025,.035,.15,[.45,.47,.48],METAL);
      b.box(s*(W+.035),belt+.10,zf-.10,.15,.10,.21,WHITE,PAINT);
      b.box(s*(W+.04),belt+.10,zf-.211,.12,.075,.009,[.28,.36,.42],GLASS);

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
  if(includeWheels)for(const s of [-1,1]) for(const z of [front,rear]) wheel(b,s*(widthAt(z)-.132),r,z,r,s,m.face,lod);
  // Body-specific lamp signatures, grille, bumpers and fictional Finnish-style plates.
  for(const end of [-1,1]) {
    b.flip=end<0;
    const z=end*(L/2+.006), lampY=topAt(end*L/2)-.09;
    b.box(0,low+.045,z,W*1.67,.035,.025,DARK,TRIM);
    const intakeW=W*(end>0?1.05:.84),intakeH=end>0?.15:.09,iz=z+end*.009;
    b.quad([-intakeW/2,low+.10,iz],[intakeW/2,low+.10,iz],[intakeW*.43,low+.10+intakeH,iz],[-intakeW*.43,low+.10+intakeH,iz],DARK,TRIM);
    b.box(0,lampY-.12,z+end*.013,W*(m.face%2?.79:.65),.15,.025,[.022,.026,.03],TRIM);
    for(const s of [-1,1]) {
      const lh=m.face%3===0?.13:.085, lw=W*(m.face%3===1?.46:.39);
      const cx=s*W*.64,zz=z+end*.022;
      const lamp=(width:number,height:number,depth:number,color:V,surface:Surface)=>{
        const bevel=Math.min(.035,height*.23),xl=cx-width/2,xr=cx+width/2,yb=lampY-height/2,yt=lampY+height/2,zl=zz+end*depth;
        const contour:V[]=[[xl+bevel,yb,zl],[xr-bevel,yb,zl],[xr,yb+bevel,zl],[xr,yt-bevel,zl],[xr-bevel,yt,zl],[xl+bevel,yt,zl],[xl,yt-bevel,zl],[xl,yb+bevel,zl]];
        for(let k=0;k<contour.length;k++)b.tri([cx,lampY,zl],contour[k],contour[(k+1)%contour.length],color,surface);
      };
      lamp(lw+.045,lh+.035,0,[.012,.016,.02],TRIM);
      lamp(lw,lh,.006,end>0?[.10,.15,.18]:[.16,.004,.003],[0,.12,.08,0]);
      if(lod<2&&end>0){
        const projectorX=cx-s*lw*.20,pr=lh*.29,pz=zz+end*.010;
        for(let k=0;k<8;k++){
          const a=k/8*Math.PI*2,c=(k+1)/8*Math.PI*2;
          b.tri([projectorX,lampY,pz],[projectorX+Math.cos(a)*pr,lampY+Math.sin(a)*pr,pz],[projectorX+Math.cos(c)*pr,lampY+Math.sin(c)*pr,pz],[.60,.68,.74],[0,.15,.45,1]);
        }
      }
      b.box(cx,lampY-lh*.29,zz+end*.012,lw*.78,.016,.004,end>0?[.72,.82,.94]:[.48,.008,.004],[0,.17,.12,end>0?1:.55]);
      // +X is the vehicle's left in the model-to-physics frame.
      b.box(s*W*.80,lampY-.07,z+end*.024,.085,.032,.025,[.75,.22,.006],[0,.22,.08,s>0?2:3]);

    }
    if(lod===0) {
      b.box(0,low+.18,z+end*.04,.51,.11,.018,[.9,.92,.87],[0,.55,0,0]);
      b.box(-.225,low+.18,z+end*.051,.045,.10,.004,[.02,.08,.36],TRIM);
      // Close-range fictional identifiers. These geometric strokes are removed beyond LOD0.
      const registration=['AVK','KRA','NRD','VRE','SLO','VLO','RTA'][m.face]+String(100+fleetHash(m.id)%900);
      const segments:Record<string,string>={
        '0':'abcedf','1':'bc','2':'abged','3':'abgcd','4':'fgbc','5':'afgcd','6':'afgecd','7':'abc','8':'abcdefg','9':'abfgcd',
        A:'abcefg',V:'hi',K:'fjk',N:'efbl',R:'abefgk',D:'abcedf',E:'afged',S:'afgcd',L:'fed',O:'abcdef',T:'am',
      };
      const lines:Record<string,number[]>={a:[0,1,1,1],b:[1,1,1,.5],c:[1,.5,1,0],d:[0,0,1,0],e:[0,0,0,.5],f:[0,.5,0,1],g:[0,.5,1,.5],
        h:[0,1,.5,0],i:[.5,0,1,1],j:[0,.5,1,1],k:[0,.5,1,0],l:[0,1,1,0],m:[.5,0,.5,1]};
      for(let k=0;k<registration.length;k++)for(const key of segments[registration[k]]){
        const [x0,y0,x1,y1]=lines[key],x=-.182+k*.058+(k>=3?.015:0),y=low+.148,zz=z+end*.053;
        const dx=(x1-x0)*.034,dy=(y1-y0)*.064,len=Math.hypot(dx,dy),ox=-dy/len*.0025,oy=dx/len*.0025;
        b.quad([x+x0*.034+ox,y+y0*.064+oy,zz],[x+x1*.034+ox,y+y1*.064+oy,zz],
          [x+x1*.034-ox,y+y1*.064-oy,zz],[x+x0*.034-ox,y+y0*.064-oy,zz],DARK,TRIM);
      }
      b.box(0,low+.18,z+end*.052,.020,.005,.002,DARK,TRIM);
      // Fictional diamond badge, deliberately no real brand mark.
      b.box(0,lampY-.04,z+end*.031,.055,.045,.008,[.55,.59,.62],METAL);
    }
  }
  b.flip=false;
  if(lod===0) {
    // Wipers and dark headrest silhouettes just inside the opaque, tinted windshield.
    for(const s of [-1,1]) {
      b.rod([s*.12,baseY+.018,zf+.008],[s*.62,baseY+.07,zf-.045],.009,DARK,TRIM,4);

    }
  }
  b.map=undefined;
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

/** A wheel centred on its local X axle, shared by driven/remote rigs. */
export function buildFleetWheel(m:FleetModel,side:-1|1,lod:0|1|2=0){
  const b=new Builder();wheel(b,0,0,0,m.wheel,side,m.face,lod);return b.geometry();
}
