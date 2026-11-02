import * as THREE from 'three/webgpu';
import { Fn, instancedBufferAttribute, uniform } from 'three/tsl';
import type Node from 'three/src/nodes/core/Node.js';
import type NodeBuilder from 'three/src/nodes/core/NodeBuilder.js';
const states=new WeakMap<THREE.Object3D,THREE.InstancedBufferAttribute>();
/** Parked engines are off; ambient traffic and driven actors power their lamps. */
export function fleetLamps(mesh:THREE.InstancedMesh, powered:readonly number[], indicators:readonly number[]=[], brakes:readonly number[]=[]) {
  let attribute=states.get(mesh);
  if(!attribute){attribute=new THREE.InstancedBufferAttribute(new Float32Array(mesh.instanceMatrix.count*4),4);states.set(mesh,attribute);}
  let dirty=false;
  powered.forEach((value,i)=>{
    const brake=brakes[i]??0,left=Number((indicators[i]??0)<0),right=Number((indicators[i]??0)>0);
    if(attribute!.getX(i)!==value||attribute!.getY(i)!==brake||attribute!.getZ(i)!==left||attribute!.getW(i)!==right){
      attribute!.setXYZW(i,value,brake,left,right);dirty=true;
    }
  });
  if(dirty)attribute.needsUpdate=true;
}
export const fleetLampNode=Fn((builder:NodeBuilder)=>{
  const attribute=states.get(builder.object);
  return attribute?instancedBufferAttribute(attribute,'vec4'):uniform(new THREE.Vector4(1,0,0,0));
}) as unknown as ()=>Node<'vec4'>;
