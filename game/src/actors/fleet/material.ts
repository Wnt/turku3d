import * as THREE from 'three/webgpu';
import { attribute, mix, select, sRGBTransferEOTF, uniform } from 'three/tsl';
import type Node from 'three/src/nodes/core/Node.js';
import { fleetLampNode } from './lamps';
import { paintNode } from '../parkedPaint';

const decode = sRGBTransferEOTF as unknown as (srgb: Node<'vec3'>) => Node<'vec3'>;
export const fleetNight = uniform(0);
let shared: THREE.MeshStandardNodeMaterial | undefined;
/** All 56 models and three LODs share this material and shader pipeline. Glass and trim
 * have independent PBR values without material groups or transparent sorting per car. */
export function fleetMaterial(): THREE.MeshStandardNodeMaterial {
  if (shared) return shared;
  const material = new THREE.MeshStandardNodeMaterial({ name: 'Finnish fleet / shared surfaces', side: THREE.DoubleSide });
  const surface=attribute('fleetSurface','vec4'), color=attribute('color','vec3');
  material.colorNode=mix(color,decode(paintNode()).mul(color),surface.x);
  material.roughnessNode=surface.y;
  material.metalnessNode=surface.z;
  const lamps=fleetLampNode();
  // Surface tags: running lamps <2, left amber=2, right amber=3, rear brake lens=4.
  const emission=select(surface.w.greaterThan(3.5),lamps.y.mul(2.5),
    select(surface.w.greaterThan(2.5),lamps.w,
      select(surface.w.greaterThan(1.5),lamps.z,surface.w.mul(fleetNight.mul(.9).add(.1)))));
  material.emissiveNode=color.mul(emission).mul(lamps.x).mul(3);
  return shared=material;
}
