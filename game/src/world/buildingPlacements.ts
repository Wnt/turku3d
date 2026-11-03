import { updateStaticBatchVisibility } from './staticBatch';
import * as THREE from 'three/webgpu';
import { mergeGeometries } from 'three/addons/utils/BufferGeometryUtils.js';
import { shareModelTextures } from './sharedAssets';
import { retainGeometry } from './modelGeometry';
import { PreparationBudget, streamingBudget } from '../render/preparationBudget';
import { placementVisibility, type PlacementVisibility } from './placementVisibility';
import { lodFades, stableMaterial, type LodFades } from '../render/lodFade';
import { StaticGroup } from './frozenTransforms';
import { streamTrace } from '../telemetry/streamTrace';

type Vec3 = [number, number, number];
/** Bench lab `hd=`: one HD-to-block-shell switch distance (m) for every block; null keeps each block's own. */
export const BLOCK_LOD = { hdDistance: null as number | null };
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

interface Resident { users: number; promise: Promise<THREE.Object3D>; trace?: ReadTrace;
  /** Tiles (or prefetch tokens) holding a lease; the resolved template; whether its render arrays were released. */
  owners: Set<object>; root?: THREE.Object3D; released?: boolean }
/** One model read for install traces: slot wait, then fetch/decode/parse, of which the texture upload wait. */
interface ReadTrace { queueMs: number; loadMs: number; textureMs: number; textures: number }
interface Lease { root: THREE.Object3D; release: () => void; trace?: ReadTrace }

/** Decode each content model once while resident. Tile cancellation releases every successful lease. */
export class BuildingModelCache {
  private entries = new Map<string, Resident>();
  private active = 0;
  /** Waiting reads, the nearest tile's first (`priority`: its distance in metres). */
  private queue: { priority: number; start: () => void }[] = [];
  /** Concurrent model reads: fetch, GLB decode and meshopt workers overlap. */
  readonly reads = 6;
  constructor(private load: (url: string) => Promise<THREE.Object3D>,
    private dispose: (root: THREE.Object3D) => void, private budget: PreparationBudget = streamingBudget(),
    private visibility: PlacementVisibility = placementVisibility,
    private styleLod: (root: THREE.Object3D, owner: THREE.Object3D) => void = () => {}) {}

  private async read(url: string, priority: number, trace: ReadTrace | undefined, wanted: () => boolean) {
    const queued = trace ? performance.now() : 0, waits = this.active >= this.reads;
    if (waits) await new Promise<void>(start => {
      const at = this.queue.findIndex(waiting => waiting.priority > priority);
      this.queue.splice(at < 0 ? this.queue.length : at, 0, { priority, start });
    });
    else this.active++;
    try {
      // Every lease was released while the read queued (a cancelled install): skip the fetch and decode.
      if (waits && !wanted()) throw new Error('building model read cancelled');
      // Reads start inside the frame's streaming window, so parallel model parses do not pile into one frame gap.
      let pause = this.budget.checkpoint();
      while (pause) { await pause; pause = this.budget.checkpoint(); }
      const loading = trace ? performance.now() : 0;
      if (trace) trace.queueMs = loading - queued;
      const root = await this.load(url);
      if (trace) {
        trace.loadMs = performance.now() - loading;
        const upload = root.userData.textureUpload as { ms: number; textures: number } | undefined;
        trace.textureMs = upload?.ms ?? 0; trace.textures = upload?.textures ?? 0;
      }
      const geometries = new Set<THREE.BufferGeometry>();
      root.traverse(o => { if ((o as THREE.Mesh).isMesh) geometries.add((o as THREE.Mesh).geometry); });
      for (const geometry of geometries) retainGeometry(geometry);
      return root;
    } finally {
      const next = this.queue.shift();
      if (next) next.start(); else this.active--;
    }
  }

