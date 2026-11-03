import * as THREE from 'three/webgpu';
import { PreparationBudget } from './preparationBudget';
import { attribute, cameraPosition, dot, modelWorldMatrixInverse, vec4 } from 'three/tsl';

interface Opening {
  origin: THREE.Vector3; tangent: THREE.Vector3; normal: THREE.Vector3;
  width: number; height: number; frame: THREE.Color; trim: THREE.Color;
  recess: number;
}

/** Exact UV coverage keeps a frame from bridging a surveyed passage or wall cut. */
function coversRectangle(triangles: number[], x: number, y: number, w: number, h: number) {
  let area=0;
  for (let i=0;i<triangles.length;i+=6) {
    let polygon=[[triangles[i],triangles[i+1]],[triangles[i+2],triangles[i+3]],[triangles[i+4],triangles[i+5]]];
    for (const [axis,bound,sign] of [[0,x,1],[0,x+w,-1],[1,y,1],[1,y+h,-1]]) {
      const clipped: number[][]=[];
      for (let j=0;j<polygon.length;j++) {
        const a=polygon[j],b=polygon[(j+1)%polygon.length];
        const da=(a[axis]-bound)*sign,db=(b[axis]-bound)*sign;
        if (da>=0) clipped.push(a);
        if ((da<0)!==(db<0)) {
          const t=da/(da-db); clipped.push([a[0]+(b[0]-a[0])*t,a[1]+(b[1]-a[1])*t]);
        }
      }
      polygon=clipped;
    }
    let twice=0;
    for(let j=0;j<polygon.length;j++){const a=polygon[j],b=polygon[(j+1)%polygon.length];twice+=a[0]*b[1]-a[1]*b[0];}
    area+=Math.abs(twice)*.5;
  }
  return area>=w*h-.0001;
}

/** Read complete reviewed rectangles and rectangular wall programs.
 * The UV rectangle is in wall metres; partial roof/passage cuts stay untouched. */
