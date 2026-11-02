import * as THREE from 'three/webgpu';
import { Fn, instancedBufferAttribute, uniform } from 'three/tsl';
import type Node from 'three/src/nodes/core/Node.js';
import type NodeBuilder from 'three/src/nodes/core/NodeBuilder.js';
const states=new WeakMap<THREE.Object3D,THREE.InstancedBufferAttribute>();
const actors=new WeakMap<THREE.Object3D,THREE.Vector4>();
const DEFAULT_STATE=new THREE.Vector4(1,0,0,0);
export function lampState(powered:boolean,indicator:-1|0|1=0,braking=false):readonly [number,number,number,number]{
  return [powered?1:0,indicator===-1?1:0,indicator===1?1:0,braking?1:0];
}
/** Instance state is powered / left / right / brake. Static parked engines are off. */
export function fleetLamps(mesh:THREE.InstancedMesh,values:readonly number[]) {
  let attribute=states.get(mesh);
  if(!attribute){attribute=new THREE.InstancedBufferAttribute(new Float32Array(mesh.instanceMatrix.count*4),4);states.set(mesh,attribute);}
  let first=Infinity,last=-1;
  for(let i=0;i<values.length;i++)if(attribute.array[i]!==values[i]){attribute.array[i]=values[i];first=Math.min(first,i);last=i;}
  if(last>=0){attribute.addUpdateRange(first,last-first+1);attribute.needsUpdate=true;}
}
export function fleetActorLamps(mesh:THREE.Mesh,powered=true,indicator:-1|0|1=0,braking=false){
  let value=actors.get(mesh);if(!value){value=new THREE.Vector4();actors.set(mesh,value);}value.set(...lampState(powered,indicator,braking));mesh.userData.fleetLamps=value.toArray();
}
function actorState(object:THREE.Object3D){
  let state=actors.get(object);
  if(!state&&Array.isArray(object.userData.fleetLamps)){state=new THREE.Vector4().fromArray(object.userData.fleetLamps);actors.set(object,state);}
  return state??DEFAULT_STATE;
}
export const fleetLampNode=Fn((builder:NodeBuilder)=>{
  const attribute=states.get(builder.object);
  return attribute?instancedBufferAttribute(attribute,'vec4'):uniform(new THREE.Vector4(1,0,0,0)).onObjectUpdate(({object})=>actorState(object!));
}) as unknown as ()=>Node<'vec4'>;
