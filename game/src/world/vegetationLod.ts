import * as THREE from 'three/webgpu';
import { QUALITY, type QualityTier } from '../render/quality';
import { PreparationBudget } from '../render/preparationBudget';
import type { NatureAsset, NatureLibrary } from './nature/library';
import { natureAsset, natureScale, natureTint } from './nature/species';

/** Metadata lives on the glTF node; loaders may put the InstancedMesh below it. */
export function vegetationOwner(object: THREE.Object3D): THREE.Object3D | null {
  for (let node: THREE.Object3D | null = object; node; node = node.parent) {
    if (node.userData.layer === 'vegetation') return node;
  }
  return null;
}

interface Bucket {
  draw: Draw;
  bounds: THREE.Sphere;
  matrices: Float32Array;
  colors: Float32Array;
  objectIds: unknown[];
  distance: number;
  selectedCount: number;
  selectedLevel: number;
  count: number;
  index: number;
}
interface Draw {
  root: THREE.Group;
  /** Per LOD level, the parts (bark, leaves, impostor) sharing one matrix and one tint buffer. */
  levels: THREE.InstancedMesh[][];
  selections: Bucket[][];
  previous: number[][];
}
interface Model {
  sources: { mesh: THREE.InstancedMesh; visible: boolean; shadow: boolean }[];
  draws: Draw[];
  buckets: Bucket[];
}
interface Placement { matrix: THREE.Matrix4; tint: [number, number, number]; id: unknown }

/**
 * Tile vegetation (EXT_mesh_gpu_instancing prototypes with measured size per instance) drawn with the nature library:
 * each instance becomes a Turku species tree or shrub (nature/species.ts), bucketed into 50 m cells, with one shared
 * per-frame instance budget per LOD (full mesh, reduced mesh, impostor) for published and draft models on either backend.
 */
export class VegetationLod {
  /** Developer screenshot range; zero restores the quality-tier budget. */
  screenshotDistance = 0;
  private models = new Map<THREE.Object3D, Model>();
  private budget = new PreparationBudget();
  private frustum = new THREE.Frustum();
  private projection = new THREE.Matrix4();
  private sphere = new THREE.Sphere();
  private position = new THREE.Vector3();
  private visible: Bucket[] = [];
  private remaining = [0, 0, 0];
  private library: NatureLibrary | null = null;
  private readonly ready: Promise<void>;
  constructor(private tier: QualityTier, library: NatureLibrary | Promise<NatureLibrary>) {
    if (library instanceof Promise) this.ready = library.then(loaded => { this.library = loaded; });
    else { this.library = library; this.ready = Promise.resolve(); }
  }

  add(model: THREE.Object3D) {
    if (!this.library) throw new Error('VegetationLod.add before the nature library loaded');
    const steps = this.addSteps(model, this.library);
    while (!steps.next().done) { /* Synchronous fixture/startup caller. */ }
  }

  async addAsync(model: THREE.Object3D) {
    await this.ready;
    const steps = this.addSteps(model, this.library!);
    while (!steps.next().done) {
      const pending = this.budget.checkpoint();
      if (pending) await pending;
    }
  }