function* openingSteps(geometry: THREE.BufferGeometry): Generator<void,Opening[]> {
  const a = geometry.attributes;
  if (!a._kit_detail || !a._kit_extent || !a._kit_frame || !a._kit_layout || !a._kit_trim || !a.uv || !a.normal) return [];
  const groups = new Map<string, number[]>();
  const index=geometry.index, direction=new Int8Array(a.position.count);
  // Program dimensions can repeat on disconnected coplanar buildings. Recover
  // each chart's metric U orientation before grouping by its world-space anchor.
  for(let t=0;t<(index?.count??a.position.count);t+=3){
    if(t%3072===0)yield;
    const ia=index?index.getX(t):t,ib=index?index.getX(t+1):t+1,ic=index?index.getX(t+2):t+2;
    if(Math.abs(a.normal.getY(ia))>.01)continue;
    const next=Math.abs(a.uv.getX(ib)-a.uv.getX(ia))>Math.abs(a.uv.getX(ic)-a.uv.getX(ia))?ib:ic;
    const du=a.uv.getX(next)-a.uv.getX(ia);if(Math.abs(du)<.00001)continue;
    const nx=a.normal.getX(ia),nz=a.normal.getZ(ia),length=Math.hypot(nx,nz);
    const metric=(-nz*(a.position.getX(next)-a.position.getX(ia))+nx*(a.position.getZ(next)-a.position.getZ(ia)))/(length*du);
    if(Math.abs(Math.abs(metric)-1)>.02)continue;
    direction[ia]=direction[ib]=direction[ic]=metric>0?1:-1;
  }
  const owners: (number[] | undefined)[] = new Array(a.position.count);
  for (let i = 0; i < a.position.count; i++) {
    if(i%512===0)yield;
    if (!direction[i] || ![0, 1].includes(a._kit_detail.getW(i)) || a._kit_layout.getX(i) >= 42 || Math.abs(a.normal.getY(i)) > .01) continue;
    const normal = new THREE.Vector3().fromBufferAttribute(a.normal, i);
    const point = new THREE.Vector3().fromBufferAttribute(a.position, i);
    const horizontal=Math.hypot(normal.x,normal.z);
    const anchor=(-normal.z*point.x+normal.x*point.z)/horizontal-direction[i]*a.uv.getX(i);
    const key = [a._kit_extent.getX(i), a._kit_extent.getY(i), a._kit_extent.getZ(i), a._kit_extent.getW(i),
      normal.x, normal.z, normal.dot(point),anchor,point.y-a.uv.getY(i),direction[i],a._kit_detail.getW(i),a._kit_layout.getX(i),a._kit_layout.getY(i)]
      .map(v => Math.round(v * 1000)).join('/');
    const group = groups.get(key);
    if (group) group.push(i); else groups.set(key, [i]);
    owners[i]=groups.get(key);
  }
  const triangles=new Map<number[],number[]>();
  for(let t=0;t<(index?.count??a.position.count);t+=3){
    if(t%3072===0)yield;
    const ia=index?index.getX(t):t, group=owners[ia];
    if(!group)continue;
    const ib=index?index.getX(t+1):t+1,ic=index?index.getX(t+2):t+2;
    if(owners[ib]!==group||owners[ic]!==group)continue;
    const uv=triangles.get(group)??[];
    for(const id of [ia,ib,ic])uv.push(a.uv.getX(id),a.uv.getY(id));
    triangles.set(group,uv);
  }
  const openings: Opening[] = [];
  for (const indices of groups.values()) {
    yield;
    if(!triangles.has(indices))continue;
    const i = indices[0], u = a._kit_extent.getX(i), v = a._kit_extent.getY(i);
    const width = a._kit_extent.getZ(i), height = a._kit_extent.getW(i);
    const sampled = a._kit_detail.getW(i) === 1;
    if (width < .3 || height < .4 || (sampled && (width > 6 || height > 5))) continue;
    const corners = [[u, v], [u + width, v], [u + width, v + height], [u, v + height]];
    const vertices = corners.map(([x, y]) => indices.find(j => Math.abs(a.uv.getX(j) - x) < .002 && Math.abs(a.uv.getY(j) - y) < .002));
    const rectangular=vertices.every(j=>j!==undefined);
    if(sampled&&!rectangular)continue;
    const next=indices.find(j=>Math.abs(a.uv.getX(j)-a.uv.getX(i))>.01);
    if(next===undefined)continue;
    const point=new THREE.Vector3().fromBufferAttribute(a.position,i);
    const tangent=new THREE.Vector3().fromBufferAttribute(a.position,next).sub(point);
    tangent.y-=a.uv.getY(next)-a.uv.getY(i);
    tangent.divideScalar(a.uv.getX(next)-a.uv.getX(i));
    if(Math.abs(tangent.y)>.01||Math.abs(tangent.length()-1)>.02)continue;
    tangent.normalize();
    const origin=point.addScaledVector(tangent,u-a.uv.getX(i)).add(new THREE.Vector3(0,v-a.uv.getY(i),0));
    const opening = {origin, tangent, normal:new THREE.Vector3().fromBufferAttribute(a.normal, i).normalize(), width, height,
      recess:Math.min(.16,width*.12),
      frame:new THREE.Color(a._kit_frame.getX(i), a._kit_frame.getY(i), a._kit_frame.getZ(i)),
      trim:new THREE.Color(a._kit_trim.getX(i), a._kit_trim.getY(i), a._kit_trim.getZ(i))};
    if (sampled) { openings.push(opening); continue; }
    if (!a._kit_floors || !a._kit_opening) continue;
    const bay=a._kit_layout.getY(i), ground=a._kit_floors.getX(i), upper=a._kit_floors.getY(i);
    const w=bay*a._kit_opening.getX(i);
    if (bay < .5 || ground < 1 || upper < 1 || w < .3 || w > 6) continue;
    const levels=[{y:0,unit:ground,base:true}];
    for(let y=ground;y<height;y+=upper)levels.push({y,unit:upper,base:false});
    const surface=triangles.get(indices)??[];
    for (const {y,unit,base} of levels) {
      const h=unit*a._kit_opening.getY(i);
      if(h<.4||h>5)continue;
      // Plain bases and shopfronts have no ordinary ground-floor windows.
      if(base&&(a._kit_detail.getZ(i)%2===1||a._kit_layout.getX(i)%3===2))continue;
      for (let col=0; col<Math.ceil(width/bay); col++) {
        if(base&&a._kit_layout.getX(i)%3===1&&col===a._kit_floors.getZ(i))continue;
        const phase=(col%2*2-1)*a._kit_detail.getX(i);
        const x=(col+.5-phase)*bay-w/2, bottom=y+a._kit_opening.getZ(i)*unit;
        if (x<0 || x+w>width || bottom+h>height) continue;
        if(!surface.length||((!rectangular||surface.length!==12)&&!coversRectangle(surface,u+x,v+bottom,w,h)))continue;
        openings.push({...opening, origin:origin.clone().addScaledVector(tangent,x).add(new THREE.Vector3(0,bottom,0)),
          width:w,height:h,recess:0});
      }
      yield;
    }
  }
  return openings;
}

