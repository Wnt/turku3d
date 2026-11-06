import * as THREE from 'three/webgpu';
import { abs, atan, attribute, float, fract, length, max, mix, select, sin, smoothstep, sRGBTransferEOTF, step, uniform, vec3 } from 'three/tsl';
import type Node from 'three/src/nodes/core/Node.js';
import { fleetLampNode } from './lamps';
import { paintNode } from '../parkedPaint';
import { deformByCage, windowStage } from './damageRig';

const decode = sRGBTransferEOTF as unknown as (srgb: Node<'vec3'>) => Node<'vec3'>;
export const fleetNight = uniform(0);
let shared: THREE.MeshStandardNodeMaterial | undefined, damageable: THREE.MeshStandardNodeMaterial | undefined;
let sharedGlass: THREE.MeshPhysicalNodeMaterial | undefined, damageableGlass: THREE.MeshPhysicalNodeMaterial | undefined;
/** Opaque fleet surfaces share one material (instanced batches and single actors); glazing uses a separate pass. */
export function fleetMaterial(): THREE.MeshStandardNodeMaterial {
  return shared ??= buildFleetMaterial('Finnish fleet / shared surfaces');
}
/** The same surfaces for cars drawn on their own (starter, driven, remote): their bodies follow the damage cage
 *  (damageRig.ts). One program for all of them, apart from the instanced batches'. */
export function fleetDamageMaterial(): THREE.MeshStandardNodeMaterial {
  return damageable ??= deformByCage(buildFleetMaterial('Finnish fleet / damage-capable surfaces'));
}
export function fleetGlassDamageMaterial(): THREE.MeshPhysicalNodeMaterial {
  return damageableGlass ??= crackedGlass(deformByCage(buildGlass('Finnish fleet / damage-capable glass'), true));
}
/** Glass damage stages on the glazing (vehicle/damage.ts GLASS_STAGE, damageRig.ts windowStage): a crack star around
 *  the window's crack origin (`crackUV`, metres in its plane: spokes and broken rings, about 0.25 m across), then
 *  crazed all over with a milky haze; a shattered window is not drawn at all (the vertex stage collapses it). */
function crackedGlass(material: THREE.MeshPhysicalNodeMaterial): THREE.MeshPhysicalNodeMaterial {
  const stage = windowStage(), uv = attribute('crackUV', 'vec2'), r = length(uv), angle = atan(uv.y, uv.x);
  const spokes = 11, turn = angle.div(2 * Math.PI).mul(spokes).add(sin(r.mul(31)).mul(.18)).add(sin(r.mul(7).add(angle.mul(3))).mul(.12));
  // Distances (m) to the nearest spoke and ring line; rings break into arcs per spoke sector.
  const spoke = abs(fract(turn).sub(.5)).mul(r).mul(2 * Math.PI / spokes);
  const ringPhase = r.mul(16).add(sin(angle.mul(5)).mul(.35)), ring = abs(fract(ringPhase).sub(.5)).div(16);
  const sector = fract(sin(turn.floor().mul(12.9898).add(ringPhase.floor().mul(78.233))).mul(43758.5453));
  const radius = select(stage.greaterThan(1.5), float(1.6), float(.26));
  const lines = max(float(1).sub(smoothstep(.0012, .0035, spoke)), float(1).sub(smoothstep(.001, .003, ring)).mul(step(.45, sector)))
    .mul(float(1).sub(smoothstep(radius.mul(.6), radius, r)));
  const crack = max(lines, float(1).sub(smoothstep(.006, .02, r))).mul(step(.5, stage));
  const haze = step(1.5, stage).mul(.22);
  const surface = attribute('fleetSurface', 'vec4');
  material.colorNode = mix(attribute('color', 'vec3'), vec3(.9, .92, .93), max(crack, haze));
  material.opacityNode = max(surface.w, max(crack.mul(.9), haze.add(surface.w)));
  material.roughnessNode = mix(surface.y, float(.55), max(crack, haze));
  return material;
}
function buildFleetMaterial(name: string): THREE.MeshStandardNodeMaterial {
  const material = new THREE.MeshPhysicalNodeMaterial({ name, side: THREE.DoubleSide });
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
  return material;
}

/** One extra shared instanced pass per visible authored model/LOD, never per vehicle. */
export function fleetGlassMaterial(): THREE.MeshPhysicalNodeMaterial {
  return sharedGlass ??= buildGlass('Finnish fleet / glass');
}
function buildGlass(name: string): THREE.MeshPhysicalNodeMaterial {
  const material = new THREE.MeshPhysicalNodeMaterial({ name,
    transparent: true, depthWrite: false, side: THREE.DoubleSide, forceSinglePass: true,
    clearcoat: 1, clearcoatRoughness: .08 });
  const surface=attribute('fleetSurface','vec4');
  material.colorNode=attribute('color','vec3');
  material.roughnessNode=surface.y; material.metalnessNode=surface.z;
  material.opacityNode=surface.w;
  return material;
}
