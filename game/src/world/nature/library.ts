// Turku3D environment asset library (public/assets/nature/nature.glb): species trees with LOD chains and impostors,
// shrubs, hedge, grass, ground cover and the fence kit. Geometry and materials are shared by every user.
import * as THREE from 'three/webgpu';
import { summerFoliage } from './foliageCards';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { MeshoptDecoder } from 'three/addons/libs/meshopt_decoder.module.js';
import { sharedKtx } from '../loadingKtx';
import { textureUploads } from '../../render/textureUploads';
import { NatureMaterials, type NatureLighting, type NatureSources } from './materials';

export type NatureKind = 'tree' | 'shrub' | 'hedge' | 'grass' | 'fence';
export interface NaturePart { geometry: THREE.BufferGeometry; material: THREE.MeshStandardNodeMaterial; triangles: number }
export interface NatureLod { parts: NaturePart[]; triangles: number }
export interface NatureAsset {
  name: string; kind: NatureKind; species?: string;
  /** Model height and crown radius (m) at scale 1; fences and hedges are 2 m modules along X. */
  height: number; radius: number; module?: number;
  /** Nearest first. Trees end with their impostor. */
  lods: NatureLod[];
}
export interface NatureManifest {
  assets: Record<string, { kind: NatureKind; species?: string; height: number; radius: number; module?: number;
    lods: { triangles: number; vertices: number; draws: string[] }[] }>;
  textures: Record<string, { width: number; height: number; bytes: number; format: string }>;
  glb: { bytes: number; sha256: string };
}

export class NatureLibrary {
  constructor(readonly assets: Map<string, NatureAsset>, readonly materials: NatureMaterials, readonly manifest: NatureManifest | null) {}
  asset(name: string): NatureAsset {
    const asset = this.assets.get(name);
    if (!asset) throw new Error(`nature library has no asset ${name}`);
    return asset;
  }
}

export const NATURE_URL = `${import.meta.env.BASE_URL}assets/nature/`;

/** Build the library from a parsed nature.glb scene (also used by tests with a synthetic scene). */
export function natureLibraryFromScene(root: THREE.Object3D, lighting: NatureLighting | null, deterministic: boolean, manifest: NatureManifest | null = null) {
  const sources = new Map<string, THREE.MeshStandardMaterial>();
  root.traverse(o => {
    const mesh = o as THREE.Mesh;
    if (mesh.isMesh) sources.set((mesh.material as THREE.Material).name, mesh.material as THREE.MeshStandardMaterial);
  });
  const materials = new NatureMaterials(Object.fromEntries(sources) as NatureSources, lighting, deterministic);
  const assets = new Map<string, NatureAsset>();
  root.traverse(o => {
    const extras = o.userData as { asset?: string; lod?: number; kind?: NatureKind };
    if (extras.asset === undefined || extras.lod === undefined) return;
    const parts: NaturePart[] = [];
    o.traverse(m => {
      const mesh = m as THREE.Mesh;
      if (!mesh.isMesh) return;
      const geometry = mesh.geometry;
      if ((mesh.material as THREE.Material).name === "nature_leaves" && extras.kind === "tree") summerFoliage(geometry);
      geometry.computeBoundingSphere();
      const triangles = (geometry.index?.count ?? geometry.getAttribute('position').count) / 3;
      parts.push({ geometry, material: materials.forSource((mesh.material as THREE.Material).name), triangles });
    });
    const info = manifest?.assets[extras.asset];
    let asset = assets.get(extras.asset);
    if (!asset) {
      const box = new THREE.Box3().setFromObject(o);
      asset = { name: extras.asset, kind: extras.kind ?? info?.kind ?? 'tree', species: info?.species,
        height: info?.height ?? box.max.y, radius: info?.radius ?? Math.max(box.max.x, -box.min.x, box.max.z, -box.min.z), module: info?.module, lods: [] };
      assets.set(extras.asset, asset);
    }
    asset.lods[extras.lod] = { parts, triangles: parts.reduce((n, p) => n + p.triangles, 0) };
  });
  return new NatureLibrary(assets, materials, manifest);
}

export async function loadNatureLibrary(renderer: THREE.WebGPURenderer, lighting: NatureLighting | null, deterministic: boolean): Promise<NatureLibrary> {
  const loader = new GLTFLoader().register(() => textureUploads(renderer).plugin()).setKTX2Loader(sharedKtx(renderer)).setMeshoptDecoder(MeshoptDecoder);
  const [gltf, manifest] = await Promise.all([
    loader.loadAsync(`${NATURE_URL}nature.glb`),
    fetch(`${NATURE_URL}nature.json`).then(r => r.ok ? r.json() as Promise<NatureManifest> : null, () => null),
  ]);
  return natureLibraryFromScene(gltf.scene, lighting, deterministic, manifest);
}

if (import.meta.hot) import.meta.hot.accept();
