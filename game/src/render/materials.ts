// The assembler resolves COMMON §4 texture paths and binds KTX2 maps into glTF.
// Runtime styling preserves those maps, their UV transforms, ORM channels and facade colours.
import * as THREE from 'three/webgpu';
import { materialColor, materialRoughness, texture, positionWorld, mix, vec3, bumpMap, float } from 'three/tsl';
import { QUALITY, type QualityTier } from './quality';
import { RiverMaterial } from './water';
import type { Environment } from './environment';

export type SurfaceStyle = 'asphalt' | 'granite' | 'cobblestone' | 'grass' | 'water' | 'standard';

export function surfaceStyle(key: string): SurfaceStyle {
  const words = key.toLowerCase().split(/[^a-z0-9]+/);
  if (words.includes('water') || words.includes('river')) return 'water';
  if (words.includes('asphalt')) return 'asphalt';
  if (words.includes('granite') || words.includes('curb')) return 'granite';
  if (words.includes('cobblestone') || words.includes('cobble') || words.includes('setts')) return 'cobblestone';
  if (words.includes('grass')) return 'grass';
  return 'standard';
}

export function materialKey(mesh: THREE.Object3D, material: THREE.Material): string | null {
  if (typeof material.userData.material_key === 'string') return material.userData.material_key;
  for (let node: THREE.Object3D | null = mesh; node; node = node.parent) {
    if (typeof node.userData.material_key === 'string') return node.userData.material_key;
  }
  return null;
}

/** Small mipmapped, deterministic detail tile. Texture derivatives avoid shimmering analytic grain. */
function detailTexture() {
  const size = 64, data = new Uint8Array(size * size * 4);
  let seed = 12345;
  for (let i = 0; i < size * size; i++) {
    seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0;
    const v = seed >>> 24;
    data.set([v, v, v, 255], i * 4);
  }
  const map = new THREE.DataTexture(data, size, size);
  map.wrapS = map.wrapT = THREE.RepeatWrapping;
  map.magFilter = THREE.LinearFilter; map.minFilter = THREE.LinearMipmapLinearFilter;
  map.generateMipmaps = true; map.needsUpdate = true;
  return map;
}

export class MaterialStyler {
  private detail = detailTexture();
  private owned = new Map<THREE.Object3D, { materials: THREE.Material[]; rivers: RiverMaterial[] }>();
  constructor(private renderer: THREE.WebGPURenderer, private env: Environment,
    private tier: QualityTier, private deterministic: boolean) {}

  apply(model: THREE.Object3D) {
    const materials: THREE.Material[] = [], rivers: RiverMaterial[] = [];
    const cache = new Map<string, THREE.Material>();
    model.traverse(object => {
      const mesh = object as THREE.Mesh;
      if (!mesh.isMesh) return;
      const style = (source: THREE.Material): THREE.Material => {
        const key = materialKey(mesh, source);
        if (!key) return source;
        const kind = surfaceStyle(key);
        if (kind === 'water') {
          const river = new RiverMaterial(mesh, this.env, this.tier, this.deterministic);
          rivers.push(river);
          mesh.castShadow = false;
          return river.material;
        }
        if (kind === 'standard' || !(source as THREE.MeshStandardMaterial).isMeshStandardMaterial) return source;
        const cacheKey = `${source.uuid}:${kind}`;
        if (cache.has(cacheKey)) return cache.get(cacheKey)!;
        // NodeLibrary carries all glTF PBR fields over, including normalScale, vertex colours and UV transforms.
        const material = this.renderer.library.fromMaterial(source.clone()) as THREE.MeshStandardNodeMaterial;
        const q = QUALITY[this.tier];
        const frequency = { asphalt: 4, granite: 2.5, cobblestone: 2, grass: 1.4 }[kind];
        // World coordinates remain continuous across tile boundaries and independently batched objects.
        const uv = positionWorld.xz.add(positionWorld.y.mul(0.37)).mul(frequency);
        const grain = texture(this.detail, uv).r.sub(0.5);
        material.colorNode = materialColor.rgb.mul(grain.mul(q.detail).add(1));
        material.roughnessNode = materialRoughness.add(grain.mul(q.detail)).clamp(0.45, 1);
        if (!material.normalMap && !material.bumpMap) material.normalNode = bumpMap(texture(this.detail, uv).r, float(0.002));
        if (kind === 'grass') {
          const patches = texture(this.detail, positionWorld.xz.mul(0.035)).r;
          material.colorNode = material.colorNode.mul(mix(vec3(0.92, 0.93, 0.83), vec3(1.04, 1.02, 0.97), patches));
        }
        // Cobbles use the supplied library normal/ORM for their joints; extra grain never replaces stone relief.
        materials.push(material); cache.set(cacheKey, material);
        return material;
      };
      mesh.material = Array.isArray(mesh.material) ? mesh.material.map(style) : style(mesh.material);
    });
    this.owned.set(model, { materials, rivers });
  }

  release(model: THREE.Object3D) {
    const owned = this.owned.get(model);
    if (!owned) return;
    for (const river of owned.rivers) river.dispose();
    for (const material of owned.materials) material.dispose();
    // The tile renderer owns and disposes the original GLB textures, geometries and source materials.
    this.owned.delete(model);
  }

  dispose() {
    for (const model of this.owned.keys()) this.release(model);
    this.detail.dispose();
  }
}
