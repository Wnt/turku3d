// The ground material families: `params → three material` for what the ground kind names (asphalt, paving, setts,
// cobbles, gravel, grass, soil, sand, bedrock, kerbstone, road_paint) and the vertex-coloured far level.
//
// A family's maps tile in metres: the mesh's UVs are engine x, z in metres, so a pattern keeps its size and continues
// across cells. Where the old game's photographic ground library has the surface (worn asphalt, mown grass, granite
// gravel, kerb granite, granite setts), its maps are the base, toned to the family's parameters; every other surface
// is synthesised from its parameters (groundSynth.ts). Over the repeat lies variation in world space at two much
// larger scales (worn and damp patches, fading), so no two stretches of a street match; worn asphalt, grass and
// gravel are scattered with the old game's stochastic tiling, and asphalt carries repairs and sealed cracks.
import * as THREE from 'three/webgpu';
import { attribute, cameraPosition, dot, float, floor, fract, mx_noise_float, normalMap, positionWorld, sin, smoothstep, texture, uv, vec2, vec3 } from 'three/tsl';
import type { Node } from 'three/webgpu';
import { registerFamily } from '../materials';
import { GROUND_FAMILIES, synthesise, type GroundParams } from './groundSynth';

/** How strongly a family varies over metres (tone) and how much relief its normals show. */
const LOOK: Record<string, { patch: number; relief: number }> = {
  asphalt: { patch: 0.28, relief: 1 }, paving: { patch: 0.14, relief: 1 }, setts: { patch: 0.14, relief: 1 },
  cobbles: { patch: 0.14, relief: 1 }, gravel: { patch: 0.2, relief: 1 }, grass: { patch: 0.3, relief: 1 },
  soil: { patch: 0.25, relief: 1 }, sand: { patch: 0.15, relief: 1 }, bedrock: { patch: 0.2, relief: 1 },
  kerbstone: { patch: 0.12, relief: 1 }, road_paint: { patch: 0.1, relief: 1 },
};

/** The photographic library (game/public/assets/ground): metres per repeat of each surface. */
const LIBRARY: Record<string, { metres: number; scattered: boolean }> = {
  asphalt_worn: { metres: 2, scattered: true }, grass: { metres: 2, scattered: true }, gravel: { metres: 2, scattered: true },
  granite_curb: { metres: 2, scattered: false }, cobblestone: { metres: 2, scattered: false },
};
/** Length of a kerb stone along the kerb. */
const KERB_STONE_M = 1.0;
/** Mean linear albedo of the library's worn asphalt: what an asphalt's tone is set against. */
const ASPHALT_MEAN = 0.207;

/** Mean linear albedo of asphalt at a tone: 0 is new and black, 1 is old and pale (the binder worn off the stone). */
export function asphaltMean(tone: number): number {
  return 1.295 * (0.035 + 0.14 * tone);
}

interface Photographic { key: string; tint: THREE.Color; relief: number }

/** The library surface a family's parameters are drawn from and how it is toned, or null for a synthesised one. */
function photographic(family: string, params: GroundParams): Photographic | null {
  const number = (value: unknown, fallback: number) => (typeof value === 'number' ? value : fallback);
  if (family === 'asphalt') {
    // The library's worn asphalt scaled to the tone; a tint colours it (red cycle lanes).
    const level = asphaltMean(number(params.tone, 0.5)) / ASPHALT_MEAN;
    const tint = typeof params.tint === 'string' ? new THREE.Color(params.tint).multiplyScalar(4 * level) : new THREE.Color(level, level, level);
    return { key: 'asphalt_worn', tint, relief: 0.25 };
  }
  if (family === 'grass') return { key: 'grass', tint: new THREE.Color(1, 1, 1), relief: 0.6 };
  if (family === 'gravel') {
    const level = params.grade === 'coarse' ? 1 : number(params.packed, 0) > 0.5 ? 0.78 : 0.9;
    return { key: 'gravel', tint: new THREE.Color(level * 1.04, level, level * 0.9), relief: 0.6 };
  }
  // Kerb granite is sawn pale stone, lighter than the library's map (the photos show it paler than the footway).
  if (family === 'kerbstone') return { key: 'granite_curb', tint: new THREE.Color(1.3, 1.3, 1.3), relief: 0.5 };
  if (family === 'setts' && number(params.size_y, 0.09) >= 0.18) return { key: 'cobblestone', tint: new THREE.Color(1, 1, 1), relief: 0.8 };
  return null;
}

function hash2(v: Node<'vec2'>) {
  return fract(sin(vec2(dot(v, vec2(127.1, 311.7)), dot(v, vec2(269.5, 183.3)))).mul(43758.5453));
}