export function rectangularOpenings(geometry: THREE.BufferGeometry) {
  const steps=openingSteps(geometry);
  let result=steps.next();while(!result.done)result=steps.next();return result.value;
}

const prepared=new WeakSet<THREE.Object3D>(), budget=new PreparationBudget();
/** Bench lab ablation (`?bench=...&off=facades`, qa/lab/ablate.ts): false builds no facade modules. */
export const facadeModuleSwitch = { on: true };

/** The +Z cap faces into the opaque wall/glazing behind every kit member. */
export function facadeModuleGeometry() {
  const geometry=new THREE.BoxGeometry(1,1,1);
  const index=Array.from(geometry.index!.array);
  geometry.setIndex([...index.slice(0,24),...index.slice(30)]);
  geometry.clearGroups();
  return geometry;
}

/** Match the one-sided backing facade, including cameras temporarily inside it. */
export function facadeModuleMaterial(source: THREE.MeshStandardMaterial) {
  const material=new THREE.MeshStandardNodeMaterial({roughness:source.roughness,metalness:source.metalness});
  material.color.copy(source.color);material.userData={...source.userData};
  const plane=attribute('_facade_plane','vec4');
  const eye=modelWorldMatrixInverse.mul(vec4(cameraPosition,1)).xyz;
  material.maskNode=dot(plane.xyz,eye).greaterThanEqual(plane.w);
  return material;
}

/** One box family per spatial cell, instance colour supplies existing facade tints.
 * Profile dimensions are bounded kit priors, not new survey measurements. */
