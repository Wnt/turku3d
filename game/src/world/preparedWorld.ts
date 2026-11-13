import * as THREE from 'three/webgpu';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { MeshoptDecoder } from 'three/addons/libs/meshopt_decoder.module.js';
import { loadGlb } from './loadGlb';
import { sharedKtx } from './loadingKtx';
import { textureUploads } from '../render/textureUploads';
import { modelGpuBytes, rendererResidency } from './gpuBytes';
import { disposeDraw } from '../render/sharedMaterials';
import { styleScenery } from '../render/sceneryMaterial';
import { releaseGeometry } from './modelGeometry';
import { releaseModelTextures } from './sharedAssets';
import { buildPreparedModel, loadPreparedTextures, disposePreparedTextures, closePreparedImages, sceneTextureKeys, type PreparedScene } from './preparedScene';
import { appearanceChanges } from './preparedAppearance';
import { loadPreparedRegion, type PreparedRegion } from './preparedRegions';
import { GroundIndex } from './groundIndex';
import { decoderReady, readColSync } from '../physics/colTile';
import type { CoarseHost, Entry } from './coarseContracts';
import { StreamStats } from './streamStats';
import type { RoadGraphJson } from '../gameplay/roadGraph';
import { prepareLighting, type PreparedLighting } from '../render/lights/preparedLighting';

export interface PreparedWorldHost extends CoarseHost {
  prepareAsset?: (root: THREE.Object3D, recipe: 'building' | 'vegetation' | 'context') => void | Promise<void>;
  releaseAsset?: (root: THREE.Object3D, recipe: 'building' | 'vegetation' | 'context') => void;
  /** A session that moves through the world (play, look-around): the camera whose distance chooses an entity's level
   *  of detail and whose view decides what is in the scene graph each frame (world/location/locationWorld.ts), and
   *  the distance in metres to which trees keep their full models. Stills leave it out: every entity draws the
   *  level of its role, whole. */
  view?: { camera: THREE.PerspectiveCamera; treeNear: () => number };
}

/** Prepared context is part of this release, already resident with its pilot. No scenery service or second world is consulted. */
class PreparedContext {
  radius = 0;
  heading: number | null = null;
  progress = { ready: true, loaded: 0, total: 0, pending: 0, errors: 0, missing: 0, cataloguePending: false };
  setRadius(_radius: number, _view?: unknown) {}
  follow(_camera: THREE.Camera) {}
  setView(_view: unknown, _now: number) {}
}

type AssetRow = PreparedScene['assets'][number];
interface LoadedAsset { row: AssetRow; root: THREE.Object3D; recipe: AssetRow['recipe'] }
const digest = async (bytes: ArrayBuffer) =>
  Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', bytes)), b => b.toString(16).padStart(2, '0')).join('');
const sameAsset = (a: AssetRow, b: AssetRow) => a.id === b.id && a.entityId === b.entityId && a.sha256 === b.sha256
  && a.recipe === b.recipe && a.position.every((n, i) => n === b.position[i]);

/** Disposes a released visual subtree's draws, geometries and materials, and its textures except `keep`. */
function disposeVisual(root: THREE.Object3D, assetRoots: Iterable<THREE.Object3D>, native: Iterable<THREE.Texture>, keep: ReadonlySet<THREE.Texture> = new Set()) {
  const textures = new Set([...native].filter(t => !keep.has(t))), geometries = new Set<THREE.BufferGeometry>(), materials = new Set<THREE.Material>();
  root.traverse(o => {
    if (!(o instanceof THREE.Mesh)) return;
    disposeDraw(o); geometries.add(o.geometry);
    for (const m of Array.isArray(o.material) ? o.material : [o.material]) {
      materials.add(m);
      for (const value of Object.values(m)) if (value instanceof THREE.Texture && !keep.has(value)) textures.add(value);
    }
  });
  for (const geometry of geometries) releaseGeometry(geometry);
  for (const material of materials) material.dispose();
  // GLTF roots own shared KTX leases. Releasing a scene must not dispose another live owner's texture.
  for (const asset of assetRoots) releaseModelTextures(asset, new Set());
  releaseModelTextures(root, textures);
  closePreparedImages(textures);
}

