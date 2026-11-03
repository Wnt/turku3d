// CPU copies of render-only geometry after their GPU upload (memory census: 80-144 MiB of arrays per full tile, most
// of it building facade programs). WebGPU keeps its own buffer, three reads a static attribute's array again only
// for a version bump, so a committed tile's arrays can shrink to an empty array of the same type (format lookups
// still see the constructor; counts are fields). Positions and indices stay for CPU queries (HUD raycasts, light
// scans, shadow and LOD merges); shadow batches and detached static-batch sources release everything.
import * as THREE from 'three/webgpu';
import { sharedGeometry } from './modelGeometry';
import { staticBatchSources } from './staticBatch';

type Target = THREE.BufferAttribute | THREE.InterleavedBuffer;
/** Whether the renderer holds a GPU buffer for this attribute (or interleaved buffer). */
export type Uploaded = (target: Target) => boolean;

const released = new WeakSet<Target>();
/** Geometry whose render-only arrays were released: CPU merges must not read it. */
const releasedGeometry = new WeakSet<THREE.BufferGeometry>();
export function arraysReleased(geometry: THREE.BufferGeometry) { return releasedGeometry.has(geometry); }

/** The renderer's own record of uploaded buffers (r186 WebGPU backend data). */
export function rendererUploaded(renderer: THREE.WebGPURenderer): Uploaded {
  const backend = (renderer as unknown as { backend?: { get?(target: object): { buffer?: unknown } | undefined } }).backend;
  return target => !!backend?.get?.(target)?.buffer;
}

function targetOf(attribute: THREE.BufferAttribute | THREE.InterleavedBufferAttribute): Target {
  return (attribute as THREE.InterleavedBufferAttribute).isInterleavedBufferAttribute
    ? (attribute as THREE.InterleavedBufferAttribute).data : attribute as THREE.BufferAttribute;
}

function empty(target: Target) {
  if (released.has(target)) return 0;
  const array = target.array as unknown as { byteLength: number; constructor: new (length: number) => typeof target.array };
  if (target.usage !== THREE.StaticDrawUsage || target.updateRanges.length || !array.byteLength) return 0;
  const bytes = array.byteLength;
  target.array = new array.constructor(0);
  released.add(target);
  return bytes;
}

/** Meshes whose geometry other code reads or rewrites on the CPU after install. */
function keepsArrays(mesh: THREE.Mesh) {
  const rows = mesh.userData.objects as Record<string, unknown>[] | undefined;
  return !!(mesh as THREE.SkinnedMesh).isSkinnedMesh || !!mesh.morphTargetInfluences
    || !!mesh.userData.prop_type || !!rows?.some(row => row.prop_type);
}

/** Release a geometry's uploaded render-only arrays (everything but position and index). */
export function releaseRenderArrays(geometry: THREE.BufferGeometry, uploaded: Uploaded) {
  let bytes = 0;
  for (const [name, attribute] of Object.entries(geometry.attributes)) {
    if (name === 'position') continue;
    const target = targetOf(attribute as THREE.BufferAttribute);
    if (!uploaded(target)) continue;
    // An interleaved buffer that also carries the position stays whole.
    if ((attribute as THREE.InterleavedBufferAttribute).isInterleavedBufferAttribute
      && targetOf(geometry.attributes.position as THREE.BufferAttribute) === target) continue;
    bytes += empty(target);
  }
  if (bytes) releasedGeometry.add(geometry);
  return bytes;
}

/** A committed full tile's own (unshared) drawn geometry and its detached static-batch sources. Shared building
 *  templates are released through BuildingModelCache once every tile that uses them is committed. */
export function releaseTileArrays(model: THREE.Object3D, uploaded: Uploaded) {
  let bytes = 0;
  const seen = new Set<THREE.BufferGeometry>();
  model.traverse(object => {
    const mesh = object as THREE.Mesh;
    if (!mesh.isMesh || keepsArrays(mesh) || (mesh as THREE.InstancedMesh).isInstancedMesh) return;
    const geometry = mesh.geometry;
    if (seen.has(geometry) || sharedGeometry(geometry)) return;
    seen.add(geometry);
    bytes += releaseRenderArrays(geometry, uploaded);
    // The shadow batch only draws in the shadow pass; no CPU query reads it (HUD raycasts and light scans skip it).
    if (mesh.userData.shadowBatch) {
      const position = targetOf(geometry.attributes.position as THREE.BufferAttribute);
      if (uploaded(position)) bytes += empty(position);
      if (geometry.index && uploaded(geometry.index)) bytes += empty(geometry.index);
    }
  });
  // Sources return to their parents only for disposal; nothing reads or draws them again.
  for (const source of staticBatchSources(model)) {
    const geometry = source.geometry;
    if (seen.has(geometry) || sharedGeometry(geometry) || keepsArrays(source)) continue;
    seen.add(geometry);
    for (const attribute of Object.values(geometry.attributes)) bytes += empty(targetOf(attribute as THREE.BufferAttribute));
    if (geometry.index) bytes += empty(geometry.index);
    releasedGeometry.add(geometry);
  }
  return bytes;
}
