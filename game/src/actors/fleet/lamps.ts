import * as THREE from 'three/webgpu';
import { Fn, instancedBufferAttribute, uniform } from 'three/tsl';
import type Node from 'three/src/nodes/core/Node.js';
import type NodeBuilder from 'three/src/nodes/core/NodeBuilder.js';
import { sharedNode } from '../parkedPaint';
const states=new WeakMap<THREE.Object3D,THREE.InstancedBufferAttribute|THREE.InterleavedBufferAttribute>();
/** The lamp-state attribute of a batch (fleetLamps), for program sharing (render/instancePrograms.ts roles). */
export function lampStateOf(mesh:THREE.Object3D){const a=states.get(mesh);return a&&(a as THREE.InstancedBufferAttribute).isInstancedBufferAttribute?a as THREE.InstancedBufferAttribute:undefined;}
/** A batch whose lamp states share an instanced buffer with other per-instance data (damageRig.ts damageBatch). */
export function shareLampBuffer(mesh:THREE.InstancedMesh,attribute:THREE.InterleavedBufferAttribute){states.set(mesh,attribute);}
/** Parked engines are off; ambient traffic and driven actors power their lamps. `indicators`: -1 left, 1 right, 2 both
 *  (hazard lights). */
export function fleetLamps(mesh:THREE.InstancedMesh, powered:readonly number[], indicators:readonly number[]=[], brakes:readonly number[]=[]) {
  let attribute=states.get(mesh);
  if(!attribute){attribute=new THREE.InstancedBufferAttribute(new Float32Array(mesh.instanceMatrix.count*4),4);states.set(mesh,attribute);}
  let dirty=false;
  powered.forEach((value,i)=>{
    const brake=brakes[i]??0,left=Number((indicators[i]??0)<0||indicators[i]===2),right=Number((indicators[i]??0)>0);
    if(attribute!.getX(i)!==value||attribute!.getY(i)!==brake||attribute!.getZ(i)!==left||attribute!.getW(i)!==right){
      attribute!.setXYZW(i,value,brake,left,right);dirty=true;
    }
  });
  if(dirty)attribute.needsUpdate=true;
}
export const fleetLampNode=Fn((builder:NodeBuilder)=>{
  const attribute=states.get(builder.object);
  const shared=attribute as THREE.InterleavedBufferAttribute|undefined;
  if(shared?.isInterleavedBufferAttribute)return sharedNode(shared,'vec4');
  return attribute?instancedBufferAttribute(attribute as THREE.InstancedBufferAttribute,'vec4'):uniform(new THREE.Vector4(0,0,0,0)).onObjectUpdate(({object})=>object?.userData.vehicleLampState ?? new THREE.Vector4(0,0,0,0));
}) as unknown as ()=>Node<'vec4'>;