  private async acquire(model: BuildingModel, base: URL, priority = Infinity, owner: object = {}): Promise<Lease> {
    // Hashes are reusable across generation URL prefixes, but never across content hosts.
    const url = new URL(model.uri, base), key = `${url.origin}/${model.id}`;
    let entry = this.entries.get(key);
    // A template whose render arrays were released cannot be cloned and merged again: a new tile reads it afresh,
    // while the tiles already drawing it keep the old one until they release it.
    if (!entry || entry.released) {
      const trace = streamTrace.on ? { queueMs: 0, loadMs: 0, textureMs: 0, textures: 0 } : undefined;
      const created: Resident = { users: 0, promise: Promise.resolve(null as unknown as THREE.Object3D), trace, owners: new Set() };
      created.promise = this.read(url.href, priority, trace, () => created.users > 0 && this.entries.get(key) === created);
      void created.promise.then(root => { created.root = root; }, () => {});
      entry = created; this.entries.set(key, entry);
    }
    entry.users++; entry.owners.add(owner);
    let released = false;
    const release = () => {
      if (released) return;
      released = true;
      entry.owners.delete(owner);
      if (--entry.users) return;
      if (this.entries.get(key) === entry) this.entries.delete(key);
      void entry.promise.then(root => this.dispose(root), () => {});
    };
    try { return { root: await entry.promise, release, trace: entry.trace }; }
    catch (error) { release(); throw error; }
  }

  private committed = new WeakSet<object>();
  /** The owner's tile is drawn: templates only committed tiles use may release their render arrays. */
  markCommitted(owner: THREE.Object3D) { this.committed.add(owner); }
  /** Release templates every holder of which is a committed tile, once each (`release` gets the template and the
   *  tiles holding it; it decides per geometry). */
  releaseCommitted(release: (root: THREE.Object3D, owners: object[]) => number) {
    let bytes = 0;
    for (const entry of this.entries.values()) {
      if (entry.released || !entry.root || !entry.owners.size || [...entry.owners].some(owner => !this.committed.has(owner))) continue;
      entry.released = true;
      bytes += release(entry.root, [...entry.owners]);
    }
    return bytes;
  }

  /** Resident model templates (memory census). */
  async templates(): Promise<THREE.Object3D[]> {
    const roots = await Promise.allSettled([...this.entries.values()].map(entry => entry.promise));
    return roots.flatMap(result => result.status === 'fulfilled' ? [result.value] : []);
  }

  /** Start reading a tile's models before its own GLB has loaded; release once `attach` holds them. */
  prefetch(data: BuildingPlacements, base: URL, priority = Infinity): () => void {
    validateBuildingPlacements(data);
    const descriptors = new Map(data.models.map(model => [model.id, model]));
    const leases = this.needed(data).map(id => this.acquire(descriptors.get(id)!, base, priority).catch(() => null));
    return () => { for (const lease of leases) void lease.then(held => held?.release()); };
  }

  private needed(data: BuildingPlacements) {
    return [...new Set([...data.placements.map(p => p.model), ...(data.blocks ?? []).flatMap(b => b.lod ? [b.lod] : [])])];
  }

