// The ground material families: `params → three material` for what the ground kind names (asphalt, paving, setts,
// cobbles, gravel, grass, soil, sand, bedrock, kerbstone, road_paint) and the vertex-coloured far level.
//
// A family's maps tile in metres: the mesh's UVs are engine x, z in metres, so a pattern keeps its size and continues
// across cells. Where the old game's photographic ground library has the surface (worn asphalt, mown grass, granite
// gravel, kerb granite, granite setts), its maps are the base, toned to the family's parameters; every other surface
// is synthesised from its parameters (groundSynth.ts). Over the repeat lies variation in world space at two much
// larger scales (worn and damp patches, fading), so no two stretches of a street match.
import * as THREE from 'three/webgpu';
import { float, mx_noise_float, normalMap, positionWorld, texture, uv, vec2, vec3 } from 'three/tsl';
import { registerFamily } from '../materials';
import { GROUND_FAMILIES, synthesise, type GroundParams } from './groundSynth';

/** How strongly a family varies over metres (tone) and how much relief its normals show. */
const LOOK: Record<string, { patch: number; relief: number }> = {
  asphalt: { patch: 0.2, relief: 1 }, paving: { patch: 0.14, relief: 1 }, setts: { patch: 0.14, relief: 1 },
  cobbles: { patch: 0.14, relief: 1 }, gravel: { patch: 0.2, relief: 1 }, grass: { patch: 0.3, relief: 1 },
  soil: { patch: 0.25, relief: 1 }, sand: { patch: 0.15, relief: 1 }, bedrock: { patch: 0.2, relief: 1 },
  kerbstone: { patch: 0.12, relief: 1 }, road_paint: { patch: 0.1, relief: 1 },
};

/** The photographic library (game/public/assets/ground): metres per repeat of each surface. */
const LIBRARY: Record<string, { metres: number }> = {
  asphalt_worn: { metres: 2 }, grass: { metres: 2 }, gravel: { metres: 2 }, granite_curb: { metres: 2 }, cobblestone: { metres: 2 },
};
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
  if (family === 'kerbstone') return { key: 'granite_curb', tint: new THREE.Color(1, 1, 1), relief: 0.5 };
  if (family === 'setts' && number(params.size_y, 0.09) >= 0.18) return { key: 'cobblestone', tint: new THREE.Color(1, 1, 1), relief: 0.8 };
  return null;
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
      const metres = LIBRARY[photo.key].metres;
      const at = uv().div(metres);
      const orm = texture(image(photo.key, 'orm'), at);
      material.colorNode = texture(image(photo.key, 'albedo'), at).rgb.mul(vec3(photo.tint.r, photo.tint.g, photo.tint.b))
        .mul(float(1).add(patches.mul(look.patch)));
      material.normalNode = normalMap(texture(image(photo.key, 'normal'), at).rgb, vec2(photo.relief));
      material.roughnessNode = orm.g.add(patches.mul(-0.05)).clamp(0.3, 1);
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

for (const family of [...GROUND_FAMILIES, 'asphalt', 'grass', 'gravel', 'kerbstone']) registerFamily(family, params => groundMaterial(family, params));
registerFamily('ground_far', far);
