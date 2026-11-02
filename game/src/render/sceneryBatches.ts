import * as THREE from 'three/webgpu';
import { mergeGeometries } from 'three/addons/utils/BufferGeometryUtils.js';
import { disposeDraw } from './sharedMaterials';
import { sceneryBounds } from '../world/sceneryView';
import { interleaveProgram } from './facadeProgramAttributes';

interface Source { mesh: THREE.Mesh; mask: number }
interface Bucket { signature: string; sources: Source[]; mesh?: THREE.Mesh }

/** Merge unchanged pigment geometry within kilometre cells; source models retain streaming ownership. */
export class SceneryBatches {
  private buckets = new Map<string, Bucket>();
  private masks = new WeakMap<THREE.Mesh, number>();
  pending = 0;
  constructor(private scene: THREE.Scene, private retire: (release: () => void) => void = release => release()) {}

  private release(bucket: Bucket) {
    for (const source of bucket.sources) source.mesh.layers.mask = source.mask;
    if (!bucket.mesh) return;
    const mesh = bucket.mesh; mesh.removeFromParent();
    this.retire(() => { disposeDraw(mesh); mesh.geometry.dispose(); });
  }
  clear() { for (const bucket of this.buckets.values()) this.release(bucket); this.buckets.clear(); this.pending = 0; }

  sync(models: Iterable<[string, THREE.Object3D]>) {
    const groups = new Map<string, Source[]>();
    for (const [id, model] of models) {
      if (!model.visible) continue;
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
        const group = groups.get(key) ?? []; group.push({mesh,mask}); groups.set(key,group);
      });
    }
    const next = new Map<string, Bucket>();
    for (const [key,sources] of groups) {
      sources.sort((a,b)=>a.mesh.uuid.localeCompare(b.mesh.uuid));
      const signature = sources.map(s=>s.mesh.uuid+':'+s.mesh.geometry.uuid).join(',');
      const previous = this.buckets.get(key);
      next.set(key,previous?.signature === signature ? previous : {signature,sources});
    }
    for (const [key,bucket] of this.buckets) if (next.get(key) !== bucket) this.release(bucket);
    this.buckets = next;
    // Admit one changed cell per frame. Until then its original meshes remain visible.
    const waiting = [...next.values()].filter(b=>b.sources.length>1&&!b.mesh);
    const bucket = waiting[0];
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
    this.pending = Math.max(0,waiting.length-Number(!!bucket));
  }
}
