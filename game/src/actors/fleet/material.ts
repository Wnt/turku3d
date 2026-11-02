import * as THREE from 'three/webgpu';
import { attribute, mix, sRGBTransferEOTF, uniform, step } from 'three/tsl';
import type Node from 'three/src/nodes/core/Node.js';
import { fleetLampNode } from './lamps';
import { paintNode } from '../parkedPaint';

const decode = sRGBTransferEOTF as unknown as (srgb: Node<'vec3'>) => Node<'vec3'>;
export const fleetNight = uniform(0);
let shared: THREE.MeshPhysicalNodeMaterial | undefined;
/** All 56 models and three LODs share this material and shader pipeline. Glass and trim
 * have independent PBR values without material groups or transparent sorting per car. */
export function fleetMaterial(): THREE.MeshPhysicalNodeMaterial {
  if (shared) return shared;
  const material = new THREE.MeshPhysicalNodeMaterial({ name: 'Finnish fleet / shared surfaces', side: THREE.DoubleSide });
  const surface=attribute('fleetSurface','vec4'), color=attribute('color','vec3');
  material.colorNode=mix(color,decode(paintNode()).mul(color),surface.x);
  material.clearcoatNode=surface.x.clamp(0,1).mul(.65);
  material.clearcoatRoughness=.18;
  material.roughnessNode=surface.y;
  material.metalnessNode=surface.z;
  const lamps=fleetLampNode(),signal=step(1.5,surface.w),right=step(2.5,surface.w),left=signal.sub(right);
  const rear=step(.4,surface.w).mul(step(.8,surface.w).oneMinus());
  const running=surface.w.mul(signal.oneMinus()).mul(fleetNight.mul(.9).add(.1)).mul(lamps.x).mul(3);
  const brake=rear.mul(lamps.w).mul(4),blink=left.mul(lamps.y).add(right.mul(lamps.z)).mul(5);
  material.emissiveNode=color.mul(running.add(brake).add(blink));
  return shared=material;
}
