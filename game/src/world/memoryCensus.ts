// Retained memory by category for perf work on small machines (`__render.memory()`). Counts the CPU-side bytes the
// page keeps: geometry arrays (still held after their GPU upload), texture source data, streaming caches, collision
// cells, and the browser's own per-context totals when measureUserAgentSpecificMemory is available. Each typed array
// and texture counts once, in the first category that reaches it.
import type * as THREE from 'three/webgpu';
import { visualBuffers } from './loadGlb';
import { decodedTextures } from './loadingKtx';
import { sharedBuffers, textureBytes, textureRelease } from './sharedAssets';
import { staticBatchSources } from './staticBatch';
import { vegetationOwner } from './vegetationLod';
import { GpuMeter } from './gpuBytes';

export interface MemoryRow { bytes: number; count: number }
export interface MemorySources {
  scene: THREE.Scene;
  /** Shader programs and node-builder states are heap the geometry rows do not see. */
  renderer?: THREE.WebGPURenderer;
  coarse?: { memoryParts(): ReturnType<import('./coarseTiles').CoarseTiles['memoryParts']> } | null;
  collision?: { retainedBytes(): MemoryRow } | null;
  /** Decoded collision ground of fixed-camera and replay modes (world/groundIndex.ts). */
  ground?: { retainedBytes(): MemoryRow } | null;
}

const MiB = 2 ** 20;
const mib = (bytes: number) => Math.round(bytes / MiB * 10) / 10;

