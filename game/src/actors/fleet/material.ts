import * as THREE from 'three/webgpu';
import { attribute, mix, sRGBTransferEOTF, uniform, step } from 'three/tsl';
import type Node from 'three/src/nodes/core/Node.js';
import { fleetLampNode } from './lamps';
import { paintNode } from '../parkedPaint';

const decode = sRGBTransferEOTF as unknown as (srgb: Node<'vec3'>) => Node<'vec3'>;
export const fleetNight = uniform(0);
const shared=new Map<boolean,THREE.MeshStandardNodeMaterial>();
/** Two shared pipelines: close paint clearcoat, economical PBR at distance. Glass and trim
 * have independent surface values without material groups or transparent sorting per car. */
export function fleetMaterial(lod=0): THREE.MeshStandardNodeMaterial {
  const close=lod===0,cached=shared.get(close);if(cached)return cached;
  const material=close?new THREE.MeshPhysicalNodeMaterial({name:'Finnish fleet / close clearcoat',side:THREE.DoubleSide})
    :new THREE.MeshStandardNodeMaterial({name:'Finnish fleet / distant surfaces',side:THREE.DoubleSide});
  const surface=attribute('fleetSurface','vec4'), color=attribute('color','vec3');
  material.colorNode=mix(color,decode(paintNode()).mul(color),surface.x);
  if(material instanceof THREE.MeshPhysicalNodeMaterial){material.clearcoatNode=surface.x.clamp(0,1).mul(.65);material.clearcoatRoughness=.18;}
  material.roughnessNode=surface.y;
  material.metalnessNode=surface.z;
  const lamps=fleetLampNode(),signal=step(1.5,surface.w),right=step(2.5,surface.w),left=signal.sub(right);
  const rear=step(.4,surface.w).mul(step(.8,surface.w).oneMinus());
  const running=surface.w.mul(signal.oneMinus()).mul(fleetNight.mul(.9).add(.1)).mul(lamps.x).mul(3);
  const brake=rear.mul(lamps.w).mul(4),blink=left.mul(lamps.y).add(right.mul(lamps.z)).mul(5);
  material.emissiveNode=color.mul(running.add(brake).add(blink));
  shared.set(close,material);return material;
}
