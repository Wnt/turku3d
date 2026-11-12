// The building material families: `params → three material`, procedural PBR at true metric scale.
//
// A compiled building names a family and its parameters per primitive (material.extras.turku in its GLB, see
// docs/design/world-pipeline.md and docs/specs/building-description.md). UVs are metres on the wall or along the roof
// slope, so a pattern keeps its real size on every building. Colours come from the building's description; the
// pattern maps (buildingPatterns.ts) carry only structure: tone, joints, roughness, occlusion and relief.
import * as THREE from 'three/webgpu';
import { attribute, color, float, fract, mix, normalMap, sin, smoothstep, step, texture, uv, vec2, vec3 } from 'three/tsl';
import { buildingPattern } from './buildingPatterns';

export interface BuildingMaterialParams {
  colour?: string; colour2?: string; finish?: string; bond?: string; orientation?: string; profile?: string;
  scale_m?: number; weathering?: number; relief?: string; course_m?: number;
  roughness?: number; metalness?: number; reflectivity?: number; tint?: string; interior?: string;
}

/** Base roughness and metalness per family; the pattern's roughness channel varies round the base. */
const SURFACE: Record<string, [number, number]> = {
  plaster: [0.93, 0], brick: [0.9, 0], weatherboard: [0.78, 0], log: [0.85, 0], granite: [0.72, 0], rubble: [0.9, 0],
  stone: [0.65, 0], concrete: [0.9, 0], ceramic_tile: [0.4, 0], sheet_metal: [0.48, 0.55], clay_tile: [0.85, 0],
  concrete_tile: [0.9, 0], felt: [0.95, 0], slate: [0.65, 0], painted_wood: [0.62, 0], metal: [0.45, 0.7], copper: [0.6, 0.6],
};
/** How strongly the pattern's relief shows per family. */
const RELIEF: Record<string, number> = { plaster: 1, brick: 1, weatherboard: 1, granite: 1, rubble: 1, sheet_metal: 1, clay_tile: 1 };

const cache = new Map<string, THREE.Material>();

function shade(hex: string, factor: number) {
  const c = new THREE.Color(hex);
  return c.multiplyScalar(factor);
}

function surface(family: string, p: BuildingMaterialParams) {
  const pattern = buildingPattern({ family, finish: p.finish, bond: p.bond, orientation: p.orientation, profile: p.profile, scale_m: p.scale_m });
  const material = new THREE.MeshStandardNodeMaterial();
  const [roughness, metalness] = SURFACE[family] ?? [0.9, 0];
  const main = new THREE.Color(p.colour ?? '#b0a898');
  const second = p.colour2 ? new THREE.Color(p.colour2) : shade(p.colour ?? '#b0a898', family === 'weatherboard' || family === 'painted_wood' ? 0.45 : 0.6);
  const at = uv().div(vec2(pattern.size[0], pattern.size[1]));
  const sample = texture(pattern.map, at);
  // The same pattern read at two much larger scales breaks the repeat and gives walls their uneven weathering.
  const far = texture(pattern.map, at.mul(0.137).add(vec2(0.31, 0.17))).r, wide = texture(pattern.map, at.mul(0.031).add(vec2(0.73, 0.41))).r;
  const weathering = p.weathering ?? 0.35;
  const macro = float(1).add(far.sub(0.5).mul(0.22 * (0.5 + weathering))).add(wide.sub(0.5).mul(0.3 * (0.5 + weathering)));
  let base = mix(color(main), color(second), sample.g).mul(sample.r.sub(0.5).mul(pattern.tone).add(1)).mul(sample.a).mul(macro);
  if (p.relief === 'banded' || p.relief === 'ashlar' || p.relief === 'v_joint') {
    const course = p.course_m ?? 0.4, y = fract(uv().y.div(course));
    let groove = float(1).sub(smoothstep(0.0, 0.035 / course, y.min(float(1).sub(y))));
    if (p.relief === 'ashlar') {
      const x = fract(uv().x.div(course * 2.2).add(step(1, uv().y.div(course).mod(2)).mul(0.5)));
      groove = groove.max(float(1).sub(smoothstep(0.0, 0.035 / (course * 2.2), x.min(float(1).sub(x)))));
    }
    base = base.mul(float(1).sub(groove.mul(0.4)));
  }
  // Walls gather dirt near the ground; uv.y is the height above the wall base.
  const grime = family === 'sheet_metal' || family === 'clay_tile' || family === 'felt' || family === 'concrete_tile'
    ? float(1) : float(1).sub(smoothstep(1.6, 0.0, uv().y).mul(0.12 * (0.5 + weathering)));
  material.colorNode = base.mul(grime);
  material.roughnessNode = float(p.roughness ?? roughness).add(sample.b.sub(0.5).mul(pattern.rough)).clamp(0.04, 1);
  material.metalnessNode = float(p.metalness ?? metalness);
  material.normalNode = normalMap(texture(pattern.normal, at), vec2(RELIEF[family] ?? 0.8));
  return material;
}