export async function memoryCensus({ scene, coarse, collision, ground, renderer }: MemorySources) {
  const arrays = new Set<ArrayBufferView>(), textures = new Set<unknown>();
  const rows = new Map<string, MemoryRow>();
  const add = (category: string, bytes: number, count = 1) => {
    const row = rows.get(category) ?? { bytes: 0, count: 0 };
    row.bytes += bytes; row.count += count; rows.set(category, row);
  };
  const arrayBytes = (array: ArrayBufferView | undefined | null) => {
    if (!array || arrays.has(array)) return 0;
    arrays.add(array); return array.byteLength;
  };
  const geometryBytes = (mesh: THREE.Mesh) => {
    let bytes = 0;
    const geometry = mesh.geometry;
    if (!geometry) return 0;
    for (const attribute of Object.values(geometry.attributes)) {
      const data = (attribute as THREE.InterleavedBufferAttribute).isInterleavedBufferAttribute
        ? (attribute as THREE.InterleavedBufferAttribute).data.array : (attribute as THREE.BufferAttribute).array;
      bytes += arrayBytes(data as ArrayBufferView);
    }
    bytes += arrayBytes(geometry.index?.array as ArrayBufferView | undefined);
    const instanced = mesh as unknown as THREE.InstancedMesh;
    if (instanced.isInstancedMesh) bytes += arrayBytes(instanced.instanceMatrix.array) + arrayBytes(instanced.instanceColor?.array);
    return bytes;
  };
  const materialTextures = (mesh: THREE.Mesh) => {
    let bytes = 0;
    for (const material of Array.isArray(mesh.material) ? mesh.material : [mesh.material]) {
      for (const value of Object.values(material ?? {})) {
        const texture = value as THREE.Texture;
        if (!texture?.isTexture) continue;
        const key = texture.source ?? texture;
        if (textures.has(key)) continue;
        textures.add(key); bytes += textureBytes(texture);
      }
    }
    return bytes;
  };
  /** Geometry by part of a tile model: placements, static and shadow batches, facade modules, vegetation, rest. */
  const tilePart = (mesh: THREE.Mesh) => {
    if (mesh.userData.facadeModules) return 'facade modules';
    if (mesh.userData.shadowBatch || mesh.name.includes('shadow')) return 'shadow batch';
    if (mesh.name === 'static-placement-batch' || mesh.name === 'static-tile-batch') return 'static batches';
    if (vegetationOwner(mesh)) return 'vegetation';
    for (let node: THREE.Object3D | null = mesh; node; node = node.parent) {
      if (node.name.startsWith('block-lod')) return 'block LOD shells';
      if (node.name.startsWith('building:')) return 'building placements';
    }
    return 'tile surfaces';
  };
  const visit = (root: THREE.Object3D, category: (mesh: THREE.Mesh) => string) => root.traverse(object => {
    const mesh = object as THREE.Mesh;
    if (!mesh.isMesh) return;
    const name = category(mesh);
    add(`geometry: ${name}`, geometryBytes(mesh));
    const texture = materialTextures(mesh);
    if (texture) add('texture source data', texture);
  });
  const parts = coarse?.memoryParts();
  const full = new Set(parts?.full), previews = new Set(parts?.previews);
  // Drawn tiles first, then hidden ones, then everything else in the scene.
  for (const model of parts?.full ?? []) {
    visit(model, mesh => `full tiles / ${tilePart(mesh)}`);
    for (const source of staticBatchSources(model)) add('geometry: static batch sources (detached)', geometryBytes(source));
  }
  for (const model of parts?.previews ?? []) visit(model, () => 'collision previews');
  for (const model of parts?.cached ?? []) {
    visit(model, mesh => `return cache / ${tilePart(mesh)}`);
    for (const source of staticBatchSources(model)) add('geometry: return cache / static batch sources', geometryBytes(source));
  }
  for (const root of await parts?.buildingTemplates() ?? []) visit(root, () => 'building model templates');
  for (const child of scene.children) {
    if (full.has(child) || previews.has(child)) continue;
    visit(child, mesh => {
      for (let node: THREE.Object3D | null = mesh; node; node = node.parent) if (node.userData.scenery) return 'scenery cells';
      return `other / ${(child.name || child.type).slice(0, 32)}`;
    });
  }
  add('cache: visual GLB bytes', visualBuffers.residentBytes, visualBuffers.count);
  add('cache: shared GLB buffers', sharedBuffers.residentBytes, sharedBuffers.count);
  add('cache: decoded KTX2 textures', decodedTextures.residentBytes, decodedTextures.count);
  if (parts) {
    add('cache: final tile fragments', parts.fragmentCache.bytes, parts.fragmentCache.count);
    add('JSON: preview fragments (serialized size estimate)', parts.previewJsonBytes(), parts.previewFragments);
    add('JSON: road graphs (serialized size estimate)', parts.roadsJsonBytes(), parts.roads);
  }
  const cells = collision?.retainedBytes();
  if (cells) add('collision cells (retained bytes)', cells.bytes, cells.count);
  const indexed = ground?.retainedBytes();
  if (indexed) add('collision ground index (replay and fixed cameras)', indexed.bytes, indexed.count);
  // Object counts: each three.js object, material and texture costs heap beyond its arrays.
  let objects = 0, meshes = 0;
  const materials = new Set<unknown>();
  scene.traverse(object => { objects++; const mesh = object as THREE.Mesh; if (mesh.isMesh) { meshes++; for (const m of [mesh.material].flat()) materials.add(m); } });
  add('scene objects (count only)', 0, objects); add('scene meshes (count only)', 0, meshes);
  add('scene materials (count only)', 0, materials.size); add('scene textures (count only)', 0, textures.size);
  // userData rows (object ranges, facade kit parameters, extras) as serialized size, skipping three.js references.
  let userData = 0, owners = 0;
  const skip = (_key: string, value: unknown) => value && typeof value === 'object'
    && ((value as THREE.Object3D).isObject3D || (value as THREE.Material).isMaterial || (value as THREE.Texture).isTexture
      || ArrayBuffer.isView(value) || value instanceof Map || value instanceof Set) ? undefined : value;
  const userDataOf = (root: THREE.Object3D) => root.traverse(object => {
    if (!Object.keys(object.userData).length) return;
    try { userData += 2 * JSON.stringify(object.userData, skip).length; owners++; } catch { /* cyclic: skipped */ }
  });
  userDataOf(scene);
  for (const model of parts?.cached ?? []) userDataOf(model);
  add('JS objects: userData (serialized size estimate)', userData, owners);
  // r186 keeps every shader stage's WGSL and each node-builder state's sources as JS strings.
  const internals = renderer as unknown as { _pipelines?: { programs?: Record<string, Map<string, unknown>> };
    _nodes?: { nodeBuilderCache?: Map<string, unknown> } } | undefined;
  let shaders = 0, stages = 0;
  for (const map of Object.values(internals?._pipelines?.programs ?? {})) for (const code of map.keys()) { shaders += 2 * code.length; stages++; }
  if (stages) add('shader stages (WGSL source strings)', shaders, stages);
  const builders = internals?._nodes?.nodeBuilderCache?.size ?? 0;
  // Node-builder states share their source strings with the stages above (counted once there).
  if (builders) add('node-builder states (count only; sources shared with shader stages)', 0, builders);
  const heap = (performance as unknown as { memory?: { usedJSHeapSize: number; totalJSHeapSize: number } }).memory;
  let browser: unknown = null;
  const measure = (performance as unknown as { measureUserAgentSpecificMemory?: () => Promise<{ bytes: number; breakdown: { bytes: number; types: string[]; attribution: { scope?: string; url?: string }[] }[] }> }).measureUserAgentSpecificMemory;
  if (measure && (globalThis as { crossOriginIsolated?: boolean }).crossOriginIsolated) {
    try {
      const result = await measure.call(performance);
      const scopes = new Map<string, number>();
      for (const entry of result.breakdown) {
        const scope = entry.attribution.map(a => a.scope ?? '?').join('+') || entry.types.join('+') || 'shared';
        scopes.set(scope, (scopes.get(scope) ?? 0) + entry.bytes);
      }
      browser = { totalMiB: mib(result.bytes), byScopeMiB: Object.fromEntries([...scopes].map(([k, v]) => [k, mib(v)])) };
    } catch (error) { browser = { error: String(error) }; }
  }
  // GPU-resident bytes (vertex/index buffers at 64 KiB placement, textures), the residency budget's meter.
  const meter = new GpuMeter(), gpu: Record<string, number> = {};
  const gpuAdd = (category: string, roots: readonly THREE.Object3D[]) => {
    let bytes = 0; for (const root of roots) bytes += meter.root(root); gpu[category] = mib(bytes);
  };
  gpuAdd('full tiles', parts?.full ?? []); gpuAdd('collision previews', parts?.previews ?? []); gpuAdd('return cache', parts?.cached ?? []);
  const rest = scene.children.filter(child => !full.has(child) && !previews.has(child));
  gpuAdd('scenery cells', rest.filter(child => { let scenery = false; child.traverse(o => { scenery ||= !!o.userData.scenery; }); return scenery; }));
  gpuAdd('other scene content', rest);
  gpu.total = Object.values(gpu).reduce((sum, value) => sum + value, 0);
  const sorted = [...rows].sort((a, b) => b[1].bytes - a[1].bytes);
  return {
    totalMiB: mib(sorted.reduce((sum, [, row]) => sum + row.bytes, 0)),
    /** CPU geometry arrays freed after their GPU upload since load (not in the total). */
    releasedMiB: parts ? mib(parts.releasedBytes) : 0, releases: parts?.releases ?? 0,
    texturesReleasedMiB: mib(textureRelease.bytes), textureReleases: textureRelease.count,
    rows: Object.fromEntries(sorted.map(([name, row]) => [name, { MiB: mib(row.bytes), count: row.count }])),
    heapMiB: heap ? { used: mib(heap.usedJSHeapSize), total: mib(heap.totalJSHeapSize) } : null,
    gpuMiB: gpu, gpuResidency: parts?.gpuResidency ?? null,
    browser,
  };
}
