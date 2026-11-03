import * as THREE from 'three/webgpu';
import { mergeSceneryGeometry } from './mergeSceneryGeometry';
import { shareSceneryProgram } from './shareSceneryProgram';
import { disposeDraw } from './sharedMaterials';
import { sceneryBounds } from '../world/sceneryView';

interface Source { mesh: THREE.Mesh; mask: number; revision?: number; owner?: THREE.Object3D }
interface Bucket { signature: string; sources: Source[]; mesh?: THREE.Mesh; releaseProgram?: () => void; queuedAt: number; readyAt: number; preparing?: boolean; cancelled?: boolean; finished?: Promise<void> }
interface ModelSources { model: THREE.Object3D; visible: boolean; groups: Map<string, Source[]> }

/** Bound merged allocations below WebGPU's default 256 MiB buffer limit. Facade
 * fields become one float buffer, so their source accessor sizes underestimate it. */
export function sceneryBatchGroups(sources: Source[], byteLimit = 128 * 1024 * 1024): Source[][] {
  const groups: Source[][] = [];
  let group: Source[] = [], bytes = 0;
  for (const source of sources) {
    const geometry = source.mesh.geometry;
    let size = (geometry.index?.count ?? 0) * 4;
    for (const [name, attribute] of Object.entries(geometry.attributes)) {
      const floats = name.startsWith('_kit_') || ['position', 'normal', 'tangent'].includes(name)
        || (attribute as THREE.InterleavedBufferAttribute).isInterleavedBufferAttribute;
      size += attribute.count * attribute.itemSize * (floats ? 4 : attribute.array.BYTES_PER_ELEMENT);
    }
    if (group.length && bytes + size > byteLimit) { groups.push(group); group = []; bytes = 0; }
    group.push(source); bytes += size;
  }
  if (group.length) groups.push(group);
  return groups;
}

/** Merge pigment geometry at the selected LOD scale; source models retain streaming ownership. */
export class SceneryBatches {
  private buckets = new Map<string, Bucket>();
  private masks = new WeakMap<THREE.Mesh, number>();
  private models = new Map<string, ModelSources>();
  private invalid = new WeakSet<THREE.Object3D>();
  private waiting: Bucket[] = [];
  private revision = 0;
  private preparing = new Set<Bucket>();
  pending = 0;

  /** Call after changing a resident model's children, geometry, material or transforms. */
  invalidate(model: THREE.Object3D) { this.invalid.add(model); }

  /** A cancelled compile may still read borrowed source materials/textures until its await completes. */
  retireModel(model: THREE.Object3D, release: () => void) {
    const active = [...this.preparing].filter(bucket => bucket.sources.some(source => source.owner === model));
    if (active.length) void Promise.allSettled(active.map(bucket => bucket.finished)).then(release);
    else release();
  }

  constructor(private scene: THREE.Scene, private retire: (release: () => void) => void = release => release(),
    private prepare?: (model: THREE.Object3D) => Promise<void>) {}

  private release(bucket: Bucket) {
    bucket.cancelled = true;
    bucket.releaseProgram?.();
    for (const source of bucket.sources) source.mesh.layers.mask = source.mask;
    if (!bucket.preparing) this.disposeMesh(bucket);
  }
  private disposeMesh(bucket: Bucket) {
    if (!bucket.mesh) return;
    const mesh = bucket.mesh; mesh.removeFromParent();
    bucket.mesh = undefined;
    this.retire(() => { disposeDraw(mesh); mesh.geometry.dispose(); });
  }
  clear() { for (const bucket of this.buckets.values()) this.release(bucket); this.buckets.clear(); this.models.clear(); this.waiting = []; this.pending = 0; }