  async attach(owner: THREE.Object3D, data: BuildingPlacements, base: URL, priority = Infinity): Promise<() => void> {
    validateBuildingPlacements(data);
    const descriptors = new Map(data.models.map(model => [model.id, model]));
    const needed = this.needed(data);
    const traced = streamTrace.on, started = traced ? performance.now() : 0;
    const results = await Promise.allSettled(needed.map(id => this.acquire(descriptors.get(id)!, base, priority, owner)));
    const leases = results.flatMap(result => result.status === 'fulfilled' ? [result.value] : []);
    const failure = results.find(result => result.status === 'rejected');
    if (failure?.status === 'rejected') { for (const lease of leases) lease.release(); throw failure.reason; }
    const templates = new Map(needed.map((id, i) => [id, leases[i].root]));
    const traceId = owner.userData.traceId as string | undefined;
    let cloning = traced ? performance.now() : 0, cloneCpu = 0;
    if (traced) {
      // Model reads (slot wait, fetch + decode + glTF parse, of which budgeted texture uploads), summed over this tile's
      // models; a model another tile already holds counts as shared.
      const reads = leases.map(lease => lease.trace).filter((t): t is ReadTrace => !!t);
      const sum = (pick: (t: ReadTrace) => number) => reads.reduce((total, t) => total + pick(t), 0);
      streamTrace.event('tile.buildings.read', traceId, { ms: cloning - started, models: needed.length,
        queueMs: sum(t => t.queueMs), loadMs: sum(t => t.loadMs), textureMs: sum(t => t.textureMs), textures: sum(t => t.textures),
        longestLoadMs: Math.max(0, ...reads.map(t => t.loadMs)) });
    }
    const geometries = new Set<THREE.BufferGeometry>();
    const materials = new Map<THREE.Material, THREE.Material>(), textures = new Map<THREE.Texture, THREE.Texture>();
    const slots: number[] = [];
    for (const placement of data.placements) {
      // Network admission does not bound the clone burst after all model promises resolve.
      let pause = this.budget.checkpoint();
      // Other tile attachments can spend this shared budget when the same yield resolves.
      while (pause) { await pause; pause = this.budget.checkpoint(); }
      const cloneStart = traced ? performance.now() : 0;
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
      if (traced) cloneCpu += performance.now() - cloneStart;
    }
    if (traced) {
      const now = performance.now();
      streamTrace.event('tile.buildings.clone', traceId, { ms: now - cloning, cpu: cloneCpu, placements: data.placements.length });
      cloning = now;
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
      states.set(block.id, { lod: wrapper, bounds: block.bounds, hdDistance: BLOCK_LOD.hdDistance ?? block.hdDistance, near: true, hd: true, settled: false });
    }
    if (states.size) {
      owner.userData.blockLods = states;
      owner.userData.blockLodTile = streamTrace.time('lod', () => mergeBlockLods(owner, [...states.values()].map(s => s.lod)));
    }
    if (traced) streamTrace.event('tile.buildings.lod', traceId, { ms: performance.now() - cloning, blocks: states.size });
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
    const material = stableMaterial(mesh.material);
    groups.set(material, [...groups.get(material) ?? [], geometry]);
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

/** The block LOD shells of a tile model, whose dissolve twins its preparation compiles (tier 0: 280 m swaps). */
export function blockLodRoots(model: THREE.Object3D): THREE.Object3D[] {
  const blocks = model.userData.blockLods as Map<string, BlockState> | undefined;
  return blocks ? [...blocks.values()].map(state => state.lod) : [];
}

/** Cull complete placements against their own expanded bounds, never their container square. */
export function updateBuildingDistances(root: THREE.Object3D, x: number, z: number,
  visibility: PlacementVisibility = placementVisibility, fades: LodFades = lodFades, y = NaN) {
  // Swap distances are 3D when the camera height is known, so a high flier sees block shells under it.
  const height = Number.isFinite(y);
  // Only a model that is actually in the scene may occlude or be culled by the visibility worker.
  const shown = root.visible && root.parent !== null;
  // GTA-style parent swap per block: every child of a block switches together, with hysteresis. The LOD shell
  // dissolves (render/lodFade.ts) on top of the opaque HD children in both directions, so neither pops.
  const blocks = root.userData.blockLods as Map<string, BlockState> | undefined;
  if (blocks) {
    let merged = true;
    for (const state of blocks.values()) {
      const { min, max } = state.bounds;
      const d = Math.hypot(Math.max(min[0] - x, 0, x - max[0]), Math.max(min[2] - z, 0, z - max[2]),
        height ? Math.max(0, y - max[1], min[1] - y) : 0);
      const near = d <= state.hdDistance + (state.near ? SWAP_BAND : -SWAP_BAND);
      if (!state.settled) { state.settled = true; state.near = state.hd = near; }
      else if (near !== state.near) {
        state.near = near;
        streamTrace.event('band.block', root.userData.traceId as string | undefined, { block: state.lod.userData.blockLod, to: near ? 'hd' : 'lod',
          d3: Math.round(d), fade: shown });
        // A shell whose dissolve twin is not compiled yet swaps at once rather than building a shader mid-frame.
        if (!shown) { fades.cancel(state.lod); state.hd = near; }
        else if (near) { state.hd = true; fades.start(state.lod, 1, 0); }
        else if (!fades.start(state.lod, 0, 1, () => { state.hd = state.near; })) state.hd = false;
      }
      merged &&= !state.near && !state.hd && !fades.has(state.lod);
    }
    // All far and settled: the merged tile parent alone; otherwise each far or dissolving block's own parent.
    for (const state of blocks.values()) state.lod.visible = !merged && (!state.near || fades.has(state.lod));
    const tile = root.userData.blockLodTile as THREE.Object3D[] | undefined;
    if (tile) for (const mesh of tile) mesh.visible = merged;
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