function* moduleSteps(model: THREE.Object3D): Generator<void> {
  if (model.userData.scenery || prepared.has(model) || model.getObjectByName('facade-modules')) return;
  model.updateWorldMatrix(true, true);
  const inverse = model.matrixWorld.clone().invert();
  const cells = new Map<string, { matrix: THREE.Matrix4; colour: THREE.Color; plane: THREE.Plane }[]>();
  const up = new THREE.Vector3(0, 1, 0), scale = new THREE.Vector3();
  const sources: THREE.Mesh[]=[];
  model.traverse(object => {
    const source = object as THREE.Mesh;
    if(source.isMesh&&source.visible)sources.push(source);
  });
  let openingCount=0;
  for(const source of sources){
    const transform = inverse.clone().multiply(source.matrixWorld);
    for (const opening of yield* openingSteps(source.geometry)) {
      if(openingCount++%32===0)yield;
      const { origin, tangent, normal, width:w, height:h } = opening;
      // Box profiles are X-symmetric; derive their orientation from the wall
      // normal so mirrored UVs never create unsupported negative instances.
      const basis = new THREE.Matrix4().makeBasis(normal.clone().cross(up).normalize(),up,normal.clone().negate());
      const wallNormal=normal.clone().applyNormalMatrix(new THREE.Matrix3().getNormalMatrix(transform));
      const plane=new THREE.Plane().setFromNormalAndCoplanarPoint(wallNormal,origin.clone().applyMatrix4(transform));
      // Five capped profiles / 50 triangles per opening. Frames sit in the existing reveal;
      // a shallow metal sill extends past its outer face and sheds rain.
      const frame = Math.min(.055, w * .045);
      const depth = opening.recess;
      for (const [x, y, z, sx, sy, sz, sill] of [
        [frame/2, h/2, .025, frame, h, .07, 0], [w-frame/2, h/2, .025, frame, h, .07, 0],
        [w/2, h-frame/2, .025, w-2*frame, frame, .07, 0], [w/2, frame/2, .025, w-2*frame, frame, .07, 0],
        [w/2, -.025, depth/2, w+.08, .05, depth+.16, 1],
      ]) {
        const position = origin.clone().addScaledVector(tangent, x).addScaledVector(up, y).addScaledVector(normal, z);
        const matrix = basis.clone().scale(scale.set(sx, sy, sz)).setPosition(position).premultiply(transform);
        const p = new THREE.Vector3().setFromMatrixPosition(matrix);
        const key = `${Math.floor(p.x/64)}/${Math.floor(p.z/64)}`;
        const rows = cells.get(key) ?? []; if (!cells.has(key)) cells.set(key, rows);
        rows.push({matrix, colour:sill ? opening.trim : opening.frame,plane});
      }
    }
  }
  prepared.add(model);
  if (!cells.size) return;
  const root = new THREE.Group(); root.name = 'facade-modules';
  const geometry = facadeModuleGeometry();
  geometry.computeBoundingBox();geometry.computeBoundingSphere();
  const material = new THREE.MeshStandardMaterial({roughness:.62, metalness:.12});
  material.userData.facadeModules=true;
  for (const rows of cells.values()) {
    const cellGeometry=new THREE.BufferGeometry();cellGeometry.index=geometry.index;
    cellGeometry.attributes={...geometry.attributes};cellGeometry.boundingBox=geometry.boundingBox;cellGeometry.boundingSphere=geometry.boundingSphere;
    const planes=new THREE.InstancedBufferAttribute(new Float32Array(rows.length*4),4);
    cellGeometry.setAttribute('_facade_plane',planes);
    const mesh = new THREE.InstancedMesh(cellGeometry, material, rows.length);
    mesh.name = 'facade-frame-sill'; mesh.userData.facadeModules = true;
    for(let i=0;i<rows.length;i++){
      if(i%128===0)yield;
      mesh.setMatrixAt(i,rows[i].matrix);mesh.setColorAt(i,rows[i].colour);
    }
    mesh.computeBoundingBox(); mesh.computeBoundingSphere();
    const centre = mesh.boundingSphere!.center.clone();
    const shift = new THREE.Matrix4().makeTranslation(centre.clone().negate());
    for(let i=0;i<rows.length;i++){
      if(i%128===0)yield;
      mesh.setMatrixAt(i,rows[i].matrix.premultiply(shift));
      const p=rows[i].plane;
      planes.setXYZW(i,p.normal.x,p.normal.y,p.normal.z,-p.constant-p.normal.dot(centre));
    }
    // Translation preserves the existing bounds. One millimetre covers the
    // second Float32 instance upload without scanning every prototype again.
    mesh.boundingBox!.translate(centre.clone().negate()).expandByScalar(.001);
    mesh.boundingSphere!.center.sub(centre);mesh.boundingSphere!.radius+=.001;
    mesh.castShadow = true; mesh.receiveShadow = true;
    const lod = new THREE.LOD(); lod.position.copy(centre);
    lod.addLevel(mesh, 0); lod.addLevel(new THREE.Object3D(), 110);
    root.add(lod);
  }
  const instances=[...cells.values()].reduce((sum,rows)=>sum+rows.length,0);
  root.userData = {instances, triangles:instances*10, cells:cells.size,
    instanceBytes:instances*92};
  model.add(root);
}

function recordPreparation(model: THREE.Object3D, cpuMs: number, maxSliceMs: number, wallMs: number) {
  const root=model.getObjectByName('facade-modules');
  if(root)Object.assign(root.userData,{prepareMs:cpuMs,maxSliceMs,prepareWallMs:wallMs});
}

export function addFacadeModules(model: THREE.Object3D) {
  if(!facadeModuleSwitch.on||prepared.has(model)||model.userData.scenery)return;
  const start=performance.now();for(const _ of moduleSteps(model))void _;
  const elapsed=performance.now()-start;recordPreparation(model,elapsed,elapsed,elapsed);
}

/** Streaming paths yield between bounded parser/construction slices while hidden. */
export async function prepareFacadeModules(model: THREE.Object3D) {
  if(!facadeModuleSwitch.on||prepared.has(model)||model.userData.scenery)return;
  const start=performance.now(),steps=moduleSteps(model);let cpuMs=0,maxSliceMs=0;
  while(true){
    const before=performance.now(),result=steps.next(),elapsed=performance.now()-before;
    cpuMs+=elapsed;maxSliceMs=Math.max(maxSliceMs,elapsed);
    if(result.done)break;
    const pending=budget.checkpoint();if(pending)await pending;
  }
  recordPreparation(model,cpuMs,maxSliceMs,performance.now()-start);
}
