import * as THREE from 'three/webgpu';
import { QUALITY, type QualityTier } from '../render/quality';
import { PreparationBudget } from '../render/preparationBudget';
import type { NatureAsset, NatureLibrary, NaturePart } from './nature/library';
import { fenceAsset, fenceTint, natureAsset, natureScale, natureTint } from './nature/species';
import { GrassField } from './nature/grass';
import { VEGETATION_FADE, leafVariant } from './nature/materials';
import { TILE_SHADOW_LAYER } from './shadowBatch';
import { materialKey, surfaceStyle } from '../render/materials';
import { shareInstanceProgram } from '../render/instancePrograms';

export function vegetationLevel(distance: number, range: { near:number; mid:number }) {
  return distance < range.near ? 0 : distance < range.mid ? 1 : 2;
}

/** How far (m) inside boundary `b` (index j: near, mid, far) a cross-fade can start: the fade's full width plus the
 *  largest per-tree stagger (nature/materials.ts lodVariant). Fades end at the boundary at the latest. */
export const fadeReach = (b: number, j: number) => b * (2 * VEGETATION_FADE.width[j] + VEGETATION_FADE.stagger[j]);

/** Bitmask of the LOD levels (of `levels`) a tree at `distance` draws: level k up to boundary k, from where the fade
 *  at boundary k-1 can start (so both while it runs). `bands` are the near, mid and far boundaries. */
export function vegetationLevels(distance: number, bands: readonly [number, number, number], levels: number) {
  let mask = 0;
  for (let k = 0; k < levels && k < 3; k++) {
    if (k > 0 && distance < bands[k - 1] - fadeReach(bands[k - 1], k - 1)) break;
    if (distance <= bands[k]) mask |= 1 << k;
  }
  return mask;
}

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
  count: number;
  index: number;
  /** World positions of the instances (xyz), written when the bucket's tile becomes active: tile roots are static. */
  world?: Float32Array;
  /** Bounds in world space, with the instance positions. */
  sphere?: THREE.Sphere;
}
interface Selection { bucket: Bucket; offset: number; count: number }
interface Draw {
  barrier: boolean;
  /** Library asset name and a stable identity, for the city-wide pools. */
  asset: string;
  id: number;
  root: THREE.Group;
  /** Per LOD level, the parts (bark, leaves, impostor) sharing one matrix and one tint buffer. */
  levels: THREE.InstancedMesh[][];
  selections: Selection[][];
  previous: number[][];
}
interface Model {
  sources: { mesh: THREE.InstancedMesh; visible: boolean; shadow: boolean }[];
  draws: Draw[];
  buckets: Bucket[];
}
interface Placement { matrix: THREE.Matrix4; tint: [number, number, number]; id: unknown }
/** One asset LOD across every resident tile: shared world-space instances for all of the level's parts. */
interface Pool { meshes: THREE.InstancedMesh[]; matrices: THREE.InstancedBufferAttribute | null; colors: THREE.InstancedBufferAttribute | null;
  capacity: number; signature: number[]; objectIds: unknown[] }
