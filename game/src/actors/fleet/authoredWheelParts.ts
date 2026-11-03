import * as THREE from 'three/webgpu';
import {weldGeometryAsync} from '../../world/weldGeometry';

export interface AuthoredWheelLayout {
  name:string; pivot:{x:number;y:number;z:number}; radius:number; width:number;
}
export interface AuthoredWheelPart extends AuthoredWheelLayout { geometry:THREE.BufferGeometry }
export interface AuthoredFleetParts {body:THREE.BufferGeometry;wheels:AuthoredWheelPart[]}

async function subset(source:THREE.BufferGeometry,indices:number[],pivot?:{x:number;y:number;z:number}) {
  const view=new THREE.BufferGeometry();
  for(const [name,attribute] of Object.entries(source.attributes))view.setAttribute(name,attribute);
  view.setIndex(indices);
  const expanded=view.toNonIndexed();view.dispose();
  if(pivot)expanded.translate(-pivot.x,-pivot.y,-pivot.z);
  const compact=await weldGeometryAsync(expanded,1e-5);expanded.dispose();
  compact.computeBoundingBox();compact.computeBoundingSphere();return compact;
}

/** Legacy assets have one baked mesh. Explicit hub metadata and material roles let
 * us partition its existing tyre/alloy triangles without changing any surface. */
export async function splitEmbeddedFleetWheels(source:THREE.BufferGeometry,layout:readonly AuthoredWheelLayout[]):Promise<AuthoredFleetParts> {
  if(layout.length!==4)throw Error('Fleet wheel layout must contain four hubs');
  const positions=source.getAttribute('position'),surface=source.getAttribute('fleetSurface'),index=source.index;
  if(!positions||!surface)throw Error('Expected flattened fleet attributes');
  const buckets=Array.from({length:5},()=>[] as number[]),count=index?.count??positions.count;
  for(let i=0;i<count;i+=3){
    const triangle=[0,1,2].map(k=>index?index.getX(i+k):i+k);
    const wheel=layout.findIndex(w=>triangle.every(v=>{
      if(surface.getX(v)>.5||!(surface.getY(v)>.75||surface.getZ(v)>.45))return false;
      const y=positions.getY(v)-w.pivot.y,z=positions.getZ(v)-w.pivot.z;
      return Math.abs(positions.getX(v)-w.pivot.x)<=w.width/2+.025
        &&y*y+z*z<=(w.radius+.0005)**2;
    }));
    buckets[wheel+1].push(...triangle);
  }
  if(buckets.slice(1).some(b=>!b.length))throw Error('Embedded asset does not match its wheel metadata');
  return {body:await subset(source,buckets[0]),wheels:await Promise.all(layout.map(async (w,i)=>({...w,geometry:await subset(source,buckets[i+1],w.pivot)})))};
}

/** Named new assets are split before flattening, preserving the existing material
 * baker and all its glass/lamp attributes. Geometry and pivots are asset-local. */
export async function splitNamedFleetWheels(root:THREE.Object3D,
  bake:(root:THREE.Object3D,exclude?:ReadonlySet<THREE.Object3D>)=>THREE.BufferGeometry|Promise<THREE.BufferGeometry>):Promise<AuthoredFleetParts|null> {
  const names=['wheel_FL','wheel_FR','wheel_RL','wheel_RR'];
  const objects=names.map(name=>root.getObjectByName(name));
  if(objects.every(o=>!o))return null;
  if(objects.some(o=>!o))throw Error('Incomplete named fleet wheel set');
  root.updateMatrixWorld(true);
  const inverse=root.matrixWorld.clone().invert(),exclude=new Set(objects as THREE.Object3D[]);
  // Pivots are read before the bakes yield: the scene graph is not touched meanwhile, but keep it explicit.
  const pivots=objects.map(object=>object!.getWorldPosition(new THREE.Vector3()).applyMatrix4(inverse));
  const [baked,...bakedWheels]=await Promise.all([bake(root,exclude),...objects.map(object=>bake(object!))]);
  const body=baked.applyMatrix4(inverse);
  const wheels=objects.map((_,i)=>{
    const pivot=pivots[i];
    const geometry=bakedWheels[i].applyMatrix4(inverse);geometry.translate(-pivot.x,-pivot.y,-pivot.z);
    geometry.computeBoundingBox();geometry.computeBoundingSphere();const box=geometry.boundingBox!;
    return {name:names[i],pivot:{x:pivot.x,y:pivot.y,z:pivot.z},radius:(box.max.y-box.min.y)/2,width:box.max.x-box.min.x,geometry};
  });
  return {body,wheels};
}
