// Ambient walkers (street-richness Phase 4): shared deterministic population (population.ts) on the resident tiles'
// FOOT network (network.ts), drawn locally around the camera (ring.ts) with baked vertex animation (visuals.ts).
import * as THREE from 'three/webgpu';
import type { RoadGraphJson } from '../../gameplay/roadGraph';
import type { QualityTier } from '../../render/quality';
import type { RayFilter, RayHit } from '../../physics/protocol';
import type { V3 } from '../../core/origin';
import { loadWalkerAsset, type WalkerAsset } from './asset';
import { GroundHeights } from './heights';
import { buildWalkNetwork, hash32, type WalkNetwork } from './network';
import { Population, type BirthClock, type Walker } from './population';
import { RING, Ring, type RingView } from './ring';
import { WalkerMeshes, type WalkerInstance } from './visuals';
import { humanAppearance } from './appearance';

export { walkersNear } from './population';
export { buildWalkNetwork } from './network';

/** Drawn walkers per quality tier (street-richness plan §7); `?walkers=N` overrides. Phones stay at zero. */
export const WALKER_CAPS: Record<QualityTier, number> = { mobile: 0, low: 10, medium: 60, high: 100 };
const MAX_WALKERS = 200;
export function walkerCap(quality: QualityTier, override: number | null): number {
  return Math.max(0, Math.min(MAX_WALKERS, override ?? WALKER_CAPS[quality]));
}

type Raycast = (origin: V3, dir: V3, maxToi: number, filter: RayFilter) => Promise<RayHit | null>;
interface WalkerOptions {
  cap: number;
  raycast: Raycast;
  /** Shared UTC milliseconds (the day clock synchronised to the multiplayer server). */
  now: () => number;
  /** The clock hour births sample (population.ts BirthClock); the shared cycle when absent. */
  birthHour?: BirthClock;
  /** Resident road graphs (CoarseTiles.roadGraphs). */
  graphs: () => readonly RoadGraphJson[];
}

const systems: Set<Walkers> = import.meta.hot?.data.systems ?? new Set<Walkers>();
/** Walkers drawn by this client right now (Phase 0 richness scorecard). */
export function liveWalkerCount(): number { let n = 0; for (const s of systems) n += s.count; return n; }

const SCAN_MS = 400, GRAPH_POLL_MS = 1000, RAYS_PER_FRAME = 24, YAW_RATE = 8, IDLE_SECONDS = 2.5;

export class Walkers {
  readonly group = new THREE.Group();
  private meshes: WalkerMeshes | null = null;
  private asset: WalkerAsset | null = null;
  private network: WalkNetwork | null = null;
  private population: Population | null = null;
  private graphs: readonly RoadGraphJson[] = [];
  private heights: GroundHeights;
  private ring = new Ring<Walker>();
  private wide: Walker[] = [];
  private lastScan = -Infinity;
  private lastGraphPoll = -Infinity;
  private request = 0;
  private assetRequest = 0;
  private disposed = false;
  private worker: Worker | null = null;
  private camera = new THREE.Vector3();
  private forward = new THREE.Vector3();
  count = 0;
  buildMs = 0;
  /** Last frame's update and last wide scan (every SCAN_MS), main thread. */
  updateMs = 0;
  scanMs = 0;

  constructor(parent: THREE.Object3D, private options: WalkerOptions) {
    this.group.name = 'walkers';
    parent.add(this.group);
    this.heights = new GroundHeights(options.raycast);
    systems.add(this);
  }

  async load(url: string) {
    if (this.options.cap <= 0 || this.disposed) return;
    const token = ++this.assetRequest;
    const asset = await loadWalkerAsset(url);
    if (this.disposed || token !== this.assetRequest) { asset.albedo?.dispose(); return; }
    const meshes = new WalkerMeshes(asset, this.options.cap + RING.margin);
    this.meshes?.dispose();
    this.asset = asset; this.meshes = meshes;
    this.group.add(meshes.group);
    this.ring.clear(); this.lastScan = -Infinity;
  }

  /** Rebuilds the network when the set of resident graphs changed (identity, not load order), in a worker when the
   *  page has one; the previous network keeps walking until the new one arrives. */
  private syncGraphs() {
    const graphs = this.options.graphs();
    if (graphs.length === this.graphs.length && graphs.every(g => this.graphs.includes(g))) return;
    this.graphs = [...graphs];
    const id = ++this.request;
    if (typeof Worker === 'undefined') { this.install(buildWalkNetwork(this.graphs), 0); return; }
    this.worker ??= this.startWorker();
    this.worker.postMessage({ id, parts: this.graphs });
  }

  private startWorker() {
    const worker = new Worker(new URL('./network.worker.ts', import.meta.url), { type: 'module', name: 'walker-network' });
    worker.onmessage = ({ data }: MessageEvent<{ id: number; network?: WalkNetwork; ms?: number; error?: string }>) => {
      if (data.error) console.warn(`[walkers] network: ${data.error}`);
      else if (data.id === this.request && data.network) this.install(data.network, data.ms ?? 0);
    };
    return worker;
  }

  private install(network: WalkNetwork, ms: number) {
    this.network = network;
    this.population = new Population(network, this.options.birthHour);
    this.heights.retain(new Set(network.chains.map(c => c.id)));
    this.buildMs = ms;
    this.lastScan = -Infinity;
  }