/** A complete compiled release, installed before gameplay starts. This bounded pilot has no background generator.
 *  Appearance (materials, textures, building assets) can be replaced in place by applyAppearance. */
export class PreparedWorld {
  readonly entries = new Map<string, Entry>();
  readonly screenshotScenery = new PreparedContext();
  readonly stats = new StreamStats();
  readonly lighting: PreparedLighting;
  onMapChange: (() => void) | null = null;
  retainedStarts: readonly { x: number; z: number }[] = [];
  scheduling = true;
  readonly visualPass = 'full';
  readonly comparisonStatus = 'Prepared scene';
  readonly releasedBytes = 0;
  status = '';
  private disposed = false;
  private assets: LoadedAsset[] = [];
  private loader: GLTFLoader | null = null;
  private ground = new GroundIndex();
  private top = 100;
  private bottom = -100;
  private updating: Promise<unknown> = Promise.resolve();
  /** The published document (startup or the last reloadAppearance): restoreAppearance returns to it. */
  private published: PreparedScene;
  /** `data` is the scene drawn now: an appearance update replaces it (geometry and collision never change). */
  private constructor(private host: PreparedWorldHost, public data: PreparedScene, readonly region: PreparedRegion, readonly sessionScope: string | null,
    private url: string, private model: THREE.Group, private textures: Map<string, THREE.Texture>) {
    this.lighting = prepareLighting(data); this.published = data;
  }

  static async start(host: PreparedWorldHost): Promise<PreparedWorld> {
    const page = host.pageUrl ?? location.href;
    const fetcher = host.fetch ?? fetch;
    const { region, url, data, sessionScope } = await loadPreparedRegion(page, fetcher);
    if (data.origin.some((n, i) => n !== host.manifest.origin_en[i])) throw new Error('Prepared scene origin differs from the active world');
    const cells = await Promise.all(Object.entries(data.collision).map(async ([id, entry]) => {
      const cell = await fetcher(new URL(entry.url, url), { signal: AbortSignal.timeout(15000) });
      if (!cell.ok) throw new Error(`Prepared collision ${id}: HTTP ${cell.status}`);
      const buffer = await cell.arrayBuffer();
      if (buffer.byteLength !== entry.bytes) throw new Error(`Prepared collision ${id}: wrong byte count`);
      if (await digest(buffer) !== entry.sha256) throw new Error(`Prepared collision ${id}: hash mismatch`);
      await host.checkCollision(buffer.slice(0));
      return { id, entry: { ...entry, url: new URL(entry.url, url).href }, buffer };
    }));
    const textures = await loadPreparedTextures(data, url, fetcher, undefined,
      bytes => new Promise((resolve, reject) => sharedKtx(host.renderer).parse(bytes, resolve, reject)));
    let model: THREE.Group;
    try { model = buildPreparedModel(data, textures); }
    catch (error) { disposePreparedTextures(textures.values()); throw error; }
    const world = new PreparedWorld(host, data, region, sessionScope, url, model, textures);
    try {
      // Loaded roots join the model at once, so a failure part-way releases them with it (leaveCity).
      await world.loadAssets(data.assets, world.assets, model);
      await host.style?.(model);
      for (const asset of world.assets) await host.prepareAsset?.(asset.root, asset.recipe);
      await host.prepareGraphics?.(model);
      const id = world.entryId();
      const collision = new Map(cells.map(c => [c.id, c.entry]));
      const h = [Math.min(...cells.map(c => c.entry.h[0])), Math.max(...cells.map(c => c.entry.h[1]))] as [number, number];
      world.entries.set(id, { state: 'draft', model, collision, nextPoll: Infinity, pass: 'full', visualHash: data.release, h });
      await decoderReady;
      world.top = h[1] + 10;
      world.bottom = h[0] - 1;
      for (const c of cells) world.ground.addLazy(c.id, c.buffer, readColSync);
      for (const c of cells) { host.manifest.collision[c.id] = c.entry; host.retainCollision?.(c.id, c.entry, c.buffer); }
      host.manifest.spawn = data.spawn; host.manifest.code = data.release;
      host.scene.add(model); host.trackModel?.(model, id);
      world.status = `${data.id} · ${data.meshes.length} prepared meshes`;
      return world;
    } catch (error) { world.leaveCity(); throw error; }
  }

