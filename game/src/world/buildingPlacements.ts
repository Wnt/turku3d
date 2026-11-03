import { updateStaticBatchVisibility } from './staticBatch';
import * as THREE from 'three/webgpu';
import { mergeGeometries } from 'three/addons/utils/BufferGeometryUtils.js';
import { shareModelTextures } from './sharedAssets';
import { retainGeometry } from './modelGeometry';
import { PreparationBudget } from '../render/preparationBudget';
import { placementVisibility, type PlacementVisibility } from './placementVisibility';
import { lodFades, type LodFades } from '../render/lodFade';
import { StaticGroup } from './frozenTransforms';

type Vec3 = [number, number, number];
export interface BuildingModel {
  id: string; uri: string; bounds: { min: Vec3; max: Vec3 };
  /** Conservative opaque volumes, never inferred from display bounds. Owned by perfcpu. */
  occluders?: unknown;
}
/** A block of nearby buildings: its HD children show within `hdDistance` of `bounds`, its `lod` model beyond. */
export interface BuildingBlock { id: string; position: Vec3; bounds: { min: Vec3; max: Vec3 }; hdDistance: number; children: string[]; lod?: string }
export interface BuildingPlacements {
  version: 1;
  models: BuildingModel[];
  blocks?: BuildingBlock[];
  /** `block`: generator block of nearby buildings; placements batch and (later) swap to LOD per block. */
  placements: { id: string; model: string; position: Vec3; viewDistance: number; block?: string }[];
}

export function validateBuildingPlacements(data: BuildingPlacements) {
  if (data.version !== 1 || !Array.isArray(data.models) || !Array.isArray(data.placements))
    throw new Error('Unsupported building placements');
  const vector = (v: number[]) => Array.isArray(v) && v.length === 3 && v.every(Number.isFinite);
  const models = new Set<string>(), ids = new Set<string>();
  for (const model of data.models) {
    if (!/^[a-f0-9]{64}$/.test(model.id) || models.has(model.id) || typeof model.uri !== 'string'
      || !model.bounds || !vector(model.bounds.min) || !vector(model.bounds.max)
      || model.bounds.min.some((v, i) => v > model.bounds.max[i])) throw new Error('Invalid building model');
    models.add(model.id);
  }
  for (const placement of data.placements) {
    if (typeof placement.id !== 'string' || ids.has(placement.id) || !models.has(placement.model)
      || !vector(placement.position) || !Number.isFinite(placement.viewDistance) || placement.viewDistance <= 0
      || (placement.block !== undefined && typeof placement.block !== 'string'))
      throw new Error('Invalid building placement');
    ids.add(placement.id);
  }
  const blocks = new Set<string>();
  for (const block of data.blocks ?? []) {
    if (typeof block.id !== 'string' || blocks.has(block.id) || !vector(block.position) || !block.bounds
      || !vector(block.bounds.min) || !vector(block.bounds.max) || !Number.isFinite(block.hdDistance) || block.hdDistance <= 0
      || !Array.isArray(block.children) || !block.children.every(id => ids.has(id))
      || (block.lod !== undefined && !models.has(block.lod))) throw new Error('Invalid building block');
    blocks.add(block.id);
  }
}

/** Swap state of one block LOD parent, kept on the tile model's userData. `near` is the band the camera is in;
 *  `hd` whether the HD children draw: it lags `near` while the LOD shell dissolves in over them. */
interface BlockState { lod: THREE.Object3D; bounds: BuildingBlock['bounds']; hdDistance: number; near: boolean; hd: boolean; settled: boolean }
/** Metres of hysteresis around a block's HD distance, so a camera on the threshold never flickers. */
const SWAP_BAND = 10;

interface Resident { users: number; promise: Promise<THREE.Object3D> }
interface Lease { root: THREE.Object3D; release: () => void }

/** Decode each content model once while resident. Tile cancellation releases every successful lease. */
export class BuildingModelCache {
  private entries = new Map<string, Resident>();
  private active = 0;
  private queue: (() => void)[] = [];
  constructor(private load: (url: string) => Promise<THREE.Object3D>,
    private dispose: (root: THREE.Object3D) => void, private budget = new PreparationBudget(),
    private visibility: PlacementVisibility = placementVisibility,
    private styleLod: (root: THREE.Object3D, owner: THREE.Object3D) => void = () => {}) {}

  private async read(url: string) {
    if (this.active >= 4) await new Promise<void>(resolve => this.queue.push(resolve));
    else this.active++;
    try {
      const root = await this.load(url);
      const geometries = new Set<THREE.BufferGeometry>();
      root.traverse(o => { if ((o as THREE.Mesh).isMesh) geometries.add((o as THREE.Mesh).geometry); });
      for (const geometry of geometries) retainGeometry(geometry);
      return root;
    } finally {
      const next = this.queue.shift();
      if (next) next(); else this.active--;
    }
  }

