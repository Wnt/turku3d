import * as THREE from 'three/webgpu';
import { Fn, instancedBufferAttribute, uniform } from 'three/tsl';
import type Node from 'three/src/nodes/core/Node.js';
import type NodeBuilder from 'three/src/nodes/core/NodeBuilder.js';
const states=new WeakMap<THREE.Object3D,THREE.InstancedBufferAttribute>();
/** Parked engines are off; ambient traffic and driven actors power their lamps. */
export function fleetLamps(mesh:THREE.InstancedMesh, powered:readonly number[]) {
  let attribute=states.get(mesh);
  if(!attribute){attribute=new THREE.InstancedBufferAttribute(new Float32Array(mesh.instanceMatrix.count),1);states.set(mesh,attribute);}
  let dirty=false;
  powered.forEach((value,i)=>{if(attribute!.getX(i)!==value){attribute!.setX(i,value);dirty=true;}});
  if(dirty)attribute.needsUpdate=true;
}
export const fleetLampNode=Fn((builder:NodeBuilder)=>{
  const attribute=states.get(builder.object);
  return attribute?instancedBufferAttribute(attribute,'float'):uniform(1);
}) as unknown as ()=>Node<'float'>;
