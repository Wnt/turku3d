import * as THREE from 'three/webgpu';

export interface AuthoredWheelLayout {
  name:string; pivot:{x:number;y:number;z:number}; radius:number; width:number;
}
export interface AuthoredWheelPart extends AuthoredWheelLayout { geometry:THREE.BufferGeometry }
export interface AuthoredFleetParts {body:THREE.BufferGeometry;wheels:AuthoredWheelPart[]}

/** Named new assets are split before flattening, preserving the existing material
 * baker and all its glass/lamp attributes. Geometry and pivots are asset-local. */
export function splitNamedFleetWheels(root:THREE.Object3D,
  bake:(root:THREE.Object3D,exclude?:ReadonlySet<THREE.Object3D>)=>THREE.BufferGeometry):AuthoredFleetParts|null {
  const names=['wheel_FL','wheel_FR','wheel_RL','wheel_RR'];
  const objects=names.map(name=>root.getObjectByName(name));
  if(objects.every(o=>!o))return null;
  if(objects.some(o=>!o))throw Error('Incomplete named fleet wheel set');
  root.updateMatrixWorld(true);
  const inverse=root.matrixWorld.clone().invert(),exclude=new Set(objects as THREE.Object3D[]);
  const body=bake(root,exclude).applyMatrix4(inverse);
  const wheels=objects.map((object,i)=>{
    const pivot=object!.getWorldPosition(new THREE.Vector3()).applyMatrix4(inverse);
    const geometry=bake(object!).applyMatrix4(inverse);geometry.translate(-pivot.x,-pivot.y,-pivot.z);
    geometry.computeBoundingBox();geometry.computeBoundingSphere();const box=geometry.boundingBox!;
    return {name:names[i],pivot:{x:pivot.x,y:pivot.y,z:pivot.z},radius:(box.max.y-box.min.y)/2,width:box.max.x-box.min.x,geometry};
  });
  return {body,wheels};
}