  private entryId() {
    return `t0_${Math.floor(this.data.spawn.position[0] / 250)}_${Math.floor(this.data.spawn.position[2] / 250)}`;
  }

  /** Fetches, verifies and decodes asset models in bounded batches; each root is pushed to `into` (and added to
   *  `parent`) as soon as it loads, so the caller can release a partial result. */
  private async loadAssets(rows: readonly AssetRow[], into: LoadedAsset[], parent: THREE.Object3D) {
    const host = this.host, fetcher = host.fetch ?? fetch;
    if (rows.length && !host.loadModel && !this.loader) {
      this.loader = new GLTFLoader().register(() => textureUploads(host.renderer).plugin())
        .setKTX2Loader(sharedKtx(host.renderer)).setMeshoptDecoder(MeshoptDecoder);
      MeshoptDecoder.useWorkers(2);
    }
    for (let i = 0; i < rows.length; i += 4) {
      const results = await Promise.allSettled(rows.slice(i, i + 4).map(async asset => {
        const assetUrl = new URL(asset.url, this.url).href;
        const response = await fetcher(assetUrl, { signal: AbortSignal.timeout(15000) });
        if (!response.ok) throw new Error(`Prepared asset ${asset.id}: HTTP ${response.status}`);
        const bytes = await response.arrayBuffer();
        if (await digest(bytes) !== asset.sha256) throw new Error(`Prepared asset ${asset.id}: hash mismatch`);
        const root = host.loadModel ? await host.loadModel(assetUrl) : (await loadGlb(this.loader!, assetUrl, undefined, bytes)).scene;
        into.push({ row: asset, root, recipe: asset.recipe });
        root.name = `asset:${asset.id}`; root.position.fromArray(asset.position);
        root.userData.entityId = asset.entityId; root.userData.layer = asset.recipe === 'building' ? 'buildings' : asset.recipe === 'vegetation' ? 'vegetation' : 'structures';
        root.traverse(o => { if (o instanceof THREE.Mesh) o.castShadow = o.receiveShadow = true; });
        if (asset.recipe === 'context') {
          // Context retains the producer's explicit scenery layers. Their facade and canopy recipes
          // are different from near building materials; raw GLB pigment is only their input.
          root.traverse(o => { o.userData.scenery = true; });
          styleScenery(root);
        }
        parent.add(root);
      }));
      const error = results.find(result => result.status === 'rejected');
      if (error?.status === 'rejected') throw error.reason;
    }
  }

  /** Re-reads this region's published scene (after `python -m turku3d.scene.appearance`) and applies it. */
  async reloadAppearance() {
    const { region, data } = await loadPreparedRegion(this.host.pageUrl ?? location.href, this.host.fetch ?? fetch);
    if (region.id !== this.region.id) throw new Error(`Published region ${region.id} is not the loaded ${this.region.id}`);
    const report = await this.applyAppearance(data);
    this.published = data;
    return report;
  }

  /** Returns to the published appearance after live patches, without fetching anything. */
  restoreAppearance() { return this.applyAppearance(this.published); }

