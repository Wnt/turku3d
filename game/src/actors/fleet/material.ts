import * as THREE from 'three/webgpu';
import { abs, atan, attribute, dot, float, floor, fract, length, max, mix, positionGeometry, select, sin, smoothstep, sRGBTransferEOTF, step, uniform, vec2, vec3 } from 'three/tsl';
import type Node from 'three/src/nodes/core/Node.js';
import { fleetLampNode } from './lamps';
import { paintNode } from '../parkedPaint';
import { deformByCage, surfaceMask, windowStage } from './damageRig';

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
  return damageable ??= wornPaint(deformByCage(buildFleetMaterial('Finnish fleet / damage-capable surfaces')));
}
/** Surface wear on a damage-capable body (vehicle/damage.ts surface cells, damageRig.ts surfaceMask): light scratches
 *  as thin streaks along the car, bare metal where the wear is deep (patchy at its edge), and dirt in blotches; the
 *  detail follows the rest-frame body, so it moves with a dented panel. Scratches and bare metal only on paint. */
function wornPaint(material: THREE.MeshPhysicalNodeMaterial): THREE.MeshPhysicalNodeMaterial {
  const m = surfaceMask(), wear = m.x, dirt = m.y, surface = attribute('fleetSurface', 'vec4'), paint = surface.x, p = positionGeometry;
  const hash = (v: Node<'vec2'>) => fract(sin(dot(v, vec2(12.9898, 78.233))).mul(43758.5453));
  // Long thin streaks along the car (40 cm by 5 mm cells, staggered row by row).
  const row = floor(p.y.add(p.x.mul(.3)).mul(200)), streak = hash(vec2(floor(p.z.mul(2.5).add(hash(vec2(row, 3.1)).mul(4))), row));
  const blot = hash(floor(vec2(p.z.mul(12).add(p.x.mul(5)), p.y.mul(12).add(p.x.mul(9))))), blot2 = hash(floor(vec2(p.z.mul(7), p.y.mul(7).add(p.x.mul(7)))).add(7.1));
  const scratches = step(float(1).sub(wear.mul(.85)), streak).mul(step(.03, wear)).mul(paint);
  const loss = smoothstep(.5, .8, wear.add(blot.sub(.5).mul(.4))).mul(paint), grime = dirt.mul(1.8).min(1).mul(smoothstep(.15, .6, blot2));
  const color = material.colorNode as Node<'vec3'>, roughness = material.roughnessNode as Node<'float'>, metalness = material.metalnessNode as Node<'float'>;
  const clearcoat = material.clearcoatNode as Node<'float'>;
  material.colorNode = mix(mix(mix(color, vec3(.8, .8, .78), scratches.mul(.65)), vec3(.47, .48, .5), loss), vec3(.2, .17, .13), grime.mul(.85));
  material.roughnessNode = mix(mix(roughness, float(.42), loss).add(scratches.mul(.2)), float(.95), grime);
  material.metalnessNode = mix(metalness, float(.85), loss).mul(float(1).sub(grime.mul(.8)));
  material.clearcoatNode = clearcoat.mul(float(1).sub(loss)).mul(float(1).sub(grime));
  return material;
}
/** Glazing of the cars drawn on their own (starter, driven, remote): they carry a seated driver (actors/driver), so the
 *  cabin reads through it like GTA's tinted glass: the source glass's opacity and metalness scaled down, its tint
 *  darkened. Measured on city.lab: at the source values (opacity 0.74, metalness 0.7, a pale tint) the driver was not
 *  visible from any view. */
const CABIN_GLASS = { opacity: 0.34, metalness: 0.15, tint: 0.45 };
export function fleetGlassDamageMaterial(): THREE.MeshPhysicalNodeMaterial {
  return damageableGlass ??= crackedGlass(deformByCage(buildGlass('Finnish fleet / damage-capable glass', CABIN_GLASS), true));
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
  // The cabin glass's own (see-through) tint and opacity under the cracks.
  const opacity = surface.w.mul(CABIN_GLASS.opacity);
  material.colorNode = mix(attribute('color', 'vec3').mul(CABIN_GLASS.tint), vec3(.9, .92, .93), max(crack, haze));
  material.opacityNode = max(opacity, max(crack.mul(.9), haze.add(opacity)));
  material.roughnessNode = mix(surface.y, float(.55), max(crack, haze));
  return material;
}
function buildFleetMaterial(name: string): THREE.MeshPhysicalNodeMaterial {
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
function buildGlass(name: string, scale = { opacity: 1, metalness: 1, tint: 1 }): THREE.MeshPhysicalNodeMaterial {
  const material = new THREE.MeshPhysicalNodeMaterial({ name,
    transparent: true, depthWrite: false, side: THREE.DoubleSide, forceSinglePass: true,
    clearcoat: 1, clearcoatRoughness: .08 });
  const surface=attribute('fleetSurface','vec4');
  material.colorNode=attribute('color','vec3').mul(scale.tint);
  material.roughnessNode=surface.y; material.metalnessNode=surface.z.mul(scale.metalness);
  material.opacityNode=surface.w.mul(scale.opacity);
  return material;
}