  private async acquire(model: BuildingModel, base: URL): Promise<Lease> {
    // Hashes are reusable across generation URL prefixes, but never across content hosts.
    const url = new URL(model.uri, base), key = `${url.origin}/${model.id}`;
    let entry = this.entries.get(key);
    if (!entry) { entry = { users: 0, promise: this.read(url.href) }; this.entries.set(key, entry); }
    entry.users++;
    let released = false;
    const release = () => {
      if (released) return;
      released = true;
      if (--entry.users) return;
      if (this.entries.get(key) === entry) this.entries.delete(key);
      void entry.promise.then(root => this.dispose(root), () => {});
    };
    try { return { root: await entry.promise, release }; }
    catch (error) { release(); throw error; }
  }

  async attach(owner: THREE.Object3D, data: BuildingPlacements, base: URL): Promise<() => void> {
    validateBuildingPlacements(data);
    const descriptors = new Map(data.models.map(model => [model.id, model]));
    const needed = [...new Set([...data.placements.map(p => p.model), ...(data.blocks ?? []).flatMap(b => b.lod ? [b.lod] : [])])];
    const results = await Promise.allSettled(needed.map(id => this.acquire(descriptors.get(id)!, base)));
    const leases = results.flatMap(result => result.status === 'fulfilled' ? [result.value] : []);
    const failure = results.find(result => result.status === 'rejected');
    if (failure?.status === 'rejected') { for (const lease of leases) lease.release(); throw failure.reason; }
    const templates = new Map(needed.map((id, i) => [id, leases[i].root]));
    const geometries = new Set<THREE.BufferGeometry>();
    const materials = new Map<THREE.Material, THREE.Material>(), textures = new Map<THREE.Texture, THREE.Texture>();
    const slots: number[] = [];
    for (const placement of data.placements) {
      // Network admission does not bound the clone burst after all model promises resolve.
      let pause = this.budget.checkpoint();
      // Other tile attachments can spend this shared budget when the same yield resolves.
      while (pause) { await pause; pause = this.budget.checkpoint(); }
      const descriptor = descriptors.get(placement.model)!;
      const root = templates.get(placement.model)!.clone(true);
      root.traverse(o => {
        const mesh = o as THREE.Mesh;
        if (!mesh.isMesh) return;
        geometries.add(mesh.geometry);
        const cloneMaterial = (source: THREE.Material) => {
          let material = materials.get(source);
          if (material) return material;
          material = source.clone(); materials.set(source, material);
          for (const [slot, value] of Object.entries(material)) {
            if (!(value as THREE.Texture)?.isTexture) continue;
            const sourceTexture = value as THREE.Texture;
            let texture = textures.get(sourceTexture);
            if (!texture) { texture = sourceTexture.clone(); textures.set(sourceTexture, texture); }
            (material as unknown as Record<string, unknown>)[slot] = texture;
          }
          return material;
        };
        mesh.material = Array.isArray(mesh.material) ? mesh.material.map(cloneMaterial) : cloneMaterial(mesh.material);
      });
      const wrapper = new StaticGroup();
      wrapper.name = `building:${placement.id}`;
      wrapper.position.fromArray(placement.position);
      wrapper.userData = { archetype: placement.model, placementId: placement.id, block: placement.block,
        viewDistance: placement.viewDistance, modelBounds: descriptor.bounds, occluders: descriptor.occluders };
      wrapper.add(root); owner.add(wrapper); wrapper.freeze();
      slots.push(this.visibility.register(wrapper, descriptor.bounds, placement.viewDistance, descriptor.occluders));
    }
    // Block LOD parents: one clone per block, swapped against its HD children by camera distance.
    const states = new Map<string, BlockState>();
    for (const block of data.blocks ?? []) {
      if (!block.lod) continue;
      const root = templates.get(block.lod)!.clone(true);
      root.traverse(o => { if ((o as THREE.Mesh).isMesh) geometries.add((o as THREE.Mesh).geometry); });
      this.styleLod(root, owner);
      const wrapper = new StaticGroup();
      wrapper.name = `block-lod:${block.id}`;
      wrapper.position.fromArray(block.position);
      wrapper.userData = { blockLod: block.id };
      wrapper.add(root); owner.add(wrapper); wrapper.freeze();
      states.set(block.id, { lod: wrapper, bounds: block.bounds, hdDistance: block.hdDistance, near: true, hd: true, settled: false });
    }
    if (states.size) {
      owner.userData.blockLods = states;
      owner.userData.blockLodTile = mergeBlockLods(owner, [...states.values()].map(s => s.lod));
    }
    for (const geometry of geometries) retainGeometry(geometry);
    // Each unstyled material owner enters the existing styling cache independently.
    shareModelTextures(owner);
    return () => {
      for (const slot of slots.splice(0)) this.visibility.unregister(slot);
      for (const lease of leases) lease.release();
    };
  }
}