  private *addSteps(model: THREE.Object3D, library: NatureLibrary): Generator<void> {
    if (this.models.has(model)) return;
    const data: Model = { sources: [], draws: [], buckets: [] };
    // Collect before adding children: traversal must never revisit generated meshes.
    model.traverse(object => {
      const mesh = object as THREE.InstancedMesh;
      if (mesh.isInstancedMesh && vegetationOwner(mesh)) data.sources.push({ mesh, visible: mesh.visible, shadow: mesh.castShadow });
    });
    if (!data.sources.length) return;
    this.models.set(model, data);
    for (const { mesh } of data.sources) { mesh.visible = false; mesh.castShadow = false; }
    // Bark and crown prototypes of one variant share their placements: one representative per variant and parent.
    const representatives = new Map<string, THREE.InstancedMesh>();
    for (const { mesh } of data.sources) {
      const owner = vegetationOwner(mesh)!;
      const variant = String(owner.userData.variant ?? owner.name.replace(/:\d+$/, ''));
      const key = `${mesh.parent?.id}:${variant}`;
      if (!representatives.has(key)) representatives.set(key, mesh);
    }
    const matrix = new THREE.Matrix4(), t = new THREE.Vector3(), q = new THREE.Quaternion(), s = new THREE.Vector3();
    for (const mesh of representatives.values()) {
      const owner = vegetationOwner(mesh)!;
      const variant = String(owner.userData.variant ?? owner.name.replace(/:\d+$/, ''));
      const ids = owner.userData.instance_object_ids as unknown[] | undefined;
      // Group placements by library asset, then by 50 m cell.
      const byAsset = new Map<string, Map<string, Placement[]>>();
      for (let i = 0; i < mesh.count; i++) {
        if (i % 256 === 0) yield;
        mesh.getMatrixAt(i, matrix);
        matrix.decompose(t, q, s);
        const id = ids?.[i] ?? `${variant}:${i}`;
        const name = natureAsset(variant, String(id), s.y);
        const asset = library.asset(name);
        const scale = natureScale(s.y, Math.max(s.x, s.z), asset.height, asset.radius);
        const placed = new THREE.Matrix4().compose(t, q, s.set(scale.xz, scale.y, scale.xz));
        const cell = `${Math.floor(t.x / 50)},${Math.floor(t.z / 50)}`;
        let cells = byAsset.get(name);
        if (!cells) byAsset.set(name, cells = new Map());
        if (!cells.has(cell)) cells.set(cell, []);
        cells.get(cell)!.push({ matrix: placed, tint: natureTint(String(id)), id: ids?.[i] });
      }
      for (const [name, cells] of byAsset) {
        yield;
        const asset = library.asset(name);
        const count = [...cells.values()].reduce((n, list) => n + list.length, 0);
        const draw = this.createDraw(mesh, owner, asset, count);
        data.draws.push(draw);
        const local = assetSphere(asset), all = new THREE.Sphere().makeEmpty(), sphere = new THREE.Sphere();
        for (const list of cells.values()) {
          const bounds = new THREE.Sphere().makeEmpty();
          const matrices = new Float32Array(list.length * 16), colors = new Float32Array(list.length * 3);
          for (let i = 0; i < list.length; i++) {
            list[i].matrix.toArray(matrices, i * 16);
            colors.set(list[i].tint, i * 3);
            bounds.union(sphere.copy(local).applyMatrix4(list[i].matrix));
          }
          all.union(bounds);
          data.buckets.push({ draw, matrices, colors, objectIds: list.map(p => p.id), bounds, distance: 0, selectedCount: 0, selectedLevel: -1, count: list.length, index: data.buckets.length });
        }
        // Static conservative bounds cover every possible packed selection and all cheaper LODs.
        for (const level of draw.levels) for (const part of level) part.boundingSphere = all.clone();
      }
    }
  }

  private createDraw(source: THREE.InstancedMesh, owner: THREE.Object3D, asset: NatureAsset, count: number): Draw {
    const root = new THREE.Group();
    root.name = `vegetation-lod:${asset.name}`; root.userData.vegetationLod = true;
    root.position.copy(source.position); root.quaternion.copy(source.quaternion); root.scale.copy(source.scale);
    const levels = asset.lods.map((lod, level) => {
      const matrices = new THREE.InstancedBufferAttribute(new Float32Array(count * 16), 16);
      const colors = new THREE.InstancedBufferAttribute(new Float32Array(count * 3), 3);
      matrices.setUsage(THREE.DynamicDrawUsage); colors.setUsage(THREE.DynamicDrawUsage);
      const impostor = asset.kind === 'tree' && level === asset.lods.length - 1;
      return lod.parts.map(part => {
        const batch = new THREE.InstancedMesh(part.geometry, part.material, count);
        batch.instanceMatrix = matrices; batch.instanceColor = colors;
        batch.userData = { ...owner.userData, vegetationLod: true, nature: asset.name, level, instance_object_ids: [] };
        batch.receiveShadow = !impostor; batch.castShadow = level === 0;
        batch.visible = false; batch.count = 0;
        root.add(batch);
        return batch;
      });
    });
    source.parent!.add(root);
    return { root, levels, selections: levels.map(() => []), previous: levels.map(() => []) };
  }

