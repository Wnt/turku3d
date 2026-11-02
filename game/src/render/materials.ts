import { wetSurface } from './weather/surfaces';
import { StyledMaterials } from './styledMaterials';
import { reflectionSurface, REFLECTION_LAYER } from './reflections';
import { modelTextures, textureUploads } from './textureUploads';
// The assembler resolves COMMON §4 texture paths and binds KTX2 maps into glTF.
// Runtime styling preserves those maps, their UV transforms, ORM channels and facade colours.
import * as THREE from 'three/webgpu';
import { materialColor, materialRoughness, texture, positionWorld, mix, vec3, bumpMap, float } from 'three/tsl';
import { facadeKitMaterial } from './facadeKit';
import { interleaveProgram, interleaveProgramAsync } from './facadeProgramAttributes';
import { QUALITY, type QualityTier } from './quality';
import { RiverMaterial } from './water';
import { GroundBindings } from './ground/binding';
import type { Environment } from './environment';

type SurfaceStyle = 'asphalt' | 'granite' | 'cobblestone' | 'grass' | 'water' | 'standard';

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
  readonly ground: GroundBindings | null;
  private detail = detailTexture();
  private shared = new StyledMaterials();
  private owned = new Map<THREE.Object3D, { sources: Map<THREE.Mesh, THREE.Mesh['material']>; rivers: THREE.Mesh[] }>();
  private rivers = new Map<string, { river: RiverMaterial; meshes: Set<THREE.Mesh> }>();
  private preparing: Promise<void> = Promise.resolve();
  private riverKeys = new Map<THREE.Mesh, string>();
  private riverSources = new Map<THREE.Mesh, THREE.Mesh['material']>();
  constructor(private renderer: THREE.WebGPURenderer, private env: Environment,
    private tier: QualityTier, private deterministic: boolean, tileset?: string) {
    this.ground = tileset ? new GroundBindings(renderer, new URL(tileset, location.href).href) : null;
  }

  async applyAsync(model: THREE.Object3D) {
    const geometries = new Set<THREE.BufferGeometry>();
    model.traverse(object => { if ((object as THREE.Mesh).isMesh) geometries.add((object as THREE.Mesh).geometry); });
    // Serial admission bounds input copies as well as worker output retained before publication.
    for (const geometry of geometries) await interleaveProgramAsync(geometry);
    return this.apply(model);
  }

  apply(model: THREE.Object3D) {
    model.updateWorldMatrix(true, true);
    const sources = new Map<THREE.Mesh, THREE.Mesh['material']>(), rivers: THREE.Mesh[] = [];
    const cache = new Map<string, THREE.Material>();
    model.traverse(object => {
      const mesh = object as THREE.Mesh;
      if (!mesh.isMesh) return;
      interleaveProgram(mesh.geometry);
      const style = (source: THREE.Material): THREE.Material => {
        const key = materialKey(mesh, source);
        if (!key) return source;
        if (key === 'facade_kit') {
          const cached = cache.get(source.uuid);
          if (cached) return cached;
          const kit = this.shared.acquire(source, key, model, () => facadeKitMaterial(source, this.renderer) ?? source);
          cache.set(source.uuid, kit); return kit;
        }
        const kind = surfaceStyle(key);
        if (kind === 'water') {
          if (!this.riverSources.has(mesh)) this.riverSources.set(mesh, mesh.material);
          mesh.layers.disable(REFLECTION_LAYER);
          mesh.updateWorldMatrix(true, false);
          const normal = new THREE.Vector3(0, 1, 0);
          const attribute = mesh.geometry.getAttribute('normal');
          if (attribute) normal.fromBufferAttribute(attribute, 0);
          normal.transformDirection(mesh.matrixWorld);
          const point = new THREE.Vector3().fromBufferAttribute(mesh.geometry.getAttribute('position'), 0).applyMatrix4(mesh.matrixWorld);
          const plane = new THREE.Plane().setFromNormalAndCoplanarPoint(normal, point);
          const key = [...normal.toArray(), plane.constant].map(v => v.toFixed(4)).join(',');
          let shared = this.rivers.get(key);
          if (!shared) {
            shared = { river: new RiverMaterial(mesh, this.env, this.tier, this.deterministic, this.renderer), meshes: new Set() };
            this.rivers.set(key, shared);
          }
          shared.meshes.add(mesh); this.riverKeys.set(mesh, key);
          rivers.push(mesh);
          mesh.castShadow = false;
          return shared.river.material;
        }
        if (!(source as THREE.MeshStandardMaterial).isMeshStandardMaterial) return source;
        if (kind === 'standard') {
          if (!/(?:concrete|stone|brick|road_paint|fieldstone)/.test(key)) return source;
          return this.shared.acquire(source, `wet:${key}`, model, () =>
            wetSurface(this.renderer.library.fromMaterial(source.clone()) as THREE.MeshStandardNodeMaterial));
        }
        const cacheKey = `${source.uuid}:${kind}`;
        if (cache.has(cacheKey)) return cache.get(cacheKey)!;
        // NodeLibrary carries all glTF PBR fields over, including normalScale, vertex colours and UV transforms.
        const material = this.shared.acquire(source, kind, model, () => {
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
          if (kind !== 'grass') wetSurface(material);
          return material;
        });
        cache.set(cacheKey, material);
        return material;
      };
      sources.set(mesh, mesh.material);
      mesh.material = Array.isArray(mesh.material) ? mesh.material.map(style) : style(mesh.material);
      if (reflectionSurface(mesh)) mesh.layers.enable(REFLECTION_LAYER);
      else mesh.layers.disable(REFLECTION_LAYER);
    });
    this.owned.set(model, { sources, rivers });
    return this.ground?.apply(model);
  }

  prepare(model: THREE.Object3D, scene: THREE.Scene, camera: THREE.PerspectiveCamera, signal?: AbortSignal): Promise<void> {
    // Initial residency precedes CSM setup; preloadWorld prepares the complete ring after its main compile.
    if (!this.env.csm.camera) return Promise.resolve();
    const prepared = this.preparing.then(async () => {
      if (signal?.aborted) return;
      await Promise.all([...modelTextures(model)].map(map => textureUploads(this.renderer).enqueue(map)));
      for (const { river } of this.rivers.values()) {
        if (signal?.aborted) return;
        await river.prepare(this.renderer, model, scene, camera);
      }
    });
    this.preparing = prepared.catch(() => {});
    return prepared;
  }

  release(model: THREE.Object3D) {
    this.ground?.release(model);
    const owned = this.owned.get(model);
    if (!owned) return;
    for (const mesh of owned.rivers) {
      const key = this.riverKeys.get(mesh)!;
      const shared = this.rivers.get(key)!;
      shared.meshes.delete(mesh); this.riverKeys.delete(mesh);
      if (shared.meshes.size) shared.river.attach(shared.meshes.values().next().value!);
      else { shared.river.dispose(); this.rivers.delete(key); }
      // CoarseTiles owns the glTF material; it must not dispose another tile's shared river.
      mesh.material = this.riverSources.get(mesh)!; this.riverSources.delete(mesh);
    }
    for (const [mesh, source] of owned.sources) mesh.material = source;
    this.shared.release(model);
    // The tile renderer owns and disposes the original GLB textures, geometries and source materials.
    this.owned.delete(model);
  }

  dispose() {
    for (const model of this.owned.keys()) this.release(model);
    this.detail.dispose();
    this.ground?.dispose();
  }
}
