// The building material families: `params → three material`, procedural PBR at true metric scale.
//
// A compiled building names a family and its parameters per primitive (material.extras.turku in its GLB, see
// docs/design/world-pipeline.md and docs/specs/building-description.md). UVs are metres on the wall or along the roof
// slope, so a pattern keeps its real size on every building. Colours come from the building's description; the
// pattern maps (buildingPatterns.ts) carry only structure: tone, joints, roughness, occlusion and relief.
import * as THREE from 'three/webgpu';
import { attribute, float, fract, mix, normalMap, sin, smoothstep, step, texture, uniform, uv, vec2, vec3 } from 'three/tsl';
import { buildingPattern, weatherMap } from './buildingPatterns';

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

// Every number and colour of a surface is a uniform, so all surfaces with the same structure (plain, banded, ashlar)
// are one shader program whatever their family, colours and scale: a street of differently painted houses costs
// one program build, not one per colour.
function surface(family: string, p: BuildingMaterialParams) {
  const pattern = buildingPattern({ family, finish: p.finish, bond: p.bond, orientation: p.orientation, profile: p.profile, scale_m: p.scale_m });
  const material = new THREE.MeshStandardNodeMaterial();
  const [roughness, metalness] = SURFACE[family] ?? [0.9, 0];
  const main = uniform(new THREE.Color(p.colour ?? '#b0a898'));
  const second = uniform(p.colour2 ? new THREE.Color(p.colour2) : shade(p.colour ?? '#b0a898', family === 'weatherboard' || family === 'painted_wood' ? 0.45 : 0.6));
  const at = uv().div(uniform(new THREE.Vector2(pattern.size[0], pattern.size[1])));
  const sample = texture(pattern.map, at);
  // Weather: soft patches, blotches and rain streaks from one shared map read at two unrelated scales, in metres on
  // the wall. `weathering` 0 is a wall painted this year, 1 one that has stood untouched for decades.
  const weathering = p.weathering ?? 0.35;
  const worn = uniform(0.5 + weathering);
  const near = texture(weatherMap(), uv().div(vec2(9.3, 7.1))), wide = texture(weatherMap(), uv().div(vec2(23.7, 17.3)).add(vec2(0.37, 0.61)));
  const patches = near.r.sub(0.5).mul(0.6).add(wide.r.sub(0.5)).add(near.b.sub(0.5).mul(0.35));
  const streaks = near.g.mul(wide.b.add(0.5));
  const macro = float(1).add(patches.mul(worn.mul(0.3))).mul(float(1).sub(streaks.mul(worn.mul(0.25))));
  let base = mix(main, second, sample.g).mul(sample.r.sub(0.5).mul(uniform(pattern.tone)).add(1)).mul(sample.a).mul(macro);
  if (p.relief === 'banded' || p.relief === 'ashlar' || p.relief === 'v_joint') {
    const course = uniform(p.course_m ?? 0.4), y = fract(uv().y.div(course));
    let groove = float(1).sub(smoothstep(float(0), float(0.035).div(course), y.min(float(1).sub(y))));
    if (p.relief === 'ashlar') {
      const block = course.mul(2.2);
      const x = fract(uv().x.div(block).add(step(1, uv().y.div(course).mod(2)).mul(0.5)));
      groove = groove.max(float(1).sub(smoothstep(float(0), float(0.035).div(block), x.min(float(1).sub(x)))));
    }
    base = base.mul(float(1).sub(groove.mul(0.4)));
  }
  // Walls gather dirt near the ground; uv.y is the height above the wall base. Roof families take none.
  const roofing = family === 'sheet_metal' || family === 'clay_tile' || family === 'felt' || family === 'concrete_tile';
  const grime = float(1).sub(smoothstep(1.6, 0.0, uv().y).mul(uniform(roofing ? 0 : 0.12 * (0.5 + weathering))));
  material.colorNode = base.mul(grime);
  material.roughnessNode = uniform(p.roughness ?? roughness).add(sample.b.sub(0.5).mul(uniform(pattern.rough))).clamp(0.04, 1);
  material.metalnessNode = uniform(p.metalness ?? metalness);
  material.normalNode = normalMap(texture(pattern.normal, at), vec2(uniform(RELIEF[family] ?? 0.8)));
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
  const room = mix(vec3(0.02, 0.022, 0.026), shop ? vec3(0.2, 0.18, 0.15) : vec3(0.1, 0.095, 0.085), tone.mul(tone))
    .mul(at.y.mul(0.5).add(0.6));
  const edge = at.x.min(float(1).sub(at.x));
  const width = curtain.mul(0.22).add(0.08);
  const inCurtain = step(edge, width).mul(step(0.001, curtain));
  const fold = sin(at.x.mul(seed.mul(60).add(70))).mul(0.16).add(0.84);
  // Curtains and blinds hang right behind the pane in full daylight: white cloth reads nearly as light as a white
  // window frame from the street (two passes through the glass take about a third).
  const cloth = mix(vec3(0.86, 0.84, 0.79), vec3(0.66, 0.56, 0.44), step(0.7, seed)).mul(fold).mul(0.64);
  const inBlind = step(float(1).sub(blind), at.y).mul(step(0.001, blind));
  const slats = sin(at.y.mul(office ? 90 : 140)).mul(0.12).add(0.88);
  const blindColour = vec3(0.84, 0.83, 0.8).mul(slats).mul(0.68);
  let inside = mix(room, cloth, inCurtain);
  inside = mix(inside, blindColour, inBlind);
  if (shop) {
    // A lit shop behind the pane: a pale back wall that brightens towards the ceiling lights, a light strip under
    // the ceiling and dark goods of uneven height along the floor; `seed` and `tone` make every pane differ.
    const back = mix(vec3(0.1, 0.095, 0.085), vec3(0.36, 0.33, 0.29), tone).mul(at.y.mul(0.75).add(0.5));
    const lights = smoothstep(0.84, 0.88, at.y).mul(smoothstep(0.97, 0.93, at.y)).mul(0.35);
    const cell = at.x.mul(seed.mul(4).add(3)).add(seed.mul(7));
    const height = fract(sin(cell.floor().mul(12.9898)).mul(43758.5453)).mul(0.26).add(0.14);
    const goods = step(at.y, height).mul(step(fract(cell), 0.82));
    const stock = mix(vec3(0.05, 0.045, 0.04), vec3(0.16, 0.1, 0.07), fract(cell.floor().mul(0.37).add(seed)));
    inside = mix(back.add(lights), stock, goods);
    // Offices and closed shops on a shop floor keep white vertical blinds drawn across the whole pane.
    const drawn = vec3(0.8, 0.79, 0.75).mul(sin(at.x.mul(seed.mul(120).add(160))).mul(0.1).add(0.9)).mul(0.64);
    inside = mix(inside, drawn, step(0.62, tone));
  }
  if (p.interior === 'frosted') inside = vec3(0.35, 0.37, 0.38);
  if (p.tint) inside = inside.mul(uniform(new THREE.Color(p.tint)).mul(2).add(0.3));
  material.colorNode = inside;
  material.roughnessNode = float(p.interior === 'frosted' ? 0.5 : 0.045);
  material.metalnessNode = float(0);
  // Double and triple glazing reflects at every pane surface: about a fifth of the sky at the default reflectivity,
  // against a twenty-fifth for one dielectric interface.
  material.ior = 1.5 + 1.85 * (p.reflectivity ?? 0.6);
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