  update(camera: THREE.PerspectiveCamera) {
    camera.updateMatrixWorld();
    this.projection.multiplyMatrices(camera.projectionMatrix, camera.matrixWorldInverse);
    this.frustum.setFromProjectionMatrix(this.projection, camera.coordinateSystem);
    camera.getWorldPosition(this.position);
    const q = QUALITY[this.tier].vegetation;
    this.visible.length = 0;
    for (const [model, data] of this.models) {
      let active = true;
      for (let node: THREE.Object3D | null = model; node; node = node.parent) if (!node.visible) active = false;
      // Inactive tiles can be cached with a parent pointer but absent from the scene's children.
      if (!model.parent || !model.parent.children.includes(model)) active = false;
      for (const draw of data.draws) {
        for (const selection of draw.selections) selection.length = 0;
        for (const level of draw.levels) for (const part of level) part.visible = false;
        if (active) draw.root.updateWorldMatrix(true, false);
      }
      for (const bucket of data.buckets) {
        bucket.selectedLevel = -1;
        if (!active) continue;
        this.sphere.copy(bucket.bounds).applyMatrix4(bucket.draw.root.matrixWorld);
        bucket.distance = Math.max(0, this.position.distanceTo(this.sphere.center) - this.sphere.radius);
        if (bucket.distance <= Math.max(q.far, this.screenshotDistance) && this.frustum.intersectsSphere(this.sphere)) this.visible.push(bucket);
      }
    }
    this.visible.sort((a, b) => a.distance - b.distance);
    const remaining = this.remaining;
    remaining[0] = q.nearInstances; remaining[1] = q.midInstances; remaining[2] = this.screenshotDistance ? Infinity : q.farInstances;
    for (const bucket of this.visible) {
      let level = bucket.distance < q.near ? 0 : bucket.distance < q.mid ? 1 : 2;
      while (level < 3 && remaining[level] <= 0) level++;
      // Shrubs and hedges have no impostor: beyond their last LOD they are not drawn.
      if (level >= bucket.draw.levels.length) continue;
      const count = Math.min(bucket.count, remaining[level]);
      remaining[level] -= count;
      bucket.selectedCount = count;
      bucket.selectedLevel = level;
    }
    // Cells cull independently, but compact into one draw per tile/asset/LOD part.
    // Only changed cell selections upload matrices; stationary views do no buffer writes.
    for (const data of this.models.values()) {
      for (const bucket of data.buckets) if (bucket.selectedLevel >= 0) bucket.draw.selections[bucket.selectedLevel].push(bucket);
      for (const draw of data.draws) {
        for (let level = 0; level < draw.levels.length; level++) {
          const parts = draw.levels[level], selections = draw.selections[level], previous = draw.previous[level];
          if (!selections.length) continue;
          // Distance chooses membership, not buffer order. Passing a cell must not repack unchanged instances.
          let changed = previous.length !== selections.length * 2;
          for (let i = 0; !changed && i < selections.length; i++) {
            changed = previous[i * 2] !== selections[i].index || previous[i * 2 + 1] !== selections[i].selectedCount;
          }
          const first = parts[0];
          if (changed) {
            const matrices = first.instanceMatrix.array as Float32Array, colors = first.instanceColor!.array as Float32Array;
            let offset = 0;
            const objectIds = first.userData.instance_object_ids as unknown[];
            objectIds.length = 0; previous.length = 0;
            for (const bucket of selections) {
              const count = bucket.selectedCount;
              matrices.set(bucket.matrices.subarray(0, count * 16), offset * 16);
              colors.set(bucket.colors.subarray(0, count * 3), offset * 3);
              offset += count;
              for (let i = 0; i < count; i++) objectIds.push(bucket.objectIds[i]);
              previous.push(bucket.index, count);
            }
            for (const part of parts) part.count = offset;
            first.instanceMatrix.needsUpdate = true; first.instanceColor!.needsUpdate = true;
          }
          for (const part of parts) part.visible = part.count > 0;
        }
      }
    }
  }

  remove(model: THREE.Object3D) {
    const data = this.models.get(model);
    if (!data) return;
    for (const draw of data.draws) {
      draw.root.removeFromParent();
      for (const level of draw.levels) for (const part of level) part.dispose();
    }
    for (const source of data.sources) { source.mesh.visible = source.visible; source.mesh.castShadow = source.shadow; }
    this.models.delete(model);
  }
}

/** Bounding sphere of an asset at scale 1 (base at the origin). */
function assetSphere(asset: NatureAsset) {
  return new THREE.Sphere(new THREE.Vector3(0, asset.height / 2, 0), Math.hypot(asset.radius, asset.height / 2));
}