/** One merged parent per LOD material for a tile whose blocks are all far: a far tile costs one or two draws. */
function mergeBlockLods(owner: THREE.Object3D, wrappers: THREE.Object3D[]) {
  owner.updateMatrixWorld(true);
  const inverse = owner.matrixWorld.clone().invert(), groups = new Map<THREE.Material, THREE.BufferGeometry[]>();
  for (const wrapper of wrappers) wrapper.traverse(o => {
    const mesh = o as THREE.Mesh;
    if (!mesh.isMesh || Array.isArray(mesh.material)) return;
    const geometry = mesh.geometry.clone().applyMatrix4(inverse.clone().multiply(mesh.matrixWorld));
    groups.set(mesh.material, [...groups.get(mesh.material) ?? [], geometry]);
  });
  const merged: THREE.Mesh[] = [];
  for (const [material, geometries] of groups) {
    const geometry = mergeGeometries(geometries);
    for (const part of geometries) part.dispose();
    if (!geometry) continue;
    geometry.computeBoundingSphere();
    const mesh = new THREE.Mesh(geometry, material);
    mesh.name = 'block-lod-tile'; mesh.castShadow = mesh.receiveShadow = true;
    owner.add(mesh); merged.push(mesh);
  }
  return merged;
}

/** Cull complete placements against their own expanded bounds, never their container square. */
export function updateBuildingDistances(root: THREE.Object3D, x: number, z: number,
  visibility: PlacementVisibility = placementVisibility, fades: LodFades = lodFades, y = NaN) {
  // Swap distances are 3D when the camera height is known, so a high flier sees block shells under it.
  const lift = (min: number, max: number) => Number.isFinite(y) ? Math.max(0, y - max, min - y) : 0;
  // Only a model that is actually in the scene may occlude or be culled by the visibility worker.
  const shown = root.visible && root.parent !== null;
  // GTA-style parent swap per block: every child of a block switches together, with hysteresis. The LOD shell
  // dissolves (render/lodFade.ts) on top of the opaque HD children in both directions, so neither pops.
  const blocks = root.userData.blockLods as Map<string, BlockState> | undefined;
  if (blocks) {
    let merged = true;
    for (const state of blocks.values()) {
      const { min, max } = state.bounds;
      const d = Math.hypot(Math.max(min[0] - x, 0, x - max[0]), Math.max(min[2] - z, 0, z - max[2]), lift(min[1], max[1]));
      const near = d <= state.hdDistance + (state.near ? SWAP_BAND : -SWAP_BAND);
      if (!state.settled) { state.settled = true; state.near = state.hd = near; }
      else if (near !== state.near) {
        state.near = near;
        if (!shown) { fades.cancel(state.lod); state.hd = near; }
        else if (near) { state.hd = true; fades.start(state.lod, 1, 0); }
        else fades.start(state.lod, 0, 1, () => { state.hd = state.near; });
      }
      merged &&= !state.near && !state.hd && !fades.has(state.lod);
    }
    // All far and settled: the merged tile parent alone; otherwise each far or dissolving block's own parent.
    for (const state of blocks.values()) state.lod.visible = !merged && (!state.near || fades.has(state.lod));
    for (const mesh of root.userData.blockLodTile as THREE.Object3D[] ?? []) mesh.visible = merged;
  }
  for (const child of root.children) {
    if (!child.userData.archetype) continue;
    const block = blocks?.get(child.userData.block as string);
    const bounds = child.userData.modelBounds as BuildingModel['bounds'];
    const dx = Math.max(child.position.x + bounds.min[0] - x, 0, x - child.position.x - bounds.max[0]);
    const dz = Math.max(child.position.z + bounds.min[2] - z, 0, z - child.position.z - bounds.max[2]);
    // With an LOD parent the block swap alone decides: a small child's own view distance (as low as 282 m) must
    // not open a hole inside the swap band before its parent appears.
    child.visible = block ? block.hd : dx * dx + dz * dz <= child.userData.viewDistance ** 2;
    visibility.setActive(child.userData.visibilitySlot as number | undefined, shown && child.visible);
  }
  updateStaticBatchVisibility(root);
}
