// CPU copies of render-only geometry after their GPU upload (memory census: 80-144 MiB of arrays per full tile).
// WebGPU keeps its own buffer and r186 reads a static attribute's array again only for a version bump (three's
// WebGPU backend never calls onUploadCallback, so this is that pattern by hand): a committed tile's arrays shrink to
// an empty array of the same type (format lookups still see the constructor; counts are fields), with no version
// bump, so nothing is re-created or re-uploaded. Positions and indices stay for CPU queries (HUD raycasts, light
// scans, shadow and LOD merges); shadow batches and geometry only merged away (detached static-batch sources,
// building templates no committed tile draws directly) release everything.
//
// Each target is decided once, when its tile commits: released at once if the GPU already has it, else queued and
// released when a later check finds its buffer (TileArrays.check, cheap and incremental).
import * as THREE from 'three/webgpu';
import { sharedGeometry } from './modelGeometry';
import { staticBatchSources } from './staticBatch';

type Target = THREE.BufferAttribute | THREE.InterleavedBuffer;
/** Whether the renderer holds a GPU buffer for this attribute (or interleaved buffer). */
export type Uploaded = (target: Target) => boolean;

const released = new WeakSet<Target>();
const decided = new WeakSet<Target>();
/** Geometry whose arrays were released: CPU merges must not read it. */
const releasedGeometry = new WeakSet<THREE.BufferGeometry>();
export function arraysReleased(geometry: THREE.BufferGeometry) { return releasedGeometry.has(geometry); }

/** The renderer's own record of uploaded buffers (r186 WebGPU backend data); never creates backend entries. */
export function rendererUploaded(renderer: THREE.WebGPURenderer): Uploaded {
  const backend = (renderer as unknown as { backend?: { data?: WeakMap<object, { buffer?: unknown }> } }).backend;
  return target => !!backend?.data?.get(target)?.buffer;
}

function targetOf(attribute: THREE.BufferAttribute | THREE.InterleavedBufferAttribute): Target {
  return (attribute as THREE.InterleavedBufferAttribute).isInterleavedBufferAttribute
    ? (attribute as THREE.InterleavedBufferAttribute).data : attribute as THREE.BufferAttribute;
}

/** Bytes freed by emptying `target` (0 if dynamic, ranged, empty or already released). */
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
    || (mesh as THREE.InstancedMesh).isInstancedMesh || !!mesh.userData.prop_type || !!rows?.some(row => row.prop_type);
}

/** Render-only targets of a drawn geometry: everything but the position (and an interleaved buffer carrying it). */
function renderTargets(geometry: THREE.BufferGeometry, all: boolean): Target[] {
  const position = geometry.attributes.position ? targetOf(geometry.attributes.position as THREE.BufferAttribute) : null;
  const out: Target[] = [];
  for (const [name, attribute] of Object.entries(geometry.attributes)) {
    const target = targetOf(attribute as THREE.BufferAttribute);
    if (!all && (name === 'position' || target === position)) continue;
    if (!out.includes(target)) out.push(target);
  }
  if (all && geometry.index) out.push(geometry.index);
  return out;
}

/** Release now what the GPU has; return the rest to wait for its upload. */
function settle(targets: Target[], uploaded: Uploaded, geometry: THREE.BufferGeometry, pending: Target[]) {
  let bytes = 0;
  for (const target of targets) {
    if (decided.has(target)) continue;
    decided.add(target);
    if (uploaded(target)) bytes += empty(target); else pending.push(target);
  }
  if (bytes) releasedGeometry.add(geometry);
  return bytes;
}

/** Release a geometry's uploaded render-only arrays (everything but position and index); for direct use. */
export function releaseRenderArrays(geometry: THREE.BufferGeometry, uploaded: Uploaded) {
  let bytes = 0;
  for (const target of renderTargets(geometry, false)) if (uploaded(target)) bytes += empty(target);
  if (bytes) releasedGeometry.add(geometry);
  return bytes;
}

/** Release every array of geometry nothing draws or reads again (detached sources, unused template parts). */
function releaseAll(geometry: THREE.BufferGeometry) {
  let bytes = 0;
  for (const target of renderTargets(geometry, true)) { decided.add(target); bytes += empty(target); }
  if (bytes) releasedGeometry.add(geometry);
  return bytes;
}

/** The geometries a model draws (its scene tree, excluding detached sources). */
export function drawnGeometries(model: THREE.Object3D) {
  const out = new Set<THREE.BufferGeometry>();
  model.traverse(object => { const mesh = object as THREE.Mesh; if (mesh.isMesh) out.add(mesh.geometry); });
  return out;
}