/** The old game's stochastic tiling (render/ground/material.ts `stencil`): a triangle lattice of one repeat whose
 *  corners each shift the map by their own random offset, so the repeat never lines up along a street. */
function scatter(at: Node<'vec2'>) {
  const cell = floor(at);
  const f = fract(at);
  const upper = f.x.add(f.y).greaterThan(1);
  const corners = [upper.select(cell.add(vec2(1)), cell), cell.add(vec2(1, 0)), cell.add(vec2(0, 1))];
  const w = upper.select(vec3(f.x.add(f.y).sub(1), float(1).sub(f.y), float(1).sub(f.x)),
    vec3(float(1).sub(f.x).sub(f.y), f.x, f.y)).max(0).pow(4);
  return { at: corners.map(corner => at.add(hash2(corner))), weight: w.div(w.x.add(w.y).add(w.z).max(0.00001)), dx: at.dFdx(), dy: at.dFdy() };
}

function scattered3(map: THREE.Texture, grid: ReturnType<typeof scatter>) {
  const sample = (i: number) => texture(map, grid.at[i]).grad(grid.dx, grid.dy).rgb;
  return sample(0).mul(grid.weight.x).add(sample(1).mul(grid.weight.y)).add(sample(2).mul(grid.weight.z));
}

/** Kerb stones a metre long along the kerb (the ground kind's `_ALONG` station, metres): a dark joint between
 *  neighbours and a tone of its own for each stone. */
function kerbStones() {
  const station = attribute('_along', 'float').div(KERB_STONE_M);
  const fromJoint = fract(station.add(0.5)).sub(0.5).abs().mul(KERB_STONE_M);
  const joint = float(1).sub(smoothstep(0.006, 0.022, fromJoint));   // the gap and the dirt that gathers beside it
  const own = fract(sin(floor(station).mul(12.9898)).mul(43758.5453)).sub(0.5).mul(0.2);
  return { tone: float(1).add(own).mul(float(1).sub(joint.mul(0.6))) };
}

/** What years of traffic and repair leave on asphalt, placed in world space so cells agree: repairs of newer, darker
 *  and smoother asphalt with crisp edges, broad pale worn stretches, and sealed cracks near the camera. */
function asphaltWear(place: Node<'vec2'>) {
  const repair = smoothstep(0.2, 0.225, mx_noise_float(vec3(place.mul(0.085), 5.3)).add(mx_noise_float(vec3(place.mul(0.4), 1.9)).mul(0.08)));
  const worn = mx_noise_float(vec3(place.mul(vec2(0.021, 0.034)), 7.7)).mul(0.1);
  const cracked = smoothstep(0.1, 0.35, mx_noise_float(vec3(place.mul(0.045), 12.4)));
  const line = float(1).sub(smoothstep(0.004, 0.012, mx_noise_float(vec3(place.mul(0.55), 21.8)).add(mx_noise_float(vec3(place.mul(2.3), 4.1)).mul(0.06)).abs()));
  const near = float(1).sub(smoothstep(12, 40, positionWorld.sub(cameraPosition).length()));
  const sealed = line.mul(cracked).mul(near);
  return { tone: float(1).add(worn).mul(float(1).sub(repair.mul(0.28))).mul(float(1).sub(sealed.mul(0.45))), sealed: sealed.max(repair.mul(0.5)) };
}

const cache = new Map<string, THREE.Material>();
const loader = new THREE.TextureLoader();
const images = new Map<string, THREE.Texture>();

function image(key: string, layer: 'albedo' | 'normal' | 'orm'): THREE.Texture {
  const url = `${import.meta.env.BASE_URL}assets/ground/${key}/${layer}.jpg`;
  let result = images.get(url);
  if (!result) {
    result = loader.load(url);
    result.colorSpace = layer === 'albedo' ? THREE.SRGBColorSpace : THREE.NoColorSpace;
    result.wrapS = result.wrapT = THREE.RepeatWrapping;
    result.anisotropy = 16;
    images.set(url, result);
  }
  return result;
}

function map(data: Uint8Array, width: number, height: number, colour: boolean) {
  const result = new THREE.DataTexture(data, width, height, THREE.RGBAFormat, THREE.UnsignedByteType);
  result.colorSpace = colour ? THREE.SRGBColorSpace : THREE.NoColorSpace;
  result.wrapS = result.wrapT = THREE.RepeatWrapping;
  result.magFilter = THREE.LinearFilter;
  result.minFilter = THREE.LinearMipmapLinearFilter;
  result.generateMipmaps = true;
  result.anisotropy = 16;
  result.needsUpdate = true;
  return result;
}

