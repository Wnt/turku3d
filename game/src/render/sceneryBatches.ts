import * as THREE from 'three/webgpu';
import { mergeGeometries } from 'three/addons/utils/BufferGeometryUtils.js';
import { disposeDraw } from './sharedMaterials';
import { sceneryBounds } from '../world/sceneryView';
import { interleaveProgram } from './facadeProgramAttributes';

interface Source { mesh: THREE.Mesh; mask: number; revision?: number }
interface Bucket { signature: string; sources: Source[]; mesh?: THREE.Mesh }
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

/** Merge unchanged pigment geometry within kilometre cells; source models retain streaming ownership. */
export class SceneryBatches {
  private buckets = new Map<string, Bucket>();
  private masks = new WeakMap<THREE.Mesh, number>();
  private models = new Map<string, ModelSources>();
  private invalid = new WeakSet<THREE.Object3D>();
  private waiting: Bucket[] = [];
  private revision = 0;
  pending = 0;

  /** Call after changing a resident model's children, geometry, material or transforms. */
  invalidate(model: THREE.Object3D) { this.invalid.add(model); }

  constructor(private scene: THREE.Scene, private retire: (release: () => void) => void = release => release()) {}

  private release(bucket: Bucket) {
    for (const source of bucket.sources) source.mesh.layers.mask = source.mask;
    if (!bucket.mesh) return;
    const mesh = bucket.mesh; mesh.removeFromParent();
    this.retire(() => { disposeDraw(mesh); mesh.geometry.dispose(); });
  }
  clear() { for (const bucket of this.buckets.values()) this.release(bucket); this.buckets.clear(); this.models.clear(); this.waiting = []; this.pending = 0; }

  sync(models: Iterable<[string, THREE.Object3D]>) {
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
      if (model.visible) {
        const b = sceneryBounds(id);
        model.updateWorldMatrix(true, true);
        model.traverseVisible(object => {
          const mesh = object as THREE.Mesh;
          if (!mesh.isMesh || (mesh as THREE.InstancedMesh).isInstancedMesh || Array.isArray(mesh.material)
            || mesh.material.transparent || !mesh.geometry.attributes.position
            || !['ground','buildings','buildings-program','buildings-mapped','vegetation','regional'].includes(mesh.userData.layer)) return;
          const mask = this.masks.get(mesh) ?? mesh.layers.mask; this.masks.set(mesh, mask);
          const layout = Object.entries(mesh.geometry.attributes).map(([name,a]) =>
            `${name}:${a.itemSize}:${a.normalized}:${a.array.constructor.name}`).sort().join(';');
          const key = `${Math.floor(b.x/1000)},${Math.floor(b.z/1000)}:${mesh.material.uuid}:${!!mesh.geometry.index}:${layout}`;
          const group = groups.get(key) ?? []; group.push({mesh,mask,revision}); groups.set(key,group);
        });
      }
      this.models.set(id, {model, visible: model.visible, groups});
      this.invalid.delete(model);
    }
    for (const id of this.models.keys()) if (!seen.has(id)) { this.models.delete(id); changed = true; }
    if (changed) this.reconcile();
    this.buildNext();
  }

  private reconcile() {
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
        next.set(chunkKey, previous?.signature === signature ? previous : {signature,sources:chunk});
      }
    }
    for (const [key,bucket] of this.buckets) if (next.get(key) !== bucket) this.release(bucket);
    this.buckets = next;
    this.waiting = [...next.values()].filter(b=>b.sources.length>1&&!b.mesh);
  }

  private buildNext() {
    // Admit one changed cell per frame. Until then its original meshes remain visible.
    const bucket = this.waiting.shift();
    if (bucket) {
      const geometries = bucket.sources.map(s=>{
        const geometry = s.mesh.geometry.clone();
        for (const [name,attribute] of Object.entries(geometry.attributes)) {
          if (!(attribute as THREE.InterleavedBufferAttribute).isInterleavedBufferAttribute) continue;
          const values=new Float32Array(attribute.count*attribute.itemSize);
          for(let i=0;i<attribute.count;i++)for(let c=0;c<attribute.itemSize;c++)values[i*attribute.itemSize+c]=attribute.getComponent(i,c);
          geometry.setAttribute(name,new THREE.BufferAttribute(values,attribute.itemSize));
        }
        // meshopt positions are normalized integers with a node dequantization transform.
        // World coordinates must be floats before applying it; integer writes clip the city.
        for (const name of ['position','normal','tangent']) {
          const attribute = geometry.getAttribute(name);
          if (!attribute) continue;
          const values = new Float32Array(attribute.count * attribute.itemSize);
          for (let i=0;i<attribute.count;i++) for (let c=0;c<attribute.itemSize;c++)
            values[i*attribute.itemSize+c] = attribute.getComponent(i,c);
          geometry.setAttribute(name,new THREE.BufferAttribute(values,attribute.itemSize));
        }
        return geometry.applyMatrix4(s.mesh.matrixWorld);
      });
      const geometry = mergeGeometries(geometries);
      for (const temporary of geometries) temporary.dispose();
      if (!geometry) throw new Error('Incompatible scenery batch attributes');
      interleaveProgram(geometry);
      geometry.computeBoundingBox(); geometry.computeBoundingSphere();
      const mesh = new THREE.Mesh(geometry,bucket.sources[0].mesh.material);
      mesh.name = 'distant-city-cell'; mesh.userData.scenery = true;
      mesh.matrixAutoUpdate = false; mesh.castShadow = mesh.receiveShadow = false;
      this.scene.add(mesh); bucket.mesh = mesh;
      for (const source of bucket.sources) source.mesh.layers.mask = 0;
    }
    this.pending = this.waiting.length;
  }
}