  /**
   * Replaces materials, textures and asset models without touching physics, collision cells, ground queries,
   * lighting data or the camera. Rejects any other difference (appearanceChanges). Unchanged asset roots and
   * textures are reused, not fetched or decoded again; the prepared material batches are rebuilt from `next`.
   * New inputs load before the drawn model is released, so a failed download leaves the old appearance in place.
   */
  applyAppearance(next: PreparedScene) {
    const run = this.updating.then(() => this.replaceAppearance(next));
    this.updating = run.catch(() => {});
    return run;
  }

  private async replaceAppearance(next: PreparedScene) {
    if (this.disposed) throw new Error('The prepared scene has been released');
    const started = performance.now(), host = this.host;
    const changes = appearanceChanges(this.data, next);
    if (!changes.materials.length && !changes.assets.length && !changes.uvMeshes && next.release === this.data.release) {
      // Nothing visible changed (a reload of the scene already drawn, a restore without patches): keep every GPU object.
      this.data = next;
      return { ...changes, release: next.release, reusedAssets: this.assets.length, loadedAssets: 0, loadedTextures: 0, loadMs: 0,
        totalMs: Math.round(performance.now() - started) };
    }
    const fresh = await loadPreparedTextures(next, this.url, host.fetch ?? fetch, undefined,
      bytes => new Promise((resolve, reject) => sharedKtx(host.renderer).parse(bytes, resolve, reject)), this.textures);
    const kept: LoadedAsset[] = [], loaded: LoadedAsset[] = [], staging = new THREE.Group();
    for (const row of next.assets) {
      const old = this.assets.find(asset => sameAsset(asset.row, row));
      if (old) kept.push({ ...old, row });
    }
    const textures = new Map([...this.textures, ...fresh]), used = sceneTextureKeys(next);
    let model: THREE.Group;
    try {
      await this.loadAssets(next.assets.filter(row => !kept.some(asset => asset.row === row)), loaded, staging);
      model = buildPreparedModel(next, textures);
    } catch (error) {
      disposeVisual(staging, loaded.map(asset => asset.root), fresh.values());
      throw error;
    }
    const loadedMs = performance.now() - started;
    // Release the drawn appearance: styling, static batches and preparation records return their source meshes.
    const id = this.entryId(), old = this.model, entry = this.entries.get(id)!;
    host.untrackModel?.(old, id);
    for (const asset of this.assets) host.releaseAsset?.(asset.root, asset.recipe);
    host.unstyle?.(old);
    old.removeFromParent();
    const dropped = this.assets.filter(asset => !kept.some(k => k.root === asset.root));
    for (const asset of [...kept, ...loaded]) model.add(asset.root);
    this.assets = [...kept, ...loaded];
    this.model = model; this.data = next;
    const keep = new Set([...textures].filter(([key]) => used.has(key)).map(([, texture]) => texture));
    const unused = [...textures].filter(([key]) => !used.has(key)).map(([, texture]) => texture);
    this.textures = new Map([...textures].filter(([key]) => used.has(key)));
    const release = () => disposeVisual(old, dropped.map(asset => asset.root), unused, keep);
    if (host.retire) host.retire(release); else release();
    // Same order as start(): styling, asset recipes, then GPU preparation before the model is drawn.
    await host.style?.(model);
    for (const asset of this.assets) await host.prepareAsset?.(asset.root, asset.recipe);
    await host.prepareGraphics?.(model);
    entry.model = model; entry.visualHash = next.release;
    host.manifest.code = next.release;
    host.scene.add(model); host.trackModel?.(model, id);
    this.status = `${next.id} · ${next.meshes.length} prepared meshes · appearance ${next.release.slice(0, 12)}`;
    return { ...changes, release: next.release, reusedAssets: kept.length, loadedAssets: loaded.length,
      loadedTextures: fresh.size, loadMs: Math.round(loadedMs), totalMs: Math.round(performance.now() - started) };
  }