/** Placements by library asset, then by 50 m cell. */
type Placements = Map<string, Map<string, Placement[]>>;
function place(placements: Placements, name: string, matrix: THREE.Matrix4, tint: [number, number, number], id: unknown) {
  const cell = `${Math.floor(matrix.elements[12] / 50)},${Math.floor(matrix.elements[14] / 50)}`;
  let cells = placements.get(name);
  if (!cells) placements.set(name, cells = new Map());
  let list = cells.get(cell);
  if (!list) cells.set(cell, list = []);
  list.push({ matrix, tint, id });
}

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
  private treePosition = new THREE.Vector3();
  private visible: Bucket[] = [];
  /** The inputs of the last selection: a still camera over an unchanged set of active tiles selects the same trees,
   *  so it is not run again (bench=static and every pause re-ran the full per-tree selection each frame). */
  private dirty = true;
  private last = { position: new THREE.Vector3(NaN, NaN, NaN), forward: new THREE.Vector3(), up: new THREE.Vector3(),
    fov: NaN, aspect: NaN, near: NaN, far: NaN, zoom: NaN, distance: NaN };
  private forward = new THREE.Vector3();
  private up = new THREE.Vector3();
  private activeTiles = new Map<THREE.Object3D, boolean>();
  private library: NatureLibrary | null = null;
  private grass: GrassField | null = null;
  private readonly ready: Promise<void>;
  private disposed = false;
  private pending = new Map<THREE.Object3D, { cancelled: boolean; promise: Promise<void> }>();
  /** With a scene, each asset LOD draws once for the whole city (census at spawn: 97 tree draws, one per species
   * per tile). Per-tile parts keep their selection, buffers and preparation but are not drawn themselves. */
  private poolRoot: THREE.Group | null = null;
  private pools = new Map<string, Pool>();
  /** Developer object editor previews (dev/editor): a world matrix per placement id, null hides it. Visual only. */
  private edits = new Map<string, THREE.Matrix4 | null>();
  private editOriginals = new Map<string, { bucket: Bucket; index: number; matrix: Float32Array }>();
  private nextDraw = 0;
  private signature: number[] = [];
  private instance = new THREE.Matrix4();
  private poolSphere = new THREE.Sphere();
  /** Near and far boundaries with the instance budgets folded in: a budget that runs out moves its boundary closer
   *  (a few metres per update) instead of dropping trees, so the cross-fade carries the change. */
  private bands: [number, number, number] = [NaN, NaN, NaN];
  /** With a scene, tile grass surfaces also grow instanced grass near the camera (world-space draws under it). */
  constructor(private tier: QualityTier, library: NatureLibrary | Promise<NatureLibrary>, scene?: THREE.Object3D) {
    const loaded = (lib: NatureLibrary) => {
      if (this.disposed) return;
      this.library = lib;
      if (scene && QUALITY[tier].grass.density > 0) { this.grass = new GrassField(lib, tier); scene.add(this.grass.root); }
      if (scene) { this.poolRoot = new THREE.Group(); this.poolRoot.name = 'vegetation-pools'; scene.add(this.poolRoot); }
    };
    if (library instanceof Promise) this.ready = library.then(loaded);
    else { loaded(library); this.ready = Promise.resolve(); }
  }

  loadedModels() { return [...new Set([...this.models.keys(), ...this.pending.keys()])]; }

  setTime(seconds: number) { this.library?.materials.setTime(seconds); }

  dispose() {
    this.disposed = true;
    for (const model of this.loadedModels()) this.remove(model);
    this.grass?.dispose();
    for (const pool of this.pools.values()) for (const mesh of pool.meshes) { mesh.removeFromParent(); mesh.dispose(); }
    this.pools.clear(); this.poolRoot?.removeFromParent();
  }

  add(model: THREE.Object3D) {
    if (this.disposed) return;
    if (!this.library) throw new Error('VegetationLod.add before the nature library loaded');
    const steps = this.addSteps(model, this.library);
    while (!steps.next().done) { /* Synchronous fixture/startup caller. */ }
  }

  async addAsync(model: THREE.Object3D) {
    if (this.disposed) return;
    const existing = this.pending.get(model);
    if (existing) return existing.promise;
    const job = { cancelled: false, promise: Promise.resolve() };
    this.pending.set(model, job);
    job.promise = (async () => {
      await this.ready;
      if (job.cancelled || this.disposed) return;
      const steps = this.addSteps(model, this.library!);
      try {
        while (!job.cancelled && !this.disposed && !steps.next().done) {
          const pending = this.budget.checkpoint();
          if (pending) await pending;
        }
      } finally { steps.return(undefined); }
    })().finally(() => { if (this.pending.get(model) === job) this.pending.delete(model); });
    return job.promise;
  }

  private *addSteps(model: THREE.Object3D, library: NatureLibrary): Generator<void> {
    if (this.models.has(model)) return;
    const data: Model = { sources: [], draws: [], buckets: [] };
    // Collect before adding children: traversal must never revisit generated meshes.
    const fences: { mesh: THREE.InstancedMesh; key: string }[] = [];
    model.traverse(object => {
      const mesh = object as THREE.InstancedMesh;
      if (!mesh.isInstancedMesh || Array.isArray(mesh.material)) return;
      if (vegetationOwner(mesh)) { data.sources.push({ mesh, visible: mesh.visible, shadow: mesh.castShadow }); return; }
      const key = materialKey(mesh, mesh.material);
      const kit = key === null ? null : fenceAsset(key, 1);
      if (kit === null) return;
      data.sources.push({ mesh, visible: mesh.visible, shadow: mesh.castShadow });
      if (kit) fences.push({ mesh, key: key! });
    });
    this.models.set(model, data); this.dirty = true;
    if (this.grass) yield* this.grass.addSteps(model);
    if (!data.sources.length) return;
    for (const { mesh } of data.sources) { mesh.visible = false; mesh.castShadow = false; }
    // Bark and crown prototypes of one variant share their placements: one representative per variant and parent.
    const representatives = new Map<string, THREE.InstancedMesh>();
    for (const { mesh } of data.sources) {
      const owner = vegetationOwner(mesh);
      if (!owner) continue;
      const variant = String(owner.userData.variant ?? owner.name.replace(/:\d+$/, ''));
      const key = `${mesh.parent?.id}:${variant}`;
      if (!representatives.has(key)) representatives.set(key, mesh);
    }
    const matrix = new THREE.Matrix4(), t = new THREE.Vector3(), q = new THREE.Quaternion(), s = new THREE.Vector3();
    const water = representatives.size ? yield* waterCells(model) : new Set<string>();
    const toModel = new THREE.Matrix4(), inModel = new THREE.Vector3();
    for (const mesh of representatives.values()) {
      const owner = vegetationOwner(mesh)!;
      const variant = String(owner.userData.variant ?? owner.name.replace(/:\d+$/, ''));
      const ids = owner.userData.instance_object_ids as unknown[] | undefined;
      toModel.copy(model.matrixWorld).invert().multiply(mesh.matrixWorld);
      const byAsset: Placements = new Map();
      for (let i = 0; i < mesh.count; i++) {
        if (i % 256 === 0) yield;
        mesh.getMatrixAt(i, matrix);
        matrix.decompose(t, q, s);
        const id = ids?.[i] ?? `${variant}:${i}`;
        inModel.copy(t).applyMatrix4(toModel);
        const nearWater = owner.userData.instance_near_water?.[i] ?? water.has(waterCell(inModel.x, inModel.z));
        const asset = library.variant(natureAsset(variant, String(id), s.y, nearWater, owner.userData.instance_species?.[i] ?? ""), String(id)), name = asset.name;
        const scale = natureScale(s.y, Math.max(s.x, s.z), asset.height, asset.radius);
        place(byAsset, name, new THREE.Matrix4().compose(t, q, s.set(scale.xz, scale.y, scale.xz)), natureTint(String(id)), ids?.[i]);
      }
      yield* this.commit(data, mesh, owner, byAsset, library, true);
    }
    // Barrier segments (unit prototypes scaled to length x height x thickness) become 2 m kit modules along their
    // length; granite walls also stack 0.6 m courses. Their shadows stay in the tile's shadow batch.
    const offset = new THREE.Vector3(), module = new THREE.Vector3();
    for (const { mesh, key } of fences) {
      const byAsset: Placements = new Map();
      for (let i = 0; i < mesh.count; i++) {
        if (i % 256 === 0) yield;
        mesh.getMatrixAt(i, matrix);
        matrix.decompose(t, q, s);
        const name = fenceAsset(key, s.y)!, asset = library.asset(name);
        const tint = fenceTint(name, `${key}:${t.x.toFixed(1)},${t.z.toFixed(1)}`);
        const modules = Math.max(1, Math.round(s.x / (asset.module ?? 2))), length = s.x / modules;
        const courses = name === 'granite_wall' ? Math.max(1, Math.round(s.y / asset.height)) : 1;
        for (let j = 0; j < modules; j++) for (let k = 0; k < courses; k++) {
          offset.set(-s.x / 2 + length * (j + 0.5), s.y * k / courses, 0).applyQuaternion(q).add(t);
          module.set(length / (asset.module ?? 2), s.y / courses / asset.height, name === 'hedge' ? s.z / 0.8 : 1);
          place(byAsset, name, new THREE.Matrix4().compose(offset, q, module), tint, undefined);
        }
      }
      yield* this.commit(data, mesh, mesh, byAsset, library, false);
    }
    if (this.edits.size) for (const bucket of data.buckets) this.applyEdits(bucket);
  }

  /** Editable trees of the drawn tiles (dev/editor): world matrix, asset size and identity of every placement with an
   *  id. Barrier modules have no ids and are skipped. Edited placements report the tile's own (original) matrix. */
  forEachTree(visit: (tree: { id: string; asset: string; matrix: THREE.Matrix4; height: number; radius: number }) => void) {
    const local = new THREE.Matrix4(), world = new THREE.Matrix4();
    for (const [model, data] of this.models) {
      if (this.activeTiles.get(model) !== true) continue;
      for (const bucket of data.buckets) {
        if (bucket.draw.barrier) continue;
        const root = bucket.draw.root;
        root.updateWorldMatrix(true, false);
        const asset = this.library?.asset(bucket.draw.asset);
        for (let i = 0; i < bucket.count; i++) {
          const id = bucket.objectIds[i];
          if (typeof id !== 'string') continue;
          const original = this.editOriginals.get(id);
          world.multiplyMatrices(root.matrixWorld, original?.bucket === bucket ? local.fromArray(original.matrix) : local.fromArray(bucket.matrices, i * 16));
          const sy = Math.hypot(world.elements[4], world.elements[5], world.elements[6]);
          const sx = Math.hypot(world.elements[0], world.elements[1], world.elements[2]);
          visit({ id, asset: bucket.draw.asset, matrix: world, height: (asset?.height ?? 8) * sy, radius: (asset?.radius ?? 2) * sx });
        }
      }
    }
  }

  /** Preview an edited placement: `delta` takes its original world matrix to the new one (new = delta × original),
   *  null hides it, undefined restores the tile's own. Applied again whenever its tile loads. */
  setEdit(id: string, delta: THREE.Matrix4 | null | undefined) {
    if (delta === undefined) this.edits.delete(id); else this.edits.set(id, delta?.clone() ?? null);
    for (const data of this.models.values()) for (const bucket of data.buckets) if (bucket.objectIds.includes(id)) this.applyEdits(bucket, id);
  }

  private applyEdits(bucket: Bucket, only?: string) {
    const root = bucket.draw.root, inverse = new THREE.Matrix4(), m = new THREE.Matrix4(), p = new THREE.Vector3();
    let changed = false;
    for (let i = 0; i < bucket.count; i++) {
      const id = bucket.objectIds[i];
      if (typeof id !== 'string' || (only !== undefined && id !== only)) continue;
      const original = this.editOriginals.get(id);
      if (!this.edits.has(id) && (!original || original.bucket !== bucket)) continue;
      if (!original || original.bucket !== bucket) this.editOriginals.set(id, { bucket, index: i, matrix: bucket.matrices.slice(i * 16, i * 16 + 16) });
      const edit = this.edits.get(id);
      root.updateWorldMatrix(true, false);
      if (edit === undefined) {
        bucket.matrices.set(this.editOriginals.get(id)!.matrix, i * 16);
        this.editOriginals.delete(id);
      } else if (edit === null) {
        m.fromArray(this.editOriginals.get(id)!.matrix);
        p.setFromMatrixPosition(m);
        m.makeScale(0, 0, 0).setPosition(p).toArray(bucket.matrices, i * 16);
      } else {
        // local' = root⁻¹ · delta · root · local (the original local matrix, not an earlier preview).
        m.fromArray(this.editOriginals.get(id)!.matrix).premultiply(root.matrixWorld).premultiply(edit)
          .premultiply(inverse.copy(root.matrixWorld).invert()).toArray(bucket.matrices, i * 16);
      }
      changed = true;
    }
    if (!changed) return;
    // Recompute the bucket's world positions and bounds; force its draw to repack (selections compare indices only).
    bucket.world = undefined;
    const local = new THREE.Sphere().makeEmpty(), asset = this.library?.asset(bucket.draw.asset);
    const sphere = asset ? assetSphere(asset) : new THREE.Sphere(new THREE.Vector3(), 3);
    for (let i = 0; i < bucket.count; i++) local.union(sphere.clone().applyMatrix4(m.fromArray(bucket.matrices, i * 16)));
    bucket.bounds = local;
    for (const level of bucket.draw.levels) for (const part of level) part.boundingSphere?.union(local);
    for (const previous of bucket.draw.previous) previous.length = 0;
    this.dirty = true;
  }

  /** One draw per asset part and LOD for this source's placements, bucketed into 50 m cells. */
  private *commit(data: Model, source: THREE.InstancedMesh, owner: THREE.Object3D, byAsset: Placements, library: NatureLibrary, shadows: boolean): Generator<void> {
    for (const [name, cells] of byAsset) {
      yield;
      const asset = library.asset(name);
      const count = [...cells.values()].reduce((n, list) => n + list.length, 0);
      const draw = this.createDraw(source, owner, asset, count, shadows);
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
        data.buckets.push({ draw, matrices, colors, objectIds: list.map(p => p.id), bounds, distance: 0, count: list.length, index: data.buckets.length });
      }
      // Static conservative bounds cover every possible packed selection and all cheaper LODs.
      for (const level of draw.levels) for (const part of level) part.boundingSphere = all.clone();
    }
  }

  private createDraw(source: THREE.InstancedMesh, owner: THREE.Object3D, asset: NatureAsset, count: number, shadows: boolean): Draw {
    const root = new THREE.Group();
    root.name = `vegetation-lod:${asset.name}`; root.userData.vegetationLod = true;
    root.position.copy(source.position); root.quaternion.copy(source.quaternion); root.scale.copy(source.scale);
    const levels = asset.lods.map((lod, level) => {
      const matrices = new THREE.InstancedBufferAttribute(new Float32Array(count * 16), 16);
      const colors = new THREE.InstancedBufferAttribute(new Float32Array(count * 3), 3);
      const objectIds: unknown[] = [];
      const impostor = asset.kind === 'tree' && level === asset.lods.length - 1;
      const batch = (part: NaturePart, shadowProxy = false) => {
        // Drawn levels cross-fade (their level's screen-door variant); shadow proxies cast with the plain material.
        const material = shadowProxy || !this.library ? part.material : this.library.materials.lodVariant(part.material, level);
        const mesh = new THREE.InstancedMesh(part.geometry, material, count);
        mesh.instanceMatrix = matrices; mesh.instanceColor = colors;
        // Only the owner's identity travels: a generated draw must not look like a tile surface to material styling.
        mesh.userData = { layer: owner.userData.layer, variant: owner.userData.variant, vegetationLod: true, nature: asset.name, level, shadowProxy, instance_object_ids: objectIds,
          ...(this.poolRoot ? { pooledVegetation: true } : {}) };
        // Library materials are object-agnostic: every tile's draws of this part reuse one built program per pass.
        // (After userData: replacing it dropped this opt-in, so trees never shared programs.)
        shareInstanceProgram(mesh, true);
        mesh.receiveShadow = !impostor && !shadowProxy; mesh.castShadow = shadowProxy;
        if (shadowProxy) mesh.layers.set(TILE_SHADOW_LAYER);
        mesh.visible = false; mesh.count = 0;
        root.add(mesh);
        return mesh;
      };
      const parts = lod.parts.map(part => batch(part));
      // Near trees cast their shadow with the reduced LOD on the shadow-only layer: the same canopy cards at about a
      // third of the triangles, as GTA's per-tree shadow proxies (docs/reports/environment-assets.md).
      if (shadows && level === 0 && asset.lods.length > 1) parts.push(...asset.lods[1].parts.map(part => batch(part, true)));
      return parts;
    });
    source.parent!.add(root);
    return { barrier: !shadows, asset: asset.name, id: this.nextDraw++, root, levels, selections: levels.map(() => []), previous: levels.map(() => []) };
  }

  /** A still camera (within 5 cm and 0.1 degrees) with the same lens and screenshot range selects the same trees. */
  private still(camera: THREE.PerspectiveCamera) {
    const last = this.last;
    camera.getWorldPosition(this.position);
    this.forward.set(0, 0, -1).transformDirection(camera.matrixWorld);
    this.up.set(0, 1, 0).transformDirection(camera.matrixWorld);
    const still = this.position.distanceToSquared(last.position) < 0.0025 && this.forward.dot(last.forward) > 0.9999985
      && this.up.dot(last.up) > 0.9999985 && camera.fov === last.fov && camera.aspect === last.aspect && camera.near === last.near
      && camera.far === last.far && camera.zoom === last.zoom && this.screenshotDistance === last.distance;
    if (!still) {
      last.position.copy(this.position); last.forward.copy(this.forward); last.up.copy(this.up);
      last.fov = camera.fov; last.aspect = camera.aspect; last.near = camera.near; last.far = camera.far; last.zoom = camera.zoom;
      last.distance = this.screenshotDistance;
    }
    return still;
  }

  update(camera: THREE.PerspectiveCamera) {
    camera.updateMatrixWorld();
    this.grass?.update(camera);
    const still = this.still(camera);
    let changed = this.dirty;
    for (const [model] of this.models) {
      let active = true;
      for (let node: THREE.Object3D | null = model; node; node = node.parent) if (!node.visible) active = false;
      // Inactive tiles can be cached with a parent pointer but absent from the scene's children.
      if (!model.parent || !model.parent.children.includes(model)) active = false;
      if (this.activeTiles.get(model) !== active) { this.activeTiles.set(model, active); changed = true; }
    }
    if (still && !changed) return;
    this.dirty = false;
    this.projection.multiplyMatrices(camera.projectionMatrix, camera.matrixWorldInverse);
    this.frustum.setFromProjectionMatrix(this.projection, camera.coordinateSystem);
    const q = QUALITY[this.tier].vegetation;
    this.visible.length = 0;
    for (const [model, data] of this.models) {
      const active = this.activeTiles.get(model) === true;
      for (const draw of data.draws) {
        for (const selection of draw.selections) selection.length = 0;
        for (const level of draw.levels) for (const part of level) part.visible = false;
      }
      if (!active) continue;
      for (const bucket of data.buckets) {
        if (!bucket.world) this.place(bucket);
        const sphere = bucket.sphere!;
        bucket.distance = Math.max(0, this.position.distanceTo(sphere.center) - sphere.radius);
        if (bucket.distance <= Math.max(q.far, this.screenshotDistance) && this.frustum.intersectsSphere(sphere)) this.visible.push(bucket);
      }
    }
    this.visible.sort((a, b) => a.distance - b.distance);
    const bands = this.bands;
    if (!Number.isFinite(bands[0])) { bands[0] = q.near; bands[2] = q.far; }
    bands[1] = q.mid;
    const reach = Math.max(bands[2], this.screenshotDistance);
    // Soft budgets: counted, then folded into the boundaries below. A near tree is never left out (it has no other
    // draw inside the near band); impostors far past their budget (1.5x, the farthest buckets) are.
    let nearDrawn = 0, farDrawn = 0;
    const farCap = this.screenshotDistance ? Infinity : q.farInstances * 1.5;
    let barriers = q.barrierInstances;
    for (const bucket of this.visible) {
      let level = bucket.distance < q.near ? 0 : bucket.distance < q.mid ? 1 : 2;
      if (bucket.draw.barrier) {
        // Barriers have no impostor, but their last mesh remains visible to the far limit.
        // Keep their modules out of the tree budgets, including distant fences with colliders.
        const count = Math.min(bucket.count, barriers);
        barriers -= count;
        if (count) bucket.draw.selections[Math.min(level, bucket.draw.levels.length - 1)].push({ bucket, offset: 0, count });
        continue;
      }
      // Cells are only culling units. The tree's own position selects its LODs, in either pass: one level, or two
      // while it is inside a cross-fade band (the shader splits the pixels between them).
      const world = bucket.world!, px = this.position.x, py = this.position.y, pz = this.position.z;
      const levels = bucket.draw.levels.length;
      for (let offset=0;offset<bucket.count;offset++) {
        const dx = world[offset * 3] - px, dy = world[offset * 3 + 1] - py, dz = world[offset * 3 + 2] - pz;
        const distance=Math.sqrt(dx * dx + dy * dy + dz * dz);
        if (distance>reach) continue;
        let mask = this.screenshotDistance && distance > bands[2] ? (levels > 2 ? 4 : 0) : vegetationLevels(distance, bands, levels);
        if (mask & 1) nearDrawn++;
        if (mask & 4 && ++farDrawn > farCap) mask &= ~4;
        for (let k = 0; k < levels; k++) {
          if (!(mask & (1 << k))) continue;
          const selections=bucket.draw.selections[k], last=selections[selections.length-1];
          if (last?.bucket===bucket && last.offset+last.count===offset) last.count++;
          else selections.push({bucket,offset,count:1});
        }
      }
    }
    // Budgets move the boundaries, smoothly: the near one at most to a quarter, the far one no closer than mid + 50 m.
    if (nearDrawn > q.nearInstances) bands[0] = Math.max(q.near * 0.25, bands[0] - 1);
    else if (nearDrawn < q.nearInstances * 0.85) bands[0] = Math.min(q.near, bands[0] + 0.5);
    if (!this.screenshotDistance && farDrawn > q.farInstances) bands[2] = Math.max(q.mid + 50, bands[2] - 10);
    else if (farDrawn < q.farInstances * 0.85) bands[2] = Math.min(q.far, bands[2] + 5);
    this.library?.materials.lodBands.value.set(bands[0], bands[1], Math.max(bands[2], this.screenshotDistance));
    // Cells cull independently, but compact into one draw per tile/asset/LOD part.
    // Only changed cell selections upload matrices; stationary views do no buffer writes.
    for (const data of this.models.values()) {
      for (const draw of data.draws) {
        for (let level = 0; level < draw.levels.length; level++) {
          const parts = draw.levels[level], selections = draw.selections[level], previous = draw.previous[level];
          if (!selections.length) continue;
          selections.sort((a, b) => a.bucket.index - b.bucket.index);
          // Distance chooses membership, not buffer order. Passing a cell must not repack unchanged instances.
          let changed = previous.length !== selections.length * 3;
          for (let i = 0; !changed && i < selections.length; i++) {
            const selection = selections[i];
            changed = previous[i * 3] !== selection.bucket.index || previous[i * 3 + 1] !== selection.offset
              || previous[i * 3 + 2] !== selection.count;
          }
          const first = parts[0];
          if (changed) {
            const matrices = first.instanceMatrix.array as Float32Array, colors = first.instanceColor!.array as Float32Array;
            let offset = 0;
            const objectIds = first.userData.instance_object_ids as unknown[];
            objectIds.length = 0; previous.length = 0;
            for (const { bucket, offset: start, count } of selections) {
              matrices.set(bucket.matrices.subarray(start * 16, (start + count) * 16), offset * 16);
              colors.set(bucket.colors.subarray(start * 3, (start + count) * 3), offset * 3);
              offset += count;
              for (let i = start; i < start + count; i++) objectIds.push(bucket.objectIds[i]);
              previous.push(bucket.index, start, count);
            }
            for (const part of parts) part.count = offset;
            for (const [attribute, width] of [[first.instanceMatrix, 16], [first.instanceColor!, 3]] as const) {
              attribute.clearUpdateRanges(); attribute.addUpdateRange(0, offset * width);
              attribute.needsUpdate = true;
            }
          }
          for (const part of parts) part.visible = part.count > 0;
        }
      }
    }
    if (this.poolRoot) this.updatePools();
  }

  /** A bucket's world bounds and instance positions, once: tile roots never move after their install. */
  private place(bucket: Bucket) {
    const root = bucket.draw.root;
    root.updateWorldMatrix(true, false);
    bucket.sphere = bucket.bounds.clone().applyMatrix4(root.matrixWorld);
    const world = bucket.world = new Float32Array(bucket.count * 3);
    for (let i = 0; i < bucket.count; i++) {
      this.treePosition.fromArray(bucket.matrices, i * 16 + 12).applyMatrix4(root.matrixWorld).toArray(world, i * 3);
    }
  }

  /** Repack an asset LOD's city-wide instances only when a contributing tile's selection changed. Tile roots are
   * static, so their instances go to world space once per change; stationary views write nothing. */
  private updatePools() {
    const live = new Map<string, { parts: THREE.InstancedMesh[]; level: number; draws: Draw[] }>();
    for (const data of this.models.values()) for (const draw of data.draws) {
      for (let level = 0; level < draw.levels.length; level++) {
        const parts = draw.levels[level];
        if (!parts[0].visible || parts[0].count === 0) continue;
        const key = `${draw.asset}|${level}|${parts.length}`; // with or without shadow proxies
        let entry = live.get(key);
        if (!entry) live.set(key, entry = { parts, level, draws: [] });
        entry.draws.push(draw);
      }
    }
    for (const [key, pool] of this.pools) if (!live.has(key)) { for (const mesh of pool.meshes) mesh.visible = false; pool.signature.length = 0; }
    for (const [key, { parts, level, draws }] of live) {
      let pool = this.pools.get(key);
      if (!pool) this.pools.set(key, pool = { meshes: [], matrices: null, colors: null, capacity: 0, signature: [], objectIds: [] });
      const signature = this.signature; signature.length = 0;
      let total = 0;
      for (const draw of draws) { const first = draw.levels[level][0]; total += first.count; signature.push(draw.id, first.count, first.instanceMatrix.version); }
      const same = signature.length === pool.signature.length && signature.every((value, i) => value === pool!.signature[i]);
      if (same) { for (const mesh of pool.meshes) mesh.visible = mesh.count > 0; continue; }
      pool.signature = signature.slice();
      if (total > pool.capacity) this.growPool(pool, parts, total);
      const matrices = pool.matrices!.array as Float32Array, colors = pool.colors!.array as Float32Array;
      const bounds = this.poolSphere.makeEmpty(), sphere = this.sphere;
      let offset = 0; pool.objectIds.length = 0;
      for (const draw of draws) {
        const first = draw.levels[level][0], count = first.count, world = draw.root.matrixWorld;
        const source = first.instanceMatrix.array as Float32Array;
        for (let i = 0; i < count; i++) this.instance.fromArray(source, i * 16).premultiply(world).toArray(matrices, (offset + i) * 16);
        colors.set((first.instanceColor!.array as Float32Array).subarray(0, count * 3), offset * 3);
        for (const id of first.userData.instance_object_ids as unknown[]) pool.objectIds.push(id);
        if (first.boundingSphere) bounds.union(sphere.copy(first.boundingSphere).applyMatrix4(world));
        offset += count;
      }
      for (const [attribute, width] of [[pool.matrices!, 16], [pool.colors!, 3]] as const) {
        attribute.clearUpdateRanges(); attribute.addUpdateRange(0, offset * width); attribute.needsUpdate = true;
      }
      for (const mesh of pool.meshes) { mesh.count = offset; mesh.visible = offset > 0; mesh.boundingSphere = bounds.clone(); }
    }
  }

  private growPool(pool: Pool, parts: THREE.InstancedMesh[], total: number) {
    for (const mesh of pool.meshes) { mesh.removeFromParent(); mesh.dispose(); }
    pool.capacity = Math.max(64, 2 ** Math.ceil(Math.log2(Math.max(1, total))));
    pool.matrices = new THREE.InstancedBufferAttribute(new Float32Array(pool.capacity * 16), 16);
    pool.colors = new THREE.InstancedBufferAttribute(new Float32Array(pool.capacity * 3), 3);
    pool.meshes = parts.map(part => {
      const mesh = new THREE.InstancedMesh(part.geometry, part.material, pool.capacity);
      mesh.instanceMatrix = pool.matrices!; mesh.instanceColor = pool.colors;
      const { pooledVegetation: _, ...userData } = part.userData;
      mesh.userData = { ...userData, instance_object_ids: pool.objectIds };
      shareInstanceProgram(mesh, true); // the tile draws' prepared programs, also after the pool grows
      mesh.name = `vegetation-pool:${part.userData.nature}:${part.userData.level}`;
      mesh.receiveShadow = part.receiveShadow && !(leafVariant === 'noshadow' && (part.material as THREE.Material).name === 'nature-leaves');
      mesh.castShadow = part.castShadow; mesh.layers.mask = part.layers.mask;
      mesh.count = 0; mesh.visible = false;
      this.poolRoot!.add(mesh);
      return mesh;
    });
  }

  remove(model: THREE.Object3D) {
    const pending = this.pending.get(model);
    if (pending) { pending.cancelled = true; this.pending.delete(model); }
    this.grass?.remove(model);
    const data = this.models.get(model);
    if (!data) return;
    for (const draw of data.draws) {
      draw.root.removeFromParent();
      for (const level of draw.levels) for (const part of level) part.dispose();
    }
    for (const source of data.sources) { source.mesh.visible = source.visible; source.mesh.castShadow = source.shadow; }
    this.models.delete(model); this.activeTiles.delete(model); this.dirty = true;
  }
}