/**
 * Glass: a dark room behind a reflecting pane. The mesh carries, per window, `_glass` = (curtain, blind, tone, seed)
 * and UVs that run 0..1 across the opening, so every window differs: some have curtains at the sides, some a blind
 * part of the way down, rooms differ in depth of tone. The sky reflection comes from the scene's environment.
 */
function glass(p: BuildingMaterialParams) {
  const material = new THREE.MeshPhysicalNodeMaterial();
  const g = attribute('_glass', 'vec4'), at = uv();
  const curtain = g.x, blind = g.y, tone = g.z, seed = g.w;
  const shop = p.interior === 'shop', office = p.interior === 'office';
  const room = mix(vec3(0.012, 0.014, 0.017), shop ? vec3(0.16, 0.14, 0.11) : vec3(0.075, 0.07, 0.062), tone.mul(tone))
    .mul(at.y.mul(0.5).add(0.6));
  const edge = at.x.min(float(1).sub(at.x));
  const width = curtain.mul(0.22).add(0.08);
  const inCurtain = step(edge, width).mul(step(0.001, curtain));
  const fold = sin(at.x.mul(seed.mul(60).add(70))).mul(0.16).add(0.84);
  const cloth = mix(vec3(0.62, 0.6, 0.55), vec3(0.5, 0.42, 0.34), step(0.7, seed)).mul(fold).mul(0.5);
  const inBlind = step(float(1).sub(blind), at.y).mul(step(0.001, blind));
  const slats = sin(at.y.mul(office ? 90 : 140)).mul(0.12).add(0.88);
  const blindColour = vec3(0.6, 0.59, 0.56).mul(slats).mul(0.55);
  let inside = mix(room, cloth, inCurtain);
  inside = mix(inside, blindColour, inBlind);
  if (p.interior === 'frosted') inside = vec3(0.35, 0.37, 0.38);
  if (p.tint) inside = inside.mul(color(new THREE.Color(p.tint)).mul(2).add(0.3));
  material.colorNode = inside;
  material.roughnessNode = float(p.interior === 'frosted' ? 0.5 : 0.045);
  material.metalnessNode = float(0);
  // Double and triple glazing reflects at every pane surface: stronger than a single dielectric interface.
  material.ior = 1.5 + 0.9 * (p.reflectivity ?? 0.6);
  material.specularIntensity = 1;
  return material;
}

/** The material of a building family, shared by every primitive with the same parameters. */
export function buildingMaterial(family: string, params: BuildingMaterialParams = {}): THREE.Material {
  const key = `${family}:${JSON.stringify(params, Object.keys(params).sort())}`;
  let material = cache.get(key);
  if (!material) {
    if (family === 'glass') material = glass(params);
    else if (family === 'flat') {
      material = new THREE.MeshStandardNodeMaterial({ color: new THREE.Color(params.colour ?? '#808080'), roughness: params.roughness ?? 0.9, metalness: params.metalness ?? 0 });
    } else material = surface(family, params);
    material.name = `building-${family}`;
    cache.set(key, material);
  }
  return material;
}

/** Families this module resolves; the location loader registers them under these names. */
export const BUILDING_FAMILIES = ['plaster', 'brick', 'weatherboard', 'log', 'granite', 'rubble', 'stone', 'concrete', 'ceramic_tile',
  'sheet_metal', 'clay_tile', 'concrete_tile', 'felt', 'slate', 'glass', 'painted_wood', 'metal', 'copper'] as const;
