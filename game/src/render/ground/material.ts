import { wetSurface } from '../weather/surfaces';
// Ground material API for the tile assembler and W1-08 renderer. TSL supports both backends.
import * as THREE from 'three/webgpu';
import {
  Fn, If, cameraPosition, clamp, dot, float, floor, fract, int, mix, normalMap,
  positionWorld, sin, smoothstep, texture, vec2, vec3,
} from 'three/tsl';
import type Node from 'three/src/nodes/core/Node.js';
import type { KTX2Loader } from 'three/addons/loaders/KTX2Loader.js';

export const GROUND_KEYS = [
  'asphalt_fresh', 'asphalt_worn', 'granite_curb', 'cobblestone',
  'concrete_slabs', 'grass', 'gravel', 'quay_stone',
] as const;

export interface GroundTileSpec {
  version: number;
  material_keys: string[];
  bounds_xz: [number, number, number, number];
  ortho_uv: { offset: number; scale: number };
  splat_uv: { offset: number; scale: number };
  textures: Record<'ortho' | 'splat0' | 'splat1', { uri: string }>;
}
interface GroundLibrary {
  albedo: THREE.Texture;
  normal: THREE.Texture;
  orm: THREE.Texture;
}
interface GroundTileTextures {
  ortho: THREE.Texture;
  splat0: THREE.Texture;
  splat1: THREE.Texture;
}

export function validateGroundSpec(spec: GroundTileSpec): void {
  if (spec.version !== 1 || spec.material_keys.join(',') !== GROUND_KEYS.join(',')) {
    throw new Error('Unsupported ground material contract or library layer order');
  }
  const [x0, z0, x1, z1] = spec.bounds_xz;
  if (![x0, z0, x1, z1].every(Number.isFinite) || x1 <= x0 || z1 <= z0) {
    throw new Error('Invalid ground tile bounds');
  }
  for (const uv of [spec.ortho_uv, spec.splat_uv]) {
    if (!(uv.offset >= 0 && uv.scale > 0 && uv.offset + uv.scale <= 1)) {
      throw new Error('Invalid ground gutter UV transform');
    }
  }
}

function hash2(v: Node<'vec2'>) {
  return fract(sin(vec2(dot(v, vec2(127.1, 311.7)), dot(v, vec2(269.5, 183.3)))).mul(43758.5453));
}

/** Evaluate the lattice before any splat branch. TSL caches shared expressions, so
 * lazily building the coordinates in channel zero leaves other channels uninitialized.
 */
function stencil(uv: Node<'vec2'>, phaseSteps = 0) {
  const cell = floor(uv);
  const f = fract(uv);
  const upper = f.x.add(f.y).greaterThan(1);
  const a = upper.select(cell.add(vec2(1)), cell);
  const b = cell.add(vec2(1, 0));
  const c = cell.add(vec2(0, 1));
  const w = upper.select(vec3(f.x.add(f.y).sub(1), float(1).sub(f.y), float(1).sub(f.x)),
    vec3(float(1).sub(f.x).sub(f.y), f.x, f.y)).max(0).pow(4);
  const coordinates = [a, b, c].map(vertex => {
    const value = vec2(0).toVar();
    const offset = hash2(vertex);
    value.assign(uv.add(phaseSteps ? floor(offset.mul(phaseSteps)).div(phaseSteps) : offset));
    return value;
  });
  const weight = vec3(0).toVar();
  weight.assign(w.div(w.x.add(w.y).add(w.z).max(.00001)));
  const dx = vec2(0).toVar(), dy = vec2(0).toVar();
  dx.assign(uv.dFdx()); dy.assign(uv.dFdy());
  return { uv: coordinates, weight, dx, dy };
}

function stochastic(map: THREE.Texture, lattice: ReturnType<typeof stencil>, layer: number) {
  const sample = (i: number) => texture(map, lattice.uv[i]).depth(int(layer)).grad(lattice.dx, lattice.dy).rgb;
  return sample(0).mul(lattice.weight.x).add(sample(1).mul(lattice.weight.y)).add(sample(2).mul(lattice.weight.z));
}

/** Mesh UVs must be planar E/N metres (u east, v north), as required by the layer contract.
 * Textures are north-first, with flipY=false. World coordinates keep patterns continuous at tile edges.
 * Caller owns shared library textures; tile release disposes only the returned material/tile textures.
 */
