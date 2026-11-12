// The ground material families: `params → three material` for what the ground kind names (asphalt, paving, setts,
// cobbles, gravel, grass, soil, sand, bedrock, kerbstone, road_paint) and the vertex-coloured far level.
//
// A family's maps are synthesised once per parameter set (groundSynth.ts) and tile in metres: the mesh's UVs are
// engine x, z in metres, so a pattern keeps its size and continues across cells. Over the repeat lies variation in
// world space at two much larger scales (worn and damp patches, fading), so no two stretches of a street match.
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

const cache = new Map<string, THREE.Material>();

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
    const maps = synthesise(family, params);
    const look = LOOK[family] ?? { patch: 0.15, relief: 1 };
    const at = uv().div(vec2(maps.metres[0], maps.metres[1]));
    const albedo = texture(map(maps.albedo, maps.width, maps.height, true), at);
    const normal = texture(map(maps.normal, maps.width, maps.height, false), at);
    const orm = texture(map(maps.orm, maps.width, maps.height, false), at);
    const place = positionWorld.xz;
    const patches = mx_noise_float(vec3(place.mul(0.29), 3.7)).mul(0.6).add(mx_noise_float(vec3(place.mul(0.057), 9.1)).mul(0.4));
    material = new THREE.MeshStandardNodeMaterial();
    material.name = `ground-${family}`;
    material.colorNode = albedo.rgb.mul(float(1).add(patches.mul(look.patch)));
    material.normalNode = normalMap(normal.rgb, vec2(look.relief));
    material.roughnessNode = orm.g.add(patches.mul(-0.05)).clamp(0.3, 1);
    material.aoNode = orm.r;
    material.metalnessNode = float(0);
    // What a later geometry path needs to displace the surface: the height is the normal map's alpha.
    material.userData.relief = { metres: maps.reliefM, tile: maps.metres, texelsPerMetre: maps.width / maps.metres[0] };
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

for (const family of GROUND_FAMILIES) registerFamily(family, params => groundMaterial(family, params));
registerFamily('ground_far', far);
