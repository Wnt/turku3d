import * as THREE from 'three/webgpu';
import { attribute, mix, select, sRGBTransferEOTF, uniform } from 'three/tsl';
import type Node from 'three/src/nodes/core/Node.js';
import { fleetLampNode } from './lamps';
import { paintNode } from '../parkedPaint';

const decode = sRGBTransferEOTF as unknown as (srgb: Node<'vec3'>) => Node<'vec3'>;
export const fleetNight = uniform(0);
let shared: THREE.MeshStandardNodeMaterial | undefined;
let sharedGlass: THREE.MeshPhysicalNodeMaterial | undefined;
/** Opaque fleet surfaces share one material; authored glazing uses a separate instanced pass. */
export function fleetMaterial(): THREE.MeshStandardNodeMaterial {
  if (shared) return shared;
  const material = new THREE.MeshPhysicalNodeMaterial({ name: 'Finnish fleet / shared surfaces', side: THREE.DoubleSide });
  const surface=attribute('fleetSurface','vec4'), color=attribute('color','vec3');
  material.colorNode=mix(color,decode(paintNode()).mul(color),surface.x);
  material.roughnessNode=surface.y;
  material.metalnessNode=surface.z;
  material.clearcoatNode=surface.x.mul(surface.y.sub(.24).mul(-2).add(1).clamp(0,1)).mul(.65);
  material.clearcoatRoughnessNode=surface.y.mul(.45).clamp(.10,.30);
  const lamps=fleetLampNode();
  // Surface tags: running lamps <2, left amber=2, right amber=3, rear brake lens=4.
  const emission=select(surface.w.greaterThan(3.5),lamps.y.mul(2.5).add(fleetNight.mul(.9).add(.1).mul(.3)),
    select(surface.w.greaterThan(2.5),lamps.w,
      select(surface.w.greaterThan(1.5),lamps.z,surface.w.max(0).mul(fleetNight.mul(.9).add(.1)))));
  material.emissiveNode=color.mul(emission).mul(lamps.x).mul(3);
  return shared=material;
}

/** One extra shared instanced pass per visible authored model/LOD, never per vehicle. */
export function fleetGlassMaterial(): THREE.MeshPhysicalNodeMaterial {
  if (sharedGlass) return sharedGlass;
  const material = new THREE.MeshPhysicalNodeMaterial({ name: 'Finnish fleet / glass',
    transparent: true, depthWrite: false, side: THREE.DoubleSide, forceSinglePass: true,
    clearcoat: 1, clearcoatRoughness: .08 });
  const surface=attribute('fleetSurface','vec4');
  material.colorNode=attribute('color','vec3');
  material.roughnessNode=surface.y; material.metalnessNode=surface.z;
  material.opacityNode=surface.w;
  return sharedGlass=material;
}
