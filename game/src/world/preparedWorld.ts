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
import { readPreparedScene, buildPreparedModel, loadPreparedTextures, disposePreparedTextures, closePreparedImages, type PreparedScene } from './preparedScene';
import { GroundIndex } from './groundIndex';
import { decoderReady, readColSync } from '../physics/colTile';
import type { CoarseHost, Entry } from './coarseContracts';
import { StreamStats } from './streamStats';
import type { RoadGraphJson } from '../gameplay/roadGraph';
import { prepareLighting, type PreparedLighting } from '../render/lights/preparedLighting';

export interface PreparedWorldHost extends CoarseHost {
  prepareAsset?: (root: THREE.Object3D, recipe: 'building' | 'vegetation' | 'context') => void | Promise<void>;
  releaseAsset?: (root: THREE.Object3D, recipe: 'building' | 'vegetation' | 'context') => void;
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

/** A complete compiled release, installed before gameplay starts. This bounded pilot has no background generator. */
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
  private assets: { root: THREE.Object3D; recipe: 'building' | 'vegetation' | 'context' }[] = [];
  private ground = new GroundIndex();
  private top = 100;
  private bottom = -100;
  private constructor(private host: PreparedWorldHost, readonly data: PreparedScene, private url: string, private model: THREE.Group, private nativeTextures: Set<THREE.Texture>) { this.lighting = prepareLighting(data); }

  static async start(host: PreparedWorldHost): Promise<PreparedWorld> {
    const page = host.pageUrl ?? location.href;
    const url = new URL(`${import.meta.env.BASE_URL}scenes/office.scene.json`, page).href;
    const fetcher = host.fetch ?? fetch;
    const response = await fetcher(url, { signal: AbortSignal.timeout(15000) });
    if (!response.ok) throw new Error(`Prepared scene: HTTP ${response.status}`);
    const data = readPreparedScene(await response.json());
    if (data.origin.some((n, i) => n !== host.manifest.origin_en[i])) throw new Error('Prepared scene origin differs from the active world');
    const cells = await Promise.all(Object.entries(data.collision).map(async ([id, entry]) => {
      const cell = await fetcher(new URL(entry.url, url), { signal: AbortSignal.timeout(15000) });
      if (!cell.ok) throw new Error(`Prepared collision ${id}: HTTP ${cell.status}`);
      const buffer = await cell.arrayBuffer();
      if (buffer.byteLength !== entry.bytes) throw new Error(`Prepared collision ${id}: wrong byte count`);
      const digest = Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', buffer)), b => b.toString(16).padStart(2, '0')).join('');
      if (digest !== entry.sha256) throw new Error(`Prepared collision ${id}: hash mismatch`);
      await host.checkCollision(buffer.slice(0));
      return { id, entry: { ...entry, url: new URL(entry.url, url).href }, buffer };
    }));
    const textures = await loadPreparedTextures(data, url, fetcher, undefined,
      bytes => new Promise((resolve, reject) => sharedKtx(host.renderer).parse(bytes, resolve, reject)));
    let model: THREE.Group;
    try { model = buildPreparedModel(data, textures); }
    catch (error) { disposePreparedTextures(textures.values()); throw error; }
    const world = new PreparedWorld(host, data, url, model, new Set(textures.values()));
    try {
      const loader = data.assets.length && !host.loadModel ? new GLTFLoader().register(() => textureUploads(host.renderer).plugin())
        .setKTX2Loader(sharedKtx(host.renderer)).setMeshoptDecoder(MeshoptDecoder) : null;
      if (loader) MeshoptDecoder.useWorkers(2);
      for (let i = 0; i < data.assets.length; i += 4) {
        const results = await Promise.allSettled(data.assets.slice(i, i + 4).map(async asset => {
          const assetUrl = new URL(asset.url, url).href;
          const response = await fetcher(assetUrl, { signal: AbortSignal.timeout(15000) });
          if (!response.ok) throw new Error(`Prepared asset ${asset.id}: HTTP ${response.status}`);
          const bytes = await response.arrayBuffer();
          const digest = Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', bytes)), b => b.toString(16).padStart(2, '0')).join('');
          if (digest !== asset.sha256) throw new Error(`Prepared asset ${asset.id}: hash mismatch`);
          const root = host.loadModel ? await host.loadModel(assetUrl) : (await loadGlb(loader!, assetUrl, undefined, bytes)).scene;
          world.assets.push({ root, recipe: asset.recipe });
          root.name = `asset:${asset.id}`; root.position.fromArray(asset.position);
          root.userData.entityId = asset.entityId; root.userData.layer = asset.recipe === 'building' ? 'buildings' : asset.recipe === 'vegetation' ? 'vegetation' : 'structures';
          root.traverse(o => { if (o instanceof THREE.Mesh) o.castShadow = o.receiveShadow = true; });
          if (asset.recipe === 'context') {
            // Context retains the producer's explicit scenery layers. Their facade and canopy recipes
            // are different from near building materials; raw GLB pigment is only their input.
            root.traverse(o => { o.userData.scenery = true; });
            styleScenery(root);
          }
          model.add(root);
        }));
        const error = results.find(result => result.status === 'rejected');
        if (error?.status === 'rejected') throw error.reason;
      }
      await host.style?.(model);
      for (const asset of world.assets) await host.prepareAsset?.(asset.root, asset.recipe);
      await host.prepareGraphics?.(model);
      const id = `t0_${Math.floor(data.spawn.position[0] / 250)}_${Math.floor(data.spawn.position[2] / 250)}`;
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
    const release = () => {
      const textures = new Set(this.nativeTextures), geometries = new Set<THREE.BufferGeometry>(), materials = new Set<THREE.Material>();
      this.model.traverse(o => {
        if (!(o instanceof THREE.Mesh)) return;
        disposeDraw(o); geometries.add(o.geometry);
        for (const m of Array.isArray(o.material) ? o.material : [o.material]) {
          materials.add(m);
          for (const value of Object.values(m)) if (value instanceof THREE.Texture) textures.add(value);
        }
      });
      for (const geometry of geometries) releaseGeometry(geometry);
      for (const material of materials) material.dispose();
      // GLTF roots own shared KTX leases. Releasing a scene must not dispose another live owner's texture.
      for (const asset of this.assets) releaseModelTextures(asset.root, new Set());
      releaseModelTextures(this.model, textures);
      closePreparedImages(this.nativeTextures);
      this.assets.length = 0; this.nativeTextures.clear();
    };
    if (this.host.retire) this.host.retire(release); else release();
  }
}