/** The material of a ground family, shared by every primitive with the same parameters. */
export function groundMaterial(family: string, params: GroundParams = {}): THREE.Material {
  const key = `${family}:${JSON.stringify(params, Object.keys(params).sort())}`;
  let material = cache.get(key) as THREE.MeshStandardNodeMaterial | undefined;
  if (!material) {
    const look = LOOK[family] ?? { patch: 0.15, relief: 1 };
    const place = positionWorld.xz;
    const patches = mx_noise_float(vec3(place.mul(0.29), 3.7)).mul(0.6).add(mx_noise_float(vec3(place.mul(0.057), 9.1)).mul(0.4));
    material = new THREE.MeshStandardNodeMaterial();
    material.name = `ground-${family}`;
    const photo = photographic(family, params);
    if (photo) {
      const { metres, scattered } = LIBRARY[photo.key];
      const at = uv().div(metres);
      const grid = scattered ? scatter(at) : null;
      const read = (layer: 'albedo' | 'normal' | 'orm') => (grid ? scattered3(image(photo.key, layer), grid) : texture(image(photo.key, layer), at).rgb);
      const orm = read('orm');
      const wear = family === 'asphalt' ? asphaltWear(place) : null;
      const stones = family === 'kerbstone' ? kerbStones() : null;
      const varied = float(1).add(patches.mul(look.patch));
      const tone = wear ? varied.mul(wear.tone) : stones ? varied.mul(stones.tone) : varied;
      material.colorNode = read('albedo').mul(vec3(photo.tint.r, photo.tint.g, photo.tint.b)).mul(tone);
      material.normalNode = normalMap(read('normal'), vec2(photo.relief));
      const rough = orm.g.add(patches.mul(-0.05));
      material.roughnessNode = (wear ? rough.add(wear.sealed.mul(-0.12)) : rough).clamp(0.3, 1);
      material.aoNode = orm.r;
      material.userData.library = photo.key;
    } else {
      const maps = synthesise(family, params);
      const at = uv().div(vec2(maps.metres[0], maps.metres[1]));
      const albedo = texture(map(maps.albedo, maps.width, maps.height, true), at);
      const normal = texture(map(maps.normal, maps.width, maps.height, false), at);
      const orm = texture(map(maps.orm, maps.width, maps.height, false), at);
      material.colorNode = albedo.rgb.mul(float(1).add(patches.mul(look.patch)));
      material.normalNode = normalMap(normal.rgb, vec2(look.relief));
      material.roughnessNode = orm.g.add(patches.mul(-0.05)).clamp(0.3, 1);
      material.aoNode = orm.r;
      // What a later geometry path needs to displace the surface: the height is the normal map's alpha.
      material.userData.relief = { metres: maps.reliefM, tile: maps.metres, texelsPerMetre: maps.width / maps.metres[0] };
    }
    material.metalnessNode = float(0);
    cache.set(key, material);
  }
  return material;
}

/** The far level of detail: one colour per node, the mean of the cover under it. */
function far(): THREE.Material {
  let material = cache.get('ground_far');
  if (!material) {
    material = new THREE.MeshStandardNodeMaterial({ vertexColors: true, roughness: 0.95, metalness: 0 });
    material.name = 'ground-far';
    cache.set('ground_far', material);
  }
  return material;
}

/** Open water (the `water` kind's surface): the murky river as it reflects the sky. A first, plain version: no
 *  ripples, no depth, no flow. */
function water(params: GroundParams = {}): THREE.Material {
  const key = `water:${JSON.stringify(params, Object.keys(params).sort())}`;
  let material = cache.get(key) as THREE.MeshStandardNodeMaterial | undefined;
  if (!material) {
    material = new THREE.MeshStandardNodeMaterial();
    material.name = 'ground-water';
    const place = positionWorld.xz;
    // Wind lanes: stretches of rougher and of calmer water, tens of metres long.
    const lanes = mx_noise_float(vec3(place.mul(vec2(0.03, 0.09)), 2.2)).mul(0.5).add(0.5);
    material.colorNode = params.body === 'pond' ? vec3(0.035, 0.05, 0.035) : vec3(0.05, 0.05, 0.036);
    material.roughnessNode = float(0.1).add(lanes.mul(0.14));
    material.metalnessNode = float(0);
    cache.set(key, material);
  }
  return material;
}

for (const family of [...GROUND_FAMILIES, 'asphalt', 'grass', 'gravel', 'kerbstone']) registerFamily(family, params => groundMaterial(family, params));
registerFamily('water', water);
registerFamily('ground_far', far);