  /** Once per frame after the camera moved. */
  update(dt: number, camera: THREE.PerspectiveCamera, frameNow: number) {
    if (!this.meshes || !this.asset) return;
    const start = performance.now();
    if (frameNow - this.lastGraphPoll >= GRAPH_POLL_MS) { this.lastGraphPoll = frameNow; this.syncGraphs(); }
    const population = this.population;
    if (!population) { this.meshes.update([]); this.count = 0; return; }
    const utc = this.options.now();
    camera.getWorldPosition(this.camera);
    camera.getWorldDirection(this.forward);
    const flat = Math.hypot(this.forward.x, this.forward.z) || 1;
    const halfH = Math.atan(Math.tan(THREE.MathUtils.degToRad(camera.fov) / 2) * camera.aspect);
    const view: RingView = { x: this.camera.x, z: this.camera.z, fx: this.forward.x / flat, fz: this.forward.z / flat,
      cosHalf: Math.cos(Math.min(Math.PI, halfH + THREE.MathUtils.degToRad(12))) };
    if (frameNow - this.lastScan >= SCAN_MS) {
      this.lastScan = frameNow;
      const scan = performance.now();
      this.wide = population.near(view.x, view.z, RING.inView + RING.keep + 15, utc);
      this.scanMs = performance.now() - scan;
    }
    const candidates: Walker[] = [];
    for (const old of this.wide) {
      const walker = population.at(old, utc);
      if (!walker) continue;
      if (!Number.isFinite(walker.y)) {
        const chain = population.network.chains[walker.chain];
        const y = this.heights.height(chain, walker.s);
        if (y === null) { this.heights.request(chain, frameNow); continue; }
        walker.y = y;
      }
      candidates.push(walker);
    }
    const drawn = this.ring.select(candidates, view, this.options.cap, dt);
    const instances: WalkerInstance[] = [];
    const turn = 1 - Math.exp(-dt * YAW_RATE);
    for (const entry of drawn) {
      const w = entry.walker, look = humanAppearance(w.id);
      const variant = this.asset.variants[look.variant];
      const scale = w.scale;
      if (entry.first) entry.yaw = w.yaw;
      else entry.yaw += Math.atan2(Math.sin(w.yaw - entry.yaw), Math.cos(w.yaw - entry.yaw)) * turn;
      const seed = (hash32(w.id, 13) % 1000) / 1000;
      const clip = w.moving ? variant.walk : variant.idle;
      const phase = w.moving ? w.distance / (variant.metresPerLoop * scale) + seed : w.idleTime / IDLE_SECONDS + seed;
      const rowStart = variant.rowBase + clip.row;
      const previousPhase = entry.first || entry.clip !== rowStart ? phase : entry.phase;
      entry.phase = phase; entry.clip = rowStart; entry.first = false;
      const distance = Math.hypot(w.x - view.x, w.z - view.z);
      entry.lod = distance > (entry.lod === 1 ? 22 : 26) ? 1 : 0;
      instances.push({
        variant: look.variant, x: w.x, y: w.y, z: w.z, yaw: entry.yaw, scale,
        lod: entry.lod, shape: look.model.shape,
        rowStart, frames: clip.frames, phase: phase % 1, previousPhase: previousPhase % 1,
        skin: look.skin, top: look.top, bottom: look.bottom, hair: look.hair,
        shoes: look.shoes, inner: look.inner, legwear: look.legwear,
        fade: Math.max(0.001, entry.fade * w.fade),
      });
    }
    this.meshes.update(instances);
    this.count = instances.length;
    this.heights.pump(RAYS_PER_FRAME, frameNow);
    this.updateMs = performance.now() - start;
  }

  /** Hot-swap only GPU meshes; the deterministic lives and ground cache survive. */
  replaceMeshes(Renderer: typeof WalkerMeshes) {
    if (!this.asset) return;
    const next = new Renderer(this.asset, this.options.cap + RING.margin);
    // Atlas belongs to the asset during a renderer-only swap.
    const atlas = this.asset.albedo;
    this.asset.albedo = undefined; this.meshes?.dispose(); this.asset.albedo = atlas;
    this.meshes = next; this.group.add(next.group);
  }

  /** Walkers drawn within `radius` metres of the camera (crowd audio). */
  nearby(radius: number): number {
    if (!this.meshes) return 0;
    let n = 0;
    for (const mesh of this.meshes.meshes) {
      for (let i = 0; i < mesh.count; i++) {
        const e = mesh.instanceMatrix.array;
        if (Math.hypot(e[16 * i + 12] - this.camera.x, e[16 * i + 14] - this.camera.z) <= radius) n++;
      }
    }
    return n;
  }

  snapshot() {
    return { cap: this.options.cap, drawn: this.count, candidates: this.wide.length, chains: this.network?.chains.length ?? 0,
      cells: this.network?.cells.size ?? 0, graphs: this.graphs.length, heightsPending: this.heights.pending, rays: this.heights.rays,
      buildMs: Number(this.buildMs.toFixed(2)), updateMs: Number(this.updateMs.toFixed(3)), scanMs: Number(this.scanMs.toFixed(3)),
      variants: this.meshes?.meshes.map(m => m.count) ?? [] };
  }

  dispose() {
    this.disposed = true; ++this.assetRequest;
    this.worker?.terminate();
    this.meshes?.dispose();
    this.group.removeFromParent();
    this.ring.clear();
    systems.delete(this);
  }
}

// Preserve the live population, height cache and streamed city while editing human appearance/meshes.
const hot = import.meta.hot;
if (hot) {
  hot.data.systems = systems;
  hot.accept();
  for (const system of systems) Object.setPrototypeOf(system, Walkers.prototype);
  hot.accept('./visuals', module => {
    if (!module) return;
    for (const system of systems) system.replaceMeshes(module.WalkerMeshes);
  });
}