  preload(_points: readonly { x: number; z: number }[], _radius: number, _boot = false, _view?: unknown) { return () => {}; }
  update(_x: number, _z: number, _now?: number, _motion?: { x: number; z: number }, _scenery = true, _y?: number) {}
  setRouteHint(_points: readonly { x: number; z: number }[] | null) {}
  setVisualPass(_pass: 'auto' | 'first' | 'full') {}
  contains(x: number, z: number) {
    const b = this.data.bounds;
    return !this.disposed && x >= b[0] && x < b[2] && z >= b[1] && z < b[3];
  }
  progressAt(x: number, z: number) {
    const covered = this.contains(x, z);
    return { loaded: covered ? 1 : 0, total: covered ? 1 : 0, errors: covered ? [] : ['Outside the prepared scene'] };
  }
  resolvedAt(x: number, z: number) { return this.contains(x, z); }
  streetsAt(x: number, z: number) { return Number.isFinite(this.groundBelow(x, z)); }
  detailedAt(x: number, z: number) { return this.contains(x, z); }
  collisionComing(_id: string) { return false; }
  loadedTiles() { return [...this.entries.keys()]; }
  drawn() { return this.loadedTiles(); }
  roadGraphs(): RoadGraphJson[] { return []; }
  manifestUrl(_id: string) { return this.url; }
  groundBelow(x: number, z: number, referenceY = this.top) {
    return this.contains(x, z) && Number.isFinite(referenceY) && referenceY >= this.bottom
      ? this.ground.ground(x, referenceY, z, referenceY - this.bottom) ?? NaN : NaN;
  }
  viewStatus(x: number, z: number) {
    const ready = this.contains(x, z);
    return { ready, near: ready ? [...this.entries].map(([id, e]) => ({ id, hash: e.visualHash, ready })) : [], scenery: this.screenshotScenery.progress };
  }
  streamSnapshot() { return { ...this.stats.snapshot(performance.now(), [], 0), previews: 0, outage: false }; }
  coverageSnapshot(x: number, z: number, r = 4) {
    const i0 = Math.floor(x / 250) - r, j0 = Math.floor(z / 250) - r, n = 2 * r + 1;
    let cells = '';
    for (let j = j0; j < j0 + n; j++) for (let i = i0; i < i0 + n; i++) cells += this.entries.has(`t0_${i}_${j}`) ? 'F' : '.';
    return { i0, j0, n, cells };
  }
  async benchInstall(_id: string, _stage: 'preview' | 'full', _x: number, _z: number) {
    return { state: 'draft', preview: false, full: !this.disposed, error: null, retryInMs: 0 };
  }
  memoryParts() {
    return { full: this.disposed ? [] : [this.model], cached: [], previews: [], previewFragments: 0, roads: 0,
      releasedBytes: 0, releases: 0, gpuResidency: { measuredMiB: Math.round(modelGpuBytes(this.model, rendererResidency(this.host.renderer)) / 2 ** 20), limitMiB: null, over: false },
      previewJsonBytes: () => 0, roadsJsonBytes: () => 0, fragmentCache: { bytes: 0, count: 0 }, buildingTemplates: async () => [] };
  }
  leaveCity() {
    if (this.disposed) return;
    this.disposed = true; this.model.removeFromParent();
    for (const [id, e] of this.entries) {
      this.host.untrackModel?.(this.model, id);
      for (const cell of e.collision.keys()) { delete this.host.manifest.collision[cell]; this.ground.remove(cell); this.host.unloadCollision(cell); }
    }
    this.entries.clear();
    for (const asset of this.assets) this.host.releaseAsset?.(asset.root, asset.recipe);
    this.host.unstyle?.(this.model);
    const model = this.model, roots = this.assets.map(asset => asset.root), textures = [...this.textures.values()];
    const release = () => { disposeVisual(model, roots, textures); this.assets.length = 0; this.textures.clear(); };
    if (this.host.retire) this.host.retire(release); else release();
  }
}