const WATER_CELL = 4, WATER_REACH = 14;
const waterCell = (x: number, z: number) => `${Math.floor(x / WATER_CELL)},${Math.floor(z / WATER_CELL)}`;

/** Model-space 4 m sample-cell neighborhoods around all water-triangle edges. */
export function* waterCells(model: THREE.Object3D): Generator<void, Set<string>> {
  const cells = new Set<string>(), sampled = new Set<string>();
  model.updateWorldMatrix(true, true);
  const inverse = model.matrixWorld.clone().invert(), toModel = new THREE.Matrix4();
  const meshes: THREE.Mesh[] = [];
  model.traverse(o => {
    const mesh = o as THREE.Mesh;
    if (!mesh.isMesh || (mesh as THREE.InstancedMesh).isInstancedMesh || Array.isArray(mesh.material)) return;
    const key = materialKey(mesh, mesh.material);
    if (mesh.userData.surface === 'water' || (key && surfaceStyle(key) === 'water')) meshes.push(mesh);
  });
  const a = new THREE.Vector3(), b = new THREE.Vector3(), r = Math.ceil(WATER_REACH / WATER_CELL);
  for (const mesh of meshes) {
    toModel.multiplyMatrices(inverse, mesh.matrixWorld);
    const position = mesh.geometry.getAttribute('position'), index = mesh.geometry.index;
    const count = index?.count ?? position.count;
    for (let i = 0; i < count; i++) {
      if (i % 3000 === 0) yield;
      const next = i % 3 === 2 ? i - 2 : i + 1;
      a.fromBufferAttribute(position, index?.getX(i) ?? i).applyMatrix4(toModel);
      b.fromBufferAttribute(position, index?.getX(next) ?? next).applyMatrix4(toModel);
      // Sample every triangle edge; interior edges of a water polygon are harmless (also water).
      const steps = Math.max(1, Math.ceil(a.distanceTo(b) / WATER_CELL));
      for (let k = 0; k <= steps; k++) {
        const x = Math.floor((a.x + (b.x - a.x) * k / steps) / WATER_CELL), z = Math.floor((a.z + (b.z - a.z) * k / steps) / WATER_CELL);
        // Shared edges and sub-cell triangles repeat centres with exactly the same bank band.
        const cell = `${x},${z}`;
        if (sampled.has(cell)) continue;
        sampled.add(cell);
        for (let dz = -r; dz <= r; dz++) for (let dx = -r; dx <= r; dx++) cells.add(`${x + dx},${z + dz}`);
      }
    }
  }
  return cells;
}

/** Bounding sphere of an asset at scale 1 (base at the origin). */
function assetSphere(asset: NatureAsset) {
  return new THREE.Sphere(new THREE.Vector3(0, asset.height / 2, 0), Math.hypot(asset.radius, asset.height / 2));
}

// Update resident models without restarting the streamed world.
if (import.meta.hot) import.meta.hot.accept(module => {
  const replace = window.__game?.replaceVegetation as ((Renderer: typeof VegetationLod) => Promise<void>) | undefined;
  if (module && replace) void replace(module.VegetationLod);
});