export function createGroundMaterial(spec: GroundTileSpec, maps: GroundTileTextures, library: GroundLibrary) {
  validateGroundSpec(spec);
  for (const map of Object.values(library)) {
    const minFilter = map.mipmaps.length > 1 ? THREE.LinearMipmapLinearFilter : THREE.LinearFilter;
    if (map.wrapS === THREE.RepeatWrapping && map.wrapT === THREE.RepeatWrapping
      && map.minFilter === minFilter && map.magFilter === THREE.LinearFilter && map.anisotropy === 4) continue;
    // The library is shared: unchanged samplers must not re-upload all arrays for every new ground tile.
    map.wrapS = map.wrapT = THREE.RepeatWrapping;
    map.minFilter = minFilter;
    map.magFilter = THREE.LinearFilter;
    map.anisotropy = 4;
    map.needsUpdate = true;
  }
  const [x0, z0, x1, z1] = spec.bounds_xz;
  const tileUv = positionWorld.xz.sub(vec2(x0, z0)).div(vec2(x1 - x0, z1 - z0));
  const orthoUv = tileUv.mul(spec.ortho_uv.scale).add(spec.ortho_uv.offset);
  const splatUv = tileUv.mul(spec.splat_uv.scale).add(spec.splat_uv.offset);
  const lo = texture(maps.splat0, splatUv).max(0);
  const hi = texture(maps.splat1, splatUv).max(0);
  // Renormalize after lossy GPU transcoding, including the all-zero defensive case.
  const total = lo.dot(vec4one()).add(hi.dot(vec4one())).max(.0001);
  const channels = [lo.r, lo.g, lo.b, lo.a, hi.r, hi.g, hi.b, hi.a].map(w => w.div(total));
  const near = float(1).sub(smoothstep(8, 45, positionWorld.sub(cameraPosition).length()));
  // World E/N orientation matches the contract and the tangent basis of planar UVs.
  const detailUv = vec2(positionWorld.x, positionWorld.z.negate()).div(2);
  const blend = (map: THREE.Texture, fallback: Node<'vec3'>, detailOnly = true) => Fn(() => {
    // Derivatives must be evaluated before non-uniform material branches.
    const lattice = stencil(detailUv);
    // Four 50 cm slabs per 2 m texture: shift by whole slabs so joints never ghost.
    const slabLattice = stencil(detailUv, 4);
    const result = vec3(0).toVar();
    If(detailOnly ? near.greaterThan(0) : float(1).greaterThan(0), () => {
      for (let i = 0; i < channels.length; i++) {
        If(channels[i].greaterThan(.001), () => {
          result.addAssign(stochastic(map, GROUND_KEYS[i] === 'concrete_slabs' ? slabLattice : lattice, i).mul(channels[i]));
        });
      }
    }).Else(() => { result.assign(fallback); });
    return result;
  })();
  const macro = texture(maps.ortho, orthoUv).level(float(6)).rgb;
  const albedo = blend(library.albedo, vec3(.5), false);
  const normal = blend(library.normal, vec3(.5, .5, 1));
  const orm = blend(library.orm, vec3(1, .9, 0));
  const material = new THREE.MeshStandardNodeMaterial();
  material.name = 'ground-ortho-stochastic-v1';
  // Baked shadows and blue/white aerial objects must not become street-level albedo.
  // Retain only a bounded, low-frequency luminance modulation of the material.
  const macroLight = macro.dot(vec3(.2126, .7152, .0722)).mul(.2).add(.96).clamp(.96, 1.06);
  material.colorNode = albedo.mul(macroLight);
  material.normalNode = normalMap(mix(vec3(.5, .5, 1), normal, near), vec2(.55));
  material.roughnessNode = clamp(mix(float(.9), orm.g, near), .45, 1);
  material.aoNode = mix(float(1), orm.r, near);
  material.metalness = 0;
  wetSurface(material, false, float(1).sub(channels[5]).sub(channels[6]).clamp(0, 1));
  return material;
}

// Kept as a node constructor so no TSL singleton leaks between renderer contexts.
function vec4one() { return float(1).xxxx; }

export async function loadGroundLibrary(baseUrl: string, loader: KTX2Loader): Promise<GroundLibrary> {
  const results = await Promise.allSettled(['albedo', 'normal', 'orm'].map(
    name => loader.loadAsync(new URL(`${name}.array.ktx2`, baseUrl).href),
  ));
  const textures = results.flatMap(result => result.status === 'fulfilled' ? [result.value] : []);
  const failure = results.find(result => result.status === 'rejected');
  const valid = textures.every(map => (map as THREE.CompressedArrayTexture).isCompressedArrayTexture
    && (map.image as { depth?: number }).depth === GROUND_KEYS.length);
  if (failure || !valid) {
    textures.forEach(map => map.dispose());
    if (failure?.status === 'rejected') throw failure.reason;
    throw new Error('Ground library must contain eight-layer compressed array textures');
  }
  const [albedo, normal, orm] = textures;
  return { albedo, normal, orm };
}

export async function loadGroundTile(specUrl: string, loader: KTX2Loader, library: GroundLibrary) {
  const response = await fetch(specUrl);
  if (!response.ok) throw new Error(`Ground material HTTP ${response.status}: ${specUrl}`);
  const spec: GroundTileSpec = await response.json();
  validateGroundSpec(spec);
  const loaded: THREE.Texture[] = [];
  try {
    for (const name of ['ortho', 'splat0', 'splat1'] as const) {
      loaded.push(await loader.loadAsync(new URL(spec.textures[name].uri, specUrl).href));
    }
    const [ortho, splat0, splat1] = loaded;
    const maps = { ortho, splat0, splat1 };
    const material = createGroundMaterial(spec, maps, library);
    return { spec, maps, material, dispose: () => { material.dispose(); loaded.forEach(t => t.dispose()); } };
  } catch (error) {
    loaded.forEach(t => t.dispose());
    throw error;
  }
}