  sync(models: Iterable<[string, THREE.Object3D]>, now = performance.now()) {
    // Residency/visibility changes are cheap to detect; unchanged models never traverse
    // their descendants or reformat attribute layouts in the animation loop.
    let changed = false;
    const seen = new Set<string>();
    for (const [id, model] of models) {
      seen.add(id);
      const previous = this.models.get(id);
      if (previous?.model === model && previous.visible === model.visible && !this.invalid.has(model)) continue;
      changed = true;
      const revision = ++this.revision;
      const groups = new Map<string, Source[]>();
      // A dissolving model draws its own meshes: a merged cell has no per-model coverage.
      if (model.visible && !model.userData.lodFading) {
        const b = sceneryBounds(id);
        // Detailed facades keep kilometre culling. Low-poly survey shells and
        // regional terrain need fewer draw submissions, including the prepass.
        const cellSize = b.level >= 4 ? 8000 : b.level >= 2 ? 4000 : 1000;
        model.updateWorldMatrix(true, true);
        model.traverseVisible(object => {
          const mesh = object as THREE.Mesh;
          if (!mesh.isMesh || (mesh as THREE.InstancedMesh).isInstancedMesh || Array.isArray(mesh.material)
            || mesh.material.transparent || !mesh.geometry.attributes.position
            || !['ground','buildings','buildings-program','buildings-mapped','vegetation','regional'].includes(mesh.userData.layer)) return;
          const mask = this.masks.get(mesh) ?? mesh.layers.mask; this.masks.set(mesh, mask);
          const layout = Object.entries(mesh.geometry.attributes).map(([name,a]) =>
            `${name}:${a.itemSize}:${a.normalized}:${a.array.constructor.name}`).sort().join(';');
          const key = `${cellSize}:${Math.floor(b.x/cellSize)},${Math.floor(b.z/cellSize)}:${mesh.material.uuid}:${!!mesh.geometry.index}:${layout}`;
          const group = groups.get(key) ?? []; group.push({mesh,mask,revision,owner:model}); groups.set(key,group);
        });
      }
      this.models.set(id, {model, visible: model.visible, groups});
      this.invalid.delete(model);
    }
    for (const id of this.models.keys()) if (!seen.has(id)) { this.models.delete(id); changed = true; }
    if (changed) this.reconcile(now);
    this.buildNext(now);
  }

  private reconcile(now: number) {
    const groups = new Map<string, Source[]>();
    for (const model of this.models.values()) for (const [key, sources] of model.groups) {
      const group = groups.get(key) ?? []; group.push(...sources); groups.set(key, group);
    }
    const next = new Map<string, Bucket>();
    for (const [key,sources] of groups) {
      sources.sort((a,b)=>a.mesh.uuid.localeCompare(b.mesh.uuid));
      for (const [part, chunk] of sceneryBatchGroups(sources).entries()) {
        const chunkKey = `${key}:${part}`;
        const signature = chunk.map(s=>s.mesh.uuid+':'+s.mesh.geometry.uuid+':'+s.revision).join(',');
        const previous = this.buckets.get(chunkKey);
        if (previous?.signature === signature) next.set(chunkKey, previous);
        else {
          // A streaming burst otherwise recopies the growing cell after each tile.
          // Wait for 100 ms of quiet, but never postpone eligibility beyond 250 ms.
          const queuedAt = previous && !previous.mesh && previous.sources.length > 1 ? previous.queuedAt : now;
          next.set(chunkKey, {signature, sources:chunk, queuedAt,
            readyAt: previous ? Math.min(now + 100, queuedAt + 250) : now});
        }
      }
    }
    for (const [key,bucket] of this.buckets) if (next.get(key) !== bucket) this.release(bucket);
    this.buckets = next;
    this.waiting = [...next.values()].filter(b=>b.sources.length>1&&!b.mesh);
  }

  private buildNext(now: number) {
    if (this.preparing.size) { this.pending = this.waiting.length + this.preparing.size; return; }
    // Admit one eligible cell per frame. Pending sources stay visible, and a
    // frequently changing cell must not block ready cells later in the queue.
    const index = this.waiting.findIndex(bucket => bucket.readyAt <= now);
    const bucket = index < 0 ? undefined : this.waiting.splice(index,1)[0];
    if (bucket) {
      const geometry = mergeSceneryGeometry(bucket.sources.map(source => source.mesh));
      const mesh = new THREE.Mesh(geometry,bucket.sources[0].mesh.material);
      mesh.name = 'distant-city-cell'; mesh.userData.scenery = true;
      mesh.matrixAutoUpdate = false; mesh.castShadow = mesh.receiveShadow = false;
      bucket.mesh = mesh;
      const commit = () => {
        bucket.releaseProgram = shareSceneryProgram(bucket.sources.map(source => source.mesh), geometry);
        this.scene.add(mesh);
        for (const source of bucket.sources) source.mesh.layers.mask = 0;
      };
      if (!this.prepare) commit();
      else {
        bucket.preparing = true; this.preparing.add(bucket);
        // Keep the already prepared sources visible while the replacement warms off the live draw path.
        bucket.finished = Promise.resolve().then(() => this.prepare!(mesh)).then(() => {
          if (!bucket.cancelled) commit();
        }).catch(error => {
          if (!bucket.cancelled) {
            console.warn(`[scenery] batch preparation: ${error}`);
            bucket.readyAt = performance.now() + 5000; this.waiting.push(bucket);
          }
        }).finally(() => {
          bucket.preparing = false; this.preparing.delete(bucket);
          if (bucket.cancelled || mesh.parent !== this.scene) this.disposeMesh(bucket);
          this.pending = this.waiting.length + this.preparing.size;
        });
      }
    }
    this.pending = this.waiting.length + this.preparing.size;
  }
}