/** Per-renderer bookkeeping: what waits for its upload, and bytes released so far. */
export class TileArrays {
  private pending = new Map<THREE.Object3D, { geometry: THREE.BufferGeometry; targets: Target[] }[]>();
  releasedBytes = 0;
  releases = 0;
  constructor(private uploaded: Uploaded) {}

  /** A committed full tile: its own drawn geometry (render arrays), shadow batch and detached sources (all). */
  commit(model: THREE.Object3D) {
    const waiting: { geometry: THREE.BufferGeometry; targets: Target[] }[] = [];
    const seen = new Set<THREE.BufferGeometry>();
    let bytes = 0;
    model.traverse(object => {
      const mesh = object as THREE.Mesh;
      if (!mesh.isMesh || keepsArrays(mesh)) return;
      const geometry = mesh.geometry;
      if (seen.has(geometry) || sharedGeometry(geometry)) return;
      seen.add(geometry);
      // The shadow batch only draws in the shadow pass; no CPU query reads it (HUD raycasts and light scans skip it).
      const targets: Target[] = [];
      bytes += settle(renderTargets(geometry, !!mesh.userData.shadowBatch), this.uploaded, geometry, targets);
      if (targets.length) waiting.push({ geometry, targets });
    });
    // Sources return to their parents only for disposal; nothing reads or draws them again.
    for (const source of staticBatchSources(model)) {
      const geometry = source.geometry;
      if (seen.has(geometry) || sharedGeometry(geometry) || keepsArrays(source)) continue;
      seen.add(geometry);
      bytes += releaseAll(geometry);
    }
    if (waiting.length) this.pending.set(model, waiting);
    this.count(bytes);
    return bytes;
  }

  /** A building template every holder of which is committed: geometry a holder draws keeps position and index
   *  (released once uploaded), geometry only merged away releases everything. */
  template(root: THREE.Object3D, drawn: (geometry: THREE.BufferGeometry) => boolean) {
    const waiting: { geometry: THREE.BufferGeometry; targets: Target[] }[] = [];
    const seen = new Set<THREE.BufferGeometry>();
    let bytes = 0;
    root.traverse(object => {
      const mesh = object as THREE.Mesh;
      if (!mesh.isMesh || keepsArrays(mesh) || seen.has(mesh.geometry)) return;
      const geometry = mesh.geometry;
      seen.add(geometry);
      if (!drawn(geometry)) { bytes += releaseAll(geometry); return; }
      const targets: Target[] = [];
      bytes += settle(renderTargets(geometry, false), this.uploaded, geometry, targets);
      if (targets.length) waiting.push({ geometry, targets });
    });
    if (waiting.length) this.pending.set(root, waiting);
    this.count(bytes);
    return bytes;
  }

  /** A shown collision preview: nothing reads its arrays on the CPU (HUD raycasts and light scans use full tiles),
   *  so every array goes once uploaded. */
  preview(model: THREE.Object3D) {
    const waiting: { geometry: THREE.BufferGeometry; targets: Target[] }[] = [];
    let bytes = 0;
    model.traverse(object => {
      const mesh = object as THREE.Mesh;
      if (!mesh.isMesh) return;
      const targets: Target[] = [];
      bytes += settle(renderTargets(mesh.geometry, true), this.uploaded, mesh.geometry, targets);
      if (targets.length) waiting.push({ geometry: mesh.geometry, targets });
    });
    if (waiting.length) this.pending.set(model, waiting);
    this.count(bytes);
    return bytes;
  }

  /** Release queued targets whose upload has happened since (only the queue is visited). */
  check() {
    let bytes = 0;
    for (const [owner, list] of this.pending) {
      for (const item of list) {
        item.targets = item.targets.filter(target => {
          if (!this.uploaded(target)) return true;
          const freed = empty(target);
          if (freed) { bytes += freed; releasedGeometry.add(item.geometry); }
          return false;
        });
      }
      const left = list.filter(item => item.targets.length);
      if (left.length) this.pending.set(owner, left); else this.pending.delete(owner);
    }
    this.count(bytes);
    return bytes;
  }

  /** A model left: nothing of it waits any more. */
  forget(model: THREE.Object3D) { this.pending.delete(model); }

  private count(bytes: number) { if (bytes) { this.releasedBytes += bytes; this.releases++; } }
}
